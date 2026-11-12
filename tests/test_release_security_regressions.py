from unittest.mock import Mock
from urllib.parse import parse_qs, urlsplit

import pytest
import requests
from flask import Flask, session

from app.routes import discord_oauth
from app.services.lms_file_proxy_service import LMSFileError, LMSFileProxyService
from app.services.lms_session_manager import LMSSessionManager
from app.utils.data_manager import DataManager


@pytest.mark.parametrize("target", [
    "http://lms.bahria.edu.pk/file",
    "https://lms.bahria.edu.pk.evil.test/file",
    "https://evil.test@lms.bahria.edu.pk/file",
    "https://lms.bahria.edu.pk:444/file",
    "//outside.example.test/file",
])
def test_off_list_metadata_never_creates_a_session(monkeypatch, target):
    service = LMSFileProxyService()
    authenticate = Mock()
    monkeypatch.setattr(service, "_create_authenticated_session", authenticate)
    with pytest.raises(LMSFileError):
        service.get_file_metadata("dummy", target)
    authenticate.assert_not_called()


@pytest.mark.parametrize("head_status,expected_get", [(302, False), (405, True)])
def test_metadata_never_follows_head_or_range_redirects(monkeypatch, head_status, expected_get):
    service = LMSFileProxyService()
    upstream = Mock()
    upstream.head.return_value = requests.Response()
    upstream.head.return_value.status_code = head_status
    upstream.head.return_value.raw = Mock()
    upstream.get.return_value = requests.Response()
    upstream.get.return_value.status_code = 302
    upstream.get.return_value.raw = Mock()
    upstream.get.return_value.headers["Location"] = "https://outside.example.test/file"
    monkeypatch.setattr(service, "_create_authenticated_session", lambda _: upstream)
    with pytest.raises(LMSFileError):
        service.get_file_metadata("dummy", "https://lms.bahria.edu.pk/file")
    assert upstream.head.call_args.kwargs["allow_redirects"] is False
    if expected_get:
        assert upstream.get.call_args.kwargs["allow_redirects"] is False
    else:
        upstream.get.assert_not_called()


def test_discord_state_is_bound_to_the_session_user_and_consumed(monkeypatch):
    app = Flask(__name__)
    app.secret_key = "synthetic"
    app.register_blueprint(discord_oauth.discord_oauth_bp, url_prefix="/discord")
    app.add_url_rule("/settings", "settings.settings", lambda: "")
    user = Mock()
    user.get_id.return_value = "dummy-user"
    monkeypatch.setattr(discord_oauth, "current_user", user)
    monkeypatch.setenv("DISCORD_CLIENT_ID", "dummy-client")
    exchange = Mock(side_effect=requests.RequestException("synthetic failure"))
    monkeypatch.setattr(discord_oauth.requests, "post", exchange)
    with app.test_request_context("/discord/login"):
        start = discord_oauth.login.__wrapped__()
        pending = dict(session["discord_link"])
        state = parse_qs(urlsplit(start.location).query)["state"][0]
        assert pending["state"] == state
    for supplied, identity, expires_at in [
        ("missing-match", "dummy-user", pending["expires_at"]),
        (state, "other-user", pending["expires_at"]),
        (state, "dummy-user", 0),
    ]:
        with app.test_request_context("/discord/callback", query_string={"code": "dummy", "state": supplied}):
            session["discord_link"] = {**pending, "expires_at": expires_at}
            user.get_id.return_value = identity
            discord_oauth.callback.__wrapped__()
    exchange.assert_not_called()
    with app.test_request_context("/discord/callback", query_string={"code": "dummy", "state": state}):
        user.get_id.return_value = "dummy-user"
        session["discord_link"] = pending
        discord_oauth.callback.__wrapped__()
        assert "discord_link" not in session
        discord_oauth.callback.__wrapped__()
    exchange.assert_called_once()


def test_both_firestore_session_writers_hide_cookie_values(monkeypatch, tmp_path):
    from app.services import firestore_service

    firestore = Mock()
    firestore.is_available.return_value = True
    monkeypatch.setattr(firestore_service, "get_firestore_service", lambda: firestore)
    cms = object.__new__(DataManager)
    monkeypatch.setattr(cms, "get_account_data_path", lambda *_: tmp_path)
    assert cms.save_session_data("dummy", {"CMS": "synthetic-cms-cookie"})
    cms_document = firestore.save_document.call_args.args[2]
    assert set(cms_document) == {"session_encrypted"}
    assert "synthetic-cms-cookie" not in repr(cms_document)
    firestore.get_document.return_value = cms_document
    assert cms.load_session_data("dummy").get("CMS") == "synthetic-cms-cookie"
    firestore.get_document.return_value = {"cookies": {"CMS": "legacy-cookie"}}
    monkeypatch.setattr(cms, "get_account_data_path", lambda *_: tmp_path / "empty")
    assert cms.load_session_data("dummy") is None

    lms = object.__new__(LMSSessionManager)
    payload = {"cookies": {"LMS": "synthetic-lms-cookie"}}
    assert lms._save_to_firestore("dummy", payload)
    lms_document = firestore.save_document.call_args.args[2]
    assert set(lms_document) == {"session_encrypted"}
    assert "synthetic-lms-cookie" not in repr(lms_document)
    firestore.get_document.return_value = lms_document
    assert lms._load_from_firestore("dummy") == payload
    assert lms._load_from_firestore("other-user") is None
    firestore.get_document.return_value = payload
    assert lms._load_from_firestore("dummy") is None

def test_account_serialization_never_emits_plaintext_password():
    from app.models.account import CMSAccount, account_manager

    account = CMSAccount("dummy", "00-000000-000", "synthetic-password", institute_id="2")
    saved = account.to_dict()
    assert "cms_password" not in saved
    assert account_manager.decrypt_data(saved["cms_password_encrypted"]) == "synthetic-password"
    account.cms_password = ""
    assert "cms_password" not in account.to_dict()
