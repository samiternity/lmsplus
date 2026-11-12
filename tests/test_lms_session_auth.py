"""
Integration tests for LMS routes with session authentication.

Tests that LMS routes properly check session authentication and
return 403 Forbidden when accessing unauthenticated accounts.

**Validates: Requirements FR-5.1, FR-5.2, FR-5.3, AC-3.5**
"""

import pytest
import sys
import os
import json
from unittest.mock import Mock, patch

# Add app to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from flask import session
from app import create_app


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


class TestLMSRouteSessionAuth:
    """Test suite for LMS route session authentication."""
    
    @patch('app.routes.lms.current_user')
    def test_lms_dashboard_requires_session_auth(self, mock_current_user, client, mock_user):
        """Test that /lms/dashboard route requires session authentication."""
        mock_current_user.is_authenticated = True
        mock_current_user.get_active_account = mock_user.get_active_account
        
        with client.session_transaction() as sess:
            sess['_user_id'] = 'test-user-id'
            # Don't add account to authenticated_accounts
        
        # Access LMS dashboard route without session authentication
        response = client.get('/lms/dashboard')
        
        # Should return 403 Forbidden
        assert response.status_code == 403
        data = json.loads(response.data)
        assert data['error'] == 'Account not authenticated in this session'
        assert data['requires_auth'] is True
        assert data['account_id'] == 'test-account-uuid'
    
    @patch('app.routes.lms.current_user')
    def test_lms_dashboard_allows_authenticated_account(self, mock_current_user, client, mock_user):
        """Test that /lms/dashboard route allows access with session authentication."""
        mock_current_user.is_authenticated = True
        mock_current_user.get_active_account = mock_user.get_active_account
        
        with client.session_transaction() as sess:
            sess['_user_id'] = 'test-user-id'
            # Add account to authenticated_accounts
            sess['authenticated_accounts'] = ['test-account-uuid']
        
        # Access LMS dashboard route with session authentication
        with patch('app.routes.lms.render_template') as mock_render:
            mock_render.return_value = 'rendered template'
            response = client.get('/lms/dashboard')
            
            # Should return 200 OK (or render template)
            assert response.status_code == 200
    
    @patch('app.routes.lms.current_user')
    def test_lms_assignments_requires_session_auth(self, mock_current_user, client, mock_user):
        """Test that /lms/assignments route requires session authentication."""
        mock_current_user.is_authenticated = True
        mock_current_user.get_active_account = mock_user.get_active_account
        
        with client.session_transaction() as sess:
            sess['_user_id'] = 'test-user-id'
            # Don't add account to authenticated_accounts
        
        # Access LMS assignments route without session authentication
        response = client.get('/lms/assignments')
        
        # Should return 403 Forbidden
        assert response.status_code == 403
        data = json.loads(response.data)
        assert data['error'] == 'Account not authenticated in this session'
        assert data['requires_auth'] is True
        assert data['account_id'] == 'test-account-uuid'
    
    @patch('app.routes.lms.current_user')
    @patch('app.routes.lms.lms_service')
    def test_lms_assignments_allows_authenticated_account(self, mock_lms_service, mock_current_user, client, mock_user):
        """Test that /lms/assignments route allows access with session authentication."""
        mock_current_user.is_authenticated = True
        mock_current_user.get_active_account = mock_user.get_active_account
        
        # Mock LMS service
        mock_lms_service.get_assignments.return_value = []
        
        with client.session_transaction() as sess:
            sess['_user_id'] = 'test-user-id'
            # Add account to authenticated_accounts
            sess['authenticated_accounts'] = ['test-account-uuid']
        
        # Access LMS assignments route with session authentication
        with patch('app.routes.lms.render_template') as mock_render:
            mock_render.return_value = 'rendered template'
            response = client.get('/lms/assignments')
            
            # Should return 200 OK (or render template)
            assert response.status_code == 200
    
    @patch('app.routes.lms.current_user')
    def test_lms_lecture_notes_requires_session_auth(self, mock_current_user, client, mock_user):
        """Test that /lms/lectures route requires session authentication."""
        mock_current_user.is_authenticated = True
        mock_current_user.get_active_account = mock_user.get_active_account
        
        with client.session_transaction() as sess:
            sess['_user_id'] = 'test-user-id'
            # Don't add account to authenticated_accounts
        
        # Access LMS lecture notes route without session authentication
        response = client.get('/lms/lectures')
        
        # Should return 403 Forbidden
        assert response.status_code == 403
        data = json.loads(response.data)
        assert data['error'] == 'Account not authenticated in this session'
        assert data['requires_auth'] is True
        assert data['account_id'] == 'test-account-uuid'
    
    @patch('app.routes.lms.current_user')
    def test_lms_lecture_notes_allows_authenticated_account(self, mock_current_user, client, mock_user):
        """Test that /lms/lectures route allows access with session authentication."""
        mock_current_user.is_authenticated = True
        mock_current_user.get_active_account = mock_user.get_active_account
        
        with client.session_transaction() as sess:
            sess['_user_id'] = 'test-user-id'
            # Add account to authenticated_accounts
            sess['authenticated_accounts'] = ['test-account-uuid']
        
        # Access LMS lecture notes route with session authentication
        with patch('app.routes.lms.render_template') as mock_render:
            mock_render.return_value = 'rendered template'
            response = client.get('/lms/lectures')
            
            # Should return 200 OK (or render template)
            assert response.status_code == 200
    
    @patch('app.routes.lms.current_user')
    def test_lms_api_dashboard_data_requires_session_auth(self, mock_current_user, client, mock_user):
        """Test that /lms/api/dashboard/data route requires session authentication."""
        mock_current_user.is_authenticated = True
        mock_current_user.get_active_account = mock_user.get_active_account
        
        with client.session_transaction() as sess:
            sess['_user_id'] = 'test-user-id'
            # Don't add account to authenticated_accounts
        
        # Access LMS API dashboard data route without session authentication
        response = client.get('/lms/api/dashboard/data')
        
        # Should return 403 Forbidden
        assert response.status_code == 403
        data = json.loads(response.data)
        assert data['error'] == 'Account not authenticated in this session'
        assert data['requires_auth'] is True
        assert data['account_id'] == 'test-account-uuid'
    
    @patch('app.routes.lms.current_user')
    @patch('app.routes.lms.lms_service')
    def test_lms_api_dashboard_data_allows_authenticated_account(self, mock_lms_service, mock_current_user, client, mock_user):
        """Test that /lms/api/dashboard/data route allows access with session authentication."""
        mock_current_user.is_authenticated = True
        mock_current_user.get_active_account = mock_user.get_active_account
        
        # Mock LMS service methods
        mock_lms_service.get_pending_assignments_summary.return_value = {
            'total_pending': 0,
            'urgent_count': 0,
            'soon_count': 0,
            'later_count': 0,
            'urgent_assignments': [],
            'soon_assignments': [],
            'overdue_count': 0
        }
        mock_lms_service.get_assignments.return_value = []
        mock_lms_service.get_lecture_notes.return_value = []
        mock_lms_service.get_service_status.return_value = {'overall_status': 'ok'}
        mock_lms_service.get_cache_info.return_value = {'cache_status': 'ok'}
        
        with client.session_transaction() as sess:
            sess['_user_id'] = 'test-user-id'
            # Add account to authenticated_accounts
            sess['authenticated_accounts'] = ['test-account-uuid']
        
        # Access LMS API dashboard data route with session authentication
        response = client.get('/lms/api/dashboard/data')
        
        # Should return 200 OK
        assert response.status_code == 200
        data = json.loads(response.data)
        assert data['success'] is True


if __name__ == '__main__':
    # Run tests with pytest
    pytest.main([__file__, '-v', '--tb=short'])
