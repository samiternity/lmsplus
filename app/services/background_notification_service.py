"""
Background Notification Service
Runs independently of user sessions — called by the scheduler endpoint.
Iterates all users, scrapes fresh data, and sends push/in-app notifications.
"""
import logging
from datetime import datetime
from typing import Dict, Any

logger = logging.getLogger(__name__)


def run_all_notifications() -> Dict[str, Any]:
    """
    Main entry point called by the scheduler endpoint.
    Iterates every user and their CMS account, scrapes fresh data,
    and fires notifications for attendance changes and assignment deadlines.
    Returns a summary dict for logging.
    """
    from app.models.account import User

    summary = {
        'users': 0, 
        'accounts': 0, 
        'skipped': 0,
        'notifications': {
            'attendance': 0,
            'assignments': 0,
            'total': 0
        }, 
        'errors': []
    }
    users = User.list_all()
    summary['users'] = len(users)

    for user in users:
        if not user.is_scheduled_check_eligible():
            summary['skipped'] += 1
            continue

        # SINGLE ACCOUNT MODE
        if user.cms_account:
            try:
                counts = _process_account(user, user.cms_account)
                summary['accounts'] += 1
                summary['notifications']['attendance'] += counts.get('attendance', 0)
                summary['notifications']['assignments'] += counts.get('assignments', 0)
                summary['notifications']['total'] += counts.get('total', 0)
            except Exception as e:
                msg = f"user={user.id} account={user.account_id}: {e}"
                logger.error(f"Background notification error — {msg}")
                summary['errors'].append(msg)
        
        # MULTI-ACCOUNT MODE (disabled)
        # for account_id, account in user.cms_accounts.items():
        #     try:
        #         counts = _process_account(user, account)
        #         summary['accounts'] += 1
        #         summary['notifications']['attendance'] += counts.get('attendance', 0)
        #         summary['notifications']['assignments'] += counts.get('assignments', 0)
        #         summary['notifications']['total'] += counts.get('total', 0)
        #     except Exception as e:
        #         msg = f"user={user.id} account={account_id}: {e}"
        #         logger.error(f"Background notification error — {msg}")
        #         summary['errors'].append(msg)

    logger.info(f"Background notifications done: {summary}")
    
    # Fire Discord Webhook Summary
    try:
        from app.services.discord_service import enqueue_webhook_log
        from datetime import datetime
        
        enqueue_webhook_log(
            title="Background Scheduler Summary",
            color=0x3498db,
            fields=[
                {'name': 'Users Evaluated', 'value': str(summary['users']), 'inline': True},
                {'name': 'Accounts Processed', 'value': str(summary['accounts']), 'inline': True},
                {'name': 'Skipped (No Discord)', 'value': str(summary['skipped']), 'inline': True},
                {'name': 'Attendance Updates', 'value': str(summary['notifications']['attendance']), 'inline': True},
                {'name': 'Assignment Updates', 'value': str(summary['notifications']['assignments']), 'inline': True},
                {'name': 'Notifications', 'value': str(summary['notifications']['total']), 'inline': True},
                {'name': 'Errors', 'value': str(len(summary['errors'])), 'inline': True}
            ],
            footer=datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
        )
    except Exception as e:
        logger.error(f"Failed to enqueue scheduler webhook: {e}")
        
    return summary


def _process_account(user, account) -> Dict[str, int]:
    """Scrape one account and send any triggered notifications. Returns notification counts."""
    counts = {'attendance': 0, 'assignments': 0, 'total': 0}
    session = None
    errors = []

    # ── Attendance ──────────────────────────────────────────────────────────
    try:
        n_att, session = _check_attendance(user, account)
        counts['attendance'] += n_att
        counts['total'] += n_att
    except Exception as e:
        logger.warning(f"Attendance check failed for {account.account_id}: {e}")
        errors.append(f"Attendance: {e}")

    # ── LMS Assignments ──────────────────────────────────────────────────────
    try:
        n_ass = _check_assignments(user, account, cms_session=session)
        counts['assignments'] += n_ass
        counts['total'] += n_ass
    except Exception as e:
        logger.warning(f"Assignment check failed for {account.account_id}: {e}")
        errors.append(f"Assignments: {e}")
        
    if errors:
        raise Exception(" | ".join(errors))

    return counts


# ── Attendance ───────────────────────────────────────────────────────────────

def _check_attendance(user, account) -> tuple[int, object]:
    """Scrape fresh attendance from CMS, detect changes, send push notifications.

    Uses a fresh CMS login for each background run so that the snapshot is
    always up-to-date.  Skips the redundant verify_login() step that the base
    scraper normally calls inside scrape_attendance() — the login() call right
    above already confirms the session is live.
    """
    from app.utils.enhanced_scraper import EnhancedCMSScraper
    from app.utils.data_manager import data_manager
    from app.services.attendance_change_tracker import AttendanceChangeTracker
    from app.routes.attendance import send_attendance_notifications
    from bs4 import BeautifulSoup

    if not account.cms_username or not account.cms_password:
        logger.info(f"[{account.account_id}] Skipping attendance — no CMS credentials")
        return 0, None

    # ── Step 1: Initialize Scraper & Check Weekend Blackout ───────────────────
    scraper = EnhancedCMSScraper(account)
    try:
        from zoneinfo import ZoneInfo
        now_pk = datetime.now(ZoneInfo("Asia/Karachi"))
    except Exception:
        now_pk = datetime.now()

    if now_pk.weekday() >= 5:
        logger.info(f"[{account.account_id}] Skipping attendance — Weekend blackout active")
        try:
            scraper.login(account.cms_username, account.cms_password)
        except Exception:
            pass
        return 0, scraper.session

    soup = None

    # ── Step 2: Fast Atomic Edge Scrape (1-shot Login + Attendance) ───────────
    # Guarantees identical egress IP to prevent ASP.NET session termination
    try:
        from app.routes.auth import get_institute_id_for_enrollment
        target_inst = get_institute_id_for_enrollment(account.cms_username, fallback=getattr(account, 'institute_id', '2'))
        soup = scraper.scrape_attendance_atomic(
            account.cms_username,
            account.cms_password,
            target_inst
        )
    except Exception as e:
        logger.warning(f"[{account.account_id}] Atomic attendance scrape exception: {e}")

    # ── Step 3: Fallback to standard flow if atomic scrape was unsupported or failed ─
    if not soup:
        logger.info(f"[{account.account_id}] Falling back to standard multi-step CMS scrape")
        if not scraper.login(account.cms_username, account.cms_password):
            raise Exception("CMS login failed")

        try:
            response = scraper._request('GET', scraper.attendance_url)
            response.raise_for_status()
        except Exception as e:
            raise Exception(f"Attendance page request failed: {e}")

        # Guard: if the response is the login page the session silently died
        if 'tbEnrollment' in response.text or 'Login.aspx' in response.url:
            raise Exception("Attendance request returned login page — session lost after login()")

        soup = BeautifulSoup(response.text, 'html.parser')

    attendance_data = scraper.parse_attendance(soup)

    if not attendance_data or 'courses' not in attendance_data or attendance_data.get('error'):
        raise Exception(f"Parsed attendance has error or is missing: {attendance_data.get('error', 'None')}")



    logger.info(f"[{account.account_id}] Fresh attendance scraped: {len(attendance_data['courses'])} courses")

    # ── Step 3: Detect changes against memory unified snapshot ────────────────────
    unified = data_manager.get_unified_dashboard(account.account_id) or {}
    old_attendance = unified.get('attendance_snapshot')
    notification_baseline = unified.get('attendance_notification_snapshot') or old_attendance

    if not notification_baseline:
        logger.info(f"[{account.account_id}] No baseline — establishing silently")
        payload = {
            'attendance_snapshot': attendance_data,
            'attendance_notification_snapshot': attendance_data,
            'last_updated': datetime.utcnow().isoformat() + 'Z',
            'legacy_migrated': True
        }
        data_manager.save_unified_dashboard(account.account_id, payload, merge=True)
        return 0, scraper.session

    from app.services.attendance_comparator import AttendanceComparator
    comparator = AttendanceComparator(data_manager, account.account_id)
    try:
        changes = comparator.compare_records(attendance_data, notification_baseline)
    except Exception as e:
        logger.error(f"[{account.account_id}] Comparison failed: {e}")
        return 0, scraper.session

    attendance_changes = [c for c in changes if hasattr(c, 'change_type') and c.change_type == 'attendance']

    if not attendance_changes:
        logger.info(f"[{account.account_id}] No attendance changes detected")

    # ── Step 4: Send push notifications ──────────────────────────────────────
    logger.info(f"[{account.account_id}] {len(attendance_changes)} attendance change(s) — sending notifications")
    delivery = send_attendance_notifications(
        user_id=user.id,
        changes=attendance_changes,
        user=user
    )

    # Advance successful courses independently. Failed courses retain their
    # notification baseline even when a web refresh updates the visible snapshot.
    failed_courses = set(delivery['failed'])
    previous_courses = {
        course['code']: course for course in notification_baseline.get('courses', [])
    }
    notification_snapshot = dict(attendance_data)
    notification_snapshot['courses'] = [
        previous_courses.get(course['code'], course)
        if course['code'] in failed_courses else course
        for course in attendance_data.get('courses', [])
    ]

    # Commit the new snapshot and keep yesterday's for the web UI diff
    payload = {
        'attendance_snapshot': attendance_data,
        'attendance_notification_snapshot': notification_snapshot,
        'last_updated': datetime.utcnow().isoformat() + 'Z',
        'legacy_migrated': True
    }
    if old_attendance and attendance_data != old_attendance:
        payload['attendance_yesterday_snapshot'] = old_attendance
    if (attendance_data != old_attendance or
            notification_snapshot != unified.get('attendance_notification_snapshot')):
        if not data_manager.save_unified_dashboard(account.account_id, payload, merge=True):
            raise RuntimeError('Failed to save attendance notification progress')

    return delivery['sent'], scraper.session


# ── LMS Assignments ──────────────────────────────────────────────────────────

def _check_assignments(user, account, cms_session=None) -> int:
    """Trigger the unified dashboard scraper and send notifications."""
    if not account.cms_username or not account.cms_password:
        logger.info(f"[{account.account_id}] Skipping assignments — no CMS credentials configured")
        return 0

    from app.services.lms_dashboard_service import LMSDashboardService
    from app.services.lms_service import LMSService
    from app.services.assignment_notification_service import get_assignment_notification_service
    from app.models.lms_models import LMSAssignment

    dashboard_service = LMSDashboardService(LMSService(provided_cms_session=cms_session))
    
    logger.info(f"[{account.account_id}] Evening batch: Triggering unified dashboard scraper...")
    
    # 1. Fetch and Cache (This does the actual scraping, saves the rich snapshot, and populates RAM cache)
    try:
        cache_key = f"pending_assignments:{account.account_id}"
        snapshot = dashboard_service._fetch_and_cache_assignments(account.account_id, cache_key)
    except Exception as e:
        raise Exception(f"Dashboard scraper failed: {e}")
        
    if not snapshot or 'pending' not in snapshot:
        raise Exception("Dashboard scraper returned invalid/empty snapshot")
        
    # 2. Extract actionable assignments (Pending + Missed)
    pending_dicts = snapshot.get('pending', [])
    missed_dicts = snapshot.get('missed', [])
    actionable_dicts = pending_dicts + missed_dicts
    
    if not actionable_dicts:
        logger.info(f"[{account.account_id}] No actionable assignments found for notifications")
        return 0
        
    # 3. Convert to models and run notification engine
    try:
        assignments = [LMSAssignment.from_dict(a) for a in actionable_dicts]
        notif_svc = get_assignment_notification_service()
        
        counts = notif_svc.check_and_notify(
            user_id=user.id,
            assignments=assignments,
            user=user,
            account_id=account.account_id,
            send_push=True
        )
        logger.info(f"[{account.account_id}] Notification engine sent {counts} alerts")
        return sum(counts.values()) if counts else 0
    except Exception as e:
        logger.warning(f"[{account.account_id}] Notification engine failed: {e}", exc_info=True)
        return 0

def check_cached_urgent_assignments_only() -> str:
    """Lightweight check to process cached urgent assignments only."""
    from app.models.account import User
    from app.utils.data_manager import data_manager
    from app.services.assignment_notification_service import get_assignment_notification_service
    from app.models.lms_models import LMSAssignment

    users = User.list_all()  # Returns list of User objects (not a dict)
    if not users:
        logger.warning("check_cached_urgent_assignments_only: no users found")
        return "Processed 0 users"

    notif_service = get_assignment_notification_service()

    total_urgent_pushed = 0
    total_users_evaluated = 0
    total_users_skipped = 0

    for user in users:  # Iterate directly over user objects
        if not user or not user.cms_account:
            continue

        if not user.is_scheduled_check_eligible():
            total_users_skipped += 1
            continue
            
        try:
            from app.utils.lms_logger import register_account_enrollment
            if user.account_id and user.cms_account.cms_username:
                register_account_enrollment(user.account_id, user.cms_account.cms_username)
        except ImportError:
            pass

        account_id = user.cms_account.account_id

        # Load unified persistent assignments snapshot from data_manager
        unified = data_manager.get_unified_dashboard(account_id) or {}
        snapshot = unified.get('assignments_snapshot')
        if not snapshot or 'pending' not in snapshot:
            logger.info(f"[{account_id}] No unified snapshot found — skipping urgent check")
            continue

        total_users_evaluated += 1

        # Parse cached assignments
        actionable_dicts = snapshot.get('pending', []) + snapshot.get('missed', [])
        course_assignments = []
        for a_dict in actionable_dicts:
            a = LMSAssignment.from_dict(a_dict)
            course_assignments.append(a)

        if not course_assignments:
            continue

        counts = notif_service.check_and_notify(
            user_id=user.id,
            assignments=course_assignments,
            user=user,
            account_id=account_id,
            send_push=True,
            urgent_only=True
        )

        total_urgent_pushed += sum(counts.values()) if counts else 0
        logger.info(f"[{account_id}] Urgent check result: {counts}")

    return f"Evaluated {total_users_evaluated} users, skipped {total_users_skipped} (no Discord/alerts), pushed {total_urgent_pushed} urgent notifications"
