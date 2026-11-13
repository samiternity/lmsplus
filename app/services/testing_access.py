"""Restrict hosted testing access using deployment identity and private configuration."""

import os
import re
from pathlib import Path

from flask import current_app

TESTING_PROJECT = 'unitrack-testing'
TESTING_SERVICE = 'university-portal-test'
TESTING_URL = 'https://unitrack-testing.web.app'
ALLOWLIST_NAME = 'testing-login-allowlist.txt'


def configure_testing_access(app, config):
    service = os.environ.get('K_SERVICE')
    is_testing_site = (
        config.firebase_project_id == TESTING_PROJECT
        or service == TESTING_SERVICE
        or config.app_url.rstrip('/') == TESTING_URL
    )
    app.config['TESTING_LOGIN_ALLOWLIST'] = None
    app.config['TEST_GROUP_UPLOAD_ENABLED'] = (
        config.is_testing
        and config.firebase_project_id == TESTING_PROJECT
        and service == TESTING_SERVICE
        and config.app_url.rstrip('/') == TESTING_URL
    )
    if not is_testing_site:
        return

    entries = os.environ.get('TESTING_LOGIN_ALLOWLIST', '')
    if not service and not entries:
        path = Path(app.root_path).parent / ALLOWLIST_NAME
        if path.is_file():
            entries = path.read_text(encoding='utf-8')

    allowlist = frozenset(entries.split())
    if not allowlist or any(not re.fullmatch(r'(0[1-9]|1[0-5])-[0-9]{6}-[0-9]{3}', entry)
                            for entry in allowlist):
        app.logger.error('Testing login allowlist is missing or invalid. Testing logins are blocked.')
        allowlist = frozenset()
    app.config['TESTING_LOGIN_ALLOWLIST'] = allowlist


def testing_login_allowed(enrollment):
    allowlist = current_app.config['TESTING_LOGIN_ALLOWLIST']
    return allowlist is None or enrollment in allowlist
