"""Regression checks for login, group writes, and public connector abuse."""

from io import BytesIO
from types import SimpleNamespace
from unittest.mock import Mock
from zipfile import ZipFile

import pytest

from app import create_app
from app.config import AppConfig
from app.models.account import User
from app.routes import auth, lms
from app.services.testing_access import configure_testing_access
from app.utils.limiter import limiter
from lms_mcp.document_content import _checked_xml
from lms_mcp.office_content import checked_archive


@pytest.fixture
def app(monkeypatch):
    monkeypatch.setenv('SECRET_KEY', 'synthetic')
    monkeypatch.delenv('K_SERVICE', raising=False)
    app = create_app()
    app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)
    limiter.reset()
    return app


@pytest.mark.parametrize('app_env, project, service, enabled', [
    ('production', 'unitrack-61e76', 'university-portal', False),
    ('production', 'unitrack-testing', 'university-portal-test', False),
    ('testing', 'unitrack-61e76', 'university-portal', False),
    ('testing', 'unitrack-testing', '', False),
    ('testing', 'unitrack-testing', 'university-portal-test', True),
])
def test_group_testing_deployment_gate(app, monkeypatch, app_env, project, service, enabled):
    monkeypatch.setenv('APP_ENV', app_env)
    monkeypatch.setenv('APP_URL', 'https://unitrack-testing.web.app')
    monkeypatch.setenv('FIREBASE_PROJECT_ID', project)
    monkeypatch.setenv('K_SERVICE', service)
    configure_testing_access(app, AppConfig())
    assert app.config['TEST_GROUP_UPLOAD_ENABLED'] is enabled


def test_testing_allowlist_rejects_before_cms_and_on_session_restore(app, monkeypatch):
    denied = '02-000000-001'
    allowed = '02-000000-002'
    monkeypatch.setenv('K_SERVICE', 'university-portal-test')
    monkeypatch.setenv('TESTING_LOGIN_ALLOWLIST', allowed)
    configure_testing_access(app, AppConfig())
    verify = Mock()
    monkeypatch.setattr(auth, 'verify_turnstile', verify)
    user = User(denied, denied, 'student@example.test', session_version='version')
    monkeypatch.setattr(User, 'get', Mock(return_value=user))
    client = app.test_client()
    assert client.post('/auth/login', data={'username': denied, 'password': 'password'}).status_code == 403
    verify.assert_not_called()
    with client.session_transaction() as session:
        session['_user_id'] = denied
        session['auth_version'] = 'version'
    assert client.get('/dashboard').status_code == 302



def test_testing_allowlisted_login_reaches_cms_and_missing_config_fails_closed(app, monkeypatch):
    from contextlib import nullcontext

    enrollment = '02-000000-001'
    monkeypatch.setenv('K_SERVICE', 'university-portal-test')
    monkeypatch.setenv('TESTING_LOGIN_ALLOWLIST', enrollment)
    configure_testing_access(app, AppConfig())
    monkeypatch.setattr(auth, 'verify_turnstile', lambda *args, **kwargs: True)
    monkeypatch.setattr('app.utils.proxy_transport.proxy_workers', lambda: ['proxy'])
    monkeypatch.setattr(User, 'get', Mock(return_value=User(enrollment, enrollment, 'student@example.test')))
    scraper = Mock()
    scraper.scrape_full_sync_atomic.return_value = {'auth_failed': True}
    monkeypatch.setattr(auth.scraper_manager, 'credential_scraper', Mock(return_value=nullcontext(scraper)))
    client = app.test_client()
    credentials = {'username': enrollment, 'password': 'wrong'}
    assert client.post('/auth/login', data=credentials).status_code == 401
    scraper.scrape_full_sync_atomic.assert_called_once()
    monkeypatch.delenv('TESTING_LOGIN_ALLOWLIST')
    configure_testing_access(app, AppConfig())
    assert client.post('/auth/login', data=credentials).status_code == 403


def test_stale_hash_cannot_bypass_cms_or_blacklist(app, monkeypatch):
    username = '02-000000-001'
    user = User(username, username, 'student@example.test')
    user.check_password = Mock(return_value=True)
    monkeypatch.setattr(User, 'get', Mock(return_value=user))
    monkeypatch.setattr(auth, 'verify_turnstile', Mock(return_value=True))
    monkeypatch.setattr('app.utils.proxy_transport.proxy_workers', lambda: ['proxy'])
    scraper = Mock()
    scraper.scrape_full_sync_atomic.return_value = {'auth_failed': True}
    from contextlib import nullcontext
    credential_scraper = Mock(return_value=nullcontext(scraper))
    monkeypatch.setattr(auth.scraper_manager, 'credential_scraper', credential_scraper)
    policy = Mock()
    policy.check_access.side_effect = [(False, 'blocked'), (True, '')]
    monkeypatch.setattr(auth, 'get_blacklist_service', lambda: policy)
    client = app.test_client()
    credentials = {'username': username, 'password': 'old'}
    assert client.post('/auth/login', data=credentials).status_code == 403
    credential_scraper.assert_not_called()
    assert client.post('/auth/login', data=credentials).status_code == 401
    credential_scraper.assert_called_once()
    user.check_password.assert_not_called()


def test_forged_cdn_ip_and_victim_enrollment_do_not_control_throttle(app):
    client = app.test_client()
    credentials = {'username': '02-000000-001', 'password': 'wrong', 'website_url': 'bot'}
    for number in range(5):
        response = client.post('/auth/login', data=credentials,
                               headers={'CF-Connecting-IP': f'203.0.113.{number}'})
        assert response.status_code == 400
    assert client.post('/auth/login', data=credentials,
                       headers={'CF-Connecting-IP': '203.0.113.99'}).status_code == 429
    assert client.post('/auth/login', data=credentials,
                       environ_overrides={'REMOTE_ADDR': '192.0.2.8'}).status_code == 400


def test_production_upload_blocks_test_parameters_without_dispatch(app, monkeypatch):
    from inspect import unwrap

    dispatch = Mock()
    monkeypatch.setattr(lms, '_submit_group_operation', dispatch)
    app.config['TEST_GROUP_UPLOAD_ENABLED'] = False
    account = SimpleNamespace(cms_username='02-000000-001', cms_password='password', account_id='account')
    monkeypatch.setattr(lms, 'current_user', SimpleNamespace(get_active_account=lambda: account))
    monkeypatch.setattr(lms, 'is_account_authenticated', lambda _: True)
    with app.test_request_context('/lms/api/assignments/upload', method='POST', data={'test_mode': '1'}):
        from werkzeug.exceptions import NotFound
        with pytest.raises(NotFound):
            unwrap(lms.upload_assignment)()
    dispatch.assert_not_called()


def test_group_consent_and_lms_ids_are_enforced(app, monkeypatch):
    group = {'group_id': 'group', 'owner': 'owner', 'course_id': 'course', 'assignment_id': 'target',
             'roster': {'owner': 'Accepted', 'member': 'Accepted', 'other': 'Declined'}}
    manager = lms.group_manager
    monkeypatch.setattr(manager, 'load_groups', lambda: {'group': group})
    monkeypatch.setattr(manager, 'save_groups', lambda _: True)
    monkeypatch.setattr(manager, 'append_audit_log', Mock())
    assert manager.start_operation('group', 'member', 'uploading') is None
    assert manager.start_operation('group', 'owner', 'uploading', {'other'}) is None
    reserved = manager.start_operation('group', 'owner', 'deleting')
    assert reserved
    with pytest.raises(ValueError):
        manager.start_operation('group', 'owner', 'uploading')

    monkeypatch.setattr(User, 'get', lambda name: SimpleNamespace(
        cms_account=SimpleNamespace(cms_username=name, account_id=name)))
    lookup = Mock(side_effect=[
        [SimpleNamespace(assignment_id='wrong', offered_course_id='wrong')],
        [SimpleNamespace(assignment_id='target', offered_course_id='bound', submission_id='member-submission')]
    ])
    delete = Mock(return_value=True)
    monkeypatch.setattr(lms.lms_service, 'get_assignments_for_course', lookup)
    monkeypatch.setattr(lms.lms_service, 'delete_assignment_submission', delete)
    with app.app_context():
        lms.lms_service._run_group_operation(manager, reserved, 'semester', None)
    delete.assert_called_once_with(account_id='member', semester='semester', course='course',
                                   assignment_id='target', offered_course_id='bound',
                                   submission_id='member-submission')
    assert group['state'] == 'idle'


def test_office_rejects_external_hyperlinks_and_oversized_xml():
    archive = BytesIO()
    with ZipFile(archive, 'w') as office:
        office.writestr('word/_rels/document.xml.rels', '<Relationships><Relationship TargetMode="External" '
                        'Type="urn:relationships/hyperlink" Target="http://127.0.0.1/image"/></Relationships>')
    with pytest.raises(ValueError, match='Externally linked'):
        checked_archive(archive.getvalue())
    with pytest.raises(ValueError, match='processing limit'):
        _checked_xml(b'<root>' + b'<a/>' * 50_000 + b'</root>')
    with pytest.raises(ValueError, match='declarations'):
        _checked_xml('<!DOCTYPE root><root/>'.encode('utf-16'))


def test_fresh_cms_scrapers_never_reuse_or_persist_cookies(app, monkeypatch):
    from app.utils.enhanced_scraper import EnhancedCMSScraper

    monkeypatch.setenv('LMS_WORKER_ORIGIN', 'http://127.0.0.1:8787')
    monkeypatch.setenv('LMS_WORKER_PROXY_KEY', 'ab' * 32)
    monkeypatch.setattr('app.utils.data_manager.data_manager.load_session_data', Mock(side_effect=AssertionError))
    monkeypatch.setattr('app.utils.data_manager.data_manager.save_session_data', Mock(side_effect=AssertionError))
    original_login = Mock(return_value=False)
    monkeypatch.setattr(EnhancedCMSScraper, 'login', original_login)
    username = '02-000000-001'
    with auth.scraper_manager.credential_scraper(username, 'first', '2') as first:
        first.session.cookies.set('test-cookie', 'previous-session')
        assert first.save_session() is False
        with auth.scraper_manager.credential_scraper(username, 'wrong', '2') as second:
            assert first is not second
            assert not second.session.cookies
            assert second.load_session() is False
    with app.app_context():
        assert auth.authenticate_with_cms(username, 'wrong') is False
    original_login.assert_called_once_with(username, 'wrong', force=True)
