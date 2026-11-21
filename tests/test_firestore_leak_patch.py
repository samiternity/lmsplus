import unittest
from unittest.mock import MagicMock, patch
import os
import uuid
from datetime import datetime
import pytz

from app.models.account import User, CMSAccount
from app.utils.lms_logger import resolve_enrollment, _account_enrollment_cache, register_account_enrollment
from app.services.notification_scheduler import NotificationScheduler


class TestFirestoreLeakPatch(unittest.TestCase):
    def setUp(self):
        _account_enrollment_cache.clear()

    def test_get_by_account_id_empty_input(self):
        """None or whitespace input should return None immediately with 0 queries."""
        with patch.object(User, '_get_firestore') as mock_fs, \
             patch.object(User, 'list_all') as mock_list_all:
            self.assertIsNone(User.get_by_account_id(None))
            self.assertIsNone(User.get_by_account_id(""))
            self.assertIsNone(User.get_by_account_id("   "))
            mock_fs.assert_not_called()
            mock_list_all.assert_not_called()

    def test_get_by_account_id_firestore_no_fallback_to_list_all(self):
        """When Firestore is active and account is not found, it must NOT call list_all."""
        mock_fs = MagicMock()
        mock_db = MagicMock()
        mock_fs.db = mock_db
        mock_collection = MagicMock()
        mock_db.collection.return_value = mock_collection

        # Return empty stream for queries
        mock_collection.where.return_value.limit.return_value.stream.return_value = iter([])

        with patch.object(User, '_get_firestore', return_value=mock_fs), \
             patch.object(User, 'list_all') as mock_list_all:
            result = User.get_by_account_id("11111111-2222-3333-4444-555555555555")
            self.assertIsNone(result)
            # Critical assertion: list_all was the leak vector downloading 225 docs!
            mock_list_all.assert_not_called()
            # UUID format must skip direct doc lookup to avoid 1 unneeded read
            mock_collection.document.assert_not_called()

    def test_get_by_account_id_skips_doc_lookup_on_uuid(self):
        """Standard 36-char UUID skips direct doc get to avoid billable read on missing doc."""
        mock_fs = MagicMock()
        mock_db = MagicMock()
        mock_fs.db = mock_db
        mock_collection = MagicMock()
        mock_db.collection.return_value = mock_collection
        mock_collection.where.return_value.limit.return_value.stream.return_value = iter([])

        test_uuid = str(uuid.uuid4())
        with patch.object(User, '_get_firestore', return_value=mock_fs):
            res = User.get_by_account_id(test_uuid)
            self.assertIsNone(res)
            mock_collection.document.assert_not_called()

    def test_get_by_account_id_tries_doc_lookup_on_username(self):
        """Username/enrollment format (< 30 chars, <= 2 hyphens) performs direct doc lookup."""
        mock_fs = MagicMock()
        mock_db = MagicMock()
        mock_fs.db = mock_db
        mock_collection = MagicMock()
        mock_db.collection.return_value = mock_collection
        mock_collection.where.return_value.limit.return_value.stream.return_value = iter([])

        mock_doc = MagicMock()
        mock_doc.exists = True
        mock_doc.id = "admin"
        mock_doc.to_dict.return_value = {"username": "admin", "email": "admin@unitrack.com"}
        mock_collection.document.return_value.get.return_value = mock_doc

        with patch.object(User, '_get_firestore', return_value=mock_fs):
            user = User.get_by_account_id("admin")
            self.assertIsNotNone(user)
            self.assertEqual(user.username, "admin")
            mock_collection.document.assert_called_with("admin")

    def test_get_by_account_id_firestore_finds_nested_cms_account(self):
        """Finds user via nested cms_account.account_id query."""
        mock_fs = MagicMock()
        mock_db = MagicMock()
        mock_fs.db = mock_db
        mock_collection = MagicMock()
        mock_db.collection.return_value = mock_collection

        # First query (top-level) empty
        query_top = MagicMock()
        query_top.limit.return_value.stream.return_value = iter([])

        # Second query (nested cms_account.account_id) returns doc
        query_nested = MagicMock()
        found_doc = MagicMock()
        found_doc.id = "02-000000-009"
        found_doc.to_dict.return_value = {
            "username": "02-000000-009",
            "email": "test@student.com",
            "batch_bucket": 15,
            "cms_account": {
                "account_id": "target-uuid-123",
                "cms_username": "02-000000-009"
            }
        }
        query_nested.limit.return_value.stream.return_value = iter([found_doc])

        def where_side_effect(*args, **kwargs):
            # Inspect filter argument
            f = kwargs.get('filter')
            if f and getattr(f, 'field_path', None) == 'cms_account.account_id':
                return query_nested
            # If where called with top-level or other
            return query_top

        mock_collection.where.side_effect = where_side_effect

        with patch.object(User, '_get_firestore', return_value=mock_fs), \
             patch.object(User, 'list_all') as mock_list_all:
            user = User.get_by_account_id("target-uuid-123")
            self.assertIsNotNone(user)
            self.assertEqual(user.username, "02-000000-009")
            mock_list_all.assert_not_called()

    def test_resolve_enrollment_caching_and_negative_miss(self):
        """Negative misses must be cached to prevent repeated DB hits per log line."""
        test_uuid = str(uuid.uuid4())
        with patch.object(User, 'get_by_account_id') as mock_get_user:
            mock_get_user.return_value = None

            # First call queries DB
            res1 = resolve_enrollment(test_uuid)
            self.assertEqual(res1, test_uuid)
            self.assertEqual(mock_get_user.call_count, 1)

            # Second call must hit cache, NOT query DB
            res2 = resolve_enrollment(test_uuid)
            self.assertEqual(res2, test_uuid)
            self.assertEqual(mock_get_user.call_count, 1)

    def test_resolve_enrollment_direct_enrollment_number(self):
        """Enrollment pattern (02-000000-009) is returned and cached without querying User."""
        with patch.object(User, 'get_by_account_id') as mock_get_user:
            res = resolve_enrollment("02-000000-009")
            self.assertEqual(res, "02-000000-009")
            mock_get_user.assert_not_called()
            self.assertEqual(_account_enrollment_cache.get("02-000000-009"), "02-000000-009")

    def test_notification_scheduler_user_object_passed_without_reget(self):
        """NotificationScheduler must pass User objects directly to workers, avoiding User.get redundant read."""
        user = User(
            id="02-000000-009",
            username="02-000000-009",
            email="test@student.com",
            account_id="user-acc-1",
            batch_bucket=20,
            cms_account=CMSAccount(account_id="cms-acc-1", cms_username="02-000000-009", cms_password="pw")
        )

        scheduler = NotificationScheduler(app=None)

        with patch.object(User, 'get_users_by_batch', return_value=[user]), \
             patch.object(User, 'get') as mock_user_get, \
             patch.object(user, 'is_scheduled_check_eligible', return_value=False):
            
            res = scheduler._run_notifications(bucket=20)
            # User.get should NOT be called because User object was preserved
            mock_user_get.assert_not_called()
            self.assertEqual(res['users_processed'], 1)
            self.assertEqual(res['skipped_count'], 1)

    def test_notification_scheduler_pre_registers_both_account_ids(self):
        """NotificationScheduler pre-registers top-level, nested account_id, and user.id in logger cache."""
        user = User(
            id="user-custom-id",
            username="02-000000-009",
            email="test@student.com",
            account_id="user-acc-top",
            batch_bucket=20,
            cms_account=CMSAccount(account_id="cms-acc-nested", cms_username="02-000000-009", cms_password="pw")
        )

        scheduler = NotificationScheduler(app=None)

        with patch.object(User, 'get_users_by_batch', return_value=[user]), \
             patch.object(user, 'is_scheduled_check_eligible', return_value=True), \
             patch.object(scheduler, '_process_account', return_value=0):
            
            scheduler._run_notifications(bucket=20)
            # All accounts must be registered in the cache
            self.assertEqual(_account_enrollment_cache.get("user-acc-top"), "02-000000-009")
            self.assertEqual(_account_enrollment_cache.get("cms-acc-nested"), "02-000000-009")
            self.assertEqual(_account_enrollment_cache.get("user-custom-id"), "02-000000-009")

    def test_minute_catch_up_then_hourly_fallback(self):
        from copy import deepcopy
        from flask import Flask
        from google.cloud import firestore
        from app.routes import scheduler as scheduler_routes

        app = Flask(__name__)
        app.register_blueprint(scheduler_routes.scheduler_bp)
        scheduler = app.notification_scheduler = NotificationScheduler(app)
        firestore_service = MagicMock()
        scheduler._get_firestore = MagicMock(return_value=firestore_service)
        scheduler.get_kill_switch_status = MagicMock(return_value={'active': False})
        scheduler._log_free_tier_usage = MagicMock()
        documents = {}

        def document_ref(name):
            document = MagicMock()
            document.key = name

            def get_snapshot(**kwargs):
                data = documents.get(name)
                return MagicMock(exists=data is not None, to_dict=lambda: deepcopy(data))

            document.get.side_effect = get_snapshot
            return document

        def update_document(ref, fields):
            for key, value in fields.items():
                if value is firestore.DELETE_FIELD:
                    documents[ref.key].pop(key, None)
                else:
                    documents[ref.key][key] = value

        firestore_service.db.collection.return_value.document.side_effect = document_ref
        transaction = firestore_service.db.transaction.return_value
        transaction.set.side_effect = lambda ref, data: documents.update({ref.key: deepcopy(data)})
        transaction.update.side_effect = update_document
        client = app.test_client()

        completed_batch = {
            'users_processed': 3, 'confirmed_discord_dms': 2, 'notifications_sent': 2,
        }
        documents['daily_discord_stats_afternoon'] = {
            'date': '2026-11-20',
            'stats_by_bucket': {str(i): completed_batch for i in range(59)},
        }
        missed = {21, 23, 38, 59}
        documents['daily_discord_stats_evening'] = {
            'date': '2026-11-20',
            'stats_by_bucket': {str(i): completed_batch for i in range(60) if i not in missed},
            'batch_claims': {'59': {'id': 'in-flight', 'expires_at': float('inf')}},
        }

        retries = []

        def process_batch(bucket, **kwargs):
            # Simulate the original Scheduler request arriving while catch-up holds this lease.
            if bucket == 21:
                retries.append(post('/api/internal/run-notifications', '2026-11-20T15:21:00Z').json)
            return completed_batch.copy()

        def post(path, scheduled_time, *, actual_time=None):
            actual = datetime.fromisoformat(actual_time or scheduled_time).astimezone(
                pytz.timezone('Asia/Karachi'),
            )
            with patch('app.services.notification_scheduler._pakistan_wall_time', return_value=actual):
                return client.post(path, headers={
                    'Authorization': 'Bearer test-secret',
                    'X-CloudScheduler-ScheduleTime': scheduled_time,
                })

        with (
            patch('google.cloud.firestore.transactional', side_effect=lambda fn: fn),
            patch.object(scheduler_routes, '_SECRET', 'test-secret'),
            patch.object(scheduler, '_run_notifications', side_effect=process_batch) as run_batch,
            patch('app.services.discord_service.enqueue_webhook_log', side_effect=[True, True, False, True]) as webhook,
            patch('app.services.background_notification_service.check_cached_urgent_assignments_only',
                  return_value='checked') as reminders,
        ):
            self.assertEqual(post('/api/internal/run-notifications', '2026-11-20T09:59:00Z').status_code, 200)
            webhook.assert_not_called()
            afternoon = post('/api/internal/urgent-check', '2026-11-20T10:00:00Z')
            self.assertEqual(afternoon.json['summary'], 'Success: checked')
            self.assertEqual(webhook.call_count, 1)
            self.assertTrue(webhook.call_args.kwargs['title'].endswith('Summary'))
            self.assertIn('Afternoon', webhook.call_args.kwargs['title'])

            post('/api/internal/urgent-check', '2026-11-20T16:00:00Z')
            self.assertEqual(webhook.call_count, 2)
            self.assertTrue(webhook.call_args.kwargs['title'].endswith('Progress'))
            self.assertIn('9:01 PM PKT', webhook.call_args.kwargs['description'])
            fields = {field['name']: field['value'] for field in webhook.call_args.kwargs['fields']}
            self.assertEqual(fields['Completed Batches'], '56 / 60')
            self.assertEqual(fields['Pending Batches'], '4')
            self.assertEqual(fields['Batches In Progress'], '1')
            self.assertTrue(documents['daily_discord_stats_evening']['progress_summary_sent'])
            self.assertEqual(reminders.call_args.kwargs['completed_buckets'], set(range(60)) - missed)
            self.assertEqual(run_batch.call_count, 1)

            for minute in range(4):
                response = post('/api/internal/run-notifications', f'2026-11-20T16:{minute:02d}:00Z')
                self.assertEqual(response.json['summary']['mode'], 'catch_up')
            self.assertEqual(retries[0]['summary']['status'], 'already_claimed')
            self.assertEqual(
                [call.kwargs['bucket'] for call in run_batch.call_args_list], [59, 21, 38, 23],
            )
            self.assertEqual(len(documents['daily_discord_stats_evening']['stats_by_bucket']), 59)
            self.assertEqual(webhook.call_count, 2)

            documents['daily_discord_stats_evening']['batch_claims']['59']['expires_at'] = 0
            post('/api/internal/run-notifications', '2026-11-20T16:04:00Z')
            self.assertEqual(webhook.call_count, 3)
            self.assertFalse(documents['daily_discord_stats_evening']['summary_sent'])
            self.assertEqual(run_batch.call_args.kwargs['bucket'], 59)

            post('/api/internal/run-notifications', '2026-11-20T16:05:00Z')
            self.assertEqual(webhook.call_count, 4)
            self.assertTrue(documents['daily_discord_stats_evening']['summary_sent'])
            self.assertIn('Evening', webhook.call_args.kwargs['title'])
            fields = {field['name']: field['value'] for field in webhook.call_args.kwargs['fields']}
            self.assertEqual(fields['Completed Batches'], '60 / 60')
            self.assertEqual(fields['Users Evaluated'], '180')
            self.assertEqual(fields['Notifications Sent'], '120')

            firestore_calls = scheduler._get_firestore.call_count
            closed = post('/api/internal/run-notifications', '2026-11-20T16:10:00Z')
            self.assertEqual(closed.json['summary']['status'], 'window_closed')
            self.assertEqual(scheduler._get_firestore.call_count, firestore_calls)

            late = post('/api/internal/run-notifications', '2026-11-20T16:09:00Z',
                        actual_time='2026-11-20T16:10:00Z')
            self.assertEqual(late.json['summary']['status'], 'window_closed')
            stale = post('/api/internal/run-notifications', '2026-11-20T15:59:00Z',
                         actual_time='2026-11-21T15:59:00Z')
            self.assertEqual(stale.json['summary']['status'], 'expired')
            past_catch_up = post('/api/internal/run-notifications', '2026-11-20T15:59:00Z',
                                 actual_time='2026-11-20T16:10:00Z')
            self.assertEqual(past_catch_up.json['summary']['status'], 'expired')
            invalid = post('/api/internal/run-notifications?bucket=abc', '2026-11-20T15:59:00Z')
            self.assertEqual(invalid.status_code, 400)
            self.assertEqual(scheduler._get_firestore.call_count, firestore_calls)

            duplicate = post('/api/internal/run-notifications', '2026-11-20T15:59:00Z')
            self.assertEqual(duplicate.json['summary']['status'], 'already_claimed')
            self.assertEqual(run_batch.call_count, 5)
            self.assertEqual(webhook.call_count, 4)
            self.assertEqual(reminders.call_count, 2)

            # A failed batch stays retryable and the last hourly report marks the gap.
            webhook.reset_mock(side_effect=True, return_value=True)
            webhook.return_value = True
            run_batch.reset_mock()
            run_batch.side_effect = lambda **kwargs: {'error': 'Firestore unavailable'}
            documents['daily_discord_stats_evening'] = {
                'date': '2026-11-21',
                'stats_by_bucket': {str(i): completed_batch for i in range(60) if i != 21},
            }
            with (
                app.test_request_context(headers={'X-CloudScheduler-ScheduleTime': '2026-11-21T17:00:00Z'}),
                patch('app.services.notification_scheduler._pakistan_wall_time',
                      return_value=datetime.fromisoformat('2026-11-21T22:00:00+05:00')),
            ):
                scheduler._recover_daily_batches(deadline=0)
            run_batch.assert_not_called()
            webhook.assert_not_called()

            failed = post('/api/internal/run-notifications', '2026-11-21T15:21:00Z')
            self.assertEqual(failed.status_code, 503)
            post('/api/internal/run-notifications', '2026-11-21T16:09:00Z')
            self.assertEqual(webhook.call_count, 1)
            self.assertIn('10:00 PM PKT', webhook.call_args.kwargs['description'])
            post('/api/internal/urgent-check', '2026-11-21T17:00:00Z')
            self.assertEqual(webhook.call_count, 1)
            post('/api/internal/urgent-check', '2026-11-21T18:00:00Z')
            self.assertEqual(run_batch.call_count, 4)
            self.assertEqual(webhook.call_count, 2)
            fields = {field['name']: field['value'] for field in webhook.call_args.kwargs['fields']}
            self.assertEqual(fields['Completed Batches'], '59 / 60')
            self.assertEqual(fields['Missing Batches'], '21')

            # The original final batch can finish after the window closes.
            webhook.reset_mock()
            run_batch.side_effect = process_batch
            documents['daily_discord_stats_afternoon'] = {
                'date': '2026-11-22',
                'stats_by_bucket': {str(i): completed_batch for i in range(59)},
            }
            response = post('/api/internal/run-notifications', '2026-11-22T09:59:00Z',
                            actual_time='2026-11-22T10:01:00Z')
            self.assertEqual(response.status_code, 200)
            webhook.assert_called_once()
            self.assertTrue(documents['daily_discord_stats_afternoon']['summary_sent'])

    def test_get_users_by_batch_casts_string_to_int(self):
        """String batch numbers (e.g. '20') must be cast to integer 20 for Firestore queries."""
        mock_fs = MagicMock()
        mock_db = MagicMock()
        mock_fs.db = mock_db
        mock_collection = MagicMock()
        mock_db.collection.return_value = mock_collection
        mock_collection.where.return_value.stream.return_value = iter([])

        with patch.object(User, '_get_firestore', return_value=mock_fs):
            User.get_users_by_batch("25")
            # Must query Firestore with integer 25, not string '25'
            mock_collection.where.assert_called_with('batch_bucket', '==', 25)
            mock_collection.where.return_value.stream.side_effect = RuntimeError('Firestore unavailable')
            with self.assertRaisesRegex(RuntimeError, 'Firestore unavailable'):
                User.get_users_by_batch(25)

    def test_scheduler_accumulates_usage_from_multiple_runs(self):
        from copy import deepcopy
        from app.services.notification_scheduler import FREE_TIER_HARD_STOP_SECONDS

        scheduler = NotificationScheduler()
        fs = MagicMock()
        doc_ref = fs.db.collection.return_value.document.return_value
        transaction = fs.db.transaction.return_value
        stored = {}
        doc_ref.get.side_effect = lambda **kwargs: MagicMock(
            exists=bool(stored), to_dict=lambda: deepcopy(stored),
        )
        transaction.set.side_effect = lambda ref, data: stored.update(deepcopy(data))
        fs.get_document.side_effect = lambda *args, **kwargs: deepcopy(stored)

        with patch.object(scheduler, '_get_firestore', return_value=fs), \
             patch('google.cloud.firestore.transactional', side_effect=lambda fn: fn):
            scheduler._log_free_tier_usage(FREE_TIER_HARD_STOP_SECONDS - 1)
            self.assertFalse(scheduler.get_kill_switch_status()['active'])
            scheduler._log_free_tier_usage(2)
            self.assertEqual(scheduler.get_kill_switch_status(), {
                'active': True, 'usage_seconds': FREE_TIER_HARD_STOP_SECONDS + 1,
            })
        fs.get_document.assert_called_with('system_config', 'usage_stats', bypass_cache=True)
        self.assertEqual(next(iter(stored.values()))['batch_runs'], 2)

    def test_run_notifications_empty_batch_skips_usage_log(self):
        """When batch has 0 users, it returns immediately without writing to usage_stats."""
        scheduler = NotificationScheduler(app=None)
        scheduler.get_kill_switch_status = MagicMock(return_value={'active': False, 'usage_seconds': 0})
        scheduler._log_free_tier_usage = MagicMock()
        scheduler._aggregate_and_fire_daily_summary = MagicMock()

        with patch.object(User, 'get_users_by_batch', return_value=[]):
            res = scheduler._run_notifications(bucket=10)
            self.assertEqual(res['users_processed'], 0)
            # Must aggregate stats
            scheduler._aggregate_and_fire_daily_summary.assert_called_once_with(10, res)
            # Must NOT write usage log when 0 users processed
            scheduler._log_free_tier_usage.assert_not_called()


if __name__ == '__main__':
    unittest.main()
