#!/usr/bin/env python3
"""
Bug #1 Preservation Tests: Timestamp Display Bug

**IMPORTANT**: Follow observation-first methodology.
These tests capture the CURRENT behavior on UNFIXED code that must be preserved.

**EXPECTED OUTCOME on UNFIXED code**: Tests PASS (confirms baseline behavior to preserve)

**Validates: Requirements 3.1, 3.4**
"""

import pytest
import time
import json
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch, Mock

from app.models.notification_models import Notification, NotificationType
from app.services.notification_service import NotificationService
from app.utils.lms_notifications import (
    LMSNotificationManager, 
    NotificationType as LMSNotificationType,
    NotificationPriority
)


class TestBug1PreservationNotificationStorage:
    """
    **Property 2: Preservation** - Notification Storage and Retrieval
    
    Tests that notification creation, storage, and retrieval operations
    continue to work correctly after the fix.
    
    **Expected Outcome on UNFIXED code**: Tests PASS
    """
    
    @pytest.fixture
    def temp_notification_dir(self):
        """Create a temporary directory for notification storage"""
        temp_dir = tempfile.mkdtemp()
        yield temp_dir
        # Cleanup
        import shutil
        shutil.rmtree(temp_dir, ignore_errors=True)
    
    def test_notification_creation_stores_valid_timestamp(self, temp_notification_dir):
        """
        Test that when a notification is created with a valid created_at timestamp,
        it is stored correctly in the JSON file.
        
        **Validates: Requirements 3.1**
        """
        # Mock FCM service
        with patch('app.services.fcm_service.get_fcm_service') as mock_fcm:
            mock_fcm_instance = Mock()
            mock_fcm_instance.get_token.return_value = None
            mock_fcm.return_value = mock_fcm_instance
            
            service = NotificationService(data_dir=temp_notification_dir)
            user_id = "test_user_storage"
            
            # Create a notification
            before_creation = datetime.now()
            notif = service.send_notification(
                user_id=user_id,
                notification_type=NotificationType.ASSIGNMENT_NEW,
                title="Test Assignment",
                message="Test message"
            )
            after_creation = datetime.now()
            
            # Verify notification was created
            assert notif is not None
            assert notif.created_at is not None
            assert isinstance(notif.created_at, datetime)
            
            # Verify timestamp is reasonable (within the time window)
            assert before_creation <= notif.created_at <= after_creation
            
            # Verify notification is stored in JSON
            user_file = Path(temp_notification_dir) / f"{user_id}.json"
            assert user_file.exists()
            
            # Read the JSON file directly
            with open(user_file, 'r') as f:
                data = json.load(f)
            
            assert len(data) == 1
            assert 'created_at' in data[0]
            assert data[0]['created_at'] is not None
            
            # Verify the timestamp is in ISO format
            stored_timestamp = data[0]['created_at']
            parsed_timestamp = datetime.fromisoformat(stored_timestamp)
            assert isinstance(parsed_timestamp, datetime)
    
    def test_notification_retrieval_returns_stored_timestamp(self, temp_notification_dir):
        """
        Test that when notifications are retrieved from JSON storage,
        the created_at timestamp is correctly deserialized.
        
        **Validates: Requirements 3.1**
        """
        # Mock FCM service
        with patch('app.services.fcm_service.get_fcm_service') as mock_fcm:
            mock_fcm_instance = Mock()
            mock_fcm_instance.get_token.return_value = None
            mock_fcm.return_value = mock_fcm_instance
            
            service = NotificationService(data_dir=temp_notification_dir)
            user_id = "test_user_retrieval"
            
            # Create a notification
            original_notif = service.send_notification(
                user_id=user_id,
                notification_type=NotificationType.ASSIGNMENT_DUE_24H,
                title="Test Assignment",
                message="Due soon"
            )
            
            original_timestamp = original_notif.created_at
            
            # Clear cache to force reload from JSON
            service._cache.clear()
            
            # Retrieve notifications
            retrieved_notifs = service.get_notifications(user_id, limit=10)
            
            assert len(retrieved_notifs) == 1
            retrieved_notif = retrieved_notifs[0]
            
            # Verify the timestamp was correctly deserialized
            assert retrieved_notif.created_at is not None
            assert isinstance(retrieved_notif.created_at, datetime)
            
            # Verify the timestamp matches the original (within microsecond precision)
            # Note: ISO format may lose some precision, so we check within 1 second
            time_diff = abs((retrieved_notif.created_at - original_timestamp).total_seconds())
            assert time_diff < 1.0, f"Timestamp mismatch: {time_diff}s difference"
    
    def test_multiple_notifications_stored_independently(self, temp_notification_dir):
        """
        Test that multiple notifications are stored independently in JSON,
        each with their own data fields.
        
        **Validates: Requirements 3.1**
        """
        # Mock FCM service
        with patch('app.services.fcm_service.get_fcm_service') as mock_fcm:
            mock_fcm_instance = Mock()
            mock_fcm_instance.get_token.return_value = None
            mock_fcm.return_value = mock_fcm_instance
            
            service = NotificationService(data_dir=temp_notification_dir)
            user_id = "test_user_multiple"
            
            # Create 3 notifications with different data
            notifications_data = [
                ("Assignment 1", "Message 1", NotificationType.ASSIGNMENT_NEW),
                ("Assignment 2", "Message 2", NotificationType.ASSIGNMENT_DUE_24H),
                ("Assignment 3", "Message 3", NotificationType.ASSIGNMENT_OVERDUE),
            ]
            
            created_notifs = []
            for title, message, notif_type in notifications_data:
                notif = service.send_notification(
                    user_id=user_id,
                    notification_type=notif_type,
                    title=title,
                    message=message
                )
                created_notifs.append(notif)
            
            # Read JSON file directly
            user_file = Path(temp_notification_dir) / f"{user_id}.json"
            with open(user_file, 'r') as f:
                data = json.load(f)
            
            assert len(data) == 3
            
            # Verify each notification has its own independent data
            for i, notif_data in enumerate(data):
                assert notif_data['title'] == notifications_data[i][0]
                assert notif_data['message'] == notifications_data[i][1]
                assert notif_data['type'] == notifications_data[i][2].value
                assert 'created_at' in notif_data
                assert notif_data['created_at'] is not None
    
    def test_notification_json_serialization_roundtrip(self, temp_notification_dir):
        """
        Test that notification data survives JSON serialization/deserialization roundtrip.
        
        **Validates: Requirements 3.1**
        """
        # Mock FCM service
        with patch('app.services.fcm_service.get_fcm_service') as mock_fcm:
            mock_fcm_instance = Mock()
            mock_fcm_instance.get_token.return_value = None
            mock_fcm.return_value = mock_fcm_instance
            
            service = NotificationService(data_dir=temp_notification_dir)
            user_id = "test_user_roundtrip"
            
            # Create notification with additional data
            original_notif = service.send_notification(
                user_id=user_id,
                notification_type=NotificationType.ASSIGNMENT_NEW,
                title="Test Assignment",
                message="Test message",
                data={'assignment_id': '12345', 'course': 'CS101'}
            )
            
            # Store original values
            original_id = original_notif.id
            original_title = original_notif.title
            original_message = original_notif.message
            original_type = original_notif.notification_type
            original_data = original_notif.data
            
            # Clear cache and reload
            service._cache.clear()
            retrieved_notifs = service.get_notifications(user_id, limit=10)
            
            assert len(retrieved_notifs) == 1
            retrieved_notif = retrieved_notifs[0]
            
            # Verify all fields survived the roundtrip
            assert retrieved_notif.id == original_id
            assert retrieved_notif.title == original_title
            assert retrieved_notif.message == original_message
            assert retrieved_notif.notification_type == original_type
            assert retrieved_notif.data == original_data
            assert retrieved_notif.read == False
            assert retrieved_notif.read_at is None


class TestBug1PreservationNotificationUI:
    """
    **Property 2: Preservation** - Notification Bell Display
    
    Tests that notification display functionality (icons, categories, formatting)
    continues to work correctly after the fix.
    
    **Expected Outcome on UNFIXED code**: Tests PASS
    """
    
    @pytest.fixture
    def temp_notification_dir(self):
        """Create a temporary directory for notification storage"""
        temp_dir = tempfile.mkdtemp()
        yield temp_dir
        import shutil
        shutil.rmtree(temp_dir, ignore_errors=True)
    
    def test_notification_icon_property_works(self, temp_notification_dir):
        """
        Test that notification icon property returns correct icons for each type.
        
        **Validates: Requirements 3.4**
        """
        # Mock FCM service
        with patch('app.services.fcm_service.get_fcm_service') as mock_fcm:
            mock_fcm_instance = Mock()
            mock_fcm_instance.get_token.return_value = None
            mock_fcm.return_value = mock_fcm_instance
            
            service = NotificationService(data_dir=temp_notification_dir)
            user_id = "test_user_icons"
            
            # Test different notification types and their icons
            test_cases = [
                (NotificationType.ASSIGNMENT_NEW, "📝"),
                (NotificationType.ASSIGNMENT_DUE_24H, "⏰"),
                (NotificationType.ASSIGNMENT_OVERDUE, "🚨"),
                (NotificationType.ATTENDANCE_ABSENT_SCHEDULED, "❌"),
            ]
            
            for notif_type, expected_icon in test_cases:
                notif = service.send_notification(
                    user_id=user_id,
                    notification_type=notif_type,
                    title="Test",
                    message="Test message"
                )
                
                assert notif.icon == expected_icon
    
    def test_notification_category_property_works(self, temp_notification_dir):
        """
        Test that notification category property correctly categorizes notifications.
        
        **Validates: Requirements 3.4**
        """
        # Mock FCM service
        with patch('app.services.fcm_service.get_fcm_service') as mock_fcm:
            mock_fcm_instance = Mock()
            mock_fcm_instance.get_token.return_value = None
            mock_fcm.return_value = mock_fcm_instance
            
            service = NotificationService(data_dir=temp_notification_dir)
            user_id = "test_user_categories"
            
            # Test different notification types and their categories
            test_cases = [
                (NotificationType.ASSIGNMENT_NEW, "assignment"),
                (NotificationType.ASSIGNMENT_DUE_24H, "assignment"),
                (NotificationType.ATTENDANCE_ABSENT_SCHEDULED, "attendance"),
                (NotificationType.SYSTEM_INFO, "system"),
            ]
            
            for notif_type, expected_category in test_cases:
                notif = service.send_notification(
                    user_id=user_id,
                    notification_type=notif_type,
                    title="Test",
                    message="Test message"
                )
                
                assert notif.category == expected_category
    
    def test_notification_to_dict_formatting(self, temp_notification_dir):
        """
        Test that notification to_dict() method produces properly formatted output
        for API responses.
        
        **Validates: Requirements 3.4**
        """
        # Mock FCM service
        with patch('app.services.fcm_service.get_fcm_service') as mock_fcm:
            mock_fcm_instance = Mock()
            mock_fcm_instance.get_token.return_value = None
            mock_fcm.return_value = mock_fcm_instance
            
            service = NotificationService(data_dir=temp_notification_dir)
            user_id = "test_user_formatting"
            
            notif = service.send_notification(
                user_id=user_id,
                notification_type=NotificationType.ASSIGNMENT_NEW,
                title="Test Assignment",
                message="Test message",
                data={'key': 'value'}
            )
            
            # Convert to dict
            notif_dict = notif.to_dict()
            
            # Verify all required fields are present
            assert 'id' in notif_dict
            assert 'user_id' in notif_dict
            assert 'type' in notif_dict
            assert 'title' in notif_dict
            assert 'message' in notif_dict
            assert 'created_at' in notif_dict
            assert 'read' in notif_dict
            assert 'read_at' in notif_dict
            assert 'data' in notif_dict
            
            # Verify field values
            assert notif_dict['id'] == notif.id
            assert notif_dict['user_id'] == user_id
            assert notif_dict['type'] == NotificationType.ASSIGNMENT_NEW.value
            assert notif_dict['title'] == "Test Assignment"
            assert notif_dict['message'] == "Test message"
            assert notif_dict['read'] == False
            assert notif_dict['read_at'] is None
            assert notif_dict['data'] == {'key': 'value'}
            
            # Verify created_at is in ISO format string
            assert isinstance(notif_dict['created_at'], str)
            # Should be parseable as ISO format
            datetime.fromisoformat(notif_dict['created_at'])
    
    def test_notification_filtering_by_category(self, temp_notification_dir):
        """
        Test that get_notifications() correctly filters by category.
        
        **Validates: Requirements 3.4**
        """
        # Mock FCM service
        with patch('app.services.fcm_service.get_fcm_service') as mock_fcm:
            mock_fcm_instance = Mock()
            mock_fcm_instance.get_token.return_value = None
            mock_fcm.return_value = mock_fcm_instance
            
            service = NotificationService(data_dir=temp_notification_dir)
            user_id = "test_user_filter"
            
            # Create notifications of different categories
            service.send_notification(
                user_id=user_id,
                notification_type=NotificationType.ASSIGNMENT_NEW,
                title="Assignment 1",
                message="Message 1"
            )
            service.send_notification(
                user_id=user_id,
                notification_type=NotificationType.ASSIGNMENT_DUE_24H,
                title="Assignment 2",
                message="Message 2"
            )
            service.send_notification(
                user_id=user_id,
                notification_type=NotificationType.ATTENDANCE_ABSENT_SCHEDULED,
                title="Attendance 1",
                message="Message 3"
            )
            
            # Filter by assignment category
            assignment_notifs = service.get_notifications(user_id, category="assignment")
            assert len(assignment_notifs) == 2
            assert all(n.category == "assignment" for n in assignment_notifs)
            
            # Filter by attendance category
            attendance_notifs = service.get_notifications(user_id, category="attendance")
            assert len(attendance_notifs) == 1
            assert all(n.category == "attendance" for n in attendance_notifs)
    
    def test_notification_sorting_by_creation_date(self, temp_notification_dir):
        """
        Test that get_notifications() returns notifications sorted by creation date
        (newest first).
        
        **Validates: Requirements 3.4**
        """
        # Mock FCM service
        with patch('app.services.fcm_service.get_fcm_service') as mock_fcm:
            mock_fcm_instance = Mock()
            mock_fcm_instance.get_token.return_value = None
            mock_fcm.return_value = mock_fcm_instance
            
            service = NotificationService(data_dir=temp_notification_dir)
            user_id = "test_user_sorting"
            
            # Create notifications with small delays
            notif1 = service.send_notification(
                user_id=user_id,
                notification_type=NotificationType.ASSIGNMENT_NEW,
                title="First",
                message="Message 1"
            )
            time.sleep(0.1)
            
            notif2 = service.send_notification(
                user_id=user_id,
                notification_type=NotificationType.ASSIGNMENT_DUE_24H,
                title="Second",
                message="Message 2"
            )
            time.sleep(0.1)
            
            notif3 = service.send_notification(
                user_id=user_id,
                notification_type=NotificationType.ASSIGNMENT_OVERDUE,
                title="Third",
                message="Message 3"
            )
            
            # Retrieve notifications
            retrieved = service.get_notifications(user_id, limit=10)
            
            assert len(retrieved) == 3
            
            # Verify they are sorted newest first
            assert retrieved[0].title == "Third"
            assert retrieved[1].title == "Second"
            assert retrieved[2].title == "First"
            
            # Verify timestamps are in descending order
            assert retrieved[0].created_at >= retrieved[1].created_at
            assert retrieved[1].created_at >= retrieved[2].created_at


class TestBug1PreservationLMSNotifications:
    """
    **Property 2: Preservation** - LMS Notification System
    
    Tests that the in-memory LMS notification system continues to work correctly.
    
    **Expected Outcome on UNFIXED code**: Tests PASS
    """
    
    def test_lms_notification_creation_stores_timestamp(self):
        """
        Test that LMS notifications are created with valid timestamps.
        
        **Validates: Requirements 3.1**
        """
        manager = LMSNotificationManager()
        account_id = "test_account_lms"
        
        before_creation = datetime.now()
        notif = manager.create_notification(
            account_id=account_id,
            notification_type=LMSNotificationType.INFO,
            title="Test Notification",
            message="Test message"
        )
        after_creation = datetime.now()
        
        assert notif is not None
        assert notif.created_at is not None
        assert isinstance(notif.created_at, datetime)
        assert before_creation <= notif.created_at <= after_creation
    
    def test_lms_notification_retrieval_works(self):
        """
        Test that LMS notifications can be retrieved after creation.
        
        **Validates: Requirements 3.1**
        """
        manager = LMSNotificationManager()
        account_id = "test_account_retrieval"
        
        # Create notification
        created_notif = manager.create_notification(
            account_id=account_id,
            notification_type=LMSNotificationType.INFO,
            title="Test",
            message="Test message"
        )
        
        # Retrieve notifications
        retrieved = manager.get_notifications(account_id)
        
        assert len(retrieved) == 1
        assert retrieved[0].id == created_notif.id
        assert retrieved[0].title == "Test"
        assert retrieved[0].message == "Test message"
    
    def test_lms_notification_to_dict_includes_timestamp(self):
        """
        Test that LMS notification to_dict() includes created_at field.
        
        **Validates: Requirements 3.4**
        """
        manager = LMSNotificationManager()
        account_id = "test_account_dict"
        
        notif = manager.create_notification(
            account_id=account_id,
            notification_type=LMSNotificationType.INFO,
            title="Test",
            message="Test message"
        )
        
        notif_dict = notif.to_dict()
        
        assert 'created_at' in notif_dict
        assert notif_dict['created_at'] is not None
        assert isinstance(notif_dict['created_at'], str)
        
        # Should be parseable as ISO format
        datetime.fromisoformat(notif_dict['created_at'])
    
    def test_lms_notification_dismiss_works(self):
        """
        Test that LMS notification dismissal continues to work.
        
        **Validates: Requirements 3.4**
        """
        manager = LMSNotificationManager()
        account_id = "test_account_dismiss"
        
        notif = manager.create_notification(
            account_id=account_id,
            notification_type=LMSNotificationType.INFO,
            title="Test",
            message="Test message"
        )
        
        # Dismiss notification
        result = manager.dismiss_notification(account_id, notif.id)
        assert result == True
        
        # Verify it's no longer in active notifications
        active_notifs = manager.get_notifications(account_id, include_expired=False)
        assert len(active_notifs) == 0
    
    def test_lms_notification_clear_works(self):
        """
        Test that clearing LMS notifications continues to work.
        
        **Validates: Requirements 3.4**
        """
        manager = LMSNotificationManager()
        account_id = "test_account_clear"
        
        # Create multiple notifications
        manager.create_notification(
            account_id=account_id,
            notification_type=LMSNotificationType.INFO,
            title="Test 1",
            message="Message 1"
        )
        manager.create_notification(
            account_id=account_id,
            notification_type=LMSNotificationType.WARNING,
            title="Test 2",
            message="Message 2"
        )
        
        # Clear all notifications
        manager.clear_notifications(account_id)
        
        # Verify all are cleared
        notifs = manager.get_notifications(account_id)
        assert len(notifs) == 0


class TestBug1PreservationNotificationOperations:
    """
    **Property 2: Preservation** - Notification Operations
    
    Tests that notification operations (mark as read, delete, etc.) continue to work.
    
    **Expected Outcome on UNFIXED code**: Tests PASS
    """
    
    @pytest.fixture
    def temp_notification_dir(self):
        """Create a temporary directory for notification storage"""
        temp_dir = tempfile.mkdtemp()
        yield temp_dir
        import shutil
        shutil.rmtree(temp_dir, ignore_errors=True)
    
    def test_mark_notification_as_read(self, temp_notification_dir):
        """
        Test that marking a notification as read continues to work.
        
        **Validates: Requirements 3.4**
        """
        # Mock FCM service
        with patch('app.services.fcm_service.get_fcm_service') as mock_fcm:
            mock_fcm_instance = Mock()
            mock_fcm_instance.get_token.return_value = None
            mock_fcm.return_value = mock_fcm_instance
            
            service = NotificationService(data_dir=temp_notification_dir)
            user_id = "test_user_read"
            
            notif = service.send_notification(
                user_id=user_id,
                notification_type=NotificationType.ASSIGNMENT_NEW,
                title="Test",
                message="Test message"
            )
            
            # Mark as read
            result = service.mark_as_read(user_id, notif.id)
            assert result == True
            
            # Verify it's marked as read
            retrieved = service.get_notifications(user_id, limit=10)
            assert len(retrieved) == 1
            assert retrieved[0].read == True
            assert retrieved[0].read_at is not None
    
    def test_delete_notification(self, temp_notification_dir):
        """
        Test that deleting a notification continues to work.
        
        **Validates: Requirements 3.4**
        """
        # Mock FCM service
        with patch('app.services.fcm_service.get_fcm_service') as mock_fcm:
            mock_fcm_instance = Mock()
            mock_fcm_instance.get_token.return_value = None
            mock_fcm.return_value = mock_fcm_instance
            
            service = NotificationService(data_dir=temp_notification_dir)
            user_id = "test_user_delete"
            
            notif = service.send_notification(
                user_id=user_id,
                notification_type=NotificationType.ASSIGNMENT_NEW,
                title="Test",
                message="Test message"
            )
            
            # Delete notification
            result = service.delete_notification(user_id, notif.id)
            assert result == True
            
            # Verify it's deleted
            retrieved = service.get_notifications(user_id, limit=10)
            assert len(retrieved) == 0
    
    def test_get_unread_count(self, temp_notification_dir):
        """
        Test that getting unread count continues to work.
        
        **Validates: Requirements 3.4**
        """
        # Mock FCM service
        with patch('app.services.fcm_service.get_fcm_service') as mock_fcm:
            mock_fcm_instance = Mock()
            mock_fcm_instance.get_token.return_value = None
            mock_fcm.return_value = mock_fcm_instance
            
            service = NotificationService(data_dir=temp_notification_dir)
            user_id = "test_user_count"
            
            # Create 3 notifications
            notif1 = service.send_notification(
                user_id=user_id,
                notification_type=NotificationType.ASSIGNMENT_NEW,
                title="Test 1",
                message="Message 1"
            )
            notif2 = service.send_notification(
                user_id=user_id,
                notification_type=NotificationType.ASSIGNMENT_DUE_24H,
                title="Test 2",
                message="Message 2"
            )
            notif3 = service.send_notification(
                user_id=user_id,
                notification_type=NotificationType.ASSIGNMENT_OVERDUE,
                title="Test 3",
                message="Message 3"
            )
            
            # All should be unread
            count = service.get_unread_count(user_id)
            assert count == 3
            
            # Mark one as read
            service.mark_as_read(user_id, notif1.id)
            count = service.get_unread_count(user_id)
            assert count == 2
            
            # Mark all as read
            service.mark_all_as_read(user_id)
            count = service.get_unread_count(user_id)
            assert count == 0
    
    def test_clear_old_notifications(self, temp_notification_dir):
        """
        Test that clearing old notifications continues to work.
        
        **Validates: Requirements 3.4**
        """
        # Mock FCM service
        with patch('app.services.fcm_service.get_fcm_service') as mock_fcm:
            mock_fcm_instance = Mock()
            mock_fcm_instance.get_token.return_value = None
            mock_fcm.return_value = mock_fcm_instance
            
            service = NotificationService(data_dir=temp_notification_dir)
            user_id = "test_user_old"
            
            # Create a notification
            notif = service.send_notification(
                user_id=user_id,
                notification_type=NotificationType.ASSIGNMENT_NEW,
                title="Test",
                message="Test message"
            )
            
            # Manually modify the created_at to be 31 days old
            notifications = service._load_user_notifications(user_id)
            notifications[0].created_at = datetime.now() - timedelta(days=31)
            service._save_user_notifications(user_id, notifications)
            
            # Clear old notifications (older than 30 days)
            removed = service.clear_old_notifications(user_id, days=30)
            assert removed == 1
            
            # Verify it's removed
            retrieved = service.get_notifications(user_id, limit=10)
            assert len(retrieved) == 0
