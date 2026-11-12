#!/usr/bin/env python3
"""
Bug #3 Preservation Tests: Notification Frequency Bug

**IMPORTANT**: Follow observation-first methodology.
These tests capture the CURRENT behavior on UNFIXED code that must be preserved.

**EXPECTED OUTCOME on UNFIXED code**: Tests PASS (confirms baseline behavior to preserve)

**Validates: Requirements 3.5, 3.6, 3.7**
"""

import pytest
import tempfile
import json
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch, Mock

from app.services.assignment_notification_service import AssignmentNotificationService
from app.models.notification_models import NotificationType


class MockLMSAssignment:
    """Mock LMS Assignment for testing"""
    def __init__(self, id: str, title: str, course_code: str, course_name: str, 
                 assignment_no: str, deadline: datetime):
        self.id = id
        self.title = title
        self.course_code = course_code
        self.course_name = course_name
        self.assignment_no = assignment_no
        self.deadline = deadline if isinstance(deadline, datetime) else datetime.fromisoformat(deadline)


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


class TestBug3PreservationAssignmentTracking:
    """
    **Property 2: Preservation** - Assignment Tracking and Deduplication
    
    Tests that assignment tracking mechanisms (known_assignments, notified_deadlines)
    continue to work correctly after the fix.
    
    **Expected Outcome on UNFIXED code**: Tests PASS
    """
    
    @pytest.fixture
    def temp_tracking_dir(self):
        """Create a temporary directory for tracking storage"""
        temp_dir = tempfile.mkdtemp()
        yield temp_dir
        # Cleanup
        import shutil
        shutil.rmtree(temp_dir, ignore_errors=True)
    
    def test_known_assignments_tracking_prevents_duplicates(self, temp_tracking_dir):
        """
        Test that known_assignments tracking prevents duplicate notifications
        for the same assignment ID.
        
        This is existing behavior that must be preserved.
        
        **Validates: Requirements 3.5**
        """
        user_id = "test_user_known_tracking"
        account_id = "test_account_known_tracking"
        user = MockUser(user_id)
        
        now = datetime.now()
        assignment = MockLMSAssignment(
            id="asn_track_001",
            title="Test Assignment",
            course_code="CS101",
            course_name="Computer Science",
            assignment_no="1",
            deadline=now + timedelta(hours=48)
        )
        
        # Mock notification service
        with patch('app.services.assignment_notification_service.get_notification_service') as mock_get_notif_svc:
            mock_notification_service = Mock()
            mock_notification_service.send_notification.return_value = Mock(
                id="notif_001",
                created_at=now
            )
            mock_get_notif_svc.return_value = mock_notification_service
            
            service = AssignmentNotificationService(data_dir=temp_tracking_dir)
            
            # First check - assignment is new
            counts1 = service.check_and_notify(
                user_id=user_id,
                assignments=[assignment],
                user=user,
                account_id=account_id
            )
            
            # Verify assignment was tracked
            tracking_file = service._get_tracking_file(account_id)
            assert tracking_file.exists()
            
            with open(tracking_file, 'r') as f:
                tracking_data = json.load(f)
            
            # Verify known_assignments contains the assignment ID
            assert 'known_assignments' in tracking_data
            assert assignment.id in tracking_data['known_assignments']
            
            # Verify tracking file structure is preserved
            assert 'notified_deadlines' in tracking_data
            assert 'last_check' in tracking_data
    
    def test_notified_deadlines_tracking_prevents_duplicates(self, temp_tracking_dir):
        """
        Test that notified_deadlines tracking prevents duplicate notifications
        for the same deadline key (e.g., assignment_id_24h).
        
        This is existing behavior that must be preserved.
        
        **Validates: Requirements 3.5**
        """
        user_id = "test_user_deadline_tracking"
        account_id = "test_account_deadline_tracking"
        user = MockUser(user_id)
        
        now = datetime.now()
        # Create urgent assignment (due in 12 hours)
        assignment = MockLMSAssignment(
            id="asn_deadline_001",
            title="Urgent Assignment",
            course_code="MATH201",
            course_name="Mathematics",
            assignment_no="2",
            deadline=now + timedelta(hours=12)
        )
        
        # Mock notification service
        with patch('app.services.assignment_notification_service.get_notification_service') as mock_get_notif_svc:
            mock_notification_service = Mock()
            mock_notification_service.send_notification.return_value = Mock(
                id="notif_deadline_001",
                created_at=now
            )
            mock_get_notif_svc.return_value = mock_notification_service
            
            service = AssignmentNotificationService(data_dir=temp_tracking_dir)
            
            # First check - should notify for urgent deadline
            counts1 = service.check_and_notify(
                user_id=user_id,
                assignments=[assignment],
                user=user,
                account_id=account_id
            )
            
            # Verify deadline was tracked
            tracking_file = service._get_tracking_file(account_id)
            with open(tracking_file, 'r') as f:
                tracking_data = json.load(f)
            
            # Verify notified_deadlines contains the deadline key
            assert 'notified_deadlines' in tracking_data
            deadline_key = f"{assignment.id}_24h"
            assert deadline_key in tracking_data['notified_deadlines']
            
            # Verify tracking data is stored (can be either old string format or new dict format)
            tracking_entry = tracking_data['notified_deadlines'][deadline_key]
            if isinstance(tracking_entry, str):
                # Old format: string timestamp in ISO format
                parsed_timestamp = datetime.fromisoformat(tracking_entry)
                assert isinstance(parsed_timestamp, datetime)
            elif isinstance(tracking_entry, dict):
                # New format: dictionary with metadata
                assert 'first_notified' in tracking_entry
                assert 'last_notified' in tracking_entry
                assert 'count' in tracking_entry
                # Verify timestamps are parseable
                parsed_first = datetime.fromisoformat(tracking_entry['first_notified'])
                parsed_last = datetime.fromisoformat(tracking_entry['last_notified'])
                assert isinstance(parsed_first, datetime)
                assert isinstance(parsed_last, datetime)
                assert tracking_entry['count'] >= 1
            else:
                raise AssertionError(f"Unexpected tracking entry format: {type(tracking_entry)}")
    
    def test_tracking_data_structure_is_preserved(self, temp_tracking_dir):
        """
        Test that the tracking data structure (JSON format) is preserved correctly.
        
        **Validates: Requirements 3.5**
        """
        user_id = "test_user_structure"
        account_id = "test_account_structure"
        user = MockUser(user_id)
        
        now = datetime.now()
        assignments = [
            MockLMSAssignment(
                id="asn_struct_001",
                title="Assignment 1",
                course_code="CS101",
                course_name="Computer Science",
                assignment_no="1",
                deadline=now + timedelta(hours=48)
            ),
            MockLMSAssignment(
                id="asn_struct_002",
                title="Assignment 2",
                course_code="MATH201",
                course_name="Mathematics",
                assignment_no="2",
                deadline=now + timedelta(hours=12)
            ),
        ]
        
        # Mock notification service
        with patch('app.services.assignment_notification_service.get_notification_service') as mock_get_notif_svc:
            mock_notification_service = Mock()
            mock_notification_service.send_notification.return_value = Mock(
                id="notif_struct_001",
                created_at=now
            )
            mock_get_notif_svc.return_value = mock_notification_service
            
            service = AssignmentNotificationService(data_dir=temp_tracking_dir)
            
            # Process assignments
            service.check_and_notify(
                user_id=user_id,
                assignments=assignments,
                user=user,
                account_id=account_id
            )
            
            # Verify tracking file structure
            tracking_file = service._get_tracking_file(account_id)
            assert tracking_file.exists()
            
            with open(tracking_file, 'r') as f:
                tracking_data = json.load(f)
            
            # Verify required fields exist
            assert 'known_assignments' in tracking_data
            assert 'notified_deadlines' in tracking_data
            assert 'last_check' in tracking_data
            
            # Verify data types
            assert isinstance(tracking_data['known_assignments'], list)
            assert isinstance(tracking_data['notified_deadlines'], dict)
            assert isinstance(tracking_data['last_check'], str)
            
            # Verify known_assignments contains both assignment IDs
            assert len(tracking_data['known_assignments']) == 2
            assert "asn_struct_001" in tracking_data['known_assignments']
            assert "asn_struct_002" in tracking_data['known_assignments']
    
    def test_multiple_deadline_keys_tracked_independently(self, temp_tracking_dir):
        """
        Test that different deadline keys (24h, 3d, overdue) are tracked independently.
        
        **Validates: Requirements 3.5**
        """
        user_id = "test_user_multi_deadline"
        account_id = "test_account_multi_deadline"
        user = MockUser(user_id)
        
        now = datetime.now()
        
        # Create assignments with different urgency levels
        assignments = [
            # Due in 12 hours (will trigger 24h notification)
            MockLMSAssignment(
                id="asn_multi_001",
                title="Urgent Assignment",
                course_code="CS101",
                course_name="Computer Science",
                assignment_no="1",
                deadline=now + timedelta(hours=12)
            ),
            # Due in 48 hours (will trigger 3d notification)
            MockLMSAssignment(
                id="asn_multi_002",
                title="Normal Assignment",
                course_code="MATH201",
                course_name="Mathematics",
                assignment_no="2",
                deadline=now + timedelta(hours=48)
            ),
            # Overdue (will trigger overdue notification)
            MockLMSAssignment(
                id="asn_multi_003",
                title="Overdue Assignment",
                course_code="PHYS101",
                course_name="Physics",
                assignment_no="3",
                deadline=now - timedelta(hours=2)
            ),
        ]
        
        # Mock notification service
        with patch('app.services.assignment_notification_service.get_notification_service') as mock_get_notif_svc:
            mock_notification_service = Mock()
            mock_notification_service.send_notification.return_value = Mock(
                id="notif_multi_001",
                created_at=now
            )
            mock_get_notif_svc.return_value = mock_notification_service
            
            service = AssignmentNotificationService(data_dir=temp_tracking_dir)
            
            # Process assignments
            service.check_and_notify(
                user_id=user_id,
                assignments=assignments,
                user=user,
                account_id=account_id
            )
            
            # Verify tracking
            tracking_file = service._get_tracking_file(account_id)
            with open(tracking_file, 'r') as f:
                tracking_data = json.load(f)
            
            notified_deadlines = tracking_data['notified_deadlines']
            
            # Verify different deadline keys are tracked
            assert f"asn_multi_001_24h" in notified_deadlines
            assert f"asn_multi_002_3d" in notified_deadlines
            assert f"asn_multi_003_overdue" in notified_deadlines
            
            # Verify each has tracking data (can be either old string format or new dict format)
            for key in [f"asn_multi_001_24h", f"asn_multi_002_3d", f"asn_multi_003_overdue"]:
                tracking_entry = notified_deadlines[key]
                if isinstance(tracking_entry, str):
                    # Old format: string timestamp
                    parsed = datetime.fromisoformat(tracking_entry)
                    assert isinstance(parsed, datetime)
                elif isinstance(tracking_entry, dict):
                    # New format: dictionary with metadata
                    assert 'first_notified' in tracking_entry
                    assert 'last_notified' in tracking_entry
                    assert 'count' in tracking_entry
                else:
                    raise AssertionError(f"Unexpected tracking entry format for {key}: {type(tracking_entry)}")


class TestBug3PreservationSubmissionHandling:
    """
    **Property 2: Preservation** - Assignment Submission/Completion Handling
    
    Tests that the system correctly handles submitted/completed assignments.
    
    **Expected Outcome on UNFIXED code**: Tests PASS
    """
    
    @pytest.fixture
    def temp_tracking_dir(self):
        """Create a temporary directory for tracking storage"""
        temp_dir = tempfile.mkdtemp()
        yield temp_dir
        import shutil
        shutil.rmtree(temp_dir, ignore_errors=True)
    
    def test_submitted_assignments_not_in_list_no_notification(self, temp_tracking_dir):
        """
        Test that when an assignment is submitted/completed and removed from the list,
        no notifications are sent for it.
        
        This simulates the current behavior where submitted assignments are filtered
        out before reaching check_and_notify().
        
        **Validates: Requirements 3.6**
        """
        user_id = "test_user_submitted"
        account_id = "test_account_submitted"
        user = MockUser(user_id)
        
        now = datetime.now()
        assignment = MockLMSAssignment(
            id="asn_submit_001",
            title="Test Assignment",
            course_code="CS101",
            course_name="Computer Science",
            assignment_no="1",
            deadline=now + timedelta(hours=12)
        )
        
        # Mock notification service
        with patch('app.services.assignment_notification_service.get_notification_service') as mock_get_notif_svc:
            mock_notification_service = Mock()
            mock_notification_service.send_notification.return_value = Mock(
                id="notif_submit_001",
                created_at=now
            )
            mock_get_notif_svc.return_value = mock_notification_service
            
            service = AssignmentNotificationService(data_dir=temp_tracking_dir)
            
            # First check - assignment is active
            counts1 = service.check_and_notify(
                user_id=user_id,
                assignments=[assignment],
                user=user,
                account_id=account_id
            )
            
            # Verify notifications were sent
            assert counts1['new'] == 1
            assert counts1['due_24h'] == 1
            
            # Reset mock
            mock_notification_service.send_notification.reset_mock()
            
            # Second check - assignment is submitted (not in list)
            counts2 = service.check_and_notify(
                user_id=user_id,
                assignments=[],  # Empty list - assignment was submitted
                user=user,
                account_id=account_id
            )
            
            # Verify no notifications were sent
            assert counts2['new'] == 0
            assert counts2['due_24h'] == 0
            assert counts2['due_3d'] == 0
            assert counts2['overdue'] == 0
            assert mock_notification_service.send_notification.call_count == 0
    
    def test_empty_assignment_list_handled_gracefully(self, temp_tracking_dir):
        """
        Test that an empty assignment list is handled gracefully without errors.
        
        **Validates: Requirements 3.6**
        """
        user_id = "test_user_empty"
        account_id = "test_account_empty"
        user = MockUser(user_id)
        
        # Mock notification service
        with patch('app.services.assignment_notification_service.get_notification_service') as mock_get_notif_svc:
            mock_notification_service = Mock()
            mock_get_notif_svc.return_value = mock_notification_service
            
            service = AssignmentNotificationService(data_dir=temp_tracking_dir)
            
            # Check with empty list
            counts = service.check_and_notify(
                user_id=user_id,
                assignments=[],
                user=user,
                account_id=account_id
            )
            
            # Verify returns zero counts
            assert counts == {'new': 0, 'due_24h': 0, 'due_3d': 0, 'overdue': 0}
            assert mock_notification_service.send_notification.call_count == 0


class TestBug3PreservationMultiUserProcessing:
    """
    **Property 2: Preservation** - Multiple Users and Accounts Processing
    
    Tests that the system correctly processes multiple users and accounts
    with proper isolation.
    
    **Expected Outcome on UNFIXED code**: Tests PASS
    """
    
    @pytest.fixture
    def temp_tracking_dir(self):
        """Create a temporary directory for tracking storage"""
        temp_dir = tempfile.mkdtemp()
        yield temp_dir
        import shutil
        shutil.rmtree(temp_dir, ignore_errors=True)
    
    def test_multiple_accounts_tracked_independently(self, temp_tracking_dir):
        """
        Test that different accounts have independent tracking data.
        
        **Validates: Requirements 3.7**
        """
        user_id = "test_user_multi_account"
        account1_id = "account_001"
        account2_id = "account_002"
        user = MockUser(user_id)
        
        now = datetime.now()
        
        # Same assignment for both accounts
        assignment = MockLMSAssignment(
            id="asn_shared_001",
            title="Shared Assignment",
            course_code="CS101",
            course_name="Computer Science",
            assignment_no="1",
            deadline=now + timedelta(hours=48)
        )
        
        # Mock notification service
        with patch('app.services.assignment_notification_service.get_notification_service') as mock_get_notif_svc:
            mock_notification_service = Mock()
            mock_notification_service.send_notification.return_value = Mock(
                id="notif_shared_001",
                created_at=now
            )
            mock_get_notif_svc.return_value = mock_notification_service
            
            service = AssignmentNotificationService(data_dir=temp_tracking_dir)
            
            # Process for account 1
            counts1 = service.check_and_notify(
                user_id=user_id,
                assignments=[assignment],
                user=user,
                account_id=account1_id
            )
            
            # Process for account 2
            counts2 = service.check_and_notify(
                user_id=user_id,
                assignments=[assignment],
                user=user,
                account_id=account2_id
            )
            
            # Both should send notifications (independent tracking)
            assert counts1['new'] == 1
            assert counts2['new'] == 1
            
            # Verify separate tracking files exist
            tracking_file1 = service._get_tracking_file(account1_id)
            tracking_file2 = service._get_tracking_file(account2_id)
            
            assert tracking_file1.exists()
            assert tracking_file2.exists()
            assert tracking_file1 != tracking_file2
            
            # Verify both have the assignment tracked
            with open(tracking_file1, 'r') as f:
                data1 = json.load(f)
            with open(tracking_file2, 'r') as f:
                data2 = json.load(f)
            
            assert assignment.id in data1['known_assignments']
            assert assignment.id in data2['known_assignments']
    
    def test_multiple_users_processed_correctly(self, temp_tracking_dir):
        """
        Test that different users can be processed with the same account ID.
        
        **Validates: Requirements 3.7**
        """
        user1_id = "user_001"
        user2_id = "user_002"
        account_id = "shared_account"
        user1 = MockUser(user1_id)
        user2 = MockUser(user2_id)
        
        now = datetime.now()
        assignment = MockLMSAssignment(
            id="asn_multi_user_001",
            title="Test Assignment",
            course_code="CS101",
            course_name="Computer Science",
            assignment_no="1",
            deadline=now + timedelta(hours=48)
        )
        
        # Mock notification service
        with patch('app.services.assignment_notification_service.get_notification_service') as mock_get_notif_svc:
            mock_notification_service = Mock()
            mock_notification_service.send_notification.return_value = Mock(
                id="notif_multi_user_001",
                created_at=now
            )
            mock_get_notif_svc.return_value = mock_notification_service
            
            service = AssignmentNotificationService(data_dir=temp_tracking_dir)
            
            # Process for user 1
            counts1 = service.check_and_notify(
                user_id=user1_id,
                assignments=[assignment],
                user=user1,
                account_id=account_id
            )
            
            # Verify notification was sent to user 1
            assert counts1['new'] == 1
            assert mock_notification_service.send_notification.call_args[1]['user_id'] == user1_id
            
            # Reset mock
            mock_notification_service.send_notification.reset_mock()
            
            # Process for user 2 (same account, but assignment already tracked)
            counts2 = service.check_and_notify(
                user_id=user2_id,
                assignments=[assignment],
                user=user2,
                account_id=account_id
            )
            
            # No notification should be sent (assignment already tracked for this account)
            assert counts2['new'] == 0
    
    def test_error_handling_continues_processing_other_assignments(self, temp_tracking_dir):
        """
        Test that if one assignment causes an error, other assignments are still processed.
        
        **Validates: Requirements 3.7**
        """
        user_id = "test_user_error_handling"
        account_id = "test_account_error_handling"
        user = MockUser(user_id)
        
        now = datetime.now()
        
        # Create assignments - one with invalid data, others valid
        assignments = [
            MockLMSAssignment(
                id="asn_error_001",
                title="Valid Assignment 1",
                course_code="CS101",
                course_name="Computer Science",
                assignment_no="1",
                deadline=now + timedelta(hours=48)
            ),
            # This will be mocked to cause an error
            Mock(
                id="asn_error_002",
                title="Invalid Assignment",
                course_code="ERROR",
                course_name="Error Course",
                assignment_no="2",
                deadline=None  # Invalid deadline
            ),
            MockLMSAssignment(
                id="asn_error_003",
                title="Valid Assignment 2",
                course_code="MATH201",
                course_name="Mathematics",
                assignment_no="3",
                deadline=now + timedelta(hours=12)
            ),
        ]
        
        # Mock notification service
        with patch('app.services.assignment_notification_service.get_notification_service') as mock_get_notif_svc:
            mock_notification_service = Mock()
            mock_notification_service.send_notification.return_value = Mock(
                id="notif_error_001",
                created_at=now
            )
            mock_get_notif_svc.return_value = mock_notification_service
            
            service = AssignmentNotificationService(data_dir=temp_tracking_dir)
            
            # Process assignments - should handle error gracefully
            counts = service.check_and_notify(
                user_id=user_id,
                assignments=assignments,
                user=user,
                account_id=account_id
            )
            
            # Verify valid assignments were processed
            # At least 2 new notifications should be sent (for the 2 valid assignments)
            assert counts['new'] >= 2
            
            # Verify tracking file was created and contains valid assignments
            tracking_file = service._get_tracking_file(account_id)
            assert tracking_file.exists()
            
            with open(tracking_file, 'r') as f:
                tracking_data = json.load(f)
            
            # Valid assignments should be tracked
            assert "asn_error_001" in tracking_data['known_assignments']
            assert "asn_error_003" in tracking_data['known_assignments']


class TestBug3PreservationNotificationPreferences:
    """
    **Property 2: Preservation** - User Notification Preferences
    
    Tests that user notification preferences are respected.
    
    **Expected Outcome on UNFIXED code**: Tests PASS
    """
    
    @pytest.fixture
    def temp_tracking_dir(self):
        """Create a temporary directory for tracking storage"""
        temp_dir = tempfile.mkdtemp()
        yield temp_dir
        import shutil
        shutil.rmtree(temp_dir, ignore_errors=True)
    
    def test_notification_preferences_respected(self, temp_tracking_dir):
        """
        Test that user notification preferences are checked and respected.
        
        **Validates: Requirements 3.7**
        """
        user_id = "test_user_prefs"
        account_id = "test_account_prefs"
        
        # User with specific preferences disabled
        user = MockUser(user_id)
        user.notification_preferences = {
            'new_assignment': False,  # Disabled
            'due_24h': True,
            'due_3d': True,
            'overdue_warning': True
        }
        
        now = datetime.now()
        assignment = MockLMSAssignment(
            id="asn_prefs_001",
            title="Test Assignment",
            course_code="CS101",
            course_name="Computer Science",
            assignment_no="1",
            deadline=now + timedelta(hours=12)
        )
        
        # Mock notification service
        with patch('app.services.assignment_notification_service.get_notification_service') as mock_get_notif_svc:
            mock_notification_service = Mock()
            mock_notification_service.send_notification.return_value = Mock(
                id="notif_prefs_001",
                created_at=now
            )
            mock_get_notif_svc.return_value = mock_notification_service
            
            service = AssignmentNotificationService(data_dir=temp_tracking_dir)
            
            # Process assignment
            counts = service.check_and_notify(
                user_id=user_id,
                assignments=[assignment],
                user=user,
                account_id=account_id
            )
            
            # Verify new_assignment notification was NOT sent (preference disabled)
            assert counts['new'] == 0
            
            # Verify due_24h notification WAS sent (preference enabled)
            assert counts['due_24h'] == 1
    
    def test_no_user_preferences_defaults_to_sending(self, temp_tracking_dir):
        """
        Test that when user has no preferences set, notifications are sent by default.
        
        **Validates: Requirements 3.7**
        """
        user_id = "test_user_no_prefs"
        account_id = "test_account_no_prefs"
        
        # User with no preferences
        user = Mock()
        user.id = user_id
        user.notification_preferences = None
        
        now = datetime.now()
        assignment = MockLMSAssignment(
            id="asn_no_prefs_001",
            title="Test Assignment",
            course_code="CS101",
            course_name="Computer Science",
            assignment_no="1",
            deadline=now + timedelta(hours=48)
        )
        
        # Mock notification service
        with patch('app.services.assignment_notification_service.get_notification_service') as mock_get_notif_svc:
            mock_notification_service = Mock()
            mock_notification_service.send_notification.return_value = Mock(
                id="notif_no_prefs_001",
                created_at=now
            )
            mock_get_notif_svc.return_value = mock_notification_service
            
            service = AssignmentNotificationService(data_dir=temp_tracking_dir)
            
            # Process assignment
            counts = service.check_and_notify(
                user_id=user_id,
                assignments=[assignment],
                user=user,
                account_id=account_id
            )
            
            # Verify notifications were sent (default behavior)
            assert counts['new'] == 1


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
