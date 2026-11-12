"""Attendance delivery progress must survive visible snapshot refreshes."""
from copy import deepcopy
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import Mock, patch
from zoneinfo import ZoneInfo

import pytest

from app.services.background_notification_service import _check_attendance
from app.services.attendance_comparator import AttendanceComparator


def course(code, absences):
    return {'code': code, 'name': code, 'total_classes': 10 + absences,
            'attended_classes': 10, 'absences': absences,
            'attendance_percentage': 1000 / (10 + absences)}


@pytest.fixture
def attendance_run():
    state = {'attendance_snapshot': {'courses': [course('CS101', 0), course('CS102', 0)]}}
    fresh = {'courses': [course('CS101', 1), course('CS102', 1)]}
    user = SimpleNamespace(id='u1', notification_preferences={'absent_scheduled': True})
    account = SimpleNamespace(account_id='acc1', cms_username='student',
                              cms_password='password', institute_id='2')

    def save(_, payload, merge=True):
        state.update(deepcopy(payload))
        return True

    with patch('app.utils.enhanced_scraper.EnhancedCMSScraper') as scraper, \
         patch('app.routes.auth.get_institute_id_for_enrollment', return_value='2'), \
         patch('app.utils.data_manager.data_manager.get_unified_dashboard',
               side_effect=lambda _: deepcopy(state)), \
         patch('app.utils.data_manager.data_manager.save_unified_dashboard', side_effect=save) as persist, \
         patch.object(AttendanceComparator, 'load_timetable_data', return_value={}), \
         patch.object(AttendanceComparator, 'categorize_course_by_schedule', return_value=True), \
         patch('app.routes.attendance.get_notification_service') as notifications, \
         patch('app.services.background_notification_service.datetime', wraps=datetime) as clock:
        clock.now.return_value = datetime(2026, 11, 9, 20, tzinfo=ZoneInfo('Asia/Karachi'))
        scraper.return_value.scrape_attendance_atomic.return_value = Mock()
        scraper.return_value.parse_attendance.side_effect = lambda _: deepcopy(fresh)
        yield SimpleNamespace(state=state, fresh=fresh, user=user,
                              run=lambda: _check_attendance(user, account)[0],
                              send=notifications.return_value.send_notification,
                              persist=persist)


def test_partial_failure_retries_only_failed_course_after_web_snapshot_refresh(attendance_run):
    env = attendance_run

    def first_delivery(**kwargs):
        return None if kwargs['data']['course_code'] == 'CS101' else Mock()

    env.send.side_effect = first_delivery
    assert env.run() == 1
    assert env.state['attendance_snapshot'] == env.fresh
    baseline = {c['code']: c for c in env.state['attendance_notification_snapshot']['courses']}
    assert baseline['CS101']['absences'] == 0
    assert baseline['CS102']['absences'] == 1

    # Refreshing the visible data must not consume the failed course's progress.
    env.state['attendance_snapshot'] = deepcopy(env.fresh)
    yesterday = deepcopy(env.state['attendance_yesterday_snapshot'])
    env.send.reset_mock()
    env.send.side_effect = None
    env.send.return_value = Mock()
    assert env.run() == 1
    assert env.send.call_count == 1
    assert env.send.call_args.kwargs['data']['course_code'] == 'CS101'
    assert env.state['attendance_notification_snapshot'] == env.fresh
    assert env.state['attendance_yesterday_snapshot'] == yesterday

    env.send.reset_mock()
    env.persist.reset_mock()
    assert env.run() == 0
    env.send.assert_not_called()
    env.persist.assert_not_called()


def test_one_delivery_exception_does_not_block_other_courses(attendance_run):
    env = attendance_run

    def delivery(**kwargs):
        if kwargs['data']['course_code'] == 'CS101':
            raise RuntimeError('temporary outage')
        return Mock()

    env.send.side_effect = delivery
    assert env.run() == 1
    assert env.send.call_count == 2
    baseline = {c['code']: c for c in env.state['attendance_notification_snapshot']['courses']}
    assert baseline['CS101']['absences'] == 0
    assert baseline['CS102']['absences'] == 1


def test_disabled_alerts_advance_without_retrying_or_inflating_counts(attendance_run):
    env = attendance_run
    env.user.notification_preferences['absent_scheduled'] = False
    assert env.run() == 0
    env.send.assert_not_called()
    assert env.state['attendance_notification_snapshot'] == env.fresh
    env.user.notification_preferences['absent_scheduled'] = True
    assert env.run() == 0
    env.send.assert_not_called()


def test_first_scrape_establishes_notification_baseline_silently(attendance_run):
    env = attendance_run
    env.state.clear()
    assert env.run() == 0
    env.send.assert_not_called()
    assert env.state['attendance_notification_snapshot'] == env.fresh
    assert env.state['attendance_snapshot'] == env.fresh


def test_persistence_failure_is_reported_without_consuming_progress(attendance_run):
    env = attendance_run
    original = deepcopy(env.state)
    env.send.return_value = None
    env.persist.side_effect = None
    env.persist.return_value = False
    with pytest.raises(RuntimeError, match='Failed to save attendance notification progress'):
        env.run()
    assert env.state == original
