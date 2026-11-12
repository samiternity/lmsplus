# app/routes/lms.py
from flask import Blueprint, render_template, session, flash, redirect, url_for, request, jsonify, Response, abort, copy_current_request_context
from flask_login import login_required, current_user
from werkzeug.utils import secure_filename
import logging
import os
import threading
import uuid
import urllib.parse
import re
from datetime import datetime, timedelta
from typing import Optional, Dict, Any
import json

from app.utils.proxy_transport import file_worker_origin, macro_endpoints, proxy_request
from app.models.group_manager import GroupManager
from app.services.lms_service import LMSService, LMSServiceError
from app.services.lms_scraper_service import CMSSurveyRequiredError
from app.services.lms_auth_service import LMSAuthenticationError, LMSSessionError
from app.services.lms_file_proxy_service import LMSFileError
from app.services.assignment_notification_service import get_assignment_notification_service
from app.utils.lms_error_handler import handle_lms_error, handle_route_error, create_error_context, ErrorInfo
from app.utils.lms_notifications import (
    create_notification, get_notifications, dismiss_notification, clear_notifications,
    update_progress, complete_progress, NotificationType, NotificationPriority
)
from app.utils.lms_logger import log_error, log_warning, log_info, log_debug, log_user_activity, log_security_event
from app.utils.session_auth import is_account_authenticated, add_authenticated_account
from app.utils.limiter import limiter
from app.config import get_config


# Configure logging
logger = logging.getLogger(__name__)

# Create LMS blueprint
lms_bp = Blueprint('lms', __name__, url_prefix='/lms')

# Initialize LMS service
lms_service = LMSService()
group_manager = GroupManager()
ASSIGNMENT_UPLOAD_MAX_SIZE = 15 * 1024 * 1024

def require_lms_authentication(f):
    """
    Decorator to ensure user has valid LMS authentication.
    Redirects to LMS setup if not authenticated (or returns JSON for API routes).
    """
    def decorated_function(*args, **kwargs):
        is_api = request.path.startswith(('/lms/api/', '/api/')) or request.is_json
        try:
            active_account = current_user.get_active_account()
            if not active_account:
                if is_api:
                    return jsonify({'success': False, 'error': 'No CMS account found. Please add a CMS account first.'}), 401
                flash('No CMS account found. Please add a CMS account first.', 'error')
                return redirect(url_for('main.dashboard'))
            
            # Check if CMS credentials exist (basic check)
            if not active_account.cms_username or not active_account.cms_password:
                if is_api:
                    return jsonify({'success': False, 'error': 'CMS credentials not configured. Please add a CMS account first.'}), 401
                flash('CMS credentials not configured. Please add a CMS account first.', 'warning')
                return redirect(url_for('main.dashboard'))
            
            # Check session authentication
            account_id = active_account.account_id
            if not is_account_authenticated(account_id):
                # SINGLE ACCOUNT MODE FIX: If account_id is in session, automatically authenticate it
                # This handles users who logged in before the authentication tracking was added
                if session.get('account_id') == account_id:
                    add_authenticated_account(account_id)
                else:
                    return jsonify({
                        'success': False,
                        'error': 'Account not authenticated in this session',
                        'requires_auth': True,
                        'account_id': account_id
                    }), 403
            
            # For pages that just show UI (like lecture notes with dropdowns),
            # we don't need to validate session upfront - it will be validated
            # when actual data is requested via API
            
            return f(*args, **kwargs)
        except Exception as e:
            logger.error(f"Error in LMS authentication check: {e}")
            if is_api:
                return jsonify({'success': False, 'error': 'Error checking authentication. Please try again.'}), 401
            flash('Error checking authentication. Please try again.', 'error')
            return redirect(url_for('main.dashboard'))
    
    decorated_function.__name__ = f.__name__
    return decorated_function

def handle_route_error(error: Exception, account_id: Optional[str] = None, 
                      operation: Optional[str] = None, default_message: str = "An error occurred") -> tuple:
    """
    Handle LMS route errors with comprehensive error handling and notifications.
    
    Args:
        error: Exception that occurred
        account_id: Account ID for context
        operation: Operation being performed
        default_message: Default error message
        
    Returns:
        Tuple of (error_message, status_code)
    """
    try:
        # Create error context
        context = create_error_context(
            account_id=account_id,
            operation=operation,
            user_agent=request.headers.get('User-Agent'),
            ip_address=request.remote_addr
        )
        
        # Handle error comprehensively
        error_info = handle_lms_error(error, context)
        
        # Create notification for user with recovery suggestions
        if account_id:
            if isinstance(error, LMSAuthenticationError):
                create_notification(account_id, 'auth_failed', 
                                  error_message=error_info.user_message,
                                  recovery_suggestions=error_info.recovery_suggestions)
            elif isinstance(error, LMSSessionError):
                create_notification(account_id, 'session_expired',
                                  recovery_suggestions=error_info.recovery_suggestions)
            elif isinstance(error, LMSFileError):
                create_notification(account_id, notification_type=NotificationType.ERROR,
                                  title="File Operation Failed", 
                                  message=error_info.user_message,
                                  priority=NotificationPriority.HIGH,
                                  actions=[
                                      {'label': 'Retry', 'action': 'retry_operation'},
                                      {'label': 'Help', 'action': 'show_help'}
                                  ])
            else:
                create_notification(account_id, notification_type=NotificationType.ERROR,
                                  title="Operation Failed", 
                                  message=error_info.user_message,
                                  priority=NotificationPriority.NORMAL,
                                  actions=[
                                      {'label': 'Retry', 'action': 'retry_operation'},
                                      {'label': 'Report Issue', 'action': 'report_issue'}
                                  ])
        
        # Determine HTTP status code
        if isinstance(error, (LMSAuthenticationError, LMSSessionError)):
            status_code = 401
        elif isinstance(error, LMSFileError):
            status_code = 400
        elif isinstance(error, LMSServiceError):
            status_code = 500
        else:
            status_code = 500
        
        return error_info.user_message, status_code
        
    except Exception as e:
        log_error(f"Error in error handler: {e}", account_id=account_id, operation=operation, exc_info=True)
        # Create fallback notification
        if account_id:
            create_notification(account_id, notification_type=NotificationType.ERROR,
                              title="System Error", 
                              message=default_message,
                              priority=NotificationPriority.HIGH)
        return default_message, 500

def get_active_account_id() -> Optional[str]:
    """
    Get the active account ID for the current user.
    
    Returns:
        Account ID or None if not found
    """
    try:
        active_account = current_user.get_active_account()
        return active_account.account_id if active_account else None
    except Exception as e:
        logger.error(f"Error getting active account ID: {e}")
        return None

def validate_request_params(required_params: list, request_data: dict) -> tuple:
    """
    Validate required parameters in request data.
    
    Args:
        required_params: List of required parameter names
        request_data: Request data dictionary
        
    Returns:
        Tuple of (is_valid, missing_params)
    """
    missing_params = [param for param in required_params if param not in request_data or not request_data[param]]
    return len(missing_params) == 0, missing_params

# Error handlers for the blueprint
@lms_bp.errorhandler(404)
def lms_not_found(error):
    """Handle 404 errors within LMS blueprint"""
    return render_template('errors/404.html', 
                         message="The requested LMS page was not found."), 404

@lms_bp.errorhandler(500)
def lms_internal_error(error):
    """Handle 500 errors within LMS blueprint"""
    logger.error(f"LMS internal error: {error}")
    return render_template('errors/500.html', 
                         message="An internal error occurred in the LMS system."), 500

@lms_bp.errorhandler(LMSServiceError)
def handle_lms_service_error(error):
    """Handle LMS service errors"""
    account_id = get_active_account_id()
    error_msg, status_code = handle_route_error(error, account_id, "lms_service_operation")
    if request.path.startswith(('/lms/api/', '/api/')) or request.is_json:
        return jsonify({'success': False, 'error': f"LMS Error: {error_msg}"}), status_code
    flash(f"LMS Error: {error_msg}", 'error')
    return redirect(url_for('lms.assignment_monitor'))

@lms_bp.errorhandler(LMSAuthenticationError)
def handle_lms_auth_error(error):
    """Handle LMS authentication errors"""
    account_id = get_active_account_id()
    error_msg, status_code = handle_route_error(error, account_id, "authentication")
    if request.path.startswith(('/lms/api/', '/api/')) or request.is_json:
        return jsonify({'success': False, 'error': error_msg}), status_code
    flash(error_msg, 'error')
    return redirect(url_for('main.dashboard'))

@lms_bp.errorhandler(LMSSessionError)
def handle_lms_session_error(error):
    """Handle LMS session errors"""
    account_id = get_active_account_id()
    error_msg, status_code = handle_route_error(error, account_id, "session_management")
    if request.path.startswith(('/lms/api/', '/api/')) or request.is_json:
        return jsonify({'success': False, 'error': error_msg}), status_code
    flash(error_msg, 'warning')
    return redirect(url_for('main.dashboard'))

# Context processor for LMS templates
@lms_bp.context_processor
def inject_lms_context():
    """Inject common LMS context into all templates"""
    # Check kill switch status globally
    kill_switch_active = False
    try:
        from flask import current_app
        scheduler = getattr(current_app, 'notification_scheduler', None)
        if scheduler:
            status = scheduler.get_kill_switch_status()
            kill_switch_active = status.get('active', False)
    except Exception as e:
        logger.error(f"Error checking kill switch in context processor: {e}")

    try:
        account_id = get_active_account_id()
        if account_id:
            # Get service status for navigation indicators
            service_status = lms_service.get_service_status(account_id)
            
            # Get notifications for the user
            notifications = get_notifications(account_id)
            
            # Count notifications by type
            notification_counts = {
                'total': len(notifications),
                'error': len([n for n in notifications if n.type == NotificationType.ERROR]),
                'warning': len([n for n in notifications if n.type == NotificationType.WARNING]),
                'success': len([n for n in notifications if n.type == NotificationType.SUCCESS]),
                'info': len([n for n in notifications if n.type == NotificationType.INFO]),
                'progress': len([n for n in notifications if n.type == NotificationType.PROGRESS])
            }
            
            return {
                'lms_authenticated': service_status.get('authentication', {}).get('is_valid', False),
                'lms_service_status': service_status.get('overall_status', 'unknown'),
                'lms_notifications': [n.to_dict() for n in notifications],
                'lms_notification_counts': notification_counts,
                'kill_switch_active': kill_switch_active
            }
    except Exception as e:
        log_warning(f"Error injecting LMS context: {e}")
    
    return {
        'lms_authenticated': False,
        'lms_service_status': 'unknown',
        'lms_notifications': [],
        'lms_notification_counts': {'total': 0, 'error': 0, 'warning': 0, 'success': 0, 'info': 0, 'progress': 0},
        'kill_switch_active': kill_switch_active
    }

# Route protection middleware
@lms_bp.before_request
def before_lms_request():
    """
    Execute before each LMS request to perform common checks.
    """
    # Skip authentication check for setup and public routes
    if request.endpoint in ['lms.authenticate', 'lms.test_connection']:
        return
    
    # Ensure user is logged in
    if not current_user.is_authenticated:
        if request.path.startswith(('/lms/api/', '/api/')) or request.is_json:
            return jsonify({'success': False, 'error': 'Authentication required. Please log in.'}), 401
        return redirect(url_for('auth.login'))
    
    # Update last_active_at (throttle to max once per hour to save Firestore writes)
    try:
        from datetime import datetime, timedelta, timezone
        now = datetime.now(timezone.utc)
        should_update = False
        
        if not hasattr(current_user, 'last_active_at') or not current_user.last_active_at:
            should_update = True
        else:
            last_active = datetime.fromisoformat(current_user.last_active_at.replace('Z', '+00:00'))
            if last_active.tzinfo is None:
                last_active = last_active.replace(tzinfo=timezone.utc)
            if (now - last_active) > timedelta(hours=1):
                should_update = True
                
        if should_update:
            current_user.last_active_at = now.isoformat()
            current_user.save()
    except Exception as e:
        logger.error(f"Failed to update last_active_at: {e}")
    
    # Log LMS access for monitoring
    logger.debug(f"LMS access: {request.endpoint} by user {current_user.id}")

# Health check route (no authentication required)
@lms_bp.route('/health')
def health_check():
    """
    Health check endpoint for LMS service.
    """
    try:
        # Basic service health check
        health_status = {
            'status': 'healthy',
            'timestamp': datetime.now().isoformat(),
            'service': 'lms',
            'version': '1.0.0'
        }
        
        # If user is authenticated, include account-specific status
        if current_user.is_authenticated:
            account_id = get_active_account_id()
            if account_id:
                try:
                    service_status = lms_service.get_service_status(account_id)
                    health_status['account_status'] = service_status.get('overall_status', 'unknown')
                except Exception as e:
                    health_status['account_status'] = 'error'
                    health_status['account_error'] = str(e)
        
        return jsonify(health_status), 200
        
    except Exception as e:
        logger.error(f"Health check error: {e}")
        return jsonify({
            'status': 'unhealthy',
            'error': str(e),
            'timestamp': datetime.now().isoformat()
        }), 500

# Dashboard and Data Display Routes

@lms_bp.route('/')
@lms_bp.route('/monitor')
@login_required
@require_lms_authentication
def assignment_monitor():
    """
    Main LMS dashboard with summary data.
    Displays pending assignments, recent lecture notes, and quick stats.
    Renders immediately and loads data via AJAX.
    """
    # Check if LMS setup is complete
    account_id = get_active_account_id()
    if not account_id:
        flash('No active account found.', 'error')
        return redirect(url_for('main.dashboard'))
    
    return render_template('lms_assignment_monitor.html', 
                         lms_connected=True, 
                         load_via_ajax=True)

@lms_bp.route('/api/dashboard/data')
@login_required
@require_lms_authentication
def get_dashboard_data():
    """
    API endpoint to get all dashboard data instantly from snapshot.
    """
    try:
        account_id = get_active_account_id()
        if not account_id:
            return jsonify({'error': 'No active account found'}), 400
        
        logger.info(f"Loading LMS dashboard data for account {account_id}")
        force_refresh = request.args.get('refresh', 'false').lower() == 'true'
        is_polling = request.args.get('poll', 'false').lower() == 'true'
        from app.utils.data_manager import data_manager
        
        if force_refresh:
            # Synchronous forced refresh
            try:
                from app.services.lms_dashboard_service import get_dashboard_service
                dashboard_service = get_dashboard_service()
                if dashboard_service:
                    dashboard_service._fetch_and_cache_assignments(account_id, f"pending_assignments:{account_id}")
                else:
                    lms_service.get_assignments(account_id, force_refresh=True)
            except Exception as e:
                logger.error(f"Error forced refreshing assignments: {e}")
            unified_snapshot = data_manager.get_unified_dashboard(account_id, bypass_cache=True)
            snapshot = unified_snapshot.get('assignments_snapshot', {}) if unified_snapshot else {}
            refresh_in_progress = False
        else:
            # Instant load from snapshot
            unified_snapshot = data_manager.get_unified_dashboard(account_id)
            snapshot = unified_snapshot.get('assignments_snapshot', {}) if unified_snapshot else {}
            refresh_in_progress = False

        if not snapshot:
            snapshot = {}
            
        # Handle both get_assignments schema and LMSBackgroundService schema
        if 'assignments' in snapshot:
            assignments_data = snapshot.get('assignments', [])
            snapshot_age = snapshot.get('last_updated')
        elif 'all_assignments' in snapshot:
            assignments_data = snapshot.get('all_assignments', [])
            snapshot_age = snapshot.get('refreshed_at')
        else:
            assignments_data = []
            snapshot_age = datetime.now().isoformat()
        from app.models.lms_models import LMSAssignment
        
        assignments = [LMSAssignment.from_dict(a) for a in assignments_data]
        pending_assignments = [a for a in assignments if a.is_pending()]
        pending_assignments.sort(key=lambda a: a.deadline or datetime.max)
        
        now = datetime.now()
        urgent, soon, later = [], [], []
        
        for assignment in pending_assignments:
            if assignment.deadline:
                time_until_due = assignment.deadline - now
                if time_until_due <= timedelta(hours=24):
                    urgent.append(assignment)
                elif time_until_due <= timedelta(days=3):
                    soon.append(assignment)
                else:
                    later.append(assignment)
            else:
                later.append(assignment)
                
        pending_summary = {
            'total_pending': len(pending_assignments),
            'urgent_count': len(urgent),
            'soon_count': len(soon),
            'later_count': len(later),
            'urgent_assignments': [a.to_dict() for a in urgent[:5]],
            'soon_assignments': [a.to_dict() for a in soon[:5]],
            'overdue_count': len([a for a in assignments if a.is_overdue()]),
            'last_updated': snapshot_age
        }
        
        recent_assignments = assignments[:10]
        serialized_assignments = [a.to_dict() for a in recent_assignments]
        
        stats = {
            'pending_assignments': pending_summary.get('total_pending', 0),
            'submitted_assignments': 0,
            'total_courses': 0,
            'lecture_notes': 0
        }
        
        recent_activity = []
        for assignment in recent_assignments:
            recent_activity.append({
                'type': 'assignment',
                'icon': 'fa-tasks',
                'description': f"New assignment: {assignment.title}",
                'time_ago': 'Recently',
                'date': datetime.now().isoformat()
            })
            
        try:
            service_status = lms_service.get_service_status(account_id)
            cache_info = lms_service.get_cache_info(account_id)
        except Exception as e:
            logger.error(f"Error getting service status/cache info: {e}")
            service_status = {'overall_status': 'error'}
            cache_info = {'cache_status': 'error'}

        dashboard_data = {
            'success': True,
            'stats': stats,
            'pending_summary': pending_summary,
            'recent_assignments': serialized_assignments,
            'recent_lecture_notes': [],
            'recent_activity': recent_activity[:5],
            'courses': [],
            'service_status': service_status,
            'cache_info': cache_info,
            'last_updated': datetime.now().isoformat(),
            'snapshot_age': snapshot_age,
            'refresh_in_progress': refresh_in_progress
        }
        
        return jsonify(dashboard_data)
        
    except Exception as e:
        logger.error(f"Dashboard data API error for account {account_id}: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@lms_bp.route('/monitoring')
@login_required
@require_lms_authentication
def monitoring():
    """
    LMS Cache and Background Service Monitoring Dashboard.
    Displays real-time statistics and performance metrics.
    """
    try:
        account_id = get_active_account_id()
        if not account_id:
            flash('No active account found.', 'error')
            return redirect(url_for('main.dashboard'))
        
        log_info(f"Loading LMS monitoring dashboard for account {account_id}",
                account_id=account_id, operation="view_monitoring")
        
        # Log user activity
        log_user_activity("view_monitoring_dashboard", account_id=account_id)
        
        return render_template('lms_monitoring.html')
        
    except Exception as e:
        error_msg, status_code = handle_route_error(e, account_id, "Failed to load monitoring dashboard")
        flash(error_msg, 'error')
        log_error(f"Monitoring dashboard error for account {account_id}: {e}",
                 error_code="MONITORING_DASHBOARD_ERROR",
                 account_id=account_id, exc_info=True)
        return render_template('lms_monitoring.html', error=error_msg), status_code


@lms_bp.route('/assignments')
@login_required
@require_lms_authentication
def assignments_new():
    """
    New assignments page with semester and course selection dropdowns.
    Page loads instantly - semester/course options are fetched via AJAX.
    """
    try:
        account_id = get_active_account_id()
        if not account_id:
            flash('No active account found.', 'error')
            return redirect(url_for('main.dashboard'))
        
        logger.info(f"Loading assignments page for account {account_id}")
        
        # Render page immediately - options will be loaded via AJAX
        return render_template('lms_assignments_new.html',
                             available_semesters=[],
                             available_courses=[],
                             load_via_ajax=True)
        
    except Exception as e:
        error_msg, status_code = handle_lms_error(e, "Failed to load assignments page")
        flash(error_msg, 'error')
        logger.error(f"Assignments page error: {e}")
        return render_template('lms_assignments_new.html', error=error_msg, lms_connection_error=True, lms_error_message=error_msg), status_code



@lms_bp.route('/lectures')
@login_required
@require_lms_authentication
def lecture_notes():
    """
    Lecture notes page with semester and course selection dropdowns.
    Page loads instantly - semester/course options are fetched via AJAX.
    """
    try:
        account_id = get_active_account_id()
        if not account_id:
            flash('No active account found.', 'error')
            return redirect(url_for('main.dashboard'))
        
        logger.info(f"Loading lecture notes page for account {account_id}")
        
        # Render page immediately - options will be loaded via AJAX
        return render_template('lms_lecture_notes_new.html',
                             available_semesters=[],
                             available_courses=[],
                             load_via_ajax=True,
                             lecture_download_origin=file_worker_origin(),
                             native_zip_enabled=os.environ.get("ZIP_NATIVE_DOWNLOADS", "false").lower() == "true")
        
    except Exception as e:
        account_id = get_active_account_id()
        error_msg, status_code = handle_route_error(e, account_id, "load_lecture_notes_page", "Failed to load lecture notes page")
        flash(error_msg, 'error')
        logger.error(f"Lecture notes page error: {e}")
        return render_template('lms_lecture_notes_new.html', error=error_msg, lms_connection_error=True, lms_error_message=error_msg), status_code

@lms_bp.route('/quizzes')
@login_required
@require_lms_authentication
def quizzes():
    if not get_active_account_id():
        flash('No active account found.', 'error')
        return redirect(url_for('main.dashboard'))
    return render_template('lms_quizzes.html', load_via_ajax=True)


def _quiz_error_response(error):
    if isinstance(error, CMSSurveyRequiredError):
        return jsonify(success=False, survey_required=True, error='CMS_SURVEY_REQUIRED',
                       message='Mandatory CMS surveys must be completed before LMS can be accessed.')
    logger.warning('Quiz request failed: %s', error, exc_info=True)
    status = 401 if isinstance(error, (LMSAuthenticationError, LMSSessionError)) else 502
    return jsonify(success=False, error=str(error)), status


@lms_bp.route('/api/quizzes/options')
@login_required
@require_lms_authentication
def get_quizzes_options():
    account_id = get_active_account_id()
    if not account_id:
        return jsonify(success=False, error='No active account found'), 400
    try:
        return jsonify(success=True, **lms_service.get_quizzes_options(account_id))
    except Exception as error:
        return _quiz_error_response(error)


@lms_bp.route('/api/quizzes/courses')
@login_required
@require_lms_authentication
def get_quizzes_courses():
    account_id = get_active_account_id()
    semester = request.args.get('semester')
    if not account_id or not semester:
        return jsonify(success=False, error='Active account and semester code are required'), 400
    try:
        return jsonify(success=True, courses=lms_service.get_quizzes_courses_for_semester(account_id, semester))
    except Exception as error:
        return _quiz_error_response(error)


@lms_bp.route('/api/quizzes/data')
@lms_bp.route('/api/quizzes/fetch')
@login_required
@require_lms_authentication
@limiter.limit("10 per minute, 60 per hour")
def get_quizzes_data():
    account_id = get_active_account_id()
    semester, course = request.args.get('semester'), request.args.get('course')
    if not account_id or not semester or not course:
        return jsonify(success=False, error='Active account, semester and course codes are required'), 400
    force_refresh = request.args.get('refresh', 'false').lower() == 'true'
    try:
        rows = None if force_refresh else lms_service._get_cached_quizzes(account_id, semester, course)
        from_cache, is_stale, task_id = rows is not None, False, None
        if rows is None and not force_refresh:
            rows = lms_service._get_cached_quizzes(account_id, semester, course, allow_stale=True)
            if rows is not None:
                from_cache, is_stale = True, True
                task_id = str(uuid.uuid4())
                # Copy the request context so authentication and persistent cache writes
                # use the requesting account, even after the response has been sent.
                @copy_current_request_context
                def refresh_quizzes():
                    result = {'done': True, 'data': None, 'error': None, 'account_id': account_id}
                    try:
                        fresh = lms_service.get_quizzes_for_course(account_id, semester, course, force_refresh=True)
                        result['data'] = [quiz.to_dict() for quiz in fresh]
                    except CMSSurveyRequiredError:
                        result.update(error='CMS_SURVEY_REQUIRED', survey_required=True)
                    except Exception as error:
                        logger.warning('Quiz background refresh failed: %s', error, exc_info=True)
                        result['error'] = 'Could not refresh quizzes. Please retry.'
                    with _refresh_tasks_lock:
                        # Expired tasks may already have been removed.
                        if task_id in _refresh_tasks:
                            result['expires_at'] = _refresh_tasks[task_id]['expires_at']
                            _refresh_tasks[task_id] = result
                with _refresh_tasks_lock:
                    now = datetime.now().timestamp()
                    for key, task in list(_refresh_tasks.items()):
                        if task.get('expires_at', float('inf')) < now:
                            _refresh_tasks.pop(key, None)
                    _refresh_tasks[task_id] = dict(done=False, data=None, error=None, account_id=account_id,
                                                  expires_at=now + 300)
                threading.Thread(target=refresh_quizzes, daemon=True).start()
        if rows is None:
            rows = lms_service.get_quizzes_for_course(account_id, semester, course, force_refresh=True)
        data = [quiz.to_dict() for quiz in sorted(rows, key=lambda quiz: quiz.quiz_no)]
        return jsonify(success=True, quizzes=data, count=len(data), from_cache=from_cache,
                       is_stale=is_stale, task_id=task_id)
    except Exception as error:
        return _quiz_error_response(error)


@lms_bp.route('/api/assignments/options')
@login_required
@require_lms_authentication
def get_assignments_options():
    """
    API endpoint to get available semesters and courses for assignments.
    Returns the dropdown options from the LMS page.
    """
    try:
        account_id = get_active_account_id()
        if not account_id:
            return jsonify({'success': False, 'error': 'No active account found'}), 400
        
        logger.info(f"Getting assignments options for account {account_id}")
        
        options = lms_service.get_assignments_options(account_id)
        
        return jsonify({
            'success': True,
            'semesters': options.get('semesters', []),
            'courses': options.get('courses', [])
        })
        
    except CMSSurveyRequiredError as se:
        logger.warning(f"LMS assignments options blocked by CMS survey for account {account_id}: {se}")
        return jsonify({
            'success': False,
            'survey_required': True,
            'error': 'CMS_SURVEY_REQUIRED',
            'message': 'Mandatory CMS surveys must be completed before LMS can be accessed.'
        }), 200
    except Exception as e:
        logger.error(f"Error getting assignments options: {e}")
        return jsonify({
            'success': False,
            'error': 'The LMS server is currently unreachable. Please check your connection and try again.'
        }), 500

@lms_bp.route('/api/lecture-notes/options')
@login_required
@require_lms_authentication
def get_lecture_notes_options():
    """
    API endpoint to get available semesters and courses for lecture notes.
    Returns the dropdown options from the LMS page.
    """
    try:
        account_id = get_active_account_id()
        if not account_id:
            return jsonify({'error': 'No active account found'}), 400
        
        logger.info(f"Getting lecture notes options for account {account_id}")
        
        options = lms_service.get_lecture_notes_options(account_id)
        
        return jsonify({
            'success': True,
            'semesters': options.get('semesters', []),
            'courses': options.get('courses', [])
        })
        
    except CMSSurveyRequiredError as se:
        logger.warning(f"LMS lecture notes options blocked by CMS survey for account {account_id}: {se}")
        return jsonify({
            'success': False,
            'survey_required': True,
            'error': 'CMS_SURVEY_REQUIRED',
            'message': 'Mandatory CMS surveys must be completed before LMS can be accessed.'
        }), 200
    except Exception as e:
        logger.error(f"Error getting lecture notes options: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@lms_bp.route('/api/lecture-notes/courses')
@login_required
@require_lms_authentication
def get_lecture_notes_courses():
    """
    API endpoint to get available courses for a specific semester.
    Returns the course dropdown options for the selected semester.
    """
    try:
        account_id = get_active_account_id()
        if not account_id:
            return jsonify({'error': 'No active account found'}), 400
        
        semester_code = request.args.get('semester')
        if not semester_code:
            return jsonify({'error': 'Semester code is required'}), 400
        
        logger.info(f"Getting courses for semester {semester_code}, account {account_id}")
        
        # Get courses for the specific semester
        courses = lms_service.get_lecture_notes_courses_for_semester(account_id, semester_code)
        
        return jsonify({
            'success': True,
            'courses': courses
        })
        
    except Exception as e:
        logger.error(f"Error getting courses for semester: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@lms_bp.route('/api/assignments/courses')
@login_required
@require_lms_authentication
def get_assignments_courses():
    """
    API endpoint to get available courses for a specific semester (for assignments).
    Returns the course dropdown options for the selected semester.
    """
    try:
        account_id = get_active_account_id()
        if not account_id:
            return jsonify({'error': 'No active account found'}), 400
        
        semester_code = request.args.get('semester')
        if not semester_code:
            return jsonify({'error': 'Semester code is required'}), 400
        
        logger.info(f"Getting courses for assignments, semester {semester_code}, account {account_id}")
        
        # Get courses for the specific semester (reuse lecture notes method)
        courses = lms_service.get_assignments_courses_for_semester(account_id, semester_code)
        
        return jsonify({
            'success': True,
            'courses': courses
        })
        
    except Exception as e:
        logger.error(f"Error getting courses for semester: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@lms_bp.route('/api/cms/check-surveys')
@login_required
def check_cms_surveys():
    """
    API endpoint to check if there are pending CMS surveys.
    """
    try:
        account = current_user.get_active_account()
        if not account:
            return jsonify({'success': False, 'error': 'No active account found'}), 400
        
        from app.utils.enhanced_scraper import scraper_manager
        scraper = scraper_manager.get_scraper(account)
        
        surveys = scraper.check_for_pending_surveys()
        
        return jsonify({
            'success': True,
            'has_surveys': len(surveys) > 0,
            'surveys': surveys
        })
    except Exception as e:
        logger.error(f"Error checking CMS surveys: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

_survey_tasks: Dict[str, Dict[str, Any]] = {}
_survey_tasks_lock = threading.Lock()

def _run_autofill_task(task_id: str, account_id: str, username: str, password: str, institute_id: str = "2"):
    """
    Background worker executing atomic autofill surveys macro.
    Attempts Cloudflare Worker (Attempt 1), falls back to Supabase (Attempt 2).
    Saves session cookies and updates task progress.
    """
    import requests
    from app.services.lms_session_manager import LMSSessionManager

    endpoints = macro_endpoints(75, 90)

    payload = {
        "action": "autofill_surveys",
        "username": username,
        "password": password,
        "institute_id": str(institute_id or "2")
    }

    last_error = None
    for tier_name, macro_url, timeout in endpoints:
        try:
            logger.info(f"[{account_id}] Starting atomic autofill_surveys via {tier_name} ({macro_url})")
            with _survey_tasks_lock:
                if task_id in _survey_tasks:
                    _survey_tasks[task_id].update({
                        "status": "running",
                        "progress": 35 if tier_name == "Cloudflare" else 65,
                        "message": "Auto-filling course surveys...",
                        "tier": tier_name
                    })

            resp = proxy_request(requests.post, macro_url, json=payload, timeout=timeout)
            if resp.status_code == 200:
                data = resp.json()
                if data.get("success"):
                    # Extract and save cookies
                    cookies_dict = {}
                    cookie_raw = data.get("cookies") or data.get("cookie_header")
                    if cookie_raw:
                        delim = "|||" if "|||" in str(cookie_raw) else ";"
                        for item in str(cookie_raw).split(delim):
                            clean_item = item.split(";")[0].strip()
                            if "=" in clean_item:
                                k, v = clean_item.split("=", 1)
                                if k.strip():
                                    cookies_dict[k.strip()] = v.strip()

                    if cookies_dict:
                        try:
                            lms_mgr = LMSSessionManager()
                            lms_mgr.store_session_cookies(account_id, cookies_dict, session_type="combined")
                            logger.info(f"[{account_id}] Stored fresh session cookies from {tier_name} macro")
                        except Exception as ce:
                            logger.warning(f"[{account_id}] Failed to store session cookies: {ce}")

                    # Invalidate cached LMS sessions & memory caches
                    try:
                        lms_service.scraper_service.invalidate_session_cache(account_id)
                        lms_service.invalidate_cache(account_id)
                    except Exception as ie:
                        logger.warning(f"[{account_id}] Failed to invalidate cache: {ie}")

                    courses = data.get("courses", [])
                    total_evaluated = data.get("surveys_filled", data.get("total_evaluated", len(courses)))

                    with _survey_tasks_lock:
                        if task_id in _survey_tasks:
                            msg = f"Completed {total_evaluated} course surveys. LMS access restored!" if total_evaluated > 0 else "No pending surveys. LMS access restored!"
                            _survey_tasks[task_id].update({
                                "status": "completed",
                                "progress": 100,
                                "total": total_evaluated,
                                "completed": total_evaluated,
                                "courses": courses,
                                "message": msg,
                                "completed_at": datetime.now().isoformat()
                            })
                    logger.info(f"[{account_id}] Autofill completed successfully via {tier_name}: {total_evaluated} courses evaluated")
                    return
                else:
                    last_error = data.get("error") or f"{tier_name} reported failure"
                    logger.warning(f"[{account_id}] {tier_name} autofill failed: {last_error}")
            else:
                last_error = f"{tier_name} HTTP {resp.status_code}"
                logger.warning(f"[{account_id}] {tier_name} responded with status {resp.status_code}")
        except Exception as te:
            last_error = f"{tier_name} request failed: {te}"
            logger.warning(f"[{account_id}] {tier_name} macro exception: {te}")

    # If all tiers failed
    logger.error(f"[{account_id}] All autofill attempts failed. Last error: {last_error}")
    with _survey_tasks_lock:
        if task_id in _survey_tasks:
            _survey_tasks[task_id].update({
                "status": "failed",
                "progress": 100,
                "error": last_error or "All worker attempts failed",
                "message": f"Auto-fill failed: {last_error}",
                "failed_at": datetime.now().isoformat()
            })

@lms_bp.route('/api/cms/auto-fill-surveys', methods=['POST'])
@login_required
@limiter.limit("5 per minute, 20 per hour")
def auto_fill_surveys():
    """
    Launch atomic autofill surveys worker macro in background thread.
    Returns task_id for progress polling.
    """
    try:
        account = current_user.get_active_account()
        if not account:
            return jsonify({'success': False, 'error': 'No active account found'}), 400

        task_id = str(uuid.uuid4())
        account_inst = getattr(account, 'institute_id', None)
        if not account_inst or str(account_inst).strip() == '2':
            if getattr(account, 'cms_username', '').strip().startswith('05-'):
                institute_id = '5'
            else:
                institute_id = '2'
        else:
            institute_id = str(account_inst)

        with _survey_tasks_lock:
            # Purge stale tasks (>1 hour old)
            now = datetime.now()
            expired = [
                tid for tid, tdata in _survey_tasks.items()
                if "created_at" in tdata and (now - datetime.fromisoformat(tdata["created_at"])).total_seconds() > 3600
            ]
            for exp_tid in expired:
                _survey_tasks.pop(exp_tid, None)

            _survey_tasks[task_id] = {
                "task_id": task_id,
                "account_id": account.account_id,
                "status": "pending",
                "progress": 15,
                "message": "Starting auto-fill...",
                "created_at": datetime.now().isoformat()
            }

        worker_thread = threading.Thread(
            target=_run_autofill_task,
            args=(task_id, account.account_id, account.cms_username, account.cms_password, institute_id),
            daemon=True
        )
        worker_thread.start()

        return jsonify({
            'success': True,
            'task_id': task_id,
            'message': 'Autofill task started'
        })
    except Exception as e:
        logger.error(f'Error starting auto_fill_surveys: {e}', exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500

@lms_bp.route('/api/cms/survey-progress', methods=['GET'])
@login_required
def survey_progress():
    """Poll survey auto-fill background task progress."""
    task_id = request.args.get('task_id')
    if not task_id:
        return jsonify({'success': False, 'error': 'task_id is required'}), 400

    with _survey_tasks_lock:
        task = _survey_tasks.get(task_id)

    if not task:
        return jsonify({'success': False, 'error': 'Task not found'}), 404

    return jsonify({
        'success': True,
        'task': task
    })


@lms_bp.route('/api/assignments/fetch')
@login_required
@require_lms_authentication
@limiter.limit("10 per minute, 60 per hour")
def fetch_assignments():
    """
    API endpoint to fetch assignments for a specific semester and course.
    Mimics selecting a course from the dropdown on the original LMS site.
    """
    try:
        account_id = get_active_account_id()
        if not account_id:
            return jsonify({'error': 'No active account found'}), 400
        
        # Get parameters
        semester_code = request.args.get('semester')
        course_code = request.args.get('course')
        force_refresh = request.args.get('refresh', 'false').lower() == 'true'
        
        if not course_code:
            return jsonify({'error': 'Course code is required'}), 400
        
        logger.info(f"Fetching assignments for account {account_id}, semester: {semester_code}, course: {course_code}")
        
        # Fetch assignments for the specific course
        assignments_list = lms_service.get_assignments_for_course(
            account_id,
            semester_code=semester_code,
            course_code=course_code,
            force_refresh=force_refresh
        )
        
        # Sort by assignment number
        assignments_list.sort(key=lambda a: a.assignment_no or 0)
        
        # DEBUG: Log before conversion
        logger.info(f"DEBUG: Converting {len(assignments_list)} assignments to dict")
        if assignments_list:
            first = assignments_list[0]
            logger.info(f"DEBUG: First assignment object - title={first.title}, deadline={first.deadline}, submission_file={first.submission_file}, solution_file={first.solution_file}, remarks={first.remarks}")
        
        # Convert to dict
        assignments_data = [a.to_dict() if hasattr(a, 'to_dict') else a for a in assignments_list]
        
        # DEBUG: Log first assignment data
        if assignments_data:
            logger.info(f"DEBUG: First assignment dict keys: {assignments_data[0].keys()}")
            logger.info(f"DEBUG: First assignment deadline: {assignments_data[0].get('deadline')}")
            logger.info(f"DEBUG: First assignment submission_file: {assignments_data[0].get('submission_file')}")
            logger.info(f"DEBUG: First assignment solution_file: {assignments_data[0].get('solution_file')}")
            logger.info(f"DEBUG: First assignment remarks: {assignments_data[0].get('remarks')}")
            
            # Count how many have solution files
            with_solution = sum(1 for a in assignments_data if a.get('solution_file'))
            logger.info(f"DEBUG: {with_solution} out of {len(assignments_data)} assignments have solution_file")
        
        return jsonify({
            'success': True,
            'assignments': assignments_data,
            'count': len(assignments_data)
        })
        
    except Exception as e:
        logger.error(f"Error fetching assignments: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@lms_bp.route('/api/lecture-notes/fetch')
@login_required
@require_lms_authentication
@limiter.limit("2 per minute, 10 per hour")
def fetch_lecture_notes():
    """
    API endpoint to fetch lecture notes for a specific semester and course.
    Mimics selecting a course from the dropdown on the original LMS site.
    """
    try:
        account_id = get_active_account_id()
        if not account_id:
            return jsonify({'error': 'No active account found'}), 400
        
        # Get parameters
        semester_code = request.args.get('semester')
        course_code = request.args.get('course')
        force_refresh = request.args.get('refresh', 'false').lower() == 'true'
        
        if not course_code:
            return jsonify({'error': 'Course code is required'}), 400
        
        logger.info(f"Fetching lecture notes for account {account_id}, semester: {semester_code}, course: {course_code}")
        
        # Fetch lecture notes for the specific course
        lecture_notes_list = lms_service.get_lecture_notes_for_course(
            account_id,
            semester_code=semester_code,
            course_code=course_code,
            force_refresh=force_refresh
        )
        
        # Sort by week number
        lecture_notes_list.sort(key=lambda n: n.week_no or 0)
        
        # Convert to dict
        notes_data = [n.to_dict() if hasattr(n, 'to_dict') else n for n in lecture_notes_list]
        
        return jsonify({
            'success': True,
            'lecture_notes': notes_data,
            'count': len(notes_data)
        })
        
    except Exception as e:
        logger.error(f"Error fetching lecture notes: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

# ---------------------------------------------------------------------------
# Stale-while-revalidate task store (in-process, per-worker)
# task_id -> {'done': bool, 'data': list|None, 'error': str|None}
# ---------------------------------------------------------------------------
_refresh_tasks: Dict[str, Dict] = {}
_refresh_tasks_lock = threading.Lock()


def _run_assignments_refresh(task_id: str, account_id: str,
                              semester_code: Optional[str], course_code: str):
    """Background thread: scrape fresh assignments and store result in task store.
    
    Calls the scraper directly (bypassing _ensure_valid_session) because the
    session was pre-warmed in the request context before this thread was started.
    _get_or_create_session will find the cached session without needing current_user.
    """
    try:
        assignments_list = lms_service.scraper_service.scrape_course_assignments(
            account_id, semester_code, course_code
        )
        assignments_list.sort(key=lambda a: a.assignment_no or 0)
        data = [a.to_dict() if hasattr(a, 'to_dict') else a for a in assignments_list]
        # Also update the service-level cache so subsequent requests get fresh data
        if assignments_list:
            cache_key = f"{semester_code}:{course_code}" if semester_code else course_code
            lms_service._cache_assignments(account_id, assignments_list, cache_key, None)
        with _refresh_tasks_lock:
            _refresh_tasks[task_id] = {'done': True, 'data': data, 'error': None}
    except Exception as e:
        logger.error(f"Background assignments refresh failed (task {task_id}): {e}", exc_info=True)
        with _refresh_tasks_lock:
            _refresh_tasks[task_id] = {'done': True, 'data': None, 'error': str(e)}


def _run_lecture_notes_refresh(task_id: str, account_id: str,
                                semester_code: Optional[str], course_code: str):
    """Background thread: scrape fresh lecture notes and store result in task store.
    
    Same rationale as _run_assignments_refresh — calls scraper directly.
    """
    try:
        notes_list = lms_service.scraper_service.scrape_course_lecture_notes(
            account_id, semester_code, course_code
        )
        notes_list.sort(key=lambda n: n.week_no or 0)
        data = [n.to_dict() if hasattr(n, 'to_dict') else n for n in notes_list]
        # Also update the service-level cache
        if notes_list:
            cache_key = f"{semester_code}:{course_code}" if semester_code else course_code
            lms_service._cache_lecture_notes(account_id, notes_list, cache_key, None)
        with _refresh_tasks_lock:
            _refresh_tasks[task_id] = {'done': True, 'data': data, 'error': None}
    except Exception as e:
        logger.error(f"Background lecture notes refresh failed (task {task_id}): {e}", exc_info=True)
        with _refresh_tasks_lock:
            _refresh_tasks[task_id] = {'done': True, 'data': None, 'error': str(e)}


@lms_bp.route('/api/assignments/data')
@login_required
@require_lms_authentication
def get_assignments_data():
    """
    Stale-while-revalidate endpoint for assignments.

    - Fresh cache hit  → return immediately, no background task
    - Stale cache hit  → return stale data immediately, fire background refresh
    - No cache at all  → scrape synchronously (first load; current_user is available here)
    """
    try:
        account_id = get_active_account_id()
        if not account_id:
            return jsonify({'error': 'No active account found'}), 400

        semester_code = request.args.get('semester')
        course_code = request.args.get('course')
        if not course_code:
            return jsonify({'error': 'Course code is required'}), 400

        combined_key = f"{semester_code}:{course_code}" if semester_code else course_code

        # 1. Try fresh cache
        cached = lms_service._get_cached_assignments(account_id, combined_key, None, allow_stale=False)
        if cached is not None:
            data = [a.to_dict() if hasattr(a, 'to_dict') else a for a in cached]
            data.sort(key=lambda a: a.get('assignment_no') or 0)
            return jsonify({'success': True, 'assignments': data, 'count': len(data),
                            'from_cache': True, 'is_stale': False, 'task_id': None})

        # 2. Try stale cache — serve it and kick off a background refresh
        stale = lms_service._get_cached_assignments(account_id, combined_key, None, allow_stale=True)
        if stale:
            # Pre-warm session in request context, then fire background thread
            try:
                lms_service.scraper_service._get_or_create_session(account_id)
            except Exception as _sess_err:
                logger.warning(f"Session pre-warm failed: {_sess_err}")

            task_id = str(uuid.uuid4())
            with _refresh_tasks_lock:
                _refresh_tasks[task_id] = {'done': False, 'data': None, 'error': None}
            t = threading.Thread(
                target=_run_assignments_refresh,
                args=(task_id, account_id, semester_code, course_code),
                daemon=True
            )
            t.start()

            data = [a.to_dict() if hasattr(a, 'to_dict') else a for a in stale]
            data.sort(key=lambda a: a.get('assignment_no') or 0)
            return jsonify({'success': True, 'assignments': data, 'count': len(data),
                            'from_cache': True, 'is_stale': True, 'task_id': task_id})

        # 3. No cache at all — scrape synchronously so current_user is available
        logger.info(f"No cache for assignments {combined_key}, scraping synchronously")
        try:
            assignments_list = lms_service.get_assignments_for_course(
                account_id, semester_code=semester_code, course_code=course_code, force_refresh=True
            )
        except Exception as scrape_err:
            logger.error(f"Synchronous assignments scrape failed: {scrape_err}", exc_info=True)
            return jsonify({'success': False, 'error': str(scrape_err)}), 500

        assignments_list.sort(key=lambda a: a.assignment_no or 0)
        data = [a.to_dict() if hasattr(a, 'to_dict') else a for a in assignments_list]
        return jsonify({'success': True, 'assignments': data, 'count': len(data),
                        'from_cache': False, 'is_stale': False, 'task_id': None})

    except CMSSurveyRequiredError as se:
        logger.warning(f"LMS assignments fetch blocked by CMS survey: {se}")
        return jsonify({
            'success': False,
            'survey_required': True,
            'error': 'CMS_SURVEY_REQUIRED',
            'message': 'Mandatory CMS surveys must be completed before LMS can be accessed.'
        }), 200
    except Exception as e:
        logger.error(f"Error in get_assignments_data: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@lms_bp.route('/api/lecture-notes/data')
@login_required
@require_lms_authentication
def get_lecture_notes_data():
    """
    Stale-while-revalidate endpoint for lecture notes.
    Same pattern as /api/assignments/data.
    """
    try:
        account_id = get_active_account_id()
        if not account_id:
            return jsonify({'error': 'No active account found'}), 400

        semester_code = request.args.get('semester')
        course_code = request.args.get('course')
        if not course_code:
            return jsonify({'error': 'Course code is required'}), 400

        combined_key = f"{semester_code}:{course_code}" if semester_code else course_code

        # 1. Try fresh cache
        cached = lms_service._get_cached_lecture_notes(account_id, combined_key, None, allow_stale=False)
        if cached is not None:
            data = [n.to_dict() if hasattr(n, 'to_dict') else n for n in cached]
            data.sort(key=lambda n: n.get('week_no') or 0)
            return jsonify({'success': True, 'lecture_notes': data, 'count': len(data),
                            'from_cache': True, 'is_stale': False, 'task_id': None})

        # 2. Try stale cache — serve it and kick off a background refresh
        stale = lms_service._get_cached_lecture_notes(account_id, combined_key, None, allow_stale=True)
        if stale:
            try:
                lms_service.scraper_service._get_or_create_session(account_id)
            except Exception as _sess_err:
                logger.warning(f"Session pre-warm failed: {_sess_err}")

            task_id = str(uuid.uuid4())
            with _refresh_tasks_lock:
                _refresh_tasks[task_id] = {'done': False, 'data': None, 'error': None}
            t = threading.Thread(
                target=_run_lecture_notes_refresh,
                args=(task_id, account_id, semester_code, course_code),
                daemon=True
            )
            t.start()

            data = [n.to_dict() if hasattr(n, 'to_dict') else n for n in stale]
            data.sort(key=lambda n: n.get('week_no') or 0)
            return jsonify({'success': True, 'lecture_notes': data, 'count': len(data),
                            'from_cache': True, 'is_stale': True, 'task_id': task_id})

        # 3. No cache at all — scrape synchronously
        logger.info(f"No cache for lecture notes {combined_key}, scraping synchronously")
        try:
            notes_list = lms_service.get_lecture_notes_for_course(
                account_id, semester_code=semester_code, course_code=course_code, force_refresh=True
            )
        except Exception as scrape_err:
            logger.error(f"Synchronous lecture notes scrape failed: {scrape_err}", exc_info=True)
            return jsonify({'success': False, 'error': str(scrape_err)}), 500

        notes_list.sort(key=lambda n: n.week_no or 0)
        data = [n.to_dict() if hasattr(n, 'to_dict') else n for n in notes_list]
        return jsonify({'success': True, 'lecture_notes': data, 'count': len(data),
                        'from_cache': False, 'is_stale': False, 'task_id': None})

    except CMSSurveyRequiredError as se:
        logger.warning(f"LMS lecture notes fetch blocked by CMS survey: {se}")
        return jsonify({
            'success': False,
            'survey_required': True,
            'error': 'CMS_SURVEY_REQUIRED',
            'message': 'Mandatory CMS surveys must be completed before LMS can be accessed.'
        }), 200
    except Exception as e:
        logger.error(f"Error in get_lecture_notes_data: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@lms_bp.route('/api/refresh-status')
@login_required
@limiter.limit("60 per minute")
def get_refresh_status():
    """
    Poll endpoint for background refresh tasks.
    Returns {'done': bool, 'data': list|None, 'error': str|None}
    """
    # Check session authentication
    account_id = get_active_account_id()
    if account_id and not is_account_authenticated(account_id):
        return jsonify({
            'error': 'Account not authenticated in this session',
            'requires_auth': True,
            'account_id': account_id
        }), 403
    
    task_id = request.args.get('task_id')
    if not task_id:
        return jsonify({'error': 'task_id is required'}), 400

    with _refresh_tasks_lock:
        task = _refresh_tasks.get(task_id)

    if task is None:
        return jsonify({'error': 'Task not found'}), 404

    if task.get('account_id') and (task['account_id'] != account_id or
                                    task.get('expires_at', float('inf')) < datetime.now().timestamp()):
        return jsonify({'error': 'Task not found'}), 404

    # Clean up completed tasks after they've been read
    if task['done']:
        with _refresh_tasks_lock:
            _refresh_tasks.pop(task_id, None)

    return jsonify({key: value for key, value in task.items() if key not in ('account_id', 'expires_at')})


@lms_bp.route('/refresh')
@login_required
@require_lms_authentication
def refresh_data():
    """
    Force refresh all LMS data and redirect back to referring page.
    """
    try:
        account_id = get_active_account_id()
        if not account_id:
            flash('No active account found.', 'error')
            return redirect(url_for('main.dashboard'))
        
        log_info(f"Force refreshing LMS data for account {account_id}",
                account_id=account_id, operation="refresh_data")
        
        # Create progress notification with detailed steps
        progress_notification = create_notification(
            account_id, 
            'refresh_started',
            steps=['Connecting to LMS', 'Refreshing assignments', 'Refreshing lecture notes', 'Updating cache']
        )
        
        # Perform data refresh with progress updates
        try:
            # Update progress: Connecting
            update_progress(account_id, progress_notification.id, 25, 'Connecting to LMS...')
            
            refresh_result = lms_service.refresh_lms_data(account_id)
            
            # Update progress: Processing
            update_progress(account_id, progress_notification.id, 75, 'Processing data...')
            
            if refresh_result.get('success'):
                assignments_count = refresh_result.get('assignments_count', 0)
                lecture_notes_count = refresh_result.get('lecture_notes_count', 0)
                
                # Complete progress notification with success
                complete_progress(account_id, progress_notification.id, True,
                                f'Successfully refreshed {assignments_count} assignments and {lecture_notes_count} lecture notes')
                
                # Create success notification
                create_notification(account_id, 'refresh_success',
                                  assignments_count=assignments_count,
                                  lecture_notes_count=lecture_notes_count)
                
                flash(f'LMS data refreshed successfully! Updated {assignments_count} assignments and {lecture_notes_count} lecture notes.', 'success')
                
                # Log user activity
                log_user_activity("refresh_data", account_id=account_id, 
                                assignments_count=assignments_count, lecture_notes_count=lecture_notes_count)
            else:
                errors = refresh_result.get('errors', [])
                error_count = len(errors)
                error_msg = '; '.join(errors) if errors else 'Unknown error occurred'
                
                # Complete progress notification with partial success
                complete_progress(account_id, progress_notification.id, False,
                                f'Data refresh completed with {error_count} errors')
                
                # Create partial success notification
                create_notification(account_id, 'refresh_partial',
                                  error_count=error_count,
                                  error_details=errors)
                
                flash(f'Data refresh completed with errors: {error_msg}', 'warning')
                
        except Exception as e:
            # Complete progress notification with failure
            complete_progress(account_id, progress_notification.id, False,
                            f'Data refresh failed: {str(e)}')
            
            # Create failure notification
            create_notification(account_id, 'refresh_failed',
                              error_message=str(e))
            raise
        
        # Redirect back to referring page or dashboard
        return redirect(request.referrer or url_for('lms.assignment_monitor'))
        
    except Exception as e:
        error_msg, status_code = handle_route_error(e, account_id, "refresh_data")
        flash(error_msg, 'error')
        return redirect(request.referrer or url_for('lms.assignment_monitor'))

# File Operation Routes

@lms_bp.route('/download/<path:file_url>')
@login_required
@require_lms_authentication
@limiter.limit("50 per day")
def download_file(file_url):
    """Download a file from the university LMS."""
    account_id = None
    try:
        account_id = get_active_account_id()
        if not account_id:
            flash('No active account found.', 'error')
            return redirect(url_for('main.dashboard'))

        # Flask routing can collapse the double slash in an embedded URL.
        if file_url.startswith('https:/') and not file_url.startswith('https://'):
            file_url = file_url.replace('https:/', 'https://', 1)

        try:
            target = urllib.parse.urlsplit(file_url)
            allowed = (
                target.scheme == 'https'
                and target.netloc.lower() in {'lms.bahria.edu.pk', 'lms.bahria.edu.pk:443'}
                and not any(char.isspace() or ord(char) < 32 or char == '\\' for char in file_url)
            )
        except ValueError:
            allowed = False
        if not allowed:
            return jsonify(error='Invalid LMS file URL.'), 400

        # Create an authenticated LMS session directly via the scraper service.
        # This avoids going through _ensure_valid_session which raises
        # LMSAuthenticationError/LMSSessionError that the blueprint error handlers
        # redirect to /lms/setup.
        try:
            from app.services.lms_scraper_service import LMSScraperService
            scraper = LMSScraperService()
            lms_session = scraper._create_authenticated_session(account_id)
        except Exception as auth_err:
            logger.error(f"Failed to establish LMS session for download: {auth_err}")
            flash('Could not authenticate with LMS. Please try again.', 'error')
            return redirect(request.referrer or url_for('lms.assignment_monitor'))

        # Generate token and redirect to Cloudflare
        try:
            from app.utils.download_token import generate_download_token
            worker = file_worker_origin()
            if not worker:
                raise ValueError("File proxy is not configured.")
            cookies_dict = lms_session.cookies.get_dict()
            # Pass a list containing one target string for single files
            token = generate_download_token([file_url], cookies_dict)
            
            cf_url = f"{worker}/download?token={token}"
            if len(cf_url) > 1900:
                logger.warning("Download token exceeds redirect limit (%d chars).", len(cf_url))
                raise ValueError("Token too long")
                
            logger.info(f"Redirecting download to Cloudflare Edge for account {account_id}")
            return redirect(cf_url)
            
        except Exception as e:
            if request.args.get('lecture') == '1':
                # Lecture downloads never fall back to Google Cloud file transfer.
                return jsonify(success=False, error='Lecture proxy unavailable. Refresh LMS and retry.'), 502
            # Fallback to the old memory stream method if the URL is dangerously long
            try:
                file_response = lms_session.get(
                    file_url, stream=True, timeout=60, allow_redirects=False
                )
                if not 200 <= file_response.status_code < 300:
                    file_response.close()
                    return jsonify(error='LMS file download failed.'), 502

                filename = secure_filename(urllib.parse.unquote(target.path.rsplit('/', 1)[-1])) or 'download'
                content_disposition = f'attachment; filename="{filename}"'

                def generate():
                    yield from file_response.iter_content(chunk_size=8192)

                response = Response(
                    generate(),
                    mimetype='application/octet-stream',
                    headers={'Content-Disposition': content_disposition, 'X-Content-Type-Options': 'nosniff'}
                )
                response.call_on_close(file_response.close)

                logger.info("Streaming LMS download through the application fallback")
                return response
            except Exception as stream_err:
                logger.warning("LMS file stream failed: %s", type(stream_err).__name__)
                flash('File download failed. The file may no longer be available.', 'error')
                return redirect(request.referrer or url_for('lms.assignment_monitor'))

    except Exception as e:
        logger.error(f"File download error for account {account_id}: {e}")
        flash('File download failed. Please try again.', 'error')
        return redirect(request.referrer or url_for('lms.assignment_monitor'))

@lms_bp.route('/upload/<assignment_id>', methods=['GET', 'POST'])
@login_required
@require_lms_authentication
def upload_assignment_file(assignment_id):
    """
    Assignment file upload route.
    Handles file uploads to LMS servers with validation.
    """
    try:
        account_id = get_active_account_id()
        if not account_id:
            flash('No active account found.', 'error')
            return redirect(url_for('main.dashboard'))
        
        if request.method == 'GET':
            # Show upload form
            try:
                # Get assignment details for upload form from snapshot
                from app.utils.data_manager import data_manager
                unified = data_manager.get_unified_dashboard(account_id) or {}
                snapshot = unified.get('assignments_snapshot') or {}
                raw_assignments = snapshot.get('assignments') or snapshot.get('all_assignments', [])
                
                from app.models.lms_models import LMSAssignment
                assignments = [LMSAssignment.from_dict(a) for a in raw_assignments]
                assignment = next((a for a in assignments if str(a.assignment_no) == str(assignment_id)), None)
                
                if not assignment:
                    flash('Assignment not found.', 'error')
                    return redirect(url_for('lms.assignments_new'))
                
                upload_data = {
                    'assignment': assignment.to_dict() if hasattr(assignment, 'to_dict') else assignment,
                    'assignment_id': assignment_id,
                    'max_file_size': ASSIGNMENT_UPLOAD_MAX_SIZE,
                    'allowed_extensions': ['pdf', 'doc', 'docx', 'txt', 'zip', 'rar']
                }
                
                # TODO: Create upload template or handle upload inline
                flash('File upload feature is not yet implemented.', 'info')
                return redirect(url_for('lms.assignments_new'))
                
            except Exception as e:
                error_msg, status_code = handle_lms_error(e, "Failed to load upload form")
                flash(error_msg, 'error')
                return redirect(url_for('lms.assignments_new'))
        
        elif request.method == 'POST':
            # Handle file upload
            log_info(f"Processing file upload for assignment {assignment_id}, account {account_id}",
                    account_id=account_id, operation="upload_file", assignment_id=assignment_id)
            
            # Validate file upload
            if 'file' not in request.files:
                create_notification(account_id, notification_type=NotificationType.ERROR,
                                  title="Upload Failed", message="No file selected for upload.")
                flash('No file selected for upload.', 'error')
                return redirect(url_for('lms.upload_assignment_file', assignment_id=assignment_id))
            
            file = request.files['file']
            if file.filename == '':
                create_notification(account_id, notification_type=NotificationType.ERROR,
                                  title="Upload Failed", message="No file selected for upload.")
                flash('No file selected for upload.', 'error')
                return redirect(url_for('lms.upload_assignment_file', assignment_id=assignment_id))
            
            # Validate assignment file size
            file.seek(0, 2)  # Seek to end
            file_size = file.tell()
            file.seek(0)  # Reset to beginning
            
            max_size = ASSIGNMENT_UPLOAD_MAX_SIZE
            if file_size > max_size:
                error_msg = f'File too large. Maximum size is {max_size // (1024*1024)}MB.'
                create_notification(account_id, notification_type=NotificationType.ERROR,
                                  title="Upload Failed", message=error_msg,
                                  actions=[
                                      {'label': 'Choose Smaller File', 'action': 'choose_file'},
                                      {'label': 'Compress File', 'action': 'compress_help'}
                                  ])
                flash(error_msg, 'error')
                return redirect(url_for('lms.upload_assignment_file', assignment_id=assignment_id))
            
            # Validate file extension
            allowed_extensions = {'pdf', 'doc', 'docx', 'txt', 'zip', 'rar', 'ppt', 'pptx'}
            file_extension = file.filename.rsplit('.', 1)[1].lower() if '.' in file.filename else ''
            
            if file_extension not in allowed_extensions:
                error_msg = f'File type not allowed. Allowed types: {", ".join(allowed_extensions)}'
                create_notification(account_id, notification_type=NotificationType.ERROR,
                                  title="Upload Failed", message=error_msg,
                                  actions=[
                                      {'label': 'Convert File', 'action': 'convert_help'},
                                      {'label': 'Choose Different File', 'action': 'choose_file'}
                                  ])
                flash(error_msg, 'error')
                return redirect(url_for('lms.upload_assignment_file', assignment_id=assignment_id))
            
            try:
                # Create upload progress notification
                upload_notification = create_notification(account_id, 'file_upload_started',
                                                        filename=file.filename,
                                                        assignment_id=assignment_id)
                
                # Get assignment requirements (if any)
                assignments = lms_service.get_assignments(account_id)
                assignment = next((a for a in assignments if str(a.assignment_no) == str(assignment_id)), None)
                
                assignment_requirements = {
                    'max_size': max_size,
                    'allowed_extensions': list(allowed_extensions),
                    'assignment_id': assignment_id
                }
                
                # Update progress: Validating
                update_progress(account_id, upload_notification.id, 25, 'Validating file...')
                
                # Update progress: Uploading
                update_progress(account_id, upload_notification.id, 50, 'Uploading to LMS...')
                
                # Perform upload
                upload_result = lms_service.upload_assignment_file(
                    account_id, 
                    assignment_id, 
                    file, 
                    assignment_requirements
                )
                
                if upload_result.get('success'):
                    # Complete progress with success
                    complete_progress(account_id, upload_notification.id, True,
                                    f'File "{file.filename}" uploaded successfully!')
                    
                    # Create success notification
                    create_notification(account_id, 'file_upload_success',
                                      filename=file.filename,
                                      assignment_id=assignment_id)
                    
                    flash(f'File "{file.filename}" uploaded successfully!', 'success')
                    log_user_activity("file_upload", account_id=account_id, 
                                     assignment_id=assignment_id, filename=file.filename, success=True)
                    return redirect(url_for('lms.assignments_new'))
                else:
                    error_msg = upload_result.get('error', 'Upload failed')
                    
                    # Complete progress with failure
                    complete_progress(account_id, upload_notification.id, False,
                                    f'Upload failed: {error_msg}')
                    
                    # Create failure notification
                    create_notification(account_id, 'file_upload_failed',
                                      filename=file.filename,
                                      error_message=error_msg)
                    
                    flash(f'Upload failed: {error_msg}', 'error')
                    log_user_activity("file_upload", account_id=account_id, 
                                     assignment_id=assignment_id, filename=file.filename, 
                                     success=False, error=error_msg)
                    return redirect(url_for('lms.upload_assignment_file', assignment_id=assignment_id))
                
            except LMSFileError as e:
                # Complete progress with failure
                if 'upload_notification' in locals():
                    complete_progress(account_id, upload_notification.id, False,
                                    f'Upload failed: {str(e)}')
                
                # Create failure notification
                create_notification(account_id, 'file_upload_failed',
                                  filename=file.filename,
                                  error_message=str(e))
                
                log_error(f"File upload error: {e}", account_id=account_id, 
                         operation="upload_file", assignment_id=assignment_id, exc_info=True)
                flash(f'Upload failed: {e}', 'error')
                return redirect(url_for('lms.upload_assignment_file', assignment_id=assignment_id))
                
    except Exception as e:
        error_msg, status_code = handle_lms_error(e, "File upload failed")
        flash(error_msg, 'error')
        logger.error(f"File upload error for assignment {assignment_id}, account {account_id}: {e}")
        return redirect(url_for('lms.assignments_new'))

@lms_bp.route('/file-info/<path:file_url>')
@login_required
@require_lms_authentication
def get_file_info(file_url):
    """
    Get file metadata without downloading.
    Returns JSON with file information.
    """
    try:
        account_id = get_active_account_id()
        if not account_id:
            return jsonify({'error': 'No active account found'}), 401
        
        logger.debug(f"Getting file info for account {account_id}: {file_url}")
        
        # Validate file URL
        if not file_url or not file_url.startswith(('http://', 'https://')):
            return jsonify({'error': 'Invalid file URL'}), 400
        
        try:
            # Get file metadata
            file_metadata = lms_service.get_file_metadata(account_id, file_url)
            
            return jsonify({
                'success': True,
                'file_info': file_metadata,
                'timestamp': datetime.now().isoformat()
            })
            
        except LMSFileError as e:
            logger.error(f"File info error: {e}")
            return jsonify({'error': f'Failed to get file info: {e}'}), 400
            
    except Exception as e:
        error_msg, status_code = handle_lms_error(e, "Failed to get file information")
        logger.error(f"File info error for account {account_id}: {e}")
        return jsonify({'error': error_msg}), status_code

@lms_bp.route('/validate-upload', methods=['POST'])
@login_required
@require_lms_authentication
def validate_upload():
    """
    Validate file upload before actual upload.
    Returns JSON with validation result.
    """
    try:
        account_id = get_active_account_id()
        if not account_id:
            return jsonify({'error': 'No active account found'}), 401
        
        # Get request data
        data = request.get_json()
        if not data:
            return jsonify({'error': 'No data provided'}), 400
        
        # Validate required parameters
        is_valid, missing_params = validate_request_params(['filename', 'file_size', 'assignment_id'], data)
        if not is_valid:
            return jsonify({'error': f'Missing parameters: {", ".join(missing_params)}'}), 400
        
        filename = data['filename']
        file_size = data['file_size']
        assignment_id = data['assignment_id']
        
        logger.debug(f"Validating upload for assignment {assignment_id}: {filename} ({file_size} bytes)")
        
        validation_result = {
            'valid': True,
            'errors': [],
            'warnings': []
        }
        
        # Validate assignment file size
        max_size = ASSIGNMENT_UPLOAD_MAX_SIZE
        if file_size > max_size:
            validation_result['valid'] = False
            validation_result['errors'].append(f'File too large. Maximum size is {max_size // (1024*1024)}MB.')
        
        # Validate file extension
        allowed_extensions = {'pdf', 'doc', 'docx', 'txt', 'zip', 'rar', 'ppt', 'pptx'}
        file_extension = filename.rsplit('.', 1)[1].lower() if '.' in filename else ''
        
        if file_extension not in allowed_extensions:
            validation_result['valid'] = False
            validation_result['errors'].append(f'File type not allowed. Allowed types: {", ".join(allowed_extensions)}')
        
        # Check if assignment exists
        try:
            assignments = lms_service.get_assignments(account_id)
            assignment = next((a for a in assignments if str(a.assignment_no) == str(assignment_id)), None)
            
            if not assignment:
                validation_result['valid'] = False
                validation_result['errors'].append('Assignment not found.')
            elif hasattr(assignment, 'is_submitted') and assignment.is_submitted():
                validation_result['warnings'].append('Assignment already submitted. Upload will replace existing submission.')
            elif hasattr(assignment, 'is_overdue') and assignment.is_overdue():
                validation_result['warnings'].append('Assignment is overdue. Late submission may not be accepted.')
                
        except Exception as e:
            logger.warning(f"Error checking assignment status: {e}")
            validation_result['warnings'].append('Could not verify assignment status.')
        
        return jsonify({
            'success': True,
            'validation': validation_result,
            'timestamp': datetime.now().isoformat()
        })
        
    except Exception as e:
        error_msg, status_code = handle_lms_error(e, "Upload validation failed")
        logger.error(f"Upload validation error for account {account_id}: {e}")
        return jsonify({'error': error_msg}), status_code

# API Endpoints for AJAX Functionality

@lms_bp.route('/api/assignments')
@login_required
@require_lms_authentication
def api_assignments():
    """
    JSON API endpoint for assignments data.
    Supports filtering and pagination for AJAX requests.
    """
    try:
        account_id = get_active_account_id()
        if not account_id:
            return jsonify({'error': 'No active account found'}), 401
        
        # Get query parameters
        semester = request.args.get('semester')
        course = request.args.get('course')
        status_filter = request.args.get('status')
        force_refresh = request.args.get('refresh', 'false').lower() == 'true'
        page = int(request.args.get('page', 1))
        per_page = min(int(request.args.get('per_page', 20)), 100)  # Max 100 per page
        
        logger.debug(f"API assignments request for account {account_id}: semester={semester}, course={course}, status={status_filter}")
        
        try:
            # Get assignments data
            assignments_list = lms_service.get_assignments(
                account_id,
                semester=semester,
                course=course,
                force_refresh=force_refresh
            )
            
            # Apply status filter
            if status_filter and status_filter != 'all':
                if status_filter == 'pending':
                    assignments_list = [a for a in assignments_list if a.is_pending()]
                elif status_filter == 'submitted':
                    assignments_list = [a for a in assignments_list if a.is_submitted()]
                elif status_filter == 'overdue':
                    assignments_list = [a for a in assignments_list if a.is_overdue()]
            
            # Sort assignments
            assignments_list.sort(key=lambda a: (
                a.deadline or datetime.max,
                a.assignment_no or 0
            ))
            
            # Pagination
            total_assignments = len(assignments_list)
            start_idx = (page - 1) * per_page
            end_idx = start_idx + per_page
            paginated_assignments = assignments_list[start_idx:end_idx]
            
            # Calculate statistics
            stats = {
                'total_assignments': total_assignments,
                'pending_count': len([a for a in assignments_list if a.is_pending()]),
                'submitted_count': len([a for a in assignments_list if a.is_submitted()]),
                'overdue_count': len([a for a in assignments_list if a.is_overdue()]),
                'page': page,
                'per_page': per_page,
                'total_pages': (total_assignments + per_page - 1) // per_page
            }
            
            return jsonify({
                'success': True,
                'assignments': [a.to_dict() if hasattr(a, 'to_dict') else a for a in paginated_assignments],
                'stats': stats,
                'filters': {
                    'semester': semester,
                    'course': course,
                    'status': status_filter
                },
                'timestamp': datetime.now().isoformat()
            })
            
        except Exception as e:
            error_msg, status_code = handle_lms_error(e, "Failed to load assignments")
            return jsonify({'error': error_msg}), status_code
            
    except Exception as e:
        logger.error(f"API assignments error for account {account_id}: {e}")
        return jsonify({'error': 'Internal server error'}), 500

@lms_bp.route('/api/lecture-notes')
@login_required
@require_lms_authentication
def api_lecture_notes():
    """
    JSON API endpoint for lecture notes data.
    Supports filtering and pagination for AJAX requests.
    """
    try:
        account_id = get_active_account_id()
        if not account_id:
            return jsonify({'error': 'No active account found'}), 401
        
        # Get query parameters
        semester = request.args.get('semester')
        course = request.args.get('course')
        force_refresh = request.args.get('refresh', 'false').lower() == 'true'
        page = int(request.args.get('page', 1))
        per_page = min(int(request.args.get('per_page', 20)), 100)  # Max 100 per page
        
        logger.debug(f"API lecture notes request for account {account_id}: semester={semester}, course={course}")
        
        try:
            # Get lecture notes data
            lecture_notes_list = lms_service.get_lecture_notes(
                account_id,
                semester=semester,
                course=course,
                force_refresh=force_refresh
            )
            
            # Sort lecture notes
            lecture_notes_list.sort(key=lambda n: (
                n.week_no or 0,
                n.upload_date or datetime.min
            ), reverse=True)
            
            # Pagination
            total_notes = len(lecture_notes_list)
            start_idx = (page - 1) * per_page
            end_idx = start_idx + per_page
            paginated_notes = lecture_notes_list[start_idx:end_idx]
            
            # Group by course for better organization
            notes_by_course = {}
            for note in paginated_notes:
                course_key = f"{note.course_code} - {note.course_name}" if hasattr(note, 'course_code') else 'Unknown Course'
                if course_key not in notes_by_course:
                    notes_by_course[course_key] = []
                notes_by_course[course_key].append(note)
            
            # Calculate statistics
            stats = {
                'total_notes': total_notes,
                'courses_count': len(set(f"{n.course_code} - {n.course_name}" for n in lecture_notes_list 
                                       if hasattr(n, 'course_code'))),
                'notes_with_files': len([n for n in lecture_notes_list 
                                       if hasattr(n, 'lecture_files') and n.lecture_files]),
                'notes_with_videos': len([n for n in lecture_notes_list 
                                        if hasattr(n, 'video_links') and n.video_links]),
                'page': page,
                'per_page': per_page,
                'total_pages': (total_notes + per_page - 1) // per_page
            }
            
            return jsonify({
                'success': True,
                'lecture_notes': [n.to_dict() if hasattr(n, 'to_dict') else n for n in paginated_notes],
                'notes_by_course': {k: [n.to_dict() if hasattr(n, 'to_dict') else n for n in v] 
                                  for k, v in notes_by_course.items()},
                'stats': stats,
                'filters': {
                    'semester': semester,
                    'course': course
                },
                'timestamp': datetime.now().isoformat()
            })
            
        except Exception as e:
            error_msg, status_code = handle_lms_error(e, "Failed to load lecture notes")
            return jsonify({'error': error_msg}), status_code
            
    except Exception as e:
        logger.error(f"API lecture notes error for account {account_id}: {e}")
        return jsonify({'error': 'Internal server error'}), 500

@lms_bp.route('/api/refresh', methods=['POST'])
@login_required
@require_lms_authentication
def api_refresh_data():
    """
    API endpoint to refresh LMS data.
    Returns JSON with refresh status and updated data counts.
    """
    try:
        account_id = get_active_account_id()
        if not account_id:
            return jsonify({'error': 'No active account found'}), 401
        
        # Get request data
        data = request.get_json() or {}
        data_type = data.get('data_type', 'all')  # 'assignments', 'lecture_notes', or 'all'
        
        # Load user to check rate limits
        user = current_user
        if not user or not getattr(user, 'cms_account', None):
            logger.error(f"[RATE-LIMIT] No cms_account found on current_user")
            return jsonify({'error': 'Account not found'}), 404
            
        cms_account = user.cms_account
        if not hasattr(cms_account, 'lms_preferences') or not isinstance(cms_account.lms_preferences, dict):
            cms_account.lms_preferences = {}
        prefs = cms_account.lms_preferences
        
        silent = data.get('silent', False)
        today = datetime.now().strftime('%Y-%m-%d')
        now = datetime.now()
        
        if not silent:
            # Enforce 3-minute cooldown
            last_refresh_str = prefs.get('last_manual_refresh')
            if last_refresh_str:
                try:
                    last_refresh = datetime.fromisoformat(last_refresh_str)
                    elapsed = (now - last_refresh).total_seconds()
                    if elapsed < 180:
                        return jsonify({'error': 'Please wait 3 minutes between manual refreshes.', 'status': 429}), 429
                except ValueError:
                    pass
            
            # Enforce 3/day daily limit
            daily_date = prefs.get('daily_refresh_date')
            count = prefs.get('daily_refresh_count', 0)
            if daily_date == today:
                if count >= 3:
                    return jsonify({'error': 'Daily manual refresh limit reached (3/3).', 'status': 429}), 429
            else:
                count = 0
        
        logger.info(f"API refresh request for account {account_id}: data_type={data_type}")
        
        refresh_result = {
            'success': True,
            'refreshed_data': [],
            'errors': [],
            'timestamp': datetime.now().isoformat()
        }
        
        try:
            if data_type in ['assignments', 'all']:
                # Refresh assignments
                assignments = lms_service.get_assignments(account_id, force_refresh=True)
                refresh_result['refreshed_data'].append({
                    'type': 'assignments',
                    'count': len(assignments),
                    'success': True
                })
                
        except Exception as e:
            refresh_result['errors'].append(f'Assignments refresh failed: {e}')
            refresh_result['refreshed_data'].append({
                'type': 'assignments',
                'count': 0,
                'success': False,
                'error': str(e)
            })
        
        try:
            if data_type in ['lecture_notes', 'all']:
                # Refresh lecture notes
                lecture_notes = lms_service.get_lecture_notes(account_id, force_refresh=True)
                refresh_result['refreshed_data'].append({
                    'type': 'lecture_notes',
                    'count': len(lecture_notes),
                    'success': True
                })
                
        except Exception as e:
            refresh_result['errors'].append(f'Lecture notes refresh failed: {e}')
            refresh_result['refreshed_data'].append({
                'type': 'lecture_notes',
                'count': 0,
                'success': False,
                'error': str(e)
            })
        
        # Update overall success status
        refresh_result['success'] = any(item['success'] for item in refresh_result['refreshed_data'])
        
        # Update rate limit counters on success for manual refreshes
        if refresh_result['success'] and not silent:
            prefs['last_manual_refresh'] = now.isoformat()
            prefs['daily_refresh_date'] = today
            prefs['daily_refresh_count'] = count + 1
            cms_account.lms_preferences = prefs
            user.save()
            logger.info(f"[RATE-LIMIT] Saved prefs after success: {prefs}")
        
        # Get updated summary data
        try:
            pending_summary = lms_service.get_pending_assignments_summary(account_id)
            refresh_result['pending_summary'] = pending_summary
        except Exception as e:
            logger.warning(f"Error getting updated pending summary: {e}")
        
        return jsonify(refresh_result)
        
    except Exception as e:
        error_msg, status_code = handle_lms_error(e, "Data refresh failed")
        logger.error(f"API refresh error for account {account_id}: {e}")
        return jsonify({'error': error_msg}), status_code

@lms_bp.route('/api/pending-summary')
@login_required
@require_lms_authentication
def api_pending_summary():
    """
    API endpoint for pending assignments summary.
    Used for dashboard widgets and notifications.
    """
    try:
        account_id = get_active_account_id()
        if not account_id:
            return jsonify({'error': 'No active account found'}), 401
        
        logger.debug(f"API pending summary request for account {account_id}")
        
        try:
            # Get pending assignments summary
            pending_summary = lms_service.get_pending_assignments_summary(account_id)
            
            return jsonify({
                'success': True,
                'pending_summary': pending_summary,
                'timestamp': datetime.now().isoformat()
            })
            
        except Exception as e:
            error_msg, status_code = handle_lms_error(e, "Failed to get pending summary")
            return jsonify({'error': error_msg}), status_code
            
    except Exception as e:
        logger.error(f"API pending summary error for account {account_id}: {e}")
        return jsonify({'error': 'Internal server error'}), 500

@lms_bp.route('/api/service-status')
@login_required
@require_lms_authentication
def api_service_status():
    """
    API endpoint for LMS service status.
    Returns comprehensive status information for monitoring.
    """
    try:
        account_id = get_active_account_id()
        if not account_id:
            return jsonify({'error': 'No active account found'}), 401
        
        logger.debug(f"API service status request for account {account_id}")
        
        try:
            # Get comprehensive service status
            service_status = lms_service.get_service_status(account_id)
            
            # Get cache information
            cache_info = lms_service.get_cache_info(account_id)
            
            # Get refresh status
            refresh_status = lms_service.get_refresh_status(account_id)
            
            return jsonify({
                'success': True,
                'service_status': service_status,
                'cache_info': cache_info,
                'refresh_status': refresh_status,
                'timestamp': datetime.now().isoformat()
            })
            
        except Exception as e:
            error_msg, status_code = handle_lms_error(e, "Failed to get service status")
            return jsonify({'error': error_msg}), status_code
            
    except Exception as e:
        logger.error(f"API service status error for account {account_id}: {e}")
        return jsonify({'error': 'Internal server error'}), 500

@lms_bp.route('/api/lms-status')
@login_required
def api_lms_status():
    """
    API endpoint for LMS server connectivity status.
    Returns cached performance data from recent LMS requests.
    No extra requests are made to LMS servers.
    """
    # Check session authentication
    account_id = get_active_account_id()
    if account_id and not is_account_authenticated(account_id):
        return jsonify({
            'error': 'Account not authenticated in this session',
            'requires_auth': True,
            'account_id': account_id
        }), 403
    
    try:
        from app.utils.lms_monitor import LMSMonitor
        status = LMSMonitor.get_status()
        
        # Add debug info
        status['debug'] = {
            'has_session_data': 'lms_last_check' in session,
            'session_keys': list(session.keys()) if session else []
        }
        
        return jsonify(status)
    except Exception as e:
        logger.error(f"Failed to get LMS status: {e}")
        return jsonify({
            'status': 'error',
            'response_time': 0,
            'signal_strength': 'unknown',
            'message': 'Failed to retrieve status',
            'error': str(e)
        }), 500

@lms_bp.route('/api/cache/invalidate', methods=['POST'])
@login_required
@require_lms_authentication
def api_invalidate_cache():
    """
    API endpoint to invalidate cache.
    Allows selective cache invalidation for better performance.
    """
    try:
        account_id = get_active_account_id()
        if not account_id:
            return jsonify({'error': 'No active account found'}), 401
        
        # Get request data
        data = request.get_json() or {}
        data_type = data.get('data_type')  # Optional: 'assignments' or 'lecture_notes'
        semester = data.get('semester')    # Optional
        course = data.get('course')        # Optional
        
        logger.info(f"API cache invalidation for account {account_id}: type={data_type}, semester={semester}, course={course}")
        
        try:
            # Invalidate cache
            invalidation_result = lms_service.invalidate_cache(
                account_id,
                data_type=data_type,
                semester=semester,
                course=course
            )
            
            return jsonify({
                'success': True,
                'invalidation_result': invalidation_result,
                'timestamp': datetime.now().isoformat()
            })
            
        except Exception as e:
            error_msg, status_code = handle_lms_error(e, "Cache invalidation failed")
            return jsonify({'error': error_msg}), status_code
            
    except Exception as e:
        logger.error(f"API cache invalidation error for account {account_id}: {e}")
        return jsonify({'error': 'Internal server error'}), 500

@lms_bp.route('/api/filters')
@login_required
@require_lms_authentication
def api_get_filters():
    """
    API endpoint to get available filter options.
    Returns available semesters and courses for filter dropdowns.
    """
    try:
        account_id = get_active_account_id()
        if not account_id:
            return jsonify({'error': 'No active account found'}), 401
        
        logger.debug(f"API filters request for account {account_id}")
        
        try:
            # Get all assignments and lecture notes to extract filter options
            assignments = lms_service.get_assignments(account_id)
            lecture_notes = lms_service.get_lecture_notes(account_id)
            
            # Extract unique semesters
            assignment_semesters = set(a.semester for a in assignments if hasattr(a, 'semester') and a.semester)
            notes_semesters = set(n.semester for n in lecture_notes if hasattr(n, 'semester') and n.semester)
            available_semesters = sorted(list(assignment_semesters.union(notes_semesters)))
            
            # Extract unique courses
            assignment_courses = set(f"{a.course_code} - {a.course_name}" for a in assignments 
                                   if hasattr(a, 'course_code') and a.course_code)
            notes_courses = set(f"{n.course_code} - {n.course_name}" for n in lecture_notes 
                              if hasattr(n, 'course_code') and n.course_code)
            available_courses = sorted(list(assignment_courses.union(notes_courses)))
            
            return jsonify({
                'success': True,
                'filters': {
                    'semesters': available_semesters,
                    'courses': available_courses,
                    'status_options': ['all', 'pending', 'submitted', 'overdue']
                },
                'timestamp': datetime.now().isoformat()
            })
            
        except Exception as e:
            error_msg, status_code = handle_lms_error(e, "Failed to get filter options")
            return jsonify({'error': error_msg}), status_code
            
    except Exception as e:
        logger.error(f"API filters error for account {account_id}: {e}")
        return jsonify({'error': 'Internal server error'}), 500

# Notification API Endpoints

@lms_bp.route('/api/notifications')
@login_required
def api_get_notifications():
    """
    API endpoint to get notifications for the current user.
    """
    try:
        account_id = get_active_account_id()
        if not account_id:
            return jsonify({'error': 'No active account found'}), 401
        
        # Check session authentication
        if not is_account_authenticated(account_id):
            return jsonify({
                'error': 'Account not authenticated in this session',
                'requires_auth': True,
                'account_id': account_id
            }), 403
        
        include_expired = request.args.get('include_expired', 'false').lower() == 'true'
        
        notifications = get_notifications(account_id, include_expired)
        
        return jsonify({
            'success': True,
            'notifications': [n.to_dict() for n in notifications],
            'count': len(notifications),
            'timestamp': datetime.now().isoformat()
        })
        
    except Exception as e:
        log_error(f"Error getting notifications: {e}", account_id=get_active_account_id(), exc_info=True)
        return jsonify({'error': 'Failed to get notifications'}), 500

@lms_bp.route('/api/notifications/<notification_id>/dismiss', methods=['POST'])
@login_required
def api_dismiss_notification(notification_id):
    """
    API endpoint to dismiss a notification.
    """
    try:
        account_id = get_active_account_id()
        if not account_id:
            return jsonify({'error': 'No active account found'}), 401
        
        # Check session authentication
        if not is_account_authenticated(account_id):
            return jsonify({
                'error': 'Account not authenticated in this session',
                'requires_auth': True,
                'account_id': account_id
            }), 403
        
        success = dismiss_notification(account_id, notification_id)
        
        if success:
            return jsonify({
                'success': True,
                'message': 'Notification dismissed',
                'timestamp': datetime.now().isoformat()
            })
        else:
            return jsonify({
                'success': False,
                'error': 'Notification not found or not dismissible'
            }), 404
            
    except Exception as e:
        log_error(f"Error dismissing notification: {e}", account_id=get_active_account_id(), exc_info=True)
        return jsonify({'error': 'Failed to dismiss notification'}), 500

@lms_bp.route('/api/notifications/clear', methods=['POST'])
@login_required
def api_clear_notifications():
    """
    API endpoint to clear notifications.
    """
    try:
        account_id = get_active_account_id()
        if not account_id:
            return jsonify({'error': 'No active account found'}), 401
        
        # Check session authentication
        if not is_account_authenticated(account_id):
            return jsonify({
                'error': 'Account not authenticated in this session',
                'requires_auth': True,
                'account_id': account_id
            }), 403
        
        data = request.get_json() or {}
        notification_type_str = data.get('type')
        
        notification_type = None
        if notification_type_str:
            try:
                notification_type = NotificationType(notification_type_str)
            except ValueError:
                return jsonify({'error': f'Invalid notification type: {notification_type_str}'}), 400
        
        clear_notifications(account_id, notification_type)
        
        return jsonify({
            'success': True,
            'message': 'Notifications cleared',
            'timestamp': datetime.now().isoformat()
        })
        
    except Exception as e:
        log_error(f"Error clearing notifications: {e}", account_id=get_active_account_id(), exc_info=True)
        return jsonify({'error': 'Failed to clear notifications'}), 500


# LMS Setup route removed. User authentication happens automatically via LMSAuthService.

@lms_bp.route('/test-connection', methods=['POST'])
@login_required
def test_connection():
    """
    Test LMS connection with provided credentials.
    Returns JSON result for AJAX testing.
    """
    try:
        active_account = current_user.get_active_account()
        if not active_account:
            return jsonify({'error': 'No active account found'}), 401
        
        # Get credentials from request
        data = request.get_json()
        if not data:
            return jsonify({'error': 'No data provided'}), 400
        
        lms_username = data.get('lms_username', '').strip()
        lms_password = data.get('lms_password', '').strip()
        
        if not lms_username or not lms_password:
            return jsonify({'error': 'Username and password required'}), 400
        
        logger.info(f"Testing LMS connection for account {active_account.account_id}")
        
        try:
            # Test authentication (without saving credentials)
            from app.services.lms_auth_service import LMSAuthService
            auth_service = LMSAuthService()
            
            # Perform test authentication
            test_result = auth_service.test_authentication(lms_username, lms_password)
            
            if test_result.get('success'):
                return jsonify({
                    'success': True,
                    'message': 'Connection successful',
                    'test_result': test_result,
                    'timestamp': datetime.now().isoformat()
                })
            else:
                return jsonify({
                    'success': False,
                    'error': test_result.get('error', 'Authentication failed'),
                    'timestamp': datetime.now().isoformat()
                })
                
        except Exception as e:
            error_msg, status_code = handle_route_error(e, active_account.account_id, "Connection test failed")
            return jsonify({'error': error_msg}), status_code
            
    except Exception as e:
        logger.error(f"Connection test error: {e}")
        return jsonify({'error': 'Internal server error'}), 500

@lms_bp.route('/logout', methods=['POST'])
@login_required
def logout_lms():
    """
    Logout from LMS and clear session data.
    """
    try:
        active_account = current_user.get_active_account()
        if not active_account:
            flash('No active account found.', 'error')
            return redirect(url_for('main.dashboard'))
        
        logger.info(f"Logging out from LMS for account {active_account.account_id}")
        
        # Logout from LMS service
        logout_success = lms_service.logout_user(active_account.account_id)
        
        if logout_success:
            flash('Successfully logged out from LMS.', 'success')
        else:
            flash('LMS logout completed with some errors.', 'warning')
        
        return redirect(url_for('main.dashboard'))
        
    except Exception as e:
        error_msg, status_code = handle_lms_error(e, "LMS logout failed")
        flash(error_msg, 'error')
        logger.error(f"LMS logout error: {e}")
        return redirect(url_for('main.dashboard'))

@lms_bp.route('/settings')
@login_required
@require_lms_authentication
def settings():
    """
    LMS settings and preferences page.
    """
    try:
        account_id = get_active_account_id()
        if not account_id:
            flash('No active account found.', 'error')
            return redirect(url_for('main.dashboard'))
        
        # Get current settings and status
        try:
            service_status = lms_service.get_service_status(account_id)
            cache_info = lms_service.get_cache_info(account_id)
            refresh_status = lms_service.get_refresh_status(account_id)
        except Exception as e:
            logger.error(f"Error getting LMS settings data: {e}")
            service_status = {'overall_status': 'error', 'error': str(e)}
            cache_info = {'cache_status': 'error', 'error': str(e)}
            refresh_status = {'enabled': False, 'error': str(e)}
        
        settings_data = {
            'service_status': service_status,
            'cache_info': cache_info,
            'refresh_status': refresh_status,
            'account_id': account_id
        }
        
        # TODO: Create LMS settings template or integrate with main settings
        flash('LMS settings are managed through the main settings page.', 'info')
        return redirect(url_for('settings.settings'))
        
    except Exception as e:
        error_msg, status_code = handle_lms_error(e, "Failed to load LMS settings")
        flash(error_msg, 'error')
        logger.error(f"LMS settings error for account {account_id}: {e}")
        return redirect(url_for('lms.assignment_monitor'))

# Additional utility routes

@lms_bp.route('/clear-cache', methods=['POST', 'GET'])
@login_required
@require_lms_authentication
def clear_cache():
    """
    Clear all LMS cache for the current account.
    """
    try:
        account_id = get_active_account_id()
        if not account_id:
            flash('No active account found.', 'error')
            return redirect(url_for('main.dashboard'))
        
        logger.info(f"Clearing LMS cache for account {account_id}")
        
        # Clear cache
        invalidation_result = lms_service.invalidate_cache(account_id)
        
        if invalidation_result.get('success'):
            flash('LMS cache cleared successfully.', 'success')
        else:
            flash('Cache clearing completed with some errors.', 'warning')
        
        return redirect(request.referrer or url_for('lms.settings'))
        
    except Exception as e:
        error_msg, status_code = handle_lms_error(e, "Failed to clear cache")
        flash(error_msg, 'error')
        logger.error(f"Cache clearing error for account {account_id}: {e}")
        return redirect(url_for('lms.settings'))

@lms_bp.route('/background-refresh/enable', methods=['POST'])
@login_required
@require_lms_authentication
def enable_background_refresh():
    """
    Enable background refresh for LMS data.
    """
    try:
        account_id = get_active_account_id()
        if not account_id:
            return jsonify({'error': 'No active account found'}), 401
        
        # Get refresh interval from request
        data = request.get_json() or {}
        refresh_hours = data.get('refresh_hours', 4)
        
        logger.info(f"Enabling background refresh for account {account_id}: {refresh_hours} hours")
        
        from datetime import timedelta
        refresh_result = lms_service.schedule_background_refresh(
            account_id,
            timedelta(hours=refresh_hours)
        )
        
        if refresh_result.get('success'):
            return jsonify({
                'success': True,
                'message': 'Background refresh enabled',
                'refresh_result': refresh_result,
                'timestamp': datetime.now().isoformat()
            })
        else:
            return jsonify({
                'success': False,
                'error': refresh_result.get('error', 'Failed to enable background refresh')
            }), 400
            
    except Exception as e:
        error_msg, status_code = handle_lms_error(e, "Failed to enable background refresh")
        return jsonify({'error': error_msg}), status_code

@lms_bp.route('/background-refresh/disable', methods=['POST'])
@login_required
@require_lms_authentication
def disable_background_refresh():
    """
    Disable background refresh for LMS data.
    """
    try:
        account_id = get_active_account_id()
        if not account_id:
            return jsonify({'error': 'No active account found'}), 401
        
        logger.info(f"Disabling background refresh for account {account_id}")
        
        success = lms_service.disable_background_refresh(account_id)
        
        if success:
            return jsonify({
                'success': True,
                'message': 'Background refresh disabled',
                'timestamp': datetime.now().isoformat()
            })
        else:
            return jsonify({
                'success': False,
                'error': 'Failed to disable background refresh'
            }), 400
            
    except Exception as e:
        error_msg, status_code = handle_lms_error(e, "Failed to disable background refresh")
        return jsonify({'error': error_msg}), status_code

@lms_bp.route('/api/assignments/upload-token', methods=['POST'])
@login_required
@require_lms_authentication
def generate_upload_token():
    """
    API endpoint to generate a secure edge-upload token.
    The frontend will use this token to POST the file directly to Cloudflare
    Worker, which streams it to the LMS without touching Cloud Run memory.
    """
    try:
        worker = file_worker_origin()
        if not worker:
            return jsonify(success=False, error='File proxy is not configured.'), 503
        account_id = get_active_account_id()
        if not account_id:
            return jsonify({'error': 'No active account found'}), 401

        from app.services.lms_scraper_service import LMSScraperService
        scraper = LMSScraperService()
        lms_session = scraper._create_authenticated_session(account_id)
        
        from app.utils.download_token import generate_download_token
        cookies_dict = lms_session.cookies.get_dict()
        
        upload_url = "https://lms.bahria.edu.pk/Student/includes/studentprocess.php?s=assignmentSubmissionAdd"
        token = generate_download_token([upload_url], cookies_dict)
        
        cf_upload_url = f"{worker}/upload?token={token}"
        
        return jsonify({
            'success': True,
            'upload_url': cf_upload_url,
            'token': token
        })
    except Exception as e:
        logger.error(f"Error generating upload token: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500

@lms_bp.route('/api/assignments/upload', methods=['POST'])
@login_required
@require_lms_authentication
def upload_assignment():
    """
    API endpoint to upload assignment submission.
    """
    try:
        account_id = get_active_account_id()
        if not account_id:
            return jsonify({'error': 'No active account found'}), 401
        
        # Get form data
        assignment_no = request.form.get('assignment_no')
        semester = request.form.get('semester')
        course = request.form.get('course')
        comments = request.form.get('comments', '')
        
        if not assignment_no or not course:
            return jsonify({'error': 'Assignment number and course are required'}), 400
        
        # Get uploaded file
        if 'file' not in request.files:
            return jsonify({'error': 'No file uploaded'}), 400
        
        file = request.files['file']
        if file.filename == '':
            return jsonify({'error': 'No file selected'}), 400
        
        # Validate file type and size
        allowed_extensions = {'pdf', 'doc', 'docx', 'zip', 'rar', 'txt'}
        file_extension = file.filename.rsplit('.', 1)[1].lower() if '.' in file.filename else ''
        
        if file_extension not in allowed_extensions:
            return jsonify({'error': 'Invalid file type. Allowed: PDF, DOC, DOCX, ZIP, RAR, TXT'}), 400
        
        # Check assignment file size
        file.seek(0, 2)  # Seek to end
        file_size = file.tell()
        file.seek(0)  # Reset to beginning
        
        if file_size > ASSIGNMENT_UPLOAD_MAX_SIZE:
            return jsonify({'error': 'File size must be 15MB or less'}), 400
        
        # Get LMS IDs if provided
        assignment_id = request.form.get('assignment_id')
        offered_course_id = request.form.get('offered_course_id')
        student_id = request.form.get('student_id')
        
        group_enrollments = request.form.get('group_enrollments')
        delete_existing = request.form.get('delete_existing') == 'true'
        
        # --- TEMP TEST FEATURE BEGIN ---
        test_mode = request.form.get('test_mode')
        if test_mode:
            logger.info(f"[TEST UPLOAD] Initiated manual test upload for account {account_id}")
            logger.info(f"[TEST UPLOAD] test_assignment_id: {assignment_id}")
            
            if group_enrollments and not get_config().is_production:
                logger.info(f"[TEST UPLOAD] Detected Group Upload for: {group_enrollments}")
                enrollment_list = [e.strip() for e in group_enrollments.split(',') if e.strip()]
                
                if enrollment_list:
                    # Reset file pointer for group upload loop
                    file.seek(0)
                    file_content = file.read()
                    
                    try:
                        results = lms_service.handle_group_test_upload(
                            active_account_id=account_id,
                            active_student_id=student_id,
                            group_enrollments=enrollment_list,
                            assignment_no=int(assignment_no),
                            file_content=file_content,
                            filename=file.filename,
                            content_type=file.content_type,
                            semester=semester,
                            course=course,
                            assignment_id=assignment_id,
                            offered_course_id=offered_course_id,
                            delete_existing=delete_existing
                        )
                        
                        # Now process the active user's upload
                        file.seek(0)
                        active_result = {'enrollment': 'Your Account', 'status': 'failed', 'message': ''}
                        try:
                            lms_service.upload_assignment_submission(
                                account_id=account_id,
                                assignment_no=int(assignment_no),
                                file=file,
                                semester=semester,
                                course=course,
                                assignment_id=assignment_id,
                                offered_course_id=offered_course_id,
                                student_id=student_id
                            )
                            active_result['status'] = 'success'
                        except Exception as e:
                            active_result['message'] = str(e)
                            
                        results.insert(0, active_result)
                        
                        return jsonify({
                            'success': True,
                            'message': 'Group test upload finished',
                            'results': results
                        })
                    except Exception as e:
                        logger.error(f"Error in handle_group_test_upload: {e}", exc_info=True)
                        return jsonify({'success': False, 'error': str(e)}), 500
        # --- TEMP TEST FEATURE END ---
        
        logger.info(f"Uploading assignment {assignment_no} for account {account_id}")
        logger.info(f"Upload params - assignment_id: {assignment_id}, offered_course_id: {offered_course_id}")
        
        # Call LMS service to upload file
        try:
            success = lms_service.upload_assignment_submission(
                account_id=account_id,
                assignment_no=int(assignment_no),
                file=file,
                semester=semester,
                course=course,
                assignment_id=assignment_id,
                offered_course_id=offered_course_id,
                student_id=student_id
            )
            
            if success:
                # --- TEMP TEST FEATURE BEGIN ---
                if test_mode:
                    logger.info(f"[TEST UPLOAD] SUCCESS! LMS ACCEPTED the test upload for assignment_id {assignment_id}.")
                # --- TEMP TEST FEATURE END ---
                logger.info(f"Successfully uploaded assignment {assignment_no}")
                return jsonify({
                    'success': True,
                    'message': 'Assignment uploaded successfully',
                    'assignment_no': assignment_no,
                    'filename': file.filename,
                    'size': file_size
                })
            else:
                # --- TEMP TEST FEATURE BEGIN ---
                if test_mode:
                    logger.warning(f"[TEST UPLOAD] FAILED. LMS REJECTED the test upload for assignment_id {assignment_id}.")
                # --- TEMP TEST FEATURE END ---
                logger.warning(f"Failed to upload assignment {assignment_no}")
                return jsonify({
                    'success': False,
                    'error': 'Failed to upload to LMS'
                }), 400
                
        except Exception as upload_error:
            logger.error(f"Error during LMS upload: {upload_error}", exc_info=True)
            return jsonify({
                'success': False,
                'error': f'LMS upload failed: {str(upload_error)}'
            }), 500
        
    except Exception as e:
        logger.error(f"Error uploading assignment: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@lms_bp.route('/api/assignments/delete', methods=['POST'])
@login_required
@require_lms_authentication
def delete_assignment():
    """
    API endpoint to delete assignment submission.
    Makes actual request to LMS to delete the submission.
    """
    try:
        account_id = get_active_account_id()
        if not account_id:
            return jsonify({'error': 'No active account found'}), 401
        
        # Get request data
        data = request.get_json()
        if not data:
            return jsonify({'error': 'No data provided'}), 400
        
        assignment_no = data.get('assignment_no')
        semester = data.get('semester')
        course = data.get('course')
        submission_id = data.get('submission_id')
        assignment_id = data.get('assignment_id')
        offered_course_id = data.get('offered_course_id')
        
        if not assignment_no or not course:
            return jsonify({'error': 'Assignment number and course are required'}), 400
        
        logger.info(f"Deleting assignment {assignment_no} for account {account_id}")
        logger.info(f"Delete params - submission_id: {submission_id}, assignment_id: {assignment_id}, offered_course_id: {offered_course_id}")
        
        # Call LMS service to delete submission
        try:
            success = lms_service.delete_assignment_submission(
                account_id=account_id,
                submission_id=submission_id,
                assignment_id=assignment_id,
                offered_course_id=offered_course_id,
                semester=semester,
                course=course
            )
            
            if success:
                logger.info(f"Successfully deleted assignment {assignment_no} submission")
                return jsonify({
                    'success': True,
                    'message': 'Assignment submission deleted successfully',
                    'assignment_no': assignment_no
                })
            else:
                logger.warning(f"Failed to delete assignment {assignment_no} submission")
                return jsonify({
                    'success': False,
                    'error': 'Failed to delete submission from LMS'
                }), 400
                
        except Exception as delete_error:
            logger.error(f"Error during LMS deletion: {delete_error}", exc_info=True)
            return jsonify({
                'success': False,
                'error': f'LMS deletion failed: {str(delete_error)}'
            }), 500
        
    except Exception as e:
        logger.error(f"Error deleting assignment: {e}", exc_info=True)
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


TYPE_EXT_MAP = {
    'PPT': '.pptx',
    'PPTX': '.pptx',
    'PDF': '.pdf',
    'DOC': '.docx',
    'DOCX': '.docx',
    'XLS': '.xlsx',
    'XLSX': '.xlsx',
    'ZIP': '.zip',
    'RAR': '.rar',
    'TXT': '.txt'
}


def _generate_cloudrun_memory_zip(*args, **kwargs):
    """Cost guard: lecture bytes and ZIP generation must never use Cloud Run."""
    return jsonify(success=False, error='Server ZIP generation is disabled. Use lecture download parts.'), 410


@lms_bp.route('/api/lecture-notes/download-zip')
@login_required
@require_lms_authentication
@limiter.limit("30 per day")
def download_lecture_notes_zip():
    """Return Cloudflare-only part instructions; never fetch lecture file bytes."""
    if request.args.get('stream') == '1':
        return _generate_cloudrun_memory_zip()
    if os.environ.get('ZIP_NATIVE_DOWNLOADS', 'false').lower() != 'true':
        return jsonify(success=False, error='Bulk downloads are temporarily unavailable. Download lectures individually.'), 503
    account_id = None
    try:
        account_id = get_active_account_id()
        if not account_id:
            return jsonify({'success': False, 'error': 'No active account found'}), 401

        semester = request.args.get('semester')
        course = request.args.get('course')

        if not course:
            return jsonify({'success': False, 'error': 'Course parameter is required'}), 400

        # Fetch lecture notes data — use get_lecture_notes_for_course with the same
        # cache key the UI uses (semester_code:course_code), so we always hit the
        # same cached data that was already displayed to the user.
        try:
            lecture_notes = lms_service.get_lecture_notes_for_course(
                account_id, semester_code=semester, course_code=course, force_refresh=False
            )
        except Exception as e:
            logger.error(f"Failed to fetch lecture notes for zip: {e}")
            return jsonify({'success': False, 'error': 'Failed to fetch lecture notes'}), 500

        # Collect all file URLs with their real filenames and file types from lecture_files_info

        file_urls = []
        for note in lecture_notes:
            files = note.lecture_files if hasattr(note, 'lecture_files') else (note.get('lecture_files') if isinstance(note, dict) else [])
            files_info = note.lecture_files_info if hasattr(note, 'lecture_files_info') else (note.get('lecture_files_info') if isinstance(note, dict) else [])
            if files:
                for i, url in enumerate(files):
                    if url:
                        # Fix double-slash collapsing (same as /download/ route)
                        if url.startswith('https:/') and not url.startswith('https://'):
                            url = url.replace('https:/', 'https://', 1)
                        elif url.startswith('http:/') and not url.startswith('http://'):
                            url = url.replace('http:/', 'http://', 1)
                        if '/Download.php' in url and '/Student/' not in url and 'lms.bahria.edu.pk' in url:
                            url = url.replace('/Download.php', '/Student/Download.php')
                        if url.startswith(('http://', 'https://')):
                            # Get real filename and type from info if available
                            real_name = None
                            file_type = None
                            if files_info and i < len(files_info):
                                info_item = files_info[i]
                                if isinstance(info_item, dict):
                                    real_name = info_item.get('name')
                                    file_type = info_item.get('type')
                                elif isinstance(info_item, str):
                                    real_name = info_item
                            file_urls.append((note, url, real_name, file_type))

        if not file_urls:
            return jsonify({'success': False, 'error': 'No downloadable lecture files found for this course'}), 404

        from app.utils.lecture_downloads import lecture_manifest, lecture_parts
        from app.utils.download_token import generate_download_token
        import time

        files, manifest = lecture_manifest(file_urls)
        expected_manifest = request.args.get('manifest')
        if expected_manifest and expected_manifest != manifest:
            return jsonify(success=False, code='manifest_changed',
                           error='Lecture list changed. Review the updated download parts.'), 409
        try:
            limit = int(request.args.get('part_size', '20'))
            start = int(request.args.get('start', '0'))
            end = int(request.args.get('end', str(len(files))))
            selections = lecture_parts(files, limit, start, end)
        except ValueError:
            return jsonify(success=False, error='Invalid lecture selection.'), 400

        worker = file_worker_origin()
        if not worker:
            return jsonify(success=False, error='File proxy is not configured.'), 503

        # Create authenticated LMS session only to obtain cookies, never lecture bytes.
        from app.services.lms_scraper_service import LMSScraperService
        lms_session = LMSScraperService()._create_authenticated_session(account_id)
        cookies = lms_session.cookies.get_dict()
        friendly_course_name = request.args.get('course_name') or course
        safe_course = re.sub(r'[^\w\- ]', '', friendly_course_name).strip().replace(' ', '_') or 'Course'
        expires_at = int(time.time()) + 900
        parts = []
        for number, selection in enumerate(selections, 1):
            first, last = selection[0]['index'], selection[-1]['index'] + 1
            subset = start != 0 or end != len(files)
            suffix = f'_Files_{first + 1:02d}-{last:02d}' if subset else (
                f'_Part_{number:02d}_of_{len(selections):02d}' if len(selections) > 1 else '')
            filename = f'{safe_course}_Lectures{suffix}.zip'
            payload = [{'url': file['url'], 'name': file['name']} for file in selection]
            parts.append(dict(
                start=first, end=last, count=len(selection),
                weeks=list(dict.fromkeys(file['week'] for file in selection if file['week'] is not None)),
                filename=filename, method='POST', download_url=worker + '/zip',
                form_fields={'token': generate_download_token(payload, cookies), 'filename': filename},
                expires_at=expires_at,
                files=[dict(name=file['name'], index=file['index'], week=file['week'],
                            method='POST', download_url=worker + '/download', expires_at=expires_at,
                            form_fields={'token': generate_download_token([payload[i]], cookies),
                                         'filename': file['name']}) for i, file in enumerate(selection)]
            ))
        response = jsonify(success=True, manifest=manifest, total_files=len(files),
                           course_name=friendly_course_name, part_size=limit, parts=parts)
        response.headers['Cache-Control'] = 'private, no-store'
        return response

    except Exception as error:
        logger.error('Lecture download instructions failed: %s', type(error).__name__)
        return jsonify(success=False, error='Could not prepare downloads. Refresh LMS and retry.'), 502

# ==========================================
# GROUP ASSIGNMENT SUBMISSION ROUTES
# ==========================================

def _groups_disabled():
    """Return 404 when group feature is disabled in production."""
    from app.config import get_config
    if get_config().is_production:
        abort(404)

@lms_bp.route('/groups', methods=['GET'])
@login_required
@require_lms_authentication
def groups_page():
    """Render the Group Management page"""
    _groups_disabled()
    active_account = current_user.get_active_account()
    enrollment = active_account.cms_username if active_account else None
    return render_template('lms/lms_groups.html', active_account=active_account, enrollment=enrollment)

@lms_bp.route('/api/groups', methods=['GET'])
@login_required
@require_lms_authentication
def get_user_groups():
    """Get all groups for the current user"""
    _groups_disabled()
    active_account = current_user.get_active_account()
    enrollment = active_account.cms_username if active_account else None
    
    if not enrollment:
        return jsonify({'success': False, 'error': 'No enrollment number found'})
        
    user_groups = group_manager.get_user_groups(enrollment)
    return jsonify({'success': True, 'groups': user_groups})

@lms_bp.route('/api/groups/create', methods=['POST'])
@login_required
@require_lms_authentication
def create_group():
    """Create a new group"""
    _groups_disabled()
    active_account = current_user.get_active_account()
    creator_enrollment = active_account.cms_username if active_account else None
    
    if not creator_enrollment:
        return jsonify({'success': False, 'error': 'No active account found'})
        
    data = request.json
    assignment_id = data.get('assignment_id')
    course_id = data.get('course_id')
    partner_enrollments = data.get('partners', [])
    
    if not assignment_id or not course_id:
        return jsonify({'success': False, 'error': 'Missing assignment or course details'})
        
    if not partner_enrollments:
        return jsonify({'success': False, 'error': 'No partners provided'})
        
    # Validate partners exist
    from app.models.account import AccountManager
    account_mgr = AccountManager()
    users_data = account_mgr.load_users()
    
    registered_enrollments = set()
    for uid, udata in users_data.items():
        acc_dict = udata.get('cms_account', {})
        enr = acc_dict.get('cms_username', '').strip().lower()
        if enr:
            registered_enrollments.add(enr)
            
    for partner in partner_enrollments:
        p_clean = partner.strip().lower()
        if p_clean and p_clean not in registered_enrollments:
            return jsonify({
                'success': False, 
                'error': f'User {partner.strip()} is not yet registered on the website.'
            })
            
    # All partners exist, create group
    group_id = group_manager.create_group(
        assignment_id=assignment_id,
        course_id=course_id,
        creator_enrollment=creator_enrollment,
        partner_enrollments=partner_enrollments
    )
    
    return jsonify({'success': True, 'group_id': group_id, 'message': 'Group created and invites sent.'})

@lms_bp.route('/api/groups/<group_id>/respond', methods=['POST'])
@login_required
@require_lms_authentication
def respond_group_invite(group_id):
    """Accept or decline a group invite"""
    _groups_disabled()
    active_account = current_user.get_active_account()
    enrollment = active_account.cms_username if active_account else None
    
    data = request.json
    action = data.get('action') # 'accept' or 'decline'
    
    if action not in ['accept', 'decline']:
        return jsonify({'success': False, 'error': 'Invalid action'})
        
    group = group_manager.get_group(group_id)
    if not group:
        return jsonify({'success': False, 'error': 'Group not found'})
        
    logs = group_manager.load_audit_logs().get(group_id, [])
    has_uploaded = any('Upload' in log['action'] for log in logs)
        
    new_status = 'Accepted' if action == 'accept' else 'Declined'
    
    success, msg = group_manager.update_member_status(group_id, enrollment, new_status)
    if not success:
        return jsonify({'success': False, 'error': msg})
        
    # Log the action
    group_manager.append_audit_log(
        group_id=group_id,
        actor=enrollment,
        action=f"{new_status} Invite",
        target_file="-",
        sync_status="-"
    )
    
    msg_suffix = ""
    if action == 'accept' and has_uploaded:
        msg_suffix = ". WARNING: The group has already submitted the assignment. The group owner must re-upload the submission so your account is included."
    elif action == 'decline' and has_uploaded:
        msg_suffix = ". WARNING: The group previously uploaded a submission to your LMS account. You must manually delete it from your assignments dashboard if you wish to upload your own."
    
    return jsonify({'success': True, 'message': f'Invite {new_status.lower()}{msg_suffix}'})

@lms_bp.route('/api/groups/my_invites', methods=['GET'])
@login_required
@require_lms_authentication
def get_my_group_invites():
    """Get all group invites for the current user"""
    _groups_disabled()
    active_account = current_user.get_active_account()
    enrollment = active_account.cms_username if active_account else None

    if not enrollment:
        return jsonify({'success': False, 'error': 'No enrollment found'})
        
    user_groups = group_manager.get_user_groups(enrollment)
    
    # Format for frontend
    formatted_groups = []
    for g in user_groups:
        roster = g.get('roster', {})
        my_status = roster.get(enrollment.lower(), 'Unknown')
        owner = next((enr for enr, status in roster.items() if status == 'Accepted'), 'Unknown') # Crude approximation since owner is always accepted first
        
        # We don't have assignment title explicitly saved in groups.json, but we can pass assignment_id
        formatted_groups.append({
            'group_id': g.get('group_id'),
            'assignment_id': g.get('assignment_id'),
            'course_id': g.get('course_id'),
            'status': my_status,
            'state': g.get('state'),
            'owner': owner,
            'created_at': g.get('created_at'),
            'partners': list(roster.keys())
        })
        
    return jsonify({'success': True, 'groups': formatted_groups})

@lms_bp.route('/api/groups/<group_id>/logs', methods=['GET'])
@login_required
@require_lms_authentication
def get_group_logs(group_id):
    """Get audit logs for a group"""
    _groups_disabled()
    logs = group_manager.load_audit_logs()
    group_logs = logs.get(group_id, [])
    return jsonify({'success': True, 'logs': group_logs})

@lms_bp.route('/api/groups/assignment_logs', methods=['GET'])
@login_required
@require_lms_authentication
def get_assignment_group_logs():
    """Get audit logs for a group associated with an assignment"""
    _groups_disabled()
    active_account = current_user.get_active_account()
    enrollment = active_account.cms_username if active_account else None
    
    if not enrollment:
        return jsonify({'success': False, 'error': 'No enrollment found'})
        
    assignment_id = request.args.get('assignment_id')
    course_id = request.args.get('course_id')
    
    group = group_manager.get_group_by_assignment_and_owner(assignment_id, enrollment)
    if not group:
        return jsonify({'success': True, 'logs': [], 'message': 'No group found for this assignment'})
        
    group_id = group['group_id']
    logs = group_manager.load_audit_logs()
    group_logs = logs.get(group_id, [])
    
    # Also return the current partners to populate the textarea
    partners = [enr for enr in group.get('roster', {}).keys() if enr != enrollment.lower()]
    
    return jsonify({'success': True, 'logs': group_logs, 'partners': partners, 'state': group.get('state'), 'group_id': group_id})

@lms_bp.route('/api/groups/upload', methods=['POST'])
@login_required
@require_lms_authentication
def upload_group_assignment():
    """Upload assignment for a group"""
    _groups_disabled()
    active_account = current_user.get_active_account()
    if not active_account:
        return jsonify({'success': False, 'error': 'No active account found'})
        
    file = request.files.get('file')
    if not file:
        return jsonify({'success': False, 'error': 'No file provided'})
    file.seek(0, 2)
    file_size = file.tell()
    file.seek(0)
    if file_size > ASSIGNMENT_UPLOAD_MAX_SIZE:
        return jsonify({'success': False, 'error': 'File size must be 15MB or less'}), 400
        
    group_id = request.form.get('group_id')
    assignment_no = request.form.get('assignment_no')
    semester = request.form.get('semester')
    course = request.form.get('course')
    assignment_id = request.form.get('assignment_id')
    offered_course_id = request.form.get('offered_course_id')
    
    if not group_id or not assignment_no or not semester or not course:
        return jsonify({'success': False, 'error': 'Missing required parameters'})
        
    try:
        assignment_no = int(assignment_no)
    except ValueError:
        return jsonify({'success': False, 'error': 'Invalid assignment number'})

    file_data = file.read()
    filename = file.filename
    content_type = file.content_type
    
    from app.models.account import AccountManager
    account_mgr = AccountManager()
    
    lms_service.group_upload_assignment(
        group_manager=group_manager,
        account_manager=account_mgr,
        group_id=group_id,
        file_data=file_data,
        filename=filename,
        content_type=content_type,
        semester=semester,
        course=course,
        assignment_no=assignment_no,
        assignment_id=assignment_id,
        offered_course_id=offered_course_id
    )
    
    return jsonify({'success': True, 'message': 'Group upload started'})

@lms_bp.route('/api/groups/delete', methods=['POST'])
@login_required
@require_lms_authentication
def delete_group_assignment():
    """Delete assignment for a group"""
    _groups_disabled()
    active_account = current_user.get_active_account()
    if not active_account:
        return jsonify({'success': False, 'error': 'No active account found'})
        
    data = request.json
    group_id = data.get('group_id')
    assignment_no = data.get('assignment_no')
    semester = data.get('semester')
    course = data.get('course')
    submission_id = data.get('submission_id')
    assignment_id = data.get('assignment_id')
    offered_course_id = data.get('offered_course_id')
    
    if not group_id or not assignment_no or not semester or not course:
        return jsonify({'success': False, 'error': 'Missing required parameters'})
        
    from app.models.account import AccountManager
    account_mgr = AccountManager()
    
    lms_service.group_delete_assignment(
        group_manager=group_manager,
        account_manager=account_mgr,
        group_id=group_id,
        semester=semester,
        course=course,
        assignment_no=assignment_no,
        submission_id=submission_id,
        assignment_id=assignment_id,
        offered_course_id=offered_course_id
    )
    
    return jsonify({'success': True, 'message': 'Group delete started'})
