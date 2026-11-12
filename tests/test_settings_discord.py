"""Tests for Discord Test DM visibility and route blocking in production."""

import pytest
from unittest.mock import patch, MagicMock
from app import create_app
from app.models.account import User


@pytest.fixture
def test_user():
    """Create a mock user with discord linked."""
    user = User(
        id='test-user-id',
        username='testuser',
        email='test@example.com',
        discord_id=str(10**17),
        discord_username='testdiscord'
    )
    user.preferences = {'theme': 'dark'}
    user.notification_preferences = {'discord_enabled': True}
    return user


@pytest.fixture
def app():
    """Create Flask test app."""
    app = create_app()
    app.config['TESTING'] = True
    app.config['WTF_CSRF_ENABLED'] = False
    app.config['SECRET_KEY'] = 'test-secret'
    return app


def test_settings_page_hides_test_dm_in_production(app, test_user):
    """Verify Send Test DM button is absent in production."""
    app.config['IS_PRODUCTION'] = True

    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess['_user_id'] = test_user.id
            sess['_fresh'] = True

        with patch('app.models.account.User.get', return_value=test_user), \
             patch('flask_login.utils._get_user', return_value=test_user), \
             patch.dict('os.environ', {'DISCORD_CLIENT_ID': 'test-client-id'}):
            resp = client.get('/settings')
            assert resp.status_code == 200
            html = resp.get_data(as_text=True)
            assert 'testDiscordBtn' not in html
            assert 'Send Test DM' not in html
            assert 'btn-unlink' in html
            assert 'fa-unlink' in html
            assert 'confirm(' in html


def test_settings_page_shows_test_dm_in_development(app, test_user):
    """Verify Send Test DM button is visible in non-production."""
    app.config['IS_PRODUCTION'] = False

    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess['_user_id'] = test_user.id
            sess['_fresh'] = True

        with patch('app.models.account.User.get', return_value=test_user), \
             patch('flask_login.utils._get_user', return_value=test_user), \
             patch.dict('os.environ', {'DISCORD_CLIENT_ID': 'test-client-id'}):
            resp = client.get('/settings')
            assert resp.status_code == 200
            html = resp.get_data(as_text=True)
            assert 'testDiscordBtn' in html
            assert 'Send Test DM' in html
            assert 'btn-unlink' in html
            assert 'fa-unlink' in html


def test_discord_test_dm_route_inaccessible_in_production(app, test_user):
    """Verify /discord/test-dm route returns 404 in production."""
    app.config['IS_PRODUCTION'] = True

    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess['_user_id'] = test_user.id
            sess['_fresh'] = True

        with patch('app.models.account.User.get', return_value=test_user), \
             patch('flask_login.utils._get_user', return_value=test_user):
            resp = client.post('/discord/test-dm')
            assert resp.status_code == 404


def test_discord_test_dm_route_accessible_in_development(app, test_user):
    """Verify /discord/test-dm route succeeds in development."""
    app.config['IS_PRODUCTION'] = False

    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess['_user_id'] = test_user.id
            sess['_fresh'] = True

        with patch('app.models.account.User.get', return_value=test_user), \
             patch('flask_login.utils._get_user', return_value=test_user), \
             patch('app.services.discord_service.enqueue_discord_notification') as mock_notify:
            resp = client.post('/discord/test-dm')
            assert resp.status_code == 200
            data = resp.get_json()
            assert data['success'] is True
            assert mock_notify.called


def test_unlinked_user_settings_page(app):
    """Verify unlinked user sees Connect Discord button, locked overlay, and unified form."""
    user = User(
        id='unlinked-user-id',
        username='unlinkeduser',
        email='unlinked@example.com',
        discord_id=None,
        discord_username=None
    )
    user.preferences = {'theme': 'dark'}
    user.notification_preferences = {}

    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess['_user_id'] = user.id
            sess['_fresh'] = True

        with patch('app.models.account.User.get', return_value=user), \
             patch('flask_login.utils._get_user', return_value=user), \
             patch.dict('os.environ', {'DISCORD_CLIENT_ID': 'test-client-id'}):
            resp = client.get('/settings')
            assert resp.status_code == 200
            html = resp.get_data(as_text=True)

            # Redesigned Connect Discord CTA button (pill with circular icon disc)
            assert 'btn-connect-discord' in html
            assert 'discord-icon-disc' in html
            assert 'Connect Discord' in html
            assert 'Not Connected' in html
            assert 'is-unlinked' in html

            # Unified notification card and locked overlay (no duplicate button)
            assert 'card-notifications' in html
            assert '<div class="notifications-locked-overlay"' in html
            assert 'Connect Discord above to unlock alerts' in html
            assert 'id="notificationForm"' in html
            assert 'class="is-locked"' in html

            # Verify only 1 Connect Discord link on page (in Discord card, not duplicated in overlay)
            assert html.count('href="/discord/login"') == 1 or html.count('btn-connect-discord') == 1

            # All 6 notification fields present
            assert 'name="new_assignment"' in html
            assert 'name="due_24h"' in html
            assert 'name="due_3d"' in html
            assert 'name="overdue_warning"' in html
            assert 'name="absent_scheduled"' in html
            assert 'name="absent_unscheduled"' in html

            # Unlink button absent for unlinked user
            assert '<button type="submit" class="btn-unlink"' not in html


def test_linked_user_settings_page_unlocked(app, test_user):
    """Verify linked user sees unlocked notification form and unlink button."""
    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess['_user_id'] = test_user.id
            sess['_fresh'] = True

        with patch('app.models.account.User.get', return_value=test_user), \
             patch('flask_login.utils._get_user', return_value=test_user), \
             patch.dict('os.environ', {'DISCORD_CLIENT_ID': 'test-client-id'}):
            resp = client.get('/settings')
            assert resp.status_code == 200
            html = resp.get_data(as_text=True)

            # Notification form unlocked
            assert 'card-notifications' in html
            assert '<div class="notifications-locked-overlay"' not in html
            assert 'id="notificationForm"' in html
            assert 'class="is-locked"' not in html

            # Linked discord status
            assert 'Connected' in html
            assert '<button type="submit" class="btn-unlink"' in html

