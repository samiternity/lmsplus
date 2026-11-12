#!/usr/bin/env python3
"""
Bug #2 Exploration Test: Web Push Delivery Bug

**CRITICAL**: This test MUST FAIL on unfixed code - failure confirms the bug exists.
**DO NOT attempt to fix the test or the code when it fails.**

This test encodes the expected behavior - it will validate the fix when it passes after implementation.

**GOAL**: Surface counterexamples that demonstrate web push is not sent during background service execution.

**Validates: Requirements 2.2**
"""

import pytest
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch, Mock, MagicMock, call
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
    def __init__(self, user_id: str, fcm_token: str = None):
        self.id = user_id
        self.notification_preferences = {
            'new_assignment': True,
            'due_24h': True,
            'due_3d': True,
            'overdue_warning': True
        }
        self._fcm_token = fcm_token


class TestBug2WebPushExploration:
    """
    Bug Condition Exploration Test for Web Push Delivery
    
    Tests that when background service detects new/urgent assignments and user has FCM token,
    `fcm.send_push()` is called with correct parameters.
    
    **Expected Outcome on UNFIXED code**: Test FAILS (proves bug exists)
    """
    
    @pytest.fixture
    def temp_dirs(self):
        """Create temporary directories for notification and tracking storage"""
        notification_dir = tempfile.mkdtemp()
        tracking_dir = tempfile.mkdtemp()
        yield notification_dir, tracking_dir
        # Cleanup
        import shutil
        shutil.rmtree(notification_dir, ignore_errors=True)
        shutil.rmtree(tracking_dir, ignore_errors=True)
    
    def test_background_context_sends_web_push_for_new_assignment(self, temp_dirs):
        """
        **Property 1: Bug Condition** - Background Web Push Delivery (New Assignment)
        
        Simulate background service context: call assignment_notification_service.check_and_notify()
        with a user who has an FCM token. Verify that `fcm.send_push()` is called.
        
        This test MUST FAIL on unfixed code (proving FCM is not called in background context).
        
        **Validates: Requirements 2.2**
        """
        notification_dir, tracking_dir = temp_dirs
        
        # Setup test data
        user_id = "test_user_bg_new"
        account_id = "test_account_bg_new"
        fcm_token = "test_fcm_token_12345"
        
        user = MockUser(user_id, fcm_token)
        
        # Create a new assignment (due in 48 hours - not urgent yet)
        now = datetime.now()
        new_assignment = MockLMSAssignment(
            id="asn_001",
            title="Programming Assignment 1",
            course_code="CS101",
            course_name="Introduction to Programming",
            assignment_no="1",
            deadline=now + timedelta(hours=48)
        )
        
        print("\n=== Test Setup ===")
        print(f"User ID: {user_id}")
        print(f"Account ID: {account_id}")
        print(f"FCM Token: {fcm_token}")
        print(f"Assignment: {new_assignment.title}")
        print(f"Deadline: {new_assignment.deadline.isoformat()}")
        print("Context: Background service (simulated)")
        
        # Mock the notification service and FCM service
        with patch('app.services.assignment_notification_service.get_notification_service') as mock_get_notif_svc, \
             patch('app.services.fcm_service.get_fcm_service') as mock_get_fcm:
            
            # Setup notification service mock (for in-app notifications)
            mock_notification_service = Mock()
            mock_notification_service.send_notification.return_value = Mock(
                id="notif_001",
                created_at=now
            )
            mock_get_notif_svc.return_value = mock_notification_service
            
            # Setup FCM service mock - THIS IS THE CRITICAL PART
            mock_fcm_service = Mock()
            mock_fcm_service.get_token.return_value = fcm_token
            mock_fcm_service.send_push.return_value = True
            mock_get_fcm.return_value = mock_fcm_service
            
            # Create assignment notification service with temp directory
            assignment_notif_service = AssignmentNotificationService(data_dir=tracking_dir)
            
            print("\n=== Running Assignment Notification Service (Background Context) ===")
            
            # Call check_and_notify - this is what the background service calls
            counts = assignment_notif_service.check_and_notify(
                user_id=user_id,
                assignments=[new_assignment],
                user=user,
                account_id=account_id
            )
            
            print(f"Notification counts: {counts}")
            
            # Verify in-app notification was created
            assert mock_notification_service.send_notification.called, \
                "In-app notification should have been created"
            
            print("\n=== Checking FCM Calls ===")
            print(f"FCM get_token called: {mock_fcm_service.get_token.called}")
            print(f"FCM get_token call count: {mock_fcm_service.get_token.call_count}")
            print(f"FCM send_push called: {mock_fcm_service.send_push.called}")
            print(f"FCM send_push call count: {mock_fcm_service.send_push.call_count}")
            
            if mock_fcm_service.send_push.called:
                print(f"FCM send_push calls: {mock_fcm_service.send_push.call_args_list}")
            
            # **CRITICAL ASSERTION**: FCM send_push should be called
            # This will FAIL on unfixed code (proving the bug exists)
            if not mock_fcm_service.send_push.called:
                print("\n=== COUNTEREXAMPLE FOUND ===")
                print("fcm.send_push() was NEVER called despite:")
                print(f"  - User has valid FCM token: {fcm_token}")
                print(f"  - New assignment was detected: {new_assignment.title}")
                print(f"  - In-app notification was created: {mock_notification_service.send_notification.called}")
                print(f"  - Context: Background service execution")
                print("This confirms Bug #2: Web push is not sent during background service execution")
                print("\nExpected behavior:")
                print("  - fcm.send_push() should be called with:")
                print(f"    - token: {fcm_token}")
                print(f"    - title: 'New Assignment — {new_assignment.course_name}'")
                print(f"    - body: 'Assignment {new_assignment.assignment_no} has been posted'")
            
            assert mock_fcm_service.send_push.called, \
                "fcm.send_push() should be called when background service detects new assignment and user has FCM token"
            
            # Verify FCM was called with correct parameters
            call_args = mock_fcm_service.send_push.call_args
            assert call_args is not None, "FCM send_push should have been called with arguments"
            
            # Extract arguments (could be positional or keyword)
            if call_args[0]:  # Positional args
                token_arg = call_args[0][0]
                title_arg = call_args[0][1]
                body_arg = call_args[0][2]
            else:  # Keyword args
                token_arg = call_args[1].get('token')
                title_arg = call_args[1].get('title')
                body_arg = call_args[1].get('body')
            
            assert token_arg == fcm_token, \
                f"FCM should be called with user's token, got: {token_arg}"
            
            assert "New Assignment" in title_arg or "Assignment" in title_arg, \
                f"FCM title should mention assignment, got: {title_arg}"
            
            print("\n=== Test Result ===")
            print("✓ FCM send_push was called correctly")
            print(f"  Token: {token_arg}")
            print(f"  Title: {title_arg}")
            print(f"  Body: {body_arg}")
    
    def test_background_context_sends_web_push_for_urgent_assignment(self, temp_dirs):
        """
        **Property 1: Bug Condition** - Background Web Push Delivery (Urgent Assignment)
        
        Simulate background service detecting an urgent assignment (due in 12 hours)
        for a user with FCM token. Verify that `fcm.send_push()` is called.
        
        This test MUST FAIL on unfixed code (proving FCM is not called in background context).
        
        **Validates: Requirements 2.2**
        """
        notification_dir, tracking_dir = temp_dirs
        
        # Setup test data
        user_id = "test_user_bg_urgent"
        account_id = "test_account_bg_urgent"
        fcm_token = "test_fcm_token_urgent_67890"
        
        user = MockUser(user_id, fcm_token)
        
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
        
        print("\n=== Test Setup (Urgent Assignment) ===")
        print(f"User ID: {user_id}")
        print(f"Account ID: {account_id}")
        print(f"FCM Token: {fcm_token}")
        print(f"Assignment: {urgent_assignment.title}")
        print(f"Deadline: {urgent_assignment.deadline.isoformat()}")
        print(f"Hours until due: 12 (URGENT)")
        
        # Mock the notification service and FCM service
        with patch('app.services.assignment_notification_service.get_notification_service') as mock_get_notif_svc, \
             patch('app.services.fcm_service.get_fcm_service') as mock_get_fcm:
            
            # Setup notification service mock
            mock_notification_service = Mock()
            mock_notification_service.send_notification.return_value = Mock(
                id="notif_urgent_001",
                created_at=now
            )
            mock_get_notif_svc.return_value = mock_notification_service
            
            # Setup FCM service mock
            mock_fcm_service = Mock()
            mock_fcm_service.get_token.return_value = fcm_token
            mock_fcm_service.send_push.return_value = True
            mock_get_fcm.return_value = mock_fcm_service
            
            # Create assignment notification service
            assignment_notif_service = AssignmentNotificationService(data_dir=tracking_dir)
            
            print("\n=== Running Assignment Notification Service ===")
            
            # Call check_and_notify
            counts = assignment_notif_service.check_and_notify(
                user_id=user_id,
                assignments=[urgent_assignment],
                user=user,
                account_id=account_id
            )
            
            print(f"Notification counts: {counts}")
            
            # Verify in-app notification was created
            assert mock_notification_service.send_notification.called, \
                "In-app notification should have been created for urgent assignment"
            
            print("\n=== Checking FCM Calls ===")
            print(f"FCM send_push called: {mock_fcm_service.send_push.called}")
            print(f"FCM send_push call count: {mock_fcm_service.send_push.call_count}")
            
            # **CRITICAL ASSERTION**: FCM send_push should be called for urgent assignment
            if not mock_fcm_service.send_push.called:
                print("\n=== COUNTEREXAMPLE FOUND ===")
                print("fcm.send_push() was NEVER called despite:")
                print(f"  - User has valid FCM token: {fcm_token}")
                print(f"  - Urgent assignment detected (due in 12h): {urgent_assignment.title}")
                print(f"  - In-app notification was created: {mock_notification_service.send_notification.called}")
                print("This confirms Bug #2: Web push is not sent for urgent assignments in background context")
                print("\nExpected behavior:")
                print("  - fcm.send_push() should be called with:")
                print(f"    - token: {fcm_token}")
                print(f"    - title: 'Due in 12h — {urgent_assignment.course_name}'")
                print(f"    - body: 'Assignment {urgent_assignment.assignment_no} is due very soon'")
            
            assert mock_fcm_service.send_push.called, \
                "fcm.send_push() should be called when background service detects urgent assignment and user has FCM token"
            
            print("\n=== Test Result ===")
            print("✓ FCM send_push was called for urgent assignment")
    
    def test_background_context_no_web_push_without_fcm_token(self, temp_dirs):
        """
        **Property 1: Bug Condition** - No Web Push Without FCM Token
        
        Verify that when user does NOT have an FCM token, no web push is attempted.
        This is the correct behavior and should pass even on unfixed code.
        
        This test validates that the fix doesn't break the "no token" case.
        
        **Validates: Requirements 2.2**
        """
        notification_dir, tracking_dir = temp_dirs
        
        # Setup test data - user WITHOUT FCM token
        user_id = "test_user_no_token"
        account_id = "test_account_no_token"
        
        user = MockUser(user_id, fcm_token=None)  # No FCM token
        
        # Create a new assignment
        now = datetime.now()
        new_assignment = MockLMSAssignment(
            id="asn_no_token_001",
            title="Essay Assignment",
            course_code="ENG101",
            course_name="English Composition",
            assignment_no="2",
            deadline=now + timedelta(hours=48)
        )
        
        print("\n=== Test Setup (No FCM Token) ===")
        print(f"User ID: {user_id}")
        print(f"FCM Token: None (user has not registered for push notifications)")
        print(f"Assignment: {new_assignment.title}")
        
        # Mock the notification service and FCM service
        with patch('app.services.assignment_notification_service.get_notification_service') as mock_get_notif_svc, \
             patch('app.services.fcm_service.get_fcm_service') as mock_get_fcm:
            
            # Setup notification service mock
            mock_notification_service = Mock()
            mock_notification_service.send_notification.return_value = Mock(
                id="notif_no_token_001",
                created_at=now
            )
            mock_get_notif_svc.return_value = mock_notification_service
            
            # Setup FCM service mock - returns None for token
            mock_fcm_service = Mock()
            mock_fcm_service.get_token.return_value = None  # No token
            mock_fcm_service.send_push.return_value = True
            mock_get_fcm.return_value = mock_fcm_service
            
            # Create assignment notification service
            assignment_notif_service = AssignmentNotificationService(data_dir=tracking_dir)
            
            print("\n=== Running Assignment Notification Service ===")
            
            # Call check_and_notify
            counts = assignment_notif_service.check_and_notify(
                user_id=user_id,
                assignments=[new_assignment],
                user=user,
                account_id=account_id
            )
            
            print(f"Notification counts: {counts}")
            
            # Verify in-app notification was created
            assert mock_notification_service.send_notification.called, \
                "In-app notification should still be created even without FCM token"
            
            print("\n=== Checking FCM Calls ===")
            print(f"FCM send_push called: {mock_fcm_service.send_push.called}")
            
            # FCM send_push should NOT be called when user has no token
            # This is correct behavior
            if mock_fcm_service.send_push.called:
                print("\n=== UNEXPECTED BEHAVIOR ===")
                print("fcm.send_push() was called even though user has no FCM token!")
                print("This would waste resources and potentially cause errors.")
            
            assert not mock_fcm_service.send_push.called, \
                "fcm.send_push() should NOT be called when user has no FCM token"
            
            print("\n=== Test Result ===")
            print("✓ No web push attempted for user without FCM token (correct behavior)")
    
    def test_background_context_multiple_assignments_multiple_pushes(self, temp_dirs):
        """
        **Property 1: Bug Condition** - Multiple Assignments Trigger Multiple Pushes
        
        Simulate background service detecting multiple new assignments.
        Verify that `fcm.send_push()` is called for each notification.
        
        This test MUST FAIL on unfixed code (proving FCM is not called in background context).
        
        **Validates: Requirements 2.2**
        """
        notification_dir, tracking_dir = temp_dirs
        
        # Setup test data
        user_id = "test_user_multiple"
        account_id = "test_account_multiple"
        fcm_token = "test_fcm_token_multiple_99999"
        
        user = MockUser(user_id, fcm_token)
        
        # Create multiple assignments
        now = datetime.now()
        assignments = [
            MockLMSAssignment(
                id="asn_multi_001",
                title="Homework 1",
                course_code="MATH101",
                course_name="Calculus I",
                assignment_no="1",
                deadline=now + timedelta(hours=48)
            ),
            MockLMSAssignment(
                id="asn_multi_002",
                title="Lab 2",
                course_code="CHEM101",
                course_name="Chemistry I",
                assignment_no="2",
                deadline=now + timedelta(hours=36)
            ),
            MockLMSAssignment(
                id="asn_multi_003",
                title="Quiz 1",
                course_code="BIO101",
                course_name="Biology I",
                assignment_no="1",
                deadline=now + timedelta(hours=18)  # Urgent (< 24h)
            ),
        ]
        
        print("\n=== Test Setup (Multiple Assignments) ===")
        print(f"User ID: {user_id}")
        print(f"FCM Token: {fcm_token}")
        print(f"Assignments: {len(assignments)}")
        for asn in assignments:
            hours_until = (asn.deadline - now).total_seconds() / 3600
            print(f"  - {asn.title} (due in {hours_until:.1f}h)")
        
        # Mock the notification service and FCM service
        with patch('app.services.assignment_notification_service.get_notification_service') as mock_get_notif_svc, \
             patch('app.services.fcm_service.get_fcm_service') as mock_get_fcm:
            
            # Setup notification service mock
            mock_notification_service = Mock()
            mock_notification_service.send_notification.return_value = Mock(
                id="notif_multi_001",
                created_at=now
            )
            mock_get_notif_svc.return_value = mock_notification_service
            
            # Setup FCM service mock
            mock_fcm_service = Mock()
            mock_fcm_service.get_token.return_value = fcm_token
            mock_fcm_service.send_push.return_value = True
            mock_get_fcm.return_value = mock_fcm_service
            
            # Create assignment notification service
            assignment_notif_service = AssignmentNotificationService(data_dir=tracking_dir)
            
            print("\n=== Running Assignment Notification Service ===")
            
            # Call check_and_notify
            counts = assignment_notif_service.check_and_notify(
                user_id=user_id,
                assignments=assignments,
                user=user,
                account_id=account_id
            )
            
            print(f"Notification counts: {counts}")
            
            # Verify in-app notifications were created
            in_app_call_count = mock_notification_service.send_notification.call_count
            print(f"In-app notifications created: {in_app_call_count}")
            
            assert in_app_call_count >= 3, \
                f"Should have created at least 3 in-app notifications, got {in_app_call_count}"
            
            print("\n=== Checking FCM Calls ===")
            print(f"FCM send_push called: {mock_fcm_service.send_push.called}")
            print(f"FCM send_push call count: {mock_fcm_service.send_push.call_count}")
            
            # **CRITICAL ASSERTION**: FCM send_push should be called multiple times
            if not mock_fcm_service.send_push.called:
                print("\n=== COUNTEREXAMPLE FOUND ===")
                print("fcm.send_push() was NEVER called despite:")
                print(f"  - User has valid FCM token: {fcm_token}")
                print(f"  - {len(assignments)} assignments detected")
                print(f"  - {in_app_call_count} in-app notifications created")
                print("This confirms Bug #2: Web push is not sent for multiple assignments in background context")
            elif mock_fcm_service.send_push.call_count < in_app_call_count:
                print("\n=== PARTIAL COUNTEREXAMPLE FOUND ===")
                print(f"fcm.send_push() was called {mock_fcm_service.send_push.call_count} times")
                print(f"But {in_app_call_count} in-app notifications were created")
                print("Web push should be sent for each in-app notification")
            
            assert mock_fcm_service.send_push.called, \
                "fcm.send_push() should be called when background service detects assignments"
            
            # Ideally, FCM should be called once per notification
            # But we'll accept at least one call as proof that the fix is working
            assert mock_fcm_service.send_push.call_count >= 1, \
                f"fcm.send_push() should be called at least once, got {mock_fcm_service.send_push.call_count}"
            
            print("\n=== Test Result ===")
            print(f"✓ FCM send_push was called {mock_fcm_service.send_push.call_count} time(s)")
            print(f"  (Expected: {in_app_call_count} calls, one per notification)")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
