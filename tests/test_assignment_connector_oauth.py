"""Exercise discovery, PKCE, consent, replay, rotation, and account isolation over HTTP."""

import json
import logging
import time
from threading import Event
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

from authlib.oauth2.rfc7636 import create_s256_code_challenge
from bs4 import BeautifulSoup
from flask import Flask
from flask_login import LoginManager, UserMixin
from flask_wtf import CSRFProtect
import pytest
from starlette.testclient import TestClient

from app.routes.assignment_connector import connector_bp
from lms_mcp.asgi import create_asgi_app
from lms_mcp.assignments import AssignmentResult
from lms_mcp.core import ConnectorSettings
from lms_mcp.oauth import AssignmentOAuthProvider
from lms_mcp.runtime import ConnectorRuntime
from lms_mcp.store import MemoryStore


ORIGIN = 'https://fixture-lms-plus.web.app'
RESOURCE = ORIGIN + '/mcp'
CALLBACK = 'https://claude.ai/api/mcp/auth_callback'
VERIFIER = 'abcde12345' * 6
MCP_HEADERS = {'Accept': 'application/json, text/event-stream', 'Mcp-Protocol-Version': '2025-11-25'}


class Student(UserMixin):
    def __init__(self, id, account_id):
        self.id = id
        self.cms_account = SimpleNamespace(account_id=account_id, display_name='Student account', cms_username='private')


@pytest.fixture
def environment(monkeypatch):
    monkeypatch.setenv('LMS_MCP_ENABLED', 'true')
    store = MemoryStore()
    settings = ConnectorSettings(ORIGIN, 'https://edge.test', bytes.fromhex('11'*32), frozenset({'a', 'b'}))
    owns = {'student-a': 'a', 'student-b': 'b'}
    oauth = AssignmentOAuthProvider(settings, store, lambda uid, aid: owns.get(uid) == aid)
    calls = []
    def get(account_id, course, assignment_id, deadline):
        calls.append((account_id, course))
        return AssignmentResult({'status': 'ready'}, [{'type': 'text', 'text': 'Private assignment for ' + account_id}], 'test')
    runtime = ConnectorRuntime(settings, store, oauth, SimpleNamespace(get=get))
    app = Flask(__name__, template_folder='../app/templates', static_folder='../app/static')
    app.secret_key = 'test-only-secret'
    CSRFProtect(app)
    login = LoginManager(app)
    login.login_view = 'login'
    users = {'student-a': Student('student-a', 'a'), 'student-b': Student('student-b', 'b')}
    login.user_loader(lambda uid: users.get(uid))
    app.add_url_rule('/login', 'auth.login', lambda: 'Sign in')
    app.add_url_rule('/settings', 'settings.settings', lambda: 'Settings')
    # Consent template tests use a minimal surrounding layout; the production
    # layout is separately rendered for visual verification.
    from jinja2 import ChoiceLoader, DictLoader
    app.jinja_loader = ChoiceLoader([DictLoader({'base.html': '<html><head>{% block extra_css %}{% endblock %}</head><body>{% block content %}{% endblock %}</body></html>'}), app.jinja_loader])
    app.extensions['lms_mcp'] = runtime
    app.register_blueprint(connector_bp)
    with TestClient(create_asgi_app(app, runtime=runtime), base_url=ORIGIN) as client:
        yield SimpleNamespace(client=client, oauth=oauth, store=store, app=app, calls=calls, owns=owns)


def sign_in(env, user='student-a', account='a'):
    with env.app.test_client() as flask_client:
        with flask_client.session_transaction() as session:
            session['_user_id'] = user
            session['_fresh'] = True
            session['authenticated_accounts'] = [account]
        value = flask_client.get_cookie('session').value
    env.client.cookies.set('session', value, domain='fixture-lms-plus.web.app', path='/')


def register(env):
    response = env.client.post('/register', json={'client_name': 'Claude', 'redirect_uris': [CALLBACK],
        'token_endpoint_auth_method': 'none', 'grant_types': ['authorization_code', 'refresh_token'], 'response_types': ['code']})
    assert response.status_code == 201, response.text
    return response.json()['client_id']


def authorize(env, client_id, account='a', verifier=VERIFIER):
    response = env.client.get('/authorize', params={'client_id': client_id, 'response_type': 'code',
        'redirect_uri': CALLBACK, 'scope': 'assignments:read', 'resource': RESOURCE,
        'code_challenge': create_s256_code_challenge(verifier), 'code_challenge_method': 'S256', 'state': 'original-state'}, follow_redirects=False)
    assert response.status_code == 302, response.text
    consent_url = response.headers['location']
    sign_in(env, 'student-' + account, account)
    page = env.client.get(consent_url)
    assert page.status_code == 200, page.text
    soup = BeautifulSoup(page.text, 'html.parser')
    bound_account = soup.find('input', {'name': 'account_id'})
    assert bound_account['type'] == 'hidden' and bound_account['value'] == account
    form = {'csrf_token': soup.find('input', {'name': 'csrf_token'})['value'],
            'request': soup.find('input', {'name': 'request'})['value'], 'account_id': account, 'decision': 'allow'}
    options = {'headers': {'Referer': consent_url}, 'follow_redirects': False}
    rejected = env.client.post('/connector/authorize', data={**form, 'account_id': 'b' if account == 'a' else 'a'}, **options)
    assert rejected.status_code == 403
    response = env.client.post('/connector/authorize', data=form, **options)
    assert response.status_code == 302, response.text
    query = parse_qs(urlsplit(response.headers['location']).query)
    assert query['state'] == ['original-state']
    return query['code'][0]


def exchange(env, client_id, code, verifier=VERIFIER):
    return env.client.post('/token', data={'grant_type': 'authorization_code', 'client_id': client_id,
        'code': code, 'redirect_uri': CALLBACK, 'code_verifier': verifier, 'resource': RESOURCE})


def tool(env, token, method='tools/call'):
    return env.client.post('/mcp', headers={**MCP_HEADERS, 'Authorization': 'Bearer ' + token}, json={
        'jsonrpc': '2.0', 'id': 1, 'method': method, 'params': {'name': 'get_pending_assignment',
            'arguments': {'course': 'Networks'}} if method == 'tools/call' else {}})


def test_discovery_unauthenticated_challenge_and_callback_validation(environment):
    env = environment
    response = env.client.post('/mcp', json={})
    assert response.status_code == 401
    assert 'resource_metadata=' in response.headers['www-authenticate']
    assert 'scope="assignments:read"' in response.headers['www-authenticate']
    assert 'invalid_token' not in response.headers['www-authenticate']
    metadata = env.client.get('/.well-known/oauth-protected-resource/mcp').json()
    assert metadata['resource'] == RESOURCE
    assert metadata['scopes_supported'] == ['assignments:read']
    authorization_server = env.client.get('/.well-known/oauth-authorization-server')
    assert authorization_server.status_code == 200
    discovery = authorization_server.json()
    assert discovery['token_endpoint_auth_methods_supported'] == ['none', 'client_secret_basic', 'client_secret_post']
    assert discovery['revocation_endpoint_auth_methods_supported'] == ['none', 'client_secret_basic', 'client_secret_post']
    assert discovery['code_challenge_methods_supported'] == ['S256']
    response = env.client.post('/register', json={'redirect_uris': ['https://user@evil.test/callback'], 'token_endpoint_auth_method': 'none'})
    assert response.status_code == 400


def test_signed_out_consent_resumes_after_login(environment):
    from flask import session
    from app.routes.auth import _resume_connector_consent

    env = environment
    client_id = register(env)
    response = env.client.get('/authorize', params={'client_id': client_id, 'response_type': 'code',
        'redirect_uri': CALLBACK, 'code_challenge': create_s256_code_challenge(VERIFIER),
        'code_challenge_method': 'S256'}, follow_redirects=False)
    consent_url = response.headers['location']
    response = env.client.get(consent_url, follow_redirects=False)
    assert response.status_code == 302 and response.headers['location'] == '/login'
    request_id = parse_qs(urlsplit(consent_url).query)['request'][0]
    with env.app.test_request_context():
        session['mcp_consent_request'] = request_id
        assert _resume_connector_consent('/dashboard') == '/connector/authorize?request=' + request_id
        assert _resume_connector_consent('/dashboard') == '/dashboard'


def test_pkce_code_replay_rotation_revocation_and_no_plaintext_tokens(environment):
    env = environment
    notifications = []
    env.oauth.on_connected = lambda account, provider: notifications.append((account, provider))
    client_id = register(env)
    code = authorize(env, client_id)
    assert exchange(env, client_id, code, 'wrong-verifier').status_code == 400
    assert notifications == []
    response = exchange(env, client_id, code)
    assert response.status_code == 200, response.text
    tokens = response.json()
    assert exchange(env, client_id, code).status_code == 400
    response = tool(env, tokens['access_token'])
    assert response.status_code == 200, response.text
    assert 'Private assignment for a' in response.text
    assert code not in json.dumps(env.store.docs)
    assert tokens['access_token'] not in json.dumps(env.store.docs)
    assert tokens['refresh_token'] not in json.dumps(env.store.docs)
    rotated = env.client.post('/token', data={'grant_type': 'refresh_token', 'client_id': client_id,
                                              'refresh_token': tokens['refresh_token']})
    assert rotated.status_code == 200, rotated.text
    newer = rotated.json()
    assert tool(env, tokens['access_token']).status_code == 401
    assert env.client.post('/token', data={'grant_type': 'refresh_token', 'client_id': client_id,
                                          'refresh_token': tokens['refresh_token']}).status_code == 400
    connection = env.store.connections('student-a')[0]
    env.oauth.disconnect(connection['id'], 'student-a')
    assert tool(env, newer['access_token']).status_code == 401
    assert env.client.post('/token', data={'grant_type': 'refresh_token', 'client_id': client_id,
                                          'refresh_token': newer['refresh_token']}).status_code == 400
    assert notifications == [('a', 'claude')]


def test_browser_account_switch_never_switches_connector_account(environment):
    env = environment
    client_id = register(env)
    tokens_a = exchange(env, client_id, authorize(env, client_id, 'a')).json()
    tokens_b = exchange(env, client_id, authorize(env, client_id, 'b')).json()
    assert 'Private assignment for a' in tool(env, tokens_a['access_token']).text
    assert 'Private assignment for b' in tool(env, tokens_b['access_token']).text
    connection_a = env.store.connections('student-a')[0]
    response = env.client.post('/settings/connections/' + connection_a['id'] + '/disconnect', data={})
    assert response.status_code == 400  # CSRF remains enabled.
    from lms_mcp.core import ConnectorError
    with pytest.raises(ConnectorError):
        env.oauth.disconnect(connection_a['id'], 'student-b')
    env.owns.pop('student-a')
    assert tool(env, tokens_a['access_token']).status_code == 401


def test_read_only_tool_schema_and_expired_tokens(environment):
    env = environment
    client_id = register(env)
    tokens = exchange(env, client_id, authorize(env, client_id)).json()
    response = tool(env, tokens['access_token'], 'tools/list')
    assert response.status_code == 200, response.text
    tools = response.json()['result']['tools']
    assert {t['name'] for t in tools} == {'get_pending_assignment', 'list_assignments'}
    assert all(t['annotations']['readOnlyHint'] for t in tools)
    listing = next(t for t in tools if t['name'] == 'list_assignments')
    assert not listing['inputSchema'].get('required')
    def list_snapshot(account_id, status, deadline):
        assert account_id == 'a'
        assert status == 'pending'
        return AssignmentResult({'status': 'ready', 'snapshot_age_seconds': 3600, 'assignments': {'pending': []}})

    env.app.extensions['lms_mcp'].assignments.list_snapshot = list_snapshot
    response = env.client.post('/mcp', headers={**MCP_HEADERS, 'Authorization': 'Bearer ' + tokens['access_token']},
        json={'jsonrpc': '2.0', 'id': 2, 'method': 'tools/call', 'params': {'name': 'list_assignments', 'arguments': {}}})
    result = response.json()['result']
    assert not result.get('isError')
    assert json.loads(result['content'][0]['text'])['snapshot_age_seconds'] == 3600
    from lms_mcp.store import digest
    key = 'mcp_tokens/' + digest(tokens['access_token'])
    env.store.docs[key]['expires_at'] = 0
    assert tool(env, tokens['access_token']).status_code == 401


@pytest.mark.parametrize('outcome', ['ready', 'missing_attachment', 'timeout'])
def test_tool_timing_event_is_safe_and_matches_metadata(environment, monkeypatch, caplog, outcome):
    from lms_mcp.core import ConnectorError, Deadline

    env = environment
    client_id = register(env)
    token = exchange(env, client_id, authorize(env, client_id)).json()['access_token']
    release = Event()

    def get(account, course, assignment_id, deadline):
        with deadline.timing.measure('download'):
            if outcome == 'timeout':
                release.wait(2)
            elif outcome == 'missing_attachment':
                raise ConnectorError(outcome, 'Missing attachment')
        deadline.timing.record_bytes('attachment', 100)
        return AssignmentResult({'status': 'ready', 'server_elapsed_ms': 1})

    monkeypatch.setattr(env.app.extensions['lms_mcp'].assignments, 'get', get)
    if outcome == 'timeout':
        monkeypatch.setattr('lms_mcp.server.Deadline', lambda: Deadline(.15))
    try:
        with caplog.at_level(logging.INFO, logger='lms_mcp.server'):
            result = tool(env, token).json()['result']
    finally:
        release.set()
    records = [record.getMessage() for record in caplog.records if record.name == 'lms_mcp.server']
    assert len(records) == 1
    metrics = json.loads(records[0])
    assert metrics['outcome'] == outcome
    assert 'Networks' not in records[0] and token not in records[0]
    status = {'ready': 'completed', 'missing_attachment': 'failed', 'timeout': 'interrupted'}[outcome]
    assert metrics['stage_status']['download'] == status
    assert metrics['stage_elapsed_ms'] == result['_meta']['lmsplus']['stage_elapsed_ms']


def test_public_oauth_limits_and_single_use_consent(monkeypatch):
    import asyncio
    from mcp.server.auth.provider import AuthorizationParams, RegistrationError
    from mcp.shared.auth import OAuthClientInformationFull
    from lms_mcp.http_security import registration_source

    async def run_inline(operation, *args):
        return operation(*args)

    monkeypatch.setattr('lms_mcp.oauth.asyncio.to_thread', run_inline)

    async def exercise():
        store = MemoryStore()
        settings = ConnectorSettings(ORIGIN, 'https://edge.test', bytes.fromhex('11' * 32), None)
        oauth = AssignmentOAuthProvider(settings, store, lambda uid, account: (uid, account) == ('user', 'account'))

        def metadata(index):
            return OAuthClientInformationFull(
                client_id=f'client-{index}', redirect_uris=[CALLBACK], token_endpoint_auth_method='none'
            )

        source = registration_source.set('192.0.2.1')
        try:
            for index in range(5):
                await oauth.register_client(metadata(index))
            with pytest.raises(RegistrationError):
                await oauth.register_client(metadata(5))
        finally:
            registration_source.reset(source)

        source = registration_source.set('192.0.2.2')
        try:
            client = metadata(6)
            await oauth.register_client(client)
        finally:
            registration_source.reset(source)

        keys = set(store.docs)
        redirect = await oauth.authorize(client, AuthorizationParams(
            redirect_uri=CALLBACK, redirect_uri_provided_explicitly=True,
            code_challenge=create_s256_code_challenge(VERIFIER), scopes=['assignments:read'], state='state'
        ))
        request_id = parse_qs(urlsplit(redirect).query)['request'][0]
        assert oauth.authorization(request_id)['client_id'] == client.client_id
        pending_key, = set(store.docs) - keys
        assert pending_key.startswith('mcp_consent_used/')
        assert store.get(pending_key)['expires_at'].timestamp() == pytest.approx(time.time() + 300, abs=2)
        assert parse_qs(urlsplit(oauth.approve(request_id, 'user', 'account')).query)['code']
        assert store.get(pending_key) is None
        from lms_mcp.core import ConnectorError
        with pytest.raises(ConnectorError):
            oauth.approve(request_id, 'user', 'account')

    asyncio.run(exercise())
