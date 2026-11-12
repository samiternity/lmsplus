"""Exercise the broader MCP OAuth policy against the SDK HTTP endpoints."""

import asyncio
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from authlib.oauth2.rfc7636 import create_s256_code_challenge

from lms_mcp.core import ANTIGRAVITY_CALLBACK, ANTIGRAVITY_CLIENT_ID, VSCODE_CALLBACK, ConnectorError, ConnectorSettings
from lms_mcp.oauth import AssignmentOAuthProvider
from lms_mcp.runtime import ConnectorRuntime
from lms_mcp.server import build_student_server
from lms_mcp.store import MemoryStore


ORIGIN = 'https://fixture-lms-plus.web.app'
RESOURCE = ORIGIN + '/mcp'
VERIFIER = 'abcde12345' * 6


def test_other_apps_oauth_registration_and_exchange(monkeypatch):
    async def inline_store_call(function, *args):
        return function(*args)

    monkeypatch.setattr(asyncio, 'to_thread', inline_store_call)

    async def run():
        store = MemoryStore()
        settings = ConnectorSettings(ORIGIN, 'https://edge.test', bytes(32), frozenset({'a'}))
        notifications = []
        oauth = AssignmentOAuthProvider(settings, store, lambda uid, aid: (uid, aid) == ('user', 'a'),
                                        lambda aid, app: notifications.append((aid, app)))
        app = build_student_server(ConnectorRuntime(settings, store, oauth, None))
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=ORIGIN) as http:
            meta = await http.get('/.well-known/oauth-authorization-server')
            assert meta.json()['client_id_metadata_document_supported'] is True

            async def register(callback):
                return await http.post('/register', json={'client_name': 'Untrusted label',
                    'redirect_uris': [callback], 'token_endpoint_auth_method': 'none',
                    'grant_types': ['authorization_code', 'refresh_token'], 'response_types': ['code']})

            local = 'http://localhost:32891/callback'
            for callback in ('http://localhost.example.com:32891/callback',
                             'http://user@localhost:32891/callback', 'https://unknown.test/callback',
                             ANTIGRAVITY_CALLBACK):
                assert (await register(callback)).status_code == 400
            response = await register(local)
            assert response.status_code == 201, response.text
            assert (await register('http://[::1]:41200/callback')).status_code == 201
            assert (await register('http://127.0.0.1:19876/mcp/oauth/callback')).status_code == 201
            hosted = await register(VSCODE_CALLBACK)
            assert hosted.status_code == 201
            for client_id, callback, label in ((response.json()['client_id'], local, 'other'),
                                                (hosted.json()['client_id'], VSCODE_CALLBACK, 'vscode'),
                                                (ANTIGRAVITY_CLIENT_ID, ANTIGRAVITY_CALLBACK, 'antigravity')):
                params = {'client_id': client_id, 'redirect_uri': callback, 'response_type': 'code',
                          'scope': 'assignments:read', 'resource': RESOURCE, 'state': 'test',
                          'code_challenge': create_s256_code_challenge(VERIFIER), 'code_challenge_method': 'S256'}
                assert (await http.get('/authorize', params={**params, 'redirect_uri': 'http://localhost:32892/callback'})).status_code == 400
                consent = await http.get('/authorize', params=params)
                assert consent.status_code == 302, consent.text
                request_id = parse_qs(urlsplit(consent.headers['location']).query)['request'][0]
                redirect = oauth.approve(request_id, 'user', 'a')
                assert redirect.split('?')[0] == callback
                code = parse_qs(urlsplit(redirect).query)['code'][0]
                form = {'grant_type': 'authorization_code', 'client_id': client_id, 'code': code,
                        'redirect_uri': callback, 'code_verifier': VERIFIER, 'resource': RESOURCE}
                assert (await http.post('/token', data={**form, 'code_verifier': 'z' * 43})).status_code == 400
                issued = await http.post('/token', data=form)
                assert issued.status_code == 200, issued.text
                assert (await http.post('/token', data=form)).status_code == 400
                refresh = await http.post('/token', data={'grant_type': 'refresh_token', 'client_id': client_id,
                    'refresh_token': issued.json()['refresh_token'], 'resource': RESOURCE})
                assert refresh.status_code == 200, refresh.text
                access = refresh.json()['access_token']
                claims = (await oauth.load_access_token(access)).claims
                assert claims['account_id'] == 'a'
                with pytest.raises(ConnectorError):
                    oauth.disconnect(claims['connection_id'], 'another-user')
                oauth.disconnect(claims['connection_id'], 'user')
                assert await oauth.load_access_token(access) is None
                assert notifications[-1] == ('a', label)
            assert len(notifications) == 3

    asyncio.run(run())
