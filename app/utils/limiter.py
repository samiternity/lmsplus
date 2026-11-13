from flask_limiter import Limiter
from flask import request
from flask_login import current_user

def get_rate_limit_key():
    """
    Returns user ID if authenticated (to protect eduroam users).
    Otherwise returns the proxy-adjusted remote address.
    """
    if current_user and current_user.is_authenticated:
        return str(current_user.id)
    
    # Unauthenticated: use IP
    return request.remote_addr or "127.0.0.1"

limiter = Limiter(
    key_func=get_rate_limit_key,
    default_limits=["30 per minute", "150 per hour"],
    storage_uri="memory://",
    strategy="fixed-window",
)
