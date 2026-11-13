import logging
logger = logging.getLogger(__name__)

import json
import os
import uuid
import time
from datetime import datetime
import threading

class GroupManager:
    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super(GroupManager, cls).__new__(cls)
                cls._instance._initialize()
            return cls._instance

    def _initialize(self):
        self.data_dir = os.path.join(os.path.dirname(__file__), '..', 'data')
        self.groups_file = os.path.join(self.data_dir, 'groups.json')
        self.audit_logs_file = os.path.join(self.data_dir, 'audit_logs.json')
        
        # File IO lock for thread safety
        self.io_lock = threading.RLock()
        
        self.ensure_directories()

    def ensure_directories(self):
        """Ensure all necessary directories and files exist"""
        with self.io_lock:
            os.makedirs(self.data_dir, exist_ok=True)
            
            if not os.path.exists(self.groups_file):
                with open(self.groups_file, 'w') as f:
                    json.dump({}, f)
                    
            if not os.path.exists(self.audit_logs_file):
                with open(self.audit_logs_file, 'w') as f:
                    json.dump({}, f)

    def load_groups(self):
        """Load all groups from JSON file"""
        with self.io_lock:
            try:
                with open(self.groups_file, 'r') as f:
                    return json.load(f)
            except Exception as e:
                logger.warning(f"Error loading groups: {e}")
                return {}

    def save_groups(self, groups_data):
        """Save groups to JSON file"""
        with self.io_lock:
            try:
                with open(self.groups_file, 'w') as f:
                    json.dump(groups_data, f, indent=2)
                return True
            except Exception as e:
                logger.warning(f"Error saving groups: {e}")
                return False

    def load_audit_logs(self):
        """Load all audit logs from JSON file"""
        with self.io_lock:
            try:
                with open(self.audit_logs_file, 'r') as f:
                    return json.load(f)
            except Exception as e:
                logger.warning(f"Error loading audit logs: {e}")
                return {}

    def save_audit_logs(self, audit_logs_data):
        """Save audit logs to JSON file"""
        with self.io_lock:
            try:
                with open(self.audit_logs_file, 'w') as f:
                    json.dump(audit_logs_data, f, indent=2)
                return True
            except Exception as e:
                logger.warning(f"Error saving audit logs: {e}")
                return False

    def get_group(self, group_id):
        """Get a specific group by ID"""
        groups = self.load_groups()
        return groups.get(group_id)

    def get_owner(self, group):
        owner = group.get('owner')
        if owner:
            return owner

        # Older groups recorded their creator in the creation audit entry.
        for entry in self.load_audit_logs().get(group['group_id'], []):
            if entry.get('action') == 'Created Group':
                return entry['actor'].strip().lower()
        return None

    def get_group_by_assignment_and_owner(self, assignment_id, course_id, owner_enrollment):
        """Find the group created by this enrollment for the exact assignment."""
        owner = owner_enrollment.strip().lower()
        return next((group for group in self.load_groups().values()
                     if group.get('assignment_id') == assignment_id
                     and group.get('course_id') == course_id
                     and self.get_owner(group) == owner), None)

    def create_group(self, assignment_id, course_id, creator_enrollment, partner_enrollments):
        """
        Create a new group with the creator as 'Accepted' and partners as 'Pending'.
        If a group already exists for this user and assignment, it updates the roster and returns the existing group_id.
        """
        with self.io_lock:
            existing_group = self.get_group_by_assignment_and_owner(assignment_id, course_id, creator_enrollment)
            groups = self.load_groups()
            
            if existing_group:
                group_id = existing_group['group_id']
                group = groups[group_id]
                roster = group['roster']
                
                # Add any new partners as Pending
                for partner in partner_enrollments:
                    p_lower = partner.strip().lower()
                    if p_lower and p_lower not in roster:
                        roster[p_lower] = 'Pending'
                
                if not self.save_groups(groups):
                    raise OSError('Could not update group.')
                return group_id
            
            # Create new group
            group_id = str(uuid.uuid4())
            roster = {}
            
            # Creator is automatically accepted
            roster[creator_enrollment.lower()] = 'Accepted'
            
            # Partners are pending
            for partner in partner_enrollments:
                if partner and partner.strip().lower() != creator_enrollment.lower():
                    roster[partner.strip().lower()] = 'Pending'
            
            new_group = {
                'group_id': group_id,
                'assignment_id': str(assignment_id),
                'course_id': str(course_id),
                'owner': creator_enrollment.strip().lower(),
                'roster': roster,
                'state': 'idle',  # 'idle', 'uploading', 'deleting'
                'created_at': datetime.now().isoformat()
            }
            
            groups[group_id] = new_group
            if not self.save_groups(groups):
                raise OSError('Could not create group.')
            
            # Initialize audit log
            audit_logs = self.load_audit_logs()
            audit_logs[group_id] = []
            self.save_audit_logs(audit_logs)
            
            self.append_audit_log(
                group_id=group_id, 
                actor=creator_enrollment, 
                action="Created Group", 
                target_file="-", 
                sync_status="Success"
            )
            
            return group_id

    def update_member_status(self, group_id, enrollment_number, new_status):
        """Update a member's status (e.g. from Pending to Accepted)"""
        with self.io_lock:
            groups = self.load_groups()
            group = groups.get(group_id)
            if not group:
                return False, "Group not found"
            
            if group.get('state', 'idle') != 'idle':
                return False, "A group operation is in progress"
            enrollment_lower = enrollment_number.strip().lower()
            if enrollment_lower not in group.get('roster', {}):
                return False, "User not in group"
            
            group['roster'][enrollment_lower] = new_status
            self.save_groups(groups)
            return True, "Success"

    def delete_group(self, group_id):
        """Deletes a group and its audit log completely"""
        with self.io_lock:
            groups = self.load_groups()
            if group_id in groups:
                del groups[group_id]
                self.save_groups(groups)
            
            audit_logs = self.load_audit_logs()
            if group_id in audit_logs:
                del audit_logs[group_id]
                self.save_audit_logs(audit_logs)
            
            return True

    def start_operation(self, group_id, owner, state, enrollments=None):
        """Authorize and reserve a group before dispatching an LMS operation."""
        with self.io_lock:
            groups = self.load_groups()
            group = groups.get(group_id)
            if not group or self.get_owner(group) != owner:
                return None
            roster = group.get('roster', {})
            if roster.get(owner) != 'Accepted':
                return None
            if enrollments and any(roster.get(enrollment) != 'Accepted' for enrollment in enrollments):
                return None
            if group.get('state', 'idle') != 'idle':
                raise ValueError('A group operation is in progress.')
            group['state'] = state
            if not self.save_groups(groups):
                raise ValueError('Could not reserve the group.')
            return group

    def set_group_state(self, group_id, state):
        """Safely transitions the state of a group for concurrency lock"""
        with self.io_lock:
            groups = self.load_groups()
            group = groups.get(group_id)
            if not group:
                return False, "Group not found"
                
            # Basic state machine validation
            current_state = group.get('state', 'idle')
            if state != 'idle' and current_state != 'idle':
                return False, f"Group is currently locked ({current_state})"
                
            group['state'] = state
            self.save_groups(groups)
            return True, "State updated"

    def append_audit_log(self, group_id, actor, action, target_file, sync_status):
        """Append a record to the group's immutable ledger"""
        with self.io_lock:
            logs = self.load_audit_logs()
            group_logs = logs.get(group_id, [])
            
            record = {
                'timestamp': datetime.now().isoformat(),
                'actor': actor,
                'action': action,
                'target_file': target_file,
                'sync_status': sync_status
            }
            
            group_logs.append(record)
            logs[group_id] = group_logs
            self.save_audit_logs(logs)

    def get_user_groups(self, enrollment_number):
        """Get all groups that a specific user is a part of"""
        groups = self.load_groups()
        enrollment_lower = enrollment_number.lower()
        
        user_groups = []
        for g_id, group in groups.items():
            if enrollment_lower in group.get('roster', {}):
                user_groups.append(group)
                
        # Sort by newest first
        return sorted(user_groups, key=lambda x: x.get('created_at', ''), reverse=True)
