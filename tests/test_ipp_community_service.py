import pytest
from unittest.mock import MagicMock, patch
from app import create_app
from app.models.account import User, CMSAccount
from app.utils.enhanced_scraper import EnhancedCMSScraper, MultiAccountScraperManager


@pytest.fixture
def app():
    app = create_app()
    app.config['TESTING'] = True
    app.config['SECRET_KEY'] = 'test-secret-key'
    app.config['WTF_CSRF_ENABLED'] = False
    return app


@pytest.fixture
def client(app):
    return app.test_client()


def test_scraper_skips_community_service_for_ipp():
    """Verify enhanced scraper skips CMS fetch for IPP students."""
    account = CMSAccount(
        account_id='test-ipp-acc',
        cms_username='05-111111-001',
        cms_password='fake-password',
        institute_id='5'
    )
    scraper = EnhancedCMSScraper(account=account)
    
    with patch.object(scraper, '_request') as mock_req:
        res = scraper.scrape_community_service('05-111111-001')
        assert mock_req.call_count == 0
        assert res is not None
        assert res.get('not_applicable') is True
        assert res.get('completed_hours') == 0


def test_atomic_csp_returns_none_for_ipp():
    """Verify atomic single-page scrape returns None immediately for IPP."""
    account = CMSAccount(
        account_id='test-ipp-acc',
        cms_username='05-111111-001',
        cms_password='fake-password',
        institute_id='5'
    )
    scraper = EnhancedCMSScraper(account=account)
    
    with patch.object(scraper, 'scrape_page_atomic') as mock_atomic:
        res = scraper.scrape_community_service_atomic('05-111111-001', 'fake-password', '5')
        assert res is None
        assert mock_atomic.call_count == 0


def test_ingest_atomic_bundle_handles_ipp_csp(app):
    """Verify ingest_atomic_bundle stores not_applicable CSP and appends to completed_steps."""
    with app.app_context():
        manager = MultiAccountScraperManager()
        account = CMSAccount(
            account_id='test-ipp-bundle',
            cms_username='05-111111-002',
            cms_password='fake-password',
            institute_id='5'
        )
        
        atomic_bundle = {
            'success': True,
            'html': {
                'profile': '<html><body><span id="ctl00_BodyPH_lblStudentName">IPP Student</span></body></html>',
                'timetable': '<html><body></body></html>',
                'attendance': '<html><body></body></html>',
                'community_service': None,  # skipped by edge worker
                'gpa': '<html><body></body></html>'
            }
        }
        
        success = manager.ingest_atomic_bundle(
            account,
            atomic_bundle,
            data_types=['profile', 'community_service'],
            step_delay=0
        )
        
        assert success is True
        status = manager.get_sync_status(account.account_id)
        assert 'community_service' in status.get('completed_steps', [])
        assert 'community_service' not in status.get('failed_steps', [])
        from app.utils.data_manager import data_manager
        saved_csp = data_manager.load_account_data(account.account_id, 'community_service')
        assert saved_csp is not None
        assert saved_csp.get('not_applicable') is True


def test_community_route_renders_not_applicable_for_ipp(app, client):
    """Verify navigating to /community-service renders Not Applicable banner for IPP student."""
    user = User(
        id='05-111111-003',
        username='05-111111-003',
        email='05-111111-003@student.bahria.edu.pk'
    )
    user.cms_account = CMSAccount(
        account_id='acc-ipp-003',
        cms_username='05-111111-003',
        cms_password='secret',
        institute_id='5'
    )
    User.save(user)
    
    with client.session_transaction() as sess:
        sess['_user_id'] = user.id
        sess['active_account_id'] = user.cms_account.account_id
        sess['authenticated_accounts'] = [user.cms_account.account_id]
        
    with patch('app.routes.community.is_account_authenticated', return_value=True):
        resp = client.get('/community-service')
        assert resp.status_code == 200
        html = resp.get_data(as_text=True)
        assert 'Community Service Not Applicable' in html
        assert 'id="refreshDataToggle"' not in html  # Refresh button hidden
        assert 'refresh-data-toggle' not in html


def test_community_refresh_route_guards_ipp(app, client):
    """Verify /community-service/refresh redirects without scraping for IPP student."""
    user = User(
        id='05-111111-004',
        username='05-111111-004',
        email='05-111111-004@student.bahria.edu.pk'
    )
    user.cms_account = CMSAccount(
        account_id='acc-ipp-004',
        cms_username='05-111111-004',
        cms_password='secret',
        institute_id='5'
    )
    User.save(user)
    
    with client.session_transaction() as sess:
        sess['_user_id'] = user.id
        sess['active_account_id'] = user.cms_account.account_id
        sess['authenticated_accounts'] = [user.cms_account.account_id]
        
    with patch('app.routes.community.is_account_authenticated', return_value=True), \
         patch('app.utils.enhanced_scraper.EnhancedCMSScraper.scrape_community_service') as mock_scrape:
        resp = client.get('/community-service/refresh', follow_redirects=True)
        assert resp.status_code == 200
        assert mock_scrape.call_count == 0
        html = resp.get_data(as_text=True)
        assert 'Community service is not applicable for IPP campus.' in html


def test_dashboard_displays_dash_for_ipp_csp(app, client):
    """Verify dashboard renders '-' for CSP Hours on IPP student account."""
    user = User(
        id='05-111111-005',
        username='05-111111-005',
        email='05-111111-005@student.bahria.edu.pk'
    )
    user.cms_account = CMSAccount(
        account_id='acc-ipp-005',
        cms_username='05-111111-005',
        cms_password='secret',
        institute_id='5'
    )
    User.save(user)

    with client.session_transaction() as sess:
        sess['_user_id'] = user.id
        sess['active_account_id'] = user.cms_account.account_id
        sess['authenticated_accounts'] = [user.cms_account.account_id]

    with patch('app.routes.main.load_user_data', return_value={'profile': {'name': 'IPP Student', 'program': 'BS Psychology'}}), \
         patch('app.utils.enhanced_scraper.MultiAccountScraperManager.get_sync_status', return_value={'is_syncing': False, 'completed_steps': ['auth', 'profile']}):
        resp = client.get('/dashboard')
        assert resp.status_code == 200
        html = resp.get_data(as_text=True)
        # Should render '-' for CSP Hours
        assert '<h3 class="card-title">-</h3>' in html
        # Should NOT show community service in the sidebar navigation
        assert 'href="/community-service"' not in html


def test_sidebar_shows_community_service_for_karachi_student(app, client):
    """Verify dashboard renders CSP Hours number and sidebar link for non-IPP (Karachi) student."""
    user = User(
        id='02-000000-008',
        username='02-000000-008',
        email='02-000000-008@student.bahria.edu.pk'
    )
    user.cms_account = CMSAccount(
        account_id='acc-khi-013',
        cms_username='02-000000-008',
        cms_password='secret',
        institute_id='2'
    )
    User.save(user)

    with client.session_transaction() as sess:
        sess['_user_id'] = user.id
        sess['active_account_id'] = user.cms_account.account_id
        sess['authenticated_accounts'] = [user.cms_account.account_id]

    with patch('app.routes.main.load_user_data', return_value={'profile': {'name': 'Karachi Student', 'program': 'BSCS'}, 'community_service': {'completed_hours': 24}}), \
         patch('app.utils.enhanced_scraper.MultiAccountScraperManager.get_sync_status', return_value={'is_syncing': False, 'completed_steps': ['auth', 'profile']}):
        resp = client.get('/dashboard')
        assert resp.status_code == 200
        html = resp.get_data(as_text=True)
        # Should render count-up for Karachi student
        assert 'data-value="24"' in html
        # Should show community service in the sidebar navigation
        assert 'href="/community-service"' in html

