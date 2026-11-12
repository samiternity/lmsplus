"""
LMS Performance Monitor
Tracks LMS server response times without making extra requests
"""
from datetime import datetime, timedelta
from flask import session
from functools import wraps
import time


class LMSMonitor:
    """Monitor LMS performance by piggybacking on existing requests"""
    
    @staticmethod
    def record_request(response_time_ms, success=True, error_message=None):
        """
        Record LMS request performance in session
        
        Args:
            response_time_ms: Response time in milliseconds
            success: Whether the request was successful
            error_message: Optional error message if request failed
        """
        try:
            from flask import has_request_context
            
            # Only record if we're in a request context
            if not has_request_context():
                return
            
            session['lms_last_check'] = datetime.now().isoformat()
            session['lms_response_time'] = round(response_time_ms, 2)
            session['lms_status'] = 'online' if success else 'error'
            if error_message:
                session['lms_error'] = error_message
            session.modified = True
        except Exception as e:
            # Silently fail if session is not available
            pass
    
    @staticmethod
    def get_status():
        """
        Get current LMS status from session
        
        Returns:
            dict: Status information including response time and last check
        """
        last_check = session.get('lms_last_check')
        response_time = session.get('lms_response_time', 0)
        status = session.get('lms_status', 'unknown')
        error_message = session.get('lms_error')
        
        # If no recent data, return unknown
        if not last_check:
            return {
                'status': 'unknown',
                'response_time': 0,
                'signal_strength': 'unknown',
                'message': 'No recent LMS activity'
            }
        
        # Check if data is stale (> 5 minutes)
        try:
            last_check_time = datetime.fromisoformat(last_check)
            age_seconds = (datetime.now() - last_check_time).total_seconds()
            
            if age_seconds > 300:  # 5 minutes
                return {
                    'status': 'stale',
                    'response_time': response_time,
                    'signal_strength': 'unknown',
                    'message': f'Last checked {int(age_seconds / 60)} minutes ago',
                    'last_check': last_check
                }
        except Exception:
            pass
        
        # Determine signal strength based on response time
        if status == 'error':
            signal_strength = 'offline'
            message = 'LMS server not responding'
            user_message = 'The LMS server is currently unreachable. This is not an issue with our application - the university\'s LMS system is not responding at the moment. Please try again later.'
        elif response_time < 500:
            signal_strength = 'excellent'
            message = 'Excellent connection'
            user_message = None
        elif response_time < 2000:
            signal_strength = 'good'
            message = 'Good connection'
            user_message = None
        else:
            signal_strength = 'poor'
            message = 'Slow connection'
            user_message = 'The LMS server is responding slowly. This is due to the university\'s LMS system performance, not our application.'
        
        result = {
            'status': status,
            'response_time': response_time,
            'signal_strength': signal_strength,
            'message': message,
            'last_check': last_check
        }
        
        if user_message:
            result['user_message'] = user_message
        
        if error_message and status == 'error':
            result['error_detail'] = error_message
        
        return result
    
    @staticmethod
    def clear_status():
        """Clear LMS status from session"""
        session.pop('lms_last_check', None)
        session.pop('lms_response_time', None)
        session.pop('lms_status', None)
        session.pop('lms_error', None)
        session.modified = True


def monitor_lms_request(func):
    """
    Decorator to automatically monitor LMS request performance
    
    Usage:
        @monitor_lms_request
        def fetch_assignments():
            # Your LMS request code
            pass
    """
    @wraps(func)
    def wrapper(*args, **kwargs):
        start_time = time.time()
        try:
            result = func(*args, **kwargs)
            response_time = (time.time() - start_time) * 1000
            LMSMonitor.record_request(response_time, success=True)
            return result
        except Exception as e:
            response_time = (time.time() - start_time) * 1000
            error_msg = str(e)[:100]  # Limit error message length
            LMSMonitor.record_request(response_time, success=False, error_message=error_msg)
            raise
    return wrapper
