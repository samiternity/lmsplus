from flask import Blueprint, current_app, render_template, request, redirect, url_for, flash, session, jsonify
from flask_login import login_user, logout_user, login_required, current_user
from app.models.account import User, CMSAccount, account_manager
from app.utils.enhanced_scraper import scraper_manager
from app.utils.data_manager import data_manager
from app.utils.session_auth import add_authenticated_account, init_authenticated_accounts, is_account_authenticated, clear_authenticated_accounts
from app.utils.turnstile import verify_turnstile
from app.utils.limiter import limiter, get_rate_limit_key
import threading
import logging
import re

logger = logging.getLogger(__name__)

auth_bp = Blueprint('auth', __name__)

ALLOWED_ENROLLMENT_REGEX = re.compile(r'^(0[1-9]|1[0-5])-\d{6}-\d{3}$')
KARACHI_ENROLLMENT_REGEX = ALLOWED_ENROLLMENT_REGEX  # Backwards compatibility alias

BAHRIA_CAMPUS_NAMES = {
    '1': 'Islamabad E-8 Campus',
    '2': 'Karachi Campus',
    '3': 'Lahore Campus',
    '4': 'NCMPR',
    '5': 'IPP (Karachi)',
    '6': 'Health Sciences (Karachi)',
    '7': 'PN School Of Logistics',
    '8': 'PN Nursing College',
    '9': 'Islamabad H-11 Campus',
    '10': 'Finishing School',
    '11': 'National School of Hydrography',
    '12': 'Health Sciences (Islamabad)',
    '13': 'BU College of Nursing',
    '14': 'ODL',
    '15': 'School for Underwater Medicine'
}

def get_institute_id_for_enrollment(enrollment: str, fallback: str = '2') -> str:
    """Resolve institute_id from Bahria enrollment prefix (01-15).
    Maps prefix XX- to str(int(XX)) for all Bahria institutes.
    """
    if not enrollment:
        return fallback
    clean = str(enrollment).strip()
    m = re.match(r'^(\d{2})-', clean)
    if m:
        try:
            prefix_num = int(m.group(1))
            if 1 <= prefix_num <= 15:
                return str(prefix_num)
        except ValueError:
            pass
    return fallback

def _fire_signup_webhook(user_obj, profile_data=None):
    """Fire a Discord webhook when a new user registers."""
    try:
        from app.services.discord_service import enqueue_webhook_log
        from datetime import datetime, timezone
        
        name_val = user_obj.username
        enrollment_val = "N/A"
        program_val = "N/A"
        campus_val = "N/A"
        
        if getattr(user_obj, 'cms_account', None):
            name_val = getattr(user_obj.cms_account, 'student_name', None) or getattr(user_obj.cms_account, 'fetched_name', None) or user_obj.username
            enrollment_val = user_obj.cms_account.cms_username
            try:
                if not profile_data:
                    from app.utils.data_manager import data_manager
                    profile_data = data_manager.load_account_data(user_obj.cms_account.account_id, 'profile')
                if profile_data:
                    name_val = profile_data.get('name') or name_val
                    program_val = profile_data.get('program') or 'N/A'
                    campus_val = profile_data.get('campus') or 'N/A'
                if (not campus_val or campus_val == 'N/A') and enrollment_val != 'N/A':
                    inst_id = get_institute_id_for_enrollment(enrollment_val)
                    campus_val = BAHRIA_CAMPUS_NAMES.get(inst_id, 'Karachi Campus')
            except Exception:
                pass

        enqueue_webhook_log(
            title="New User Registration",
            color=0x2ecc71,
            fields=[
                {'name': 'Name', 'value': str(name_val), 'inline': True},
                {'name': 'Enrollment', 'value': str(enrollment_val), 'inline': True},
                {'name': 'Program', 'value': str(program_val), 'inline': True},
                {'name': 'Campus', 'value': str(campus_val), 'inline': True}
            ],
            footer=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        )
    except Exception as e:
        import logging
        logging.getLogger(__name__).error(f"Failed to enqueue signup webhook: {e}")



# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _set_active_account_in_session(account_id, cms_username):
    """Store the active account in the Flask session (browser-side only)."""
    session['active_account_id'] = account_id
    session['cms_username'] = cms_username
    session.modified = True


def _get_active_account_id_from_session():
    """Return the active account ID from the session, falling back to the
    user's stored default if the session key is missing."""
    account_id = session.get('active_account_id')
    if account_id and current_user.is_authenticated:
        if account_id in current_user.cms_accounts:
            return account_id
    # Fall back to the user's stored default
    if current_user.is_authenticated:
        return current_user.active_account_id
    return None


# ---------------------------------------------------------------------------
# Login
# ---------------------------------------------------------------------------

def _resume_connector_consent(fallback):
    request_id = session.pop('mcp_consent_request', None)
    if request_id and current_app.extensions.get('lms_mcp'):
        return url_for('assignment_connector.consent', request=request_id)
    return fallback

@auth_bp.route('/login', methods=['GET', 'POST'])
@limiter.limit("5 per minute", key_func=lambda: request.form.get("username") or get_rate_limit_key())
@limiter.limit("30 per minute")
def login():
    # Already authenticated — just redirect
    if current_user.is_authenticated:
        target = _resume_connector_consent(url_for('main.dashboard'))
        if request.method == 'POST':
            return jsonify({'success': True, 'redirect_url': target})
        return redirect(target)

    if request.method == 'POST':
        # Honeypot check (bots fill hidden field)
        if request.form.get('website_url'):
            return jsonify({'error': 'Security verification failed.'}), 400

        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')

        if not username or not password:
            return jsonify({'error': 'Please fill in all fields.'}), 400

        if not ALLOWED_ENROLLMENT_REGEX.match(username):
            return jsonify({'error': 'Invalid enrollment format. Must follow Bahria format: XX-XXXXXX-XXX (prefix 01-15).'}), 400

        if current_app.config['APP_ENV'] == 'local':
            from app.utils.proxy_transport import proxy_workers
            if not proxy_workers():
                return jsonify({'error': 'Configure your Worker URL and proxy key before signing in.'}), 503

        # Turnstile verification
        turnstile_token = request.form.get('cf-turnstile-response')
        client_ip = request.headers.get('CF-Connecting-IP') or request.remote_addr
        if not verify_turnstile(turnstile_token, remote_ip=client_ip, action='login'):
            return jsonify({'error': 'Security verification failed. Please refresh and try again.'}), 400

        user_obj = User.get(username)

        if user_obj and user_obj.check_password(password):
            # User exists and password matches
            # ALWAYS update CMS password to ensure it's current
            if user_obj.cms_account:
                # Update the CMS password in case it changed
                user_obj.cms_account.cms_password = password
                user_obj.save()
            
            # Always remember — session survives browser restarts
            login_user(user_obj, remember=True)
            session.permanent = True  # ensure 30-day Max-Age is set on __session cookie

            # SINGLE ACCOUNT MODE: Set account ID in session
            if user_obj.cms_account and user_obj.account_id:
                session['account_id'] = user_obj.account_id
                session['cms_username'] = user_obj.cms_account.cms_username
                # Mark account as authenticated for LMS access
                add_authenticated_account(user_obj.account_id)
            
            # MULTI-ACCOUNT MODE (disabled)
            # # Initialize session-based account tracking
            # init_authenticated_accounts()
            #
            # # Restore the last-used active account into the session
            # if user_obj.active_account_id and user_obj.active_account_id in user_obj.cms_accounts:
            #     acc = user_obj.cms_accounts[user_obj.active_account_id]
            #     _set_active_account_in_session(user_obj.active_account_id, acc.cms_username)
            #     # Add to authenticated accounts list
            account_id = user_obj.account_id
            if not user_obj.cms_account:
                redirect_target = url_for('main.dashboard')
            else:
                existing_profile = data_manager.load_account_data(account_id, 'profile') if account_id else None
                if not existing_profile or existing_profile.get('sync_failed'):
                    redirect_target = url_for('main.sync_screen')
                else:
                    redirect_target = url_for('main.dashboard')

            return jsonify({'success': True, 'redirect_url': _resume_connector_consent(redirect_target)})

        # New user or first-time CMS login
        if not user_obj:
            if User.get_total_count() >= 500:
                return jsonify({
                    'error': "We've hit our launch capacity! New signups are temporarily paused while we monitor performance.",
                    'action': {
                        'text': 'Get notified on Discord →',
                        'url': 'https://discord.gg/DVUq7gaM3E'
                    }
                }), 403

        # Check blacklist before attempting authentication
        from app.services.cms_blacklist import get_blacklist_service
        blacklist_service = get_blacklist_service()
        is_allowed, reason = blacklist_service.check_access(username)
        if not is_allowed:
            logger.warning(f"Login attempt blocked for blacklisted user: {username}")
            return jsonify({'error': reason}), 403

        # Step 1: Attempt Single-Flight Atomic Edge Verification & Scrape
        target_institute = get_institute_id_for_enrollment(
            username,
            fallback=(getattr(user_obj.cms_account, 'institute_id', None) if user_obj and getattr(user_obj, 'cms_account', None) else None) or request.form.get('institute_id') or '2'
        )

        temp_account = CMSAccount(
            account_id='temp_auth',
            cms_username=username,
            cms_password=password,
            institute_id=target_institute
        )
        scraper = scraper_manager.get_scraper(temp_account)
        atomic_bundle = scraper.scrape_full_sync_atomic(username, password, institute_id=target_institute)
        scraper_manager.cleanup_scraper('temp_auth')

        cms_auth_result = None
        if atomic_bundle and atomic_bundle.get('cms_down'):
            return jsonify({
                'error': 'Bahria University CMS server is currently down (HTTP 500 Runtime Error). This is an issue on the university portal, not LMS+. Please try again once university IT restores it.',
                'is_cms_down': True,
                'action': {
                    'text': 'Verify Bahria CMS Status ->',
                    'url': 'https://cms.bahria.edu.pk/Logins/Student/Login.aspx'
                }
            }), 503
        elif atomic_bundle and atomic_bundle.get('session_lost'):
            return jsonify({
                'error': 'CMS connection dropped. Please try again.',
                'is_session_lost': True
            }), 502
        elif atomic_bundle and atomic_bundle.get('auth_failed'):
            return jsonify({'error': 'Invalid credentials. Please check your CMS username and password.'}), 401
        elif atomic_bundle and atomic_bundle.get('success') and atomic_bundle.get('html'):
            cms_auth_result = True
        else:
            logger.warning(f"Atomic macro inconclusive for {username}, falling back to standard login")
            cms_auth_result = authenticate_with_cms(username, password, user_obj=user_obj, institute_id=target_institute)

        is_new_user = False

        if cms_auth_result is True:
            if not user_obj:
                is_new_user = True
                user_obj = User.create(
                    username=username,
                    email=f"{username}@student.bahria.edu.pk",
                    password=password
                )
                if not user_obj:
                    return jsonify({'error': 'Failed to create user account.'}), 500
            else:
                from werkzeug.security import generate_password_hash
                user_obj.password_hash = generate_password_hash(password)

            # SINGLE ACCOUNT MODE: Set the single CMS account
            account_uuid = user_obj.set_cms_account(
                cms_username=username,
                cms_password=password,
                institute_id=target_institute
            )
            
            # Save user with CMS account BEFORE logging in
            if not user_obj.save():
                return jsonify({'error': 'Failed to save CMS account.'}), 500
            
            # Handle bundle ingestion and background scraping
            extracted_profile = None
            if atomic_bundle and atomic_bundle.get('success') and atomic_bundle.get('html'):
                if is_new_user:
                    logger.info(f"Starting staged atomic onboarding for new user {username}")
                    # Pre-extract student profile info for instant Discord webhook & user model
                    prof_html = atomic_bundle.get('html', {}).get('profile') if isinstance(atomic_bundle.get('html'), dict) else None
                    if prof_html:
                        try:
                            from bs4 import BeautifulSoup
                            scraper = scraper_manager.get_scraper(user_obj.cms_account)
                            soup = BeautifulSoup(prof_html, 'html.parser')
                            extracted_profile = scraper.parse_profile(soup)
                            if extracted_profile and extracted_profile.get('name'):
                                user_obj.cms_account.update_student_name(extracted_profile['name'])
                                user_obj.save()
                        except Exception as pe:
                            logger.warning(f"Error pre-extracting profile for webhook: {pe}")

                    # Spawn staged background ingestion for /sync animated stepper
                    scraper_manager.start_staged_atomic_ingestion(user_obj.cms_account, atomic_bundle)
                else:
                    logger.info(f"Ingesting pre-fetched atomic bundle directly for returning user {username}")
                    scraper_manager.ingest_atomic_bundle(
                        user_obj.cms_account,
                        atomic_bundle,
                        data_types=['profile', 'timetable', 'attendance', 'community_service', 'gpa']
                    )
                    user_obj.save()
            else:
                # Trigger standard background scrape only if atomic macro was unavailable
                try:
                    logger.debug(f"Starting initial data scrape for user {username}")
                    scraper_manager.scrape_account_data(
                        user_obj.cms_account,
                        data_types=['profile', 'timetable', 'attendance', 'community_service', 'gpa', 'assignments']
                    )
                except Exception as e:
                    logger.warning(f"Error during initial data scrape: {e}")
            
            if is_new_user:
                _fire_signup_webhook(user_obj, profile_data=extracted_profile)
            
            # Log in the user
            login_user(user_obj, remember=True)
            session.permanent = True  # ensure 30-day Max-Age is set on __session cookie

            # SINGLE ACCOUNT MODE: Set account ID in session
            session['account_id'] = account_uuid
            session['cms_username'] = username
            # Mark account as authenticated for LMS access
            add_authenticated_account(account_uuid)

            if is_new_user:
                redirect_target = url_for('main.sync_screen')
            else:
                existing_profile = data_manager.load_account_data(account_uuid, 'profile')
                if not existing_profile or existing_profile.get('sync_failed'):
                    redirect_target = url_for('main.sync_screen')
                else:
                    redirect_target = url_for('main.dashboard')

            return jsonify({'success': True, 'redirect_url': _resume_connector_consent(redirect_target)})

        elif cms_auth_result == "CMS_DOWN":
            return jsonify({
                'error': 'Bahria University CMS server is currently down (HTTP 500 Runtime Error). This is an issue on the university portal, not LMS+. Please try again once university IT restores it.',
                'is_cms_down': True,
                'action': {
                    'text': 'Verify Bahria CMS Status ->',
                    'url': 'https://cms.bahria.edu.pk/Logins/Student/Login.aspx'
                }
            }), 503
        elif cms_auth_result == "SESSION_LOST":
            return jsonify({
                'error': 'CMS connection dropped. Please try again.',
                'is_session_lost': True
            }), 502
        elif isinstance(cms_auth_result, str):
            return jsonify({'error': cms_auth_result}), 403
        else:
            return jsonify({'error': 'Invalid credentials. Please check your CMS username and password.'}), 401

    return render_template('auth/login.html')




# ---------------------------------------------------------------------------
# Add account (DISABLED - Multi-account feature temporarily disabled)
# ---------------------------------------------------------------------------

@auth_bp.route('/add_account', methods=['POST'])
@login_required
@limiter.limit("5 per minute")
def add_account():
    """DISABLED: Multi-account feature temporarily disabled for security reasons.
    
    To re-enable:
    1. Uncomment the implementation below
    2. Update User model to use cms_accounts
    3. Implement proper session isolation
    """
    return jsonify({
        'error': 'Multi-account feature is temporarily disabled for security reasons. '
                 'Please contact support if you need to manage multiple accounts.'
    }), 403
    
    # MULTI-ACCOUNT IMPLEMENTATION (disabled)
    # """Add a new CMS account to the current user without logging out."""
    # try:
    #     cms_username = request.form.get('cms_username', '').strip()
    #     cms_password = request.form.get('cms_password', '')
    #     display_name = request.form.get('display_name', '').strip()
    #
    #     if not cms_username or not cms_password:
    #         return jsonify({'error': 'CMS username and password are required.'}), 400
    #
    #     for account in current_user.cms_accounts.values():
    #         if account.cms_username == cms_username:
    #             return jsonify({'error': 'This CMS account is already added.'}), 400
    #
    #     if not authenticate_with_cms(cms_username, cms_password):
    #         return jsonify({'error': 'Invalid CMS credentials. Please check your username and password.'}), 401
    #
    #     account_id = current_user.add_cms_account(
    #         cms_username=cms_username,
    #         cms_password=cms_password,
    #         display_name=display_name or cms_username
    #     )
    #
    #     if current_user.save():
    #         # Add account to session's authenticated accounts list
    #         add_authenticated_account(account_id)
    #         
    #         return jsonify({
    #             'success': True,
    #             'message': 'CMS account added successfully!',
    #             'account_id': account_id
    #         })
    #     else:
    #         return jsonify({'error': 'Failed to save account to database.'}), 500
    #
    # except Exception as e:
    #     logger.warning(f"Error in add_account: {e}")
    #     return jsonify({'error': f'Server error: {str(e)}'}), 500


# ---------------------------------------------------------------------------
# Switch account (DISABLED - Multi-account feature temporarily disabled)
# ---------------------------------------------------------------------------

@auth_bp.route('/switch_account', methods=['POST'])
@login_required
@limiter.limit("5 per minute")
def switch_account():
    """DISABLED: Multi-account feature temporarily disabled for security reasons."""
    return jsonify({
        'error': 'Multi-account feature is temporarily disabled. '
                 'You can only use one account per user.'
    }), 403
    
    # MULTI-ACCOUNT IMPLEMENTATION (disabled)
    # """Switch the active CMS account.
    # 
    # Requires the account to be authenticated in the current session.
    # If not authenticated, requires CMS credentials for re-authentication.
    # """
    # account_id = request.form.get('account_id')
    #
    # if not account_id:
    #     return jsonify({'error': 'Account ID is required.'}), 400
    #
    # if account_id not in current_user.cms_accounts:
    #     return jsonify({'error': 'Account not found.'}), 404
    #
    # active_account = current_user.cms_accounts[account_id]
    #
    # # Check if account is authenticated in this session
    # if not is_account_authenticated(account_id):
    #     # Account not authenticated - require CMS credentials
    #     cms_password = request.form.get('cms_password')
    #     
    #     if not cms_password:
    #         return jsonify({
    #             'error': 'Account not authenticated in this session',
    #             'requires_auth': True,
    #             'account_id': account_id
    #         }), 403
    #     
    #     # Authenticate with CMS
    #     auth_result = authenticate_with_cms(active_account.cms_username, cms_password)
    #
    #     if not auth_result:
    #         return jsonify({'error': 'Invalid CMS credentials'}), 401
    #     
    #     # Add to session's authenticated accounts
    #     add_authenticated_account(account_id)
    #
    # # Update session only — no DB write, instant switch
    # _set_active_account_in_session(account_id, active_account.cms_username)
    #
    # # Also persist as the user's default so it survives a fresh login
    # current_user.active_account_id = account_id
    # current_user.save()
    #
    # return jsonify({
    #     'success': True,
    #     'message': 'Account switched successfully!',
    #     'active_account': {
    #         'account_id': account_id,
    #         'cms_username': active_account.cms_username,
    #         'display_name': active_account.student_name or active_account.display_name or active_account.cms_username
    #     }
    # })


# ---------------------------------------------------------------------------
# Logout — one account only
# ---------------------------------------------------------------------------

@auth_bp.route('/logout_account', methods=['POST'])
@login_required
def logout_account():
    """Sign out of one specific CMS account.

    - Removes the account from the user's profile (credentials + data).
    - If other accounts remain, switches to the next one automatically.
    - If it was the last account, performs a full session logout.
    - Does NOT delete the user record — only the account entry and its data.
    """
    account_id = request.form.get('account_id') or session.get('active_account_id')

    if not account_id:
        return jsonify({'error': 'No account specified.'}), 400

    if account_id not in current_user.cms_accounts:
        return jsonify({'error': 'Account not found.'}), 404

    is_last = len(current_user.cms_accounts) <= 1

    # Remove the account entry from the user object
    current_user.remove_cms_account(account_id)

    # Delete that account's scraped data (attendance, timetable, LMS cache, etc.)
    data_manager.delete_account_data(account_id)

    if is_last:
        # No accounts left — full session logout (but keep user record)
        session.clear()
        logout_user()
        return jsonify({
            'success': True,
            'message': 'Signed out.',
            'redirect_url': url_for('auth.login')
        })

    # Switch session to the next available account
    next_account_id = list(current_user.cms_accounts.keys())[0]
    next_account = current_user.cms_accounts[next_account_id]
    current_user.active_account_id = next_account_id
    _set_active_account_in_session(next_account_id, next_account.cms_username)
    current_user.save()

    return jsonify({
        'success': True,
        'message': 'Account removed. Switched to next account.',
        'redirect_url': url_for('main.dashboard'),
        'active_account': {
            'account_id': next_account_id,
            'cms_username': next_account.cms_username,
            'display_name': next_account.student_name or next_account.display_name or next_account.cms_username
        }
    })


# ---------------------------------------------------------------------------
# Logout — clear session and log out
# ---------------------------------------------------------------------------

@auth_bp.route('/logout')
@login_required
@limiter.limit("5 per minute")
def logout():
    """Sign out and clear session. User can log back in without re-scraping data."""
    session.clear()
    logout_user()
    return redirect(url_for('auth.login'))
    
    # MULTI-ACCOUNT MODE (disabled)
    # """Sign out of all accounts. Clears the session but keeps user data in DB
    # so the user can log back in without re-scraping everything."""
    # clear_authenticated_accounts()
    # session.clear()
    # logout_user()
    # return redirect(url_for('auth.login'))


# ---------------------------------------------------------------------------
# Legacy route kept for backwards compatibility
# ---------------------------------------------------------------------------

@auth_bp.route('/logout_current_account', methods=['POST'])
@login_required
def logout_current_account():
    """Backwards-compatible alias for logout_account."""
    return logout_account()


# ---------------------------------------------------------------------------
# Remove account (DISABLED - Multi-account feature temporarily disabled)
# ---------------------------------------------------------------------------

@auth_bp.route('/remove_account', methods=['POST'])
@login_required
def remove_account():
    """DISABLED: Multi-account feature temporarily disabled for security reasons."""
    return jsonify({
        'error': 'Multi-account feature is temporarily disabled. '
                 'To remove your account, please use the logout function.'
    }), 403
    
    # MULTI-ACCOUNT IMPLEMENTATION (disabled)
    # """Remove a CMS account from the current user."""
    # account_id = request.form.get('account_id')
    #
    # if not account_id:
    #     return jsonify({'error': 'Account ID is required.'}), 400
    #
    # if account_id not in current_user.cms_accounts:
    #     return jsonify({'error': 'Account not found.'}), 404
    #
    # # Don't allow removing the last account
    # if len(current_user.cms_accounts) == 1:
    #     return jsonify({'error': 'Cannot remove the last account. Use logout instead.'}), 400
    #
    # # Remove from session's authenticated accounts
    # remove_authenticated_account(account_id)
    #
    # # Remove from user object
    # current_user.remove_cms_account(account_id)
    # current_user.save()
    #
    # # Delete account data directory
    # account_manager.delete_account_directory(account_id)
    #
    # return jsonify({'success': True, 'message': 'Account removed successfully'})
# ---------------------------------------------------------------------------
# DUPLICATE remove_account REMOVED - Using the disabled version above
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Migration helpers (unchanged)
# ---------------------------------------------------------------------------

@auth_bp.route('/migrate_data', methods=['POST'])
@login_required
def migrate_data():
    try:
        from app.utils.migration import migration_manager
        success, log = migration_manager.run_full_migration()
        return jsonify({
            'success': success,
            'message': 'Migration completed' if success else 'Migration failed',
            'log': log
        })
    except Exception as e:
        return jsonify({'success': False, 'message': f'Migration error: {str(e)}', 'log': []}), 500


@auth_bp.route('/check_migration')
@login_required
def check_migration():
    try:
        from app.utils.migration import migration_manager
        needs_migration = migration_manager.check_migration_needed()
        return jsonify({
            'needs_migration': needs_migration,
            'needs_account_setup': len(current_user.cms_accounts) == 0,
            'has_cms_accounts': len(current_user.cms_accounts) > 0
        })
    except Exception as e:
        return jsonify({
            'needs_migration': False,
            'needs_account_setup': True,
            'has_cms_accounts': False,
            'error': str(e)
        })


# ---------------------------------------------------------------------------
# CMS authentication helper
# ---------------------------------------------------------------------------

def authenticate_with_cms(cms_username, cms_password, user_obj=None, institute_id=None):
    """Authenticate against Bahria University CMS."""
    from app.services.cms_blacklist import get_blacklist_service
    import logging
    logger = logging.getLogger(__name__)

    if not institute_id or (str(institute_id).strip() == '2' and cms_username and not str(cms_username).strip().startswith('02-')):
        institute_id = get_institute_id_for_enrollment(cms_username)

    blacklist_service = get_blacklist_service()
    is_allowed, reason = blacklist_service.check_access(cms_username)
    if not is_allowed:
        logger.warning(f"Login attempt blocked for blacklisted user: {cms_username}")
        return reason

    try:
        logger.debug(f"Attempting to authenticate CMS user: {cms_username}")
        temp_account = CMSAccount(
            account_id='temp_auth',
            cms_username=cms_username,
            cms_password=cms_password,
            institute_id=institute_id
        )
        scraper = scraper_manager.get_scraper(temp_account)
        login_result = scraper.login(cms_username, cms_password)
        is_cms_down = getattr(scraper, 'cms_down', False) or (login_result == "CMS_DOWN")
        is_session_lost = getattr(scraper, 'session_lost', False) or (login_result == "SESSION_LOST")
        logger.debug(f"CMS login result for {cms_username}: {login_result}, cms_down: {is_cms_down}, session_lost: {is_session_lost}")

        scraper_manager.cleanup_scraper('temp_auth')

        if login_result is True:
            return True
        elif is_cms_down:
            return "CMS_DOWN"
        elif is_session_lost:
            return "SESSION_LOST"
        else:
            return False

    except Exception as e:
        logger.warning(f"Error authenticating with CMS for {cms_username}: {e}")

        # Offline fallback — check stored password hash
        if user_obj:
            if getattr(user_obj, 'cms_password', None) == cms_password:
                logger.warning(f"[OFFLINE LOGIN] Using stored credentials for {cms_username}")
                return True
        else:
            stored_user = User.get(cms_username)
            if stored_user and stored_user.password_hash:
                from werkzeug.security import check_password_hash
                if check_password_hash(stored_user.password_hash, cms_password):
                    logger.debug(f"[OFFLINE LOGIN] Authenticated via synced hash for {cms_username}")
                    return True

        import traceback
        traceback.print_exc()
        return False
