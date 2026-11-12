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

    def test_daily_summary_slot_separation(self):
        """Afternoon (14:00) and Evening (20:00) use separate Firestore documents and titles."""
        mock_fs = MagicMock()
        mock_db = MagicMock()
        mock_fs.db = mock_db
        mock_doc_ref = MagicMock()
        mock_db.collection.return_value.document.return_value = mock_doc_ref

        # Mock transaction
        mock_transaction = MagicMock()
        mock_db.transaction.return_value = mock_transaction

        tz_pk = pytz.timezone('Asia/Karachi')
        fake_afternoon = datetime(2026, 10, 17, 14, 59, 0, tzinfo=tz_pk)
        fake_evening = datetime(2026, 10, 17, 20, 59, 0, tzinfo=tz_pk)

        scheduler = NotificationScheduler(app=None)
        scheduler._get_firestore = MagicMock(return_value=mock_fs)

        with patch('app.services.notification_scheduler.get_current_pakistan_time', return_value=fake_afternoon), \
             patch.object(scheduler, '_fire_daily_summary_webhook') as mock_fire:
            
            # Simulate transactional callback executing
            def mock_transactional_decorator(fn):
                def wrapper(t, ref):
                    # Simulate doc.get inside transaction
                    mock_snap = MagicMock()
                    mock_snap.exists = True
                    mock_snap.to_dict.return_value = {'date': '2026-10-17', 'summary_sent': False}
                    t.get.return_value = mock_snap
                    return fn(t, ref)
                return wrapper

            with patch('google.cloud.firestore.transactional', side_effect=mock_transactional_decorator):
                scheduler._aggregate_and_fire_daily_summary(59, {'users_processed': 10})
                mock_db.collection.return_value.document.assert_called_with('daily_discord_stats_afternoon')
                mock_fire.assert_called_once()
                self.assertIn("Afternoon", mock_fire.call_args[1].get('slot_title', ''))

        with patch('app.services.notification_scheduler.get_current_pakistan_time', return_value=fake_evening), \
             patch.object(scheduler, '_fire_daily_summary_webhook') as mock_fire:

            with patch('google.cloud.firestore.transactional', side_effect=mock_transactional_decorator):
                scheduler._aggregate_and_fire_daily_summary(59, {'users_processed': 10})
                mock_db.collection.return_value.document.assert_called_with('daily_discord_stats_evening')
                mock_fire.assert_called_once()
                self.assertIn("Evening", mock_fire.call_args[1].get('slot_title', ''))

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
