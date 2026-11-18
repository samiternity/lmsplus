# app/services/lms_scraper_service.py
import requests
from bs4 import BeautifulSoup
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any, Tuple
import logging
import re
import os
import base64
from urllib.parse import urljoin, urlparse, unquote, urlencode, quote
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from app.utils.proxy_transport import macro_endpoints, proxy_request
from app.models.lms_models import LMSAssignment, LMSLectureNote, LMSQuiz
from app.services.lms_auth_service import LMSAuthService
from app.utils.lms_error_handler import handle_lms_error, create_error_context
from app.utils.lms_logger import log_error, log_warning, log_info, log_debug


# Configure logging
logger = logging.getLogger(__name__)

class LMSScrapingError(Exception):
    """Exception raised when LMS scraping operations fail"""
    pass

class LMSParsingError(Exception):
    """Exception raised when HTML parsing fails"""
    pass

class LMSAuthenticationRequired(Exception):
    """The strict connector request needs a refreshed university session."""
    pass

class LMSNetworkError(Exception):
    """Exception raised when network operations fail"""
    pass

class CMSSurveyRequiredError(Exception):
    """Exception raised when mandatory CMS surveys block LMS access"""
    def __init__(self, message="Mandatory CMS surveys must be completed before LMS can be accessed", surveys=None):
        super().__init__(message)
        self.surveys = surveys or []

class LMSScraperService:
    """
    Service for scraping data from Bahria University's LMS.
    Handles HTTP requests, HTML parsing, and data extraction.
    """
    
    def __init__(self, provided_cms_session=None):
        self.lms_base_url = "https://lms.bahria.edu.pk"
        self.assignments_url = f"{self.lms_base_url}/Student/Assignments.php"
        self.lecture_notes_url = f"{self.lms_base_url}/Student/LectureNotes.php"
        self.quizzes_url = f"{self.lms_base_url}/Student/Quizzes.php"

        # Initialize auth service for session management
        self.auth_service = LMSAuthService()
        self.provided_cms_session = provided_cms_session
        
        # Request configuration
        self.request_config = {
            'timeout': 15,
            'headers': {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
                'Accept-Language': 'en-US,en;q=0.5',
                'Accept-Encoding': 'gzip, deflate',
                'Connection': 'keep-alive',
                'Upgrade-Insecure-Requests': '1'
            }
        }
        
        # Retry configuration
        self.max_retries = 2
        self.retry_delay = 1  # seconds

        # Semaphore: max 3 concurrent Worker requests to avoid CF 1015 rate limit
        self._worker_semaphore = threading.Semaphore(3)
    
    def _monitored_request(self, session, method, url, **kwargs):
        """
        Wrapper for HTTP requests that monitors performance.
        Records response time for LMS signal widget.
        
        Args:
            session: requests.Session object
            method: HTTP method ('get' or 'post')
            url: URL to request
            **kwargs: Additional arguments for the request
            
        Returns:
            Response object
        """
        import time
        from app.utils.lms_monitor import LMSMonitor
        from app.utils.proxy_manager import log_proxy_telemetry
        
        start_time = time.perf_counter()
        try:
            if method.lower() == 'post':
                response = proxy_request(session.post, url, **kwargs)
            else:
                response = proxy_request(session.get, url, **kwargs)
            
            latency_ms = (time.perf_counter() - start_time) * 1000
            LMSMonitor.record_request(latency_ms, success=True)
            
            worker_url = getattr(session, 'worker_url', None)
            if worker_url and url.startswith(worker_url):
                log_proxy_telemetry(worker_url, url, latency_ms, response.status_code)
                
            return response
        except Exception as e:
            latency_ms = (time.perf_counter() - start_time) * 1000
            LMSMonitor.record_request(latency_ms, success=False, error_message=str(e)[:100])
            
            worker_url = getattr(session, 'worker_url', None)
            if worker_url and url.startswith(worker_url):
                log_proxy_telemetry(worker_url, url, latency_ms, 500)
                
            raise
    
    def _proxy(self, url: str, session: 'requests.Session' = None, use_native_redirects: bool = False) -> str:
        """Prepend the proxy prefix to a URL.
        
        The Worker reads the `url` query param and forwards the request to that URL.
        The entire target URL (including its own query string) must be percent-encoded
        so the Worker treats it as a single value — otherwise any `&` in the target
        URL's query string would be parsed as additional Worker-level params and lost.
        """
        worker_url = getattr(session, 'worker_url', None) if session else None
        if not worker_url:
            from app.utils.proxy_manager import get_proxy_manager
            worker_url = get_proxy_manager().get_worker("unknown")

        if url.startswith(worker_url):
            proxy_url = url
        else:
            from urllib.parse import quote
            proxy_url = f"{worker_url}{quote(url, safe='')}"
            
        if use_native_redirects:
            # If the original url didn't already have native_redirects, append it to the CF query string
            if "&native_redirects=true" not in proxy_url:
                proxy_url += "&native_redirects=true"
                
        return proxy_url

    # Per-account session cache: account_id -> requests.Session
    # Avoids re-authenticating on every per-course scrape call within the same process.
    _session_cache: Dict[str, 'requests.Session'] = {}
    _session_cache_lock = threading.Lock()

    def _get_or_create_session(self, account_id: str) -> 'requests.Session':
        """
        Return a cached authenticated session for the account if one exists,
        otherwise create a new one and cache it.
        This prevents N re-authentications when scraping N courses in parallel.
        """
        with self._session_cache_lock:
            session = self._session_cache.get(account_id)
        if session is not None:
            return session
        session = self._create_authenticated_session(account_id)
        with self._session_cache_lock:
            self._session_cache[account_id] = session
        return session

    def invalidate_session_cache(self, account_id: str):
        """Remove a cached session (call after logout or auth failure)."""
        with self._session_cache_lock:
            self._session_cache.pop(account_id, None)

    def _create_authenticated_session(self, account_id: str, retry_count: int = 0) -> requests.Session:
        """
        Create an authenticated session for the given account.
        Performs CMS login and gateway access to establish LMS session.
        
        Args:
            account_id: Account ID to create session for
            retry_count: Current retry attempt (for exponential backoff)
            
        Returns:
            Authenticated requests session
            
        Raises:
            LMSNetworkError: When session creation fails
        """
        max_retries = 3
        base_delay = 1.0  # Base delay in seconds
        
        try:
            log_info(f"Creating authenticated session for account {account_id} (attempt {retry_count + 1})",
                    account_id=account_id, operation="create_session", retry_count=retry_count)
            
            # Check if we have a valid cached session first
            cached_session = self._try_cached_session(account_id)
            if cached_session:
                log_info(f"Using cached session for account {account_id}",
                        account_id=account_id, operation="create_session")
                return cached_session
            
            # ── Resolve the CMS account WITHOUT relying on current_user ──────────────
            # Background/scheduler runs have no HTTP request context.
            # current_user is AnonymousUser there, so we must load the account directly.
            cms_account = None

            # 1) Try to find the user who owns this account_id via direct lookup
            try:
                from app.models.account import User
                user = User.get_by_account_id(account_id)
                if user and user.cms_account:
                    cms_account = user.cms_account
            except Exception as e:
                log_warning(f"Background user lookup failed: {e}", account_id=account_id, operation="create_session")

            # 2) Fall back to the active Flask session user (interactive / test calls)
            if cms_account is None:
                try:
                    from flask_login import current_user
                    if current_user.is_authenticated:
                        if current_user.account_id == account_id and current_user.cms_account:
                            cms_account = current_user.cms_account
                except Exception:
                    pass

            if cms_account is None:
                log_error(f"CMS account {account_id} not found in any context",
                         account_id=account_id, operation="create_session", error_type="account_not_found")
                raise LMSNetworkError(f"CMS account {account_id} not found")

            # Get CMS credentials
            cms_username = cms_account.cms_username
            cms_password = cms_account.cms_password
            
            if cms_username:
                from app.utils.lms_logger import register_account_enrollment
                register_account_enrollment(account_id, cms_username)
            
            if not cms_username or not cms_password:
                log_error(f"CMS credentials not available for account {account_id}",
                         account_id=account_id, operation="create_session", error_type="no_credentials")
                raise LMSNetworkError("CMS credentials not available")
            
            # Step 1: Try Fast Atomic Edge Transition (1-shot Login + GoToLMS)
            # This completely eliminates the IP mismatch session drop between CMS Login and GoToLMS
            atomic_gateway_success = False
            session = requests.Session()
            session.account_id = account_id
            session.headers.update(self.request_config['headers'])
            
            try:
                from app.utils.proxy_manager import get_proxy_manager
                session.worker_url = get_proxy_manager().get_worker(account_id)
                # Prioritize Cloudflare Worker Macro (Tier 1), Fallback to Supabase Macro (Tier 2)
                timeout = self.request_config['timeout'] * 2
                endpoints = macro_endpoints(timeout, timeout)
                
                from app.routes.auth import get_institute_id_for_enrollment
                target_inst = get_institute_id_for_enrollment(cms_username, fallback=getattr(cms_account, 'institute_id', '2'))
                payload = {
                    "action": "lms_gateway",
                    "username": cms_username,
                    "password": cms_password,
                    "institute_id": str(target_inst)
                }

                for tier_name, macro_url, timeout in endpoints:
                    try:
                        log_debug(f"Attempting atomic CMS->LMS gateway macro via {tier_name} for account {account_id}",
                                 account_id=account_id, operation="create_session")
                        macro_res = proxy_request(session.post, macro_url, json=payload, timeout=timeout)
                        if macro_res.status_code == 409 or macro_res.headers.get('X-CMS-Survey-Blocked') == 'true':
                            log_warning(f"Atomic LMS gateway ({tier_name}) detected pending CMS surveys for account {account_id}",
                                        account_id=account_id, operation="create_session")
                            raise CMSSurveyRequiredError("Mandatory CMS surveys must be completed before LMS can be accessed")

                        if macro_res.status_code == 200:
                            self._process_cf_proxy_headers(session, macro_res, account_id)
                            actual_url = self._get_actual_url(macro_res)
                            if 'qualityassurance' in actual_url.lower() or 'survey' in actual_url.lower() or 'surveys (pending)' in macro_res.text.lower():
                                log_warning(f"Atomic LMS gateway landed on survey page for account {account_id}: {actual_url}",
                                            account_id=account_id, operation="create_session")
                                raise CMSSurveyRequiredError("Mandatory CMS surveys must be completed before LMS can be accessed")
                            login_indicators = ['login.aspx', 'login.php', '/login', 'logins/']
                            if not any(indicator in actual_url.lower() for indicator in login_indicators):
                                log_info(f"Atomic LMS gateway session established via {tier_name}. Final URL: {actual_url}",
                                         account_id=account_id, operation="create_session")
                                atomic_gateway_success = True
                                break
                    except CMSSurveyRequiredError:
                        raise
                    except Exception as tier_err:
                        log_warning(f"Atomic LMS gateway {tier_name} failed: {tier_err}",
                                    account_id=account_id, operation="create_session")
                        continue
            except CMSSurveyRequiredError:
                raise
            except Exception as me:
                log_warning(f"Atomic LMS gateway attempts skipped/failed: {me}",
                            account_id=account_id, operation="create_session")

            if not atomic_gateway_success:
                # ── Fallback Flow: Legacy Re-use or Manual Login + Gateway ──────────
                if getattr(self, 'provided_cms_session', None):
                    session = self.provided_cms_session
                    self.provided_cms_session = None
                    session.account_id = account_id
                    if not getattr(session, 'worker_url', None):
                        from app.utils.proxy_manager import get_proxy_manager
                        session.worker_url = get_proxy_manager().get_worker(account_id)
                    session.headers.update(self.request_config['headers'])
                    cms_login_success = True
                    log_info(f"Re-using provided CMS session for account {account_id}",
                             account_id=account_id, operation="create_session")
                else:
                    log_debug(f"Performing CMS login for account {account_id}",
                             account_id=account_id, operation="create_session")
                    cms_login_success = False
                    for cms_attempt in range(self.max_retries):
                        try:
                            from app.utils.proxy_manager import get_proxy_manager
                            session.worker_url = get_proxy_manager().get_worker(account_id)
                            log_info(f"Assigned proxy worker: {session.worker_url} (Attempt {cms_attempt + 1})", account_id=account_id, operation="create_session")
                            from app.routes.auth import get_institute_id_for_enrollment
                            inst_id = get_institute_id_for_enrollment(cms_username, fallback=getattr(cms_account, 'institute_id', '2'))
                            if self._perform_cms_login(session, cms_username, cms_password, institute_id=inst_id):
                                cms_login_success = True
                                break
                            else:
                                if cms_attempt < self.max_retries - 1:
                                    delay = base_delay * (2 ** cms_attempt)
                                    log_warning(f"CMS login attempt {cms_attempt + 1} failed, retrying in {delay}s",
                                               account_id=account_id, operation="create_session")
                                    import time
                                    time.sleep(delay)
                        except Exception as e:
                            if cms_attempt < self.max_retries - 1:
                                delay = base_delay * (2 ** cms_attempt)
                                log_warning(f"CMS login attempt {cms_attempt + 1} error: {e}, retrying in {delay}s",
                                           account_id=account_id, operation="create_session")
                                import time
                                time.sleep(delay)
                            else:
                                raise
                    
                    if not cms_login_success:
                        log_error(f"CMS login failed after {self.max_retries} attempts for account {account_id}",
                                 account_id=account_id, operation="create_session", error_type="cms_login_failed")
                        raise LMSNetworkError("CMS login failed after multiple attempts")

                # Step 2: Access LMS gateway with retry logic (for fallback)
                log_debug(f"Accessing LMS gateway for account {account_id}",
                         account_id=account_id, operation="create_session")
                
                gateway_success = False
                for gateway_attempt in range(self.max_retries):
                    try:
                        if self._access_lms_gateway(session, account_id):
                            gateway_success = True
                            break
                        else:
                            if gateway_attempt < self.max_retries - 1:
                                delay = base_delay * (2 ** gateway_attempt)
                                log_warning(f"Gateway access attempt {gateway_attempt + 1} failed, retrying in {delay}s",
                                           account_id=account_id, operation="create_session")
                                import time
                                time.sleep(delay)
                    except Exception as e:
                        if gateway_attempt < self.max_retries - 1:
                            delay = base_delay * (2 ** gateway_attempt)
                            log_warning(f"Gateway access attempt {gateway_attempt + 1} error: {e}, retrying in {delay}s",
                                       account_id=account_id, operation="create_session")
                            import time
                            time.sleep(delay)
                        else:
                            raise
                
                if not gateway_success:
                    log_error(f"LMS gateway access failed after {self.max_retries} attempts for account {account_id}",
                             account_id=account_id, operation="create_session", error_type="gateway_failed")
                    raise LMSNetworkError("LMS gateway access failed after multiple attempts")
            
            # Step 3: Store session cookies for future use
            self._store_session_cookies(account_id, session)
            
            log_info(f"Successfully created authenticated session for account {account_id}",
                    account_id=account_id, operation="create_session")
            return session
            
        except (LMSNetworkError, CMSSurveyRequiredError):
            # Re-raise known errors immediately without retrying
            raise
        except Exception as e:
            # Implement exponential backoff for retries
            if retry_count < max_retries:
                delay = base_delay * (2 ** retry_count)
                log_warning(f"Session creation attempt {retry_count + 1} failed for account {account_id}: {e}, retrying in {delay}s",
                           account_id=account_id, operation="create_session", retry_count=retry_count, exc_info=True)
                import time
                time.sleep(delay)
                return self._create_authenticated_session(account_id, retry_count + 1)
            else:
                log_error(f"Failed to create authenticated session for account {account_id} after {max_retries} attempts: {e}",
                         account_id=account_id, operation="create_session", error_type="system_error", exc_info=True)
                raise LMSNetworkError(f"Session creation failed after {max_retries} attempts: {e}")
    
    def _perform_cms_login(self, session: requests.Session, username: str, password: str, institute_id: str = None) -> bool:
        """
        Perform CMS login with form token extraction.
        
        Steps:
        1. GET login page to extract __VIEWSTATE, __VIEWSTATEGENERATOR, __EVENTVALIDATION
        2. POST login with all required form fields
        3. Validate successful authentication
        
        Args:
            session: Requests session to use for login
            username: CMS username
            password: CMS password
            
        Returns:
            True if login successful, False otherwise
        """
        try:
            cms_login_url = "https://cms.bahria.edu.pk/Logins/Student/Login.aspx"
            
            log_debug("Fetching CMS login page to extract form tokens", operation="cms_login")
            
            # Step 1: GET login page via Worker proxy to avoid GCP IP block
            try:
                login_page_response = self._monitored_request(
                    session, 'get',
                    self._proxy(cms_login_url, session=session, use_native_redirects=True),
                    timeout=self.request_config['timeout'],
                    allow_redirects=False
                )
                self._process_cf_proxy_headers(session, login_page_response, account_id="login_get")
                
                # Follow redirects manually for fallback mode
                while login_page_response.is_redirect:
                    location = login_page_response.headers.get('Location', '')
                    target = urljoin(cms_login_url, location)
                    login_page_response = self._monitored_request(
                        session, 'get',
                        self._proxy(target, session=session, use_native_redirects=True),
                        timeout=self.request_config['timeout'],
                        allow_redirects=False
                    )
                
                login_page_response.raise_for_status()
            except requests.RequestException as e:
                # If we get a proxy error or WAF block, mark the proxy dead
                if 'login_page_response' in locals() and getattr(login_page_response, 'status_code', None) in [403, 500, 502, 503, 504]:
                    if getattr(session, 'worker_url', None):
                        log_warning(f"Proxy error {login_page_response.status_code} detected! Marking dead: {session.worker_url}", operation="cms_login")
                        from app.utils.proxy_manager import get_proxy_manager
                        get_proxy_manager().mark_dead(session.worker_url)
                
                log_error(f"Failed to fetch CMS login page: {e}",
                         operation="cms_login", error_type="network_error", exc_info=True)
                return False
            
            # Step 2: Parse login page and extract form tokens
            log_debug("Parsing CMS login page for form tokens", operation="cms_login")
            soup = BeautifulSoup(login_page_response.text, 'html.parser')
            
            # Extract ASP.NET form tokens
            viewstate = soup.find('input', {'name': '__VIEWSTATE'})
            viewstate_generator = soup.find('input', {'name': '__VIEWSTATEGENERATOR'})
            event_validation = soup.find('input', {'name': '__EVENTVALIDATION'})
            
            if not viewstate or not viewstate_generator or not event_validation:
                log_error("Failed to extract required form tokens from CMS login page",
                         operation="cms_login", error_type="parsing_error")
                return False
            
            viewstate_value = viewstate.get('value', '')
            viewstate_generator_value = viewstate_generator.get('value', '')
            event_validation_value = event_validation.get('value', '')
            
            log_debug("Successfully extracted form tokens", operation="cms_login")
            
            from app.routes.auth import get_institute_id_for_enrollment
            target_institute = institute_id
            if not target_institute or (str(target_institute).strip() == '2' and username and not str(username).strip().startswith('02-')):
                target_institute = get_institute_id_for_enrollment(username, fallback=str(target_institute) if target_institute else '2')
            login_data = {
                '__LASTFOCUS': '',
                '__EVENTTARGET': 'ctl00$BodyPH$btnLogin',
                '__EVENTARGUMENT': '',
                '__VIEWSTATE': viewstate_value,
                '__VIEWSTATEGENERATOR': viewstate_generator_value,
                '__EVENTVALIDATION': event_validation_value,
                'ctl00$BodyPH$tbEnrollment': username,
                'ctl00$BodyPH$tbPassword': password,
                'ctl00$BodyPH$ddlInstituteID': target_institute,
                'ctl00$BodyPH$ddlSubUserType': 'None',
                'ctl00$hfJsEnabled': '0'
            }
            
            log_debug(f"Posting CMS login for user {username}", operation="cms_login")
            
            # Step 4: POST login credentials via Worker proxy
            try:
                login_response = self._monitored_request(
                    session, 'post',
                    self._proxy(cms_login_url, session=session, use_native_redirects=True),
                    data=login_data,
                    timeout=self.request_config['timeout'],
                    allow_redirects=False
                )
                self._process_cf_proxy_headers(session, login_response, account_id="login_post")
                
                # Follow redirects manually for fallback mode
                while login_response.is_redirect:
                    location = login_response.headers.get('Location', '')
                    target = urljoin(cms_login_url, location)
                    login_response = self._monitored_request(
                        session, 'get',
                        self._proxy(target, session=session, use_native_redirects=True),
                        timeout=self.request_config['timeout'],
                        allow_redirects=False
                    )
                    cms_login_url = target
                    
                login_response.raise_for_status()
            except requests.RequestException as e:
                if 'login_response' in locals() and getattr(login_response, 'status_code', None) in [403, 500, 502, 503, 504]:
                    if getattr(session, 'worker_url', None):
                        log_warning(f"Proxy error {login_response.status_code} on POST! Marking dead: {session.worker_url}", operation="cms_login")
                        from app.utils.proxy_manager import get_proxy_manager
                        get_proxy_manager().mark_dead(session.worker_url)
                        
                log_error(f"CMS login POST request failed: {e}",
                         operation="cms_login", error_type="network_error", exc_info=True)
                return False
            
            # Step 5: Validate authentication success
            log_debug("Validating CMS authentication", operation="cms_login")
            
            # Decode the real URL — response.url is always the CF proxy wrapper URL
            # X-Final-Url is only set by the native redirects worker, not the minimal one
            final_url = login_response.headers.get('X-Final-Url') or self._get_actual_url(login_response)
            final_url = final_url.lower()
            
            log_debug(f"Login final URL resolved to: {final_url}", operation="cms_login")
            
            if 'login.aspx' in final_url:
                log_warning(f"Still on login page after authentication attempt. Final URL: {final_url}",
                           operation="cms_login")
                return False
            
            # Check for error messages in response
            response_text = login_response.text.lower()
            error_indicators = [
                'invalid username',
                'invalid password',
                'login failed',
                'authentication failed',
                'incorrect credentials',
                'access denied'
            ]
            
            for indicator in error_indicators:
                if indicator in response_text:
                    log_warning(f"Authentication failure indicator found: {indicator}",
                               operation="cms_login")
                    return False
            
            # Check for successful redirect (usually to dashboard or main page)
            if login_response.status_code == 200 and 'login.aspx' not in final_url:
                log_info(f"CMS login successful for user {username}", operation="cms_login")
                return True
            
            log_warning("CMS login validation inconclusive", operation="cms_login")
            return False
            
        except Exception as e:
            log_error(f"Unexpected error during CMS login: {e}",
                     operation="cms_login", error_type="system_error", exc_info=True)
            return False
    
    def _try_cached_session(self, account_id: str) -> Optional[requests.Session]:
        """
        Try to use a cached session if available and not expired.
        Uses stored expiry timestamp — avoids a live network round-trip on every call.
        Only falls back to a live test when the session is within 10 minutes of expiry.
        """
        try:
            # Check expiry via session_manager directly (reads from disk, no current_user needed)
            if not self.auth_service.session_manager.is_session_valid(account_id):
                return None
            
            # Get stored cookies
            cookies = self.auth_service.get_session_cookies(account_id)
            if not cookies:
                return None
            
            # Check if session is expiring soon — only then do a live test
            session_expiring_soon = self.auth_service.session_manager.is_session_expiring_soon(account_id)
            
            # Build the session with stored cookies
            session = requests.Session()
            session.account_id = account_id
            
            from app.utils.proxy_manager import get_proxy_manager
            session.worker_url = get_proxy_manager().get_worker(account_id)
            
            session.headers.update(self.request_config['headers'])
            for name, value in cookies.items():
                session.cookies.set(name, value)
            
            if session_expiring_soon:
                # Session is close to expiry — verify it's still alive before trusting it
                if self._test_session_validity(session, account_id):
                    log_debug(f"Cached session valid (expiring soon, live-tested) for account {account_id}",
                             account_id=account_id, operation="cached_session")
                    return session
                else:
                    log_debug(f"Cached session expired for account {account_id}",
                             account_id=account_id, operation="cached_session")
                    self.auth_service.logout(account_id)
                    return None
            
            # Session is fresh — trust the timestamp, skip the network test
            log_debug(f"Cached session valid (timestamp check) for account {account_id}",
                     account_id=account_id, operation="cached_session")
            return session
                
        except Exception as e:
            log_warning(f"Error trying cached session for account {account_id}: {e}",
                       account_id=account_id, operation="cached_session", exc_info=True)
            return None
    
    def _test_session_validity(self, session: requests.Session, account_id: str) -> bool:
        """
        Test if a session is still valid by making a lightweight request.
        
        Args:
            session: Session to test
            account_id: Account ID for logging
            
        Returns:
            True if session is valid, False otherwise
        """
        try:
            # Make a lightweight request to LMS dashboard
            test_url = f"{self.lms_base_url}/Student/Dashboard.php"
            
            log_debug(f"Testing session validity with request to {test_url}",
                     account_id=account_id, operation="test_session")
            
            response = self._monitored_request(
                session, 'get',
                self._proxy(test_url, session=session, use_native_redirects=True),
                timeout=10,  # Short timeout for quick test
            )
            
            # Check if we're redirected to login (session expired)
            if 'login' in self._get_actual_url(response).lower():
                log_debug(f"Session test failed - redirected to login for account {account_id}",
                         account_id=account_id, operation="test_session",
                         reason="redirected_to_login", final_url=response.url)
                return False
            
            # Check for authentication-related error messages
            if self._requires_cms_authentication(response):
                log_debug(f"Session test failed - authentication required for account {account_id}",
                         account_id=account_id, operation="test_session",
                         reason="authentication_required")
                return False
            
            # Check for successful response
            if response.status_code == 200:
                log_debug(f"Session test successful for account {account_id}",
                         account_id=account_id, operation="test_session",
                         status_code=response.status_code)
                return True
            
            log_debug(f"Session test failed - status {response.status_code} for account {account_id}",
                     account_id=account_id, operation="test_session",
                     status_code=response.status_code, reason="non_200_status")
            return False
            
        except requests.exceptions.Timeout as e:
            log_debug(f"Session test timeout for account {account_id}: {e}",
                     account_id=account_id, operation="test_session",
                     error_type="timeout")
            return False
        except requests.exceptions.ConnectionError as e:
            log_debug(f"Session test connection error for account {account_id}: {e}",
                     account_id=account_id, operation="test_session",
                     error_type="connection_error")
            return False
        except Exception as e:
            log_debug(f"Session test error for account {account_id}: {e}",
                     account_id=account_id, operation="test_session",
                     error_type="unexpected_error")
            return False
    
    def _store_session_cookies(self, account_id: str, session: requests.Session):
        """
        Store session cookies for future use.
        
        Args:
            account_id: Account ID
            session: Authenticated session with cookies
        """
        try:
            # Extract all cookies from session
            cookies = {}
            for cookie in session.cookies:
                cookies[cookie.name] = cookie.value
            
            if cookies:
                # Store in session manager
                from datetime import datetime, timedelta
                expires_at = datetime.now() + timedelta(hours=2)  # Default 2 hour expiry
                
                # Separate CMS and LMS cookies for better management
                cms_cookies = {k: v for k, v in cookies.items() 
                             if any(domain in k.lower() for domain in ['cms', 'bahria']) or 
                                k.lower() in ['asp.net_sessionid', 'viewstate', '__viewstate']}
                lms_cookies = {k: v for k, v in cookies.items() 
                             if any(domain in k.lower() for domain in ['lms']) or 
                                k.lower() in ['phpsessid', 'lms_session']}
                
                # Store combined cookies
                success = self.auth_service.session_manager.store_session_cookies(
                    account_id, cookies, expires_at, session_type='combined'
                )
                
                if success:
                    log_debug(f"Stored {len(cookies)} session cookies for account {account_id} "
                             f"(CMS: {len(cms_cookies)}, LMS: {len(lms_cookies)})",
                             account_id=account_id, operation="store_cookies", 
                             cookie_count=len(cookies), cms_count=len(cms_cookies), 
                             lms_count=len(lms_cookies))
                else:
                    log_warning(f"Failed to store session cookies for account {account_id}",
                               account_id=account_id, operation="store_cookies")
            else:
                log_warning(f"No cookies to store for account {account_id}",
                           account_id=account_id, operation="store_cookies")
                
        except Exception as e:
            log_warning(f"Error storing session cookies for account {account_id}: {e}",
                       account_id=account_id, operation="store_cookies", 
                       error_type="system_error", exc_info=True)
                       

    
    def handle_authentication_error(self, account_id: str, error: Exception, 
                                   operation: str = "unknown") -> Dict[str, Any]:
        """
        Handle authentication errors with comprehensive error context and recovery suggestions.
        
        Args:
            account_id: Account ID
            error: The exception that occurred
            operation: The operation that failed
            
        Returns:
            Dictionary with error details and recovery suggestions
        """
        try:
            log_error(f"Handling authentication error for account {account_id} during {operation}: {error}",
                     account_id=account_id, operation="handle_auth_error",
                     failed_operation=operation, error_type=type(error).__name__)
            
            error_context = {
                'account_id': account_id,
                'operation': operation,
                'error_type': type(error).__name__,
                'error_message': str(error),
                'timestamp': datetime.now().isoformat(),
                'recovery_suggestions': []
            }
            
            # Determine error category and recovery suggestions
            if isinstance(error, LMSNetworkError):
                if "session expired" in str(error).lower():
                    error_context['category'] = 'session_expired'
                    error_context['user_message'] = 'Your session has expired. Please try again.'
                    error_context['recovery_suggestions'] = [
                        'Clear existing session',
                        'Re-authenticate with CMS credentials',
                        'Retry the operation'
                    ]
                elif "authentication" in str(error).lower() or "login" in str(error).lower():
                    error_context['category'] = 'authentication_failed'
                    error_context['user_message'] = 'Authentication failed. Please check your CMS credentials.'
                    error_context['recovery_suggestions'] = [
                        'Verify CMS credentials are correct',
                        'Update credentials in settings',
                        'Try logging in to CMS directly to verify access'
                    ]
                elif "timeout" in str(error).lower():
                    error_context['category'] = 'network_timeout'
                    error_context['user_message'] = 'Connection timeout. Please check your internet connection.'
                    error_context['recovery_suggestions'] = [
                        'Check internet connectivity',
                        'Retry the operation',
                        'Try again later if server is overloaded'
                    ]
                elif "connection" in str(error).lower():
                    error_context['category'] = 'network_error'
                    error_context['user_message'] = 'Network connection error. Please check your internet connection.'
                    error_context['recovery_suggestions'] = [
                        'Check internet connectivity',
                        'Verify CMS/LMS servers are accessible',
                        'Check firewall settings'
                    ]
                else:
                    error_context['category'] = 'network_error_other'
                    error_context['user_message'] = 'A network error occurred. Please try again.'
                    error_context['recovery_suggestions'] = [
                        'Retry the operation',
                        'Check internet connectivity'
                    ]
            
            elif isinstance(error, LMSParsingError):
                error_context['category'] = 'parsing_error'
                error_context['user_message'] = 'Unable to process LMS data. The page structure may have changed.'
                error_context['recovery_suggestions'] = [
                    'Retry the operation',
                    'Report the issue if it persists',
                    'Check if LMS website has been updated'
                ]
            
            elif isinstance(error, LMSScrapingError):
                error_context['category'] = 'scraping_error'
                error_context['user_message'] = 'Unable to retrieve LMS data. Please try again.'
                error_context['recovery_suggestions'] = [
                    'Retry the operation',
                    'Check if you have access to the LMS page',
                    'Verify your account permissions'
                ]
            
            else:
                error_context['category'] = 'unknown_error'
                error_context['user_message'] = 'An unexpected error occurred. Please try again.'
                error_context['recovery_suggestions'] = [
                    'Retry the operation',
                    'Clear session and re-authenticate',
                    'Report the issue if it persists'
                ]
            
            # Check session status for additional context
            try:
                session_info = self.auth_service.session_manager.get_session_info(account_id)
                if session_info:
                    error_context['session_status'] = session_info['status']
                    error_context['session_time_remaining'] = session_info['time_remaining']
                else:
                    error_context['session_status'] = 'no_session'
            except Exception as session_error:
                log_warning(f"Could not get session info while handling error: {session_error}",
                           account_id=account_id, operation="handle_auth_error")
            
            log_info(f"Error context created for account {account_id}: {error_context['category']}",
                    account_id=account_id, operation="handle_auth_error",
                    error_category=error_context['category'])
            
            return error_context
            
        except Exception as e:
            log_error(f"Error while handling authentication error for account {account_id}: {e}",
                     account_id=account_id, operation="handle_auth_error",
                     error_type="meta_error", exc_info=True)
            
            # Return minimal error context
            return {
                'account_id': account_id,
                'operation': operation,
                'error_type': 'error_handler_failed',
                'error_message': str(error),
                'user_message': 'An error occurred. Please try again.',
                'recovery_suggestions': ['Retry the operation']
            }
    
    def _access_lms_gateway(self, session: requests.Session, account_id: str = "unknown") -> bool:
        """
        Access GoToLMS gateway to establish LMS session.
        
        Steps:
        1. GET https://cms.bahria.edu.pk/Sys/Common/GoToLMS.aspx
        2. Follow all redirects (typically through auth.php)
        3. Verify landing on LMS dashboard
        4. Store any additional cookies
        
        Args:
            session: Authenticated CMS session
            account_id: Optional account ID for logging
            
        Returns:
            True if gateway access successful, False otherwise
        """
        try:
            gateway_url = "https://cms.bahria.edu.pk/Sys/Common/GoToLMS.aspx"
            
            log_debug(f"Accessing LMS gateway for account {account_id}", 
                     account_id=account_id, operation="lms_gateway")
            
            # Follow the full redirect chain manually through the proxy.
            # The gateway (GoToLMS.aspx) triggers a series of redirects that
            # establish the LMS session cookies. We don't care where we finally
            # land — we just need to make sure we didn't end up on a login page.
            gateway_response = self._monitored_request(
                session, 'get',
                self._proxy(gateway_url, session=session, use_native_redirects=True),
                timeout=self.request_config['timeout'],
                allow_redirects=False
            )
            self._process_cf_proxy_headers(session, gateway_response, account_id)
            
            redirect_count = 0
            max_redirects = 10
            while gateway_response.is_redirect and redirect_count < max_redirects:
                location = gateway_response.headers.get('Location', '')
                if not location:
                    break
                # IMPORTANT: resolve relative redirects against the CURRENT gateway_url,
                # not the original one, otherwise we get wrong URLs after the first hop.
                gateway_url = urljoin(gateway_url, location)
                log_debug(f"Gateway redirect {redirect_count + 1}: {gateway_url}", operation="lms_gateway")
                gateway_response = self._monitored_request(
                    session, 'get',
                    self._proxy(gateway_url, session=session, use_native_redirects=True),
                    timeout=self.request_config['timeout'],
                    allow_redirects=False
                )
                self._process_cf_proxy_headers(session, gateway_response, account_id)
                redirect_count += 1

            # Decode the actual destination URL from the CF proxy wrapper
            actual_gateway_url = self._get_actual_url(gateway_response)
            log_debug(f"Gateway final URL: {actual_gateway_url} (status {gateway_response.status_code})",
                     account_id=account_id, operation="lms_gateway")
            
            if gateway_response.status_code in [403, 500, 502, 503, 504]:
                if getattr(session, 'worker_url', None):
                    log_warning(f"Proxy error {gateway_response.status_code} on gateway! Marking dead: {session.worker_url}", operation="lms_gateway")
                    from app.utils.proxy_manager import get_proxy_manager
                    get_proxy_manager().mark_dead(session.worker_url)
                return False
            
            # Success = we did NOT end up on a login page.
            # We may land on CMS dashboard, LMS dashboard, or any intermediate page — all fine.
            login_indicators = ['login.aspx', 'login.php', '/login', 'logins/']
            if any(indicator in actual_gateway_url.lower() for indicator in login_indicators):
                log_warning(f"Gateway redirected to login page: {actual_gateway_url}",
                           operation="lms_gateway")
                return False

            # Check if redirected to mandatory QA surveys page
            if ('qualityassurance' in actual_gateway_url.lower() or
                'survey' in actual_gateway_url.lower() or
                'complete/evaluate the surveys first' in gateway_response.text.lower() or
                'bodyph_gvsurveyconducts' in gateway_response.text.lower()):
                log_warning(f"Gateway redirected to survey page: {actual_gateway_url}", operation="lms_gateway")
                raise CMSSurveyRequiredError("Mandatory CMS surveys must be completed before LMS can be accessed")
            
            log_info(f"LMS gateway session established. Final URL: {actual_gateway_url}",
                    account_id=account_id, operation="lms_gateway")
            return True
            
        except CMSSurveyRequiredError:
            raise
        except Exception as e:
            log_error(f"Unexpected error accessing LMS gateway: {e}",
                     operation="lms_gateway", error_type="system_error", exc_info=True)
            return False
    
    def _requires_cms_authentication(self, response: requests.Response) -> bool:
        """
        Check if the response indicates that CMS authentication is required.
        
        Args:
            response: HTTP response to check
            
        Returns:
            True if authentication is required, False otherwise
        """
        try:
            # Decode the real URL from the CF proxy wrapper before checking
            actual_url = self._get_actual_url(response).lower()
            
            # Check if redirected to login page
            if 'login.php' in actual_url:
                return True
            
            # Check response content for authentication indicators
            content = response.text.lower()
            auth_indicators = [
                'please log in',
                'login required',
                'authentication required',
                'access denied',
                'unauthorized',
                'session expired'
            ]
            
            for indicator in auth_indicators:
                if indicator in content:
                    return True
            
            # Check for login forms
            if '<form' in content and ('login' in content or 'password' in content):
                return True
            
            return False
            
        except Exception:
            # If we can't check, assume authentication is required
            return True
    
    def _get_actual_url(self, response: requests.Response) -> str:
        """Extract the real destination URL from a proxied response.
        
        When requests go through the Cloudflare Worker, `response.url` is the
        proxy URL like: https://proxy.example.invalid/?url=https%3A%2F%2Fcms...
        This method decodes the inner URL so we can check it correctly.
        """
        proxy_url = response.url
        from urllib.parse import urlparse, parse_qs, unquote
        parsed = urlparse(proxy_url)
        params = parse_qs(parsed.query)
        
        # If the proxy returned X-Final-Url header, it followed redirects internally
        final_url_header = response.headers.get('X-Final-Url')
        if final_url_header:
            return final_url_header
            
        # Otherwise parse it from the query param
        if 'url' in params:
            return unquote(params['url'][0])
            
        return proxy_url

    def _process_cf_proxy_headers(self, session: requests.Session, response: requests.Response, account_id: Optional[str] = None):
        """Process custom headers returned by the Cloudflare Worker in native redirects mode."""
        # Handle Cookies combined by the CF Worker during native redirects
        proxy_cookies = response.headers.get('X-Proxy-Set-Cookie')
        if proxy_cookies:
            log_debug(f"Received proxy combined cookies", account_id=account_id)
            for cookie_str in proxy_cookies.split('|||'):
                if cookie_str.strip():
                    try:
                        key_val = cookie_str.split(';')[0].strip()
                        if '=' in key_val:
                            k, v = key_val.split('=', 1)
                            session.cookies.set(k, v)
                    except:
                        pass
        else:
            # Fallback for dumb proxies that return raw Set-Cookie headers
            # Python requests drops these due to proxy domain mismatch, so we extract manually
            if hasattr(response, 'raw') and hasattr(response.raw, 'headers'):
                raw_cookies = response.raw.headers.getlist('Set-Cookie')
                for cookie_str in raw_cookies:
                    if cookie_str.strip():
                        try:
                            key_val = cookie_str.split(';')[0].strip()
                            if '=' in key_val:
                                k, v = key_val.split('=', 1)
                                session.cookies.set(k, v)
                        except Exception as ce:
                            log_debug(f"Failed to parse proxy cookie {cookie_str}: {ce}")

    def _make_request(self, session: requests.Session, url: str, 
                     params: Optional[Dict[str, Any]] = None,
                     method: str = 'GET', data: Optional[Dict[str, Any]] = None,
                     account_id: Optional[str] = None, auto_retry_auth: bool = True) -> requests.Response:
        """
        Make HTTP request with error handling, retries, and automatic re-authentication.
        
        Args:
            session: Authenticated session
            url: URL to request
            params: URL parameters
            method: HTTP method (GET, POST)
            data: POST data
            account_id: Account ID for re-authentication
            auto_retry_auth: Whether to automatically retry with re-authentication
            
        Returns:
            Response object
            
        Raises:
            LMSNetworkError: When request fails after retries
        """
        last_error = None
        base_delay = 1.0
        
        for attempt in range(self.max_retries):
            try:
                log_debug(f"Making {method} request to {url} (attempt {attempt + 1})",
                         account_id=account_id, operation="make_request", attempt=attempt + 1)
                
                # Embed query params into the target URL BEFORE proxying.
                # The Worker only reads the `url` query param and forwards the request
                # to that URL — any extra params on the Worker URL are lost.
                # So we must bake them into the target URL ourselves.
                if params:
                    target_url = f"{url}?{urlencode(params)}"
                else:
                    target_url = url

                # Set to True ONLY when the advanced native-redirects CF Worker is deployed.
                # Currently using the minimal worker, so Python handles redirects manually.
                use_native_redirects = True

                if method.upper() == 'POST':
                    response = self._monitored_request(
                        session, 'post',
                        self._proxy(target_url, session=session, use_native_redirects=use_native_redirects),
                        data=data,
                        timeout=self.request_config['timeout'],
                        allow_redirects=False
                    )
                else:
                    response = self._monitored_request(
                        session, 'get',
                        self._proxy(target_url, session=session, use_native_redirects=use_native_redirects),
                        timeout=self.request_config['timeout'],
                        allow_redirects=False
                    )
                
                self._process_cf_proxy_headers(session, response, account_id=account_id)
                
                # Check for WAF Blocks (Cloudflare or University firewall)
                if response.status_code in [403, 503]:
                    if account_id and getattr(session, 'worker_url', None):
                        log_warning(f"WAF Block detected! Status {response.status_code} using worker {session.worker_url}", account_id=account_id)
                        from app.utils.proxy_manager import get_proxy_manager
                        get_proxy_manager().mark_dead(session.worker_url)
                        
                        # Failover and Silent Re-login
                        if auto_retry_auth and account_id and attempt == 0:
                            log_info(f"Failing over to new proxy worker for account {account_id}", account_id=account_id)
                            self.invalidate_session_cache(account_id)
                            new_session = self._create_authenticated_session(account_id)
                            
                            # Copy new cookies and state to current session to propagate up
                            session.cookies.update(new_session.cookies)
                            session.headers.update(new_session.headers)
                            session.worker_url = getattr(new_session, 'worker_url', None)
                            
                            # Retry this request with the new worker
                            continue
                # Follow redirects manually through the proxy ONLY if native redirects are disabled
                # OR if the proxy returned a redirect anyway (meaning it doesn't support native redirects, e.g. Vercel)
                if not use_native_redirects or response.is_redirect:
                    while response.is_redirect:
                        location = response.headers.get('Location', '')
                        target = urljoin(url, location)
                        response = self._monitored_request(
                            session, 'get',
                            self._proxy(target, session=session, use_native_redirects=use_native_redirects),
                            timeout=self.request_config['timeout'],
                            allow_redirects=False
                        )
                        url = target
                
                # Check for successful response
                response.raise_for_status()
                
                # Check if we were redirected to login (session expired)
                if self._requires_cms_authentication(response):
                    log_warning(f"Session expired during request to {url}",
                               account_id=account_id, operation="make_request", attempt=attempt + 1)
                    
                    if auto_retry_auth and account_id and attempt == 0:
                        # Try to re-authenticate and retry once
                        log_info(f"Attempting automatic re-authentication for account {account_id}",
                                account_id=account_id, operation="make_request")
                        
                        try:
                            # Clear expired session
                            self.auth_service.logout(account_id)
                            self.invalidate_session_cache(account_id)
                            
                            # Create new authenticated session
                            new_session = self._create_authenticated_session(account_id)
                            
                            # Copy new cookies to current session
                            session.cookies.clear()
                            for cookie in new_session.cookies:
                                session.cookies.set(cookie.name, cookie.value)
                            
                            log_info(f"Re-authentication successful for account {account_id}",
                                    account_id=account_id, operation="make_request")
                            
                            # Retry the request with new session
                            continue
                            
                        except CMSSurveyRequiredError:
                            raise
                        except Exception as auth_error:
                            log_error(f"Re-authentication failed for account {account_id}: {auth_error}",
                                     account_id=account_id, operation="make_request", 
                                     error_type="reauth_failed", exc_info=True)
                            raise LMSNetworkError(f"Session expired and re-authentication failed: {auth_error}")
                    else:
                        raise LMSNetworkError("Session expired - authentication required")
                
                log_debug(f"Request successful: {response.status_code}",
                         account_id=account_id, operation="make_request")
                return response
                
            except requests.exceptions.Timeout as e:
                last_error = f"Request timeout: {e}"
                log_warning(f"Request timeout on attempt {attempt + 1}: {e}",
                           account_id=account_id, operation="make_request", 
                           error_type="timeout", attempt=attempt + 1)
                
            except requests.exceptions.ConnectionError as e:
                last_error = f"Connection error: {e}"
                log_warning(f"Connection error on attempt {attempt + 1}: {e}",
                           account_id=account_id, operation="make_request", 
                           error_type="connection", attempt=attempt + 1)
                
            except requests.exceptions.HTTPError as e:
                last_error = f"HTTP error: {e}"
                log_warning(f"HTTP error on attempt {attempt + 1}: {e}",
                           account_id=account_id, operation="make_request", 
                           error_type="http", attempt=attempt + 1)
                
                # Don't retry on client errors (4xx) except 401/403 which might be auth issues
                if 400 <= response.status_code < 500 and response.status_code not in [401, 403]:
                    break
                    
            except LMSNetworkError:
                # Re-raise LMS network errors (including auth failures)
                raise
                
            except Exception as e:
                last_error = f"Unexpected error: {e}"
                log_warning(f"Unexpected error on attempt {attempt + 1}: {e}",
                           account_id=account_id, operation="make_request", 
                           error_type="unexpected", attempt=attempt + 1, exc_info=True)
            
            # Wait before retry (except on last attempt) with exponential backoff
            if attempt < self.max_retries - 1:
                delay = base_delay * (2 ** attempt)
                log_debug(f"Waiting {delay}s before retry",
                         account_id=account_id, operation="make_request", delay=delay)
                import time
                time.sleep(delay)
        
        # All retries failed
        error_msg = f"Request to {url} failed after {self.max_retries} attempts. Last error: {last_error}"
        log_error(error_msg, account_id=account_id, operation="make_request", 
                 error_type="max_retries_exceeded")
        raise LMSNetworkError(error_msg)
    
    def _parse_html(self, html_content: str, url: str) -> BeautifulSoup:
        """
        Parse HTML content with error handling.
        
        Args:
            html_content: HTML content to parse
            url: URL for error reporting
            
        Returns:
            BeautifulSoup object
            
        Raises:
            LMSParsingError: When HTML parsing fails
        """
        try:
            if not html_content or not html_content.strip():
                raise LMSParsingError("Empty HTML content")
            
            soup = BeautifulSoup(html_content, 'html.parser')
            
            # Basic validation - check if we have a valid HTML structure
            if not soup.find('html') and not soup.find('body'):
                logger.warning(f"HTML structure seems incomplete for {url}")
            
            logger.debug(f"Successfully parsed HTML content from {url}")
            return soup
            
        except Exception as e:
            logger.error(f"Failed to parse HTML from {url}: {e}")
            raise LMSParsingError(f"HTML parsing failed: {e}")
    
    def _extract_table_data(self, soup: BeautifulSoup, table_selector: str = 'table') -> List[List[str]]:
        """
        Extract data from HTML table.
        
        Args:
            soup: BeautifulSoup object
            table_selector: CSS selector for table
            
        Returns:
            List of rows, each row is a list of cell values
            
        Raises:
            LMSParsingError: When table extraction fails
        """
        try:
            table = soup.select_one(table_selector)
            if not table:
                raise LMSParsingError(f"No table found with selector: {table_selector}")
            
            rows = []
            table_rows = table.find_all('tr')
            
            if not table_rows:
                logger.warning("Table found but no rows detected")
                return []
            
            for row in table_rows:
                cells = row.find_all(['td', 'th'])
                cell_values = []
                
                for cell in cells:
                    # Extract text and clean it
                    cell_text = cell.get_text(strip=True)
                    cell_values.append(cell_text)
                
                if cell_values:  # Only add non-empty rows
                    rows.append(cell_values)
            
            logger.debug(f"Extracted {len(rows)} rows from table")
            return rows
            
        except Exception as e:
            logger.error(f"Failed to extract table data: {e}")
            raise LMSParsingError(f"Table extraction failed: {e}")
    
    def _extract_links_from_cell(self, cell_soup: BeautifulSoup, base_url: str) -> List[str]:
        """
        Extract all links from a table cell.
        
        Args:
            cell_soup: BeautifulSoup object for the cell
            base_url: Base URL for resolving relative links
            
        Returns:
            List of absolute URLs
        """
        try:
            links = []
            link_elements = cell_soup.find_all('a', href=True)
            
            for link in link_elements:
                href = link.get('href')
                if href:
                    # If URL is already absolute (starts with http:// or https://), use it as-is
                    if href.startswith('http://') or href.startswith('https://'):
                        absolute_url = href
                    else:
                        # Convert relative URLs to absolute
                        absolute_url = urljoin(base_url, href)
                    links.append(absolute_url)
            
            return links
            
        except Exception as e:
            logger.warning(f"Error extracting links from cell: {e}")
            return []
    
    def _clean_text(self, text: str) -> str:
        """
        Clean and normalize text content.
        
        Args:
            text: Raw text to clean
            
        Returns:
            Cleaned text
        """
        if not text:
            return ""
        
        # Remove extra whitespace and normalize
        cleaned = re.sub(r'\s+', ' ', text.strip())
        
        # Remove common HTML entities that might have been missed
        cleaned = cleaned.replace('&nbsp;', ' ')
        cleaned = cleaned.replace('&amp;', '&')
        cleaned = cleaned.replace('&lt;', '<')
        cleaned = cleaned.replace('&gt;', '>')
        
        return cleaned
    
    def _parse_date(self, date_str: str) -> Optional[datetime]:
        """
        Parse date string into datetime object.
        
        Args:
            date_str: Date string to parse
            
        Returns:
            Datetime object or None if parsing fails
        """
        if not date_str or not date_str.strip():
            return None
        
        date_str = self._clean_text(date_str)
        
        # LMS-specific format: "9 September 2025-11:55 pm" or "12 September 2025 - 11:55 pm"
        # Handle with or without space around dash
        lms_pattern = r'(\d{1,2})\s+([A-Za-z]+)\s+(\d{4})\s*-\s*(\d{1,2}):(\d{2})\s*(am|pm)'
        match = re.match(lms_pattern, date_str, re.IGNORECASE)
        if match:
            day, month_name, year, hour, minute, ampm = match.groups()
            try:
                # Convert to 24-hour format
                hour = int(hour)
                if ampm.lower() == 'pm' and hour != 12:
                    hour += 12
                elif ampm.lower() == 'am' and hour == 12:
                    hour = 0
                
                # Parse month name
                month_map = {
                    'january': 1, 'february': 2, 'march': 3, 'april': 4,
                    'may': 5, 'june': 6, 'july': 7, 'august': 8,
                    'september': 9, 'october': 10, 'november': 11, 'december': 12
                }
                month = month_map.get(month_name.lower())
                if month:
                    return datetime(int(year), month, int(day), hour, int(minute))
            except (ValueError, KeyError) as e:
                logger.debug(f"Error parsing LMS date format: {e}")
        
        # Common date formats used in LMS
        date_formats = [
            '%Y-%m-%d',           # 2024-01-15
            '%d-%m-%Y',           # 15-01-2024
            '%d/%m/%Y',           # 15/01/2024
            '%m/%d/%Y',           # 01/15/2024
            '%Y-%m-%d %H:%M:%S',  # 2024-01-15 23:59:59
            '%d-%m-%Y %H:%M:%S',  # 15-01-2024 23:59:59
            '%d/%m/%Y %H:%M:%S',  # 15/01/2024 23:59:59
            '%B %d, %Y',          # January 15, 2024
            '%d %B %Y',           # 15 January 2024
        ]
        
        for date_format in date_formats:
            try:
                return datetime.strptime(date_str, date_format)
            except ValueError:
                continue
        
        logger.warning(f"Could not parse date: {date_str}")
        return None
    
    def _parse_numeric_value(self, value_str: str) -> Optional[float]:
        """
        Parse numeric value from string.
        
        Args:
            value_str: String containing numeric value
            
        Returns:
            Float value or None if parsing fails
        """
        if not value_str or not value_str.strip():
            return None
        
        # Clean the string
        cleaned = self._clean_text(value_str)
        
        # Remove common non-numeric characters
        cleaned = re.sub(r'[^\d.-]', '', cleaned)
        
        try:
            return float(cleaned)
        except (ValueError, TypeError):
            logger.debug(f"Could not parse numeric value: {value_str}")
            return None
    
    def _validate_scraped_data(self, data: List[Any], data_type: str) -> List[Any]:
        """
        Validate scraped data for consistency and completeness.
        
        Args:
            data: List of scraped data objects
            data_type: Type of data ('assignments' or 'lecture_notes')
            
        Returns:
            Validated and filtered data list
        """
        try:
            validated_data = []
            
            for item in data:
                if data_type == 'assignments' and isinstance(item, LMSAssignment):
                    # Validate assignment data
                    if item.title and item.course_code:
                        validated_data.append(item)
                    else:
                        logger.warning(f"Skipping invalid assignment: {item.title}")
                        
                elif data_type == 'lecture_notes' and isinstance(item, LMSLectureNote):
                    # Validate lecture note data
                    if self._validate_lecture_note(item):
                        validated_data.append(item)
                    else:
                        logger.warning(f"Skipping invalid lecture note: {item.title}")
            
            logger.debug(f"Validated {len(validated_data)} out of {len(data)} {data_type} items")
            return validated_data
            
        except Exception as e:
            logger.error(f"Error validating {data_type} data: {e}")
            return data  # Return original data if validation fails
    
    def _validate_lecture_note(self, note: LMSLectureNote) -> bool:
        """
        Validate a single lecture note for completeness and consistency.
        
        Args:
            note: LMSLectureNote object to validate
            
        Returns:
            True if valid, False otherwise
        """
        try:
            # Required fields
            if not note.title or not note.title.strip():
                logger.debug(f"Lecture note missing title")
                return False
            
            if not note.course_code or not note.course_code.strip():
                logger.debug(f"Lecture note '{note.title}' missing course code")
                return False
            
            # At least one of the following should be present
            has_content = (
                note.lecture_files or 
                note.video_links or 
                note.supplementary_links or
                note.lecture_files_info or
                note.video_links_info or
                note.supplementary_links_info or
                note.remarks
            )
            
            if not has_content:
                logger.debug(f"Lecture note '{note.title}' has no content (files, videos, or remarks)")
                return False
            
            # Validate file URLs if present
            if note.lecture_files:
                for url in note.lecture_files:
                    if not self._is_valid_url(url):
                        logger.debug(f"Lecture note '{note.title}' has invalid file URL: {url}")
                        return False
            
            if note.video_links:
                for url in note.video_links:
                    if not self._is_valid_url(url):
                        logger.debug(f"Lecture note '{note.title}' has invalid video URL: {url}")
                        return False
            
            # Validate file info if present
            if note.lecture_files_info:
                for file_info in note.lecture_files_info:
                    if not isinstance(file_info, dict) or 'url' not in file_info:
                        logger.debug(f"Lecture note '{note.title}' has invalid file info structure")
                        return False
            
            return True
            
        except Exception as e:
            logger.warning(f"Error validating lecture note: {e}")
            return False
    
    def _is_valid_url(self, url: str) -> bool:
        """
        Check if a URL is valid.
        
        Args:
            url: URL to validate
            
        Returns:
            True if valid, False otherwise
        """
        if not url or not isinstance(url, str):
            return False
        
        url = url.strip()
        
        # Basic URL validation
        if not url.startswith(('http://', 'https://', '/')):
            return False
        
        # Check for common invalid patterns
        if url in ['#', 'javascript:', 'javascript:void(0)']:
            return False
        
        return True
    
    def _get_quizzes_soup(self, account_id: str, params: Dict[str, str]) -> BeautifulSoup:
        session = self._get_or_create_session(account_id)
        response = self._make_request(session, self.quizzes_url, params, account_id=account_id)
        response.raise_for_status()
        if self._requires_cms_authentication(response):
            raise LMSScrapingError('LMS session expired. Please reconnect and retry.')
        return self._parse_html(response.text, self.quizzes_url)

    def get_quizzes_options(self, account_id: str) -> Dict[str, Any]:
        soup = self._get_quizzes_soup(account_id, {})
        semesters = self._extract_semesters_from_dropdown(soup)
        if not semesters or soup.find('select', id='courseId') is None:
            raise LMSParsingError('LMS did not return quiz selection options. Please retry.')
        return {'semesters': semesters, 'courses': self._extract_courses_from_dropdown(soup)}

    def get_quizzes_courses_for_semester(self, account_id: str, semester_code: str) -> List[Dict[str, str]]:
        soup = self._get_quizzes_soup(account_id, {'s': semester_code})
        if soup.find('select', id='courseId') is None:
            raise LMSParsingError('LMS did not return quiz courses. Please retry.')
        return self._extract_courses_from_dropdown(soup)

    def scrape_course_quizzes(self, account_id: str, semester_code: Optional[str],
                              course_code: str) -> List[LMSQuiz]:
        params = {'oc': course_code}
        if semester_code:
            params['s'] = semester_code
        soup = self._get_quizzes_soup(account_id, params)
        course_info = self._extract_selected_course_info(soup, course_code)
        return self.parse_quiz_data(soup, course_info, semester_code)

    def parse_quiz_data(self, soup: BeautifulSoup, course_info: Optional[Dict[str, str]] = None,
                        semester: Optional[str] = None) -> List[LMSQuiz]:
        """Identify the quiz table by headers; never turn a failed page into an empty list."""
        aliases = {'quizno': 'quiz_no', 'quiznumber': 'quiz_no', 'no': 'quiz_no',
                   'quiztitle': 'title', 'title': 'title', 'quiz': 'quiz_file',
                   'quizfile': 'quiz_file', 'quizmarks': 'marks', 'marks': 'marks',
                   'quizsolution': 'solution_file', 'solution': 'solution_file'}
        for table in soup.find_all('table'):
            rows = table.find_all('tr')
            for header_index, header in enumerate(rows):
                mapping = {}
                for index, cell in enumerate(header.find_all(['th', 'td'], recursive=False)):
                    key = re.sub(r'[^a-z0-9]', '', cell.get_text(strip=True).lower())
                    if key in aliases:
                        mapping[aliases[key]] = index
                if not {'quiz_no', 'title', 'quiz_file', 'marks', 'solution_file'} <= mapping.keys():
                    continue
                quizzes = []
                for row in rows[header_index + 1:]:
                    cells = row.find_all('td', recursive=False)
                    if not cells:
                        continue
                    text = row.get_text(' ', strip=True).lower()
                    if len(cells) == 1 and cells[0].has_attr('colspan') and re.search(
                            r'no\s+(?:quiz(?:zes)?|records?|data)|no .*found', text):
                        continue
                    if len(cells) <= max(mapping.values()):
                        raise LMSParsingError('LMS returned an incomplete quiz row. Please retry.')
                    def value(field):
                        return cells[mapping[field]].get_text(' ', strip=True)
                    number = re.search(r'\d+', value('quiz_no'))
                    if not number:
                        raise LMSParsingError('LMS returned an invalid quiz number. Please retry.')
                    def file_url(field):
                        link = cells[mapping[field]].find('a', href=True)
                        if not link:
                            return None
                        href = link['href'].strip()
                        if not href or href.startswith('#'):
                            return None
                        url = urljoin(self.quizzes_url, href)
                        parsed = urlparse(url)
                        if parsed.scheme not in ('http', 'https') or parsed.hostname != urlparse(self.lms_base_url).hostname:
                            raise LMSParsingError('LMS returned an invalid quiz file link.')
                        return url
                    info = course_info or {}
                    marks = value('marks')
                    quizzes.append(LMSQuiz(
                        quiz_no=int(number.group()), title=value('title'),
                        course_code=info.get('course_code', ''), course_name=info.get('course_name', ''),
                        marks=None if marks in ('', '-', '—', '---') else marks,
                        quiz_file=file_url('quiz_file'), solution_file=file_url('solution_file'),
                        semester=semester))
                return sorted(quizzes, key=lambda quiz: quiz.quiz_no)
        raise LMSParsingError('LMS did not return a valid quiz table. Please retry.')

    def scrape_assignments_page(self, account_id: str, semester: Optional[str] = None, 
                              course: Optional[str] = None) -> List[LMSAssignment]:
        """
        Scrape assignments data from LMS assignments page.
        
        Args:
            account_id: Account ID for authentication
            semester: Optional semester filter
            course: Optional course filter
            
        Returns:
            List of LMSAssignment objects
            
        Raises:
            LMSScrapingError: When scraping fails
        """
        try:
            log_debug(f"Scraping assignments for account {account_id}",
                    account_id=account_id, operation="scrape_assignments", 
                    semester=semester, course=course)
            
            # Create authenticated session
            session = self._create_authenticated_session(account_id)
            
            # Prepare request parameters
            params = {}
            if semester:
                params['semester'] = semester
            if course:
                params['course'] = course
            
            # Make request to assignments page
            try:
                response = self._make_request(session, self.assignments_url, params, account_id=account_id)
                
                # Check if response indicates authentication is required
                if self._requires_cms_authentication(response):
                    log_info(f"LMS assignments page requires CMS authentication for account {account_id}",
                            account_id=account_id, operation="scrape_assignments")
                    # Return empty list since we can't access without CMS login
                    return []
                
                # Parse HTML
                soup = self._parse_html(response.text, self.assignments_url)
                
                # Extract assignment data
                assignments = self.parse_assignment_data(soup)
                
                # Validate data
                validated_assignments = self._validate_scraped_data(assignments, 'assignments')
                
            except LMSNetworkError as e:
                if "redirected to login" in str(e).lower() or "authentication" in str(e).lower():
                    log_info(f"LMS assignments page requires authentication for account {account_id}",
                            account_id=account_id, operation="scrape_assignments")
                    return []  # Return empty list instead of failing
                else:
                    raise  # Re-raise other network errors
            
            log_info(f"Successfully scraped {len(validated_assignments)} assignments",
                    account_id=account_id, operation="scrape_assignments", 
                    count=len(validated_assignments))
            return validated_assignments
            
        except CMSSurveyRequiredError:
            raise
        except (LMSNetworkError, LMSParsingError) as e:
            # Re-raise known errors with enhanced context
            log_error(f"Assignment scraping failed for account {account_id}: {e}",
                     account_id=account_id, operation="scrape_assignments", 
                     error_type=type(e).__name__, exc_info=True)
            raise LMSScrapingError(f"Assignment scraping failed: {e}")
        except Exception as e:
            log_error(f"Unexpected error scraping assignments for account {account_id}: {e}",
                     account_id=account_id, operation="scrape_assignments", 
                     error_type="system_error", exc_info=True)
            raise LMSScrapingError(f"Assignment scraping failed: {e}")
    
    def scrape_lecture_notes_page(self, account_id: str, semester: Optional[str] = None,
                                course: Optional[str] = None) -> List[LMSLectureNote]:
        """
        Scrape lecture notes data from LMS lecture notes page.
        
        The LMS lecture notes page requires:
        1. First load the page to get available semesters and courses
        2. Select a semester (via ?s=<semester_code> parameter)
        3. Select a course (via ?oc=<course_code> parameter)
        4. Then the actual lecture notes are displayed
        
        Args:
            account_id: Account ID for authentication
            semester: Optional semester filter (semester code)
            course: Optional course filter (course code)
            
        Returns:
            List of LMSLectureNote objects with file metadata
            
        Raises:
            LMSScrapingError: When scraping fails
        """
        try:
            log_debug(f"Scraping lecture notes for account {account_id}",
                    account_id=account_id, operation="scrape_lecture_notes", 
                    semester=semester, course=course)
            
            # Create authenticated session
            try:
                session = self._create_authenticated_session(account_id)
            except LMSNetworkError as e:
                log_error(f"Failed to create authenticated session for lecture notes: {e}",
                         account_id=account_id, operation="scrape_lecture_notes",
                         error_type="authentication_error", exc_info=True)
                raise LMSScrapingError(f"Authentication failed: {e}")
            
            all_lecture_notes = []
            
            # If no specific course is requested, we need to get all courses
            if not course:
                log_debug(f"No specific course requested, fetching all courses",
                         account_id=account_id, operation="scrape_lecture_notes")
                
                # First, get the initial page to extract available courses
                try:
                    initial_response = self._make_request(session, self.lecture_notes_url, {}, account_id=account_id)
                    initial_soup = self._parse_html(initial_response.text, self.lecture_notes_url)
                    
                    # Extract available courses from dropdown
                    courses = self._extract_courses_from_dropdown(initial_soup)
                    log_debug(f"Found {len(courses)} courses for lecture notes",
                            account_id=account_id, operation="scrape_lecture_notes",
                            course_count=len(courses))
                    
                    # If no courses found, return empty list
                    if not courses:
                        log_warning(f"No courses found in lecture notes page",
                                   account_id=account_id, operation="scrape_lecture_notes")
                        return []
                    
                    # Scrape lecture notes for all courses in parallel (max 3 concurrent Worker requests)
                    def _fetch_course_notes(course_item):
                        code = course_item['code']
                        name = course_item['name']
                        try:
                            log_debug(f"Scraping lecture notes for course: {name} ({code})",
                                     account_id=account_id, operation="scrape_lecture_notes")
                            with self._worker_semaphore:
                                return self._scrape_course_lecture_notes(
                                    session, account_id, semester, code, name
                                )
                        except Exception as e:
                            log_warning(f"Failed to scrape lecture notes for course {name}: {e}",
                                       account_id=account_id, operation="scrape_lecture_notes")
                            return []

                    with ThreadPoolExecutor(max_workers=len(courses)) as executor:
                        futures = [executor.submit(_fetch_course_notes, c) for c in courses]
                        for future in as_completed(futures):
                            result = future.result()
                            if result:
                                all_lecture_notes.extend(result)
                    
                except Exception as e:
                    log_error(f"Failed to extract courses from lecture notes page: {e}",
                             account_id=account_id, operation="scrape_lecture_notes",
                             error_type="parsing_error", exc_info=True)
                    return []
            else:
                # Specific course requested
                log_debug(f"Scraping lecture notes for specific course: {course}",
                         account_id=account_id, operation="scrape_lecture_notes")
                all_lecture_notes = self._scrape_course_lecture_notes(
                    session, account_id, semester, course, None
                )
            
            log_info(f"Successfully scraped {len(all_lecture_notes)} lecture notes",
                    account_id=account_id, operation="scrape_lecture_notes", 
                    count=len(all_lecture_notes))
            return all_lecture_notes
            
        except CMSSurveyRequiredError:
            raise
        except (LMSNetworkError, LMSParsingError) as e:
            log_error(f"Lecture notes scraping failed for account {account_id}: {e}",
                     account_id=account_id, operation="scrape_lecture_notes", 
                     error_type=type(e).__name__, exc_info=True)
            raise LMSScrapingError(f"Lecture notes scraping failed: {e}")
        except Exception as e:
            log_error(f"Unexpected error scraping lecture notes for account {account_id}: {e}",
                     account_id=account_id, operation="scrape_lecture_notes", 
                     error_type="system_error", exc_info=True)
            raise LMSScrapingError(f"Lecture notes scraping failed: {e}")
    
    def get_lecture_notes_options(self, account_id: str) -> Dict[str, Any]:
        """
        Get available semesters and courses from the lecture notes page.
        Scrapes the initial page to extract dropdown options.
        
        Args:
            account_id: Account ID for authentication
            
        Returns:
            Dictionary with 'semesters' and 'courses' lists
            Each semester: {'code': str, 'name': str}
            Each course: {'code': str, 'name': str}
        """
        try:
            log_debug(f"Getting lecture notes options for account {account_id}",
                    account_id=account_id, operation="get_lecture_notes_options")
            
            # Create authenticated session — use shared cache to avoid redundant re-auth
            session = self._get_or_create_session(account_id)
            
            # Get the initial lecture notes page
            response = self._make_request(session, self.lecture_notes_url, {}, account_id=account_id)
            
            # Parse HTML
            soup = self._parse_html(response.text, self.lecture_notes_url)
            
            # Extract semesters and courses
            semesters = self._extract_semesters_from_dropdown(soup)
            courses = self._extract_courses_from_dropdown(soup)
            
            log_debug(f"Found {len(semesters)} semesters and {len(courses)} courses",
                    account_id=account_id, operation="get_lecture_notes_options")
            
            return {
                'semesters': semesters,
                'courses': courses
            }
            
        except CMSSurveyRequiredError:
            raise
        except Exception as e:
            log_error(f"Error getting lecture notes options: {e}",
                     account_id=account_id, operation="get_lecture_notes_options",
                     exc_info=True)
            return {'semesters': [], 'courses': []}
    
    def _extract_semesters_from_dropdown(self, soup: BeautifulSoup) -> List[Dict[str, str]]:
        """
        Extract available semesters from the semester dropdown.
        
        Args:
            soup: BeautifulSoup object of the lecture notes page
            
        Returns:
            List of dictionaries with 'code' and 'name' keys
        """
        semesters = []
        
        try:
            # Find the semester dropdown (id="semesterId")
            semester_select = soup.find('select', {'id': 'semesterId'})
            
            if not semester_select:
                log_debug("Semester dropdown not found in HTML")
                return semesters
            
            # Extract all semester options
            options = semester_select.find_all('option')
            
            for option in options:
                # URL-decode the value: HTML option values are URL-encoded (e.g. MjAyNTM%3D).
                # Storing them decoded (MjAyNTM=) lets requests re-encode them correctly
                # when building query strings, avoiding double-encoding (%253D).
                semester_code = unquote(option.get('value', '').strip())
                semester_name = option.get_text(strip=True)
                
                # Skip empty values
                if semester_code and semester_code != '':
                    semesters.append({
                        'code': semester_code,
                        'name': semester_name
                    })
                    log_debug(f"Found semester: {semester_name} ({semester_code})")
            
        except Exception as e:
            log_warning(f"Error extracting semesters from dropdown: {e}")
        
        return semesters
    
    def _extract_courses_from_dropdown(self, soup: BeautifulSoup) -> List[Dict[str, str]]:
        """
        Extract available courses from the course dropdown.
        
        Args:
            soup: BeautifulSoup object of the lecture notes page
            
        Returns:
            List of dictionaries with 'code' and 'name' keys
        """
        courses = []
        
        try:
            # Find the course dropdown (id="courseId")
            course_select = soup.find('select', {'id': 'courseId'})
            
            if not course_select:
                log_debug("Course dropdown not found in HTML")
                return courses
            
            # Extract all course options (skip the first "Select Course" option)
            options = course_select.find_all('option')
            
            for option in options:
                # URL-decode the value: same double-encoding issue as semesters.
                course_code = unquote(option.get('value', '').strip())
                course_name = option.get_text(strip=True)
                
                # Skip empty values and "Select Course" option
                if course_code and course_code != '' and course_name.strip().lower() != 'select course':
                    courses.append({
                        'code': course_code,
                        'name': course_name
                    })
                    log_debug(f"Found course: {course_name} ({course_code})")
            
        except Exception as e:
            log_warning(f"Error extracting courses from dropdown: {e}")
        
        return courses
    
    def get_lecture_notes_courses_for_semester(self, account_id: str, semester_code: str) -> List[Dict[str, str]]:
        """
        Get available courses for a specific semester.
        Mimics selecting a semester from the dropdown to get updated course list.
        
        Args:
            account_id: Account ID for authentication
            semester_code: Semester code (e.g., 'MjAyNTM=')
            
        Returns:
            List of dictionaries with 'code' and 'name' keys
        """
        try:
            log_debug(f"Getting courses for semester {semester_code}",
                    account_id=account_id, operation="get_courses_for_semester",
                    semester_code=semester_code)
            
            # Reuse cached session — avoids re-auth on every per-semester call
            session = self._get_or_create_session(account_id)
            
            # Request the lecture notes page with semester parameter
            params = {'s': semester_code}
            response = self._make_request(session, self.lecture_notes_url, params, account_id=account_id)
            
            # Parse HTML
            soup = self._parse_html(response.text, self.lecture_notes_url)
            
            # Extract courses from the updated dropdown
            courses = self._extract_courses_from_dropdown(soup)
            
            log_debug(f"Found {len(courses)} courses for semester {semester_code}",
                    account_id=account_id, operation="get_courses_for_semester")
            
            return courses
            
        except CMSSurveyRequiredError:
            raise
        except Exception as e:
            log_error(f"Error getting courses for semester {semester_code}: {e}",
                     account_id=account_id, operation="get_courses_for_semester",
                     exc_info=True)
            return []
    
    def get_assignments_options(self, account_id: str) -> Dict[str, Any]:
        """
        Get available semesters and courses from the assignments page.
        Scrapes the initial page to extract dropdown options.
        
        Args:
            account_id: Account ID for authentication
            
        Returns:
            Dictionary with 'semesters' and 'courses' lists
            Each semester: {'code': str, 'name': str}
            Each course: {'code': str, 'name': str}
        """
        try:
            log_debug(f"Getting assignments options for account {account_id}",
                    account_id=account_id, operation="get_assignments_options")
            
            # Create authenticated session — use shared cache to avoid redundant re-auth
            session = self._get_or_create_session(account_id)
            
            # Get the initial assignments page
            response = self._make_request(session, self.assignments_url, {}, account_id=account_id)
            
            # DEBUG: Log response status and length
            log_debug(f"DEBUG: Response status: {response.status_code}, length: {len(response.text)}",
                    account_id=account_id, operation="get_assignments_options")
            
            # Parse HTML
            soup = self._parse_html(response.text, self.assignments_url)
            
            # DEBUG: Check if we can find the dropdowns
            semester_dropdown = soup.find('select', {'id': 'semesterId'})
            course_dropdown = soup.find('select', {'id': 'courseId'})
            log_debug(f"DEBUG: Found semester dropdown: {semester_dropdown is not None}, course dropdown: {course_dropdown is not None}",
                    account_id=account_id, operation="get_assignments_options")
            
            # Extract semesters and courses (same structure as lecture notes)
            semesters = self._extract_semesters_from_dropdown(soup)
            courses = self._extract_courses_from_dropdown(soup)
            
            log_debug(f"Found {len(semesters)} semesters and {len(courses)} courses",
                    account_id=account_id, operation="get_assignments_options")
            
            # DEBUG: Log first few items
            if semesters:
                log_debug(f"DEBUG: First semester: {semesters[0]}",
                        account_id=account_id, operation="get_assignments_options")
            if courses:
                log_debug(f"DEBUG: First course: {courses[0]}",
                        account_id=account_id, operation="get_assignments_options")
            
            return {
                'semesters': semesters,
                'courses': courses
            }
            
        except CMSSurveyRequiredError:
            raise
        except Exception as e:
            log_error(f"Error getting assignments options: {e}",
                     account_id=account_id, operation="get_assignments_options",
                     exc_info=True)
            return {'semesters': [], 'courses': []}
    
    def get_assignments_courses_for_semester(self, account_id: str, semester_code: str) -> List[Dict[str, str]]:
        """
        Get available courses for a specific semester (for assignments).
        Mimics selecting a semester from the dropdown to get updated course list.
        
        Args:
            account_id: Account ID for authentication
            semester_code: Semester code (e.g., 'MjAyNTM=')
            
        Returns:
            List of dictionaries with 'code' and 'name' keys
        """
        try:
            log_debug(f"Getting courses for assignments, semester {semester_code}",
                    account_id=account_id, operation="get_assignments_courses_for_semester",
                    semester_code=semester_code)
            
            # Reuse cached session — avoids re-auth on every per-semester call
            session = self._get_or_create_session(account_id)
            
            # Request the assignments page with semester parameter
            params = {'s': semester_code}
            response = self._make_request(session, self.assignments_url, params, account_id=account_id)
            
            # Parse HTML
            soup = self._parse_html(response.text, self.assignments_url)
            
            # Extract courses from the updated dropdown
            courses = self._extract_courses_from_dropdown(soup)
            
            log_debug(f"Found {len(courses)} courses for semester {semester_code}",
                    account_id=account_id, operation="get_assignments_courses_for_semester")
            
            return courses
            
        except CMSSurveyRequiredError:
            raise
        except Exception as e:
            log_error(f"Error getting courses for semester {semester_code}: {e}",
                     account_id=account_id, operation="get_assignments_courses_for_semester",
                     exc_info=True)
            return []
    
    def scrape_course_assignments(self, account_id: str, semester_code: Optional[str],
                                  course_code: str, *, strict: bool = False,
                                  expected_course_name: Optional[str] = None) -> List[LMSAssignment]:
        """
        Scrape assignments for a specific course.
        Mimics selecting a course from the dropdown on the original LMS site.
        
        Args:
            account_id: Account ID for authentication
            semester_code: Semester code (e.g., 'MjAyNTM=')
            course_code: Course code (e.g., 'MTM2NjM2')
            
        Returns:
            List of LMSAssignment objects
        """
        try:
            log_debug(f"Scraping assignments for course {course_code}",
                    account_id=account_id, operation="scrape_course_assignments",
                    semester_code=semester_code, course_code=course_code)
            
            # Reuse cached session — avoids re-auth on every per-course call
            session = self._get_or_create_session(account_id)
            
            # Build URL with parameters (mimics the dropdown selection)
            params = {}
            if semester_code:
                params['s'] = semester_code
            params['oc'] = course_code
            
            log_debug(f"Requesting assignments with params: {params}",
                     account_id=account_id, operation="scrape_course_assignments")
            
            # Make request
            response = self._make_request(session, self.assignments_url, params, account_id=account_id)
            
            # Debug: Log response details
            log_debug(f"Response URL: {response.url}, Status: {response.status_code}",
                     account_id=account_id, operation="scrape_course_assignments")
            
            # Check if redirected to login
            if self._requires_cms_authentication(response):
                if strict:
                    raise LMSAuthenticationRequired('University authentication is required')
                log_warning(f"Redirected to CMS login when accessing course {course_code}",
                           account_id=account_id, operation="scrape_course_assignments")
                return []
            
            # Parse HTML
            soup = self._parse_html(response.text, self.assignments_url)
            
            # Extract course information from the selected dropdown option
            if strict and response.status_code != 200:
                raise LMSNetworkError('University assignment request failed')
            course_info = self._extract_selected_course_info(
                soup, course_code, expected_course_name=expected_course_name, strict=strict)
            
            # Extract assignments with course context
            assignments = self.parse_assignment_data(soup, course_info=course_info, strict=strict)
            
            # Log what we found
            log_debug(f"Parsed {len(assignments)} assignments from HTML",
                     account_id=account_id, operation="scrape_course_assignments")
            
            log_debug(f"Successfully scraped {len(assignments)} assignments for course {course_code}",
                    account_id=account_id, operation="scrape_course_assignments",
                    count=len(assignments))
            
            return assignments
            
        except CMSSurveyRequiredError:
            raise
        except Exception as e:
            if strict:
                # A failed scrape must not masquerade as a course with no work.
                raise
            log_error(f"Error scraping assignments for course {course_code}: {e}",
                     account_id=account_id, operation="scrape_course_assignments",
                     exc_info=True)
            return []
    
    def scrape_course_lecture_notes(self, account_id: str, semester_code: Optional[str],
                                   course_code: str) -> List[LMSLectureNote]:
        """
        Scrape lecture notes for a specific course.
        Mimics selecting a course from the dropdown on the original LMS site.
        
        Args:
            account_id: Account ID for authentication
            semester_code: Semester code (e.g., 'MjAyNTM=')
            course_code: Course code (e.g., 'MTM2NjMz')
            
        Returns:
            List of LMSLectureNote objects
        """
        try:
            log_debug(f"Scraping lecture notes for course {course_code}",
                    account_id=account_id, operation="scrape_course_lecture_notes",
                    semester_code=semester_code, course_code=course_code)
            
            # Reuse cached session — avoids re-auth on every per-course call
            session = self._get_or_create_session(account_id)
            
            # Build URL with parameters (mimics the dropdown selection)
            params = {}
            if semester_code:
                params['s'] = semester_code
            params['oc'] = course_code
            
            log_debug(f"Requesting lecture notes with params: {params}",
                     account_id=account_id, operation="scrape_course_lecture_notes")
            
            # Make request
            response = self._make_request(session, self.lecture_notes_url, params, account_id=account_id)
            
            # Check if redirected to login
            if self._requires_cms_authentication(response):
                log_warning(f"Redirected to CMS login when accessing course {course_code}",
                           account_id=account_id, operation="scrape_course_lecture_notes")
                return []
            
            # Parse HTML
            soup = self._parse_html(response.text, self.lecture_notes_url)
            
            # Extract course information from the selected dropdown option
            course_info = self._extract_selected_course_info(soup, course_code)
            
            # Extract lecture notes with course context
            lecture_notes = self.parse_lecture_data(soup, course_info=course_info)
            
            # Log what we found
            log_debug(f"Parsed {len(lecture_notes)} lecture notes from HTML",
                     account_id=account_id, operation="scrape_course_lecture_notes")
            
            log_debug(f"Successfully scraped {len(lecture_notes)} lecture notes for course {course_code}",
                    account_id=account_id, operation="scrape_course_lecture_notes",
                    count=len(lecture_notes))
            
            return lecture_notes
            
        except CMSSurveyRequiredError:
            raise
        except Exception as e:
            log_error(f"Error scraping lecture notes for course {course_code}: {e}",
                     account_id=account_id, operation="scrape_course_lecture_notes",
                     exc_info=True)
            return []
    
    def _scrape_course_lecture_notes(self, session: requests.Session, account_id: str,
                                    semester: Optional[str], course_code: str,
                                    course_name: Optional[str]) -> List[LMSLectureNote]:
        """
        Scrape lecture notes for a specific course.
        
        Args:
            session: Authenticated session
            account_id: Account ID
            semester: Semester code (optional)
            course_code: Course code
            course_name: Course name (optional, for logging)
            
        Returns:
            List of LMSLectureNote objects for the course
        """
        try:
            # Build URL with parameters
            params = {}
            if semester:
                params['s'] = semester
            params['oc'] = course_code
            
            # Make request to lecture notes page with course selected
            log_debug(f"Requesting lecture notes for course {course_code} with params: {params}",
                     account_id=account_id, operation="scrape_lecture_notes")
            
            response = self._make_request(session, self.lecture_notes_url, params, account_id=account_id)
            
            # Debug: Log response details
            log_debug(f"Response URL: {response.url}, Status: {response.status_code}",
                     account_id=account_id, operation="scrape_lecture_notes")
            
            # Check if response indicates authentication is required
            if self._requires_cms_authentication(response):
                log_warning(f"LMS lecture notes page requires CMS authentication (redirected to: {response.url})",
                           account_id=account_id, operation="scrape_lecture_notes",
                           response_url=response.url, course_code=course_code)
                return []
            
            # Validate response content
            if not response.text or len(response.text.strip()) < 100:
                log_warning(f"Lecture notes page returned minimal content",
                           account_id=account_id, operation="scrape_lecture_notes",
                           content_length=len(response.text) if response.text else 0)
                return []
            
            # Parse HTML
            log_debug(f"Parsing lecture notes HTML (size: {len(response.text)} bytes)",
                     account_id=account_id, operation="scrape_lecture_notes")
            soup = self._parse_html(response.text, self.lecture_notes_url)
            
            # Extract lecture notes data
            log_debug(f"Extracting lecture notes data from parsed HTML",
                     account_id=account_id, operation="scrape_lecture_notes")
            lecture_notes = self.parse_lecture_data(soup)
            
            if not lecture_notes:
                log_debug(f"No lecture notes found for course {course_code}",
                         account_id=account_id, operation="scrape_lecture_notes")
                return []
            
            # Validate data
            log_debug(f"Validating {len(lecture_notes)} lecture notes",
                     account_id=account_id, operation="scrape_lecture_notes")
            validated_notes = self._validate_scraped_data(lecture_notes, 'lecture_notes')
            
            if len(validated_notes) < len(lecture_notes):
                log_warning(f"Some lecture notes failed validation: {len(lecture_notes) - len(validated_notes)} out of {len(lecture_notes)}",
                           account_id=account_id, operation="scrape_lecture_notes")
            
            return validated_notes
            
        except Exception as e:
            log_warning(f"Error scraping lecture notes for course {course_code}: {e}",
                       account_id=account_id, operation="scrape_lecture_notes")
            return []
    
    def parse_assignment_data(self, soup: BeautifulSoup, course_info: Optional[Dict[str, str]] = None,
                              strict: bool = False) -> List[LMSAssignment]:
        """
        Parse assignment data from HTML soup.
        
        Args:
            soup: BeautifulSoup object containing assignments page HTML
            course_info: Optional course information dict with 'course_code' and 'course_name'
            
        Returns:
            List of LMSAssignment objects
            
        Raises:
            LMSParsingError: When assignment data parsing fails
        """
        try:
            logger.debug("Parsing assignment data from HTML")
            assignments = []
            
            # Look for assignment table - try multiple selectors
            table_selectors = [
                'table.table-hover',  # LMS uses this class
                'table.table',  # Bootstrap table class
                'table.assignment-table',
                'table#assignments',
                'table[class*="assignment"]',
                'table[id*="assignment"]',
                '.assignment-container table',
                '.content table',
                '.box-body table',  # LMS wraps tables in box-body
                'table'  # Fallback to any table
            ]
            
            assignment_table = None
            if strict:
                # A generic page table must never masquerade as an empty assignment list.
                for table in soup.find_all('table'):
                    for row in table.find_all('tr'):
                        mapping = self._map_assignment_columns(row)
                        if 'title' in mapping and 'deadline' in mapping:
                            assignment_table = table
                            break
                    if assignment_table is not None:
                        break
            for selector in table_selectors:
                if strict:
                    break
                assignment_table = soup.select_one(selector)
                if assignment_table:
                    logger.debug(f"Found assignment table with selector: {selector}")
                    break
            
            if not assignment_table:
                if strict:
                    raise LMSParsingError("The university assignment table was not recognized")
                logger.warning("No assignment table found in HTML")
                # DEBUG: Log what tables we can find
                all_tables = soup.find_all('table')
                logger.warning(f"DEBUG: Found {len(all_tables)} tables in HTML")
                if all_tables:
                    for i, table in enumerate(all_tables[:3]):  # Log first 3 tables
                        classes = table.get('class', [])
                        table_id = table.get('id', 'no-id')
                        logger.warning(f"DEBUG: Table {i}: classes={classes}, id={table_id}")
                return []
            
            # Extract table rows
            rows = assignment_table.find_all('tr')
            logger.debug(f"Found {len(rows)} rows in assignment table")
            if not rows:
                if strict:
                    raise LMSParsingError("The university assignment table has no header")
                logger.warning("No rows found in assignment table")
                return []
            
            # Find header row to determine column structure
            header_row = None
            data_rows = []
            
            for i, row in enumerate(rows):
                cells = row.find_all(['th', 'td'])
                if not cells:
                    continue
                    
                # Check if this looks like a header row
                if row.find('th') or (i == 0 and self._looks_like_header_row(cells)):
                    header_row = row
                    logger.debug(f"Found header row at index {i}")
                else:
                    data_rows.append(row)
            
            logger.debug(f"Found {len(data_rows)} data rows in assignment table")
            if not data_rows:
                logger.warning("No data rows found in assignment table")
                return []
            
            # Determine column mapping from header
            column_mapping = self._map_assignment_columns(header_row) if header_row else self._guess_assignment_columns(data_rows[0])
            logger.debug(f"Column mapping: {column_mapping}")
            
            # Parse each data row
            for row_index, row in enumerate(data_rows):
                try:
                    if strict:
                        cells = row.find_all(['td', 'th'], recursive=False)
                        empty_text = row.get_text(' ', strip=True).lower()
                        if len(cells) == 1 and any(marker in empty_text for marker in
                                ('no assignment', 'no record', 'no data', 'nothing to display')):
                            continue
                        if len(cells) <= max(column_mapping.values(), default=0):
                            raise LMSParsingError("An assignment row has missing columns")
                    assignment = self._parse_assignment_row(row, column_mapping, row_index, course_info)
                    if assignment:
                        assignments.append(assignment)
                    elif strict:
                        raise LMSParsingError("An assignment row could not be parsed")
                except Exception as e:
                    if strict:
                        raise LMSParsingError("An assignment row could not be parsed") from e
                    logger.warning(f"Failed to parse assignment row {row_index}: {e}")
                    continue
            
            logger.debug(f"Successfully parsed {len(assignments)} assignments from HTML")
            return assignments
            
        except Exception as e:
            logger.error(f"Failed to parse assignment data: {e}")
            raise LMSParsingError(f"Assignment data parsing failed: {e}")

    def parse_course_assignments_response(self, response, course_code):
        """Strict read-only adapter; the caller owns request deadlines and recovery.

        Unlike legacy dashboard scraping, authentication and unknown markup are
        errors rather than empty results. No cache or session writes happen here.
        """
        if response.is_redirect or response.status_code in (401, 403) or self._requires_cms_authentication(response):
            raise LMSAuthenticationRequired("University authentication is required")
        if response.status_code != 200:
            raise LMSNetworkError("University assignment request failed")
        soup = self._parse_html(response.text, self.assignments_url)
        select = soup.find('select', id='courseId')
        selected = select.find('option', selected=True) if select else None
        if selected and unquote(selected.get('value', '')) != course_code:
            raise LMSParsingError("University returned a different course")
        return self.parse_assignment_data(soup, self._extract_selected_course_info(soup, course_code), strict=True)
    
    def _looks_like_header_row(self, cells) -> bool:
        """
        Check if a row looks like a header row based on cell content.
        
        Args:
            cells: List of cell elements
            
        Returns:
            True if row appears to be a header
        """
        if not cells:
            return False
        
        # Common header keywords
        header_keywords = [
            'assignment', 'no', 'number', 'title', 'course', 'marks', 'status', 
            'deadline', 'due', 'submission', 'grade', 'total', 'obtained'
        ]
        
        cell_texts = [cell.get_text(strip=True).lower() for cell in cells]
        
        # Check if any cell contains header keywords
        for text in cell_texts:
            for keyword in header_keywords:
                if keyword in text:
                    return True
        
        return False
    
    def _map_assignment_columns(self, header_row) -> Dict[str, int]:
        """
        Map assignment data columns based on header row.
        
        Args:
            header_row: Header row element
            
        Returns:
            Dictionary mapping field names to column indices
        """
        column_mapping = {}
        
        if not header_row:
            return column_mapping
        
        cells = header_row.find_all(['th', 'td'])
        
        for i, cell in enumerate(cells):
            header_text = cell.get_text(strip=True).lower()
            
            # Map common header variations to field names
            # Check for assignment number first (most specific)
            if 'assign' in header_text and ('no' in header_text or '#' in header_text or 'number' in header_text):
                column_mapping['assignment_no'] = i
            # Title column
            elif 'title' in header_text:
                column_mapping['title'] = i
            # Assignment files column (contains "assignment" and "solution" or "file")
            elif 'assignment' in header_text and ('solution' in header_text or 'file' in header_text or 'remark' in header_text):
                column_mapping['assignment_file'] = i
                column_mapping['remarks'] = i  # Same column contains remarks
            # Returned submission/comments (check before general submission)
            elif 'returned' in header_text or 'comment' in header_text:
                column_mapping['comments'] = i
            # Added submission column
            elif 'added submission' in header_text or 'submission' in header_text:
                column_mapping['submission_file'] = i
            # Marks obtained
            elif 'marks obtained' in header_text or 'obtained' in header_text:
                column_mapping['marks_obtained'] = i
            # Action/Status
            elif 'action' in header_text or 'status' in header_text:
                column_mapping['submission_status'] = i
            # Deadline
            elif 'deadline' in header_text or 'due' in header_text:
                column_mapping['deadline'] = i
            # Course
            elif 'course' in header_text or 'subject' in header_text:
                column_mapping['course'] = i
        
        logger.debug(f"Mapped columns: {column_mapping}")
        return column_mapping
    
    def _guess_assignment_columns(self, first_row) -> Dict[str, int]:
        """
        Guess column mapping based on first data row content.
        
        Args:
            first_row: First data row element
            
        Returns:
            Dictionary mapping field names to column indices
        """
        column_mapping = {}
        
        if not first_row:
            return column_mapping
        
        cells = first_row.find_all(['td', 'th'])
        
        # Common patterns for assignment tables
        # Usually: Assignment No, Title, Course, Marks, Status, Deadline
        if len(cells) >= 6:
            column_mapping = {
                'assignment_no': 0,
                'title': 1,
                'course': 2,
                'marks_obtained': 3,
                'total_marks': 4,
                'submission_status': 5
            }
            if len(cells) >= 7:
                column_mapping['deadline'] = 6
            if len(cells) >= 8:
                column_mapping['assignment_file'] = 7
        elif len(cells) >= 4:
            # Minimal mapping
            column_mapping = {
                'assignment_no': 0,
                'title': 1,
                'course': 2,
                'submission_status': 3
            }
        
        logger.debug(f"Guessed column mapping for {len(cells)} columns: {column_mapping}")
        return column_mapping
    
    def _parse_assignment_row(self, row, column_mapping: Dict[str, int], row_index: int, course_info: Optional[Dict[str, str]] = None) -> Optional[LMSAssignment]:
        """
        Parse a single assignment row into LMSAssignment object.
        
        Args:
            row: Table row element
            column_mapping: Column index mapping
            row_index: Row index for error reporting
            course_info: Optional course information dict with 'course_code' and 'course_name'
            
        Returns:
            LMSAssignment object or None if parsing fails
        """
        try:
            cells = row.find_all(['td', 'th'])
            if not cells:
                return None
            
            # Extract basic fields
            assignment_no = self._extract_assignment_number(cells, column_mapping)
            title = self._extract_cell_text(cells, column_mapping, 'title')
            
            # Use provided course_info or try to extract from cells
            if course_info:
                row_course_info = course_info
            else:
                row_course_info = self._extract_course_info(cells, column_mapping)
            
            marks_obtained = self._extract_numeric_field(cells, column_mapping, 'marks_obtained')
            total_marks = self._extract_numeric_field(cells, column_mapping, 'total_marks')
            submission_status = self._extract_submission_status(cells, column_mapping)
            
            # Extract files from assignment column (assignment and solution files)
            assignment_files = self._extract_assignment_files(cells, column_mapping)
            assignment_file = assignment_files.get('assignment_file')
            solution_file = assignment_files.get('solution_file')
            
            # DEBUG: Log solution file extraction
            if solution_file:
                logger.debug(f"Row {row_index} ({title}): Solution file found - {solution_file[:100]}...")
            else:
                logger.debug(f"Row {row_index} ({title}): No solution file extracted")
            
            # Extract submission file (from submission_file column)
            submission_file = self._extract_file_links(cells, column_mapping, 'submission_file')
            
            # Extract returned file and comments from returned submission column
            returned_data = self._extract_returned_submission_data(cells, column_mapping)
            returned_file = returned_data.get('returned_file')
            comments = returned_data.get('comments')
            
            # Extract remarks from the title attribute of the "Hover To View" span
            remarks = self._extract_remarks(cells, column_mapping)
            
            # Extract both actual and extended deadlines
            deadline_data = self._extract_deadline_data(cells, column_mapping)
            deadline = deadline_data.get('primary_deadline')
            actual_deadline = deadline_data.get('actual_deadline')
            extended_deadline = deadline_data.get('extended_deadline')
            
            # Extract delete IDs from action column (for deletion functionality)
            # Also extracts upload IDs if submit button is present
            submission_id, assignment_id, offered_course_id, student_id = self._extract_action_ids_from_row(row, cells, column_mapping)
            
            # DEBUG: Log what we extracted (use INFO to see in logs)
            if assignment_id or offered_course_id:
                logger.debug(f"Row {row_index} ({title}): Extracted IDs - submission_id={submission_id}, assignment_id={assignment_id}, offered_course_id={offered_course_id}, student_id={student_id}")
            
            logger.debug(f"Row {row_index}: title={title}, deadline={deadline}, assignment_file={assignment_file is not None}, solution_file={solution_file is not None}, submission_file={submission_file is not None}, remarks={remarks}, status={submission_status}")
            
            # Validate required fields
            if not title or not row_course_info.get('course_code'):
                logger.debug(f"Skipping row {row_index}: missing required fields (title: {title}, course: {row_course_info.get('course_code')})")
                return None
            
            # Create assignment object
            assignment = LMSAssignment(
                assignment_no=assignment_no,
                title=title,
                course_code=row_course_info.get('course_code', ''),
                course_name=row_course_info.get('course_name', ''),
                marks_obtained=marks_obtained,
                total_marks=total_marks,
                submission_status=submission_status,
                deadline=deadline,
                actual_deadline=actual_deadline,
                extended_deadline=extended_deadline,
                assignment_file=assignment_file,
                solution_file=solution_file,
                submission_file=submission_file,
                returned_file=returned_file,
                remarks=remarks,
                comments=comments,
                submission_id=submission_id,
                assignment_id=assignment_id,
                offered_course_id=offered_course_id,
                student_id=student_id
            )
            
            logger.debug(f"Successfully parsed assignment: {assignment.title} ({assignment.course_code})")
            return assignment
            
        except Exception as e:
            logger.warning(f"Failed to parse assignment row {row_index}: {e}")
            return None
    
    def _extract_assignment_number(self, cells, column_mapping: Dict[str, int]) -> int:
        """Extract assignment number from cells."""
        try:
            if 'assignment_no' in column_mapping:
                cell_text = self._extract_cell_text(cells, column_mapping, 'assignment_no')
                # Extract number from text
                numbers = re.findall(r'\d+', cell_text)
                if numbers:
                    return int(numbers[0])
            return 0
        except:
            return 0
    
    def _extract_cell_text(self, cells, column_mapping: Dict[str, int], field: str) -> str:
        """Extract text from a specific cell."""
        try:
            if field in column_mapping and column_mapping[field] < len(cells):
                cell = cells[column_mapping[field]]
                return self._clean_text(cell.get_text(strip=True))
            return ""
        except:
            return ""
    
    def _extract_selected_course_info(self, soup: BeautifulSoup, course_code: str,
                                      expected_course_name: Optional[str] = None,
                                      strict: bool = False) -> Dict[str, str]:
        """
        Extract course information from the selected dropdown option.
        
        Args:
            soup: BeautifulSoup object containing the page HTML
            course_code: The course code that was selected
            
        Returns:
            Dictionary with 'course_code' and 'course_name'
        """
        try:
            # Find the course dropdown
            course_select = soup.find('select', {'id': 'courseId'}) or soup.find('select', {'name': 'courseName'})
            
            if course_select:
                from app.utils.assignment_identity import normalize_label
                options = course_select.find_all('option')
                matches = [option for option in options
                           if unquote(option.get('value', '').strip()) == unquote(course_code.strip())]
                if strict:
                    expected = normalize_label(expected_course_name)
                    named = [option for option in options if normalize_label(option.get_text(strip=True)) == expected]
                    if not expected or len(named) != 1:
                        raise LMSParsingError('Course name is missing or ambiguous')
                    selected = course_select.find_all('option', selected=True)
                    if len(selected) > 1 or (selected and selected[0] is not named[0]):
                        raise LMSParsingError('University returned a different course')
                    if not selected and (len(matches) != 1 or matches[0] is not named[0]):
                        raise LMSParsingError('University course context could not be verified')
                    if expected == normalize_label(unquote(named[0].get('value', ''))):
                        raise LMSParsingError('Course label is a transport token')
                    return {'course_code': course_code, 'course_name': expected_course_name.strip()}
                # Find the selected option or the option with matching value
                selected_option = matches[0] if len(matches) == 1 else None
                
                if selected_option:
                    course_name = selected_option.get_text(strip=True)
                    logger.debug(f"Extracted course info from dropdown: {course_code} - {course_name}")
                    return {
                        'course_code': course_code,
                        'course_name': course_name
                    }
            
            # Fallback: use course code as name
            # Keep unknown labels empty instead of exposing the historical token fallback.
            if strict:
                raise LMSParsingError('University course dropdown is missing')
            logger.debug('Could not extract a course name from dropdown')
            return {
                'course_code': course_code,
                'course_name': ''
            }
            
        except Exception as e:
            if strict:
                raise
            logger.warning(f"Error extracting course info from dropdown: {e}")
            return {
                'course_code': course_code,
                'course_name': ''
            }
    
    def _extract_course_info(self, cells, column_mapping: Dict[str, int]) -> Dict[str, str]:
        """Extract course code and name from cells."""
        course_text = self._extract_cell_text(cells, column_mapping, 'course')
        
        # Try to split course code and name
        # Common formats: "CS101 - Computer Science", "CS101", "Computer Science (CS101)"
        course_code = ""
        course_name = ""
        
        if course_text:
            # Pattern 1: "CODE - NAME" or "CODE: NAME"
            match = re.match(r'^([A-Z]{2,4}\d{3,4})\s*[-:]\s*(.+)$', course_text, re.IGNORECASE)
            if match:
                course_code = match.group(1).upper()
                course_name = match.group(2).strip()
            else:
                # Pattern 2: "NAME (CODE)" 
                match = re.match(r'^(.+)\s*\(([A-Z]{2,4}\d{3,4})\)$', course_text, re.IGNORECASE)
                if match:
                    course_name = match.group(1).strip()
                    course_code = match.group(2).upper()
                else:
                    # Pattern 3: Just code "CS101"
                    match = re.match(r'^([A-Z]{2,4}\d{3,4})$', course_text, re.IGNORECASE)
                    if match:
                        course_code = match.group(1).upper()
                        course_name = course_code
                    else:
                        # Fallback: use entire text as both
                        course_code = course_text[:10]  # Limit length
                        course_name = course_text
        
        return {
            'course_code': course_code,
            'course_name': course_name
        }
    
    def _extract_numeric_field(self, cells, column_mapping: Dict[str, int], field: str) -> Optional[float]:
        """Extract numeric value from a cell."""
        cell_text = self._extract_cell_text(cells, column_mapping, field)
        return self._parse_numeric_value(cell_text)
    
    def _extract_submission_status(self, cells, column_mapping: Dict[str, int]) -> str:
        """
        Extract and normalize submission status from the Action column.
        Checks for "Deadline Exceeded" div, submit button, or delete button.
        """
        try:
            if 'submission_status' in column_mapping and column_mapping['submission_status'] < len(cells):
                cell = cells[column_mapping['submission_status']]
                
                # Check for "Deadline Exceeded" div (red background)
                deadline_div = cell.find('div', string=lambda text: text and 'deadline exceeded' in text.lower())
                if deadline_div:
                    logger.debug("Found 'Deadline Exceeded' div -> status: overdue")
                    return 'overdue'
                
                # Check for delete button (yellow background) - means already submitted
                delete_link = cell.find('a', string=lambda text: text and 'delete' in text.lower())
                if delete_link:
                    logger.debug("Found 'Delete' button -> status: submitted")
                    return 'submitted'
                
                # Check for submit button/link (green background) - means can submit
                submit_link = cell.find('a', string=lambda text: text and 'submit' in text.lower())
                if submit_link:
                    logger.debug("Found 'Submit' button -> status: pending (no submission)")
                    return 'pending'
                
                # If no specific button found, default to pending
                logger.debug("No specific action button found -> status: pending (default)")
                return 'pending'
            
            return 'pending'
        except Exception as e:
            logger.error(f"Error extracting submission status: {e}", exc_info=True)
            return 'pending'
    
    def _extract_deadline(self, cells, column_mapping: Dict[str, int]) -> Optional[datetime]:
        """
        Extract deadline from cells. 
        Returns the extended deadline if available, otherwise the actual deadline.
        """
        try:
            if 'deadline' in column_mapping and column_mapping['deadline'] < len(cells):
                cell = cells[column_mapping['deadline']]
                
                # Look for extended deadline first (label-warning)
                extended_label = cell.find('small', class_='label-warning')
                if extended_label:
                    extended_text = extended_label.get_text(strip=True)
                    extended_date = self._parse_date(extended_text)
                    if extended_date:
                        return extended_date
                
                # Fall back to actual deadline (label-info)
                actual_label = cell.find('small', class_='label-info')
                if actual_label:
                    actual_text = actual_label.get_text(strip=True)
                    actual_date = self._parse_date(actual_text)
                    if actual_date:
                        return actual_date
                
                # If no labels found, try extracting all text
                deadline_text = self._extract_cell_text(cells, column_mapping, 'deadline')
                return self._parse_date(deadline_text)
            
            return None
        except Exception as e:
            logger.debug(f"Error extracting deadline: {e}")
            return None
    
    def _validate_lms_url(self, url: str) -> Optional[str]:
        """Fix and ensure URL contains /Student/ path."""
        if not url:
            return None
        
        # If URL is missing /Student/, fix it
        if '/Student/' not in url and 'lms.bahria.edu.pk' in url:
            # Replace any existing role (Teacher, Admin, etc.) with Student
            if '/Teacher/' in url:
                fixed_url = url.replace('/Teacher/', '/Student/')
                logger.debug(f"Fixed LMS URL (Teacher->Student): {url} -> {fixed_url}")
                return fixed_url
            elif '/Admin/' in url:
                fixed_url = url.replace('/Admin/', '/Student/')
                logger.debug(f"Fixed LMS URL (Admin->Student): {url} -> {fixed_url}")
                return fixed_url
            # Add /Student/ after domain if no role specified
            elif '/Download.php' in url:
                fixed_url = url.replace('/Download.php', '/Student/Download.php')
                logger.debug(f"Fixed LMS URL (added Student): {url} -> {fixed_url}")
                return fixed_url
        
        return url
    
    def _extract_file_links(self, cells, column_mapping: Dict[str, int], field: str) -> Optional[str]:
        """Extract file download links from a cell."""
        try:
            if field in column_mapping and column_mapping[field] < len(cells):
                cell = cells[column_mapping[field]]
                links = self._extract_links_from_cell(cell, self.lms_base_url)
                validated_url = self._validate_lms_url(links[0]) if links else None
                return validated_url
            return None
        except:
            return None
    
    def _extract_assignment_files(self, cells, column_mapping: Dict[str, int]) -> Dict[str, Optional[str]]:
        """
        Extract assignment and solution files from the assignment column.
        Returns dict with 'assignment_file' and 'solution_file' keys.
        """
        result = {'assignment_file': None, 'solution_file': None}
        
        try:
            if 'assignment_file' in column_mapping and column_mapping['assignment_file'] < len(cells):
                cell = cells[column_mapping['assignment_file']]
                
                # Find all links in the cell
                links = cell.find_all('a', href=True)
                logger.debug(f"Found {len(links)} links in assignment column")
                
                for link in links:
                    link_text = link.get_text(strip=True).lower()
                    href = link.get('href')
                    logger.debug(f"Link text: '{link_text}', href: {href[:80] if href else 'None'}...")
                    
                    # Convert relative URLs to absolute URLs
                    if href:
                        if href.startswith('http://') or href.startswith('https://'):
                            absolute_url = href
                        else:
                            absolute_url = urljoin(self.lms_base_url, href)
                        
                        validated_url = self._validate_lms_url(absolute_url)
                        
                        if 'assignment' in link_text and not result['assignment_file'] and validated_url:
                            result['assignment_file'] = validated_url
                            logger.debug(f"Set assignment_file: {validated_url[:80]}...")
                        elif 'solution' in link_text and not result['solution_file'] and validated_url:
                            result['solution_file'] = validated_url
                            logger.debug(f"Set solution_file: {validated_url[:80]}...")
                
                logger.debug(f"Final result: assignment_file={result['assignment_file'] is not None}, solution_file={result['solution_file'] is not None}")
                if result['solution_file']:
                    logger.debug(f"Solution file extracted: {result['solution_file'][:100]}...")
                else:
                    logger.warning(f"No solution file found in assignment column")
            
            return result
        except Exception as e:
            logger.error(f"Error extracting assignment files: {e}", exc_info=True)
            return result
    
    def _extract_returned_submission_data(self, cells, column_mapping: Dict[str, int]) -> Dict[str, Optional[str]]:
        """
        Extract returned file and comments from the returned submission column.
        Returns dict with 'returned_file' and 'comments' keys.
        """
        result = {'returned_file': None, 'comments': None}
        
        try:
            if 'comments' in column_mapping and column_mapping['comments'] < len(cells):
                cell = cells[column_mapping['comments']]
                
                # Look for file link first
                file_link = cell.find('a', href=True)
                if file_link:
                    href = file_link.get('href')
                    # Convert relative URLs to absolute URLs
                    if href:
                        if href.startswith('http://') or href.startswith('https://'):
                            absolute_url = href
                        else:
                            absolute_url = urljoin(self.lms_base_url, href)
                        
                        validated_url = self._validate_lms_url(absolute_url)
                        result['returned_file'] = validated_url
                
                # Look for comments in span title attribute
                span = cell.find('span', {'title': True})
                if span and span.get('title'):
                    comments_text = span.get('title').strip()
                    # Return comments even if they say "No comments" for UI consistency
                    if comments_text:
                        result['comments'] = comments_text
                else:
                    # If no span found but cell has text, use default
                    cell_text = cell.get_text(strip=True)
                    if cell_text and cell_text != '---':
                        result['comments'] = "No comments."
            
            return result
        except Exception as e:
            logger.debug(f"Error extracting returned submission data: {e}")
            return result
    
    def _extract_action_ids_from_row(self, row, cells, column_mapping: Dict[str, int]) -> Tuple[Optional[str], Optional[str], Optional[str], Optional[str]]:
        """
        Extract submission_id, assignment_id, offered_course_id, and student_id from assignment row's action button.
        Handles both delete button (for existing submissions) and submit button (for new submissions).
        These IDs are needed for delete and upload operations.
        
        Args:
            row: BeautifulSoup table row element
            cells: List of cell elements
            column_mapping: Column index mapping
            
        Returns:
            Tuple of (submission_id, assignment_id, offered_course_id, student_id) or (None, None, None, None)
        """
        try:
            # Action column should be at index 6 (7th column)
            action_col_index = column_mapping.get('action', 6)
            
            if action_col_index >= len(cells):
                return None, None, None, None
            
            action_cell = cells[action_col_index]
            
            # Look for action button link with modal target (delete or submit)
            action_link = action_cell.find('a', {'data-toggle': 'modal'})
            
            if not action_link:
                # No action button - might be "Deadline Exceeded" or "No Submission"
                logger.debug(f"No modal action button found in action cell")
                return None, None, None, None
            
            # Get the modal target ID
            modal_target = action_link.get('data-target', '')
            if not modal_target:
                logger.debug(f"Action link found but no data-target attribute")
                return None, None, None, None
            
            logger.debug(f"Found action link with modal target: {modal_target}")
            
            # Find the corresponding modal in the page
            modal_id = modal_target.replace('#', '')
            
            # Get the full page soup from the row's parent
            page_soup = row.find_parent('html')
            if not page_soup:
                page_soup = row.find_parent('body')
            if not page_soup:
                logger.debug(f"Could not find page root to search for modal {modal_id}")
                return None, None, None, None
            
            modal = page_soup.find('div', {'id': modal_id})
            
            if not modal:
                logger.warning(f"Modal {modal_id} not found in page - IDs cannot be extracted for upload")
                return None, None, None, None
            
            logger.debug(f"Found modal {modal_id}")
            
            # Check if this is a delete modal or submit modal
            # Delete modal has btn-danger link, submit modal has form with hidden inputs
            
            # Try delete modal first
            actual_delete_link = modal.find('a', {'class': 'btn-danger'})
            if actual_delete_link:
                # This is a delete modal
                delete_url = actual_delete_link.get('href', '')
                if delete_url:
                    # Delete modal doesn't have student_id
                    submission_id, assignment_id, offered_course_id = self._parse_delete_url_ids(delete_url)
                    return submission_id, assignment_id, offered_course_id, None
            
            # Try submit modal (has form with hidden inputs)
            form = modal.find('form')
            if form:
                # This is a submit modal - returns all 4 IDs
                return self._parse_submit_form_ids(form)
            
            logger.debug(f"Could not find delete link or submit form in modal {modal_id}")
            return None, None, None, None
            
        except Exception as e:
            logger.debug(f"Error extracting action IDs from row: {e}")
            return None, None, None, None
    
    def _parse_delete_url_ids(self, delete_url: str) -> Tuple[Optional[str], Optional[str], Optional[str]]:
        """Parse IDs from delete URL."""
        try:
            from urllib.parse import urlparse, parse_qs, unquote
            parsed = urlparse(delete_url)
            params = parse_qs(parsed.query)
            
            submission_id = None
            assignment_id = None
            offered_course_id = None
            
            # Keep IDs in base64-encoded format as the LMS server expects them
            # Extract submission ID (aSubId)
            if 'aSubId' in params:
                submission_id = unquote(params['aSubId'][0])
                logger.debug(f"Extracted submission_id (base64): {submission_id}")
            
            # Extract assignment ID (a)
            if 'a' in params:
                assignment_id = params['a'][0]
                logger.debug(f"Extracted assignment_id (base64): {assignment_id}")
            
            # Extract offered course ID (oc)
            if 'oc' in params:
                offered_course_id = params['oc'][0]
                logger.debug(f"Extracted offered_course_id (base64): {offered_course_id}")
            
            if submission_id and assignment_id and offered_course_id:
                logger.debug(f"Extracted delete IDs (base64 encoded) - submission: {submission_id}, assignment: {assignment_id}, course: {offered_course_id}")
            
            return submission_id, assignment_id, offered_course_id
        except Exception as e:
            logger.debug(f"Error parsing delete URL: {e}")
            return None, None, None
    
    def _parse_submit_form_ids(self, form) -> Tuple[Optional[str], Optional[str], Optional[str], Optional[str]]:
        """Parse IDs from submit form hidden inputs.
        
        Returns:
            Tuple of (submission_id, assignment_id, offered_course_id, student_id)
        """
        try:
            # For submit form, we don't have submission_id (it doesn't exist yet)
            # But we have assignmentID, offeredCourseID, and studentID
            submission_id = None  # No submission yet
            assignment_id = None
            offered_course_id = None
            student_id = None
            
            # Find hidden inputs
            assignment_input = form.find('input', {'name': 'assignmentID'})
            if assignment_input:
                # Keep the ID in base64-encoded format as the LMS server expects it
                assignment_id = assignment_input.get('value', '')
                if assignment_id:
                    logger.debug(f"Extracted assignmentID (base64): {assignment_id}")
            
            course_input = form.find('input', {'name': 'offeredCourseID'})
            if course_input:
                # Keep the ID in base64-encoded format as the LMS server expects it
                offered_course_id = course_input.get('value', '')
                if offered_course_id:
                    logger.debug(f"Extracted offeredCourseID (base64): {offered_course_id}")
            
            # Extract student ID from form
            student_input = form.find('input', {'name': 'studentID'})
            if student_input:
                # Keep the ID in base64-encoded format as the LMS server expects it
                student_id = student_input.get('value', '')
                if student_id:
                    logger.debug(f"Extracted studentID (base64): {student_id}")
            
            if assignment_id and offered_course_id:
                logger.debug(f"Extracted submit IDs (base64 encoded) - assignment: {assignment_id}, course: {offered_course_id}, student: {student_id}")
            
            return submission_id, assignment_id, offered_course_id, student_id
        except Exception as e:
            logger.debug(f"Error parsing submit form: {e}")
            return None, None, None, None
    
    def _extract_deadline_data(self, cells, column_mapping: Dict[str, int]) -> Dict[str, Optional[datetime]]:
        """
        Extract both actual and extended deadlines from the deadline column.
        Returns dict with 'primary_deadline', 'actual_deadline', and 'extended_deadline' keys.
        """
        result = {
            'primary_deadline': None,
            'actual_deadline': None,
            'extended_deadline': None
        }
        
        try:
            if 'deadline' in column_mapping and column_mapping['deadline'] < len(cells):
                cell = cells[column_mapping['deadline']]
                
                # Look for extended deadline first (label-warning)
                extended_label = cell.find('small', class_='label-warning')
                if extended_label:
                    extended_text = extended_label.get_text(strip=True)
                    extended_date = self._parse_date(extended_text)
                    if extended_date:
                        result['extended_deadline'] = extended_date
                        result['primary_deadline'] = extended_date  # Use extended as primary
                
                # Look for actual deadline (label-info)
                actual_label = cell.find('small', class_='label-info')
                if actual_label:
                    actual_text = actual_label.get_text(strip=True)
                    actual_date = self._parse_date(actual_text)
                    if actual_date:
                        result['actual_deadline'] = actual_date
                        # If no extended deadline, use actual as primary
                        if not result['primary_deadline']:
                            result['primary_deadline'] = actual_date
                
                # If no labels found, try extracting all text as fallback
                if not result['primary_deadline']:
                    deadline_text = cell.get_text(strip=True)
                    fallback_date = self._parse_date(deadline_text)
                    if fallback_date:
                        result['primary_deadline'] = fallback_date
                        result['actual_deadline'] = fallback_date
            
            return result
        except Exception as e:
            logger.debug(f"Error extracting deadline data: {e}")
            return result
    
    def _extract_remarks(self, cells, column_mapping: Dict[str, int]) -> Optional[str]:
        """
        Extract remarks from the title attribute of a span in the remarks cell.
        Always returns a value for UI consistency - either actual remarks or default text.
        """
        try:
            if 'remarks' in column_mapping and column_mapping['remarks'] < len(cells):
                cell = cells[column_mapping['remarks']]
                
                # Look for span with title attribute (the "Hover To View" element)
                span = cell.find('span', {'title': True})
                if span and span.get('title'):
                    remarks_text = span.get('title').strip()
                    # Return the actual remarks text
                    if remarks_text:
                        return remarks_text
                
                # If no span found, return default text for UI consistency
                return "No remarks"
            
            return "No remarks"
        except Exception as e:
            logger.debug(f"Error extracting remarks: {e}")
            return "No remarks"
    
    def parse_lecture_data(self, soup: BeautifulSoup, course_info: Optional[Dict[str, str]] = None) -> List[LMSLectureNote]:
        """
        Parse lecture notes data from HTML soup.
        
        Args:
            soup: BeautifulSoup object containing lecture notes page HTML
            course_info: Optional course information dict with 'course_code' and 'course_name'
            
        Returns:
            List of LMSLectureNote objects
            
        Raises:
            LMSParsingError: When lecture notes data parsing fails
        """
        try:
            logger.debug("Parsing lecture notes data from HTML")
            lecture_notes = []
            
            # Look for lecture notes table - try multiple selectors
            table_selectors = [
                'table.lecture-table',
                'table#lectures',
                'table[class*="lecture"]',
                'table[id*="lecture"]',
                '.lecture-container table',
                '.notes-container table',
                '.content table',
                'table.table'  # Common Bootstrap table class
            ]
            
            lecture_table = None
            for selector in table_selectors:
                lecture_table = soup.select_one(selector)
                if lecture_table:
                    logger.debug(f"Found lecture table with selector: {selector}")
                    break
            
            # Fallback: find table with lecture-related content
            if not lecture_table:
                all_tables = soup.find_all('table')
                for table in all_tables:
                    # Check if table has lecture-related headers
                    headers = table.find_all('th')
                    header_text = ' '.join([h.get_text(strip=True).lower() for h in headers])
                    if any(keyword in header_text for keyword in ['week', 'lecture', 'title', 'file']):
                        lecture_table = table
                        logger.debug(f"Found lecture table by content matching")
                        break
            
            if not lecture_table:
                logger.warning("No lecture notes table found in HTML")
                # Log what we did find
                all_tables = soup.find_all('table')
                logger.debug(f"Found {len(all_tables)} total tables in HTML")
                for i, table in enumerate(all_tables[:3]):  # Log first 3 tables
                    table_classes = table.get('class', [])
                    table_id = table.get('id', '')
                    logger.debug(f"Table {i}: classes={table_classes}, id={table_id}")
                return []
            
            # Extract table rows
            rows = lecture_table.find_all('tr')
            if not rows:
                logger.warning("No rows found in lecture notes table")
                return []
            
            # Find header row to determine column structure
            header_row = None
            data_rows = []
            
            for i, row in enumerate(rows):
                cells = row.find_all(['th', 'td'])
                if not cells:
                    continue
                    
                # Check if this looks like a header row
                if row.find('th') or (i == 0 and self._looks_like_lecture_header_row(cells)):
                    header_row = row
                    logger.debug(f"Found header row at index {i}")
                else:
                    data_rows.append(row)
            
            if not data_rows:
                logger.warning("No data rows found in lecture notes table")
                return []
            
            # Determine column mapping from header
            column_mapping = self._map_lecture_columns(header_row) if header_row else self._guess_lecture_columns(data_rows[0])
            logger.debug(f"Lecture column mapping: {column_mapping}")
            
            # Parse each data row
            for row_index, row in enumerate(data_rows):
                try:
                    lecture_note = self._parse_lecture_row(row, column_mapping, row_index, course_info=course_info)
                    if lecture_note:
                        lecture_notes.append(lecture_note)
                except Exception as e:
                    logger.warning(f"Failed to parse lecture row {row_index}: {e}")
                    continue
            
            logger.debug(f"Successfully parsed {len(lecture_notes)} lecture notes from HTML")
            return lecture_notes
            
        except Exception as e:
            logger.error(f"Failed to parse lecture notes data: {e}")
            raise LMSParsingError(f"Lecture notes data parsing failed: {e}")
    
    def _looks_like_lecture_header_row(self, cells) -> bool:
        """
        Check if a row looks like a lecture notes header row.
        
        Args:
            cells: List of cell elements
            
        Returns:
            True if row appears to be a header
        """
        if not cells:
            return False
        
        # Common header keywords for lecture notes
        header_keywords = [
            'week', 'lecture', 'title', 'topic', 'course', 'file', 'download',
            'video', 'link', 'material', 'notes', 'remarks', 'date', 'upload'
        ]
        
        cell_texts = [cell.get_text(strip=True).lower() for cell in cells]
        
        # Check if any cell contains header keywords
        for text in cell_texts:
            for keyword in header_keywords:
                if keyword in text:
                    return True
        
        return False
    
    def _map_lecture_columns(self, header_row) -> Dict[str, int]:
        """
        Map lecture notes data columns based on header row.
        
        Args:
            header_row: Header row element
            
        Returns:
            Dictionary mapping field names to column indices
        """
        column_mapping = {}
        
        if not header_row:
            return column_mapping
        
        cells = header_row.find_all(['th', 'td'])
        
        for i, cell in enumerate(cells):
            header_text = cell.get_text(strip=True).lower()
            
            # Map common header variations to field names
            # Based on actual LMS structure: Week #, Lecture Title, Lecture File(s), Video Link(s), Video Link, Remarks/Reference
            if any(keyword in header_text for keyword in ['week', 'no', 'number', '#']):
                column_mapping['week_no'] = i
            elif 'lecture title' in header_text or (any(keyword in header_text for keyword in ['title', 'topic']) and 'lecture' in header_text):
                column_mapping['title'] = i
            elif any(keyword in header_text for keyword in ['course', 'subject']):
                column_mapping['course'] = i
            elif 'lecture file' in header_text or ('file' in header_text and 'lecture' in header_text):
                column_mapping['lecture_files'] = i
            elif 'self recorded' in header_text or ('video' in header_text and 'self' in header_text):
                column_mapping['video_links'] = i
            elif 'other' in header_text or 'supplementary' in header_text or ('video' in header_text and 'other' in header_text):
                column_mapping['supplementary_links'] = i
            elif any(keyword in header_text for keyword in ['remarks', 'reference', 'comment', 'note', 'description']):
                column_mapping['remarks'] = i
            elif any(keyword in header_text for keyword in ['date', 'upload', 'posted']):
                column_mapping['upload_date'] = i
        
        return column_mapping
    
    def _guess_lecture_columns(self, first_row) -> Dict[str, int]:
        """
        Guess column mapping based on first data row content.
        
        Args:
            first_row: First data row element
            
        Returns:
            Dictionary mapping field names to column indices
        """
        column_mapping = {}
        
        if not first_row:
            return column_mapping
        
        cells = first_row.find_all(['td', 'th'])
        
        # Based on actual LMS structure from debug HTML:
        # Column 0: Week # (e.g., "1", "2", "3")
        # Column 1: Lecture Title (e.g., "Lecture1", "Lecture2")
        # Column 2: Lecture File(s) (download links)
        # Column 3: Video Link(s) Self Recorded (empty or video links)
        # Column 4: Video Link Other/Supplementary (empty or video links)
        # Column 5: Remarks/Reference (empty or text)
        if len(cells) >= 6:
            column_mapping = {
                'week_no': 0,
                'title': 1,
                'lecture_files': 2,
                'video_links': 3,
                'supplementary_links': 4,
                'remarks': 5
            }
        elif len(cells) >= 5:
            column_mapping = {
                'week_no': 0,
                'title': 1,
                'lecture_files': 2,
                'video_links': 3,
                'remarks': 4
            }
        elif len(cells) >= 3:
            # Minimal mapping
            column_mapping = {
                'week_no': 0,
                'title': 1,
                'lecture_files': 2
            }
        
        logger.debug(f"Guessed lecture column mapping for {len(cells)} columns: {column_mapping}")
        return column_mapping
    
    def _parse_lecture_row(self, row, column_mapping: Dict[str, int], row_index: int, 
                          course_info: Optional[Dict[str, str]] = None) -> Optional[LMSLectureNote]:
        """
        Parse a single lecture notes row into LMSLectureNote object.
        
        Args:
            row: Table row element
            column_mapping: Column index mapping
            row_index: Row index for error reporting
            course_info: Optional course information dict with 'course_code' and 'course_name'
            
        Returns:
            LMSLectureNote object or None if parsing fails
        """
        try:
            cells = row.find_all(['td', 'th'])
            if not cells:
                return None
            
            # Extract basic fields
            week_no = self._extract_week_number(cells, column_mapping)
            title = self._extract_cell_text(cells, column_mapping, 'title')
            
            # Use provided course_info if available, otherwise try to extract from cells
            if course_info:
                extracted_course_info = course_info
            else:
                extracted_course_info = self._extract_course_info(cells, column_mapping)
            
            # Extract file information with metadata
            lecture_files_info = []
            video_links_info = []
            supplementary_links_info = []
            
            # Extract lecture files with metadata
            if 'lecture_files' in column_mapping and column_mapping['lecture_files'] < len(cells):
                cell = cells[column_mapping['lecture_files']]
                lecture_files_info = self._extract_file_info_from_cell(cell, self.lms_base_url)
            
            # Extract video links with metadata
            if 'video_links' in column_mapping and column_mapping['video_links'] < len(cells):
                cell = cells[column_mapping['video_links']]
                video_links_info = self._extract_file_info_from_cell(cell, self.lms_base_url)
            
            # Extract supplementary links with metadata
            if 'supplementary_links' in column_mapping and column_mapping['supplementary_links'] < len(cells):
                cell = cells[column_mapping['supplementary_links']]
                supplementary_links_info = self._extract_file_info_from_cell(cell, self.lms_base_url)
            
            # Also extract video links using the smart video detection
            video_links = self._extract_video_links(cells, column_mapping)
            
            # Merge video links into video_links_info if not already present
            for video_url in video_links:
                if not any(info['url'] == video_url for info in video_links_info):
                    video_links_info.append({
                        'url': video_url,
                        'type': 'VIDEO',
                        'size': None,
                        'name': os.path.basename(urlparse(video_url).path) or 'Video'
                    })
            
            # Extract backward-compatible URL lists
            lecture_files = [info['url'] for info in lecture_files_info]
            video_links_urls = [info['url'] for info in video_links_info]
            supplementary_links = [info['url'] for info in supplementary_links_info]
            
            remarks = self._extract_cell_text(cells, column_mapping, 'remarks')
            upload_date = self._extract_upload_date(cells, column_mapping)
            
            # Validate required fields
            if not title:
                logger.debug(f"Skipping lecture row {row_index}: missing title")
                return None
            
            # If no course info available, use placeholder
            if not extracted_course_info or not extracted_course_info.get('course_code'):
                logger.debug(f"Lecture row {row_index} has no course info, using placeholder")
                extracted_course_info = {'course_code': 'UNKNOWN', 'course_name': 'Unknown Course'}
            
            # Create lecture note object
            lecture_note = LMSLectureNote(
                week_no=week_no,
                title=title,
                course_code=extracted_course_info['course_code'],
                course_name=extracted_course_info['course_name'],
                lecture_files=lecture_files,
                video_links=video_links_urls,
                supplementary_links=supplementary_links,
                lecture_files_info=lecture_files_info,
                video_links_info=video_links_info,
                supplementary_links_info=supplementary_links_info,
                remarks=remarks,
                upload_date=upload_date
            )
            
            logger.debug(f"Successfully parsed lecture note: {lecture_note.title} ({lecture_note.course_code}) with {len(lecture_files_info)} files, {len(video_links_info)} videos")
            return lecture_note
            
        except Exception as e:
            logger.warning(f"Failed to parse lecture row {row_index}: {e}")
            return None
    
    def _extract_week_number(self, cells, column_mapping: Dict[str, int]) -> int:
        """Extract week number from cells."""
        try:
            if 'week_no' in column_mapping:
                cell_text = self._extract_cell_text(cells, column_mapping, 'week_no')
                # Extract number from text
                numbers = re.findall(r'\d+', cell_text)
                if numbers:
                    return int(numbers[0])
            return 0
        except:
            return 0
    
    def _extract_multiple_links(self, cells, column_mapping: Dict[str, int], field: str) -> List[str]:
        """Extract multiple links from a cell."""
        try:
            if field in column_mapping and column_mapping[field] < len(cells):
                cell = cells[column_mapping[field]]
                return self._extract_links_from_cell(cell, self.lms_base_url)
            return []
        except:
            return []
    
    def _extract_file_info_from_cell(self, cell, base_url: str) -> List[Dict[str, Any]]:
        """
        Extract file information including URL, type, and size from a cell.
        
        Args:
            cell: BeautifulSoup cell element
            base_url: Base URL for resolving relative links
            
        Returns:
            List of dictionaries with file info (url, type, size, name)
        """
        try:
            files_info = []
            link_elements = cell.find_all('a', href=True)
            
            for link in link_elements:
                href = link.get('href')
                if not href:
                    continue
                
                # Convert relative URLs to absolute
                # Special handling for Download.php - it should be in /Student/ directory
                if 'Download.php' in href and not href.startswith('http'):
                    # Ensure Download.php is in the Student directory
                    if not href.startswith('/Student/'):
                        href = f"/Student/{href}" if not href.startswith('/') else f"/Student{href}"
                
                absolute_url = urljoin(base_url, href)
                
                # Try to extract filename from LMS download URL
                filename = self._extract_filename_from_lms_url(absolute_url)
                
                # If no filename extracted, use link text or URL basename
                if not filename:
                    filename = link.get_text(strip=True) or os.path.basename(urlparse(absolute_url).path)
                
                # Extract file type from filename or URL
                file_type = self._determine_file_type(filename if filename else absolute_url, link.get_text(strip=True))
                
                # Try to extract file size from link text or nearby elements
                file_size = self._extract_file_size_from_text(link.get_text(strip=True))
                
                # If size not in link text, check parent or sibling elements
                if not file_size:
                    parent = link.parent
                    if parent:
                        parent_text = parent.get_text(strip=True)
                        file_size = self._extract_file_size_from_text(parent_text)
                
                files_info.append({
                    'url': absolute_url,
                    'type': file_type,
                    'size': file_size,
                    'name': filename
                })
            
            return files_info
            
        except Exception as e:
            logger.debug(f"Error extracting file info from cell: {e}")
            return []
    
    def _extract_filename_from_lms_url(self, url: str) -> Optional[str]:
        """
        Extract filename from LMS download URL.
        LMS uses Download.php?k=<base64_encoded_params> format.
        The encoded params contain: id,date,type,num,filename,referrer
        
        Args:
            url: Download URL
            
        Returns:
            Extracted filename or None
        """
        try:
            import base64
            from urllib.parse import urlparse, parse_qs
            
            # Check if this is a Download.php URL
            if 'Download.php' not in url:
                return None
            
            # Parse URL and extract 'k' parameter
            parsed = urlparse(url)
            query_params = parse_qs(parsed.query)
            
            if 'k' not in query_params:
                return None
            
            # Decode the base64 parameter
            encoded_param = query_params['k'][0]
            
            # URL decode first (in case it's URL encoded)
            from urllib.parse import unquote
            encoded_param = unquote(encoded_param)
            
            # Base64 decode
            decoded = base64.b64decode(encoded_param).decode('utf-8')
            
            # Split by comma - format is: id,date,type,num,filename,referrer
            parts = decoded.split(',')
            
            if len(parts) >= 5:
                filename = parts[4]  # 5th element is the filename
                logger.debug(f"Extracted filename from LMS URL: {filename}")
                return filename
            
            return None
            
        except Exception as e:
            logger.debug(f"Error extracting filename from LMS URL: {e}")
            return None
    
    def _determine_file_type(self, url: str, link_text: str = '') -> str:
        """
        Determine file type from URL or link text.
        
        Args:
            url: File URL
            link_text: Link text that might contain file type info
            
        Returns:
            File type string (PDF, PPT, DOC, etc.) or 'Unknown'
        """
        if not url:
            return 'Unknown'
        
        url_lower = url.lower()
        text_lower = link_text.lower()
        
        # Check URL extension
        if url_lower.endswith('.pdf'):
            return 'PDF'
        elif url_lower.endswith(('.ppt', '.pptx')):
            return 'PPT'
        elif url_lower.endswith(('.doc', '.docx')):
            return 'DOC'
        elif url_lower.endswith(('.xls', '.xlsx')):
            return 'XLS'
        elif url_lower.endswith('.txt'):
            return 'TXT'
        elif url_lower.endswith(('.zip', '.rar', '.7z')):
            return 'ZIP'
        elif url_lower.endswith(('.jpg', '.jpeg', '.png', '.gif', '.bmp')):
            return 'IMG'
        elif url_lower.endswith(('.mp4', '.avi', '.mov', '.wmv', '.flv', '.webm', '.mkv')):
            return 'VIDEO'
        elif url_lower.endswith(('.mp3', '.wav', '.ogg', '.m4a')):
            return 'AUDIO'
        
        # Check link text for file type indicators
        if 'pdf' in text_lower:
            return 'PDF'
        elif 'powerpoint' in text_lower or 'ppt' in text_lower or 'presentation' in text_lower:
            return 'PPT'
        elif 'word' in text_lower or 'doc' in text_lower or 'document' in text_lower:
            return 'DOC'
        elif 'excel' in text_lower or 'xls' in text_lower or 'spreadsheet' in text_lower:
            return 'XLS'
        elif 'video' in text_lower:
            return 'VIDEO'
        elif 'audio' in text_lower:
            return 'AUDIO'
        
        # Check for common file patterns in URL
        if '/download/' in url_lower or '/file/' in url_lower or '/attachment/' in url_lower:
            # Try to extract extension from query parameters
            parsed = urlparse(url)
            if parsed.query:
                for param in parsed.query.split('&'):
                    if 'file=' in param or 'name=' in param:
                        param_value = param.split('=')[1] if '=' in param else ''
                        if '.' in param_value:
                            ext = param_value.split('.')[-1].lower()
                            if ext in ['pdf', 'ppt', 'pptx', 'doc', 'docx', 'xls', 'xlsx']:
                                return ext.upper().replace('X', '')
        
        return 'Unknown'
    
    def _extract_file_size_from_text(self, text: str) -> Optional[str]:
        """
        Extract file size from text.
        
        Args:
            text: Text that might contain file size
            
        Returns:
            File size string (e.g., "2.5 MB") or None
        """
        if not text:
            return None
        
        # Common patterns for file sizes
        # Examples: "2.5 MB", "1.2MB", "500 KB", "1.5 GB", "100KB"
        size_pattern = r'(\d+(?:\.\d+)?)\s*(KB|MB|GB|TB|kb|mb|gb|tb)'
        
        match = re.search(size_pattern, text, re.IGNORECASE)
        if match:
            size_value = match.group(1)
            size_unit = match.group(2).upper()
            return f"{size_value} {size_unit}"
        
        return None
    
    def _extract_video_links(self, cells, column_mapping: Dict[str, int]) -> List[str]:
        """Extract video links from cells, including both direct links and embedded videos."""
        try:
            video_links = []
            
            # First try the dedicated video links column
            if 'video_links' in column_mapping:
                video_links.extend(self._extract_multiple_links(cells, column_mapping, 'video_links'))
            
            # Also check lecture files column for video links
            if 'lecture_files' in column_mapping and column_mapping['lecture_files'] < len(cells):
                cell = cells[column_mapping['lecture_files']]
                all_links = self._extract_links_from_cell(cell, self.lms_base_url)
                
                # Filter for video-related links
                for link in all_links:
                    if self._is_video_link(link):
                        video_links.append(link)
            
            # Look for embedded video elements (iframe, video tags)
            for cell in cells:
                # Check for YouTube embeds
                youtube_iframes = cell.find_all('iframe', src=True)
                for iframe in youtube_iframes:
                    src = iframe.get('src')
                    if 'youtube.com' in src or 'youtu.be' in src:
                        video_links.append(src)
                
                # Check for video tags
                video_tags = cell.find_all('video', src=True)
                for video in video_tags:
                    src = video.get('src')
                    if src:
                        video_links.append(urljoin(self.lms_base_url, src))
            
            # Remove duplicates while preserving order
            unique_links = []
            for link in video_links:
                if link not in unique_links:
                    unique_links.append(link)
            
            return unique_links
            
        except Exception as e:
            logger.debug(f"Error extracting video links: {e}")
            return []
    
    def _is_video_link(self, url: str) -> bool:
        """Check if a URL appears to be a video link."""
        if not url:
            return False
        
        url_lower = url.lower()
        
        # Check for video hosting platforms
        video_platforms = [
            'youtube.com', 'youtu.be', 'vimeo.com', 'dailymotion.com',
            'twitch.tv', 'facebook.com/watch'
        ]
        
        for platform in video_platforms:
            if platform in url_lower:
                return True
        
        # Check for video file extensions
        video_extensions = ['.mp4', '.avi', '.mov', '.wmv', '.flv', '.webm', '.mkv']
        for ext in video_extensions:
            if url_lower.endswith(ext):
                return True
        
        # Check for common video-related keywords in URL
        video_keywords = ['video', 'watch', 'stream', 'play']
        for keyword in video_keywords:
            if keyword in url_lower:
                return True
        
        return False
    
    def _extract_upload_date(self, cells, column_mapping: Dict[str, int]) -> Optional[datetime]:
        """Extract upload date from cells."""
        upload_date_text = self._extract_cell_text(cells, column_mapping, 'upload_date')
        return self._parse_date(upload_date_text)
    
    def organize_lecture_notes_by_course(self, lecture_notes: List[LMSLectureNote]) -> Dict[str, List[LMSLectureNote]]:
        """
        Organize lecture notes by course.
        
        Args:
            lecture_notes: List of LMSLectureNote objects
            
        Returns:
            Dictionary mapping course codes to lists of lecture notes
        """
        try:
            organized = {}
            
            for note in lecture_notes:
                course_key = note.course_code
                if course_key not in organized:
                    organized[course_key] = []
                organized[course_key].append(note)
            
            # Sort notes within each course by week number and upload date
            for course_code in organized:
                organized[course_code].sort(
                    key=lambda x: (x.week_no, x.upload_date if x.upload_date else datetime.min),
                    reverse=False  # Ascending order (earliest first)
                )
            
            log_info(f"Organized {len(lecture_notes)} lecture notes into {len(organized)} courses",
                    operation="organize_lecture_notes", course_count=len(organized))
            
            return organized
            
        except Exception as e:
            log_error(f"Failed to organize lecture notes by course: {e}",
                     operation="organize_lecture_notes", error_type="system_error", exc_info=True)
            # Return ungrouped notes as fallback
            return {'all': lecture_notes}
    
    def test_connectivity(self, account_id: str) -> Dict[str, Any]:
        """
        Test connectivity to LMS and return status information.
        
        Args:
            account_id: Account ID to test
            
        Returns:
            Dictionary with connectivity test results
        """
        try:
            log_info(f"Testing LMS connectivity for account {account_id}",
                    account_id=account_id, operation="test_connectivity")
            
            # Test session creation
            session = self._create_authenticated_session(account_id)
            
            # Test assignments page access
            assignments_response = self._make_request(session, self.assignments_url)
            assignments_accessible = assignments_response.status_code == 200
            
            # Test lecture notes page access
            notes_response = self._make_request(session, self.lecture_notes_url)
            notes_accessible = notes_response.status_code == 200
            
            result = {
                'success': True,
                'assignments_accessible': assignments_accessible,
                'lecture_notes_accessible': notes_accessible,
                'session_valid': True,
                'message': 'LMS connectivity test successful'
            }
            
            log_info(f"LMS connectivity test successful for account {account_id}",
                    account_id=account_id, operation="test_connectivity", 
                    assignments_accessible=assignments_accessible, 
                    notes_accessible=notes_accessible)
            
            return result
            
        except Exception as e:
            log_error(f"LMS connectivity test failed for account {account_id}: {e}",
                     account_id=account_id, operation="test_connectivity", 
                     error_type="connectivity_error", exc_info=True)
            return {
                'success': False,
                'assignments_accessible': False,
                'lecture_notes_accessible': False,
                'session_valid': False,
                'message': f'Connectivity test failed: {e}',
                'error': str(e)
            }
