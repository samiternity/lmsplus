"""
Tests for Discord-connected daily job restriction and notice banner.
"""
import pytest
from unittest.mock import Mock, patch, MagicMock
from app.models.account import User, CMSAccount
from app.services.notification_scheduler import NotificationScheduler
from app.services.background_notification_service import (
    run_all_notifications,
    check_cached_urgent_assignments_only
)


class TestUserScheduledCheckEligibility:
    """Tests for User.is_scheduled_check_eligible()"""

    def test_ineligible_when_no_discord_id(self):
        user = User(
            id="user1",
            username="user1",
            email="u1@test.com",
            discord_id=None,
            notification_preferences={'discord_enabled': True, 'absent_scheduled': True}
        )
        assert user.is_scheduled_check_eligible() is False

    @pytest.mark.parametrize("empty_val", ["", "   ", "null", "none", "None", "NULL"])
    def test_ineligible_when_discord_id_empty_or_null_string(self, empty_val):
        user = User(
            id="user1",
            username="user1",
            email="u1@test.com",
            discord_id=empty_val,
            notification_preferences={'discord_enabled': True, 'absent_scheduled': True}
        )
        assert user.is_scheduled_check_eligible() is False

    def test_ineligible_when_discord_disabled(self):
        user = User(
            id="user1",
            username="user1",
            email="u1@test.com",
            discord_id="987654321",
            notification_preferences={'discord_enabled': False, 'absent_scheduled': True}
        )
        assert user.is_scheduled_check_eligible() is False

    def test_ineligible_when_no_alert_toggles_enabled(self):
        user = User(
            id="user1",
            username="user1",
            email="u1@test.com",
            discord_id="987654321",
            notification_preferences={
                'discord_enabled': True,
                'absent_scheduled': False,
                'absent_unscheduled': False,
                'new_assignment': False,
                'due_24h': False,
                'due_3d': False,
                'overdue_warning': False
            }
        )
        assert user.is_scheduled_check_eligible() is False

    def test_ineligible_when_notification_preferences_is_none(self):
        user = User(
            id="user1",
            username="user1",
            email="u1@test.com",
            discord_id="987654321",
            notification_preferences=None
        )
        # Defaults to False for all preferences
        assert user.is_scheduled_check_eligible() is False

    @pytest.mark.parametrize("alert_key", [
        'absent_scheduled',
        'absent_unscheduled',
        'new_assignment',
        'due_24h',
        'due_3d',
        'overdue_warning'
    ])
    def test_eligible_when_at_least_one_alert_enabled(self, alert_key):
        prefs = {'discord_enabled': True}
        prefs[alert_key] = True
        user = User(
            id="user1",
            username="user1",
            email="u1@test.com",
            discord_id="987654321",
            notification_preferences=prefs
        )
        assert user.is_scheduled_check_eligible() is True

    def test_eligible_with_string_booleans(self):
        user = User(
            id="user1",
            username="user1",
            email="u1@test.com",
            discord_id=123456789012345678,  # integer id
            notification_preferences={
                'discord_enabled': 'true',
                'due_24h': 'TRUE'
            }
        )
        assert user.is_scheduled_check_eligible() is True


class TestSchedulerFiltering:
    """Test scheduler micro-batch execution skips ineligible users"""

    def test_run_notifications_skips_ineligible_users(self):
        scheduler = NotificationScheduler()

        # User 1: Eligible (Discord + alert)
        cms1 = CMSAccount(account_id="acc1", cms_username="01-111", cms_password="pw1")
        user1 = User(
            id="u1", username="u1", email="u1@test.com",
            cms_account=cms1, account_id="acc1",
            discord_id="11111",
            notification_preferences={'discord_enabled': True, 'absent_scheduled': True}
        )

        # User 2: Ineligible (No Discord)
        cms2 = CMSAccount(account_id="acc2", cms_username="01-222", cms_password="pw2")
        user2 = User(
            id="u2", username="u2", email="u2@test.com",
            cms_account=cms2, account_id="acc2",
            discord_id=None,
            notification_preferences={'discord_enabled': False}
        )

        # User 3: Ineligible (Discord linked, but 0 alerts active)
        cms3 = CMSAccount(account_id="acc3", cms_username="01-333", cms_password="pw3")
        user3 = User(
            id="u3", username="u3", email="u3@test.com",
            cms_account=cms3, account_id="acc3",
            discord_id="33333",
            notification_preferences={'discord_enabled': True, 'absent_scheduled': False, 'due_24h': False}
        )

        users_map = {"u1": user1, "u2": user2, "u3": user3}

        with patch.object(scheduler, 'get_kill_switch_status', return_value={'active': False, 'usage_seconds': 0}), \
             patch.object(scheduler, '_log_free_tier_usage'), \
             patch('app.models.account.User.get', side_effect=lambda uid: users_map.get(uid)), \
             patch('app.models.account.User.get_users_by_batch', return_value=[user1, user2, user3]), \
             patch.object(scheduler, '_process_account', return_value=1) as mock_process:

            summary = scheduler._run_notifications(bucket=10)

            assert summary['users_processed'] == 3
            assert summary['success_count'] == 1
            assert summary['skipped_count'] == 2
            assert summary['error_count'] == 0
            assert summary['notifications_sent'] == 1

            # Only u1 was scraped
            assert mock_process.call_count == 1
            mock_process.assert_called_once_with(user1, cms1)


class TestBackgroundServiceFiltering:
    """Test urgent check and run_all_notifications skip ineligible users"""

    def test_run_all_notifications_skips_ineligible(self):
        user_eligible = User(
            id="u1", username="u1", email="u1@test.com",
            cms_account=CMSAccount(account_id="a1", cms_username="u1", cms_password="p1"),
            account_id="a1",
            discord_id="123",
            notification_preferences={'discord_enabled': True, 'absent_scheduled': True}
        )
        user_unlinked = User(
            id="u2", username="u2", email="u2@test.com",
            cms_account=CMSAccount(account_id="a2", cms_username="u2", cms_password="p2"),
            account_id="a2",
            discord_id=None
        )

        with patch('app.models.account.User.list_all', return_value=[user_eligible, user_unlinked]), \
             patch('app.services.background_notification_service._process_account', return_value={'attendance': 1, 'assignments': 0, 'total': 1}) as mock_proc, \
             patch('app.services.discord_service.enqueue_webhook_log'):

            summary = run_all_notifications()

            assert summary['users'] == 2
            assert summary['accounts'] == 1
            assert summary['skipped'] == 1
            assert summary['notifications']['total'] == 1
            assert mock_proc.call_count == 1

    def test_check_cached_urgent_assignments_only_skips_ineligible(self):
        user_unlinked = User(
            id="u2", username="u2", email="u2@test.com",
            cms_account=CMSAccount(account_id="a2", cms_username="u2", cms_password="p2"),
            account_id="a2",
            discord_id=None
        )

        with patch('app.models.account.User.list_all', return_value=[user_unlinked]), \
             patch('app.utils.data_manager.data_manager.get_unified_dashboard') as mock_dash:

            result = check_cached_urgent_assignments_only()

            assert "skipped 1" in result
            assert "Evaluated 0" in result
            mock_dash.assert_not_called()


class TestDiscordNoticeBannerComponent:
    """Test Jinja rendering of components/discord_notice_banner.html"""

    @pytest.fixture
    def app(self):
        import os
        from flask import Flask
        app = Flask(__name__, template_folder=os.path.abspath('app/templates'))
        app.secret_key = 'test'
        app.add_url_rule('/settings', endpoint='settings.settings')
        return app

    def test_renders_unlinked_state(self, app):
        from flask import render_template_string

        user = User(id="u1", username="u1", email="u1@test.com", discord_id=None)

        template = "{% include 'components/discord_notice_banner.html' %}"

        with app.test_request_context():
            rendered = render_template_string(template, current_user=user)
            assert "Link Discord to enable daily checks" in rendered
            assert "Connect Discord" in rendered

    def test_renders_alerts_off_state(self, app):
        from flask import render_template_string

        user = User(
            id="u1", username="u1", email="u1@test.com",
            discord_id="123456",
            notification_preferences={'discord_enabled': True, 'absent_scheduled': False}
        )

        template = "{% include 'components/discord_notice_banner.html' %}"

        with app.test_request_context():
            rendered = render_template_string(template, current_user=user)
            assert "Turn on alerts to resume daily checks" in rendered
            assert "Manage Alerts" in rendered

    def test_renders_nothing_when_eligible(self, app):
        from flask import render_template_string

        user = User(
            id="u1", username="u1", email="u1@test.com",
            discord_id="123456",
            notification_preferences={'discord_enabled': True, 'due_24h': True}
        )

        template = "{% include 'components/discord_notice_banner.html' %}"

        with app.test_request_context():
            rendered = render_template_string(template, current_user=user)
            assert rendered.strip() == ""
