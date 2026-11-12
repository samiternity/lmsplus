# app/utils/lms_notifications.py
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any, Union
from enum import Enum
from dataclasses import dataclass, asdict
import json
import uuid

from app.utils.lms_logger import log_info, log_debug, log_warning

class NotificationType(Enum):
    """Types of notifications"""
    SUCCESS = "success"
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"
    PROGRESS = "progress"

class NotificationPriority(Enum):
    """Notification priority levels"""
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    URGENT = "urgent"

@dataclass
class Notification:
    """Notification data structure"""
    id: str
    type: NotificationType
    title: str
    message: str
    priority: NotificationPriority = NotificationPriority.NORMAL
    dismissible: bool = True
    auto_dismiss: bool = True
    auto_dismiss_delay: int = 5000  # milliseconds
    actions: Optional[List[Dict[str, str]]] = None
    metadata: Optional[Dict[str, Any]] = None
    created_at: Optional[datetime] = None
    expires_at: Optional[datetime] = None
    
    def __post_init__(self):
        """Post-initialization validation and logging"""
        # Ensure created_at is never None and is unique for each instance
        if self.created_at is None:
            self.created_at = datetime.now()
        
        # Validate created_at is a datetime object
        if not isinstance(self.created_at, datetime):
            log_warning(f"LMS Notification {self.id}: created_at is not a datetime object, type: {type(self.created_at)}")
            raise ValueError(f"created_at must be a datetime object, got {type(self.created_at)}")
        
        # Set expiration if auto-dismiss is enabled
        if self.expires_at is None and self.auto_dismiss:
            # Auto-expire after 1 hour by default
            self.expires_at = self.created_at + timedelta(hours=1)
        
        # Log notification creation with unique timestamp
        log_debug(f"LMS Notification created: {self.id} at {self.created_at.isoformat()}")
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert notification to dictionary for JSON serialization"""
        data = asdict(self)
        data['type'] = self.type.value
        data['priority'] = self.priority.value
        data['created_at'] = self.created_at.isoformat() if self.created_at else None
        data['expires_at'] = self.expires_at.isoformat() if self.expires_at else None
        return data
    
    def is_expired(self) -> bool:
        """Check if notification has expired"""
        if not self.expires_at:
            return False
        return datetime.now() > self.expires_at

class LMSNotificationManager:
    """
    Manages notifications for LMS operations.
    Provides success/error notifications, progress indicators, and status messages.
    """
    
    def __init__(self):
        self.notifications = {}  # account_id -> list of notifications
        self.notification_templates = self._initialize_templates()
        
    def _initialize_templates(self) -> Dict[str, Dict[str, Any]]:
        """Initialize notification templates for common operations"""
        return {
            # Authentication notifications
            'auth_success': {
                'type': NotificationType.SUCCESS,
                'title': 'Authentication Successful',
                'message': 'Successfully connected to LMS',
                'auto_dismiss_delay': 3000
            },
            'auth_failed': {
                'type': NotificationType.ERROR,
                'title': 'Authentication Failed',
                'message': 'Unable to connect to LMS. Please check your credentials.',
                'auto_dismiss': False,
                'actions': [
                    {'label': 'Retry', 'action': 'retry_auth'},
                    {'label': 'Update Credentials', 'action': 'update_credentials'}
                ]
            },
            'session_expired': {
                'type': NotificationType.WARNING,
                'title': 'Session Expired',
                'message': 'Your LMS session has expired. Please re-authenticate.',
                'auto_dismiss': False,
                'actions': [
                    {'label': 'Re-authenticate', 'action': 'reauth'}
                ]
            },
            
            # Data refresh notifications
            'refresh_started': {
                'type': NotificationType.PROGRESS,
                'title': 'Refreshing Data',
                'message': 'Updating assignments and lecture notes from LMS...',
                'dismissible': False,
                'auto_dismiss': False
            },
            'refresh_success': {
                'type': NotificationType.SUCCESS,
                'title': 'Data Updated',
                'message': 'Successfully refreshed {assignments_count} assignments and {lecture_notes_count} lecture notes',
                'auto_dismiss_delay': 4000
            },
            'refresh_partial': {
                'type': NotificationType.WARNING,
                'title': 'Partial Update',
                'message': 'Some data could not be updated. {error_count} errors occurred.',
                'auto_dismiss_delay': 6000,
                'actions': [
                    {'label': 'View Details', 'action': 'view_errors'},
                    {'label': 'Retry', 'action': 'retry_refresh'}
                ]
            },
            'refresh_failed': {
                'type': NotificationType.ERROR,
                'title': 'Update Failed',
                'message': 'Unable to refresh data from LMS. {error_message}',
                'auto_dismiss': False,
                'actions': [
                    {'label': 'Retry', 'action': 'retry_refresh'},
                    {'label': 'Check Status', 'action': 'check_status'}
                ]
            },
            
            # File operation notifications
            'file_upload_started': {
                'type': NotificationType.PROGRESS,
                'title': 'Uploading File',
                'message': 'Uploading {filename} to assignment {assignment_id}...',
                'dismissible': False,
                'auto_dismiss': False
            },
            'file_upload_success': {
                'type': NotificationType.SUCCESS,
                'title': 'File Uploaded',
                'message': 'Successfully uploaded {filename} to assignment {assignment_id}',
                'auto_dismiss_delay': 4000
            },
            'file_upload_failed': {
                'type': NotificationType.ERROR,
                'title': 'Upload Failed',
                'message': 'Failed to upload {filename}. {error_message}',
                'auto_dismiss': False,
                'actions': [
                    {'label': 'Retry', 'action': 'retry_upload'},
                    {'label': 'Choose Different File', 'action': 'choose_file'}
                ]
            },
            'file_download_started': {
                'type': NotificationType.PROGRESS,
                'title': 'Downloading File',
                'message': 'Downloading {filename}...',
                'dismissible': False,
                'auto_dismiss': False
            },
            'file_download_failed': {
                'type': NotificationType.ERROR,
                'title': 'Download Failed',
                'message': 'Failed to download {filename}. {error_message}',
                'auto_dismiss_delay': 5000
            },
            
            # Assignment notifications
            'assignment_deadline_urgent': {
                'type': NotificationType.WARNING,
                'title': 'Assignment Due Soon',
                'message': 'Assignment "{assignment_title}" is due in {hours_remaining} hours',
                'priority': NotificationPriority.HIGH,
                'auto_dismiss': False,
                'actions': [
                    {'label': 'View Assignment', 'action': 'view_assignment'},
                    {'label': 'Upload File', 'action': 'upload_file'}
                ]
            },
            'assignment_overdue': {
                'type': NotificationType.ERROR,
                'title': 'Assignment Overdue',
                'message': 'Assignment "{assignment_title}" was due {days_overdue} days ago',
                'priority': NotificationPriority.URGENT,
                'auto_dismiss': False,
                'actions': [
                    {'label': 'Submit Late', 'action': 'submit_late'},
                    {'label': 'Contact Instructor', 'action': 'contact_instructor'}
                ]
            },
            
            # System notifications
            'system_maintenance': {
                'type': NotificationType.INFO,
                'title': 'System Maintenance',
                'message': 'LMS integration will be temporarily unavailable for maintenance',
                'priority': NotificationPriority.HIGH,
                'auto_dismiss': False
            },
            'connection_restored': {
                'type': NotificationType.SUCCESS,
                'title': 'Connection Restored',
                'message': 'LMS connection has been restored',
                'auto_dismiss_delay': 3000
            }
        }
    
    def create_notification(self, account_id: str, template_name: Optional[str] = None,
                          notification_type: Optional[NotificationType] = None,
                          title: Optional[str] = None, message: Optional[str] = None,
                          priority: NotificationPriority = NotificationPriority.NORMAL,
                          **kwargs) -> Notification:
        """
        Create a notification for an account.
        
        Args:
            account_id: Account ID to create notification for
            template_name: Name of template to use
            notification_type: Type of notification (if not using template)
            title: Notification title (if not using template)
            message: Notification message (if not using template)
            priority: Notification priority
            **kwargs: Additional parameters for template formatting
            
        Returns:
            Created Notification object
        """
        try:
            notification_id = str(uuid.uuid4())
            
            if template_name and template_name in self.notification_templates:
                # Use template
                template = self.notification_templates[template_name].copy()
                
                # Format message with provided parameters
                if 'message' in template and kwargs:
                    try:
                        template['message'] = template['message'].format(**kwargs)
                    except KeyError as e:
                        log_warning(f"Missing template parameter: {e}", 
                                  account_id=account_id, template=template_name)
                
                # Format title if needed
                if 'title' in template and kwargs:
                    try:
                        template['title'] = template['title'].format(**kwargs)
                    except KeyError:
                        pass  # Title formatting is optional
                
                notification = Notification(
                    id=notification_id,
                    type=template.get('type', NotificationType.INFO),
                    title=template.get('title', 'Notification'),
                    message=template.get('message', ''),
                    priority=template.get('priority', priority),
                    dismissible=template.get('dismissible', True),
                    auto_dismiss=template.get('auto_dismiss', True),
                    auto_dismiss_delay=template.get('auto_dismiss_delay', 5000),
                    actions=template.get('actions'),
                    metadata=kwargs
                )
            else:
                # Create custom notification
                if not notification_type or not title or not message:
                    raise ValueError("notification_type, title, and message are required for custom notifications")
                
                notification = Notification(
                    id=notification_id,
                    type=notification_type,
                    title=title,
                    message=message,
                    priority=priority,
                    metadata=kwargs
                )
            
            # Store notification
            if account_id not in self.notifications:
                self.notifications[account_id] = []
            
            self.notifications[account_id].append(notification)
            
            # Clean up expired notifications
            self._cleanup_expired_notifications(account_id)
            
            log_debug(f"Created notification {notification_id} for account {account_id}",
                     account_id=account_id, notification_id=notification_id,
                     notification_type=notification.type.value, template=template_name)
            
            return notification
            
        except Exception as e:
            log_warning(f"Failed to create notification: {e}", 
                       account_id=account_id, template=template_name)
            # Return a basic error notification
            return Notification(
                id=str(uuid.uuid4()),
                type=NotificationType.ERROR,
                title="Notification Error",
                message="Failed to create notification"
            )
    
    def get_notifications(self, account_id: str, include_expired: bool = False) -> List[Notification]:
        """
        Get notifications for an account.
        
        Args:
            account_id: Account ID
            include_expired: Whether to include expired notifications
            
        Returns:
            List of notifications
        """
        if account_id not in self.notifications:
            return []
        
        notifications = self.notifications[account_id]
        
        if not include_expired:
            notifications = [n for n in notifications if not n.is_expired()]
        
        # Sort by priority and creation time
        priority_order = {
            NotificationPriority.URGENT: 0,
            NotificationPriority.HIGH: 1,
            NotificationPriority.NORMAL: 2,
            NotificationPriority.LOW: 3
        }
        
        notifications.sort(key=lambda n: (
            priority_order.get(n.priority, 2),
            n.created_at or datetime.min
        ), reverse=True)
        
        return notifications
    
    def dismiss_notification(self, account_id: str, notification_id: str) -> bool:
        """
        Dismiss a notification.
        
        Args:
            account_id: Account ID
            notification_id: Notification ID to dismiss
            
        Returns:
            True if notification was dismissed, False if not found
        """
        if account_id not in self.notifications:
            return False
        
        notifications = self.notifications[account_id]
        
        for i, notification in enumerate(notifications):
            if notification.id == notification_id:
                if notification.dismissible:
                    del notifications[i]
                    log_debug(f"Dismissed notification {notification_id}",
                             account_id=account_id, notification_id=notification_id)
                    return True
                else:
                    log_debug(f"Attempted to dismiss non-dismissible notification {notification_id}",
                             account_id=account_id, notification_id=notification_id)
                    return False
        
        return False
    
    def clear_notifications(self, account_id: str, notification_type: Optional[NotificationType] = None):
        """
        Clear notifications for an account.
        
        Args:
            account_id: Account ID
            notification_type: Optional type filter
        """
        if account_id not in self.notifications:
            return
        
        if notification_type:
            # Clear only notifications of specific type
            self.notifications[account_id] = [
                n for n in self.notifications[account_id] 
                if n.type != notification_type or not n.dismissible
            ]
        else:
            # Clear all dismissible notifications
            self.notifications[account_id] = [
                n for n in self.notifications[account_id] 
                if not n.dismissible
            ]
        
        log_debug(f"Cleared notifications for account {account_id}",
                 account_id=account_id, notification_type=notification_type.value if notification_type else "all")
    
    def update_progress_notification(self, account_id: str, notification_id: str,
                                   progress: int, message: Optional[str] = None) -> bool:
        """
        Update progress notification.
        
        Args:
            account_id: Account ID
            notification_id: Notification ID
            progress: Progress percentage (0-100)
            message: Optional updated message
            
        Returns:
            True if notification was updated, False if not found
        """
        if account_id not in self.notifications:
            return False
        
        for notification in self.notifications[account_id]:
            if notification.id == notification_id and notification.type == NotificationType.PROGRESS:
                if not notification.metadata:
                    notification.metadata = {}
                
                notification.metadata['progress'] = progress
                
                if message:
                    notification.message = message
                
                log_debug(f"Updated progress notification {notification_id} to {progress}%",
                         account_id=account_id, notification_id=notification_id, progress=progress)
                return True
        
        return False
    
    def complete_progress_notification(self, account_id: str, notification_id: str,
                                     success: bool = True, final_message: Optional[str] = None):
        """
        Complete a progress notification by converting it to success/error.
        
        Args:
            account_id: Account ID
            notification_id: Notification ID
            success: Whether operation was successful
            final_message: Optional final message
        """
        if account_id not in self.notifications:
            return
        
        for notification in self.notifications[account_id]:
            if notification.id == notification_id and notification.type == NotificationType.PROGRESS:
                # Convert to success or error notification
                notification.type = NotificationType.SUCCESS if success else NotificationType.ERROR
                notification.dismissible = True
                notification.auto_dismiss = True
                notification.auto_dismiss_delay = 4000 if success else 6000
                
                if final_message:
                    notification.message = final_message
                
                # Update metadata
                if not notification.metadata:
                    notification.metadata = {}
                notification.metadata['completed'] = True
                notification.metadata['success'] = success
                notification.metadata['progress'] = 100
                
                log_debug(f"Completed progress notification {notification_id} (success: {success})",
                         account_id=account_id, notification_id=notification_id, success=success)
                break
    
    def _cleanup_expired_notifications(self, account_id: str):
        """Clean up expired notifications for an account"""
        if account_id not in self.notifications:
            return
        
        original_count = len(self.notifications[account_id])
        self.notifications[account_id] = [
            n for n in self.notifications[account_id] 
            if not n.is_expired()
        ]
        
        cleaned_count = original_count - len(self.notifications[account_id])
        if cleaned_count > 0:
            log_debug(f"Cleaned up {cleaned_count} expired notifications",
                     account_id=account_id, cleaned_count=cleaned_count)
    
    def get_notification_stats(self, account_id: Optional[str] = None) -> Dict[str, Any]:
        """
        Get notification statistics.
        
        Args:
            account_id: Optional account ID filter
            
        Returns:
            Dictionary with notification statistics
        """
        stats = {
            'total_notifications': 0,
            'by_type': {},
            'by_priority': {},
            'active_notifications': 0,
            'expired_notifications': 0,
            'generated_at': datetime.now().isoformat()
        }
        
        accounts_to_check = [account_id] if account_id else self.notifications.keys()
        
        for acc_id in accounts_to_check:
            if acc_id not in self.notifications:
                continue
                
            for notification in self.notifications[acc_id]:
                stats['total_notifications'] += 1
                
                # Count by type
                type_key = notification.type.value
                stats['by_type'][type_key] = stats['by_type'].get(type_key, 0) + 1
                
                # Count by priority
                priority_key = notification.priority.value
                stats['by_priority'][priority_key] = stats['by_priority'].get(priority_key, 0) + 1
                
                # Count active vs expired
                if notification.is_expired():
                    stats['expired_notifications'] += 1
                else:
                    stats['active_notifications'] += 1
        
        return stats

# Global notification manager instance
lms_notification_manager = LMSNotificationManager()

# Convenience functions
def create_notification(account_id: str, template_name: Optional[str] = None, **kwargs) -> Notification:
    """Convenience function to create notifications"""
    return lms_notification_manager.create_notification(account_id, template_name, **kwargs)

def get_notifications(account_id: str, include_expired: bool = False) -> List[Notification]:
    """Convenience function to get notifications"""
    return lms_notification_manager.get_notifications(account_id, include_expired)

def dismiss_notification(account_id: str, notification_id: str) -> bool:
    """Convenience function to dismiss notifications"""
    return lms_notification_manager.dismiss_notification(account_id, notification_id)

def clear_notifications(account_id: str, notification_type: Optional[NotificationType] = None):
    """Convenience function to clear notifications"""
    return lms_notification_manager.clear_notifications(account_id, notification_type)

def update_progress(account_id: str, notification_id: str, progress: int, message: Optional[str] = None) -> bool:
    """Convenience function to update progress"""
    return lms_notification_manager.update_progress_notification(account_id, notification_id, progress, message)

def complete_progress(account_id: str, notification_id: str, success: bool = True, final_message: Optional[str] = None):
    """Convenience function to complete progress"""
    return lms_notification_manager.complete_progress_notification(account_id, notification_id, success, final_message)