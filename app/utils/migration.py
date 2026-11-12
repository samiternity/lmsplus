# app/utils/migration.py
import os
import json
import shutil
from datetime import datetime
from app.models.account import User, account_manager
from app.models.user import User as LegacyUser
from app.utils.data_manager import data_manager

class DataMigration:
    """Handle migration from old single-account system to new multi-account system"""
    
    def __init__(self):
        self.old_data_dir = os.path.join(os.path.dirname(__file__), '..', 'data')
        self.migration_log = []
    
    def check_migration_needed(self):
        """Check if migration is needed"""
        try:
            # Check if there are old-style data files
            old_files = []
            if os.path.exists(self.old_data_dir):
                for filename in os.listdir(self.old_data_dir):
                    if (filename.endswith('.json') and 
                        any(data_type in filename for data_type in ['attendance', 'timetable', 'gpa', 'community_service']) and
                        'latest' in filename):
                        old_files.append(filename)
            
            return len(old_files) > 0
        except Exception as e:
            print(f"Error checking migration status: {e}")
            return False
    
    def migrate_user_data(self):
        """Migrate all user data from old system to new system"""
        try:
            users = account_manager.load_users()
            migrated_users = 0
            
            for user_id, user_data in users.items():
                if self.migrate_single_user(user_id, user_data):
                    migrated_users += 1
            
            self.migration_log.append(f"Successfully migrated {migrated_users} users")
            return True
            
        except Exception as e:
            self.migration_log.append(f"Error during migration: {e}")
            return False
    
    def migrate_single_user(self, user_id, user_data):
        """Migrate a single user from old format to new format"""
        try:
            # Check if user already has new format
            if 'cms_accounts' in user_data:
                self.migration_log.append(f"User {user_id} already migrated")
                return True
            
            # Create new user object
            user = User(
                id=user_id,
                username=user_data.get('username', user_id),
                email=user_data.get('email', f"{user_id}@student.bahria.edu.pk"),
                password_hash=user_data.get('password_hash')
            )
            
            # Migrate CMS credentials if they exist
            cms_username = user_data.get('cms_username')
            cms_password = user_data.get('cms_password')
            
            if cms_username and cms_password:
                account_id = user.add_cms_account(
                    cms_username=cms_username,
                    cms_password=cms_password,
                    display_name=f"{cms_username} (Migrated)"
                )
                
                # Migrate data files for this account
                self.migrate_data_files(cms_username, account_id)
                
                self.migration_log.append(f"Migrated user {user_id} with CMS account {cms_username}")
            else:
                self.migration_log.append(f"Migrated user {user_id} without CMS account")
            
            # Save migrated user
            return user.save()
            
        except Exception as e:
            self.migration_log.append(f"Error migrating user {user_id}: {e}")
            return False
    
    def migrate_data_files(self, cms_username, account_id):
        """Migrate data files for a specific CMS account"""
        try:
            data_types = ['attendance', 'timetable', 'gpa', 'community_service']
            
            for data_type in data_types:
                # Look for old files
                pattern = f"{cms_username}_{data_type}"
                
                for filename in os.listdir(self.old_data_dir):
                    if filename.startswith(pattern) and filename.endswith('.json'):
                        old_path = os.path.join(self.old_data_dir, filename)
                        
                        try:
                            # Load old data
                            with open(old_path, 'r', encoding='utf-8') as f:
                                data = json.load(f)
                            
                            # Add migration metadata
                            data['migrated_from'] = filename
                            data['migration_date'] = datetime.now().isoformat()
                            
                            # Save to new location
                            if 'latest' in filename:
                                data_manager.save_account_data(
                                    account_id=account_id,
                                    data_type=data_type,
                                    data=data,
                                    create_history=False
                                )
                            else:
                                # Extract timestamp from filename
                                timestamp = filename.replace(f"{cms_username}_{data_type}_", "").replace(".json", "")
                                data_manager.save_account_data(
                                    account_id=account_id,
                                    data_type=data_type,
                                    data=data,
                                    create_history=True
                                )
                            
                            self.migration_log.append(f"Migrated data file: {filename}")
                            
                        except Exception as e:
                            self.migration_log.append(f"Error migrating file {filename}: {e}")
            
            # Migrate session file
            session_file = os.path.join(self.old_data_dir, f'session_{cms_username}.pkl')
            if os.path.exists(session_file):
                try:
                    with open(session_file, 'rb') as f:
                        session_data = f.read()
                    
                    # Save to new location
                    new_session_path = os.path.join(
                        data_manager.get_account_data_path(account_id, 'sessions'),
                        'cms_session.pkl'
                    )
                    os.makedirs(os.path.dirname(new_session_path), exist_ok=True)
                    
                    with open(new_session_path, 'wb') as f:
                        f.write(session_data)
                    
                    self.migration_log.append(f"Migrated session file for {cms_username}")
                    
                except Exception as e:
                    self.migration_log.append(f"Error migrating session file: {e}")
                    
        except Exception as e:
            self.migration_log.append(f"Error migrating data files for {cms_username}: {e}")
    
    def backup_old_data(self):
        """Create backup of old data before migration"""
        try:
            backup_dir = os.path.join(self.old_data_dir, f'backup_{datetime.now().strftime("%Y%m%d_%H%M%S")}')
            os.makedirs(backup_dir, exist_ok=True)
            
            # Copy all old files to backup
            for filename in os.listdir(self.old_data_dir):
                if filename.endswith(('.json', '.pkl')) and not filename.startswith('backup_'):
                    old_path = os.path.join(self.old_data_dir, filename)
                    backup_path = os.path.join(backup_dir, filename)
                    shutil.copy2(old_path, backup_path)
            
            self.migration_log.append(f"Created backup at: {backup_dir}")
            return backup_dir
            
        except Exception as e:
            self.migration_log.append(f"Error creating backup: {e}")
            return None
    
    def cleanup_old_files(self, backup_dir=None):
        """Clean up old files after successful migration"""
        try:
            if not backup_dir:
                self.migration_log.append("No backup directory provided, skipping cleanup")
                return False
            
            # Only cleanup if backup was successful
            if os.path.exists(backup_dir):
                files_cleaned = 0
                
                for filename in os.listdir(self.old_data_dir):
                    if (filename.endswith(('.json', '.pkl')) and 
                        not filename.startswith('backup_') and
                        filename != 'users.json'):  # Keep users.json as it's still used
                        
                        old_path = os.path.join(self.old_data_dir, filename)
                        try:
                            os.remove(old_path)
                            files_cleaned += 1
                        except OSError:
                            pass
                
                self.migration_log.append(f"Cleaned up {files_cleaned} old files")
                return True
            
            return False
            
        except Exception as e:
            self.migration_log.append(f"Error during cleanup: {e}")
            return False
    
    def run_full_migration(self):
        """Run complete migration process"""
        try:
            self.migration_log.append("Starting migration process...")
            
            # Check if migration is needed
            if not self.check_migration_needed():
                self.migration_log.append("No migration needed")
                return True, self.migration_log
            
            # Create backup
            backup_dir = self.backup_old_data()
            if not backup_dir:
                return False, self.migration_log
            
            # Migrate user data
            if not self.migrate_user_data():
                return False, self.migration_log
            
            # Clean up old files
            self.cleanup_old_files(backup_dir)
            
            self.migration_log.append("Migration completed successfully!")
            return True, self.migration_log
            
        except Exception as e:
            self.migration_log.append(f"Migration failed: {e}")
            return False, self.migration_log
    
    def get_migration_status(self):
        """Get current migration status"""
        return {
            'migration_needed': self.check_migration_needed(),
            'log': self.migration_log
        }

# Global migration instance
migration_manager = DataMigration()