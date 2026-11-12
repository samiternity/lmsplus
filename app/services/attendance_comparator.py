# app/services/attendance_comparator.py
from typing import Dict, List, Optional, Any
from datetime import datetime
import json
import logging
import re
from app.models.attendance_changes import AttendanceChange, NewCourseChange, RemovedCourseChange, AttendanceChangeBase

# Configure logging for attendance comparison
logger = logging.getLogger(__name__)

class AttendanceComparatorError(Exception):
    """Base exception for attendance comparison operations"""
    pass

class DataIntegrityError(AttendanceComparatorError):
    """Exception raised when data integrity checks fail"""
    pass

class CourseValidationError(AttendanceComparatorError):
    """Exception raised when course data validation fails"""
    pass

class TimetableError(AttendanceComparatorError):
    """Exception raised when timetable operations fail"""
    pass


class AttendanceComparator:
    """
    Engine for comparing attendance records and identifying specific changes.
    Handles course code mapping and change categorization.
    """
    
    def __init__(self, data_manager=None, account_id: Optional[str] = None):
        """
        Initialize the comparator with course code mappings.
        
        Args:
            data_manager: Optional data manager for loading timetable data
            account_id: Optional account ID for loading timetable data
        """
        # Course code mapping from attendance codes to timetable codes
        self.course_code_mapping = {
            'CSC 221': 'DSAA',  # Data Structures and Algorithms
            'CSL 221': 'DSAAL',  # Data Structures and Algorithms Lab
            'CEN 323': 'COAL',  # Computer Organization & Assembly Language
            'CEL 323': 'COALL',  # Computer Organization & Assembly Language Lab
            'AIC 202': 'PFAI',  # Programming for Artificial Intelligence
            'AIL 202': 'PFAIL',  # Programming for Artificial Intelligence Lab
            'GSC 121': 'LA',  # Linear Algebra
            'ENG 134': 'CS',  # Communication Skills
            'HSS 219': 'CACE',  # Civic and Community Engagement
            'ISL 109': 'UQI-I',  # Understanding Quran-II
        }
        
        # Store data manager and account ID for timetable access
        self.data_manager = data_manager
        self.account_id = account_id
        self._timetable_data = None  # Cache timetable data
    
    def compare_records(self, today_data: Dict, yesterday_data: Dict) -> List[AttendanceChangeBase]:
        """
        Compare today's and yesterday's attendance records to detect changes with comprehensive validation.
        
        Args:
            today_data: Today's attendance data
            yesterday_data: Yesterday's attendance data
            
        Returns:
            List of detected changes as AttendanceChangeBase objects
            
        Raises:
            AttendanceComparatorError: When comparison operations fail
        """
        try:
            logger.info("Starting attendance records comparison")
            
            # Validate input data integrity
            if not self._validate_comparison_data(today_data, yesterday_data):
                raise DataIntegrityError("Input data failed integrity validation")
            
            changes = []
            
            # Extract and validate course data
            today_courses = self._extract_and_validate_courses(today_data, 'today')
            yesterday_courses = self._extract_and_validate_courses(yesterday_data, 'yesterday')
            
            if not today_courses and not yesterday_courses:
                logger.warning("Both today and yesterday have no valid courses")
                return []
            
            # Detect attendance changes for existing courses with error handling
            try:
                attendance_changes = self.detect_attendance_changes(today_courses, yesterday_courses)
                changes.extend(attendance_changes)
                logger.debug(f"Detected {len(attendance_changes)} attendance changes")
            except Exception as e:
                logger.error(f"Error detecting attendance changes: {e}")
                raise AttendanceComparatorError(f"Failed to detect attendance changes: {e}")
            
            # Detect new courses with error handling
            try:
                new_courses = self.detect_new_courses(today_courses, yesterday_courses)
                changes.extend(new_courses)
                logger.debug(f"Detected {len(new_courses)} new courses")
            except Exception as e:
                logger.error(f"Error detecting new courses: {e}")
                raise AttendanceComparatorError(f"Failed to detect new courses: {e}")
            
            # Detect removed courses with error handling
            try:
                removed_courses = self.detect_removed_courses(today_courses, yesterday_courses)
                changes.extend(removed_courses)
                logger.debug(f"Detected {len(removed_courses)} removed courses")
            except Exception as e:
                logger.error(f"Error detecting removed courses: {e}")
                raise AttendanceComparatorError(f"Failed to detect removed courses: {e}")
            
            logger.info(f"Comparison completed successfully: {len(changes)} total changes detected")
            return changes
            
        except (DataIntegrityError, AttendanceComparatorError):
            # Re-raise specific errors
            raise
        except Exception as e:
            logger.error(f"Unexpected error comparing attendance records: {e}")
            raise AttendanceComparatorError(f"Attendance comparison failed: {e}")
    
    def _validate_comparison_data(self, today_data: Dict, yesterday_data: Dict) -> bool:
        """
        Validate data integrity for comparison operations.
        
        Args:
            today_data: Today's attendance data
            yesterday_data: Yesterday's attendance data
            
        Returns:
            True if data is valid for comparison, False otherwise
        """
        try:
            # Basic type validation
            if not isinstance(today_data, dict) or not isinstance(yesterday_data, dict):
                logger.error("Comparison data must be dictionaries")
                return False
            
            # Check for required structure
            if 'courses' not in today_data or 'courses' not in yesterday_data:
                logger.error("Comparison data missing 'courses' field")
                return False
            
            # Validate courses structure
            today_courses = today_data.get('courses', [])
            yesterday_courses = yesterday_data.get('courses', [])
            
            if not isinstance(today_courses, list) or not isinstance(yesterday_courses, list):
                logger.error("Courses must be lists")
                return False
            
            # Validate individual courses
            for i, course in enumerate(today_courses):
                if not self._validate_course_for_comparison(course, f"today[{i}]"):
                    return False
            
            for i, course in enumerate(yesterday_courses):
                if not self._validate_course_for_comparison(course, f"yesterday[{i}]"):
                    return False
            
            logger.debug("Comparison data validation passed")
            return True
            
        except Exception as e:
            logger.error(f"Error validating comparison data: {e}")
            return False
    
    def _validate_course_for_comparison(self, course: Dict, context: str) -> bool:
        """
        Validate individual course data for comparison operations.
        
        Args:
            course: Course data to validate
            context: Context string for error reporting
            
        Returns:
            True if course is valid for comparison, False otherwise
        """
        try:
            if not isinstance(course, dict):
                logger.error(f"Course {context} is not a dictionary")
                return False
            
            # Check required fields for comparison
            required_fields = ['code', 'name', 'total_classes', 'attended_classes', 'attendance_percentage']
            for field in required_fields:
                if field not in course:
                    logger.error(f"Course {context} missing required field: {field}")
                    return False
            
            # Validate field types and ranges
            code = course.get('code')
            if not isinstance(code, str) or not code.strip():
                logger.error(f"Course {context} has invalid code: {code}")
                return False
            
            name = course.get('name')
            if not isinstance(name, str) or not name.strip():
                logger.error(f"Course {context} has invalid name: {name}")
                return False
            
            # Validate numeric fields
            numeric_fields = ['total_classes', 'attended_classes', 'attendance_percentage']
            for field in numeric_fields:
                value = course.get(field)
                if not isinstance(value, (int, float)) or value < 0:
                    logger.error(f"Course {context} has invalid {field}: {value}")
                    return False
            
            # Validate logical consistency
            total_classes = course.get('total_classes')
            attended_classes = course.get('attended_classes')
            
            if attended_classes > total_classes:
                logger.error(f"Course {context} has attended_classes ({attended_classes}) > total_classes ({total_classes})")
                return False
            
            return True
            
        except Exception as e:
            logger.error(f"Error validating course {context}: {e}")
            return False
    
    def _extract_and_validate_courses(self, data: Dict, data_label: str) -> Dict[str, Dict]:
        """
        Extract and validate courses from attendance data.
        
        Args:
            data: Attendance data
            data_label: Label for logging ('today' or 'yesterday')
            
        Returns:
            Dictionary of validated courses by code
            
        Raises:
            CourseValidationError: When course validation fails
        """
        try:
            courses_list = data.get('courses', [])
            courses_dict = {}
            invalid_courses = []
            
            for i, course in enumerate(courses_list):
                try:
                    if self._validate_course_for_comparison(course, f"{data_label}[{i}]"):
                        course_code = course['code']
                        
                        # Check for duplicate course codes
                        if course_code in courses_dict:
                            logger.warning(f"Duplicate course code {course_code} in {data_label} data")
                            # Keep the first occurrence
                            continue
                        
                        courses_dict[course_code] = course
                    else:
                        invalid_courses.append(f"{data_label}[{i}]: {course.get('code', 'unknown')}")
                        
                except Exception as course_error:
                    logger.error(f"Error processing course {i} in {data_label} data: {course_error}")
                    invalid_courses.append(f"{data_label}[{i}]: processing error")
            
            if invalid_courses:
                logger.warning(f"Found {len(invalid_courses)} invalid courses in {data_label} data: {invalid_courses}")
            
            logger.debug(f"Extracted {len(courses_dict)} valid courses from {data_label} data")
            return courses_dict
            
        except Exception as e:
            logger.error(f"Error extracting courses from {data_label} data: {e}")
            raise CourseValidationError(f"Failed to extract courses from {data_label} data: {e}")
    
    def detect_attendance_changes(self, today_courses: Dict, yesterday_courses: Dict) -> List[AttendanceChange]:
        """
        Detect changes in attendance for existing courses with comprehensive validation.
        
        Args:
            today_courses: Dictionary of today's courses by code
            yesterday_courses: Dictionary of yesterday's courses by code
            
        Returns:
            List of AttendanceChange objects
            
        Raises:
            AttendanceComparatorError: When attendance change detection fails
        """
        changes = []
        failed_courses = []
        
        try:
            logger.debug(f"Detecting attendance changes for {len(today_courses)} today courses vs {len(yesterday_courses)} yesterday courses")
            
            for course_code, today_course in today_courses.items():
                if course_code in yesterday_courses:
                    try:
                        yesterday_course = yesterday_courses[course_code]
                        
                        # Validate course data before processing
                        if not self._validate_course_pair_for_comparison(today_course, yesterday_course, course_code):
                            failed_courses.append(course_code)
                            continue
                        
                        # Extract attendance metrics with validation
                        metrics = self._extract_attendance_metrics(today_course, yesterday_course, course_code)
                        if not metrics:
                            failed_courses.append(course_code)
                            continue
                        
                        # Check if any significant changes occurred
                        if self._has_significant_changes(metrics):
                            # Create attendance change with error handling
                            change = self._create_attendance_change(course_code, today_course, metrics)
                            
                            if change and change.validate():
                                changes.append(change)
                                logger.debug(f"Detected attendance change for course {course_code}")
                            else:
                                logger.warning(f"Failed to create valid attendance change for course {course_code}")
                                failed_courses.append(course_code)
                        
                    except Exception as course_error:
                        logger.error(f"Error processing attendance change for course {course_code}: {course_error}")
                        failed_courses.append(course_code)
            
            if failed_courses:
                logger.warning(f"Failed to process attendance changes for {len(failed_courses)} courses: {failed_courses}")
            
            logger.info(f"Successfully detected {len(changes)} attendance changes")
            return changes
            
        except Exception as e:
            logger.error(f"Error detecting attendance changes: {e}")
            raise AttendanceComparatorError(f"Failed to detect attendance changes: {e}")
    
    def _validate_course_pair_for_comparison(self, today_course: Dict, yesterday_course: Dict, course_code: str) -> bool:
        """
        Validate a pair of courses for comparison.
        
        Args:
            today_course: Today's course data
            yesterday_course: Yesterday's course data
            course_code: Course code for error reporting
            
        Returns:
            True if courses are valid for comparison, False otherwise
        """
        try:
            # Validate both courses individually
            if not self._validate_course_for_comparison(today_course, f"today-{course_code}"):
                return False
            
            if not self._validate_course_for_comparison(yesterday_course, f"yesterday-{course_code}"):
                return False
            
            # Validate course consistency
            if today_course.get('code') != yesterday_course.get('code'):
                logger.error(f"Course code mismatch for {course_code}: "
                           f"today={today_course.get('code')}, yesterday={yesterday_course.get('code')}")
                return False
            
            # Check for reasonable data consistency
            today_total = today_course.get('total_classes', 0)
            yesterday_total = yesterday_course.get('total_classes', 0)
            
            # Total classes should not decrease significantly (allow for minor corrections)
            if today_total < yesterday_total - 1:
                logger.warning(f"Course {course_code} total classes decreased significantly: "
                             f"{yesterday_total} -> {today_total}")
            
            return True
            
        except Exception as e:
            logger.error(f"Error validating course pair for {course_code}: {e}")
            return False
    
    def _extract_attendance_metrics(self, today_course: Dict, yesterday_course: Dict, course_code: str) -> Optional[Dict]:
        """
        Extract and validate attendance metrics from course pair.
        
        Args:
            today_course: Today's course data
            yesterday_course: Yesterday's course data
            course_code: Course code for error reporting
            
        Returns:
            Dictionary of attendance metrics or None if extraction fails
        """
        try:
            # Extract metrics with default values
            old_attended_classes = yesterday_course.get('attended_classes', 0)
            new_attended_classes = today_course.get('attended_classes', 0)
            old_total_classes = yesterday_course.get('total_classes', 0)
            new_total_classes = today_course.get('total_classes', 0)
            old_percentage = yesterday_course.get('attendance_percentage', 0.0)
            new_percentage = today_course.get('attendance_percentage', 0.0)
            old_absences = yesterday_course.get('absences', 0)
            new_absences = today_course.get('absences', 0)
            
            # Validate extracted values
            numeric_values = [
                ('old_attended_classes', old_attended_classes),
                ('new_attended_classes', new_attended_classes),
                ('old_total_classes', old_total_classes),
                ('new_total_classes', new_total_classes),
                ('old_percentage', old_percentage),
                ('new_percentage', new_percentage),
                ('old_absences', old_absences),
                ('new_absences', new_absences)
            ]
            
            for name, value in numeric_values:
                if not isinstance(value, (int, float)) or value < 0:
                    logger.error(f"Invalid {name} for course {course_code}: {value}")
                    return None
            
            # Calculate differences
            classes_difference = new_attended_classes - old_attended_classes
            total_classes_difference = new_total_classes - old_total_classes
            percentage_difference = new_percentage - old_percentage
            absences_difference = new_absences - old_absences
            
            return {
                'old_attended_classes': old_attended_classes,
                'new_attended_classes': new_attended_classes,
                'old_total_classes': old_total_classes,
                'new_total_classes': new_total_classes,
                'old_percentage': old_percentage,
                'new_percentage': new_percentage,
                'old_absences': old_absences,
                'new_absences': new_absences,
                'classes_difference': classes_difference,
                'total_classes_difference': total_classes_difference,
                'percentage_difference': round(percentage_difference, 2),
                'absences_difference': absences_difference
            }
            
        except Exception as e:
            logger.error(f"Error extracting attendance metrics for course {course_code}: {e}")
            return None
    
    def _has_significant_changes(self, metrics: Dict) -> bool:
        """
        Determine if the attendance metrics represent significant changes.
        
        Args:
            metrics: Dictionary of attendance metrics
            
        Returns:
            True if changes are significant, False otherwise
        """
        try:
            # Check for any non-zero changes
            classes_difference = metrics.get('classes_difference', 0)
            total_classes_difference = metrics.get('total_classes_difference', 0)
            percentage_difference = metrics.get('percentage_difference', 0.0)
            absences_difference = metrics.get('absences_difference', 0)
            
            # Consider changes significant if any metric changed
            return (classes_difference != 0 or 
                    total_classes_difference != 0 or 
                    abs(percentage_difference) > 0.01 or  # More than 0.01% change
                    absences_difference != 0)
            
        except Exception as e:
            logger.error(f"Error checking for significant changes: {e}")
            return False
    
    def _create_attendance_change(self, course_code: str, today_course: Dict, metrics: Dict) -> Optional[AttendanceChange]:
        """
        Create an AttendanceChange object with error handling.
        
        Args:
            course_code: Course code
            today_course: Today's course data
            metrics: Attendance metrics dictionary
            
        Returns:
            AttendanceChange object or None if creation fails
        """
        try:
            # Load timetable data for scheduling check with error handling
            try:
                timetable_data = self.load_timetable_data()
                is_scheduled = self.categorize_course_by_schedule(course_code, timetable_data)
            except Exception as timetable_error:
                logger.warning(f"Error loading timetable data for course {course_code}: {timetable_error}")
                is_scheduled = self.get_fallback_categorization(course_code)
            
            change = AttendanceChange(
                course_code=course_code,
                course_name=today_course.get('name', ''),
                instructor=today_course.get('instructor', ''),
                change_type='attendance',
                is_scheduled=is_scheduled,
                timestamp=datetime.now().isoformat(),
                old_attended_classes=metrics['old_attended_classes'],
                new_attended_classes=metrics['new_attended_classes'],
                old_total_classes=metrics['old_total_classes'],
                new_total_classes=metrics['new_total_classes'],
                old_percentage=metrics['old_percentage'],
                new_percentage=metrics['new_percentage'],
                old_absences=metrics['old_absences'],
                new_absences=metrics['new_absences'],
                classes_difference=metrics['classes_difference'],
                total_classes_difference=metrics['total_classes_difference'],
                percentage_difference=metrics['percentage_difference'],
                absences_difference=metrics['absences_difference']
            )
            
            return change
            
        except Exception as e:
            logger.error(f"Error creating attendance change for course {course_code}: {e}")
            return None
    
    def detect_new_courses(self, today_courses: Dict, yesterday_courses: Dict) -> List[NewCourseChange]:
        """
        Detect courses that are new in today's data with comprehensive validation.
        
        Args:
            today_courses: Dictionary of today's courses by code
            yesterday_courses: Dictionary of yesterday's courses by code
            
        Returns:
            List of NewCourseChange objects
            
        Raises:
            AttendanceComparatorError: When new course detection fails
        """
        changes = []
        failed_courses = []
        
        try:
            logger.debug(f"Detecting new courses: {len(today_courses)} today vs {len(yesterday_courses)} yesterday")
            
            for course_code, today_course in today_courses.items():
                if course_code not in yesterday_courses:
                    try:
                        # This is a new course - validate it
                        if not self._validate_course_for_comparison(today_course, f"new-{course_code}"):
                            failed_courses.append(course_code)
                            continue
                        
                        # Create new course change with error handling
                        change = self._create_new_course_change(course_code, today_course)
                        
                        if change and change.validate():
                            changes.append(change)
                            logger.debug(f"Detected new course: {course_code}")
                        else:
                            logger.warning(f"Failed to create valid new course change for {course_code}")
                            failed_courses.append(course_code)
                            
                    except Exception as course_error:
                        logger.error(f"Error processing new course {course_code}: {course_error}")
                        failed_courses.append(course_code)
            
            if failed_courses:
                logger.warning(f"Failed to process {len(failed_courses)} new courses: {failed_courses}")
            
            logger.info(f"Successfully detected {len(changes)} new courses")
            return changes
            
        except Exception as e:
            logger.error(f"Error detecting new courses: {e}")
            raise AttendanceComparatorError(f"Failed to detect new courses: {e}")
    
    def _create_new_course_change(self, course_code: str, today_course: Dict) -> Optional[NewCourseChange]:
        """
        Create a NewCourseChange object with error handling.
        
        Args:
            course_code: Course code
            today_course: Today's course data
            
        Returns:
            NewCourseChange object or None if creation fails
        """
        try:
            # Load timetable data for scheduling check with error handling
            try:
                timetable_data = self.load_timetable_data()
                is_scheduled = self.categorize_course_by_schedule(course_code, timetable_data)
            except Exception as timetable_error:
                logger.warning(f"Error loading timetable data for new course {course_code}: {timetable_error}")
                is_scheduled = self.get_fallback_categorization(course_code)
            
            # Extract and validate course data
            total_classes = today_course.get('total_classes', 0)
            attended_classes = today_course.get('attended_classes', 0)
            attendance_percentage = today_course.get('attendance_percentage', 0.0)
            absences = today_course.get('absences', 0)
            status = today_course.get('status', 'unknown')
            
            # Validate extracted values
            if not isinstance(total_classes, (int, float)) or total_classes < 0:
                logger.error(f"Invalid total_classes for new course {course_code}: {total_classes}")
                return None
            
            if not isinstance(attended_classes, (int, float)) or attended_classes < 0:
                logger.error(f"Invalid attended_classes for new course {course_code}: {attended_classes}")
                return None
            
            if not isinstance(attendance_percentage, (int, float)) or attendance_percentage < 0:
                logger.error(f"Invalid attendance_percentage for new course {course_code}: {attendance_percentage}")
                return None
            
            change = NewCourseChange(
                course_code=course_code,
                course_name=today_course.get('name', ''),
                instructor=today_course.get('instructor', ''),
                change_type='new_course',
                is_scheduled=is_scheduled,
                timestamp=datetime.now().isoformat(),
                total_classes=total_classes,
                attended_classes=attended_classes,
                attendance_percentage=attendance_percentage,
                absences=absences,
                status=status
            )
            
            return change
            
        except Exception as e:
            logger.error(f"Error creating new course change for {course_code}: {e}")
            return None
    
    def detect_removed_courses(self, today_courses: Dict, yesterday_courses: Dict) -> List[RemovedCourseChange]:
        """
        Detect courses that were removed from today's data with comprehensive validation.
        
        Args:
            today_courses: Dictionary of today's courses by code
            yesterday_courses: Dictionary of yesterday's courses by code
            
        Returns:
            List of RemovedCourseChange objects
            
        Raises:
            AttendanceComparatorError: When removed course detection fails
        """
        changes = []
        failed_courses = []
        
        try:
            logger.debug(f"Detecting removed courses: {len(yesterday_courses)} yesterday vs {len(today_courses)} today")
            
            for course_code, yesterday_course in yesterday_courses.items():
                if course_code not in today_courses:
                    try:
                        # This course was removed - validate it
                        if not self._validate_course_for_comparison(yesterday_course, f"removed-{course_code}"):
                            failed_courses.append(course_code)
                            continue
                        
                        # Create removed course change with error handling
                        change = self._create_removed_course_change(course_code, yesterday_course)
                        
                        if change and change.validate():
                            changes.append(change)
                            logger.debug(f"Detected removed course: {course_code}")
                        else:
                            logger.warning(f"Failed to create valid removed course change for {course_code}")
                            failed_courses.append(course_code)
                            
                    except Exception as course_error:
                        logger.error(f"Error processing removed course {course_code}: {course_error}")
                        failed_courses.append(course_code)
            
            if failed_courses:
                logger.warning(f"Failed to process {len(failed_courses)} removed courses: {failed_courses}")
            
            logger.info(f"Successfully detected {len(changes)} removed courses")
            return changes
            
        except Exception as e:
            logger.error(f"Error detecting removed courses: {e}")
            raise AttendanceComparatorError(f"Failed to detect removed courses: {e}")
    
    def _create_removed_course_change(self, course_code: str, yesterday_course: Dict) -> Optional[RemovedCourseChange]:
        """
        Create a RemovedCourseChange object with error handling.
        
        Args:
            course_code: Course code
            yesterday_course: Yesterday's course data
            
        Returns:
            RemovedCourseChange object or None if creation fails
        """
        try:
            # Load timetable data for scheduling check with error handling
            try:
                timetable_data = self.load_timetable_data()
                is_scheduled = self.categorize_course_by_schedule(course_code, timetable_data)
            except Exception as timetable_error:
                logger.warning(f"Error loading timetable data for removed course {course_code}: {timetable_error}")
                is_scheduled = self.get_fallback_categorization(course_code)
            
            # Extract and validate course data
            last_total_classes = yesterday_course.get('total_classes', 0)
            last_attended_classes = yesterday_course.get('attended_classes', 0)
            last_attendance_percentage = yesterday_course.get('attendance_percentage', 0.0)
            last_absences = yesterday_course.get('absences', 0)
            
            # Validate extracted values
            if not isinstance(last_total_classes, (int, float)) or last_total_classes < 0:
                logger.error(f"Invalid last_total_classes for removed course {course_code}: {last_total_classes}")
                return None
            
            if not isinstance(last_attended_classes, (int, float)) or last_attended_classes < 0:
                logger.error(f"Invalid last_attended_classes for removed course {course_code}: {last_attended_classes}")
                return None
            
            if not isinstance(last_attendance_percentage, (int, float)) or last_attendance_percentage < 0:
                logger.error(f"Invalid last_attendance_percentage for removed course {course_code}: {last_attendance_percentage}")
                return None
            
            change = RemovedCourseChange(
                course_code=course_code,
                course_name=yesterday_course.get('name', ''),
                instructor=yesterday_course.get('instructor', ''),
                change_type='removed_course',
                is_scheduled=is_scheduled,
                timestamp=datetime.now().isoformat(),
                last_total_classes=last_total_classes,
                last_attended_classes=last_attended_classes,
                last_attendance_percentage=last_attendance_percentage,
                last_absences=last_absences
            )
            
            return change
            
        except Exception as e:
            logger.error(f"Error creating removed course change for {course_code}: {e}")
            return None
    
    def categorize_course_by_schedule(self, course_code: str, timetable_data: Optional[Dict] = None, 
                                    current_day: Optional[str] = None) -> bool:
        """
        Determine if a course is scheduled for the current day with comprehensive error handling.
        
        Args:
            course_code: The course code to check
            timetable_data: Optional timetable data for checking schedule
            current_day: Optional current day (defaults to today)
            
        Returns:
            True if course is scheduled for the current day, False if unscheduled
        """
        try:
            logger.debug(f"Categorizing course {course_code} by schedule")
            
            # Validate course code
            if not course_code or not isinstance(course_code, str):
                logger.error(f"Invalid course code for categorization: {course_code}")
                return self.get_fallback_categorization(course_code)
            
            # Get current day if not provided with error handling
            if not current_day:
                try:
                    current_day = self._get_current_day_name()
                except Exception as day_error:
                    logger.error(f"Error getting current day name: {day_error}")
                    current_day = 'Monday'  # Default fallback
            
            # Validate current day
            valid_days = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']
            if current_day not in valid_days:
                logger.warning(f"Invalid current day: {current_day}, using Monday as fallback")
                current_day = 'Monday'
            
            # If it's a weekend day, most courses are unscheduled
            try:
                if self.is_weekend_day(current_day):
                    logger.debug(f"Weekend day ({current_day}) detected for course {course_code}")
                    # Weekend classes are typically unscheduled unless specifically found in timetable
                    if timetable_data and self._validate_timetable_data(timetable_data):
                        return self._is_course_scheduled_today(course_code, timetable_data, current_day)
                    else:
                        return False
            except Exception as weekend_error:
                logger.warning(f"Error checking weekend day: {weekend_error}")
            
            # Load timetable data if not provided
            if not timetable_data:
                try:
                    timetable_data = self.load_timetable_data()
                except TimetableError as timetable_error:
                    logger.error(f"Critical timetable error for course {course_code}: {timetable_error}")
                    return self.get_fallback_categorization(course_code)
                except Exception as load_error:
                    logger.warning(f"Error loading timetable data for course {course_code}: {load_error}")
                    timetable_data = None
                
                if not timetable_data:
                    logger.debug(f"No timetable data available for course {course_code}, using fallback")
                    return self.get_fallback_categorization(course_code)
            
            # Validate timetable data before using
            if not self._validate_timetable_data(timetable_data):
                logger.error(f"Invalid timetable data for course {course_code}, using fallback")
                return self.get_fallback_categorization(course_code)
            
            # Check if course is scheduled for the current day using timetable data
            try:
                is_scheduled = self._is_course_scheduled_today(course_code, timetable_data, current_day)
                
                # If not found in timetable for today, check if course exists in timetable for other days
                if not is_scheduled:
                    try:
                        schedule_info = self.get_course_schedule_info(course_code)
                        if schedule_info.get('is_scheduled', False):
                            # Course exists in timetable but not for today - unscheduled for today
                            logger.debug(f"Course {course_code} exists in timetable but not scheduled for {current_day}")
                            return False
                        else:
                            # Course not in timetable at all - use fallback
                            logger.debug(f"Course {course_code} not found in timetable, using fallback")
                            return self.get_fallback_categorization(course_code)
                    except Exception as schedule_info_error:
                        logger.warning(f"Error getting schedule info for course {course_code}: {schedule_info_error}")
                        return self.get_fallback_categorization(course_code)
                
                logger.debug(f"Course {course_code} categorized as {'scheduled' if is_scheduled else 'unscheduled'} for {current_day}")
                return is_scheduled
                
            except Exception as schedule_check_error:
                logger.error(f"Error checking schedule for course {course_code}: {schedule_check_error}")
                return self.get_fallback_categorization(course_code)
            
        except Exception as e:
            logger.error(f"Unexpected error categorizing course {course_code}: {e}")
            return self.get_fallback_categorization(course_code)
    
    def _get_current_day_name(self) -> str:
        """
        Get the current day name in the format used by timetable data.
        
        Returns:
            Current day name (e.g., 'Monday', 'Tuesday', etc.)
        """
        try:
            from datetime import datetime
            
            # Get current day of week (0=Monday, 6=Sunday)
            current_weekday = datetime.now().weekday()
            
            # Map to day names used in timetable
            day_names = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']
            
            return day_names[current_weekday]
            
        except Exception as e:
            logger.warning(f"Error getting current day name: {e}")
            return 'Monday'  # Default fallback
    
    def _is_course_scheduled_today(self, course_code: str, timetable_data: Dict, current_day: str) -> bool:
        """
        Check if a course is scheduled for the specified day.
        
        Args:
            course_code: The attendance system course code
            timetable_data: The timetable data structure
            current_day: The day to check (e.g., 'Monday')
            
        Returns:
            True if course is scheduled for the day, False otherwise
        """
        try:
            # Map attendance code to timetable code
            timetable_code = self.map_course_codes(course_code)
            
            if not timetable_code:
                # If no mapping found, consider it unscheduled
                return False
            
            # Get schedule for the current day
            schedule = timetable_data.get('schedule', {})
            day_schedule = schedule.get(current_day, {})
            
            # Check if any time slot has this course
            for time_slot, class_info in day_schedule.items():
                if class_info and class_info.get('code') == timetable_code:
                    return True
            
            return False
            
        except Exception as e:
            logger.warning(f"Error checking if course {course_code} is scheduled for {current_day}: {e}")
            return False
    
    def map_course_codes(self, course_code: str) -> Optional[str]:
        """
        Map attendance course codes to timetable course codes.
        
        Args:
            course_code: The attendance system course code
            
        Returns:
            Corresponding timetable course code or None if not found
        """
        try:
            return self.course_code_mapping.get(course_code)
            
        except Exception as e:
            logger.warning(f"Error mapping course code {course_code}: {e}")
            return None
    
    def get_course_mapping(self) -> Dict[str, str]:
        """
        Get the complete course code mapping.
        
        Returns:
            Dictionary of attendance codes to timetable codes
        """
        return self.course_code_mapping.copy()
    
    def add_course_mapping(self, attendance_code: str, timetable_code: str) -> None:
        """
        Add a new course code mapping.
        
        Args:
            attendance_code: The attendance system course code
            timetable_code: The timetable system course code
        """
        try:
            self.course_code_mapping[attendance_code] = timetable_code
            logger.debug(f"Added course mapping: {attendance_code} -> {timetable_code}")
            
        except Exception as e:
            logger.warning(f"Error adding course mapping {attendance_code} -> {timetable_code}: {e}")
    
    def load_timetable_data(self) -> Optional[Dict]:
        """
        Load timetable data for the current account with comprehensive error handling.
        
        Returns:
            Timetable data dictionary or None if not available
            
        Raises:
            TimetableError: When timetable loading fails critically
        """
        try:
            if not self.data_manager or not self.account_id:
                logger.debug("No data manager or account ID available for timetable loading")
                return None
            
            # Load from cache if available and valid
            if self._timetable_data is not None:
                if self._validate_timetable_data(self._timetable_data):
                    logger.debug("Using cached timetable data")
                    return self._timetable_data
                else:
                    logger.warning("Cached timetable data is invalid, reloading")
                    self._timetable_data = None
            
            # Load from data manager with error handling
            try:
                timetable_data = self.data_manager.load_account_data(
                    account_id=self.account_id,
                    data_type='timetable'
                )
                
                if timetable_data:
                    # Validate loaded data
                    if self._validate_timetable_data(timetable_data):
                        self._timetable_data = timetable_data
                        logger.debug(f"Successfully loaded timetable data for account {self.account_id}")
                        return self._timetable_data
                    else:
                        logger.error(f"Loaded timetable data is invalid for account {self.account_id}")
                        return None
                else:
                    logger.info(f"No timetable data found for account {self.account_id}")
                    return None
                    
            except Exception as load_error:
                logger.error(f"Error loading timetable data for account {self.account_id}: {load_error}")
                raise TimetableError(f"Failed to load timetable data: {load_error}")
            
        except TimetableError:
            # Re-raise timetable errors
            raise
        except Exception as e:
            logger.error(f"Unexpected error loading timetable data: {e}")
            return None  # Don't raise for unexpected errors - return None for graceful degradation
    
    def _validate_timetable_data(self, timetable_data: Dict) -> bool:
        """
        Validate timetable data structure and content.
        
        Args:
            timetable_data: Timetable data to validate
            
        Returns:
            True if timetable data is valid, False otherwise
        """
        try:
            if not isinstance(timetable_data, dict):
                logger.error("Timetable data is not a dictionary")
                return False
            
            # Check for schedule field
            if 'schedule' not in timetable_data:
                logger.error("Timetable data missing 'schedule' field")
                return False
            
            schedule = timetable_data['schedule']
            if not isinstance(schedule, dict):
                logger.error("Timetable schedule is not a dictionary")
                return False
            
            # Validate day schedules
            valid_days = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']
            for day, day_schedule in schedule.items():
                if day not in valid_days:
                    logger.warning(f"Invalid day in timetable: {day}")
                    continue
                
                if not isinstance(day_schedule, dict):
                    logger.error(f"Day schedule for {day} is not a dictionary")
                    return False
                
                # Validate time slots
                for time_slot, class_info in day_schedule.items():
                    if class_info is not None:  # Allow None for empty slots
                        if not isinstance(class_info, dict):
                            logger.error(f"Class info for {day} {time_slot} is not a dictionary")
                            return False
                        
                        # Check for required fields in class info
                        if 'code' not in class_info:
                            logger.warning(f"Class info for {day} {time_slot} missing 'code' field")
            
            logger.debug("Timetable data validation passed")
            return True
            
        except Exception as e:
            logger.error(f"Error validating timetable data: {e}")
            return False
    
    def get_current_day_schedule(self, current_day: Optional[str] = None) -> Dict:
        """
        Get the schedule for the current day.
        
        Args:
            current_day: Optional day name (defaults to today)
            
        Returns:
            Dictionary of time slots and classes for the day
        """
        try:
            timetable_data = self.load_timetable_data()
            
            if not timetable_data:
                return {}
            
            if not current_day:
                current_day = self._get_current_day_name()
            
            schedule = timetable_data.get('schedule', {})
            return schedule.get(current_day, {})
            
        except Exception as e:
            logger.warning(f"Error getting current day schedule: {e}")
            return {}
    
    def get_course_schedule_info(self, course_code: str) -> Dict:
        """
        Get detailed schedule information for a specific course.
        
        Args:
            course_code: The attendance system course code
            
        Returns:
            Dictionary with schedule information including days, times, and frequency
        """
        try:
            timetable_data = self.load_timetable_data()
            
            if not timetable_data:
                return {'scheduled_days': [], 'total_slots': 0, 'is_scheduled': False}
            
            # Map attendance code to timetable code
            timetable_code = self.map_course_codes(course_code)
            
            if not timetable_code:
                return {'scheduled_days': [], 'total_slots': 0, 'is_scheduled': False}
            
            # Find all occurrences of this course in the timetable
            scheduled_days = []
            total_slots = 0
            schedule = timetable_data.get('schedule', {})
            
            for day, day_schedule in schedule.items():
                for time_slot, class_info in day_schedule.items():
                    if class_info and class_info.get('code') == timetable_code:
                        if day not in scheduled_days:
                            scheduled_days.append(day)
                        total_slots += 1
            
            return {
                'scheduled_days': scheduled_days,
                'total_slots': total_slots,
                'is_scheduled': len(scheduled_days) > 0,
                'timetable_code': timetable_code
            }
            
        except Exception as e:
            logger.warning(f"Error getting course schedule info for {course_code}: {e}")
            return {'scheduled_days': [], 'total_slots': 0, 'is_scheduled': False}
    
    def categorize_courses_by_schedule(self, course_codes: List[str], current_day: Optional[str] = None) -> Dict[str, Dict]:
        """
        Categorize multiple courses by their schedule status for the current day.
        
        Args:
            course_codes: List of attendance system course codes
            current_day: Optional day name (defaults to today)
            
        Returns:
            Dictionary mapping course codes to their categorization info
        """
        try:
            if not current_day:
                current_day = self._get_current_day_name()
            
            categorization = {}
            
            for course_code in course_codes:
                schedule_info = self.get_course_schedule_info(course_code)
                is_scheduled_today = self.categorize_course_by_schedule(course_code, self.load_timetable_data(), current_day)
                
                categorization[course_code] = {
                    'is_scheduled_today': is_scheduled_today,
                    'schedule_info': schedule_info,
                    'category': 'scheduled' if is_scheduled_today else 'unscheduled'
                }
            
            return categorization
            
        except Exception as e:
            logger.warning(f"Error categorizing courses by schedule: {e}")
            return {}
    
    def is_weekend_day(self, day_name: Optional[str] = None) -> bool:
        """
        Check if the given day is a weekend day.
        
        Args:
            day_name: Optional day name (defaults to today)
            
        Returns:
            True if it's a weekend day, False otherwise
        """
        try:
            if not day_name:
                day_name = self._get_current_day_name()
            
            weekend_days = ['Saturday', 'Sunday']
            return day_name in weekend_days
            
        except Exception as e:
            logger.warning(f"Error checking weekend day: {e}")
            return False
    
    def get_fallback_categorization(self, course_code: str) -> bool:
        """
        Provide fallback categorization when timetable data is not available with comprehensive validation.
        
        Args:
            course_code: The attendance system course code
            
        Returns:
            True if likely scheduled, False if likely unscheduled
        """
        try:
            logger.debug(f"Using fallback categorization for course {course_code}")
            
            # Validate course code
            if not course_code or not isinstance(course_code, str):
                logger.error(f"Invalid course code for fallback categorization: {course_code}")
                return False
            
            # Use course code mapping as primary fallback
            try:
                timetable_code = self.map_course_codes(course_code)
                
                if timetable_code:
                    # If we have a mapping, it's likely a regular course
                    logger.debug(f"Course {course_code} has timetable mapping: {timetable_code}")
                    return True
            except Exception as mapping_error:
                logger.warning(f"Error mapping course code {course_code}: {mapping_error}")
            
            # Additional heuristics for fallback categorization
            try:
                if self._is_standard_course_pattern(course_code):
                    logger.debug(f"Course {course_code} follows standard pattern")
                    return True
            except Exception as pattern_error:
                logger.warning(f"Error checking course pattern for {course_code}: {pattern_error}")
            
            # Check for common course prefixes that indicate regular courses
            try:
                if self._has_standard_course_prefix(course_code):
                    logger.debug(f"Course {course_code} has standard prefix")
                    return True
            except Exception as prefix_error:
                logger.warning(f"Error checking course prefix for {course_code}: {prefix_error}")
            
            # Default to unscheduled if no clear indication
            logger.debug(f"Course {course_code} categorized as unscheduled by fallback")
            return False
            
        except Exception as e:
            logger.error(f"Error in fallback categorization for {course_code}: {e}")
            return False
    
    def _has_standard_course_prefix(self, course_code: str) -> bool:
        """
        Check if course code has standard academic prefixes.
        
        Args:
            course_code: Course code to check
            
        Returns:
            True if has standard prefix, False otherwise
        """
        try:
            # Common academic course prefixes
            standard_prefixes = [
                'CSC', 'CSL',  # Computer Science
                'CEN', 'CEL',  # Computer Engineering
                'AIC', 'AIL',  # Artificial Intelligence
                'GSC',         # General Science
                'ENG',         # English
                'HSS',         # Humanities and Social Sciences
                'ISL',         # Islamic Studies
                'MTH', 'MAT',  # Mathematics
                'PHY',         # Physics
                'CHE',         # Chemistry
                'BIO',         # Biology
                'ECO',         # Economics
                'PSY',         # Psychology
                'SOC',         # Sociology
                'HIS',         # History
                'POL',         # Political Science
            ]
            
            course_upper = course_code.upper().strip()
            
            for prefix in standard_prefixes:
                if course_upper.startswith(prefix):
                    return True
            
            return False
            
        except Exception as e:
            logger.error(f"Error checking standard course prefix for {course_code}: {e}")
            return False
    
    def _is_standard_course_pattern(self, course_code: str) -> bool:
        """
        Check if course code follows standard academic course patterns with comprehensive validation.
        
        Args:
            course_code: The course code to check
            
        Returns:
            True if it follows standard patterns, False otherwise
        """
        try:
            if not course_code or not isinstance(course_code, str):
                return False
            
            course_code = course_code.strip()
            if not course_code:
                return False
            
            # Standard patterns: 3 letters + space + 3 digits (e.g., "CSC 221")
            standard_pattern = r'^[A-Z]{3}\s\d{3}$'
            
            if re.match(standard_pattern, course_code.upper()):
                return True
            
            # Additional patterns for lab courses: 3 letters + L + space + 3 digits (e.g., "CSL 221")
            lab_pattern = r'^[A-Z]{3}L\s\d{3}$'
            
            if re.match(lab_pattern, course_code.upper()):
                return True
            
            # Pattern for 4-letter prefixes: 4 letters + space + 3 digits (e.g., "MATH 101")
            four_letter_pattern = r'^[A-Z]{4}\s\d{3}$'
            
            if re.match(four_letter_pattern, course_code.upper()):
                return True
            
            # Pattern without spaces: 3 letters + 3 digits (e.g., "CSC221")
            no_space_pattern = r'^[A-Z]{3}\d{3}$'
            
            if re.match(no_space_pattern, course_code.upper()):
                return True
            
            # Pattern for lab courses without spaces: 3 letters + L + 3 digits (e.g., "CSL221")
            lab_no_space_pattern = r'^[A-Z]{3}L\d{3}$'
            
            if re.match(lab_no_space_pattern, course_code.upper()):
                return True
            
            return False
            
        except Exception as e:
            logger.error(f"Error checking standard course pattern for {course_code}: {e}")
            return False