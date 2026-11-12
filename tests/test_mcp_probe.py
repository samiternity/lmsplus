"""Protocol and access-control checks; these do not assert Claude can ingest files."""

import base64
from io import BytesIO
import os
from unittest.mock import patch
from urllib.parse import urlsplit
from zipfile import ZipFile

from flask import Flask
import pytest
from starlette.testclient import TestClient

from lms_mcp.asgi import create_asgi_app
from lms_mcp.fixtures import MIME_TYPES, diagram_png, document_bytes
from lms_mcp.probe import ProbeSettings, issue_file_token, read_file_token


KEY = bytes.fromhex("11" * 32)
SETTINGS = ProbeSettings("https://fixture-lms-plus.web.app", "test-bearer-" * 4, KEY)
HEADERS = {"Authorization": "Bearer " + SETTINGS.bearer,
           "Accept": "application/json, text/event-stream",
           "Mcp-Protocol-Version": "2025-11-25"}


@pytest.fixture
def client():
    flask = Flask(__name__)
    flask.add_url_rule("/existing", view_func=lambda: "existing Flask route")
    with TestClient(create_asgi_app(flask, SETTINGS), base_url=SETTINGS.origin) as c:
        yield c


def call(client, method, params=None, headers=None):
    return client.post("/mcp-probe/mcp/", headers=HEADERS if headers is None else headers,
                       json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}})


def test_disabled_and_production_guard():
    with patch.dict(os.environ, {}, clear=True):
        assert ProbeSettings.from_environment() is None
    with patch.dict(os.environ, {"LMS_MCP_PROBE_ENABLED": "true", "APP_ENV": "production"}, clear=True):
        with pytest.raises(ValueError, match="restricted to testing"):
            ProbeSettings.from_environment()
    with patch.dict(os.environ, {"LMS_MCP_PROBE_ENABLED": "true", "APP_ENV": "testing"}, clear=True):
        with pytest.raises(ValueError, match="random LMS_MCP_PROBE_TOKEN"):
            ProbeSettings.from_environment()
    flask = Flask(__name__)
    with TestClient(create_asgi_app(flask)) as c:
        assert c.post("/mcp-probe/mcp/").status_code == 404


def test_private_auth_and_existing_flask_routes(client):
    assert client.get("/existing").text == "existing Flask route"
    for auth in (None, "Bearer wrong", "Basic abc"):
        headers = {"Accept": "application/json, text/event-stream"}
        if auth:
            headers["Authorization"] = auth
        response = call(client, "tools/list", headers=headers)
        assert response.status_code == 401
        assert SETTINGS.bearer not in response.text
    response = call(client, "initialize", {"protocolVersion": "2025-11-25",
                                           "clientInfo": {"name": "test", "version": "1"}, "capabilities": {}})
    assert response.status_code == 200
    assert "serverInfo" in response.json()["result"]
    assert "mcp-session-id" not in response.headers
    assert response.headers["cache-control"] == "private, no-store"


def test_tool_schema_has_only_private_experiments(client):
    response = call(client, "tools/list")
    tools = response.json()["result"]["tools"]
    assert {tool["name"] for tool in tools} == {"list_probe_cases", "probe_file_delivery"}
    probe = next(t for t in tools if t["name"] == "probe_file_delivery")
    assert set(probe["inputSchema"]["properties"]["method"]["enum"]) == {
        "embedded", "resource_link", "signed_url", "text_image", "document_content"}
    assert probe["annotations"]["readOnlyHint"] is True


@pytest.mark.parametrize("case", ["pdf", "docx"])
def test_embedded_bytes_and_no_duplicate_text(client, case):
    result = call(client, "tools/call", {"name": "probe_file_delivery", "arguments": {
        "case": case, "method": "embedded"}}).json()["result"]
    assert not result.get("isError", False)
    assert len(result["content"]) == 1
    resource = result["content"][0]["resource"]
    assert resource["mimeType"] == MIME_TYPES[case]
    assert base64.b64decode(resource["blob"]) == document_bytes(case)
    assert resource["uri"].startswith("probe://")
    assert result["_meta"]["probe"]["claude_ingestion_verified"] is False


@pytest.mark.parametrize("method", ["resource_link", "signed_url"])
def test_signed_link_download_and_format_binding(client, method):
    result = call(client, "tools/call", {"name": "probe_file_delivery", "arguments": {
        "case": "pdf", "method": method}}).json()["result"]
    item = result["content"][0]
    url = item["uri"] if method == "resource_link" else item["text"].splitlines()[-1]
    response = client.get(url)
    assert response.status_code == 200
    assert response.content == document_bytes("pdf")
    assert response.headers["cache-control"] == "private, no-store"
    assert response.headers["content-type"] == "application/pdf"
    assert client.get(url.replace("/files/pdf?", "/files/docx?")).status_code == 401
    assert client.get(urlsplit(url).path).status_code == 401


def test_bad_tool_input_cannot_fetch_arbitrary_urls(client):
    for arguments in ({"case": "https://evil.test", "method": "embedded"},
                      {"case": "pdf", "method": "convert"}):
        result = call(client, "tools/call", {"name": "probe_file_delivery", "arguments": arguments}).json()
        assert "error" in result or result["result"].get("isError")


def test_resource_links_support_authenticated_resources_read(client):
    result = call(client, "tools/call", {"name": "probe_file_delivery", "arguments": {
        "case": "docx", "method": "resource_link"}}).json()["result"]
    uri = result["content"][0]["uri"]
    response = call(client, "resources/read", {"uri": uri}).json()
    contents = response["result"]["contents"]
    assert contents[0]["mimeType"] == MIME_TYPES["docx"]
    assert base64.b64decode(contents[0]["blob"]) == document_bytes("docx")


def test_token_expiration_tampering_and_cross_key():
    token = issue_file_token("pdf", KEY, now=1000)
    assert read_file_token(token, KEY, now=1119)["case"] == "pdf"
    for value, key, clock in ((token, KEY, 1120), (token, KEY, 994),
                              (token, bytes(32), 1000), (token[:-5] + "AAAAA", KEY, 1000),
                              (None, KEY, 1000), ("x" * 2049, KEY, 1000)):
        with pytest.raises(ValueError, match="Invalid or expired"):
            read_file_token(value, key, now=clock)


def test_visual_fixture_is_present_and_answers_are_not_alt_text():
    pdf = document_bytes("pdf")
    assert pdf.startswith(b"%PDF-1.4")
    assert b"/Subtype /Image" in pdf
    assert b"2x + 3 = 17" in pdf
    with ZipFile(BytesIO(document_bytes("docx"))) as archive:
        assert archive.testzip() is None
        assert archive.read("word/media/diagram.png") == diagram_png()
        document = archive.read("word/document.xml")
        assert b'r:embed="diagram"' in document
        assert b"2x + 3 = 17" in document
        assert b"shortest path is" not in document
    assert document_bytes("docx") == document_bytes("docx")


def test_known_cloud_run_proxy_host_is_allowed_without_accepting_arbitrary_hosts():
    settings = ProbeSettings(SETTINGS.origin, SETTINGS.bearer, KEY,
                             proxy_hosts=("university-portal-test-fixture.a.run.app",))
    with TestClient(create_asgi_app(Flask(__name__), settings), base_url=SETTINGS.origin) as client:
        headers = {**HEADERS, "Host": "university-portal-test-fixture.a.run.app"}
        assert call(client, "tools/list", headers=headers).status_code == 200
        headers["Host"] = "evil.test"
        assert call(client, "tools/list", headers=headers).status_code == 421


def test_testing_capability_url_supports_clients_without_request_headers(client):
    response = client.post("/mcp-probe/mcp/?probe_key=" + SETTINGS.bearer,
                           headers={"Accept": "application/json, text/event-stream"},
                           json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    assert response.status_code == 200
    assert SETTINGS.bearer not in response.text
    assert response.headers["cache-control"] == "private, no-store"


def test_text_and_image_use_supported_blocks_without_answer_leakage(client):
    result = call(client, "tools/call", {"name": "probe_file_delivery", "arguments": {
        "case": "docx", "method": "text_image"}}).json()["result"]
    assert [item["type"] for item in result["content"]] == ["text", "image"]
    assert "2x + 3 = 17" in result["content"][0]["text"]
    assert "11" not in result["content"][0]["text"]
    assert base64.b64decode(result["content"][1]["data"]) == diagram_png()
    assert result["_meta"]["probe"]["synthetic_modality_only"] is True
    assert result["_meta"]["probe"]["edge"] is False
