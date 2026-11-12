"""Optional production-only analytics context, without database access."""

import hashlib
import hmac
import re
from urllib.parse import urlsplit

from flask import current_app, request
from flask_login import current_user


def analytics_context():
    """Expose only pseudonymous identity and a route template to the browser."""
    config = current_app.config
    measurement_id = config.get('GA4_MEASUREMENT_ID', '')
    secret = config.get('GA4_USER_ID_SECRET', '')
    try:
        origin = urlsplit(config.get('APP_URL', ''))
    except ValueError:
        return {'analytics': None}
    if (
        config.get('APP_ENV') != 'production'
        or config.get('TESTING')
        or origin.scheme != 'https'
        or request.host != origin.netloc
        or not re.fullmatch(r'G-[A-Z0-9]+', measurement_id)
        or not secret
        or not current_user.is_authenticated
        or request.url_rule is None
    ):
        return {'analytics': None}

    user_id = current_user.get_id()
    if not user_id:
        return {'analytics': None}

    # User IDs may be enrollment numbers. A keyed hash prevents guessing them
    # from the public analytics identifier; the key stays on the server.
    analytics_id = hmac.new(
        secret.encode('utf-8'), str(user_id).encode('utf-8'), hashlib.sha256
    ).hexdigest()
    # Use the route definition, never the actual URL or dynamic route values.
    path = re.sub(r'<(?:[^:<>]+:)?([^<>]+)>', r':\1', request.url_rule.rule)
    return {'analytics': {
        'measurement_id': measurement_id,
        'user_id': analytics_id,
        'page_location': f'{origin.scheme}://{origin.netloc}' + path,
        'page_title': 'LMS+',
        'page_referrer': '',
    }}


def register_analytics(app):
    app.context_processor(analytics_context)
