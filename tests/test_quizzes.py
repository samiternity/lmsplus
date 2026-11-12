"""Quiz parsing, cache isolation, and authenticated route behavior without upstream calls."""
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from bs4 import BeautifulSoup
from flask import Flask
from flask_login import LoginManager
from app.models.lms_models import LMSQuiz
from app.services.lms_scraper_service import LMSScraperService, LMSParsingError, LMSScrapingError, CMSSurveyRequiredError
from app.services.lms_service import LMSService
from app.routes import lms

HEADERS = '<tr><th>Quiz No.</th><th>Quiz Title</th><th>Quiz</th><th>Quiz Marks</th><th>Quiz Solution</th></tr>'
ROW = '<tr><td>2</td><td>Quiz &lt;two&gt;</td><td><a href="Download.php?f=quiz%3D">Download Quiz File</a></td><td>0</td><td><a href="/Student/Download.php?f=solution">Solution</a></td></tr>'


def soup(rows='', headers=HEADERS):
    return BeautifulSoup('<table>' + headers + rows + '</table>', 'html.parser')


class QuizParserTests(unittest.TestCase):
    def setUp(self):
        self.scraper = LMSScraperService.__new__(LMSScraperService)
        self.scraper.lms_base_url = 'https://lms.bahria.edu.pk'
        self.scraper.quizzes_url = self.scraper.lms_base_url + '/Student/Quizzes.php'

    def test_rows_marks_links_context_and_serialization(self):
        rows = self.scraper.parse_quiz_data(soup(ROW + '<tr><td>1</td><td>Quiz 1</td><td></td><td></td><td></td></tr>'),
                                          {'course_code': 'CS', 'course_name': 'Algorithms'}, 'Fall')
        self.assertEqual([r.quiz_no for r in rows], [1, 2])
        self.assertIsNone(rows[0].marks)
        self.assertIsNone(rows[0].solution_file)
        self.assertEqual(rows[1].marks, '0')
        self.assertEqual(rows[1].title, 'Quiz <two>')
        self.assertEqual(rows[1].quiz_file, 'https://lms.bahria.edu.pk/Student/Download.php?f=quiz%3D')
        self.assertEqual(rows[1].course_name, 'Algorithms')
        self.assertEqual(LMSQuiz.from_dict(rows[1].to_dict()).to_dict(), rows[1].to_dict())

    def test_reordered_columns(self):
        headers = '<tr><td>Quiz Marks</td><td>Quiz Solution</td><td>Quiz Title</td><td>Quiz No.</td><td>Quiz</td></tr>'
        rows = self.scraper.parse_quiz_data(soup('<tr><td>5/10</td><td></td><td>Quiz</td><td>3</td><td></td></tr>', headers))
        self.assertEqual((rows[0].quiz_no, rows[0].marks), (3, '5/10'))

    def test_valid_empty_tables(self):
        for html in ('', '<tr><td colspan="5">No quizzes found</td></tr>'):
            self.assertEqual(self.scraper.parse_quiz_data(soup(html)), [])

    def test_malformed_or_login_html_is_not_empty(self):
        for html in ('<form>Login</form>', str(soup('<tr><td>1</td></tr>')),
                     str(soup('<tr><td colspan="5">Server error</td></tr>'))):
            with self.assertRaises(LMSParsingError):
                self.scraper.parse_quiz_data(BeautifulSoup(html, 'html.parser'))

    def test_invalid_links_fail(self):
        for href in ('javascript:alert(1)', 'https://other.example/quiz'):
            with self.assertRaises(LMSParsingError):
                self.scraper.parse_quiz_data(soup(ROW.replace('Download.php?f=quiz%3D', href)))

    def test_options_and_encoded_codes(self):
        html = BeautifulSoup('<select id="semesterId"><option value="MjAyNjM%3D">Fall-2026</option></select>'
                             '<select id="courseId"><option value="">Select Course</option>'
                             '<option value="MTUxNzU0%3D">Algorithms</option></select>', 'html.parser')
        self.scraper._get_quizzes_soup = Mock(return_value=html)
        options = self.scraper.get_quizzes_options('account')
        self.assertEqual(options['semesters'][0]['code'], 'MjAyNjM=')
        self.assertEqual(options['courses'][0]['code'], 'MTUxNzU0=')
        self.scraper.get_quizzes_courses_for_semester('account', 'MjAyNjM=')
        self.scraper._get_quizzes_soup.assert_called_with('account', {'s': 'MjAyNjM='})
        self.scraper._get_quizzes_soup.return_value = soup(ROW)
        self.scraper.scrape_course_quizzes('account', 'MjAyNjM=', 'MTUxNzU0=')
        self.scraper._get_quizzes_soup.assert_called_with('account', {'s': 'MjAyNjM=', 'oc': 'MTUxNzU0='})

    def test_authentication_and_network_errors_propagate(self):
        self.scraper._get_or_create_session = Mock(return_value=object())
        response = Mock(text='<form>Login</form>')
        self.scraper._make_request = Mock(return_value=response)
        self.scraper._requires_cms_authentication = Mock(return_value=True)
        with self.assertRaises(LMSScrapingError):
            self.scraper._get_quizzes_soup('account', {})
        self.scraper._make_request.side_effect = CMSSurveyRequiredError()
        with self.assertRaises(CMSSurveyRequiredError):
            self.scraper.get_quizzes_options('account')


class QuizCacheTests(unittest.TestCase):
    def setUp(self):
        self.service = LMSService.__new__(LMSService)
        self.service._memory_cache = {}
        self.service._save_persistent_cache = Mock()
        self.service._load_persistent_cache = Mock(return_value=None)
        self.service._ensure_valid_session = Mock()
        self.service.scraper_service = Mock()

    def test_isolation_and_valid_empty_cache(self):
        service = self.service
        service._cache_quizzes('a', [], 's', 'c')
        self.assertEqual(service._get_cached_quizzes('a', 's', 'c'), [])
        for args in [('b', 's', 'c'), ('a', 'other', 'c'), ('a', 's', 'other')]:
            self.assertIsNone(service._get_cached_quizzes(*args))
        self.assertNotIn(service._get_cache_key('a', 'lecture_notes', 's', 'c'), service._memory_cache)
        service._save_persistent_cache.assert_called_once()

    def test_stale_then_forced_refresh_replaces_rows_with_empty(self):
        service = self.service
        service._cache_quizzes('a', [LMSQuiz(1, 'Old')], 's', 'c')
        entry = service._memory_cache[service._get_cache_key('a', 'quizzes', 's', 'c')]
        entry.expires_at = datetime.now() - timedelta(seconds=1)
        self.assertIsNone(service._get_cached_quizzes('a', 's', 'c'))
        self.assertEqual(service._get_cached_quizzes('a', 's', 'c', allow_stale=True)[0].title, 'Old')
        service.scraper_service.scrape_course_quizzes.return_value = []
        self.assertEqual(service.get_quizzes_for_course('a', 's', 'c', force_refresh=True), [])
        self.assertEqual(service._get_cached_quizzes('a', 's', 'c'), [])

    def test_failed_scrape_does_not_overwrite_cache(self):
        service = self.service
        service._cache_quizzes('a', [LMSQuiz(1, 'Saved')], 's', 'c')
        service.scraper_service.scrape_course_quizzes.side_effect = LMSParsingError('bad page')
        with self.assertRaises(LMSParsingError):
            service.get_quizzes_for_course('a', 's', 'c', force_refresh=True)
        self.assertEqual(service._get_cached_quizzes('a', 's', 'c')[0].title, 'Saved')


class QuizRouteTests(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.config.update(SECRET_KEY='test', TESTING=True, RATELIMIT_ENABLED=False)
        self.user = Mock(is_authenticated=True, is_active=True, is_anonymous=False, id='test')
        self.user.last_active_at = datetime.now(timezone.utc).isoformat()
        self.user.get_id.return_value = 'test'
        self.user.get_active_account.return_value = SimpleNamespace(account_id='a', cms_username='student', cms_password='password')
        manager = LoginManager(self.app)
        manager.user_loader(lambda user_id: self.user)
        self.app.register_blueprint(lms.lms_bp)
        self.client = self.app.test_client()
        with self.client.session_transaction() as session:
            session['_user_id'] = 'test'
            session['authenticated_accounts'] = ['a']
        self.current_user = patch.object(lms, 'current_user', self.user)
        self.current_user.start()
        self.addCleanup(self.current_user.stop)
        self.service = Mock()
        self.service._get_cached_quizzes.return_value = None
        self.service.get_quizzes_for_course.return_value = [LMSQuiz(2, 'Quiz 2', marks=0), LMSQuiz(1, 'Quiz 1')]
        service_patch = patch.object(lms, 'lms_service', self.service)
        service_patch.start()
        self.addCleanup(service_patch.stop)
        lms._refresh_tasks.clear()

    def test_authentication_required_for_all_quiz_endpoints(self):
        with self.client.session_transaction() as session:
            session.pop('authenticated_accounts', None)
        for path in ('/lms/quizzes', '/lms/api/quizzes/options', '/lms/api/quizzes/courses?semester=s',
                     '/lms/api/quizzes/data?semester=s&course=c', '/lms/api/quizzes/fetch?semester=s&course=c'):
            self.assertEqual(self.client.get(path).status_code, 403, path)

    def test_missing_parameters(self):
        for path in ('/lms/api/quizzes/courses', '/lms/api/quizzes/data?semester=s', '/lms/api/quizzes/fetch?course=c'):
            self.assertEqual(self.client.get(path).status_code, 400)

    def test_options_courses_and_sorted_rows(self):
        self.service.get_quizzes_options.return_value = {'semesters': [{'code': 's', 'name': 'Fall'}], 'courses': []}
        self.service.get_quizzes_courses_for_semester.return_value = [{'code': 'c', 'name': 'Algorithms'}]
        self.assertTrue(self.client.get('/lms/api/quizzes/options').json['success'])
        self.assertEqual(self.client.get('/lms/api/quizzes/courses?semester=s').json['courses'][0]['code'], 'c')
        data = self.client.get('/lms/api/quizzes/data?semester=s&course=c').json
        self.assertEqual([q['quiz_no'] for q in data['quizzes']], [1, 2])
        self.assertEqual(data['quizzes'][1]['marks'], 0)
        self.assertFalse(data['is_stale'])

    def test_forced_refresh_bypasses_cache(self):
        self.client.get('/lms/api/quizzes/fetch?semester=s&course=c&refresh=true')
        self.service._get_cached_quizzes.assert_not_called()
        self.service.get_quizzes_for_course.assert_called_once_with('a', 's', 'c', force_refresh=True)

    def test_survey_and_upstream_failure_are_errors(self):
        self.service.get_quizzes_options.side_effect = CMSSurveyRequiredError()
        self.assertTrue(self.client.get('/lms/api/quizzes/options').json['survey_required'])
        self.service.get_quizzes_options.side_effect = LMSParsingError('bad page')
        response = self.client.get('/lms/api/quizzes/options')
        self.assertEqual(response.status_code, 502)
        self.assertFalse(response.json['success'])

    def test_stale_cache_refresh_and_task_ownership(self):
        self.service._get_cached_quizzes.side_effect = [None, [LMSQuiz(1, 'Saved')]]
        with patch.object(lms.threading, 'Thread') as thread:
            thread.return_value.start.side_effect = lambda: thread.call_args.kwargs['target']()
            data = self.client.get('/lms/api/quizzes/data?semester=s&course=c').json
        self.assertTrue(data['is_stale'])
        task_id = data['task_id']
        lms._refresh_tasks[task_id]['account_id'] = 'other-account'
        self.assertEqual(self.client.get('/lms/api/refresh-status?task_id=' + task_id).status_code, 404)
        lms._refresh_tasks[task_id]['account_id'] = 'a'
        result = self.client.get('/lms/api/refresh-status?task_id=' + task_id).json
        self.assertTrue(result['done'])
        self.assertEqual(len(result['data']), 2)
        self.assertNotIn('account_id', result)

    def test_sidebar_and_no_bulk_download_routes(self):
        base = Path('app/templates/base.html').read_text()
        self.assertLess(base.index('>Lecture notes</span>'), base.index('>Quizzes</span>'))
        source = Path('app/templates/lms_quizzes.html').read_text() + Path('app/static/js/lms_quizzes.js').read_text()
        self.assertNotIn('downloadAll', source)
        self.assertNotIn('download-zip', source)
        self.assertFalse(any('quizzes' in r.rule and 'zip' in r.rule for r in self.app.url_map.iter_rules()))


if __name__ == '__main__':
    unittest.main()
