"""Browser consent and connection controls use existing LMS+ session authentication."""

from flask import Blueprint, abort, current_app, flash, redirect, render_template, request, session, url_for
from flask_login import current_user, login_required

from app.utils.session_auth import is_account_authenticated
from lms_mcp.core import ConnectorError
from lms_mcp.runtime import user_accounts


connector_bp = Blueprint('assignment_connector', __name__)


def runtime():
    configured = current_app.extensions.get('lms_mcp')
    if not configured:
        abort(404)
    return configured


def eligible_accounts(configured):
    return {key: account for key, account in user_accounts(current_user).items()
            if configured.settings.allows_account(key) and is_account_authenticated(key)}


@connector_bp.after_request
def private_response(response):
    response.headers['Cache-Control'] = 'private, no-store'
    # Same-origin referrers are required by Flask-WTF's HTTPS CSRF check.
    # Cross-origin OAuth redirects still omit the authorization request URL.
    response.headers['Referrer-Policy'] = 'same-origin'
    response.headers['X-Robots-Tag'] = 'noindex, nofollow'
    response.headers['X-Frame-Options'] = 'DENY'
    return response


@connector_bp.route('/connector/authorize', methods=['GET', 'POST'])
def consent():
    configured = runtime()
    request_id = request.values.get('request', '')
    authorization = configured.oauth.authorization(request_id)
    if not authorization:
        return render_template('connector_consent.html', expired=True, hide_chrome=True), 400
    if not current_user.is_authenticated:
        if request.method != 'GET':
            abort(401)
        session['mcp_consent_request'] = request_id
        return redirect(url_for('auth.login'))
    active_account = current_user.cms_account
    account = eligible_accounts(configured).get(active_account.account_id) if active_account else None
    if request.method == 'POST':
        if request.form.get('decision') == 'deny':
            return redirect(configured.oauth.deny(request_id))
        if request.form.get('decision') != 'allow' or not account or request.form.get('account_id') != account.account_id:
            abort(403)
        try:
            return redirect(configured.oauth.approve(request_id, current_user.get_id(), account.account_id))
        except ConnectorError as error:
            return render_template('connector_consent.html', expired=True, hide_chrome=True, error=error.message), 400
    account_name = (getattr(account, 'fetched_name', None) or account.display_name) if account else None
    if account and account_name == account.cms_username:
        account_name = None
    return render_template('connector_consent.html', expired=False, hide_chrome=True, account=account, account_name=account_name,
                           request_id=request_id, client_name=authorization['client_name'])


@connector_bp.route('/settings/connections/<connection_id>/disconnect', methods=['POST'])
@login_required
def disconnect(connection_id):
    if len(connection_id) > 128 or '/' in connection_id:
        abort(404)
    try:
        runtime().oauth.disconnect(connection_id, current_user.get_id())
    except ConnectorError:
        abort(404)
    flash('Connection disconnected.', 'success')
    return redirect(url_for('settings.settings') + '#claude-connections')
