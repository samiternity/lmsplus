from inspect import unwrap
from unittest.mock import Mock

import pytest
from flask import Flask

from app.routes import lms
from app.services.lms_scraper_service import LMSScraperService


@pytest.fixture
def download_route(monkeypatch):
    session = Mock()
    authenticate = Mock(return_value=session)
    monkeypatch.setattr(LMSScraperService, "_create_authenticated_session", authenticate)
    monkeypatch.setattr(lms, "get_active_account_id", lambda: "synthetic")
    account = Mock(account_id="synthetic")
    monkeypatch.setattr(lms, "current_user", Mock(get_active_account=Mock(return_value=account)))
    monkeypatch.setattr(lms, "is_account_authenticated", lambda _: True)
    app = Flask(__name__)
    app.secret_key = "synthetic"

    def call(url):
        with app.test_request_context():
            return app.make_response(unwrap(lms.download_file)(url))

    return call, authenticate, session


@pytest.mark.parametrize("url", [
    "http://lms.bahria.edu.pk/file.pdf",
    "https://outside.example.test/" + "a" * 4096,
    "https://lms.bahria.edu.pk.outside.example.test/file.pdf",
    "https://outside.example.test@lms.bahria.edu.pk/file.pdf",
    "https://lms.bahria.edu.pk:444/file.pdf",
    "https://lms.bahria.edu.pk\\@outside.example.test/file.pdf",
])
def test_off_list_download_never_authenticates_or_fetches(download_route, url):
    call, authenticate, session = download_route
    assert call(url).status_code == 400
    authenticate.assert_not_called()
    session.get.assert_not_called()


def test_download_fallback_refuses_upstream_redirect(download_route, monkeypatch):
    call, _, session = download_route
    monkeypatch.setattr(lms, "file_worker_origin", lambda: None)
    upstream = session.get.return_value
    upstream.status_code = 302
    upstream.headers = {"Location": "http://outside.example.test/file"}
    assert call("https://lms.bahria.edu.pk/file.pdf").status_code == 502
    session.get.assert_called_once_with(
        "https://lms.bahria.edu.pk/file.pdf", stream=True, timeout=(5, 10), allow_redirects=False
    )
    upstream.close.assert_called_once()


def test_allowed_download_fallback_streams_an_attachment(download_route, monkeypatch):
    call, _, session = download_route
    monkeypatch.setattr(lms, "file_worker_origin", lambda: None)
    upstream = session.get.return_value
    upstream.status_code = 200
    upstream.headers = {}
    upstream.iter_content.return_value = iter([b"synthetic file"])
    response = call("https:/lms.bahria.edu.pk/file.pdf")
    try:
        assert response.status_code == 200
        assert response.headers["Content-Disposition"] == 'attachment; filename="file.pdf"'
        assert response.get_data() == b"synthetic file"
    finally:
        response.close()
    upstream.close.assert_called_once()
