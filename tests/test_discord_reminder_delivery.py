"""Regression coverage for reminders consumed without Discord delivery."""
from copy import deepcopy
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
import pytz

from app.models.lms_models import LMSAssignment
from app.models.notification_models import NotificationType
from app.services.assignment_notification_service import AssignmentNotificationService
from app.services.notification_service import NotificationService
from app.services import discord_service


@pytest.fixture
def engine(tmp_path):
    service = AssignmentNotificationService(str(tmp_path))
    state = {'known_assignments': [], 'notified_deadlines': {}}
    service._load_tracked_assignments = lambda _: deepcopy(state)
    service._save_tracked_assignments = lambda _, data: state.update(deepcopy(data)) or True
    return service, state


def assignment(days=0):
    now = datetime.now(pytz.timezone('Asia/Karachi')).replace(tzinfo=None)
    return LMSAssignment.from_dict(dict(
        assignment_id='a1', assignment_no=1, title='Report', course_code='CS101',
        course_name='Computing', deadline=now + timedelta(days=days),
        submission_status='pending'))


def test_dashboard_read_does_not_consume_later_discord_reminders(engine):
    service, state = engine
    with patch('app.services.assignment_notification_service.get_notification_service') as get_delivery:
        service.check_and_notify('u1', [assignment(2)], send_push=False)
        get_delivery.assert_not_called()
        assert state == {'known_assignments': [], 'notified_deadlines': {}}
        counts = service.check_and_notify('u1', [assignment(2)], send_push=True)
    assert counts['new'] == 1
    assert counts['due_3d'] == 1


@pytest.mark.parametrize('days,kind,tracking', [(0, 'due_24h', '24h'), (2, 'due_3d', '3d'), (-2, 'overdue', 'overdue')])
def test_failed_delivery_is_retried_without_consuming_frequency(engine, days, kind, tracking):
    service, state = engine
    state['known_assignments'] = ['a1']
    with patch('app.services.assignment_notification_service.get_notification_service') as get_delivery:
        delivery = get_delivery.return_value.send_notification
        delivery.return_value = None
        counts = service.check_and_notify('u1', [assignment(days)])
        assert counts[kind] == 0
        assert not state['notified_deadlines']
        delivery.return_value = Mock()
        counts = service.check_and_notify('u1', [assignment(days)])
    assert counts[kind] == 1
    assert f'a1_{tracking}' in state['notified_deadlines']


def test_disabled_alert_does_not_consume_future_reminder(engine):
    service, state = engine
    state['known_assignments'] = ['a1']
    user = SimpleNamespace(notification_preferences={'due_3d': False})
    service.check_and_notify('u1', [assignment(2)], user=user)
    assert not state['notified_deadlines']


@pytest.mark.parametrize('delivered', [True, False])
def test_unqueued_delivery_finishes_before_returning(delivered):
    with patch.dict('os.environ', {'DISCORD_BOT_TOKEN': 'test', 'WORKER_URL': ''}), \
         patch.object(discord_service, '_process_discord_notification_sync', return_value=delivered) as send:
        assert discord_service.enqueue_discord_notification('123', 'Title', 'Message') is delivered
        send.assert_called_once()


def test_queue_uses_authentication_accepted_by_worker():
    from google.cloud import tasks_v2
    with patch.dict('os.environ', {'DISCORD_BOT_TOKEN': 'test', 'WORKER_URL': 'https://worker.example',
                                  'FIREBASE_PROJECT_ID': 'project', 'SCHEDULER_SECRET': 'test-secret',
                                  'WORKER_SERVICE_ACCOUNT': ''}), \
         patch.object(tasks_v2, 'CloudTasksClient') as client:
        assert discord_service.enqueue_discord_notification('123', 'Title', 'Message')
    request = client.return_value.create_task.call_args.kwargs['request']
    assert request['task']['http_request']['headers']['Authorization'] == 'Bearer test-secret'


def test_private_worker_keeps_platform_and_application_authentication():
    from flask import Flask
    from google.cloud import tasks_v2
    from app.routes import scheduler

    with patch.dict('os.environ', {
        'DISCORD_BOT_TOKEN': 'test', 'WORKER_URL': 'https://worker.example/',
        'FIREBASE_PROJECT_ID': 'project', 'SCHEDULER_SECRET': 'test-secret',
        'WORKER_SERVICE_ACCOUNT': 'worker@project.iam.gserviceaccount.com'
    }), patch.object(tasks_v2, 'CloudTasksClient') as client:
        assert discord_service.enqueue_discord_notification('123', 'Title', 'Message')

    http = client.return_value.create_task.call_args.kwargs['request']['task']['http_request']
    assert http['oidc_token'] == {
        'service_account_email': 'worker@project.iam.gserviceaccount.com',
        'audience': 'https://worker.example'
    }
    assert 'Authorization' not in http['headers']
    # Cloud Tasks supplies the identity token at dispatch time.
    headers = dict(http['headers'], Authorization='Bearer google-identity-token')
    app = Flask(__name__)
    app.register_blueprint(scheduler.scheduler_bp)
    with patch.object(scheduler, '_SECRET', 'test-secret'), \
         patch.object(discord_service, '_process_discord_notification_sync', return_value=True) as send:
        response = app.test_client().post('/api/internal/discord/send_dm',
                                          data=http['body'], headers=headers)
    assert response.status_code == 200
    assert send.call_args.args[0]['discord_id'] == '123'


@pytest.mark.parametrize('secret', [None, '', 'wrong-secret'])
def test_worker_rejects_missing_or_invalid_application_secret(secret):
    from flask import Flask
    from app.routes import scheduler

    app = Flask(__name__)
    app.register_blueprint(scheduler.scheduler_bp)
    headers = {'Authorization': 'Bearer google-identity-token'}
    if secret is not None:
        headers['X-Scheduler-Secret'] = secret
    with patch.object(scheduler, '_SECRET', 'test-secret'), \
         patch.object(discord_service, '_process_discord_notification_sync') as send:
        response = app.test_client().post('/api/internal/discord/send_dm', json={}, headers=headers)
    assert response.status_code == 401
    send.assert_not_called()


def test_worker_header_does_not_authorize_other_scheduler_endpoints():
    from flask import Flask
    from app.routes import scheduler

    app = Flask(__name__)
    app.register_blueprint(scheduler.scheduler_bp)
    with patch.object(scheduler, '_SECRET', 'test-secret'):
        response = app.test_client().post('/api/internal/run-notifications',
                                          headers={'X-Scheduler-Secret': 'test-secret'})
    assert response.status_code == 401


@pytest.mark.parametrize('user', [None, SimpleNamespace(discord_id='123', notification_preferences={'discord_enabled': True})])
def test_notification_reports_delivery_failure(tmp_path, user):
    service = NotificationService(str(tmp_path))
    with patch('app.models.account.User.get', return_value=user), \
         patch.object(discord_service, 'enqueue_discord_notification', return_value=False):
        assert service.send_notification('u1', NotificationType.ASSIGNMENT_DUE_24H,
                                         'Due soon', 'Report', send_push=True) is None


def test_mixed_attendance_update_still_sends_absence_alert():
    from app.routes.attendance import send_attendance_notifications
    change = SimpleNamespace(change_type='attendance', is_scheduled=True,
                             absences_difference=1, course_code='CS101', course_name='Computing',
                             get_change_severity=lambda: 'positive')
    user = SimpleNamespace(notification_preferences={'absent_scheduled': True})
    with patch('app.routes.attendance.get_notification_service') as service:
        send_attendance_notifications('u1', [change], user)
    args = service.return_value.send_notification.call_args.kwargs
    assert args['notification_type'] == NotificationType.ATTENDANCE_ABSENT_SCHEDULED
    assert args['title'] == 'Absent Marked'


def test_failed_new_assignment_delivery_remains_retryable(engine):
    service, state = engine
    with patch('app.services.assignment_notification_service.get_notification_service') as delivery:
        delivery.return_value.send_notification.return_value = None
        assert not any(service.check_and_notify('u1', [assignment(5)]).values())
        assert state == {'known_assignments': [], 'notified_deadlines': {}}
        delivery.return_value.send_notification.return_value = Mock()
        assert service.check_and_notify('u1', [assignment(5)])['new'] == 1


@pytest.mark.parametrize('days,kind', [(0, 'due_24h'), (2, 'due_3d'), (-2, 'overdue')])
def test_failed_new_alert_does_not_block_deadline_alert_or_its_own_retry(engine, days, kind):
    service, state = engine
    with patch('app.services.assignment_notification_service.get_notification_service') as delivery:
        send = delivery.return_value.send_notification
        send.side_effect = [None, Mock()]
        counts = service.check_and_notify('u1', [assignment(days)])
        assert counts['new'] == 0
        assert counts[kind] == 1
        assert send.call_count == 2
        assert 'a1' not in state['known_assignments']
        assert 'a1_new' not in state['notified_deadlines']

        # An intervening urgent check must not consume the failed new alert.
        service.check_and_notify('u1', [assignment(days)], urgent_only=True)
        assert 'a1' not in state['known_assignments']
        send.side_effect = None
        send.return_value = Mock()
        counts = service.check_and_notify('u1', [assignment(days)])
        assert counts['new'] == 1
        assert counts[kind] == 0


def test_queue_failure_falls_back_to_confirmed_delivery():
    from google.cloud import tasks_v2
    with patch.dict('os.environ', {'DISCORD_BOT_TOKEN': 'test', 'WORKER_URL': 'https://worker.example',
                                  'FIREBASE_PROJECT_ID': 'project', 'SCHEDULER_SECRET': 'test-secret'}), \
         patch.object(tasks_v2, 'CloudTasksClient', side_effect=RuntimeError('unavailable')), \
         patch.object(discord_service, '_process_discord_notification_sync', return_value=False) as send:
        assert discord_service.enqueue_discord_notification('123', 'Title', 'Message') is False
        send.assert_called_once()


@pytest.mark.parametrize('hours_left,hours_since,expected', [(2, 1, True), (2, .5, False), (5, 1, False), (5, 4, True)])
@pytest.mark.parametrize('legacy', [True, False])
def test_final_three_hours_use_hourly_reminders(engine, hours_left, hours_since, expected, legacy):
    service, _ = engine
    now = datetime(2026, 11, 9, 23)
    timestamp = (now - timedelta(hours=hours_since)).isoformat()
    tracking = {'a1_24h': timestamp if legacy else {'last_notified': timestamp}}
    assert service._should_notify_based_on_frequency('a1', '24h', tracking, now, hours_left) is expected


def test_midnight_cached_check_sends_overdue_once(engine):
    service, state = engine
    now = datetime(2026, 11, 10, 0, 1)
    with patch('app.services.assignment_notification_service.datetime', wraps=datetime) as clock, \
         patch.object(service, '_parse_deadline', return_value=datetime(2026, 11, 10)), \
         patch('app.services.assignment_notification_service.get_notification_service') as delivery:
        clock.now.return_value = now
        assert service.check_and_notify('u1', [assignment(5)], urgent_only=True)['overdue'] == 1
        assert service.check_and_notify('u1', [assignment(5)], urgent_only=True)['overdue'] == 0
        assert delivery.return_value.send_notification.call_count == 1
