import logging
logger = logging.getLogger(__name__)

"""
Notification Service
Handles notification creation, delivery, and management.
"""
import json
import os
from datetime import datetime, timedelta
from typing import List, Optional, Dict, Any
from pathlib import Path

from app.models.notification_models import Notification, NotificationType


class NotificationService:
    """Service for managing user notifications"""
    
    def __init__(self, data_dir: str = None):
        """Initialize notification service"""
        if data_dir is None:
            # Default to app data directory
            base_dir = Path(__file__).parent.parent.parent
            data_dir = base_dir / 'data' / 'notifications'
        
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        
        # In-memory cache for quick access
        self._cache: Dict[str, List[Notification]] = {}
        self._max_notifications_per_user = 100  # Limit stored notifications
    
    def _get_user_file(self, user_id: str) -> Path:
        """Get path to user's notification file"""
        return self.data_dir / f"{user_id}.json"
    
    def _load_user_notifications(self, user_id: str) -> List[Notification]:
        """Load notifications for a user from storage"""
        if user_id in self._cache:
            return self._cache[user_id]
        
        file_path = self._get_user_file(user_id)
        if not file_path.exists():
            self._cache[user_id] = []
            return []
        
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            notifications = [Notification.from_dict(n) for n in data]
            self._cache[user_id] = notifications
            return notifications
        except Exception as e:
            logger.warning(f"Error loading notifications for {user_id}: {e}")
            return []
    
    def _save_user_notifications(self, user_id: str, notifications: List[Notification]) -> bool:
        """Save notifications for a user to storage"""
        file_path = self._get_user_file(user_id)
        
        try:
            # Limit stored notifications
            if len(notifications) > self._max_notifications_per_user:
                notifications = notifications[-self._max_notifications_per_user:]
            
            data = [n.to_dict() for n in notifications]
            with open(file_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2)
            
            self._cache[user_id] = notifications
            return True
        except Exception as e:
            logger.warning(f"Error saving notifications for {user_id}: {e}")
            return False
    
    def send_notification(
        self, 
        user_id: str, 
        notification_type: NotificationType, 
        title: str, 
        message: str,
        data: dict = None,
        send_push: bool = False
    ) -> Optional[Notification]:
        """Create and send a notification to a user
        
        Args:
            user_id: User ID to send notification to
            notification_type: Type of notification
            title: Notification title
            message: Notification message
            data: Additional data dictionary
            send_push: Whether to send FCM push notification (default False for backward compatibility)
        """
        notification = Notification(
            user_id=user_id,
            notification_type=notification_type,
            title=title,
            message=message,
            data=data or {}
        )
        
        from app.models.account import User
        user = User.get(user_id)
        
        # Send Push Notification (only if send_push=True)
        if send_push:
            # Discord DMs
            logger.debug("Discord notification requested for user %s", user_id)
            if user and user.discord_id and user.notification_preferences and user.notification_preferences.get('discord_enabled'):
                try:
                    from app.services.discord_service import enqueue_discord_notification
                    logger.debug(f"[DEBUG DISCORD] Calling enqueue_discord_notification for {user.id}")
                    
                    # Determine embed color based on notification type and context
                    color = 0x8B5CF6 # Default Purple
                    if notification_type.value == 'attendance':
                        color = 0x10B981 if 'Present' in title else 0xEF4444
                    elif notification_type.value == 'assignment':
                        color = 0xDC2626 if 'Overdue' in title else (0xF59E0B if 'Due' in title else 0x6366F1)
                        
                    # Extract fields from data dict if present
                    fields = []
                    url = None
                    if data:
                        if 'course_name' in data:
                            fields.append({'name': 'Course', 'value': data['course_name'], 'inline': True})
                        if 'deadline' in data:
                            deadline_str = data['deadline']
                            try:
                                from datetime import datetime
                                # Assuming deadline is ISO format like "2026-05-25T23:55:00"
                                dt = datetime.fromisoformat(deadline_str.replace('Z', '+00:00'))
                                deadline_formatted = dt.strftime("%B %d, %Y at %I:%M %p")
                            except Exception:
                                deadline_formatted = deadline_str.replace('T', ' ')
                            fields.append({'name': 'Deadline', 'value': deadline_formatted, 'inline': True})
                        if 'url' in data:
                            url = data['url']
                            
                    queued = enqueue_discord_notification(
                        discord_id=user.discord_id,
                        title=title,
                        description=message,
                        color=color,
                        url=url,
                        fields=fields
                    )
                    if not queued:
                        return None
                except Exception as e:
                    logger.warning(f"Failed to enqueue Discord DM: {e}")
                    return None
            else:
                return None

        return notification

    
    def get_notifications(
        self, 
        user_id: str, 
        unread_only: bool = False,
        limit: int = 50,
        category: str = None
    ) -> List[Notification]:
        """Get notifications for a user"""
        notifications = self._load_user_notifications(user_id)
        
        # Filter by read status
        if unread_only:
            notifications = [n for n in notifications if not n.read]
        
        # Filter by category
        if category:
            notifications = [n for n in notifications if n.category == category]
        
        # Sort by creation date (newest first)
        notifications.sort(key=lambda n: n.created_at, reverse=True)
        
        # Apply limit
        return notifications[:limit]
    
    def get_unread_count(self, user_id: str) -> int:
        """Get count of unread notifications"""
        notifications = self._load_user_notifications(user_id)
        return sum(1 for n in notifications if not n.read)
    
    def mark_as_read(self, user_id: str, notification_id: str) -> bool:
        """Mark a specific notification as read"""
        notifications = self._load_user_notifications(user_id)
        
        for notification in notifications:
            if notification.id == notification_id:
                notification.mark_as_read()
                return self._save_user_notifications(user_id, notifications)
        
        return False
    
    def mark_all_as_read(self, user_id: str) -> bool:
        """Mark all notifications as read"""
        notifications = self._load_user_notifications(user_id)
        
        for notification in notifications:
            if not notification.read:
                notification.mark_as_read()
        
        return self._save_user_notifications(user_id, notifications)
    
    def delete_notification(self, user_id: str, notification_id: str) -> bool:
        """Delete a specific notification"""
        notifications = self._load_user_notifications(user_id)
        
        original_count = len(notifications)
        notifications = [n for n in notifications if n.id != notification_id]
        
        if len(notifications) < original_count:
            return self._save_user_notifications(user_id, notifications)
        return False
    
    def clear_old_notifications(self, user_id: str, days: int = 30) -> int:
        """Clear notifications older than specified days"""
        notifications = self._load_user_notifications(user_id)
        cutoff = datetime.now() - timedelta(days=days)
        
        original_count = len(notifications)
        notifications = [n for n in notifications if n.created_at > cutoff]
        
        removed = original_count - len(notifications)
        if removed > 0:
            self._save_user_notifications(user_id, notifications)
        
        return removed
    
    def check_user_preference(self, user, notification_type: NotificationType) -> bool:
        """Check if user has enabled a specific notification type"""
        if not hasattr(user, 'notification_preferences') or not user.notification_preferences:
            return False
        
        return user.notification_preferences.get(notification_type.value, False)


# Singleton instance
_notification_service = None

def get_notification_service() -> NotificationService:
    """Get the singleton notification service instance"""
    global _notification_service
    if _notification_service is None:
        _notification_service = NotificationService()
    return _notification_service
