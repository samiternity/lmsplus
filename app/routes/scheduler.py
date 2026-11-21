"""
Scheduler endpoint — called by Cloud Scheduler (or any HTTP cron service).
Protected by a shared secret so only the scheduler can trigger it.
"""
import os
import logging
from flask import Blueprint, request, jsonify
from app.services.notification_scheduler import DAILY_BATCH_COUNT

logger = logging.getLogger(__name__)

scheduler_bp = Blueprint('scheduler', __name__, url_prefix='/api/internal')

_SECRET = os.environ.get('SCHEDULER_SECRET', '')


def _authorized(allow_worker_header: bool = False) -> bool:
    """Check the Authorization header against SCHEDULER_SECRET."""
    if not _SECRET:
        # Secret not configured — block all requests to avoid accidental exposure
        logger.error('SCHEDULER_SECRET not set — rejecting scheduler request')
        return False
    auth = request.headers.get('Authorization', '')
    return auth == f'Bearer {_SECRET}' or (
        allow_worker_header and request.headers.get('X-Scheduler-Secret') == _SECRET
    )


@scheduler_bp.route('/run-notifications', methods=['POST'])
def run_notifications():
    """
    Trigger background notification checks for a specific micro-batch.
    Handles the regular batches and the minute-by-minute catch-up checks.
    """
    if not _authorized():
        return jsonify({'error': 'Unauthorized'}), 401

    try:
        from flask import current_app
        scheduler = getattr(current_app, 'notification_scheduler', None)
        
        # Optional: Allow passing a specific bucket, otherwise defaults to current minute
        bucket = request.args.get('bucket', type=int)
        if 'bucket' in request.args and (bucket is None or not 0 <= bucket < DAILY_BATCH_COUNT):
            return jsonify({'success': False, 'error': f'Batch number must be between 0 and {DAILY_BATCH_COUNT - 1}'}), 400
        
        if scheduler:
            # Synchronous execution
            summary = scheduler.run_scheduled_notifications(bucket=bucket)
            if 'error' in summary and 'usage_seconds' not in summary:
                return jsonify({'success': False, 'mode': 'micro_batch', 'summary': summary}), 503
            return jsonify({'success': True, 'mode': 'micro_batch', 'summary': summary})
        else:
            return jsonify({'success': False, 'error': 'Scheduler not initialized'}), 500
    except Exception as e:
        logger.error(f'Scheduler run-notifications error: {e}')
        return jsonify({'success': False, 'error': str(e)}), 500


@scheduler_bp.route('/status', methods=['GET'])
def scheduler_status():
    """
    Get status of the kill switch and free tier usage.
    Useful for monitoring and debugging.
    """
    if not _authorized():
        return jsonify({'error': 'Unauthorized'}), 401
    
    try:
        from flask import current_app
        scheduler = getattr(current_app, 'notification_scheduler', None)
        
        if not scheduler:
            return jsonify({
                'success': True,
                'scheduler': {
                    'message': 'Scheduler not initialized.'
                }
            })
        
        status = scheduler.get_status()
        return jsonify({
            'success': True,
            'scheduler': status
        })
    except Exception as e:
        logger.error(f'Scheduler status error: {e}')
        return jsonify({'success': False, 'error': str(e)}), 500


@scheduler_bp.route('/trigger', methods=['POST'])
def trigger_now():
    """
    Legacy endpoint or manual trigger. Maps to /run-notifications.
    """
    return run_notifications()


@scheduler_bp.route('/urgent-check', methods=['POST'])
def urgent_check():
    """
    Lightweight urgent assignment check using only cached snapshots.
    Called hourly by Cloud Scheduler; includes overdue warnings.
    """
    if not _authorized():
        return jsonify({'error': 'Unauthorized'}), 401

    try:
        from flask import current_app
        scheduler = getattr(current_app, 'notification_scheduler', None)

        if scheduler:
            summary = scheduler._run_urgent_assignment_check()
            return jsonify({'success': True, 'mode': 'urgent_check', 'summary': summary})
        else:
            return jsonify({'success': False, 'error': 'Scheduler not initialized'}), 500

    except Exception as e:
        logger.error(f'Urgent-check error: {e}')
        return jsonify({'success': False, 'error': str(e)}), 500


@scheduler_bp.route('/simulate-all', methods=['POST'])
def simulate_all():
    """
    Full simulation endpoint — bypasses the bucket filter.
    Processes EVERY user synchronously. 
    WARNING: For large userbases, this will timeout Cloud Run.
    """
    if not _authorized():
        return jsonify({'error': 'Unauthorized'}), 401

    try:
        from flask import current_app
        scheduler = getattr(current_app, 'notification_scheduler', None)

        if scheduler:
            logger.info('simulate-all triggered — running full force-mode sweep')
            summary = scheduler._run_notifications(force=True)
            return jsonify({'success': True, 'mode': 'simulate_all', 'summary': summary})
        else:
            return jsonify({'success': False, 'error': 'Scheduler not initialized'}), 500

    except Exception as e:
        logger.error(f'simulate-all error: {e}')
        return jsonify({'success': False, 'error': str(e)}), 500

@scheduler_bp.route('/discord/send_dm', methods=['POST'])
def send_discord_dm():
    """
    Worker endpoint to process enqueued Discord notifications via Google Cloud Tasks.
    This safely executes outside the main web request cycle.
    """
    if not _authorized(allow_worker_header=True):
        return jsonify({'error': 'Unauthorized'}), 401
        
    try:
        payload = request.get_json()
        if not payload:
            return jsonify({'error': 'Missing JSON payload'}), 400
            
        from app.services.discord_service import _process_discord_notification_sync
        success = _process_discord_notification_sync(payload)
        
        if success:
            return jsonify({'success': True}), 200
        else:
            return jsonify({'error': 'Failed to send DM'}), 500
    except Exception as e:
        logger.error(f'Discord send_dm error: {e}')
        return jsonify({'success': False, 'error': str(e)}), 500

