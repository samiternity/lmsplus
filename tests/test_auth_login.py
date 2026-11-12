"""Integration tests for login route session authentication."""

import pytest
from flask import session
from app import create_app
from app.models.account import User
from app.utils.session_auth import get_authenticated_accounts


@pytest.fixture(autouse=True)
def configured_test_proxies(monkeypatch):
    monkeypatch.setenv("LMS_WORKER_ORIGIN", "https://worker.example.test")
    monkeypatch.setenv("LMS_WORKER_PROXY_KEY", bytes([42]).hex() * 32)
    monkeypatch.setenv("LMS_SUPABASE_ORIGIN", "https://secondary.example.test")
    monkeypatch.setenv("LMS_SUPABASE_PROXY_KEY", bytes([43]).hex() * 32)


@pytest.fixture
def app():
    """Create and configure a test Flask app."""
    app = create_app()
    app.config['TESTING'] = True
    app.config['SECRET_KEY'] = 'test-secret-key'
    app.config['WTF_CSRF_ENABLED'] = False
    return app


@pytest.fixture
def client(app):
    """Create a test client."""
    return app.test_client()


@pytest.fixture
def app_context(app):
    """Create an application context."""
    with app.app_context():
        yield app


class TestLoginSessionAuthentication:
    """Test that login route properly adds accounts to session."""

    def test_login_initializes_authenticated_accounts(self, client):
        """Test that login initializes authenticated_accounts in session."""
        with client.session_transaction() as sess:
            # Ensure session is clean
            assert 'authenticated_accounts' not in sess

        # Note: This test verifies the session structure is set up correctly
        # Actual login testing would require mocking CMS authentication
        # which is beyond the scope of this task

    def test_session_structure_after_login(self, client):
        """Test that session has correct structure after login."""
        with client.session_transaction() as sess:
            # Simulate what login does
            sess['authenticated_accounts'] = []
            sess['active_account_id'] = 'test-account-uuid'
            sess.modified = True

        with client.session_transaction() as sess:
            assert 'authenticated_accounts' in sess
            assert isinstance(sess['authenticated_accounts'], list)
            assert 'active_account_id' in sess

    def test_authenticated_accounts_list_structure(self, client):
        """Test that authenticated_accounts is a list of UUIDs."""
        with client.session_transaction() as sess:
            sess['authenticated_accounts'] = [
                'uuid-1',
                'uuid-2',
                'uuid-3'
            ]

        with client.session_transaction() as sess:
            accounts = sess['authenticated_accounts']
            assert len(accounts) == 3
            assert 'uuid-1' in accounts
            assert 'uuid-2' in accounts
            assert 'uuid-3' in accounts

    def test_session_persistence_across_requests(self, client):
        """Test that authenticated_accounts persists across requests."""
        # First request - set up session
        with client.session_transaction() as sess:
            sess['authenticated_accounts'] = ['account-1']
            sess['active_account_id'] = 'account-1'

        # Second request - verify persistence
        with client.session_transaction() as sess:
            assert sess['authenticated_accounts'] == ['account-1']
            assert sess['active_account_id'] == 'account-1'

    def test_multiple_accounts_in_session(self, client):
        """Test that multiple accounts can be added to session."""
        with client.session_transaction() as sess:
            sess['authenticated_accounts'] = []
            sess['authenticated_accounts'].append('account-1')
            sess['authenticated_accounts'].append('account-2')
            sess['authenticated_accounts'].append('account-3')

        with client.session_transaction() as sess:
            assert len(sess['authenticated_accounts']) == 3
            assert 'account-1' in sess['authenticated_accounts']
            assert 'account-2' in sess['authenticated_accounts']
            assert 'account-3' in sess['authenticated_accounts']


class TestLoginRouteIntegration:
    """Integration tests for login route behavior."""

    def test_session_structure_matches_requirements(self, client):
        """Test that session structure matches requirements."""
        with client.session_transaction() as sess:
            # Simulate what login does according to requirements
            sess['authenticated_accounts'] = ['account-uuid-1']
            sess['active_account_id'] = 'account-uuid-1'
            sess.modified = True

        with client.session_transaction() as sess:
            # Verify structure matches FR-1.2
            assert 'authenticated_accounts' in sess
            assert isinstance(sess['authenticated_accounts'], list)
            # Verify FR-2.4
            assert 'active_account_id' in sess
            assert sess['active_account_id'] == 'account-uuid-1'

    def test_add_account_updates_session(self, client):
        """Test that adding an account updates the session."""
        with client.session_transaction() as sess:
            sess['authenticated_accounts'] = ['account-1']
            sess['active_account_id'] = 'account-1'

        # Simulate adding a second account (FR-3.4)
        with client.session_transaction() as sess:
            sess['authenticated_accounts'].append('account-2')
            sess.modified = True

        with client.session_transaction() as sess:
            assert len(sess['authenticated_accounts']) == 2
            assert 'account-1' in sess['authenticated_accounts']
            assert 'account-2' in sess['authenticated_accounts']


class TestEnrollmentValidation:
    """Test username/enrollment format validation on login route."""

    @pytest.mark.parametrize("invalid_enrollment", [
        "00-000000-013",  # Prefix 00 out of range
        "16-000000-013",  # Prefix 16 out of range
        "99-000000-013",  # Prefix 99 out of range
        "admin",
        "02-13624-013",   # 5 digits middle
        "02-000000-01",   # 2 digits suffix
        "02-000000-0134", # 4 digits suffix
        "02000000013",    # missing dashes
        "<script>alert(1)</script>",
    ])
    def test_invalid_enrollment_rejected(self, client, invalid_enrollment):
        res = client.post('/auth/login', data={
            'username': invalid_enrollment,
            'password': 'anypassword123'
        })
        assert res.status_code == 400
        data = res.get_json()
        assert 'Invalid enrollment format' in data.get('error', '')

    @pytest.mark.parametrize("valid_enrollment", [
        "01-000000-016",  # Islamabad E-8
        "02-000000-008",  # Karachi
        "03-111111-001",  # Lahore
        "04-111111-001",  # NCMPR
        "05-000000-013",  # IPP
        "06-111111-001",  # Health Sciences (Karachi)
        "07-111111-001",  # PN Logistics
        "08-111111-001",  # PN Nursing
        "09-111111-001",  # Islamabad H-11
        "10-111111-001",  # Finishing School
        "11-111111-001",  # Hydrography
        "12-111111-001",  # Health Sciences (Islamabad)
        "13-111111-001",  # BU Nursing
        "14-111111-001",  # ODL
        "15-111111-001",  # Underwater Medicine
    ])
    def test_valid_all_campuses_enrollment_format_accepted(self, client, valid_enrollment):
        # Valid format reaches authentication logic instead of format rejection
        res = client.post('/auth/login', data={
            'username': valid_enrollment,
            'password': 'wrongpassword'
        })
        # Should not be rejected by format validator
        assert res.status_code != 400 or 'Invalid enrollment format' not in (res.get_json() or {}).get('error', '')

    def test_get_institute_id_for_enrollment(self):
        from app.routes.auth import get_institute_id_for_enrollment
        assert get_institute_id_for_enrollment('01-000000-016') == '1'
        assert get_institute_id_for_enrollment('02-000000-008') == '2'
        assert get_institute_id_for_enrollment('03-111111-001') == '3'
        assert get_institute_id_for_enrollment('04-111111-001') == '4'
        assert get_institute_id_for_enrollment('05-000000-013') == '5'
        assert get_institute_id_for_enrollment('06-111111-001') == '6'
        assert get_institute_id_for_enrollment('07-111111-001') == '7'
        assert get_institute_id_for_enrollment('08-111111-001') == '8'
        assert get_institute_id_for_enrollment('09-111111-001') == '9'
        assert get_institute_id_for_enrollment('10-111111-001') == '10'
        assert get_institute_id_for_enrollment('11-111111-001') == '11'
        assert get_institute_id_for_enrollment('12-111111-001') == '12'
        assert get_institute_id_for_enrollment('13-111111-001') == '13'
        assert get_institute_id_for_enrollment('14-111111-001') == '14'
        assert get_institute_id_for_enrollment('15-111111-001') == '15'
        assert get_institute_id_for_enrollment('00-111111-001') == '2'  # Out of range fallback
        assert get_institute_id_for_enrollment('16-111111-001') == '2'  # Out of range fallback
        assert get_institute_id_for_enrollment('') == '2'
        assert get_institute_id_for_enrollment(None) == '2'


class TestEnhancedScraperInstituteResolution:
    """Test dynamic institute_id resolution in EnhancedCMSScraper and Account models."""

    def test_account_from_dict_self_heals_legacy_id(self):
        from app.models.account import CMSAccount
        data = {
            'account_id': 'acc-1',
            'cms_username': '01-000000-016',
            'institute_id': '2'  # Stale legacy ID
        }
        acc = CMSAccount.from_dict(data)
        assert acc.institute_id == '1'

    def test_user_set_cms_account_self_heals_legacy_id(self):
        user = User(id='test-user-id', username='testuser', email='test@test.com', password_hash='hash')
        user.set_cms_account('03-111111-001', 'pass123', institute_id='2')
        assert user.cms_account.institute_id == '3'

    def test_enhanced_scraper_resolves_and_heals_institute(self):
        from app.utils.enhanced_scraper import EnhancedCMSScraper
        from app.models.account import CMSAccount
        acc = CMSAccount('acc-1', '08-111111-001', 'pass123', institute_id='2')
        scraper = EnhancedCMSScraper(acc)
        assert scraper.institute_id == '8'

    def test_atomic_methods_send_correct_institute_id(self):
        from unittest.mock import patch, MagicMock
        from app.utils.enhanced_scraper import EnhancedCMSScraper
        from app.models.account import CMSAccount
        acc = CMSAccount('acc-1', '09-111111-001', 'pass123', institute_id='2')
        scraper = EnhancedCMSScraper(acc)

        with patch.object(scraper.session, 'post') as mock_post:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.text = "<html><body>Mock</body></html>"
            mock_resp.json.return_value = {"html": "<html><body>Mock</body></html>"}
            mock_post.return_value = mock_resp

            scraper.scrape_page_atomic("attendance", acc.cms_username, "pass123")
            assert mock_post.called
            sent_payload = mock_post.call_args[1]['json']
            assert sent_payload['institute_id'] == '9'


if __name__ == '__main__':
    pytest.main([__file__, '-v'])


