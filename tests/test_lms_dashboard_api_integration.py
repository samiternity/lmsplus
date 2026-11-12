#!/usr/bin/env python3
"""
Integration tests for LMS Dashboard API endpoints.
Tests all API endpoints, query parameters, response formats, error responses, and authentication.
"""

import pytest
import json
import tempfile
import shutil
from datetime import datetime, timedelta
from unittest.mock import Mock, patch, MagicMock
from flask import Flask
from flask_login import LoginManager, login_user, logout_user

from app.routes.lms_dashboard_api import lms_dashboard_api_bp, init_dashboard_api
from app.services.lms_dashboard_service import LMSDashboardService
from app.services.lms_cache_service import LMSCacheService
from app.services.lms_background_service import LMSBackgroundService
from app.models.account import User, CMSAccount


class TestLMSDashboardAPIIntegration:
    """Integration tests for LMS Dashboard API endpoints"""
    
    @pytest.fixture
    def app(self):
        """Create and configure a test Flask application"""
        app = Flask(__name__)
        app.config['TESTING'] = True
        app.config['SECRET_KEY'] = 'test-secret-key'
        app.config['WTF_CSRF_ENABLED'] = False
        
        # LMS configuration
        app.config['LMS_CACHE_DIR'] = tempfile.mkdtemp()
        app.config['LMS_CACHE_MAX_ENTRIES'] = 100
        app.config['LMS_CACHE_TTL_MINUTES'] = 30
        app.config['LMS_BACKGROUND_WORKERS'] = 2
        app.config['LMS_BACKGROUND_MAX_RETRIES'] = 2
        app.config['LMS_ENABLE_DISK_CACHE'] = True
        app.config['LMS_ENABLE_STALE_WHILE_REVALIDATE'] = True
        
        # Initialize Flask-Login
        login_manager = LoginManager()
        login_manager.init_app(app)
        
        @login_manager.user_loader
        def load_user(user_id):
            return User.get(user_id)
        
        # Register blueprint
        app.register_blueprint(lms_dashboard_api_bp)
        
        # Initialize services
        app.lms_service = Mock()
        app.cache_service = LMSCacheService(cache_dir=app.config['LMS_CACHE_DIR'])
        app.background_service = LMSBackgroundService(app.lms_service)
        
        # Initialize dashboard API
        init_dashboard_api(app)
        
        yield app
        
        # Cleanup
        shutil.rmtree(app.config['LMS_CACHE_DIR'], ignore_errors=True)
        if app.background_service.is_running:
            app.background_service.stop()
    
    @pytest.fixture
    def client(self, app):
        """Create a test client"""
        return app.test_client()
    
    @pytest.fixture
    def mock_user(self):
        """Create a mock user with active account"""
        user = Mock(spec=User)
        user.user_id = 'test_user_123'
        user.is_authenticated = True
        user.is_active = True
        user.is_anonymous = False
        user.get_id.return_value = 'test_user_123'
        
        # Mock active account
        active_account = Mock(spec=CMSAccount)
        active_account.account_id = 'acc_123'
        active_account.cms_username = 'test_user'
        active_account.cms_password = 'test_pass'
        
        user.get_active_account.return_value = active_account
        
        return user
    
    @pytest.fixture
    def authenticated_client(self, app, client, mock_user):
        """Create an authenticated test client"""
        with app.test_request_context():
            with patch('flask_login.utils._get_user', return_value=mock_user):
                with patch('app.routes.lms_dashboard_api.current_user', mock_user):
                    yield client


class TestPendingAssignmentsEndpoint(TestLMSDashboardAPIIntegration):
    """Test GET /lms/api/dashboard/pending-assignments endpoint"""
    
    def test_pending_assignments_success(self, app, authenticated_client, mock_user):
        """Test successful retrieval of pending assignments"""
        # Mock widget data
        mock_widget_data = {
            'pending': [
                {
                    'id': '1',
                    'title': 'Assignment 1',
                    'course_name': 'CS101',
                    'deadline': '2025-12-01T23:59:00',
                    'urgency_level': 'urgent'
                }
            ],
            'missed': [],
            'total_pending': 1,
            'total_missed': 0,
            'latest_semester': 'SEM2025',
            'latest_semester_name': 'Fall 2025',
            'refreshed_at': datetime.now().isoformat(),
            'from_cache': True,
            'is_stale': False,
            'cache_age_minutes': 5.0
        }
        
        with patch('app.routes.lms_dashboard_api.current_user', mock_user):
            with patch('app.routes.lms_dashboard_api.dashboard_service') as mock_service:
                mock_service.get_pending_assignments_widget_data.return_value = mock_widget_data
                
                response = authenticated_client.get('/lms/api/dashboard/pending-assignments')
                
                assert response.status_code == 200
                data = json.loads(response.data)
                
                assert data['success'] is True
                assert 'pending' in data
                assert 'missed' in data
                assert data['total_pending'] == 1
                assert data['total_missed'] == 0
                assert data['from_cache'] is True
    
    def test_pending_assignments_force_refresh(self, app, authenticated_client, mock_user):
        """Test force refresh parameter"""
        mock_widget_data = {
            'pending': [],
            'missed': [],
            'total_pending': 0,
            'total_missed': 0,
            'from_cache': False
        }
        
        with patch('app.routes.lms_dashboard_api.current_user', mock_user):
            with patch('app.routes.lms_dashboard_api.dashboard_service') as mock_service:
                mock_service.get_pending_assignments_widget_data.return_value = mock_widget_data
                
                response = authenticated_client.get(
                    '/lms/api/dashboard/pending-assignments?force_refresh=true'
                )
                
                assert response.status_code == 200
                data = json.loads(response.data)
                
                # Verify force_refresh was passed to service
                mock_service.get_pending_assignments_widget_data.assert_called_once()
                call_args = mock_service.get_pending_assignments_widget_data.call_args
                assert call_args[1]['force_refresh'] is True
    
    def test_pending_assignments_with_error(self, app, authenticated_client, mock_user):
        """Test handling of widget data with error"""
        mock_widget_data = {
            'error': 'Failed to fetch assignments',
            'pending': [],
            'missed': [],
            'total_pending': 0,
            'total_missed': 0
        }
        
        with patch('app.routes.lms_dashboard_api.current_user', mock_user):
            with patch('app.routes.lms_dashboard_api.dashboard_service') as mock_service:
                mock_service.get_pending_assignments_widget_data.return_value = mock_widget_data
                
                response = authenticated_client.get('/lms/api/dashboard/pending-assignments')
                
                assert response.status_code == 200
                data = json.loads(response.data)
                
                assert data['success'] is True  # Still returns success
                assert 'user_message' in data
                assert data['error'] == 'Failed to fetch assignments'
    
    def test_pending_assignments_exception(self, app, authenticated_client, mock_user):
        """Test handling of exceptions"""
        with patch('app.routes.lms_dashboard_api.current_user', mock_user):
            with patch('app.routes.lms_dashboard_api.dashboard_service') as mock_service:
                mock_service.get_pending_assignments_widget_data.side_effect = Exception('Test error')
                
                response = authenticated_client.get('/lms/api/dashboard/pending-assignments')
                
                assert response.status_code == 500
                data = json.loads(response.data)
                
                assert data['success'] is False
                assert 'error' in data
                assert data['error_code'] == 'FETCH_ERROR'
                assert data['retry_available'] is True


class TestSummaryEndpoint(TestLMSDashboardAPIIntegration):
    """Test GET /lms/api/dashboard/summary endpoint"""
    
    def test_summary_success(self, app, authenticated_client, mock_user):
        """Test successful retrieval of summary"""
        mock_summary = {
            'total_pending': 5,
            'total_missed': 2,
            'urgent_count': 1,
            'latest_semester': 'SEM2025',
            'last_updated': datetime.now().isoformat()
        }
        
        with patch('app.routes.lms_dashboard_api.current_user', mock_user):
            with patch('app.routes.lms_dashboard_api.dashboard_service') as mock_service:
                mock_service.get_assignments_summary.return_value = mock_summary
                
                response = authenticated_client.get('/lms/api/dashboard/summary')
                
                assert response.status_code == 200
                data = json.loads(response.data)
                
                assert data['success'] is True
                assert data['total_pending'] == 5
                assert data['total_missed'] == 2
                assert data['urgent_count'] == 1
    
    def test_summary_exception(self, app, authenticated_client, mock_user):
        """Test handling of exceptions in summary endpoint"""
        with patch('app.routes.lms_dashboard_api.current_user', mock_user):
            with patch('app.routes.lms_dashboard_api.dashboard_service') as mock_service:
                mock_service.get_assignments_summary.side_effect = Exception('Test error')
                
                response = authenticated_client.get('/lms/api/dashboard/summary')
                
                assert response.status_code == 500
                data = json.loads(response.data)
                
                assert data['success'] is False
                assert data['error_code'] == 'SUMMARY_ERROR'
                assert data['retry_available'] is True


class TestRefreshEndpoint(TestLMSDashboardAPIIntegration):
    """Test POST /lms/api/dashboard/refresh endpoint"""
    
    def test_refresh_success(self, app, authenticated_client, mock_user):
        """Test successful background refresh trigger"""
        with patch('app.routes.lms_dashboard_api.current_user', mock_user):
            with patch('app.routes.lms_dashboard_api.get_background_service') as mock_get_service:
                mock_bg_service = Mock()
                mock_bg_service.schedule_pending_assignments_refresh.return_value = 'task_123'
                mock_get_service.return_value = mock_bg_service
                
                response = authenticated_client.post('/lms/api/dashboard/refresh')
                
                assert response.status_code == 202
                data = json.loads(response.data)
                
                assert data['success'] is True
                assert data['task_id'] == 'task_123'
                assert 'status_endpoint' in data
                assert data['message'] == 'Refresh started in background'
    
    def test_refresh_skipped(self, app, authenticated_client, mock_user):
        """Test refresh skipped when recently updated"""
        with patch('app.routes.lms_dashboard_api.current_user', mock_user):
            with patch('app.routes.lms_dashboard_api.get_background_service') as mock_get_service:
                mock_bg_service = Mock()
                mock_bg_service.schedule_pending_assignments_refresh.return_value = None
                mock_get_service.return_value = mock_bg_service
                
                response = authenticated_client.post('/lms/api/dashboard/refresh')
                
                assert response.status_code == 200
                data = json.loads(response.data)
                
                assert data['success'] is True
                assert data['skipped'] is True
                assert 'recently refreshed' in data['message'].lower()
    
    def test_refresh_service_unavailable(self, app, authenticated_client, mock_user):
        """Test refresh when background service is unavailable"""
        with patch('app.routes.lms_dashboard_api.current_user', mock_user):
            with patch('app.routes.lms_dashboard_api.get_background_service') as mock_get_service:
                mock_get_service.return_value = None
                
                response = authenticated_client.post('/lms/api/dashboard/refresh')
                
                assert response.status_code == 503
                data = json.loads(response.data)
                
                assert data['success'] is False
                assert data['error_code'] == 'SERVICE_UNAVAILABLE'
                assert 'fallback' in data
    
    def test_refresh_exception(self, app, authenticated_client, mock_user):
        """Test handling of exceptions in refresh endpoint"""
        with patch('app.routes.lms_dashboard_api.current_user', mock_user):
            with patch('app.routes.lms_dashboard_api.get_background_service') as mock_get_service:
                mock_get_service.side_effect = Exception('Test error')
                
                response = authenticated_client.post('/lms/api/dashboard/refresh')
                
                assert response.status_code == 500
                data = json.loads(response.data)
                
                assert data['success'] is False
                assert data['error_code'] == 'REFRESH_ERROR'


class TestRefreshStatusEndpoint(TestLMSDashboardAPIIntegration):
    """Test GET /lms/api/dashboard/refresh-status/<task_id> endpoint"""
    
    def test_refresh_status_success(self, app, authenticated_client, mock_user):
        """Test successful retrieval of task status"""
        mock_task_status = {
            'task_id': 'task_123',
            'account_id': 'acc_123',
            'is_complete': False,
            'is_failed': False,
            'retry_count': 0,
            'created_at': datetime.now().isoformat(),
            'duration_seconds': 3.5
        }
        
        with patch('app.routes.lms_dashboard_api.current_user', mock_user):
            with patch('app.routes.lms_dashboard_api.get_background_service') as mock_get_service:
                mock_bg_service = Mock()
                mock_bg_service.get_task_status.return_value = mock_task_status
                mock_get_service.return_value = mock_bg_service
                
                response = authenticated_client.get('/lms/api/dashboard/refresh-status/task_123')
                
                assert response.status_code == 200
                data = json.loads(response.data)
                
                assert data['success'] is True
                assert data['task_id'] == 'task_123'
                assert data['is_complete'] is False
    
    def test_refresh_status_task_not_found(self, app, authenticated_client, mock_user):
        """Test task not found"""
        with patch('app.routes.lms_dashboard_api.current_user', mock_user):
            with patch('app.routes.lms_dashboard_api.get_background_service') as mock_get_service:
                mock_bg_service = Mock()
                mock_bg_service.get_task_status.return_value = None
                mock_get_service.return_value = mock_bg_service
                
                response = authenticated_client.get('/lms/api/dashboard/refresh-status/nonexistent')
                
                assert response.status_code == 404
                data = json.loads(response.data)
                
                assert data['success'] is False
                assert data['error_code'] == 'TASK_NOT_FOUND'
    
    def test_refresh_status_service_unavailable(self, app, authenticated_client, mock_user):
        """Test status check when service is unavailable"""
        with patch('app.routes.lms_dashboard_api.current_user', mock_user):
            with patch('app.routes.lms_dashboard_api.get_background_service') as mock_get_service:
                mock_get_service.return_value = None
                
                response = authenticated_client.get('/lms/api/dashboard/refresh-status/task_123')
                
                assert response.status_code == 503
                data = json.loads(response.data)
                
                assert data['success'] is False
                assert data['error_code'] == 'SERVICE_UNAVAILABLE'


class TestCacheStatsEndpoint(TestLMSDashboardAPIIntegration):
    """Test GET /lms/api/dashboard/cache-stats endpoint"""
    
    def test_cache_stats_success(self, app, authenticated_client, mock_user):
        """Test successful retrieval of cache statistics"""
        mock_cache_stats = {
            'hits': 100,
            'misses': 20,
            'hit_rate_percent': 83.33,
            'memory_entries': 45,
            'memory_size_mb': 2.3
        }
        
        mock_bg_stats = {
            'active_tasks': 1,
            'queued_tasks': 0,
            'completed_tasks': 234
        }
        
        with patch('app.routes.lms_dashboard_api.current_user', mock_user):
            with patch('app.routes.lms_dashboard_api.get_cache_service') as mock_get_cache:
                with patch('app.routes.lms_dashboard_api.get_background_service') as mock_get_bg:
                    mock_cache_service = Mock()
                    mock_cache_service.get_stats.return_value = mock_cache_stats
                    mock_get_cache.return_value = mock_cache_service
                    
                    mock_bg_service = Mock()
                    mock_bg_service.get_stats.return_value = mock_bg_stats
                    mock_get_bg.return_value = mock_bg_service
                    
                    response = authenticated_client.get('/lms/api/dashboard/cache-stats')
                    
                    assert response.status_code == 200
                    data = json.loads(response.data)
                    
                    assert data['success'] is True
                    assert 'cache_stats' in data
                    assert 'background_service' in data
                    assert data['cache_stats']['hits'] == 100
                    assert data['background_service']['active_tasks'] == 1
    
    def test_cache_stats_partial_failure(self, app, authenticated_client, mock_user):
        """Test cache stats when one service fails"""
        with patch('app.routes.lms_dashboard_api.current_user', mock_user):
            with patch('app.routes.lms_dashboard_api.get_cache_service') as mock_get_cache:
                with patch('app.routes.lms_dashboard_api.get_background_service') as mock_get_bg:
                    # Cache service fails
                    mock_cache_service = Mock()
                    mock_cache_service.get_stats.side_effect = Exception('Cache error')
                    mock_get_cache.return_value = mock_cache_service
                    
                    # Background service succeeds
                    mock_bg_service = Mock()
                    mock_bg_service.get_stats.return_value = {'active_tasks': 0}
                    mock_get_bg.return_value = mock_bg_service
                    
                    response = authenticated_client.get('/lms/api/dashboard/cache-stats')
                    
                    assert response.status_code == 200
                    data = json.loads(response.data)
                    
                    assert data['success'] is True
                    assert 'error' in data['cache_stats']
                    assert data['background_service']['active_tasks'] == 0


class TestAuthenticationRequirements(TestLMSDashboardAPIIntegration):
    """Test authentication requirements for all endpoints"""
    
    def test_no_authentication(self, client):
        """Test endpoints without authentication"""
        endpoints = [
            ('/lms/api/dashboard/pending-assignments', 'GET'),
            ('/lms/api/dashboard/summary', 'GET'),
            ('/lms/api/dashboard/refresh', 'POST'),
            ('/lms/api/dashboard/refresh-status/task_123', 'GET'),
            ('/lms/api/dashboard/cache-stats', 'GET')
        ]
        
        for endpoint, method in endpoints:
            if method == 'GET':
                response = client.get(endpoint)
            else:
                response = client.post(endpoint)
            
            # Should redirect to login or return 401
            assert response.status_code in [302, 401]
    
    def test_no_active_account(self, app, authenticated_client, mock_user):
        """Test endpoints without active account"""
        # Override mock user to have no active account
        mock_user.get_active_account.return_value = None
        
        with patch('app.routes.lms_dashboard_api.current_user', mock_user):
            response = authenticated_client.get('/lms/api/dashboard/pending-assignments')
            
            assert response.status_code == 401
            data = json.loads(response.data)
            
            assert data['success'] is False
            assert data['error_code'] == 'NO_ACCOUNT'
            assert data['action_required'] == 'configure_account'
    
    def test_no_cms_credentials(self, app, authenticated_client, mock_user):
        """Test endpoints without CMS credentials"""
        # Override mock account to have no credentials
        mock_account = Mock(spec=CMSAccount)
        mock_account.account_id = 'acc_123'
        mock_account.cms_username = None
        mock_account.cms_password = None
        
        mock_user.get_active_account.return_value = mock_account
        
        with patch('app.routes.lms_dashboard_api.current_user', mock_user):
            response = authenticated_client.get('/lms/api/dashboard/pending-assignments')
            
            assert response.status_code == 401
            data = json.loads(response.data)
            
            assert data['success'] is False
            assert data['error_code'] == 'NO_CREDENTIALS'
            assert data['action_required'] == 'configure_credentials'


class TestErrorHandlers(TestLMSDashboardAPIIntegration):
    """Test error handlers for the blueprint"""
    
    def test_404_handler(self, authenticated_client, mock_user):
        """Test 404 error handler - Flask returns HTML by default for non-blueprint routes"""
        with patch('app.routes.lms_dashboard_api.current_user', mock_user):
            response = authenticated_client.get('/lms/api/dashboard/nonexistent-endpoint')
            
            # Flask's default 404 handler returns HTML, not JSON
            # This is expected behavior for routes outside the blueprint
            assert response.status_code == 404
            # Verify it's an HTML response
            assert b'404' in response.data or b'Not Found' in response.data


class TestResponseFormats(TestLMSDashboardAPIIntegration):
    """Test response format consistency"""
    
    def test_success_response_format(self, app, authenticated_client, mock_user):
        """Test that success responses have consistent format"""
        mock_widget_data = {
            'pending': [],
            'missed': [],
            'total_pending': 0,
            'total_missed': 0
        }
        
        with patch('app.routes.lms_dashboard_api.current_user', mock_user):
            with patch('app.routes.lms_dashboard_api.dashboard_service') as mock_service:
                mock_service.get_pending_assignments_widget_data.return_value = mock_widget_data
                
                response = authenticated_client.get('/lms/api/dashboard/pending-assignments')
                
                assert response.status_code == 200
                assert response.content_type == 'application/json'
                
                data = json.loads(response.data)
                assert 'success' in data
                assert data['success'] is True
    
    def test_error_response_format(self, app, authenticated_client, mock_user):
        """Test that error responses have consistent format"""
        with patch('app.routes.lms_dashboard_api.current_user', mock_user):
            with patch('app.routes.lms_dashboard_api.dashboard_service') as mock_service:
                mock_service.get_pending_assignments_widget_data.side_effect = Exception('Test')
                
                response = authenticated_client.get('/lms/api/dashboard/pending-assignments')
                
                assert response.status_code == 500
                assert response.content_type == 'application/json'
                
                data = json.loads(response.data)
                assert 'success' in data
                assert data['success'] is False
                assert 'error' in data
                assert 'error_code' in data
                assert 'user_message' in data


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
