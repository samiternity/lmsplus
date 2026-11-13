"""SDK OAuth provider backed by revocable account-bound Firestore records."""

import asyncio
from base64 import urlsafe_b64encode
from datetime import datetime, timezone
import hmac
import json
import logging
import time
from urllib.parse import urlencode

from cryptography.fernet import Fernet, InvalidToken

from authlib.common.security import generate_token
from authlib.common.urls import add_params_to_uri
from mcp.server.auth.provider import (
    AccessToken, AuthorizationCode, AuthorizationParams, AuthorizeError,
    OAuthAuthorizationServerProvider, RefreshToken, RegistrationError, TokenError,
)
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken

from lms_mcp.core import (
    ANTIGRAVITY_CALLBACK, ANTIGRAVITY_CLIENT_ID, PROVIDER_LABELS, ConnectorError, SCOPE,
    callback_provider,
)
from lms_mcp.http_security import registration_source
from lms_mcp.store import digest

logger = logging.getLogger(__name__)


class AssignmentOAuthProvider(OAuthAuthorizationServerProvider):
    def __init__(self, settings, store, owns_account, on_connected=None):
        self.settings, self.store, self.owns_account = settings, store, owns_account
        self.on_connected = on_connected
        # Derive a separate key for short-lived browser consent requests.
        self.consent_cipher = Fernet(urlsafe_b64encode(hmac.digest(settings.key, b'mcp-consent-v1', 'sha256')))

    async def get_client(self, client_id):
        if client_id == ANTIGRAVITY_CLIENT_ID:
            return OAuthClientInformationFull(
                client_id=client_id, client_name=PROVIDER_LABELS['antigravity'],
                redirect_uris=[ANTIGRAVITY_CALLBACK], token_endpoint_auth_method='none',
                grant_types=['authorization_code', 'refresh_token'], response_types=['code'], scope=SCOPE)
        record = await asyncio.to_thread(self.store.get, 'mcp_clients/' + digest(client_id))
        return OAuthClientInformationFull.model_validate(record) if record else None

    async def register_client(self, client_info):
        if (client_info.token_endpoint_auth_method != 'none' or not client_info.redirect_uris
                or any(not self.settings.allows_callback(str(uri)) for uri in client_info.redirect_uris)
                or set(client_info.grant_types) - {'authorization_code', 'refresh_token'}
                or (client_info.scope and set(client_info.scope.split()) - {SCOPE})):
            raise RegistrationError('invalid_client_metadata', 'Use a supported public client with read-only assignment access.')
        client_info.client_secret = None
        # Registration is bounded independently from student account grants.
        day = time.strftime('%Y-%m-%d', time.gmtime())
        quota_key = 'mcp_limits/registration-' + day
        source_key = 'mcp_limits/registration-source-' + digest(day + ':' + registration_source.get())
        client_key = 'mcp_clients/' + digest(client_info.client_id)

        def register(docs):
            quota = docs[quota_key] or {'count': 0}
            source = docs[source_key] or {'count': 0}
            if source['count'] >= 5 or quota['count'] >= 200:
                raise RegistrationError('invalid_client_metadata', 'Client registration is temporarily unavailable.')
            quota['count'] += 1
            source['count'] += 1
            return {quota_key: quota, source_key: source, client_key: client_info.model_dump(mode='json')}, None

        await asyncio.to_thread(self.store.run, [quota_key, source_key, client_key], register)

    async def authorize(self, client, params: AuthorizationParams):
        if (params.resource not in (None, self.settings.resource)
                or set(params.scopes or [SCOPE]) != {SCOPE}):
            raise AuthorizeError('invalid_scope', 'Only read-only assignment access to this connector is available.')
        callback = str(params.redirect_uri)
        if params.redirect_uri not in (client.redirect_uris or []):
            raise AuthorizeError('invalid_request', 'The callback does not match this client registration.')
        if not self.settings.allows_callback(callback, pinned=client.client_id == ANTIGRAVITY_CLIENT_ID):
            raise AuthorizeError('unauthorized_client', 'This app is not enabled for LMS+.')
        provider = callback_provider(callback)
        request = {
            'params': params.model_dump(mode='json'), 'client_id': client.client_id,
            'client_name': PROVIDER_LABELS[provider], 'expires': time.time() + 300,
        }
        request_id = self.consent_cipher.encrypt(json.dumps(request).encode()).decode()
        if len(request_id) > 2400:
            raise AuthorizeError('invalid_request', 'Authorization request is too large.')
        return self.settings.origin + '/connector/authorize?' + urlencode({'request': request_id})

    def authorization(self, request_id):
        if not isinstance(request_id, str) or len(request_id) > 2400:
            return None
        try:
            record = json.loads(self.consent_cipher.decrypt(request_id.encode(), ttl=300))
        except (InvalidToken, ValueError):
            return None
        return record if record['expires'] > time.time() else None

    def approve(self, request_id, user_id, account_id):
        self.settings.check_account(account_id)
        if not self.owns_account(user_id, account_id):
            raise ConnectorError('connection_unavailable', 'This university account is unavailable.')
        code, connection_id = generate_token(48), generate_token(32)
        pending_key = 'mcp_consent_used/' + digest(request_id)
        request = self.authorization(request_id)
        code_key, connection_key = 'mcp_codes/' + digest(code), 'mcp_connections/' + connection_id
        def grant(docs):
            if docs[pending_key] or not request or request['expires'] <= time.time():
                raise ConnectorError('connection_unavailable', 'The connection request expired. Start again in your AI app.')
            params = request['params']
            if not self.settings.allows_callback(params['redirect_uri'], pinned=request['client_id'] == ANTIGRAVITY_CLIENT_ID):
                raise ConnectorError('connection_unavailable', 'This app is not enabled for LMS+.')
            code_record = dict(params, client_id=request['client_id'], expires_at=time.time() + 120,
                               subject=user_id, connection_id=connection_id)
            code_record['scopes'] = [SCOPE]
            code_record['resource'] = self.settings.resource
            connection = {'user_id': user_id, 'account_id': account_id, 'client_id': request['client_id'],
                          'client_name': request['client_name'], 'created': time.time(), 'revoked': False,
                          'provider': callback_provider(params['redirect_uri']),
                          'access_hash': None, 'refresh_hash': None, 'resource': self.settings.resource}
            changes = {pending_key: {'expires_at': datetime.fromtimestamp(request['expires'], timezone.utc)}, code_key: code_record, connection_key: connection}
            redirect = add_params_to_uri(params['redirect_uri'], [(key, value) for key, value in {
                'code': code, 'state': params.get('state'), 'iss': self.settings.origin}.items() if value is not None])
            return changes, redirect
        return self.store.run([pending_key, code_key, connection_key], grant)

    def deny(self, request_id):
        request = self.authorization(request_id)
        if not request:
            raise ConnectorError('connection_unavailable', 'The connection request expired.')
        key = 'mcp_consent_used/' + digest(request_id)

        def deny_request(docs):
            if docs[key] or request['expires'] <= time.time():
                raise ConnectorError('connection_unavailable', 'The connection request expired.')
            params = request['params']
            return {key: {'expires_at': datetime.fromtimestamp(request['expires'], timezone.utc)}}, add_params_to_uri(
                params['redirect_uri'], [(k, v) for k, v in {
                    'error': 'access_denied', 'state': params.get('state'), 'iss': self.settings.origin
                }.items() if v is not None]
            )
        return self.store.run([key], deny_request)

    def _check_connection(self, connection):
        if not connection or connection.get('revoked'):
            raise TokenError('invalid_grant', 'The connection was revoked.')
        try:
            self.settings.check_account(connection['account_id'])
        except ConnectorError:
            raise TokenError('invalid_grant', 'The connection is unavailable.') from None
        if not self.owns_account(connection['user_id'], connection['account_id']):
            raise TokenError('invalid_grant', 'The account is no longer available.')

    async def load_authorization_code(self, client, authorization_code):
        record = await asyncio.to_thread(self.store.get, 'mcp_codes/' + digest(authorization_code))
        if not record or record['client_id'] != client.client_id:
            return None
        return AuthorizationCode(code=authorization_code, **{k: v for k, v in record.items() if k != 'connection_id'})

    def _issue(self, connection_id, connection, access, refresh):
        now = int(time.time())
        connection.update(access_hash=digest(access), refresh_hash=digest(refresh),
                          access_expires=now + 900, refresh_expires=now + 30 * 86400)
        common = {'connection_id': connection_id, 'client_id': connection['client_id'],
                  'scopes': [SCOPE], 'resource': self.settings.resource, 'subject': connection['user_id']}
        records = {'mcp_tokens/' + digest(access): dict(common, kind='access', expires_at=now + 900),
                   'mcp_tokens/' + digest(refresh): dict(common, kind='refresh', expires_at=now + 30 * 86400)}
        return records, OAuthToken(access_token=access, token_type='Bearer', expires_in=900,
                                   refresh_token=refresh, scope=SCOPE)

    async def exchange_authorization_code(self, client, authorization_code):
        code_key = 'mcp_codes/' + digest(authorization_code.code)
        record = await asyncio.to_thread(self.store.get, code_key)
        if not record:
            raise TokenError('invalid_grant', 'Authorization code is unavailable.')
        connection_key = 'mcp_connections/' + record['connection_id']
        access, refresh = generate_token(48), generate_token(48)
        token_keys = ['mcp_tokens/' + digest(access), 'mcp_tokens/' + digest(refresh)]
        def exchange(docs):
            code, connection = docs[code_key], docs[connection_key]
            self._check_connection(connection)
            if not code or code['expires_at'] <= time.time() or code['client_id'] != client.client_id:
                raise TokenError('invalid_grant', 'Authorization code is expired or already used.')
            tokens, response = self._issue(record['connection_id'], connection, access, refresh)
            provider = callback_provider(code['redirect_uri'])
            return {**tokens, code_key: None, connection_key: connection}, (response, connection['account_id'], provider)
        response, account_id, provider = await asyncio.to_thread(
            self.store.run, [code_key, connection_key, *token_keys], exchange)
        if self.on_connected:
            try:
                self.on_connected(account_id, provider)
            except Exception:
                logger.warning('Could not queue the MCP authorization notification.')
        return response

    def _load_token(self, token, kind):
        if not isinstance(token, str) or len(token) > 256:
            return None
        token_hash = digest(token)
        record = self.store.get('mcp_tokens/' + token_hash)
        if not record or record['kind'] != kind or record['expires_at'] <= time.time():
            return None
        connection = self.store.get('mcp_connections/' + record['connection_id'])
        try:
            self._check_connection(connection)
        except TokenError:
            return None
        if not hmac.compare_digest(connection.get(kind + '_hash') or '', token_hash):
            return None
        return record, connection

    async def load_access_token(self, token):
        loaded = await asyncio.to_thread(self._load_token, token, 'access')
        if not loaded:
            return None
        record, connection = loaded
        return AccessToken(token=token, client_id=record['client_id'], scopes=record['scopes'],
                           expires_at=record['expires_at'], resource=record['resource'], subject=record['subject'],
                           claims={'account_id': connection['account_id'], 'connection_id': record['connection_id']})

    async def load_refresh_token(self, client, refresh_token):
        loaded = await asyncio.to_thread(self._load_token, refresh_token, 'refresh')
        if not loaded or loaded[0]['client_id'] != client.client_id:
            return None
        record = loaded[0]
        return RefreshToken(token=refresh_token, **{k: v for k, v in record.items() if k not in ('kind', 'connection_id')})

    async def exchange_refresh_token(self, client, refresh_token, scopes):
        loaded = await asyncio.to_thread(self._load_token, refresh_token.token, 'refresh')
        if not loaded or scopes != [SCOPE] or loaded[0]['client_id'] != client.client_id:
            raise TokenError('invalid_grant', 'Refresh token is unavailable.')
        record, _ = loaded
        connection_key = 'mcp_connections/' + record['connection_id']
        access, refresh = generate_token(48), generate_token(48)
        token_keys = ['mcp_tokens/' + digest(access), 'mcp_tokens/' + digest(refresh)]
        def rotate(docs):
            connection = docs[connection_key]
            self._check_connection(connection)
            if (not hmac.compare_digest(connection.get('refresh_hash') or '', digest(refresh_token.token))
                    or connection.get('refresh_expires', 0) <= time.time()):
                raise TokenError('invalid_grant', 'Refresh token has already been used.')
            tokens, response = self._issue(record['connection_id'], connection, access, refresh)
            return {**tokens, connection_key: connection}, response
        return await asyncio.to_thread(self.store.run, [connection_key, *token_keys], rotate)

    def disconnect(self, connection_id, user_id):
        key = 'mcp_connections/' + connection_id
        def revoke(docs):
            connection = docs[key]
            if not connection or connection['user_id'] != user_id:
                raise ConnectorError('connection_unavailable', 'This connection is unavailable.')
            connection.update(revoked=True, access_hash=None, refresh_hash=None)
            return {key: connection}, None
        self.store.run([key], revoke)

    async def revoke_token(self, token):
        record = await asyncio.to_thread(self.store.get, 'mcp_tokens/' + digest(token.token))
        if record:
            await asyncio.to_thread(self.disconnect, record['connection_id'], record['subject'])
