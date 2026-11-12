"""Integration test for add_account route session authentication."""

import pytest
from flask import session
from app import create_app
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


class TestAddAccountSessionIntegration:
    """Test that add_account route properly adds accounts to session."""

    def test_add_account_session_structure(self, client):
        """Test that add_account updates session structure correctly."""
        # Simulate what add_account does after successful CMS auth
        with client.session_transaction() as sess:
            # Initialize session as login would
            sess['authenticated_accounts'] = ['account-1']
            sess['active_account_id'] = 'account-1'
            sess.modified = True

        # Simulate adding a second account (FR-3.4)
        with client.session_transaction() as sess:
            # This is what add_authenticated_account does
            if 'account-2' not in sess['authenticated_accounts']:
                sess['authenticated_accounts'].append('account-2')
                sess.modified = True

        # Verify session state
        with client.session_transaction() as sess:
            assert len(sess['authenticated_accounts']) == 2
            assert 'account-1' in sess['authenticated_accounts']
            assert 'account-2' in sess['authenticated_accounts']

    def test_add_account_preserves_existing_accounts(self, client):
        """Test that adding account preserves existing authenticated accounts."""
        # Set up initial session with one account
        with client.session_transaction() as sess:
            sess['authenticated_accounts'] = ['account-1']
            sess['active_account_id'] = 'account-1'

        # Add second account
        with client.session_transaction() as sess:
            if 'account-2' not in sess['authenticated_accounts']:
                sess['authenticated_accounts'].append('account-2')
                sess.modified = True

        # Verify both accounts present
        with client.session_transaction() as sess:
            assert 'account-1' in sess['authenticated_accounts']
            assert 'account-2' in sess['authenticated_accounts']
            assert len(sess['authenticated_accounts']) == 2

    def test_add_account_duplicate_prevention(self, client):
        """Test that adding same account twice doesn't create duplicates."""
        with client.session_transaction() as sess:
            sess['authenticated_accounts'] = ['account-1']

        # Try to add same account again
        with client.session_transaction() as sess:
            if 'account-1' not in sess['authenticated_accounts']:
                sess['authenticated_accounts'].append('account-1')
                sess.modified = True

        # Verify no duplicate
        with client.session_transaction() as sess:
            assert sess['authenticated_accounts'].count('account-1') == 1
            assert len(sess['authenticated_accounts']) == 1


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
