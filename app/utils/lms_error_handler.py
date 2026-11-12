# app/utils/lms_error_handler.py
import logging
from datetime import datetime
from typing import Dict, Any, Optional, Tuple, List
from enum import Enum
from dataclasses import dataclass
import traceback
import json

# Import exceptions dynamically to avoid circular imports

# Configure logging
logger = logging.getLogger(__name__)

class ErrorSeverity(Enum):
    """Error severity levels for categorization and handling"""
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"

class ErrorCategory(Enum):
    """Error categories for better organization and handling"""
    AUTHENTICATION = "authentication"
    NETWORK = "network"
    PARSING = "parsing"
    FILE_OPERATION = "file_operation"
    SESSION = "session"
    VALIDATION = "validation"
    SERVICE = "service"
    SYSTEM = "system"

@dataclass
class ErrorContext:
    """Context information for error handling"""
    account_id: Optional[str] = None
    operation: Optional[str] = None
    request_id: Optional[str] = None
    user_agent: Optional[str] = None
    ip_address: Optional[str] = None
    timestamp: Optional[datetime] = None
    additional_data: Optional[Dict[str, Any]] = None

@dataclass
class ErrorInfo:
    """Comprehensive error information"""
    error_type: str
    category: ErrorCategory
    severity: ErrorSeverity
    message: str
    user_message: str
    recovery_suggestions: List[str]
    technical_details: Optional[str] = None
    error_code: Optional[str] = None
    context: Optional[ErrorContext] = None
    timestamp: Optional[datetime] = None

class LMSErrorHandler:
    """
    Comprehensive error handler for LMS operations.
    Provides error categorization, user-friendly messages, and recovery suggestions.
    """
    
    def __init__(self):
        self.error_mappings = self._initialize_error_mappings()
        self.recovery_strategies = self._initialize_recovery_strategies()
        self.error_counters = {}  # For tracking error frequency
        
    def _initialize_error_mappings(self) -> Dict[str, Dict[str, Any]]:
        """Initialize error type mappings with categories and severities"""
        return {
            'LMSAuthenticationError': {
                'category': ErrorCategory.AUTHENTICATION,
                'severity': ErrorSeverity.HIGH,
                'default_message': 'Authentication with LMS failed',
                'recovery_suggestions': [
                    'Verify your LMS username and password',
                    'Check if your LMS account is active',
                    'Try logging into LMS directly to verify credentials',
                    'Contact IT support if the problem persists'
                ]
            },
            'LMSSessionError': {
                'category': ErrorCategory.SESSION,
                'severity': ErrorSeverity.MEDIUM,
                'default_message': 'LMS session has expired or is invalid',
                'recovery_suggestions': [
                    'Re-authenticate with your LMS credentials',
                    'Clear browser cache and cookies',
                    'Try refreshing the page',
                    'Log out and log back in'
                ]
            },
            'LMSNetworkError': {
                'category': ErrorCategory.NETWORK,
                'severity': ErrorSeverity.MEDIUM,
                'default_message': 'Network connection to LMS failed',
                'recovery_suggestions': [
                    'Check your internet connection',
                    'Verify LMS server is accessible',
                    'Try again in a few moments',
                    'Contact network administrator if issue persists'
                ]
            },
            'LMSScrapingError': {
                'category': ErrorCategory.SERVICE,
                'severity': ErrorSeverity.MEDIUM,
                'default_message': 'Failed to retrieve data from LMS',
                'recovery_suggestions': [
                    'Try refreshing the data',
                    'Check if LMS is currently accessible',
                    'Verify your session is still valid',
                    'Contact support if the issue continues'
                ]
            },
            'LMSParsingError': {
                'category': ErrorCategory.PARSING,
                'severity': ErrorSeverity.LOW,
                'default_message': 'Failed to process LMS page content',
                'recovery_suggestions': [
                    'Try refreshing the page',
                    'Clear browser cache',
                    'The LMS page structure may have changed - contact support',
                    'Try accessing the data directly from LMS'
                ]
            },
            'LMSFileError': {
                'category': ErrorCategory.FILE_OPERATION,
                'severity': ErrorSeverity.MEDIUM,
                'default_message': 'File operation failed',
                'recovery_suggestions': [
                    'Check file permissions and availability',
                    'Verify file size is within limits',
                    'Ensure stable internet connection',
                    'Try the operation again'
                ]
            },
            'LMSFileValidationError': {
                'category': ErrorCategory.VALIDATION,
                'severity': ErrorSeverity.LOW,
                'default_message': 'File validation failed',
                'recovery_suggestions': [
                    'Check file format and size requirements',
                    'Ensure file is not corrupted',
                    'Verify file meets assignment requirements',
                    'Try with a different file'
                ]
            },
            'LMSServiceError': {
                'category': ErrorCategory.SERVICE,
                'severity': ErrorSeverity.HIGH,
                'default_message': 'LMS service error occurred',
                'recovery_suggestions': [
                    'Try the operation again',
                    'Check service status',
                    'Contact support if the problem persists',
                    'Use alternative methods if available'
                ]
            }
        }
    
    def _initialize_recovery_strategies(self) -> Dict[ErrorCategory, List[str]]:
        """Initialize recovery strategies by error category"""
        return {
            ErrorCategory.AUTHENTICATION: [
                'redirect_to_login',
                'clear_credentials',
                'show_credential_form',
                'contact_support'
            ],
            ErrorCategory.SESSION: [
                'refresh_session',
                'redirect_to_login',
                'clear_session_data',
                'retry_operation'
            ],
            ErrorCategory.NETWORK: [
                'retry_with_backoff',
                'check_connectivity',
                'use_cached_data',
                'show_offline_mode'
            ],
            ErrorCategory.PARSING: [
                'retry_operation',
                'use_fallback_parser',
                'report_parsing_issue',
                'show_raw_data'
            ],
            ErrorCategory.FILE_OPERATION: [
                'retry_operation',
                'validate_file_again',
                'check_file_permissions',
                'use_alternative_method'
            ],
            ErrorCategory.VALIDATION: [
                'show_validation_errors',
                'provide_correction_hints',
                'allow_override',
                'redirect_to_form'
            ],
            ErrorCategory.SERVICE: [
                'retry_operation',
                'use_cached_data',
                'escalate_to_admin',
                'show_service_status'
            ],
            ErrorCategory.SYSTEM: [
                'log_system_error',
                'notify_administrators',
                'show_generic_error',
                'provide_support_contact'
            ]
        }
    
    def handle_error(self, error: Exception, context: Optional[ErrorContext] = None) -> ErrorInfo:
        """
        Handle an error and return comprehensive error information.
        
        Args:
            error: The exception that occurred
            context: Optional context information
            
        Returns:
            ErrorInfo object with comprehensive error details
        """
        try:
            # Get error mapping
            error_mapping = self._get_error_mapping(error)
            
            # Create error info
            error_info = ErrorInfo(
                error_type=type(error).__name__,
                category=error_mapping['category'],
                severity=error_mapping['severity'],
                message=str(error),
                user_message=self._generate_user_message(error, error_mapping),
                recovery_suggestions=self._get_recovery_suggestions(error, error_mapping),
                technical_details=self._get_technical_details(error),
                error_code=self._generate_error_code(error, error_mapping),
                context=context or ErrorContext(),
                timestamp=datetime.now()
            )
            
            # Log the error
            self._log_error(error_info)
            
            # Update error counters
            self._update_error_counters(error_info)
            
            return error_info
            
        except Exception as e:
            # Fallback error handling
            logger.error(f"Error in error handler: {e}")
            return self._create_fallback_error_info(error, context)
    
    def _get_error_mapping(self, error: Exception) -> Dict[str, Any]:
        """Get error mapping for the given error type"""
        error_type_name = type(error).__name__
        
        # Check for exact match
        if error_type_name in self.error_mappings:
            return self.error_mappings[error_type_name]
        
        # Check for partial matches (e.g., if error contains "LMS" and "Error")
        for mapped_type_name, mapping in self.error_mappings.items():
            if 'LMS' in error_type_name and 'Error' in error_type_name:
                # Return first LMS error mapping as fallback
                if 'LMS' in mapped_type_name and 'Error' in mapped_type_name:
                    return mapping
        
        # Default mapping for unknown errors
        return {
            'category': ErrorCategory.SYSTEM,
            'severity': ErrorSeverity.HIGH,
            'default_message': 'An unexpected error occurred',
            'recovery_suggestions': [
                'Try the operation again',
                'Refresh the page',
                'Contact support if the problem persists',
                'Check system status'
            ]
        }
    
    def _generate_user_message(self, error: Exception, error_mapping: Dict[str, Any]) -> str:
        """Generate user-friendly error message"""
        try:
            error_str = str(error).lower()
            
            # Specific message patterns
            if 'timeout' in error_str:
                return "The operation timed out. Please check your connection and try again."
            elif 'connection' in error_str:
                return "Unable to connect to LMS. Please check your internet connection."
            elif 'authentication' in error_str or 'login' in error_str:
                return "Authentication failed. Please check your LMS credentials."
            elif 'session' in error_str or 'expired' in error_str:
                return "Your LMS session has expired. Please log in again."
            elif 'file' in error_str and 'size' in error_str:
                return "File is too large. Please check the size requirements."
            elif 'file' in error_str and 'format' in error_str:
                return "File format is not supported. Please check the allowed file types."
            elif 'permission' in error_str or 'access' in error_str:
                return "Access denied. Please check your permissions."
            elif 'not found' in error_str or '404' in error_str:
                return "The requested resource was not found."
            elif 'server' in error_str or '500' in error_str:
                return "LMS server error. Please try again later."
            else:
                return error_mapping['default_message']
                
        except Exception:
            return error_mapping['default_message']
    
    def _get_recovery_suggestions(self, error: Exception, error_mapping: Dict[str, Any]) -> List[str]:
        """Get recovery suggestions for the error"""
        try:
            suggestions = error_mapping['recovery_suggestions'].copy()
            
            # Add context-specific suggestions
            error_str = str(error).lower()
            
            if 'timeout' in error_str:
                suggestions.insert(0, 'Check your internet connection speed')
            elif 'file' in error_str and 'size' in error_str:
                suggestions.insert(0, 'Reduce file size or compress the file')
            elif 'session' in error_str:
                suggestions.insert(0, 'Clear browser cookies and cache')
            
            return suggestions[:4]  # Limit to 4 suggestions
            
        except Exception:
            return error_mapping['recovery_suggestions']
    
    def _get_technical_details(self, error: Exception) -> str:
        """Get technical details for debugging"""
        try:
            details = {
                'error_type': type(error).__name__,
                'error_message': str(error),
                'traceback': traceback.format_exc()
            }
            
            # Add specific error attributes if available
            if hasattr(error, 'response'):
                details['response_status'] = getattr(error.response, 'status_code', None)
                details['response_headers'] = dict(getattr(error.response, 'headers', {}))
            
            if hasattr(error, 'request'):
                details['request_url'] = getattr(error.request, 'url', None)
                details['request_method'] = getattr(error.request, 'method', None)
            
            return json.dumps(details, indent=2, default=str)
            
        except Exception:
            return f"{type(error).__name__}: {str(error)}"
    
    def _generate_error_code(self, error: Exception, error_mapping: Dict[str, Any]) -> str:
        """Generate unique error code for tracking"""
        try:
            category_code = error_mapping['category'].value.upper()[:3]
            error_type_code = type(error).__name__[:3].upper()
            timestamp_code = datetime.now().strftime("%Y%m%d%H%M%S")
            
            return f"LMS-{category_code}-{error_type_code}-{timestamp_code}"
            
        except Exception:
            return f"LMS-ERR-{datetime.now().strftime('%Y%m%d%H%M%S')}"
    
    def _log_error(self, error_info: ErrorInfo):
        """Log error with appropriate level based on severity"""
        try:
            # Safely extract context information
            account_id = None
            operation = None
            if error_info.context and hasattr(error_info.context, 'account_id'):
                account_id = error_info.context.account_id
            if error_info.context and hasattr(error_info.context, 'operation'):
                operation = error_info.context.operation
            
            log_data = {
                'error_code': error_info.error_code,
                'error_type': error_info.error_type,
                'category': error_info.category.value,
                'severity': error_info.severity.value,
                'account_id': account_id,
                'operation': operation,
                'timestamp': error_info.timestamp.isoformat() if error_info.timestamp else None
            }
            
            log_message = f"LMS Error [{error_info.error_code}]: {error_info.message}"
            
            if error_info.severity == ErrorSeverity.CRITICAL:
                logger.critical(log_message, extra=log_data)
            elif error_info.severity == ErrorSeverity.HIGH:
                logger.error(log_message, extra=log_data)
            elif error_info.severity == ErrorSeverity.MEDIUM:
                logger.warning(log_message, extra=log_data)
            else:
                logger.info(log_message, extra=log_data)
            
            # Log technical details separately for debugging
            if error_info.technical_details:
                logger.debug(f"Technical details for {error_info.error_code}: {error_info.technical_details}")
                
        except Exception as e:
            logger.error(f"Failed to log error: {e}")
    
    def _update_error_counters(self, error_info: ErrorInfo):
        """Update error counters for monitoring"""
        try:
            counter_key = f"{error_info.category.value}_{error_info.error_type}"
            
            if counter_key not in self.error_counters:
                self.error_counters[counter_key] = {
                    'count': 0,
                    'first_seen': error_info.timestamp,
                    'last_seen': error_info.timestamp,
                    'accounts_affected': set()
                }
            
            self.error_counters[counter_key]['count'] += 1
            self.error_counters[counter_key]['last_seen'] = error_info.timestamp
            
            if error_info.context and hasattr(error_info.context, 'account_id') and error_info.context.account_id:
                self.error_counters[counter_key]['accounts_affected'].add(error_info.context.account_id)
                
        except Exception as e:
            logger.warning(f"Failed to update error counters: {e}")
    
    def _create_fallback_error_info(self, error: Exception, context: Optional[ErrorContext]) -> ErrorInfo:
        """Create fallback error info when main error handling fails"""
        return ErrorInfo(
            error_type=type(error).__name__,
            category=ErrorCategory.SYSTEM,
            severity=ErrorSeverity.HIGH,
            message=str(error),
            user_message="An unexpected error occurred. Please try again or contact support.",
            recovery_suggestions=[
                "Try the operation again",
                "Refresh the page",
                "Contact support if the problem persists"
            ],
            technical_details=f"{type(error).__name__}: {str(error)}",
            error_code=f"LMS-FALLBACK-{datetime.now().strftime('%Y%m%d%H%M%S')}",
            context=context or ErrorContext(),
            timestamp=datetime.now()
        )
    
    def get_error_statistics(self) -> Dict[str, Any]:
        """Get error statistics for monitoring"""
        try:
            total_errors = sum(counter['count'] for counter in self.error_counters.values())
            
            # Convert sets to counts for JSON serialization
            stats = {}
            for key, counter in self.error_counters.items():
                stats[key] = {
                    'count': counter['count'],
                    'first_seen': counter['first_seen'].isoformat() if counter['first_seen'] else None,
                    'last_seen': counter['last_seen'].isoformat() if counter['last_seen'] else None,
                    'accounts_affected_count': len(counter['accounts_affected'])
                }
            
            return {
                'total_errors': total_errors,
                'error_types': stats,
                'generated_at': datetime.now().isoformat()
            }
            
        except Exception as e:
            logger.error(f"Failed to get error statistics: {e}")
            return {'error': str(e)}
    
    def clear_error_counters(self):
        """Clear error counters (for maintenance)"""
        try:
            self.error_counters.clear()
            logger.info("Error counters cleared")
        except Exception as e:
            logger.error(f"Failed to clear error counters: {e}")

# Global error handler instance
lms_error_handler = LMSErrorHandler()

def handle_lms_error(error: Exception, context: Optional[ErrorContext] = None) -> ErrorInfo:
    """
    Convenience function to handle LMS errors.
    
    Args:
        error: The exception that occurred
        context: Optional context information
        
    Returns:
        ErrorInfo object with comprehensive error details
    """
    return lms_error_handler.handle_error(error, context)

def handle_route_error(error: Exception, account_id: str = None, operation: str = None) -> tuple[str, int]:
    """
    Handle LMS errors for route handlers that expect (message, status_code) tuple.
    
    Args:
        error: The exception that occurred
        account_id: Optional account ID for context
        operation: Optional operation name for context
        
    Returns:
        Tuple of (error_message, http_status_code)
    """
    try:
        # Create context if provided
        context = None
        if account_id or operation:
            context = create_error_context(account_id=account_id, operation=operation)
        
        # Get error info
        error_info = handle_lms_error(error, context)
        
        # Determine HTTP status code based on error category
        status_code = 500  # Default server error
        if error_info.category == ErrorCategory.AUTHENTICATION:
            status_code = 401
        elif error_info.category == ErrorCategory.VALIDATION:
            status_code = 400
        elif error_info.category == ErrorCategory.NETWORK:
            status_code = 503
        
        return error_info.user_message, status_code
        
    except Exception as e:
        # Fallback if error handling itself fails
        return f"An unexpected error occurred: {str(error)}", 500

def create_error_context(account_id: Optional[str] = None, operation: Optional[str] = None,
                        request_id: Optional[str] = None, **kwargs) -> ErrorContext:
    """
    Convenience function to create error context.
    
    Args:
        account_id: Account ID
        operation: Operation being performed
        request_id: Request ID for tracking
        **kwargs: Additional context data
        
    Returns:
        ErrorContext object
    """
    return ErrorContext(
        account_id=account_id,
        operation=operation,
        request_id=request_id,
        timestamp=datetime.now(),
        additional_data=kwargs
    )