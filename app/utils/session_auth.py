"""Session-based account authentication helpers.

This module provides helper functions for managing session-based account authentication.
Each browser session maintains its own list of authenticated accounts, ensuring that
accounts authenticated in one session are not automatically accessible in other sessions.

This is a critical security feature that prevents unauthorized cross-session account access.
"""

from flask import session


def init_authenticated_accounts():
    """Initialize authenticated_accounts list in session if not present.
    
    This function ensures the session has an authenticated_accounts list.
    It should be called before any operations that read or modify the list.
    """
    if 'authenticated_accounts' not in session:
        session['authenticated_accounts'] = []


def add_authenticated_account(account_id):
    """Add an account to the session's authenticated accounts list.
    
    This function should be called after successful CMS authentication to mark
    an account as authenticated in the current session.
    
    Args:
        account_id (str): UUID of the account to add
    """
    init_authenticated_accounts()
    if account_id not in session['authenticated_accounts']:
        session['authenticated_accounts'].append(account_id)
        session.modified = True


def remove_authenticated_account(account_id):
    """Remove an account from the session's authenticated accounts list.
    
    This function can be used to revoke session-level access to a specific account
    without logging out the entire session.
    
    Args:
        account_id (str): UUID of the account to remove
    """
    init_authenticated_accounts()
    if account_id in session['authenticated_accounts']:
        session['authenticated_accounts'].remove(account_id)
        session.modified = True


def is_account_authenticated(account_id):
    """Check if an account is authenticated in the current session.
    
    This is the primary function for verifying session-level account access.
    Routes should use this function to ensure users can only access accounts
    they have authenticated in the current session.
    
    Args:
        account_id (str): UUID of the account to check
        
    Returns:
        bool: True if account is authenticated in this session, False otherwise
    """
    init_authenticated_accounts()
    return account_id in session.get('authenticated_accounts', [])


def get_authenticated_accounts():
    """Get list of authenticated account IDs in the current session.
    
    This function returns the list of account UUIDs that have been authenticated
    in the current session. Use this to filter the account switcher UI.
    
    Returns:
        list: List of account UUIDs (strings)
    """
    init_authenticated_accounts()
    return session.get('authenticated_accounts', [])


def clear_authenticated_accounts():
    """Clear all authenticated accounts from the session.
    
    This function should be called on logout to ensure the session no longer
    has access to any accounts. It can also be called when a session expires.
    """
    session['authenticated_accounts'] = []
    session.modified = True
