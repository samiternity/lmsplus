"""Analytics rendering and privacy checks without Firestore or upstream access."""
import json
import re
from pathlib import Path
from types import SimpleNamespace

import pytest
from flask import Flask, render_template
from flask_login import LoginManager, UserMixin, login_user

from app.config import AppConfig
from app.utils.analytics import analytics_context, register_analytics


class Student(UserMixin):
    def __init__(self, user_id):
        self.id = user_id
        self.username = user_id
        self.email = 'private@example.test'
        self.settings = {}
        self.cms_account = None


@pytest.fixture
def app():
    root = Path(__file__).resolve().parents[1] / 'app'
    app = Flask(__name__, template_folder=str(root / 'templates'), static_folder=str(root / 'static'))
    app.config.update(SECRET_KEY='test-session-key', APP_ENV='production', APP_URL='https://lmsplus.xyz',
                      GA4_MEASUREMENT_ID='G-TEST123', GA4_USER_ID_SECRET='test-analytics-key')
    manager = LoginManager(app)
    manager.user_loader(Student)
    register_analytics(app)
    app.jinja_env.globals['csrf_token'] = lambda: ''
    app.add_url_rule('/auth/logout', endpoint='auth.logout', view_func=lambda: '')

    @app.route('/dashboard')
    @app.route('/student/<student_id>')
    @app.route('/file/<path:file_url>')
    def page(**kwargs):
        return render_template('base.html', hide_chrome=True)

    @app.route('/standalone/<template>')
    def standalone(template):
        return render_template(template, gpa_stats=SimpleNamespace(), user_data={'gpa': {}})

    return app


def client_for(app, user_id='01-123456-001', host='https://lmsplus.xyz'):
    client = app.test_client()
    with client.session_transaction(base_url=host) as session:
        session['_user_id'] = user_id
        session['_fresh'] = True
    return client


def config_from(response):
    match = re.search(r'<script id="usage-analytics-config" type="application/json">(.*?)</script>',
                      response.get_data(as_text=True))
    return json.loads(match[1]) if match else None


def test_persistent_login_is_tracked_without_new_login(app):
    response = client_for(app).get('/dashboard', base_url='https://lmsplus.xyz')
    assert response.status_code == 200
    config = config_from(response)
    assert re.fullmatch('[0-9a-f]{64}', config['user_id'])
    assert config['page_location'] == 'https://lmsplus.xyz/dashboard'
    assert config['page_title'] == 'LMS+'
    assert config['page_referrer'] == ''
    assert response.get_data(as_text=True).count('js/analytics.js') == 1


@pytest.mark.parametrize('settings,host', [
    ({'APP_ENV': 'testing'}, 'https://lmsplus.xyz'),
    ({'APP_ENV': 'local'}, 'https://lmsplus.xyz'),
    ({'TESTING': True}, 'https://lmsplus.xyz'),
    ({'GA4_MEASUREMENT_ID': ''}, 'https://lmsplus.xyz'),
    ({'GA4_MEASUREMENT_ID': 'G-INVALID<script>'}, 'https://lmsplus.xyz'),
    ({'GA4_USER_ID_SECRET': ''}, 'https://lmsplus.xyz'),
    ({}, 'http://localhost'),
    ({}, 'https://fixture-lms-plus.web.app'),
])
def test_disabled_environments_emit_no_tracking(app, settings, host):
    app.config.update(settings)
    response = client_for(app, host=host).get('/dashboard', base_url=host)
    assert response.status_code == 200
    assert config_from(response) is None
    assert 'js/analytics.js' not in response.get_data(as_text=True)


def test_self_hosted_analytics_uses_configured_app_origin(app):
    origin = 'https://portal.example.test'
    app.config['APP_URL'] = origin
    response = client_for(app, host=origin).get('/dashboard', base_url=origin)
    assert config_from(response)['page_location'] == origin + '/dashboard'


def test_anonymous_pages_emit_no_tracking(app):
    response = app.test_client().get('/dashboard', base_url='https://lmsplus.xyz')
    assert response.status_code == 200
    assert config_from(response) is None
    assert 'js/analytics.js' not in response.get_data(as_text=True)


def test_identity_stable_across_browsers_and_different_for_other_users(app):
    def identity(user):
        return config_from(client_for(app, user).get('/dashboard', base_url='https://lmsplus.xyz'))['user_id']
    first = identity('01-123456-001')
    assert first == identity('01-123456-001')
    assert first != identity('01-123456-002')
    app.config['GA4_USER_ID_SECRET'] = 'different-key'
    assert first != identity('01-123456-001')


@pytest.mark.parametrize('url,expected', [
    ('/student/01-123456-001?token=secret&name=Private', '/student/:student_id'),
    ('/file/https://upstream.test/private.pdf?token=secret', '/file/:file_url'),
])
def test_sensitive_route_values_queries_and_referrers_are_removed(app, url, expected):
    response = client_for(app).get(url, base_url='https://lmsplus.xyz',
                                   headers={'Referer': 'https://private.test/?token=secret'})
    config = config_from(response)
    assert config['page_location'] == 'https://lmsplus.xyz' + expected
    payload = json.dumps(config)
    for private in ('01-123456-001', 'Private', 'secret', 'private@example.test', 'private.test', 'upstream.test'):
        assert private not in payload
    assert app.config['GA4_USER_ID_SECRET'] not in response.get_data(as_text=True)


@pytest.mark.parametrize('template', ['gpa_results.html', 'push_setup.html'])
def test_standalone_authenticated_pages_include_tracking(app, template):
    response = client_for(app).get('/standalone/' + template, base_url='https://lmsplus.xyz')
    assert response.status_code == 200
    assert config_from(response)
    assert response.get_data(as_text=True).count('js/analytics.js') == 1


def test_unmatched_routes_do_not_expose_analytics(app):
    with app.test_request_context('/missing', base_url='https://lmsplus.xyz'):
        login_user(Student('01-123456-001'))
        assert analytics_context() == {'analytics': None}


def test_optional_environment_settings(monkeypatch):
    monkeypatch.delenv('GA4_MEASUREMENT_ID', raising=False)
    monkeypatch.delenv('GA4_USER_ID_SECRET', raising=False)
    config = AppConfig()
    assert config.ga4_measurement_id == config.ga4_user_id_secret == ''
    monkeypatch.setenv('GA4_MEASUREMENT_ID', ' G-TEST123 ')
    monkeypatch.setenv('GA4_USER_ID_SECRET', ' key ')
    assert config.ga4_measurement_id == 'G-TEST123'
    assert config.ga4_user_id_secret == 'key'
