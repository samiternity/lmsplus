import logging
logger = logging.getLogger(__name__)

import os
import requests
from flask import Blueprint, redirect, request, url_for, flash, current_app, jsonify, abort
from flask_login import login_required, current_user
import urllib.parse
from app.models.account import account_manager

discord_oauth_bp = Blueprint('discord_oauth', __name__)

DISCORD_API_BASE_URL = 'https://discord.com/api/v10'

@discord_oauth_bp.route('/login')
@login_required
def login():
    """Redirect to Discord OAuth2 Authorization page."""
    client_id = os.environ.get('DISCORD_CLIENT_ID')
    if not client_id:
        flash("Discord Client ID is missing. Cannot link account.", "danger")
        return redirect(url_for('settings.settings'))

    redirect_uri = url_for('discord_oauth.callback', _external=True)
    # Require https in production, but allow http for localhost
    if '127.0.0.1' not in redirect_uri and 'localhost' not in redirect_uri:
        app_url = os.environ.get('APP_URL', '')
        if app_url and 'university-portal' in redirect_uri:
            redirect_uri = f'{app_url}/discord/callback'
        else:
            redirect_uri = redirect_uri.replace('http://', 'https://')
        
    scopes = 'identify guilds.join'
    
    auth_url = (
        f"{DISCORD_API_BASE_URL}/oauth2/authorize"
        f"?client_id={client_id}"
        f"&redirect_uri={urllib.parse.quote(redirect_uri)}"
        f"&response_type=code"
        f"&scope={urllib.parse.quote(scopes)}"
    )
    
    return redirect(auth_url)

@discord_oauth_bp.route('/callback')
@login_required
def callback():
    """Handle the OAuth2 callback from Discord."""
    code = request.args.get('code')
    error = request.args.get('error')
    
    if error:
        flash(f"Discord linking failed or was cancelled: {error}", "warning")
        return redirect(url_for('settings.settings'))
        
    if not code:
        flash("No authorization code received.", "danger")
        return redirect(url_for('settings.settings'))
        
    client_id = os.environ.get('DISCORD_CLIENT_ID')
    client_secret = os.environ.get('DISCORD_CLIENT_SECRET')
    bot_token = os.environ.get('DISCORD_BOT_TOKEN')
    guild_id = os.environ.get('DISCORD_GUILD_ID')
    role_id = os.environ.get('DISCORD_AUTHORIZED_ROLE_ID', '').strip() or None
    
    redirect_uri = url_for('discord_oauth.callback', _external=True)
    if '127.0.0.1' not in redirect_uri and 'localhost' not in redirect_uri:
        app_url = os.environ.get('APP_URL', '')
        if app_url and 'university-portal' in redirect_uri:
            redirect_uri = f'{app_url}/discord/callback'
        else:
            redirect_uri = redirect_uri.replace('http://', 'https://')
        
    # 1. Exchange code for access token
    data = {
        'client_id': client_id,
        'client_secret': client_secret,
        'grant_type': 'authorization_code',
        'code': code,
        'redirect_uri': redirect_uri
    }
    
    headers = {
        'Content-Type': 'application/x-www-form-urlencoded'
    }
    
    try:
        r = requests.post(f"{DISCORD_API_BASE_URL}/oauth2/token", data=data, headers=headers)
        r.raise_for_status()
        token_data = r.json()
        access_token = token_data.get('access_token')
        
        # 2. Get user info
        user_headers = {
            'Authorization': f"Bearer {access_token}"
        }
        user_req = requests.get(f"{DISCORD_API_BASE_URL}/users/@me", headers=user_headers)
        user_req.raise_for_status()
        discord_user = user_req.json()
        
        discord_id = discord_user.get('id')
        discord_username = discord_user.get('username')
        
        if not discord_id:
            raise Exception("Discord API didn't return a user ID")
            
        # 3. Update User model
        current_user.discord_id = discord_id
        current_user.discord_username = discord_username
        if current_user.notification_preferences is None:
            current_user.notification_preferences = {}
        current_user.notification_preferences['discord_enabled'] = True
        
        # Save to database (persists to Firestore in production)
        current_user.save()
            
        # Fire Discord Account Linked Webhook
        try:
            from app.services.discord_service import enqueue_webhook_log
            from datetime import datetime
            
            # Use fetched name from CMS if available, otherwise fallback
            name_val = current_user.username
            enrollment_val = "N/A"
            if current_user.cms_account:
                name_val = current_user.cms_account.fetched_name
                enrollment_val = current_user.cms_account.cms_username
                
            enqueue_webhook_log(
                title="Discord Account Linked",
                color=0x9b59b6,
                fields=[
                    {'name': 'Name', 'value': str(name_val), 'inline': True},
                    {'name': 'Enrollment', 'value': str(enrollment_val), 'inline': True},
                    {'name': 'Discord ID', 'value': str(discord_id), 'inline': True},
                    {'name': 'Discord user name', 'value': str(discord_username), 'inline': True}
                ],
                footer=datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
            )
        except Exception as e:
            current_app.logger.error(f"Failed to enqueue Discord linking webhook: {e}")
            
        # 4. Automatically add user to our Guild (Server) using the bot token and access token
        if guild_id and bot_token:
            add_member_url = f"{DISCORD_API_BASE_URL}/guilds/{guild_id}/members/{discord_id}"
            bot_headers = {
                'Authorization': f"Bot {bot_token}",
                'Content-Type': 'application/json'
            }
            add_data = {
                'access_token': access_token
            }
            # 1. Add member to server
            add_req = requests.put(add_member_url, headers=bot_headers, json=add_data)
            
            # 201 means added, 204 means already a member. Both are good.
            # 2. Add role in a separate request so role failures don't prevent server joining
            if add_req.status_code in (201, 204) and role_id:
                role_add_url = f"{DISCORD_API_BASE_URL}/guilds/{guild_id}/members/{discord_id}/roles/{role_id}"
                role_req = requests.put(role_add_url, headers=bot_headers)
                if role_req.status_code != 204:
                    current_app.logger.warning(f"Failed to assign role to Discord member (Server Join status: {add_req.status_code}): {role_req.status_code} {role_req.text}")
                    
            if add_req.status_code not in (201, 204):
                current_app.logger.warning(f"Failed to add user to Discord server: {add_req.status_code} {add_req.text}")
                flash(f"Successfully linked account as {discord_username}, but we couldn't automatically add you to the server. You may need to join manually.", "warning")
            else:
                flash(f"Successfully linked account as {discord_username} and added you to the Official Server!", "success")
        else:
            flash(f"Successfully linked account as {discord_username}. (Note: Server auto-join skipped due to missing config).", "success")
            
    except requests.exceptions.RequestException as e:
        current_app.logger.error(f"Discord OAuth request failed: {e}")
        flash("Failed to communicate with Discord. Please try again later.", "danger")
    except Exception as e:
        current_app.logger.error(f"Discord linking failed: {e}")
        flash("An unexpected error occurred during linking.", "danger")
        
    return redirect(url_for('settings.settings'))

@discord_oauth_bp.route('/unlink', methods=['POST'])
@login_required
def unlink():
    """Unlink Discord account."""
    if current_user.discord_id:
        current_user.discord_id = None
        if current_user.notification_preferences:
            current_user.notification_preferences['discord_enabled'] = False
            
        current_user.save()
        flash("Discord account unlinked successfully.", "success")
    return redirect(url_for('settings.settings'))

@discord_oauth_bp.route('/test-dm', methods=['POST'])
@login_required
def test_discord_dm():
    """Send a generic test DM via Discord bot."""
    if current_app.config.get('IS_PRODUCTION', False):
        abort(404)

    if not current_user.discord_id:
        return jsonify({'error': 'Discord account not linked'}), 400
        
    try:
        from app.services.discord_service import enqueue_discord_notification
        enqueue_discord_notification(
            discord_id=current_user.discord_id,
            title="Discord Integration Test",
            description="Your Discord integration is working perfectly! You will now receive LMS notifications here.",
            color=0x22c55e # Green
        )
        return jsonify({'success': True, 'message': 'Test message sent successfully'})
    except Exception as e:
        current_app.logger.error(f"Failed to send Discord test DM: {e}")
        return jsonify({'error': 'Failed to send test message'}), 500
