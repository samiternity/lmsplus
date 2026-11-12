"""
Multi-environment configuration loader for UniTrack.

Resolution order:
1. Load .env (for APP_ENV selector + shared secrets)
2. Load .env.<APP_ENV> (environment-specific Firebase config)
3. Local mode reads only .env.local, with explicit process variables taking precedence

Usage:
    from app.config import get_config, load_environment
    load_environment()  # Call once at startup
    config = get_config()
    config.is_production  # True/False
"""
import json
import os
from pathlib import Path
from dotenv import load_dotenv

_config = None

# Private deployments ship this optional file with their non-secret endpoint settings.
_endpoint_file = Path(__file__).with_name('deployment_endpoints.json')
try:
    _deployment_origins = json.loads(_endpoint_file.read_text(encoding='utf-8'))
except FileNotFoundError:
    _deployment_origins = {}


def get_deployment_origin(name):
    default = f"https://{name.lower().replace('_', '-')}.invalid"
    private_default = default if get_config().is_local else _deployment_origins.get(name, default)
    return (os.environ.get(name) or private_default).rstrip('/')


class AppConfig:
    """Centralized application configuration."""
    
    def __init__(self):
        self.env_name = os.environ.get('APP_ENV', 'testing')
        
    @property
    def is_production(self):
        return self.env_name == 'production'
    
    @property
    def is_local(self):
        return self.env_name == 'local'

    @property
    def is_testing(self):
        return self.env_name == 'testing'
    
    @property
    def use_firestore(self):
        return not self.is_local and os.environ.get('USE_FIRESTORE', '').lower() == 'true'
    
    @property
    def firebase_project_id(self):
        return '' if self.is_local else os.environ.get('FIREBASE_PROJECT_ID', '')
    
    @property
    def app_url(self):
        return os.environ.get('APP_URL', 'http://localhost:5000')
    
    @property
    def cost_safety_enabled(self):
        return os.environ.get('COST_SAFETY_ENABLED', 'false').lower() == 'true'
    
    @property
    def turnstile_site_key(self):
        return os.environ.get('TURNSTILE_SITE_KEY', '')
        
    @property
    def turnstile_secret_key(self):
        return os.environ.get('TURNSTILE_SECRET_KEY', '')

    @property
    def ga4_measurement_id(self):
        return os.environ.get('GA4_MEASUREMENT_ID', '').strip()

    @property
    def ga4_user_id_secret(self):
        return os.environ.get('GA4_USER_ID_SECRET', '').strip()
    
    def __repr__(self):
        return (f"AppConfig(env={self.env_name}, firestore={self.use_firestore}, "
                f"project={self.firebase_project_id})")


def load_environment(env_override=None):
    """Load environment configuration files in the correct order.
    
    Args:
        env_override: Override APP_ENV (e.g., from --env CLI flag)
    """
    global _config
    
    project_root = Path(__file__).parent.parent
    
    # A local run uses a dedicated file and inherits explicit shell variables.
    if env_override:
        os.environ['APP_ENV'] = env_override
    if os.environ.get('APP_ENV') == 'local':
        load_dotenv(project_root / '.env.local', override=False)
    else:
        load_dotenv(project_root / '.env', override=False)
        env_name = os.environ.get('APP_ENV', 'testing')
        load_dotenv(project_root / f'.env.{env_name}', override=True)

    _config = AppConfig()
    return _config


def get_config():
    """Get the current configuration. Call load_environment() first."""
    global _config
    if _config is None:
        _config = AppConfig()
    return _config
