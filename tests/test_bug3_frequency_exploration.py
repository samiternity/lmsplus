#!/usr/bin/env python3
"""
Bug #3 Exploration Test: Notification Frequency Bug

**CRITICAL**: This test MUST FAIL on unfixed code - failure confirms the bug exists.
**DO NOT attempt to fix the test or the code when it fails.**

This test encodes the expected behavior - it will validate the fix when it passes after implementation.

**GOAL**: Surface counterexamples that demonstrate duplicate notifications or missing reminders.

**Validates: Requirements 2.4, 2.5, 2.6, 2.7**
"""

import pytest
import tempfile
import json
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch, Mock
from dataclasses import dataclass

from app.services.assignment_notification_service import AssignmentNotificationService
from app.models.notification_models import NotificationType


@dataclass
class MockLMSAssignment:
    """Mock LMS Assignment for testing"""
    id: str
    title: str
    course_code: str
    course_name: str
    assignment_no: str
    deadline: datetime
    
    def __post_init__(self):
        # Ensure deadline is a datetime object
        if isinstance(self.deadline, str):
            self.deadline = datetime.fromisoformat(self.deadline)


class MockUser:
    """Mock User object for testing"""
    def __init__(self, user_id: str):
        self.id = user_id
        self.notification_preferences = {
            'new_assignment': True,
            'due_24h': True,
            'due_3d': True,
            'overdue_warning': True
        }


class TestBug3FrequencyExploration:
    """
    Bug Condition Exploration Test for Notification Frequency
    
    Tests that frequency control rules are applied correctly:
    - Once for new assignments
    - Once for overdue assignments
    - Periodic reminders for urgent assignments (every 3-6 hours)
    - Reduced frequency for non-urgent assignments
    
    **Expected Outcome on UNFIXED code**: Test FAILS (proves bug exists)
    """
    
    @pytest.fixture
    def temp_tracking_dir(self):
        """Create a temporary directory for tracking storage"""
        temp_dir = tempfile.mkdtemp()
        yield temp_dir
        # Cleanup
        import shutil
        shutil.rmtree(temp_dir, ignore_errors=True)
    
    def test_new_assignment_notified_only_once(self, temp_tracking_dir):
        """
        **Property 1: Bug Condition** - New Assignment Frequency Control
        
        Run `check_and_notify()` twice with the same new assignment.
        Verify only one notification is sent (not duplicate).
        
        This test MUST FAIL on unfixed code if duplicate notifications are sent.
        
        **Validates: Requirements 2.4**
        """
        # Setup test data
        user_id = "test_user_new_once"
        account_id = "test_account_new_once"
        user = MockUser(user_id)
        
        # Create a new assignment (due in 48 hours - not urgent)
        now = datetime.now()
        new_assignment = MockLMSAssignment(
            id="asn_new_001",
            title="Programming Assignment 1",
            course_code="CS101",
            course_name="Introduction to Programming",
            assignment_no="1",
            deadline=now + timedelta(hours=48)
        )
        
        print("\n=== Test Setup: New Assignment Frequency ===")
        print(f"Assignment: {new_assignment.title}")
        print(f"Deadline: {new_assignment.deadline.isoformat()}")
        print("Expected: Notify once when first detected, then no more notifications")
        
        # Mock the notification service
        with patch('app.services.assignment_notification_service.get_notification_service') as mock_get_notif_svc:
            mock_notification_service = Mock()
            mock_notification_service.send_notification.return_value = Mock(
                id="notif_001",
                created_at=now
            )
            mock_get_notif_svc.return_value = mock_notification_service
            
            # Create assignment notification service
            assignment_notif_service = AssignmentNotificationService(data_dir=temp_tracking_dir)
            
            print("\n=== First Run: check_and_notify() ===")
            # First run - should send notification for new assignment
            counts1 = assignment_notif_service.check_and_notify(
                user_id=user_id,
                assignments=[new_assignment],
                user=user,
                account_id=account_id
            )
            
            print(f"First run notification counts: {counts1}")
            print(f"Notifications sent: {mock_notification_service.send_notification.call_count}")
            
            # Verify notification was sent on first run
            # Note: Assignment may trigger multiple notifications (new + due_3d)
            assert counts1['new'] == 1, "Should send notification for new assignment on first run"
            first_run_total = sum(counts1.values())
            assert first_run_total >= 1, "Should have sent at least one notification"
            
            # Reset mock to track second run separately
            first_run_call_count = mock_notification_service.send_notification.call_count
            mock_notification_service.send_notification.reset_mock()
            
            print("\n=== Second Run: check_and_notify() (immediately after) ===")
            # Second run - should NOT send notification (assignment already known)
            counts2 = assignment_notif_service.check_and_notify(
                user_id=user_id,
                assignments=[new_assignment],
                user=user,
                account_id=account_id
            )
            
            print(f"Second run notification counts: {counts2}")
            print(f"Notifications sent in second run: {mock_notification_service.send_notification.call_count}")
            
            # **CRITICAL ASSERTION**: No notification should be sent on second run
            # This will FAIL on unfixed code if duplicate notifications are sent
            if counts2['new'] > 0:
                print("\n=== COUNTEREXAMPLE FOUND ===")
                print(f"Duplicate notification sent for same new assignment!")
                print(f"First run: {counts1['new']} notification(s)")
                print(f"Second run: {counts2['new']} notification(s)")
                print("This confirms Bug #3: System sends notifications every time instead of once")
                print("\nExpected behavior:")
                print("  - First run: 1 notification (assignment is new)")
                print("  - Second run: 0 notifications (assignment already known)")
            
            assert counts2['new'] == 0, \
                f"Should NOT send notification for same assignment on second run, but sent {counts2['new']}"
            
            assert mock_notification_service.send_notification.call_count == 0, \
                f"Should not call send_notification on second run, but called {mock_notification_service.send_notification.call_count} times"
            
            print("\n=== Test Result ===")
            print("✓ New assignment notified only once (no duplicates)")
    
    def test_urgent_assignment_periodic_reminders(self, temp_tracking_dir):
        """
        **Property 1: Bug Condition** - Urgent Assignment Reminder Frequency
        
        Create urgent assignment (due in 12h), notify once, advance time by 4 hours,
        run again, verify reminder notification is sent.
        
        This test MUST FAIL on unfixed code if no reminder logic exists.
        
        **Validates: Requirements 2.6**
        """
        # Setup test data
        user_id = "test_user_urgent_reminder"
        account_id = "test_account_urgent_reminder"
        user = MockUser(user_id)
        
        # Create an urgent assignment (due in 12 hours)
        now = datetime.now()
        urgent_assignment = MockLMSAssignment(
            id="asn_urgent_001",
            title="Lab Report 3",
            course_code="PHYS201",
            course_name="Physics II",
            assignment_no="3",
            deadline=now + timedelta(hours=12)
        )
        
        print("\n=== Test Setup: Urgent Assignment Reminders ===")
        print(f"Assignment: {urgent_assignment.title}")
        print(f"Deadline: {urgent_assignment.deadline.isoformat()}")
        print(f"Hours until due: 12 (URGENT)")
        print("Expected: Send reminder every 3-6 hours for urgent assignments")
        
        # Mock the notification service
        with patch('app.services.assignment_notification_service.get_notification_service') as mock_get_notif_svc:
            mock_notification_service = Mock()
            mock_notification_service.send_notification.return_value = Mock(
                id="notif_urgent_001",
                created_at=now
            )
            mock_get_notif_svc.return_value = mock_notification_service
            
            # Create assignment notification service
            assignment_notif_service = AssignmentNotificationService(data_dir=temp_tracking_dir)
            
            print("\n=== First Run: check_and_notify() at T+0 ===")
            # First run - should send notification for urgent assignment (due in 24h)
            counts1 = assignment_notif_service.check_and_notify(
                user_id=user_id,
                assignments=[urgent_assignment],
                user=user,
                account_id=account_id
            )
            
            print(f"First run notification counts: {counts1}")
            print(f"Notifications sent: {mock_notification_service.send_notification.call_count}")
            
            # Verify notification was sent on first run
            assert counts1['due_24h'] == 1, "Should send notification for urgent assignment on first run"
            
            # Reset mock
            mock_notification_service.send_notification.reset_mock()
            
            # Simulate time passing: 4 hours later
            # We need to manually update the tracking file to simulate time passing
            tracking_file = assignment_notif_service._get_tracking_file(account_id)
            with open(tracking_file, 'r') as f:
                tracking_data = json.load(f)
            
            print("\n=== Simulating Time Passage: 4 hours later ===")
            # Update the timestamp in notified_deadlines to 4 hours ago
            deadline_key = f"{urgent_assignment.id}_24h"
            if deadline_key in tracking_data['notified_deadlines']:
                four_hours_ago = now - timedelta(hours=4)
                tracking_data['notified_deadlines'][deadline_key] = four_hours_ago.isoformat()
                print(f"Updated {deadline_key} timestamp to 4 hours ago: {four_hours_ago.isoformat()}")
            
            # Save updated tracking data
            with open(tracking_file, 'w') as f:
                json.dump(tracking_data, f, indent=2)
            
            print("\n=== Second Run: check_and_notify() at T+4h ===")
            # Second run - should send reminder notification (4 hours have passed)
            counts2 = assignment_notif_service.check_and_notify(
                user_id=user_id,
                assignments=[urgent_assignment],
                user=user,
                account_id=account_id
            )
            
            print(f"Second run notification counts: {counts2}")
            print(f"Notifications sent in second run: {mock_notification_service.send_notification.call_count}")
            
            # **CRITICAL ASSERTION**: Reminder notification should be sent after 4 hours
            # This will FAIL on unfixed code if no reminder logic exists
            if counts2['due_24h'] == 0:
                print("\n=== COUNTEREXAMPLE FOUND ===")
                print("No reminder notification sent for urgent assignment after 4 hours!")
                print(f"First run (T+0): {counts1['due_24h']} notification(s)")
                print(f"Second run (T+4h): {counts2['due_24h']} notification(s)")
                print("This confirms Bug #3: System does not send periodic reminders for urgent assignments")
                print("\nExpected behavior:")
                print("  - First run: 1 notification (assignment is urgent)")
                print("  - Second run (4h later): 1 reminder notification (periodic reminder)")
            
            assert counts2['due_24h'] == 1, \
                f"Should send reminder notification for urgent assignment after 4 hours, but sent {counts2['due_24h']}"
            
            assert mock_notification_service.send_notification.call_count == 1, \
                f"Should call send_notification once for reminder, but called {mock_notification_service.send_notification.call_count} times"
            
            print("\n=== Test Result ===")
            print("✓ Urgent assignment sends periodic reminders (every 3-6 hours)")
    
    def test_non_urgent_assignment_no_immediate_duplicate(self, temp_tracking_dir):
        """
        **Property 1: Bug Condition** - Non-Urgent Assignment Frequency Control
        
        Create non-urgent assignment (due in 48h), notify once, run again immediately,
        verify no duplicate notification is sent.
        
        This test MUST FAIL on unfixed code if duplicate notifications are sent.
        
        **Validates: Requirements 2.7**
        """
        # Setup test data
        user_id = "test_user_non_urgent"
        account_id = "test_account_non_urgent"
        user = MockUser(user_id)
        
        # Create a non-urgent assignment (due in 48 hours)
        now = datetime.now()
        non_urgent_assignment = MockLMSAssignment(
            id="asn_non_urgent_001",
            title="Essay Assignment",
            course_code="ENG101",
            course_name="English Composition",
            assignment_no="2",
            deadline=now + timedelta(hours=48)
        )
        
        print("\n=== Test Setup: Non-Urgent Assignment Frequency ===")
        print(f"Assignment: {non_urgent_assignment.title}")
        print(f"Deadline: {non_urgent_assignment.deadline.isoformat()}")
        print(f"Hours until due: 48 (NOT URGENT)")
        print("Expected: Notify once, then no duplicates immediately after")
        
        # Mock the notification service
        with patch('app.services.assignment_notification_service.get_notification_service') as mock_get_notif_svc:
            mock_notification_service = Mock()
            mock_notification_service.send_notification.return_value = Mock(
                id="notif_non_urgent_001",
                created_at=now
            )
            mock_get_notif_svc.return_value = mock_notification_service
            
            # Create assignment notification service
            assignment_notif_service = AssignmentNotificationService(data_dir=temp_tracking_dir)
            
            print("\n=== First Run: check_and_notify() ===")
            # First run - should send notification for new assignment
            counts1 = assignment_notif_service.check_and_notify(
                user_id=user_id,
                assignments=[non_urgent_assignment],
                user=user,
                account_id=account_id
            )
            
            print(f"First run notification counts: {counts1}")
            print(f"Notifications sent: {mock_notification_service.send_notification.call_count}")
            
            # Verify notification was sent on first run (as new assignment)
            assert counts1['new'] == 1, "Should send notification for new assignment on first run"
            
            # Reset mock
            mock_notification_service.send_notification.reset_mock()
            
            print("\n=== Second Run: check_and_notify() (immediately after) ===")
            # Second run - should NOT send notification (no status change)
            counts2 = assignment_notif_service.check_and_notify(
                user_id=user_id,
                assignments=[non_urgent_assignment],
                user=user,
                account_id=account_id
            )
            
            print(f"Second run notification counts: {counts2}")
            print(f"Notifications sent in second run: {mock_notification_service.send_notification.call_count}")
            
            # **CRITICAL ASSERTION**: No notification should be sent on second run
            # This will FAIL on unfixed code if duplicate notifications are sent
            total_second_run = sum(counts2.values())
            if total_second_run > 0:
                print("\n=== COUNTEREXAMPLE FOUND ===")
                print(f"Duplicate notification sent for non-urgent assignment!")
                print(f"First run: {sum(counts1.values())} notification(s)")
                print(f"Second run: {total_second_run} notification(s)")
                print(f"Second run details: {counts2}")
                print("This confirms Bug #3: System sends duplicate notifications for non-urgent assignments")
                print("\nExpected behavior:")
                print("  - First run: 1 notification (assignment is new)")
                print("  - Second run: 0 notifications (no status change)")
            
            assert total_second_run == 0, \
                f"Should NOT send notification for non-urgent assignment on second run, but sent {total_second_run}"
            
            assert mock_notification_service.send_notification.call_count == 0, \
                f"Should not call send_notification on second run, but called {mock_notification_service.send_notification.call_count} times"
            
            print("\n=== Test Result ===")
            print("✓ Non-urgent assignment does not send duplicate notifications")
    
    def test_overdue_assignment_notified_only_once(self, temp_tracking_dir):
        """
        **Property 1: Bug Condition** - Overdue Assignment Frequency Control
        
        Create overdue assignment, notify once, run again, verify no duplicate.
        
        This test MUST FAIL on unfixed code if duplicate notifications are sent.
        
        **Validates: Requirements 2.5**
        """
        # Setup test data
        user_id = "test_user_overdue_once"
        account_id = "test_account_overdue_once"
        user = MockUser(user_id)
        
        # Create an overdue assignment (deadline was 2 hours ago)
        now = datetime.now()
        overdue_assignment = MockLMSAssignment(
            id="asn_overdue_001",
            title="Homework 5",
            course_code="MATH201",
            course_name="Calculus II",
            assignment_no="5",
            deadline=now - timedelta(hours=2)
        )
        
        print("\n=== Test Setup: Overdue Assignment Frequency ===")
        print(f"Assignment: {overdue_assignment.title}")
        print(f"Deadline: {overdue_assignment.deadline.isoformat()}")
        print(f"Status: OVERDUE (2 hours ago)")
        print("Expected: Notify once when overdue detected, then no more notifications")
        
        # Mock the notification service
        with patch('app.services.assignment_notification_service.get_notification_service') as mock_get_notif_svc:
            mock_notification_service = Mock()
            mock_notification_service.send_notification.return_value = Mock(
                id="notif_overdue_001",
                created_at=now
            )
            mock_get_notif_svc.return_value = mock_notification_service
            
            # Create assignment notification service
            assignment_notif_service = AssignmentNotificationService(data_dir=temp_tracking_dir)
            
            print("\n=== First Run: check_and_notify() ===")
            # First run - should send notification for overdue assignment
            counts1 = assignment_notif_service.check_and_notify(
                user_id=user_id,
                assignments=[overdue_assignment],
                user=user,
                account_id=account_id
            )
            
            print(f"First run notification counts: {counts1}")
            print(f"Notifications sent: {mock_notification_service.send_notification.call_count}")
            
            # Verify notification was sent on first run
            # Note: overdue assignment is also "new" on first detection
            assert counts1['new'] == 1 or counts1['overdue'] == 1, \
                "Should send notification for overdue assignment on first run"
            
            # Reset mock
            mock_notification_service.send_notification.reset_mock()
            
            print("\n=== Second Run: check_and_notify() (immediately after) ===")
            # Second run - should NOT send notification (already notified)
            counts2 = assignment_notif_service.check_and_notify(
                user_id=user_id,
                assignments=[overdue_assignment],
                user=user,
                account_id=account_id
            )
            
            print(f"Second run notification counts: {counts2}")
            print(f"Notifications sent in second run: {mock_notification_service.send_notification.call_count}")
            
            # **CRITICAL ASSERTION**: No notification should be sent on second run
            # This will FAIL on unfixed code if duplicate notifications are sent
            if counts2['overdue'] > 0:
                print("\n=== COUNTEREXAMPLE FOUND ===")
                print(f"Duplicate overdue notification sent!")
                print(f"First run: {counts1['overdue']} overdue notification(s)")
                print(f"Second run: {counts2['overdue']} overdue notification(s)")
                print("This confirms Bug #3: System sends overdue notifications every time instead of once")
                print("\nExpected behavior:")
                print("  - First run: 1 notification (assignment is overdue)")
                print("  - Second run: 0 notifications (already notified)")
            
            assert counts2['overdue'] == 0, \
                f"Should NOT send overdue notification on second run, but sent {counts2['overdue']}"
            
            assert mock_notification_service.send_notification.call_count == 0, \
                f"Should not call send_notification on second run, but called {mock_notification_service.send_notification.call_count} times"
            
            print("\n=== Test Result ===")
            print("✓ Overdue assignment notified only once (no duplicates)")
    
    def test_frequency_control_comprehensive_scenario(self, temp_tracking_dir):
        """
        **Property 1: Bug Condition** - Comprehensive Frequency Control Test
        
        Test all frequency control rules in a single comprehensive scenario:
        - New assignment: notify once
        - Urgent assignment: periodic reminders
        - Non-urgent: no duplicates
        - Overdue: notify once
        
        This test provides a comprehensive view of the frequency control bug.
        
        **Validates: Requirements 2.4, 2.5, 2.6, 2.7**
        """
        # Setup test data
        user_id = "test_user_comprehensive"
        account_id = "test_account_comprehensive"
        user = MockUser(user_id)
        
        now = datetime.now()
        
        # Create multiple assignments with different urgency levels
        assignments = [
            # New, non-urgent (due in 48h)
            MockLMSAssignment(
                id="asn_comp_001",
                title="Essay 1",
                course_code="ENG101",
                course_name="English",
                assignment_no="1",
                deadline=now + timedelta(hours=48)
            ),
            # Urgent (due in 12h)
            MockLMSAssignment(
                id="asn_comp_002",
                title="Lab 2",
                course_code="CHEM101",
                course_name="Chemistry",
                assignment_no="2",
                deadline=now + timedelta(hours=12)
            ),
            # Overdue (2h ago)
            MockLMSAssignment(
                id="asn_comp_003",
                title="Quiz 3",
                course_code="MATH101",
                course_name="Math",
                assignment_no="3",
                deadline=now - timedelta(hours=2)
            ),
        ]
        
        print("\n=== Test Setup: Comprehensive Frequency Control ===")
        print(f"Testing {len(assignments)} assignments with different urgency levels")
        for asn in assignments:
            hours_until = (asn.deadline - now).total_seconds() / 3600
            status = "OVERDUE" if hours_until < 0 else ("URGENT" if hours_until <= 24 else "NORMAL")
            print(f"  - {asn.title}: {status} (due in {hours_until:.1f}h)")
        
        # Mock the notification service
        with patch('app.services.assignment_notification_service.get_notification_service') as mock_get_notif_svc:
            mock_notification_service = Mock()
            mock_notification_service.send_notification.return_value = Mock(
                id="notif_comp_001",
                created_at=now
            )
            mock_get_notif_svc.return_value = mock_notification_service
            
            # Create assignment notification service
            assignment_notif_service = AssignmentNotificationService(data_dir=temp_tracking_dir)
            
            print("\n=== Run 1: Initial check_and_notify() ===")
            # First run - should send notifications for all assignments
            counts1 = assignment_notif_service.check_and_notify(
                user_id=user_id,
                assignments=assignments,
                user=user,
                account_id=account_id
            )
            
            print(f"Run 1 notification counts: {counts1}")
            total_run1 = sum(counts1.values())
            print(f"Total notifications sent: {total_run1}")
            
            # Verify notifications were sent
            assert total_run1 >= 3, f"Should send at least 3 notifications on first run, got {total_run1}"
            
            # Reset mock
            first_run_count = mock_notification_service.send_notification.call_count
            mock_notification_service.send_notification.reset_mock()
            
            print("\n=== Run 2: Immediate re-check (no time passed) ===")
            # Second run - should NOT send any notifications (no time passed, no status changes)
            counts2 = assignment_notif_service.check_and_notify(
                user_id=user_id,
                assignments=assignments,
                user=user,
                account_id=account_id
            )
            
            print(f"Run 2 notification counts: {counts2}")
            total_run2 = sum(counts2.values())
            print(f"Total notifications sent: {total_run2}")
            
            # **CRITICAL ASSERTION**: No notifications should be sent on immediate re-check
            if total_run2 > 0:
                print("\n=== COUNTEREXAMPLE FOUND ===")
                print(f"Duplicate notifications sent on immediate re-check!")
                print(f"Run 1: {total_run1} notification(s)")
                print(f"Run 2: {total_run2} notification(s)")
                print(f"Run 2 details: {counts2}")
                print("This confirms Bug #3: System sends duplicate notifications")
            
            assert total_run2 == 0, \
                f"Should NOT send any notifications on immediate re-check, but sent {total_run2}"
            
            # Simulate time passing: 4 hours later for urgent assignment reminder
            tracking_file = assignment_notif_service._get_tracking_file(account_id)
            with open(tracking_file, 'r') as f:
                tracking_data = json.load(f)
            
            print("\n=== Simulating Time Passage: 4 hours later ===")
            # Update urgent assignment timestamp to 4 hours ago
            urgent_key = f"asn_comp_002_24h"
            if urgent_key in tracking_data['notified_deadlines']:
                four_hours_ago = now - timedelta(hours=4)
                tracking_data['notified_deadlines'][urgent_key] = four_hours_ago.isoformat()
                print(f"Updated {urgent_key} timestamp to 4 hours ago")
            
            with open(tracking_file, 'w') as f:
                json.dump(tracking_data, f, indent=2)
            
            mock_notification_service.send_notification.reset_mock()
            
            print("\n=== Run 3: Re-check after 4 hours ===")
            # Third run - should send reminder for urgent assignment only
            counts3 = assignment_notif_service.check_and_notify(
                user_id=user_id,
                assignments=assignments,
                user=user,
                account_id=account_id
            )
            
            print(f"Run 3 notification counts: {counts3}")
            total_run3 = sum(counts3.values())
            print(f"Total notifications sent: {total_run3}")
            
            # **CRITICAL ASSERTION**: Should send reminder for urgent assignment
            if counts3['due_24h'] == 0:
                print("\n=== COUNTEREXAMPLE FOUND ===")
                print("No reminder sent for urgent assignment after 4 hours!")
                print(f"Run 3 due_24h count: {counts3['due_24h']}")
                print("This confirms Bug #3: System does not send periodic reminders for urgent assignments")
            
            assert counts3['due_24h'] == 1, \
                f"Should send 1 reminder for urgent assignment after 4 hours, but sent {counts3['due_24h']}"
            
            # Should NOT send reminders for non-urgent or overdue
            assert counts3['new'] == 0, "Should not send new assignment notifications again"
            assert counts3['overdue'] == 0, "Should not send overdue notifications again"
            
            print("\n=== Test Result ===")
            print("✓ Comprehensive frequency control test passed")
            print(f"  Run 1: {total_run1} notifications (initial)")
            print(f"  Run 2: {total_run2} notifications (no duplicates)")
            print(f"  Run 3: {total_run3} notifications (urgent reminder only)")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
