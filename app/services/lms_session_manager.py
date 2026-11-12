# app/services/lms_session_manager.py
import os
import json
import pickle
from datetime import datetime, timedelta
from typing import Dict, Optional, Any
import logging
from app.models.account import account_manager
from app.models.lms_models import LMSCredentials

# Configure logging
logger = logging.getLogger(__name__)

class LMSSessionManager:
    """
    Manages LMS session cookies with secure storage and automatic cleanup.
    Handles session persistence, expiration, and cleanup operations.
    """
    
    def __init__(self):
        self.session_timeout = timedelta(hours=2)  # Default session timeout
        self.cleanup_interval = timedelta(hours=24)  # Cleanup old sessions daily
        self.max_session_age = timedelta(days=7)  # Maximum age for session files
    
    def store_session_cookies(self, account_id: str, cookies: Dict[str, str], 
                            expires_at: Optional[datetime] = None, 
                            session_type: str = 'combined') -> bool:
        """
        Store session cookies securely for an account.
        Supports both CMS and LMS cookies in a unified storage.
        
        Args:
            account_id: Account ID
            cookies: Session cookies dictionary
            expires_at: Session expiration time (defaults to now + session_timeout)
            session_type: Type of session ('cms', 'lms', or 'combined')
            
        Returns:
            True if storage successful, False otherwise
        """
        try:
            if expires_at is None:
                expires_at = datetime.now() + self.session_timeout
            
            logger.debug(f"Storing {session_type} session cookies for account {account_id}")
            
            # Get account data path for LMS sessions
            session_data_path = self._get_session_data_path(account_id)
            os.makedirs(os.path.dirname(session_data_path), exist_ok=True)
            
            # Load existing session data if it exists
            existing_data = self._load_from_firestore(account_id)
            if not existing_data:
                if os.path.exists(session_data_path):
                    try:
                        with open(session_data_path, 'rb') as f:
                            existing_data = pickle.load(f)
                        if not self._validate_session_data(existing_data):
                            existing_data = None
                    except Exception as e:
                        logger.warning(f"Could not load existing session data for account {account_id}: {e}")
                        existing_data = None
            elif not self._validate_session_data(existing_data):
                existing_data = None
            
            # Prepare session data
            if existing_data and session_type != 'combined':
                # Merge with existing cookies
                session_data = existing_data.copy()
                
                # Update cookies based on session type
                if session_type == 'cms':
                    # Separate CMS and LMS cookies
                    cms_cookies = {k: v for k, v in cookies.items() 
                                 if any(domain in k.lower() for domain in ['cms', 'bahria']) or 
                                    k.lower() in ['asp.net_sessionid', 'viewstate', '__viewstate']}
                    session_data['cms_cookies'] = cms_cookies
                    
                    # Keep existing LMS cookies if they exist
                    if 'lms_cookies' not in session_data:
                        session_data['lms_cookies'] = {}
                        
                elif session_type == 'lms':
                    # Separate LMS cookies
                    lms_cookies = {k: v for k, v in cookies.items() 
                                 if any(domain in k.lower() for domain in ['lms']) or 
                                    k.lower() in ['phpsessid', 'lms_session']}
                    session_data['lms_cookies'] = lms_cookies
                    
                    # Keep existing CMS cookies if they exist
                    if 'cms_cookies' not in session_data:
                        session_data['cms_cookies'] = {}
                
                # Merge all cookies for backward compatibility
                all_cookies = {}
                all_cookies.update(session_data.get('cms_cookies', {}))
                all_cookies.update(session_data.get('lms_cookies', {}))
                session_data['cookies'] = all_cookies
                
            else:
                # Create new session data
                session_data = {
                    'account_id': account_id,
                    'cookies': cookies,
                    'created_at': datetime.now().isoformat(),
                    'expires_at': expires_at.isoformat(),
                    'last_accessed': datetime.now().isoformat(),
                    'session_type': session_type
                }
                
                # Separate CMS and LMS cookies for better management
                cms_cookies = {k: v for k, v in cookies.items() 
                             if any(domain in k.lower() for domain in ['cms', 'bahria']) or 
                                k.lower() in ['asp.net_sessionid', 'viewstate', '__viewstate']}
                lms_cookies = {k: v for k, v in cookies.items() 
                             if any(domain in k.lower() for domain in ['lms']) or 
                                k.lower() in ['phpsessid', 'lms_session']}
                
                # Store both separated and combined cookies
                session_data['cms_cookies'] = cms_cookies
                session_data['lms_cookies'] = lms_cookies
            
            # Update timestamps
            session_data['expires_at'] = expires_at.isoformat()
            session_data['last_accessed'] = datetime.now().isoformat()
            
            # Store session data securely
            with open(session_data_path, 'wb') as f:
                pickle.dump(session_data, f)
            
            # Set file permissions (read/write for owner only)
            os.chmod(session_data_path, 0o600)
            
            self._save_to_firestore(account_id, session_data)
            
            logger.info(f"Successfully stored {len(cookies)} {session_type} session cookies for account {account_id}")
            return True
            
        except Exception as e:
            logger.error(f"Error storing session cookies for account {account_id}: {e}")
            return False
    
    def load_session_cookies(self, account_id: str, cookie_type: str = 'all') -> Optional[Dict[str, str]]:
        """
        Load session cookies for an account.
        
        Args:
            account_id: Account ID
            cookie_type: Type of cookies to load ('all', 'cms', 'lms')
            
        Returns:
            Session cookies dictionary or None if not available/expired
        """
        try:
            session_data = self._load_from_firestore(account_id)
            session_data_path = self._get_session_data_path(account_id)
            
            if not session_data:
                if not os.path.exists(session_data_path):
                    logger.debug(f"No session file found for account {account_id}")
                    return None
                
                # Load session data
                with open(session_data_path, 'rb') as f:
                    session_data = pickle.load(f)
            
            # Validate session data structure
            if not self._validate_session_data(session_data):
                logger.warning(f"Invalid session data for account {account_id}")
                self._cleanup_session_file(account_id)
                return None
            
            # Check if session has expired
            expires_at = datetime.fromisoformat(session_data['expires_at'])
            if datetime.now() > expires_at:
                logger.debug(f"Session expired for account {account_id}")
                self._cleanup_session_file(account_id)
                return None
            
            # Update last accessed time
            session_data['last_accessed'] = datetime.now().isoformat()
            with open(session_data_path, 'wb') as f:
                pickle.dump(session_data, f)
            self._save_to_firestore(account_id, session_data)
            
            # Return requested cookie type
            if cookie_type == 'cms':
                cookies = session_data.get('cms_cookies', {})
            elif cookie_type == 'lms':
                cookies = session_data.get('lms_cookies', {})
            else:  # 'all' or any other value
                cookies = session_data.get('cookies', {})
            
            logger.debug(f"Successfully loaded {len(cookies)} {cookie_type} session cookies for account {account_id}")
            return cookies
            
        except Exception as e:
            logger.error(f"Error loading session cookies for account {account_id}: {e}")
            # Clean up corrupted session file
            self._cleanup_session_file(account_id)
            return None
    
    def get_cms_cookies(self, account_id: str) -> Optional[Dict[str, str]]:
        """
        Get CMS-specific cookies for an account.
        
        Args:
            account_id: Account ID
            
        Returns:
            CMS cookies dictionary or None if not available
        """
        return self.load_session_cookies(account_id, 'cms')
    
    def get_lms_cookies(self, account_id: str) -> Optional[Dict[str, str]]:
        """
        Get LMS-specific cookies for an account.
        
        Args:
            account_id: Account ID
            
        Returns:
            LMS cookies dictionary or None if not available
        """
        return self.load_session_cookies(account_id, 'lms')
    
    def is_session_valid(self, account_id: str) -> bool:
        """
        Check if session is valid (exists and not expired).
        
        Args:
            account_id: Account ID
            
        Returns:
            True if session is valid, False otherwise
        """
        try:
            session_data = self._load_from_firestore(account_id)
            session_data_path = self._get_session_data_path(account_id)
            if not session_data:
                if not os.path.exists(session_data_path):
                    return False
                
                # Load and validate session
                with open(session_data_path, 'rb') as f:
                    session_data = pickle.load(f)
            
            if not self._validate_session_data(session_data):
                return False
            
            # Check expiration
            expires_at = datetime.fromisoformat(session_data['expires_at'])
            return datetime.now() <= expires_at
            
        except Exception as e:
            logger.error(f"Error checking session validity for account {account_id}: {e}")
            return False
    
    def refresh_session_with_backoff(self, account_id: str, max_retries: int = 3, 
                                     auth_callback: Optional[callable] = None) -> bool:
        """
        Refresh session with exponential backoff retry logic.
        
        This method clears the existing session and optionally triggers re-authentication
        through a callback function. The callback should handle the actual authentication
        process (CMS login + gateway access).
        
        Args:
            account_id: Account ID
            max_retries: Maximum number of retry attempts
            auth_callback: Optional callback function to perform authentication.
                          Should accept account_id and return True on success.
            
        Returns:
            True if refresh successful, False otherwise
        """
        base_delay = 1.0
        last_error = None
        
        for attempt in range(max_retries):
            try:
                logger.debug(f"Attempting session refresh for account {account_id} (attempt {attempt + 1}/{max_retries})")
                
                # Clear existing session
                self.clear_session(account_id)
                logger.debug(f"Cleared existing session for account {account_id}")
                
                # If auth callback provided, use it to re-authenticate
                if auth_callback:
                    try:
                        logger.debug(f"Invoking authentication callback for account {account_id}")
                        if auth_callback(account_id):
                            logger.info(f"Session refresh successful for account {account_id} on attempt {attempt + 1}")
                            return True
                        else:
                            last_error = "Authentication callback returned False"
                            logger.warning(f"Authentication callback failed for account {account_id}")
                    except Exception as auth_error:
                        last_error = f"Authentication callback error: {auth_error}"
                        logger.warning(f"Authentication callback raised exception for account {account_id}: {auth_error}")
                        raise
                else:
                    # No callback provided, just clear session and return success
                    logger.info(f"Session cleared for refresh on account {account_id} (no auth callback)")
                    return True
                
            except Exception as e:
                last_error = str(e)
                if attempt < max_retries - 1:
                    delay = base_delay * (2 ** attempt)
                    logger.warning(f"Session refresh attempt {attempt + 1} failed for account {account_id}: {e}, retrying in {delay}s")
                    import time
                    time.sleep(delay)
                else:
                    logger.error(f"Session refresh failed after {max_retries} attempts for account {account_id}. Last error: {last_error}")
                    return False
        
        logger.error(f"Session refresh failed after {max_retries} attempts for account {account_id}. Last error: {last_error}")
        return False
    
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
            
            session_data = self._load_from_firestore(account_id)
            session_data_path = self._get_session_data_path(account_id)
            if not session_data:
                if not os.path.exists(session_data_path):
                    logger.debug(f"No session to extend for account {account_id}")
                    return False
                
                # Load session data
                with open(session_data_path, 'rb') as f:
                    session_data = pickle.load(f)
            
            if not self._validate_session_data(session_data):
                return False
            
            # Extend expiration time
            current_expires = datetime.fromisoformat(session_data['expires_at'])
            new_expires = max(datetime.now(), current_expires) + additional_time
            
            session_data['expires_at'] = new_expires.isoformat()
            session_data['last_accessed'] = datetime.now().isoformat()
            
            # Save updated session data
            with open(session_data_path, 'wb') as f:
                pickle.dump(session_data, f)
            self._save_to_firestore(account_id, session_data)
            
            logger.debug(f"Extended session for account {account_id} until {new_expires}")
            return True
            
        except Exception as e:
            logger.error(f"Error extending session for account {account_id}: {e}")
            return False
    
    def clear_session(self, account_id: str) -> bool:
        """
        Clear session data for an account.
        
        Args:
            account_id: Account ID
            
        Returns:
            True if clearing successful, False otherwise
        """
        try:
            logger.info(f"Clearing session for account {account_id}")
            return self._cleanup_session_file(account_id)
            
        except Exception as e:
            logger.error(f"Error clearing session for account {account_id}: {e}")
            return False
    
    def cleanup_expired_sessions(self) -> Dict[str, int]:
        """
        Clean up expired sessions across all accounts.
        
        Returns:
            Dictionary with cleanup statistics
        """
        cleanup_stats = {
            'checked': 0,
            'expired': 0,
            'corrupted': 0,
            'errors': 0
        }
        
        try:
            logger.info("Starting cleanup of expired sessions")
            
            # Get accounts directory
            accounts_dir = account_manager.accounts_dir
            
            if not os.path.exists(accounts_dir):
                logger.debug("No accounts directory found")
                return cleanup_stats
            
            # Iterate through account directories
            for account_id in os.listdir(accounts_dir):
                account_dir = os.path.join(accounts_dir, account_id)
                
                if not os.path.isdir(account_dir):
                    continue
                
                try:
                    session_file = self._get_session_data_path(account_id)
                    
                    if not os.path.exists(session_file):
                        continue
                    
                    cleanup_stats['checked'] += 1
                    
                    # Check if session is expired or corrupted
                    try:
                        with open(session_file, 'rb') as f:
                            session_data = pickle.load(f)
                        
                        if not self._validate_session_data(session_data):
                            logger.debug(f"Removing corrupted session for account {account_id}")
                            os.remove(session_file)
                            cleanup_stats['corrupted'] += 1
                            continue
                        
                        # Check expiration
                        expires_at = datetime.fromisoformat(session_data['expires_at'])
                        if datetime.now() > expires_at:
                            logger.debug(f"Removing expired session for account {account_id}")
                            os.remove(session_file)
                            cleanup_stats['expired'] += 1
                        
                    except (pickle.PickleError, ValueError, KeyError) as e:
                        logger.debug(f"Removing corrupted session file for account {account_id}: {e}")
                        os.remove(session_file)
                        cleanup_stats['corrupted'] += 1
                    
                except Exception as e:
                    logger.warning(f"Error processing session for account {account_id}: {e}")
                    cleanup_stats['errors'] += 1
            
            logger.info(f"Session cleanup completed: {cleanup_stats}")
            return cleanup_stats
            
        except Exception as e:
            logger.error(f"Error during session cleanup: {e}")
            cleanup_stats['errors'] += 1
            return cleanup_stats
    
    def get_session_info(self, account_id: str) -> Optional[Dict[str, Any]]:
        """
        Get session information for an account.
        
        Args:
            account_id: Account ID
            
        Returns:
            Session information dictionary or None if no session
        """
        try:
            session_data = self._load_from_firestore(account_id)
            session_data_path = self._get_session_data_path(account_id)
            if not session_data:
                if not os.path.exists(session_data_path):
                    return None
                
                with open(session_data_path, 'rb') as f:
                    session_data = pickle.load(f)
            
            if not self._validate_session_data(session_data):
                return None
            
            expires_at = datetime.fromisoformat(session_data['expires_at'])
            created_at = datetime.fromisoformat(session_data['created_at'])
            last_accessed = datetime.fromisoformat(session_data['last_accessed'])
            
            # Calculate time remaining
            now = datetime.now()
            time_remaining = (expires_at - now).total_seconds() if expires_at > now else 0
            
            # Determine session health status
            if time_remaining <= 0:
                status = 'expired'
            elif time_remaining < 300:  # Less than 5 minutes
                status = 'expiring_soon'
            elif time_remaining < 1800:  # Less than 30 minutes
                status = 'active_low'
            else:
                status = 'active'
            
            return {
                'account_id': session_data['account_id'],
                'created_at': created_at,
                'expires_at': expires_at,
                'last_accessed': last_accessed,
                'is_valid': time_remaining > 0,
                'time_remaining': time_remaining,
                'cookie_count': len(session_data.get('cookies', {})),
                'cms_cookie_count': len(session_data.get('cms_cookies', {})),
                'lms_cookie_count': len(session_data.get('lms_cookies', {})),
                'status': status,
                'session_type': session_data.get('session_type', 'combined')
            }
            
        except Exception as e:
            logger.error(f"Error getting session info for account {account_id}: {e}", exc_info=True)
            return None
    
    def is_session_expiring_soon(self, account_id: str, threshold_seconds: int = 300) -> bool:
        """
        Check if session is expiring soon (within threshold).
        
        Args:
            account_id: Account ID
            threshold_seconds: Time threshold in seconds (default 5 minutes)
            
        Returns:
            True if session is expiring soon, False otherwise
        """
        try:
            session_info = self.get_session_info(account_id)
            if not session_info:
                return False
            
            return 0 < session_info['time_remaining'] <= threshold_seconds
            
        except Exception as e:
            logger.error(f"Error checking session expiry for account {account_id}: {e}")
            return False
    
    def _get_session_data_path(self, account_id: str) -> str:
        """
        Get the file path for storing session data.
        
        Args:
            account_id: Account ID
            
        Returns:
            File path for session data
        """
        account_dir = account_manager.get_account_directory(account_id)
        lms_dir = os.path.join(account_dir, 'lms')
        return os.path.join(lms_dir, 'session_data.pkl')
    
    def _validate_session_data(self, session_data: Any) -> bool:
        """
        Validate session data structure.
        Supports both old and new session data formats.
        
        Args:
            session_data: Session data to validate
            
        Returns:
            True if valid, False otherwise
        """
        try:
            if not isinstance(session_data, dict):
                return False
            
            required_fields = ['account_id', 'created_at', 'expires_at']
            for field in required_fields:
                if field not in session_data:
                    return False
            
            # Validate datetime fields
            datetime.fromisoformat(session_data['created_at'])
            datetime.fromisoformat(session_data['expires_at'])
            
            # Validate cookies - support both old and new formats
            has_cookies = False
            
            # Check for old format (just 'cookies')
            if 'cookies' in session_data and isinstance(session_data['cookies'], dict):
                has_cookies = True
            
            # Check for new format (separate cms_cookies and lms_cookies)
            if ('cms_cookies' in session_data and isinstance(session_data['cms_cookies'], dict)) or \
               ('lms_cookies' in session_data and isinstance(session_data['lms_cookies'], dict)):
                has_cookies = True
            
            if not has_cookies:
                return False
            
            return True
            
        except (ValueError, TypeError, KeyError):
            return False
    
    def _cleanup_session_file(self, account_id: str) -> bool:
        """
        Remove session file for an account.
        
        Args:
            account_id: Account ID
            
        Returns:
            True if cleanup successful, False otherwise
        """
        try:
            self._delete_from_firestore(account_id)
            
            session_data_path = self._get_session_data_path(account_id)
            
            if os.path.exists(session_data_path):
                os.remove(session_data_path)
                logger.debug(f"Removed session file for account {account_id}")
            
            return True
            
        except Exception as e:
            logger.error(f"Error removing session file for account {account_id}: {e}")
            return False

    def _save_to_firestore(self, account_id: str, session_data: dict) -> bool:
        """Save session data to Firestore"""
        try:
            from app.services.firestore_service import get_firestore_service
            fs = get_firestore_service()
            if fs.is_available():
                from app.utils.session_encryption import encrypt_session
                doc_id = f'{account_id}_lms_session'
                if not fs.save_document('account_data', doc_id, encrypt_session(doc_id, session_data)):
                    return False
                logger.debug(f"Saved LMS session to Firestore for account {account_id}")
                return True
        except Exception as e:
            logger.warning(f"Failed to save LMS session to Firestore for account {account_id}: {e}")
        return False

    def _load_from_firestore(self, account_id: str) -> Optional[dict]:
        """Load session data from Firestore"""
        try:
            from app.services.firestore_service import get_firestore_service
            fs = get_firestore_service()
            if fs.is_available():
                from app.utils.session_encryption import decrypt_session
                doc_id = f'{account_id}_lms_session'
                data = decrypt_session(doc_id, fs.get_document('account_data', doc_id))
                if data:
                    logger.debug(f"Loaded LMS session from Firestore for account {account_id}")
                    return data
        except Exception as e:
            logger.warning(f"Failed to load LMS session from Firestore for account {account_id}: {e}")
        return None

    def _delete_from_firestore(self, account_id: str) -> bool:
        """Delete session data from Firestore"""
        try:
            from app.services.firestore_service import get_firestore_service
            fs = get_firestore_service()
            if fs.is_available():
                fs.delete_document('account_data', f'{account_id}_lms_session')
                logger.debug(f"Deleted LMS session from Firestore for account {account_id}")
                return True
        except Exception as e:
            logger.warning(f"Failed to delete LMS session from Firestore for account {account_id}: {e}")
        return False