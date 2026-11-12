# app/models/attendance_changes.py
from dataclasses import dataclass, asdict
from typing import Dict, List, Optional, Any
from datetime import datetime, timedelta
import json
import logging

# Configure logging for attendance changes
logger = logging.getLogger(__name__)

class AttendanceChangeValidationError(Exception):
    """Exception raised when attendance change validation fails"""
    pass


@dataclass
class AttendanceChangeBase:
    """Base class for all attendance changes."""
    course_code: str
    course_name: str
    instructor: str
    change_type: str  # 'attendance', 'new_course', 'removed_course'
    is_scheduled: bool
    timestamp: str
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert the change to a dictionary."""
        return asdict(self)
    
    def to_json(self) -> str:
        """Convert the change to JSON string."""
        return json.dumps(self.to_dict(), indent=2)
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]):
        """Create an instance from a dictionary."""
        return cls(**data)
    
    @classmethod
    def from_json(cls, json_str: str):
        """Create an instance from a JSON string."""
        data = json.loads(json_str)
        return cls.from_dict(data)
    
    def get_display_message(self) -> str:
        """Get a human-readable display message for the change."""
        return f"Change detected in {self.course_name} ({self.course_code})"
    
    def validate(self) -> bool:
        """Validate the change data with comprehensive checks."""
        try:
            validation_errors = []
            
            # Validate course code
            if not self.course_code or not isinstance(self.course_code, str) or not self.course_code.strip():
                validation_errors.append("Invalid or empty course_code")
            
            # Validate course name
            if not self.course_name or not isinstance(self.course_name, str) or not self.course_name.strip():
                validation_errors.append("Invalid or empty course_name")
            
            # Validate instructor (can be empty but must be string if present)
            if self.instructor is not None and not isinstance(self.instructor, str):
                validation_errors.append("Invalid instructor type")
            
            # Validate change type
            valid_change_types = ['attendance', 'new_course', 'removed_course']
            if self.change_type not in valid_change_types:
                validation_errors.append(f"Invalid change_type: {self.change_type}")
            
            # Validate is_scheduled
            if not isinstance(self.is_scheduled, bool):
                validation_errors.append("Invalid is_scheduled type")
            
            # Validate timestamp format
            if not self.timestamp or not isinstance(self.timestamp, str):
                validation_errors.append("Invalid timestamp type")
            else:
                try:
                    datetime.fromisoformat(self.timestamp.replace('Z', '+00:00'))
                except (ValueError, TypeError) as e:
                    validation_errors.append(f"Invalid timestamp format: {e}")
            
            if validation_errors:
                logger.error(f"Validation failed for {self.__class__.__name__}: {validation_errors}")
                return False
            
            return True
            
        except Exception as e:
            logger.error(f"Error validating {self.__class__.__name__}: {e}")
            return False
    
    def validate_strict(self) -> None:
        """Strict validation that raises exceptions with detailed error messages."""
        validation_errors = []
        
        # Validate course code
        if not self.course_code or not isinstance(self.course_code, str) or not self.course_code.strip():
            validation_errors.append("Course code is required and must be a non-empty string")
        elif len(self.course_code.strip()) > 20:
            validation_errors.append("Course code is too long (max 20 characters)")
        
        # Validate course name
        if not self.course_name or not isinstance(self.course_name, str) or not self.course_name.strip():
            validation_errors.append("Course name is required and must be a non-empty string")
        elif len(self.course_name.strip()) > 200:
            validation_errors.append("Course name is too long (max 200 characters)")
        
        # Validate instructor
        if self.instructor is not None:
            if not isinstance(self.instructor, str):
                validation_errors.append("Instructor must be a string")
            elif len(self.instructor) > 100:
                validation_errors.append("Instructor name is too long (max 100 characters)")
        
        # Validate change type
        valid_change_types = ['attendance', 'new_course', 'removed_course']
        if self.change_type not in valid_change_types:
            validation_errors.append(f"Change type must be one of: {valid_change_types}")
        
        # Validate is_scheduled
        if not isinstance(self.is_scheduled, bool):
            validation_errors.append("is_scheduled must be a boolean")
        
        # Validate timestamp
        if not self.timestamp or not isinstance(self.timestamp, str):
            validation_errors.append("Timestamp is required and must be a string")
        else:
            try:
                parsed_time = datetime.fromisoformat(self.timestamp.replace('Z', '+00:00'))
                # Check if timestamp is not too far in the future
                if parsed_time > datetime.now().replace(tzinfo=parsed_time.tzinfo) + timedelta(hours=1):
                    validation_errors.append("Timestamp cannot be more than 1 hour in the future")
            except (ValueError, TypeError) as e:
                validation_errors.append(f"Invalid timestamp format: {e}")
        
        if validation_errors:
            error_msg = f"Validation failed for {self.__class__.__name__}: " + "; ".join(validation_errors)
            raise AttendanceChangeValidationError(error_msg)


@dataclass
class AttendanceChange(AttendanceChangeBase):
    """Represents a change in attendance for an existing course."""
    old_attended_classes: int
    new_attended_classes: int
    old_total_classes: int
    new_total_classes: int
    old_percentage: float
    new_percentage: float
    old_absences: int
    new_absences: int
    classes_difference: int  # positive = attended more, negative = attended less
    total_classes_difference: int  # change in total classes
    percentage_difference: float
    absences_difference: int
    
    def __post_init__(self):
        """Post-initialization to set change_type."""
        if not hasattr(self, 'change_type') or not self.change_type:
            self.change_type = 'attendance'
    
    def get_display_message(self) -> str:
        """Get a human-readable display message for the attendance change."""
        messages = []
        
        # Attended classes change
        if self.classes_difference != 0:
            if self.classes_difference > 0:
                messages.append(f"Attended {self.classes_difference} more class(es)")
            else:
                messages.append(f"Missed {abs(self.classes_difference)} class(es)")
        
        # Total classes change
        if self.total_classes_difference != 0:
            if self.total_classes_difference > 0:
                messages.append(f"{self.total_classes_difference} new class(es) added")
            else:
                messages.append(f"{abs(self.total_classes_difference)} class(es) removed")
        
        # Percentage change
        if abs(self.percentage_difference) > 0.01:
            if self.percentage_difference > 0:
                messages.append(f"Attendance improved by {self.percentage_difference:.2f}%")
            else:
                messages.append(f"Attendance decreased by {abs(self.percentage_difference):.2f}%")
        
        if not messages:
            messages.append("Minor attendance update")
        
        base_msg = f"{self.course_name} ({self.course_code}): "
        return base_msg + ", ".join(messages)
    
    def get_change_severity(self) -> str:
        """Get the severity level of the change."""
        # Positive changes (improvements)
        if (self.classes_difference > 0 or 
            self.percentage_difference > 1.0 or 
            self.absences_difference < 0):
            return "positive"
        
        # Negative changes (deterioration)
        if (self.classes_difference < 0 or 
            self.percentage_difference < -1.0 or 
            self.absences_difference > 0):
            return "negative"
        
        # Neutral changes
        return "neutral"
    
    def get_formatted_before_after(self) -> Dict[str, str]:
        """Get formatted before/after values for display."""
        return {
            'attended_classes': f"{self.old_attended_classes} → {self.new_attended_classes}",
            'total_classes': f"{self.old_total_classes} → {self.new_total_classes}",
            'percentage': f"{self.old_percentage:.2f}% → {self.new_percentage:.2f}%",
            'absences': f"{self.old_absences} → {self.new_absences}"
        }
    
    def validate(self) -> bool:
        """Validate the attendance change data with comprehensive checks."""
        try:
            if not super().validate():
                return False
            
            validation_errors = []
            
            # Validate numeric fields are non-negative integers or floats
            numeric_fields = [
                ('old_attended_classes', self.old_attended_classes),
                ('new_attended_classes', self.new_attended_classes),
                ('old_total_classes', self.old_total_classes),
                ('new_total_classes', self.new_total_classes),
                ('old_absences', self.old_absences),
                ('new_absences', self.new_absences),
                ('classes_difference', self.classes_difference),
                ('total_classes_difference', self.total_classes_difference),
                ('absences_difference', self.absences_difference)
            ]
            
            for field_name, value in numeric_fields:
                if not isinstance(value, (int, float)):
                    validation_errors.append(f"Invalid {field_name} type: {type(value)}")
                elif field_name not in ['classes_difference', 'total_classes_difference', 'absences_difference', 'percentage_difference']:
                    # Allow negative values for difference fields
                    if value < 0:
                        validation_errors.append(f"Invalid {field_name}: {value} (must be non-negative)")
            
            # Validate percentages
            percentage_fields = [
                ('old_percentage', self.old_percentage),
                ('new_percentage', self.new_percentage)
            ]
            
            for field_name, value in percentage_fields:
                if not isinstance(value, (int, float)):
                    validation_errors.append(f"Invalid {field_name} type: {type(value)}")
                elif value < 0 or value > 100:
                    validation_errors.append(f"Invalid {field_name}: {value} (must be 0-100)")
            
            # Validate percentage_difference
            if not isinstance(self.percentage_difference, (int, float)):
                validation_errors.append(f"Invalid percentage_difference type: {type(self.percentage_difference)}")
            elif abs(self.percentage_difference) > 100:
                validation_errors.append(f"Invalid percentage_difference: {self.percentage_difference} (too large)")
            
            # Validate logical consistency
            if self.old_attended_classes > self.old_total_classes:
                validation_errors.append(f"old_attended_classes ({self.old_attended_classes}) > old_total_classes ({self.old_total_classes})")
            
            if self.new_attended_classes > self.new_total_classes:
                validation_errors.append(f"new_attended_classes ({self.new_attended_classes}) > new_total_classes ({self.new_total_classes})")
            
            # Validate calculated differences
            expected_classes_diff = self.new_attended_classes - self.old_attended_classes
            if abs(self.classes_difference - expected_classes_diff) > 0.01:
                validation_errors.append(f"classes_difference mismatch: expected {expected_classes_diff}, got {self.classes_difference}")
            
            expected_total_diff = self.new_total_classes - self.old_total_classes
            if abs(self.total_classes_difference - expected_total_diff) > 0.01:
                validation_errors.append(f"total_classes_difference mismatch: expected {expected_total_diff}, got {self.total_classes_difference}")
            
            expected_percentage_diff = self.new_percentage - self.old_percentage
            if abs(self.percentage_difference - expected_percentage_diff) > 0.1:
                validation_errors.append(f"percentage_difference mismatch: expected {expected_percentage_diff:.2f}, got {self.percentage_difference}")
            
            if validation_errors:
                logger.error(f"AttendanceChange validation failed: {validation_errors}")
                return False
            
            return True
            
        except Exception as e:
            logger.error(f"Error validating AttendanceChange: {e}")
            return False


@dataclass
class NewCourseChange(AttendanceChangeBase):
    """Represents a new course that appeared in today's attendance."""
    total_classes: int
    attended_classes: int
    attendance_percentage: float
    absences: int
    status: str  # 'safe', 'warning', 'danger'
    
    def __post_init__(self):
        """Post-initialization to set change_type."""
        if not hasattr(self, 'change_type') or not self.change_type:
            self.change_type = 'new_course'
    
    def get_display_message(self) -> str:
        """Get a human-readable display message for the new course."""
        status_msg = ""
        if self.status == 'danger':
            status_msg = " (⚠️ Low attendance)"
        elif self.status == 'warning':
            status_msg = " (⚡ Monitor attendance)"
        elif self.status == 'safe':
            status_msg = " (✅ Good attendance)"
        
        return (f"New course added: {self.course_name} ({self.course_code}) - "
                f"{self.attended_classes}/{self.total_classes} classes "
                f"({self.attendance_percentage:.2f}%){status_msg}")
    
    def get_status_info(self) -> Dict[str, Any]:
        """Get detailed status information."""
        return {
            'status': self.status,
            'attendance_ratio': f"{self.attended_classes}/{self.total_classes}",
            'percentage': f"{self.attendance_percentage:.2f}%",
            'absences': self.absences,
            'is_critical': self.status == 'danger'
        }
    
    def validate(self) -> bool:
        """Validate the new course change data with comprehensive checks."""
        try:
            if not super().validate():
                return False
            
            validation_errors = []
            
            # Validate numeric fields
            numeric_fields = [
                ('total_classes', self.total_classes),
                ('attended_classes', self.attended_classes),
                ('absences', self.absences)
            ]
            
            for field_name, value in numeric_fields:
                if not isinstance(value, (int, float)):
                    validation_errors.append(f"Invalid {field_name} type: {type(value)}")
                elif value < 0:
                    validation_errors.append(f"Invalid {field_name}: {value} (must be non-negative)")
                elif field_name in ['total_classes', 'attended_classes'] and value > 1000:
                    validation_errors.append(f"Invalid {field_name}: {value} (unreasonably large)")
            
            # Validate percentage
            if not isinstance(self.attendance_percentage, (int, float)):
                validation_errors.append(f"Invalid attendance_percentage type: {type(self.attendance_percentage)}")
            elif self.attendance_percentage < 0 or self.attendance_percentage > 100:
                validation_errors.append(f"Invalid attendance_percentage: {self.attendance_percentage} (must be 0-100)")
            
            # Validate status
            valid_statuses = ['safe', 'warning', 'danger', 'unknown']
            if not isinstance(self.status, str) or self.status not in valid_statuses:
                validation_errors.append(f"Invalid status: {self.status} (must be one of {valid_statuses})")
            
            # Validate logical consistency
            if self.attended_classes > self.total_classes:
                validation_errors.append(f"attended_classes ({self.attended_classes}) > total_classes ({self.total_classes})")
            
            # Validate percentage calculation (allow small floating point differences)
            if self.total_classes > 0:
                expected_percentage = (self.attended_classes / self.total_classes) * 100
                if abs(self.attendance_percentage - expected_percentage) > 1.0:
                    validation_errors.append(f"Percentage inconsistency: expected {expected_percentage:.2f}%, got {self.attendance_percentage:.2f}%")
            elif self.attendance_percentage != 0:
                validation_errors.append("Attendance percentage should be 0 when total_classes is 0")
            
            # Validate absences consistency
            expected_absences = self.total_classes - self.attended_classes
            if abs(self.absences - expected_absences) > 1:
                validation_errors.append(f"Absences inconsistency: expected {expected_absences}, got {self.absences}")
            
            if validation_errors:
                logger.error(f"NewCourseChange validation failed: {validation_errors}")
                return False
            
            return True
            
        except Exception as e:
            logger.error(f"Error validating NewCourseChange: {e}")
            return False


@dataclass
class RemovedCourseChange(AttendanceChangeBase):
    """Represents a course that was removed from today's attendance."""
    last_total_classes: int
    last_attended_classes: int
    last_attendance_percentage: float
    last_absences: int
    
    def __post_init__(self):
        """Post-initialization to set change_type."""
        if not hasattr(self, 'change_type') or not self.change_type:
            self.change_type = 'removed_course'
    
    def get_display_message(self) -> str:
        """Get a human-readable display message for the removed course."""
        return (f"Course removed: {self.course_name} ({self.course_code}) - "
                f"Last recorded: {self.last_attended_classes}/{self.last_total_classes} classes "
                f"({self.last_attendance_percentage:.2f}%)")
    
    def get_last_status_info(self) -> Dict[str, Any]:
        """Get information about the last recorded status."""
        return {
            'last_attendance_ratio': f"{self.last_attended_classes}/{self.last_total_classes}",
            'last_percentage': f"{self.last_attendance_percentage:.2f}%",
            'last_absences': self.last_absences
        }
    
    def validate(self) -> bool:
        """Validate the removed course change data with comprehensive checks."""
        try:
            if not super().validate():
                return False
            
            validation_errors = []
            
            # Validate numeric fields
            numeric_fields = [
                ('last_total_classes', self.last_total_classes),
                ('last_attended_classes', self.last_attended_classes),
                ('last_absences', self.last_absences)
            ]
            
            for field_name, value in numeric_fields:
                if not isinstance(value, (int, float)):
                    validation_errors.append(f"Invalid {field_name} type: {type(value)}")
                elif value < 0:
                    validation_errors.append(f"Invalid {field_name}: {value} (must be non-negative)")
                elif field_name in ['last_total_classes', 'last_attended_classes'] and value > 1000:
                    validation_errors.append(f"Invalid {field_name}: {value} (unreasonably large)")
            
            # Validate percentage
            if not isinstance(self.last_attendance_percentage, (int, float)):
                validation_errors.append(f"Invalid last_attendance_percentage type: {type(self.last_attendance_percentage)}")
            elif self.last_attendance_percentage < 0 or self.last_attendance_percentage > 100:
                validation_errors.append(f"Invalid last_attendance_percentage: {self.last_attendance_percentage} (must be 0-100)")
            
            # Validate logical consistency
            if self.last_attended_classes > self.last_total_classes:
                validation_errors.append(f"last_attended_classes ({self.last_attended_classes}) > last_total_classes ({self.last_total_classes})")
            
            # Validate percentage calculation (allow small floating point differences)
            if self.last_total_classes > 0:
                expected_percentage = (self.last_attended_classes / self.last_total_classes) * 100
                if abs(self.last_attendance_percentage - expected_percentage) > 1.0:
                    validation_errors.append(f"Last percentage inconsistency: expected {expected_percentage:.2f}%, got {self.last_attendance_percentage:.2f}%")
            elif self.last_attendance_percentage != 0:
                validation_errors.append("Last attendance percentage should be 0 when last_total_classes is 0")
            
            # Validate absences consistency
            expected_absences = self.last_total_classes - self.last_attended_classes
            if abs(self.last_absences - expected_absences) > 1:
                validation_errors.append(f"Last absences inconsistency: expected {expected_absences}, got {self.last_absences}")
            
            if validation_errors:
                logger.error(f"RemovedCourseChange validation failed: {validation_errors}")
                return False
            
            return True
            
        except Exception as e:
            logger.error(f"Error validating RemovedCourseChange: {e}")
            return False


class AttendanceChangeFactory:
    """Factory class for creating attendance change objects."""
    
    @staticmethod
    def create_change(change_data: Dict[str, Any]) -> Optional[AttendanceChangeBase]:
        """
        Create an appropriate change object based on the change type with comprehensive validation.
        
        Args:
            change_data: Dictionary containing change information
            
        Returns:
            Appropriate change object or None if invalid
        """
        try:
            if not isinstance(change_data, dict):
                logger.error(f"Invalid change_data type: {type(change_data)}")
                return None
            
            change_type = change_data.get('change_type')
            
            if not change_type:
                logger.error("Missing change_type in change_data")
                return None
            
            # Validate required base fields
            required_base_fields = ['course_code', 'course_name', 'change_type', 'is_scheduled', 'timestamp']
            missing_fields = [field for field in required_base_fields if field not in change_data]
            
            if missing_fields:
                logger.error(f"Missing required fields in change_data: {missing_fields}")
                return None
            
            # Create appropriate change object
            change_obj = None
            
            if change_type == 'attendance':
                # Validate attendance-specific required fields
                attendance_fields = ['old_attended_classes', 'new_attended_classes', 'old_total_classes', 
                                   'new_total_classes', 'old_percentage', 'new_percentage', 'old_absences', 
                                   'new_absences', 'classes_difference', 'total_classes_difference', 
                                   'percentage_difference', 'absences_difference']
                
                missing_attendance_fields = [field for field in attendance_fields if field not in change_data]
                if missing_attendance_fields:
                    logger.error(f"Missing attendance fields: {missing_attendance_fields}")
                    return None
                
                change_obj = AttendanceChange.from_dict(change_data)
                
            elif change_type == 'new_course':
                # Validate new course-specific required fields
                new_course_fields = ['total_classes', 'attended_classes', 'attendance_percentage', 'absences', 'status']
                
                missing_new_course_fields = [field for field in new_course_fields if field not in change_data]
                if missing_new_course_fields:
                    logger.error(f"Missing new course fields: {missing_new_course_fields}")
                    return None
                
                change_obj = NewCourseChange.from_dict(change_data)
                
            elif change_type == 'removed_course':
                # Validate removed course-specific required fields
                removed_course_fields = ['last_total_classes', 'last_attended_classes', 'last_attendance_percentage', 'last_absences']
                
                missing_removed_course_fields = [field for field in removed_course_fields if field not in change_data]
                if missing_removed_course_fields:
                    logger.error(f"Missing removed course fields: {missing_removed_course_fields}")
                    return None
                
                change_obj = RemovedCourseChange.from_dict(change_data)
                
            else:
                logger.error(f"Unknown change type: {change_type}")
                return None
            
            # Validate the created object
            if change_obj and change_obj.validate():
                return change_obj
            else:
                logger.error(f"Created change object failed validation for type: {change_type}")
                return None
                
        except Exception as e:
            logger.error(f"Error creating change object: {e}")
            return None
    
    @staticmethod
    def create_changes_from_list(changes_data: List[Dict[str, Any]]) -> List[AttendanceChangeBase]:
        """
        Create a list of change objects from a list of dictionaries with comprehensive validation.
        
        Args:
            changes_data: List of change dictionaries
            
        Returns:
            List of valid change objects
        """
        if not isinstance(changes_data, list):
            logger.error(f"Invalid changes_data type: {type(changes_data)}")
            return []
        
        changes = []
        failed_changes = []
        
        for i, change_data in enumerate(changes_data):
            try:
                change = AttendanceChangeFactory.create_change(change_data)
                if change and change.validate():
                    changes.append(change)
                else:
                    failed_changes.append(f"index {i}: validation failed")
                    logger.warning(f"Invalid change data at index {i}: {change_data}")
                    
            except Exception as e:
                failed_changes.append(f"index {i}: {str(e)}")
                logger.error(f"Error processing change data at index {i}: {e}")
        
        if failed_changes:
            logger.warning(f"Failed to create {len(failed_changes)} changes: {failed_changes}")
        
        logger.info(f"Successfully created {len(changes)} change objects from {len(changes_data)} input items")
        return changes
    
    @staticmethod
    def serialize_changes(changes: List[AttendanceChangeBase]) -> List[Dict[str, Any]]:
        """
        Serialize a list of change objects to dictionaries with validation.
        
        Args:
            changes: List of change objects
            
        Returns:
            List of change dictionaries for valid changes only
        """
        if not isinstance(changes, list):
            logger.error(f"Invalid changes type: {type(changes)}")
            return []
        
        serialized_changes = []
        failed_serializations = []
        
        for i, change in enumerate(changes):
            try:
                if not isinstance(change, AttendanceChangeBase):
                    failed_serializations.append(f"index {i}: not an AttendanceChangeBase instance")
                    continue
                
                if change.validate():
                    serialized_changes.append(change.to_dict())
                else:
                    failed_serializations.append(f"index {i}: validation failed")
                    logger.warning(f"Change at index {i} failed validation during serialization")
                    
            except Exception as e:
                failed_serializations.append(f"index {i}: {str(e)}")
                logger.error(f"Error serializing change at index {i}: {e}")
        
        if failed_serializations:
            logger.warning(f"Failed to serialize {len(failed_serializations)} changes: {failed_serializations}")
        
        logger.debug(f"Successfully serialized {len(serialized_changes)} changes from {len(changes)} input items")
        return serialized_changes
    
    @staticmethod
    def validate_changes_batch(changes: List[AttendanceChangeBase]) -> Dict[str, Any]:
        """
        Validate a batch of changes and return detailed validation results.
        
        Args:
            changes: List of change objects to validate
            
        Returns:
            Dictionary with validation results and statistics
        """
        if not isinstance(changes, list):
            return {
                'valid': False,
                'error': f"Invalid input type: {type(changes)}",
                'valid_changes': 0,
                'invalid_changes': 0,
                'total_changes': 0
            }
        
        valid_changes = 0
        invalid_changes = 0
        validation_errors = []
        
        for i, change in enumerate(changes):
            try:
                if not isinstance(change, AttendanceChangeBase):
                    invalid_changes += 1
                    validation_errors.append(f"Change {i}: not an AttendanceChangeBase instance")
                    continue
                
                if change.validate():
                    valid_changes += 1
                else:
                    invalid_changes += 1
                    validation_errors.append(f"Change {i}: validation failed")
                    
            except Exception as e:
                invalid_changes += 1
                validation_errors.append(f"Change {i}: validation error - {str(e)}")
        
        total_changes = len(changes)
        
        return {
            'valid': invalid_changes == 0,
            'valid_changes': valid_changes,
            'invalid_changes': invalid_changes,
            'total_changes': total_changes,
            'validation_errors': validation_errors,
            'success_rate': (valid_changes / total_changes * 100) if total_changes > 0 else 0
        }