"""Deadline regressions runnable without the app's external service dependencies."""
import ast
import importlib.util
from datetime import datetime, timedelta, timezone
from pathlib import Path
import unittest
from unittest.mock import patch
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    'categorization', ROOT / 'app/utils/assignment_categorization.py')
utils = importlib.util.module_from_spec(spec)
spec.loader.exec_module(utils)


class SubmissionCutoffTests(unittest.TestCase):
    def test_listed_times_close_at_following_midnight(self):
        for value in ('1 November 2026-11:00 am', '1 November 2026-12:00 am',
                      '1 November 2026-11:59 pm'):
            self.assertEqual(utils.submission_cutoff(value), datetime(2026, 11, 2))
        self.assertEqual(utils.submission_cutoff('31 December 2026-11:00 am'),
                         datetime(2027, 1, 1))
        self.assertIsNone(utils.submission_cutoff(None))
        self.assertIsNone(utils.submission_cutoff('unknown'))

    def test_timezone_date_is_pakistan_date(self):
        self.assertEqual(utils.submission_cutoff(
            datetime(2026, 11, 1, 22, tzinfo=timezone.utc)), datetime(2026, 11, 3))

    def test_monitor_stays_pending_after_listed_time_and_preserves_original(self):
        assignment = dict(deadline='1 November 2026-11:00 am',
                          submission_status='pending', can_submit=True, is_past_due=True)
        with patch.object(utils, 'calculate_hours_until_deadline', return_value=10):
            result = utils.enrich_assignment_with_metadata(assignment.copy())
        self.assertEqual(result['category'], 'pending')
        self.assertEqual(result['deadline'], assignment['deadline'])
        self.assertEqual(result['submission_deadline'], '01 November 2026-11:59:59 pm PKT')
        self.assertEqual(result['deadline_datetime'], '2026-11-02T00:00:00+05:00')
        with patch.object(utils, 'calculate_hours_until_deadline', return_value=0):
            self.assertEqual(utils.categorize_assignment(assignment), ('missed', 'overdue'))
        assignment['submission_file'] = 'uploaded.pdf'
        with patch.object(utils, 'calculate_hours_until_deadline', return_value=-1):
            self.assertEqual(utils.categorize_assignment(assignment)[0], 'submitted')

    def test_reminders_use_same_cutoff_for_strings_and_datetimes(self):
        # Execute the actual service method without starting external services.
        tree = ast.parse((ROOT / 'app/services/assignment_notification_service.py').read_text())
        cls = next(n for n in tree.body if isinstance(n, ast.ClassDef))
        method = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == '_parse_deadline')
        method.returns = None
        namespace = {'submission_cutoff': utils.submission_cutoff}
        exec(compile(ast.Module(body=[method], type_ignores=[]), '<service>', 'exec'), namespace)
        for deadline in ('1 November 2026-11:00 am', datetime(2026, 11, 1, 11)):
            self.assertEqual(namespace['_parse_deadline'](None, SimpleNamespace(deadline=deadline)),
                             datetime(2026, 11, 2))
        cutoff = utils.submission_cutoff('1 November 2026-11:00 am')
        self.assertEqual(utils.calculate_hours_until_deadline(cutoff, datetime(2026, 11, 1, 12)), 12)
        self.assertEqual(utils.calculate_hours_until_deadline(cutoff, datetime(2026, 11, 2)), 0)


if __name__ == '__main__':
    unittest.main()
