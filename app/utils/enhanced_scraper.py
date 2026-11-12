# app/utils/enhanced_scraper.py
import os
import sys
import requests
from app.utils.proxy_transport import macro_endpoints, proxy_request
from app.cms_scraper import BahriaCMSScraper
from app.utils.data_manager import data_manager
from app.models.account import CMSAccount
import threading
from datetime import datetime
from bs4 import BeautifulSoup


class EnhancedCMSScraper(BahriaCMSScraper):
    """Enhanced CMS scraper with multi-account support"""
    
    def __init__(self, account: CMSAccount):
        super().__init__()
        self.account = account
        self.account_id = account.account_id
        
        # Override data directory to use account-specific directory
        from app.routes.auth import get_institute_id_for_enrollment
        account_inst = getattr(account, 'institute_id', None)
        cms_user = getattr(account, 'cms_username', '')
        resolved_inst = account_inst or get_institute_id_for_enrollment(cms_user)
        if str(resolved_inst).strip() == '2' and cms_user:
            resolved_inst = get_institute_id_for_enrollment(cms_user)
        self.institute_id = str(resolved_inst)
        
        # Re-initialize proxy worker with the correct account_id
        # (super().__init__ used 'unknown' because account_id wasn't set yet)
        from app.utils.proxy_manager import get_proxy_manager
        self.session.worker_url = get_proxy_manager().get_worker(self.account_id)
        
        # Load existing session cookies for this account if available
        self.load_session(self.account.cms_username)
    
    def save_session(self, username=None):
        """Save session cookies for the account"""
        try:
            # Save to standard CMS data manager
            result = data_manager.save_session_data(self.account_id, self.session.cookies)
            
            # Also sync these CMS cookies into the LMS session manager
            # This ensures if LMS needs to authenticate, it can try to use this fresh CMS session
            try:
                from app.services.lms_session_manager import LMSSessionManager
                import requests
                
                lms_mgr = LMSSessionManager()
                cookie_dict = requests.utils.dict_from_cookiejar(self.session.cookies)
                # Store as CMS cookies in the unified session
                lms_mgr.store_session_cookies(self.account_id, cookie_dict, session_type='cms')
            except Exception as e:
                print(f"Warning: Failed to sync CMS session to LMS manager: {e}")
                
            return result
        except Exception as e:
            print(f"Error saving session for account {self.account_id}: {e}")
            return False
    
    def load_session(self, username=None):
        """Load saved session cookies for the account"""
        try:
            has_cookies = False
            
            # Load from standard CMS data manager first
            cookies = data_manager.load_session_data(self.account_id)
            if cookies:
                self.session.cookies.update(cookies)
                has_cookies = True
                
            # Then try to load from LMS session manager, allowing it to overwrite older CMS cookies
            # Since LMS updates LMSSessionManager but NOT data_manager, LMS's copy might be fresher
            # And since CMS save_session updates BOTH, LMSSessionManager is always >= freshness.
            try:
                from app.services.lms_session_manager import LMSSessionManager
                lms_mgr = LMSSessionManager()
                if lms_mgr.is_session_valid(self.account_id):
                    cms_cookies = lms_mgr.get_cms_cookies(self.account_id)
                    if cms_cookies:
                        self.session.cookies.update(cms_cookies)
                        has_cookies = True
            except Exception as e:
                print(f"Warning: Failed to load CMS session from LMS manager: {e}")
                
            return has_cookies
        except Exception as e:
            print(f"Error loading session for account {self.account_id}: {e}")
            return False
    
    def save_data(self, username, data_type, data):
        """Save scraped data using the enhanced data manager"""
        try:
            # Add metadata
            enhanced_data = {
                **data,
                'account_id': self.account_id,
                'cms_username': self.account.cms_username,
                'scraped_at': datetime.now().isoformat(),
                'scraper_version': '2.0'
            }
            
            return data_manager.save_account_data(
                account_id=self.account_id,
                data_type=data_type,
                data=enhanced_data
            )
        except Exception as e:
            print(f"Error saving {data_type} data for account {self.account_id}: {e}")
            return False
    
    def load_data(self, username, data_type):
        """Load saved data using the enhanced data manager"""
        try:
            return data_manager.load_account_data(
                account_id=self.account_id,
                data_type=data_type
            )
        except Exception as e:
            print(f"Error loading {data_type} data for account {self.account_id}: {e}")
            return None
    
    def scrape_page_atomic(self, action: str, username: str, password: str, institute_id: str = None):
        """
        Scrape a single CMS page in a single atomic edge execution (Login + Fetch).
        Guarantees identical egress IP to prevent CMS ASP.NET session termination.
        Returns BeautifulSoup soup, or None on failure.
        """
        import logging
        from bs4 import BeautifulSoup
        from app.routes.auth import get_institute_id_for_enrollment
        logger = logging.getLogger(__name__)
        
        target_inst = institute_id
        if not target_inst or (str(target_inst).strip() == '2' and username and not str(username).strip().startswith('02-')):
            target_inst = get_institute_id_for_enrollment(username, fallback=getattr(getattr(self, 'account', None), 'institute_id', None) or getattr(self, 'institute_id', '2'))
        payload = {
            "action": action,
            "username": username,
            "password": password,
            "institute_id": str(target_inst)
        }
        
        # Tier 1 (Cloudflare: 100k/day free) -> Tier 2 (Supabase: 500k/mo fallback)
        endpoints = macro_endpoints(20, 25)

        for tier_name, macro_url, timeout in endpoints:
            try:
                logger.info(f"[{self.account_id}] Requesting atomic {action} via {tier_name} macro: {macro_url}")
                resp = proxy_request(self.session.post, macro_url, json=payload, timeout=timeout)
                
                if resp.status_code == 401:
                    try:
                        err_data = resp.json()
                        err_code = err_data.get('error_code')
                        if err_code in ('REAL_BAD_PASSWORD', 'INVALID_CREDENTIALS'):
                            logger.warning(f"[{self.account_id}] Verified bad credentials reported by {tier_name} during atomic {action}. Halting.")
                            return None
                    except Exception:
                        pass
                    continue

                if resp.status_code == 200 and 'tbEnrollment' not in resp.text:
                    proxy_cookies = resp.headers.get('X-Proxy-Set-Cookie')
                    if proxy_cookies:
                        for cookie_str in proxy_cookies.split('|||'):
                            key_val = cookie_str.split(';')[0].strip()
                            if '=' in key_val:
                                k, v = key_val.split('=', 1)
                                self.session.cookies.set(k, v)
                    
                    self.save_session(username)
                    logger.info(f"[{self.account_id}] Atomic {action} scrape successful via {tier_name}")
                    return BeautifulSoup(resp.text, 'html.parser')
                else:
                    logger.warning(f"[{self.account_id}] {tier_name} {action} returned {resp.status_code}: {resp.text[:200]}")
            except Exception as e:
                logger.warning(f"[{self.account_id}] {tier_name} atomic {action} failed: {e}")

        return None

    def scrape_attendance_atomic(self, username: str, password: str, institute_id: str = None):
        """Scrape attendance in a single atomic edge execution (Login + Fetch)."""
        return self.scrape_page_atomic("attendance", username, password, institute_id)

    def scrape_timetable_atomic(self, username: str, password: str, institute_id: str = None):
        """Scrape timetable in a single atomic edge execution (Login + Fetch)."""
        return self.scrape_page_atomic("timetable", username, password, institute_id)

    def scrape_community_service_atomic(self, username: str, password: str, institute_id: str = None):
        """Scrape community service in a single atomic edge execution (Login + Fetch)."""
        from app.routes.auth import get_institute_id_for_enrollment
        target_inst = institute_id
        if not target_inst or (str(target_inst).strip() == '2' and username and not str(username).strip().startswith('02-')):
            target_inst = get_institute_id_for_enrollment(username, fallback=getattr(getattr(self, 'account', None), 'institute_id', None) or getattr(self, 'institute_id', '2'))
        target_institute = str(target_inst)
        if target_institute == '5' or (username and str(username).strip().startswith('05-')):
            return None
        return self.scrape_page_atomic("community_service", username, password, target_institute)

    def scrape_gpa_data_atomic(self, username: str, password: str, institute_id: str = None):
        """Scrape GPA / exam results in a single atomic edge execution (Login + Fetch)."""
        return self.scrape_page_atomic("results", username, password, institute_id)

    def scrape_profile_atomic(self, username: str, password: str, institute_id: str = None):
        """Scrape profile in a single atomic edge execution (Login + Fetch)."""
        return self.scrape_page_atomic("profile", username, password, institute_id)

    def scrape_full_sync_atomic(self, username: str, password: str, institute_id: str = None):
        """
        Scrape entire first-run dataset in a single atomic edge macro execution.
        Tier 1: Cloudflare Worker Macro (https://proxy.example.invalid/?macro=true)
        Tier 2: Supabase Edge Function Macro (https://proxy.example.invalid/functions/v1/proxy?macro=true)

        Guarantees identical egress IP across all sequential fetches to prevent
        ASP.NET session state termination.
        Returns dict with raw HTML and cookies, or None on failure.
        """
        import logging
        from app.routes.auth import get_institute_id_for_enrollment
        logger = logging.getLogger(__name__)

        target_inst = institute_id
        if not target_inst or (str(target_inst).strip() == '2' and username and not str(username).strip().startswith('02-')):
            target_inst = get_institute_id_for_enrollment(username, fallback=getattr(getattr(self, 'account', None), 'institute_id', None) or getattr(self, 'institute_id', '2'))
        payload = {
            "action": "full_sync",
            "username": username,
            "password": password,
            "institute_id": str(target_inst)
        }

        # Tier 1 (Cloudflare: 100k/day) -> Tier 2 (Supabase: 500k/month fallback)
        endpoints = macro_endpoints(20, 30)

        auth_failed_count = 0
        session_lost_count = 0
        total_tiers = len(endpoints)
        cms_down_detected = False

        for tier_name, macro_url, timeout in endpoints:
            try:
                logger.info(f"[{self.account_id}] Requesting atomic full_sync via {tier_name}: {macro_url}")
                resp = proxy_request(self.session.post, macro_url, json=payload, timeout=timeout)

                if resp.status_code == 401:
                    err_code = None
                    try:
                        err_data = resp.json()
                        err_code = err_data.get('error_code')
                    except Exception:
                        pass
                    if err_code in ('REAL_BAD_PASSWORD', 'INVALID_CREDENTIALS'):
                        logger.warning(f"[{self.account_id}] Verified bad credentials reported by {tier_name}. Halting failover.")
                        return {"auth_failed": True, "error": "Invalid credentials"}
                    auth_failed_count += 1
                    logger.warning(f"[{self.account_id}] Unverified 401 returned by {tier_name} ({auth_failed_count}/{total_tiers} failed). Advancing tier.")
                    continue

                if resp.status_code in (403, 502):
                    is_session_lost = False
                    try:
                        err_data = resp.json()
                        if err_data.get('error_code') == 'SESSION_LOST' or 'session' in str(err_data).lower():
                            is_session_lost = True
                    except Exception:
                        if 'SESSION_LOST' in (resp.text or ''):
                            is_session_lost = True
                    if is_session_lost:
                        session_lost_count += 1
                        logger.warning(f"[{self.account_id}] Session drop / IP jitter reported by {tier_name}. Advancing to next tier.")
                        continue

                if resp.status_code == 200:
                    try:
                        data = resp.json()
                        if data.get('success') and data.get('html'):
                            cookies_str = data.get('cookies') or resp.headers.get('X-Proxy-Set-Cookie')
                            if cookies_str:
                                if 'cookies' not in data:
                                    data['cookies'] = cookies_str
                                for cookie_entry in cookies_str.split('|||'):
                                    key_val = cookie_entry.split(';')[0].strip()
                                    if '=' in key_val:
                                        k, v = key_val.split('=', 1)
                                        self.session.cookies.set(k, v)
                                self.save_session(username)

                            logger.info(f"[{self.account_id}] Atomic full_sync succeeded via {tier_name}!")
                            return data
                    except Exception as parse_err:
                        logger.warning(f"[{self.account_id}] Failed to parse JSON from {tier_name}: {parse_err}")

                resp_text = resp.text or ""
                if resp.status_code in (500, 502, 503, 504):
                    try:
                        err_json = resp.json()
                        if err_json.get('cms_down'):
                            cms_down_detected = True
                    except Exception:
                        pass
                    if any(phrase in resp_text for phrase in ("Failed to extract ViewState", "Runtime Error", "Server Error", "CMS server is currently down")):
                        cms_down_detected = True

                logger.warning(f"[{self.account_id}] {tier_name} full_sync returned status {resp.status_code}: {resp_text[:200]}")
            except Exception as e:
                logger.warning(f"[{self.account_id}] {tier_name} full_sync failed: {e}")

        if cms_down_detected:
            self.cms_down = True
            logger.warning(f"[{self.account_id}] CMS server downtime detected across atomic macro endpoints.")
            return {
                "cms_down": True,
                "error": "Bahria University CMS server is currently down (HTTP 500 Runtime Error). This is an issue on the university portal, not LMS+."
            }

        if session_lost_count > 0:
            logger.warning(f"[{self.account_id}] Session drop detected across atomic macro endpoints ({session_lost_count}/{total_tiers}).")
            return {
                "session_lost": True,
                "error": "CMS connection dropped. Please try again."
            }

        logger.error(f"[{self.account_id}] All atomic full_sync tiers failed.")
        return None

    def scrape_attendance(self, username=None):
        """
        Scrape attendance data with atomic edge execution priority.
        Guarantees single-hop login+scrape on identical egress IP to prevent
        ASP.NET session drops in containerized / proxy environments.
        Falls back to standard flow if atomic macro fails.
        """
        import logging
        logger = logging.getLogger(__name__)
        target_username = username or self.account.cms_username

        # ── Step 1: Fast Atomic Edge Scrape (1-shot Login + Attendance) ───────────
        try:
            password = getattr(self.account, 'cms_password', None)
            from app.routes.auth import get_institute_id_for_enrollment
            inst_id = get_institute_id_for_enrollment(target_username, fallback=getattr(self, 'institute_id', None) or getattr(self.account, 'institute_id', '2'))
            if password:
                soup = self.scrape_attendance_atomic(target_username, password, inst_id)
                if soup:
                    attendance_data = self.parse_attendance(soup)
                    if attendance_data and 'courses' in attendance_data and not attendance_data.get('error'):
                        self.save_data(target_username, 'attendance', attendance_data)
                        return attendance_data
        except Exception as e:
            logger.warning(f"[{self.account_id}] Atomic attendance scrape failed: {e}")

        # ── Step 2: Fallback to standard CMS scrape ──────────────────────────────
        try:
            if not self.verify_login():
                password = getattr(self.account, 'cms_password', None)
                if password:
                    if not self.login(target_username, password):
                        return None
                else:
                    return None
            return super().scrape_attendance(target_username)
        except Exception as e:
            logger.warning(f"[{self.account_id}] Standard attendance scrape failed: {e}")
            return None

    def scrape_timetable(self, username=None):
        """
        Scrape timetable data with atomic edge execution priority.
        Guarantees single-hop login+scrape on identical egress IP to prevent
        ASP.NET session drops in containerized / proxy environments.
        Falls back to standard flow if atomic macro fails.
        """
        import logging
        logger = logging.getLogger(__name__)
        target_username = username or self.account.cms_username

        # ── Step 1: Fast Atomic Edge Scrape (1-shot Login + Timetable) ────────────
        try:
            password = getattr(self.account, 'cms_password', None)
            from app.routes.auth import get_institute_id_for_enrollment
            inst_id = get_institute_id_for_enrollment(target_username, fallback=getattr(self, 'institute_id', None) or getattr(self.account, 'institute_id', '2'))
            if password:
                soup = self.scrape_timetable_atomic(target_username, password, inst_id)
                if soup:
                    timetable_data = self.parse_timetable(soup)
                    if timetable_data and timetable_data.get('time_slots') and not timetable_data.get('error'):
                        self.save_data(target_username, 'timetable', timetable_data)
                        return timetable_data
        except Exception as e:
            logger.warning(f"[{self.account_id}] Atomic timetable scrape failed: {e}")

        # ── Step 2: Fallback to standard CMS scrape ──────────────────────────────
        try:
            if not self.verify_login():
                password = getattr(self.account, 'cms_password', None)
                if password:
                    if not self.login(target_username, password):
                        return None
                else:
                    return None
            return super().scrape_timetable(target_username)
        except Exception as e:
            logger.warning(f"[{self.account_id}] Standard timetable scrape failed: {e}")
            return None

    def scrape_community_service(self, username=None):
        """
        Scrape community service data with atomic edge execution priority.
        Guarantees single-hop login+scrape on identical egress IP to prevent
        ASP.NET session drops in containerized / proxy environments.
        Falls back to standard flow if atomic macro fails.
        """
        import logging
        from datetime import datetime
        logger = logging.getLogger(__name__)
        target_username = username or self.account.cms_username
        from app.routes.auth import get_institute_id_for_enrollment
        target_institute = str(get_institute_id_for_enrollment(target_username, fallback=getattr(self, 'institute_id', None) or getattr(self.account, 'institute_id', '2')))
        if target_institute == '5' or (target_username and str(target_username).strip().startswith('05-')):
            logger.info(f"[{self.account_id}] Skipping community service scrape (IPP campus).")
            cs_data = {
                'not_applicable': True,
                'total_hours': 0,
                'total_projects': 0,
                'projects': [],
                'completed_hours': 0,
                'completed_activities': [],
                'scraped_at': datetime.now().isoformat()
            }
            self.save_data(target_username, 'community_service', cs_data)
            return cs_data

        # ── Step 1: Fast Atomic Edge Scrape (1-shot Login + CSP) ─────────────────
        try:
            password = getattr(self.account, 'cms_password', None)
            if password:
                soup = self.scrape_community_service_atomic(target_username, password, target_institute)
                if soup:
                    cs_data = self.parse_community_service(soup)
                    if cs_data and not cs_data.get('error'):
                        self.save_data(target_username, 'community_service', cs_data)
                        return cs_data
        except Exception as e:
            logger.warning(f"[{self.account_id}] Atomic community service scrape failed: {e}")

        # ── Step 2: Fallback to standard CMS scrape ──────────────────────────────
        try:
            if not self.verify_login():
                password = getattr(self.account, 'cms_password', None)
                if password:
                    if not self.login(target_username, password):
                        return None
                else:
                    return None
            return super().scrape_community_service(target_username)
        except Exception as e:
            logger.warning(f"[{self.account_id}] Standard community service scrape failed: {e}")
            return None

    def scrape_gpa_data(self, username=None):
        """
        Scrape GPA / exam results data with atomic edge execution priority.
        Guarantees single-hop login+scrape on identical egress IP to prevent
        ASP.NET session drops in containerized / proxy environments.
        Falls back to standard flow if atomic macro fails.
        """
        import logging
        logger = logging.getLogger(__name__)
        target_username = username or self.account.cms_username

        # ── Step 1: Fast Atomic Edge Scrape (1-shot Login + Results) ─────────────
        try:
            password = getattr(self.account, 'cms_password', None)
            from app.routes.auth import get_institute_id_for_enrollment
            inst_id = get_institute_id_for_enrollment(target_username, fallback=getattr(self, 'institute_id', None) or getattr(self.account, 'institute_id', '2'))
            if password:
                soup = self.scrape_gpa_data_atomic(target_username, password, inst_id)
                if soup:
                    gpa_data = self.parse_gpa_data(soup)
                    if gpa_data and not gpa_data.get('error'):
                        self.save_data(target_username, 'gpa', gpa_data)
                        return gpa_data
        except Exception as e:
            logger.warning(f"[{self.account_id}] Atomic GPA data scrape failed: {e}")

        # ── Step 2: Fallback to standard CMS scrape ──────────────────────────────
        try:
            if not self.verify_login():
                password = getattr(self.account, 'cms_password', None)
                if password:
                    if not self.login(target_username, password):
                        return None
                else:
                    return None
            return super().scrape_gpa_data(target_username)
        except Exception as e:
            logger.warning(f"[{self.account_id}] Standard GPA data scrape failed: {e}")
            return None

    def scrape_attendance_summary_atomic(self, username: str, password: str, course_code: str = "", institute_id: str = None, detail_url: str = ""):
        """
        Scrape course attendance summary in a single atomic edge macro execution.
        Login -> StudentWiseAttendance -> extract detail_url -> ClassAttendanceSummary.
        Guarantees identical egress IP across all hops to prevent token and session expiration.
        Tier 1: Cloudflare Worker (?macro=true, 100k req/day free)
        Tier 2: Supabase Edge Function (?macro=true, 500k req/mo free fallback)
        """
        import logging
        from bs4 import BeautifulSoup
        from app.routes.auth import get_institute_id_for_enrollment
        logger = logging.getLogger(__name__)

        target_inst = institute_id
        if not target_inst or (str(target_inst).strip() == '2' and username and not str(username).strip().startswith('02-')):
            target_inst = get_institute_id_for_enrollment(username, fallback=getattr(getattr(self, 'account', None), 'institute_id', None) or getattr(self, 'institute_id', '2'))
        payload = {
            "action": "attendance_summary",
            "username": username,
            "password": password,
            "course_code": course_code or "",
            "detail_url": detail_url or "",
            "institute_id": str(target_inst)
        }

        endpoints = macro_endpoints(25, 30)

        import re
        for tier_name, macro_url, timeout in endpoints:
            for attempt in range(2):
                try:
                    logger.info(f"[{self.account_id}] Requesting atomic attendance_summary for {course_code} via {tier_name}: {macro_url}")
                    resp = proxy_request(self.session.post, macro_url, json=payload, timeout=timeout, headers={"Connection": "close"})

                    if resp.status_code == 401:
                        try:
                            err_data = resp.json()
                            err_code = err_data.get('error_code')
                            if err_code == 'REAL_BAD_PASSWORD':
                                logger.warning(f"[{self.account_id}] Verified bad credentials reported by {tier_name} during attendance summary. Halting.")
                                return None
                        except Exception:
                            pass
                        break  # Session/auth error on this tier, advance to next tier

                    is_error_json = resp.text.strip().startswith('{') and ('"error"' in resp.text or '"error_code"' in resp.text)
                    is_dashboard_title = bool(re.search(r'<title>[^<]*Dashboard', resp.text, re.I))

                    if resp.status_code == 200 and 'tbEnrollment' not in resp.text and not is_error_json and not is_dashboard_title:
                        proxy_cookies = resp.headers.get('X-Proxy-Set-Cookie')
                        if proxy_cookies:
                            for cookie_str in proxy_cookies.split('|||'):
                                key_val = cookie_str.split(';')[0].strip()
                                if '=' in key_val:
                                    k, v = key_val.split('=', 1)
                                    self.session.cookies.set(k, v)

                        self.save_session(username)
                        logger.info(f"[{self.account_id}] Atomic attendance_summary successful via {tier_name}")
                        soup = BeautifulSoup(resp.text, 'html.parser')
                        parsed = self.parse_attendance_summary(soup)
                        if parsed is not None and parsed not in ["STALE_TOKEN", "EXPIRED"]:
                            return parsed
                    else:
                        logger.warning(f"[{self.account_id}] {tier_name} attendance_summary returned {resp.status_code} (dashboard_title={is_dashboard_title}, err_json={is_error_json}): {resp.text[:200]}")
                    break
                except (requests.exceptions.ConnectionError, requests.exceptions.ChunkedEncodingError, requests.exceptions.Timeout) as ce:
                    if attempt == 0:
                        logger.debug(f"[{self.account_id}] {tier_name} socket closed or timed out, retrying immediately: {ce}")
                        continue
                    logger.warning(f"[{self.account_id}] {tier_name} atomic attendance_summary connection error: {ce}")
                    break
                except Exception as e:
                    logger.warning(f"[{self.account_id}] {tier_name} atomic attendance_summary failed: {e}")
                    break

        return None

    def scrape_attendance_summary(self, detail_url=None, username=None, password=None, course_code=None):
        """
        Scrape detailed attendance summary with atomic edge macro priority.
        Falls back to standard scrape with explicit Referer header if macro fails.
        """
        target_username = username or self.account.cms_username
        target_password = password or getattr(self.account, 'cms_password', None)
        from app.routes.auth import get_institute_id_for_enrollment
        target_institute = get_institute_id_for_enrollment(target_username, fallback=getattr(self, 'institute_id', None) or getattr(self.account, 'institute_id', '2'))

        # ── Step 1: Fast Atomic Edge Scrape ──────────────────────────────────────
        if target_password and (course_code or detail_url):
            summary = self.scrape_attendance_summary_atomic(
                username=target_username,
                password=target_password,
                course_code=course_code or "",
                institute_id=target_institute,
                detail_url=detail_url or ""
            )
            if summary is not None and summary not in ["STALE_TOKEN", "EXPIRED"]:
                return summary

        # ── Step 2: Fallback to standard CMS scrape with Referer header ──────────
        try:
            import re
            active_detail_url = detail_url
            if not active_detail_url and course_code:
                att_data = self.scrape_attendance(target_username)
                if att_data and att_data.get('courses'):
                    clean_c = (course_code or '').strip().lower()
                    norm_c = re.sub(r'[\s\-_]+', '', clean_c)
                    matched = next((c for c in att_data['courses'] if (c.get('code') or '').strip().lower() == clean_c or re.sub(r'[\s\-_]+', '', str(c.get('code') or '')).lower() == norm_c), None)
                    if matched:
                        active_detail_url = matched.get('detail_url')

            if active_detail_url:
                old_referer = self.session.headers.get('Referer')
                self.session.headers['Referer'] = 'https://cms.bahria.edu.pk/Sys/Student/ClassAttendance/StudentWiseAttendance.aspx'
                try:
                    summary = super().scrape_attendance_summary(active_detail_url, target_username, target_password, course_code=course_code)
                    if summary in ["STALE_TOKEN", "EXPIRED"]:
                        # Recover: refresh attendance once for fresh detail token and retry
                        att_data = self.scrape_attendance(target_username)
                        if att_data and att_data.get('courses'):
                            clean_c = (course_code or '').strip().lower()
                            norm_c = re.sub(r'[\s\-_]+', '', clean_c)
                            matched = next((c for c in att_data['courses'] if (c.get('code') or '').strip().lower() == clean_c or re.sub(r'[\s\-_]+', '', str(c.get('code') or '')).lower() == norm_c), None)
                            if matched and matched.get('detail_url'):
                                summary = super().scrape_attendance_summary(matched['detail_url'], target_username, target_password, course_code=course_code)
                    return summary
                finally:
                    if old_referer:
                        self.session.headers['Referer'] = old_referer
                    else:
                        self.session.headers.pop('Referer', None)
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning(f"[{self.account_id}] Standard attendance summary fallback failed: {e}")

        return None

    def scrape_profile(self, username):
        """Scrape student profile information from CMS"""
        target_username = username or getattr(getattr(self, 'account', None), 'cms_username', None) or username
        # ── Step 1: Fast Atomic Edge Scrape (1-shot Login + Profile) ─────────────
        try:
            password = getattr(self.account, 'cms_password', None)
            from app.routes.auth import get_institute_id_for_enrollment
            inst_id = get_institute_id_for_enrollment(target_username, fallback=getattr(self, 'institute_id', None) or getattr(self.account, 'institute_id', '2'))
            if password:
                soup = self.scrape_profile_atomic(target_username, password, inst_id)
                if soup:
                    profile_data = self.parse_profile(soup)
                    if profile_data:
                        self.save_data(target_username, 'profile', profile_data)
                        return profile_data
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning(f"[{self.account_id}] Atomic profile scrape failed: {e}")

        # ── Step 2: Fallback to standard CMS scrape ──────────────────────────────
        try:
            print(f"Scraping profile for {username}")
            
            # Check if session is active by trying dashboard first
            dashboard_url = "https://cms.bahria.edu.pk/Sys/Student/Dashboard.aspx"
            dashboard_response = self._request('GET', dashboard_url, follow_redirects=True)
            
            # If we got redirected to login, we need to re-login
            if 'Login.aspx' in dashboard_response.url or 'tbEnrollment' in dashboard_response.text:
                print("Session expired, re-logging in...")
                # Try to get password and re-login
                if hasattr(self, 'account') and self.account:
                    if self.login(self.account.cms_username, self.account.cms_password):
                        print("✅ Re-login successful")
                        # Try dashboard again
                        dashboard_response = self._request('GET', dashboard_url, follow_redirects=True)
                    else:
                        print("❌ Re-login failed")
                        return None
                else:
                    print("❌ Cannot re-login: no account credentials available")
                    return None
            
            # Give the server a bit more time to fully establish the session/profile data
            import time
            time.sleep(3)
            
            # Now fetch the profile page
            profile_url = "https://cms.bahria.edu.pk/Sys/Student/Profile.aspx"
            response = self._request('GET', profile_url, follow_redirects=True)
            
            if response.status_code != 200:
                print(f"Failed to fetch profile page. Status: {response.status_code}")
                return None
            
            # Check if we got redirected back to login
            if 'Login.aspx' in response.url or 'tbEnrollment' in response.text:
                print("Profile page returned login page even after re-login")
                return None
            
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(response.text, 'html.parser')
            profile_data = self.parse_profile(soup)
            
            if profile_data:
                self.save_data(username, 'profile', profile_data)
                print(f"✅ Profile data scraped and saved for {username}")
            
            return profile_data
            
        except Exception as e:
            print(f"Error scraping profile: {e}")
            import traceback
            traceback.print_exc()
            return None
    
    def parse_profile(self, soup):
        """Parse profile information from the HTML"""
        try:
            profile_data = {
                'scraped_at': datetime.now().isoformat()
            }
            
            # Map ALL profile fields from the CMS profile page
            # Keys will be matched against lowercase, stripped labels (no colons)
            field_mapping = {
                # Basic Information
                'enrollment': 'enrollment',
                'registration no.': 'registration_no',
                'registration no': 'registration_no',
                'name': 'name',
                'student name': 'name',
                'father name': 'father_name',
                "father's name": 'father_name',
                
                # Academic Information
                'program': 'program',
                'degree duration': 'degree_duration',
                'intake semester': 'intake_semester',
                'max semester': 'max_semester',
                
                # Contact Information
                'mobile no.': 'mobile_no',
                'mobile no': 'mobile_no',
                'phone no.': 'phone_no',
                'phone no': 'phone_no',
                'personal email': 'personal_email',
                'university email': 'university_email',
                
                # Address Information
                'current address': 'current_address',
                'permanent address': 'permanent_address',
                
                # Additional fields (if they exist)
                'cnic': 'cnic',
                'date of birth': 'date_of_birth',
                'gender': 'gender',
                'blood group': 'blood_group',
                'religion': 'religion',
                'nationality': 'nationality',
                'domicile': 'domicile',
                'campus': 'campus',
                'department': 'department',
                'batch': 'batch',
                'section': 'section',
                'advisor': 'advisor',
                'status': 'status'
            }
            
            # Find the main profile table
            tables = soup.find_all('table')
            
            for table in tables:
                rows = table.find_all('tr')
                for row in rows:
                    cells = row.find_all(['th', 'td'])
                    
                    # Handle 4-column layout (th-td-th-td) - two label-value pairs per row
                    if len(cells) >= 4:
                        # First pair (cells 0 and 1)
                        label1 = cells[0].get_text(strip=True).replace(':', '').strip().lower()
                        value_cell1 = cells[1]
                        # Remove any anchor tags (like "Update" links)
                        for a in value_cell1.find_all('a'):
                            a.decompose()
                        value1 = value_cell1.get_text(strip=True)
                        
                        if label1 in field_mapping and value1:
                            profile_data[field_mapping[label1]] = value1
                        
                        # Second pair (cells 2 and 3)
                        label2 = cells[2].get_text(strip=True).replace(':', '').strip().lower()
                        value_cell2 = cells[3]
                        # Remove any anchor tags
                        for a in value_cell2.find_all('a'):
                            a.decompose()
                        value2 = value_cell2.get_text(strip=True)
                        
                        if label2 in field_mapping and value2:
                            profile_data[field_mapping[label2]] = value2
                    
                    # Handle 2-column layout (th-td) - single label-value pair per row
                    elif len(cells) >= 2:
                        label = cells[0].get_text(strip=True).replace(':', '').strip().lower()
                        # Get value and clean it - remove any links/buttons
                        value_cell = cells[1]
                        # Remove any anchor tags (like "Update" links)
                        for a in value_cell.find_all('a'):
                            a.decompose()
                        value = value_cell.get_text(strip=True)
                        
                        if label in field_mapping and value:
                            profile_data[field_mapping[label]] = value
            
            # Also extract from span elements (header info)
            username_span = soup.find('span', {'id': lambda x: x and ('lblUsername' in x or 'lbl_Username' in x)})
            if username_span:
                profile_data['cms_username'] = username_span.get_text(strip=True)
            
            name_span = soup.find('span', {'id': lambda x: x and ('lblName' in x or 'lblStudentName' in x or 'lbl_Name' in x)})
            if name_span and 'name' not in profile_data:
                profile_data['name'] = name_span.get_text(strip=True)
            
            email_span = soup.find('span', {'id': lambda x: x and ('lblEmail' in x or 'lbl_Email' in x)})
            if email_span and 'university_email' not in profile_data:
                profile_data['university_email'] = email_span.get_text(strip=True)
                
            # ULTRA ROBUST FALLBACK: Search for the literal text "Name" or "Student Name" 
            # and grab the next sibling element's text.
            import re
            if 'name' not in profile_data:
                name_labels = soup.find_all(string=re.compile(r'^\s*(Name|Student Name)\s*:?\s*$', re.IGNORECASE))
                for label_node in name_labels:
                    parent = label_node.parent
                    next_node = parent.find_next_sibling()
                    if next_node:
                        val = next_node.get_text(strip=True)
                        if val and len(val) > 2:
                            profile_data['name'] = val
                            break
            
            # If name is STILL not found, sometimes it's nested differently (e.g. <div><span>Name</span><span>VALUE</span></div>)
            if 'name' not in profile_data:
                name_labels = soup.find_all(string=re.compile(r'^\s*(Name|Student Name)\s*:?\s*$', re.IGNORECASE))
                for label_node in name_labels:
                    # Look at all parent's siblings and children
                    container = label_node.find_parent(['tr', 'div', 'li'])
                    if container:
                        text_content = container.get_text(separator='|', strip=True)
                        parts = [p.strip() for p in text_content.split('|') if p.strip()]
                        for i, part in enumerate(parts):
                            if part.lower() in ['name', 'student name', 'name:', 'student name:'] and i + 1 < len(parts):
                                val = parts[i+1].strip()
                                if val and len(val) > 2:
                                    profile_data['name'] = val
                                    break
                    if 'name' in profile_data:
                        break
            
            return profile_data
            
        except Exception as e:
            print(f"Error parsing profile: {e}")
            import traceback
            traceback.print_exc()
            return None
            
    def check_for_pending_surveys(self):
        """Check if there are any pending CMS surveys for the current account"""
        try:
            # Use _is_logged_in_cms instead of verify_login, because when surveys are
            # blocking LMS, the timetable page (used by verify_login) is redirected
            # to the survey page, causing a false negative.
            if not self._is_logged_in_cms():
                print(f"[SURVEY] CMS session invalid, logging in for {self.account.cms_username}")
                if not self.login(self.account.cms_username, self.account.cms_password):
                    print(f"[SURVEY] Failed to login for survey check")
                    return []
            
            # Call the base class method
            return super().check_for_pending_surveys(self.account.cms_username)
        except Exception as e:
            print(f"[SURVEY] Error in enhanced scraper check_for_surveys: {e}")
            import traceback
            traceback.print_exc()
            return []
            
    def auto_fill_survey(self, survey_url):
        """Automatically fill a survey for the current account"""
        try:
            # Same fix: use _is_logged_in_cms instead of verify_login
            if not self._is_logged_in_cms():
                print(f"[SURVEY] CMS session invalid, logging in for auto-fill")
                if not self.login(self.account.cms_username, self.account.cms_password):
                    print(f"[SURVEY] Failed to login for auto-fill")
                    return False, "Login failed"
            
            # Call the base class method
            return super().auto_fill_survey(self.account.cms_username, survey_url)
        except Exception as e:
            print(f"[SURVEY] Error in enhanced scraper auto_fill_survey: {e}")
            import traceback
            traceback.print_exc()
            return False, str(e)
    
    def scrape_all_data_async(self):
        """Scrape all data asynchronously"""
        def scrape_background():
            try:
                if self.login(self.account.cms_username, self.account.cms_password):
                    self.account.update_last_login()
                    
                    print(f"Scraping data for account {self.account.cms_username}...")
                    
                    # Scrape profile first to get student name
                    profile_data = self.scrape_profile(self.account.cms_username)
                    
                    # Scrape all other data types
                    self.scrape_timetable(self.account.cms_username)
                    self.scrape_attendance(self.account.cms_username)
                    self.scrape_community_service(self.account.cms_username)
                    self.scrape_gpa_data(self.account.cms_username)
                    
                    print(f"Completed scraping for account {self.account.cms_username}")
                else:
                    print(f"Failed to login for account {self.account.cms_username}")
                    
            except Exception as e:
                print(f"Error in background scraping for account {self.account_id}: {e}")
        
        # Start background thread
        scraping_thread = threading.Thread(target=scrape_background)
        scraping_thread.daemon = True
        scraping_thread.start()
        
        return scraping_thread

class MultiAccountScraperManager:
    """Manager for handling multiple CMS accounts"""
    
    def __init__(self):
        self.active_scrapers = {}
        self._active_sync_status = {}
        self._active_threads = {}
        self._lms_workers = {}
        self._survey_events = {}
        self._lock = threading.Lock()
    
    def get_sync_status(self, account_id: str) -> dict:
        """Get live sync status for an account (thread-safe, memory first, then disk)"""
        from datetime import datetime
        with self._lock:
            status = self._active_sync_status.get(account_id)
            if status:
                # Check TTL (60s) - do not expire if survey modal is pending user action
                expires_at_str = status.get('expires_at')
                if expires_at_str and status.get('is_syncing') and not status.get('survey_required'):
                    try:
                        expires_at = datetime.fromisoformat(expires_at_str)
                        if datetime.now() > expires_at:
                            status['is_syncing'] = False
                            status['status_message'] = 'Sync timed out'
                            status['sub_message'] = 'Initial sync took longer than expected. You can continue.'
                    except Exception:
                        pass
                return dict(status)
        
        # Fallback to persistent storage
        try:
            from app.utils.data_manager import data_manager
            persisted = data_manager.load_account_data(account_id, 'sync_status')
            if persisted:
                expires_at_str = persisted.get('expires_at')
                if expires_at_str and persisted.get('is_syncing') and not persisted.get('survey_required'):
                    try:
                        expires_at = datetime.fromisoformat(expires_at_str)
                        if datetime.now() > expires_at:
                            persisted['is_syncing'] = False
                    except Exception:
                        pass
                return persisted
        except Exception:
            pass
            
        return {
            'is_syncing': False,
            'progress': 0,
            'status_message': 'Idle',
            'completed_steps': [],
            'failed_steps': []
        }

    def _update_sync_status(self, account_id: str, **kwargs):
        """Internal helper to update active status in memory and persist key milestones"""
        from datetime import datetime, timedelta
        from app.utils.data_manager import data_manager
        with self._lock:
            if account_id not in self._active_sync_status:
                self._active_sync_status[account_id] = {}
            self._active_sync_status[account_id].update(kwargs)
            now = datetime.now()
            self._active_sync_status[account_id]['updated_at'] = now.isoformat()
            if self._active_sync_status[account_id].get('is_syncing') and 'expires_at' not in kwargs:
                # If survey required, give 10 minutes TTL; otherwise 60 seconds
                ttl_secs = 600 if self._active_sync_status[account_id].get('survey_required') else 60
                self._active_sync_status[account_id]['expires_at'] = (now + timedelta(seconds=ttl_secs)).isoformat()
            current_copy = dict(self._active_sync_status[account_id])
        
        try:
            data_manager.save_account_data(account_id, 'sync_status', current_copy)
        except Exception as e:
            print(f"Warning: could not save sync_status to disk: {e}")

    def get_scraper(self, account: CMSAccount) -> EnhancedCMSScraper:
        """Get or create scraper for an account"""
        if account.account_id not in self.active_scrapers:
            self.active_scrapers[account.account_id] = EnhancedCMSScraper(account)
        
        return self.active_scrapers[account.account_id]

    def _launch_concurrent_lms_assignments(self, account: CMSAccount, scraper: EnhancedCMSScraper, app_obj=None):
        """Spawn concurrent daemon thread to fetch LMS assignments snapshot while CMS pages are ingested."""
        account_id = account.account_id
        
        with self._lock:
            existing = self._lms_workers.get(account_id)
            if existing and existing.get('thread') and existing['thread'].is_alive():
                return
            
            event = threading.Event()
            self._survey_events[account_id] = event
            worker_info = {
                'done': False,
                'success': False,
                'error': None,
                'survey_blocked': False,
                'thread': None
            }
            self._lms_workers[account_id] = worker_info

        def worker_target():
            def run_job():
                try:
                    is_testing = False
                    try:
                        from flask import has_app_context, current_app
                        if has_app_context() and current_app and current_app.config.get('TESTING'):
                            is_testing = True
                    except Exception:
                        pass

                    if is_testing:
                        worker_info['done'] = True
                        worker_info['success'] = True
                        return

                    from app.services.lms_service import LMSService
                    from app.services.lms_dashboard_service import LMSDashboardService
                    from app.services.lms_scraper_service import CMSSurveyRequiredError

                    while True:
                        try:
                            dashboard_svc = LMSDashboardService(LMSService(provided_cms_session=scraper.session))
                            cache_key = f"pending_assignments:{account_id}"
                            result = dashboard_svc._fetch_and_cache_assignments(account_id, cache_key)

                            if isinstance(result, dict) and result.get('error') and not result.get('all_assignments'):
                                err_str = str(result.get('error'))
                                if 'survey' in err_str.lower():
                                    raise CMSSurveyRequiredError("Mandatory CMS surveys must be completed before LMS can be accessed")
                                print(f"[{account_id}] Concurrent LMS assignments non-fatal error: {result.get('error')}")
                                worker_info['done'] = True
                                worker_info['success'] = False
                                worker_info['error'] = result.get('error')
                            else:
                                worker_info['done'] = True
                                worker_info['success'] = True
                            break

                        except CMSSurveyRequiredError:
                            print(f"[{account_id}] Survey block detected in concurrent LMS worker! Pausing sync.")
                            worker_info['survey_blocked'] = True
                            from datetime import datetime, timedelta
                            now = datetime.now()
                            self._update_sync_status(
                                account_id,
                                survey_required=True,
                                survey_status='detected',
                                status_message='Mandatory CMS Survey Detected',
                                sub_message='University QA survey required before LMS access can proceed',
                                expires_at=(now + timedelta(seconds=600)).isoformat()
                            )
                            # Wait for user resolution on /sync modal
                            event.wait()
                            event.clear()

                            action = worker_info.get('resolution_action')
                            if action == 'skip':
                                print(f"[{account_id}] Survey skipped by user.")
                                worker_info['done'] = True
                                worker_info['success'] = False
                                worker_info['error'] = 'Skipped by user'
                                break
                            elif action == 'resume':
                                print(f"[{account_id}] Resuming LMS assignment fetch after survey autofill...")
                                worker_info['survey_blocked'] = False
                                # Synchronize fresh combined cookies
                                try:
                                    from app.services.lms_session_manager import LMSSessionManager
                                    lms_mgr = LMSSessionManager()
                                    fresh_cookies = lms_mgr.get_session_cookies(account_id, session_type="combined")
                                    if fresh_cookies and isinstance(fresh_cookies, dict):
                                        for k, v in fresh_cookies.items():
                                            scraper.session.cookies.set(k, v)
                                except Exception as ce:
                                    print(f"[{account_id}] Cookie refresh warning after survey: {ce}")
                                continue
                            else:
                                worker_info['done'] = True
                                worker_info['success'] = False
                                break

                        except Exception as e:
                            print(f"[{account_id}] Concurrent LMS assignments exception: {e}")
                            worker_info['done'] = True
                            worker_info['success'] = False
                            worker_info['error'] = str(e)
                            break
                except Exception as fatal_e:
                    print(f"[{account_id}] Fatal error in concurrent LMS worker: {fatal_e}")
                    worker_info['done'] = True
                    worker_info['success'] = False
                    worker_info['error'] = str(fatal_e)

            if app_obj:
                with app_obj.app_context():
                    run_job()
            else:
                run_job()

        t = threading.Thread(target=worker_target)
        t.daemon = True
        worker_info['thread'] = t
        t.start()

    def resolve_survey_for_account(self, account_id: str, action: str, task_id: str = None) -> bool:
        """Resolve pending survey block (action: 'resume' | 'skip') and unpause worker."""
        with self._lock:
            worker_info = self._lms_workers.get(account_id)
            event = self._survey_events.get(account_id)

        if not worker_info or not event:
            if action == 'skip':
                self._update_sync_status(
                    account_id,
                    survey_required=False,
                    survey_status='skipped'
                )
            elif action == 'resume':
                self._update_sync_status(
                    account_id,
                    survey_required=False,
                    survey_status='resumed'
                )
            return True

        worker_info['resolution_action'] = action
        if task_id:
            worker_info['survey_task_id'] = task_id

        if action == 'skip':
            self._update_sync_status(
                account_id,
                survey_required=False,
                survey_status='skipped',
                status_message='LMS survey skipped. Finalizing portal...',
                sub_message='Continuing to dashboard'
            )
            event.set()
            return True
        elif action == 'resume':
            self._update_sync_status(
                account_id,
                survey_required=False,
                survey_status='resumed',
                status_message='Survey completed! Syncing LMS assignments...',
                sub_message='Finalizing academic setup'
            )
            event.set()
            return True

        return False

    def _fetch_and_cache_lms_assignments_step(self, account: CMSAccount, scraper: EnhancedCMSScraper, completed_steps: list, failed_steps: list, step_delay: float = 0.0):
        """Wait for concurrent LMS assignment snapshot worker or fetch directly if not started."""
        import time
        account_id = account.account_id

        self._update_sync_status(
            account_id,
            is_syncing=True,
            completed_steps=list(completed_steps),
            failed_steps=list(failed_steps),
            current_step='assignments',
            progress=95,
            status_message='Syncing LMS assignments...',
            sub_message='Fetching course assignments and upcoming deadlines'
        )

        worker_info = self._lms_workers.get(account_id)
        if worker_info:
            # Concurrent worker was launched! Wait for completion with bounded timeout.
            start_wait = time.time()
            while not worker_info.get('done'):
                if worker_info.get('survey_blocked'):
                    # Wait for user resolution
                    time.sleep(0.3)
                    continue

                if time.time() - start_wait > 12.0:
                    print(f"[{account_id}] Timed out waiting for concurrent LMS assignments worker.")
                    break
                time.sleep(0.1)

            if worker_info.get('success'):
                completed_steps.append('assignments')
            else:
                failed_steps.append('assignments')
        else:
            # Direct synchronous execution fallback
            is_testing = False
            try:
                from flask import has_app_context, current_app
                if has_app_context() and current_app and current_app.config.get('TESTING'):
                    is_testing = True
            except Exception:
                pass

            try:
                if is_testing:
                    completed_steps.append('assignments')
                else:
                    from app.services.lms_service import LMSService
                    from app.services.lms_dashboard_service import LMSDashboardService
                    from app.services.lms_scraper_service import CMSSurveyRequiredError

                    try:
                        dashboard_svc = LMSDashboardService(LMSService(provided_cms_session=scraper.session))
                        cache_key = f"pending_assignments:{account_id}"
                        result = dashboard_svc._fetch_and_cache_assignments(account_id, cache_key)

                        if isinstance(result, dict) and result.get('error') and not result.get('all_assignments'):
                            err_str = str(result.get('error'))
                            if 'survey' in err_str.lower():
                                raise CMSSurveyRequiredError("Mandatory CMS surveys must be completed before LMS can be accessed")
                            print(f"[{account_id}] LMS assignments returned non-fatal error: {result.get('error')}")
                            failed_steps.append('assignments')
                        else:
                            completed_steps.append('assignments')
                    except CMSSurveyRequiredError:
                        self._update_sync_status(
                            account_id,
                            survey_required=True,
                            survey_status='detected',
                            status_message='Mandatory CMS Survey Detected',
                            sub_message='University QA survey required before LMS access can proceed'
                        )
                        failed_steps.append('assignments')
            except Exception as e:
                print(f"[{account_id}] Non-fatal error fetching LMS assignments during sync: {e}")
                failed_steps.append('assignments')

        if step_delay > 0:
            time.sleep(step_delay)

    def ingest_atomic_bundle(self, account: CMSAccount, atomic_bundle: dict, data_types=None, step_delay: float = 0.0, app_obj=None) -> bool:
        """Parse and save atomic sync bundle directly into data manager and session without separate scraper requests."""
        import time
        from bs4 import BeautifulSoup
        from datetime import datetime
        from app.utils.data_manager import data_manager

        if data_types is None:
            data_types = ['profile', 'timetable', 'attendance', 'community_service', 'gpa', 'assignments']

        account_id = account.account_id
        scraper = self.get_scraper(account)
        html_map = atomic_bundle.get('html', {})

        # Ingest cookies into account scraper session & LMS session manager
        cookies_val = atomic_bundle.get('cookies')
        if isinstance(cookies_val, str):
            for cookie_entry in cookies_val.split('|||'):
                key_val = cookie_entry.split(';')[0].strip()
                if '=' in key_val:
                    k, v = key_val.split('=', 1)
                    scraper.session.cookies.set(k, v)
            scraper.save_session(account.cms_username)
        elif isinstance(cookies_val, dict):
            for k, v in cookies_val.items():
                scraper.session.cookies.set(k, v)
            scraper.save_session(account.cms_username)

        # Spawn concurrent LMS assignment fetching immediately in background thread
        if 'assignments' in data_types:
            self._launch_concurrent_lms_assignments(account, scraper, app_obj=app_obj)

        completed_steps = ['auth']
        failed_steps = []
        from app.routes.auth import get_institute_id_for_enrollment
        cms_user = str(getattr(account, 'cms_username', '') or '')
        institute_id = str(getattr(account, 'institute_id', '') or '')
        if not institute_id or (institute_id == '2' and cms_user and not cms_user.startswith('02-')):
            institute_id = get_institute_id_for_enrollment(cms_user, fallback=institute_id or '2')
        is_ipp = institute_id == '5' or cms_user.startswith('05-')

        def save_fallback_profile():
            try:
                fallback_data = {
                    'enrollment': account.cms_username,
                    'name': account.student_name or account.cms_username,
                    'sync_failed': True
                }
                data_manager.save_account_data(account_id, 'profile', fallback_data)
            except Exception as e:
                print(f"Failed to save fallback profile for {account.cms_username}: {e}")

        # 1. Profile
        if 'profile' in data_types:
            self._update_sync_status(
                account_id,
                is_syncing=True,
                completed_steps=list(completed_steps),
                failed_steps=list(failed_steps),
                current_step='profile',
                progress=25,
                status_message='Extracting student profile...',
                sub_message='Loading student details and enrolled program'
            )
            prof_html = html_map.get('profile')
            if prof_html:
                try:
                    soup = BeautifulSoup(prof_html, 'html.parser')
                    profile_data = scraper.parse_profile(soup)
                    if profile_data:
                        scraper.save_data(account.cms_username, 'profile', profile_data)
                        if profile_data.get('name'):
                            if hasattr(account, 'update_student_name'):
                                account.update_student_name(profile_data['name'])
                            else:
                                account.student_name = profile_data['name']
                            # Persist updated student name to parent User object
                            try:
                                from app.models.account import User
                                parent_user = User.get(account.cms_username) or User.get_by_account_id(account.account_id)
                                if parent_user and parent_user.cms_account:
                                    parent_user.cms_account.update_student_name(profile_data['name'])
                                    parent_user.save()
                            except Exception:
                                pass
                        completed_steps.append('profile')
                    else:
                        save_fallback_profile()
                        failed_steps.append('profile')
                except Exception as pe:
                    print(f"Error parsing profile from atomic bundle: {pe}")
                    save_fallback_profile()
                    failed_steps.append('profile')
            else:
                save_fallback_profile()
                failed_steps.append('profile')

            if step_delay > 0:
                time.sleep(step_delay)

        # 2. Timetable
        if 'timetable' in data_types:
            self._update_sync_status(
                account_id,
                is_syncing=True,
                completed_steps=list(completed_steps),
                failed_steps=list(failed_steps),
                current_step='timetable',
                progress=45,
                status_message='Building semester timetable...',
                sub_message='Mapping class schedule, classrooms and lecture times'
            )
            tt_html = html_map.get('timetable')
            if tt_html:
                try:
                    soup = BeautifulSoup(tt_html, 'html.parser')
                    tt_data = scraper.parse_timetable(soup)
                    if tt_data:
                        scraper.save_data(account.cms_username, 'timetable', tt_data)
                        completed_steps.append('timetable')
                    else:
                        failed_steps.append('timetable')
                except Exception as te:
                    print(f"Error parsing timetable from atomic bundle: {te}")
                    failed_steps.append('timetable')
            else:
                failed_steps.append('timetable')

            if step_delay > 0:
                time.sleep(step_delay)

        # 3. Attendance
        if 'attendance' in data_types:
            self._update_sync_status(
                account_id,
                is_syncing=True,
                completed_steps=list(completed_steps),
                failed_steps=list(failed_steps),
                current_step='attendance',
                progress=65,
                status_message='Syncing attendance records...',
                sub_message='Analyzing course attendance matrices and absences'
            )
            att_html = html_map.get('attendance')
            if att_html:
                try:
                    soup = BeautifulSoup(att_html, 'html.parser')
                    att_data = scraper.parse_attendance(soup)
                    if att_data:
                        scraper.save_data(account.cms_username, 'attendance', att_data)
                        completed_steps.append('attendance')
                    else:
                        failed_steps.append('attendance')
                except Exception as ae:
                    print(f"Error parsing attendance from atomic bundle: {ae}")
                    failed_steps.append('attendance')
            else:
                failed_steps.append('attendance')

            if step_delay > 0:
                time.sleep(step_delay)

        # 4. Community Service
        if 'community_service' in data_types:
            self._update_sync_status(
                account_id,
                is_syncing=True,
                completed_steps=list(completed_steps),
                failed_steps=list(failed_steps),
                current_step='community_service',
                progress=80,
                status_message='Checking community service records...',
                sub_message='Not applicable for IPP campus' if is_ipp else 'Fetching verified CSP volunteer hours'
            )
            if is_ipp:
                cs_data = {
                    'not_applicable': True,
                    'total_hours': 0,
                    'total_projects': 0,
                    'projects': [],
                    'completed_hours': 0,
                    'completed_activities': [],
                    'scraped_at': datetime.now().isoformat()
                }
                scraper.save_data(account.cms_username, 'community_service', cs_data)
                completed_steps.append('community_service')
            else:
                cs_html = html_map.get('community_service')
                if cs_html:
                    try:
                        soup = BeautifulSoup(cs_html, 'html.parser')
                        cs_data = scraper.parse_community_service(soup)
                        if cs_data:
                            scraper.save_data(account.cms_username, 'community_service', cs_data)
                            completed_steps.append('community_service')
                        else:
                            failed_steps.append('community_service')
                    except Exception as ce:
                        print(f"Error parsing community service from atomic bundle: {ce}")
                        failed_steps.append('community_service')
                else:
                    failed_steps.append('community_service')

            if step_delay > 0:
                time.sleep(step_delay)

        # 5. GPA
        if 'gpa' in data_types:
            self._update_sync_status(
                account_id,
                is_syncing=True,
                completed_steps=list(completed_steps),
                failed_steps=list(failed_steps),
                current_step='gpa',
                progress=80,
                status_message='Loading GPA & examination records...',
                sub_message='Processing academic standing and cumulative CGPA'
            )
            gpa_html = html_map.get('gpa')
            if gpa_html:
                try:
                    soup = BeautifulSoup(gpa_html, 'html.parser')
                    gpa_data = scraper.parse_gpa_data(soup)
                    if gpa_data:
                        scraper.save_data(account.cms_username, 'gpa', gpa_data)
                        completed_steps.append('gpa')
                    else:
                        failed_steps.append('gpa')
                except Exception as ge:
                    print(f"Error parsing GPA from atomic bundle: {ge}")
                    failed_steps.append('gpa')
            else:
                failed_steps.append('gpa')

            if step_delay > 0:
                time.sleep(step_delay)

        # 6. LMS Assignments
        if 'assignments' in data_types:
            self._fetch_and_cache_lms_assignments_step(account, scraper, completed_steps, failed_steps, step_delay=step_delay)

        account.update_last_login()
        is_success = 'auth' in completed_steps and ('profile' in completed_steps or 'profile' not in data_types)
        self._update_sync_status(
            account_id,
            is_syncing=False,
            progress=100 if is_success else 0,
            current_step='completed' if is_success else 'failed',
            completed_steps=list(completed_steps),
            failed_steps=list(failed_steps),
            status_message='All systems ready!' if is_success else 'Sync incomplete',
            sub_message='Launching your dashboard...' if is_success else 'Could not process academic profile',
            completed_at=datetime.now().isoformat()
        )
        return is_success

    def start_staged_atomic_ingestion(self, account: CMSAccount, atomic_bundle: dict, data_types=None, step_delay: float = None) -> threading.Thread:
        """Run staged atomic bundle ingestion in background thread for new user onboarding stepper."""
        from datetime import datetime, timedelta
        if step_delay is None:
            try:
                from flask import current_app
                if current_app and current_app.config.get('TESTING'):
                    step_delay = 0.05
                else:
                    step_delay = 0.65
            except Exception:
                step_delay = 0.65
        account_id = account.account_id
        with self._lock:
            existing_thread = self._active_threads.get(account_id)
            if existing_thread and existing_thread.is_alive():
                print(f"[{account_id}] Staged ingestion thread already active, deduplicating.")
                return existing_thread

        now = datetime.now()
        # Initialize sync_status immediately before returning to caller
        self._update_sync_status(
            account_id,
            is_syncing=True,
            progress=15,
            current_step='profile',
            steps=['auth', 'profile', 'timetable', 'attendance', 'community_service', 'gpa', 'assignments'],
            completed_steps=['auth'],
            failed_steps=[],
            status_message='Bahria CMS Gateway verified',
            sub_message='Extracting academic records',
            started_at=now.isoformat(),
            expires_at=(now + timedelta(seconds=90)).isoformat()
        )

        app_obj = None
        try:
            from flask import current_app
            if current_app:
                app_obj = current_app._get_current_object()
        except Exception:
            pass

        def run_staged():
            try:
                if app_obj:
                    with app_obj.app_context():
                        self.ingest_atomic_bundle(account, atomic_bundle, data_types=data_types, step_delay=step_delay, app_obj=app_obj)
                else:
                    self.ingest_atomic_bundle(account, atomic_bundle, data_types=data_types, step_delay=step_delay, app_obj=app_obj)
            except Exception as e:
                print(f"Error during staged atomic ingestion for {account_id}: {e}")
                self._update_sync_status(
                    account_id,
                    is_syncing=False,
                    status_message='Sync incomplete',
                    sub_message='An error occurred while processing portal data.'
                )
            finally:
                with self._lock:
                    self._active_threads.pop(account_id, None)

        thread = threading.Thread(target=run_staged)
        thread.daemon = True
        with self._lock:
            self._active_threads[account_id] = thread
        thread.start()
        return thread

    def scrape_account_data(self, account: CMSAccount, data_types=None):
        """Scrape data for a specific account with granular progress reporting"""
        from datetime import datetime, timedelta
        if data_types is None:
            data_types = ['profile', 'timetable', 'attendance', 'community_service', 'gpa', 'assignments']
        
        account_id = account.account_id
        with self._lock:
            existing_thread = self._active_threads.get(account_id)
            if existing_thread and existing_thread.is_alive():
                print(f"[{account_id}] Sync thread already active, deduplicating.")
                return existing_thread

        scraper = self.get_scraper(account)
        now = datetime.now()
        self._update_sync_status(
            account_id,
            is_syncing=True,
            progress=10,
            current_step='auth',
            steps=['auth', 'profile', 'timetable', 'attendance', 'community_service', 'gpa', 'assignments'],
            completed_steps=[],
            failed_steps=[],
            status_message='Connecting to university CMS...',
            sub_message='Verifying student credentials',
            started_at=now.isoformat(),
            expires_at=(now + timedelta(seconds=120)).isoformat()
        )
        
        app_obj = None
        try:
            from flask import current_app
            if current_app:
                app_obj = current_app._get_current_object()
        except Exception:
            pass

        def scrape_background():
            from app.utils.data_manager import data_manager
            completed_steps = []
            failed_steps = []
            from app.routes.auth import get_institute_id_for_enrollment
            cms_user = str(getattr(account, 'cms_username', '') or '')
            inst_id = str(getattr(account, 'institute_id', '') or '')
            if not inst_id or (inst_id == '2' and cms_user and not cms_user.startswith('02-')):
                inst_id = get_institute_id_for_enrollment(cms_user, fallback=inst_id or '2')
            is_ipp = inst_id == '5' or cms_user.startswith('05-')
            
            def save_fallback_profile():
                try:
                    fallback_data = {
                        'enrollment': account.cms_username,
                        'name': account.student_name or account.cms_username,
                        'sync_failed': True
                    }
                    data_manager.save_account_data(account_id, 'profile', fallback_data)
                    print(f"Saved fallback profile for {account.cms_username} to prevent infinite syncing loop.")
                except Exception as e:
                    print(f"Failed to save fallback profile for {account.cms_username}: {e}")

            try:
                use_atomic = bool(getattr(account, 'cms_password', None))
                atomic_success = False

                if use_atomic:
                    print(f"[{account_id}] Initiating atomic full_sync edge macro for {account.cms_username}...")
                    atomic_bundle = scraper.scrape_full_sync_atomic(
                        account.cms_username,
                        account.cms_password,
                        inst_id
                    )

                    if atomic_bundle and atomic_bundle.get('cms_down'):
                        print(f"[{account_id}] Atomic sync reported CMS downtime.")
                        failed_steps.append('auth')
                        if 'profile' in data_types:
                            save_fallback_profile()
                        self._update_sync_status(
                            account_id,
                            is_syncing=False,
                            failed_steps=list(failed_steps),
                            status_message='University CMS Server Down',
                            sub_message='Bahria CMS returned HTTP 500 Runtime Error. Not an LMS+ issue.',
                            is_cms_down=True
                        )
                        return

                    if atomic_bundle and atomic_bundle.get('auth_failed'):
                        print(f"[{account_id}] Atomic sync reported authentication failure.")
                        failed_steps.append('auth')
                        if 'profile' in data_types:
                            save_fallback_profile()
                        self._update_sync_status(
                            account_id,
                            failed_steps=list(failed_steps),
                            status_message='Failed to connect to CMS',
                            sub_message='Could not authenticate with university server'
                        )
                        return

                    if atomic_bundle and atomic_bundle.get('success') and atomic_bundle.get('html'):
                        atomic_success = self.ingest_atomic_bundle(account, atomic_bundle, data_types, step_delay=0.65, app_obj=app_obj)
                        if atomic_success:
                            print(f"[{account_id}] Atomic edge initial sync successfully parsed and stored for {account.cms_username}!")
                            return

                if not atomic_success and 'auth' not in failed_steps:
                    print(f"[{account_id}] Executing standard step-by-step CMS scraping fallback...")
                    if scraper.login(account.cms_username, account.cms_password):
                        account.update_last_login()
                        completed_steps.append('auth')
                        if 'assignments' in data_types:
                            self._launch_concurrent_lms_assignments(account, scraper, app_obj=app_obj)
                        self._update_sync_status(
                            account_id,
                            completed_steps=list(completed_steps),
                            current_step='profile',
                            progress=25,
                            status_message='Extracting student profile...',
                            sub_message='Loading student details and enrolled program'
                        )
                        
                        # 1. Profile
                        if 'profile' in data_types:
                            try:
                                print(f"Scraping profile for {account.cms_username}")
                                profile_data = scraper.scrape_profile(account.cms_username)
                                if profile_data:
                                    scraper.save_data(account.cms_username, 'profile', profile_data)
                                    completed_steps.append('profile')
                                else:
                                    save_fallback_profile()
                                    failed_steps.append('profile')
                            except Exception as pe:
                                print(f"Error scraping profile for {account.cms_username}: {pe}")
                                save_fallback_profile()
                                failed_steps.append('profile')
                        
                        # 2. Timetable
                        if 'timetable' in data_types:
                            self._update_sync_status(
                                account_id,
                                completed_steps=list(completed_steps),
                                failed_steps=list(failed_steps),
                                current_step='timetable',
                                progress=45,
                                status_message='Building semester timetable...',
                                sub_message='Mapping class schedule, classrooms and lecture times'
                            )
                            try:
                                print(f"Scraping timetable for {account.cms_username}")
                                tt_data = scraper.scrape_timetable(account.cms_username)
                                if tt_data:
                                    completed_steps.append('timetable')
                                else:
                                    failed_steps.append('timetable')
                            except Exception as te:
                                print(f"Error scraping timetable for {account.cms_username}: {te}")
                                failed_steps.append('timetable')
                        
                        # 3. Attendance
                        if 'attendance' in data_types:
                            self._update_sync_status(
                                account_id,
                                completed_steps=list(completed_steps),
                                failed_steps=list(failed_steps),
                                current_step='attendance',
                                progress=65,
                                status_message='Syncing attendance records...',
                                sub_message='Analyzing course attendance matrices and absences'
                            )
                            try:
                                print(f"Scraping attendance for {account.cms_username}")
                                att_data = scraper.scrape_attendance(account.cms_username)
                                if att_data:
                                    completed_steps.append('attendance')
                                else:
                                    failed_steps.append('attendance')
                            except Exception as ae:
                                print(f"Error scraping attendance for {account.cms_username}: {ae}")
                                failed_steps.append('attendance')
                        
                        # 4. Community Service
                        if 'community_service' in data_types:
                            self._update_sync_status(
                                account_id,
                                completed_steps=list(completed_steps),
                                failed_steps=list(failed_steps),
                                current_step='community_service',
                                progress=80,
                                status_message='Checking community service records...',
                                sub_message='Not applicable for IPP campus' if is_ipp else 'Fetching verified CSP volunteer hours'
                            )
                            if is_ipp:
                                cs_data = {
                                    'not_applicable': True,
                                    'total_hours': 0,
                                    'total_projects': 0,
                                    'projects': [],
                                    'completed_hours': 0,
                                    'completed_activities': [],
                                    'scraped_at': datetime.now().isoformat()
                                }
                                scraper.save_data(account.cms_username, 'community_service', cs_data)
                                completed_steps.append('community_service')
                            else:
                                try:
                                    print(f"Scraping community service for {account.cms_username}")
                                    cs_data = scraper.scrape_community_service(account.cms_username)
                                    if cs_data:
                                        completed_steps.append('community_service')
                                    else:
                                        failed_steps.append('community_service')
                                except Exception as ce:
                                    print(f"Error scraping community service for {account.cms_username}: {ce}")
                                    failed_steps.append('community_service')
                        
                        # 5. GPA
                        if 'gpa' in data_types:
                            self._update_sync_status(
                                account_id,
                                completed_steps=list(completed_steps),
                                failed_steps=list(failed_steps),
                                current_step='gpa',
                                progress=80,
                                status_message='Loading GPA & examination records...',
                                sub_message='Processing academic standing and cumulative CGPA'
                            )
                            try:
                                print(f"Scraping GPA for {account.cms_username}")
                                gpa_data = scraper.scrape_gpa_data(account.cms_username)
                                if gpa_data:
                                    completed_steps.append('gpa')
                                else:
                                    failed_steps.append('gpa')
                            except Exception as ge:
                                print(f"Error scraping GPA for {account.cms_username}: {ge}")
                                failed_steps.append('gpa')

                        # 6. LMS Assignments
                        if 'assignments' in data_types:
                            self._fetch_and_cache_lms_assignments_step(account, scraper, completed_steps, failed_steps, step_delay=0.65)
                        
                        print(f"Completed scraping for account {account.cms_username}")
                    else:
                        print(f"Failed to login for account {account.cms_username}")
                        failed_steps.append('auth')
                        if 'profile' in data_types:
                            save_fallback_profile()
                        is_cms_down = getattr(scraper, 'cms_down', False)
                        self._update_sync_status(
                            account_id,
                            is_syncing=False,
                            failed_steps=list(failed_steps),
                            status_message='University CMS Server Down' if is_cms_down else 'Failed to connect to CMS',
                            sub_message='Bahria CMS returned HTTP 500 Runtime Error. Not an LMS+ issue.' if is_cms_down else 'Could not authenticate with university server',
                            is_cms_down=is_cms_down
                        )
            except Exception as e:
                print(f"Error scraping account {account_id}: {e}")
                if 'profile' in data_types and 'profile' not in completed_steps:
                    save_fallback_profile()
            finally:
                with self._lock:
                    self._active_threads.pop(account_id, None)
                if not atomic_success:
                    is_cms_down = getattr(scraper, 'cms_down', False) or bool(atomic_bundle and atomic_bundle.get('cms_down'))
                    is_success = 'auth' in completed_steps and 'auth' not in failed_steps
                    if is_cms_down and 'auth' in failed_steps:
                        final_status_msg = 'University CMS Server Down'
                        final_sub_msg = 'Bahria CMS returned HTTP 500 Runtime Error. Not an LMS+ issue.'
                    elif is_success:
                        final_status_msg = 'All systems ready!'
                        final_sub_msg = 'Launching your dashboard...'
                    elif 'auth' in failed_steps:
                        final_status_msg = 'Failed to connect to CMS'
                        final_sub_msg = 'Could not authenticate with university server'
                    else:
                        final_status_msg = 'Sync incomplete'
                        final_sub_msg = 'Some records could not be retrieved'
                    self._update_sync_status(
                        account_id,
                        is_syncing=False,
                        progress=100 if is_success else 0,
                        current_step='completed' if is_success else 'failed',
                        completed_steps=list(completed_steps),
                        failed_steps=list(failed_steps),
                        status_message=final_status_msg,
                        sub_message=final_sub_msg,
                        is_cms_down=bool(is_cms_down and 'auth' in failed_steps),
                        completed_at=datetime.now().isoformat()
                    )
                    print(f"Finalized sync_status for {account.cms_username}")
        
        # Start background thread
        thread = threading.Thread(target=scrape_background)
        thread.daemon = True
        with self._lock:
            self._active_threads[account_id] = thread
        thread.start()
        return thread
    
    def scrape_all_accounts(self, accounts: list, data_types=None):
        """Scrape data for multiple accounts"""
        threads = []
        
        for account in accounts:
            thread = self.scrape_account_data(account, data_types)
            threads.append(thread)
        
        return threads
    
    def cleanup_scraper(self, account_id: str):
        """Clean up scraper for an account"""
        if account_id in self.active_scrapers:
            del self.active_scrapers[account_id]

# Global scraper manager
scraper_manager = MultiAccountScraperManager()