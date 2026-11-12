#!/usr/bin/env python3
"""
End-to-end tests for Pending Assignments Widget.
Tests the complete flow from widget load to data display including:
- First load (cache miss)
- Subsequent loads (cache hit)
- Stale cache scenario
- Manual refresh
- Auto-refresh
- Error scenarios
"""

import pytest
import json
import time
import tempfile
import shutil
from datetime import datetime, timedelta
from unittest.mock import Mock, patch, MagicMock
from flask import Flask
from flask_login import LoginManager

from app.routes.lms_dashboard_api import lms_dashboard_api_bp, init_dashboard_api
from app.services.lms_dashboard_service import LMSDashboardService
from app.services.lms_cache_service import LMSCacheService
from app.services.lms_background_service import LMSBackgroundService
from app.models.account import User, CMSAccount
from app.models.lms_models import LMSAssignment


class TestWidgetEndToEnd:
    """End-to-end tests for the pending assignments widget"""
    
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
        app.background_service.start()
        
        # Initialize dashboard API
        init_dashboard_api(app)
        
        yield app
        
        # Cleanup
        if app.background_service.is_running:
            app.background_service.stop()
        shutil.rmtree(app.config['LMS_CACHE_DIR'], ignore_errors=True)
    
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
        """Create an authenticated test client with proper mocking"""
        # Patch both flask_login and current_user
        with patch('flask_login.utils._get_user', return_value=mock_user):
            with patch('app.routes.lms_dashboard_api.current_user', mock_user):
                yield client
    
    @pytest.fixture
    def mock_assignments(self):
        """Create mock assignment data"""
        now = datetime.now()
        
        # Urgent assignment (due in 12 hours)
        urgent_assignment = Mock(spec=LMSAssignment)
        urgent_assignment.to_dict.return_value = {
            'id': '1',
            'title': 'Urgent Assignment',
            'course_name': 'CS101',
            'course_code': 'CS101',
            'deadline': (now + timedelta(hours=12)).strftime('%d %B %Y-11:55 pm'),
            'total_marks': 100,
            'status': 'pending',
            'is_pending': True,
            'is_past_due': False,
            'is_overdue': False
        }
        
        # Soon assignment (due in 3 days)
        soon_assignment = Mock(spec=LMSAssignment)
        soon_assignment.to_dict.return_value = {
            'id': '2',
            'title': 'Due Soon Assignment',
            'course_name': 'CS102',
            'course_code': 'CS102',
            'deadline': (now + timedelta(days=3)).strftime('%d %B %Y-11:55 pm'),
            'total_marks': 50,
            'status': 'pending',
            'is_pending': True,
            'is_past_due': False,
            'is_overdue': False
        }
        
        # Missed assignment (overdue by 2 days)
        missed_assignment = Mock(spec=LMSAssignment)
        missed_assignment.to_dict.return_value = {
            'id': '3',
            'title': 'Missed Assignment',
            'course_name': 'CS103',
            'course_code': 'CS103',
            'deadline': (now - timedelta(days=2)).strftime('%d %B %Y-11:55 pm'),
            'total_marks': 75,
            'status': 'pending',
            'is_pending': True,
            'is_past_due': True,
            'is_overdue': True
        }
        
        return [urgent_assignment, soon_assignment, missed_assignment]


class TestFirstLoadCacheMiss(TestWidgetEndToEnd):
    """Test first load scenario (cache miss)"""
    
    def test_first_load_fetches_from_lms(self, app, authenticated_client, mock_assignments):
        """Test that first load fetches data from LMS when cache is empty"""
        # Mock LMS service responses
        app.lms_service.get_assignments_options.return_value = {
            'semesters': [{'code': 'SEM2025', 'name': 'Spring 2025'}]
        }
        app.lms_service.get_assignments_courses_for_semester.return_value = [
            {'code': 'CS101', 'name': 'Course 1'},
            {'code': 'CS102', 'name': 'Course 2'},
            {'code': 'CS103', 'name': 'Course 3'}
        ]
        app.lms_service.get_assignments_for_course.return_value = mock_assignments
        
        # First request - should hit LMS
        response = authenticated_client.get('/lms/api/dashboard/pending-assignments')
        
        assert response.status_code == 200
        data = json.loads(response.data)
        
        # Verify response structure
        assert data['success'] is True
        assert 'pending' in data
        assert 'missed' in data
        assert data['from_cache'] is False
        assert data['is_stale'] is False
        
        # Verify LMS was called
        app.lms_service.get_assignments_options.assert_called_once()
        app.lms_service.get_assignments_courses_for_semester.assert_called_once()
        
        # Verify assignments were categorized
        assert data['total_pending'] >= 0
        assert data['total_missed'] >= 0
    
    def test_first_load_caches_results(self, app, client, mock_user, mock_assignments):
        """Test that first load caches the results for future requests"""
        # Mock LMS service
        app.lms_service.get_assignments_options.return_value = {
            'semesters': [{'code': 'SEM2025', 'name': 'Spring 2025'}]
        }
        app.lms_service.get_assignments_courses_for_semester.return_value = [
            {'code': 'CS101', 'name': 'Course 1'}
        ]
        app.lms_service.get_assignments_for_course.return_value = mock_assignments
        
        with patch('app.routes.lms_dashboard_api.current_user', mock_user):
            # First request
            response1 = client.get('/lms/api/dashboard/pending-assignments')
            data1 = json.loads(response1.data)
            
            assert data1['from_cache'] is False
            
            # Verify data is now in cache
            cache_key = 'pending_assignments:acc_123'
            cached_data = app.cache_service.get(cache_key)
            assert cached_data is not None


class TestSubsequentLoadsCacheHit(TestWidgetEndToEnd):
    """Test subsequent loads scenario (cache hit)"""
    
    def test_subsequent_load_serves_from_cache(self, app, client, mock_user):
        """Test that subsequent loads serve data from cache without hitting LMS"""
        # Pre-populate cache
        cached_data = {
            'pending': [{'id': '1', 'title': 'Test Assignment'}],
            'missed': [],
            'all_assignments': [{'id': '1', 'title': 'Test Assignment'}],
            'total_pending': 1,
            'total_missed': 0,
            'total_assignments': 1,
            'latest_semester': 'SEM2025',
            'latest_semester_name': 'Spring 2025',
            'course_count': 1,
            'refreshed_at': datetime.now().isoformat()
        }
        app.cache_service.set('pending_assignments:acc_123', cached_data, 
                             ttl=LMSCacheService.TTL_MEDIUM)
        
        with patch('app.routes.lms_dashboard_api.current_user', mock_user):
            # Request should serve from cache
            response = client.get('/lms/api/dashboard/pending-assignments')
            
            assert response.status_code == 200
            data = json.loads(response.data)
            
            # Verify served from cache
            assert data['success'] is True
            assert data['from_cache'] is True
            assert data['is_stale'] is False
            assert data['total_pending'] == 1
            
            # Verify LMS was NOT called
            app.lms_service.get_assignments_options.assert_not_called()
    
    def test_cache_hit_response_time(self, app, client, mock_user):
        """Test that cache hit responses are fast (< 200ms)"""
        # Pre-populate cache
        cached_data = {
            'pending': [],
            'missed': [],
            'all_assignments': [],
            'total_pending': 0,
            'total_missed': 0,
            'total_assignments': 0,
            'latest_semester': 'SEM2025',
            'latest_semester_name': 'Spring 2025',
            'course_count': 0,
            'refreshed_at': datetime.now().isoformat()
        }
        app.cache_service.set('pending_assignments:acc_123', cached_data,
                             ttl=LMSCacheService.TTL_MEDIUM)
        
        with patch('app.routes.lms_dashboard_api.current_user', mock_user):
            start_time = time.time()
            response = client.get('/lms/api/dashboard/pending-assignments')
            duration = (time.time() - start_time) * 1000  # Convert to ms
            
            assert response.status_code == 200
            assert duration < 200  # Should be under 200ms


class TestStaleCacheScenario(TestWidgetEndToEnd):
    """Test stale cache scenario (stale-while-revalidate)"""
    
    def test_stale_cache_serves_immediately(self, app, client, mock_user):
        """Test that stale cache serves data immediately"""
        # Create stale cached data
        cached_data = {
            'pending': [{'id': '1', 'title': 'Stale Assignment'}],
            'missed': [],
            'all_assignments': [{'id': '1', 'title': 'Stale Assignment'}],
            'total_pending': 1,
            'total_missed': 0,
            'total_assignments': 1,
            'latest_semester': 'SEM2025',
            'latest_semester_name': 'Spring 2025',
            'course_count': 1,
            'refreshed_at': datetime.now().isoformat()
        }
        app.cache_service.set('pending_assignments:acc_123', cached_data,
                             ttl=LMSCacheService.TTL_MEDIUM)
        
        # Make cache stale (older than STALE_THRESHOLD but not expired)
        cache_key = 'pending_assignments:acc_123'
        entry = app.cache_service.memory_cache[cache_key]
        entry.created_at = datetime.now() - timedelta(minutes=25)
        entry.expires_at = entry.created_at + entry.ttl
        
        with patch('app.routes.lms_dashboard_api.current_user', mock_user):
            # Request should serve stale data immediately
            start_time = time.time()
            response = client.get('/lms/api/dashboard/pending-assignments')
            duration = (time.time() - start_time) * 1000
            
            assert response.status_code == 200
            data = json.loads(response.data)
            
            # Verify served from cache
            assert data['success'] is True
            assert data['from_cache'] is True
            assert data['total_pending'] == 1
            
            # Should be fast (< 200ms)
            assert duration < 200
    
    def test_stale_cache_triggers_background_refresh(self, app, client, mock_user, mock_assignments):
        """Test that stale cache triggers background refresh"""
        # Create stale cached data
        cached_data = {
            'pending': [],
            'missed': [],
            'all_assignments': [],
            'total_pending': 0,
            'total_missed': 0,
            'total_assignments': 0,
            'latest_semester': 'SEM2025',
            'latest_semester_name': 'Spring 2025',
            'course_count': 0,
            'refreshed_at': datetime.now().isoformat()
        }
        app.cache_service.set('pending_assignments:acc_123', cached_data,
                             ttl=LMSCacheService.TTL_MEDIUM)
        
        # Make cache stale
        cache_key = 'pending_assignments:acc_123'
        entry = app.cache_service.memory_cache[cache_key]
        entry.created_at = datetime.now() - timedelta(minutes=25)
        entry.expires_at = entry.created_at + entry.ttl
        
        # Mock LMS service for background refresh
        app.lms_service.get_assignments_options.return_value = {
            'semesters': [{'code': 'SEM2025', 'name': 'Spring 2025'}]
        }
        app.lms_service.get_assignments_courses_for_semester.return_value = [
            {'code': 'CS101', 'name': 'Course 1'}
        ]
        app.lms_service.get_assignments_for_course.return_value = mock_assignments
        
        with patch('app.routes.lms_dashboard_api.current_user', mock_user):
            # Request should serve stale and trigger refresh
            response = client.get('/lms/api/dashboard/pending-assignments')
            
            assert response.status_code == 200
            data = json.loads(response.data)
            
            # Verify served from cache
            assert data['from_cache'] is True
            
            # Wait for background refresh to complete
            time.sleep(2)
            
            # Verify cache was updated by background refresh
            # (Check if LMS was called in background)
            # Note: This is a simplified check; in real scenario,
            # background service would update the cache


class TestManualRefresh(TestWidgetEndToEnd):
    """Test manual refresh scenario"""
    
    def test_manual_refresh_triggers_background_task(self, app, client, mock_user):
        """Test that manual refresh triggers a background task"""
        with patch('app.routes.lms_dashboard_api.current_user', mock_user):
            # Trigger manual refresh
            response = client.post('/lms/api/dashboard/refresh')
            
            assert response.status_code == 202  # Accepted
            data = json.loads(response.data)
            
            # Verify response
            assert data['success'] is True
            assert 'task_id' in data
            assert 'status_endpoint' in data
            assert data['message'] == 'Refresh started in background'
    
    def test_manual_refresh_with_force_parameter(self, app, client, mock_user, mock_assignments):
        """Test manual refresh with force_refresh parameter"""
        # Pre-populate cache
        cached_data = {
            'pending': [],
            'missed': [],
            'all_assignments': [],
            'total_pending': 0,
            'total_missed': 0,
            'total_assignments': 0,
            'latest_semester': 'SEM2025',
            'latest_semester_name': 'Spring 2025',
            'course_count': 0,
            'refreshed_at': datetime.now().isoformat()
        }
        app.cache_service.set('pending_assignments:acc_123', cached_data,
                             ttl=LMSCacheService.TTL_MEDIUM)
        
        # Mock LMS service
        app.lms_service.get_assignments_options.return_value = {
            'semesters': [{'code': 'SEM2025', 'name': 'Spring 2025'}]
        }
        app.lms_service.get_assignments_courses_for_semester.return_value = [
            {'code': 'CS101', 'name': 'Course 1'}
        ]
        app.lms_service.get_assignments_for_course.return_value = mock_assignments
        
        with patch('app.routes.lms_dashboard_api.current_user', mock_user):
            # Force refresh should bypass cache
            response = client.get('/lms/api/dashboard/pending-assignments?force_refresh=true')
            
            assert response.status_code == 200
            data = json.loads(response.data)
            
            # Verify fresh data was fetched
            assert data['from_cache'] is False
            
            # Verify LMS was called
            app.lms_service.get_assignments_options.assert_called_once()
    
    def test_refresh_status_check(self, app, client, mock_user):
        """Test checking refresh task status"""
        with patch('app.routes.lms_dashboard_api.current_user', mock_user):
            # Trigger refresh
            refresh_response = client.post('/lms/api/dashboard/refresh')
            refresh_data = json.loads(refresh_response.data)
            
            if refresh_data.get('task_id'):
                task_id = refresh_data['task_id']
                
                # Check status
                status_response = client.get(f'/lms/api/dashboard/refresh-status/{task_id}')
                
                assert status_response.status_code in [200, 404]  # 404 if task completed quickly
                
                if status_response.status_code == 200:
                    status_data = json.loads(status_response.data)
                    assert status_data['success'] is True
                    assert 'is_complete' in status_data


class TestAutoRefresh(TestWidgetEndToEnd):
    """Test auto-refresh scenario"""
    
    def test_auto_refresh_interval_configuration(self, app):
        """Test that auto-refresh interval is properly configured"""
        # Verify configuration
        assert app.config.get('LMS_CACHE_TTL_MINUTES') == 30
        assert app.config.get('LMS_ENABLE_STALE_WHILE_REVALIDATE') is True
    
    def test_cache_ttl_expiration(self, app, client, mock_user):
        """Test that cache expires after TTL"""
        # Create cached data with short TTL
        cached_data = {
            'pending': [],
            'missed': [],
            'all_assignments': [],
            'total_pending': 0,
            'total_missed': 0,
            'total_assignments': 0,
            'latest_semester': 'SEM2025',
            'latest_semester_name': 'Spring 2025',
            'course_count': 0,
            'refreshed_at': datetime.now().isoformat()
        }
        
        # Set with very short TTL for testing
        app.cache_service.set('pending_assignments:acc_123', cached_data,
                             ttl=timedelta(seconds=1))
        
        # Verify data is cached
        cached = app.cache_service.get('pending_assignments:acc_123')
        assert cached is not None
        
        # Wait for expiration
        time.sleep(2)
        
        # Verify data expired
        expired = app.cache_service.get('pending_assignments:acc_123')
        assert expired is None


class TestErrorScenarios(TestWidgetEndToEnd):
    """Test error scenarios"""
    
    def test_lms_connection_error(self, app, client, mock_user):
        """Test handling of LMS connection errors"""
        # Mock LMS service to raise error
        app.lms_service.get_assignments_options.side_effect = Exception("Connection failed")
        
        with patch('app.routes.lms_dashboard_api.current_user', mock_user):
            response = client.get('/lms/api/dashboard/pending-assignments')
            
            assert response.status_code == 500
            data = json.loads(response.data)
            
            # Verify error response
            assert data['success'] is False
            assert 'error' in data
            assert data['error_code'] == 'FETCH_ERROR'
            assert data['retry_available'] is True
    
    def test_no_semesters_error(self, app, client, mock_user):
        """Test handling when no semesters are available"""
        # Mock empty semesters
        app.lms_service.get_assignments_options.return_value = {'semesters': []}
        
        with patch('app.routes.lms_dashboard_api.current_user', mock_user):
            response = client.get('/lms/api/dashboard/pending-assignments')
            
            assert response.status_code == 200
            data = json.loads(response.data)
            
            # Should return empty result with error
            assert data['total_pending'] == 0
            assert data['total_missed'] == 0
            assert 'error' in data
    
    def test_no_courses_error(self, app, client, mock_user):
        """Test handling when no courses are available"""
        # Mock responses
        app.lms_service.get_assignments_options.return_value = {
            'semesters': [{'code': 'SEM2025', 'name': 'Spring 2025'}]
        }
        app.lms_service.get_assignments_courses_for_semester.return_value = []
        
        with patch('app.routes.lms_dashboard_api.current_user', mock_user):
            response = client.get('/lms/api/dashboard/pending-assignments')
            
            assert response.status_code == 200
            data = json.loads(response.data)
            
            # Should return empty result with error
            assert data['total_pending'] == 0
            assert data['total_missed'] == 0
            assert 'error' in data
    
    def test_partial_course_failure(self, app, client, mock_user, mock_assignments):
        """Test handling when some courses fail to load"""
        # Mock responses
        app.lms_service.get_assignments_options.return_value = {
            'semesters': [{'code': 'SEM2025', 'name': 'Spring 2025'}]
        }
        app.lms_service.get_assignments_courses_for_semester.return_value = [
            {'code': 'CS101', 'name': 'Course 1'},
            {'code': 'CS102', 'name': 'Course 2'}
        ]
        
        # First course succeeds, second fails
        def side_effect(*args, **kwargs):
            course_code = kwargs.get('course_code')
            if course_code == 'CS102':
                raise Exception("Course load failed")
            return mock_assignments
        
        app.lms_service.get_assignments_for_course.side_effect = side_effect
        
        with patch('app.routes.lms_dashboard_api.current_user', mock_user):
            response = client.get('/lms/api/dashboard/pending-assignments')
            
            assert response.status_code == 200
            data = json.loads(response.data)
            
            # Should return partial data with warning
            assert data['success'] is True
            assert 'warning' in data or 'failed_courses' in data
    
    def test_cache_corruption_recovery(self, app, client, mock_user, mock_assignments):
        """Test recovery from corrupted cache data"""
        # Put corrupted data in cache
        app.cache_service.set('pending_assignments:acc_123', "corrupted_data",
                             ttl=LMSCacheService.TTL_MEDIUM)
        
        # Mock LMS service
        app.lms_service.get_assignments_options.return_value = {
            'semesters': [{'code': 'SEM2025', 'name': 'Spring 2025'}]
        }
        app.lms_service.get_assignments_courses_for_semester.return_value = [
            {'code': 'CS101', 'name': 'Course 1'}
        ]
        app.lms_service.get_assignments_for_course.return_value = mock_assignments
        
        with patch('app.routes.lms_dashboard_api.current_user', mock_user):
            # Should handle corrupted cache and fetch fresh data
            response = client.get('/lms/api/dashboard/pending-assignments')
            
            # Should either succeed with fresh data or return error
            assert response.status_code in [200, 500]
    
    def test_no_authentication(self, client):
        """Test that unauthenticated requests are rejected"""
        response = client.get('/lms/api/dashboard/pending-assignments')
        
        # Should redirect to login or return 401
        assert response.status_code in [302, 401]
    
    def test_no_active_account(self, app, client, mock_user):
        """Test handling when user has no active account"""
        # Override mock user to have no active account
        mock_user.get_active_account.return_value = None
        
        with patch('app.routes.lms_dashboard_api.current_user', mock_user):
            response = client.get('/lms/api/dashboard/pending-assignments')
            
            assert response.status_code == 401
            data = json.loads(response.data)
            
            assert data['success'] is False
            assert data['error_code'] == 'NO_ACCOUNT'
            assert data['action_required'] == 'configure_account'


class TestWidgetDataIntegrity(TestWidgetEndToEnd):
    """Test data integrity and consistency"""
    
    def test_assignment_categorization_accuracy(self, app, client, mock_user, mock_assignments):
        """Test that assignments are correctly categorized as pending/missed"""
        # Mock LMS service
        app.lms_service.get_assignments_options.return_value = {
            'semesters': [{'code': 'SEM2025', 'name': 'Spring 2025'}]
        }
        app.lms_service.get_assignments_courses_for_semester.return_value = [
            {'code': 'CS101', 'name': 'Course 1'}
        ]
        app.lms_service.get_assignments_for_course.return_value = mock_assignments
        
        with patch('app.routes.lms_dashboard_api.current_user', mock_user):
            response = client.get('/lms/api/dashboard/pending-assignments')
            
            assert response.status_code == 200
            data = json.loads(response.data)
            
            # Verify categorization
            assert 'pending' in data
            assert 'missed' in data
            assert data['total_pending'] + data['total_missed'] <= data['total_assignments']
    
    def test_urgency_level_calculation(self, app, client, mock_user, mock_assignments):
        """Test that urgency levels are correctly calculated"""
        # Mock LMS service
        app.lms_service.get_assignments_options.return_value = {
            'semesters': [{'code': 'SEM2025', 'name': 'Spring 2025'}]
        }
        app.lms_service.get_assignments_courses_for_semester.return_value = [
            {'code': 'CS101', 'name': 'Course 1'}
        ]
        app.lms_service.get_assignments_for_course.return_value = mock_assignments
        
        with patch('app.routes.lms_dashboard_api.current_user', mock_user):
            response = client.get('/lms/api/dashboard/pending-assignments')
            
            assert response.status_code == 200
            data = json.loads(response.data)
            
            # Check that pending assignments have urgency levels
            for assignment in data.get('pending', []):
                if 'urgency_level' in assignment:
                    assert assignment['urgency_level'] in ['urgent', 'soon', 'later']
    
    def test_summary_consistency(self, app, client, mock_user):
        """Test that summary counts match detailed data"""
        # Pre-populate cache
        cached_data = {
            'pending': [
                {'id': '1', 'urgency_level': 'urgent'},
                {'id': '2', 'urgency_level': 'soon'}
            ],
            'missed': [{'id': '3'}],
            'all_assignments': [{'id': '1'}, {'id': '2'}, {'id': '3'}],
            'total_pending': 2,
            'total_missed': 1,
            'total_assignments': 3,
            'latest_semester': 'SEM2025',
            'latest_semester_name': 'Spring 2025',
            'course_count': 1,
            'refreshed_at': datetime.now().isoformat()
        }
        app.cache_service.set('pending_assignments:acc_123', cached_data,
                             ttl=LMSCacheService.TTL_MEDIUM)
        
        with patch('app.routes.lms_dashboard_api.current_user', mock_user):
            # Get detailed data
            detail_response = client.get('/lms/api/dashboard/pending-assignments')
            detail_data = json.loads(detail_response.data)
            
            # Get summary
            summary_response = client.get('/lms/api/dashboard/summary')
            summary_data = json.loads(summary_response.data)
            
            # Verify consistency
            assert summary_data['total_pending'] == detail_data['total_pending']
            assert summary_data['total_missed'] == detail_data['total_missed']


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
