"""
Notification Scheduler Service
Synchronous service designed to be called by Google Cloud Scheduler endpoints.
Processes users in 60 micro-batches (1 batch per minute) to gently distribute load 
and bypass firewall/timeout limits while strictly staying within the Cloud Run Free Tier.
"""
import logging
import time
from datetime import datetime
from typing import List, Dict, Any
import pytz

logger = logging.getLogger(__name__)

# Pakistan timezone (UTC+5, no DST)
PAKISTAN_TZ = pytz.timezone('Asia/Karachi')

# Free tier limits for monitoring
FREE_TIER_CPU_HOURS_PER_MONTH = 50.0
# 180,000 seconds = 50 hours. We hard stop at 160,000 seconds (44.4 hours)
FREE_TIER_HARD_STOP_SECONDS = 160000  

# Global instance
_scheduler = None

def get_current_pakistan_time() -> datetime:
    """Get current datetime in Pakistan timezone, using Cloud Scheduler header if present."""
    try:
        from flask import has_request_context, request
        if has_request_context() and hasattr(request, 'headers'):
            # Google Cloud Scheduler passes the intended execution time.
            # E.g., X-CloudScheduler-ScheduleTime: 2023-10-18T20:59:00Z
            schedule_time = request.headers.get('X-CloudScheduler-ScheduleTime')
            if schedule_time:
                # Replace Z with +00:00 for strict ISO format compatibility
                schedule_time = schedule_time.replace('Z', '+00:00')
                dt = datetime.fromisoformat(schedule_time)
                return dt.astimezone(PAKISTAN_TZ)
    except Exception as e:
        logger.warning(f"Failed to parse Cloud Scheduler time: {e}")
        
    return datetime.now(PAKISTAN_TZ)

def get_current_batch_number() -> int:
    """Map current time minute to micro-batch bucket (0-59).
    Uses Cloud Scheduler headers if available to prevent cold-start rollover bugs.
    """
    return get_current_pakistan_time().minute

class NotificationScheduler:
    """Manages synchronous background notification checks."""
    
    def __init__(self, app=None):
        self.app = app
        
    def init_app(self, app):
        """Initialize with Flask app context"""
        self.app = app
        
    def _get_firestore(self):
        """Helper to get the firestore service"""
        try:
            from app.services.firestore_service import get_firestore_service
            fs = get_firestore_service()
            if fs and fs.is_available():
                return fs
        except Exception:
            pass
        return None

    def get_kill_switch_status(self) -> Dict[str, Any]:
        """Check if the kill switch is active due to high free-tier usage."""
        fs = self._get_firestore()
        if not fs:
            # If no firestore, we can't reliably track across container restarts, 
            # assume safe but log warning.
            return {'active': False, 'usage_seconds': 0}
            
        doc = fs.get_document('system_config', 'usage_stats')
        if not doc:
            return {'active': False, 'usage_seconds': 0}
            
        current_month = datetime.now(pytz.UTC).strftime('%Y-%m')
        stats = doc.get(current_month, {})
        usage_seconds = stats.get('total_cpu_seconds', 0)
        
        active = usage_seconds >= FREE_TIER_HARD_STOP_SECONDS
        return {'active': active, 'usage_seconds': usage_seconds}

    def _log_free_tier_usage(self, duration_seconds: float):
        """Persistently log execution time to Firestore to enforce the kill switch."""
        fs = self._get_firestore()
        if not fs:
            return

        try:
            current_month = datetime.now(pytz.UTC).strftime('%Y-%m')
            
            # Use a transaction or simple increment (simple read-modify-write for now)
            doc = fs.get_document('system_config', 'usage_stats') or {}
            
            month_stats = doc.get(current_month, {'total_cpu_seconds': 0, 'batch_runs': 0})
            month_stats['total_cpu_seconds'] += duration_seconds
            month_stats['batch_runs'] += 1
            
            # Check if kill switch was just triggered
            if month_stats['total_cpu_seconds'] >= FREE_TIER_HARD_STOP_SECONDS:
                month_stats['kill_switch_active'] = True
                logger.critical(
                    f"HARD STOP: Free Tier Kill Switch Engaged! "
                    f"Usage reached {month_stats['total_cpu_seconds']}s (Limit: {FREE_TIER_HARD_STOP_SECONDS}s)."
                )
            else:
                month_stats['kill_switch_active'] = False
                
            doc[current_month] = month_stats
            fs.save_document('system_config', 'usage_stats', doc)
            
            logger.info(
                f"Usage Logged: {duration_seconds:.2f}s (Total this month: {month_stats['total_cpu_seconds']:.2f}s)"
            )
        except Exception as e:
            logger.error(f"Failed to log usage stats: {e}")

    def _run_urgent_assignment_check(self) -> str:
        """Run continuous urgent assignment checks using cached data only."""
        kill_status = self.get_kill_switch_status()
        if kill_status['active']:
            return "Skipped: Free tier kill switch active"

        try:
            logger.info("Starting synchronous urgent assignment check (cache only)")
            
            from app.services.background_notification_service import check_cached_urgent_assignments_only
            
            start_time = time.time()
            if self.app:
                with self.app.app_context():
                    summary = check_cached_urgent_assignments_only()
            else:
                summary = check_cached_urgent_assignments_only()
                
            duration = time.time() - start_time
            logger.info(f"Urgent check complete in {duration:.2f}s: {summary}")
            
            # Log usage for kill switch
            self._log_free_tier_usage(duration)
            
            return f"Success: {summary}"
            
        except Exception as e:
            logger.error(f"Error running periodic urgent assignment check: {e}", exc_info=True)
            return f"Error: {e}"

    def _run_notifications(self, force: bool = False, bucket: int = None, max_workers: int = 15) -> Dict[str, Any]:
        """Run evening batch notifications synchronously.
        
        Args:
            force: If True, bypasses bucket logic and processes ALL users.
            bucket: Specific bucket (0-59) to process. If None, uses current minute.
            max_workers: Maximum number of parallel threads (default 15).
        """
        try:
            # COST SAFETY CHECK
            kill_status = self.get_kill_switch_status()
            if kill_status['active']:
                logger.critical(
                    f"HARD STOP: Micro-batch halted. Usage ({kill_status['usage_seconds']}s) "
                    f"exceeds limit ({FREE_TIER_HARD_STOP_SECONDS}s)."
                )
                return {
                    'error': 'Cost safety hard stop triggered',
                    'usage_seconds': kill_status['usage_seconds'],
                    'threshold': FREE_TIER_HARD_STOP_SECONDS
                }
            
            if force:
                batch_number = -1
                now_pakistan = datetime.now(PAKISTAN_TZ)
                logger.info(f"FORCE RUN: Processing ALL users at {now_pakistan.strftime('%I:%M %p')}")
            else:
                batch_number = bucket if bucket is not None else get_current_batch_number()
                now_pakistan = datetime.now(PAKISTAN_TZ)
                logger.info(f"Starting batch {batch_number} at {now_pakistan.strftime('%I:%M %p')}")
            
            # Get users using highly optimized O(1) query
            from app.models.account import User
            if force:
                batch_users = User.list_all()
            else:
                # Optimized fetch - holds fully populated User models
                batch_users = User.get_users_by_batch(batch_number)
            
            logger.info(f"Processing {len(batch_users)} users (batch {batch_number})")
            
            if not batch_users:
                summary_dict = {
                    'mode': 'force_run' if force else f'batch_{batch_number}',
                    'users_processed': 0,
                    'success_count': 0,
                    'error_count': 0,
                    'skipped_count': 0,
                    'notifications_sent': 0,
                    'duration_seconds': 0.0
                }
                if not force:
                    try:
                        self._aggregate_and_fire_daily_summary(batch_number, summary_dict)
                    except Exception as e:
                        logger.error(f"Failed to aggregate daily summary: {e}")
                return summary_dict
            
            success_count = 0
            error_count = 0
            skipped_count = 0
            notification_count = 0
            start_time = time.time()
            
            import concurrent.futures
            
            def scrape_single_user(user_or_id):
                local_n = 0
                local_err = False
                local_skipped = False
                try:
                    if isinstance(user_or_id, str):
                        user = User.get(user_or_id)
                        user_id = user_or_id
                    else:
                        user = user_or_id
                        user_id = getattr(user, 'id', 'unknown')

                    if not user:
                        return (0, True, False)
                    
                    if not user.is_scheduled_check_eligible():
                        logger.debug(f"[{user_id}] Skipped: Discord not connected or no active alerts")
                        return (0, False, True)

                    if user.cms_account:
                        try:
                            from app.utils.lms_logger import register_account_enrollment
                            # Pre-register all account ID variants to avoid runtime cache misses
                            if user.cms_account.cms_username:
                                if user.account_id:
                                    register_account_enrollment(user.account_id, user.cms_account.cms_username)
                                if getattr(user.cms_account, 'account_id', None):
                                    register_account_enrollment(user.cms_account.account_id, user.cms_account.cms_username)
                                if getattr(user, 'id', None):
                                    register_account_enrollment(user.id, user.cms_account.cms_username)
                            
                            if self.app:
                                with self.app.app_context():
                                    local_n = self._process_account(user, user.cms_account)
                            else:
                                local_n = self._process_account(user, user.cms_account)
                        except Exception as e:
                            logger.error(f"Error scraping {getattr(user, 'account_id', 'unknown')} / {user_id}: {e}")
                            local_err = True
                    else:
                        local_skipped = True
                except Exception as e:
                    logger.error(f"Error loading user {user_id}: {e}")
                    local_err = True
                
                return (local_n, local_err, local_skipped)

            # Process micro-batch concurrently
            with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
                futures = {executor.submit(scrape_single_user, u): getattr(u, 'id', str(u)) for u in batch_users}
                for future in concurrent.futures.as_completed(futures):
                    user_id = futures[future]
                    try:
                        n, crashed, skipped = future.result()
                        if skipped:
                            skipped_count += 1
                        elif crashed:
                            error_count += 1
                        else:
                            success_count += 1
                            notification_count += n
                    except Exception as e:
                        logger.error(f"Future crashed for user {user_id}: {e}")
                        error_count += 1
            
            duration = time.time() - start_time
            avg_time_per_user = duration / len(batch_users) if batch_users else 0
            
            logger.info(
                f"Batch {batch_number} complete: "
                f"{success_count} success, {error_count} errors, {skipped_count} skipped, "
                f"{notification_count} notifications sent, "
                f"{duration:.2f}s total, {avg_time_per_user:.2f}s per user"
            )
            
            # 3. Structured Logging for GCP Metrics Explorer
            logger.info(f"METRIC_SUMMARY | bucket={batch_number} users={len(batch_users)} scraped={success_count+error_count} skipped={skipped_count} duration={duration:.2f} avg_per_user={avg_time_per_user:.2f}")
            
            # Log exact usage to Firestore kill switch
            self._log_free_tier_usage(duration)
            
            summary_dict = {
                'mode': 'force_run' if force else f'batch_{batch_number}',
                'users_processed': len(batch_users),
                'success_count': success_count,
                'error_count': error_count,
                'skipped_count': skipped_count,
                'notifications_sent': notification_count,
                'duration_seconds': duration
            }
            
            if force:
                # Mode 1: Manual run -> Fire webhook immediately
                try:
                    from app.services.discord_service import enqueue_webhook_log
                    
                    enqueue_webhook_log(
                        title="Background Scheduler Summary (Manual Run)",
                        color=0x3498db,
                        fields=[
                            {'name': 'Users Evaluated', 'value': str(summary_dict['users_processed']), 'inline': True},
                            {'name': 'Accounts Processed', 'value': str(summary_dict['success_count'] + summary_dict['error_count']), 'inline': True},
                            {'name': 'Skipped (No Discord)', 'value': str(summary_dict['skipped_count']), 'inline': True},
                            {'name': 'Notifications Sent', 'value': str(summary_dict['notifications_sent']), 'inline': True},
                            {'name': 'Errors', 'value': str(summary_dict['error_count']), 'inline': True},
                            {'name': 'Duration', 'value': f"{summary_dict['duration_seconds']:.2f}s", 'inline': True}
                        ],
                        footer=datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC"),
                        sync=True
                    )
                except Exception as e:
                    logger.error(f"Failed to enqueue scheduler webhook: {e}")
            else:
                # Mode 2: Automated run -> Aggregate stats and fire once at bucket 59
                try:
                    self._aggregate_and_fire_daily_summary(batch_number, summary_dict)
                except Exception as e:
                    logger.error(f"Failed to aggregate daily summary: {e}")
                
            return summary_dict
            
        except Exception as e:
            logger.error(f"Error running notifications (force={force}): {e}", exc_info=True)
            return {'error': str(e)}
    
    def _process_account(self, user, account) -> int:
        """Process one account and send notifications."""
        from app.services.background_notification_service import _process_account
        counts = _process_account(user, account)
        return counts.get('total', 0) if isinstance(counts, dict) else counts
    
    def _aggregate_and_fire_daily_summary(self, batch_number: int, summary_dict: Dict[str, Any]):
        """Aggregates micro-batch stats to Firestore and fires ONE webhook at bucket 59."""
        fs = self._get_firestore()
        if not fs or not hasattr(fs, 'db') or not fs.db:
            return
            
        now = get_current_pakistan_time()
        today_str = now.strftime("%Y-%m-%d")
        
        # Identify slot for multi-slot scheduling (Afternoon vs Evening)
        if now.hour in (14, 15):
            slot_id = "afternoon"
            slot_title = "Afternoon Batch (2:00 PM – 3:00 PM PKT)"
            is_target_slot = True
        elif now.hour in (20, 21):
            slot_id = "evening"
            slot_title = "Evening Batch (8:00 PM – 9:00 PM PKT)"
            is_target_slot = True
        else:
            slot_id = f"slot_{now.hour}"
            slot_title = f"Manual Batch ({now.strftime('%I:%M %p')} PKT)"
            is_target_slot = False

        doc_ref = fs.db.collection('system_config').document(f'daily_discord_stats_{slot_id}')
        bucket_key = str(batch_number)
        
        # We must use a transaction to safely update stats without race conditions
        from google.cloud import firestore
        transaction = fs.db.transaction()
        
        @firestore.transactional
        def update_in_transaction(transaction, doc_ref):
            doc = doc_ref.get(transaction=transaction)
            data = doc.to_dict() if doc.exists else {}
            
            should_fire = False
            
            if data.get('date') != today_str:
                # Wipe it completely for a new day in this slot
                new_data = {
                    'date': today_str,
                    'slot': slot_id,
                    'summary_sent': False,
                    'duplicate_triggers_ignored': 0,
                    'stats_by_bucket': {
                        bucket_key: summary_dict
                    }
                }
                
                if batch_number == 59:
                    should_fire = True
                    if is_target_slot:
                        new_data['summary_sent'] = True
                        
                transaction.set(doc_ref, new_data)
                return new_data, should_fire
            else:
                duplicates = data.get('duplicate_triggers_ignored', 0)
                if bucket_key in data.get('stats_by_bucket', {}):
                    duplicates += 1
                
                # Reconstruct full data natively
                final_data = dict(data)
                final_data['duplicate_triggers_ignored'] = duplicates
                if 'stats_by_bucket' not in final_data:
                    final_data['stats_by_bucket'] = {}
                final_data['stats_by_bucket'][bucket_key] = summary_dict
                
                if batch_number == 59:
                    if is_target_slot:
                        if not final_data.get('summary_sent', False):
                            should_fire = True
                            final_data['summary_sent'] = True
                    else:
                        # Manual test outside the expected cron window - fire but don't lock
                        should_fire = True
                
                transaction.set(doc_ref, final_data)
                return final_data, should_fire
                
        try:
            final_data, should_fire = update_in_transaction(transaction, doc_ref)
        except Exception as e:
            logger.error(f"Transaction failed for daily stats: {e}")
            return
            
        # Fire webhook exactly once for the true target slot
        if should_fire:
            self._fire_daily_summary_webhook(final_data, slot_title=slot_title)
            
    def _fire_daily_summary_webhook(self, final_data: Dict[str, Any], slot_title: str = "Evening Batch"):
        stats_by_bucket = final_data.get('stats_by_bucket', {})
        total_users = sum(b.get('users_processed', 0) for b in stats_by_bucket.values())
        total_success = sum(b.get('success_count', 0) for b in stats_by_bucket.values())
        total_errors = sum(b.get('error_count', 0) for b in stats_by_bucket.values())
        total_skipped = sum(b.get('skipped_count', 0) for b in stats_by_bucket.values())
        total_notifications = sum(b.get('notifications_sent', 0) for b in stats_by_bucket.values())
        
        total_processed = total_success + total_errors
        success_rate = (total_success / total_processed * 100) if total_processed > 0 else 100
        
        duplicates_ignored = final_data.get('duplicate_triggers_ignored', 0)
        active_buckets = sum(1 for b in stats_by_bucket.values() if b.get('users_processed', 0) > 0)
        
        try:
            from app.services.discord_service import enqueue_webhook_log
            enqueue_webhook_log(
                title=f"Daily {slot_title} Summary",
                color=0x2ecc71, # Green for daily summary
                fields=[
                    {'name': 'Users Evaluated', 'value': str(total_users), 'inline': True},
                    {'name': 'Accounts Processed', 'value': str(total_processed), 'inline': True},
                    {'name': 'Skipped (No Discord)', 'value': str(total_skipped), 'inline': True},
                    {'name': 'Notifications Sent', 'value': str(total_notifications), 'inline': True},
                    {'name': 'Errors', 'value': str(total_errors), 'inline': True},
                    {'name': 'Success Rate', 'value': f"{success_rate:.1f}%", 'inline': True},
                    {'name': 'Active Buckets', 'value': f"{active_buckets} / 60", 'inline': True},
                    {'name': 'Duplicate Triggers Ignored', 'value': str(duplicates_ignored), 'inline': False}
                ],
                footer=datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC"),
                sync=True
            )
        except Exception as e:
            logger.error(f"Failed to send daily summary webhook: {e}")
            
    def get_status(self):
        """Get kill switch status"""
        status = self.get_kill_switch_status()
        return {
            'running': False, # BackgroundScheduler is removed
            'kill_switch_active': status['active'],
            'usage_seconds': status['usage_seconds'],
            'limit_seconds': FREE_TIER_HARD_STOP_SECONDS
        }

# Singleton instance
def get_notification_scheduler():
    global _scheduler
    if _scheduler is None:
        _scheduler = NotificationScheduler()
    return _scheduler

def init_notification_scheduler(app):
    """Initialize the synchronous notification scheduler"""
    scheduler = get_notification_scheduler()
    scheduler.init_app(app)
    return scheduler
