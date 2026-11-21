"""
Notification Scheduler Service
Synchronous service designed to be called by Google Cloud Scheduler endpoints.
Processes users in 60 micro-batches (1 batch per minute) to gently distribute load 
and bypass firewall/timeout limits while strictly staying within the Cloud Run Free Tier.
"""
import logging
import time
from datetime import datetime, timedelta
from typing import Dict, Any
from uuid import uuid4
import pytz

logger = logging.getLogger(__name__)

# Pakistan timezone (UTC+5, no DST)
PAKISTAN_TZ = pytz.timezone('Asia/Karachi')

# Free tier limits for monitoring
FREE_TIER_CPU_HOURS_PER_MONTH = 50.0
# 180,000 seconds = 50 hours. We hard stop at 160,000 seconds (44.4 hours)
FREE_TIER_HARD_STOP_SECONDS = 160000  

DAILY_SUMMARY_TITLES = {
    'afternoon': 'Afternoon Batch (2:00 PM – 3:00 PM PKT)',
    'evening': 'Evening Batch (8:00 PM – 9:00 PM PKT)',
}
DAILY_BATCH_HOURS = {14: 'afternoon', 20: 'evening'}
DAILY_BATCH_COUNT = 60
MINUTE_CATCH_UP_WINDOW = 10
DAILY_BATCH_LEASE_SECONDS = 600
# Leave time for the last batch to finish before Cloud Scheduler's HTTP deadline.
CATCH_UP_START_BUDGET_SECONDS = 75
SUMMARY_CLAIM_SECONDS = 120

# Global instance
_scheduler = None

def _pakistan_wall_time() -> datetime:
    return datetime.now(PAKISTAN_TZ)


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
                if dt.tzinfo is None:
                    raise ValueError('schedule time must include a timezone')
                return dt.astimezone(PAKISTAN_TZ)
    except Exception as e:
        logger.warning(f"Failed to parse Cloud Scheduler time: {e}")
        
    return _pakistan_wall_time()

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
            
        doc = fs.get_document('system_config', 'usage_stats', bypass_cache=True)
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
            from google.cloud import firestore

            month = datetime.now(pytz.UTC).strftime('%Y-%m')
            doc_ref = fs.db.collection('system_config').document('usage_stats')

            @firestore.transactional
            def add_usage(transaction):
                snapshot = doc_ref.get(transaction=transaction)
                data = snapshot.to_dict() if snapshot.exists else {}
                month_stats = data.setdefault(month, {})
                total = month_stats.get('total_cpu_seconds', 0) + duration_seconds
                month_stats['total_cpu_seconds'] = total
                month_stats['batch_runs'] = month_stats.get('batch_runs', 0) + 1
                month_stats['kill_switch_active'] = total >= FREE_TIER_HARD_STOP_SECONDS
                transaction.set(doc_ref, data)
                return total

            total = add_usage(fs.db.transaction())
            if total >= FREE_TIER_HARD_STOP_SECONDS:
                logger.critical("HARD STOP: Free tier usage reached %.2fs", total)
            logger.info("Usage logged: %.2fs (%.2fs this month)", duration_seconds, total)
        except Exception as e:
            logger.error("Failed to log usage stats: %s", e)

    def _run_urgent_assignment_check(self) -> str:
        """Run continuous urgent assignment checks using cached data only."""
        catch_up_deadline = time.monotonic() + CATCH_UP_START_BUDGET_SECONDS
        try:
            kill_status = self.get_kill_switch_status()
            completed_buckets = self._post_window_close_summary(paused=kill_status['active'])
            if kill_status['active']:
                return "Skipped: Free tier kill switch active"

            logger.info("Starting synchronous urgent assignment check (cache only)")
            
            from app.services.background_notification_service import check_cached_urgent_assignments_only
            
            start_time = time.time()
            if self.app:
                with self.app.app_context():
                    summary = check_cached_urgent_assignments_only(completed_buckets=completed_buckets)
            else:
                summary = check_cached_urgent_assignments_only(completed_buckets=completed_buckets)
                
            duration = time.time() - start_time
            logger.info(f"Urgent check complete in {duration:.2f}s: {summary}")
            
            # Log usage for kill switch
            self._log_free_tier_usage(duration)
            
            return f"Success: {summary}"
            
        except Exception as e:
            logger.error(f"Error running periodic urgent assignment check: {e}", exc_info=True)
            return f"Error: {e}"
        finally:
            self._recover_daily_batches(catch_up_deadline)

    def _run_notifications(self, force: bool = False, bucket: int = None, max_workers: int = 15,
                           *, record_batch: bool = True) -> Dict[str, Any]:
        """Run evening batch notifications synchronously.
        
        Args:
            force: If True, bypasses bucket logic and processes ALL users.
            bucket: Specific bucket (0-59) to process. If None, uses current minute.
            max_workers: Maximum number of parallel threads (default 15).
            record_batch: False when the caller owns a batch claim and records completion.
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
            from app.services.notification_service import track_scheduled_discord_delivery
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
                    'confirmed_discord_dms': 0,
                    'duration_seconds': 0.0
                }
                if not force and record_batch:
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
                user_id = user_or_id if isinstance(user_or_id, str) else getattr(user_or_id, 'id', 'unknown')
                try:
                    if isinstance(user_or_id, str):
                        user = User.get(user_or_id)
                    else:
                        user = user_or_id

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
                            
                            with track_scheduled_discord_delivery() as delivery:
                                try:
                                    if self.app:
                                        with self.app.app_context():
                                            self._process_account(user, user.cms_account)
                                    else:
                                        self._process_account(user, user.cms_account)
                                finally:
                                    local_n = delivery.sent
                                    local_err = delivery.failed
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
                        notification_count += n
                        if skipped:
                            skipped_count += 1
                        elif crashed:
                            error_count += 1
                        else:
                            success_count += 1
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
                'confirmed_discord_dms': notification_count,
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
            elif record_batch:
                # Mode 2: Automated run -> Aggregate stats and fire once at bucket 59
                try:
                    self._aggregate_and_fire_daily_summary(batch_number, summary_dict)
                except Exception as e:
                    logger.error(f"Failed to aggregate daily summary: {e}")
                
            return summary_dict
            
        except Exception as e:
            logger.error(f"Error running notifications (force={force}): {e}", exc_info=True)
            return {'error': str(e)}
    
    def _process_account(self, user, account):
        """Process one account, recording delivery outcomes in the active tracker."""
        from app.services.background_notification_service import _process_account
        _process_account(user, account)

    def run_scheduled_notifications(self, bucket: int = None) -> Dict[str, Any]:
        scheduled_at = get_current_pakistan_time()
        now = _pakistan_wall_time()
        if scheduled_at.date() != now.date():
            return {'status': 'expired'}
        recovery_slot = DAILY_BATCH_HOURS.get(scheduled_at.hour - 1)
        if recovery_slot and bucket is None:
            if (scheduled_at.minute >= MINUTE_CATCH_UP_WINDOW or now.hour != scheduled_at.hour
                    or now.minute >= MINUTE_CATCH_UP_WINDOW):
                return {'mode': 'catch_up', 'status': 'window_closed'}
            self._recover_daily_batches(
                time.monotonic() + CATCH_UP_START_BUDGET_SECONDS, catch_up_at=scheduled_at,
            )
            return {'mode': 'catch_up'}

        slot_id = DAILY_BATCH_HOURS.get(scheduled_at.hour) or recovery_slot
        if not slot_id:
            return self._run_notifications(bucket=bucket)
        if bucket is None and now.hour > scheduled_at.hour and (
            now.hour > scheduled_at.hour + 1 or now.minute >= MINUTE_CATCH_UP_WINDOW
        ):
            # Past the minute window, the hourly job owns recovery.
            return {'status': 'expired'}
        fs = self._get_firestore()
        if not fs or not fs.db:
            from app.config import get_config
            if get_config().use_firestore:
                return {'error': 'Firestore unavailable for scheduled batches'}
            return self._run_notifications(bucket=bucket)

        batch_number = scheduled_at.minute if bucket is None else bucket
        if not 0 <= batch_number < DAILY_BATCH_COUNT:
            return {'error': 'Batch number must be between 0 and 59'}

        date = scheduled_at.date().isoformat()
        doc_ref = fs.db.collection('system_config').document(f'daily_discord_stats_{slot_id}')
        result = self._run_daily_batch(fs.db, doc_ref, date, slot_id, batch_number)
        now = _pakistan_wall_time()
        if 'status' not in result and 'error' not in result and (
            now.date() == scheduled_at.date() and now.hour > scheduled_at.hour
        ):
            # A late original request might be the last batch still running.
            self._send_pending_daily_summary(fs.db, doc_ref, date, slot_id)
        return result

    def _run_daily_batch(self, db, doc_ref, date: str, slot_id: str, batch_number: int):
        """Claim a dated batch before running it, then record successful completion."""
        from google.cloud import firestore

        bucket_key = str(batch_number)
        claim_id = uuid4().hex

        @firestore.transactional
        def claim_batch(transaction):
            snapshot = doc_ref.get(transaction=transaction)
            data = snapshot.to_dict() if snapshot.exists else {}
            stored_date = data.get('date') or ''
            if stored_date > date:
                return 'expired'
            if stored_date != date:
                data = {'date': date, 'slot': slot_id, 'stats_by_bucket': {}}

            claims = data.setdefault('batch_claims', {})
            existing_claim = claims.get(bucket_key, {})
            now = time.time()
            if bucket_key in data.get('stats_by_bucket', {}) or existing_claim.get('expires_at', 0) > now:
                data['duplicate_triggers_ignored'] = data.get('duplicate_triggers_ignored', 0) + 1
                transaction.set(doc_ref, data)
                return 'already_claimed'

            claims[bucket_key] = {'id': claim_id, 'expires_at': now + DAILY_BATCH_LEASE_SECONDS}
            transaction.set(doc_ref, data)
            return 'claimed'

        @firestore.transactional
        def finish_batch(transaction, result):
            snapshot = doc_ref.get(transaction=transaction)
            data = snapshot.to_dict() if snapshot.exists else {}
            claims = data.get('batch_claims', {})
            if data.get('date') != date or claims.get(bucket_key, {}).get('id') != claim_id:
                return False
            del claims[bucket_key]
            if 'error' not in result:
                data.setdefault('stats_by_bucket', {})[bucket_key] = result
            transaction.set(doc_ref, data)
            return True

        status = claim_batch(db.transaction())
        if status != 'claimed':
            return {'mode': f'batch_{batch_number}', 'status': status}

        result = self._run_notifications(bucket=batch_number, record_batch=False)
        if not finish_batch(db.transaction(), result):
            logger.warning("Lost the claim for %s batch %s on %s", slot_id, batch_number, date)
        return result

    def _aggregate_and_fire_daily_summary(self, batch_number: int, summary_dict: Dict[str, Any]):
        """Store micro-batch stats and attempt prompt delivery after bucket 59."""
        fs = self._get_firestore()
        if not fs or not fs.db:
            return

        now = get_current_pakistan_time()
        today = now.strftime('%Y-%m-%d')
        if now.hour in (14, 15):
            slot_id = 'afternoon'
        elif now.hour in (20, 21):
            slot_id = 'evening'
        else:
            slot_id = f'slot_{now.hour}'

        doc_ref = fs.db.collection('system_config').document(f'daily_discord_stats_{slot_id}')
        bucket_key = str(batch_number)

        from google.cloud import firestore

        @firestore.transactional
        def update_stats(transaction):
            snapshot = doc_ref.get(transaction=transaction)
            data = snapshot.to_dict() if snapshot.exists else {}
            stored_date = data.get('date') or ''
            if stored_date > today:
                # An old retry must not overwrite the next day's stats.
                return None
            if stored_date != today:
                data = {
                    'date': today,
                    'slot': slot_id,
                    'summary_sent': False,
                    'duplicate_triggers_ignored': 0,
                    'stats_by_bucket': {},
                }

            stats_by_bucket = data.setdefault('stats_by_bucket', {})
            previous = stats_by_bucket.get(bucket_key, {})
            bucket_stats = dict(summary_dict)
            if bucket_key in stats_by_bucket:
                data['duplicate_triggers_ignored'] = data.get('duplicate_triggers_ignored', 0) + 1
                # Reprocessing the bucket can send additional DMs. Keep prior confirmations.
                if 'confirmed_discord_dms' in bucket_stats:
                    bucket_stats['confirmed_discord_dms'] += previous.get('confirmed_discord_dms', 0)
                    bucket_stats['notifications_sent'] = bucket_stats['confirmed_discord_dms']
                    if previous.get('unverified_discord_counts') or (
                        'notifications_sent' in previous and 'confirmed_discord_dms' not in previous
                    ):
                        bucket_stats['unverified_discord_counts'] = True
            stats_by_bucket[bucket_key] = bucket_stats
            transaction.set(doc_ref, data)
            return data

        data = update_stats(fs.db.transaction())
        if batch_number == 59 and data:
            if slot_id in DAILY_SUMMARY_TITLES:
                self._send_pending_daily_summary(fs.db, doc_ref, today, slot_id)
            else:
                self._fire_daily_summary_webhook(data, slot_title=f"Manual Batch ({now:%I:%M %p} PKT)")

    def _recover_daily_batches(self, deadline: float, *, catch_up_at: datetime = None):
        """Recover one batch per minute, then use hourly checks for any remaining gaps."""
        try:
            now = catch_up_at or get_current_pakistan_time()
            if now.date() != _pakistan_wall_time().date():
                return
            if catch_up_at is not None:
                slot_id = DAILY_BATCH_HOURS[now.hour - 1]
            elif 16 <= now.hour < 20:
                slot_id = 'afternoon'
            elif now.hour >= 22:
                slot_id = 'evening'
            else:
                return

            fs = self._get_firestore()
            if not fs or not fs.db:
                if catch_up_at is not None:
                    raise RuntimeError('Firestore unavailable for batch catch-up')
                return

            date = now.date().isoformat()
            doc_ref = fs.db.collection('system_config').document(f'daily_discord_stats_{slot_id}')
            if catch_up_at is not None:
                kill_status = self.get_kill_switch_status()
                self._send_pending_daily_summary(
                    fs.db, doc_ref, date, slot_id, window_closed_at=now, paused=kill_status['active'],
                )
                if kill_status['active']:
                    return

            snapshot = doc_ref.get()
            data = snapshot.to_dict() if snapshot.exists else {}
            stored_date = data.get('date') or ''
            if stored_date > date:
                return

            completed = data.get('stats_by_bucket', {}) if stored_date == date else {}
            claims = data.get('batch_claims', {}) if stored_date == date else {}
            if len(completed) == DAILY_BATCH_COUNT and data.get('summary_sent'):
                return

            wall_time = time.time()
            pending = [
                i for i in range(DAILY_BATCH_COUNT)
                if str(i) not in completed and claims.get(str(i), {}).get('expires_at', 0) <= wall_time
            ]
            if catch_up_at is not None and pending:
                # Give other pending batches a turn if one keeps failing.
                offset = now.minute % len(pending)
                pending = pending[offset:] + pending[:offset]
            for batch_number in pending:
                if time.monotonic() >= deadline:
                    break
                result = self._run_daily_batch(fs.db, doc_ref, date, slot_id, batch_number)
                if 'status' in result:
                    continue
                if 'error' in result:
                    logger.warning("Could not recover %s batch %s on %s", slot_id, batch_number, date)
                    if 'usage_seconds' in result:
                        break
                else:
                    logger.info("Recovered %s batch %s on %s", slot_id, batch_number, date)
                if catch_up_at is not None:
                    break

            # The remaining hourly invocations can finish a backlog or retry a failed send.
            self._send_pending_daily_summary(fs.db, doc_ref, date, slot_id, allow_partial=now.hour in (19, 23))
        except Exception:
            logger.exception("Failed to recover daily notification batches")
            if catch_up_at is not None:
                raise

    def _post_window_close_summary(self, *, paused: bool) -> set[int] | None:
        """Post the window status and return the buckets safe for the hourly reminder to check."""
        now = get_current_pakistan_time()
        slot_id = DAILY_BATCH_HOURS.get(now.hour - 1)
        if not slot_id or now.date() != _pakistan_wall_time().date():
            return None
        try:
            fs = self._get_firestore()
            if fs and fs.db:
                doc_ref = fs.db.collection('system_config').document(f'daily_discord_stats_{slot_id}')
                date = now.date().isoformat()
                snapshot = doc_ref.get()
                data = snapshot.to_dict() if snapshot.exists else {}
                self._send_pending_daily_summary(
                    fs.db, doc_ref, date, slot_id, window_closed_at=now, paused=paused,
                )
                if data.get('date') == date:
                    completed = data.get('stats_by_bucket', {})
                    return {i for i in range(DAILY_BATCH_COUNT) if str(i) in completed}
        except Exception:
            logger.exception("Failed to post notification status at window close")
        # Catch-up will handle accounts whose batch has not completed.
        return set()

    def _send_pending_daily_summary(self, db, doc_ref, date: str, slot_id: str, *,
                                    allow_partial: bool = False, window_closed_at: datetime = None,
                                    paused: bool = False):
        from google.cloud import firestore

        claim_id = uuid4().hex

        @firestore.transactional
        def claim_summary(transaction):
            snapshot = doc_ref.get(transaction=transaction)
            data = snapshot.to_dict() if snapshot.exists else {}
            stored_date = data.get('date') or ''
            if stored_date > date or (stored_date != date and window_closed_at is None):
                return None
            if stored_date != date:
                data = {'date': date, 'slot': slot_id, 'stats_by_bucket': {}}
            completed = data.get('stats_by_bucket', {})
            missing = [str(i) for i in range(DAILY_BATCH_COUNT) if str(i) not in completed]
            progress = bool(missing and window_closed_at is not None)
            if data.get('summary_sent') or (progress and data.get('progress_summary_sent')):
                return None
            if missing and not (progress or allow_partial):
                return None

            now = time.time()
            if data.get('summary_claim_expires_at', 0) > now:
                return None
            data.update({
                'summary_claim_id': claim_id,
                'summary_claim_expires_at': now + SUMMARY_CLAIM_SECONDS,
            })
            transaction.set(doc_ref, data)
            return data, missing, progress

        @firestore.transactional
        def finish_summary(transaction, sent, progress):
            snapshot = doc_ref.get(transaction=transaction)
            data = snapshot.to_dict() if snapshot.exists else {}
            if data.get('date') == date and data.get('summary_claim_id') == claim_id:
                transaction.update(doc_ref, {
                    ('progress_summary_sent' if progress else 'summary_sent'): sent,
                    'summary_claim_id': firestore.DELETE_FIELD,
                    'summary_claim_expires_at': firestore.DELETE_FIELD,
                })

        claimed = claim_summary(db.transaction())
        if claimed is None:
            return

        data, missing, progress = claimed
        slot_title = DAILY_SUMMARY_TITLES[slot_id]
        sent = False
        try:
            if progress:
                sent = self._fire_batch_progress_webhook(data, missing, slot_title, window_closed_at, paused=paused)
            else:
                sent = self._fire_daily_summary_webhook(data, slot_title=slot_title)
        except Exception:
            logger.exception("Failed to send notification summary for %s on %s", slot_id, date)
        finally:
            finish_summary(db.transaction(), sent, progress)
        if sent:
            logger.info(
                "Delivered daily notification summary for %s on %s (%d/60 batches)",
                slot_id, date, len(data.get('stats_by_bucket', {})),
            )
        else:
            logger.warning("Daily notification summary delivery failed for %s on %s", slot_id, date)

    def _fire_batch_progress_webhook(self, data, missing, slot_title, window_closed_at, *, paused: bool) -> bool:
        from app.services.discord_service import enqueue_webhook_log

        current_minute = max(window_closed_at, _pakistan_wall_time()).replace(second=0, microsecond=0)
        hourly_check = current_minute.replace(minute=0) + timedelta(hours=1)
        next_check = current_minute + timedelta(minutes=1)
        if current_minute.hour != window_closed_at.hour or next_check.minute >= MINUTE_CATCH_UP_WINDOW:
            next_check = hourly_check
        retry_time = next_check.strftime('%I:%M %p').lstrip('0') + ' PKT'
        now = time.time()
        claims = data.get('batch_claims', {})
        in_progress = sum(claims.get(key, {}).get('expires_at', 0) > now for key in missing)
        accounts_processed = sum(
            batch.get('success_count', 0) + batch.get('error_count', 0)
            for batch in data.get('stats_by_bucket', {}).values()
        )
        if paused:
            description = f"Catch-up is paused by the usage limit. The scheduler will check again at {retry_time}."
        else:
            description = (
                f"The batch job checks for pending work every minute during the {MINUTE_CATCH_UP_WINDOW}-minute catch-up window. "
                f"The next scheduled check is at {retry_time}."
            )
        return enqueue_webhook_log(
            title=f"Daily {slot_title} Progress",
            color=0xf1c40f,
            description=description,
            fields=[
                {'name': 'Completed Batches', 'value': f"{DAILY_BATCH_COUNT - len(missing)} / 60", 'inline': True},
                {'name': 'Pending Batches', 'value': str(len(missing)), 'inline': True},
                {'name': 'Batches In Progress', 'value': str(in_progress), 'inline': True},
                {'name': 'Accounts Processed So Far', 'value': str(accounts_processed), 'inline': True},
            ],
            footer=datetime.now(pytz.UTC).strftime("%Y-%m-%d %H:%M:%S UTC"),
            sync=True,
        )

    def _fire_daily_summary_webhook(self, final_data: Dict[str, Any], slot_title: str = "Evening Batch") -> bool:
        stats_by_bucket = final_data.get('stats_by_bucket', {})
        missing = [str(i) for i in range(DAILY_BATCH_COUNT) if str(i) not in stats_by_bucket]
        total_users = sum(b.get('users_processed', 0) for b in stats_by_bucket.values())
        total_success = sum(b.get('success_count', 0) for b in stats_by_bucket.values())
        total_errors = sum(b.get('error_count', 0) for b in stats_by_bucket.values())
        total_skipped = sum(b.get('skipped_count', 0) for b in stats_by_bucket.values())
        total_notifications = sum(b.get('confirmed_discord_dms', 0) for b in stats_by_bucket.values())
        notification_display = str(total_notifications)
        if any(
            b.get('unverified_discord_counts') or
            ('notifications_sent' in b and 'confirmed_discord_dms' not in b)
            for b in stats_by_bucket.values()
        ):
            notification_display = f"{total_notifications} confirmed (older batches unverified)"
        
        total_processed = total_success + total_errors
        success_rate = (total_success / total_processed * 100) if total_processed > 0 else 100
        
        duplicates_ignored = final_data.get('duplicate_triggers_ignored', 0)
        active_buckets = sum(1 for b in stats_by_bucket.values() if b.get('users_processed', 0) > 0)
        
        try:
            from app.services.discord_service import enqueue_webhook_log
            return enqueue_webhook_log(
                title=f"Daily {slot_title} Summary",
                color=0x2ecc71, # Green for daily summary
                fields=[
                    {'name': 'Users Evaluated', 'value': str(total_users), 'inline': True},
                    {'name': 'Accounts Processed', 'value': str(total_processed), 'inline': True},
                    {'name': 'Skipped (No Discord)', 'value': str(total_skipped), 'inline': True},
                    {'name': 'Notifications Sent', 'value': notification_display, 'inline': True},
                    {'name': 'Errors', 'value': str(total_errors), 'inline': True},
                    {'name': 'Success Rate', 'value': f"{success_rate:.1f}%", 'inline': True},
                    {'name': 'Completed Batches', 'value': f"{DAILY_BATCH_COUNT - len(missing)} / 60", 'inline': True},
                    *([{'name': 'Missing Batches', 'value': ', '.join(missing), 'inline': False}] if missing else []),
                    {'name': 'Active Buckets', 'value': f"{active_buckets} / 60", 'inline': True},
                    {'name': 'Duplicate Triggers Ignored', 'value': str(duplicates_ignored), 'inline': False}
                ],
                footer=datetime.now(pytz.UTC).strftime("%Y-%m-%d %H:%M:%S UTC"),
                sync=True
            )
        except Exception as e:
            logger.error(f"Failed to send daily summary webhook: {e}")
            return False
            
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
