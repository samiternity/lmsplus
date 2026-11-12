# app/services/attendance_change_tracker.py
from typing import Dict, List, Optional, Any
from datetime import datetime
import json
import os
import logging
from app.services.attendance_comparator import AttendanceComparator
from app.models.attendance_changes import AttendanceChangeBase, AttendanceChangeFactory

# Configure logging for change tracking
logger = logging.getLogger(__name__)

class ChangeTrackerError(Exception):
    """Base exception for change tracking operations"""
    pass

class DataValidationError(ChangeTrackerError):
    """Exception raised when data validation fails"""
    pass

class RecordStorageError(ChangeTrackerError):
    """Exception raised when record storage operations fail"""
    pass

class ComparisonError(ChangeTrackerError):
    """Exception raised when attendance comparison fails"""
    pass


class AttendanceChangeTracker:
    """
    Main service for tracking attendance changes between today and yesterday.
    Manages record lifecycle and coordinates change detection.
    """
    
    def __init__(self, data_manager_instance, account_id: str):
        """
        Initialize the change tracker for a specific account.
        
        Args:
            data_manager_instance: The data manager instance to use
            account_id: The account ID to track changes for
        """
        self.data_manager = data_manager_instance
        self.account_id = account_id
        self.comparator = AttendanceComparator(data_manager_instance, account_id)
    
    def detect_changes(self) -> List[AttendanceChangeBase]:
        """
        Detect changes between today's and yesterday's attendance records.
        
        Returns:
            List of AttendanceChangeBase objects representing detected changes
            
        Raises:
            ChangeTrackerError: When change detection fails
        """
        try:
            logger.info(f"Starting change detection for account {self.account_id}")
            
            # Load today's attendance data with validation
            today_data = self._load_and_validate_attendance_data('attendance', 'today')
            if not today_data:
                logger.warning(f"No valid today's attendance data found for account {self.account_id}")
                return []
            
            # Load yesterday's attendance data with validation
            yesterday_data = self._load_and_validate_attendance_data('attendance_yesterday', 'yesterday')
            if not yesterday_data:
                logger.warning(f"No valid yesterday's attendance data found for account {self.account_id}")
                # Return empty list for graceful handling of missing yesterday records
                return []
            
            # Validate data integrity before comparison
            if not self._validate_data_integrity(today_data, yesterday_data):
                logger.error(f"Data integrity check failed for account {self.account_id}")
                raise DataValidationError("Data integrity validation failed")
            
            # Use comparator to detect changes with error handling
            try:
                changes = self.comparator.compare_records(today_data, yesterday_data)
                logger.info(f"Successfully detected {len(changes)} changes for account {self.account_id}")
                return changes
                
            except Exception as comp_error:
                logger.error(f"Comparison failed for account {self.account_id}: {comp_error}")
                raise ComparisonError(f"Failed to compare attendance records: {comp_error}")
            
        except (DataValidationError, ComparisonError):
            # Re-raise specific errors
            raise
        except Exception as e:
            logger.error(f"Unexpected error detecting changes for account {self.account_id}: {e}")
            raise ChangeTrackerError(f"Change detection failed: {e}")
    
    def _load_and_validate_attendance_data(self, data_type: str, data_label: str) -> Optional[Dict]:
        """
        Load and validate attendance data with comprehensive error handling.
        
        Args:
            data_type: Type of data to load ('attendance' or 'attendance_yesterday')
            data_label: Human-readable label for logging ('today' or 'yesterday')
            
        Returns:
            Validated attendance data or None if invalid/missing
            
        Raises:
            DataValidationError: When data validation fails
        """
        try:
            # Load data from data manager
            data = self.data_manager.load_account_data(
                account_id=self.account_id,
                data_type=data_type
            )
            
            if not data:
                logger.info(f"No {data_label} attendance data found for account {self.account_id}")
                return None
            
            # Validate data structure
            if not self._validate_attendance_data(data):
                logger.error(f"Invalid {data_label} attendance data structure for account {self.account_id}")
                if data_type == 'attendance':
                    # Today's data being invalid is more critical
                    raise DataValidationError(f"Invalid {data_label} attendance data structure")
                else:
                    # Yesterday's data being invalid is less critical - return None for graceful handling
                    return None
            
            logger.debug(f"Successfully loaded and validated {data_label} data for account {self.account_id}")
            return data
            
        except DataValidationError:
            # Re-raise validation errors
            raise
        except Exception as e:
            logger.error(f"Error loading {data_label} data for account {self.account_id}: {e}")
            if data_type == 'attendance':
                # Today's data loading failure is critical
                raise DataValidationError(f"Failed to load {data_label} attendance data: {e}")
            else:
                # Yesterday's data loading failure is less critical
                return None
    
    def _validate_data_integrity(self, today_data: Dict, yesterday_data: Dict) -> bool:
        """
        Validate data integrity between today and yesterday records.
        
        Args:
            today_data: Today's attendance data
            yesterday_data: Yesterday's attendance data
            
        Returns:
            True if data integrity is valid, False otherwise
        """
        try:
            # Check if both datasets have the required structure
            if not isinstance(today_data, dict) or not isinstance(yesterday_data, dict):
                logger.error("Data integrity check failed: Invalid data types")
                return False
            
            # Check if both have courses
            today_courses = today_data.get('courses', [])
            yesterday_courses = yesterday_data.get('courses', [])
            
            if not isinstance(today_courses, list) or not isinstance(yesterday_courses, list):
                logger.error("Data integrity check failed: Invalid courses structure")
                return False
            
            # Check for reasonable data consistency
            if len(today_courses) == 0:
                logger.warning("Data integrity check: Today has no courses")
                return False
            
            # Validate course data consistency
            for course in today_courses:
                if not self._validate_course_data_integrity(course):
                    logger.error(f"Data integrity check failed for course: {course.get('code', 'unknown')}")
                    return False
            
            for course in yesterday_courses:
                if not self._validate_course_data_integrity(course):
                    logger.error(f"Data integrity check failed for yesterday course: {course.get('code', 'unknown')}")
                    return False
            
            logger.debug("Data integrity check passed")
            return True
            
        except Exception as e:
            logger.error(f"Error during data integrity check: {e}")
            return False
    
    def _validate_course_data_integrity(self, course: Dict) -> bool:
        """
        Validate integrity of individual course data.
        
        Args:
            course: Course data dictionary
            
        Returns:
            True if course data is valid, False otherwise
        """
        try:
            # Check required fields
            required_fields = ['code', 'name', 'total_classes', 'attended_classes', 'attendance_percentage']
            for field in required_fields:
                if field not in course:
                    return False
            
            # Validate numeric consistency
            total_classes = course.get('total_classes', 0)
            attended_classes = course.get('attended_classes', 0)
            percentage = course.get('attendance_percentage', 0)
            
            # Check for logical consistency
            if total_classes < 0 or attended_classes < 0:
                return False
            
            if attended_classes > total_classes:
                return False
            
            # Check percentage calculation (allow small floating point differences)
            if total_classes > 0:
                expected_percentage = (attended_classes / total_classes) * 100
                if abs(percentage - expected_percentage) > 1.0:  # Allow 1% tolerance
                    logger.warning(f"Percentage inconsistency in course {course.get('code')}: "
                                 f"expected {expected_percentage:.2f}%, got {percentage:.2f}%")
            
            return True
            
        except Exception as e:
            logger.error(f"Error validating course data integrity: {e}")
            return False
    
    def save_today_as_yesterday(self) -> bool:
        """
        Save today's attendance record as yesterday's record for future comparison.
        Includes validation and backup functionality with comprehensive error handling.
        
        Returns:
            True if successful, False otherwise
            
        Raises:
            RecordStorageError: When record storage operations fail critically
        """
        backup_created = False
        backup_data = None
        
        try:
            logger.info(f"Starting save today as yesterday for account {self.account_id}")
            
            # Load and validate today's attendance data
            today_data = self._load_and_validate_attendance_data('attendance', 'today')
            if not today_data:
                logger.error(f"No valid today's attendance data to save for account {self.account_id}")
                return False
            
            # Additional validation before saving
            if not self._validate_data_for_storage(today_data):
                logger.error(f"Today's attendance data failed storage validation for account {self.account_id}")
                raise DataValidationError("Today's data is not suitable for storage")
            
            # Create backup of existing yesterday record if it exists
            try:
                existing_yesterday = self.data_manager.load_account_data(
                    account_id=self.account_id,
                    data_type='attendance_yesterday'
                )
                
                if existing_yesterday:
                    backup_data = existing_yesterday.copy()  # Keep in memory for rollback
                    backup_success = self.data_manager.save_account_data(
                        account_id=self.account_id,
                        data_type='attendance_yesterday_backup',
                        data=existing_yesterday,
                        create_history=False
                    )
                    backup_created = backup_success
                    
                    if backup_success:
                        logger.info(f"Created backup of existing yesterday record for account {self.account_id}")
                    else:
                        logger.warning(f"Failed to create backup, proceeding with caution for account {self.account_id}")
                        
            except Exception as backup_error:
                logger.warning(f"Backup creation failed for account {self.account_id}: {backup_error}")
                # Continue without backup - not critical enough to fail the operation
            
            # Save as yesterday's record with retry logic
            success = self._save_with_retry(
                data_type='attendance_yesterday',
                data=today_data,
                max_retries=3
            )
            
            if success:
                logger.info(f"Successfully saved today's attendance as yesterday's record for account {self.account_id}")
                
                # Clean up backup if save was successful
                if backup_created:
                    self._cleanup_backup_file('attendance_yesterday_backup')
                
                return True
            else:
                logger.error(f"Failed to save yesterday's record for account {self.account_id}")
                
                # Attempt to restore from backup if save failed
                if backup_data:
                    self._restore_from_backup(backup_data)
                
                return False
            
        except DataValidationError:
            # Re-raise validation errors
            raise
        except Exception as e:
            logger.error(f"Unexpected error saving today as yesterday for account {self.account_id}: {e}")
            
            # Attempt to restore from backup if we have it
            if backup_data:
                self._restore_from_backup(backup_data)
            
            raise RecordStorageError(f"Failed to save today as yesterday: {e}")
    
    def _validate_data_for_storage(self, data: Dict) -> bool:
        """
        Validate data is suitable for storage operations.
        
        Args:
            data: Data to validate for storage
            
        Returns:
            True if data is suitable for storage, False otherwise
        """
        try:
            # Basic structure validation
            if not self._validate_attendance_data(data):
                return False
            
            # Check for minimum data requirements
            courses = data.get('courses', [])
            if len(courses) == 0:
                logger.warning("Data has no courses - may not be suitable for storage")
                return False
            
            # Check for data freshness (optional)
            last_updated = data.get('last_updated')
            if last_updated:
                try:
                    if isinstance(last_updated, str):
                        update_time = datetime.fromisoformat(last_updated.replace('Z', '+00:00'))
                    else:
                        update_time = last_updated
                    
                    # Check if data is too old (more than 24 hours)
                    age = datetime.now() - update_time
                    if age.total_seconds() > 86400:  # 24 hours
                        logger.warning(f"Data is {age.total_seconds()/3600:.1f} hours old")
                        
                except (ValueError, TypeError) as e:
                    logger.warning(f"Could not parse last_updated timestamp: {e}")
            
            return True
            
        except Exception as e:
            logger.error(f"Error validating data for storage: {e}")
            return False
    
    def _save_with_retry(self, data_type: str, data: Dict, max_retries: int = 3) -> bool:
        """
        Save data with retry logic for handling transient failures.
        
        Args:
            data_type: Type of data to save
            data: Data to save
            max_retries: Maximum number of retry attempts
            
        Returns:
            True if successful, False otherwise
        """
        for attempt in range(max_retries):
            try:
                logger.debug(f"Save attempt {attempt + 1}/{max_retries} for {data_type}")
                
                success = self.data_manager.save_account_data(
                    account_id=self.account_id,
                    data_type=data_type,
                    data=data,
                    create_history=False
                )
                
                if success:
                    logger.debug(f"Save successful on attempt {attempt + 1}")
                    return True
                else:
                    logger.warning(f"Save failed on attempt {attempt + 1}")
                    
            except Exception as e:
                logger.warning(f"Save attempt {attempt + 1} failed with error: {e}")
            
            # Wait before retry (exponential backoff)
            if attempt < max_retries - 1:
                wait_time = 2 ** attempt  # 1, 2, 4 seconds
                logger.debug(f"Waiting {wait_time} seconds before retry")
                import time
                time.sleep(wait_time)
        
        logger.error(f"All {max_retries} save attempts failed for {data_type}")
        return False
    
    def _restore_from_backup(self, backup_data: Dict) -> bool:
        """
        Restore yesterday's record from backup data.
        
        Args:
            backup_data: Backup data to restore
            
        Returns:
            True if restoration successful, False otherwise
        """
        try:
            logger.info(f"Attempting to restore yesterday record from backup for account {self.account_id}")
            
            restore_success = self.data_manager.save_account_data(
                account_id=self.account_id,
                data_type='attendance_yesterday',
                data=backup_data,
                create_history=False
            )
            
            if restore_success:
                logger.info(f"Successfully restored yesterday record from backup for account {self.account_id}")
                return True
            else:
                logger.error(f"Failed to restore yesterday record from backup for account {self.account_id}")
                return False
                
        except Exception as restore_error:
            logger.error(f"Error restoring yesterday record from backup for account {self.account_id}: {restore_error}")
            return False
    
    def _cleanup_backup_file(self, backup_data_type: str) -> None:
        """
        Clean up backup file after successful operation.
        
        Args:
            backup_data_type: Type of backup data to clean up
        """
        try:
            # Note: data_manager doesn't expose _get_file_path, so we'll try a different approach
            # We'll attempt to load and then delete by saving None or empty data
            logger.debug(f"Cleaning up backup file {backup_data_type} for account {self.account_id}")
            
            # For now, we'll leave the backup file as it will be cleaned up by the data manager's
            # automatic cleanup process. This is safer than trying to access private methods.
            
        except Exception as cleanup_error:
            logger.warning(f"Failed to clean up backup file {backup_data_type}: {cleanup_error}")
            # Non-critical error - don't fail the operation
    
    def _validate_attendance_data(self, data: Dict) -> bool:
        """
        Validate attendance data structure and content with comprehensive checks.
        
        Args:
            data: Attendance data to validate
            
        Returns:
            True if data is valid, False otherwise
        """
        try:
            # Basic type check
            if not isinstance(data, dict):
                logger.error("Attendance data is not a dictionary")
                return False
            
            # Check required top-level fields
            required_top_fields = ['courses']
            for field in required_top_fields:
                if field not in data:
                    logger.error(f"Missing required field: {field}")
                    return False
            
            courses = data['courses']
            if not isinstance(courses, list):
                logger.error("Courses field is not a list")
                return False
            
            # Validate each course with detailed error reporting
            for i, course in enumerate(courses):
                if not self._validate_course_data(course, i):
                    return False
            
            # Validate optional top-level fields if present
            if 'overall_percentage' in data:
                overall_percentage = data['overall_percentage']
                if not isinstance(overall_percentage, (int, float)) or overall_percentage < 0 or overall_percentage > 100:
                    logger.error(f"Invalid overall_percentage: {overall_percentage}")
                    return False
            
            if 'last_updated' in data:
                last_updated = data['last_updated']
                if not isinstance(last_updated, str):
                    logger.error(f"Invalid last_updated format: {type(last_updated)}")
                    return False
                
                # Try to parse the timestamp
                try:
                    datetime.fromisoformat(last_updated.replace('Z', '+00:00'))
                except (ValueError, TypeError) as e:
                    logger.error(f"Invalid last_updated timestamp format: {last_updated}, error: {e}")
                    return False
            
            logger.debug(f"Attendance data validation passed for {len(courses)} courses")
            return True
            
        except Exception as e:
            logger.error(f"Error validating attendance data: {e}")
            return False
    
    def _validate_course_data(self, course: Dict, course_index: int) -> bool:
        """
        Validate individual course data with detailed error reporting.
        
        Args:
            course: Course data to validate
            course_index: Index of course in the list (for error reporting)
            
        Returns:
            True if course data is valid, False otherwise
        """
        try:
            if not isinstance(course, dict):
                logger.error(f"Course {course_index} is not a dictionary")
                return False
            
            # Check required course fields
            required_fields = ['code', 'name', 'total_classes', 'attended_classes', 'attendance_percentage']
            for field in required_fields:
                if field not in course:
                    logger.error(f"Course {course_index} missing required field: {field}")
                    return False
            
            # Validate string fields
            string_fields = ['code', 'name']
            for field in string_fields:
                if not isinstance(course.get(field), str) or not course[field].strip():
                    logger.error(f"Course {course_index} has invalid {field}: {course.get(field)}")
                    return False
            
            # Validate numeric fields with range checks
            total_classes = course.get('total_classes')
            attended_classes = course.get('attended_classes')
            attendance_percentage = course.get('attendance_percentage')
            
            # Total classes validation
            if not isinstance(total_classes, (int, float)) or total_classes < 0:
                logger.error(f"Course {course_index} ({course.get('code')}) has invalid total_classes: {total_classes}")
                return False
            
            # Attended classes validation
            if not isinstance(attended_classes, (int, float)) or attended_classes < 0:
                logger.error(f"Course {course_index} ({course.get('code')}) has invalid attended_classes: {attended_classes}")
                return False
            
            # Attendance percentage validation
            if not isinstance(attendance_percentage, (int, float)) or attendance_percentage < 0 or attendance_percentage > 100:
                logger.error(f"Course {course_index} ({course.get('code')}) has invalid attendance_percentage: {attendance_percentage}")
                return False
            
            # Logical consistency checks
            if attended_classes > total_classes:
                logger.error(f"Course {course_index} ({course.get('code')}) has attended_classes ({attended_classes}) > total_classes ({total_classes})")
                return False
            
            # Validate optional fields if present
            optional_numeric_fields = [
                'absences', 'max_absences', 'remaining_absences', 'credit_hours',
                'safe_skips', 'fine_skips', 'shortfall_hours', 'fine_amount',
                'safe_max_absences', 'fine_max_absences'
            ]
            for field in optional_numeric_fields:
                if field in course:
                    value = course[field]
                    if not isinstance(value, (int, float)) or value < 0:
                        logger.error(f"Course {course_index} ({course.get('code')}) has invalid {field}: {value}")
                        return False
            
            # Validate status field if present
            if 'status' in course:
                status = course['status']
                valid_statuses = ['safe', 'warning', 'danger', 'unknown']
                if status not in valid_statuses:
                    logger.error(f"Course {course_index} ({course.get('code')}) has invalid status: {status}")
                    return False
            
            # Validate instructor field if present
            if 'instructor' in course:
                instructor = course['instructor']
                if not isinstance(instructor, str):
                    logger.error(f"Course {course_index} ({course.get('code')}) has invalid instructor: {instructor}")
                    return False
            
            return True
            
        except Exception as e:
            logger.error(f"Error validating course {course_index} data: {e}")
            return False
    
    def cleanup_old_records(self) -> bool:
        """
        Clean up old attendance records beyond yesterday with comprehensive error handling.
        Currently maintains only today and yesterday records.
        
        Returns:
            True if successful, False otherwise
            
        Raises:
            RecordStorageError: When cleanup operations fail critically
        """
        try:
            logger.info(f"Starting cleanup of old records for account {self.account_id}")
            
            # Check if we need to rotate records based on date
            rotation_success = self._rotate_records_if_needed()
            if not rotation_success:
                logger.warning(f"Record rotation check failed for account {self.account_id}")
                # Continue with cleanup even if rotation fails
            
            # Perform additional cleanup operations
            cleanup_results = {
                'rotation_check': rotation_success,
                'backup_cleanup': self._cleanup_old_backup_files(),
                'temp_cleanup': self._cleanup_temporary_files()
            }
            
            # Log cleanup results
            successful_operations = sum(1 for result in cleanup_results.values() if result)
            total_operations = len(cleanup_results)
            
            logger.info(f"Cleanup completed for account {self.account_id}: "
                       f"{successful_operations}/{total_operations} operations successful")
            
            # Return True if at least rotation check succeeded (most critical)
            return cleanup_results['rotation_check']
            
        except Exception as e:
            logger.error(f"Error during cleanup for account {self.account_id}: {e}")
            raise RecordStorageError(f"Cleanup operation failed: {e}")
    
    def _cleanup_old_backup_files(self) -> bool:
        """
        Clean up old backup files that are no longer needed.
        
        Returns:
            True if successful, False otherwise
        """
        try:
            # Try to clean up backup files older than 24 hours
            # This is a best-effort operation
            logger.debug(f"Cleaning up old backup files for account {self.account_id}")
            
            # Since we don't have direct access to file paths, we'll rely on
            # the data manager's built-in cleanup mechanisms
            # This is a placeholder for future enhancement
            
            return True
            
        except Exception as e:
            logger.warning(f"Error cleaning up backup files for account {self.account_id}: {e}")
            return False
    
    def _cleanup_temporary_files(self) -> bool:
        """
        Clean up any temporary files created during operations.
        
        Returns:
            True if successful, False otherwise
        """
        try:
            # Clean up any temporary files that might have been created
            # This is a placeholder for future enhancement
            logger.debug(f"Cleaning up temporary files for account {self.account_id}")
            
            return True
            
        except Exception as e:
            logger.warning(f"Error cleaning up temporary files for account {self.account_id}: {e}")
            return False
    
    def _rotate_records_if_needed(self) -> bool:
        """
        Rotate records if a new day has started with comprehensive error handling.
        This ensures yesterday's record is properly maintained.
        
        Returns:
            True if rotation was performed or not needed, False on error
        """
        try:
            logger.debug(f"Checking if record rotation is needed for account {self.account_id}")
            
            # Load yesterday's record to check its date with validation
            yesterday_data = self._load_and_validate_attendance_data('attendance_yesterday', 'yesterday')
            
            if not yesterday_data:
                logger.info(f"No yesterday record exists for account {self.account_id}, no rotation needed")
                return True
            
            # Check if yesterday's record is actually from yesterday
            yesterday_timestamp = yesterday_data.get('last_updated')
            if not yesterday_timestamp:
                logger.warning(f"Yesterday record has no timestamp for account {self.account_id}")
                return True  # Continue without rotation - not critical
            
            try:
                # Parse the timestamp with error handling
                if isinstance(yesterday_timestamp, str):
                    yesterday_date = datetime.fromisoformat(yesterday_timestamp.replace('Z', '+00:00'))
                else:
                    yesterday_date = yesterday_timestamp
                
                current_date = datetime.now()
                
                # Calculate age of yesterday's record
                time_diff = current_date - yesterday_date
                days_diff = time_diff.days
                hours_diff = time_diff.total_seconds() / 3600
                
                logger.debug(f"Yesterday record is {days_diff} days ({hours_diff:.1f} hours) old")
                
                # If the "yesterday" record is ~24 hours old or older, consider rotation
                if days_diff >= 1 or hours_diff >= 23:
                    logger.info(f"Yesterday record is {hours_diff:.1f} hours old, considering rotation for account {self.account_id}")
                    
                    # Check if we have fresh today's data to replace it with
                    today_data = self._load_and_validate_attendance_data('attendance', 'today')
                    
                    if today_data:
                        logger.info(f"Fresh today's data available, performing rotation for account {self.account_id}")
                        try:
                            return self.save_today_as_yesterday()
                        except (DataValidationError, RecordStorageError) as rotation_error:
                            logger.error(f"Record rotation failed for account {self.account_id}: {rotation_error}")
                            return False
                    else:
                        logger.warning(f"No fresh today's data available for rotation for account {self.account_id}")
                        return True  # Keep existing yesterday record
                
                elif hours_diff > 36:  # More than 36 hours old
                    logger.info(f"Yesterday record is {hours_diff:.1f} hours old, may need attention for account {self.account_id}")
                
                return True
                
            except (ValueError, TypeError) as timestamp_error:
                logger.error(f"Error parsing timestamp for record rotation for account {self.account_id}: {timestamp_error}")
                # Continue without rotation - timestamp parsing error is not critical
                return True
            
        except Exception as e:
            logger.error(f"Error during record rotation check for account {self.account_id}: {e}")
            return False
    
    def perform_record_lifecycle_management(self) -> Dict[str, bool]:
        """
        Perform complete record lifecycle management including rotation and cleanup.
        
        Returns:
            Dictionary with results of each operation
        """
        results = {
            'rotation_check': False,
            'cleanup': False,
            'yesterday_record_exists': False
        }
        
        try:
            # Check and perform record rotation if needed
            results['rotation_check'] = self._rotate_records_if_needed()
            
            # Perform cleanup of old records
            results['cleanup'] = self.cleanup_old_records()
            
            # Check if yesterday record exists after operations
            results['yesterday_record_exists'] = self.has_yesterday_record()
            
            return results
            
        except Exception as e:
            logger.warning(f"Error during record lifecycle management for account {self.account_id}: {e}")
            results['error'] = str(e)
            return results
    
    def get_change_summary(self) -> Dict:
        """
        Get a summary of detected changes with comprehensive error handling.
        
        Returns:
            Dictionary containing change summary information
        """
        try:
            logger.debug(f"Getting change summary for account {self.account_id}")
            
            # Detect changes with error handling
            try:
                changes = self.detect_changes()
            except (ChangeTrackerError, DataValidationError, ComparisonError) as e:
                logger.error(f"Change detection failed for summary for account {self.account_id}: {e}")
                return {
                    'total_changes': 0,
                    'attendance_changes': 0,
                    'new_courses': 0,
                    'removed_courses': 0,
                    'has_changes': False,
                    'error': str(e),
                    'error_type': type(e).__name__
                }
            
            if not changes:
                logger.debug(f"No changes detected for account {self.account_id}")
                return {
                    'total_changes': 0,
                    'attendance_changes': 0,
                    'new_courses': 0,
                    'removed_courses': 0,
                    'has_changes': False
                }
            
            # Categorize changes with validation
            attendance_changes = 0
            new_courses = 0
            removed_courses = 0
            invalid_changes = 0
            
            for change in changes:
                try:
                    change_type = change.change_type
                    if change_type == 'attendance':
                        attendance_changes += 1
                    elif change_type == 'new_course':
                        new_courses += 1
                    elif change_type == 'removed_course':
                        removed_courses += 1
                    else:
                        logger.warning(f"Unknown change type: {change_type}")
                        invalid_changes += 1
                except AttributeError as e:
                    logger.error(f"Invalid change object in summary: {e}")
                    invalid_changes += 1
            
            # Convert changes to dictionaries for JSON serialization with error handling
            try:
                changes_dict = AttendanceChangeFactory.serialize_changes(changes)
            except Exception as serialization_error:
                logger.error(f"Error serializing changes for account {self.account_id}: {serialization_error}")
                changes_dict = []  # Provide empty list as fallback
            
            summary = {
                'total_changes': len(changes),
                'attendance_changes': attendance_changes,
                'new_courses': new_courses,
                'removed_courses': removed_courses,
                'has_changes': len(changes) > 0,
                'changes': changes_dict
            }
            
            # Add warning if there were invalid changes
            if invalid_changes > 0:
                summary['invalid_changes'] = invalid_changes
                summary['warning'] = f"{invalid_changes} invalid changes detected"
            
            logger.info(f"Change summary for account {self.account_id}: {attendance_changes} attendance, "
                       f"{new_courses} new courses, {removed_courses} removed courses")
            
            return summary
            
        except Exception as e:
            logger.error(f"Unexpected error getting change summary for account {self.account_id}: {e}")
            return {
                'total_changes': 0,
                'attendance_changes': 0,
                'new_courses': 0,
                'removed_courses': 0,
                'has_changes': False,
                'error': str(e),
                'error_type': 'UnexpectedError'
            }
    
    def has_yesterday_record(self) -> bool:
        """
        Check if yesterday's record exists for comparison with comprehensive validation.
        
        Returns:
            True if yesterday's record exists and is valid, False otherwise
        """
        try:
            logger.debug(f"Checking if yesterday record exists for account {self.account_id}")
            
            # Load yesterday's data with validation
            yesterday_data = self._load_and_validate_attendance_data('attendance_yesterday', 'yesterday')
            
            if not yesterday_data:
                logger.debug(f"No valid yesterday record found for account {self.account_id}")
                return False
            
            # Additional checks for data completeness
            courses = yesterday_data.get('courses', [])
            if not courses or len(courses) == 0:
                logger.debug(f"Yesterday record exists but has no courses for account {self.account_id}")
                return False
            
            logger.debug(f"Valid yesterday record found with {len(courses)} courses for account {self.account_id}")
            return True
            
        except Exception as e:
            logger.error(f"Error checking yesterday record for account {self.account_id}: {e}")
            return False
    
    def initialize_yesterday_record(self) -> bool:
        """
        Initialize yesterday's record with today's data if it doesn't exist.
        This is useful for first-time setup with comprehensive error handling.
        
        Returns:
            True if successful, False otherwise
            
        Raises:
            ChangeTrackerError: When initialization fails critically
        """
        try:
            logger.info(f"Initializing yesterday record for account {self.account_id}")
            
            # Check if yesterday record already exists
            if self.has_yesterday_record():
                logger.info(f"Yesterday record already exists for account {self.account_id}")
                return True
            
            # Verify we have valid today's data before initializing
            today_data = self._load_and_validate_attendance_data('attendance', 'today')
            if not today_data:
                logger.error(f"Cannot initialize yesterday record - no valid today's data for account {self.account_id}")
                raise DataValidationError("No valid today's data available for initialization")
            
            # Initialize yesterday record with today's data
            try:
                success = self.save_today_as_yesterday()
                if success:
                    logger.info(f"Successfully initialized yesterday record for account {self.account_id}")
                else:
                    logger.error(f"Failed to initialize yesterday record for account {self.account_id}")
                
                return success
                
            except (DataValidationError, RecordStorageError) as init_error:
                logger.error(f"Initialization failed for account {self.account_id}: {init_error}")
                raise ChangeTrackerError(f"Yesterday record initialization failed: {init_error}")
            
        except (DataValidationError, ChangeTrackerError):
            # Re-raise specific errors
            raise
        except Exception as e:
            logger.error(f"Unexpected error initializing yesterday record for account {self.account_id}: {e}")
            raise ChangeTrackerError(f"Yesterday record initialization failed: {e}")