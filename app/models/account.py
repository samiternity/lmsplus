import logging
logger = logging.getLogger(__name__)

# app/models/account.py
from flask_login import UserMixin
import json
import os
import shutil
import uuid
import time
import stat
from datetime import datetime, timezone
from werkzeug.security import generate_password_hash, check_password_hash
from cryptography.fernet import Fernet
import base64
import hashlib
from .lms_models import LMSCredentials

class AccountManager:
    def __init__(self):
        self.data_dir = os.path.join(os.path.dirname(__file__), '..', 'data')
        self.accounts_dir = os.path.join(self.data_dir, 'accounts')
        self.users_file = os.path.join(self.data_dir, 'users.json')
        self.ensure_directories()
        
        # Initialize encryption key for storing CMS credentials securely
        self.encryption_key = self._get_or_create_encryption_key()
        self.cipher_suite = Fernet(self.encryption_key)
    
    def ensure_directories(self):
        """Ensure all necessary directories exist"""
        os.makedirs(self.data_dir, exist_ok=True)
        os.makedirs(self.accounts_dir, exist_ok=True)
        
        if not os.path.exists(self.users_file):
            with open(self.users_file, 'w') as f:
                json.dump({}, f)
    
    def _get_or_create_encryption_key(self):
        """Get or create encryption key for storing sensitive data.
        Uses SECRET_KEY from environment if available to ensure consistency
        across deployments and environments (like Cloud Run)."""
        import os
        import hashlib
        import base64
        
        # Ensure environment is loaded (for scripts that don't import app first)
        try:
            from app.config import load_environment
            load_environment()
        except ImportError:
            pass
        
        # Allow dedicated key for password encryption so SECRET_KEY can be rotated
        secret_key = os.environ.get('CMS_ENCRYPTION_KEY') or os.environ.get('SECRET_KEY')
        if secret_key:
            # Fernet requires a 32-byte url-safe base64-encoded key
            derived = hashlib.sha256(secret_key.encode()).digest()
            return base64.urlsafe_b64encode(derived)

        key_file = os.path.join(self.data_dir, '.encryption_key')
        
        if os.path.exists(key_file):
            with open(key_file, 'rb') as f:
                return f.read()
        else:
            key = Fernet.generate_key()
            with open(key_file, 'wb') as f:
                f.write(key)
            return key
    
    def encrypt_data(self, data):
        """Encrypt sensitive data"""
        if isinstance(data, str):
            data = data.encode()
        return self.cipher_suite.encrypt(data).decode()
    
    def decrypt_data(self, encrypted_data):
        """Decrypt sensitive data"""
        if isinstance(encrypted_data, str):
            encrypted_data = encrypted_data.encode()
        return self.cipher_suite.decrypt(encrypted_data).decode()
    
    def load_users(self):
        """Load users from JSON file"""
        try:
            with open(self.users_file, 'r') as f:
                return json.load(f)
        except:
            return {}
    
    def save_users(self, users):
        """Save users to JSON file"""
        try:
            with open(self.users_file, 'w') as f:
                json.dump(users, f, indent=2)
            return True
        except Exception as e:
            logger.warning(f"Error saving users: {e}")
            return False
    
    def delete_user_completely(self, user_id):
        """Completely delete a user and all their data"""
        try:
            logger.debug(f"[DELETION] Starting complete deletion of user: {user_id}")
            
            # Load current users
            users = self.load_users()
            user_data = users.get(user_id)
            
            if user_data:
                logger.debug(f"[DELETION] User {user_id} found in users.json")
                
                # Delete all CMS account data
                cms_accounts = user_data.get('cms_accounts', {})
                account_ids = list(cms_accounts.keys())
                if user_data.get('account_id'):
                    account_ids.append(user_data['account_id'])
                if user_data.get('cms_account') and isinstance(user_data['cms_account'], dict):
                    if user_data['cms_account'].get('account_id'):
                        account_ids.append(user_data['cms_account']['account_id'])
                account_ids = list(set(account_ids))
                logger.debug(f"[DELETION] User has {len(account_ids)} CMS accounts to delete")
                
                deleted_accounts = 0
                for account_id in account_ids:
                    account_dir = os.path.join(self.accounts_dir, account_id)
                    logger.debug(f"[DELETION] Checking account directory: {account_dir}")
                    
                    if os.path.exists(account_dir):
                        # List contents before deletion
                        try:
                            contents = os.listdir(account_dir)
                            logger.debug(f"[DELETION] Account {account_id} contains: {contents}")
                        except:
                            logger.debug(f"[DELETION] Could not list contents of {account_dir}")
                        
                        # Use robust deletion method
                        if self._robust_delete_directory(account_dir):
                            deleted_accounts += 1
                            logger.debug(f"[DELETION] Successfully deleted account directory: {account_dir}")
                        else:
                            logger.warning(f"[DELETION] Failed to delete account directory: {account_dir}")
                    else:
                        logger.warning(f"[DELETION] Account directory not found: {account_dir}")
                
                logger.debug(f"[DELETION] Deleted {deleted_accounts} out of {len(cms_accounts)} account directories")
                
                # If some accounts failed to delete, try alternative cleanup
                if deleted_accounts < len(cms_accounts):
                    logger.warning(f"[DELETION] Some accounts failed to delete, attempting alternative cleanup...")
                    self._cleanup_remaining_accounts(cms_accounts, user_id)
                
                # Remove user from users.json
                del users[user_id]
                save_result = self.save_users(users)
                
                # Also remove from Firestore if available
                try:
                    from app.services.firestore_service import get_firestore_service
                    fs = get_firestore_service()
                    if fs and fs.is_available():
                        fs.delete_document('users', user_id)
                except Exception as fe:
                    logger.warning(f"[DELETION] Failed to delete user {user_id} from Firestore: {fe}")
                
                if save_result:
                    logger.debug(f"[DELETION] User {user_id} removed from users.json")
                else:
                    logger.warning(f"[DELETION] Failed to save users.json after removing {user_id}")
                
                # Verify deletion
                updated_users = self.load_users()
                if user_id not in updated_users:
                    logger.debug(f"[DELETION] Verification: User {user_id} successfully removed from system")
                else:
                    logger.warning(f"[DELETION] Verification FAILED: User {user_id} still exists in system!")
                
                logger.debug(f"[DELETION] Complete deletion of user {user_id} finished successfully")
                return True
            else:
                logger.warning(f"[DELETION] User {user_id} not found in users.json")
                return False
                
        except Exception as e:
            logger.warning(f"[DELETION] Error completely deleting user {user_id}: {e}")
            import traceback
            traceback.print_exc()
            return False
    
    def clear_all_data(self):
        """Clear all user data - for testing/debugging only"""
        try:
            logger.debug("WARNING: Clearing all user data!")
            
            # Clear users.json
            self.save_users({})
            
            # Clear all account directories
            if os.path.exists(self.accounts_dir):
                shutil.rmtree(self.accounts_dir)
                os.makedirs(self.accounts_dir, exist_ok=True)
            
            logger.debug("All user data cleared")
            return True
            
        except Exception as e:
            logger.warning(f"Error clearing all data: {e}")
            return False
    
    def _force_remove_readonly(self, func, path, exc_info):
        """
        Error handler for Windows readonly files during deletion
        """
        try:
            logger.debug(f"[DELETION] Attempting to fix readonly file: {path}")
            os.chmod(path, stat.S_IWRITE)
            func(path)
            logger.debug(f"[DELETION] Successfully removed readonly file: {path}")
        except Exception as e:
            logger.warning(f"[DELETION] Failed to remove readonly file {path}: {e}")
    
    def _robust_delete_directory(self, directory_path, max_retries=3):
        """
        Robustly delete a directory with retry logic for Windows
        """
        for attempt in range(max_retries):
            try:
                logger.debug(f"[DELETION] Attempt {attempt + 1}/{max_retries} to delete: {directory_path}")
                
                if not os.path.exists(directory_path):
                    logger.debug(f"[DELETION] Directory already deleted: {directory_path}")
                    return True
                
                # First, try to make all files writable
                try:
                    for root, dirs, files in os.walk(directory_path):
                        for file in files:
                            file_path = os.path.join(root, file)
                            try:
                                os.chmod(file_path, stat.S_IWRITE)
                            except:
                                pass  # Ignore chmod errors
                        for dir in dirs:
                            dir_path = os.path.join(root, dir)
                            try:
                                os.chmod(dir_path, stat.S_IWRITE)
                            except:
                                pass  # Ignore chmod errors
                except Exception as e:
                    logger.warning(f"[DELETION] Could not change permissions: {e}")
                
                # Try to delete with error handler for readonly files
                shutil.rmtree(directory_path, onerror=self._force_remove_readonly)
                
                # Verify deletion
                if not os.path.exists(directory_path):
                    logger.debug(f"[DELETION] Successfully deleted directory: {directory_path}")
                    return True
                else:
                    logger.warning(f"[DELETION] Directory still exists after deletion attempt")
                    
            except PermissionError as e:
                logger.warning(f"[DELETION] Permission error on attempt {attempt + 1}: {e}")
                if attempt < max_retries - 1:
                    logger.debug(f"⏳ [DELETION] Waiting 2 seconds before retry...")
                    time.sleep(2)
                    continue
                else:
                    logger.warning(f"[DELETION] All attempts failed due to permission errors")
                    return False
                    
            except Exception as e:
                logger.warning(f"[DELETION] Unexpected error on attempt {attempt + 1}: {e}")
                if attempt < max_retries - 1:
                    logger.debug(f"⏳ [DELETION] Waiting 1 second before retry...")
                    time.sleep(1)
                    continue
                else:
                    logger.warning(f"[DELETION] All attempts failed")
                    return False
        
        return False
    
    def create_account_directory(self, account_id):
        """Create directory structure for an account"""
        account_dir = os.path.join(self.accounts_dir, account_id)
        os.makedirs(account_dir, exist_ok=True)
        
        # Create subdirectories for different data types
        subdirs = ['attendance', 'timetable', 'gpa', 'community_service', 'sessions', 'history', 'lms']
        for subdir in subdirs:
            os.makedirs(os.path.join(account_dir, subdir), exist_ok=True)
        
        # Create LMS-specific subdirectories
        lms_subdirs = ['assignments', 'lecture_notes', 'cache', 'files']
        lms_dir = os.path.join(account_dir, 'lms')
        for subdir in lms_subdirs:
            os.makedirs(os.path.join(lms_dir, subdir), exist_ok=True)
        
        return account_dir
    
    def get_account_directory(self, account_id):
        """Get account directory path"""
        return os.path.join(self.accounts_dir, account_id)
    
    def migrate_existing_accounts_for_lms(self):
        """Add LMS directories and data structures to existing accounts"""
        try:
            if not os.path.exists(self.accounts_dir):
                logger.debug("[MIGRATION] No accounts directory found, skipping LMS migration")
                return True
            
            migrated_count = 0
            updated_count = 0
            error_count = 0
            
            for account_id in os.listdir(self.accounts_dir):
                account_dir = os.path.join(self.accounts_dir, account_id)
                
                if os.path.isdir(account_dir):
                    try:
                        # Check and create LMS directory structure
                        lms_dir = os.path.join(account_dir, 'lms')
                        directory_created = False
                        
                        if not os.path.exists(lms_dir):
                            os.makedirs(lms_dir, exist_ok=True)
                            directory_created = True
                        
                        # Create LMS-specific subdirectories
                        lms_subdirs = ['assignments', 'lecture_notes', 'cache', 'files']
                        for subdir in lms_subdirs:
                            subdir_path = os.path.join(lms_dir, subdir)
                            if not os.path.exists(subdir_path):
                                os.makedirs(subdir_path, exist_ok=True)
                                directory_created = True
                        
                        # Initialize LMS data structures in account data
                        account_data_file = os.path.join(account_dir, 'account_data.json')
                        if os.path.exists(account_data_file):
                            try:
                                with open(account_data_file, 'r', encoding='utf-8') as f:
                                    account_data = json.load(f)
                                
                                data_updated = False
                                
                                # Initialize LMS cache structure if not present
                                if 'lms_cache' not in account_data:
                                    account_data['lms_cache'] = {}
                                    data_updated = True
                                
                                # Initialize LMS preferences if not present
                                if 'lms_preferences' not in account_data:
                                    account_data['lms_preferences'] = {
                                        'auto_refresh_enabled': False,
                                        'refresh_interval_hours': 4,
                                        'notification_preferences': {
                                            'assignment_reminders': True,
                                            'new_lecture_notes': True
                                        },
                                        'default_semester': None,
                                        'created_at': datetime.now().isoformat()
                                    }
                                    data_updated = True
                                
                                # Save updated account data
                                if data_updated:
                                    with open(account_data_file, 'w', encoding='utf-8') as f:
                                        json.dump(account_data, f, indent=2, ensure_ascii=False)
                                    updated_count += 1
                                
                            except json.JSONDecodeError as e:
                                logger.warning(f"[MIGRATION] Invalid JSON in account {account_id}: {e}")
                                error_count += 1
                            except Exception as e:
                                logger.warning(f"[MIGRATION] Error updating account data for {account_id}: {e}")
                                error_count += 1
                        
                        if directory_created:
                            migrated_count += 1
                            logger.debug(f"[MIGRATION] Added LMS directories to account: {account_id}")
                        
                    except Exception as e:
                        logger.warning(f"[MIGRATION] Error processing account {account_id}: {e}")
                        error_count += 1
            
            # Summary
            total_processed = migrated_count + updated_count
            if total_processed > 0:
                logger.debug(f"[MIGRATION] LMS migration completed:")
                logger.debug(f"Directory structures created: {migrated_count}")
                logger.debug(f"Account data updated: {updated_count}")
                if error_count > 0:
                    logger.warning(f"Errors encountered: {error_count}")
            else:
                logger.debug("[MIGRATION] All accounts already have LMS integration setup")
            
            return error_count == 0
            
        except Exception as e:
            logger.warning(f"[MIGRATION] Critical error during LMS migration: {e}")
            return False
    
    def verify_lms_compatibility(self):
        """Verify that all accounts have proper LMS integration setup"""
        try:
            if not os.path.exists(self.accounts_dir):
                return True, "No accounts directory found"
            
            total_accounts = 0
            compatible_accounts = 0
            issues = []
            
            for account_id in os.listdir(self.accounts_dir):
                account_dir = os.path.join(self.accounts_dir, account_id)
                
                if os.path.isdir(account_dir):
                    total_accounts += 1
                    account_compatible = True
                    account_issues = []
                    
                    # Check LMS directory structure
                    lms_dir = os.path.join(account_dir, 'lms')
                    if not os.path.exists(lms_dir):
                        account_compatible = False
                        account_issues.append("Missing LMS directory")
                    else:
                        # Check subdirectories
                        required_subdirs = ['assignments', 'lecture_notes', 'cache', 'files']
                        for subdir in required_subdirs:
                            subdir_path = os.path.join(lms_dir, subdir)
                            if not os.path.exists(subdir_path):
                                account_compatible = False
                                account_issues.append(f"Missing LMS subdirectory: {subdir}")
                    
                    # Check account data structure
                    account_data_file = os.path.join(account_dir, 'account_data.json')
                    if os.path.exists(account_data_file):
                        try:
                            with open(account_data_file, 'r', encoding='utf-8') as f:
                                account_data = json.load(f)
                            
                            # Check for LMS data structures
                            if 'lms_cache' not in account_data:
                                account_compatible = False
                                account_issues.append("Missing LMS cache structure")
                            
                            if 'lms_preferences' not in account_data:
                                account_compatible = False
                                account_issues.append("Missing LMS preferences structure")
                                
                        except (json.JSONDecodeError, Exception) as e:
                            account_compatible = False
                            account_issues.append(f"Invalid account data: {e}")
                    
                    if account_compatible:
                        compatible_accounts += 1
                    else:
                        issues.append(f"Account {account_id}: {', '.join(account_issues)}")
            
            compatibility_rate = (compatible_accounts / total_accounts * 100) if total_accounts > 0 else 100
            
            summary = f"LMS Compatibility: {compatible_accounts}/{total_accounts} accounts ({compatibility_rate:.1f}%)"
            
            if issues:
                summary += f"\nIssues found:\n" + "\n".join(f"  - {issue}" for issue in issues)
            
            return len(issues) == 0, summary
            
        except Exception as e:
            return False, f"Error during compatibility check: {e}"
    
    def _cleanup_remaining_accounts(self, cms_accounts, user_id):
        """
        Alternative cleanup method for accounts that failed to delete
        """
        try:
            logger.debug(f"[CLEANUP] Starting alternative cleanup for user {user_id}")
            
            for account_id in cms_accounts.keys():
                account_dir = os.path.join(self.accounts_dir, account_id)
                
                if os.path.exists(account_dir):
                    logger.debug(f"[CLEANUP] Attempting individual file deletion for: {account_id}")
                    
                    # Try to delete files individually
                    deleted_files = 0
                    total_files = 0
                    
                    for root, dirs, files in os.walk(account_dir, topdown=False):
                        for file in files:
                            total_files += 1
                            file_path = os.path.join(root, file)
                            try:
                                os.chmod(file_path, stat.S_IWRITE)
                                os.remove(file_path)
                                deleted_files += 1
                            except Exception as e:
                                logger.warning(f"[CLEANUP] Could not delete file {file_path}: {e}")
                        
                        # Try to remove empty directories
                        for dir in dirs:
                            dir_path = os.path.join(root, dir)
                            try:
                                os.rmdir(dir_path)
                            except Exception as e:
                                logger.warning(f"[CLEANUP] Could not remove directory {dir_path}: {e}")
                    
                    # Try to remove the main directory
                    try:
                        os.rmdir(account_dir)
                        logger.debug(f"[CLEANUP] Successfully removed account directory: {account_id}")
                    except Exception as e:
                        logger.warning(f"[CLEANUP] Could not remove main directory {account_dir}: {e}")
                    
                    logger.debug(f"[CLEANUP] Deleted {deleted_files}/{total_files} files for account {account_id}")
                    
        except Exception as e:
            logger.warning(f"[CLEANUP] Error during alternative cleanup: {e}")

# Initialize account manager
account_manager = AccountManager()

class CMSAccount:
    """Represents a CMS account with encrypted credentials"""
    
    def __init__(self, account_id, cms_username, cms_password, display_name=None, 
                 is_active=True, created_at=None, last_login=None, student_name=None,
                 lms_credentials=None, institute_id=None, user_id=None, **kwargs):
        self.account_id = account_id
        self.cms_username = cms_username
        self.cms_password = cms_password  # This will be encrypted when stored
        self.display_name = display_name or cms_username
        self.student_name = student_name  # Actual student name from CMS profile
        from app.routes.auth import get_institute_id_for_enrollment
        resolved_inst = get_institute_id_for_enrollment(cms_username)
        if not institute_id or str(institute_id).strip() == '2':
            self.institute_id = resolved_inst
        else:
            self.institute_id = str(institute_id)
        self.is_active = is_active
        self.created_at = created_at or datetime.now().isoformat()
        self.last_login = last_login
        self.data_directory = account_manager.get_account_directory(account_id)
        self.lms_credentials = lms_credentials  # LMSCredentials object
    
    def to_dict(self):
        """Convert account to dictionary for storage"""
        from app.routes.auth import get_institute_id_for_enrollment
        data = {
            'account_id': self.account_id,
            'cms_username': self.cms_username,
            'display_name': self.display_name,
            'student_name': self.student_name,
            'institute_id': getattr(self, 'institute_id', None) or get_institute_id_for_enrollment(self.cms_username),
            'is_active': self.is_active,
            'created_at': self.created_at,
            'last_login': self.last_login
        }
        
        if self.cms_password:
            data['cms_password_encrypted'] = account_manager.encrypt_data(self.cms_password)
        elif getattr(self, '_raw_encrypted_password', None):
            # Preserve existing ciphertext if decryption failed earlier, never wipe DB password
            data['cms_password_encrypted'] = self._raw_encrypted_password
        
        # Include LMS credentials if available
        if self.lms_credentials:
            data['lms_credentials'] = self.lms_credentials.to_dict()
            
        if hasattr(self, 'lms_cache'):
            data['lms_cache'] = self.lms_cache
            
        if hasattr(self, 'lms_preferences'):
            data['lms_preferences'] = self.lms_preferences
        
        return data
    
    @classmethod
    def from_dict(cls, data):
        """Create account from dictionary"""
        # Load LMS credentials if available
        lms_credentials = None
        if 'lms_credentials' in data:
            try:
                lms_credentials = LMSCredentials.from_dict(data['lms_credentials'])
            except Exception as e:
                logger.warning(f"Error loading LMS credentials for account {data.get('account_id')}: {e}")
        
        from app.routes.auth import get_institute_id_for_enrollment
        raw_inst = data.get('institute_id')
        cms_user = data.get('cms_username')
        resolved_inst = raw_inst or get_institute_id_for_enrollment(cms_user)
        # Self-heal legacy institute_id='2' if username prefix belongs to another campus
        if str(resolved_inst).strip() == '2' and cms_user:
            resolved_inst = get_institute_id_for_enrollment(cms_user)
        
        account = cls(
            account_id=data['account_id'],
            cms_username=data['cms_username'],
            cms_password='',  # Will be set below
            display_name=data.get('display_name'),
            student_name=data.get('student_name'),
            is_active=data.get('is_active', True),
            created_at=data.get('created_at'),
            last_login=data.get('last_login'),
            lms_credentials=lms_credentials,
            institute_id=resolved_inst
        )
        
        # Decrypt password if encrypted
        if 'cms_password_encrypted' in data:
            account._raw_encrypted_password = data['cms_password_encrypted']
            try:
                account.cms_password = account_manager.decrypt_data(data['cms_password_encrypted'])
            except Exception as e:
                logger.warning(f"Error decrypting password for account {account.cms_username or account.account_id}: {e}")
                account.cms_password = ''
        else:
            account.cms_password = data.get('cms_password', '')
            
        account.lms_cache = data.get('lms_cache', {})
        account.lms_preferences = data.get('lms_preferences', {})
        
        return account
    
    def update_last_login(self):
        """Update last login timestamp"""
        self.last_login = datetime.now().isoformat()
    
    def update_student_name(self, student_name):
        """Update student name from profile data"""
        if student_name and student_name.strip():
            stripped_name = student_name.strip()
            # Prevent false positives from the scraper
            bad_names = {'bahria university', 'student profile', 'student details'}
            if stripped_name.lower() in bad_names:
                return
                
            self.student_name = stripped_name
            # Update display name to use student name if not manually set
            if not self.display_name or self.display_name == self.cms_username or self.display_name.lower() in bad_names:
                self.display_name = self.student_name
    
    def get_data_path(self, data_type, filename=None):
        """Get path for storing account-specific data"""
        base_path = os.path.join(self.data_directory, data_type)
        if filename:
            return os.path.join(base_path, filename)
        return base_path
    
    def get_lms_data_path(self, lms_data_type, filename=None):
        """Get path for storing LMS-specific data"""
        base_path = os.path.join(self.data_directory, 'lms', lms_data_type)
        if filename:
            return os.path.join(base_path, filename)
        return base_path
    
    def set_lms_credentials(self, lms_username, lms_password):
        """Set LMS credentials for this account"""
        encrypted_password = account_manager.encrypt_data(lms_password)
        self.lms_credentials = LMSCredentials(
            lms_username=lms_username,
            lms_password_encrypted=encrypted_password
        )
    
    @property
    def fetched_name(self):
        """Get the actual name from the scraped profile via data_manager if available"""
        try:
            from app.utils.data_manager import data_manager
            profile_data = data_manager.load_account_data(self.account_id, 'profile')
            if profile_data:
                name = profile_data.get('name')
                if name:
                    return name.title()
        except Exception:
            pass
        return self.student_name or self.display_name or self.cms_username
    
    def get_lms_password(self):
        """Get decrypted LMS password"""
        if self.lms_credentials and self.lms_credentials.lms_password_encrypted:
            try:
                return account_manager.decrypt_data(self.lms_credentials.lms_password_encrypted)
            except Exception as e:
                logger.warning(f"Error decrypting LMS password for account {self.account_id}: {e}")
                return None
        return None
    
    def has_lms_credentials(self):
        """Check if account has LMS credentials configured (which are now unified with CMS)"""
        return bool(self.cms_username and self.cms_password)
    
    def update_lms_session(self, session_cookies, expires_at=None):
        """Update LMS session data"""
        if not self.lms_credentials:
            return False
        
        self.lms_credentials.session_cookies = session_cookies
        self.lms_credentials.session_expires = expires_at
        return True
    
    def clear_lms_session(self):
        """Clear LMS session data"""
        if self.lms_credentials:
            self.lms_credentials.clear_session()
    
    def is_lms_session_valid(self):
        """Check if LMS session is still valid"""
        if not self.lms_credentials:
            return False
        return self.lms_credentials.is_session_valid()
    
    def update_lms_sync_time(self):
        """Update last LMS sync timestamp"""
        if self.lms_credentials:
            self.lms_credentials.last_sync = datetime.now()

class User(UserMixin):
    """User class with single CMS account support
    
    MULTI-ACCOUNT FEATURE TEMPORARILY DISABLED:
    The multi-account feature has been disabled due to security concerns.
    To re-enable in the future:
    1. Uncomment the cms_accounts field and related methods
    2. Update routes to use cms_accounts instead of cms_account
    3. Re-enable add_account, remove_account, switch_account routes
    4. Implement proper session isolation for multi-account
    """
    
    def __init__(self, id, username, email, password_hash=None, 
                 cms_account=None, account_id=None,
                 # MULTI-ACCOUNT: Uncomment these when re-enabling
                 # cms_accounts=None, active_account_id=None,
                 preferences=None, notification_preferences=None,
                 batch_bucket=None, discord_id=None, discord_username=None, last_active_at=None, created_at=None, session_version=None):
        self.id = id
        self.username = username
        self.email = email
        self.password_hash = password_hash
        self.session_version = session_version
        self.discord_id = discord_id
        self.discord_username = discord_username
        self.last_active_at = last_active_at
        self.created_at = created_at or datetime.now(timezone.utc).isoformat()
        
        # Calculate batch bucket for scalable scheduler (0-59)
        if batch_bucket is not None:
            try:
                self.batch_bucket = int(batch_bucket)
            except (ValueError, TypeError):
                self.batch_bucket = int(hashlib.md5(str(id).encode('utf-8')).hexdigest(), 16) % 60
        else:
            self.batch_bucket = int(hashlib.md5(str(id).encode('utf-8')).hexdigest(), 16) % 60
        
        # SINGLE ACCOUNT MODE (current)
        self.cms_account = cms_account  # Single CMSAccount object
        self.account_id = account_id  # Single account ID
        
        # MULTI-ACCOUNT MODE (disabled)
        # self.cms_accounts = cms_accounts or {}  # Dict of account_id -> CMSAccount
        # self.active_account_id = active_account_id
        
        self.preferences = preferences or {
            'theme': 'dark',
            'notifications': True,
            'auto_refresh': True,
            # MULTI-ACCOUNT: 'default_account': None
        }
        
        # Notification preferences for attendance and assignments
        self.notification_preferences = notification_preferences or {
            # Attendance notifications
            'absent_scheduled': False,
            'absent_unscheduled': False,
            # Assignment notifications
            'new_assignment': False,
            'due_24h': False,
            'due_3d': False,
            'overdue_warning': False,
            # Discord notifications
            'discord_enabled': False
        }
    
    def is_scheduled_check_eligible(self) -> bool:
        """Return True if user has linked Discord, enabled discord notifications, and active alert toggles."""
        if not self.discord_id:
            return False
        d_id_str = str(self.discord_id).strip()
        if not d_id_str or d_id_str.lower() in ('none', 'null'):
            return False

        prefs = getattr(self, 'notification_preferences', None) or {}
        if not isinstance(prefs, dict):
            return False

        discord_enabled = prefs.get('discord_enabled')
        if not (discord_enabled is True or str(discord_enabled).lower() == 'true'):
            return False

        alert_keys = (
            'absent_scheduled',
            'absent_unscheduled',
            'new_assignment',
            'due_24h',
            'due_3d',
            'overdue_warning'
        )
        return any(prefs.get(k) is True or str(prefs.get(k)).lower() == 'true' for k in alert_keys)

    def to_dict(self):
        """Convert user to dictionary for storage"""
        data = {
            'username': self.username,
            'email': self.email,
            'password_hash': self.password_hash,
            'session_version': self.session_version,
            'preferences': self.preferences,
            'notification_preferences': self.notification_preferences,
            'batch_bucket': self.batch_bucket,
            'discord_id': self.discord_id,
            'discord_username': self.discord_username,
            'last_active_at': self.last_active_at,
            'created_at': self.created_at
        }
        
        # SINGLE ACCOUNT MODE
        if self.cms_account:
            data['cms_account'] = self.cms_account.to_dict()
            data['account_id'] = self.account_id
        
        # MULTI-ACCOUNT MODE (disabled)
        # data['cms_accounts'] = {
        #     account_id: account.to_dict() 
        #     for account_id, account in self.cms_accounts.items()
        # }
        # data['active_account_id'] = self.active_account_id
        
        return data
    
    @classmethod
    def from_dict(cls, user_id, data):
        """Create user from dictionary"""
        # SINGLE ACCOUNT MODE
        cms_account = None
        account_id = data.get('account_id')
        
        if 'cms_account' in data:
            cms_account = CMSAccount.from_dict(data['cms_account'])
        # Migration: If old multi-account data exists, use first account
        elif 'cms_accounts' in data and data['cms_accounts']:
            # Take the active account or first account
            old_accounts = data['cms_accounts']
            active_id = data.get('active_account_id')
            if active_id and active_id in old_accounts:
                account_id = active_id
                cms_account = CMSAccount.from_dict(old_accounts[active_id])
            else:
                # Take first account
                account_id = list(old_accounts.keys())[0]
                cms_account = CMSAccount.from_dict(old_accounts[account_id])
        
        user = cls(
            id=user_id,
            username=data['username'],
            email=data['email'],
            password_hash=data.get('password_hash'),
            session_version=data.get('session_version'),
            cms_account=cms_account,
            account_id=account_id,
            preferences=data.get('preferences', {}),
            notification_preferences=data.get('notification_preferences'),
            batch_bucket=data.get('batch_bucket'),
            discord_id=data.get('discord_id'),
            discord_username=data.get('discord_username'),
            last_active_at=data.get('last_active_at'),
            created_at=data.get('created_at')
        )
        
        # MULTI-ACCOUNT MODE (disabled)
        # user = cls(
        #     id=user_id,
        #     username=data['username'],
        #     email=data['email'],
        #     password_hash=data.get('password_hash'),
        #     active_account_id=data.get('active_account_id'),
        #     preferences=data.get('preferences', {}),
        #     notification_preferences=data.get('notification_preferences')
        # )
        # 
        # # Load CMS accounts
        # cms_accounts_data = data.get('cms_accounts', {})
        # for account_id, account_data in cms_accounts_data.items():
        #     user.cms_accounts[account_id] = CMSAccount.from_dict(account_data)
        
        return user
    
    # MULTI-ACCOUNT METHODS (disabled - keep for future re-enablement)
    
    # def add_cms_account(self, cms_username, cms_password, display_name=None):
    #     """Add a new CMS account"""
    #     account_id = str(uuid.uuid4())
    #     
    #     # Create account directory
    #     account_manager.create_account_directory(account_id)
    #     
    #     # Create CMS account
    #     cms_account = CMSAccount(
    #         account_id=account_id,
    #         cms_username=cms_username,
    #         cms_password=cms_password,
    #         display_name=display_name
    #     )
    #     
    #     self.cms_accounts[account_id] = cms_account
    #     
    #     # Set as active if it's the first account
    #     if not self.active_account_id:
    #         self.active_account_id = account_id
    #         self.preferences['default_account'] = account_id
    #     
    #     return account_id
    
    # def remove_cms_account(self, account_id):
    #     """Remove a CMS account"""
    #     if account_id in self.cms_accounts:
    #         del self.cms_accounts[account_id]
    #         
    #         # Update active account if removed account was active
    #         if self.active_account_id == account_id:
    #             if self.cms_accounts:
    #                 self.active_account_id = list(self.cms_accounts.keys())[0]
    #             else:
    #                 self.active_account_id = None
    #         
    #         return True
    #     return False
    
    # def set_active_account(self, account_id):
    #     """Set the active CMS account"""
    #     if account_id in self.cms_accounts:
    #         self.active_account_id = account_id
    #         return True
    #     return False
    
    # def get_account_list(self):
    #     """Get list of all CMS accounts for display"""
    #     # Determine the currently active account (session takes priority)
    #     active_id = self.active_account_id
    #     try:
    #         from flask import session, has_request_context
    #         if has_request_context():
    #             session_id = session.get('active_account_id')
    #             if session_id and session_id in self.cms_accounts:
    #                 active_id = session_id
    #     except Exception:
    #         pass
    #
    #     return [
    #         {
    #             'account_id': account_id,
    #             'cms_username': account.cms_username,
    #             'display_name': account.student_name or account.display_name,
    #             'student_name': account.student_name,
    #             'is_active': account_id == active_id,
    #             'last_login': account.last_login,
    #             'status': 'active' if account.is_active else 'inactive'
    #         }
    #         for account_id, account in self.cms_accounts.items()
    #     ]
    
    # SINGLE ACCOUNT METHODS (current)
    
    def set_cms_account(self, cms_username, cms_password, display_name=None, institute_id=None):
        """Set the single CMS account for this user"""
        from app.routes.auth import get_institute_id_for_enrollment
        resolved_inst = institute_id or get_institute_id_for_enrollment(cms_username, fallback='2')
        if str(resolved_inst).strip() == '2' and cms_username and not str(cms_username).strip().startswith('02-'):
            resolved_inst = get_institute_id_for_enrollment(cms_username)

        # Generate account ID if not exists
        if not self.account_id:
            self.account_id = str(uuid.uuid4())
            # Create account directory
            account_manager.create_account_directory(self.account_id)
        
        # Create or update CMS account
        self.cms_account = CMSAccount(
            account_id=self.account_id,
            cms_username=cms_username,
            cms_password=cms_password,
            display_name=display_name,
            institute_id=str(resolved_inst)
        )
        
        return self.account_id
    
    def get_active_account(self):
        """Get the currently active CMS account (single account mode)"""
        return self.cms_account
        
        # MULTI-ACCOUNT MODE (disabled)
        # """Get the currently active CMS account.
        #
        # Checks the Flask session first (set by switch_account / login) so that
        # per-request account switching is reflected immediately without a DB write.
        # Falls back to the persisted active_account_id if the session key is absent.
        # """
        # # Try session-based active account first (requires a request context)
        # try:
        #     from flask import session, has_request_context
        #     if has_request_context():
        #         session_account_id = session.get('active_account_id')
        #         if session_account_id and session_account_id in self.cms_accounts:
        #             return self.cms_accounts[session_account_id]
        # except Exception:
        #     pass
        #
        # # Fall back to the DB-persisted value
        # if self.active_account_id and self.active_account_id in self.cms_accounts:
        #     return self.cms_accounts[self.active_account_id]
        # return None
    
    @staticmethod
    def _get_firestore():
        """Safely get Firestore service, returns None if unavailable"""
        try:
            from app.services.firestore_service import get_firestore_service
            fs = get_firestore_service()
            if fs and fs.is_available():
                return fs
        except Exception:
            pass
        return None
    
    @staticmethod
    def get(user_id, *, fresh=False):
        """Get user by ID - tries Firestore first, then local storage"""
        try:
            # Try Firestore first (works on Cloud Run)
            fs = User._get_firestore()
            if fs:
                options = {'bypass_cache': True} if fresh else {}
                user_data = fs.get_document('users', user_id, **options)
                if user_data:
                    user = User.from_dict(user_id, user_data)
                    return user
                if fresh:
                    return None
            
            # Fallback to local JSON (works locally)
            users = account_manager.load_users()
            user_data = users.get(user_id)
            if user_data:
                user = User.from_dict(user_id, user_data)
                return user
            return None
        except Exception as e:
            logger.warning(f"Error getting user {user_id}: {e}")
            return None

    @staticmethod
    def get_total_count():
        """Get total number of users"""
        try:
            fs = User._get_firestore()
            if fs:
                count = fs.get_collection_count('users')
                if count == -1:
                    return 9999 # Fail closed on DB error
                return count
            
            # Fallback to local
            users = account_manager.load_users()
            return len(users)
        except Exception as e:
            logger.warning(f"Error getting total user count: {e}")
            return 9999
    
    @staticmethod
    def list_all():
        """Return all users — tries Firestore first, falls back to local JSON"""
        try:
            fs = User._get_firestore()
            if fs:
                docs = fs.list_collection('users')
                if docs:
                    return [User.from_dict(uid, data) for uid, data in docs]
            # Fallback: local JSON
            users_data = account_manager.load_users()
            return [User.from_dict(uid, data) for uid, data in users_data.items()]
        except Exception as e:
            logger.warning(f"Error listing users: {e}")
            return []

    @staticmethod
    def get_users_by_batch(batch_number: int):
        """Query users for a specific batch bucket (0-59).
        Significantly reduces Firestore reads vs list_all().
        """
        try:
            batch_num = int(batch_number)
            fs = User._get_firestore()
            if fs and hasattr(fs, 'db') and fs.db:
                # O(1) query by batch_bucket
                docs = fs.db.collection('users').where('batch_bucket', '==', batch_num).stream()
                users = []
                for doc in docs:
                    users.append(User.from_dict(doc.id, doc.to_dict()))
                return users
                
            # Fallback for local JSON / missing Firestore
            users = User.list_all()
            return [u for u in users if u.batch_bucket == batch_num]
        except Exception as e:
            logger.warning(f"Error getting users by batch {batch_number}: {e}")
            return []

    @staticmethod
    def get_by_account_id(account_id: str):
        """Find a user by their CMS account_id. Essential for background tasks."""
        if not account_id:
            return None

        account_str = str(account_id).strip()
        if not account_str:
            return None

        try:
            from google.cloud import firestore
            fs = User._get_firestore()
            if fs and hasattr(fs, 'db') and fs.db:
                # 1. Query top-level account_id
                docs = fs.db.collection('users').where(filter=firestore.FieldFilter('account_id', '==', account_str)).limit(1).stream()
                for doc in docs:
                    return User.from_dict(doc.id, doc.to_dict())

                # 2. Query nested cms_account.account_id (for single-account structure)
                docs = fs.db.collection('users').where(filter=firestore.FieldFilter('cms_account.account_id', '==', account_str)).limit(1).stream()
                for doc in docs:
                    return User.from_dict(doc.id, doc.to_dict())

                # 3. Direct document lookup (only if account_str could be a username/doc ID, not a 36-char UUID)
                if len(account_str) < 30 and account_str.count('-') <= 2:
                    doc = fs.db.collection('users').document(account_str).get()
                    if doc.exists:
                        return User.from_dict(doc.id, doc.to_dict())

                # Strict stop on Firestore: do not fall through to full collection scan!
                return None
                
            # Fallback for local JSON file storage only (when Firestore is not configured)
            users = User.list_all()
            for u in users:
                if u.account_id == account_str or u.id == account_str or (u.cms_account and getattr(u.cms_account, 'account_id', None) == account_str):
                    return u
            return None
        except Exception as e:
            logger.warning(f"Error getting user by account_id {account_id}: {e}")
            return None

    @staticmethod
    def create(username, email, password):
        """Create a new user - saves to both Firestore and local"""
        try:
            user = User(
                id=username,
                username=username,
                email=email,
                password_hash=generate_password_hash(password)
            )
            
            # Save to local JSON
            users = account_manager.load_users()
            users[username] = user.to_dict()
            account_manager.save_users(users)
            
            # Also save to Firestore for persistence across Cloud Run instances
            if not os.environ.get('PYTEST_CURRENT_TEST'):
                fs = User._get_firestore()
                if fs:
                    fs.save_document('users', username, user.to_dict())
                    logger.debug(f"User.create({username}): saved to Firestore")
            
            return user
        except Exception as e:
            logger.warning(f"Error creating user: {e}")
            return None
    
    def save(self):
        """Save user data - saves to both Firestore and local"""
        try:
            user_dict = self.to_dict()
            
            # Save to local JSON
            users = account_manager.load_users()
            users[self.id] = user_dict
            account_manager.save_users(users)
            
            # Also save to Firestore for persistence across Cloud Run instances
            # Prevent local pytest suite from contaminating live Firestore
            if not os.environ.get('PYTEST_CURRENT_TEST'):
                fs = User._get_firestore()
                if fs:
                    if not fs.save_document('users', self.id, user_dict):
                        return False
            
            return True
        except Exception as e:
            logger.warning(f"Error saving user {self.id}: {e}")
            return False
    
    def check_password(self, password):
        """Check if provided password matches user's password"""
        if self.password_hash:
            return check_password_hash(self.password_hash, password)
        return False
    
    @property
    def settings(self):
        """Compatibility property for template access"""
        return self.preferences