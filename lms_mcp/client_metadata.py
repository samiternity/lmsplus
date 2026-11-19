"""OAuth redirect validation and public client metadata discovery."""

import asyncio
import ipaddress
import json
import re
import socket
from urllib.parse import urlsplit

import httpx
from mcp.shared.auth import InvalidRedirectUriError, OAuthClientInformationFull
from pydantic import Field

from lms_mcp.core import SCOPE
from lms_mcp.http_security import request_redirect


TOKEN_AUTH_METHODS = ('none', 'client_secret_basic', 'client_secret_post')
MAX_METADATA_BYTES = 64 * 1024
GLOBAL_UNICAST_IPV6 = ipaddress.IPv6Network('2000::/3')
DNS_LABEL = re.compile(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', re.I | re.ASCII)
URI_CHARACTERS = re.compile(r"[A-Za-z0-9._~:/?\[\]@!$&'()+,;=%-]+")


class RegisteredClient(OAuthClientInformationFull):
    exact_redirect_uris: list[str] = Field(default_factory=list, exclude=True)

    def validate_redirect_uri(self, redirect_uri):
        raw = request_redirect.get()
        registered = self.exact_redirect_uris or [str(uri) for uri in self.redirect_uris or []]
        if raw and (len(raw) != 1 or raw[0] not in registered):
            raise InvalidRedirectUriError('The callback does not match this client registration.')
        return super().validate_redirect_uri(redirect_uri)


def valid_callback(url):
    if not URI_CHARACTERS.fullmatch(url) or re.search(r'%(?![0-9a-fA-F]{2})', url):
        return False
    try:
        parsed = urlsplit(url)
        host, port = parsed.hostname, parsed.port
        if not host or parsed.username is not None or port == 0:
            return False
        if not re.fullmatch(r'(?:\[[^\]]+\]|[^:\[\]]+)(?::[0-9]+)?', parsed.netloc):
            return False
        if parsed.scheme == 'http':
            return (port is not None and not parsed.query
                    and parsed.netloc in {f'{loopback}:{port}' for loopback in ('localhost', '127.0.0.1', '[::1]')})
        if parsed.scheme != 'https':
            return False
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            return all(DNS_LABEL.fullmatch(label) for label in host.rstrip('.').split('.'))
        return '%' not in host and (address.version == 4 or parsed.netloc.startswith('['))
    except ValueError:
        return False


async def fetch_client_metadata(client_id):
    if not valid_callback(client_id):
        return None
    try:
        url = httpx.URL(client_id)
        if url.scheme != 'https' or not urlsplit(client_id).path:
            return None
        async with asyncio.timeout(5):
            results = await asyncio.get_running_loop().getaddrinfo(
                url.host, url.port or 443, type=socket.SOCK_STREAM)
            addresses = [ipaddress.ip_address(result[4][0]) for result in results]
            if not addresses or any(
                not address.is_global or address.is_multicast or address.is_reserved
                or (address.version == 6 and
                    (address not in GLOBAL_UNICAST_IPV6 or address.sixtofour or address.teredo))
                for address in addresses
            ):
                return None
            # Connect by IP, but send and verify the original hostname for TLS.
            pinned_url = url.copy_with(host=str(addresses[0]))
            async with httpx.AsyncClient(trust_env=False, follow_redirects=False) as http:
                async with http.stream(
                    'GET', pinned_url,
                    headers={'Host': url.netloc.decode('ascii'), 'Accept': 'application/json',
                             'Accept-Encoding': 'identity'},
                    extensions={'sni_hostname': url.host},
                ) as response:
                    if response.status_code != 200 or response.headers.get('content-encoding', 'identity') != 'identity':
                        return None
                    body = bytearray()
                    async for chunk in response.aiter_raw(chunk_size=8192):
                        body.extend(chunk)
                        if len(body) > MAX_METADATA_BYTES:
                            return None
            document = json.loads(body)
            if not isinstance(document, dict) or document.get('client_id') != client_id:
                return None
            if not isinstance(document.get('client_name'), str) or not document['client_name'].strip():
                return None
            redirects = document.get('redirect_uris')
            if not isinstance(redirects, list) or not redirects or not all(
                isinstance(uri, str) and valid_callback(uri) for uri in redirects
            ):
                return None
            # CIMD clients can publish multiple methods (SEP-3149). The legacy
            # singular value is a preference, not a requirement when the plural
            # field is present. This server supports public CIMD clients with
            # PKCE; never silently downgrade a client that only supports JWT.
            methods = document.get('token_endpoint_auth_methods_supported',
                                   [document.get('token_endpoint_auth_method', 'none')])
            if (not isinstance(methods, list) or not methods
                    or not all(isinstance(method, str) and method for method in methods)
                    or 'none' not in methods):
                return None
            scope = document.get('scope', SCOPE)
            if not isinstance(scope, str) or set(scope.split()) != {SCOPE}:
                return None
            grants = document.get('grant_types', ['authorization_code'])
            responses = document.get('response_types', ['code'])
            if (not isinstance(grants, list) or not all(isinstance(grant, str) for grant in grants)
                    or 'authorization_code' not in grants or responses != ['code']):
                return None
            return RegisteredClient(
                client_id=client_id, client_name=document['client_name'], redirect_uris=redirects, exact_redirect_uris=redirects,
                token_endpoint_auth_method='none',
                grant_types=[grant for grant in ('authorization_code', 'refresh_token') if grant in grants],
                response_types=responses, scope=SCOPE,
            )
    except (OSError, TimeoutError, ValueError, RecursionError, httpx.HTTPError, httpx.InvalidURL):
        return None
