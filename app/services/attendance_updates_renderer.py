import logging
logger = logging.getLogger(__name__)

# app/services/attendance_updates_renderer.py
from typing import List, Dict, Any, Optional
from app.models.attendance_changes import AttendanceChangeBase, AttendanceChange, NewCourseChange, RemovedCourseChange


class AttendanceUpdatesRenderer:
    """Service for rendering attendance changes for display in the UI."""
    
    def __init__(self):
        """Initialize the renderer."""
        # Course code to friendly name mapping
        self.course_friendly_names = {
            'CSC 221': 'DSA',
            'CSL 221': 'DSA Lab',
            'CEN 323': 'Computer Architecture',
            'CEL 323': 'Computer Architecture Lab',
            'AIC 202': 'AI Programming',
            'AIL 202': 'AI Programming Lab',
            'GSC 121': 'Linear Algebra',
            'ENG 134': 'Communication Skills',
            'HSS 219': 'Civic Engagement',
            'ISL 109': 'Quran Studies',
        }
    
    def render_updates_section(self, changes: List[AttendanceChangeBase], scheduled_courses: List[str] = None, is_weekend: bool = False, is_first_time: bool = False) -> Dict[str, Any]:
        """
        Render the complete updates section with all changes.
        
        Args:
            changes: List of attendance changes to render
            scheduled_courses: List of scheduled course codes for today
            is_weekend: Whether today is a weekend
            is_first_time: Whether this is a first-time user
            
        Returns:
            Dictionary containing rendered updates section data
        """
        # Always show the section, but determine if it should be collapsed
        # Count any attendance change including total_classes_difference
        has_attendance_changes = any(
            hasattr(change, 'change_type') and change.change_type == 'attendance' and 
            (change.classes_difference != 0 or change.absences_difference != 0 or change.total_classes_difference != 0)
            for change in changes
        )
        
        # Render attendance changes
        rendered_changes = []
        updated_course_codes = set()
        
        if changes:
            for change in changes:
                # Handle all change types
                if hasattr(change, 'change_type'):
                    if change.change_type == 'attendance':
                        rendered_change = self._render_simple_attendance_change(change)
                        if rendered_change:
                            rendered_changes.append(rendered_change)
                            updated_course_codes.add(change.course_code)
                    elif change.change_type == 'new_course':
                        # For new courses, we don't show them in the simple view
                        # Just mark them as updated so they don't show "not yet updated"
                        updated_course_codes.add(change.course_code)
        
        # Add messages for scheduled courses with no updates
        if scheduled_courses:
            for course_code in scheduled_courses:
                if course_code not in updated_course_codes:
                    no_update_message = self._render_no_update_message(course_code)
                    if no_update_message:
                        rendered_changes.append(no_update_message)
        
        # Add system messages
        system_messages = self._generate_system_messages(is_weekend, is_first_time, len(changes))
        rendered_changes.extend(system_messages)
        
        return {
            'always_show': True,
            'has_changes': len(rendered_changes) > 0,
            'has_attendance_changes': has_attendance_changes,
            'should_collapse': not has_attendance_changes,  # Collapse if no present/absent changes
            'total_changes': len([c for c in rendered_changes if c.get('type') in ['present', 'absent', 'update']]),
            'changes': rendered_changes,
            'section_class': 'attendance-updates-always-visible'
        }
    
    def _render_simple_attendance_change(self, change: AttendanceChange) -> Optional[Dict[str, Any]]:
        """
        Render a simple attendance change message.
        
        Args:
            change: The attendance change to render
            
        Returns:
            Dictionary containing simple rendered change data
        """
        try:
            course_name = change.course_name or self._get_friendly_course_name(change.course_code)
            
            # Determine the type of change and message
            if change.classes_difference > 0:
                # Present marked
                hours = change.classes_difference
                return {
                    'type': 'present',
                    'course_code': change.course_code,
                    'course_name': course_name,
                    'message': f"Present marked for {course_name} (+{hours} hr{'s' if hours != 1 else ''})",
                    'hours': hours,
                    'is_scheduled': change.is_scheduled,
                    'css_class': 'update-present',
                    'icon': '<i class="fas fa-check-circle text-success"></i>'
                }
            elif change.classes_difference < 0:
                # This shouldn't happen in normal flow, but handle it
                hours = abs(change.classes_difference)
                return {
                    'type': 'absent',
                    'course_code': change.course_code,
                    'course_name': course_name,
                    'message': f"Classes removed for {course_name} (-{hours} hr{'s' if hours != 1 else ''})",
                    'hours': hours,
                    'is_scheduled': change.is_scheduled,
                    'css_class': 'update-neutral',
                    'icon': '<i class="fas fa-clipboard-list text-info"></i>'
                }
            elif change.absences_difference > 0:
                # Absent marked
                hours = change.absences_difference
                return {
                    'type': 'absent',
                    'course_code': change.course_code,
                    'course_name': course_name,
                    'message': f"Absent marked for {course_name} ({hours} hr{'s' if hours != 1 else ''})",
                    'hours': hours,
                    'is_scheduled': change.is_scheduled,
                    'css_class': 'update-absent',
                    'icon': '<i class="fas fa-times-circle text-danger"></i>'
                }
            else:
                # Other changes (total classes, etc.) - show as neutral update
                return {
                    'type': 'update',
                    'course_code': change.course_code,
                    'course_name': course_name,
                    'message': f"Attendance updated for {course_name}",
                    'hours': 0,
                    'is_scheduled': change.is_scheduled,
                    'css_class': 'update-neutral',
                    'icon': '<i class="fas fa-clipboard-list text-info"></i>'
                }
                
        except Exception as e:
            logger.warning(f"Error rendering simple attendance change: {e}")
            return None
    
    def _render_no_update_message(self, course_code: str) -> Dict[str, Any]:
        """
        Render a message for courses with no updates.
        
        Args:
            course_code: The course code
            
        Returns:
            Dictionary containing no-update message data
        """
        course_name = self._get_friendly_course_name(course_code)
        return {
            'type': 'no_update',
            'course_code': course_code,
            'course_name': course_name,
            'message': f"Attendance not yet updated for {course_name}",
            'hours': 0,
            'is_scheduled': True,
            'css_class': 'update-no-change',
            'icon': '<i class="fas fa-hourglass-half text-warning"></i>'
        }
    
    def _generate_system_messages(self, is_weekend: bool, is_first_time: bool, changes_count: int) -> List[Dict[str, Any]]:
        """
        Generate system messages for special conditions.
        
        Args:
            is_weekend: Whether today is a weekend
            is_first_time: Whether this is a first-time user
            changes_count: Number of changes detected
            
        Returns:
            List of system message dictionaries
        """
        messages = []
        
        if is_first_time:
            messages.append({
                'type': 'system',
                'message': 'First visit - establishing baseline for future comparisons',
                'css_class': 'update-system',
                'icon': '<i class="fas fa-sync text-primary"></i>'
            })
        elif is_weekend:
            messages.append({
                'type': 'system',
                'message': 'Weekend - no classes scheduled today',
                'css_class': 'update-system',
                'icon': '<i class="fas fa-calendar text-primary"></i>'
            })
        elif changes_count == 0:
            messages.append({
                'type': 'system',
                'message': 'No attendance changes detected today',
                'css_class': 'update-system',
                'icon': '<i class="fas fa-chart-bar text-primary"></i>'
            })
        
        return messages
    
    def _get_friendly_course_name(self, course_code: str) -> str:
        """
        Get friendly course name from course code.
        
        Args:
            course_code: The course code
            
        Returns:
            Friendly course name
        """
        return self.course_friendly_names.get(course_code, course_code)

    def _render_single_change(self, change: AttendanceChangeBase) -> Optional[Dict[str, Any]]:
        """
        Render a single attendance change.
        
        Args:
            change: The attendance change to render
            
        Returns:
            Dictionary containing rendered change data
        """
        try:
            if isinstance(change, AttendanceChange):
                return self.format_attendance_change(change)
            elif isinstance(change, NewCourseChange):
                return self.format_new_course_change(change)
            elif isinstance(change, RemovedCourseChange):
                return self.format_removed_course_change(change)
            else:
                return None
        except Exception as e:
            logger.warning(f"Error rendering change: {e}")
            return None
    
    def format_attendance_change(self, change: AttendanceChange) -> Dict[str, Any]:
        """
        Format an attendance change for display.
        
        Args:
            change: The attendance change to format
            
        Returns:
            Dictionary containing formatted change data
        """
        # Get visual indicators
        indicators = self.get_change_icon_and_color(change)
        
        # Format before/after values
        before_after = change.get_formatted_before_after()
        
        # Generate change details
        change_details = []
        
        # Attended classes change
        if change.classes_difference != 0:
            change_details.append({
                'type': 'attended_classes',
                'label': 'Attended Classes',
                'before_after': before_after['attended_classes'],
                'difference': change.classes_difference,
                'difference_text': f"({'+' if change.classes_difference > 0 else ''}{change.classes_difference})"
            })
        
        # Total classes change
        if change.total_classes_difference != 0:
            change_details.append({
                'type': 'total_classes',
                'label': 'Total Classes',
                'before_after': before_after['total_classes'],
                'difference': change.total_classes_difference,
                'difference_text': f"({'+' if change.total_classes_difference > 0 else ''}{change.total_classes_difference})"
            })
        
        # Percentage change
        if abs(change.percentage_difference) > 0.01:
            change_details.append({
                'type': 'percentage',
                'label': 'Attendance Percentage',
                'before_after': before_after['percentage'],
                'difference': change.percentage_difference,
                'difference_text': f"({'+' if change.percentage_difference > 0 else ''}{change.percentage_difference:.2f}%)"
            })
        
        # Absences change
        if change.absences_difference != 0:
            change_details.append({
                'type': 'absences',
                'label': 'Absences',
                'before_after': before_after['absences'],
                'difference': change.absences_difference,
                'difference_text': f"({'+' if change.absences_difference > 0 else ''}{change.absences_difference})"
            })
        
        return {
            'change_type': 'attendance',
            'course_code': change.course_code,
            'course_name': change.course_name,
            'instructor': change.instructor,
            'is_scheduled': change.is_scheduled,
            'severity': change.get_change_severity(),
            'icon': indicators['icon'],
            'color': indicators['color'],
            'css_class': indicators['css_class'],
            'display_message': change.get_display_message(),
            'change_details': change_details,
            'timestamp': change.timestamp,
            'summary_text': self._generate_change_summary_text(change)
        }
    
    def format_new_course_change(self, change: NewCourseChange) -> Dict[str, Any]:
        """
        Format a new course change for display.
        
        Args:
            change: The new course change to format
            
        Returns:
            Dictionary containing formatted change data
        """
        # Get status-based indicators
        indicators = self._get_new_course_indicators(change)
        
        # Get status information
        status_info = change.get_status_info()
        
        return {
            'change_type': 'new_course',
            'course_code': change.course_code,
            'course_name': change.course_name,
            'instructor': change.instructor,
            'is_scheduled': change.is_scheduled,
            'severity': 'neutral',  # New courses are generally neutral
            'icon': indicators['icon'],
            'color': indicators['color'],
            'css_class': indicators['css_class'],
            'display_message': change.get_display_message(),
            'status': change.status,
            'status_info': status_info,
            'attendance_details': {
                'attended_classes': change.attended_classes,
                'total_classes': change.total_classes,
                'attendance_percentage': change.attendance_percentage,
                'absences': change.absences
            },
            'timestamp': change.timestamp,
            'summary_text': f"New course: {change.attendance_percentage:.1f}% attendance"
        }
    
    def format_removed_course_change(self, change: RemovedCourseChange) -> Dict[str, Any]:
        """
        Format a removed course change for display.
        
        Args:
            change: The removed course change to format
            
        Returns:
            Dictionary containing formatted change data
        """
        # Removed courses get warning indicators
        indicators = {
            'icon': '<i class="fas fa-trash-alt"></i>',
            'color': '#ff9800',  # Orange
            'css_class': 'change-removed'
        }
        
        # Get last status information
        last_status = change.get_last_status_info()
        
        return {
            'change_type': 'removed_course',
            'course_code': change.course_code,
            'course_name': change.course_name,
            'instructor': change.instructor,
            'is_scheduled': change.is_scheduled,
            'severity': 'neutral',
            'icon': indicators['icon'],
            'color': indicators['color'],
            'css_class': indicators['css_class'],
            'display_message': change.get_display_message(),
            'last_status': last_status,
            'last_attendance_details': {
                'attended_classes': change.last_attended_classes,
                'total_classes': change.last_total_classes,
                'attendance_percentage': change.last_attendance_percentage,
                'absences': change.last_absences
            },
            'timestamp': change.timestamp,
            'summary_text': f"Course removed (was {change.last_attendance_percentage:.1f}%)"
        }
    
    def get_change_icon_and_color(self, change: AttendanceChange) -> Dict[str, str]:
        """
        Get appropriate icon and color for an attendance change.
        
        Args:
            change: The attendance change
            
        Returns:
            Dictionary containing icon, color, and CSS class
        """
        severity = change.get_change_severity()
        
        if severity == 'positive':
            return {
                'icon': '<i class="fas fa-arrow-up"></i>',  # Trending up
                'color': '#4caf50',  # Green
                'css_class': 'change-positive'
            }
        elif severity == 'negative':
            return {
                'icon': '<i class="fas fa-arrow-down"></i>',  # Trending down
                'color': '#f44336',  # Red
                'css_class': 'change-negative'
            }
        else:  # neutral
            return {
                'icon': '<i class="fas fa-chart-bar"></i>',  # Bar chart
                'color': '#2196f3',  # Blue
                'css_class': 'change-neutral'
            }
    
    def _get_new_course_indicators(self, change: NewCourseChange) -> Dict[str, str]:
        """
        Get appropriate indicators for a new course based on its status.
        
        Args:
            change: The new course change
            
        Returns:
            Dictionary containing icon, color, and CSS class
        """
        if change.status == 'safe':
            return {
                'icon': '<i class="fas fa-check-circle"></i>',  # Check mark
                'color': '#4caf50',  # Green
                'css_class': 'change-new-safe'
            }
        elif change.status == 'warning':
            return {
                'icon': '<i class="fas fa-exclamation-triangle"></i>',  # Warning
                'color': '#ff9800',  # Orange
                'css_class': 'change-new-warning'
            }
        elif change.status == 'danger':
            return {
                'icon': '<i class="fas fa-bell"></i>',  # Alert
                'color': '#f44336',  # Red
                'css_class': 'change-new-danger'
            }
        else:
            return {
                'icon': '<i class="fas fa-plus-circle"></i>',  # Plus
                'color': '#2196f3',  # Blue
                'css_class': 'change-new-neutral'
            }
    
    def _categorize_changes(self, changes: List[AttendanceChangeBase]) -> Dict[str, List[AttendanceChangeBase]]:
        """
        Categorize changes by type and scheduling status.
        
        Args:
            changes: List of changes to categorize
            
        Returns:
            Dictionary with categorized changes
        """
        categorized = {
            'scheduled': {
                'attendance': [],
                'new_course': [],
                'removed_course': []
            },
            'unscheduled': {
                'attendance': [],
                'new_course': [],
                'removed_course': []
            }
        }
        
        for change in changes:
            schedule_key = 'scheduled' if change.is_scheduled else 'unscheduled'
            categorized[schedule_key][change.change_type].append(change)
        
        return categorized
    
    def _generate_summary(self, changes: List[AttendanceChangeBase], categorized: Dict) -> Dict[str, Any]:
        """
        Generate a summary of all changes.
        
        Args:
            changes: List of all changes
            categorized: Categorized changes
            
        Returns:
            Dictionary containing summary information
        """
        total_changes = len(changes)
        
        # Count by type
        attendance_changes = len([c for c in changes if c.change_type == 'attendance'])
        new_courses = len([c for c in changes if c.change_type == 'new_course'])
        removed_courses = len([c for c in changes if c.change_type == 'removed_course'])
        
        # Count by schedule status
        scheduled_changes = len([c for c in changes if c.is_scheduled])
        unscheduled_changes = len([c for c in changes if not c.is_scheduled])
        
        # Count by severity (for attendance changes only)
        positive_changes = len([c for c in changes if isinstance(c, AttendanceChange) and c.get_change_severity() == 'positive'])
        negative_changes = len([c for c in changes if isinstance(c, AttendanceChange) and c.get_change_severity() == 'negative'])
        
        return {
            'total_changes': total_changes,
            'by_type': {
                'attendance_changes': attendance_changes,
                'new_courses': new_courses,
                'removed_courses': removed_courses
            },
            'by_schedule': {
                'scheduled': scheduled_changes,
                'unscheduled': unscheduled_changes
            },
            'by_severity': {
                'positive': positive_changes,
                'negative': negative_changes,
                'neutral': attendance_changes - positive_changes - negative_changes
            }
        }
    
    def _generate_change_summary_text(self, change: AttendanceChange) -> str:
        """
        Generate a concise summary text for an attendance change.
        
        Args:
            change: The attendance change
            
        Returns:
            Summary text string
        """
        parts = []
        
        if change.classes_difference != 0:
            if change.classes_difference > 0:
                parts.append(f"+{change.classes_difference} attended")
            else:
                parts.append(f"{change.classes_difference} attended")
        
        if abs(change.percentage_difference) > 0.01:
            if change.percentage_difference > 0:
                parts.append(f"+{change.percentage_difference:.1f}%")
            else:
                parts.append(f"{change.percentage_difference:.1f}%")
        
        if change.total_classes_difference != 0:
            parts.append(f"{change.total_classes_difference:+d} total")
        
        return ", ".join(parts) if parts else "Minor update"
    
    def _get_section_css_class(self, changes: List[AttendanceChangeBase]) -> str:
        """
        Get the appropriate CSS class for the entire updates section.
        
        Args:
            changes: List of changes
            
        Returns:
            CSS class string
        """
        if not changes:
            return 'updates-section-empty'
        
        # Check if there are any negative changes
        has_negative = any(
            isinstance(c, AttendanceChange) and c.get_change_severity() == 'negative'
            for c in changes
        )
        
        # Check if there are any positive changes
        has_positive = any(
            isinstance(c, AttendanceChange) and c.get_change_severity() == 'positive'
            for c in changes
        )
        
        if has_negative and not has_positive:
            return 'updates-section-warning'
        elif has_positive and not has_negative:
            return 'updates-section-positive'
        elif has_positive and has_negative:
            return 'updates-section-mixed'
        else:
            return 'updates-section-neutral'
    
    def categorize_changes_by_schedule(self, changes: List[AttendanceChangeBase]) -> Dict[str, List[Dict[str, Any]]]:
        """
        Categorize changes by scheduled vs unscheduled classes with formatted data.
        
        Args:
            changes: List of attendance changes
            
        Returns:
            Dictionary with scheduled and unscheduled changes, each containing formatted change data
        """
        scheduled_changes = []
        unscheduled_changes = []
        
        for change in changes:
            rendered_change = self._render_single_change(change)
            if rendered_change:
                if change.is_scheduled:
                    scheduled_changes.append(rendered_change)
                else:
                    unscheduled_changes.append(rendered_change)
        
        return {
            'scheduled': {
                'changes': scheduled_changes,
                'count': len(scheduled_changes),
                'title': 'Scheduled Classes',
                'description': 'Changes in your regular timetable classes'
            },
            'unscheduled': {
                'changes': unscheduled_changes,
                'count': len(unscheduled_changes),
                'title': 'Unscheduled Classes',
                'description': 'Changes in classes outside your regular timetable'
            }
        }
    
    def format_before_after_values(self, change: AttendanceChange) -> Dict[str, Dict[str, Any]]:
        """
        Format before/after values with detailed percentage calculations and visual indicators.
        
        Args:
            change: The attendance change to format
            
        Returns:
            Dictionary containing formatted before/after values with calculations
        """
        formatted_values = {}
        
        # Attended classes formatting
        if change.classes_difference != 0:
            formatted_values['attended_classes'] = {
                'label': 'Attended Classes',
                'before': change.old_attended_classes,
                'after': change.new_attended_classes,
                'difference': change.classes_difference,
                'difference_text': f"{change.classes_difference:+d}",
                'before_after_text': f"{change.old_attended_classes} → {change.new_attended_classes}",
                'is_improvement': change.classes_difference > 0,
                'severity': 'positive' if change.classes_difference > 0 else 'negative'
            }
        
        # Total classes formatting
        if change.total_classes_difference != 0:
            formatted_values['total_classes'] = {
                'label': 'Total Classes',
                'before': change.old_total_classes,
                'after': change.new_total_classes,
                'difference': change.total_classes_difference,
                'difference_text': f"{change.total_classes_difference:+d}",
                'before_after_text': f"{change.old_total_classes} → {change.new_total_classes}",
                'is_improvement': change.total_classes_difference > 0,  # More classes is generally neutral/positive
                'severity': 'neutral'
            }
        
        # Percentage formatting with detailed calculations
        if abs(change.percentage_difference) > 0.01:
            formatted_values['percentage'] = {
                'label': 'Attendance Percentage',
                'before': change.old_percentage,
                'after': change.new_percentage,
                'difference': change.percentage_difference,
                'difference_text': f"{change.percentage_difference:+.2f}%",
                'before_after_text': f"{change.old_percentage:.2f}% → {change.new_percentage:.2f}%",
                'is_improvement': change.percentage_difference > 0,
                'severity': self._get_percentage_change_severity(change.percentage_difference),
                'percentage_points': abs(change.percentage_difference),
                'relative_change': (change.percentage_difference / change.old_percentage * 100) if change.old_percentage > 0 else 0
            }
        
        # Absences formatting
        if change.absences_difference != 0:
            formatted_values['absences'] = {
                'label': 'Absences',
                'before': change.old_absences,
                'after': change.new_absences,
                'difference': change.absences_difference,
                'difference_text': f"{change.absences_difference:+d}",
                'before_after_text': f"{change.old_absences} → {change.new_absences}",
                'is_improvement': change.absences_difference < 0,  # Fewer absences is better
                'severity': 'positive' if change.absences_difference < 0 else 'negative'
            }
        
        return formatted_values
    
    def create_visual_indicators(self, change: AttendanceChangeBase) -> Dict[str, Any]:
        """
        Create comprehensive visual indicators for different change types.
        
        Args:
            change: The attendance change
            
        Returns:
            Dictionary containing visual indicators and styling information
        """
        if isinstance(change, AttendanceChange):
            return self._create_attendance_change_indicators(change)
        elif isinstance(change, NewCourseChange):
            return self._create_new_course_indicators(change)
        elif isinstance(change, RemovedCourseChange):
            return self._create_removed_course_indicators(change)
        else:
            return self._create_default_indicators()
    
    def _create_attendance_change_indicators(self, change: AttendanceChange) -> Dict[str, Any]:
        """Create visual indicators for attendance changes."""
        severity = change.get_change_severity()
        
        indicators = {
            'positive': {
                'icon': '<i class="fas fa-arrow-up"></i>',
                'color': '#4caf50',
                'background_color': '#e8f5e8',
                'border_color': '#4caf50',
                'css_class': 'change-positive',
                'badge_text': 'Improved',
                'badge_class': 'badge-success'
            },
            'negative': {
                'icon': '<i class="fas fa-arrow-down"></i>',
                'color': '#f44336',
                'background_color': '#ffeaea',
                'border_color': '#f44336',
                'css_class': 'change-negative',
                'badge_text': 'Declined',
                'badge_class': 'badge-danger'
            },
            'neutral': {
                'icon': '<i class="fas fa-chart-bar"></i>',
                'color': '#2196f3',
                'background_color': '#e3f2fd',
                'border_color': '#2196f3',
                'css_class': 'change-neutral',
                'badge_text': 'Updated',
                'badge_class': 'badge-info'
            }
        }
        
        base_indicator = indicators.get(severity, indicators['neutral'])
        
        # Add specific details for attendance changes
        base_indicator.update({
            'severity': severity,
            'has_percentage_change': abs(change.percentage_difference) > 0.01,
            'has_classes_change': change.classes_difference != 0,
            'has_total_change': change.total_classes_difference != 0,
            'priority': self._calculate_change_priority(change)
        })
        
        return base_indicator
    
    def _create_new_course_indicators(self, change: NewCourseChange) -> Dict[str, Any]:
        """Create visual indicators for new course changes."""
        status_indicators = {
            'safe': {
                'icon': '<i class="fas fa-check-circle"></i>',
                'color': '#4caf50',
                'background_color': '#e8f5e8',
                'border_color': '#4caf50',
                'css_class': 'change-new-safe',
                'badge_text': 'Good Attendance',
                'badge_class': 'badge-success'
            },
            'warning': {
                'icon': '<i class="fas fa-exclamation-triangle"></i>',
                'color': '#ff9800',
                'background_color': '#fff3e0',
                'border_color': '#ff9800',
                'css_class': 'change-new-warning',
                'badge_text': 'Monitor',
                'badge_class': 'badge-warning'
            },
            'danger': {
                'icon': '<i class="fas fa-bell"></i>',
                'color': '#f44336',
                'background_color': '#ffeaea',
                'border_color': '#f44336',
                'css_class': 'change-new-danger',
                'badge_text': 'Low Attendance',
                'badge_class': 'badge-danger'
            }
        }
        
        base_indicator = status_indicators.get(change.status, status_indicators['safe'])
        
        base_indicator.update({
            'severity': 'neutral',  # New courses are generally neutral
            'status': change.status,
            'attendance_percentage': change.attendance_percentage,
            'is_critical': change.status == 'danger',
            'priority': 2 if change.status == 'danger' else 3
        })
        
        return base_indicator
    
    def _create_removed_course_indicators(self, change: RemovedCourseChange) -> Dict[str, Any]:
        """Create visual indicators for removed course changes."""
        return {
            'icon': '<i class="fas fa-trash-alt"></i>',
            'color': '#ff9800',
            'background_color': '#fff3e0',
            'border_color': '#ff9800',
            'css_class': 'change-removed',
            'badge_text': 'Removed',
            'badge_class': 'badge-warning',
            'severity': 'neutral',
            'priority': 4  # Lower priority than active changes
        }
    
    def _create_default_indicators(self) -> Dict[str, Any]:
        """Create default visual indicators."""
        return {
            'icon': '📋',
            'color': '#757575',
            'background_color': '#f5f5f5',
            'border_color': '#757575',
            'css_class': 'change-default',
            'badge_text': 'Updated',
            'badge_class': 'badge-secondary',
            'severity': 'neutral',
            'priority': 5
        }
    
    def _get_percentage_change_severity(self, percentage_diff: float) -> str:
        """
        Determine severity based on percentage change.
        
        Args:
            percentage_diff: The percentage difference
            
        Returns:
            Severity level string
        """
        if percentage_diff > 1.0:
            return 'positive'
        elif percentage_diff < -1.0:
            return 'negative'
        else:
            return 'neutral'
    
    def _calculate_change_priority(self, change: AttendanceChange) -> int:
        """
        Calculate priority for sorting changes (lower number = higher priority).
        
        Args:
            change: The attendance change
            
        Returns:
            Priority number (1-5, where 1 is highest priority)
        """
        # High priority: significant negative changes
        if (change.classes_difference < -1 or 
            change.percentage_difference < -2.0 or 
            change.absences_difference > 1):
            return 1
        
        # Medium-high priority: positive changes
        if (change.classes_difference > 0 or 
            change.percentage_difference > 1.0 or 
            change.absences_difference < 0):
            return 2
        
        # Medium priority: neutral changes with some impact
        if (abs(change.percentage_difference) > 0.5 or 
            change.total_classes_difference != 0):
            return 3
        
        # Low priority: minor changes
        return 4
    
    def group_and_sort_changes(self, changes: List[AttendanceChangeBase]) -> Dict[str, List[Dict[str, Any]]]:
        """
        Group changes by type and sort by priority for organized display.
        
        Args:
            changes: List of attendance changes
            
        Returns:
            Dictionary with grouped and sorted changes
        """
        # Render all changes first
        rendered_changes = []
        for change in changes:
            rendered_change = self._render_single_change(change)
            if rendered_change:
                # Add visual indicators
                indicators = self.create_visual_indicators(change)
                rendered_change.update(indicators)
                rendered_changes.append(rendered_change)
        
        # Group by change type
        grouped = {
            'attendance_changes': [],
            'new_courses': [],
            'removed_courses': []
        }
        
        for rendered_change in rendered_changes:
            change_type = rendered_change['change_type']
            if change_type == 'attendance':
                grouped['attendance_changes'].append(rendered_change)
            elif change_type == 'new_course':
                grouped['new_courses'].append(rendered_change)
            elif change_type == 'removed_course':
                grouped['removed_courses'].append(rendered_change)
        
        # Sort each group by priority
        for group in grouped.values():
            group.sort(key=lambda x: x.get('priority', 5))
        
        return grouped