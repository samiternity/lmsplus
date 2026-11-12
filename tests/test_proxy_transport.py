from unittest.mock import Mock

import pytest

from app import config
from app.utils.download_token import _get_encryption_key
from app.utils.proxy_manager import FirestoreProxyManager
from app.utils.proxy_transport import ProxyConfigurationError, macro_endpoints, proxy_request


KEY = bytes([42]) * 32


@pytest.fixture(autouse=True)
def isolate_proxy_config(monkeypatch):
    monkeypatch.setattr(config, "_config", None)
    monkeypatch.setattr(config, "_deployment_origins", {})
    monkeypatch.setenv("APP_ENV", "testing")
    for provider in ("WORKER", "SUPABASE", "NETLIFY", "VERCEL", "VAL_TOWN"):
        monkeypatch.delenv(f"LMS_{provider}_ORIGIN", raising=False)
        monkeypatch.delenv(f"LMS_{provider}_PROXY_KEY", raising=False)


def test_credentials_are_bound_to_configured_proxy_origin_and_path(monkeypatch):
    monkeypatch.setenv("LMS_SUPABASE_ORIGIN", "https://own-proxy.example.test")
    monkeypatch.setenv("LMS_SUPABASE_PROXY_KEY", KEY.hex())
    send = Mock()
    url = "https://own-proxy.example.test/functions/v1/proxy?macro=true"

    proxy_request(send, url, allow_redirects=True, headers={"Cookie": "fixture"})
    send.assert_called_once_with(
        url, allow_redirects=False, headers={"Cookie": "fixture", "X-LMS-Proxy-Key": KEY.hex()}
    )
    for invalid in (url.replace("own-proxy", "other"), url.replace("/proxy?", "/other?"),
                    url.replace("https://", "https://user@"), url.replace("https://", "http://")):
        with pytest.raises(ProxyConfigurationError):
            proxy_request(send, invalid)
    assert send.call_count == 1


def test_local_uses_only_explicit_contributor_worker(monkeypatch):
    monkeypatch.setenv("APP_ENV", "local")
    monkeypatch.setattr(config, "_deployment_origins", {"LMS_WORKER_ORIGIN": "https://official.example.test"})
    monkeypatch.setenv("LMS_WORKER_PROXY_KEY", KEY.hex())
    assert macro_endpoints(20, 25) == []
    monkeypatch.setenv("LMS_WORKER_ORIGIN", "http://127.0.0.1:8787")
    monkeypatch.setenv("LMS_SUPABASE_ORIGIN", "https://own-secondary.example.test")
    monkeypatch.setenv("LMS_SUPABASE_PROXY_KEY", KEY.hex())
    assert macro_endpoints(20, 25) == [("Cloudflare", "http://127.0.0.1:8787/?macro=true", 20)]


def test_firestore_pool_cannot_send_requests_to_unconfigured_workers(monkeypatch):
    monkeypatch.setenv("LMS_WORKER_ORIGIN", "https://own-proxy.example.test")
    monkeypatch.setenv("LMS_WORKER_PROXY_KEY", KEY.hex())
    manager = FirestoreProxyManager()
    monkeypatch.setattr(manager, "_get_state", lambda: {"workers": ["https://unknown.example.test/?url="]})
    assert manager.get_worker("fixture") == "https://own-proxy.example.test/?url="


@pytest.mark.parametrize("value", ["", "a" * 63, "z" * 64])
def test_file_key_has_no_fallback(monkeypatch, value):
    monkeypatch.setenv("DOWNLOAD_ENCRYPTION_KEY", value)
    with pytest.raises(ValueError, match="DOWNLOAD_ENCRYPTION_KEY"):
        _get_encryption_key()


def test_testing_provider_isolation_preserves_browser_worker(monkeypatch):
    from app.utils.proxy_transport import file_worker_origin, proxy_workers
    for provider in ('WORKER', 'SUPABASE'):
        monkeypatch.setenv(f'LMS_{provider}_ORIGIN', f'https://{provider.lower()}.example.test')
        monkeypatch.setenv(f'LMS_{provider}_PROXY_KEY', KEY.hex())
    monkeypatch.setenv('LMS_TEST_PROXY_PROVIDER', 'SUPABASE')
    assert list(proxy_workers()) == ['https://supabase.example.test/functions/v1/proxy?url=']
    assert macro_endpoints(20, 25) == [('Supabase', 'https://supabase.example.test/functions/v1/proxy?macro=true', 25)]
    assert file_worker_origin() == 'https://worker.example.test'
    monkeypatch.setenv('APP_ENV', 'production')
    monkeypatch.setattr(config, '_config', None)
    assert len(proxy_workers()) == 2
    assert len(macro_endpoints(20, 25)) == 2


def test_unavailable_testing_provider_fails_closed(monkeypatch):
    from app.utils.proxy_transport import proxy_workers
    monkeypatch.setenv('LMS_TEST_PROXY_PROVIDER', 'SUPABASE')
    with pytest.raises(ProxyConfigurationError):
        proxy_workers()


def test_transparent_provider_test_keeps_atomic_login_and_browser_worker(monkeypatch):
    from app.utils.proxy_transport import file_worker_origin, proxy_workers
    for provider in ('WORKER', 'SUPABASE', 'VERCEL'):
        monkeypatch.setenv(f'LMS_{provider}_ORIGIN', f'https://{provider.lower()}.example.test')
        monkeypatch.setenv(f'LMS_{provider}_PROXY_KEY', KEY.hex())
    monkeypatch.setenv('LMS_TEST_PROXY_PROVIDER', 'VERCEL')
    assert list(proxy_workers()) == ['https://vercel.example.test/api/proxy?url=']
    assert [x[0] for x in macro_endpoints(20, 25)] == ['Cloudflare', 'Supabase']
    assert file_worker_origin() == 'https://worker.example.test'
