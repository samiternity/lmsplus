"""
Assignment Notification Service
Handles detection and notification of assignment-related events.
"""
import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Dict, Optional, Any
import logging
from copy import deepcopy

from app.services.notification_service import get_notification_service, record_scheduled_notification_error
from app.models.notification_models import NotificationType, notification_preference_enabled
from app.utils.assignment_categorization import submission_cutoff
from app.utils.assignment_identity import (
    assignment_title_identity, identity_index, migrate_tracking, normalize_label,
    notification_timestamp, reconcile_tracking,
)

logger = logging.getLogger(__name__)


class AssignmentNotificationService:
    """Service for detecting and sending assignment notifications"""
    
    def __init__(self, data_dir: str = None):
        """Initialize assignment notification service"""
        if data_dir is None:
            base_dir = Path(__file__).parent.parent.parent
            data_dir = base_dir / 'data' / 'assignment_tracking'
        
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
    
    def _load_tracked_assignments(self, account_id: str) -> Dict[str, Any]:
        """Load previously tracked assignments for an account"""
        try:
            from app.utils.data_manager import data_manager
            unified = data_manager.get_unified_dashboard(account_id, bypass_cache=True) or {}
            return unified.get('assignment_tracking_v2') or unified.get(
                'assignment_tracking', {'known_assignments': [], 'notified_deadlines': {}})
        except Exception as e:
            logger.error(f"Error loading tracked assignments: {e}")
            raise RuntimeError('Failed to load assignment notification tracking') from e
            
    def _save_tracked_assignments(self, account_id: str, data: Dict[str, Any]) -> bool:
        """Save tracked assignments for an account as an atomic slice"""
        try:
            from app.utils.data_manager import data_manager
            payload = {
                'assignment_tracking_v2' if data.get('identity_version') == 2 else 'assignment_tracking': data,
                'last_updated': datetime.utcnow().isoformat() + 'Z',
                'legacy_migrated': True
            }
            if data.get('identity_version') == 2:
                # Clear the checkpoint atomically with the successful migration write.
                payload['assignment_tracking_migration_snapshot'] = None
            return data_manager.save_unified_dashboard(account_id, payload, merge=True)
        except Exception as e:
            logger.error(f"Error saving tracked assignments: {e}")
            return False
    
    def _should_notify_based_on_frequency(self, assignment_id: str, notification_type: str, 
                                          notified_deadlines: Dict, now: datetime, hours_until_due: float = None) -> bool:
        """
        Check if enough time has passed to send a notification based on frequency rules.
        
        Args:
            assignment_id: Assignment identifier
            notification_type: Type of notification ('new', '24h', '3d', 'overdue')
            notified_deadlines: Dictionary of notification tracking data
            now: Current datetime
            
        Returns:
            True if notification should be sent, False otherwise
        """
        # Frequency rules by notification type
        FREQUENCY_RULES = {
            'new': {'notify_once': True},
            'overdue': {'notify_once': True},
            '24h': {'periodic': True, 'interval_hours': 4},  # Urgent: every 3-6 hours (using 4)
            '3d': {'notify_once': True}  # Non-urgent: notify once
        }
        
        deadline_key = f"{assignment_id}_{notification_type}"
        
        # If never notified, allow notification
        if deadline_key not in notified_deadlines:
            return True
        
        # Get tracking data for this deadline
        tracking_info = notified_deadlines[deadline_key]
        
        # Get the rule for this notification type
        rule = FREQUENCY_RULES.get(notification_type, {'notify_once': True})
        if notification_type == '24h' and hours_until_due is not None and hours_until_due <= 3:
            rule = {'periodic': True, 'interval_hours': 1}
        
        # Handle legacy format (string timestamp)
        if isinstance(tracking_info, str):
            # Legacy format: just a timestamp string
            # For periodic notifications, check if enough time has passed
            if rule.get('periodic', False):
                try:
                    last_notified = datetime.fromisoformat(tracking_info)
                    hours_since_last = (now - last_notified).total_seconds() / 3600
                    interval_hours = rule.get('interval_hours', 4)
                    return hours_since_last >= interval_hours
                except (ValueError, TypeError):
                    # If parsing fails, allow notification
                    return True
            else:
                # For notify_once, already notified
                return False
        
        # New format: dictionary with metadata
        if rule.get('notify_once', False):
            # Notify only once - already notified
            return False
        
        if rule.get('periodic', False):
            # Periodic notifications - check if enough time has passed
            last_notified_str = tracking_info.get('last_notified')
            if not last_notified_str:
                return True
            
            try:
                last_notified = datetime.fromisoformat(last_notified_str)
                hours_since_last = (now - last_notified).total_seconds() / 3600
                interval_hours = rule.get('interval_hours', 4)
                
                # Send reminder if enough time has passed
                return hours_since_last >= interval_hours
            except (ValueError, TypeError):
                # If parsing fails, allow notification
                return True
        
        # Default: don't notify
        return False
    
    def _update_notification_tracking(self, deadline_key: str, notified_deadlines: Dict, now: datetime) -> None:
        """
        Update tracking data after sending a notification.
        
        Args:
            deadline_key: Key for the notification (e.g., 'asn_001_24h')
            notified_deadlines: Dictionary of notification tracking data
            now: Current datetime
        """
        if deadline_key not in notified_deadlines:
            # First notification for this deadline
            notified_deadlines[deadline_key] = {
                'first_notified': now.isoformat(),
                'last_notified': now.isoformat(),
                'count': 1
            }
        else:
            # Subsequent notification - update last_notified and increment count
            tracking_info = notified_deadlines[deadline_key]
            
            # Handle legacy format (string timestamp) - convert to new format
            if isinstance(tracking_info, str):
                notified_deadlines[deadline_key] = {
                    'first_notified': tracking_info,
                    'last_notified': now.isoformat(),
                    'count': 2
                }
            else:
                # Update existing tracking info
                tracking_info['last_notified'] = now.isoformat()
                tracking_info['count'] = tracking_info.get('count', 1) + 1
    
    def check_and_notify(self, user_id: str, assignments: List[Any], user=None, account_id: str = None, send_push: bool = True, urgent_only: bool = False,
                         *, snapshot: Optional[Dict] = None, previous_snapshot: Optional[Dict] = None,
                         complete_scrape: bool = False) -> Dict[str, int]:
        """
        Check assignments for notification triggers and send notifications.
        
        Args:
            user_id: User ID to send notifications to
            assignments: List of LMSAssignment objects
            user: User object to check preferences
            account_id: Account ID for per-account tracking (falls back to user_id)
            send_push: Whether to send FCM push notifications (default True for background service)
            snapshot: Validated semester and assignment metadata for semantic identities.
            previous_snapshot: Snapshot captured before the current scrape replaced it.
            complete_scrape: Allow migration only after a complete fresh scrape.
            
        Returns:
            Dictionary with counts of notifications sent by type
        """
        # Dashboard reads must not consume the Discord reminder frequency state.
        if not send_push:
            return {'new': 0, 'due_24h': 0, 'due_3d': 0, 'overdue': 0}
        if not assignments and not complete_scrape:
            return {'new': 0, 'due_24h': 0, 'due_3d': 0, 'overdue': 0}
        if snapshot and (snapshot.get('error') or snapshot.get('failed_courses') or snapshot.get('warning')):
            raise ValueError('Cannot notify from an incomplete assignment scrape')
        
        # Use account_id for tracking to avoid cross-account pollution
        tracking_key = account_id or user_id
        
        notification_service = get_notification_service()
        tracking_data = self._load_tracked_assignments(tracking_key)
        use_v2 = tracking_data.get('identity_version') == 2
        valid_identities = {}
        if snapshot is not None:
            # Old snapshots may predate readable semester metadata. Their IDs remain
            # legacy until a fresh scrape, but duplicate rows still must be rejected.
            validation_snapshot = dict(snapshot)
            if not use_v2 and not complete_scrape and not snapshot.get('latest_semester_name'):
                validation_snapshot['latest_semester_name'] = 'legacy snapshot'
            valid_identities, invalid = identity_index(tracking_key, validation_snapshot,
                                                       tracking_data.get('known_assignments', []))
            if invalid:
                logger.warning('Skipping %d invalid or conflicting assignment identities', invalid)
            if use_v2 and previous_snapshot:
                reconciled = reconcile_tracking(tracking_key, tracking_data, snapshot, previous_snapshot)
                if reconciled != tracking_data:
                    if not self._save_tracked_assignments(tracking_key, reconciled):
                        raise RuntimeError('Failed to save assignment identity reconciliation')
                    tracking_data = reconciled
            if complete_scrape and not urgent_only and not use_v2:
                if invalid or not snapshot.get('latest_semester_name'):
                    raise ValueError('Cannot migrate invalid assignment metadata')
                tracking_data = migrate_tracking(tracking_key, tracking_data, snapshot, previous_snapshot)
                # Commit the baseline before delivering reminders. A failed save must remain retryable.
                if not self._save_tracked_assignments(tracking_key, tracking_data):
                    raise RuntimeError('Failed to save assignment tracking migration')
                logger.info('Assignment tracking migration completed; baselined %d assignments', len(valid_identities))
                use_v2 = True
        elif complete_scrape or use_v2:
            raise ValueError('Semantic assignment tracking requires snapshot metadata')
        valid_by_title = {
            assignment_title_identity(tracking_key, validation_snapshot.get('latest_semester_name'), row): key
            for key, row in valid_identities.items()
        } if snapshot is not None else {}
        initial_known = set(tracking_data.get('known_assignments', []))
        known_assignments = set(initial_known)
        initial_notified = deepcopy(tracking_data.get('notified_deadlines', {}))
        notified_deadlines = deepcopy(initial_notified)
        
        counts = {'new': 0, 'due_24h': 0, 'due_3d': 0, 'overdue': 0}
        
        import pytz
        # Deadlines from LMS are parsed as naive PKT times. Match that exactly.
        now = datetime.now(pytz.timezone('Asia/Karachi')).replace(tzinfo=None)
        
        for assignment in assignments:
            try:
                course_label = normalize_label(getattr(assignment, 'course_name', None))
                if not course_label or course_label == normalize_label(getattr(assignment, 'course_code', None)):
                    logger.warning('Skipping assignment with an unverified course label')
                    continue
                # Get assignment ID using robust attributes (LMSAssignment uses assignment_id and assignment_no, not id)
                lms_id = getattr(assignment, 'assignment_id', None)
                fallback_id = f"{getattr(assignment, 'assignment_no', '')}_{assignment.title}_{assignment.course_code}"
                assignment_id = lms_id if lms_id else fallback_id
                notification_assignment_id = assignment_id
                if snapshot is not None:
                    row = assignment.to_dict() if hasattr(assignment, 'to_dict') else assignment
                    validation_id = valid_by_title.get(assignment_title_identity(
                        tracking_key, validation_snapshot.get('latest_semester_name'), row))
                    if not validation_id:
                        continue
                if use_v2:
                    assignment_id = validation_id
                
                # Use robust categorization to determine if it's submitted
                from app.utils.assignment_categorization import categorize_assignment
                cat, _ = categorize_assignment(assignment.to_dict() if hasattr(assignment, 'to_dict') else assignment)
                is_submitted = (cat == 'submitted')
                
                # Check for new assignments
                if assignment_id not in known_assignments:
                    record_assignment = True
                    # Edge case: If the assignment is already submitted, skip the "New Assignment" notification
                    if not urgent_only and not is_submitted and cat != 'missed':
                        # Check frequency rules for new assignment notifications
                        if self._should_notify_based_on_frequency(assignment_id, 'new', notified_deadlines, now):
                            if self._should_notify(user, 'new_assignment'):
                                course_display = getattr(assignment, 'course_name', None) or getattr(assignment, 'course_code', '')
                                asn_title = getattr(assignment, 'title', None)
                                asn_no = getattr(assignment, 'assignment_no', None)
                                asn_label = asn_title if asn_title else (f"Assignment {asn_no}" if asn_no else "New Assignment")
                                asn_url = getattr(assignment, 'url', None) or getattr(assignment, 'link', None)
                                notification = notification_service.send_notification(
                                    user_id=user_id,
                                    notification_type=NotificationType.ASSIGNMENT_NEW,
                                    title=f"New Assignment — {course_display}",
                                    message=f"{asn_label} has been posted",
                                    data={'assignment_id': notification_assignment_id, 'course_name': course_display, 'url': asn_url},
                                    send_push=send_push
                                )
                                if notification is None:
                                    record_assignment = False
                                else:
                                    counts['new'] += 1
                                    # Update tracking data
                                    self._update_notification_tracking(f"{assignment_id}_new", notified_deadlines, now)
                    if record_assignment and not urgent_only:
                        known_assignments.add(assignment_id)
                
                # Check deadline-based notifications
                deadline = self._parse_deadline(assignment)
                
                if deadline and not is_submitted:
                    hours_until_due = (deadline - now).total_seconds() / 3600
                    course_display = getattr(assignment, 'course_name', None) or getattr(assignment, 'course_code', '')
                    asn_title = getattr(assignment, 'title', None)
                    asn_no = getattr(assignment, 'assignment_no', None)
                    asn_label = asn_title if asn_title else (f"Assignment {asn_no}" if asn_no else "An assignment")
                    
                    # Overdue
                    if hours_until_due <= 0:
                        deadline_key = f"{assignment_id}_overdue"
                        # Check frequency rules for overdue notifications
                        if self._should_notify_based_on_frequency(assignment_id, 'overdue', notified_deadlines, now):
                            if self._should_notify(user, 'overdue_warning'):
                                asn_url = getattr(assignment, 'url', None) or getattr(assignment, 'link', None)
                                notification = notification_service.send_notification(
                                    user_id=user_id,
                                    notification_type=NotificationType.ASSIGNMENT_OVERDUE,
                                    title=f"Missed Deadline — {course_display}",
                                    message=f"{asn_label} deadline has passed",
                                    data={'assignment_id': notification_assignment_id, 'course_name': course_display, 'deadline': deadline.isoformat(), 'url': asn_url},
                                    send_push=send_push
                                )
                                if notification is None:
                                    continue
                                counts['overdue'] += 1
                                # Update tracking data
                                self._update_notification_tracking(deadline_key, notified_deadlines, now)

                    # Due in 24 hours
                    elif 0 <= hours_until_due <= 24:
                        deadline_key = f"{assignment_id}_24h"
                        # Check frequency rules for urgent (24h) notifications - periodic reminders
                        if self._should_notify_based_on_frequency(assignment_id, '24h', notified_deadlines, now, hours_until_due):
                            if self._should_notify(user, 'due_24h'):
                                hours_left = int(hours_until_due)
                                time_str = f"{hours_left}h" if hours_left > 0 else "less than 1h"
                                asn_url = getattr(assignment, 'url', None) or getattr(assignment, 'link', None)
                                notification = notification_service.send_notification(
                                    user_id=user_id,
                                    notification_type=NotificationType.ASSIGNMENT_DUE_24H,
                                    title=f"Due in {time_str} — {course_display}",
                                    message=f"{asn_label} is due very soon",
                                    data={'assignment_id': notification_assignment_id, 'course_name': course_display, 'deadline': deadline.isoformat(), 'url': asn_url},
                                    send_push=send_push
                                )
                                if notification is None:
                                    continue
                                counts['due_24h'] += 1
                                # Update tracking data
                                self._update_notification_tracking(deadline_key, notified_deadlines, now)
                    
                    # Due in 3 days
                    elif 24 < hours_until_due <= 72:
                        if not urgent_only:
                            deadline_key = f"{assignment_id}_3d"
                            # Check frequency rules for non-urgent (3d) notifications - notify once
                            if self._should_notify_based_on_frequency(assignment_id, '3d', notified_deadlines, now):
                                if self._should_notify(user, 'due_3d'):
                                    days_left = int(hours_until_due / 24)
                                    asn_url = getattr(assignment, 'url', None) or getattr(assignment, 'link', None)
                                    notification = notification_service.send_notification(
                                        user_id=user_id,
                                        notification_type=NotificationType.ASSIGNMENT_DUE_3D,
                                        title=f"Due in {days_left} day{'s' if days_left != 1 else ''} — {course_display}",
                                        message=f"{asn_label} deadline is approaching",
                                        data={'assignment_id': notification_assignment_id, 'course_name': course_display, 'deadline': deadline.isoformat(), 'url': asn_url},
                                        send_push=send_push
                                    )
                                    if notification is None:
                                        continue
                                    counts['due_3d'] += 1
                                    # Update tracking data
                                    self._update_notification_tracking(deadline_key, notified_deadlines, now)
                
            except Exception as e:
                record_scheduled_notification_error()
                logger.error(f"Error processing assignment notification: {e}")
                continue
        
        # Only persist to Firestore if tracking state actually changed (new assignment discovered or notification sent)
        has_tracking_changed = (
            known_assignments != initial_known or
            notified_deadlines != initial_notified or
            any(counts.values())
        )
        if has_tracking_changed:
            tracking_data['known_assignments'] = list(known_assignments)
            tracking_data['notified_deadlines'] = notified_deadlines
            tracking_data['last_check'] = now.isoformat()
            if not self._save_tracked_assignments(tracking_key, tracking_data):
                raise RuntimeError('Failed to save assignment notification progress')
        
        # Web push notifications are now sent discretely via notification_service.send_notification
        
        return counts
    
    def _parse_deadline(self, assignment) -> Optional[datetime]:
        """Parse deadline from assignment object using the LMS-aware parser"""
        try:
            deadline_str = getattr(assignment, 'deadline', None) or getattr(assignment, 'due_date', None)
            if not deadline_str:
                return None
            return submission_cutoff(deadline_str)
        except Exception as e:
            record_scheduled_notification_error()
            logger.error(f"Error parsing deadline: {e}")
            return None
    
    def _should_notify(self, user, notification_type: str) -> bool:
        """Check if user has enabled a specific notification type"""
        if not user or not hasattr(user, 'notification_preferences'):
            return True  # Default to sending if no preferences set
        prefs = user.notification_preferences
        if not prefs:
            return True
        return notification_preference_enabled(prefs.get(notification_type, True))  # Legacy opt-out default
    
    def cleanup_old_tracking(self, account_id: str, days: int = 30) -> int:
        """Clean up old notification tracking data"""
        tracking_data = self._load_tracked_assignments(account_id)
        notified_deadlines = tracking_data.get('notified_deadlines', {})
        
        cutoff = datetime.utcnow() + timedelta(hours=5) - timedelta(days=days)
        original_count = len(notified_deadlines)
        
        # Remove old entries
        cleaned = {}
        for key, timestamp in notified_deadlines.items():
            try:
                entry_time = notification_timestamp(timestamp)
                if entry_time is not None and entry_time > cutoff:
                    cleaned[key] = timestamp
            except (ValueError, TypeError):
                pass
        
        if len(cleaned) != original_count:
            tracking_data['notified_deadlines'] = cleaned
            if not self._save_tracked_assignments(account_id, tracking_data):
                raise RuntimeError('Failed to save assignment tracking cleanup')
        
        return original_count - len(cleaned)


# Singleton instance
_assignment_notification_service = None

def get_assignment_notification_service() -> AssignmentNotificationService:
    """Get the singleton assignment notification service instance"""
    global _assignment_notification_service
    if _assignment_notification_service is None:
        _assignment_notification_service = AssignmentNotificationService()
    return _assignment_notification_service
