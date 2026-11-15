"""Exercise the broader MCP OAuth policy against the SDK HTTP endpoints."""

import asyncio
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from authlib.oauth2.rfc7636 import create_s256_code_challenge

from lms_mcp.client_metadata import RegisteredClient, fetch_client_metadata
from lms_mcp.core import ANTIGRAVITY_CALLBACK, ANTIGRAVITY_CLIENT_ID, VSCODE_CALLBACK, ConnectorError, ConnectorSettings
from lms_mcp.http_security import ConnectorHTTPGuard
from lms_mcp.oauth import AssignmentOAuthProvider
from lms_mcp.runtime import ConnectorRuntime
from lms_mcp.server import build_student_server
from lms_mcp.store import MemoryStore, digest


ORIGIN = 'https://fixture-lms-plus.web.app'
RESOURCE = ORIGIN + '/mcp'
VERIFIER = 'abcde12345' * 6


def test_other_apps_oauth_registration_and_exchange(monkeypatch):
    async def inline_store_call(function, *args):
        return function(*args)

    monkeypatch.setattr(asyncio, 'to_thread', inline_store_call)
    metadata_id = 'https://metadata.example.test/client.json'
    metadata_callback = 'https://different.example.test/oauth/callback'

    async def resolve_metadata(client_id):
        if client_id == metadata_id:
            return RegisteredClient(
                client_id=client_id, client_name='Untrusted label', redirect_uris=[metadata_callback],
                exact_redirect_uris=[metadata_callback], token_endpoint_auth_method='none',
                grant_types=['authorization_code', 'refresh_token'], scope='assignments:read',
            )
        return None

    monkeypatch.setattr('lms_mcp.oauth.fetch_client_metadata', resolve_metadata)

    async def run():
        store = MemoryStore()
        settings = ConnectorSettings(ORIGIN, 'https://edge.test', bytes(32), frozenset({'a'}))
        notifications = []
        oauth = AssignmentOAuthProvider(settings, store, lambda uid, aid: (uid, aid) == ('user', 'a'),
                                        lambda aid, app: notifications.append((aid, app)))
        app = ConnectorHTTPGuard(build_student_server(ConnectorRuntime(settings, store, oauth, None)), ORIGIN)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=ORIGIN) as http:
            meta = await http.get('/.well-known/oauth-authorization-server')
            assert meta.json()['client_id_metadata_document_supported'] is True

            async def register(callback, method='none'):
                return await http.post('/register', json={'client_name': 'Untrusted label',
                    'redirect_uris': [callback], 'token_endpoint_auth_method': method,
                    'grant_types': ['authorization_code', 'refresh_token'], 'response_types': ['code']})

            local = 'http://localhost:32891/callback'
            for callback in ('http://localhost.example.com:32891/callback',
                             'http://user@localhost:32891/callback', 'https://example.test/*',
                             'https://example.test/callback#fragment'):
                assert (await register(callback)).status_code == 400
            response = await register(local)
            assert response.status_code == 201, response.text
            assert (await register('http://[::1]:41200/callback')).status_code == 201
            gemini = 'https://oauth-redirect.googleusercontent.com/r/user_bound_custom-mcp-fixture'
            confidential = await register(gemini, 'client_secret_post')
            assert confidential.status_code == 201, confidential.text
            basic_callback = 'https://Unknown.test:443/callback'
            basic = await register(basic_callback, 'client_secret_basic')
            assert basic.status_code == 201, basic.text
            assert basic.json()['redirect_uris'] == [basic_callback]
            hosted = await register(VSCODE_CALLBACK)
            assert hosted.status_code == 201
            for client_id, callback, label, secret, basic_auth in (
                (response.json()['client_id'], local, 'other', None, False),
                (hosted.json()['client_id'], VSCODE_CALLBACK, 'vscode', None, False),
                (ANTIGRAVITY_CLIENT_ID, ANTIGRAVITY_CALLBACK, 'antigravity', None, False),
                (confidential.json()['client_id'], gemini, 'other', confidential.json()['client_secret'], False),
                (basic.json()['client_id'], basic_callback, 'other', basic.json()['client_secret'], True),
                (metadata_id, metadata_callback, 'other', None, False),
            ):
                state = 's' * 2000
                params = {'client_id': client_id, 'redirect_uri': callback, 'response_type': 'code',
                          'scope': 'assignments:read', 'resource': RESOURCE, 'state': state,
                          'code_challenge': create_s256_code_challenge(VERIFIER), 'code_challenge_method': 'S256'}
                assert (await http.get('/authorize', params={**params, 'redirect_uri': 'http://localhost:32892/callback'})).status_code == 400
                if basic_auth:
                    alias = {**params, 'redirect_uri': 'https://unknown.test/callback'}
                    assert (await http.get('/authorize', params=alias)).status_code == 400
                consent = await http.get('/authorize', params=params)
                assert consent.status_code == 302, consent.text
                request_id = parse_qs(urlsplit(consent.headers['location']).query)['request'][0]
                if client_id == metadata_id:
                    assert oauth.authorization(request_id)['client_name'] == 'different.example.test'
                redirect = oauth.approve(request_id, 'user', 'a')
                assert redirect.split('?')[0] == callback
                assert parse_qs(urlsplit(redirect).query)['state'] == [state]
                assert len(request_id) == 48
                with pytest.raises(ConnectorError):
                    oauth.approve(request_id, 'user', 'a')
                code = parse_qs(urlsplit(redirect).query)['code'][0]
                form = {'grant_type': 'authorization_code', 'client_id': client_id, 'code': code,
                        'redirect_uri': callback, 'code_verifier': VERIFIER, 'resource': RESOURCE}
                auth = (client_id, secret) if basic_auth else None
                credentials = {'client_secret': secret} if secret and not basic_auth else {}
                if secret:
                    stored = store.get('mcp_clients/' + digest(client_id))
                    assert 'client_secret' not in stored and secret not in stored['encrypted_client_secret']
                    bad_auth = (client_id, 'wrong') if basic_auth else None
                    bad_form = form if basic_auth else {**form, 'client_secret': 'wrong'}
                    assert (await http.post('/token', data=bad_form, auth=bad_auth)).status_code == 401
                if basic_auth:
                    assert (await http.post('/token', data={**form, 'redirect_uri': 'https://unknown.test/callback'},
                                            auth=auth)).status_code == 400
                form.update(credentials)
                if basic_auth:
                    form.pop('client_id')
                assert (await http.post('/token', data={**form, 'code_verifier': 'z' * 43}, auth=auth)).status_code == 400
                issued = await http.post('/token', data=form, auth=auth)
                assert issued.status_code == 200, issued.text
                assert (await http.post('/token', data=form, auth=auth)).status_code == 400
                refresh = await http.post('/token', data={'grant_type': 'refresh_token', 'client_id': client_id,
                    'refresh_token': issued.json()['refresh_token'], 'resource': RESOURCE, **credentials}, auth=auth)
                assert refresh.status_code == 200, refresh.text
                access = refresh.json()['access_token']
                claims = (await oauth.load_access_token(access)).claims
                assert claims['account_id'] == 'a'
                with pytest.raises(ConnectorError):
                    oauth.disconnect(claims['connection_id'], 'another-user')
                if secret:
                    revoke_form = {'token': access, **credentials}
                    if not basic_auth:
                        revoke_form['client_id'] = client_id
                    revoked = await http.post('/revoke', data=revoke_form, auth=auth)
                    assert revoked.status_code == 200, revoked.text
                else:
                    oauth.disconnect(claims['connection_id'], 'user')
                assert await oauth.load_access_token(access) is None
                assert notifications[-1] == ('a', label)
            assert len(notifications) == 6

    asyncio.run(run())


def test_metadata_fetch_pins_public_ip_and_rejects_unsafe_responses(monkeypatch):
    client_id = 'https://metadata.example.test/client.json'
    callback = 'https://oauth-redirect.googleusercontent.com/r/user_bound_custom-mcp-fixture'
    document = {'client_id': client_id, 'client_name': 'Gemini', 'redirect_uris': [callback]}
    addresses = ['8.8.8.8']
    response = httpx.Response(200, json=document)
    calls = []
    real_client = httpx.AsyncClient

    def serve(request):
        calls.append(request)
        assert str(request.url) == 'https://8.8.8.8/client.json'
        assert request.headers['host'] == 'metadata.example.test'
        assert request.extensions['sni_hostname'] == 'metadata.example.test'
        return httpx.Response(response.status_code, headers=response.headers, stream=httpx.ByteStream(response.content))

    def mock_client(**options):
        assert options == {'trust_env': False, 'follow_redirects': False}
        return real_client(transport=httpx.MockTransport(serve), **options)

    async def run():
        async def resolve(host, port, **kwargs):
            assert (host, port) == ('metadata.example.test', 443)
            return [(None, None, None, '', (address, port)) for address in addresses]

        monkeypatch.setattr(asyncio.get_running_loop(), 'getaddrinfo', resolve)
        monkeypatch.setattr(httpx, 'AsyncClient', mock_client)
        client = await fetch_client_metadata(client_id)
        assert client.exact_redirect_uris == [callback]
        addresses.append('169.254.169.254')
        assert await fetch_client_metadata(client_id) is None
        assert len(calls) == 1

    asyncio.run(run())


@pytest.mark.parametrize('response', [
    httpx.Response(302, headers={'Location': 'https://127.0.0.1/'}),
    httpx.Response(200, content=b' ' * (64 * 1024 + 1)),
    httpx.Response(200, json={'client_id': 'https://other.test/document', 'client_name': 'Other',
                              'redirect_uris': ['https://other.test/callback']}),
])
def test_metadata_fetch_rejects_invalid_response(monkeypatch, response):
    real_client = httpx.AsyncClient

    async def run():
        async def resolve(host, port, **kwargs):
            return [(None, None, None, '', ('8.8.8.8', port))]

        monkeypatch.setattr(asyncio.get_running_loop(), 'getaddrinfo', resolve)
        monkeypatch.setattr(httpx, 'AsyncClient', lambda **kwargs: real_client(
            transport=httpx.MockTransport(lambda request: httpx.Response(
                response.status_code, headers=response.headers, stream=httpx.ByteStream(response.content))), **kwargs))
        assert await fetch_client_metadata('https://metadata.example.test/client.json') is None

    asyncio.run(run())
