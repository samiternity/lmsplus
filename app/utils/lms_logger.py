# app/utils/lms_logger.py
import logging
import logging.handlers
import os
import json
import re
from datetime import datetime
from typing import Dict, Any, Optional
from pathlib import Path

_account_enrollment_cache: Dict[str, str] = {}

def register_account_enrollment(account_id: str, enrollment: str):
    """Store mapping from account UUID to student enrollment number"""
    if account_id and enrollment:
        acc_str = str(account_id).strip()
        enr_str = str(enrollment).strip()
        if acc_str and enr_str:
            _account_enrollment_cache[acc_str] = enr_str

def resolve_enrollment(account_id: Optional[str]) -> Optional[str]:
    """Resolve account UUID to student enrollment number (e.g., 02-000000-001)"""
    if not account_id:
        return account_id
    account_str = str(account_id).strip()
    if not account_str:
        return account_id

    global _account_enrollment_cache
    if len(_account_enrollment_cache) > 5000:
        _account_enrollment_cache.clear()

    if account_str in _account_enrollment_cache:
        return _account_enrollment_cache[account_str]

    # If it already looks like an enrollment number (e.g. 02-000000-001), cache and return
    if account_str.count('-') == 2 and len(account_str) < 20:
        _account_enrollment_cache[account_str] = account_str
        return account_str

    try:
        from app.models.account import User
        user = User.get_by_account_id(account_str)
        if user and user.cms_account and user.cms_account.cms_username:
            enrollment = user.cms_account.cms_username
            _account_enrollment_cache[account_str] = enrollment
            return enrollment
        elif user and user.username:
            _account_enrollment_cache[account_str] = user.username
            return user.username
        else:
            # Cache negative result to prevent repeated database queries on every log line
            _account_enrollment_cache[account_str] = account_str
    except Exception:
        _account_enrollment_cache[account_str] = account_str
    return account_str

EMOJI_PATTERN = re.compile(r'[\U00010000-\U0010ffff\u2600-\u26FF\u2700-\u27BF\uFE00-\uFE0F\u200D]')

def strip_emojis(text: str) -> str:
    if not text:
        return text
    cleaned = EMOJI_PATTERN.sub('', str(text))
    return re.sub(r'  +', ' ', cleaned).strip()

class LMSLogFormatter(logging.Formatter):
    """Custom formatter for LMS logs with structured output"""
    
    def __init__(self):
        super().__init__()
        
    def format(self, record):
        # Create structured log entry
        log_entry = {
            'timestamp': datetime.fromtimestamp(record.created).isoformat(),
            'level': record.levelname,
            'logger': record.name,
            'message': strip_emojis(record.getMessage()),
            'module': record.module,
            'function': record.funcName,
            'line': record.lineno
        }
        
        # Add extra fields if present
        if hasattr(record, 'error_code'):
            log_entry['error_code'] = record.error_code
            
        cms_id = None
        if hasattr(record, 'account_id') and record.account_id:
            cms_id = resolve_enrollment(record.account_id)
        
        try:
            from flask import has_request_context, g
            if not cms_id and has_request_context() and getattr(g, 'cms_id', None):
                cms_id = g.cms_id
        except ImportError:
            pass
            
        if cms_id:
            log_entry['account_id'] = cms_id
            
        if hasattr(record, 'operation'):
            log_entry['operation'] = record.operation
        if hasattr(record, 'category'):
            log_entry['category'] = record.category
        if hasattr(record, 'severity'):
            log_entry['severity'] = record.severity
        
        # Add exception info if present
        if record.exc_info:
            log_entry['exception'] = self.formatException(record.exc_info)
        
        return json.dumps(log_entry, ensure_ascii=False)

class LMSLogger:
    """
    Centralized logging configuration for LMS operations.
    Provides structured logging with different handlers for different purposes.
    """
    
    def __init__(self, log_dir: str = "logs"):
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(exist_ok=True)
        
        # Configure loggers
        self._setup_loggers()
    
    def _setup_loggers(self):
        """Setup different loggers for different purposes"""
        
        # Main LMS logger
        self.lms_logger = logging.getLogger('lms')
        self.lms_logger.setLevel(logging.DEBUG)
        
        # Error logger for critical issues
        self.error_logger = logging.getLogger('lms.errors')
        self.error_logger.setLevel(logging.ERROR)
        
        # Performance logger for monitoring
        self.performance_logger = logging.getLogger('lms.performance')
        self.performance_logger.setLevel(logging.INFO)
        
        # Security logger for authentication and access
        self.security_logger = logging.getLogger('lms.security')
        self.security_logger.setLevel(logging.INFO)
        
        # User activity logger
        self.activity_logger = logging.getLogger('lms.activity')
        self.activity_logger.setLevel(logging.INFO)
        
        # Setup handlers
        self._setup_handlers()
    
    def _setup_handlers(self):
        """Setup log handlers for stdout JSON"""
        
        # Console handler for everything (Cloud Run/Functions captures this)
        console_handler = logging.StreamHandler()
        console_handler.setLevel(logging.INFO)  # Keep INFO, drop DEBUG for prod/free tier
        console_handler.setFormatter(LMSLogFormatter())
        
        # Add handler to loggers
        self.lms_logger.addHandler(console_handler)
        self.error_logger.addHandler(console_handler)
        self.performance_logger.addHandler(console_handler)
        self.security_logger.addHandler(console_handler)
        self.activity_logger.addHandler(console_handler)
        
        # Prevent duplicate logs
        self.lms_logger.propagate = False
        self.error_logger.propagate = False
        self.performance_logger.propagate = False
        self.security_logger.propagate = False
        self.activity_logger.propagate = False
    
    def _prepare_log_target(self, message: str, account_id: Optional[str]):
        resolved = resolve_enrollment(account_id)
        
        try:
            from flask import has_request_context, g
            if not resolved and has_request_context() and getattr(g, 'cms_id', None):
                resolved = g.cms_id
        except ImportError:
            pass

        if resolved and account_id and resolved != account_id:
            message = message.replace(str(account_id), resolved)
            return message, resolved
        return message, resolved or account_id

    def log_error(self, message: str, error_code: Optional[str] = None, 
                  account_id: Optional[str] = None, operation: Optional[str] = None,
                  category: Optional[str] = None, severity: Optional[str] = None,
                  exc_info: bool = False, **kwargs):
        """Log error with structured data"""
        message, account_id = self._prepare_log_target(message, account_id)
        extra = {
            'error_code': error_code,
            'account_id': account_id,
            'operation': operation,
            'category': category,
            'severity': severity,
            **kwargs
        }
        # Remove None values
        extra = {k: v for k, v in extra.items() if v is not None}
        
        self.error_logger.error(message, extra=extra, exc_info=exc_info)
    
    def log_warning(self, message: str, account_id: Optional[str] = None, 
                   operation: Optional[str] = None, **kwargs):
        """Log warning with structured data"""
        message, account_id = self._prepare_log_target(message, account_id)
        extra = {
            'account_id': account_id,
            'operation': operation,
            **kwargs
        }
        # Remove None values and extract exc_info if present
        exc_info = extra.pop('exc_info', False)
        extra = {k: v for k, v in extra.items() if v is not None}
        
        self.lms_logger.warning(message, extra=extra, exc_info=exc_info)
    
    def log_info(self, message: str, account_id: Optional[str] = None, 
                operation: Optional[str] = None, **kwargs):
        """Log info with structured data"""
        message, account_id = self._prepare_log_target(message, account_id)
        extra = {
            'account_id': account_id,
            'operation': operation,
            **kwargs
        }
        # Remove None values
        extra = {k: v for k, v in extra.items() if v is not None}
        
        self.lms_logger.info(message, extra=extra)
    
    def log_debug(self, message: str, account_id: Optional[str] = None, 
                 operation: Optional[str] = None, **kwargs):
        """Log debug with structured data"""
        message, account_id = self._prepare_log_target(message, account_id)
        extra = {
            'account_id': account_id,
            'operation': operation,
            **kwargs
        }
        # Remove None values
        extra = {k: v for k, v in extra.items() if v is not None}
        
        self.lms_logger.debug(message, extra=extra)
    
    def log_performance(self, operation: str, duration: float, account_id: Optional[str] = None,
                       success: bool = True, **kwargs):
        """Log performance metrics"""
        extra = {
            'operation': operation,
            'duration_seconds': duration,
            'account_id': account_id,
            'success': success,
            **kwargs
        }
        # Remove None values
        extra = {k: v for k, v in extra.items() if v is not None}
        
        message = f"Operation '{operation}' completed in {duration:.3f}s (success: {success})"
        self.performance_logger.info(message, extra=extra)
    
    def log_security_event(self, event_type: str, account_id: Optional[str] = None,
                          ip_address: Optional[str] = None, user_agent: Optional[str] = None,
                          success: bool = True, **kwargs):
        """Log security events"""
        extra = {
            'event_type': event_type,
            'account_id': account_id,
            'ip_address': ip_address,
            'user_agent': user_agent,
            'success': success,
            **kwargs
        }
        # Remove None values
        extra = {k: v for k, v in extra.items() if v is not None}
        
        message = f"Security event: {event_type} (success: {success})"
        self.security_logger.info(message, extra=extra)
    
    def log_user_activity(self, activity: str, account_id: Optional[str] = None,
                         resource: Optional[str] = None, **kwargs):
        """Log user activity"""
        extra = {
            'activity': activity,
            'account_id': account_id,
            'resource': resource,
            **kwargs
        }
        # Remove None values
        extra = {k: v for k, v in extra.items() if v is not None}
        
        message = f"User activity: {activity}"
        if resource:
            message += f" on {resource}"
        
        self.activity_logger.info(message, extra=extra)
    
    def get_log_stats(self) -> Dict[str, Any]:
        """Get logging statistics"""
        stats = {
            'log_directory': str(self.log_dir),
            'log_files': {},
            'generated_at': datetime.now().isoformat()
        }
        
        # Get file sizes and modification times
        for log_file in self.log_dir.glob('*.log'):
            try:
                file_stat = log_file.stat()
                stats['log_files'][log_file.name] = {
                    'size_bytes': file_stat.st_size,
                    'size_mb': round(file_stat.st_size / (1024 * 1024), 2),
                    'modified': datetime.fromtimestamp(file_stat.st_mtime).isoformat()
                }
            except Exception as e:
                stats['log_files'][log_file.name] = {'error': str(e)}
        
        return stats

# Global logger instance
lms_logger = LMSLogger()

# Convenience functions for easy access
def log_error(message: str, **kwargs):
    """Convenience function to log errors"""
    lms_logger.log_error(message, **kwargs)

def log_warning(message: str, **kwargs):
    """Convenience function to log warnings"""
    lms_logger.log_warning(message, **kwargs)

def log_info(message: str, **kwargs):
    """Convenience function to log info"""
    lms_logger.log_info(message, **kwargs)

def log_debug(message: str, **kwargs):
    """Convenience function to log debug"""
    lms_logger.log_debug(message, **kwargs)

def log_performance(operation: str, duration: float, **kwargs):
    """Convenience function to log performance"""
    lms_logger.log_performance(operation, duration, **kwargs)

def log_security_event(event_type: str, **kwargs):
    """Convenience function to log security events"""
    lms_logger.log_security_event(event_type, **kwargs)

def log_user_activity(activity: str, **kwargs):
    """Convenience function to log user activity"""
    lms_logger.log_user_activity(activity, **kwargs)