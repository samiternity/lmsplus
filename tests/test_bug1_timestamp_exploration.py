#!/usr/bin/env python3
"""
Bug #1 Exploration Test: Timestamp Display Bug

**CRITICAL**: This test MUST FAIL on unfixed code - failure confirms the bug exists.
**DO NOT attempt to fix the test or the code when it fails.**

This test encodes the expected behavior - it will validate the fix when it passes after implementation.

**GOAL**: Surface counterexamples that demonstrate all notifications share the same timestamp.

**Validates: Requirements 2.1**
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


class TestBug1TimestampExploration:
    """
    Bug Condition Exploration Test for Timestamp Display
    
    Tests that when multiple notifications are created at different times (t1, t2, t3),
    each notification's `created_at` field is unique after JSON serialization/deserialization.
    
    **Expected Outcome on UNFIXED code**: Test FAILS (proves bug exists)
    """
    
    @pytest.fixture
    def temp_notification_dir(self):
        """Create a temporary directory for notification storage"""
        temp_dir = tempfile.mkdtemp()
        yield temp_dir
        # Cleanup
        import shutil
        shutil.rmtree(temp_dir, ignore_errors=True)
    
    def test_individual_timestamp_display_after_serialization(self, temp_notification_dir):
        """
        **Property 1: Bug Condition** - Individual Timestamp Display
        
        Create 3 notifications with 1-minute delays between each, serialize to JSON,
        deserialize, and verify each has a unique `created_at` timestamp.
        
        **Validates: Requirements 2.1**
        """
        # Mock FCM service to prevent hanging on push notifications
        with patch('app.services.fcm_service.get_fcm_service') as mock_fcm:
            mock_fcm_instance = Mock()
            mock_fcm_instance.get_token.return_value = None  # No FCM token
            mock_fcm.return_value = mock_fcm_instance
            
            # Setup
            service = NotificationService(data_dir=temp_notification_dir)
            user_id = "test_user_123"
            
            # Create 3 notifications with delays to ensure different timestamps
            notification_times = []
            
            # Notification 1 - created at t1
            notif1 = service.send_notification(
                user_id=user_id,
                notification_type=NotificationType.ASSIGNMENT_NEW,
                title="Assignment 1",
                message="New assignment posted"
            )
            notification_times.append(notif1.created_at)
            print(f"Notification 1 created at: {notif1.created_at.isoformat()}")
            
            # Wait 1 second (simulating time passing)
            time.sleep(1)
            
            # Notification 2 - created at t2
            notif2 = service.send_notification(
                user_id=user_id,
                notification_type=NotificationType.ASSIGNMENT_DUE_24H,
                title="Assignment 2",
                message="Assignment due in 24 hours"
            )
            notification_times.append(notif2.created_at)
            print(f"Notification 2 created at: {notif2.created_at.isoformat()}")
            
            # Wait 1 second
            time.sleep(1)
            
            # Notification 3 - created at t3
            notif3 = service.send_notification(
                user_id=user_id,
                notification_type=NotificationType.ASSIGNMENT_OVERDUE,
                title="Assignment 3",
                message="Assignment is overdue"
            )
            notification_times.append(notif3.created_at)
            print(f"Notification 3 created at: {notif3.created_at.isoformat()}")
            
            # Verify notifications were created at different times (before serialization)
            assert len(set(notification_times)) == 3, \
                "Notifications should have been created at different times"
            
            # Now simulate the bug scenario: serialize to JSON and deserialize
            # This mimics what happens when notifications are stored and retrieved
            
            # Force reload from JSON storage (clear cache)
            service._cache.clear()
            
            # Retrieve notifications from storage (this deserializes from JSON)
            retrieved_notifications = service.get_notifications(user_id, limit=10)
            
            # Sort by creation order (oldest first) to match our creation order
            retrieved_notifications.sort(key=lambda n: n.created_at)
            
            print("\n=== Retrieved Notifications After Deserialization ===")
            for i, notif in enumerate(retrieved_notifications, 1):
                print(f"Notification {i}: created_at = {notif.created_at.isoformat()}")
            
            # Extract timestamps from retrieved notifications
            retrieved_times = [n.created_at for n in retrieved_notifications]
            
            # **CRITICAL ASSERTION**: Each notification should have a unique timestamp
            # This will FAIL on unfixed code if all notifications share the same timestamp
            unique_timestamps = set(retrieved_times)
            
            print(f"\nUnique timestamps found: {len(unique_timestamps)}")
            print(f"Expected unique timestamps: 3")
            
            # Document the counterexample if test fails
            if len(unique_timestamps) != 3:
                print("\n=== COUNTEREXAMPLE FOUND ===")
                print(f"All notifications have the same timestamp: {retrieved_times[0].isoformat()}")
                print("This confirms Bug #1: All notifications display the same timestamp")
                print("Expected: Each notification should have its own unique timestamp")
            
            # The assertion that should fail on unfixed code
            assert len(unique_timestamps) == 3, \
                f"Expected 3 unique timestamps, but found {len(unique_timestamps)}. " \
                f"Timestamps: {[t.isoformat() for t in retrieved_times]}"
            
            # Additional verification: timestamps should be in chronological order
            # and separated by at least 1 second
            for i in range(len(retrieved_times) - 1):
                time_diff = (retrieved_times[i + 1] - retrieved_times[i]).total_seconds()
                assert time_diff >= 1, \
                    f"Timestamps should be at least 1 second apart, but found {time_diff}s"
    
    def test_timestamp_uniqueness_with_rapid_creation(self, temp_notification_dir):
        """
        Additional test: Create multiple notifications rapidly (no delays)
        to verify timestamps are still unique even with fast creation.
        
        **Validates: Requirements 2.1**
        """
        # Mock FCM service
        with patch('app.services.fcm_service.get_fcm_service') as mock_fcm:
            mock_fcm_instance = Mock()
            mock_fcm_instance.get_token.return_value = None
            mock_fcm.return_value = mock_fcm_instance
            
            service = NotificationService(data_dir=temp_notification_dir)
            user_id = "test_user_rapid"
            
            # Create 5 notifications rapidly
            created_notifications = []
            for i in range(5):
                notif = service.send_notification(
                    user_id=user_id,
                    notification_type=NotificationType.ASSIGNMENT_NEW,
                    title=f"Assignment {i+1}",
                    message=f"Assignment {i+1} posted"
                )
                created_notifications.append(notif)
            
            # Force reload from JSON storage
            service._cache.clear()
            retrieved_notifications = service.get_notifications(user_id, limit=10)
            
            # Extract timestamps
            retrieved_times = [n.created_at for n in retrieved_notifications]
            unique_timestamps = set(retrieved_times)
            
            print(f"\nRapid creation test: {len(unique_timestamps)} unique timestamps out of 5")
            
            # Even with rapid creation, each notification should have its own timestamp
            # (though they might be very close in time)
            assert len(unique_timestamps) == 5, \
                f"Expected 5 unique timestamps, but found {len(unique_timestamps)}"


class TestBug1LMSNotificationTimestamp:
    """
    Bug Condition Exploration Test for LMS Notification Timestamp Display
    
    Tests the in-memory LMS notification system used by the API endpoints.
    This is the actual notification system that displays in the UI.
    
    **Expected Outcome on UNFIXED code**: Test FAILS (proves bug exists)
    """
    
    def test_lms_notification_individual_timestamps(self):
        """
        **Property 1: Bug Condition** - Individual Timestamp Display (LMS Notifications)
        
        Create 3 LMS notifications with delays between each, and verify each has
        a unique `created_at` timestamp when retrieved via get_notifications().
        
        This tests the actual notification system used by /api/notifications endpoint.
        
        **Validates: Requirements 2.1**
        """
        # Setup
        manager = LMSNotificationManager()
        account_id = "test_account_lms"
        
        # Create 3 notifications with delays to ensure different timestamps
        notification_times = []
        
        # Notification 1 - created at t1
        notif1 = manager.create_notification(
            account_id=account_id,
            notification_type=LMSNotificationType.INFO,
            title="Assignment 1",
            message="New assignment posted"
        )
        notification_times.append(notif1.created_at)
        print(f"LMS Notification 1 created at: {notif1.created_at.isoformat()}")
        
        # Wait 1 second (simulating time passing)
        time.sleep(1)
        
        # Notification 2 - created at t2
        notif2 = manager.create_notification(
            account_id=account_id,
            notification_type=LMSNotificationType.WARNING,
            title="Assignment 2",
            message="Assignment due in 24 hours"
        )
        notification_times.append(notif2.created_at)
        print(f"LMS Notification 2 created at: {notif2.created_at.isoformat()}")
        
        # Wait 1 second
        time.sleep(1)
        
        # Notification 3 - created at t3
        notif3 = manager.create_notification(
            account_id=account_id,
            notification_type=LMSNotificationType.ERROR,
            title="Assignment 3",
            message="Assignment is overdue"
        )
        notification_times.append(notif3.created_at)
        print(f"LMS Notification 3 created at: {notif3.created_at.isoformat()}")
        
        # Verify notifications were created at different times
        assert len(set(notification_times)) == 3, \
            "Notifications should have been created at different times"
        
        # Now retrieve notifications (this is what the API endpoint does)
        retrieved_notifications = manager.get_notifications(account_id, include_expired=False)
        
        print("\n=== Retrieved LMS Notifications ===")
        for i, notif in enumerate(retrieved_notifications, 1):
            print(f"Notification {i}: created_at = {notif.created_at.isoformat()}")
        
        # Extract timestamps from retrieved notifications
        retrieved_times = [n.created_at for n in retrieved_notifications]
        
        # **CRITICAL ASSERTION**: Each notification should have a unique timestamp
        # This will FAIL on unfixed code if all notifications share the same timestamp
        unique_timestamps = set(retrieved_times)
        
        print(f"\nUnique timestamps found: {len(unique_timestamps)}")
        print(f"Expected unique timestamps: 3")
        
        # Document the counterexample if test fails
        if len(unique_timestamps) != 3:
            print("\n=== COUNTEREXAMPLE FOUND ===")
            print(f"All notifications have the same timestamp: {retrieved_times[0].isoformat()}")
            print("This confirms Bug #1: All LMS notifications display the same timestamp")
            print("Expected: Each notification should have its own unique timestamp")
        
        # The assertion that should fail on unfixed code
        assert len(unique_timestamps) == 3, \
            f"Expected 3 unique timestamps, but found {len(unique_timestamps)}. " \
            f"Timestamps: {[t.isoformat() for t in retrieved_times]}"
        
        # Additional verification: timestamps should be in chronological order
        # (Note: get_notifications sorts by priority first, then by created_at)
        # So we need to sort by created_at to verify the time differences
        sorted_times = sorted(retrieved_times)
        for i in range(len(sorted_times) - 1):
            time_diff = (sorted_times[i + 1] - sorted_times[i]).total_seconds()
            assert time_diff >= 1, \
                f"Timestamps should be at least 1 second apart, but found {time_diff}s"
    
    def test_lms_notification_to_dict_preserves_timestamps(self):
        """
        Test that to_dict() method preserves unique timestamps when serializing
        notifications for the API response.
        
        This simulates what happens in the /api/notifications endpoint:
        return jsonify({'notifications': [n.to_dict() for n in notifications]})
        
        **Validates: Requirements 2.1**
        """
        manager = LMSNotificationManager()
        account_id = "test_account_dict"
        
        # Create 3 notifications with delays
        for i in range(3):
            manager.create_notification(
                account_id=account_id,
                notification_type=LMSNotificationType.INFO,
                title=f"Assignment {i+1}",
                message=f"Assignment {i+1} posted"
            )
            if i < 2:  # Don't sleep after the last one
                time.sleep(1)
        
        # Retrieve and serialize to dict (like the API does)
        notifications = manager.get_notifications(account_id)
        notification_dicts = [n.to_dict() for n in notifications]
        
        print("\n=== Serialized Notification Timestamps ===")
        for i, notif_dict in enumerate(notification_dicts, 1):
            print(f"Notification {i}: created_at = {notif_dict['created_at']}")
        
        # Extract created_at timestamps from dicts
        timestamps = [notif_dict['created_at'] for notif_dict in notification_dicts]
        unique_timestamps = set(timestamps)
        
        print(f"\nUnique timestamps in serialized form: {len(unique_timestamps)}")
        print(f"Expected: 3")
        
        # Document counterexample if test fails
        if len(unique_timestamps) != 3:
            print("\n=== COUNTEREXAMPLE FOUND ===")
            print(f"All serialized notifications have the same timestamp: {timestamps[0]}")
            print("This confirms Bug #1: Timestamps are not unique in API responses")
        
        assert len(unique_timestamps) == 3, \
            f"Expected 3 unique timestamps in serialized form, but found {len(unique_timestamps)}. " \
            f"Timestamps: {timestamps}"


class TestBug1BackgroundServiceScenario:
    """
    Bug Condition Exploration Test simulating the exact background service scenario.
    
    Tests the scenario where multiple notifications are created in a loop
    (like in background_notification_service._check_assignments).
    
    **Expected Outcome on UNFIXED code**: Test FAILS (proves bug exists)
    """
    
    def test_multiple_notifications_in_loop_like_background_service(self):
        """
        **Property 1: Bug Condition** - Timestamp uniqueness in loop creation
        
        Simulates the exact pattern used in background_notification_service:
        - Loop through assignments
        - Call send_notification() multiple times in quick succession
        - Verify each notification has a unique timestamp
        
        This is the most realistic test of the actual bug scenario.
        
        **Validates: Requirements 2.1**
        """
        # Mock FCM service
        with patch('app.services.fcm_service.get_fcm_service') as mock_fcm:
            mock_fcm_instance = Mock()
            mock_fcm_instance.get_token.return_value = None
            mock_fcm.return_value = mock_fcm_instance
            
            # Setup
            import tempfile
            temp_dir = tempfile.mkdtemp()
            service = NotificationService(data_dir=temp_dir)
            user_id = "test_user_background"
            
            # Simulate the background service loop creating multiple notifications
            # This is what happens in assignment_notification_service.check_and_notify()
            notification_types = [
                (NotificationType.ASSIGNMENT_NEW, "New Assignment", "Assignment 1 posted"),
                (NotificationType.ASSIGNMENT_DUE_24H, "Due Soon", "Assignment 2 due in 12h"),
                (NotificationType.ASSIGNMENT_DUE_3D, "Upcoming", "Assignment 3 due in 2 days"),
            ]
            
            created_notifications = []
            print("\n=== Creating notifications in loop (like background service) ===")
            for notif_type, title, message in notification_types:
                notif = service.send_notification(
                    user_id=user_id,
                    notification_type=notif_type,
                    title=title,
                    message=message
                )
                created_notifications.append(notif)
                print(f"Created: {title} at {notif.created_at.isoformat()}")
            
            # Now retrieve from storage (simulating API call)
            service._cache.clear()  # Force reload from JSON
            retrieved = service.get_notifications(user_id, limit=10)
            
            print("\n=== Retrieved from storage ===")
            for notif in retrieved:
                print(f"Retrieved: {notif.title} at {notif.created_at.isoformat()}")
            
            # Extract timestamps
            retrieved_times = [n.created_at for n in retrieved]
            unique_timestamps = set(retrieved_times)
            
            print(f"\nUnique timestamps: {len(unique_timestamps)}")
            print(f"Expected: 3")
            
            # Document counterexample
            if len(unique_timestamps) != 3:
                print("\n=== COUNTEREXAMPLE FOUND ===")
                print(f"All notifications share timestamp: {retrieved_times[0].isoformat()}")
                print("This confirms Bug #1: Notifications created in a loop share the same timestamp")
                print("\nTimestamps found:")
                for i, t in enumerate(retrieved_times, 1):
                    print(f"  Notification {i}: {t.isoformat()}")
            
            # Cleanup
            import shutil
            shutil.rmtree(temp_dir, ignore_errors=True)
            
            # The critical assertion
            assert len(unique_timestamps) == 3, \
                f"Expected 3 unique timestamps, but found {len(unique_timestamps)}. " \
                f"All timestamps: {[t.isoformat() for t in retrieved_times]}"
    
    def test_notification_dataclass_default_factory(self):
        """
        Test the Notification dataclass default_factory behavior directly.
        
        This tests if the issue is with how dataclasses handle default_factory.
        
        **Validates: Requirements 2.1**
        """
        from app.models.notification_models import Notification as NotificationModel
        
        print("\n=== Testing dataclass default_factory ===")
        
        # Create 3 notification objects directly (not through service)
        notifications = []
        for i in range(3):
            notif = NotificationModel(
                user_id="test_user",
                notification_type=NotificationType.ASSIGNMENT_NEW,
                title=f"Test {i+1}",
                message=f"Message {i+1}"
                # Note: NOT passing created_at, letting default_factory create it
            )
            notifications.append(notif)
            print(f"Notification {i+1} created_at: {notif.created_at.isoformat()}")
            time.sleep(0.1)  # Small delay to ensure different timestamps
        
        # Check if timestamps are unique
        timestamps = [n.created_at for n in notifications]
        unique_timestamps = set(timestamps)
        
        print(f"\nUnique timestamps: {len(unique_timestamps)}")
        print(f"Expected: 3")
        
        if len(unique_timestamps) != 3:
            print("\n=== COUNTEREXAMPLE FOUND ===")
            print("Dataclass default_factory is creating shared timestamps!")
            print("This is the root cause of Bug #1")
        
        assert len(unique_timestamps) == 3, \
            f"Expected 3 unique timestamps from dataclass default_factory, but found {len(unique_timestamps)}"
    
    def test_rapid_fire_notifications_same_millisecond(self):
        """
        Test creating multiple notifications as fast as possible (no delays).
        
        If notifications are created within the same millisecond, they might
        share the same timestamp, which would cause the bug.
        
        **Validates: Requirements 2.1**
        """
        # Mock FCM service
        with patch('app.services.fcm_service.get_fcm_service') as mock_fcm:
            mock_fcm_instance = Mock()
            mock_fcm_instance.get_token.return_value = None
            mock_fcm.return_value = mock_fcm_instance
            
            import tempfile
            temp_dir = tempfile.mkdtemp()
            service = NotificationService(data_dir=temp_dir)
            user_id = "test_user_rapid"
            
            print("\n=== Creating notifications as fast as possible (no delays) ===")
            
            # Create 10 notifications as fast as possible
            for i in range(10):
                service.send_notification(
                    user_id=user_id,
                    notification_type=NotificationType.ASSIGNMENT_NEW,
                    title=f"Assignment {i+1}",
                    message=f"Assignment {i+1} posted"
                )
            
            # Retrieve from storage
            service._cache.clear()
            retrieved = service.get_notifications(user_id, limit=20)
            
            print(f"Created {len(retrieved)} notifications")
            
            # Check timestamps
            timestamps = [n.created_at for n in retrieved]
            unique_timestamps = set(timestamps)
            
            print(f"Unique timestamps: {len(unique_timestamps)}")
            print(f"Total notifications: {len(timestamps)}")
            
            # Show timestamp distribution
            from collections import Counter
            timestamp_counts = Counter([t.isoformat() for t in timestamps])
            duplicates = {ts: count for ts, count in timestamp_counts.items() if count > 1}
            
            if duplicates:
                print("\n=== COUNTEREXAMPLE FOUND ===")
                print("Some notifications share the same timestamp:")
                for ts, count in duplicates.items():
                    print(f"  {ts}: {count} notifications")
                print("\nThis confirms Bug #1: Rapid notification creation causes shared timestamps")
            
            # Cleanup
            import shutil
            shutil.rmtree(temp_dir, ignore_errors=True)
            
            # The assertion - we expect all timestamps to be unique
            # If this fails, we found the bug!
            assert len(unique_timestamps) == len(timestamps), \
                f"Expected {len(timestamps)} unique timestamps, but found {len(unique_timestamps)}. " \
                f"Duplicates: {duplicates}"


