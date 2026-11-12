import logging
logger = logging.getLogger(__name__)

"""
Notification Routes
API endpoints for managing user notifications.
"""
from flask import Blueprint, jsonify, request
from flask_login import login_required, current_user

from app.services.notification_service import get_notification_service
from app.models.notification_models import NotificationType


notifications_bp = Blueprint('notifications', __name__, url_prefix='/api/notifications')


@notifications_bp.route('', methods=['GET'])
@login_required
def get_notifications():
    """Get notifications for current user"""
    service = get_notification_service()
    
    # Query parameters
    unread_only = request.args.get('unread', 'false').lower() == 'true'
    limit = min(int(request.args.get('limit', 50)), 100)
    category = request.args.get('category')  # 'attendance', 'assignment', 'system'
    
    notifications = service.get_notifications(
        user_id=current_user.id,
        unread_only=unread_only,
        limit=limit,
        category=category
    )
    
    return jsonify({
        'success': True,
        'notifications': [n.to_dict() for n in notifications],
        'count': len(notifications)
    })


@notifications_bp.route('/unread-count', methods=['GET'])
@login_required
def get_unread_count():
    """Get count of unread notifications"""
    service = get_notification_service()
    count = service.get_unread_count(current_user.id)
    
    return jsonify({
        'success': True,
        'unread_count': count
    })


@notifications_bp.route('/<notification_id>/read', methods=['POST'])
@login_required
def mark_as_read(notification_id):
    """Mark a specific notification as read"""
    service = get_notification_service()
    
    if service.mark_as_read(current_user.id, notification_id):
        return jsonify({'success': True})
    else:
        return jsonify({'success': False, 'error': 'Notification not found'}), 404


@notifications_bp.route('/read-all', methods=['POST'])
@login_required
def mark_all_as_read():
    """Mark all notifications as read"""
    service = get_notification_service()
    
    if service.mark_all_as_read(current_user.id):
        return jsonify({'success': True})
    else:
        return jsonify({'success': False, 'error': 'Failed to mark all as read'}), 500


@notifications_bp.route('/<notification_id>', methods=['DELETE'])
@login_required
def delete_notification(notification_id):
    """Delete a specific notification"""
    service = get_notification_service()
    
    if service.delete_notification(current_user.id, notification_id):
        return jsonify({'success': True})
    else:
        return jsonify({'success': False, 'error': 'Notification not found'}), 404



