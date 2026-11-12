import logging
logger = logging.getLogger(__name__)

import requests
from bs4 import BeautifulSoup
import json
import pickle
import os
from datetime import datetime, timedelta
import re
from functools import partial
from urllib.parse import urljoin
import time

from app.utils.proxy_transport import proxy_request

class BahriaCMSScraper:
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
            'X-Forwarded-For': '10.0.0.1',
            'X-Real-IP': '10.0.0.1',
            'Client-IP': '10.0.0.1'
        })
        
        self.login_url = 'https://cms.bahria.edu.pk/Logins/Student/Login.aspx'
        self.timetable_url = 'https://cms.bahria.edu.pk/Sys/Student/CourseRegistration/TimeTable.aspx'
        self.attendance_url = 'https://cms.bahria.edu.pk/Sys/Student/ClassAttendance/StudentWiseAttendance.aspx'
        self.community_service_url = 'https://cms.bahria.edu.pk/Sys/Student/Exams/CommunityServices.aspx'
        self.gpa_url = 'https://cms.bahria.edu.pk/Sys/Student/Exams/ExamResult.aspx'
        
        self.institute_id = '2'  # Karachi campus
        self.username = None
        self.cms_down = False
        
        # Create data directory if it doesn't exist
        self.data_dir = os.path.join(os.path.dirname(__file__), 'data')
        if not os.path.exists(self.data_dir):
            os.makedirs(self.data_dir)
    
    def save_session(self, username):
        """Save session cookies for future use"""
        try:
            session_file = os.path.join(self.data_dir, f'session_{username}.pkl')
            with open(session_file, 'wb') as f:
                pickle.dump(self.session.cookies, f)
            return True
        except Exception as e:
            logger.warning(f"Error saving session: {e}")
            return False
    
    def load_session(self, username):
        """Load saved session cookies"""
        try:
            session_file = os.path.join(self.data_dir, f'session_{username}.pkl')
            if os.path.exists(session_file):
                with open(session_file, 'rb') as f:
                    cookies = pickle.load(f)
                    self.session.cookies.update(cookies)
                return True
            return False
        except Exception as e:
            logger.warning(f"Error loading session: {e}")
            return False
    
    def _request(self, method, url, max_retries=3, follow_redirects=True, **kwargs):
        """Wrap requests through the Proxy Manager with robust retry logic."""
        import urllib.parse
        import time
        from app.utils.proxy_manager import get_proxy_manager, log_proxy_telemetry
        
        worker_id = getattr(self, 'account_id', self.username or "unknown")
        worker_url = get_proxy_manager().get_worker(worker_id)
        
        if url.startswith(worker_url):
            proxy_url = url
        else:
            # We must encode the ENTIRE URL (safe='') so that any '&' or '=' characters
            # in the target URL are not parsed by the proxy worker as its own query parameters.
            # This ensures the target url is treated as a single opaque string by the worker.
            encoded_url = urllib.parse.quote(url, safe='')
            proxy_url = f"{worker_url}{encoded_url}"
            if follow_redirects:
                proxy_url += "&native_redirects=true"
        kwargs['allow_redirects'] = False
        
        # Ensure cookies are explicitly passed to proxy worker even if domain-scoped
        if 'headers' not in kwargs:
            kwargs['headers'] = {}
        if 'Cookie' not in kwargs['headers'] and self.session.cookies:
            cookie_parts = [f"{c.name}={c.value}" for c in self.session.cookies if c.name and c.value]
            if cookie_parts:
                kwargs['headers']['Cookie'] = '; '.join(cookie_parts)
        
        for attempt in range(max_retries):
            start_time = time.perf_counter()
            try:
                response = proxy_request(partial(self.session.request, method), proxy_url, **kwargs)
                latency_ms = (time.perf_counter() - start_time) * 1000
                
                # Emit Cloud Run Telemetry
                log_proxy_telemetry(worker_url, url, latency_ms, response.status_code)
                
                # Check for WAF blocks and transient errors
                if response.status_code in [403, 500, 502, 503, 504, 1102]:
                    if response.status_code in [403, 500, 502, 503, 504]:
                        logger.warning(f"Proxy failure/WAF detected! Status {response.status_code} on worker {worker_url}")
                        get_proxy_manager().mark_dead(worker_url)
                        # Switch to a new worker for the next attempt
                        worker_url = get_proxy_manager().get_worker(worker_id)
                        if not url.startswith(worker_url):
                            proxy_url = f"{worker_url}{urllib.parse.quote(url, safe='')}"
                            if follow_redirects:
                                proxy_url += "&native_redirects=true"
                    
                    if attempt == max_retries - 1:
                        logger.warning(f"Proxy request failed after {max_retries} attempts. Status: {response.status_code}")
                        break
                        
                    sleep_time = 2 ** (attempt + 1)
                    logger.warning(f"Proxy encountered error {response.status_code}. Retrying in {sleep_time}s... (Attempt {attempt+1}/{max_retries})")
                    time.sleep(sleep_time)
                    continue
                
                # Handle Cookies combined by the CF Worker during native redirects
                proxy_cookies = response.headers.get('X-Proxy-Set-Cookie')
                if proxy_cookies:
                    for cookie_str in proxy_cookies.split('|||'):
                        if cookie_str.strip():
                            try:
                                key_val = cookie_str.split(';')[0].strip()
                                if '=' in key_val:
                                    k, v = key_val.split('=', 1)
                                    self.session.cookies.set(k, v)
                            except:
                                pass
                else:
                    # If not using native redirects, we MUST manually extract Set-Cookie headers
                    # because requests will reject cookies whose domains don't match the proxy URL!
                    if hasattr(response, 'raw') and hasattr(response.raw, 'headers'):
                        if hasattr(response.raw.headers, 'getlist'):
                            raw_cookies = response.raw.headers.getlist('Set-Cookie')
                            for cookie_str in raw_cookies:
                                if cookie_str.strip():
                                    try:
                                        key_val = cookie_str.split(';')[0].strip()
                                        if '=' in key_val:
                                            k, v = key_val.split('=', 1)
                                            self.session.cookies.set(k, v)
                                    except:
                                        pass
                
                # If caller doesn't want redirects followed, return immediately
                if not follow_redirects:
                    return response
                
                # Success or non-transient error (like 401 Unauthorized or 1015 Rate Limit), proceed to redirect handling
                while response.is_redirect:
                    location = response.headers.get('Location')
                    if not location:
                        break
                        
                    target_url = urllib.parse.urljoin(url, location)
                    encoded_target = urllib.parse.quote(target_url, safe='')
                    next_proxy_url = f"{worker_url}{encoded_target}"
                    
                    response = proxy_request(partial(self.session.request, 'GET'), next_proxy_url)
                    
                    # Manually extract cookies for intermediate hops too!
                    proxy_cookies_inner = response.headers.get('X-Proxy-Set-Cookie')
                    if proxy_cookies_inner:
                        for cookie_str in proxy_cookies_inner.split('|||'):
                            if cookie_str.strip():
                                try:
                                    key_val = cookie_str.split(';')[0].strip()
                                    if '=' in key_val:
                                        k, v = key_val.split('=', 1)
                                        self.session.cookies.set(k, v)
                                except:
                                    pass
                    else:
                        if hasattr(response, 'raw') and hasattr(response.raw, 'headers'):
                            if hasattr(response.raw.headers, 'getlist'):
                                raw_cookies_inner = response.raw.headers.getlist('Set-Cookie')
                                for cookie_str in raw_cookies_inner:
                                    if cookie_str.strip():
                                        try:
                                            key_val = cookie_str.split(';')[0].strip()
                                            if '=' in key_val:
                                                k, v = key_val.split('=', 1)
                                                self.session.cookies.set(k, v)
                                        except:
                                            pass
                                        
                    url = target_url
                    
                return response
                
            except requests.exceptions.RequestException as e:
                if attempt == max_retries - 1:
                    logger.warning(f"Proxy request failed with network error after {max_retries} attempts: {e}")
                    raise
                    
                sleep_time = 2 ** (attempt + 1)
                logger.warning(f"Network error: {e}. Retrying in {sleep_time}s... (Attempt {attempt+1}/{max_retries})")
                time.sleep(sleep_time)
                continue

    def get_login_form_data(self):
        """Get the login page and extract necessary form data"""
        try:
            response = self._request('GET', self.login_url)
            resp_text = response.text or ""
            
            # Check for CMS server downtime / ASP.NET runtime error
            if response.status_code >= 500 or "Runtime Error" in resp_text or "Server Error in '/' Application" in resp_text:
                self.cms_down = True
                logger.error(f"CMS login page returned server error (HTTP {response.status_code} / Runtime Error)")
                return None

            response.raise_for_status()
            
            soup = BeautifulSoup(resp_text, 'html.parser')
            
            # Extract required hidden fields
            viewstate = soup.find('input', {'id': '__VIEWSTATE'})
            viewstate_gen = soup.find('input', {'id': '__VIEWSTATEGENERATOR'})
            event_validation = soup.find('input', {'id': '__EVENTVALIDATION'})
            
            if not all([viewstate, viewstate_gen, event_validation]):
                if "Runtime Error" in resp_text or "Server Error" in resp_text:
                    self.cms_down = True
                raise Exception("Could not find required form fields")
            
            return {
                '__VIEWSTATE': viewstate.get('value', ''),
                '__VIEWSTATEGENERATOR': viewstate_gen.get('value', ''),
                '__EVENTVALIDATION': event_validation.get('value', '')
            }
        except Exception as e:
            err_str = str(e)
            if any(code in err_str for code in ("500", "502", "503", "504")) or "Runtime Error" in err_str:
                self.cms_down = True
            logger.warning(f"Error getting login form data: {e}")
            return None
    
    def login(self, username, password, force=False):
        """Login to CMS and maintain session. If force=True, ignore cached sessions."""
        self.username = username
        from app.routes.auth import get_institute_id_for_enrollment
        if username:
            self.institute_id = get_institute_id_for_enrollment(username, fallback=getattr(self, 'institute_id', '2'))
        try:
            # Check for existing session first unless forced
            if not force and self.load_session(username):
                if self.verify_login():
                    logger.debug(f"Successfully loaded existing session for {username}")
                    return True
            
            # Get login form data
            form_data_base = self.get_login_form_data()
            if not form_data_base:
                if getattr(self, 'cms_down', False):
                    logger.warning("Login failed - CMS server is down (HTTP 500 / Runtime Error)")
                    return "CMS_DOWN"
                logger.warning("Login failed - could not get login form data")
                return False
            
            # Prepare form data
            form_data = {
                '__LASTFOCUS': '',
                '__EVENTTARGET': 'ctl00$BodyPH$btnLogin',
                '__EVENTARGUMENT': '',
                '__VIEWSTATE': form_data_base['__VIEWSTATE'],
                '__VIEWSTATEGENERATOR': form_data_base['__VIEWSTATEGENERATOR'],
                '__EVENTVALIDATION': form_data_base['__EVENTVALIDATION'],
                'ctl00$BodyPH$tbEnrollment': username,
                'ctl00$BodyPH$tbPassword': password,
                'ctl00$BodyPH$ddlInstituteID': self.institute_id,
                'ctl00$BodyPH$ddlSubUserType': 'None',
                'ctl00$hfJsEnabled': '0'
            }
            
            # Submit login form WITHOUT following redirects
            # This avoids the Cloudflare IP-change session kill on PrepareSession.aspx
            response = self._request('POST', self.login_url, data=form_data, follow_redirects=False)
            
            if response.status_code >= 500 or "Runtime Error" in response.text or "Server Error in '/' Application" in response.text:
                self.cms_down = True
                logger.warning(f"CMS login POST returned server error {response.status_code}")
                return "CMS_DOWN"
            
            logger.debug(f"Login POST status: {response.status_code}, Location: {response.headers.get('Location', 'None')}")
            
            # Check login success by inspecting the 302 redirect target
            # Correct credentials -> 302 to PrepareSession.aspx
            # Wrong credentials -> 200 (stays on login page with error)
            if response.status_code == 302:
                location = response.headers.get('Location', '')
                if 'PrepareSession' in location or 'Dashboard' in location or 'QualityAssurance' in location or 'Survey' in location:
                    logger.debug(f"Successfully logged in as {username} (verified via POST redirect)")
                    
                    # Follow the redirect chain to establish the session fully
                    # This is CRITICAL - without it, subsequent requests will fail
                    try:
                        import urllib.parse
                        import time
                        
                        target_url = urllib.parse.urljoin(self.login_url, location)
                        logger.debug(f"Following redirect to: {target_url}")
                        
                        max_retries = 3
                        session_established = False
                        for attempt in range(max_retries):
                            try:
                                redirect_response = self._request('GET', target_url, follow_redirects=True)
                                logger.debug(f"Redirect response status: {redirect_response.status_code}")
                                
                                # Verify we're not back at login page
                                if 'tbEnrollment' not in redirect_response.text and 'tbPassword' not in redirect_response.text:
                                    logger.debug("Session established successfully")
                                    session_established = True
                                    break
                                else:
                                    logger.debug(f"Attempt {attempt + 1}: Still at login page, retrying...")
                                    if attempt < max_retries - 1:
                                        time.sleep(1)
                            except Exception as e:
                                logger.warning(f"Attempt {attempt + 1} failed: {e}")
                                if attempt < max_retries - 1:
                                    time.sleep(1)
                                else:
                                    raise
                        
                        if not session_established:
                            logger.warning("Failed to establish session after POST redirect - cookies dropped or WAF block")
                            self.session_lost = True
                            return "SESSION_LOST"
                            
                    except Exception as e:
                        logger.warning(f"Post-login redirect failed: {e}")
                        logger.debug("   Session may not be fully established")
                        self.session_lost = True
                        return "SESSION_LOST"
                        
                    self.save_session(username)
                    return True
                elif 'Login' in location:
                    logger.warning("Login failed - CMS rejected credentials (redirected back to login)")
                    return False
            
            # If we got a 200, check if the page contains error messages
            if response.status_code == 200:
                if 'Invalid' in response.text or 'incorrect' in response.text.lower():
                    logger.warning("Login failed - invalid credentials")
                else:
                    logger.warning(f"Login failed - unexpected 200 response")
                return False
            
            logger.warning(f"Login failed - unexpected status {response.status_code}")
            return False
                
        except Exception as e:
            logger.warning(f"Error during login: {e}")
            import traceback
            traceback.print_exc()
            return False
    
    def verify_login(self):
        """Verify if we're still logged in"""
        try:
            # Try to access a protected page
            response = self._request('GET', self.timetable_url)
            response.raise_for_status()
            
            # If we get redirected to login page, we're not logged in
            if 'Login.aspx' in response.url:
                return False
            
            # Check for common elements that indicate successful login
            if 'Student Portal' in response.text or 'TimeTable' in response.text:
                return True
            
            return False
        except Exception as e:
            logger.warning(f"Error verifying login: {e}")
            return False
    
    def scrape_timetable(self, username):
        """Scrape timetable data"""
        try:
            if not self.verify_login():
                logger.debug("Not logged in, cannot scrape timetable")
                return None
            
            response = self._request('GET', self.timetable_url)
            response.raise_for_status()
            
            soup = BeautifulSoup(response.text, 'html.parser')
            
            # Parse timetable data
            timetable_data = self.parse_timetable(soup)
            
            # Save timetable data
            self.save_data(username, 'timetable', timetable_data)
            
            return timetable_data
            
        except Exception as e:
            logger.warning(f"Error scraping timetable: {e}")
            return None
    
    def scrape_attendance(self, username):
        """Scrape attendance data"""
        try:
            if not self.verify_login():
                logger.debug("Not logged in, cannot scrape attendance")
                return None
            
            response = self._request('GET', self.attendance_url)
            response.raise_for_status()
            
            soup = BeautifulSoup(response.text, 'html.parser')
            
            # Parse attendance data
            attendance_data = self.parse_attendance(soup)
            
            # Save attendance data
            self.save_data(username, 'attendance', attendance_data)
            
            return attendance_data
            
        except Exception as e:
            logger.warning(f"Error scraping attendance: {e}")
            return None
            
    def scrape_attendance_summary(self, detail_url, username=None, password=None, course_code=None, **kwargs):
        """Scrape detailed attendance summary for a specific course.
        
        The CMS attendance summary page has this table structure (from screenshot):
        Col 0: #  |  Col 1: Class Date  |  Col 2: Class Type  |  Col 3: Room
        Col 4: Remarks/Topics Covered  |  Col 5: Total Hours  |  Col 6: Present Hours  |  Col 7: Absent Hours
        """
        try:
            full_url = urljoin('https://cms.bahria.edu.pk/Sys/Student/ClassAttendance/', detail_url)
            logger.debug(f"Fetching attendance summary from: {full_url}")
            
            def _fetch_and_parse():
                response = self._request('GET', full_url)
                response.raise_for_status()
                soup = BeautifulSoup(response.text, 'html.parser')
                title = soup.find('title')
                logger.debug(f"Summary page title: {title.get_text(strip=True) if title else 'N/A'}")
                body_text = soup.get_text(strip=True)
                logger.debug(f"Page body length: {len(body_text)} chars")
                return soup, body_text
            
            soup, body_text = _fetch_and_parse()
            
            def _check_page_state(soup, body_text):
                # Check for session expiry
                if len(body_text) < 200:
                    return "EXPIRED"
                title_tag = soup.find('title')
                if title_tag:
                    title_lower = title_tag.get_text(strip=True).lower()
                    if 'login' in title_lower or 'session' in title_lower:
                        return "EXPIRED"
                    if 'dashboard' in title_lower:
                        return "STALE_TOKEN"
                return "OK"
            
            state = _check_page_state(soup, body_text)
            
            if state == "EXPIRED":
                logger.debug("Session appears expired (empty/login page). Need to re-login and get fresh tokens.")
                return "EXPIRED"
            
            if state == "STALE_TOKEN":
                logger.debug("Detail URL token appears to be stale (redirected to Dashboard). Need fresh tokens.")
                return "STALE_TOKEN"
            
            return self.parse_attendance_summary(soup)
            
        except Exception as e:
            logger.warning(f"Error scraping attendance summary: {e}")
            import traceback
            traceback.print_exc()
            return None

    def parse_attendance_summary(self, soup):
        """Parse attendance summary table from BeautifulSoup page"""
        if not soup:
            return None
            
        raw_html = str(soup).lower()
        page_text = soup.get_text(separator=' ', strip=True).lower()
        if 'tbenrollment' in raw_html or 'login.aspx' in raw_html or 'login.aspx' in page_text:
            logger.warning("Attendance summary page contains login elements, session expired")
            return "EXPIRED"

        if ('student dashboard' in page_text or bool(re.search(r'<title>[^<]*Dashboard', raw_html, re.I))) and not ('present' in page_text and 'absent' in page_text):
            logger.warning("Attendance summary page contains dashboard text, token invalid")
            return "STALE_TOKEN"

        tables = soup.find_all('table')
        logger.debug(f"Found {len(tables)} tables on page")
        
        target_table = None
        for i, table in enumerate(tables):
            header_row = table.find('tr')
            if not header_row:
                continue
            header_text = ' '.join([th.get_text(strip=True).lower() for th in header_row.find_all(['th', 'td'])])
            logger.debug(f"Table {i} header: {header_text[:120]}")
            if ('present' in header_text or 'hours' in header_text) and 'absent' in header_text:
                target_table = table
                logger.debug(f"Using table {i} as attendance summary table")
                break
        
        if not target_table:
            for i, table in enumerate(tables):
                table_text = table.get_text(separator=' ', strip=True).lower()
                if 'present' in table_text and 'absent' in table_text and 'class date' in table_text:
                    target_table = table
                    logger.debug(f"Fallback: using table {i} as attendance summary table")
                    break
        
        if not target_table:
            # Check if this is a legitimate attendance page with no records yet
            if ('class attendance' in page_text and 'summary' in page_text) or 'no record' in page_text:
                logger.debug("No attendance table found, but page is attendance summary (0 records)")
                return []
            logger.warning("No attendance summary table found on page")
            return None
        
        summary_data = []
        rows = target_table.find_all('tr')
        logger.debug(f"Table has {len(rows)} rows")
        
        for row_idx, row in enumerate(rows):
            cells = row.find_all(['td', 'th'])
            if not cells:
                continue
            
            row_text = ' '.join([c.get_text(strip=True).lower() for c in cells])
            if 'present' in row_text and 'absent' in row_text:
                logger.debug(f"  Skipping header row {row_idx}: {row_text[:80]}")
                continue
            
            if len(cells) < 7:
                logger.debug(f"  Skipping short row {row_idx} ({len(cells)} cells): {row_text[:60]}")
                continue
            
            try:
                offset = 1 if len(cells) >= 8 else 0
                date_val = cells[offset].get_text(separator=' ', strip=True)
                present_val = cells[offset + 5].get_text(strip=True)
                
                # Skip summary/footer rows (empty date, percentages, or 'total')
                if not date_val or '%' in present_val or ':' in present_val or 'total' in date_val.lower():
                    logger.debug(f"  Skipping footer/summary row {row_idx}: {row_text[:60]}")
                    continue

                entry = {
                    'date': date_val,
                    'type': cells[offset + 1].get_text(strip=True),
                    'room': cells[offset + 2].get_text(strip=True),
                    'remarks': cells[offset + 3].get_text(separator='\n', strip=True),
                    'total_hours': cells[offset + 4].get_text(strip=True),
                    'present_hours': present_val,
                    'absent_hours': cells[offset + 6].get_text(strip=True),
                }
                logger.debug(f"  Row {row_idx}: {entry}")
                summary_data.append(entry)
            except IndexError as ie:
                logger.warning(f"  IndexError on row {row_idx} ({len(cells)} cells): {ie}")
        
        logger.debug(f"Parsed {len(summary_data)} summary entries")
        return summary_data


    
    def parse_timetable(self, soup):
        """Parse timetable HTML and extract structured data"""
        timetable_data = {
            'days': ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday'],
            'time_slots': [],
            'schedule': {},
            'last_updated': datetime.now().isoformat(),
            'raw_html_size': len(str(soup)) if soup else 0,
            'parsing_success': False
        }
        
        try:
            # Look for JSON data in hidden input field (new approach)
            calendar_json_input = soup.find('input', {'id': 'BodyPH_hfCalendarJson'})
            
            if calendar_json_input and calendar_json_input.get('value'):
                import html
                json_value = html.unescape(calendar_json_input.get('value'))
                logger.debug(f"Found timetable JSON data: {len(json_value)} characters")
                
                try:
                    calendar_data = json.loads(json_value)
                    logger.debug(f"Successfully parsed calendar JSON: {len(calendar_data.get('eventsData', []))} events")
                    
                    # Process the calendar data
                    timetable_data = self.process_calendar_json(calendar_data)
                    timetable_data['parsing_success'] = True
                    return timetable_data
                    
                except json.JSONDecodeError as je:
                    logger.warning(f"Error parsing calendar JSON: {je}")
            
            # Fallback: Try traditional table parsing
            logger.debug("Falling back to traditional table parsing...")
            
            # Find timetable table - try multiple approaches
            tables = soup.find_all('table')
            table = None
            
            # Look for the main timetable table
            for t in tables:
                # Check if table has time-related content
                table_text = t.get_text().lower()
                if any(time_word in table_text for time_word in ['monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'time', 'schedule']):
                    table = t
                    break
            
            if not table and tables:
                # Fallback to the largest table
                table = max(tables, key=lambda t: len(t.find_all('tr')))
            
            if not table:
                logger.debug("No timetable table found")
                return timetable_data
            
            rows = table.find_all('tr')
            
            if len(rows) < 1:
                logger.debug("Invalid timetable structure - no rows found")
                return timetable_data
            
            # Traditional parsing logic (simplified)
            logger.debug(f"Found {len(rows)} table rows, attempting traditional parsing...")
            
        except Exception as e:
            logger.warning(f"Error parsing timetable: {e}")
        
        return timetable_data
    
    def parse_attendance(self, soup):
        """Parse attendance HTML and extract structured data"""
        attendance_data = {
            'courses': [],
            'overall_percentage': 0,
            'last_updated': datetime.now().astimezone().isoformat()
        }
        
        try:
            # Find attendance table
            tables = soup.find_all('table')
            table_found = False
            
            for table in tables:
                rows = table.find_all('tr')
                if not rows:
                    continue
                
                # Check if this looks like an attendance table
                header_row = rows[0]
                headers = [th.get_text(strip=True).lower() for th in header_row.find_all(['th', 'td'])]
                
                if any(keyword in ' '.join(headers) for keyword in ['course', 'attendance', 'present', 'absent']):
                    table_found = True
                    
                    if len(rows) < 2:
                        continue
                        
                    # Parse attendance data
                    for row in rows[1:]:
                        cells = row.find_all(['td', 'th'])
                        
                        if len(cells) < 4:
                            continue
                        
                        course_data = self.parse_attendance_row(cells)
                        if course_data:
                            attendance_data['courses'].append(course_data)
            
            if not table_found:
                # If we didn't even find the table headers, it's likely a silent CMS error page
                attendance_data['error'] = 'Attendance table not found on page'
                return attendance_data
                
            # Calculate overall percentage
            if attendance_data['courses']:
                total_percentage = sum(course['attendance_percentage'] for course in attendance_data['courses'])
                attendance_data['overall_percentage'] = total_percentage / len(attendance_data['courses'])
            
        except Exception as e:
            logger.warning(f"Error parsing attendance: {e}")
            attendance_data['error'] = f"Parsing error: {str(e)}"
        
        return attendance_data
    
    def parse_class_info(self, class_text):
        """Parse individual class information from text"""
        try:
            # Example formats:
            # "CS301 - Data Structures - Dr. Smith - CS-101"
            # "CS301\nData Structures\nDr. Smith\nCS-101"
            
            parts = re.split(r'\s*[-\n]\s*', class_text)
            
            if len(parts) >= 2:
                return {
                    'code': parts[0].strip(),
                    'name': parts[1].strip() if len(parts) > 1 else '',
                    'instructor': parts[2].strip() if len(parts) > 2 else '',
                    'room': parts[3].strip() if len(parts) > 3 else ''
                }
            else:
                return {
                    'code': class_text.strip(),
                    'name': '',
                    'instructor': '',
                    'room': ''
                }
        except Exception as e:
            logger.warning(f"Error parsing class info: {e}")
            return {
                'code': class_text.strip(),
                'name': '',
                'instructor': '',
                'room': ''
            }
    
    
    def save_data(self, username, data_type, data):
        """Save scraped data to file"""
        try:
            filename = f"{username}_{data_type}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
            filepath = os.path.join(self.data_dir, filename)
            
            with open(filepath, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            
            # Also save as latest
            latest_filename = f"{username}_{data_type}_latest.json"
            latest_filepath = os.path.join(self.data_dir, latest_filename)
            
            with open(latest_filepath, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            
            logger.debug(f"Saved {data_type} data to {filepath}")
            return True
            
        except Exception as e:
            logger.warning(f"Error saving data: {e}")
            return False
    
    def load_data(self, username, data_type):
        """Load saved data from file"""
        try:
            latest_filename = f"{username}_{data_type}_latest.json"
            latest_filepath = os.path.join(self.data_dir, latest_filename)
            
            if os.path.exists(latest_filepath):
                with open(latest_filepath, 'r', encoding='utf-8') as f:
                    return json.load(f)
            
            return None
            
        except Exception as e:
            logger.warning(f"Error loading data: {e}")
            return None
    
    def process_calendar_json(self, calendar_data):
        """Process the calendar JSON data into our timetable format"""
        timetable_data = {
            'days': ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday'],
            'time_slots': [],
            'schedule': {},
            'last_updated': datetime.now().isoformat(),
            'parsing_success': True,
            'semester': calendar_data.get('semester', 'Unknown')
        }
        
        # Initialize schedule for each day
        for day in timetable_data['days']:
            timetable_data['schedule'][day] = {}
        
        # Process events data
        events_data = calendar_data.get('eventsData', [])
        time_slots_set = set()
        
        day_mapping = {
            1: 'Monday', 2: 'Tuesday', 3: 'Wednesday', 
            4: 'Thursday', 5: 'Friday', 6: 'Saturday', 7: 'Sunday'
        }
        
        for event in events_data:
            week_day = event.get('weekDay')
            timing = event.get('timing', '')
            event_data = event.get('data', {})
            
            # Map week day number to day name
            day_name = day_mapping.get(week_day)
            if not day_name or day_name not in timetable_data['days']:
                continue
            
            # Extract class information
            class_info = {
                'code': event_data.get('shortCourseTitle', '').replace('&amp;', '&'),
                'name': event_data.get('courseTitle', '').replace('&amp;', '&'),
                'instructor': event_data.get('facultyMemberName', ''),
                'room': event_data.get('roomName', ''),
                'building': event_data.get('buildingAlias', ''),
                'class_name': event_data.get('className', '')
            }
            
            # Add timing to time slots
            if timing:
                time_slots_set.add(timing)
                timetable_data['schedule'][day_name][timing] = class_info
        
        # Sort and set time slots
        timetable_data['time_slots'] = sorted(list(time_slots_set), key=self.parse_time_slot)
        
        # Optimize the timetable structure
        timetable_data = self.optimize_timetable_structure(timetable_data)
        
        logger.debug(f"Processed {len(events_data)} events into {len(timetable_data['time_slots'])} time slots")
        return timetable_data
    
    def parse_time_slot(self, time_slot):
        """Parse time slot for sorting (e.g., '08:30 AM - 10:30 AM')"""
        try:
            start_time = time_slot.split(' - ')[0].strip()
            # Convert to 24-hour format for sorting
            if 'PM' in start_time and not start_time.startswith('12'):
                hour = int(start_time.split(':')[0]) + 12
                minute = start_time.split(':')[1].replace(' PM', '')
                return f"{hour:02d}:{minute}"
            elif 'AM' in start_time and start_time.startswith('12'):
                return start_time.replace('12:', '00:').replace(' AM', '')
            else:
                return start_time.replace(' AM', '').replace(' PM', '')
        except:
            return time_slot
    
    def optimize_timetable_structure(self, timetable_data):
        """Optimize timetable structure to handle multi-slot classes efficiently"""
        
        # Create standardized hourly time slots
        standard_slots = [
            "08:30 AM - 09:30 AM", "09:30 AM - 10:30 AM", "10:30 AM - 11:30 AM",
            "11:30 AM - 12:30 PM", "12:30 PM - 01:30 PM", "01:30 PM - 02:30 PM",
            "02:30 PM - 03:30 PM", "03:30 PM - 04:30 PM", "04:30 PM - 05:30 PM"
        ]
        
        optimized_schedule = {}
        
        for day in timetable_data['days']:
            optimized_schedule[day] = {}
            day_schedule = timetable_data['schedule'].get(day, {})
            
            # Group consecutive slots by course code
            course_groups = self.group_consecutive_classes(day_schedule, standard_slots)
            
            for course_code, slots_info in course_groups.items():
                first_slot = slots_info['slots'][0]
                class_info = slots_info['class_info']
                
                # Calculate total duration
                total_duration = len(slots_info['slots'])
                
                # Create optimized class info
                optimized_class_info = class_info.copy()
                optimized_class_info.update({
                    'duration_slots': total_duration,
                    'covered_time_slots': slots_info['slots'],
                    'start_time': slots_info['start_time'],
                    'end_time': slots_info['end_time'],
                    'optimized': True
                })
                
                # Add to the first slot only
                optimized_schedule[day][first_slot] = optimized_class_info
                
                logger.debug(f"Optimized {course_code} on {day}: {total_duration} slots starting at {first_slot}")
        
        # Update timetable data with optimized structure
        timetable_data['time_slots'] = standard_slots
        timetable_data['schedule'] = optimized_schedule
        timetable_data['optimized'] = True
        
        return timetable_data
    
    def group_consecutive_classes(self, day_schedule, standard_slots):
        """Group consecutive time slots for the same course"""
        course_groups = {}
        
        # Process each original time slot from the schedule
        for time_slot, class_info in day_schedule.items():
            course_code = class_info.get('code', '')
            if not course_code:
                continue
            
            # Parse the original time slot
            start_time, end_time = self.parse_time_range(time_slot)
            if start_time is None or end_time is None:
                continue
            
            # Find all standard slots that this original time slot covers
            covered_slots = self.find_covered_slots(start_time, end_time, standard_slots)
            
            if not covered_slots:
                continue
            
            # Create unique key for this course session
            course_key = f"{course_code}_{class_info.get('instructor', '')}_{class_info.get('room', '')}"
            
            if course_key not in course_groups:
                course_groups[course_key] = {
                    'slots': [],
                    'class_info': class_info,
                    'start_time': start_time,
                    'end_time': end_time,
                    'original_slots': []
                }
            
            # Add all covered slots to this course group
            for slot in covered_slots:
                if slot not in course_groups[course_key]['slots']:
                    course_groups[course_key]['slots'].append(slot)
            
            # Keep track of original time slots for debugging
            course_groups[course_key]['original_slots'].append(time_slot)
            
            # Update time range to encompass all sessions
            if start_time < course_groups[course_key]['start_time']:
                course_groups[course_key]['start_time'] = start_time
            if end_time > course_groups[course_key]['end_time']:
                course_groups[course_key]['end_time'] = end_time
        
        # Sort slots within each group by start time
        for course_key in course_groups:
            slots = course_groups[course_key]['slots']
            slots.sort(key=lambda slot: self.parse_time_range(slot)[0] or 0)
            
            # Debug output
            original_slots = course_groups[course_key]['original_slots']
            logger.debug(f"Course {course_key}: Original slots {original_slots} -> Covers {len(slots)} standard slots: {slots}")
        
        return course_groups
    
    def parse_time_range(self, time_slot):
        """Parse time range string to get start and end times"""
        try:
            if ' - ' not in time_slot:
                return None, None
            
            start_str, end_str = time_slot.split(' - ')
            start_time = self.parse_time_to_minutes(start_str.strip())
            end_time = self.parse_time_to_minutes(end_str.strip())
            
            return start_time, end_time
        except:
            return None, None
    
    def parse_time_to_minutes(self, time_str):
        """Convert time string to minutes since midnight"""
        try:
            time_str = time_str.strip()
            if 'AM' in time_str or 'PM' in time_str:
                time_part = time_str.replace(' AM', '').replace(' PM', '')
                hour, minute = map(int, time_part.split(':'))
                
                if 'PM' in time_str and hour != 12:
                    hour += 12
                elif 'AM' in time_str and hour == 12:
                    hour = 0
                
                return hour * 60 + minute
            return None
        except:
            return None
    
    def find_covered_slots(self, start_time, end_time, standard_slots):
        """Find which standard slots are covered by the given time range"""
        covered_slots = []
        
        for slot in standard_slots:
            slot_start, slot_end = self.parse_time_range(slot)
            if slot_start is None or slot_end is None:
                continue
            
            # Check if the class time overlaps with this slot (with better overlap detection)
            # A slot is covered if there's any overlap between class time and slot time
            if not (end_time <= slot_start or start_time >= slot_end):
                covered_slots.append(slot)
        
        return covered_slots
    
    def parse_attendance_row(self, cells):
        """Parse individual attendance row with improved logic"""
        try:
            if len(cells) < 10:  # Need at least 10 columns based on the structure
                return None
            
            # Based on the debug output structure:
            # Column 1: #, Column 2: Code, Column 3: Course Title, Column 4: Credit Hours
            # Column 8: Teacher Name, Column 10: Present Hours, Column 11: Absent Hours, Column 12: Total Hours
            
            course_code = cells[1].get_text(strip=True) if len(cells) > 1 else ''
            course_name = cells[2].get_text(strip=True) if len(cells) > 2 else ''
            credit_hours = cells[3].get_text(strip=True) if len(cells) > 3 else '3'
            instructor = cells[7].get_text(strip=True) if len(cells) > 7 else ''
            present_hours_text = cells[9].get_text(strip=True) if len(cells) > 9 else ''
            absent_hours_text = cells[10].get_text(strip=True) if len(cells) > 10 else ''
            total_hours_text = cells[11].get_text(strip=True) if len(cells) > 11 else ''
            
            # Parse present hours and percentage
            present_hours = 0
            attendance_percentage = 0
            
            # Present hours format: "40.0 :83.33%"
            if ':' in present_hours_text and '%' in present_hours_text:
                parts = present_hours_text.split(':')
                if len(parts) >= 2:
                    present_hours = float(parts[0].strip())
                    percentage_str = parts[1].replace('%', '').strip()
                    attendance_percentage = float(percentage_str)
            
            # Parse absent and total hours
            absent_hours = float(absent_hours_text) if absent_hours_text else 0
            total_hours = float(total_hours_text) if total_hours_text else 0
            
            # Credit hours for calculating max absences
            try:
                credit_hrs = int(float(str(credit_hours).strip()))
            except:
                credit_hrs = 3  # Default
            
            # Calculate max absences based on credit hours and course type (75% min safe = 25% max absence; 60% min with fine = 40% max absence)
            course_name_lower = course_name.lower()
            
            # Check for Lab courses first (contains "lab" in name)
            if 'lab' in course_name_lower:
                safe_max_absences = 12
                fine_max_absences = 19
            # Check for Understanding Quran (0 credit hours)
            elif credit_hrs == 0 or 'understanding quran' in course_name_lower or 'quran' in course_name_lower:
                safe_max_absences = 1
                fine_max_absences = 2
            else:  # Regular courses (16 weeks: 3 credit -> 12h safe / 19h fine, 2 credit -> 8h safe / 12h fine)
                safe_max_absences = int(credit_hrs * 16 * 0.25)
                fine_max_absences = int(credit_hrs * 16 * 0.40)
            
            abs_count = int(round(float(absent_hours))) if absent_hours else 0
            safe_skips = max(0, safe_max_absences - abs_count)
            fine_skips = max(0, fine_max_absences - max(abs_count, safe_max_absences))
            shortfall_hours = max(0, abs_count - safe_max_absences)
            fine_amount = shortfall_hours * 500
            
            # Determine status based on official Bahria policy
            if total_hours == 0 or (attendance_percentage == 0 and abs_count == 0):
                status = 'safe'
            elif attendance_percentage >= 75.0:
                status = 'safe'
            elif attendance_percentage >= 60.0:
                status = 'warning'
            else:
                status = 'danger'
            
            # Extract detail URL
            detail_url = ''
            if len(cells) > 12:
                link = cells[12].find('a')
                if link and link.get('href'):
                    detail_url = link.get('href')
            
            course_data = {
                'code': course_code,
                'name': course_name,
                'instructor': instructor,
                'credit_hours': credit_hrs,
                'total_classes': int(total_hours) if total_hours else 0,
                'attended_classes': int(present_hours) if present_hours else 0,
                'attendance_percentage': attendance_percentage,
                'absences': abs_count,
                'max_absences': safe_max_absences,
                'remaining_absences': safe_skips,
                'safe_skips': safe_skips,
                'fine_skips': fine_skips,
                'shortfall_hours': shortfall_hours,
                'fine_amount': fine_amount,
                'safe_max_absences': safe_max_absences,
                'fine_max_absences': fine_max_absences,
                'status': status,
                'detail_url': detail_url
            }
            
            # Only return if we have meaningful data (course code and name exist)
            # Courses with 0 hours are kept (start of semester)
            if course_code and course_name:
                return course_data
            
            return None
            
        except Exception as e:
            logger.warning(f"Error parsing attendance row: {e}")
            return None
    
    def scrape_community_service(self, username):
        """Scrape community service data"""
        try:
            if not self.verify_login():
                logger.debug("Not logged in, cannot scrape community service")
                return None
            
            response = self._request('GET', self.community_service_url)
            response.raise_for_status()
            
            soup = BeautifulSoup(response.text, 'html.parser')
            
            # Parse community service data
            community_service_data = self.parse_community_service(soup)
            
            # Save community service data
            self.save_data(username, 'community_service', community_service_data)
            
            return community_service_data
            
        except Exception as e:
            logger.warning(f"Error scraping community service: {e}")
            return None
    
    def scrape_gpa_data(self, username):
        """Scrape GPA/exam results data"""
        try:
            if not self.verify_login():
                logger.debug("Not logged in, cannot scrape GPA data")
                return None
            
            response = self._request('GET', self.gpa_url)
            response.raise_for_status()
            
            soup = BeautifulSoup(response.text, 'html.parser')
            
            # Parse GPA data
            gpa_data = self.parse_gpa_data(soup)
            
            # Save GPA data
            self.save_data(username, 'gpa', gpa_data)
            
            return gpa_data
            
        except Exception as e:
            logger.warning(f"Error scraping GPA data: {e}")
            return None
    
    def parse_community_service(self, soup):
        """Parse community service HTML and extract structured data"""
        community_service_data = {
            'opportunities': [],
            'completed_hours': 0,
            'required_hours': 40,  # Default requirement
            'last_updated': datetime.now().isoformat()
        }
        
        try:
            # Find community service table(s)
            tables = soup.find_all('table')
            
            for table in tables:
                rows = table.find_all('tr')
                
                if len(rows) < 2:
                    continue
                
                # Check if this looks like a community service table
                header_row = rows[0]
                headers = [th.get_text(strip=True).lower() for th in header_row.find_all(['th', 'td'])]
                
                if any(keyword in ' '.join(headers) for keyword in ['service', 'hours', 'activity', 'date']):
                    # Parse community service data
                    for row in rows[1:]:
                        cells = row.find_all(['td', 'th'])
                        
                        if len(cells) < 3:
                            continue
                        
                        service_data = self.parse_community_service_row(cells)
                        if service_data:
                            community_service_data['opportunities'].append(service_data)
            
            # Calculate completed hours
            community_service_data['completed_hours'] = sum(
                opportunity.get('hours', 0) for opportunity in community_service_data['opportunities']
            )
            
        except Exception as e:
            logger.warning(f"Error parsing community service: {e}")
        
        return community_service_data
    
    def parse_gpa_data(self, soup):
        """Parse GPA/exam results HTML and extract structured data"""
        gpa_data = {
            'semesters': [],
            'current_cgpa': 0.0,
            'total_credit_hours': 0,
            'last_updated': datetime.now().isoformat()
        }
        
        try:
            # Find all tables. The structure is often a semester name followed by a course table.
            tables = soup.find_all('table', class_='table-bordered')
            
            for table in tables:
                caption = table.find('caption')
                if caption and ('Fall' in caption.text or 'Spring' in caption.text or 'Summer' in caption.text):
                    semester_name = caption.text.strip()
                    
                    semester_courses = []
                    rows = table.find_all('tr')
                    for row in rows[1:]:  # Skip header row
                        cells = row.find_all('td')
                        if len(cells) > 7:
                            course_data = self.parse_course_result_row(cells)
                            if course_data:
                                semester_courses.append(course_data)
                    
                    if semester_courses:
                        new_semester = {
                            'semester_name': semester_name,
                            'courses': semester_courses,
                            'sgpa': 0.0,
                            'cgpa': 0.0 # This would ideally be parsed from a summary table
                        }
                        gpa_data['semesters'].append(new_semester)

            # Post-processing: Calculate SGPA and total credits, excluding semesters with all N/A grades
            total_credit_hours = 0
            total_points_all_semesters = 0
            valid_semesters = []
            
            for semester in gpa_data['semesters']:
                semester_total_points = 0
                semester_total_credits = 0
                has_valid_grades = False
                
                for course in semester.get('courses', []):
                    credits = course.get('credit_hours', 0)
                    points = course.get('grade_points', 0)
                    grade = course.get('grade', '').upper()
                    
                    # Check if course has a valid grade (not N/A, empty, or similar)
                    if grade and grade not in ['N/A', 'NA', '', '-', 'TBD', 'PENDING']:
                        has_valid_grades = True
                        if credits > 0 and points > 0:
                            semester_total_points += points * credits
                            semester_total_credits += credits
                
                # Only include semester if it has valid grades
                if has_valid_grades and semester_total_credits > 0:
                    semester['sgpa'] = round(semester_total_points / semester_total_credits, 2)
                    total_points_all_semesters += semester_total_points
                    total_credit_hours += semester_total_credits
                    valid_semesters.append(semester)
                else:
                    logger.debug(f"Excluding semester {semester['semester_name']} - no valid grades found")
            
            # Update semesters list to only include valid ones
            gpa_data['semesters'] = valid_semesters
            gpa_data['total_credit_hours'] = total_credit_hours
            if total_credit_hours > 0:
                gpa_data['current_cgpa'] = round(total_points_all_semesters / total_credit_hours, 2)

            logger.debug(f"Final GPA data: {len(gpa_data['semesters'])} semesters, CGPA: {gpa_data['current_cgpa']}")

        except Exception as e:
            logger.warning(f"Error parsing GPA data: {e}")
            import traceback
            traceback.print_exc()
        
        return gpa_data
    
    def parse_community_service_row(self, cells):
        """Parse individual community service row"""
        try:
            if len(cells) < 2:
                return None
            
            # Based on typical community service table structure:
            # Col 1: Semester (e.g., "Fall-2024", "Spring-2025")
            # Col 2: Activity description
            # Col 3: Hours (often the last column)
            
            semester = cells[0].get_text(strip=True) if len(cells) > 0 else ''
            activity = cells[1].get_text(strip=True) if len(cells) > 1 else ''
            
            service_data = {
                'activity': semester,  # Use semester as activity identifier
                'date': activity,      # Use activity description as date/description
                'hours': 0,
                'status': 'completed'
            }
            
            # Extract hours from any cell - try multiple approaches
            hours_found = False
            for i, cell in enumerate(cells):
                text = cell.get_text(strip=True)
                
                # Try different patterns for hours
                hour_patterns = [
                    r'(\d+(?:\.\d+)?)\s*(?:hours?|hrs?)',  # "X hours" or "X hrs"
                    r'(\d+(?:\.\d+)?)\s*h\b',              # "Xh"
                    r'^(\d+(?:\.\d+)?)$'                    # Just a number in its own cell
                ]
                
                for pattern in hour_patterns:
                    hour_match = re.search(pattern, text, re.IGNORECASE)
                    if hour_match:
                        hours = float(hour_match.group(1))
                        # Only consider reasonable hour values (0-100)
                        if 0 <= hours <= 100:
                            service_data['hours'] = hours
                            hours_found = True
                            break
                
                if hours_found:
                    break
            
            # If no hours found in regular cells, check if last cell might contain hours
            if not hours_found and len(cells) > 2:
                last_cell_text = cells[-1].get_text(strip=True)
                # Check if it's a number
                try:
                    hours = float(last_cell_text)
                    if 0 <= hours <= 100:  # Reasonable range for community service hours
                        service_data['hours'] = hours
                except ValueError:
                    pass
            
            # Only return if we have meaningful data
            if service_data['activity'] or service_data['date']:
                return service_data
            
            return None
            
        except Exception as e:
            logger.warning(f"Error parsing community service row: {e}")
            return None
    
    def parse_semester_row(self, cells):
        """Parse individual semester result row"""
        try:
            if len(cells) < 4:
                return None
            
            semester_data = {
                'semester_name': cells[0].get_text(strip=True) if len(cells) > 0 else '',
                'semester_number': 0,
                'sgpa': 0.0,
                'cgpa': 0.0,
                'credit_hours': 0,
                'status': 'completed'
            }
            
            # Extract numerical data
            for i, cell in enumerate(cells):
                text = cell.get_text(strip=True)
                
                # Look for GPA values
                gpa_match = re.search(r'(\d+\.\d+)', text)
                if gpa_match:
                    gpa_value = float(gpa_match.group(1))
                    if 'sgpa' in cell.get_text(strip=True).lower() or i == 1:
                        semester_data['sgpa'] = gpa_value
                    elif 'cgpa' in cell.get_text(strip=True).lower() or i == 2:
                        semester_data['cgpa'] = gpa_value
                
                # Look for credit hours
                credit_match = re.search(r'(\d+)\s*(?:credits?|hrs?)', text, re.IGNORECASE)
                if credit_match:
                    semester_data['credit_hours'] = int(credit_match.group(1))
            
            # Extract semester number from name
            sem_num_match = re.search(r'(\d+)', semester_data['semester_name'])
            if sem_num_match:
                semester_data['semester_number'] = int(sem_num_match.group(1))
            
            # Only return if we have meaningful data
            if semester_data['semester_name']:
                return semester_data
            
            return None
            
        except Exception as e:
            logger.warning(f"Error parsing semester row: {e}")
            return None
    
    def parse_course_result_row(self, cells):
        """Parse individual course result row"""
        try:
            if len(cells) < 8:
                return None
            
            course_data = {
                'code': cells[1].get_text(strip=True),
                'name': cells[2].get_text(strip=True),
                'credit_hours': 0,
                'grade': cells[5].get_text(strip=True),
                'grade_points': 0.0,
                'status': 'completed'
            }
            
            # Extract credit hours
            try:
                credit_hours = int(cells[4].get_text(strip=True))
                if 0 <= credit_hours <= 6:
                    course_data['credit_hours'] = credit_hours
            except (ValueError, IndexError):
                pass

            # Extract grade points
            try:
                grade_points = float(cells[6].get_text(strip=True))
                course_data['grade_points'] = grade_points
            except (ValueError, IndexError):
                pass

            # Calculate grade points if not found
            if course_data['grade'] and not course_data['grade_points']:
                course_data['grade_points'] = self.calculate_grade_points(course_data['grade'])
            
            # Only return if we have meaningful data
            if course_data['code'] and course_data['name']:
                return course_data
            
            return None
            
        except Exception as e:
            logger.warning(f"Error parsing course result row: {e}")
            return None
    
    def calculate_grade_points(self, grade):
        """Calculate grade points based on the provided grading scheme"""
        grade_scale = {
            'A': 4.0, 'A-': 3.7,
            'B+': 3.3, 'B': 3.0, 'B-': 2.7,
            'C+': 2.3, 'C': 2.0, 'C-': 1.7,
            'D+': 1.3, 'D': 1.0,
            'F': 0.0
        }
        return grade_scale.get(grade.upper(), 0.0)
    
    def scrape_profile(self, username):
        """Scrape student profile information"""
        try:
            # Small delay to ensure session is fully established
            import time
            time.sleep(1)
            
            # First access dashboard to ensure session is active
            dashboard_url = "https://cms.bahria.edu.pk/Sys/Student/Dashboard.aspx"
            dashboard_response = self._request('GET', dashboard_url)
            
            if dashboard_response.status_code != 200:
                logger.warning(f"Failed to access dashboard. Status: {dashboard_response.status_code}")
                return None
            
            # Give the server a bit more time to fully establish the session/profile data
            time.sleep(3)
            
            # Now fetch the profile page
            profile_url = "https://cms.bahria.edu.pk/Sys/Student/Profile.aspx"
            response = self._request('GET', profile_url)
            
            if response.status_code != 200:
                logger.warning(f"Failed to fetch profile page. Status: {response.status_code}")
                return None
            
            # Check if we got redirected back to login
            if 'Login.aspx' in response.url:
                logger.debug("Session expired. Please login again.")
                return None
            
            soup = BeautifulSoup(response.text, 'html.parser')
            profile_data = self.parse_profile(soup)
            
            if profile_data:
                # Save to local storage
                self.save_data(username, 'profile', profile_data)
            
            return profile_data
            
        except Exception as e:
            logger.warning(f"Error scraping profile: {e}")
            import traceback
            traceback.print_exc()
            return None
    
    def save_profile_to_firebase(self, account_id, profile_data):
        """Save profile data to Firebase"""
        try:
            from app.services.profile_service import get_profile_service
            profile_service = get_profile_service()
            return profile_service.save_profile(account_id, profile_data)
        except Exception as e:
            logger.warning(f"Error saving profile to Firebase: {e}")
            return False
    
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
            logger.warning(f"Error parsing profile: {e}")
            import traceback
            traceback.print_exc()
            return None

    def _is_logged_in_cms(self):
        """Check CMS login state using the login page itself (safe even when surveys block LMS)."""
        try:
            # Use the CMS dashboard — always accessible if logged in, even during survey blocks
            response = self._request('GET', 'https://cms.bahria.edu.pk/Sys/Student/Dashboard.aspx')
            final_url = response.headers.get('X-Final-Url', response.url)
            resp_text = response.text or ""

            if 'Login.aspx' in final_url or 'tbEnrollment' in resp_text or 'tbPassword' in resp_text:
                logger.debug("[SURVEY] Not logged in to CMS (login page detected)")
                return False
            # If we see the survey redirect itself, we ARE logged in — just blocked
            if 'QualityAssurance' in final_url or 'QualityAssuranceSurveys' in final_url or 'Complete/evaluate the surveys first' in resp_text or 'Surveys (Pending)' in resp_text:
                logger.debug("[SURVEY] Logged in to CMS, but redirected to survey page — surveys pending")
                return True
            # Normal dashboard access
            if 'Student Portal' in resp_text or ('Dashboard' in final_url and 'tbEnrollment' not in resp_text):
                return True
            # Any non-login page means we're likely still authenticated
            if 'tbEnrollment' not in resp_text and 'tbPassword' not in resp_text:
                return True
            return False
        except Exception as e:
            logger.warning(f"[SURVEY] Error checking CMS login: {e}")
            return False

    def check_for_pending_surveys(self, username):
        """Check if there are any pending CMS surveys.
        
        NOTE: Uses _is_logged_in_cms() instead of verify_login() because when a survey
        is blocking LMS access, the timetable page redirects to the survey page rather than
        a normal page, causing verify_login() to incorrectly return False.
        """
        surveys = []
        try:
            if not self._is_logged_in_cms():
                logger.debug(f"[SURVEY] Not logged in, skipping survey check for {username}")
                return surveys
            
            surveys_url = 'https://cms.bahria.edu.pk/Sys/Student/QualityAssurance/QualityAssuranceSurveys.aspx'
            response = self._request('GET', surveys_url)
            response.raise_for_status()
            
            final_url = response.headers.get('X-Final-Url', response.url)
            logger.debug(f"[SURVEY DEBUG] QA page URL returned: {final_url}")
            
            resp_text = response.text or ""
            # If we got redirected away from the surveys page, something's wrong
            if 'Login.aspx' in final_url or 'tbEnrollment' in resp_text or 'tbPassword' in resp_text:
                logger.debug("[SURVEY] Redirected to login page when accessing surveys — session expired")
                return surveys
            
            soup = BeautifulSoup(response.text, 'html.parser')
            
            # Find survey links — on the QA page these say 'Evaluate' and
            # point to SurveyStudentCourseWise.aspx (not 'Details' as originally assumed)
            links = soup.find_all('a')
            
            logger.debug(f"[SURVEY DEBUG] Total links on QA page: {len(links)}")
            
            for link in links:
                link_text = link.get_text(strip=True).lower()
                href = link.get('href', '') or ''
                # Match 'Evaluate' text OR a direct href to the survey page
                is_survey_link = (
                    link_text == 'evaluate' or
                    'SurveyStudentCourseWise' in href or
                    'details' in link_text  # keep as fallback for other survey variants
                )
                if is_survey_link and href:
                    # Extract course name from the same table row
                    row = link.find_parent('tr')
                    course_name = 'Unknown Survey'
                    if row:
                        cells = row.find_all('td')
                        for cell in cells:
                            text = cell.get_text(strip=True)
                            # Skip empty or the link cell itself
                            if (text and len(text) > 3 and
                                    'evaluate' not in text.lower() and
                                    'details' not in text.lower()):
                                course_name = text
                                break
                    
                    # Resolve relative URL using the QA surveys page as base
                    full_url = href if href.startswith('http') else urljoin(surveys_url, href)
                    surveys.append({'url': full_url, 'name': course_name})
            
            # Deduplicate by URL
            seen = set()
            surveys = [s for s in surveys if not (s['url'] in seen or seen.add(s['url']))]
            
            logger.debug(f"[SURVEY] Found {len(surveys)} pending surveys for {username}")
        except Exception as e:
            logger.warning(f"[SURVEY] Error checking for surveys: {e}")
            import traceback
            traceback.print_exc()
            
        return surveys

    def auto_fill_survey(self, username, survey_url, skip_login_check=False):
        """Fill a single survey form with neutral ('Uncertain') responses.

        Returns:
            (True, 'submitted')  — form was found and submitted
            (False, reason)      — session dead or form not found; caller should re-login and retry
        """
        try:
            if not skip_login_check and not self._is_logged_in_cms():
                return False, 'session_dead'

            logger.debug(f'[SURVEY] Fetching: {survey_url}')
            response = self._request('GET', survey_url)
            response.raise_for_status()

            if 'Login.aspx' in response.url or 'tbEnrollment' in response.text:
                return False, 'session_dead'

            soup = BeautifulSoup(response.text, 'html.parser')

            viewstate = soup.find('input', {'id': '__VIEWSTATE'})
            if not viewstate:
                logger.debug(f'[SURVEY] No __VIEWSTATE at {response.url} — not a survey form')
                return False, 'session_dead'  # Likely got the wrong page due to dead session

            # Find radio groups BEFORE deciding if this is the right page
            radio_groups = {}
            for inp in soup.find_all('input', {'type': 'radio'}):
                r_name = inp.get('name')
                if r_name:
                    radio_groups.setdefault(r_name, []).append(inp)

            if len(radio_groups) == 0:
                logger.debug(f'[SURVEY] 0 radio groups found — wrong page loaded (session may be dead)')
                return False, 'session_dead'

            # Resolve POST URL from form action relative to the SURVEY URL (not the domain)
            form_tag = soup.find('form')
            if form_tag and form_tag.get('action'):
                post_url = urljoin(survey_url, form_tag['action'])
            else:
                post_url = survey_url
            logger.debug(f'[SURVEY] POST target: {post_url}')

            # Detect submit button postback target
            submit_target = 'ctl00$BodyPH$surveyUserControl$btnSubmit'
            pb_match = re.search(r'WebForm_DoPostBackWithOptions\(new\s+WebForm_PostBackOptions\(["\']([^"\']*btnSubmit[^"\']*)["\']', response.text)
            if not pb_match:
                pb_match = re.search(r'WebForm_DoPostBackWithOptions\(new\s+WebForm_PostBackOptions\(&quot;([^&"]*btnSubmit[^&"]*)&quot;', response.text)
            if pb_match:
                submit_target = pb_match.group(1)

            form_data = {
                '__VIEWSTATE': viewstate.get('value', ''),
                '__VIEWSTATEGENERATOR': (soup.find('input', {'id': '__VIEWSTATEGENERATOR'}) or {}).get('value', ''),
                '__EVENTVALIDATION': (soup.find('input', {'id': '__EVENTVALIDATION'}) or {}).get('value', ''),
                '__EVENTTARGET': submit_target,
                '__EVENTARGUMENT': '',
                '__LASTFOCUS': '',
                '__SCROLLPOSITIONX': '0',
                '__SCROLLPOSITIONY': '0',
                'ctl00$hfJsEnabled': '0',
            }

            # All other hidden fields
            for hidden in soup.find_all('input', {'type': 'hidden'}):
                h_name = hidden.get('name')
                if h_name and h_name not in form_data:
                    form_data[h_name] = hidden.get('value', '')

            # Submit button (if input submit exists)
            submit_btn = soup.find('input', {'type': 'submit'}) or soup.find('button', {'type': 'submit'})
            if submit_btn and submit_btn.get('name'):
                form_data[submit_btn['name']] = submit_btn.get('value', 'Submit')
                logger.debug(f'[SURVEY] Submit input: {submit_btn["name"]}={submit_btn.get("value")}')

            logger.debug(f'[SURVEY] Found {len(radio_groups)} radio groups, target={submit_target}')

            # Select 'Uncertain' for each radio group
            for r_name, radios in radio_groups.items():
                chosen_value = None

                # 1. label[for=id] containing 'uncertain'
                for radio in radios:
                    rid = radio.get('id', '')
                    if rid:
                        lbl = soup.find('label', {'for': rid})
                        if lbl and 'uncertain' in lbl.get_text(strip=True).lower():
                            chosen_value = radio.get('value', '')
                            break

                # 2. value attribute contains 'uncertain'
                if chosen_value is None:
                    for radio in radios:
                        if 'uncertain' in radio.get('value', '').lower():
                            chosen_value = radio.get('value', '')
                            break

                # 3. middle radio (index 2 of 5 = Uncertain on Likert scale)
                if chosen_value is None and radios:
                    chosen_value = radios[len(radios) // 2].get('value', '')

                if chosen_value is not None:
                    form_data[r_name] = chosen_value

            # Fill comment textareas
            for ta in soup.find_all('textarea'):
                ta_name = ta.get('name')
                if ta_name:
                    form_data[ta_name] = 'Good and satisfactory'

            unanswered = [n for n in radio_groups if n not in form_data]
            if unanswered:
                logger.debug(f'[SURVEY] WARNING: {len(unanswered)} radio groups have no answer set')

            logger.debug(f'[SURVEY] Submitting {len(form_data)} fields ({len(radio_groups)} answered)')
            self._request('POST', post_url, data=form_data, follow_redirects=True)
            return True, 'submitted'

        except Exception as e:
            logger.warning(f'[SURVEY] Exception filling survey: {e}')
            import traceback
            traceback.print_exc()
            return False, str(e)


    def scrape_all_data(self, username, password):
        """Scrape all available data (timetable, attendance, community service, GPA, profile)"""
        try:
            if not self.login(username, password):
                return None
            
            logger.debug("Scraping profile...")
            profile_data = self.scrape_profile(username)
            
            logger.debug("Scraping timetable...")
            timetable_data = self.scrape_timetable(username)
            
            logger.debug("Scraping attendance...")
            attendance_data = self.scrape_attendance(username)
            
            logger.debug("Scraping community service...")
            community_service_data = self.scrape_community_service(username)
            
            logger.debug("Scraping GPA data...")
            gpa_data = self.scrape_gpa_data(username)
            
            return {
                'profile': profile_data,
                'timetable': timetable_data,
                'attendance': attendance_data,
                'community_service': community_service_data,
                'gpa': gpa_data,
                'scraped_at': datetime.now().isoformat()
            }
            
        except Exception as e:
            logger.warning(f"Error scraping all data: {e}")
            return None
