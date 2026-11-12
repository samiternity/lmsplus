"""Explicit account ownership and lazy application wiring."""

from dataclasses import dataclass

from lms_mcp.assignments import AssignmentConnector
from lms_mcp.oauth import AssignmentOAuthProvider
from lms_mcp.sessions import SessionCoordinator
from lms_mcp.store import FirestoreStore
from lms_mcp.university import UniversityClient


def user_accounts(user):
    if user is None:
        return {}
    accounts = dict(getattr(user, 'cms_accounts', {}) or {})
    account = getattr(user, 'cms_account', None)
    if account:
        accounts[account.account_id] = account
    return accounts


def load_account(account_id):
    from app.models.account import User
    return user_accounts(User.get_by_account_id(account_id)).get(account_id)


@dataclass
class ConnectorRuntime:
    settings: object
    store: object
    oauth: object
    assignments: object


def build_runtime(settings):
    from app.services.discord_service import enqueue_mcp_authorization
    from app.services.firestore_service import get_firestore_service
    from app.utils.data_manager import data_manager
    from lms_mcp.office_content import prepare_assignment_content
    fs = get_firestore_service()
    if not fs or not fs.is_available() or fs.db is None:
        raise RuntimeError('The assignment connector requires Firestore')
    store = FirestoreStore(fs.db)
    from lms_mcp.privacy import install_scraper_filters
    install_scraper_filters()
    university = UniversityClient(settings, load_account)

    def initial(account_id):
        from app.services.lms_session_manager import LMSSessionManager
        return LMSSessionManager().load_session_cookies(account_id, cookie_type='all')

    coordinator = SessionCoordinator(store, settings.key, university.recover, initial=initial)

    def metadata(account_id):
        return (data_manager.get_unified_dashboard(account_id) or {}).get('assignments_snapshot', {})

    assignments = AssignmentConnector(settings, store, coordinator, university, metadata, prepare_assignment_content)
    def owns_fresh(user_id, account_id):
        # User.get() may return the existing five-minute application cache.
        # Authorization must use a fresh server-side ownership record instead.
        user = store.get('users/' + user_id) or {}
        single = user.get('cms_account') or {}
        return (user.get('account_id') == account_id and single.get('account_id') == account_id
                or account_id in (user.get('cms_accounts') or {}))

    return ConnectorRuntime(settings, store,
                            AssignmentOAuthProvider(settings, store, owns_fresh, enqueue_mcp_authorization),
                            assignments)
