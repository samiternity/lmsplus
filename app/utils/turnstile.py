"""
Cloudflare Turnstile server-side verification utility.
Validates cf-turnstile-response tokens against Cloudflare's siteverify API.
"""
import os
import logging
import requests
from flask import current_app

logger = logging.getLogger(__name__)

SITEVERIFY_URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify"


def verify_turnstile(token: str, remote_ip: str = None, action: str = None) -> bool:
    """
    Verify Cloudflare Turnstile token on the backend.
    
    Args:
        token: The cf-turnstile-response string submitted by client.
        remote_ip: Client address resolved by the application server.
        action: Expected action (e.g., 'login', 'register').
        
    Returns:
        bool: True if verification passes, False otherwise.
    """
    if not token or not isinstance(token, str) or len(token.strip()) == 0 or len(token) > 2048:
        logger.warning("[TURNSTILE] Missing or invalid token format.")
        return False

    secret_key = os.environ.get("TURNSTILE_SECRET_KEY")
    if not secret_key:
        try:
            from flask import has_app_context
            if has_app_context():
                secret_key = current_app.config.get("TURNSTILE_SECRET_KEY")
        except RuntimeError:
            pass

    # Local development can run without a Turnstile key. Hosted logins fail closed.
    if not secret_key:
        logger.warning("[TURNSTILE] TURNSTILE_SECRET_KEY not set; skipping check in development.")
        return not bool(os.environ.get('K_SERVICE'))

    payload = {
        "secret": secret_key,
        "response": token.strip(),
    }
    if remote_ip:
        payload["remoteip"] = remote_ip

    try:
        resp = requests.post(SITEVERIFY_URL, data=payload, timeout=8)
        if resp.status_code != 200:
            logger.error(f"[TURNSTILE] Siteverify returned HTTP status {resp.status_code}")
            return False

        data = resp.json()
        success = data.get("success", False)
        
        if not success:
            logger.warning(f"[TURNSTILE] Verification failed. Error codes: {data.get('error-codes', [])}")
            return False

        # Optional action check if action was configured on widget
        if action and data.get("action"):
            if data.get("action") != action:
                logger.warning(f"[TURNSTILE] Action mismatch. Expected {action}, got {data.get('action')}")
                return False

        return True

    except requests.RequestException as e:
        logger.error(f"[TURNSTILE] Network error connecting to Cloudflare siteverify: {e}")
        return False
    except Exception as e:
        logger.error(f"[TURNSTILE] Unexpected error during Turnstile verification: {e}")
        return False
