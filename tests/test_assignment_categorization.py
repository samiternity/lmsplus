"""
Test script for assignment categorization utilities.
"""

from datetime import datetime, timedelta
from app.utils.assignment_categorization import (
    parse_deadline,
    format_deadline,
    calculate_hours_until_deadline,
    calculate_urgency_level,
    categorize_assignment,
    enrich_assignment_with_metadata,
    categorize_assignments,
    sort_assignments_by_deadline,
    filter_assignments_by_date_range,
    filter_assignments_by_urgency,
    get_assignments_summary,
    format_time_until_deadline
)


def test_parse_deadline():
    """Test deadline parsing"""
    print("Testing parse_deadline...")
    
    # Test valid deadline
    deadline_str = "9 September 2025-11:55 pm"
    result = parse_deadline(deadline_str)
    assert result is not None, "Failed to parse valid deadline"
    assert result.year == 2025
    assert result.month == 9
    assert result.day == 9
    assert result.hour == 23
    assert result.minute == 55
    print(f"  ✓ Parsed '{deadline_str}' -> {result}")
    
    # Test AM time
    deadline_str = "15 January 2025-09:30 am"
    result = parse_deadline(deadline_str)
    assert result is not None
    assert result.hour == 9
    print(f"  ✓ Parsed '{deadline_str}' -> {result}")
    
    # Test noon
    deadline_str = "1 March 2025-12:00 pm"
    result = parse_deadline(deadline_str)
    assert result is not None
    assert result.hour == 12
    print(f"  ✓ Parsed '{deadline_str}' -> {result}")
    
    # Test midnight
    deadline_str = "1 March 2025-12:00 am"
    result = parse_deadline(deadline_str)
    assert result is not None
    assert result.hour == 0
    print(f"  ✓ Parsed '{deadline_str}' -> {result}")
    
    print("  ✓ All parse_deadline tests passed!\n")


def test_format_deadline():
    """Test deadline formatting"""
    print("Testing format_deadline...")
    
    deadline = datetime(2025, 9, 9, 23, 55)
    result = format_deadline(deadline)
    assert "september" in result.lower()
    assert "11:55 pm" in result.lower()
    print(f"  ✓ Formatted {deadline} -> '{result}'")
    
    print("  ✓ All format_deadline tests passed!\n")


def test_calculate_urgency_level():
    """Test urgency level calculation"""
    print("Testing calculate_urgency_level...")
    
    now = datetime.now()
    
    # Urgent: 12 hours from now
    urgent_deadline = now + timedelta(hours=12)
    assignment = {'deadline': format_deadline(urgent_deadline)}
    urgency = calculate_urgency_level(assignment, now)
    assert urgency == 'urgent', f"Expected 'urgent', got '{urgency}'"
    print(f"  ✓ 12 hours from now -> {urgency}")
    
    # Soon: 48 hours from now
    soon_deadline = now + timedelta(hours=48)
    assignment = {'deadline': format_deadline(soon_deadline)}
    urgency = calculate_urgency_level(assignment, now)
    assert urgency == 'soon', f"Expected 'soon', got '{urgency}'"
    print(f"  ✓ 48 hours from now -> {urgency}")
    
    # Later: 7 days from now
    later_deadline = now + timedelta(days=7)
    assignment = {'deadline': format_deadline(later_deadline)}
    urgency = calculate_urgency_level(assignment, now)
    assert urgency == 'later', f"Expected 'later', got '{urgency}'"
    print(f"  ✓ 7 days from now -> {urgency}")
    
    # Overdue: 1 day ago
    overdue_deadline = now - timedelta(days=1)
    assignment = {'deadline': format_deadline(overdue_deadline)}
    urgency = calculate_urgency_level(assignment, now)
    assert urgency == 'overdue', f"Expected 'overdue', got '{urgency}'"
    print(f"  ✓ 1 day ago -> {urgency}")
    
    print("  ✓ All calculate_urgency_level tests passed!\n")


def test_categorize_assignment():
    """Test assignment categorization"""
    print("Testing categorize_assignment...")
    
    # Pending assignment
    assignment = {
        'is_pending': True,
        'is_past_due': False,
        'is_overdue': False,
        'submission_status': 'pending',
        'deadline': format_deadline(datetime.now() + timedelta(hours=12))
    }
    category, urgency = categorize_assignment(assignment)
    assert category == 'pending', f"Expected 'pending', got '{category}'"
    assert urgency == 'urgent', f"Expected 'urgent', got '{urgency}'"
    print(f"  ✓ Pending assignment -> category={category}, urgency={urgency}")
    
    # Missed assignment
    assignment = {
        'is_pending': True,
        'is_past_due': True,
        'is_overdue': True,
        'submission_status': 'pending',
        'deadline': format_deadline(datetime.now() - timedelta(days=1))
    }
    category, urgency = categorize_assignment(assignment)
    assert category == 'missed', f"Expected 'missed', got '{category}'"
    print(f"  ✓ Missed assignment -> category={category}, urgency={urgency}")
    
    # Submitted assignment
    assignment = {
        'is_pending': False,
        'is_past_due': False,
        'is_overdue': False,
        'submission_status': 'submitted',
        'deadline': format_deadline(datetime.now() + timedelta(days=1))
    }
    category, urgency = categorize_assignment(assignment)
    assert category == 'submitted', f"Expected 'submitted', got '{category}'"
    print(f"  ✓ Submitted assignment -> category={category}, urgency={urgency}")
    
    print("  ✓ All categorize_assignment tests passed!\n")


def test_categorize_assignments():
    """Test batch categorization"""
    print("Testing categorize_assignments...")
    
    now = datetime.now()
    
    assignments = [
        {
            'title': 'Assignment 1',
            'is_pending': True,
            'is_past_due': False,
            'is_overdue': False,
            'submission_status': 'pending',
            'deadline': format_deadline(now + timedelta(hours=12))
        },
        {
            'title': 'Assignment 2',
            'is_pending': True,
            'is_past_due': True,
            'is_overdue': True,
            'submission_status': 'pending',
            'deadline': format_deadline(now - timedelta(days=1))
        },
        {
            'title': 'Assignment 3',
            'is_pending': False,
            'is_past_due': False,
            'is_overdue': False,
            'submission_status': 'submitted',
            'deadline': format_deadline(now + timedelta(days=1))
        }
    ]
    
    categorized = categorize_assignments(assignments)
    
    assert len(categorized['pending']) == 1, f"Expected 1 pending, got {len(categorized['pending'])}"
    assert len(categorized['missed']) == 1, f"Expected 1 missed, got {len(categorized['missed'])}"
    assert len(categorized['submitted']) == 1, f"Expected 1 submitted, got {len(categorized['submitted'])}"
    
    print(f"  ✓ Categorized {len(assignments)} assignments:")
    print(f"    - Pending: {len(categorized['pending'])}")
    print(f"    - Missed: {len(categorized['missed'])}")
    print(f"    - Submitted: {len(categorized['submitted'])}")
    
    print("  ✓ All categorize_assignments tests passed!\n")


def test_sort_assignments_by_deadline():
    """Test sorting by deadline"""
    print("Testing sort_assignments_by_deadline...")
    
    now = datetime.now()
    
    assignments = [
        {'title': 'Assignment 3', 'deadline': format_deadline(now + timedelta(days=7))},
        {'title': 'Assignment 1', 'deadline': format_deadline(now + timedelta(hours=12))},
        {'title': 'Assignment 2', 'deadline': format_deadline(now + timedelta(days=2))}
    ]
    
    sorted_asc = sort_assignments_by_deadline(assignments, ascending=True)
    assert sorted_asc[0]['title'] == 'Assignment 1', "First should be Assignment 1"
    assert sorted_asc[2]['title'] == 'Assignment 3', "Last should be Assignment 3"
    print(f"  ✓ Sorted ascending: {[a['title'] for a in sorted_asc]}")
    
    sorted_desc = sort_assignments_by_deadline(assignments, ascending=False)
    assert sorted_desc[0]['title'] == 'Assignment 3', "First should be Assignment 3"
    assert sorted_desc[2]['title'] == 'Assignment 1', "Last should be Assignment 1"
    print(f"  ✓ Sorted descending: {[a['title'] for a in sorted_desc]}")
    
    print("  ✓ All sort_assignments_by_deadline tests passed!\n")


def test_filter_assignments_by_date_range():
    """Test filtering by date range"""
    print("Testing filter_assignments_by_date_range...")
    
    now = datetime.now()
    
    assignments = [
        {'title': 'Assignment 1', 'deadline': format_deadline(now + timedelta(days=1))},
        {'title': 'Assignment 2', 'deadline': format_deadline(now + timedelta(days=5))},
        {'title': 'Assignment 3', 'deadline': format_deadline(now + timedelta(days=10))}
    ]
    
    # Filter for next 7 days
    filtered = filter_assignments_by_date_range(
        assignments,
        start_date=now,
        end_date=now + timedelta(days=7)
    )
    
    assert len(filtered) == 2, f"Expected 2 assignments, got {len(filtered)}"
    print(f"  ✓ Filtered to next 7 days: {len(filtered)} assignments")
    
    print("  ✓ All filter_assignments_by_date_range tests passed!\n")


def test_filter_assignments_by_urgency():
    """Test filtering by urgency"""
    print("Testing filter_assignments_by_urgency...")
    
    now = datetime.now()
    
    assignments = [
        {'title': 'Urgent', 'deadline': format_deadline(now + timedelta(hours=12))},
        {'title': 'Soon', 'deadline': format_deadline(now + timedelta(days=2))},
        {'title': 'Later', 'deadline': format_deadline(now + timedelta(days=7))}
    ]
    
    # Filter for urgent only
    urgent_only = filter_assignments_by_urgency(assignments, ['urgent'])
    assert len(urgent_only) == 1, f"Expected 1 urgent, got {len(urgent_only)}"
    print(f"  ✓ Filtered urgent only: {len(urgent_only)} assignments")
    
    # Filter for urgent and soon
    urgent_and_soon = filter_assignments_by_urgency(assignments, ['urgent', 'soon'])
    assert len(urgent_and_soon) == 2, f"Expected 2, got {len(urgent_and_soon)}"
    print(f"  ✓ Filtered urgent and soon: {len(urgent_and_soon)} assignments")
    
    print("  ✓ All filter_assignments_by_urgency tests passed!\n")


def test_get_assignments_summary():
    """Test summary generation"""
    print("Testing get_assignments_summary...")
    
    now = datetime.now()
    
    assignments = [
        {
            'title': 'Assignment 1',
            'is_pending': True,
            'is_past_due': False,
            'is_overdue': False,
            'submission_status': 'pending',
            'deadline': format_deadline(now + timedelta(hours=12))
        },
        {
            'title': 'Assignment 2',
            'is_pending': True,
            'is_past_due': False,
            'is_overdue': False,
            'submission_status': 'pending',
            'deadline': format_deadline(now + timedelta(days=2))
        },
        {
            'title': 'Assignment 3',
            'is_pending': True,
            'is_past_due': True,
            'is_overdue': True,
            'submission_status': 'pending',
            'deadline': format_deadline(now - timedelta(days=1))
        }
    ]
    
    summary = get_assignments_summary(assignments)
    
    assert summary['total'] == 3
    assert summary['pending'] == 2
    assert summary['missed'] == 1
    assert summary['urgent'] == 1
    assert summary['soon'] == 1
    
    print(f"  ✓ Summary generated:")
    print(f"    - Total: {summary['total']}")
    print(f"    - Pending: {summary['pending']}")
    print(f"    - Missed: {summary['missed']}")
    print(f"    - Urgent: {summary['urgent']}")
    print(f"    - Soon: {summary['soon']}")
    
    print("  ✓ All get_assignments_summary tests passed!\n")


def test_format_time_until_deadline():
    """Test time formatting"""
    print("Testing format_time_until_deadline...")
    
    # Test various time ranges
    assert "30 minutes" in format_time_until_deadline(0.5)
    assert "2 hours" in format_time_until_deadline(2)
    assert "2 days" in format_time_until_deadline(48)
    assert "Overdue by 1 day" in format_time_until_deadline(-24)
    
    print("  ✓ All format_time_until_deadline tests passed!\n")


def run_all_tests():
    """Run all tests"""
    print("=" * 60)
    print("Running Assignment Categorization Tests")
    print("=" * 60 + "\n")
    
    try:
        test_parse_deadline()
        test_format_deadline()
        test_calculate_urgency_level()
        test_categorize_assignment()
        test_categorize_assignments()
        test_sort_assignments_by_deadline()
        test_filter_assignments_by_date_range()
        test_filter_assignments_by_urgency()
        test_get_assignments_summary()
        test_format_time_until_deadline()
        
        print("=" * 60)
        print("✓ ALL TESTS PASSED!")
        print("=" * 60)
        return True
        
    except AssertionError as e:
        print(f"\n✗ TEST FAILED: {e}")
        return False
    except Exception as e:
        print(f"\n✗ ERROR: {e}")
        import traceback
        traceback.print_exc()
        return False


if __name__ == '__main__':
    success = run_all_tests()
    exit(0 if success else 1)
