"""Route server requests through explicitly configured, authenticated proxies."""

import os
import re
from urllib.parse import urlsplit

from requests.structures import CaseInsensitiveDict

from app.config import get_config


# Provider name, URL path, and relative routing weight.
PROVIDERS = (
    ("WORKER", "/", 100),
    ("SUPABASE", "/functions/v1/proxy", 16),
    ("VERCEL", "/api/proxy", 16),
    ("NETLIFY", "/", 4),
    ("VAL_TOWN", "/", 3),
)
SERVICE_HEADER = "X-LMS-Proxy-Key"


class ProxyConfigurationError(RuntimeError):
    pass


def _origin(value):
    try:
        parsed = urlsplit(value)
        host = parsed.hostname
        port = parsed.port
    except ValueError:
        return None
    if not host or parsed.username is not None or parsed.password is not None:
        return None
    if parsed.scheme != "https" and not (
        get_config().is_local and parsed.scheme == "http" and host in ("localhost", "127.0.0.1", "::1")
    ):
        return None
    display_host = f"[{host}]" if ":" in host else host
    default_port = 443 if parsed.scheme == "https" else 80
    suffix = f":{port}" if port is not None and port != default_port else ""
    return f"{parsed.scheme}://{display_host}{suffix}"


def _configured():
    for provider, path, weight in PROVIDERS:
        if get_config().is_local and provider != "WORKER":
            continue
        value = os.environ.get(f"LMS_{provider}_ORIGIN", "").strip()
        origin = _origin(value)
        key = os.environ.get(f"LMS_{provider}_PROXY_KEY", "")
        if not origin or not re.fullmatch(r"[0-9a-fA-F]{64}", key):
            continue
        parsed = urlsplit(value)
        if parsed.path not in ("", "/") or parsed.query or parsed.fragment or parsed.hostname.endswith(".invalid"):
            continue
        yield provider, origin, path, weight, key


def _routing_configured():
    configured = list(_configured())
    selected = os.environ.get("LMS_TEST_PROXY_PROVIDER", "").strip() if get_config().is_testing else ""
    if selected:
        configured = [entry for entry in configured if entry[0] == selected]
        if not configured:
            raise ProxyConfigurationError("The selected testing provider is not configured.")
    return configured


def proxy_workers():
    return {f"{origin}{path}?url=": weight for _, origin, path, weight, _ in _routing_configured()}


def file_worker_origin():
    return next((origin for provider, origin, _, _, _ in _configured() if provider == "WORKER"), "")


def macro_endpoints(worker_timeout, supabase_timeout):
    names = {"WORKER": ("Cloudflare", worker_timeout), "SUPABASE": ("Supabase", supabase_timeout)}
    configured = _routing_configured()
    # Transparent-only provider tests must keep atomic login on macro-capable providers.
    if get_config().is_testing and os.environ.get("LMS_TEST_PROXY_PROVIDER", "").strip() in {"VERCEL", "NETLIFY", "VAL_TOWN"}:
        configured = list(_configured())
    return [
        (names[provider][0], f"{origin}{path}?macro=true", names[provider][1])
        for provider, origin, path, _, _ in configured if provider in names
    ]


def proxy_request(send, url, **kwargs):
    """Send the key per request so redirects and unrelated requests cannot inherit it."""
    origin = _origin(url)
    if not origin:
        raise ProxyConfigurationError("No authenticated proxy is configured for this destination.")
    parsed = urlsplit(url)
    for _, configured_origin, path, _, key in _configured():
        if origin == configured_origin and parsed.path == path:
            headers = CaseInsensitiveDict(kwargs.pop("headers", None) or {})
            headers[SERVICE_HEADER] = key
            kwargs["allow_redirects"] = False
            return send(url, headers=headers, **kwargs)
    raise ProxyConfigurationError("No authenticated proxy is configured for this destination.")
