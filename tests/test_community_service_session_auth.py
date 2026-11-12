"""
Integration tests for community service routes with session authentication.

Tests that community service routes properly check session authentication and
return 403 Forbidden when accessing unauthenticated accounts.

**Validates: Requirements FR-5.1, FR-5.2, FR-5.3, AC-3.6**
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


class TestCommunityServiceRouteSessionAuth:
    """Test suite for community service route session authentication."""
    
    def test_community_service_route_requires_session_auth(self, client, mock_user):
        """Test that /community-service route requires session authentication."""
        with patch('flask_login.utils._get_user', return_value=mock_user):
            with client.session_transaction() as sess:
                sess['_user_id'] = 'test-user-id'
                # Don't add account to authenticated_accounts
            
            # Access community-service route without session authentication
            response = client.get('/community-service')
            
            # Should return 403 Forbidden
            assert response.status_code == 403
            data = json.loads(response.data)
            assert data['error'] == 'Account not authenticated in this session'
            assert data['requires_auth'] is True
            assert data['account_id'] == 'test-account-uuid'
    
    @patch('app.routes.community.data_manager')
    def test_community_service_route_allows_authenticated_account(self, mock_data_manager, client, mock_user):
        """Test that /community-service route allows access with session authentication."""
        # Mock data manager to return empty community service data
        mock_data_manager.load_account_data.return_value = None
        
        with patch('flask_login.utils._get_user', return_value=mock_user):
            with client.session_transaction() as sess:
                sess['_user_id'] = 'test-user-id'
                # Add account to authenticated_accounts
                sess['authenticated_accounts'] = ['test-account-uuid']
            
            # Access community-service route with session authentication
            with patch('app.routes.community.render_template') as mock_render:
                mock_render.return_value = 'rendered template'
                response = client.get('/community-service')
                
                # Should return 200 OK (or render template)
                assert response.status_code == 200
    
    def test_community_service_refresh_requires_session_auth(self, client, mock_user):
        """Test that /community-service/refresh route requires session authentication."""
        with patch('flask_login.utils._get_user', return_value=mock_user):
            with client.session_transaction() as sess:
                sess['_user_id'] = 'test-user-id'
                # Don't add account to authenticated_accounts
            
            # Access refresh route without session authentication
            response = client.get('/community-service/refresh')
            
            # Should return 403 Forbidden
            assert response.status_code == 403
            data = json.loads(response.data)
            assert data['error'] == 'Account not authenticated in this session'
            assert data['requires_auth'] is True
            assert data['account_id'] == 'test-account-uuid'
    
    @patch('app.routes.community.scraper_manager')
    def test_community_service_refresh_allows_authenticated_account(self, mock_scraper_manager, client, mock_user):
        """Test that /community-service/refresh route allows access with session authentication."""
        # Mock scraper
        mock_scraper = Mock()
        mock_scraper.login.return_value = True
        mock_scraper.scrape_community_service.return_value = {'activities': [], 'opportunities': []}
        mock_scraper_manager.get_scraper.return_value = mock_scraper
        
        with patch('flask_login.utils._get_user', return_value=mock_user):
            with client.session_transaction() as sess:
                sess['_user_id'] = 'test-user-id'
                # Add account to authenticated_accounts
                sess['authenticated_accounts'] = ['test-account-uuid']
            
            # Access refresh route with session authentication
            response = client.get('/community-service/refresh', follow_redirects=False)
            
            # Should redirect (302) not return 403
            assert response.status_code == 302


if __name__ == '__main__':
    # Run tests with pytest
    pytest.main([__file__, '-v', '--tb=short'])
