"""Bounded university access using the existing atomic gateway and HTML parser."""

import base64
import json
import os
import time
from urllib.parse import urlencode

import requests
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from lms_mcp.core import ConnectorError
from lms_mcp.document_content import MAX_SOURCE_BYTES


def issue_download_token(settings, file_url, cookies, now=None):
    now = int(time.time() if now is None else now)
    settings.check_file_url(file_url)
    payload = {"purpose": "mcp-assignment-download", "url": file_url, "cookies": cookies,
               "issued": now, "expires": now + 120}
    nonce = os.urandom(12)
    raw = AESGCM(settings.key).encrypt(nonce, json.dumps(payload).encode(), b"mcp-download-v1")
    return base64.urlsafe_b64encode(nonce + raw).decode().rstrip('=')


def ingest_proxy_cookies(response, session):
    # Do not consume X-Worker-Debug-Log: it may contain credential-bearing URLs.
    for cookie in response.headers.get('X-Proxy-Set-Cookie', '').split('|||'):
        pair = cookie.split(';', 1)[0].strip()
        if '=' in pair:
            name, value = pair.split('=', 1)
            session.cookies.set(name, value)


class UniversityClient:
    def __init__(self, settings, account_loader, parser_factory=None, http=None):
        self.settings, self.account_loader = settings, account_loader
        if parser_factory is None:
            from app.services.lms_scraper_service import LMSScraperService
            parser_factory = LMSScraperService
        self.parser_factory = parser_factory
        # No shared cookie jar across student accounts.
        self.http = http or requests

    def recover(self, account_id, deadline):
        # Select this exact CMS account; never consult the owner's active account.
        account = self.account_loader(account_id)
        if not account or not account.cms_username or not account.cms_password:
            raise ConnectorError("authentication_failure", "Reconnect your university account in LMS+.")
        from app.routes.auth import get_institute_id_for_enrollment
        try:
            with requests.Session() as session:
                response = session.post(self.settings.worker_origin + '/?macro=true', json={
                    'action': 'lms_gateway', 'username': account.cms_username,
                    'password': account.cms_password,
                    'institute_id': str(get_institute_id_for_enrollment(account.cms_username,
                                         fallback=getattr(account, 'institute_id', '2'))),
                }, headers=self.settings.proxy_headers, timeout=deadline.remaining(25), allow_redirects=False)
                try:
                    if response.status_code == 409 or response.headers.get('X-CMS-Survey-Blocked') == 'true':
                        raise ConnectorError("authentication_failure", "Complete the mandatory university survey in LMS+ first.")
                    parser = self.parser_factory()
                    if response.status_code != 200 or parser._requires_cms_authentication(response):
                        raise ConnectorError("authentication_failure", "University session recovery failed. Check LMS+ sign-in.")
                    ingest_proxy_cookies(response, session)
                    cookies = session.cookies.get_dict()
                    if not cookies:
                        raise ConnectorError("authentication_failure", "University session recovery returned no session.")
                    return cookies
                finally:
                    response.close()
        except requests.RequestException:
            raise ConnectorError("university_failure", "The university could not be reached during sign-in.") from None

    def page(self, cookies, params, deadline):
        target = 'https://lms.bahria.edu.pk/Student/Assignments.php'
        if params:
            target += '?' + urlencode(params)
        try:
            response = self.http.get(self.settings.worker_origin + '/', params={'url': target, 'native_redirects': 'true'},
                headers={**self.settings.proxy_headers, 'Cookie': '; '.join(f'{k}={v}' for k, v in cookies.items())},
                timeout=deadline.remaining(15), allow_redirects=False, stream=True)
            with response:
                if response.is_redirect or response.status_code in (401, 403):
                    raise ConnectorError("authentication_failure", "The university session expired.")
                if response.status_code != 200:
                    raise ConnectorError("university_failure", "The university assignment page is unavailable.")
                data = bytearray()
                for chunk in response.iter_content(65536):
                    deadline.remaining()
                    data.extend(chunk)
                    if len(data) > 2 * 1024 * 1024:
                        raise ConnectorError("processing_limit", "The university assignment page exceeded the processing limit.")
                response._content = bytes(data)
                response._content_consumed = True
                if self.parser_factory()._requires_cms_authentication(response):
                    raise ConnectorError("authentication_failure", "The university session expired.")
                for pair in response.headers.get('X-Proxy-Set-Cookie', '').split('|||'):
                    if '=' in pair:
                        name, value = pair.split(';', 1)[0].split('=', 1)
                        cookies[name.strip()] = value
                return response
        except requests.RequestException:
            raise ConnectorError("university_failure", "The university assignment request failed.") from None

    def options(self, cookies, deadline):
        parser = self.parser_factory()
        page = self.page(cookies, {}, deadline)
        soup = parser._parse_html(page.text, parser.assignments_url)
        courses = parser._extract_courses_from_dropdown(soup)
        semesters = parser._extract_semesters_from_dropdown(soup)
        if not courses or not semesters:
            raise ConnectorError("scraping_failure", "University course options could not be read.")
        selected = soup.select_one('#semesterId option[selected]')
        from urllib.parse import unquote
        semester = unquote(selected.get('value')) if selected else semesters[0]['code']
        return {'courses': courses, 'semester': semester}

    def assignments(self, cookies, semester, course, deadline):
        from app.services.lms_scraper_service import LMSAuthenticationRequired, LMSParsingError
        parser = self.parser_factory()
        page = self.page(cookies, {'s': semester, 'oc': course}, deadline)
        try:
            return [assignment.to_dict() for assignment in parser.parse_course_assignments_response(page, course)]
        except LMSAuthenticationRequired:
            raise ConnectorError("authentication_failure", "The university session expired.") from None
        except LMSParsingError:
            raise ConnectorError("scraping_failure", "The university assignment table could not be read.") from None

    def download(self, cookies, file_url, deadline):
        self.settings.check_file_url(file_url)
        token = issue_download_token(self.settings, file_url, cookies)
        try:
            response = self.http.post(self.settings.worker_origin + '/mcp-download', data={'token': token},
                                      headers=self.settings.proxy_headers,
                                      timeout=deadline.remaining(20), allow_redirects=False, stream=True)
            with response:
                if response.status_code == 401 or response.is_redirect:
                    raise ConnectorError("authentication_failure", "The university file session expired.")
                if response.status_code == 404:
                    raise ConnectorError("missing_attachment", "The assignment attachment is unavailable.")
                if response.status_code == 413:
                    raise ConnectorError("processing_limit", "The assignment exceeds the 10 MiB file limit.")
                if response.status_code == 415:
                    raise ConnectorError("unsupported_content", "The university returned an unsupported file format.")
                if response.status_code != 200:
                    raise ConnectorError("university_failure", "The university attachment could not be downloaded.")
                if 'text/html' in response.headers.get('Content-Type', '').lower():
                    raise ConnectorError("authentication_failure", "The university returned a sign-in page instead of the file.")
                data = bytearray()
                for chunk in response.iter_content(65536):
                    deadline.remaining()
                    data.extend(chunk)
                    if len(data) > MAX_SOURCE_BYTES:
                        raise ConnectorError("processing_limit", "The assignment exceeds the 10 MiB file limit.")
                return bytes(data)
        except requests.RequestException:
            raise ConnectorError("university_failure", "The university attachment transfer failed.") from None
