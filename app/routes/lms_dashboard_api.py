# app/routes/lms_dashboard_api.py
"""
LMS Dashboard API Blueprint
Provides REST API endpoints for dashboard widgets with optimized caching.
"""

from flask import Blueprint, jsonify, request
from flask_login import login_required, current_user
from datetime import datetime
from typing import Optional
import logging

from app.services.lms_dashboard_service import LMSDashboardService
from app.services.lms_service import LMSService
from app.services.lms_background_service import get_background_service
from app.services.lms_cache_service import get_cache_service
from app.utils.lms_logger import log_info, log_warning, log_error, log_user_activity, log_performance
from app.utils.limiter import limiter

# Configure logging
logger = logging.getLogger(__name__)

# Create blueprint
lms_dashboard_api_bp = Blueprint('lms_dashboard_api', __name__, url_prefix='/lms/api/dashboard')

# Initialize services (will be properly initialized in app factory)
lms_service = None
dashboard_service = None


def get_active_account_id() -> Optional[str]:
    """
    Get the active account ID for the current user.
    
    Returns:
        Account ID or None if not found
    """
    try:
        if not current_user.is_authenticated:
            return None
        active_account = current_user.get_active_account()
        return active_account.account_id if active_account else None
    except Exception as e:
        logger.error(f"Error getting active account ID: {e}")
        return None


def require_lms_authentication(f):
    """
    Decorator to ensure user has valid LMS authentication.
    Returns JSON error if not authenticated.
    """
    def decorated_function(*args, **kwargs):
        try:
            account_id = get_active_account_id()
            if not account_id:
                log_warning("API request without active account", 
                           endpoint=request.endpoint)
                return jsonify({
                    'success': False,
                    'error': 'No active CMS account found',
                    'error_code': 'NO_ACCOUNT',
                    'action_required': 'configure_account'
                }), 401
            
            # Check if CMS credentials exist
            active_account = current_user.get_active_account()
            if not active_account.cms_username or not active_account.cms_password:
                log_warning("API request without CMS credentials",
                           account_id=account_id, endpoint=request.endpoint)
                return jsonify({
                    'success': False,
                    'error': 'CMS credentials not configured',
                    'error_code': 'NO_CREDENTIALS',
                    'action_required': 'configure_credentials'
                }), 401
            
            return f(*args, **kwargs)
        except Exception as e:
            log_error(f"Error in API authentication check: {e}",
                     endpoint=request.endpoint, exc_info=True)
            return jsonify({
                'success': False,
                'error': 'Authentication check failed',
                'error_code': 'AUTH_ERROR'
            }), 500
    
    decorated_function.__name__ = f.__name__
    return decorated_function


# API Endpoints

@lms_dashboard_api_bp.route('/pending-assignments', methods=['GET'])
@login_required
@require_lms_authentication
def get_pending_assignments():
    """
    Get pending and missed assignments for dashboard widget.
    
    Query Parameters:
        force_refresh (bool): Force immediate refresh bypassing cache
    
    Returns:
        JSON with pending/missed assignments and metadata
    """
    start_time = datetime.now()
    account_id = None
    
    try:
        account_id = get_active_account_id()
        force_refresh = request.args.get('force_refresh', 'false').lower() == 'true'
        
        if force_refresh:
            user = current_user
            cms_account = user.cms_account
            prefs = cms_account.lms_preferences or {}
            now = datetime.now()
            today = now.strftime('%Y-%m-%d')
            
            # 1. Cooldown limit (10 seconds)
            last_refresh_str = prefs.get('last_manual_refresh')
            if last_refresh_str:
                try:
                    last_refresh = datetime.fromisoformat(last_refresh_str)
                    if (now - last_refresh).total_seconds() < 10:
                        return jsonify({
                            'error': 'Please wait 10 seconds between manual refreshes.',
                            'error_code': 'REFRESH_COOLDOWN',
                        }), 429
                except ValueError:
                    pass
            
            # 2. Daily limit (15)
            daily_date = prefs.get('daily_refresh_date')
            count = prefs.get('daily_refresh_count', 0)
            if daily_date == today:
                if count >= 15:
                    return jsonify({
                        'error': 'Daily manual refresh limit (15) reached.',
                        'error_code': 'REFRESH_DAILY_LIMIT',
                    }), 429
            else:
                count = 0
                
            # 3. Update limits
            prefs['last_manual_refresh'] = now.isoformat()
            prefs['daily_refresh_date'] = today
            prefs['daily_refresh_count'] = count + 1
            cms_account.lms_preferences = prefs
            user.save()
        
        client_version = request.cookies.get('lms_snapshot_version')
        
        log_info(f"API: Getting pending assignments for {account_id}",
                account_id=account_id, operation="api_pending_assignments",
                force_refresh=force_refresh, client_version=client_version)
        
        # Get widget data from dashboard service
        widget_data = dashboard_service.get_pending_assignments_widget_data(
            account_id, force_refresh=force_refresh, min_version=client_version
        )

        # Fire assignment notifications using only pending + missed assignments.
        # Using all_assignments would notify for submitted/graded work too.
        try:
            from app.models.lms_models import LMSAssignment
            from app.services.assignment_notification_service import get_assignment_notification_service
            pending_dicts = widget_data.get('pending', [])
            missed_dicts = widget_data.get('missed', [])
            actionable = pending_dicts + missed_dicts
            if actionable:
                assignments_objs = [LMSAssignment.from_dict(a) for a in actionable]
                notif_svc = get_assignment_notification_service()
                notif_svc.check_and_notify(
                    user_id=current_user.id,
                    assignments=assignments_objs,
                    user=current_user,
                    account_id=account_id,
                    send_push=False  # Dashboard context - don't send redundant web push
                )
        except Exception as notif_err:
            logger.error(f"Error firing assignment notifications: {notif_err}", exc_info=True)
        
        # Check if there's an error in the widget data
        if (widget_data.get('error') or widget_data.get('refresh_failed') or
                widget_data.get('failed_courses') or widget_data.get('warning')):
            log_warning(f"Widget data contains error: {widget_data.get('error')}",
                       account_id=account_id)
            
            # Log performance even on error
            duration = (datetime.now() - start_time).total_seconds()
            log_performance("api_pending_assignments", duration,
                           account_id=account_id, success=False, error=widget_data.get('error'))
            
            # Return partial success with error message
            # Saved data may be included, but the attempted refresh did not succeed.
            response = jsonify({
                **widget_data,
                'success': False,
                'error': widget_data.get('error') or 'Could not refresh assignments.',
                'user_message': widget_data.get('user_message') or 'Could not refresh assignments. Please try again later.',
            })
            response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
            return response, 200
        
        # Log user activity
        log_user_activity("view_pending_assignments", account_id=account_id,
                         pending_count=widget_data.get('total_pending', 0),
                         missed_count=widget_data.get('total_missed', 0))
        
        # Log performance
        duration = (datetime.now() - start_time).total_seconds()
        log_performance("api_pending_assignments", duration,
                       account_id=account_id, success=True,
                       force_refresh=force_refresh)
        
        response = jsonify({
            'success': True,
            **widget_data
        })
        response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
        response.headers['Pragma'] = 'no-cache'
        response.headers['Expires'] = '0'
        
        if 'refreshed_at' in widget_data:
            response.set_cookie('lms_snapshot_version', widget_data['refreshed_at'], max_age=86400, httponly=True, samesite='Lax')
            
        return response, 200
        
    except Exception as e:
        duration = (datetime.now() - start_time).total_seconds()
        log_error(f"Error getting pending assignments: {e}",
                 error_code="API_PENDING_ASSIGNMENTS_ERROR",
                 account_id=account_id, operation="api_pending_assignments",
                 duration=duration, exc_info=True)
        
        log_performance("api_pending_assignments", duration,
                       account_id=account_id, success=False, error=str(e))
        
        return jsonify({
            'success': False,
            'error': 'Unable to load your assignments at this time',
            'error_code': 'FETCH_ERROR',
            'user_message': 'We encountered an issue loading your assignments. Please try refreshing the page.',
            'retry_available': True
        }), 500


@lms_dashboard_api_bp.route('/summary', methods=['GET'])
@login_required
@require_lms_authentication
def get_summary():
    """
    Get quick summary of assignments for dashboard cards.
    Uses aggressive caching (5 minutes) for fast response.
    
    Returns:
        JSON with assignment counts and quick stats
    """
    start_time = datetime.now()
    account_id = None
    
    try:
        account_id = get_active_account_id()
        
        log_info(f"API: Getting assignments summary for {account_id}",
                account_id=account_id, operation="api_summary")
        
        # Get summary from dashboard service
        summary = dashboard_service.get_assignments_summary(account_id)
        
        # Log performance
        duration = (datetime.now() - start_time).total_seconds()
        log_performance("api_summary", duration,
                       account_id=account_id, success=True)
        
        response = jsonify({
            'success': True,
            **summary
        })
        response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
        response.headers['Pragma'] = 'no-cache'
        response.headers['Expires'] = '0'
        return response, 200
        
    except Exception as e:
        duration = (datetime.now() - start_time).total_seconds()
        log_error(f"Error getting assignments summary: {e}",
                 error_code="API_SUMMARY_ERROR",
                 account_id=account_id, operation="api_summary",
                 duration=duration, exc_info=True)
        
        log_performance("api_summary", duration,
                       account_id=account_id, success=False, error=str(e))
        
        return jsonify({
            'success': False,
            'error': 'Unable to load assignment summary',
            'error_code': 'SUMMARY_ERROR',
            'user_message': 'Could not load assignment counts. Please try again.',
            'retry_available': True
        }), 500


@lms_dashboard_api_bp.route('/refresh', methods=['POST'])
@login_required
@require_lms_authentication
@limiter.limit("2 per minute, 10 per hour")
def trigger_refresh():
    """
    Trigger background refresh of pending assignments.
    Returns immediately with task ID for status tracking.
    
    Returns:
        JSON with task ID and status
    """
    start_time = datetime.now()
    account_id = None
    
    try:
        account_id = get_active_account_id()
        
        log_info(f"API: Triggering background refresh for {account_id}",
                account_id=account_id, operation="api_trigger_refresh")
        
        # Get background service
        background_service = get_background_service()
        
        if not background_service:
            log_warning("Background service not available",
                       error_code="BACKGROUND_SERVICE_UNAVAILABLE",
                       account_id=account_id)
            return jsonify({
                'success': False,
                'error': 'Background refresh service is currently unavailable',
                'error_code': 'SERVICE_UNAVAILABLE',
                'user_message': 'Background refresh is not available. Try using the force refresh option instead.',
                'fallback': 'Use force_refresh parameter on pending-assignments endpoint'
            }), 503
        
        # Schedule background refresh with high priority
        task_id = background_service.schedule_pending_assignments_refresh(
            account_id, priority=1, force=True
        )
        
        if not task_id:
            log_info("Refresh skipped - recently updated",
                    account_id=account_id)
            return jsonify({
                'success': True,
                'message': 'Your data was recently refreshed',
                'user_message': 'Your assignments are already up to date',
                'skipped': True
            }), 200
        
        # Log user activity
        log_user_activity("trigger_refresh", account_id=account_id,
                         task_id=task_id)
        
        # Log performance
        duration = (datetime.now() - start_time).total_seconds()
        log_performance("api_trigger_refresh", duration,
                       account_id=account_id, success=True, task_id=task_id)
        
        return jsonify({
            'success': True,
            'message': 'Refresh started in background',
            'user_message': 'Your assignments are being refreshed',
            'task_id': task_id,
            'status_endpoint': f'/lms/api/dashboard/refresh-status/{task_id}'
        }), 202
        
    except Exception as e:
        duration = (datetime.now() - start_time).total_seconds()
        log_error(f"Error triggering refresh: {e}",
                 error_code="API_TRIGGER_REFRESH_ERROR",
                 account_id=account_id, operation="api_trigger_refresh",
                 duration=duration, exc_info=True)
        
        log_performance("api_trigger_refresh", duration,
                       account_id=account_id, success=False, error=str(e))
        
        return jsonify({
            'success': False,
            'error': 'Unable to start refresh',
            'error_code': 'REFRESH_ERROR',
            'user_message': 'Could not start the refresh process. Please try again.',
            'retry_available': True
        }), 500


@lms_dashboard_api_bp.route('/refresh-status/<task_id>', methods=['GET'])
@login_required
@require_lms_authentication
@limiter.limit("60 per minute")
def get_refresh_status(task_id: str):
    """
    Check status of background refresh task.
    
    Args:
        task_id: Task ID returned from refresh endpoint
    
    Returns:
        JSON with task status and progress
    """
    start_time = datetime.now()
    account_id = None
    
    try:
        account_id = get_active_account_id()
        
        log_info(f"API: Checking refresh status for task {task_id}",
                account_id=account_id, operation="api_refresh_status",
                task_id=task_id)
        
        # Get background service
        background_service = get_background_service()
        
        if not background_service:
            log_warning("Background service not available",
                       error_code="BACKGROUND_SERVICE_UNAVAILABLE",
                       account_id=account_id, task_id=task_id)
            return jsonify({
                'success': False,
                'error': 'Background service not available',
                'error_code': 'SERVICE_UNAVAILABLE',
                'user_message': 'Cannot check refresh status at this time'
            }), 503
        
        # Get task status
        task_status = background_service.get_task_status(task_id)
        
        if not task_status:
            log_warning(f"Task not found: {task_id}",
                       error_code="TASK_NOT_FOUND",
                       account_id=account_id, task_id=task_id)
            return jsonify({
                'success': False,
                'error': 'Task not found',
                'error_code': 'TASK_NOT_FOUND',
                'user_message': 'The refresh task could not be found. It may have expired.',
                'task_id': task_id
            }), 404
        
        # Log performance
        duration = (datetime.now() - start_time).total_seconds()
        log_performance("api_refresh_status", duration,
                       account_id=account_id, success=True,
                       task_id=task_id, is_complete=task_status.get('is_complete'))
        
        return jsonify({
            'success': True,
            **task_status
        }), 200
        
    except Exception as e:
        duration = (datetime.now() - start_time).total_seconds()
        log_error(f"Error getting refresh status: {e}",
                 error_code="API_REFRESH_STATUS_ERROR",
                 account_id=account_id, operation="api_refresh_status",
                 task_id=task_id, duration=duration, exc_info=True)
        
        log_performance("api_refresh_status", duration,
                       account_id=account_id, success=False,
                       task_id=task_id, error=str(e))
        
        return jsonify({
            'success': False,
            'error': 'Failed to get task status',
            'error_code': 'STATUS_ERROR',
            'user_message': 'Could not check refresh status. Please try again.',
            'retry_available': True
        }), 500


@lms_dashboard_api_bp.route('/cache-stats', methods=['GET'])
@login_required
@require_lms_authentication
def get_cache_stats():
    """
    Get cache statistics for monitoring and debugging.
    
    Returns:
        JSON with cache and background service statistics
    """
    start_time = datetime.now()
    account_id = None
    
    try:
        account_id = get_active_account_id()
        
        log_info(f"API: Getting cache stats for {account_id}",
                account_id=account_id, operation="api_cache_stats")
        
        # Get cache service
        cache_service = get_cache_service()
        
        # Get background service
        background_service = get_background_service()
        
        # Collect statistics with error handling for each component
        cache_stats = {}
        bg_stats = {}
        
        try:
            if cache_service:
                cache_stats = cache_service.get_stats()
        except Exception as cache_error:
            log_warning(f"Failed to get cache stats: {cache_error}",
                       account_id=account_id)
            cache_stats = {'error': 'Failed to retrieve cache statistics'}
        
        try:
            if background_service:
                bg_stats = background_service.get_stats()
        except Exception as bg_error:
            log_warning(f"Failed to get background service stats: {bg_error}",
                       account_id=account_id)
            bg_stats = {'error': 'Failed to retrieve background service statistics'}
        
        # Collect statistics
        stats = {
            'cache_stats': cache_stats,
            'background_service': bg_stats,
            'timestamp': datetime.now().isoformat()
        }
        
        # Log performance
        duration = (datetime.now() - start_time).total_seconds()
        log_performance("api_cache_stats", duration,
                       account_id=account_id, success=True)
        
        return jsonify({
            'success': True,
            **stats
        }), 200
        
    except Exception as e:
        duration = (datetime.now() - start_time).total_seconds()
        log_error(f"Error getting cache stats: {e}",
                 error_code="API_CACHE_STATS_ERROR",
                 account_id=account_id, operation="api_cache_stats",
                 duration=duration, exc_info=True)
        
        log_performance("api_cache_stats", duration,
                       account_id=account_id, success=False, error=str(e))
        
        return jsonify({
            'success': False,
            'error': 'Failed to get cache statistics',
            'error_code': 'STATS_ERROR',
            'user_message': 'Could not retrieve system statistics',
            'retry_available': True
        }), 500


# Error handlers for the blueprint

@lms_dashboard_api_bp.errorhandler(404)
def api_not_found(error):
    """Handle 404 errors for API endpoints"""
    log_warning(f"API endpoint not found: {request.path}",
               error_code="API_NOT_FOUND",
               endpoint=request.path, method=request.method)
    
    return jsonify({
        'success': False,
        'error': 'API endpoint not found',
        'error_code': 'NOT_FOUND',
        'user_message': 'The requested API endpoint does not exist',
        'requested_path': request.path
    }), 404


@lms_dashboard_api_bp.errorhandler(500)
def api_internal_error(error):
    """Handle 500 errors for API endpoints"""
    log_error(f"API internal error: {error}",
             error_code="API_INTERNAL_ERROR",
             endpoint=request.path, method=request.method, exc_info=True)
    
    return jsonify({
        'success': False,
        'error': 'Internal server error',
        'error_code': 'INTERNAL_ERROR',
        'user_message': 'An unexpected error occurred. Please try again later.',
        'retry_available': True
    }), 500


# Blueprint initialization function
def init_dashboard_api(app):
    """
    Initialize the dashboard API blueprint with required services.
    Should be called from app factory after services are initialized.
    
    Args:
        app: Flask application instance
    """
    global lms_service, dashboard_service
    
    # Get services from app context
    lms_service = app.lms_service
    
    # Initialize dashboard service with background service
    background_service = get_background_service()
    dashboard_service = LMSDashboardService(lms_service, background_service)
    
    log_info("LMS Dashboard API initialized",
            has_background_service=background_service is not None)
