"""
Unit tests for session authentication helper functions.

Tests the session-based account isolation functionality that prevents
cross-session account access.

**Validates: Requirements 2.1, 2.2, 2.3, 2.4, 2.5, 2.6, 2.7, 2.8**
"""

import pytest
import sys
import os

# Add app to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from flask import session
from app import create_app
from app.utils.session_auth import (
    init_authenticated_accounts,
    add_authenticated_account,
    remove_authenticated_account,
    is_account_authenticated,
    get_authenticated_accounts,
    clear_authenticated_accounts
)


@pytest.fixture
def app():
    """Create and configure a test Flask application."""
    app = create_app()
    app.config['TESTING'] = True
    app.config['SECRET_KEY'] = 'test-secret-key'
    return app


@pytest.fixture
def client(app):
    """Create a test client for the Flask application."""
    return app.test_client()


@pytest.fixture
def app_context(app):
    """Create an application context for testing."""
    with app.app_context():
        yield app


class TestInitAuthenticatedAccounts:
    """Test suite for init_authenticated_accounts function."""
    
    def test_init_authenticated_accounts(self, app):
        """Test initializing authenticated accounts list."""
        with app.test_request_context():
            # Initially, authenticated_accounts should not exist
            assert 'authenticated_accounts' not in session
            
            # Initialize
            init_authenticated_accounts()
            
            # Now it should exist as an empty list
            assert 'authenticated_accounts' in session
            assert session['authenticated_accounts'] == []
            assert isinstance(session['authenticated_accounts'], list)
    
    def test_init_authenticated_accounts_idempotent(self, app):
        """Test that init_authenticated_accounts is idempotent."""
        with app.test_request_context():
            # Initialize twice
            init_authenticated_accounts()
            init_authenticated_accounts()
            
            # Should still be an empty list
            assert session['authenticated_accounts'] == []
    
    def test_init_authenticated_accounts_preserves_existing(self, app):
        """Test that init_authenticated_accounts preserves existing data."""
        with app.test_request_context():
            # Manually set some accounts
            session['authenticated_accounts'] = ['uuid-1', 'uuid-2']
            
            # Initialize (should not overwrite)
            init_authenticated_accounts()
            
            # Should preserve existing data
            assert session['authenticated_accounts'] == ['uuid-1', 'uuid-2']


class TestAddAuthenticatedAccount:
    """Test suite for add_authenticated_account function."""
    
    def test_add_authenticated_account(self, app):
        """Test adding account to session."""
        with app.test_request_context():
            account_id = 'uuid-test-1'
            
            # Add account
            add_authenticated_account(account_id)
            
            # Verify it was added
            assert account_id in session['authenticated_accounts']
            assert len(session['authenticated_accounts']) == 1
    
    def test_add_authenticated_account_multiple(self, app):
        """Test adding multiple accounts to session."""
        with app.test_request_context():
            account_ids = ['uuid-1', 'uuid-2', 'uuid-3']
            
            # Add multiple accounts
            for account_id in account_ids:
                add_authenticated_account(account_id)
            
            # Verify all were added
            assert len(session['authenticated_accounts']) == 3
            for account_id in account_ids:
                assert account_id in session['authenticated_accounts']
    
    def test_add_authenticated_account_duplicate_prevention(self, app):
        """Test adding account to session (including duplicate prevention)."""
        with app.test_request_context():
            account_id = 'uuid-duplicate'
            
            # Add account twice
            add_authenticated_account(account_id)
            add_authenticated_account(account_id)
            
            # Should only appear once
            assert session['authenticated_accounts'].count(account_id) == 1
            assert len(session['authenticated_accounts']) == 1
    
    def test_add_authenticated_account_initializes_session(self, app):
        """Test that add_authenticated_account initializes session if needed."""
        with app.test_request_context():
            # Don't initialize manually
            assert 'authenticated_accounts' not in session
            
            # Add account (should initialize automatically)
            add_authenticated_account('uuid-auto-init')
            
            # Should have initialized and added
            assert 'authenticated_accounts' in session
            assert 'uuid-auto-init' in session['authenticated_accounts']


class TestRemoveAuthenticatedAccount:
    """Test suite for remove_authenticated_account function."""
    
    def test_remove_authenticated_account(self, app):
        """Test removing account from session."""
        with app.test_request_context():
            account_id = 'uuid-to-remove'
            
            # Add account first
            add_authenticated_account(account_id)
            assert account_id in session['authenticated_accounts']
            
            # Remove account
            remove_authenticated_account(account_id)
            
            # Verify it was removed
            assert account_id not in session['authenticated_accounts']
            assert len(session['authenticated_accounts']) == 0
    
    def test_remove_authenticated_account_from_multiple(self, app):
        """Test removing one account while keeping others."""
        with app.test_request_context():
            # Add multiple accounts
            add_authenticated_account('uuid-1')
            add_authenticated_account('uuid-2')
            add_authenticated_account('uuid-3')
            
            # Remove one
            remove_authenticated_account('uuid-2')
            
            # Verify correct account was removed
            assert 'uuid-1' in session['authenticated_accounts']
            assert 'uuid-2' not in session['authenticated_accounts']
            assert 'uuid-3' in session['authenticated_accounts']
            assert len(session['authenticated_accounts']) == 2
    
    def test_remove_authenticated_account_nonexistent(self, app):
        """Test removing account that doesn't exist (should not error)."""
        with app.test_request_context():
            # Add one account
            add_authenticated_account('uuid-exists')
            
            # Try to remove non-existent account (should not raise error)
            remove_authenticated_account('uuid-does-not-exist')
            
            # Original account should still be there
            assert 'uuid-exists' in session['authenticated_accounts']
            assert len(session['authenticated_accounts']) == 1
    
    def test_remove_authenticated_account_from_empty(self, app):
        """Test removing account from empty list (should not error)."""
        with app.test_request_context():
            init_authenticated_accounts()
            
            # Try to remove from empty list (should not raise error)
            remove_authenticated_account('uuid-any')
            
            # Should still be empty
            assert len(session['authenticated_accounts']) == 0


class TestIsAccountAuthenticated:
    """Test suite for is_account_authenticated function."""
    
    def test_is_account_authenticated(self, app):
        """Test checking if account is authenticated."""
        with app.test_request_context():
            account_id = 'uuid-check'
            
            # Initially not authenticated
            assert is_account_authenticated(account_id) is False
            
            # Add account
            add_authenticated_account(account_id)
            
            # Now should be authenticated
            assert is_account_authenticated(account_id) is True
    
    def test_is_account_authenticated_multiple(self, app):
        """Test checking multiple accounts."""
        with app.test_request_context():
            # Add some accounts
            add_authenticated_account('uuid-1')
            add_authenticated_account('uuid-2')
            
            # Check authenticated accounts
            assert is_account_authenticated('uuid-1') is True
            assert is_account_authenticated('uuid-2') is True
            
            # Check non-authenticated account
            assert is_account_authenticated('uuid-3') is False
    
    def test_is_account_authenticated_after_removal(self, app):
        """Test checking account after it's been removed."""
        with app.test_request_context():
            account_id = 'uuid-temp'
            
            # Add and verify
            add_authenticated_account(account_id)
            assert is_account_authenticated(account_id) is True
            
            # Remove and verify
            remove_authenticated_account(account_id)
            assert is_account_authenticated(account_id) is False
    
    def test_is_account_authenticated_empty_session(self, app):
        """Test checking account with empty session."""
        with app.test_request_context():
            # Don't initialize session
            assert 'authenticated_accounts' not in session
            
            # Should return False (and initialize session)
            assert is_account_authenticated('uuid-any') is False
            
            # Should have initialized session
            assert 'authenticated_accounts' in session


class TestGetAuthenticatedAccounts:
    """Test suite for get_authenticated_accounts function."""
    
    def test_get_authenticated_accounts(self, app):
        """Test getting all authenticated accounts."""
        with app.test_request_context():
            # Initially empty
            accounts = get_authenticated_accounts()
            assert accounts == []
            assert isinstance(accounts, list)
    
    def test_get_authenticated_accounts_with_data(self, app):
        """Test getting authenticated accounts with data."""
        with app.test_request_context():
            # Add accounts
            add_authenticated_account('uuid-1')
            add_authenticated_account('uuid-2')
            add_authenticated_account('uuid-3')
            
            # Get accounts
            accounts = get_authenticated_accounts()
            
            # Verify
            assert len(accounts) == 3
            assert 'uuid-1' in accounts
            assert 'uuid-2' in accounts
            assert 'uuid-3' in accounts
    
    def test_get_authenticated_accounts_returns_copy(self, app):
        """Test that get_authenticated_accounts returns the actual list."""
        with app.test_request_context():
            # Add accounts
            add_authenticated_account('uuid-1')
            add_authenticated_account('uuid-2')
            
            # Get accounts
            accounts = get_authenticated_accounts()
            
            # Verify it's the same list from session
            assert accounts == session['authenticated_accounts']
    
    def test_get_authenticated_accounts_initializes_session(self, app):
        """Test that get_authenticated_accounts initializes session if needed."""
        with app.test_request_context():
            # Don't initialize manually
            assert 'authenticated_accounts' not in session
            
            # Get accounts (should initialize)
            accounts = get_authenticated_accounts()
            
            # Should have initialized
            assert 'authenticated_accounts' in session
            assert accounts == []


class TestClearAuthenticatedAccounts:
    """Test suite for clear_authenticated_accounts function."""
    
    def test_clear_authenticated_accounts(self, app):
        """Test clearing all authenticated accounts."""
        with app.test_request_context():
            # Add accounts
            add_authenticated_account('uuid-1')
            add_authenticated_account('uuid-2')
            add_authenticated_account('uuid-3')
            
            # Verify they exist
            assert len(session['authenticated_accounts']) == 3
            
            # Clear
            clear_authenticated_accounts()
            
            # Verify cleared
            assert session['authenticated_accounts'] == []
            assert len(session['authenticated_accounts']) == 0
    
    def test_clear_authenticated_accounts_empty(self, app):
        """Test clearing already empty list."""
        with app.test_request_context():
            init_authenticated_accounts()
            
            # Clear empty list (should not error)
            clear_authenticated_accounts()
            
            # Should still be empty
            assert session['authenticated_accounts'] == []
    
    def test_clear_authenticated_accounts_multiple_times(self, app):
        """Test clearing multiple times."""
        with app.test_request_context():
            # Add accounts
            add_authenticated_account('uuid-1')
            
            # Clear multiple times
            clear_authenticated_accounts()
            clear_authenticated_accounts()
            clear_authenticated_accounts()
            
            # Should still be empty
            assert session['authenticated_accounts'] == []


class TestSessionIsolation:
    """Integration tests for session isolation."""
    
    def test_session_isolation_between_contexts(self, app):
        """Test that different request contexts have isolated sessions."""
        # Context 1: Add accounts
        with app.test_request_context():
            add_authenticated_account('uuid-context-1')
            accounts_1 = get_authenticated_accounts()
            assert 'uuid-context-1' in accounts_1
        
        # Context 2: Should be separate session
        with app.test_request_context():
            accounts_2 = get_authenticated_accounts()
            # New context should have empty session
            assert accounts_2 == []
            assert 'uuid-context-1' not in accounts_2
    
    def test_session_persistence_within_context(self, app):
        """Test that session persists within the same context."""
        with app.test_request_context():
            # Add accounts
            add_authenticated_account('uuid-1')
            add_authenticated_account('uuid-2')
            
            # Get accounts multiple times
            accounts_1 = get_authenticated_accounts()
            accounts_2 = get_authenticated_accounts()
            
            # Should be the same
            assert accounts_1 == accounts_2
            assert len(accounts_1) == 2


class TestEdgeCases:
    """Test edge cases and error conditions."""
    
    def test_empty_string_account_id(self, app):
        """Test handling empty string as account ID."""
        with app.test_request_context():
            # Add empty string (should work but not recommended)
            add_authenticated_account('')
            
            # Should be in session
            assert '' in session['authenticated_accounts']
            assert is_account_authenticated('') is True
    
    def test_none_account_id(self, app):
        """Test handling None as account ID."""
        with app.test_request_context():
            # Add None (should work but not recommended)
            add_authenticated_account(None)
            
            # Should be in session
            assert None in session['authenticated_accounts']
            assert is_account_authenticated(None) is True
    
    def test_special_characters_in_account_id(self, app):
        """Test handling special characters in account ID."""
        with app.test_request_context():
            special_ids = [
                'uuid-with-dashes',
                'uuid_with_underscores',
                'uuid.with.dots',
                'uuid@with@at',
                'uuid#with#hash',
            ]
            
            for account_id in special_ids:
                add_authenticated_account(account_id)
                assert is_account_authenticated(account_id) is True
    
    def test_very_long_account_id(self, app):
        """Test handling very long account ID."""
        with app.test_request_context():
            long_id = 'uuid-' + 'a' * 1000
            
            add_authenticated_account(long_id)
            assert is_account_authenticated(long_id) is True


if __name__ == '__main__':
    # Run tests with pytest
    pytest.main([__file__, '-v', '--tb=short'])
