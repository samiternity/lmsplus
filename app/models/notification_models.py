import logging
logger = logging.getLogger(__name__)

"""
Notification Models
Defines data structures for the notification system.
"""
from dataclasses import dataclass, field, asdict
from datetime import datetime
from enum import Enum
from typing import Optional
import uuid


def notification_preference_enabled(value) -> bool:
    return value is True or (isinstance(value, str) and value.strip().lower() == 'true')


class NotificationType(Enum):
    """Types of notifications"""
    # Attendance notifications
    ATTENDANCE_ABSENT_SCHEDULED = "absent_scheduled"
    ATTENDANCE_ABSENT_UNSCHEDULED = "absent_unscheduled"
    ATTENDANCE_PRESENT_SCHEDULED = "present_scheduled"
    ATTENDANCE_PRESENT_UNSCHEDULED = "present_unscheduled"
    
    # Assignment notifications
    ASSIGNMENT_NEW = "new_assignment"
    ASSIGNMENT_DUE_24H = "due_24h"
    ASSIGNMENT_DUE_3D = "due_3d"
    ASSIGNMENT_OVERDUE = "overdue_warning"
    
    # System notifications
    SYSTEM_INFO = "system_info"
    SYSTEM_WARNING = "system_warning"


@dataclass
class Notification:
    """Represents a single notification"""
    user_id: str
    notification_type: NotificationType
    title: str
    message: str
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    created_at: datetime = field(default_factory=datetime.now)
    read: bool = False
    read_at: Optional[datetime] = None
    data: dict = field(default_factory=dict)  # Additional context data
    
    def __post_init__(self):
        """Post-initialization validation and logging"""
        # Ensure created_at is never None
        if self.created_at is None:
            self.created_at = datetime.now()
            logger.warning(f"[Notification] Warning: created_at was None for notification {self.id}, set to current time")
        
        # Validate created_at is a datetime object
        if not isinstance(self.created_at, datetime):
            logger.warning(f"[Notification] Error: created_at is not a datetime object for notification {self.id}, type: {type(self.created_at)}")
            raise ValueError(f"created_at must be a datetime object, got {type(self.created_at)}")
        
        # Log notification creation with unique timestamp
        logger.debug(f"[Notification] Created notification {self.id} at {self.created_at.isoformat()} for user {self.user_id}")
    
    def to_dict(self) -> dict:
        """Convert notification to dictionary for storage/API"""
        created_iso = self.created_at.isoformat()
        if not created_iso.endswith('Z') and '+' not in created_iso:
            created_iso += 'Z'
            
        read_iso = None
        if self.read_at:
            read_iso = self.read_at.isoformat()
            if not read_iso.endswith('Z') and '+' not in read_iso:
                read_iso += 'Z'
                
        return {
            'id': self.id,
            'user_id': self.user_id,
            'type': self.notification_type.value,
            'title': self.title,
            'message': self.message,
            'created_at': created_iso,
            'read': self.read,
            'read_at': read_iso,
            'data': self.data
        }
    
    @classmethod
    def from_dict(cls, data: dict) -> 'Notification':
        """Create notification from dictionary"""
        notification_type = NotificationType(data['type'])
        
        # Parse created_at with validation
        created_at_str = data.get('created_at')
        if created_at_str is None:
            logger.warning(f"[Notification] Warning: created_at is None in data for notification {data.get('id')}, using current time")
            created_at = datetime.now()
        elif isinstance(created_at_str, str):
            try:
                created_at = datetime.fromisoformat(created_at_str)
            except (ValueError, TypeError) as e:
                logger.warning(f"[Notification] Error: Failed to parse created_at '{created_at_str}' for notification {data.get('id')}: {e}")
                logger.debug(f"[Notification] Using current time as fallback")
                created_at = datetime.now()
        elif isinstance(created_at_str, datetime):
            created_at = created_at_str
        else:
            logger.warning(f"[Notification] Warning: created_at has unexpected type {type(created_at_str)} for notification {data.get('id')}")
            created_at = datetime.now()
        
        # Parse read_at with validation
        read_at_str = data.get('read_at')
        if read_at_str is None:
            read_at = None
        elif isinstance(read_at_str, str):
            try:
                read_at = datetime.fromisoformat(read_at_str)
            except (ValueError, TypeError) as e:
                logger.warning(f"[Notification] Warning: Failed to parse read_at '{read_at_str}': {e}")
                read_at = None
        elif isinstance(read_at_str, datetime):
            read_at = read_at_str
        else:
            logger.warning(f"[Notification] Warning: read_at has unexpected type {type(read_at_str)}")
            read_at = None
        
        return cls(
            id=data['id'],
            user_id=data['user_id'],
            notification_type=notification_type,
            title=data['title'],
            message=data['message'],
            created_at=created_at,
            read=data.get('read', False),
            read_at=read_at,
            data=data.get('data', {})
        )
    
    def mark_as_read(self):
        """Mark notification as read"""
        self.read = True
        self.read_at = datetime.now()
    
    @property
    def icon(self) -> str:
        """Get appropriate icon for notification type"""
        icons = {
            NotificationType.ATTENDANCE_ABSENT_SCHEDULED: "❌",
            NotificationType.ATTENDANCE_ABSENT_UNSCHEDULED: "⚠️",
            NotificationType.ATTENDANCE_PRESENT_SCHEDULED: "✅",
            NotificationType.ATTENDANCE_PRESENT_UNSCHEDULED: "✓",
            NotificationType.ASSIGNMENT_NEW: "📝",
            NotificationType.ASSIGNMENT_DUE_24H: "⏰",
            NotificationType.ASSIGNMENT_DUE_3D: "📅",
            NotificationType.ASSIGNMENT_OVERDUE: "🚨",
            NotificationType.SYSTEM_INFO: "ℹ️",
            NotificationType.SYSTEM_WARNING: "⚠️",
        }
        return icons.get(self.notification_type, "🔔")
    
    @property
    def category(self) -> str:
        """Get category for grouping"""
        if "ATTENDANCE" in self.notification_type.name:
            return "attendance"
        elif "ASSIGNMENT" in self.notification_type.name:
            return "assignment"
        else:
            return "system"
