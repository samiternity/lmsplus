"""Unit and integration tests for edge proxy auth failover and session drops."""

from unittest.mock import MagicMock, patch
import pytest
from app import create_app
from app.models.account import CMSAccount
from app.utils.enhanced_scraper import EnhancedCMSScraper


@pytest.fixture(autouse=True)
def configured_test_proxies(monkeypatch):
    monkeypatch.setenv("LMS_WORKER_ORIGIN", "https://worker.example.test")
    monkeypatch.setenv("LMS_WORKER_PROXY_KEY", bytes([42]).hex() * 32)
    monkeypatch.setenv("LMS_SUPABASE_ORIGIN", "https://secondary.example.test")
    monkeypatch.setenv("LMS_SUPABASE_PROXY_KEY", bytes([43]).hex() * 32)


@pytest.fixture
def app():
    """Create Flask test app."""
    app = create_app()
    app.config['TESTING'] = True
    app.config['SECRET_KEY'] = 'test-secret-key'
    app.config['WTF_CSRF_ENABLED'] = False
    return app


@pytest.fixture
def client(app):
    """Create Flask test client."""
    return app.test_client()


class TestScraperAtomicAuthFailover:
    """Test enhanced scraper atomic failover handling."""

    def test_real_bad_password_halts_failover(self):
        """Tier 1 reporting REAL_BAD_PASSWORD halts failover immediately."""
        account = CMSAccount(account_id='test', cms_username='02-000000-017', cms_password='pwd')
        scraper = EnhancedCMSScraper(account)

        mock_resp1 = MagicMock()
        mock_resp1.status_code = 401
        mock_resp1.json.return_value = {
            'success': False,
            'error_code': 'REAL_BAD_PASSWORD',
            'error': 'Invalid credentials'
        }

        with patch.object(scraper.session, 'post', return_value=mock_resp1) as mock_post:
            result = scraper.scrape_full_sync_atomic('02-000000-017', 'pwd')
            assert result == {'auth_failed': True, 'error': 'Invalid credentials'}
            # Halts immediately, Tier 2 is never called
            assert mock_post.call_count == 1

    def test_unverified_401_advances_to_tier2(self):
        """Tier 1 returning generic 401 advances to Tier 2 instead of halting."""
        account = CMSAccount(account_id='test', cms_username='02-000000-017', cms_password='pwd')
        scraper = EnhancedCMSScraper(account)

        mock_resp1 = MagicMock()
        mock_resp1.status_code = 401
        mock_resp1.json.return_value = {'error_code': 'UNKNOWN'}

        mock_resp2 = MagicMock()
        mock_resp2.status_code = 200
        mock_resp2.json.return_value = {
            'success': True,
            'html': {'profile': '<html>profile</html>'},
            'cookies': 'ASP.NET_SessionId=xyz'
        }

        with patch.object(scraper.session, 'post', side_effect=[mock_resp1, mock_resp2]) as mock_post:
            result = scraper.scrape_full_sync_atomic('02-000000-017', 'pwd')
            assert result.get('success') is True
            assert mock_post.call_count == 2

    def test_session_lost_advances_to_tier2(self):
        """Tier 1 returning 502 SESSION_LOST advances to Tier 2."""
        account = CMSAccount(account_id='test', cms_username='02-000000-017', cms_password='pwd')
        scraper = EnhancedCMSScraper(account)

        mock_resp1 = MagicMock()
        mock_resp1.status_code = 502
        mock_resp1.json.return_value = {
            'success': False,
            'error_code': 'SESSION_LOST',
            'error': 'Session dropped'
        }

        mock_resp2 = MagicMock()
        mock_resp2.status_code = 200
        mock_resp2.json.return_value = {
            'success': True,
            'html': {'profile': '<html>profile</html>'},
            'cookies': 'ASP.NET_SessionId=xyz'
        }

        with patch.object(scraper.session, 'post', side_effect=[mock_resp1, mock_resp2]) as mock_post:
            result = scraper.scrape_full_sync_atomic('02-000000-017', 'pwd')
            assert result.get('success') is True
            assert mock_post.call_count == 2

    def test_both_tiers_session_lost_returns_session_lost_not_auth_failed(self):
        """Both tiers returning SESSION_LOST returns session_lost dict, never auth_failed."""
        account = CMSAccount(account_id='test', cms_username='02-000000-017', cms_password='pwd')
        scraper = EnhancedCMSScraper(account)

        mock_resp1 = MagicMock()
        mock_resp1.status_code = 502
        mock_resp1.json.return_value = {'error_code': 'SESSION_LOST'}

        mock_resp2 = MagicMock()
        mock_resp2.status_code = 502
        mock_resp2.json.return_value = {'error_code': 'SESSION_LOST'}

        with patch.object(scraper.session, 'post', side_effect=[mock_resp1, mock_resp2]):
            result = scraper.scrape_full_sync_atomic('02-000000-017', 'pwd')
            assert result.get('session_lost') is True
            assert result.get('auth_failed') is not True


class TestAuthLoginRouteEdgeCases:
    """Test /auth/login route handling for session drops vs real auth failures."""

    @patch('app.routes.auth.verify_turnstile', return_value=True)
    @patch('app.models.account.User.get', return_value=None)
    @patch('app.models.account.User.get_total_count', return_value=10)
    @patch('app.services.cms_blacklist.get_blacklist_service')
    def test_login_route_session_lost_returns_502(self, mock_bl, mock_count, mock_user_get, mock_turnstile, client):
        """When atomic scraper returns session_lost, route returns HTTP 502."""
        mock_bl.return_value.check_access.return_value = (True, "")

        mock_bundle = {
            'session_lost': True,
            'error': 'University CMS dropped session.'
        }

        with patch('app.utils.enhanced_scraper.EnhancedCMSScraper.scrape_full_sync_atomic', return_value=mock_bundle):
            resp = client.post('/auth/login', data={
                'username': '02-000000-013',
                'password': 'validpassword'
            })
            assert resp.status_code == 502
            data = resp.get_json()
            assert data.get('is_session_lost') is True
            assert 'CMS connection dropped' in data.get('error', '')
            assert 'Invalid credentials' not in data.get('error', '')

    @patch('app.routes.auth.verify_turnstile', return_value=True)
    @patch('app.models.account.User.get', return_value=None)
    @patch('app.models.account.User.get_total_count', return_value=10)
    @patch('app.services.cms_blacklist.get_blacklist_service')
    def test_login_route_real_bad_password_returns_401(self, mock_bl, mock_count, mock_user_get, mock_turnstile, client):
        """When atomic scraper returns auth_failed, route returns HTTP 401."""
        mock_bl.return_value.check_access.return_value = (True, "")

        mock_bundle = {
            'auth_failed': True,
            'error': 'Invalid credentials'
        }

        with patch('app.utils.enhanced_scraper.EnhancedCMSScraper.scrape_full_sync_atomic', return_value=mock_bundle):
            resp = client.post('/auth/login', data={
                'username': '02-000000-013',
                'password': 'wrongpassword'
            })
            assert resp.status_code == 401
            data = resp.get_json()
            assert 'Invalid credentials' in data.get('error', '')

    @patch('app.routes.auth.verify_turnstile', return_value=True)
    @patch('app.models.account.User.get', return_value=None)
    @patch('app.models.account.User.get_total_count', return_value=10)
    @patch('app.services.cms_blacklist.get_blacklist_service')
    @patch('app.routes.auth.authenticate_with_cms', return_value='SESSION_LOST')
    def test_login_route_fallback_session_lost_returns_502(self, mock_auth_cms, mock_bl, mock_count, mock_user_get, mock_turnstile, client):
        """When atomic scraper is inconclusive and fallback returns SESSION_LOST, route returns HTTP 502."""
        mock_bl.return_value.check_access.return_value = (True, "")

        with patch('app.utils.enhanced_scraper.EnhancedCMSScraper.scrape_full_sync_atomic', return_value=None):
            resp = client.post('/auth/login', data={
                'username': '02-000000-013',
                'password': 'validpassword'
            })
            assert resp.status_code == 502
            data = resp.get_json()
            assert data.get('is_session_lost') is True
            assert 'CMS connection dropped' in data.get('error', '')

    @patch('app.routes.auth.verify_turnstile', return_value=True)
    @patch('app.models.account.User.get', return_value=None)
    @patch('app.models.account.User.create')
    @patch('app.models.account.User.get_total_count', return_value=10)
    @patch('app.services.cms_blacklist.get_blacklist_service')
    def test_login_route_safe_profile_extraction_when_html_is_none(self, mock_bl, mock_count, mock_user_create, mock_user_get, mock_turnstile, client):
        """When bundle has html as None or unexpected type, profile extraction does not crash."""
        mock_bl.return_value.check_access.return_value = (True, "")
        mock_user = MagicMock()
        mock_user.set_cms_account.return_value = 'mock-uuid'
        mock_user.save.return_value = True
        mock_user.is_authenticated = True
        mock_user_create.return_value = mock_user

        mock_bundle = {
            'success': True,
            'html': None
        }

        with patch('app.utils.enhanced_scraper.EnhancedCMSScraper.scrape_full_sync_atomic', return_value=mock_bundle):
            with patch('app.routes.auth.login_user'):
                resp = client.post('/auth/login', data={
                    'username': '02-000000-013',
                    'password': 'validpassword'
                })
                # Does not crash with 500 error
                assert resp.status_code != 500
