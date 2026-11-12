#!/usr/bin/env python3
"""
Bug #2 Preservation Tests: Web Push Delivery Bug

**IMPORTANT**: Follow observation-first methodology.
These tests observe and capture the EXISTING behavior on UNFIXED code.

**GOAL**: Verify that existing notification creation, assignment tracking, 
user preferences, and background service iteration continue to work correctly.

These tests should PASS on unfixed code (confirming baseline behavior to preserve).

**Validates: Requirements 3.2, 3.3, 3.7**
"""

import pytest
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch, Mock
from dataclasses import dataclass
from hypothesis import given, strategies as st, settings, assume, HealthCheck
from hypothesis import Phase

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
        if isinstance(self.deadline, str):
            self.deadline = datetime.fromisoformat(self.deadline)


class MockUser:
    """Mock User object for testing"""
    def __init__(self, user_id: str, preferences: dict = None):
        self.id = user_id
        self.notification_preferences = preferences or {
            'new_assignment': True,
            'due_24h': True,
            'due_3d': True,
            'overdue_warning': True
        }


class TestBug2PreservationInAppNotifications:
    """
    **Property 2: Preservation** - In-App Notification Creation
    
    Verify that background service creates in-app notifications correctly
    on UNFIXED code. This behavior must be preserved after the fix.
    
    **Expected Outcome**: Tests PASS on unfixed code
    """
    
    @pytest.fixture
    def temp_dirs(self):
        """Create temporary directories for notification and tracking storage"""
        notification_dir = tempfile.mkdtemp()
        tracking_dir = tempfile.mkdtemp()
        yield notification_dir, tracking_dir
        import shutil
        shutil.rmtree(notification_dir, ignore_errors=True)
        shutil.rmtree(tracking_dir, ignore_errors=True)
    
    def test_in_app_notification_created_for_new_assignment(self, temp_dirs):
        """
        **Property 2: Preservation** - In-App Notification Creation (New Assignment)
        
        Verify that when check_and_notify() is called with a new assignment,
        an in-app notification is created via notification_service.send_notification().
        
        This is the core functionality that must be preserved.
        
        **Validates: Requirements 3.2**
        """
        notification_dir, tracking_dir = temp_dirs
        
        user_id = "test_user_preserve_new"
        account_id = "test_account_preserve_new"
        user = MockUser(user_id)
        
        now = datetime.now()
        new_assignment = MockLMSAssignment(
            id="asn_preserve_001",
            title="Homework 1",
            course_code="CS101",
            course_name="Computer Science",
            assignment_no="1",
            deadline=now + timedelta(hours=48)
        )
        
        print("\n=== Testing In-App Notification Creation (Preservation) ===")
        print(f"Assignment: {new_assignment.title}")
        
        with patch('app.services.assignment_notification_service.get_notification_service') as mock_get_notif_svc:
            mock_notification_service = Mock()
            mock_notification_service.send_notification.return_value = Mock(
                id="notif_preserve_001",
                created_at=now
            )
            mock_get_notif_svc.return_value = mock_notification_service
            
            assignment_notif_service = AssignmentNotificationService(data_dir=tracking_dir)
            
            counts = assignment_notif_service.check_and_notify(
                user_id=user_id,
                assignments=[new_assignment],
                user=user,
                account_id=account_id
            )
            
            print(f"Notification counts: {counts}")
            print(f"send_notification called: {mock_notification_service.send_notification.called}")
            
            # PRESERVATION: In-app notification should be created
            assert mock_notification_service.send_notification.called, \
                "In-app notification should be created for new assignment"
            
            assert counts['new'] == 1, \
                f"Expected 1 new assignment notification, got {counts['new']}"
            
            # Verify correct parameters - check all calls for the NEW notification
            all_calls = mock_notification_service.send_notification.call_args_list
            assert len(all_calls) >= 1, "Should have at least one notification call"
            
            # Find the NEW assignment notification call
            new_notification_found = False
            for call in all_calls:
                kwargs = call[1] if call[1] else {}
                if kwargs.get('notification_type') == NotificationType.ASSIGNMENT_NEW:
                    assert kwargs.get('user_id') == user_id
                    new_notification_found = True
                    break
            
            assert new_notification_found, "Should have created a NEW assignment notification"
            
            print("✓ In-app notification created correctly (preserved behavior)")

    
    def test_in_app_notification_created_for_urgent_assignment(self, temp_dirs):
        """
        **Property 2: Preservation** - In-App Notification Creation (Urgent)
        
        Verify that urgent assignments (due within 24h) trigger in-app notifications.
        
        **Validates: Requirements 3.2**
        """
        notification_dir, tracking_dir = temp_dirs
        
        user_id = "test_user_preserve_urgent"
        account_id = "test_account_preserve_urgent"
        user = MockUser(user_id)
        
        now = datetime.now()
        urgent_assignment = MockLMSAssignment(
            id="asn_preserve_urgent",
            title="Lab Report",
            course_code="PHYS201",
            course_name="Physics",
            assignment_no="2",
            deadline=now + timedelta(hours=12)
        )
        
        print("\n=== Testing Urgent Assignment Notification (Preservation) ===")
        
        with patch('app.services.assignment_notification_service.get_notification_service') as mock_get_notif_svc:
            mock_notification_service = Mock()
            mock_notification_service.send_notification.return_value = Mock(
                id="notif_preserve_urgent",
                created_at=now
            )
            mock_get_notif_svc.return_value = mock_notification_service
            
            assignment_notif_service = AssignmentNotificationService(data_dir=tracking_dir)
            
            counts = assignment_notif_service.check_and_notify(
                user_id=user_id,
                assignments=[urgent_assignment],
                user=user,
                account_id=account_id
            )
            
            print(f"Notification counts: {counts}")
            
            # PRESERVATION: Should create both new and 24h notifications
            assert mock_notification_service.send_notification.called
            assert counts['new'] == 1 or counts['due_24h'] == 1, \
                "Should create notification for urgent assignment"
            
            print("✓ Urgent assignment notification created (preserved behavior)")


class TestBug2PreservationAssignmentTracking:
    """
    **Property 2: Preservation** - Assignment State Tracking
    
    Verify that assignment tracking (known_assignments, notified_deadlines)
    works correctly on UNFIXED code.
    
    **Expected Outcome**: Tests PASS on unfixed code
    """
    
    @pytest.fixture
    def temp_dirs(self):
        notification_dir = tempfile.mkdtemp()
        tracking_dir = tempfile.mkdtemp()
        yield notification_dir, tracking_dir
        import shutil
        shutil.rmtree(notification_dir, ignore_errors=True)
        shutil.rmtree(tracking_dir, ignore_errors=True)
    
    def test_assignment_tracking_prevents_duplicate_new_notifications(self, temp_dirs):
        """
        **Property 2: Preservation** - Assignment Tracking (Deduplication)
        
        Verify that once an assignment is marked as "known", it doesn't trigger
        another "new assignment" notification on subsequent checks.
        
        **Validates: Requirements 3.2**
        """
        notification_dir, tracking_dir = temp_dirs
        
        user_id = "test_user_tracking"
        account_id = "test_account_tracking"
        user = MockUser(user_id)
        
        now = datetime.now()
        assignment = MockLMSAssignment(
            id="asn_tracking_001",
            title="Essay",
            course_code="ENG101",
            course_name="English",
            assignment_no="1",
            deadline=now + timedelta(hours=48)
        )
        
        print("\n=== Testing Assignment Tracking (Preservation) ===")
        
        with patch('app.services.assignment_notification_service.get_notification_service') as mock_get_notif_svc:
            mock_notification_service = Mock()
            mock_notification_service.send_notification.return_value = Mock(
                id="notif_tracking",
                created_at=now
            )
            mock_get_notif_svc.return_value = mock_notification_service
            
            assignment_notif_service = AssignmentNotificationService(data_dir=tracking_dir)
            
            # First check - should create notification
            counts1 = assignment_notif_service.check_and_notify(
                user_id=user_id,
                assignments=[assignment],
                user=user,
                account_id=account_id
            )
            
            print(f"First check counts: {counts1}")
            assert counts1['new'] == 1, "First check should create new notification"
            
            # Reset mock
            mock_notification_service.send_notification.reset_mock()
            
            # Second check - should NOT create another new notification
            counts2 = assignment_notif_service.check_and_notify(
                user_id=user_id,
                assignments=[assignment],
                user=user,
                account_id=account_id
            )
            
            print(f"Second check counts: {counts2}")
            
            # PRESERVATION: Should not create duplicate "new" notification
            assert counts2['new'] == 0, \
                "Second check should not create duplicate new notification"
            
            print("✓ Assignment tracking prevents duplicates (preserved behavior)")

    
    def test_deadline_tracking_prevents_duplicate_deadline_notifications(self, temp_dirs):
        """
        **Property 2: Preservation** - Deadline Tracking
        
        Verify that notified_deadlines tracking prevents duplicate notifications
        for the same deadline threshold (e.g., 24h, 3d, overdue).
        
        **Validates: Requirements 3.2**
        """
        notification_dir, tracking_dir = temp_dirs
        
        user_id = "test_user_deadline_tracking"
        account_id = "test_account_deadline_tracking"
        user = MockUser(user_id)
        
        now = datetime.now()
        # Assignment due in 12 hours (triggers 24h notification)
        assignment = MockLMSAssignment(
            id="asn_deadline_track",
            title="Quiz",
            course_code="MATH101",
            course_name="Mathematics",
            assignment_no="3",
            deadline=now + timedelta(hours=12)
        )
        
        print("\n=== Testing Deadline Tracking (Preservation) ===")
        
        with patch('app.services.assignment_notification_service.get_notification_service') as mock_get_notif_svc:
            mock_notification_service = Mock()
            mock_notification_service.send_notification.return_value = Mock(
                id="notif_deadline",
                created_at=now
            )
            mock_get_notif_svc.return_value = mock_notification_service
            
            assignment_notif_service = AssignmentNotificationService(data_dir=tracking_dir)
            
            # First check - should create 24h notification
            counts1 = assignment_notif_service.check_and_notify(
                user_id=user_id,
                assignments=[assignment],
                user=user,
                account_id=account_id
            )
            
            print(f"First check counts: {counts1}")
            # Should create new + 24h notifications
            total_first = sum(counts1.values())
            assert total_first >= 1, "First check should create at least one notification"
            
            # Reset mock
            mock_notification_service.send_notification.reset_mock()
            
            # Second check - should NOT create duplicate 24h notification
            counts2 = assignment_notif_service.check_and_notify(
                user_id=user_id,
                assignments=[assignment],
                user=user,
                account_id=account_id
            )
            
            print(f"Second check counts: {counts2}")
            
            # PRESERVATION: Should not create duplicate deadline notifications
            assert counts2['due_24h'] == 0, \
                "Second check should not create duplicate 24h notification"
            
            print("✓ Deadline tracking prevents duplicates (preserved behavior)")


class TestBug2PreservationUserPreferences:
    """
    **Property 2: Preservation** - User Notification Preferences
    
    Verify that user notification preferences are respected on UNFIXED code.
    
    **Expected Outcome**: Tests PASS on unfixed code
    """
    
    @pytest.fixture
    def temp_dirs(self):
        notification_dir = tempfile.mkdtemp()
        tracking_dir = tempfile.mkdtemp()
        yield notification_dir, tracking_dir
        import shutil
        shutil.rmtree(notification_dir, ignore_errors=True)
        shutil.rmtree(tracking_dir, ignore_errors=True)
    
    def test_user_preferences_disable_new_assignment_notifications(self, temp_dirs):
        """
        **Property 2: Preservation** - User Preferences (Disabled)
        
        Verify that when user disables new_assignment notifications,
        no notification is sent for new assignments.
        
        **Validates: Requirements 3.3**
        """
        notification_dir, tracking_dir = temp_dirs
        
        user_id = "test_user_prefs_disabled"
        account_id = "test_account_prefs_disabled"
        
        # User with new_assignment disabled
        user = MockUser(user_id, preferences={
            'new_assignment': False,  # DISABLED
            'due_24h': True,
            'due_3d': True,
            'overdue_warning': True
        })
        
        now = datetime.now()
        new_assignment = MockLMSAssignment(
            id="asn_prefs_disabled",
            title="Project",
            course_code="CS201",
            course_name="Data Structures",
            assignment_no="1",
            deadline=now + timedelta(hours=48)
        )
        
        print("\n=== Testing User Preferences (Disabled) ===")
        print(f"User preferences: new_assignment = False")
        
        with patch('app.services.assignment_notification_service.get_notification_service') as mock_get_notif_svc:
            mock_notification_service = Mock()
            mock_notification_service.send_notification.return_value = Mock(
                id="notif_prefs",
                created_at=now
            )
            mock_get_notif_svc.return_value = mock_notification_service
            
            assignment_notif_service = AssignmentNotificationService(data_dir=tracking_dir)
            
            counts = assignment_notif_service.check_and_notify(
                user_id=user_id,
                assignments=[new_assignment],
                user=user,
                account_id=account_id
            )
            
            print(f"Notification counts: {counts}")
            
            # PRESERVATION: Should respect user preference and NOT send notification
            assert counts['new'] == 0, \
                "Should not send new assignment notification when user has disabled it"
            
            print("✓ User preferences respected (preserved behavior)")

    
    def test_user_preferences_enable_all_notifications(self, temp_dirs):
        """
        **Property 2: Preservation** - User Preferences (Enabled)
        
        Verify that when user enables all notification types,
        notifications are sent appropriately.
        
        **Validates: Requirements 3.3**
        """
        notification_dir, tracking_dir = temp_dirs
        
        user_id = "test_user_prefs_enabled"
        account_id = "test_account_prefs_enabled"
        
        # User with all notifications enabled
        user = MockUser(user_id, preferences={
            'new_assignment': True,
            'due_24h': True,
            'due_3d': True,
            'overdue_warning': True
        })
        
        now = datetime.now()
        new_assignment = MockLMSAssignment(
            id="asn_prefs_enabled",
            title="Lab",
            course_code="CHEM101",
            course_name="Chemistry",
            assignment_no="2",
            deadline=now + timedelta(hours=48)
        )
        
        print("\n=== Testing User Preferences (Enabled) ===")
        
        with patch('app.services.assignment_notification_service.get_notification_service') as mock_get_notif_svc:
            mock_notification_service = Mock()
            mock_notification_service.send_notification.return_value = Mock(
                id="notif_enabled",
                created_at=now
            )
            mock_get_notif_svc.return_value = mock_notification_service
            
            assignment_notif_service = AssignmentNotificationService(data_dir=tracking_dir)
            
            counts = assignment_notif_service.check_and_notify(
                user_id=user_id,
                assignments=[new_assignment],
                user=user,
                account_id=account_id
            )
            
            print(f"Notification counts: {counts}")
            
            # PRESERVATION: Should send notification when enabled
            assert counts['new'] == 1, \
                "Should send new assignment notification when user has enabled it"
            
            print("✓ User preferences respected (preserved behavior)")


class TestBug2PreservationMultipleUsersAccounts:
    """
    **Property 2: Preservation** - Multiple Users and Accounts
    
    Verify that background service correctly iterates through multiple users
    and accounts on UNFIXED code.
    
    **Expected Outcome**: Tests PASS on unfixed code
    """
    
    @pytest.fixture
    def temp_dirs(self):
        notification_dir = tempfile.mkdtemp()
        tracking_dir = tempfile.mkdtemp()
        yield notification_dir, tracking_dir
        import shutil
        shutil.rmtree(notification_dir, ignore_errors=True)
        shutil.rmtree(tracking_dir, ignore_errors=True)
    
    def test_multiple_accounts_tracked_separately(self, temp_dirs):
        """
        **Property 2: Preservation** - Per-Account Tracking
        
        Verify that assignment tracking is per-account, not per-user.
        Two accounts should have independent tracking.
        
        **Validates: Requirements 3.7**
        """
        notification_dir, tracking_dir = temp_dirs
        
        user_id = "test_user_multi_account"
        account_id_1 = "account_1"
        account_id_2 = "account_2"
        user = MockUser(user_id)
        
        now = datetime.now()
        # Same assignment ID but different accounts
        assignment = MockLMSAssignment(
            id="asn_shared_id",
            title="Homework",
            course_code="CS101",
            course_name="Computer Science",
            assignment_no="1",
            deadline=now + timedelta(hours=48)
        )
        
        print("\n=== Testing Per-Account Tracking (Preservation) ===")
        
        with patch('app.services.assignment_notification_service.get_notification_service') as mock_get_notif_svc:
            mock_notification_service = Mock()
            mock_notification_service.send_notification.return_value = Mock(
                id="notif_multi",
                created_at=now
            )
            mock_get_notif_svc.return_value = mock_notification_service
            
            assignment_notif_service = AssignmentNotificationService(data_dir=tracking_dir)
            
            # Check for account 1
            counts1 = assignment_notif_service.check_and_notify(
                user_id=user_id,
                assignments=[assignment],
                user=user,
                account_id=account_id_1
            )
            
            print(f"Account 1 counts: {counts1}")
            assert counts1['new'] == 1, "Account 1 should create new notification"
            
            # Reset mock
            mock_notification_service.send_notification.reset_mock()
            
            # Check for account 2 (same assignment ID, different account)
            counts2 = assignment_notif_service.check_and_notify(
                user_id=user_id,
                assignments=[assignment],
                user=user,
                account_id=account_id_2
            )
            
            print(f"Account 2 counts: {counts2}")
            
            # PRESERVATION: Account 2 should also create notification (independent tracking)
            assert counts2['new'] == 1, \
                "Account 2 should create new notification (independent tracking)"
            
            print("✓ Per-account tracking works correctly (preserved behavior)")



class TestBug2PreservationPropertyBased:
    """
    **Property 2: Preservation** - Property-Based Tests
    
    Use property-based testing to verify preservation across many inputs.
    
    **Expected Outcome**: Tests PASS on unfixed code
    """
    
    @pytest.fixture
    def temp_dirs(self):
        notification_dir = tempfile.mkdtemp()
        tracking_dir = tempfile.mkdtemp()
        yield notification_dir, tracking_dir
        import shutil
        shutil.rmtree(notification_dir, ignore_errors=True)
        shutil.rmtree(tracking_dir, ignore_errors=True)
    
    @given(
        assignment_id=st.text(min_size=1, max_size=20, alphabet=st.characters(whitelist_categories=('Lu', 'Ll', 'Nd'))),
        hours_until_due=st.integers(min_value=1, max_value=168)  # 1 hour to 7 days
    )
    @settings(
        max_examples=20, 
        phases=[Phase.generate, Phase.target],
        suppress_health_check=[HealthCheck.function_scoped_fixture],
        deadline=None  # Disable deadline check for this test
    )
    def test_property_notification_created_for_any_new_assignment(self, temp_dirs, assignment_id, hours_until_due):
        """
        **Property 2: Preservation** - Universal Notification Creation
        
        Property: For ANY new assignment with a valid deadline,
        check_and_notify() creates an in-app notification.
        
        This property should hold on UNFIXED code.
        
        **Validates: Requirements 3.2**
        """
        notification_dir, tracking_dir = temp_dirs
        
        # Skip if assignment_id is empty after stripping
        assume(assignment_id.strip() != "")
        
        # Make assignment_id unique by adding timestamp to avoid tracking conflicts
        import time
        unique_id = f"{assignment_id}_{int(time.time() * 1000000)}"
        
        user_id = f"test_user_prop_{unique_id[:10]}"
        account_id = f"test_account_prop_{unique_id[:10]}"
        user = MockUser(user_id)
        
        now = datetime.now()
        assignment = MockLMSAssignment(
            id=unique_id,
            title=f"Assignment {assignment_id}",
            course_code="TEST101",
            course_name="Test Course",
            assignment_no="1",
            deadline=now + timedelta(hours=hours_until_due)
        )
        
        with patch('app.services.assignment_notification_service.get_notification_service') as mock_get_notif_svc:
            mock_notification_service = Mock()
            mock_notification_service.send_notification.return_value = Mock(
                id=f"notif_{unique_id}",
                created_at=now
            )
            mock_get_notif_svc.return_value = mock_notification_service
            
            assignment_notif_service = AssignmentNotificationService(data_dir=tracking_dir)
            
            counts = assignment_notif_service.check_and_notify(
                user_id=user_id,
                assignments=[assignment],
                user=user,
                account_id=account_id,
                send_push=False  # Disable FCM in test to avoid errors
            )
            
            # PROPERTY: For any new assignment, at least one notification should be created
            total_notifications = sum(counts.values())
            assert total_notifications >= 1, \
                f"Expected at least 1 notification for new assignment, got {total_notifications}"
            
            # PROPERTY: send_notification should be called
            assert mock_notification_service.send_notification.called, \
                "send_notification should be called for any new assignment"
    
    @given(
        num_assignments=st.integers(min_value=1, max_value=10)
    )
    @settings(
        max_examples=10, 
        phases=[Phase.generate, Phase.target],
        suppress_health_check=[HealthCheck.function_scoped_fixture]
    )
    def test_property_multiple_assignments_create_multiple_notifications(self, temp_dirs, num_assignments):
        """
        **Property 2: Preservation** - Multiple Assignments
        
        Property: For N new assignments, check_and_notify() creates N notifications.
        
        This property should hold on UNFIXED code.
        
        **Validates: Requirements 3.2, 3.7**
        """
        notification_dir, tracking_dir = temp_dirs
        
        user_id = f"test_user_multi_{num_assignments}"
        account_id = f"test_account_multi_{num_assignments}"
        user = MockUser(user_id)
        
        now = datetime.now()
        assignments = []
        for i in range(num_assignments):
            assignments.append(MockLMSAssignment(
                id=f"asn_multi_{i}",
                title=f"Assignment {i}",
                course_code="TEST101",
                course_name="Test Course",
                assignment_no=str(i),
                deadline=now + timedelta(hours=48 + i)
            ))
        
        with patch('app.services.assignment_notification_service.get_notification_service') as mock_get_notif_svc:
            mock_notification_service = Mock()
            mock_notification_service.send_notification.return_value = Mock(
                id="notif_multi",
                created_at=now
            )
            mock_get_notif_svc.return_value = mock_notification_service
            
            assignment_notif_service = AssignmentNotificationService(data_dir=tracking_dir)
            
            counts = assignment_notif_service.check_and_notify(
                user_id=user_id,
                assignments=assignments,
                user=user,
                account_id=account_id,
                send_push=False  # Disable FCM in test to avoid errors
            )
            
            # PROPERTY: Total notifications should equal number of assignments
            total_notifications = sum(counts.values())
            assert total_notifications >= num_assignments, \
                f"Expected at least {num_assignments} notifications, got {total_notifications}"
            
            # PROPERTY: send_notification should be called at least num_assignments times
            assert mock_notification_service.send_notification.call_count >= num_assignments, \
                f"Expected at least {num_assignments} calls to send_notification, got {mock_notification_service.send_notification.call_count}"


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
