import logging
logger = logging.getLogger(__name__)

from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify
from flask_login import login_required, current_user
from app.utils.enhanced_scraper import scraper_manager
from app.utils.data_manager import data_manager
from app.utils.session_auth import add_authenticated_account

account_bp = Blueprint('account', __name__)

# MULTI-ACCOUNT: Manage Accounts Page (disabled)
@account_bp.route('/manage-accounts')
@login_required
def manage_accounts():
    """DISABLED: Multi-account feature temporarily disabled.
    
    Redirects to dashboard since account management is no longer needed
    in single-account mode. Users can sign out from the profile dropdown.
    """
    from flask import redirect, url_for
    return redirect(url_for('main.dashboard'))
    
    # MULTI-ACCOUNT IMPLEMENTATION (disabled)
    # """Display account management page"""
    # return render_template('account_manager.html')

@account_bp.route('/add-account', methods=['GET', 'POST'])
@login_required
def add_account():
    """Add a new CMS account to the current user"""
    if request.method == 'GET':
        # Show the add account form
        return render_template('add_account.html')
    
    try:
        cms_username = request.form.get('cms_username')
        cms_password = request.form.get('cms_password')
        display_name = request.form.get('display_name')
        
        logger.debug(f"Add account request: username={cms_username}, display_name={display_name}")
        logger.debug(f"Current user: {current_user.username if current_user else 'None'}")
        logger.debug(f"Current user CMS accounts: {len(current_user.cms_accounts) if current_user else 'None'}")
        
        if not cms_username or not cms_password:
            logger.debug("Missing username or password")
            return jsonify({'error': 'CMS username and password are required.'}), 400

        from app.routes.auth import ALLOWED_ENROLLMENT_REGEX, get_institute_id_for_enrollment
        if not ALLOWED_ENROLLMENT_REGEX.match(cms_username.strip()):
            return jsonify({'error': 'Invalid enrollment format. Must follow Bahria format: XX-XXXXXX-XXX (prefix 01-15).'}), 400
        
        # Check if account already exists
        for account in current_user.cms_accounts.values():
            if account.cms_username == cms_username:
                return jsonify({'error': 'This CMS account is already added.'}), 400
        
        target_institute = request.form.get('institute_id')
        if not target_institute or (str(target_institute).strip() == '2' and cms_username and not str(cms_username).strip().startswith('02-')):
            target_institute = get_institute_id_for_enrollment(cms_username)

        # Verify CMS credentials
        from app.routes.auth import authenticate_with_cms
        logger.debug(f"Authenticating CMS credentials for {cms_username} at institute {target_institute}")
        auth_result = authenticate_with_cms(cms_username, cms_password, institute_id=target_institute)
        logger.debug(f"CMS authentication result: {auth_result}")
        
        if auth_result:
            logger.debug(f"Adding CMS account for user {current_user.username}")
            if hasattr(current_user, 'set_cms_account'):
                account_id = current_user.set_cms_account(
                    cms_username=cms_username,
                    cms_password=cms_password,
                    display_name=None,
                    institute_id=target_institute
                )
                new_account = current_user.get_active_account()
            else:
                account_id = current_user.add_cms_account(
                    cms_username=cms_username,
                    cms_password=cms_password,
                    display_name=None
                )
                new_account = current_user.cms_accounts.get(account_id)
            logger.debug(f"Account ID created: {account_id}")
            
            # Add account to session's authenticated accounts list
            add_authenticated_account(account_id)
            logger.debug(f"Account {account_id} added to session authenticated accounts")
            
            if current_user.save():
                logger.debug("User saved successfully")
                return jsonify({
                    'success': True,
                    'message': 'CMS account added successfully!',
                    'account_id': account_id
                })
            else:
                logger.warning("Failed to save user")
                return jsonify({'error': 'Failed to save account to database.'}), 500
        else:
            logger.warning("CMS authentication failed")
            return jsonify({'error': 'Invalid CMS credentials. Please check your username and password.'}), 401
            
    except Exception as e:
        logger.warning(f"Error in add_account route: {e}")
        return jsonify({'error': f'Server error: {str(e)}'}), 500

@account_bp.route('/switch-account', methods=['POST'])
@login_required
def switch_account():
    """Switch the active CMS account and automatically refresh data"""
    try:
        account_id = request.form.get('account_id')
        
        if not account_id:
            return jsonify({'error': 'Account ID is required.'}), 400
        
        if current_user.set_active_account(account_id):
            if current_user.save():
                active_account = current_user.get_active_account()
                
                # Automatically start background data refresh for the switched account
                try:
                    logger.debug(f"Starting automatic data refresh for switched account: {active_account.cms_username}")
                    scraper_manager.scrape_account_data(active_account)
                except Exception as e:
                    logger.warning(f"Error starting background refresh: {e}")
                
                return jsonify({
                    'success': True,
                    'message': 'Account switched successfully! Data is being refreshed in the background.',
                    'active_account': {
                        'account_id': account_id,
                        'cms_username': active_account.cms_username,
                        'display_name': active_account.student_name or active_account.display_name
                    }
                })
            else:
                return jsonify({'error': 'Failed to save changes.'}), 500
        else:
            return jsonify({'error': 'Account not found.'}), 404
            
    except Exception as e:
        logger.warning(f"Error switching account: {e}")
        return jsonify({'error': f'Server error: {str(e)}'}), 500

@account_bp.route('/remove-account', methods=['POST'])
@login_required
def remove_account():
    """Remove a CMS account from the current user"""
    try:
        account_id = request.form.get('account_id')
        user_id = current_user.id
        
        logger.debug(f"[REMOVE_ACCOUNT] Request to remove account: {account_id}")
        logger.debug(f"[REMOVE_ACCOUNT] User: {user_id}")
        
        if not account_id:
            logger.warning(f"[REMOVE_ACCOUNT] No account ID provided")
            return jsonify({'error': 'Account ID is required.'}), 400
        
        account_count = len(current_user.cms_accounts)
        logger.debug(f"[REMOVE_ACCOUNT] User has {account_count} accounts")
        
        if account_count <= 1:
            logger.warning(f"[REMOVE_ACCOUNT] Cannot remove last account")
            return jsonify({'error': 'Cannot remove the last CMS account.'}), 400
        
        if current_user.remove_cms_account(account_id):
            logger.debug(f"[REMOVE_ACCOUNT] Account {account_id} removed from user object")
            
            # Clean up account data
            logger.debug(f"[REMOVE_ACCOUNT] Deleting data for account: {account_id}")
            data_deletion_result = data_manager.delete_account_data(account_id)
            
            if data_deletion_result:
                logger.debug(f"[REMOVE_ACCOUNT] Account data deleted successfully")
            else:
                logger.warning(f"[REMOVE_ACCOUNT] Account data deletion may have failed")
            
            if current_user.save():
                logger.debug(f"[REMOVE_ACCOUNT] User data saved successfully")
                logger.debug(f"[REMOVE_ACCOUNT] Account removal completed for {account_id}")
                return jsonify({'success': True, 'message': 'Account removed successfully!'})
            else:
                logger.warning(f"[REMOVE_ACCOUNT] Failed to save user data")
                return jsonify({'error': 'Failed to save changes.'}), 500
        else:
            logger.warning(f"[REMOVE_ACCOUNT] Account {account_id} not found in user object")
            return jsonify({'error': 'Account not found.'}), 404
            
    except Exception as e:
        logger.warning(f"[REMOVE_ACCOUNT] Error removing account: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'error': f'Server error: {str(e)}'}), 500



@account_bp.route('/account-status/<account_id>')
@login_required
def account_status(account_id):
    """Get status information for an account"""
    try:
        account = current_user.cms_accounts.get(account_id)
        if not account:
            return jsonify({'error': 'Account not found.'}), 404
        
        # Check if account has recent data
        data_types = ['attendance', 'timetable', 'gpa', 'community_service']
        status = {
            'account_id': account_id,
            'cms_username': account.cms_username,
            'display_name': account.display_name,
            'is_active': account_id == current_user.active_account_id,
            'data_status': {}
        }
        
        for data_type in data_types:
            try:
                data = data_manager.load_account_data(account_id, data_type)
                status['data_status'][data_type] = {
                    'available': data is not None,
                    'last_updated': data.get('last_updated') if data else None
                }
            except:
                status['data_status'][data_type] = {
                    'available': False,
                    'last_updated': None
                }
        
        return jsonify(status)
        
    except Exception as e:
        logger.warning(f"Error getting account status: {e}")
        return jsonify({'error': f'Server error: {str(e)}'}), 500

@account_bp.route('/test-add', methods=['POST'])
@login_required
def test_add_account():
    """Test route to add account without CMS verification"""
    try:
        cms_username = request.form.get('cms_username', 'test123')
        cms_password = request.form.get('cms_password', 'test123')
        display_name = request.form.get('display_name', 'Test Account')
        
        logger.debug(f"Test add account: {cms_username}")
        
        # Check if account already exists
        for account in current_user.cms_accounts.values():
            if account.cms_username == cms_username:
                return jsonify({'error': 'This CMS account is already added.'}), 400
        
        # Add account without CMS verification
        account_id = current_user.add_cms_account(
            cms_username=cms_username,
            cms_password=cms_password,
            display_name=display_name
        )
        
        logger.debug(f"Account created with ID: {account_id}")
        
        if current_user.save():
            logger.debug("User saved successfully")
            return jsonify({
                'success': True,
                'message': 'Test account added successfully!',
                'account_id': account_id
            })
        else:
            logger.warning("Failed to save user")
            return jsonify({'error': 'Failed to save account to database.'}), 500
            
    except Exception as e:
        logger.warning(f"Error in test add account: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'error': f'Server error: {str(e)}'}), 500

import os

@account_bp.route('/debug/clear-all-data', methods=['POST'])
@login_required
def debug_clear_all_data():
    """DEBUG ONLY: Clear all user data from the system"""
    admin_secret = os.environ.get('ADMIN_SECRET')
    if not admin_secret or request.headers.get('X-Admin-Secret') != admin_secret:
        return jsonify({'error': 'Unauthorized'}), 403
        
    try:
        from app.models.account import account_manager
        account_manager.clear_all_data()
        return jsonify({'success': True, 'message': 'All data cleared'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500