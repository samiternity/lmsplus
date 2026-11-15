"""No-store responses and bounded request bodies on public connector endpoints."""

from base64 import b64decode
import json
import logging
import os
from contextvars import ContextVar
from ipaddress import IPv6Address, ip_address, ip_network
from urllib.parse import parse_qs, unquote, urlencode

from starlette.exceptions import HTTPException
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.datastructures import Headers

from lms_mcp.core import SCOPE


logger = logging.getLogger(__name__)
OAUTH_ERRORS = {'invalid_request', 'invalid_client', 'invalid_grant', 'invalid_scope',
                'unauthorized_client', 'unsupported_grant_type', 'invalid_target', 'server_error'}


registration_source = ContextVar('registration_source', default='unknown')
registration_redirects = ContextVar('registration_redirects', default=None)
request_redirect = ContextVar('request_redirect', default=None)


def _registration_source(scope):
    # Use the ASGI server's connection identity, never a client-supplied CDN header.
    client = scope.get('client')
    remote = client[0] if client else ''
    if os.environ.get('K_SERVICE'):
        # Cloud Run is the final ingress hop, including for Hosting rewrites.
        forwarded = Headers(scope=scope).getlist('x-forwarded-for')
        if forwarded:
            remote = forwarded[-1].rsplit(',', 1)[-1].strip()
    try:
        address = ip_address(remote)
    except ValueError:
        return 'unknown'
    if isinstance(address, IPv6Address):
        if address.ipv4_mapped:
            return str(address.ipv4_mapped)
        return str(ip_network((address, 64), strict=False))
    return str(address) if address else 'unknown'


async def _normalize_client_form(scope, body):
    """Supply fields required by the SDK's Basic auth and revocation parsers."""
    async def receive():
        return {'type': 'http.request', 'body': bytes(body), 'more_body': False}

    request = Request(scope, receive)
    additions = {}
    try:
        async with request.form() as form:
            if 'client_id' not in form:
                scheme, _, encoded = request.headers.get('authorization', '').partition(' ')
                if scheme.lower() == 'basic':
                    client_id, separator, _ = b64decode(encoded, validate=True).decode('utf-8').partition(':')
                    if separator:
                        additions['client_id'] = unquote(client_id)
            if scope['path'] == '/revoke' and 'client_secret' not in form:
                additions['client_secret'] = ''
            fields = form.multi_items()
            if not additions or any(not isinstance(value, str) for _, value in fields):
                return scope, body
    except (HTTPException, ValueError):
        return scope, body

    encoded_body = urlencode([*fields, *additions.items()]).encode()
    headers = [(key, value) for key, value in scope['headers'] if key not in {b'content-type', b'content-length'}]
    headers.extend([(b'content-type', b'application/x-www-form-urlencoded'),
                    (b'content-length', str(len(encoded_body)).encode())])
    return dict(scope, headers=headers), encoded_body


class ConnectorHTTPGuard:
    def __init__(self, app, origin):
        self.app = app
        self.challenge = f'Bearer resource_metadata="{origin}/.well-known/oauth-protected-resource/mcp", scope="{SCOPE}"'

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http' or not (scope['path'] in {'/mcp', '/authorize', '/token', '/register', '/revoke'}
                                          or scope['path'].startswith('/.well-known/oauth-')):
            return await self.app(scope, receive, send)
        if scope['path'] == '/authorize' and len(scope['query_string']) > 16384:
            return await JSONResponse({'error': 'request_too_large'}, status_code=414,
                                      headers={'Cache-Control': 'private, no-store'})(scope, receive, send)
        # Buffer only bounded OAuth/tool requests, never document responses.
        body = bytearray()
        if scope['method'] in {'POST', 'PUT', 'PATCH'}:
            while True:
                message = await receive()
                if message['type'] == 'http.disconnect':
                    return
                body.extend(message.get('body', b''))
                if len(body) > 16384:
                    return await JSONResponse({'error': 'request_too_large'}, status_code=413,
                                              headers={'Cache-Control': 'private, no-store'})(scope, receive, send)
                if not message.get('more_body', False):
                    break
            if scope['method'] == 'POST' and scope['path'] in {'/token', '/revoke'}:
                scope, body = await _normalize_client_form(scope, body)
            delivered = False
            original_receive = receive
            async def replay():
                nonlocal delivered
                if not delivered:
                    delivered = True
                    return {'type': 'http.request', 'body': bytes(body), 'more_body': False}
                return await original_receive()
            receive = replay

        # A first-time client needs an authorization challenge, not an expired-token error.
        # Requests with credentials still use the SDK's normal validation and error responses.
        if (scope['path'] == '/mcp' and scope['method'] in {'GET', 'POST', 'DELETE'}
                and not any(key == b'authorization' for key, _ in scope['headers'])):
            return await Response(status_code=401, headers={
                'WWW-Authenticate': self.challenge,
                'Cache-Control': 'private, no-store',
                'Referrer-Policy': 'no-referrer',
            })(scope, receive, send)

        token_error_status = None
        registration_headers = None

        async def private_send(message):
            nonlocal token_error_status, registration_headers
            if message['type'] == 'http.response.start':
                if scope['path'] == '/token' and scope['method'] == 'POST' and message['status'] >= 400:
                    token_error_status = message['status']
                headers = [(key, value) for key, value in message.get('headers', [])
                           if key.lower() not in {b'cache-control', b'referrer-policy'}]
                headers.extend([(b'cache-control', b'private, no-store'), (b'referrer-policy', b'no-referrer')])
                message = dict(message, headers=headers)
                # The SDK canonicalizes URL fields. Echo the exact registered strings.
                if scope['path'] == '/register' and message['status'] == 201 and registration_redirects.get():
                    registration_headers = message
                    return
            elif registration_headers is not None:
                response = json.loads(message['body'])
                response['redirect_uris'] = registration_redirects.get()
                encoded = json.dumps(response, separators=(',', ':')).encode()
                headers = [(key, value) for key, value in registration_headers['headers']
                           if key != b'content-length']
                headers.append((b'content-length', str(len(encoded)).encode()))
                await send(dict(registration_headers, headers=headers))
                message = dict(message, body=encoded)
            elif token_error_status and message['type'] == 'http.response.body' and not message.get('more_body'):
                try:
                    response = json.loads(message.get('body', b''))
                    error = response.get('error') if isinstance(response, dict) else None
                except (ValueError, UnicodeError):
                    error = None
                grant = parse_qs(body.decode('ascii', errors='replace')).get('grant_type', [''])[0]
                logger.warning('MCP token request failed: status=%s grant=%s error=%s', token_error_status,
                               grant if grant in ('authorization_code', 'refresh_token') else 'other',
                               error if isinstance(error, str) and error in OAUTH_ERRORS else 'unknown')
            await send(message)
        path = scope['path']
        if path == '/register' and scope['method'] == 'POST':
            try:
                payload = json.loads(body)
            except (ValueError, UnicodeError):
                payload = None
            redirects = payload.get('redirect_uris') if isinstance(payload, dict) else None
            source = registration_source.set(_registration_source(scope))
            raw = registration_redirects.set(redirects)
            try:
                await self.app(scope, receive, private_send)
            finally:
                registration_redirects.reset(raw)
                registration_source.reset(source)
        elif path in {'/authorize', '/token'}:
            async def read_body():
                return {'type': 'http.request', 'body': bytes(body), 'more_body': False}

            request = Request(scope, read_body)
            if scope['method'] == 'GET':
                redirects = request.query_params.getlist('redirect_uri')
            else:
                try:
                    async with request.form() as form:
                        redirects = form.getlist('redirect_uri')
                except (HTTPException, ValueError):
                    redirects = []
            raw = request_redirect.set(redirects or None)
            try:
                await self.app(scope, receive, private_send)
            finally:
                request_redirect.reset(raw)
        else:
            await self.app(scope, receive, private_send)
