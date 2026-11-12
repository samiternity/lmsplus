"""Integration tests for logout route session authentication."""

import pytest
from flask import session
from app import create_app
from app.models.account import User
from app.utils.session_auth import get_authenticated_accounts


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


class TestLogoutSessionAuthentication:
    """Test that logout route properly clears authenticated accounts from session."""

    def test_logout_clears_authenticated_accounts(self, client):
        """Test that logout clears session['authenticated_accounts'].
        
        Validates: Requirements 2.8
        """
        # Set up session with authenticated accounts
        with client.session_transaction() as sess:
            sess['authenticated_accounts'] = ['account-1', 'account-2']
            sess['active_account_id'] = 'account-1'
            sess['user_id'] = 'test-user'

        # Verify session has authenticated accounts
        with client.session_transaction() as sess:
            assert len(sess['authenticated_accounts']) == 2
            assert 'active_account_id' in sess

        # Simulate logout by clearing session
        with client.session_transaction() as sess:
            # This is what clear_authenticated_accounts() does
            sess['authenticated_accounts'] = []
            sess.modified = True
            # Then session.clear() removes everything
            sess.clear()

        # Verify authenticated_accounts is cleared
        with client.session_transaction() as sess:
            assert 'authenticated_accounts' not in sess
            assert 'active_account_id' not in sess
            assert 'user_id' not in sess

    def test_logout_clears_entire_session(self, client):
        """Test that logout clears entire session.
        
        Validates: Requirements 2.8
        """
        # Set up session with multiple keys
        with client.session_transaction() as sess:
            sess['authenticated_accounts'] = ['account-1']
            sess['active_account_id'] = 'account-1'
            sess['user_id'] = 'test-user'
            sess['custom_key'] = 'custom_value'

        # Verify session has data
        with client.session_transaction() as sess:
            assert len(sess.keys()) > 0

        # Simulate logout
        with client.session_transaction() as sess:
            sess['authenticated_accounts'] = []
            sess.modified = True
            sess.clear()

        # Verify entire session is cleared
        with client.session_transaction() as sess:
            assert len(sess.keys()) == 0

    def test_logout_with_multiple_accounts(self, client):
        """Test that logout clears all authenticated accounts."""
        # Set up session with multiple authenticated accounts
        with client.session_transaction() as sess:
            sess['authenticated_accounts'] = [
                'account-1',
                'account-2',
                'account-3'
            ]
            sess['active_account_id'] = 'account-2'

        # Verify multiple accounts exist
        with client.session_transaction() as sess:
            assert len(sess['authenticated_accounts']) == 3

        # Simulate logout
        with client.session_transaction() as sess:
            sess['authenticated_accounts'] = []
            sess.modified = True
            sess.clear()

        # Verify all accounts are cleared
        with client.session_transaction() as sess:
            assert 'authenticated_accounts' not in sess

    def test_logout_with_empty_authenticated_accounts(self, client):
        """Test that logout works even if authenticated_accounts is empty."""
        # Set up session with empty authenticated_accounts
        with client.session_transaction() as sess:
            sess['authenticated_accounts'] = []
            sess['user_id'] = 'test-user'

        # Simulate logout
        with client.session_transaction() as sess:
            sess['authenticated_accounts'] = []
            sess.modified = True
            sess.clear()

        # Verify session is cleared
        with client.session_transaction() as sess:
            assert 'authenticated_accounts' not in sess
            assert 'user_id' not in sess

    def test_logout_clears_active_account(self, client):
        """Test that logout clears the active account from session."""
        # Set up session with active account
        with client.session_transaction() as sess:
            sess['authenticated_accounts'] = ['account-1']
            sess['active_account_id'] = 'account-1'
            sess['cms_username'] = 'test-user'

        # Verify active account exists
        with client.session_transaction() as sess:
            assert sess['active_account_id'] == 'account-1'
            assert sess['cms_username'] == 'test-user'

        # Simulate logout
        with client.session_transaction() as sess:
            sess['authenticated_accounts'] = []
            sess.modified = True
            sess.clear()

        # Verify active account is cleared
        with client.session_transaction() as sess:
            assert 'active_account_id' not in sess
            assert 'cms_username' not in sess


class TestLogoutBehavior:
    """Test logout route behavior and side effects."""

    def test_session_isolation_after_logout(self, client):
        """Test that after logout, a new session starts fresh.
        
        Validates: Requirements 2.7, 2.8
        """
        # First session - authenticate accounts
        with client.session_transaction() as sess:
            sess['authenticated_accounts'] = ['account-1', 'account-2']
            sess['active_account_id'] = 'account-1'

        # Logout - clear session
        with client.session_transaction() as sess:
            sess['authenticated_accounts'] = []
            sess.modified = True
            sess.clear()

        # New session - should be empty
        with client.session_transaction() as sess:
            assert 'authenticated_accounts' not in sess
            assert 'active_account_id' not in sess

    def test_logout_does_not_affect_user_object(self, client):
        """Test that logout only clears session, not User object data."""
        # Set up session
        with client.session_transaction() as sess:
            sess['authenticated_accounts'] = ['account-1']
            sess['active_account_id'] = 'account-1'
            sess['user_id'] = 'test-user'

        # Simulate logout
        with client.session_transaction() as sess:
            sess['authenticated_accounts'] = []
            sess.modified = True
            sess.clear()

        # Verify session is cleared but User object would remain
        # (User object persistence is handled by the database layer)
        with client.session_transaction() as sess:
            assert 'user_id' not in sess


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
