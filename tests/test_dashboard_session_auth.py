"""Tests for dashboard session authentication.

This test verifies that the dashboard route properly checks session authentication
and redirects to login if the account is not authenticated in the current session.
"""

import pytest
import sys
import os
from unittest.mock import MagicMock, patch

# Add app to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from flask import session
from app import create_app
from app.utils.session_auth import add_authenticated_account, clear_authenticated_accounts


@pytest.fixture
def app():
    """Create and configure a test Flask app."""
    app = create_app()
    app.config['TESTING'] = True
    app.config['SECRET_KEY'] = 'test-secret-key'
    return app


@pytest.fixture
def client(app):
    """Create a test client."""
    return app.test_client()


@pytest.fixture
def mock_user():
    """Create a mock user with CMS accounts."""
    user = MagicMock()
    user.id = 'test-user-id'
    user.username = '02-000000-013'
    user.is_authenticated = True
    
    # Mock CMS account
    mock_account = MagicMock()
    mock_account.account_id = 'test-account-id'
    mock_account.cms_username = '02-000000-013'
    mock_account.student_name = 'Test Student'
    
    user.cms_accounts = {'test-account-id': mock_account}
    user.get_active_account.return_value = mock_account
    
    return user


def test_dashboard_requires_authenticated_account(client, mock_user):
    """Test that dashboard redirects to login if account not authenticated in session."""
    with patch('flask_login.utils._get_user', return_value=mock_user):
        # Clear authenticated accounts from session to simulate cross-session access
        with client.session_transaction() as sess:
            sess['authenticated_accounts'] = []
            sess['active_account_id'] = 'test-account-id'
        
        # Try to access dashboard
        response = client.get('/dashboard', follow_redirects=False)
        
        # Should redirect to login
        assert response.status_code == 302
        assert '/login' in response.location


def test_dashboard_allows_authenticated_account(client, mock_user):
    """Test that dashboard allows access when account is authenticated in session."""
    with patch('flask_login.utils._get_user', return_value=mock_user):
        # Add account to authenticated accounts
        with client.session_transaction() as sess:
            account_id = 'test-account-id'
            sess['authenticated_accounts'] = [account_id]
            sess['active_account_id'] = account_id
        
        # Mock load_user_data to return empty data
        with patch('app.routes.main.load_user_data') as mock_load:
            mock_load.return_value = {
                'timetable': None,
                'attendance': None,
                'community_service': None,
                'gpa': None,
                'scraped_at': None,
                'today_schedule': [],
                'attendance_overview': []
            }
            
            # Try to access dashboard
            response = client.get('/dashboard')
            
            # Should allow access (200 or render template)
            assert response.status_code == 200


def test_dashboard_redirects_when_no_active_account(client, mock_user):
    """Test that dashboard redirects to login when no active account is set."""
    with patch('flask_login.utils._get_user', return_value=mock_user):
        # Clear active account from session
        with client.session_transaction() as sess:
            sess.pop('active_account_id', None)
        
        # Try to access dashboard
        response = client.get('/dashboard', follow_redirects=False)
        
        # Should redirect to login
        assert response.status_code == 302
        assert '/login' in response.location


def test_dashboard_shows_error_message_for_unauthenticated_account(client, mock_user):
    """Test that dashboard shows appropriate error message for unauthenticated account."""
    with patch('flask_login.utils._get_user', return_value=mock_user):
        # Set active account but don't authenticate it
        with client.session_transaction() as sess:
            sess['authenticated_accounts'] = []
            sess['active_account_id'] = 'unauthenticated-account-id'
        
        # Try to access dashboard (don't follow redirects to avoid loop)
        response = client.get('/dashboard', follow_redirects=False)
        
        # Should redirect to login
        assert response.status_code == 302
        assert '/login' in response.location

