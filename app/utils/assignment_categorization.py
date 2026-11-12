# app/utils/assignment_categorization.py
"""
Utility functions for categorizing and processing assignments.
Handles deadline parsing, urgency calculation, sorting, and filtering.
"""

from datetime import datetime, timedelta, timezone
from typing import List, Dict, Any, Optional, Tuple
import re
import logging

logger = logging.getLogger(__name__)


# Urgency thresholds
URGENT_THRESHOLD_HOURS = 24  # Due within 24 hours
SOON_THRESHOLD_HOURS = 72    # Due within 3 days (72 hours)


def parse_deadline(deadline_str: str) -> Optional[datetime]:
    """
    Parse deadline string to datetime object.
    
    Supports LMS format: "9 September 2025-11:55 pm"
    
    Args:
        deadline_str: Deadline string from LMS
        
    Returns:
        datetime object or None if parsing fails
        
    Examples:
        >>> parse_deadline("9 September 2025-11:55 pm")
        datetime(2025, 9, 9, 23, 55)
        >>> parse_deadline("15 January 2025-09:30 am")
        datetime(2025, 1, 15, 9, 30)
    """
    if not deadline_str:
        return None
    
    try:
        # LMS format: "9 September 2025-11:55 pm"
        pattern = r'(\d{1,2})\s+([A-Za-z]+)\s+(\d{4})\s*-\s*(\d{1,2}):(\d{2})\s*(am|pm)'
        match = re.match(pattern, deadline_str.strip(), re.IGNORECASE)
        
        if match:
            day, month_name, year, hour, minute, ampm = match.groups()
            
            # Month name to number mapping
            month_map = {
                'january': 1, 'february': 2, 'march': 3, 'april': 4,
                'may': 5, 'june': 6, 'july': 7, 'august': 8,
                'september': 9, 'october': 10, 'november': 11, 'december': 12
            }
            
            month = month_map.get(month_name.lower())
            if not month:
                logger.warning(f"Unknown month name: {month_name}")
                return None
            
            # Convert to 24-hour format
            hour = int(hour)
            if ampm.lower() == 'pm' and hour != 12:
                hour += 12
            elif ampm.lower() == 'am' and hour == 12:
                hour = 0
            
            return datetime(int(year), month, int(day), hour, int(minute))
        
        # Try ISO format as fallback
        return datetime.fromisoformat(deadline_str)
        
    except Exception as e:
        logger.warning(f"Failed to parse deadline '{deadline_str}': {e}")
        return None


def submission_cutoff(value) -> Optional[datetime]:
    """Return exclusive midnight cutoff after the LMS date, in naive PKT.

    Monitor and reminders use this; the original deadline stays unchanged.
    """
    deadline = value if isinstance(value, datetime) else parse_deadline(value)
    if deadline is None:
        return None
    if deadline.tzinfo is not None:
        deadline = deadline.astimezone(timezone(timedelta(hours=5))).replace(tzinfo=None)
    return deadline.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)


def format_deadline(deadline: datetime) -> str:
    """
    Format datetime to LMS deadline string format.
    
    Args:
        deadline: datetime object
        
    Returns:
        Formatted deadline string
        
    Examples:
        >>> format_deadline(datetime(2025, 9, 9, 23, 55))
        "9 September 2025-11:55 pm"
    """
    try:
        return deadline.strftime("%d %B %Y-%I:%M %p").lower()
    except Exception as e:
        logger.warning(f"Failed to format deadline {deadline}: {e}")
        return deadline.isoformat()


def calculate_hours_until_deadline(deadline: datetime, reference_time: Optional[datetime] = None) -> float:
    """
    Calculate hours until deadline from reference time.
    
    Args:
        deadline: Deadline datetime
        reference_time: Reference time (defaults to now)
        
    Returns:
        Hours until deadline (negative if past due)
        
    Examples:
        >>> now = datetime(2025, 1, 1, 12, 0)
        >>> deadline = datetime(2025, 1, 2, 12, 0)
        >>> calculate_hours_until_deadline(deadline, now)
        24.0
    """
    if not deadline:
        return float('inf')
    
    if reference_time is None:
        # The LMS deadlines are in PKT (UTC+5). Cloud Run servers are in UTC.
        # We must adjust the server's UTC time to PKT before subtracting.
        reference_time = datetime.utcnow() + timedelta(hours=5)
    
    time_delta = deadline - reference_time
    return time_delta.total_seconds() / 3600


def calculate_urgency_level(assignment: Dict[str, Any], reference_time: Optional[datetime] = None) -> str:
    """
    Calculate urgency level for an assignment.
    
    Urgency levels:
    - "urgent": Due within 24 hours
    - "soon": Due within 3 days (72 hours)
    - "later": Due later than 3 days
    - "no_deadline": No deadline specified
    
    Args:
        assignment: Assignment dictionary
        reference_time: Reference time (defaults to now)
        
    Returns:
        Urgency level string
        
    Examples:
        >>> assignment = {'deadline': '9 September 2025-11:55 pm'}
        >>> calculate_urgency_level(assignment)
        'urgent'  # if within 24 hours
    """
    deadline_str = assignment.get('deadline')
    if not deadline_str:
        return 'no_deadline'
    
    deadline = submission_cutoff(deadline_str)
    if not deadline:
        return 'no_deadline'
    
    hours_until = calculate_hours_until_deadline(deadline, reference_time)
    
    # Past due
    if hours_until <= 0:
        return 'overdue'
    
    # Urgent: within 24 hours
    if hours_until <= URGENT_THRESHOLD_HOURS:
        return 'urgent'
    
    # Soon: within 3 days
    if hours_until <= SOON_THRESHOLD_HOURS:
        return 'soon'
    
    # Later: more than 3 days
    return 'later'


def categorize_assignment(assignment: Dict[str, Any]) -> Tuple[str, str]:
    """
    Categorize assignment as pending or missed, and calculate urgency.
    
    Logic:
    - Pending: Can still submit (can_submit=True) AND deadline is upcoming
    - Missed: submission_status is 'overdue' (deadline exceeded, nothing submitted)
    - Submitted: Assignment has been submitted (deadline may or may not have passed)
    
    Args:
        assignment: Assignment dictionary
        
    Returns:
        Tuple of (category, urgency_level)
        - category: "pending", "missed", or "submitted"
        - urgency_level: "urgent", "soon", "later", "no_deadline", or "overdue"
        
    Examples:
        >>> assignment = {
        ...     'can_submit': True,
        ...     'is_past_due': False,
        ...     'deadline': '9 September 2025-11:55 pm'
        ... }
        >>> categorize_assignment(assignment)
        ('pending', 'urgent')
    """
    submission_status = assignment.get('submission_status', '')
    cutoff = submission_cutoff(assignment.get('deadline'))
    is_past_due = (calculate_hours_until_deadline(cutoff) <= 0
                   if cutoff else assignment.get('is_past_due', False))
    is_overdue = assignment.get('is_overdue', False)
    submission_file = assignment.get('submission_file')
    # can_submit is set by to_dict(); fall back to deriving it from submission_status
    can_submit = assignment.get('can_submit', submission_status == 'pending')

    # submission_file is the most reliable signal — if the student uploaded a file,
    # it's submitted regardless of what the action column says (LMS shows
    # "Deadline Exceeded" in the action column even for submitted assignments)
    if submission_file:
        category = 'submitted'
    # Explicit submitted status (e.g. graded, returned)
    elif submission_status == 'submitted':
        category = 'submitted'
    # Missed: deadline passed and nothing was submitted
    elif is_past_due or is_overdue:
        category = 'missed'
    # Pending: can still submit
    elif can_submit:
        category = 'pending'
    else:
        category = 'submitted'
    
    # Calculate urgency
    urgency_level = calculate_urgency_level(assignment)
    
    return category, urgency_level


def enrich_assignment_with_metadata(assignment: Dict[str, Any]) -> Dict[str, Any]:
    """
    Enrich assignment dictionary with calculated metadata.
    
    Adds the following fields:
    - deadline_datetime: ISO format datetime string
    - hours_until_due: Hours until deadline (negative if past)
    - urgency_level: "urgent", "soon", "later", "no_deadline", or "overdue"
    - category: "pending", "missed", or "submitted"
    - days_until_due: Days until deadline (for display)
    - is_urgent: Boolean flag for urgent assignments
    
    Args:
        assignment: Assignment dictionary
        
    Returns:
        Enriched assignment dictionary (modifies in place and returns)
    """
    # Calculate monitor cutoff without changing the original LMS deadline.
    deadline_str = assignment.get('deadline')
    deadline = submission_cutoff(deadline_str) if deadline_str else None
    
    # Add deadline_datetime in ISO format
    if deadline:
        assignment['deadline_datetime'] = deadline.replace(tzinfo=timezone(timedelta(hours=5))).isoformat()
        assignment['submission_deadline'] = (deadline - timedelta(seconds=1)).strftime('%d %B %Y-11:59:59 pm PKT')
        assignment['hours_until_due'] = calculate_hours_until_deadline(deadline)
        assignment['days_until_due'] = assignment['hours_until_due'] / 24
    else:
        assignment['deadline_datetime'] = None
        assignment['submission_deadline'] = None
        assignment['hours_until_due'] = None
        assignment['days_until_due'] = None
    
    # Categorize and calculate urgency
    category, urgency_level = categorize_assignment(assignment)
    assignment['category'] = category
    assignment['urgency_level'] = urgency_level
    assignment['is_urgent'] = urgency_level == 'urgent'
    
    return assignment


def categorize_assignments(assignments: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    """
    Categorize a list of assignments into pending, missed, and submitted.
    
    Args:
        assignments: List of assignment dictionaries
        
    Returns:
        Dictionary with keys 'pending', 'missed', 'submitted' containing categorized assignments
        
    Examples:
        >>> assignments = [
        ...     {'is_pending': True, 'is_past_due': False, 'deadline': '...'},
        ...     {'is_pending': True, 'is_past_due': True, 'deadline': '...'}
        ... ]
        >>> result = categorize_assignments(assignments)
        >>> result.keys()
        dict_keys(['pending', 'missed', 'submitted'])
    """
    categorized = {
        'pending': [],
        'missed': [],
        'submitted': []
    }
    
    for assignment in assignments:
        # Enrich with metadata
        enriched = enrich_assignment_with_metadata(assignment.copy())
        
        # Add to appropriate category
        category = enriched.get('category', 'submitted')
        if category in categorized:
            categorized[category].append(enriched)
    
    return categorized


def sort_assignments_by_deadline(assignments: List[Dict[str, Any]], 
                                 ascending: bool = True) -> List[Dict[str, Any]]:
    """
    Sort assignments by deadline.
    
    Args:
        assignments: List of assignment dictionaries
        ascending: Sort in ascending order (earliest first) if True
        
    Returns:
        Sorted list of assignments
        
    Examples:
        >>> assignments = [
        ...     {'deadline': '15 January 2025-09:30 am'},
        ...     {'deadline': '9 January 2025-11:55 pm'}
        ... ]
        >>> sorted_assignments = sort_assignments_by_deadline(assignments)
        >>> sorted_assignments[0]['deadline']
        '9 January 2025-11:55 pm'
    """
    def get_sort_key(assignment: Dict[str, Any]) -> datetime:
        """Get sort key for assignment (deadline datetime)"""
        deadline_str = assignment.get('deadline')
        if not deadline_str:
            # Assignments without deadlines go to the end
            return datetime.max if ascending else datetime.min
        
        deadline = parse_deadline(deadline_str)
        if not deadline:
            return datetime.max if ascending else datetime.min
        
        return deadline
    
    return sorted(assignments, key=get_sort_key, reverse=not ascending)


def filter_assignments_by_date_range(assignments: List[Dict[str, Any]], 
                                     start_date: Optional[datetime] = None,
                                     end_date: Optional[datetime] = None) -> List[Dict[str, Any]]:
    """
    Filter assignments by deadline date range.
    
    Args:
        assignments: List of assignment dictionaries
        start_date: Start date (inclusive), None for no lower bound
        end_date: End date (inclusive), None for no upper bound
        
    Returns:
        Filtered list of assignments
        
    Examples:
        >>> assignments = [
        ...     {'deadline': '9 January 2025-11:55 pm'},
        ...     {'deadline': '15 February 2025-09:30 am'}
        ... ]
        >>> start = datetime(2025, 1, 1)
        >>> end = datetime(2025, 1, 31)
        >>> filtered = filter_assignments_by_date_range(assignments, start, end)
        >>> len(filtered)
        1
    """
    filtered = []
    
    for assignment in assignments:
        deadline_str = assignment.get('deadline')
        if not deadline_str:
            continue
        
        deadline = parse_deadline(deadline_str)
        if not deadline:
            continue
        
        # Check date range
        if start_date and deadline < start_date:
            continue
        if end_date and deadline > end_date:
            continue
        
        filtered.append(assignment)
    
    return filtered


def filter_assignments_by_urgency(assignments: List[Dict[str, Any]], 
                                  urgency_levels: List[str]) -> List[Dict[str, Any]]:
    """
    Filter assignments by urgency level.
    
    Args:
        assignments: List of assignment dictionaries
        urgency_levels: List of urgency levels to include
                       (e.g., ['urgent', 'soon'])
        
    Returns:
        Filtered list of assignments
        
    Examples:
        >>> assignments = [
        ...     {'deadline': '9 January 2025-11:55 pm', 'urgency_level': 'urgent'},
        ...     {'deadline': '15 February 2025-09:30 am', 'urgency_level': 'later'}
        ... ]
        >>> urgent_only = filter_assignments_by_urgency(assignments, ['urgent'])
        >>> len(urgent_only)
        1
    """
    # Ensure assignments have urgency_level calculated
    enriched_assignments = [
        enrich_assignment_with_metadata(a.copy()) if 'urgency_level' not in a else a
        for a in assignments
    ]
    
    return [
        a for a in enriched_assignments
        if a.get('urgency_level') in urgency_levels
    ]


def get_assignments_summary(assignments: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Generate summary statistics for assignments.
    
    Args:
        assignments: List of assignment dictionaries
        
    Returns:
        Dictionary with summary statistics
        
    Examples:
        >>> assignments = [...]
        >>> summary = get_assignments_summary(assignments)
        >>> summary.keys()
        dict_keys(['total', 'pending', 'missed', 'submitted', 'urgent', 'soon', 'later'])
    """
    # Categorize assignments
    categorized = categorize_assignments(assignments)
    
    # Count by urgency
    urgent_count = 0
    soon_count = 0
    later_count = 0
    
    for assignment in categorized['pending']:
        urgency = assignment.get('urgency_level', 'later')
        if urgency == 'urgent':
            urgent_count += 1
        elif urgency == 'soon':
            soon_count += 1
        elif urgency == 'later':
            later_count += 1
    
    return {
        'total': len(assignments),
        'pending': len(categorized['pending']),
        'missed': len(categorized['missed']),
        'submitted': len(categorized['submitted']),
        'urgent': urgent_count,
        'soon': soon_count,
        'later': later_count,
        'by_category': {
            'pending': categorized['pending'],
            'missed': categorized['missed'],
            'submitted': categorized['submitted']
        }
    }


def format_time_until_deadline(hours: float) -> str:
    """
    Format hours until deadline in human-readable format.
    
    Args:
        hours: Hours until deadline (negative if past)
        
    Returns:
        Human-readable string
        
    Examples:
        >>> format_time_until_deadline(2.5)
        'Due in 2 hours'
        >>> format_time_until_deadline(48)
        'Due in 2 days'
        >>> format_time_until_deadline(-24)
        'Overdue by 1 day'
    """
    if hours is None:
        return 'No deadline'
    
    abs_hours = abs(hours)
    
    # Past due
    if hours < 0:
        if abs_hours < 1:
            return f'Overdue by {int(abs_hours * 60)} minutes'
        elif abs_hours < 24:
            return f'Overdue by {int(abs_hours)} hours'
        else:
            days = int(abs_hours / 24)
            return f'Overdue by {days} day{"s" if days != 1 else ""}'
    
    # Future
    if abs_hours < 1:
        minutes = int(abs_hours * 60)
        return f'Due in {minutes} minute{"s" if minutes != 1 else ""}'
    elif abs_hours < 24:
        return f'Due in {int(abs_hours)} hour{"s" if int(abs_hours) != 1 else ""}'
    else:
        days = int(abs_hours / 24)
        return f'Due in {days} day{"s" if days != 1 else ""}'
