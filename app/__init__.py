from flask import Flask, request, jsonify, flash, redirect, url_for
from flask_login import LoginManager
import os
from secrets import token_hex
import logging
from app.config import load_environment, get_config

# Load environment configuration (.env + .env.<APP_ENV>)
load_environment()

login_manager = LoginManager()


def create_app():
    app = Flask(__name__)

    @app.get('/favicon.ico')
    def site_icon():
        return app.send_static_file('logo-transparent.png')
    
    from werkzeug.middleware.proxy_fix import ProxyFix
    app.wsgi_app = ProxyFix(
        app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1
    )
    
    # Configuration
    config = get_config()
    if config.is_local and os.environ.get('K_SERVICE'):
        raise RuntimeError("Local mode is restricted to local development.")
    secret_key = os.environ.get('SECRET_KEY')
    if not secret_key and (config.is_production or os.environ.get('K_SERVICE')):
        raise RuntimeError("Configure SECRET_KEY for hosted deployments.")
    app.config['SECRET_KEY'] = secret_key or token_hex(32)

    is_production = config.is_production
    app.config['APP_ENV'] = config.env_name
    app.config['IS_PRODUCTION'] = is_production
    app.config['APP_URL'] = config.app_url
    from app.services.testing_access import configure_testing_access
    configure_testing_access(app, config)
    app.config['USE_FIRESTORE'] = config.use_firestore
    app.config['TURNSTILE_SITE_KEY'] = config.turnstile_site_key
    app.config['TURNSTILE_SECRET_KEY'] = config.turnstile_secret_key
    app.config['GA4_MEASUREMENT_ID'] = '' if config.is_local else config.ga4_measurement_id
    app.config['GA4_USER_ID_SECRET'] = config.ga4_user_id_secret

    from app.utils.analytics import register_analytics
    register_analytics(app)

    # Initialize CSRF Protection
    from flask_wtf.csrf import CSRFProtect
    csrf = CSRFProtect()
    csrf.init_app(app)

    # Initialize Rate Limiter
    from app.utils.limiter import limiter
    limiter.init_app(app)
    
    # Custom 429 Error Handler
    from flask import request, jsonify, render_template
    @app.errorhandler(429)
    def ratelimit_handler(e):
        app.logger.warning(f"Rate limit exceeded: {e.description} - Path: {request.path}")
        if request.path.startswith('/api/') or request.path.startswith('/lms/api/') or request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return jsonify(error="Too many requests. Please try again later.", details=str(e.description)), 429
        return render_template('errors/429.html', description=str(e.description)), 429

    # Configure root logging level
    log_level = logging.WARNING if is_production else logging.DEBUG
    logging.basicConfig(level=log_level, format='%(asctime)s [%(levelname)s] %(name)s: %(message)s')
    
    # Always silence noisy third-party libraries in production
    if is_production:
        logging.getLogger('urllib3').setLevel(logging.WARNING)
        logging.getLogger('werkzeug').setLevel(logging.WARNING)
        logging.getLogger('google.auth').setLevel(logging.WARNING)

    # Session cookie config.
    # Firebase Hosting ONLY forwards cookies named '__session' — all others are
    # stripped by the CDN.  We therefore use a single permanent '__session' cookie
    # for everything (auth + active-account state).  Making the session permanent
    # with a 30-day lifetime means the browser keeps it across restarts without
    # needing a separate remember-me cookie.
    app.config['SESSION_COOKIE_NAME'] = '__session'
    app.config['SESSION_COOKIE_SECURE'] = is_production or bool(os.environ.get('K_SERVICE'))
    app.config['SESSION_COOKIE_HTTPONLY'] = True
    app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'

    # Make the session permanent so it survives browser restarts.
    # Flask will set a Max-Age / Expires on the cookie equal to
    # PERMANENT_SESSION_LIFETIME (30 days).
    app.config['SESSION_PERMANENT'] = True
    app.config['PERMANENT_SESSION_LIFETIME'] = 30 * 24 * 60 * 60  # 30 days in seconds

    # LMS-related configuration
    app.config['LMS_BASE_URL'] = os.environ.get('LMS_BASE_URL') or 'https://lms.bahria.edu.pk'
    app.config['LMS_CACHE_TIMEOUT'] = int(os.environ.get('LMS_CACHE_TIMEOUT', '3600'))  # 1 hour default
    app.config['LMS_REQUEST_TIMEOUT'] = int(os.environ.get('LMS_REQUEST_TIMEOUT', '30'))  # 30 seconds
    app.config['LMS_MAX_FILE_SIZE'] = int(os.environ.get('LMS_MAX_FILE_SIZE', str(50 * 1024 * 1024)))  # 50MB
    app.config['LMS_RETRY_ATTEMPTS'] = int(os.environ.get('LMS_RETRY_ATTEMPTS', '3'))
    
    # Cache configuration for pending assignments widget
    app.config['LMS_CACHE_DIR'] = os.environ.get('LMS_CACHE_DIR', 'app/data/cache')
    app.config['LMS_CACHE_MAX_ENTRIES'] = int(os.environ.get('LMS_CACHE_MAX_ENTRIES', '1000'))
    app.config['LMS_CACHE_TTL_MINUTES'] = int(os.environ.get('LMS_CACHE_TTL_MINUTES', '30'))
    
    # Background service configuration
    app.config['LMS_BACKGROUND_WORKERS'] = int(os.environ.get('LMS_BACKGROUND_WORKERS', '3'))
    app.config['LMS_BACKGROUND_MAX_RETRIES'] = int(os.environ.get('LMS_BACKGROUND_MAX_RETRIES', '2'))
    app.config['LMS_BACKGROUND_TASK_TIMEOUT'] = int(os.environ.get('LMS_BACKGROUND_TASK_TIMEOUT', '300'))
    
    # Feature flags
    app.config['LMS_ENABLE_DISK_CACHE'] = os.environ.get('LMS_ENABLE_DISK_CACHE', 'true').lower() == 'true'
    app.config['LMS_ENABLE_STALE_WHILE_REVALIDATE'] = os.environ.get('LMS_ENABLE_STALE_WHILE_REVALIDATE', 'true').lower() == 'true'
    app.config['LMS_ENABLE_AUTO_REFRESH'] = os.environ.get('LMS_ENABLE_AUTO_REFRESH', 'true').lower() == 'true'
    
    # Initialize extensions
    login_manager.init_app(app)
    login_manager.login_view = 'auth.login'
    login_manager.login_message = 'Please log in to access this page.'
    login_manager.login_message_category = 'info'

    @login_manager.unauthorized_handler
    def unauthorized():
        if request.path.startswith(('/api/', '/lms/api/')) or request.is_json:
            resp = jsonify({'success': False, 'error': 'Authentication required. Please log in.'})
            resp.status_code = 401
            return resp
        flash(login_manager.login_message, login_manager.login_message_category)
        return redirect(url_for(login_manager.login_view, next=request.url))
    
    # Register blueprints
    from app.routes.auth import auth_bp
    from app.routes.main import main_bp
    from app.routes.timetable import timetable_bp
    from app.routes.attendance import attendance_bp
    from app.routes.gpa import gpa_bp
    from app.routes.community import community_bp
    from app.routes.settings import settings_bp
    from app.routes.account import account_bp
    from app.routes.lms import lms_bp
    from app.routes.lms_dashboard_api import lms_dashboard_api_bp
    from app.routes.notifications import notifications_bp
    from app.routes.scheduler import scheduler_bp
    from app.routes.discord_oauth import discord_oauth_bp
    from app.routes.errors import errors_bp
    
    app.register_blueprint(auth_bp, url_prefix='/auth')
    app.register_blueprint(main_bp)
    app.register_blueprint(timetable_bp)
    app.register_blueprint(attendance_bp)
    app.register_blueprint(gpa_bp)
    app.register_blueprint(community_bp)
    app.register_blueprint(settings_bp)
    app.register_blueprint(account_bp, url_prefix='/account')
    app.register_blueprint(lms_bp, url_prefix='/lms')
    app.register_blueprint(lms_dashboard_api_bp)
    app.register_blueprint(notifications_bp)
    if not config.is_local:
        csrf.exempt(scheduler_bp)
        app.register_blueprint(scheduler_bp)
    app.register_blueprint(discord_oauth_bp, url_prefix='/discord')
    app.register_blueprint(errors_bp)

    from lms_mcp.core import ConnectorSettings
    connector_settings = None if config.is_local else ConnectorSettings.from_environment()
    if connector_settings:
        from lms_mcp.runtime import build_runtime
        from app.routes.assignment_connector import connector_bp
        app.extensions['lms_mcp'] = build_runtime(connector_settings, app.config['TESTING_LOGIN_ALLOWLIST'])
        app.register_blueprint(connector_bp)
    
    # Store cms_id in flask.g for logger
    @app.before_request
    def set_cms_id_in_context():
        from flask import g
        from flask_login import current_user
        if current_user.is_authenticated and current_user.cms_account:
            g.cms_id = current_user.cms_account.cms_username
        else:
            g.cms_id = None
    
    # Redirect Firebase default domains to custom domain in production
    @app.before_request
    def redirect_firebase_domains():
        if is_production:
            from flask import redirect
            host = (request.host or '').lower().split(':')[0]
            if host.endswith('.web.app') or host.endswith('.firebaseapp.com'):
                target_url = f"https://lmsplus.xyz{request.path}"
                if request.query_string:
                    target_url += f"?{request.query_string.decode('utf-8', errors='ignore')}"
                return redirect(target_url, code=301)
    
    # Exempt static files from rate limiting
    limiter.exempt(app.view_functions['static'])

    # Add context processor for templates
    @app.context_processor
    def utility_processor():
        from datetime import datetime
        from flask_login import current_user
        
        is_syncing = False
        if current_user.is_authenticated and getattr(current_user, 'cms_account', None):
            try:
                from app.utils.enhanced_scraper import scraper_manager
                account_id = current_user.cms_account.account_id
                sync_status = scraper_manager.get_sync_status(account_id)
                if sync_status and sync_status.get('is_syncing'):
                    is_syncing = True
            except Exception as e:
                app.logger.debug(f"Exception checking sync status: {e}")
                
        return dict(now=datetime.now, is_syncing=is_syncing, is_production=is_production)
    
    # Initialize LMS service and migrate existing accounts
    with app.app_context():
        from app.services.lms_service import LMSService
        from app.models.account import account_manager
        
        app.logger.info("[STARTUP] Checking for LMS data migration...")
        migration_success = account_manager.migrate_existing_accounts_for_lms()
        if migration_success:
            app.logger.info("[STARTUP] LMS data migration completed successfully")
        else:
            app.logger.warning("[STARTUP] LMS data migration encountered issues")
        
        # Verify backward compatibility
        app.logger.info("[STARTUP] Verifying LMS backward compatibility...")
        is_compatible, compatibility_report = account_manager.verify_lms_compatibility()
        if is_compatible:
            app.logger.info("[STARTUP] LMS backward compatibility verified")
        else:
            app.logger.warning("[STARTUP] LMS compatibility issues detected:")
            app.logger.info(compatibility_report)
        
        # Initialize LMS service
        app.logger.info("[STARTUP] Initializing LMS service...")
        app.lms_service = LMSService()
        app.logger.info("[STARTUP] LMS service initialized successfully")
        
        # Initialize LMS Cache Service
        app.logger.info("[STARTUP] Initializing LMS Cache Service...")
        try:
            from app.services.lms_cache_service import LMSCacheService, get_cache_service
            
            # Create cache directory if it doesn't exist
            cache_dir = app.config['LMS_CACHE_DIR']
            os.makedirs(cache_dir, exist_ok=True)
            
            # Initialize cache service with configuration
            cache_service = LMSCacheService(cache_dir=cache_dir)
            cache_service.max_memory_entries = app.config['LMS_CACHE_MAX_ENTRIES']
            cache_service.enable_disk_cache = app.config['LMS_ENABLE_DISK_CACHE']
            cache_service.enable_stale_while_revalidate = app.config['LMS_ENABLE_STALE_WHILE_REVALIDATE']
            
            app.cache_service = cache_service
            app.logger.info(f"[STARTUP] LMS Cache Service initialized (dir: {cache_dir}, max_entries: {app.config['LMS_CACHE_MAX_ENTRIES']})")
        except Exception as e:
            app.logger.warning(f"[STARTUP] Failed to initialize LMS Cache Service: {e}")
            app.cache_service = None
        
        # Initialize LMS Background Service
        app.logger.info("[STARTUP] Initializing LMS Background Service...")
        try:
            from app.services.lms_background_service import init_background_service, get_background_service
            
            # Initialize background service with LMS service dependency
            background_service = init_background_service(app.lms_service)
            background_service.max_workers = app.config['LMS_BACKGROUND_WORKERS']
            
            app.background_service = background_service
            app.logger.info(f"[STARTUP] LMS Background Service initialized (workers: {app.config['LMS_BACKGROUND_WORKERS']})")
        except Exception as e:
            app.logger.warning(f"[STARTUP] Failed to initialize LMS Background Service: {e}")
            app.background_service = None
        
        # Initialize dashboard API with services
        app.logger.info("[STARTUP] Initializing LMS Dashboard API...")
        try:
            from app.routes.lms_dashboard_api import init_dashboard_api
            init_dashboard_api(app)
            app.logger.info("[STARTUP] LMS Dashboard API initialized successfully")
        except Exception as e:
            app.logger.warning(f"[STARTUP] Failed to initialize LMS Dashboard API: {e}")
        
        if not config.is_local:
            # Initialize Notification Scheduler
            app.logger.info("[STARTUP] Initializing Notification Scheduler...")
            try:
                from app.services.notification_scheduler import init_notification_scheduler
                scheduler = init_notification_scheduler(app)
                app.notification_scheduler = scheduler
                app.logger.info("[STARTUP] Notification Scheduler initialized successfully")
                app.logger.info("[STARTUP] Cloud Scheduler will trigger micro-batches exactly on time")
            except Exception as e:
                app.logger.warning(f"[STARTUP] Failed to initialize Notification Scheduler: {e}")
                app.logger.warning(f"[STARTUP] Background notifications will not run automatically")
                app.notification_scheduler = None

    # Set up user loader
    @login_manager.user_loader
    def load_user(user_id):
        from app.models.account import User
        from app.services.cms_blacklist import get_blacklist_service
        from app.services.testing_access import testing_login_allowed

        from flask import session

        hosted = bool(os.environ.get('K_SERVICE'))
        user = User.get(user_id, fresh=True) if hosted else User.get(user_id)
        version = session.get('auth_version')
        if not user or version != user.session_version:
            return None
        if hosted and not version:
            return None
        enrollments = {user.username}
        if user.cms_account:
            enrollments.add(user.cms_account.cms_username)
        blacklist = get_blacklist_service()
        if all(testing_login_allowed(enrollment) and blacklist.is_allowed(enrollment)
               for enrollment in enrollments):
            return user
        return None
    
    return app
