# app/services/lms_auth_service.py
import requests
from datetime import datetime, timedelta
from typing import Dict, Optional, Any
import logging
from bs4 import BeautifulSoup
from app.models.account import account_manager
from app.models.lms_models import LMSCredentials
from app.services.lms_session_manager import LMSSessionManager
from app.utils.lms_error_handler import handle_lms_error, create_error_context
from app.utils.lms_logger import log_error, log_warning, log_info, log_debug, log_security_event

# Configure logging
logger = logging.getLogger(__name__)

class LMSAuthenticationError(Exception):
    """Exception raised when LMS authentication fails"""
    pass

class LMSSessionError(Exception):
    """Exception raised when LMS session operations fail"""
    pass

class LMSAuthService:
    """
    Service for managing LMS authentication and session handling.
    Handles credential encryption/decryption and session management.
    """
    
    def __init__(self):
        self.lms_base_url = "https://lms.bahria.edu.pk"
        # Note: LMS requires CMS authentication, so we use CMS login
        self.assignments_url = f"{self.lms_base_url}/Student/Assignments.php"
        self.lecture_notes_url = f"{self.lms_base_url}/Student/LectureNotes.php"
        self.session_timeout = timedelta(hours=2)  # Default session timeout
        
        # Initialize session manager
        self.session_manager = LMSSessionManager()
        
        # Request session configuration
        self.session_config = {
            'timeout': 15,
            'headers': {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
                'Accept-Language': 'en-US,en;q=0.5',
                'Accept-Encoding': 'gzip, deflate',
                'Connection': 'keep-alive',
                'Upgrade-Insecure-Requests': '1'
            }
        }
    
    def authenticate(self, account_id: str) -> bool:
        """
        Validate that CMS credentials exist for the account.
        Since LMS uses CMS credentials, we don't need separate LMS credentials.
        This method validates that CMS credentials are available and can be used for LMS access.
        
        Args:
            account_id: Account ID to validate credentials for
            
        Returns:
            True if CMS credentials are available
            
        Raises:
            LMSAuthenticationError: When CMS credentials are not available
        """
        try:
            log_info(f"Validating CMS credentials for LMS access for account {account_id}",
                    account_id=account_id, operation="authenticate")
            
            # Log security event
            log_security_event("lms_authentication_attempt", account_id=account_id, success=False)
            
            # Get CMS account to check if credentials exist
            cms_account = self._get_cms_account(account_id)
            if not cms_account:
                raise LMSAuthenticationError("CMS account not found")
            
            # Validate CMS credentials exist
            if not cms_account.cms_username or not cms_account.cms_password:
                raise LMSAuthenticationError("CMS credentials are not configured. Please update your CMS credentials.")
            
            log_info(f"CMS credentials validated for account {account_id}",
                    account_id=account_id, operation="authenticate", username=cms_account.cms_username)
            log_security_event("lms_authentication_success", account_id=account_id, 
                             username=cms_account.cms_username, success=True)
            return True
            
        except LMSAuthenticationError:
            # Re-raise authentication errors
            raise
        except Exception as e:
            log_error(f"Unexpected error during authentication for account {account_id}: {e}",
                     account_id=account_id, operation="authenticate", error_type="system_error", exc_info=True)
            raise LMSAuthenticationError(f"Authentication failed: {e}")
    

    
    def _get_cms_account(self, account_id: str):
        """
        Get CMS account by account ID.
        
        Args:
            account_id: Account ID
            
        Returns:
            CMSAccount object or None if not found
        """
        try:
            from app.models.account import User
            
            user = User.get_by_account_id(account_id)
            if not user:
                logger.error(f"User not found for account {account_id}")
                return None
            
            # SINGLE ACCOUNT MODE
            if user.account_id == account_id and user.cms_account:
                return user.cms_account
            
            logger.error(f"CMS account {account_id} not found for user {user.id}")
            return None
            
        except Exception as e:
            logger.error(f"Error getting CMS account for account {account_id}: {e}")
            return None
    
    def _store_session_data(self, account_id: str, session_cookies: Dict[str, str], 
                          session_expires: datetime) -> bool:
        """
        Store LMS session data (cookies and expiration).
        Note: We no longer store separate LMS credentials since we use CMS credentials.
        
        Args:
            account_id: Account ID
            session_cookies: Session cookies from CMS/LMS authentication
            session_expires: Session expiration time
            
        Returns:
            True if storage successful, False otherwise
        """
        try:
            # Get CMS account
            cms_account = self._get_cms_account(account_id)
            if not cms_account:
                logger.error(f"CMS account {account_id} not found")
                return False
            
            # Initialize LMS credentials if not present
            if not cms_account.lms_credentials:
                # Use CMS username since LMS uses same credentials
                cms_account.lms_credentials = LMSCredentials(
                    lms_username=cms_account.cms_username,
                    lms_password_encrypted=None,  # Not storing password separately
                    session_cookies=session_cookies,
                    session_expires=session_expires,
                    last_sync=datetime.now()
                )
            else:
                # Update existing credentials with new session data
                cms_account.lms_credentials.session_cookies = session_cookies
                cms_account.lms_credentials.session_expires = session_expires
                cms_account.lms_credentials.last_sync = datetime.now()
            
            # Save user data
            from app.models.account import User
            user = User.get_by_account_id(account_id)
            if user:
                if user.save():
                    logger.info(f"Successfully stored LMS session data for account {account_id}")
                    return True
                else:
                    logger.error(f"Failed to save user data for account {account_id}")
                    return False
            else:
                logger.error(f"No user found to save session data for account {account_id}")
                return False
            
        except Exception as e:
            logger.error(f"Error storing session data for account {account_id}: {e}")
            return False
    
    def is_session_valid(self, account_id: str, check_connectivity: bool = False) -> bool:
        """
        Check if LMS session is available and valid.
        Checks both session manager and credentials storage.
        
        Args:
            account_id: Account ID to check
            check_connectivity: Whether to test actual connectivity (slower but more accurate)
            
        Returns:
            True if session is valid, False otherwise
        """
        try:
            log_debug(f"Checking session validity for account {account_id} (connectivity check: {check_connectivity})",
                     account_id=account_id, operation="is_session_valid")
            
            # First check if we have CMS credentials (required for LMS access)
            cms_account = self._get_cms_account(account_id)
            if not cms_account or not cms_account.cms_username or not cms_account.cms_password:
                log_debug(f"No CMS credentials available for account {account_id}",
                         account_id=account_id, operation="is_session_valid", 
                         reason="no_cms_credentials")
                return False
            
            # Check session manager for valid session
            if not self.session_manager.is_session_valid(account_id):
                log_debug(f"Session manager reports invalid session for account {account_id}",
                         account_id=account_id, operation="is_session_valid",
                         reason="session_manager_invalid")
                return False
            
            # Check if session is expiring soon
            if self.session_manager.is_session_expiring_soon(account_id):
                log_warning(f"Session is expiring soon for account {account_id}",
                           account_id=account_id, operation="is_session_valid",
                           reason="session_expiring_soon")
                # Still valid but warn about upcoming expiry
            
            # Check LMS credentials for session data
            lms_credentials = self._get_lms_credentials(account_id)
            if lms_credentials and lms_credentials.is_session_valid():
                # Optionally test actual connectivity if requested
                if check_connectivity:
                    if self._test_session_connectivity(lms_credentials):
                        log_debug(f"Session is valid with connectivity confirmed for account {account_id}",
                                 account_id=account_id, operation="is_session_valid")
                        return True
                    else:
                        log_warning(f"Session connectivity test failed for account {account_id}",
                                   account_id=account_id, operation="is_session_valid",
                                   reason="connectivity_test_failed")
                        # Clear invalid session
                        self.session_manager.clear_session(account_id)
                        return False
                else:
                    # Skip connectivity test, just check expiry
                    log_debug(f"Session is valid for account {account_id} (no connectivity test)",
                             account_id=account_id, operation="is_session_valid")
                    return True
            
            log_debug(f"No valid session found for account {account_id}",
                     account_id=account_id, operation="is_session_valid",
                     reason="no_valid_credentials")
            return False
            
        except Exception as e:
            log_error(f"Error checking session validity for account {account_id}: {e}",
                     account_id=account_id, operation="is_session_valid", 
                     error_type="system_error", exc_info=True)
            return False    

    def _test_session_connectivity(self, lms_credentials: LMSCredentials) -> bool:
        """
        Test session connectivity by making a lightweight request.
        
        Args:
            lms_credentials: LMS credentials with session data
            
        Returns:
            True if session is working, False otherwise
        """
        try:
            # Create session with stored cookies
            session = requests.Session()
            session.headers.update(self.session_config['headers'])
            
            # Set cookies from stored session
            for name, value in lms_credentials.session_cookies.items():
                session.cookies.set(name, value)
            
            # Make a lightweight request to test connectivity
            test_response = session.get(
                self.assignments_url,
                timeout=10  # Shorter timeout for connectivity test
            )
            
            # Check if we're redirected to login (session expired)
            if 'login.php' in test_response.url.lower():
                return False
            
            # Check for successful response
            return test_response.status_code == 200
            
        except Exception as e:
            logger.debug(f"Session connectivity test failed: {e}")
            return False
    
    def refresh_session(self, account_id: str) -> bool:
        """
        Refresh LMS session by validating CMS credentials.
        Since LMS uses CMS authentication, we just validate that CMS credentials exist.
        
        Args:
            account_id: Account ID to refresh session for
            
        Returns:
            True if CMS credentials are available, False otherwise
            
        Raises:
            LMSSessionError: When CMS credentials are not available
        """
        try:
            log_info(f"Refreshing LMS session for account {account_id}",
                    account_id=account_id, operation="refresh_session")
            
            # Get CMS account
            cms_account = self._get_cms_account(account_id)
            if not cms_account:
                log_error(f"CMS account not found for account {account_id}",
                         account_id=account_id, operation="refresh_session", error_type="no_account")
                raise LMSSessionError("CMS account not found")
            
            # Validate CMS credentials exist
            if not cms_account.cms_username or not cms_account.cms_password:
                log_error(f"CMS credentials not configured for account {account_id}",
                         account_id=account_id, operation="refresh_session", error_type="no_credentials")
                raise LMSSessionError("CMS credentials are not configured")
            
            # Credentials are valid
            log_info(f"LMS session refresh successful for account {account_id}",
                    account_id=account_id, operation="refresh_session")
            return True
            
        except LMSSessionError:
            # Re-raise session errors
            raise
        except Exception as e:
            log_error(f"Unexpected error refreshing session for account {account_id}: {e}",
                     account_id=account_id, operation="refresh_session", error_type="system_error", exc_info=True)
            raise LMSSessionError(f"Session refresh failed: {e}")
    
    def logout(self, account_id: str) -> bool:
        """
        Clear LMS session data and logout.
        
        Args:
            account_id: Account ID to logout
            
        Returns:
            True if logout successful, False otherwise
        """
        try:
            log_info(f"Logging out LMS session for account {account_id}",
                    account_id=account_id, operation="logout")
            
            # Log security event
            log_security_event("lms_logout_attempt", account_id=account_id, success=False)
            
            # Clear session in session manager
            session_cleared = self.session_manager.clear_session(account_id)
            
            # Load user and clear LMS session
            from app.models.account import User
            user = User.get(account_id)
            if not user:
                log_warning(f"User not found for account {account_id}",
                           account_id=account_id, operation="logout", error_type="user_not_found")
                return session_cleared
            
            # Get the active CMS account
            cms_account = user.get_active_account()
            if not cms_account:
                log_warning(f"No active CMS account found for user {account_id}",
                           account_id=account_id, operation="logout", error_type="no_active_account")
                return session_cleared
            
            # Clear LMS session data in credentials
            credentials_cleared = False
            if cms_account.lms_credentials:
                cms_account.clear_lms_session()
                
                # Save updated user data
                if user.save():
                    credentials_cleared = True
                    log_info(f"Successfully cleared LMS credentials for account {account_id}",
                            account_id=account_id, operation="logout")
                else:
                    log_error(f"Failed to save logout state for account {account_id}",
                             account_id=account_id, operation="logout", error_type="save_error")
            else:
                log_debug(f"No LMS credentials to clear for account {account_id}",
                         account_id=account_id, operation="logout")
                credentials_cleared = True
            
            # Return true if either session manager or credentials were cleared successfully
            success = session_cleared or credentials_cleared
            if success:
                log_info(f"Successfully logged out LMS session for account {account_id}",
                        account_id=account_id, operation="logout")
                log_security_event("lms_logout_success", account_id=account_id, success=True)
            else:
                log_warning(f"Logout partially failed for account {account_id}",
                           account_id=account_id, operation="logout")
                log_security_event("lms_logout_failed", account_id=account_id, success=False)
            
            return success
            
        except Exception as e:
            log_error(f"Error during logout for account {account_id}: {e}",
                     account_id=account_id, operation="logout", error_type="system_error", exc_info=True)
            log_security_event("lms_logout_error", account_id=account_id, success=False, error=str(e))
            return False
    
    def get_session_cookies(self, account_id: str) -> Optional[Dict[str, str]]:
        """
        Get current session cookies for account.
        
        Args:
            account_id: Account ID
            
        Returns:
            Session cookies dictionary or None if not available
        """
        try:
            # Try session manager first (more secure storage)
            cookies = self.session_manager.load_session_cookies(account_id)
            if cookies:
                return cookies
            
            # Fallback to credentials storage
            lms_credentials = self._get_lms_credentials(account_id)
            if lms_credentials and lms_credentials.is_session_valid():
                return lms_credentials.session_cookies
            
            return None
            
        except Exception as e:
            logger.error(f"Error getting session cookies for account {account_id}: {e}")
            return None
    
    def update_session_expiry(self, account_id: str, new_expiry: Optional[datetime] = None) -> bool:
        """
        Update session expiry time.
        
        Args:
            account_id: Account ID
            new_expiry: New expiry time (defaults to current time + session_timeout)
            
        Returns:
            True if update successful, False otherwise
        """
        try:
            if new_expiry is None:
                new_expiry = datetime.now() + self.session_timeout
            
            # Load user and update session expiry
            from app.models.account import User
            user = User.get(account_id)
            if not user:
                return False
            
            cms_account = user.get_active_account()
            if not cms_account or not cms_account.lms_credentials:
                return False
            
            # Update expiry time
            cms_account.lms_credentials.session_expires = new_expiry
            
            # Save user data
            return user.save()
            
        except Exception as e:
            logger.error(f"Error updating session expiry for account {account_id}: {e}")
            return False
    
    def _get_lms_credentials(self, account_id: str) -> Optional[LMSCredentials]:
        """
        Get LMS credentials for account.
        
        Args:
            account_id: Account ID
            
        Returns:
            LMSCredentials object or None if not found
        """
        try:
            from app.models.account import User
            
            user = User.get_by_account_id(account_id)
            if not user:
                return None
            
            # SINGLE ACCOUNT MODE
            if user.account_id == account_id and user.cms_account:
                return user.cms_account.lms_credentials
            
            return None
            
        except Exception as e:
            logger.error(f"Error getting LMS credentials for account {account_id}: {e}")
            return None
    
    def has_credentials(self, account_id: str) -> bool:
        """
        Check if account has CMS credentials configured (used for LMS access).
        
        Args:
            account_id: Account ID to check
            
        Returns:
            True if CMS credentials exist, False otherwise
        """
        try:
            cms_account = self._get_cms_account(account_id)
            return (cms_account is not None and 
                   cms_account.cms_username and 
                   cms_account.cms_password)
            
        except Exception as e:
            logger.error(f"Error checking credentials for account {account_id}: {e}")
            return False
    

    
    def get_session_status(self, account_id: str) -> Dict[str, Any]:
        """
        Get comprehensive session status information.
        
        Args:
            account_id: Account ID
            
        Returns:
            Dictionary with session status information
        """
        try:
            lms_credentials = self._get_lms_credentials(account_id)
            
            if not lms_credentials:
                return {
                    'has_credentials': False,
                    'is_valid': False,
                    'expires_at': None,
                    'last_sync': None,
                    'status': 'no_credentials'
                }
            
            is_valid = lms_credentials.is_session_valid()
            
            return {
                'has_credentials': True,
                'is_valid': is_valid,
                'expires_at': lms_credentials.session_expires.isoformat() if lms_credentials.session_expires else None,
                'last_sync': lms_credentials.last_sync.isoformat() if lms_credentials.last_sync else None,
                'status': 'valid' if is_valid else 'expired',
                'username': lms_credentials.lms_username
            }
            
        except Exception as e:
            logger.error(f"Error getting session status for account {account_id}: {e}")
            return {
                'has_credentials': False,
                'is_valid': False,
                'expires_at': None,
                'last_sync': None,
                'status': 'error',
                'error': str(e)
            }
    
    def cleanup_expired_sessions(self) -> Dict[str, int]:
        """
        Clean up expired sessions across all accounts.
        
        Returns:
            Dictionary with cleanup statistics
        """
        try:
            logger.info("Starting cleanup of expired LMS sessions")
            return self.session_manager.cleanup_expired_sessions()
            
        except Exception as e:
            logger.error(f"Error during session cleanup: {e}")
            return {'checked': 0, 'expired': 0, 'corrupted': 0, 'errors': 1}
    
    def extend_session(self, account_id: str, additional_time: Optional[timedelta] = None) -> bool:
        """
        Extend session expiration time.
        
        Args:
            account_id: Account ID
            additional_time: Additional time to add (defaults to session_timeout)
            
        Returns:
            True if extension successful, False otherwise
        """
        try:
            if additional_time is None:
                additional_time = self.session_timeout
            
            log_info(f"Extending session for account {account_id} by {additional_time}",
                    account_id=account_id, operation="extend_session")
            
            # Extend in session manager
            session_extended = self.session_manager.extend_session(account_id, additional_time)
            
            # Also update credentials
            new_expiry = datetime.now() + additional_time
            credentials_updated = self.update_session_expiry(account_id, new_expiry)
            
            success = session_extended or credentials_updated
            
            if success:
                log_info(f"Successfully extended session for account {account_id}",
                        account_id=account_id, operation="extend_session")
            else:
                log_warning(f"Failed to extend session for account {account_id}",
                           account_id=account_id, operation="extend_session")
            
            return success
            
        except Exception as e:
            log_error(f"Error extending session for account {account_id}: {e}",
                     account_id=account_id, operation="extend_session",
                     error_type="system_error", exc_info=True)
            return False
    
    def refresh_session_if_needed(self, account_id: str, force: bool = False) -> bool:
        """
        Refresh session if it's expired or expiring soon.
        
        Args:
            account_id: Account ID
            force: Force refresh even if session is still valid
            
        Returns:
            True if session is valid after refresh attempt, False otherwise
        """
        try:
            log_debug(f"Checking if session refresh needed for account {account_id} (force: {force})",
                     account_id=account_id, operation="refresh_session_if_needed")
            
            # Check if refresh is needed
            if not force:
                # Check if session is still valid and not expiring soon
                if self.is_session_valid(account_id):
                    if not self.session_manager.is_session_expiring_soon(account_id):
                        log_debug(f"Session is still valid for account {account_id}, no refresh needed",
                                 account_id=account_id, operation="refresh_session_if_needed")
                        return True
                    else:
                        log_info(f"Session is expiring soon for account {account_id}, refreshing",
                                account_id=account_id, operation="refresh_session_if_needed")
                else:
                    log_info(f"Session is invalid for account {account_id}, refreshing",
                            account_id=account_id, operation="refresh_session_if_needed")
            else:
                log_info(f"Forcing session refresh for account {account_id}",
                        account_id=account_id, operation="refresh_session_if_needed")
            
            # Attempt to refresh session
            log_info(f"Refreshing session for account {account_id}",
                    account_id=account_id, operation="refresh_session_if_needed")
            
            # Use session manager's refresh with backoff
            # Note: The actual re-authentication will be handled by the scraper service
            # when it tries to create a new authenticated session
            success = self.session_manager.refresh_session_with_backoff(account_id)
            
            if success:
                log_info(f"Session refresh successful for account {account_id}",
                        account_id=account_id, operation="refresh_session_if_needed")
                log_security_event("lms_session_refreshed", account_id=account_id, success=True)
            else:
                log_error(f"Session refresh failed for account {account_id}",
                         account_id=account_id, operation="refresh_session_if_needed",
                         error_type="refresh_failed")
                log_security_event("lms_session_refresh_failed", account_id=account_id, success=False)
            
            return success
            
        except Exception as e:
            log_error(f"Error refreshing session for account {account_id}: {e}",
                     account_id=account_id, operation="refresh_session_if_needed",
                     error_type="system_error", exc_info=True)
            log_security_event("lms_session_refresh_error", account_id=account_id, 
                             success=False, error=str(e))
            return False
    
    def get_detailed_session_status(self, account_id: str) -> Dict[str, Any]:
        """
        Get detailed session status including both session manager and credentials info.
        
        Args:
            account_id: Account ID
            
        Returns:
            Comprehensive session status dictionary
        """
        try:
            # Get basic status from credentials
            basic_status = self.get_session_status(account_id)
            
            # Get detailed info from session manager
            session_info = self.session_manager.get_session_info(account_id)
            
            # Combine information
            detailed_status = basic_status.copy()
            
            if session_info:
                detailed_status.update({
                    'session_manager': {
                        'created_at': session_info['created_at'].isoformat(),
                        'last_accessed': session_info['last_accessed'].isoformat(),
                        'time_remaining': session_info['time_remaining'],
                        'cookie_count': session_info['cookie_count']
                    }
                })
            else:
                detailed_status['session_manager'] = None
            
            return detailed_status
            
        except Exception as e:
            logger.error(f"Error getting detailed session status for account {account_id}: {e}")
            return {
                'has_credentials': False,
                'is_valid': False,
                'status': 'error',
                'error': str(e),
                'session_manager': None
            }