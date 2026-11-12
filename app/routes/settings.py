import logging
logger = logging.getLogger(__name__)

from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify, current_app
import os
from flask_login import login_required, current_user
from app.config import get_deployment_origin
from app.services.lms_auth_service import LMSAuthService

settings_bp = Blueprint('settings', __name__)

def lms_setup_needed():
    """Check if LMS setup is needed for the current user"""
    if not current_user.is_authenticated:
        return False
    
    active_account = current_user.get_active_account()
    if not active_account:
        return False
    
    return not active_account.has_lms_credentials()

@settings_bp.context_processor
def inject_lms_setup_status():
    """Inject LMS setup status into template context"""
    return {
        'lms_setup_needed': lms_setup_needed()
    }

@settings_bp.route('/settings')
@login_required
def settings():
    # Get user preferences
    user_settings = current_user.preferences or {'theme': 'dark'}
    
    # Get notification preferences from user model
    notification_settings = current_user.notification_preferences or {
        'absent_scheduled': False,
        'absent_unscheduled': False,

        'new_assignment': False,
        'due_24h': False,
        'due_3d': False,
        'overdue_warning': False
    }
    
    discord_app_configured = current_app.config['APP_ENV'] != 'local' and bool(os.environ.get('DISCORD_CLIENT_ID'))
    
    is_production = current_app.config.get('IS_PRODUCTION', False)

    connector = current_app.extensions.get('lms_mcp')
    connections = []
    connector_available = False
    connector_url = connector.settings.resource if connector else None
    if connector:
        from app.routes.assignment_connector import eligible_accounts
        from lms_mcp.core import CHATGPT_CALLBACK, PROVIDER_LABELS
        from lms_mcp.store import digest

        connector_available = bool(eligible_accounts(connector))
        for record in connector.store.connections(current_user.get_id()):
            if record.get('revoked'):
                continue
            provider = record.get('provider')
            if provider is None:
                # Older ChatGPT grants saved the name, but Claude clients could supply it too.
                provider = 'claude'
                if record.get('client_name') == 'ChatGPT' and record.get('client_id'):
                    client = connector.store.get('mcp_clients/' + digest(record['client_id'])) or {}
                    if CHATGPT_CALLBACK in client.get('redirect_uris', []):
                        provider = 'chatgpt'
            connections.append({
                'id': record['id'],
                'provider': provider,
                'app_label': PROVIDER_LABELS.get(provider, PROVIDER_LABELS['other']),
                'connected': bool(record.get('refresh_hash')),
            })

    peer = request.environ.get('werkzeug.proxy_fix.orig', {}).get('REMOTE_ADDR', request.remote_addr)
    connector_preview = (current_app.debug and not is_production and not connector_available and not connections
                         and request.host.split(':', 1)[0] in ('localhost', '127.0.0.1')
                         and peer in ('127.0.0.1', '::1'))
    if connector_preview:
        connector_available = True
        connector_url = f"{get_deployment_origin('MCP_PREVIEW_ORIGIN')}/mcp"

    return render_template('settings.html', 
                         user_settings=user_settings,
                         notification_settings=notification_settings,
                         discord_app_configured=discord_app_configured,
                         is_production=is_production,
                         connector_available=connector_available,
                         connector_url=connector_url,
                         connector_connections=connections)

@settings_bp.route('/settings/theme', methods=['POST'])
@login_required
def update_theme():
    theme = request.form.get('theme')
    
    if theme not in ['light', 'dark', 'system']:
        flash('Invalid theme selection.', 'error')
        return redirect(url_for('settings.settings'))
    
    # Update user preferences
    current_user.preferences['theme'] = theme
    current_user.save()
    
    flash('Theme preference updated successfully.', 'success')
    return redirect(url_for('settings.settings'))

@settings_bp.route('/settings/notifications', methods=['POST'])
@login_required
def update_notifications():
    # Get all notification settings from form
    notification_keys = [
        'absent_scheduled', 'absent_unscheduled', 
        'new_assignment', 'due_24h', 'due_3d', 'overdue_warning'
    ]
    
    # Update notification preferences (checkbox present = True, absent = False)
    for key in notification_keys:
        current_user.notification_preferences[key] = key in request.form
    
    # Save to persistent storage
    if current_user.save():
        return jsonify({'success': True, 'message': 'Notification preferences saved'})
    else:
        return jsonify({'success': False, 'error': 'Failed to save preferences'}), 500

@settings_bp.route('/api/theme', methods=['POST'])
@login_required
def api_update_theme():
    data = request.get_json()
    theme = data.get('theme')
    
    if theme not in ['light', 'dark', 'system']:
        return jsonify({'error': 'Invalid theme'}), 400
    
    # Local mode - settings not persisted
    logger.debug(f"Theme changed to {theme} via API (not persisted in local mode)")
    
    return jsonify({'success': True, 'theme': theme})

@settings_bp.route('/api/notifications', methods=['POST'])
@login_required
def api_update_notifications():
    data = request.get_json() or {}

    notification_keys = [
        'absent_scheduled', 'absent_unscheduled',
        'new_assignment', 'due_24h', 'due_3d', 'overdue_warning'
    ]

    for key in notification_keys:
        if key in data:
            current_user.notification_preferences[key] = bool(data[key])

    if current_user.save():
        return jsonify({'success': True})
    else:
        return jsonify({'success': False, 'error': 'Failed to save preferences'}), 500



@settings_bp.route('/lms-credentials', methods=['POST'])
@login_required
def update_lms_credentials():
    """Update LMS credentials for the active account"""
    try:
        lms_username = request.form.get('lms_username', '').strip()
        lms_password = request.form.get('lms_password', '').strip()
        
        if not lms_username or not lms_password:
            return jsonify({'error': 'LMS username and password are required.'}), 400
        
        active_account = current_user.get_active_account()
        if not active_account:
            return jsonify({'error': 'No active CMS account found.'}), 400
        
        # Test LMS credentials before saving
        auth_service = LMSAuthService()
        auth_result = auth_service.test_credentials(lms_username, lms_password)
        
        if not auth_result['success']:
            return jsonify({'error': f'LMS authentication failed: {auth_result["error"]}'}), 401
        
        # Save credentials if authentication successful
        active_account.set_lms_credentials(lms_username, lms_password)
        
        if current_user.save():
            return jsonify({
                'success': True,
                'message': 'LMS credentials updated successfully!'
            })
        else:
            return jsonify({'error': 'Failed to save credentials.'}), 500
            
    except Exception as e:
        logger.warning(f"Error updating LMS credentials: {e}")
        return jsonify({'error': f'Server error: {str(e)}'}), 500

@settings_bp.route('/lms-credentials/test', methods=['POST'])
@login_required
def test_lms_credentials():
    """Test LMS credentials without saving"""
    try:
        lms_username = request.form.get('lms_username', '').strip()
        lms_password = request.form.get('lms_password', '').strip()
        
        # If no credentials provided, try to test existing credentials
        if not lms_username or not lms_password:
            active_account = current_user.get_active_account()
            if active_account and active_account.has_lms_credentials():
                lms_username = active_account.lms_credentials.lms_username
                lms_password = active_account.get_lms_password()
                
                if not lms_password:
                    return jsonify({'error': 'Could not decrypt stored LMS password.'}), 400
            else:
                return jsonify({'error': 'LMS username and password are required.'}), 400
        
        # Test credentials
        auth_service = LMSAuthService()
        auth_result = auth_service.test_credentials(lms_username, lms_password)
        
        if auth_result['success']:
            return jsonify({
                'success': True,
                'message': 'LMS credentials are valid!',
                'student_info': auth_result.get('student_info', {})
            })
        else:
            return jsonify({
                'success': False,
                'error': auth_result['error']
            })
            
    except Exception as e:
        logger.warning(f"Error testing LMS credentials: {e}")
        return jsonify({'error': f'Server error: {str(e)}'}), 500

@settings_bp.route('/lms-credentials/remove', methods=['POST'])
@login_required
def remove_lms_credentials():
    """Remove LMS credentials from the active account"""
    try:
        active_account = current_user.get_active_account()
        if not active_account:
            return jsonify({'error': 'No active CMS account found.'}), 400
        
        # Clear LMS credentials
        active_account.lms_credentials = None
        
        if current_user.save():
            return jsonify({
                'success': True,
                'message': 'LMS credentials removed successfully!'
            })
        else:
            return jsonify({'error': 'Failed to remove credentials.'}), 500
            
    except Exception as e:
        logger.warning(f"Error removing LMS credentials: {e}")
        return jsonify({'error': f'Server error: {str(e)}'}), 500

@settings_bp.route('/lms-setup')
@login_required
def lms_setup_wizard():
    """Display LMS setup wizard for new users"""
    active_account = current_user.get_active_account()
    if not active_account:
        flash('No active CMS account found. Please add a CMS account first.', 'error')
        return redirect(url_for('account.manage_accounts'))
    
    # Check if LMS credentials are already configured
    if active_account.has_lms_credentials():
        flash('LMS credentials are already configured for this account.', 'info')
        return redirect(url_for('settings.settings'))
    
    return render_template('lms_setup_wizard.html')

@settings_bp.route('/lms-setup/complete', methods=['POST'])
@login_required
def complete_lms_setup():
    """Complete the LMS setup wizard"""
    try:
        lms_username = request.form.get('lms_username', '').strip()
        lms_password = request.form.get('lms_password', '').strip()
        
        if not lms_username or not lms_password:
            return jsonify({'error': 'LMS username and password are required.'}), 400
        
        active_account = current_user.get_active_account()
        if not active_account:
            return jsonify({'error': 'No active CMS account found.'}), 400
        
        # Test LMS credentials before saving
        auth_service = LMSAuthService()
        auth_result = auth_service.test_credentials(lms_username, lms_password)
        
        if not auth_result['success']:
            return jsonify({'error': f'LMS authentication failed: {auth_result["error"]}'}), 401
        
        # Save credentials if authentication successful
        active_account.set_lms_credentials(lms_username, lms_password)
        
        if current_user.save():
            return jsonify({
                'success': True,
                'message': 'LMS setup completed successfully!',
                'student_info': auth_result.get('student_info', {}),
                'redirect_url': url_for('lms.assignment_monitor')
            })
        else:
            return jsonify({'error': 'Failed to save LMS credentials.'}), 500
            
    except Exception as e:
        logger.warning(f"Error completing LMS setup: {e}")
        return jsonify({'error': f'Server error: {str(e)}'}), 500

