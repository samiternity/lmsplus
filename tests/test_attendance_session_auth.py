"""
Integration tests for attendance routes with session authentication.

Tests that attendance routes properly check session authentication and
return 403 Forbidden when accessing unauthenticated accounts.

**Validates: Requirements FR-5.1, FR-5.2, FR-5.3, AC-3.1, AC-3.2**
"""

import pytest
import sys
import os
import json
from unittest.mock import Mock, patch, MagicMock

# Add app to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from flask import session
from app import create_app
from app.utils.session_auth import add_authenticated_account, clear_authenticated_accounts


@pytest.fixture
def app():
    """Create and configure a test Flask application."""
    app = create_app()
    app.config['TESTING'] = True
    app.config['SECRET_KEY'] = 'test-secret-key'
    app.config['WTF_CSRF_ENABLED'] = False
    return app


@pytest.fixture
def client(app):
    """Create a test client for the Flask application."""
    return app.test_client()


@pytest.fixture
def mock_user():
    """Create a mock user with CMS accounts."""
    user = Mock()
    user.id = 'test-user-id'
    user.username = '02-000000-013'
    user.is_authenticated = True
    
    # Mock CMS account
    mock_account = Mock()
    mock_account.account_id = 'test-account-uuid'
    mock_account.cms_username = '02-000000-013'
    mock_account.cms_password = 'encrypted-password'
    mock_account.student_name = 'Test Student'
    
    user.cms_accounts = {'test-account-uuid': mock_account}
    user.get_active_account = Mock(return_value=mock_account)
    
    return user


class TestAttendanceRouteSessionAuth:
    """Test suite for attendance route session authentication."""
    
    def test_attendance_route_requires_session_auth(self, client, mock_user):
        """Test that /attendance route requires session authentication."""
        with patch('flask_login.utils._get_user', return_value=mock_user):
            with client.session_transaction() as sess:
                sess['_user_id'] = 'test-user-id'
                # Don't add account to authenticated_accounts
            
            # Access attendance route without session authentication
            response = client.get('/attendance')
            
            # Should return 403 Forbidden
            assert response.status_code == 403
            data = json.loads(response.data)
            assert data['error'] == 'Account not authenticated in this session'
            assert data['requires_auth'] is True
            assert data['account_id'] == 'test-account-uuid'
    
    @patch('app.routes.attendance.data_manager')
    def test_attendance_route_allows_authenticated_account(self, mock_data_manager, client, mock_user):
        """Test that /attendance route allows access with session authentication."""
        # Mock data manager to return empty attendance data
        mock_data_manager.load_account_data.return_value = None
        
        with patch('flask_login.utils._get_user', return_value=mock_user):
            with client.session_transaction() as sess:
                sess['_user_id'] = 'test-user-id'
                # Add account to authenticated_accounts
                sess['authenticated_accounts'] = ['test-account-uuid']
            
            # Access attendance route with session authentication
            with patch('app.routes.attendance.render_template') as mock_render:
                mock_render.return_value = 'rendered template'
                response = client.get('/attendance')
                
                # Should return 200 OK (or render template)
                assert response.status_code == 200
    
    def test_attendance_refresh_requires_session_auth(self, client, mock_user):
        """Test that /attendance/refresh route requires session authentication."""
        with patch('flask_login.utils._get_user', return_value=mock_user):
            with client.session_transaction() as sess:
                sess['_user_id'] = 'test-user-id'
                # Don't add account to authenticated_accounts
            
            # Access refresh route without session authentication
            response = client.get('/attendance/refresh')
            
            # Should return 403 Forbidden
            assert response.status_code == 403
            data = json.loads(response.data)
            assert data['error'] == 'Account not authenticated in this session'
            assert data['requires_auth'] is True
            assert data['account_id'] == 'test-account-uuid'
    
    @patch('app.routes.attendance.scraper_manager')
    def test_attendance_refresh_allows_authenticated_account(self, mock_scraper_manager, client, mock_user):
        """Test that /attendance/refresh route allows access with session authentication."""
        # Mock scraper
        mock_scraper = Mock()
        mock_scraper.login.return_value = True
        mock_scraper.scrape_attendance.return_value = {'courses': []}
        mock_scraper_manager.get_scraper.return_value = mock_scraper
        
        with patch('flask_login.utils._get_user', return_value=mock_user):
            with client.session_transaction() as sess:
                sess['_user_id'] = 'test-user-id'
                # Add account to authenticated_accounts
                sess['authenticated_accounts'] = ['test-account-uuid']
            
            # Access refresh route with session authentication
            response = client.get('/attendance/refresh', follow_redirects=False)
            
            # Should redirect (302) not return 403
            assert response.status_code == 302
    
    def test_attendance_mark_as_seen_requires_session_auth(self, client, mock_user):
        """Test that /attendance/mark-as-seen route requires session authentication."""
        with patch('flask_login.utils._get_user', return_value=mock_user):
            with client.session_transaction() as sess:
                sess['_user_id'] = 'test-user-id'
                # Don't add account to authenticated_accounts
            
            # Access mark-as-seen route without session authentication
            response = client.get('/attendance/mark-as-seen')
            
            # Should return 403 Forbidden
            assert response.status_code == 403
            data = json.loads(response.data)
            assert data['error'] == 'Account not authenticated in this session'
            assert data['requires_auth'] is True
            assert data['account_id'] == 'test-account-uuid'
    
    @patch('app.routes.attendance.AttendanceChangeTracker')
    def test_attendance_mark_as_seen_allows_authenticated_account(self, mock_tracker_class, client, mock_user):
        """Test that /attendance/mark-as-seen route allows access with session authentication."""
        # Mock change tracker
        mock_tracker = Mock()
        mock_tracker.detect_changes.return_value = []
        mock_tracker_class.return_value = mock_tracker
        
        with patch('flask_login.utils._get_user', return_value=mock_user):
            with client.session_transaction() as sess:
                sess['_user_id'] = 'test-user-id'
                # Add account to authenticated_accounts
                sess['authenticated_accounts'] = ['test-account-uuid']
            
            # Access mark-as-seen route with session authentication
            response = client.get('/attendance/mark-as-seen', follow_redirects=False)
            
            # Should redirect (302) not return 403
            assert response.status_code == 302
    
    def test_attendance_manage_records_requires_session_auth(self, client, mock_user):
        """Test that /attendance/manage-records route requires session authentication."""
        with patch('flask_login.utils._get_user', return_value=mock_user):
            with client.session_transaction() as sess:
                sess['_user_id'] = 'test-user-id'
                # Don't add account to authenticated_accounts
            
            # Access manage-records route without session authentication
            response = client.get('/attendance/manage-records')
            
            # Should return 403 Forbidden
            assert response.status_code == 403
            data = json.loads(response.data)
            assert data['error'] == 'Account not authenticated in this session'
            assert data['requires_auth'] is True
            assert data['account_id'] == 'test-account-uuid'
    
    @patch('app.routes.attendance.AttendanceChangeTracker')
    def test_attendance_manage_records_allows_authenticated_account(self, mock_tracker_class, client, mock_user):
        """Test that /attendance/manage-records route allows access with session authentication."""
        # Mock change tracker
        mock_tracker = Mock()
        mock_tracker.perform_record_lifecycle_management.return_value = {}
        mock_tracker_class.return_value = mock_tracker
        
        with patch('flask_login.utils._get_user', return_value=mock_user):
            with client.session_transaction() as sess:
                sess['_user_id'] = 'test-user-id'
                # Add account to authenticated_accounts
                sess['authenticated_accounts'] = ['test-account-uuid']
            
            # Access manage-records route with session authentication
            response = client.get('/attendance/manage-records', follow_redirects=False)
            
            # Should redirect (302) not return 403
            assert response.status_code == 302


class TestAttendanceRouteErrorResponses:
    """Test suite for attendance route error response format."""
    
    def test_error_response_format(self, client, mock_user):
        """Test that error response includes all required fields."""
        with patch('flask_login.utils._get_user', return_value=mock_user):
            with client.session_transaction() as sess:
                sess['_user_id'] = 'test-user-id'
                # Don't add account to authenticated_accounts
            
            # Access attendance route without session authentication
            response = client.get('/attendance')
            
            # Verify error response format
            assert response.status_code == 403
            data = json.loads(response.data)
            
            # Check all required fields
            assert 'error' in data
            assert 'requires_auth' in data
            assert 'account_id' in data
            
            # Check field values
            assert isinstance(data['error'], str)
            assert data['requires_auth'] is True
            assert data['account_id'] == 'test-account-uuid'


class TestAttendanceRouteNoActiveAccount:
    """Test suite for attendance routes when no active account exists."""
    
    def test_attendance_route_no_active_account(self, client):
        """Test that /attendance route handles no active account gracefully."""
        # Create a mock user with no active account
        mock_user_no_account = Mock()
        mock_user_no_account.is_authenticated = True
        mock_user_no_account.get_active_account = Mock(return_value=None)
        
        with patch('flask_login.utils._get_user', return_value=mock_user_no_account):
            with client.session_transaction() as sess:
                sess['_user_id'] = 'test-user-id'
            
            # Access attendance route without active account
            with patch('app.routes.attendance.render_template') as mock_render:
                mock_render.return_value = 'rendered template'
                response = client.get('/attendance')
                
                # Should render template with error (not return 403)
                assert response.status_code == 200


if __name__ == '__main__':
    # Run tests with pytest
    pytest.main([__file__, '-v', '--tb=short'])
