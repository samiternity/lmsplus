"""Regressions for course-token leakage and notification identity migration."""

from copy import deepcopy
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import Mock, patch

from bs4 import BeautifulSoup
import pytest
import pytz

from app.models.lms_models import LMSAssignment
from app.services.assignment_notification_service import AssignmentNotificationService
from app.services.background_notification_service import _check_assignments, check_cached_urgent_assignments_only
from app.services.lms_dashboard_service import LMSDashboardService
from app.services.lms_background_service import LMSBackgroundService
from app.services.lms_scraper_service import LMSScraperService, LMSParsingError
from app.services.lms_service import LMSService
from app.utils.assignment_identity import (
    assignment_identity, assignment_title_identity, identity_index, migrate_tracking,
    normalize_label, assignment_snapshot_payload,
)
from app.utils.assignment_categorization import categorize_assignments


def assignment(number=1, *, course='Networks', code='course-token', token='action-token', days=14):
    now = datetime.now(pytz.timezone('Asia/Karachi')).replace(tzinfo=None)
    return LMSAssignment(number, f'Report {number}', code, course,
                         deadline=now + timedelta(days=days), assignment_id=token)


def snapshot(*assignments, term='Fall 2026', term_code='semester-token'):
    rows = [a.to_dict() for a in assignments]
    groups = categorize_assignments(rows)
    return dict(all_assignments=rows, pending=groups['pending'], missed=groups['missed'],
                latest_semester_name=term, latest_semester=term_code, total_assignments=len(rows))


@pytest.fixture
def storage():
    state = {}
    with patch('app.utils.data_manager.data_manager') as manager:
        manager.get_unified_dashboard.side_effect = lambda *a, **kw: deepcopy(state)

        def save(_, payload, **kwargs):
            state.update(deepcopy(payload))
            return True

        manager.save_unified_dashboard.side_effect = save
        yield state, manager


@pytest.fixture
def engine(storage):
    service = AssignmentNotificationService.__new__(AssignmentNotificationService)
    with patch('app.services.assignment_notification_service.get_notification_service') as delivery:
        delivery.return_value.send_notification.return_value = Mock()
        yield service, delivery.return_value.send_notification


def run(service, rows, *, fresh=True, previous=None, term='Fall 2026', account='account'):
    return service.check_and_notify('user', rows, account_id=account, snapshot=snapshot(*rows, term=term),
                                    previous_snapshot=previous, complete_scrape=fresh)


@pytest.mark.parametrize('value,requested', [('token%3D', 'token='), ('token', 'token')])
def test_dropdown_normalizes_once_and_keeps_request_token(value, requested):
    scraper = LMSScraperService.__new__(LMSScraperService)
    soup = BeautifulSoup(f'<select id="courseId"><option selected value="{value}">Networks</option></select>', 'html.parser')
    assert scraper._extract_selected_course_info(soup, requested) == dict(course_code=requested, course_name='Networks')


def test_dropdown_does_not_double_decode():
    scraper = LMSScraperService.__new__(LMSScraperService)
    soup = BeautifulSoup('<select id="courseId"><option value="token%253D">Networks</option></select>', 'html.parser')
    assert scraper._extract_selected_course_info(soup, 'token=')['course_name'] == ''


@pytest.mark.parametrize('html,accept', [
    ('<option selected value="changed">Networks</option>', True),
    ('<option value="token%3D">Networks</option>', True),
    ('<option selected value="changed">Calculus</option><option value="token=">Networks</option>', False),
    ('<option selected value="changed">Networks</option><option value="other"> networks </option>', False),
    ('<option value="changed">Networks</option>', False),
    ('<option selected value="token=">token=</option>', False),
])
def test_strict_course_context(html, accept):
    scraper = LMSScraperService.__new__(LMSScraperService)
    soup = BeautifulSoup(f'<select id="courseId">{html}</select>', 'html.parser')
    if accept:
        assert scraper._extract_selected_course_info(soup, 'token=', 'Networks', True) == dict(course_code='token=', course_name='Networks')
    else:
        with pytest.raises(LMSParsingError):
            scraper._extract_selected_course_info(soup, 'token=', 'Networks', True)


def test_missing_course_context_never_becomes_name():
    scraper = LMSScraperService.__new__(LMSScraperService)
    soup = BeautifulSoup('<table></table>', 'html.parser')
    assert scraper._extract_selected_course_info(soup, 'token')['course_name'] == ''
    with pytest.raises(LMSParsingError):
        scraper._extract_selected_course_info(soup, 'token', 'Networks', True)


def test_html_scrape_preserves_operation_tokens_and_validates_empty_or_bad_pages(storage, engine, dashboard):
    scraper = LMSScraperService.__new__(LMSScraperService)
    scraper.lms_base_url = 'https://lms.bahria.edu.pk'
    scraper.assignments_url = scraper.lms_base_url + '/Student/Assignments.php'
    scraper._get_or_create_session = Mock(return_value=object())
    scraper._requires_cms_authentication = Mock(return_value=False)
    html = '''<html><body>
      <select id="courseId"><option selected value="response-course-token">Networks</option></select>
      <table><tr><th>Assignment No</th><th>Title</th><th>Assignment File</th>
      <th>Deadline</th><th>Added Submission</th><th>Marks Obtained</th><th>Action</th></tr>
      <tr><td>1</td><td>Report</td><td></td><td>20 December 2099-11:55 pm</td>
      <td></td><td></td><td><a data-toggle="modal" data-target="#submit">Submit</a></td></tr></table>
      <div id="submit"><form><input name="assignmentID" value="operation-assignment-token">
      <input name="offeredCourseID" value="operation-course-token">
      <input name="studentID" value="operation-student-token"></form></div>
      </body></html>'''
    response = SimpleNamespace(text=html, status_code=200, url=scraper.assignments_url)
    scraper._make_request = Mock(return_value=response)
    scraper._parse_html = lambda text, _: BeautifulSoup(text, 'html.parser')
    rows = scraper.scrape_course_assignments('account', 'term-token', 'request-course-token',
                                             strict=True, expected_course_name='Networks')
    assert len(rows) == 1
    assert rows[0].course_name == 'Networks'
    assert rows[0].course_code == 'request-course-token'
    assert rows[0].assignment_id == 'operation-assignment-token'
    assert rows[0].offered_course_id == 'operation-course-token'
    assert rows[0].student_id == 'operation-student-token'
    assert scraper._make_request.call_args.args[2] == {'s': 'term-token', 'oc': 'request-course-token'}
    # Exercise the full HTML -> snapshot -> notification path with transport-token changes.
    dashboard.lms_service.scraper_service = scraper
    first = dashboard._fetch_and_cache_assignments('account', 'pending_assignments:account')
    service, send = engine
    service.check_and_notify('user', [LMSAssignment.from_dict(row) for row in first['pending']],
                              account_id='account', snapshot=first, complete_scrape=True)
    send.assert_not_called()
    response.text = html.replace('<td>1</td>', '<td>2</td>').replace('response-course-token', 'next-response-token')
    fresh = dashboard._fetch_and_cache_assignments('account', 'pending_assignments:account')
    counts = service.check_and_notify('user', [LMSAssignment.from_dict(row) for row in fresh['pending']],
                                      account_id='account', snapshot=fresh, complete_scrape=True)
    assert counts['new'] == 1
    assert storage[0]['assignments_snapshot']['pending'][0]['course_name'] == 'Networks'
    assert send.call_args.kwargs['data']['course_name'] == 'Networks'
    assert send.call_args.kwargs['data']['assignment_id'] == 'operation-assignment-token'
    response.text = html[:html.index('<tr><td>1')] + '</table></body></html>'
    assert scraper.scrape_course_assignments('account', 'term-token', 'request-course-token',
                                              strict=True, expected_course_name='Networks') == []
    response.text = '<select id="courseId"><option selected value="new">Networks</option></select><table><tr><td>error</td></tr></table>'
    with pytest.raises(LMSParsingError):
        scraper.scrape_course_assignments('account', 'term-token', 'request-course-token', strict=True,
                                          expected_course_name='Networks')


@pytest.fixture
def dashboard():
    service = LMSDashboardService.__new__(LMSDashboardService)
    service.cache = Mock()
    service.lms_service = Mock()
    service.lms_service.get_assignments_options.return_value = {
        'semesters': [{'code': 'semester-token', 'name': 'Fall 2026'}],
        'courses': [{'code': 'course-token', 'name': 'Networks'}],
    }
    service.lms_service.scraper_service.scrape_course_assignments.return_value = [assignment()]
    return service


def test_force_sync_fetches_fresh_dropdown_tokens(storage, dashboard):
    lms = LMSService.__new__(LMSService)
    lms._ensure_valid_session = Mock()
    lms._cache_assignments = Mock()
    lms.scraper_service = dashboard.lms_service.scraper_service
    cached = dashboard.lms_service.get_assignments_options.return_value
    fresh = {
        'semesters': [{'code': 'new-term', 'name': 'Fall 2026'}],
        'courses': [{'code': 'new-course', 'name': 'Networks'}],
    }
    lms._memory_cache = {'assignments_options:account': (datetime.now() + timedelta(hours=1), cached)}
    lms.scraper_service.get_assignments_options.return_value = fresh
    lms.scraper_service.scrape_course_assignments.return_value = [assignment(code='new-course')]
    dashboard.lms_service = lms

    assert lms.get_assignments_options('account') == cached
    lms.scraper_service.get_assignments_options.assert_not_called()
    result = dashboard.get_pending_assignments_widget_data('account', force_refresh=True)

    assert not result.get('error')
    assert storage[0]['assignments_snapshot']['latest_semester'] == 'new-term'
    lms.scraper_service.scrape_course_assignments.assert_called_once_with(
        'account', semester_code='new-term', course_code='new-course',
        strict=True, expected_course_name='Networks',
    )
    assert lms.get_assignments_options('account') == fresh

    lms.scraper_service.get_assignments_options.return_value = {'semesters': [], 'courses': []}
    failed = dashboard.get_pending_assignments_widget_data('account', force_refresh=True)
    assert failed['refresh_failed'] and failed['using_saved_snapshot']
    assert failed['refreshed_at'] == result['refreshed_at']
    assert failed['pending'][0]['course_code'] == 'new-course'
    assert lms.get_assignments_options('account') == fresh


def test_name_only_repair_persists_and_clears_summary(storage, dashboard):
    state, manager = storage
    first = dashboard._fetch_and_cache_assignments('account', 'pending_assignments:account')
    old = deepcopy(first)
    for group in ('all_assignments', 'pending'):
        old[group][0]['course_name'] = 'course-token'
    state['assignments_snapshot'] = old
    manager.save_unified_dashboard.reset_mock()
    dashboard.cache.delete.reset_mock()
    repaired = dashboard._fetch_and_cache_assignments('account', 'pending_assignments:account')
    assert not repaired.get('error')
    assert state['assignments_snapshot']['pending'][0]['course_name'] == 'Networks'
    manager.save_unified_dashboard.assert_called_once()
    dashboard.cache.delete.assert_called_once_with('assignments_summary:account')
    call = dashboard.lms_service.scraper_service.scrape_course_assignments.call_args
    assert call.kwargs['strict'] is True
    assert call.kwargs['expected_course_name'] == 'Networks'


def test_semester_label_only_repair_persists(storage, dashboard):
    state, manager = storage
    dashboard._fetch_and_cache_assignments('account', 'pending_assignments:account')
    state['assignments_snapshot']['latest_semester_name'] = 'Fall 2025'
    manager.save_unified_dashboard.reset_mock()
    dashboard._fetch_and_cache_assignments('account', 'pending_assignments:account')
    manager.save_unified_dashboard.assert_called_once()
    assert state['assignments_snapshot']['latest_semester_name'] == 'Fall 2026'


def test_partial_scrape_preserves_snapshot_and_monitor_cache(storage, dashboard):
    state, manager = storage
    state['assignments_snapshot'] = snapshot(assignment())
    before = deepcopy(state)
    dashboard.lms_service.scraper_service.scrape_course_assignments.side_effect = LMSParsingError('bad markup')
    result = dashboard._fetch_and_cache_assignments('account', 'pending_assignments:account')
    assert result['failed_courses']
    assert state == before
    manager.save_unified_dashboard.assert_not_called()
    dashboard.cache.set.assert_not_called()


def test_failed_snapshot_save_does_not_publish_monitor_cache(storage, dashboard):
    _, manager = storage
    manager.save_unified_dashboard.side_effect = None
    manager.save_unified_dashboard.return_value = False
    result = dashboard._fetch_and_cache_assignments('account', 'pending_assignments:account')
    assert result['error']
    dashboard.cache.set.assert_not_called()


@pytest.mark.parametrize('failure', ['error', 'partial', 'exception'])
def test_failed_manual_refresh_returns_saved_assignments_and_original_age(storage, dashboard, failure):
    state, manager = storage
    saved = snapshot(assignment(), assignment(2), assignment(3), assignment(4, days=-2), assignment(5, days=-2))
    saved['refreshed_at'] = '2026-11-17T15:07:00Z'
    state['assignments_snapshot'] = deepcopy(saved)
    failed = dashboard._empty_result('Failed to scrape')
    if failure == 'partial':
        failed.pop('error')
        failed['failed_courses'] = ['Networks']
        failed['warning'] = 'Could not load a course'
    with patch.object(dashboard, '_fetch_and_cache_assignments', return_value=failed) as fetch:
        if failure == 'exception':
            fetch.side_effect = RuntimeError('scrape failed')
        result = dashboard.get_pending_assignments_widget_data('account', force_refresh=True)
    assert result['error']
    assert result['refresh_failed'] is True
    assert result['using_saved_snapshot'] is True
    assert len(result['pending']) == result['total_pending'] == 3
    assert len(result['missed']) == result['total_missed'] == 2
    assert result['refreshed_at'] == saved['refreshed_at']
    assert state['assignments_snapshot'] == saved
    manager.save_unified_dashboard.assert_not_called()


def test_failed_first_load_has_no_fake_freshness(storage, dashboard):
    failed = dashboard._empty_result('Could not load assignments')
    with patch.object(dashboard, '_fetch_and_cache_assignments', return_value=failed):
        result = dashboard.get_pending_assignments_widget_data('account', force_refresh=True)
    assert result['refresh_failed'] is True
    assert result['using_saved_snapshot'] is False
    assert result['refreshed_at'] is None


def test_successful_empty_refresh_is_not_replaced_with_old_assignments(storage, dashboard):
    storage[0]['assignments_snapshot'] = snapshot(assignment())
    fresh = dict(snapshot(), refreshed_at='2026-11-18T15:07:00Z')
    with patch.object(dashboard, '_fetch_and_cache_assignments', return_value=fresh):
        result = dashboard.get_pending_assignments_widget_data('account', force_refresh=True)
    assert result == fresh
    assert not result.get('refresh_failed')


@pytest.mark.parametrize('partial', [False, True])
def test_failed_refresh_api_never_claims_success_or_advances_version_cookie(storage, dashboard, partial):
    from flask import Flask
    from flask_login import LoginManager
    from app.routes import lms_dashboard_api as api

    app = Flask(__name__)
    app.config.update(TESTING=True, SECRET_KEY='synthetic', LOGIN_DISABLED=True)
    LoginManager(app)
    app.register_blueprint(api.lms_dashboard_api_bp)
    account = SimpleNamespace(account_id='account', cms_username='test', cms_password='test', lms_preferences={})
    user = SimpleNamespace(is_authenticated=True, id='user', cms_account=account,
                           get_active_account=lambda: account, save=Mock())
    saved = dict(snapshot(assignment(), assignment(2, days=-2)), refreshed_at='2026-11-17T15:07:00Z')
    storage[0]['assignments_snapshot'] = saved
    failed = dashboard._empty_result('Scrape failed')
    if partial:
        failed.pop('error')
        failed['warning'] = 'Partial scrape'
    with patch.object(api, 'current_user', user), patch.object(api, 'dashboard_service', dashboard), \
         patch.object(dashboard, '_fetch_and_cache_assignments', return_value=failed):
        response = app.test_client().get('/lms/api/dashboard/pending-assignments?force_refresh=true')
    assert response.status_code == 200
    data = response.get_json()
    assert data['success'] is False
    assert data['using_saved_snapshot'] is True
    assert data['total_pending'] == 1
    assert data['total_missed'] == 1
    assert data['refreshed_at'] == saved['refreshed_at']
    assert 'no-store' in response.headers['Cache-Control']
    assert not any('lms_snapshot_version' in cookie for cookie in response.headers.getlist('Set-Cookie'))


def test_ambiguous_discovery_stops_before_scraping(storage, dashboard):
    dashboard.lms_service.get_assignments_options.return_value['courses'].append({'code': 'other', 'name': ' NETWORKS '})
    assert dashboard._fetch_and_cache_assignments('account', 'key')['error']
    dashboard.lms_service.scraper_service.scrape_course_assignments.assert_not_called()
    storage[1].save_unified_dashboard.assert_not_called()


def test_identity_ignores_transport_title_and_deadline_changes():
    a = assignment()
    key = assignment_identity('account', 'Fall 2026', a.to_dict())
    a.assignment_id, a.course_code = 'changed', 'changed-course'
    a.title, a.deadline = 'Updated report', a.deadline + timedelta(days=3)
    assert assignment_identity('account', 'Fall 2026', a.to_dict()) == key
    assert assignment_identity('other', 'Fall 2026', a.to_dict()) != key
    assert assignment_identity('account', 'Fall 2027', a.to_dict()) != key
    a.course_name = 'Calculus'
    assert assignment_identity('account', 'Fall 2026', a.to_dict()) != key


def test_identity_fallback_and_unicode_normalization():
    a = assignment(0)
    key = assignment_identity('account', 'Fall 2026', a.to_dict())
    a.course_name, a.title = '  Ｎｅｔｗｏｒｋｓ ', '  REPORT   0 '
    assert assignment_identity('account', ' fall 2026 ', a.to_dict()) == key
    a.title += '!'
    assert assignment_identity('account', 'Fall 2026', a.to_dict()) != key
    assert normalize_label('A-B') != normalize_label('A B')


def test_migration_baselines_silently_without_changing_legacy(storage, engine):
    state, _ = storage
    service, send = engine
    legacy = {'known_assignments': ['old'], 'notified_deadlines': {}}
    state['assignment_tracking'] = deepcopy(legacy)
    assert not any(run(service, [assignment()]).values())
    send.assert_not_called()
    assert state['assignment_tracking'] == legacy
    assert state['assignment_tracking_v2']['identity_version'] == 2


def test_token_changes_do_not_repeat_new_alerts_but_new_assignment_alerts_once(storage, engine):
    service, send = engine
    a = assignment()
    run(service, [a])
    for index in range(3):
        a.assignment_id, a.course_code = f'action-{index}', f'course-{index}'
        assert run(service, [a])['new'] == 0
    b = assignment(2, token='new')
    assert run(service, [a, b])['new'] == 1
    assert run(service, [a, b])['new'] == 0
    assert send.call_count == 1
    assert send.call_args.kwargs['data']['course_name'] == 'Networks'
    assert send.call_args.kwargs['title'] == 'New Assignment — Networks'


@pytest.mark.parametrize('kind', ['new', '24h', '3d', 'overdue'])
def test_migration_preserves_latest_legacy_alias_timestamp(kind):
    old, current = assignment(token='old'), assignment(token='current')
    old_snapshot, current_snapshot = snapshot(old), snapshot(current)
    legacy = {'known_assignments': ['old', 'current'], 'notified_deadlines': {
        f'old_{kind}': '2026-11-17T20:00:00',
        f'current_{kind}': {'first_notified': '2026-11-17T20:00:00', 'last_notified': '2026-11-18T12:00:00', 'count': 2},
    }}
    result = migrate_tracking('account', legacy, current_snapshot, old_snapshot)
    key = next(iter(result['known_assignments']))
    assert result['notified_deadlines'][f'{key}_{kind}'] == legacy['notified_deadlines'][f'current_{kind}']


def test_legacy_fallback_alias_and_token_only_label_recovery():
    old, current = assignment(token=None), assignment(token='current')
    old.course_name = old.course_code
    key = assignment_identity('account', 'Fall 2026', current.to_dict())
    legacy = {'notified_deadlines': {'1_Report 1_course-token_3d': '2026-11-18T12:00:00'}}
    result = migrate_tracking('account', legacy, snapshot(current), snapshot(old))
    assert result['notified_deadlines'][f'{key}_3d'] == '2026-11-18T12:00:00'


def test_ambiguous_alias_does_not_suppress_another_courses_reminder():
    first = assignment(course='Networks', token='same')
    second = assignment(course='Calculus', code='other', token='same')
    result = migrate_tracking('account', {'notified_deadlines': {'same_overdue': '2026-11-18T12:00:00'}}, snapshot(first, second), None)
    assert len(result['known_assignments']) == 2
    assert not result['notified_deadlines']


def test_different_semesters_do_not_share_legacy_aliases():
    old, new = assignment(token='old'), assignment(token='new')
    result = migrate_tracking('account', {'notified_deadlines': {'old_3d': '2026-11-18T12:00:00'}},
                              snapshot(new), snapshot(old, term='Spring 2026'))
    assert not result['notified_deadlines']


def test_deadline_reminders_survive_silent_baseline(storage, engine):
    service, send = engine
    a = assignment(days=2)
    result = run(service, [a])
    assert result['new'] == 0
    assert result['due_3d'] == 1
    assert run(service, [a])['due_3d'] == 0
    assert send.call_count == 1


def test_migration_preserves_delivered_deadline_reminder(storage, engine):
    state, _ = storage
    service, send = engine
    old, new = assignment(token='old', days=2), assignment(token='new', days=2)
    state['assignment_tracking'] = {'notified_deadlines': {'old_3d': '2026-11-17T20:00:00'}}
    assert not any(run(service, [new], previous=snapshot(old)).values())
    send.assert_not_called()


def test_failed_migration_never_sends_or_claims_success(storage, engine, caplog):
    _, manager = storage
    service, send = engine
    manager.save_unified_dashboard.side_effect = None
    manager.save_unified_dashboard.return_value = False
    with pytest.raises(RuntimeError, match='tracking migration'):
        run(service, [assignment(days=2)])
    send.assert_not_called()
    assert 'migration completed' not in caplog.text


def test_failed_migration_retry_keeps_old_reminder_aliases_across_refreshes(storage, engine, dashboard):
    state, manager = storage
    service, send = engine
    old = assignment(token='old-action', code='old-course', days=2)
    current = assignment(token='new-action', days=2)
    state['assignments_snapshot'] = snapshot(old)
    state['assignment_tracking'] = {
        'known_assignments': ['old-action'],
        'notified_deadlines': {'old-action_3d': '2026-11-18T12:00:00'},
    }
    dashboard.lms_service.scraper_service.scrape_course_assignments.return_value = [current]
    save = manager.save_unified_dashboard.side_effect
    failure = [True]

    def fail_first_migration(account_id, payload, **kwargs):
        if 'assignment_tracking_v2' in payload and failure[0]:
            failure[0] = False
            return False
        return save(account_id, payload, **kwargs)

    manager.save_unified_dashboard.side_effect = fail_first_migration
    account = SimpleNamespace(account_id='account', cms_username='test', cms_password='test')
    user = SimpleNamespace(id='user')
    with patch('app.services.lms_dashboard_service.LMSDashboardService', return_value=dashboard), \
         patch('app.services.lms_service.LMSService'), \
         patch('app.services.assignment_notification_service.get_assignment_notification_service', return_value=service):
        with pytest.raises(RuntimeError, match='tracking migration'):
            _check_assignments(user, account)
        assert state['assignments_snapshot']['all_assignments'][0]['assignment_id'] == 'new-action'
        checkpoint = deepcopy(state['assignment_tracking_migration_snapshot'])
        assert checkpoint['all_assignments'][0]['assignment_id'] == 'old-action'
        assert 'assignment_tracking_v2' not in state
        send.assert_not_called()

        # A separate refresh or process restart must not replace the original aliases.
        current.course_code = 'next-course-token'
        dashboard.lms_service.get_assignments_options.return_value['courses'][0]['code'] = current.course_code
        dashboard._fetch_and_cache_assignments('account', 'pending_assignments:account')
        assert state['assignment_tracking_migration_snapshot'] == checkpoint
        assert _check_assignments(user, account) == 0
        send.assert_not_called()
        assert state['assignment_tracking_v2']['identity_version'] == 2
        assert state['assignment_tracking_migration_snapshot'] is None
        key = assignment_identity('account', 'Fall 2026', current.to_dict())
        assert state['assignment_tracking_v2']['notified_deadlines'][f'{key}_3d'] == '2026-11-18T12:00:00'


def test_snapshot_checkpoint_is_minimal_and_stops_after_migration():
    previous = snapshot(assignment())
    previous['all_assignments'][0]['submission_file'] = 'private-submission'
    payload = assignment_snapshot_payload({'assignments_snapshot': previous}, snapshot())
    checkpoint = payload['assignment_tracking_migration_snapshot']
    assert set(checkpoint['all_assignments'][0]) == {
        'assignment_id', 'assignment_no', 'title', 'course_code', 'course_name',
    }
    assert 'private-submission' not in str(checkpoint)
    assert assignment_snapshot_payload({
        'assignments_snapshot': previous, 'assignment_tracking_v2': {'identity_version': 2},
    }, snapshot()) == {'assignments_snapshot': snapshot()}


def test_failed_v2_delivery_and_persistence_are_reported_and_retryable(storage, engine):
    _, manager = storage
    service, send = engine
    run(service, [assignment()])
    a = assignment(2, token='second', days=2)
    send.return_value = None
    assert not any(run(service, [a]).values())
    send.return_value = Mock()
    assert run(service, [a])['new'] == 1
    manager.save_unified_dashboard.side_effect = None
    manager.save_unified_dashboard.return_value = False
    with pytest.raises(RuntimeError, match='notification progress'):
        run(service, [assignment(3)])


@pytest.mark.parametrize('submitted', [False, True])
def test_submitted_and_missed_never_get_new_alert(storage, engine, submitted):
    service, send = engine
    run(service, [assignment()])
    a = assignment(2, token='second', days=-2)
    if submitted:
        a.submission_status = 'submitted'
    assert run(service, [a])['new'] == 0
    assert all(call.kwargs['notification_type'].value != 'assignment_new' for call in send.call_args_list)


def test_collisions_block_migration_and_skip_v2_alerts(storage, engine):
    service, send = engine
    first, second = assignment(), assignment(token='other')
    second.title = first.title
    assert identity_index('account', snapshot(first, second))[1] == 2
    with pytest.raises(ValueError, match='invalid assignment metadata'):
        run(service, [first, second])
    assert 'assignment_tracking_v2' not in storage[0]
    run(service, [first])
    assert not any(run(service, [first, second]).values())
    send.assert_not_called()


def test_duplicate_submission_rows_allow_migration_without_alerts(storage, engine):
    state, _ = storage
    service, send = engine
    first = assignment(7, token=None, days=-2)
    second = assignment(7, token='other-submission', days=-2)
    first.title = second.title = 'LAB 07'
    first.submission_file = '/submitted-file'
    indexed, invalid = identity_index('account', snapshot(first, second))
    assert not indexed and invalid == 2

    second.submission_status = 'submitted'
    assert not any(run(service, [first, second]).values())
    tracking = state['assignment_tracking_v2']
    assert len(tracking['known_assignments']) == 1
    send.assert_not_called()

    new = assignment(8, token='new-assignment')
    assert run(service, [second, new])['new'] == 1
    assert not any(run(service, [first, second, new]).values())
    send.assert_called_once()


@pytest.mark.parametrize('background', [False, True])
def test_complete_scrape_updates_monitor_even_with_conflicting_notification_ids(storage, dashboard, background, caplog):
    state, _ = storage
    first, second = assignment(), assignment(token='other', days=-2)
    before = snapshot(assignment(3))
    state['assignments_snapshot'] = before
    lms = dashboard.lms_service
    lms.scraper_service.scrape_course_assignments.return_value = [first, second]
    assert identity_index('account', snapshot(first, second))[1] == 2

    if background:
        lms.get_assignments_courses_for_semester.return_value = lms.get_assignments_options.return_value['courses']
        with patch('app.services.lms_cache_service.get_cache_service'):
            assert LMSBackgroundService(lms)._refresh_all_pending_assignments('account')['success']
    else:
        refresh = dashboard.get_pending_assignments_widget_data('account', force_refresh=True)
        assert not refresh.get('error')
        assert not refresh.get('using_saved_snapshot')

    saved = state['assignments_snapshot']
    assert saved['total_pending'] == saved['total_missed'] == 1
    assert {row['assignment_id'] for row in saved['all_assignments']} == {'action-token', 'other'}
    assert 'assignment_tracking_v2' not in state
    assert state['assignment_tracking_migration_snapshot']['all_assignments'][0]['assignment_no'] == 3
    diagnostic = [record.getMessage() for record in caplog.records
                  if record.getMessage().startswith('Assignment identity diagnostic:')]
    assert diagnostic == ["Assignment identity diagnostic: {'collision_distinct_action_ids': 2}"]


def test_reused_display_numbers_with_different_titles_are_valid_assignments(storage, dashboard):
    first, second = assignment(token='first'), assignment(token='second')
    first.title, second.title = 'Assignment 01', 'CCP'
    indexed, invalid = identity_index('account', snapshot(first, second))
    assert invalid == 0
    assert len(indexed) == 2
    dashboard.lms_service.scraper_service.scrape_course_assignments.return_value = [first, second]
    result = dashboard._fetch_and_cache_assignments('account', 'pending_assignments:account')
    assert not result.get('error')
    assert result['total_pending'] == 2
    assert {row['title'] for row in storage[0]['assignments_snapshot']['pending']} == {'Assignment 01', 'CCP'}


def test_legacy_migration_preserves_reminders_for_reused_numbers():
    first, second = assignment(token='first', days=2), assignment(token='second', days=2)
    first.title, second.title = 'Assignment 01', 'CCP'
    old = snapshot(first, second)
    first.assignment_id, second.assignment_id = 'changed-first', 'changed-second'
    legacy = {'notified_deadlines': {'first_3d': '2026-11-18T12:00:00', 'second_3d': '2026-11-18T13:00:00'}}
    migrated = migrate_tracking('account', legacy, snapshot(first, second), old)
    assert len(migrated['known_assignments']) == 2
    for row, timestamp in [(first, '2026-11-18T12:00:00'), (second, '2026-11-18T13:00:00')]:
        key = assignment_title_identity('account', 'Fall 2026', row.to_dict())
        assert migrated['notified_deadlines'][f'{key}_3d'] == timestamp


def test_new_work_reusing_a_known_number_does_not_replay_existing_work(storage, engine):
    service, send = engine
    old = assignment(token='old')
    old.title = 'Assignment 01'
    run(service, [old])
    previous = snapshot(old)
    new = assignment(token='new')
    new.title = 'CCP'
    old.assignment_id = 'changed-old'
    assert run(service, [old, new], previous=previous)['new'] == 1
    assert send.call_args.kwargs['message'] == 'CCP has been posted'
    assert run(service, [old, new])['new'] == 0
    assert run(service, [new])['new'] == 0
    third = assignment(token='third')
    third.title = 'Another report'
    assert run(service, [third])['new'] == 1
    assert run(service, [third])['new'] == 0


def test_reused_number_transition_keeps_delivered_deadline_reminders(storage, engine):
    service, send = engine
    old = assignment(token='old', days=2)
    old.title = 'Assignment 01'
    assert run(service, [old])['due_3d'] == 1
    previous = snapshot(old)
    new = assignment(token='new', days=2)
    new.title = 'CCP'
    send.reset_mock()
    counts = run(service, [old, new], previous=previous)
    assert counts['new'] == 1
    assert counts['due_3d'] == 1
    assert all('CCP' in call.kwargs['message'] for call in send.call_args_list)
    assert not any(run(service, [old, new]).values())


def test_failed_number_reconciliation_keeps_durable_aliases_for_retry(storage, engine, dashboard):
    state, manager = storage
    service, send = engine
    old = assignment(token='old', days=2)
    old.title = 'Assignment 01'
    run(service, [old])
    state['assignments_snapshot'] = snapshot(old)
    new = assignment(token='new', days=2)
    new.title = 'CCP'
    dashboard.lms_service.scraper_service.scrape_course_assignments.return_value = [old, new]
    user = SimpleNamespace(id='user')
    account = SimpleNamespace(account_id='account', cms_username='test', cms_password='test')
    save = manager.save_unified_dashboard.side_effect
    fail = [True]

    def save_with_failure(account_id, payload, **kwargs):
        if 'assignment_tracking_v2' in payload and fail[0]:
            fail[0] = False
            return False
        return save(account_id, payload, **kwargs)

    manager.save_unified_dashboard.side_effect = save_with_failure
    send.reset_mock()
    with patch('app.services.lms_dashboard_service.LMSDashboardService', return_value=dashboard), \
         patch('app.services.lms_service.LMSService'), \
         patch('app.services.assignment_notification_service.get_assignment_notification_service', return_value=service):
        with pytest.raises(RuntimeError, match='identity reconciliation'):
            _check_assignments(user, account)
        checkpoint = state['assignment_tracking_migration_snapshot']
        assert checkpoint['all_assignments'][0]['title'] == 'Assignment 01'
        send.assert_not_called()
        assert _check_assignments(user, account) == 2
        assert state['assignment_tracking_migration_snapshot'] is None
        assert all('CCP' in call.kwargs['message'] for call in send.call_args_list)


def test_urgent_checks_can_use_checkpointed_v2_aliases_without_replaying_old_reminders(storage, engine):
    service, send = engine
    old = assignment(token='old', days=-2)
    old.title = 'Assignment 01'
    assert run(service, [old])['overdue'] == 1
    previous = snapshot(old)
    new = assignment(token='new', days=-2)
    new.title = 'CCP'
    send.reset_mock()
    counts = service.check_and_notify('user', [old, new], account_id='account', urgent_only=True,
                                     snapshot=snapshot(old, new), previous_snapshot=previous)
    assert counts['new'] == 0
    assert counts['overdue'] == 1
    assert send.call_args.kwargs['message'] == 'CCP deadline has passed'


def test_urgent_check_uses_legacy_until_fresh_migration(storage, engine):
    state, _ = storage
    service, send = engine
    a = assignment(days=2)
    legacy = {'known_assignments': ['action-token'], 'notified_deadlines': {'action-token_3d': '2026-11-17T20:00:00'}}
    state['assignment_tracking'] = deepcopy(legacy)
    service.check_and_notify('user', [a], account_id='account', urgent_only=True, snapshot=snapshot(a))
    assert 'assignment_tracking_v2' not in state
    assert state['assignment_tracking'] == legacy
    send.assert_not_called()


def test_urgent_check_uses_v2_after_token_change(storage, engine):
    service, send = engine
    a = assignment(days=-2)
    assert run(service, [a])['overdue'] == 1
    a.assignment_id, a.course_code = 'changed-action', 'changed-course'
    result = service.check_and_notify('user', [a], account_id='account', urgent_only=True, snapshot=snapshot(a))
    assert not any(result.values())
    assert send.call_count == 1


def test_cached_urgent_runner_passes_snapshot_without_authorizing_migration(storage, engine):
    state, _ = storage
    service, send = engine
    a = assignment(days=-2)
    run(service, [a])
    a.assignment_id, a.course_code = 'changed-action', 'changed-course'
    state['assignments_snapshot'] = snapshot(a)
    user = SimpleNamespace(id='user', account_id='account',
                           cms_account=SimpleNamespace(account_id='account', cms_username=''),
                           is_scheduled_check_eligible=lambda: True)
    with patch('app.models.account.User.list_all', return_value=[user]), \
         patch('app.services.assignment_notification_service.get_assignment_notification_service', return_value=service):
        assert check_cached_urgent_assignments_only() == 'Evaluated 1 users, skipped 0 (no Discord/alerts), pushed 0 urgent notifications'
    assert send.call_count == 1


def test_legacy_cached_snapshot_without_term_metadata_remains_legacy(storage, engine):
    state, _ = storage
    service, send = engine
    a = assignment(days=-2)
    cached = snapshot(a)
    cached.pop('latest_semester_name')
    counts = service.check_and_notify('user', [a], account_id='account', urgent_only=True, snapshot=cached)
    assert counts['overdue'] == 1
    assert 'assignment_tracking_v2' not in state
    assert 'action-token_overdue' in state['assignment_tracking']['notified_deadlines']
    assert send.call_count == 1


def test_cached_corrupt_labels_do_not_escape_through_legacy_urgent_checks(storage, engine):
    service, send = engine
    a = assignment(days=-2)
    a.course_name = a.course_code
    assert not any(service.check_and_notify('user', [a], account_id='account', urgent_only=True,
                                             snapshot=snapshot(a)).values())
    send.assert_not_called()
    assert not storage[0]


def test_v2_cleanup_preserves_recent_delivery_metadata(storage, engine):
    state, _ = storage
    service, _ = engine
    run(service, [assignment(days=-2)])
    before = deepcopy(state['assignment_tracking_v2'])
    state['assignment_tracking_v2']['notified_deadlines']['expired'] = '2000-01-01T00:00:00'
    assert service.cleanup_old_tracking('account') == 1
    assert state['assignment_tracking_v2'] == before


def test_partial_scrape_cannot_initialize_migration(storage, engine):
    service, send = engine
    a = assignment()
    partial = dict(snapshot(a), failed_courses=['Calculus'])
    with pytest.raises(ValueError, match='incomplete'):
        service.check_and_notify('user', [a], account_id='account', snapshot=partial, complete_scrape=True)
    assert not storage[0]
    send.assert_not_called()


def test_empty_complete_snapshot_migrates_without_alerts(storage, engine):
    service, send = engine
    assert not any(run(service, []).values())
    assert storage[0]['assignment_tracking_v2']['known_assignments'] == []
    send.assert_not_called()


def test_scheduler_captures_previous_snapshot_and_rejects_partial(storage):
    state, _ = storage
    old = snapshot(assignment(token='old'))
    state['assignments_snapshot'] = deepcopy(old)
    user = SimpleNamespace(id='user')
    account = SimpleNamespace(account_id='account', cms_username='test', cms_password='test')
    fresh = snapshot(assignment(token='new'))
    with patch('app.services.lms_dashboard_service.LMSDashboardService') as dashboard, \
         patch('app.services.lms_service.LMSService'), \
         patch('app.services.assignment_notification_service.get_assignment_notification_service') as notify:
        def fetch(*args):
            state['assignments_snapshot'] = deepcopy(fresh)
            return fresh
        dashboard.return_value._fetch_and_cache_assignments.side_effect = fetch
        notify.return_value.check_and_notify.return_value = {'new': 0}
        _check_assignments(user, account)
        call = notify.return_value.check_and_notify.call_args
        assert call.kwargs['previous_snapshot'] == old
        assert call.kwargs['complete_scrape'] is True
        fresh['failed_courses'] = ['Calculus']
        notify.reset_mock()
        with pytest.raises(Exception, match='invalid/empty snapshot'):
            _check_assignments(user, account)
        notify.return_value.check_and_notify.assert_not_called()


def test_other_background_refresh_uses_strict_metadata_and_preserves_partial(storage):
    state, manager = storage
    lms = Mock()
    lms.get_assignments_options.return_value = {'semesters': [{'code': 'term', 'name': 'Fall 2026'}]}
    lms.get_assignments_courses_for_semester.return_value = [{'code': 'course-token', 'name': 'Networks'}]
    lms.scraper_service.scrape_course_assignments.return_value = [assignment()]
    service = LMSBackgroundService(lms)
    with patch('app.services.lms_cache_service.get_cache_service') as cache:
        result = service._refresh_all_pending_assignments('account')
        assert result['success']
        assert state['assignments_snapshot']['latest_semester_name'] == 'Fall 2026'
        assert lms.scraper_service.scrape_course_assignments.call_args.kwargs['strict'] is True
        cache.return_value.delete.assert_called_with('assignments_summary:account')
        before = deepcopy(state)
        manager.save_unified_dashboard.reset_mock()
        lms.scraper_service.scrape_course_assignments.side_effect = LMSParsingError('bad markup')
        assert not service._refresh_all_pending_assignments('account')['success']
        assert state == before
        manager.save_unified_dashboard.assert_not_called()
