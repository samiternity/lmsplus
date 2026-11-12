"""Tests for first-run sync onboarding flow, TTL expiry, and non-blocking status tracking."""

import unittest
from unittest.mock import patch, MagicMock
from datetime import datetime, timedelta
from app import create_app
from app.models.account import User, CMSAccount
from app.utils.enhanced_scraper import MultiAccountScraperManager, scraper_manager
from app.utils.data_manager import data_manager


class TestSyncStatusManager(unittest.TestCase):
    """Test MultiAccountScraperManager thread-safe in-memory status and TTL."""

    def test_default_status_idle(self):
        mgr = MultiAccountScraperManager()
        status = mgr.get_sync_status('non-existent-id')
        self.assertFalse(status['is_syncing'])
        self.assertEqual(status['progress'], 0)
        self.assertEqual(status['status_message'], 'Idle')

    def test_active_sync_updates(self):
        mgr = MultiAccountScraperManager()
        account_id = 'test-acc-123'
        
        now = datetime.now()
        mgr._update_sync_status(
            account_id,
            is_syncing=True,
            progress=30,
            current_step='profile',
            completed_steps=['auth'],
            failed_steps=[],
            status_message='Extracting profile...',
            sub_message='Loading program info',
            expires_at=(now + timedelta(seconds=60)).isoformat()
        )

        status = mgr.get_sync_status(account_id)
        self.assertTrue(status['is_syncing'])
        self.assertEqual(status['progress'], 30)
        self.assertEqual(status['current_step'], 'profile')
        self.assertIn('auth', status['completed_steps'])
        self.assertEqual(status['status_message'], 'Extracting profile...')

    def test_zombie_sync_ttl_expiry(self):
        """Verify that a hung sync past expires_at is automatically timed out."""
        mgr = MultiAccountScraperManager()
        account_id = 'test-zombie-acc'
        
        past_time = datetime.now() - timedelta(seconds=10)
        mgr._update_sync_status(
            account_id,
            is_syncing=True,
            progress=40,
            current_step='attendance',
            expires_at=past_time.isoformat()
        )

        status = mgr.get_sync_status(account_id)
        self.assertFalse(status['is_syncing'])
        self.assertIn('timed out', status['status_message'].lower())


class TestSyncRoutes(unittest.TestCase):
    """Test /sync and /api/sync/status HTTP endpoints."""

    def setUp(self):
        self.app = create_app()
        self.app.config['TESTING'] = True
        self.app.config['SECRET_KEY'] = 'test-secret-key'
        self.app.config['WTF_CSRF_ENABLED'] = False
        self.client = self.app.test_client()

    def tearDown(self):
        from app.models.account import account_manager
        test_uids = [
            'testuser_sync_done',
            'testuser_sync_active',
            'testuser_force_refresh',
            'testuser_new_dash',
            'testuser_auto_start'
        ]
        for uid in test_uids:
            try:
                account_manager.delete_user_completely(uid)
            except Exception:
                pass

    def test_sync_screen_unauthenticated(self):
        """Unauthenticated visit should redirect to login."""
        response = self.client.get('/sync')
        self.assertEqual(response.status_code, 302)
        self.assertIn('/auth/login', response.headers.get('Location', ''))

    def test_api_sync_status_unauthenticated(self):
        """Unauthenticated call to API should return 401 or redirect to login."""
        response = self.client.get('/api/sync/status')
        self.assertIn(response.status_code, [302, 401])

    def test_sync_screen_tab_abandonment_redirect(self):
        """If user already has valid profile and is not syncing, /sync redirects to /dashboard."""
        with self.app.app_context():
            user = User(id='testuser_sync_done', username='testuser_sync_done', email='test@example.com')
            user.set_cms_account('01-134212-999', 'password123')
            user.save()

            # Save fake profile
            data_manager.save_account_data(
                user.cms_account.account_id,
                'profile',
                {'name': 'Test Student', 'program': 'BSCS'}
            )
            # Ensure not syncing
            scraper_manager._update_sync_status(
                user.cms_account.account_id,
                is_syncing=False,
                progress=100
            )

            with self.client.session_transaction() as sess:
                sess['_user_id'] = user.id
                sess['account_id'] = user.cms_account.account_id

            response = self.client.get('/sync')
            self.assertEqual(response.status_code, 302)
            self.assertIn('/dashboard', response.headers.get('Location', ''))

    def test_api_sync_status_authenticated(self):
        """Authenticated call to /api/sync/status returns status JSON."""
        with self.app.app_context():
            user = User(id='testuser_sync_active', username='testuser_sync_active', email='test2@example.com')
            user.set_cms_account('01-134212-888', 'password123')
            user.save()

            scraper_manager._update_sync_status(
                user.cms_account.account_id,
                is_syncing=True,
                progress=55,
                current_step='timetable',
                status_message='Building timetable...',
                expires_at=(datetime.now() + timedelta(seconds=60)).isoformat()
            )

            with self.client.session_transaction() as sess:
                sess['_user_id'] = user.id
                sess['account_id'] = user.cms_account.account_id

            response = self.client.get('/api/sync/status')
            self.assertEqual(response.status_code, 200)
            data = response.get_json()
            self.assertTrue(data['is_syncing'])
            self.assertEqual(data['progress'], 55)
            self.assertEqual(data['current_step'], 'timetable')

    def test_refresh_all_data_json_trigger(self):
        """Authenticated GET /refresh-all-data?format=json should trigger async sync and return JSON."""
        with self.app.app_context():
            user = User(id='testuser_force_refresh', username='testuser_force_refresh', email='test3@example.com')
            user.set_cms_account('01-000000-001', 'password123')
            user.save()

            # Ensure idle
            scraper_manager._update_sync_status(
                user.cms_account.account_id,
                is_syncing=False,
                progress=100
            )

            with self.client.session_transaction() as sess:
                sess['_user_id'] = user.id
                sess['account_id'] = user.cms_account.account_id

            response = self.client.get('/refresh-all-data?format=json')
            self.assertEqual(response.status_code, 200)
            data = response.get_json()
            self.assertTrue(data['success'])
            self.assertTrue(data.get('is_syncing') or data.get('already_syncing'))

    def test_dashboard_redirects_new_user_to_sync(self):
        """User with no profile and never synced should be redirected to /sync."""
        with self.app.app_context():
            user = User(id='testuser_new_dash', username='testuser_new_dash', email='testnew@example.com')
            user.set_cms_account('01-134212-666', 'password123')
            user.save()

            # Ensure idle
            scraper_manager._update_sync_status(
                user.cms_account.account_id,
                is_syncing=False,
                progress=0,
                status_message='Idle',
                completed_steps=[],
                failed_steps=[]
            )

            with self.client.session_transaction() as sess:
                sess['_user_id'] = user.id
                sess['account_id'] = user.cms_account.account_id

            response = self.client.get('/dashboard')
            self.assertEqual(response.status_code, 302)
            self.assertIn('/sync', response.headers.get('Location', ''))

    @patch('app.utils.enhanced_scraper.MultiAccountScraperManager.scrape_account_data')
    def test_sync_screen_auto_triggers_idle_sync(self, mock_scrape):
        """User landing on /sync when sync is idle triggers initial scrape."""
        with self.app.app_context():
            user = User(id='testuser_auto_start', username='testuser_auto_start', email='teststart@example.com')
            user.set_cms_account('01-134212-555', 'password123')
            user.save()

            # Ensure idle
            scraper_manager._update_sync_status(
                user.cms_account.account_id,
                is_syncing=False,
                progress=0,
                status_message='Idle',
                completed_steps=[],
                failed_steps=[]
            )

            with self.client.session_transaction() as sess:
                sess['_user_id'] = user.id
                sess['account_id'] = user.cms_account.account_id

            response = self.client.get('/sync')
            self.assertEqual(response.status_code, 200)
            mock_scrape.assert_called_once()


from unittest.mock import patch, MagicMock

class TestAtomicFullSync(unittest.TestCase):
    """Test atomic edge macro execution, quota preservation, and failover behavior."""

    @patch('requests.Session.post')
    def test_cf_success(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            'success': True,
            'html': {'profile': '<html><body>Profile</body></html>'},
            'cookies': 'ASP.NET_SessionId=test1234'
        }
        mock_post.return_value = mock_resp

        account = CMSAccount(account_id='test-cf-acc', cms_username='01-111111-111', cms_password='secretpassword')
        from app.utils.enhanced_scraper import EnhancedCMSScraper
        scraper = EnhancedCMSScraper(account)

        res = scraper.scrape_full_sync_atomic(account.cms_username, account.cms_password)
        self.assertIsNotNone(res)
        self.assertTrue(res['success'])
        # Only 1 call made (Cloudflare Worker succeeded, Supabase was spared)
        self.assertEqual(mock_post.call_count, 1)
        self.assertIn('workers.dev', mock_post.call_args_list[0][0][0])

    @patch('requests.Session.post')
    def test_cf_fail_supabase_rescue(self, mock_post):
        cf_fail_resp = MagicMock()
        cf_fail_resp.status_code = 524
        cf_fail_resp.text = "Gateway Timeout"

        sb_succ_resp = MagicMock()
        sb_succ_resp.status_code = 200
        sb_succ_resp.json.return_value = {
            'success': True,
            'html': {'profile': '<html><body>Rescued Profile</body></html>'},
            'cookies': 'ASP.NET_SessionId=sb-rescued'
        }
        mock_post.side_effect = [cf_fail_resp, sb_succ_resp]

        account = CMSAccount(account_id='test-sb-acc', cms_username='01-222222-222', cms_password='secretpassword')
        from app.utils.enhanced_scraper import EnhancedCMSScraper
        scraper = EnhancedCMSScraper(account)

        res = scraper.scrape_full_sync_atomic(account.cms_username, account.cms_password)
        self.assertIsNotNone(res)
        self.assertTrue(res['success'])
        # 2 calls made (CF failed, then Supabase rescued)
        self.assertEqual(mock_post.call_count, 2)
        self.assertIn('workers.dev', mock_post.call_args_list[0][0][0])
        self.assertIn('supabase.co', mock_post.call_args_list[1][0][0])

    @patch('requests.Session.post')
    def test_invalid_credentials_prevents_supabase_burn(self, mock_post):
        bad_cred_resp = MagicMock()
        bad_cred_resp.status_code = 401
        bad_cred_resp.json.return_value = {
            'success': False,
            'error_code': 'INVALID_CREDENTIALS',
            'error': 'CMS login failed'
        }
        mock_post.return_value = bad_cred_resp

        account = CMSAccount(account_id='test-bad-cred', cms_username='01-333333-333', cms_password='wrongpassword')
        from app.utils.enhanced_scraper import EnhancedCMSScraper
        scraper = EnhancedCMSScraper(account)

        res = scraper.scrape_full_sync_atomic(account.cms_username, account.cms_password)
        self.assertIsNotNone(res)
        self.assertTrue(res.get('auth_failed'))
        # Only 1 call made (CF returned INVALID_CREDENTIALS, Supabase quota was protected)
        self.assertEqual(mock_post.call_count, 1)

    @patch('app.utils.enhanced_scraper.EnhancedCMSScraper.scrape_full_sync_atomic')
    def test_scrape_account_data_atomic_success(self, mock_atomic):
        mock_atomic.return_value = {
            'success': True,
            'html': {
                'profile': '<table><tr><td>Name:</td><td>Test Student</td></tr></table>',
                'timetable': '<html><body>Timetable</body></html>',
                'attendance': '<html><body>Attendance</body></html>',
                'community_service': '<html><body>CSP</body></html>',
                'gpa': '<html><body>GPA</body></html>'
            },
            'cookies': 'ASP.NET_SessionId=valid123'
        }

        account = CMSAccount(account_id='test-atomic-acc', cms_username='01-000000-002', cms_password='validpass', student_name='Test Student')
        from app.utils.enhanced_scraper import scraper_manager
        from app.utils.data_manager import data_manager

        thread = scraper_manager.scrape_account_data(account)
        thread.join(timeout=25)

        sync_status = scraper_manager.get_sync_status(account.account_id)
        self.assertFalse(sync_status.get('is_syncing'))
        self.assertEqual(sync_status.get('current_step'), 'completed')
        self.assertEqual(sync_status.get('progress'), 100)
        self.assertIn('auth', sync_status.get('completed_steps', []))
        self.assertNotIn('auth', sync_status.get('failed_steps', []))

        # Crucial check: verify fallback profile with sync_failed was NOT saved
        profile = data_manager.load_account_data(account.account_id, 'profile')
        self.assertIsNotNone(profile)
        self.assertFalse(profile.get('sync_failed', False))

    def test_active_thread_deduplication(self):
        """Verify calling scrape_account_data concurrently returns existing running thread."""
        account = CMSAccount(account_id='test-dedup-acc', cms_username='01-555555-555', cms_password='validpass')
        from app.utils.enhanced_scraper import scraper_manager
        import time

        with patch.object(scraper_manager, 'ingest_atomic_bundle', side_effect=lambda *args, **kwargs: (time.sleep(0.5), True)[1]):
            with patch('app.utils.enhanced_scraper.EnhancedCMSScraper.scrape_full_sync_atomic') as mock_atomic:
                mock_atomic.return_value = {'success': True, 'html': {'profile': '<html></html>'}}
                t1 = scraper_manager.scrape_account_data(account)
                t2 = scraper_manager.scrape_account_data(account)

                self.assertIs(t1, t2)
                t1.join(timeout=5)

    def test_ingest_atomic_bundle_direct(self):
        """Verify ingest_atomic_bundle parses all domains, updates session, and sets 100% sync status."""
        account = CMSAccount(account_id='test-ingest-acc', cms_username='01-666666-666', cms_password='validpass', student_name='Ingest Student')
        from app.utils.enhanced_scraper import scraper_manager
        from app.utils.data_manager import data_manager

        bundle = {
            'success': True,
            'html': {
                'profile': '<table><tr><td>Name:</td><td>Ingest Student</td></tr></table>',
                'timetable': '<html><body>Timetable</body></html>',
                'attendance': '<html><body>Attendance</body></html>',
                'community_service': '<html><body>CSP</body></html>',
                'gpa': '<html><body>GPA</body></html>'
            },
            'cookies': 'ASP.NET_SessionId=ingest_cookie_123'
        }

        success = scraper_manager.ingest_atomic_bundle(account, bundle)
        self.assertTrue(success)

        status = scraper_manager.get_sync_status(account.account_id)
        self.assertFalse(status.get('is_syncing'))
        self.assertEqual(status.get('progress'), 100)
        self.assertEqual(status.get('current_step'), 'completed')
        self.assertEqual(status.get('status_message'), 'All systems ready!')

        scraper = scraper_manager.get_scraper(account)
        self.assertEqual(scraper.session.cookies.get('ASP.NET_SessionId'), 'ingest_cookie_123')

    @patch('app.routes.auth._fire_signup_webhook')
    @patch('app.routes.auth.verify_turnstile', return_value=True)
    @patch('app.utils.enhanced_scraper.EnhancedCMSScraper.scrape_full_sync_atomic')
    def test_single_flight_onboarding_login(self, mock_atomic, mock_turnstile, mock_webhook):
        """Verify signup executes single-flight atomic macro, ingests bundle, and redirects directly."""
        mock_atomic.return_value = {
            'success': True,
            'html': {
                'profile': '<table><tr><td>Name:</td><td>Single Flight Student</td></tr></table>',
                'timetable': '<html><body>Timetable</body></html>',
                'attendance': '<html><body>Attendance</body></html>',
                'community_service': '<html><body>CSP</body></html>',
                'gpa': '<html><body>GPA</body></html>'
            },
            'cookies': 'ASP.NET_SessionId=singleflight123'
        }

        app = create_app()
        app.config['TESTING'] = True
        app.config['SECRET_KEY'] = 'test-secret-key'
        app.config['WTF_CSRF_ENABLED'] = False

        client = app.test_client()
        with app.app_context():
            from app.models.account import account_manager
            account_manager.delete_user_completely('02-777777-777')
            user = None
            try:
                res = client.post('/auth/login', data={
                    'username': '02-777777-777',
                    'password': 'StrongPassword123!'
                })

                self.assertEqual(res.status_code, 200)
                data = res.get_json()
                self.assertTrue(data.get('success'))
                self.assertEqual(data.get('redirect_url'), '/sync')

                user = User.get('02-777777-777')
                self.assertIsNotNone(user)
                self.assertIsNotNone(user.cms_account)
                self.assertEqual(user.cms_account.student_name, 'Single Flight Student')
                self.assertEqual(user.cms_account.institute_id, '2')
                self.assertTrue(mock_webhook.called)

                # Verify initial sync status is live and ready for /sync stepper
                from app.utils.enhanced_scraper import scraper_manager
                initial_status = scraper_manager.get_sync_status(user.account_id)
                self.assertTrue(initial_status.get('is_syncing') or initial_status.get('progress') == 100)

                # Wait for staged ingestion thread to finish
                active_thread = scraper_manager._active_threads.get(user.account_id)
                if active_thread:
                    active_thread.join(timeout=25)

                final_status = scraper_manager.get_sync_status(user.account_id)
                self.assertFalse(final_status.get('is_syncing'))
                self.assertEqual(final_status.get('progress'), 100)

                # Returning user login should go straight to /dashboard
                res_returning = client.post('/auth/login', data={
                    'username': '02-777777-777',
                    'password': 'StrongPassword123!'
                })
                self.assertEqual(res_returning.status_code, 200)
                ret_data = res_returning.get_json()
                self.assertTrue(ret_data.get('success'))
                self.assertEqual(ret_data.get('redirect_url'), '/dashboard')
            finally:
                if user and getattr(user, 'account_id', None):
                    data_manager.delete_account_data(user.account_id)
                from app.models.account import account_manager
                account_manager.delete_user_completely('02-777777-777')

    @patch('requests.Session.post')
    def test_cms_downtime_detected_in_atomic_sync(self, mock_post):
        """Verify that when edge workers report ViewState extraction failure or 500, cms_down is returned."""
        cf_err_resp = MagicMock()
        cf_err_resp.status_code = 500
        cf_err_resp.text = '{"error":"Failed to extract ViewState from CMS login page"}'
        cf_err_resp.json.return_value = {"error": "Failed to extract ViewState from CMS login page"}

        sb_err_resp = MagicMock()
        sb_err_resp.status_code = 503
        sb_err_resp.text = '{"error":"CMS server is currently down (HTTP 500 Runtime Error)","cms_down":true}'
        sb_err_resp.json.return_value = {"error": "CMS server is currently down", "cms_down": True}

        mock_post.side_effect = [cf_err_resp, sb_err_resp]

        account = CMSAccount(account_id='test-cms-down-acc', cms_username='02-888888-888', cms_password='anypassword')
        from app.utils.enhanced_scraper import EnhancedCMSScraper
        scraper = EnhancedCMSScraper(account)

        res = scraper.scrape_full_sync_atomic(account.cms_username, account.cms_password)
        self.assertIsNotNone(res)
        self.assertTrue(res.get('cms_down'))
        self.assertIn('down', res.get('error', '').lower())

    @patch('app.routes.auth.verify_turnstile', return_value=True)
    @patch('app.utils.enhanced_scraper.EnhancedCMSScraper.scrape_full_sync_atomic')
    def test_login_returns_503_when_cms_down(self, mock_atomic, mock_turnstile):
        """Verify login returns 503 with is_cms_down: True when CMS server is down."""
        mock_atomic.return_value = {
            'cms_down': True,
            'error': 'Bahria University CMS server is currently down (HTTP 500 Runtime Error).'
        }

        app = create_app()
        app.config['TESTING'] = True
        app.config['SECRET_KEY'] = 'test-secret-key'
        app.config['WTF_CSRF_ENABLED'] = False

        client = app.test_client()
        with app.app_context():
            from app.models.account import account_manager
            account_manager.delete_user_completely('02-999999-999')
            try:
                res = client.post('/auth/login', data={
                    'username': '02-999999-999',
                    'password': 'AnyPassword123!'
                })

                self.assertEqual(res.status_code, 503)
                data = res.get_json()
                self.assertTrue(data.get('is_cms_down'))
                self.assertIn('CMS server is currently down', data.get('error'))
                self.assertIn('action', data)
                self.assertIn('cms.bahria.edu.pk', data['action']['url'])
            finally:
                from app.models.account import account_manager
                account_manager.delete_user_completely('02-999999-999')

    def test_steps_list_includes_assignments(self):
        """Verify steps array in sync status contains assignments and has 90s TTL."""
        mgr = MultiAccountScraperManager()
        account = CMSAccount(account_id='test-steps-acc', cms_username='01-111222-333', cms_password='pass')
        bundle = {'success': True, 'html': {}}

        with patch.object(mgr, 'ingest_atomic_bundle'):
            thread = mgr.start_staged_atomic_ingestion(account, bundle)
            status = mgr.get_sync_status(account.account_id)
            self.assertIn('assignments', status.get('steps', []))
            self.assertEqual(status['steps'][-1], 'assignments')
            self.assertTrue(status['is_syncing'])
            thread.join(timeout=2)

    def test_ingest_atomic_bundle_lms_assignments_success(self):
        """Verify ingest_atomic_bundle completes assignments step and records in completed_steps."""
        app = create_app()
        app.config['TESTING'] = True
        app.config['SECRET_KEY'] = 'test-secret-key'

        with app.app_context():
            account = CMSAccount(account_id='test-lms-success-acc', cms_username='01-333444-555', cms_password='pass', student_name='LMS Student')
            bundle = {
                'success': True,
                'html': {
                    'profile': '<table><tr><td>Name:</td><td>LMS Student</td></tr></table>',
                    'timetable': '<html></html>',
                    'attendance': '<html></html>',
                    'community_service': '<html></html>',
                    'gpa': '<html></html>'
                },
                'cookies': 'ASP.NET_SessionId=cookie_test'
            }

            from app.utils.enhanced_scraper import scraper_manager
            with patch('app.services.lms_dashboard_service.LMSDashboardService._fetch_and_cache_assignments') as mock_fetch:
                mock_fetch.return_value = {
                    'pending': [{'title': 'Assignment 1'}],
                    'missed': [],
                    'all_assignments': [{'title': 'Assignment 1'}],
                    'total_pending': 1
                }
                # Force non-testing branch inside _fetch_and_cache_lms_assignments_step
                with patch('flask.current_app.config.get', side_effect=lambda k, d=None: False if k == 'TESTING' else app.config.get(k, d)):
                    success = scraper_manager.ingest_atomic_bundle(account, bundle)
                    self.assertTrue(success)

                    status = scraper_manager.get_sync_status(account.account_id)
                    self.assertEqual(status.get('current_step'), 'completed')
                    self.assertEqual(status.get('progress'), 100)
                    self.assertIn('assignments', status.get('completed_steps', []))
                    self.assertNotIn('assignments', status.get('failed_steps', []))
                    mock_fetch.assert_called_once()

    def test_ingest_atomic_bundle_lms_failure_is_non_fatal(self):
        """Verify LMS failure marks step as failed/skipped but does not fail entire onboarding sync."""
        app = create_app()
        app.config['TESTING'] = True
        app.config['SECRET_KEY'] = 'test-secret-key'

        with app.app_context():
            account = CMSAccount(account_id='test-lms-fail-acc', cms_username='01-555666-777', cms_password='pass', student_name='LMS Fail Student')
            bundle = {
                'success': True,
                'html': {
                    'profile': '<table><tr><td>Name:</td><td>LMS Fail Student</td></tr></table>',
                    'timetable': '<html></html>',
                    'attendance': '<html></html>',
                    'community_service': '<html></html>',
                    'gpa': '<html></html>'
                },
                'cookies': 'ASP.NET_SessionId=cookie_fail'
            }

            from app.utils.enhanced_scraper import scraper_manager
            with patch('app.services.lms_dashboard_service.LMSDashboardService._fetch_and_cache_assignments', side_effect=Exception("LMS Gateway Down (503)")):
                with patch('flask.current_app.config.get', side_effect=lambda k, d=None: False if k == 'TESTING' else app.config.get(k, d)):
                    success = scraper_manager.ingest_atomic_bundle(account, bundle)
                    # Sync overall must still SUCCEED because LMS failure is non-fatal!
                    self.assertTrue(success)

                    status = scraper_manager.get_sync_status(account.account_id)
                    self.assertEqual(status.get('current_step'), 'completed')
                    self.assertEqual(status.get('progress'), 100)
                    self.assertIn('assignments', status.get('failed_steps', []))
                    self.assertNotIn('assignments', status.get('completed_steps', []))

    def test_survey_required_pauses_sync_and_freezes_ttl(self):
        """Verify CMSSurveyRequiredError pauses sync and prevents TTL expiry."""
        app = create_app()
        app.config['TESTING'] = True
        app.config['SECRET_KEY'] = 'test-secret-key'

        with app.app_context():
            from app.services.lms_scraper_service import CMSSurveyRequiredError
            from app.utils.enhanced_scraper import scraper_manager
            account = CMSAccount(account_id='test-survey-acc', cms_username='01-999888-777', cms_password='pass', student_name='Survey Student')
            scraper = scraper_manager.get_scraper(account)

            # Trigger synchronous fallback with CMSSurveyRequiredError
            with patch('app.services.lms_dashboard_service.LMSDashboardService._fetch_and_cache_assignments', side_effect=CMSSurveyRequiredError("Survey required")):
                with patch('flask.current_app.config.get', side_effect=lambda k, d=None: False if k == 'TESTING' else app.config.get(k, d)):
                    completed_steps = ['auth', 'profile']
                    failed_steps = []
                    scraper_manager._fetch_and_cache_lms_assignments_step(account, scraper, completed_steps, failed_steps)

                    status = scraper_manager.get_sync_status(account.account_id)
                    self.assertTrue(status.get('survey_required'))
                    self.assertEqual(status.get('survey_status'), 'detected')
                    self.assertIn('assignments', failed_steps)

    def test_survey_resolve_skip(self):
        """Verify resolve_survey_for_account with action='skip' unpauses sync and updates status."""
        from app.utils.enhanced_scraper import scraper_manager
        account_id = 'test-survey-skip-acc'
        scraper_manager._update_sync_status(
            account_id,
            is_syncing=True,
            survey_required=True,
            survey_status='detected'
        )

        res = scraper_manager.resolve_survey_for_account(account_id, action='skip')
        self.assertTrue(res)

        status = scraper_manager.get_sync_status(account_id)
        self.assertFalse(status.get('survey_required'))
        self.assertEqual(status.get('survey_status'), 'skipped')

    def test_survey_resolve_resume(self):
        """Verify resolve_survey_for_account with action='resume' clears survey_required."""
        from app.utils.enhanced_scraper import scraper_manager
        account_id = 'test-survey-resume-acc'
        scraper_manager._update_sync_status(
            account_id,
            is_syncing=True,
            survey_required=True,
            survey_status='detected'
        )

        res = scraper_manager.resolve_survey_for_account(account_id, action='resume')
        self.assertTrue(res)

        status = scraper_manager.get_sync_status(account_id)
        self.assertFalse(status.get('survey_required'))
        self.assertEqual(status.get('survey_status'), 'resumed')


if __name__ == '__main__':
    unittest.main()



