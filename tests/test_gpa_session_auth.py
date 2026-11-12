"""
Integration tests for GPA routes with session authentication.

Tests that GPA routes properly check session authentication and
return 403 Forbidden when accessing unauthenticated accounts.

**Validates: Requirements FR-5.1, FR-5.2, FR-5.3, AC-3.4**
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


class TestGPARouteSessionAuth:
    """Test suite for GPA route session authentication."""
    
    def test_gpa_calculator_requires_session_auth(self, client, mock_user):
        """Test that /gpa-calculator route requires session authentication."""
        with patch('flask_login.utils._get_user', return_value=mock_user):
            with client.session_transaction() as sess:
                sess['_user_id'] = 'test-user-id'
                # Don't add account to authenticated_accounts
            
            # Access gpa-calculator route without session authentication
            response = client.get('/gpa-calculator')
            
            # Should return 403 Forbidden
            assert response.status_code == 403
            data = json.loads(response.data)
            assert data['error'] == 'Account not authenticated in this session'
            assert data['requires_auth'] is True
            assert data['account_id'] == 'test-account-uuid'
    
    def test_gpa_calculator_allows_authenticated_account(self, client, mock_user):
        """Test that /gpa-calculator route allows access with session authentication."""
        with patch('flask_login.utils._get_user', return_value=mock_user):
            with client.session_transaction() as sess:
                sess['_user_id'] = 'test-user-id'
                # Add account to authenticated_accounts
                sess['authenticated_accounts'] = ['test-account-uuid']
            
            # Access gpa-calculator route with session authentication
            with patch('app.routes.gpa.render_template') as mock_render:
                mock_render.return_value = 'rendered template'
                response = client.get('/gpa-calculator')
                
                # Should return 200 OK
                assert response.status_code == 200
    
    def test_calculate_gpa_requires_session_auth(self, client, mock_user):
        """Test that /calculate-gpa route requires session authentication."""
        with patch('flask_login.utils._get_user', return_value=mock_user):
            with client.session_transaction() as sess:
                sess['_user_id'] = 'test-user-id'
                # Don't add account to authenticated_accounts
            
            # Access calculate-gpa route without session authentication
            response = client.post('/calculate-gpa', 
                                  json={'courses': []},
                                  content_type='application/json')
            
            # Should return 403 Forbidden
            assert response.status_code == 403
            data = json.loads(response.data)
            assert data['error'] == 'Account not authenticated in this session'
            assert data['requires_auth'] is True
            assert data['account_id'] == 'test-account-uuid'
    
    def test_calculate_gpa_allows_authenticated_account(self, client, mock_user):
        """Test that /calculate-gpa route allows access with session authentication."""
        with patch('flask_login.utils._get_user', return_value=mock_user):
            with client.session_transaction() as sess:
                sess['_user_id'] = 'test-user-id'
                # Add account to authenticated_accounts
                sess['authenticated_accounts'] = ['test-account-uuid']
            
            # Access calculate-gpa route with session authentication
            response = client.post('/calculate-gpa',
                                  json={'courses': [
                                      {'grade': 'A', 'credit_hours': 3}
                                  ]},
                                  content_type='application/json')
            
            # Should return 200 OK
            assert response.status_code == 200
            data = json.loads(response.data)
            assert 'gpa' in data
    
    def test_provisional_results_requires_session_auth(self, client, mock_user):
        """Test that /provisional-results route requires session authentication."""
        with patch('flask_login.utils._get_user', return_value=mock_user):
            with client.session_transaction() as sess:
                sess['_user_id'] = 'test-user-id'
                # Don't add account to authenticated_accounts
            
            # Access provisional-results route without session authentication
            response = client.get('/provisional-results')
            
            # Should return 403 Forbidden
            assert response.status_code == 403
            data = json.loads(response.data)
            assert data['error'] == 'Account not authenticated in this session'
            assert data['requires_auth'] is True
            assert data['account_id'] == 'test-account-uuid'
    
    @patch('app.routes.gpa.data_manager')
    def test_provisional_results_allows_authenticated_account(self, mock_data_manager, client, mock_user):
        """Test that /provisional-results route allows access with session authentication."""
        # Mock data manager to return empty GPA data
        mock_data_manager.load_account_data.return_value = None
        
        with patch('flask_login.utils._get_user', return_value=mock_user):
            with client.session_transaction() as sess:
                sess['_user_id'] = 'test-user-id'
                # Add account to authenticated_accounts
                sess['authenticated_accounts'] = ['test-account-uuid']
            
            # Access provisional-results route with session authentication
            with patch('app.routes.gpa.render_template') as mock_render:
                mock_render.return_value = 'rendered template'
                response = client.get('/provisional-results')
                
                # Should return 200 OK (or render template)
                assert response.status_code == 200
    
    def test_refresh_provisional_results_requires_session_auth(self, client, mock_user):
        """Test that /provisional-results/refresh route requires session authentication."""
        with patch('flask_login.utils._get_user', return_value=mock_user):
            with client.session_transaction() as sess:
                sess['_user_id'] = 'test-user-id'
                # Don't add account to authenticated_accounts
            
            # Access refresh route without session authentication
            response = client.get('/provisional-results/refresh')
            
            # Should return 403 Forbidden
            assert response.status_code == 403
            data = json.loads(response.data)
            assert data['error'] == 'Account not authenticated in this session'
            assert data['requires_auth'] is True
            assert data['account_id'] == 'test-account-uuid'
    
    @patch('app.routes.gpa.scraper_manager')
    def test_refresh_provisional_results_allows_authenticated_account(self, mock_scraper_manager, client, mock_user):
        """Test that /provisional-results/refresh route allows access with session authentication."""
        # Mock scraper
        mock_scraper = Mock()
        mock_scraper.login.return_value = True
        mock_scraper.scrape_gpa_data.return_value = {'semesters': []}
        mock_scraper_manager.get_scraper.return_value = mock_scraper
        
        with patch('flask_login.utils._get_user', return_value=mock_user):
            with client.session_transaction() as sess:
                sess['_user_id'] = 'test-user-id'
                # Add account to authenticated_accounts
                sess['authenticated_accounts'] = ['test-account-uuid']
            
            # Access refresh route with session authentication
            response = client.get('/provisional-results/refresh', follow_redirects=False)
            
            # Should redirect (302) not return 403
            assert response.status_code == 302


if __name__ == '__main__':
    # Run tests with pytest
    pytest.main([__file__, '-v', '--tb=short'])
