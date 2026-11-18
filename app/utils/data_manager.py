# app/utils/data_manager.py
import os
import json
import pickle
import stat
import time
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any
import shutil


def _to_json_safe(obj):
    """
    Recursively convert all datetime/Timestamp objects to ISO-format strings
    so the data can be safely stored in and retrieved from Firestore without
    Firestore changing Python datetimes into opaque Timestamp objects.
    """
    if isinstance(obj, dict):
        return {k: _to_json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_to_json_safe(v) for v in obj]
    if isinstance(obj, datetime):
        return obj.isoformat()
    # Handle google.cloud.firestore Timestamp / DatetimeWithNanoseconds
    try:
        # Firestore Timestamps have a .isoformat() via the datetime mixin
        if hasattr(obj, 'isoformat'):
            return obj.isoformat()
    except Exception:
        pass
    return obj

class DataManager:
    """Enhanced data manager for organized multi-account data storage"""
    
    def __init__(self):
        self.base_data_dir = os.path.join(os.path.dirname(__file__), '..', 'data')
        self.accounts_dir = os.path.join(self.base_data_dir, 'accounts')
        self._memory_cache = {}
        self.ensure_directories()
    
    def ensure_directories(self):
        """Ensure all necessary directories exist"""
        os.makedirs(self.base_data_dir, exist_ok=True)
        os.makedirs(self.accounts_dir, exist_ok=True)
    
    def get_account_data_path(self, account_id: str, data_type: str) -> str:
        """Get the data directory path for a specific account and data type"""
        return os.path.join(self.accounts_dir, account_id, data_type)
    
    def save_account_data(self, account_id: str, data_type: str, data: Dict, 
                         create_history: bool = True) -> bool:
        """Save data for a specific account with automatic history management"""
        # Convert all datetime objects to ISO strings upfront for safe JSON serialization (disk + Firestore)
        safe_data = _to_json_safe(data)

        # Phase 3: Firestore Persistence
        from app.services.firestore_service import get_firestore_service
        fs = get_firestore_service()
        if fs.is_available():
            if data_type in ('attendance', 'attendance_yesterday', 'assignments_snapshot'):
                # Snapshots are consolidated in 'user_dashboards' to eliminate separate collection documents
                key = (
                    'attendance_snapshot' if data_type == 'attendance'
                    else 'attendance_yesterday_snapshot' if data_type == 'attendance_yesterday'
                    else 'assignments_snapshot'
                )
                
                unified = self.get_unified_dashboard(account_id) or {}
                has_changed = True
                if data_type in ('attendance', 'attendance_yesterday'):
                    # Guard: do not persist error states or empty course lists if we already have valid courses
                    old_snapshot = unified.get(key) or {}
                    old_courses = old_snapshot.get('courses', [])
                    new_courses = safe_data.get('courses', [])
                    if safe_data.get('error') or (not new_courses and old_courses):
                        has_changed = False
                    elif old_courses and len(old_courses) == len(new_courses):
                        def _course_sig(c):
                            if not isinstance(c, dict):
                                return str(c)
                            return (
                                str(c.get('code') or '').strip(),
                                str(c.get('name') or '').strip(),
                                str(c.get('instructor') or '').strip(),
                                str(c.get('credit_hours') or '').strip(),
                                str(c.get('total_classes') or '').strip(),
                                str(c.get('attended_classes') or '').strip(),
                                str(c.get('attendance_percentage') or '').strip(),
                                str(c.get('absences') or '').strip(),
                                str(c.get('remaining_absences') or '').strip(),
                                str(c.get('status') or '').strip()
                            )
                        if sorted(_course_sig(c) for c in old_courses) == sorted(_course_sig(c) for c in new_courses):
                            has_changed = False
                elif data_type == 'assignments_snapshot':
                    old_snapshot = unified.get('assignments_snapshot') or {}
                    if not old_snapshot:
                        has_changed = True
                    else:
                        old_pending = old_snapshot.get('pending', [])
                        old_missed = old_snapshot.get('missed', [])
                        old_all = old_snapshot.get('all_assignments', [])
                        new_pending = safe_data.get('pending', [])
                        new_missed = safe_data.get('missed', [])
                        new_all = safe_data.get('all_assignments', [])

                        def _assignment_sig(a):
                            if not isinstance(a, dict):
                                return str(a)
                            return (
                                str(a.get('assignment_no') or '').strip(),
                                str(a.get('title') or '').strip(),
                                str(a.get('course_code') or '').strip(),
                                str(a.get('course_name') or '').strip(),
                                str(a.get('semester') or '').strip(),
                                str(a.get('deadline') or '').strip(),
                                str(a.get('actual_deadline') or '').strip(),
                                str(a.get('extended_deadline') or '').strip(),
                                str(a.get('submission_status') or '').strip(),
                                str(a.get('submission_file') or '').strip(),
                                str(a.get('assignment_file') or '').strip(),
                                str(a.get('solution_file') or '').strip(),
                                str(a.get('remarks') or '').strip(),
                                str(a.get('marks_obtained') or '').strip(),
                                str(a.get('total_marks') or '').strip(),
                                str(a.get('category') or '').strip(),
                                str(a.get('urgency_level') or '').strip()
                            )

                        if (safe_data.get('latest_semester_name') == old_snapshot.get('latest_semester_name') and
                            len(new_pending) == len(old_pending) and
                            len(new_missed) == len(old_missed) and
                            len(new_all) == len(old_all) and
                            sorted(_assignment_sig(a) for a in new_pending) == sorted(_assignment_sig(a) for a in old_pending) and
                            sorted(_assignment_sig(a) for a in new_missed) == sorted(_assignment_sig(a) for a in old_missed) and
                            sorted(_assignment_sig(a) for a in new_all) == sorted(_assignment_sig(a) for a in old_all)):
                            has_changed = False

                if has_changed:
                    payload = {
                        key: safe_data,
                        'last_updated': datetime.utcnow().isoformat() + 'Z',
                        'legacy_migrated': True
                    }
                    if data_type == 'assignments_snapshot':
                        from app.utils.assignment_identity import assignment_snapshot_payload
                        payload.update(assignment_snapshot_payload(unified, safe_data, account_id))
                    self.save_unified_dashboard(account_id, payload, merge=True)
                    from app.utils.lms_logger import resolve_enrollment
                    print(f"[DATA_MGR] Saved {data_type} to user_dashboards for {resolve_enrollment(account_id)}")
            elif data_type == 'attendance_yesterday_backup':
                # Ephemeral rollback backup - skip Firestore
                pass
            else:
                try:
                    fs.save_document('account_data', f'{account_id}_{data_type}', safe_data)
                    from app.utils.lms_logger import resolve_enrollment
                    print(f"[DATA_MGR] Saved {data_type} to Firestore for {resolve_enrollment(account_id)}")
                except Exception as e:
                    print(f"[DATA_MGR] Firestore save failed: {e}")

        try:

            data_dir = self.get_account_data_path(account_id, data_type)
            os.makedirs(data_dir, exist_ok=True)
            
            # Save timestamped version for history
            if create_history:
                timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
                history_file = os.path.join(data_dir, f'{data_type}_{timestamp}.json')
                with open(history_file, 'w', encoding='utf-8') as f:
                    json.dump(safe_data, f, indent=2, ensure_ascii=False)
            
            # Save latest version
            latest_file = os.path.join(data_dir, f'{data_type}_latest.json')
            with open(latest_file, 'w', encoding='utf-8') as f:
                json.dump(safe_data, f, indent=2, ensure_ascii=False)
            
            # Clean up old history files (keep last 10)
            if create_history:
                self.cleanup_old_files(data_dir, data_type, keep_count=10)
            
            return True
            
        except Exception as e:
            from app.utils.lms_logger import resolve_enrollment
            print(f"Error saving {data_type} data for account {resolve_enrollment(account_id)}: {e}")
            return False
    
    def load_account_data(self, account_id: str, data_type: str, 
                         timestamp: Optional[str] = None, bypass_cache: bool = False) -> Optional[Dict]:
        """Load data for a specific account"""
        # Phase 3: Firestore Persistence
        from app.services.firestore_service import get_firestore_service
        fs = get_firestore_service()
        if fs.is_available() and not timestamp:
            if data_type in ('attendance', 'attendance_yesterday', 'assignments_snapshot'):
                # Snapshots are unified in user_dashboards (reads from 5m in-memory cache)
                unified = self.get_unified_dashboard(account_id, bypass_cache=bypass_cache)
                if data_type == 'attendance':
                    key = 'attendance_snapshot'
                elif data_type == 'attendance_yesterday':
                    key = 'attendance_yesterday_snapshot'
                else:
                    key = 'assignments_snapshot'

                if unified and unified.get(key) is not None:
                    return unified.get(key)
                return None
            else:
                # Prefer Firestore for latest data
                try:
                    data = fs.get_document('account_data', f'{account_id}_{data_type}', bypass_cache=bypass_cache)
                    if data:
                        # Sanitize any Firestore Timestamp objects that may have slipped through.
                        data = _to_json_safe(data)
                        from app.utils.lms_logger import resolve_enrollment
                        print(f"[DATA_MGR] Loaded {data_type} from Firestore for {resolve_enrollment(account_id)}")
                        return data
                except Exception as e:
                    print(f"[DATA_MGR] Firestore load failed: {e}")

        try:

            data_dir = self.get_account_data_path(account_id, data_type)
            
            if timestamp:
                # Load specific timestamped version
                file_path = os.path.join(data_dir, f'{data_type}_{timestamp}.json')
            else:
                # Load latest version
                file_path = os.path.join(data_dir, f'{data_type}_latest.json')
            
            if os.path.exists(file_path):
                with open(file_path, 'r', encoding='utf-8') as f:
                    return json.load(f)
            
            return None
            
        except Exception as e:
            from app.utils.lms_logger import resolve_enrollment
            print(f"Error loading {data_type} data for account {resolve_enrollment(account_id)}: {e}")
            return None
    
    def get_data_history(self, account_id: str, data_type: str, 
                        limit: int = 10) -> List[Dict]:
        """Get history of data changes for an account"""
        try:
            data_dir = self.get_account_data_path(account_id, data_type)
            
            if not os.path.exists(data_dir):
                return []
            
            # Find all timestamped files
            history_files = []
            for filename in os.listdir(data_dir):
                if filename.startswith(f'{data_type}_') and filename.endswith('.json') and 'latest' not in filename:
                    timestamp = filename.replace(f'{data_type}_', '').replace('.json', '')
                    try:
                        # Validate timestamp format
                        datetime.strptime(timestamp, '%Y%m%d_%H%M%S')
                        history_files.append({
                            'timestamp': timestamp,
                            'filename': filename,
                            'datetime': datetime.strptime(timestamp, '%Y%m%d_%H%M%S')
                        })
                    except ValueError:
                        continue
            
            # Sort by timestamp (newest first) and limit
            history_files.sort(key=lambda x: x['datetime'], reverse=True)
            return history_files[:limit]
            
        except Exception as e:
            from app.utils.lms_logger import resolve_enrollment
            print(f"Error getting data history for account {resolve_enrollment(account_id)}: {e}")
            return []
    
    def cleanup_old_files(self, data_dir: str, data_type: str, keep_count: int = 10):
        """Clean up old data files, keeping only the most recent ones"""
        try:
            history_files = []
            for filename in os.listdir(data_dir):
                if (filename.startswith(f'{data_type}_') and 
                    filename.endswith('.json') and 
                    'latest' not in filename):
                    
                    file_path = os.path.join(data_dir, filename)
                    timestamp = filename.replace(f'{data_type}_', '').replace('.json', '')
                    
                    try:
                        dt = datetime.strptime(timestamp, '%Y%m%d_%H%M%S')
                        history_files.append({
                            'path': file_path,
                            'datetime': dt,
                            'filename': filename
                        })
                    except ValueError:
                        continue
            
            # Sort by date (newest first)
            history_files.sort(key=lambda x: x['datetime'], reverse=True)
            
            # Remove files beyond keep_count
            for file_info in history_files[keep_count:]:
                try:
                    os.remove(file_info['path'])
                    print(f"Cleaned up old file: {file_info['filename']}")
                except OSError:
                    pass
                    
        except Exception as e:
            print(f"Error cleaning up old files: {e}")
    
    def save_session_data(self, account_id: str, session_data: Any) -> bool:
        """Save session data (cookies, etc.) for an account"""
        try:
            # Phase 3: Firestore persistence
            from app.services.firestore_service import get_firestore_service
            fs = get_firestore_service()
            if fs.is_available():
                try:
                    import requests
                    dict_data = session_data
                    if isinstance(session_data, requests.cookies.RequestsCookieJar):
                        dict_data = requests.utils.dict_from_cookiejar(session_data)
                    from app.utils.session_encryption import encrypt_session
                    doc_id = f'{account_id}_cms_session'
                    fs.save_document('account_data', doc_id, encrypt_session(doc_id, {'cookies': dict_data}))
                    from app.utils.lms_logger import resolve_enrollment
                    print(f"[DATA_MGR] Saved cms_session to Firestore for {resolve_enrollment(account_id)}")
                except Exception as e:
                    print(f"[DATA_MGR] Firestore session save failed: {e}")

            sessions_dir = self.get_account_data_path(account_id, 'sessions')
            os.makedirs(sessions_dir, exist_ok=True)
            
            session_file = os.path.join(sessions_dir, 'cms_session.pkl')
            with open(session_file, 'wb') as f:
                pickle.dump(session_data, f)
            
            return True
            
        except Exception as e:
            from app.utils.lms_logger import resolve_enrollment
            print(f"Error saving session data for account {resolve_enrollment(account_id)}: {e}")
            return False
    
    def load_session_data(self, account_id: str) -> Optional[Any]:
        """Load session data for an account"""
        try:
            # Phase 3: Firestore persistence
            from app.services.firestore_service import get_firestore_service
            fs = get_firestore_service()
            if fs.is_available():
                try:
                    from app.utils.session_encryption import decrypt_session
                    doc_id = f'{account_id}_cms_session'
                    data = decrypt_session(doc_id, fs.get_document('account_data', doc_id))
                    if data and 'cookies' in data:
                        import requests
                        jar = requests.cookies.RequestsCookieJar()
                        jar.update(data['cookies'])
                        from app.utils.lms_logger import resolve_enrollment
                        print(f"[DATA_MGR] Loaded cms_session from Firestore for {resolve_enrollment(account_id)}")
                        return jar
                except Exception as e:
                    print(f"[DATA_MGR] Firestore session load failed: {e}")

            sessions_dir = self.get_account_data_path(account_id, 'sessions')
            session_file = os.path.join(sessions_dir, 'cms_session.pkl')
            
            if os.path.exists(session_file):
                with open(session_file, 'rb') as f:
                    return pickle.load(f)
            
            return None
            
        except Exception as e:
            from app.utils.lms_logger import resolve_enrollment
            print(f"Error loading session data for account {resolve_enrollment(account_id)}: {e}")
            return None
    
    def delete_account_data(self, account_id: str) -> bool:
        """Delete all data for an account (local files + Firestore)"""
        try:
            # Delete from Firestore first
            from app.services.firestore_service import get_firestore_service
            fs = get_firestore_service()
            if fs.is_available():
                data_types = ['attendance', 'timetable', 'gpa', 'community_service',
                              'profile', 'lms', 'assignments', 'lecture_notes', 'cms_session', 'lms_session']
                for dt in data_types:
                    try:
                        fs.delete_document('account_data', f'{account_id}_{dt}')
                    except Exception:
                        pass

            account_dir = os.path.join(self.accounts_dir, account_id)
            from app.utils.lms_logger import resolve_enrollment
            print(f"[DATA_MGR] Attempting to delete account data: {resolve_enrollment(account_id)}")
            print(f"[DATA_MGR] Account directory path: {account_dir}")
            
            if os.path.exists(account_dir):
                # List contents before deletion
                try:
                    contents = []
                    for root, dirs, files in os.walk(account_dir):
                        for file in files:
                            file_path = os.path.join(root, file)
                            rel_path = os.path.relpath(file_path, account_dir)
                            contents.append(rel_path)
                    
                    from app.utils.lms_logger import resolve_enrollment
                    print(f"[DATA_MGR] Account {resolve_enrollment(account_id)} contains {len(contents)} files:")
                    for content in contents[:10]:  # Show first 10 files
                        print(f"   - {content}")
                    if len(contents) > 10:
                        print(f"   ... and {len(contents) - 10} more files")
                        
                except Exception as list_error:
                    print(f"[DATA_MGR] Could not list contents: {list_error}")
                
                # Delete the directory with robust method
                if self._robust_delete_directory(account_dir):
                    from app.utils.lms_logger import resolve_enrollment
                    print(f"[DATA_MGR] Successfully deleted all data for account {resolve_enrollment(account_id)}")
                    
                    # Verify deletion
                    if not os.path.exists(account_dir):
                        print(f"[DATA_MGR] Verification: Account directory {resolve_enrollment(account_id)} no longer exists")
                    else:
                        print(f"[DATA_MGR] Verification FAILED: Account directory {resolve_enrollment(account_id)} still exists!")
                    
                    return True
                else:
                    print(f"[DATA_MGR] Failed to delete account directory after all retries")
                    return False
            else:
                print(f"[DATA_MGR] Account directory does not exist: {account_dir}")
                return False
            
        except Exception as e:
            from app.utils.lms_logger import resolve_enrollment
            print(f"[DATA_MGR] Error deleting account data for {resolve_enrollment(account_id)}: {e}")
            import traceback
            traceback.print_exc()
            return False
    
    def _force_remove_readonly(self, func, path, exc_info):
        """
        Error handler for Windows readonly files during deletion
        """
        try:
            print(f"[DATA_MGR] Attempting to fix readonly file: {path}")
            os.chmod(path, stat.S_IWRITE)
            func(path)
            print(f"[DATA_MGR] Successfully removed readonly file: {path}")
        except Exception as e:
            print(f"[DATA_MGR] Failed to remove readonly file {path}: {e}")
    
    def _robust_delete_directory(self, directory_path, max_retries=3):
        """
        Robustly delete a directory with retry logic for Windows
        """
        for attempt in range(max_retries):
            try:
                print(f"[DATA_MGR] Attempt {attempt + 1}/{max_retries} to delete: {directory_path}")
                
                if not os.path.exists(directory_path):
                    print(f"[DATA_MGR] Directory already deleted: {directory_path}")
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
                    print(f"[DATA_MGR] Could not change permissions: {e}")
                
                # Try to delete with error handler for readonly files
                shutil.rmtree(directory_path, onerror=self._force_remove_readonly)
                
                # Verify deletion
                if not os.path.exists(directory_path):
                    print(f"[DATA_MGR] Successfully deleted directory: {directory_path}")
                    return True
                else:
                    print(f"[DATA_MGR] Directory still exists after deletion attempt")
                    
            except PermissionError as e:
                print(f"[DATA_MGR] Permission error on attempt {attempt + 1}: {e}")
                if attempt < max_retries - 1:
                    print(f"[DATA_MGR] Waiting 2 seconds before retry...")
                    time.sleep(2)
                    continue
                else:
                    print(f"[DATA_MGR] All attempts failed due to permission errors")
                    return False
                    
            except Exception as e:
                print(f"[DATA_MGR] Unexpected error on attempt {attempt + 1}: {e}")
                if attempt < max_retries - 1:
                    print(f"[DATA_MGR] Waiting 1 second before retry...")
                    time.sleep(1)
                    continue
                else:
                    print(f"[DATA_MGR] All attempts failed")
                    return False
        
        return False
    
    def get_account_storage_info(self, account_id: str) -> Dict:
        """Get storage information for an account"""
        try:
            account_dir = os.path.join(self.accounts_dir, account_id)
            
            if not os.path.exists(account_dir):
                return {'total_size': 0, 'file_count': 0, 'data_types': {}}
            
            total_size = 0
            file_count = 0
            data_types = {}
            
            for root, dirs, files in os.walk(account_dir):
                for file in files:
                    file_path = os.path.join(root, file)
                    file_size = os.path.getsize(file_path)
                    total_size += file_size
                    file_count += 1
                    
                    # Categorize by data type
                    rel_path = os.path.relpath(root, account_dir)
                    if rel_path not in data_types:
                        data_types[rel_path] = {'size': 0, 'files': 0}
                    
                    data_types[rel_path]['size'] += file_size
                    data_types[rel_path]['files'] += 1
            
            return {
                'total_size': total_size,
                'file_count': file_count,
                'data_types': data_types,
                'total_size_mb': round(total_size / (1024 * 1024), 2)
            }
            
        except Exception as e:
            from app.utils.lms_logger import resolve_enrollment
            print(f"Error getting storage info for account {resolve_enrollment(account_id)}: {e}")
            return {'total_size': 0, 'file_count': 0, 'data_types': {}}
    
    def migrate_old_data(self, old_username: str) -> Optional[str]:
        """Migrate data from old flat structure to new account-based structure"""
        try:
            # Create new account ID
            account_id = f"migrated_{old_username}_{datetime.now().strftime('%Y%m%d')}"
            
            # Create account directory structure
            account_dir = os.path.join(self.accounts_dir, account_id)
            os.makedirs(account_dir, exist_ok=True)
            
            data_types = ['attendance', 'timetable', 'gpa', 'community_service']
            
            for data_type in data_types:
                data_type_dir = os.path.join(account_dir, data_type)
                os.makedirs(data_type_dir, exist_ok=True)
                
                # Look for old files
                old_pattern = f"{old_username}_{data_type}"
                
                for filename in os.listdir(self.base_data_dir):
                    if filename.startswith(old_pattern) and filename.endswith('.json'):
                        old_path = os.path.join(self.base_data_dir, filename)
                        new_filename = filename.replace(f"{old_username}_", "")
                        new_path = os.path.join(data_type_dir, new_filename)
                        
                        # Copy file to new location
                        shutil.copy2(old_path, new_path)
                        print(f"Migrated {filename} to {new_path}")
            
            # Migrate session file
            old_session_file = os.path.join(self.base_data_dir, f'session_{old_username}.pkl')
            if os.path.exists(old_session_file):
                sessions_dir = os.path.join(account_dir, 'sessions')
                os.makedirs(sessions_dir, exist_ok=True)
                new_session_file = os.path.join(sessions_dir, 'cms_session.pkl')
                shutil.copy2(old_session_file, new_session_file)
                print(f"Migrated session file for {old_username}")
            
            return account_id
            
        except Exception as e:
            print(f"Error migrating data for {old_username}: {e}")
            return None

    def get_unified_dashboard(self, account_id: str, bypass_cache: bool = False) -> Optional[Dict]:
        """Fetch the unified snapshot (1-Read) with Flask memory caching and automatic legacy backfill."""
        cache_key = f"dashboard_{account_id}"
        now = time.time()
        
        if not bypass_cache and cache_key in self._memory_cache:
            data, t = self._memory_cache[cache_key]
            if now - t < 300: # 5 minutes TTL
                import copy
                return copy.deepcopy(data) if data is not None else None
                
        from app.services.firestore_service import get_firestore_service
        fs = get_firestore_service()
        if fs.is_available():
            doc = fs.get_document('user_dashboards', account_id, bypass_cache=bypass_cache)
            if doc is not None:
                doc = _to_json_safe(doc)
                if isinstance(doc, dict):
                    # Ensure missing snapshot fields are backfilled from legacy documents once
                    if not doc.get('legacy_migrated'):
                        needs_save = False
                        backfill_payload = {'legacy_migrated': True}
                        if 'attendance_snapshot' not in doc:
                            try:
                                legacy_att = fs.get_document('account_data', f'{account_id}_attendance', bypass_cache=bypass_cache)
                                if legacy_att and isinstance(legacy_att, dict):
                                    safe_att = _to_json_safe(legacy_att)
                                    doc['attendance_snapshot'] = safe_att
                                    backfill_payload['attendance_snapshot'] = safe_att
                                    needs_save = True
                            except Exception as e:
                                print(f"[DATA_MGR] Legacy attendance backfill failed: {e}")

                        if 'assignments_snapshot' not in doc:
                            try:
                                legacy_asg = fs.get_document('account_data', f'{account_id}_assignments_snapshot', bypass_cache=bypass_cache)
                                if legacy_asg and isinstance(legacy_asg, dict):
                                    safe_asg = _to_json_safe(legacy_asg)
                                    doc['assignments_snapshot'] = safe_asg
                                    backfill_payload['assignments_snapshot'] = safe_asg
                                    needs_save = True
                            except Exception as e:
                                print(f"[DATA_MGR] Legacy assignments backfill failed: {e}")

                        if 'attendance_yesterday_snapshot' not in doc:
                            try:
                                legacy_yest = fs.get_document('account_data', f'{account_id}_attendance_yesterday', bypass_cache=bypass_cache)
                                if legacy_yest and isinstance(legacy_yest, dict):
                                    safe_yest = _to_json_safe(legacy_yest)
                                    doc['attendance_yesterday_snapshot'] = safe_yest
                                    backfill_payload['attendance_yesterday_snapshot'] = safe_yest
                                    needs_save = True
                            except Exception as e:
                                print(f"[DATA_MGR] Legacy attendance_yesterday backfill failed: {e}")

                        doc['legacy_migrated'] = True
                        backfill_payload['last_updated'] = datetime.utcnow().isoformat() + 'Z'
                        doc['last_updated'] = backfill_payload['last_updated']
                        self.save_unified_dashboard(account_id, backfill_payload, merge=True)

                import copy
                self._memory_cache[cache_key] = (copy.deepcopy(doc), now)
                return doc

            # Document does not exist in user_dashboards: one-time migration backfill from legacy account_data
            doc = {}
            needs_save = False
            try:
                legacy_att = fs.get_document('account_data', f'{account_id}_attendance', bypass_cache=bypass_cache)
                if legacy_att and isinstance(legacy_att, dict):
                    doc['attendance_snapshot'] = _to_json_safe(legacy_att)
                    needs_save = True
            except Exception as e:
                print(f"[DATA_MGR] Legacy attendance backfill failed: {e}")

            try:
                legacy_asg = fs.get_document('account_data', f'{account_id}_assignments_snapshot', bypass_cache=bypass_cache)
                if legacy_asg and isinstance(legacy_asg, dict):
                    doc['assignments_snapshot'] = _to_json_safe(legacy_asg)
                    needs_save = True
            except Exception as e:
                print(f"[DATA_MGR] Legacy assignments backfill failed: {e}")

            try:
                legacy_yest = fs.get_document('account_data', f'{account_id}_attendance_yesterday', bypass_cache=bypass_cache)
                if legacy_yest and isinstance(legacy_yest, dict):
                    doc['attendance_yesterday_snapshot'] = _to_json_safe(legacy_yest)
                    needs_save = True
            except Exception as e:
                print(f"[DATA_MGR] Legacy attendance_yesterday backfill failed: {e}")

            if needs_save:
                doc['legacy_migrated'] = True
                doc['last_updated'] = datetime.utcnow().isoformat() + 'Z'
                self.save_unified_dashboard(account_id, doc, merge=True)

            if doc:
                import copy
                self._memory_cache[cache_key] = (copy.deepcopy(doc), now)
                return doc
            else:
                # Cache negative result to prevent repeated multi-collection lookups
                self._memory_cache[cache_key] = (None, now)
                return None
        return None

    def save_unified_dashboard(self, account_id: str, data: Dict, merge: bool = True) -> bool:
        """Save the unified snapshot (1-Write) and update memory cache with atomic merge."""
        from app.services.firestore_service import get_firestore_service
        fs = get_firestore_service()
        success = False
        safe_data = _to_json_safe(data)
        if fs.is_available():
            try:
                success = fs.save_document('user_dashboards', account_id, safe_data, merge=merge)
            except Exception as e:
                print(f"[DATA_MGR] Failed to save unified dashboard: {e}")
        else:
            success = True  # In-memory/offline fallback
            
        if success:
            cache_key = f"dashboard_{account_id}"
            import copy
            if merge:
                if cache_key in self._memory_cache and self._memory_cache[cache_key][0] is not None:
                    cached_data = self._memory_cache[cache_key][0]
                    merged_data = {**cached_data, **copy.deepcopy(safe_data)}
                    self._memory_cache[cache_key] = (merged_data, time.time())
                else:
                    self._memory_cache.pop(cache_key, None)
            else:
                self._memory_cache[cache_key] = (copy.deepcopy(safe_data), time.time())
            return True
        return False

# Initialize global data manager
data_manager = DataManager()
