"""Testing-only MCP file-ingestion experiment; no student accounts are accessed.

Enable with LMS_MCP_PROBE_ENABLED=true, APP_ENV=testing, a random
LMS_MCP_PROBE_TOKEN (at least 32 characters), LMS_MCP_PROBE_ENCRYPTION_KEY
(64 hexadecimal characters), and APP_URL set to the testing origin.
LMS_MCP_PROBE_WORKER_URL enables the Worker streaming branch after that
Worker has MCP_PROBE_ORIGIN configured to the same APP_URL.
Its MCP_PROBE_ENCRYPTION_KEY must match LMS_MCP_PROBE_ENCRYPTION_KEY.
Set LMS_MCP_PROBE_PROXY_HOSTS to the testing service's explicit run.app
hostnames when Firebase Hosting rewrites the Host header.

Connect Claude to APP_URL/mcp-probe/mcp/ with the request header
Authorization: Bearer <LMS_MCP_PROBE_TOKEN>. This temporary developer
authentication is replaced by student OAuth only after the ingestion gate.
For clients without request-header support, the testing-only capability URL
APP_URL/mcp-probe/mcp/?probe_key=<LMS_MCP_PROBE_TOKEN> has the same fixture-only
scope. Treat that URL as private. It must never be used for student access.
"""

import base64
from contextlib import asynccontextmanager
from dataclasses import dataclass
import hmac
import json
import os
import time
from typing import Literal
from urllib.parse import parse_qs, urlencode, urlsplit
from uuid import uuid4
import zlib

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from mcp import types
from mcp.server import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from starlette.applications import Starlette
from starlette.responses import JSONResponse, Response
from starlette.routing import Mount, Route

from lms_mcp.fixtures import MIME_TYPES, QUESTION, diagram_png, document_bytes


def checked_origin(value):
    parsed = urlsplit(value)
    local = parsed.hostname in ("localhost", "127.0.0.1", "[::1]", "::1")
    if (parsed.scheme != "https" and not (local and parsed.scheme == "http")) or (
        not parsed.netloc or parsed.username or parsed.password or parsed.query
        or parsed.fragment or parsed.path not in ("", "/")
    ):
        raise ValueError("Probe origins must be HTTPS origins without paths or credentials")
    return value.rstrip("/")


@dataclass(frozen=True)
class ProbeSettings:
    origin: str
    bearer: str
    key: bytes
    worker_origin: str = ""
    proxy_hosts: tuple[str, ...] = ()

    @classmethod
    def from_environment(cls):
        if os.getenv("LMS_MCP_PROBE_ENABLED", "false").lower() != "true":
            return None
        if os.getenv("APP_ENV") != "testing":
            raise ValueError("The MCP ingestion probe is restricted to testing")
        if os.getenv("K_SERVICE") and os.getenv("FIREBASE_PROJECT_ID") != "unitrack-testing":
            raise ValueError("The MCP ingestion probe requires the testing Firebase project")
        bearer = os.getenv("LMS_MCP_PROBE_TOKEN", "")
        if len(bearer) < 32:
            raise ValueError("Set a random LMS_MCP_PROBE_TOKEN of at least 32 characters")
        try:
            key = bytes.fromhex(os.getenv("LMS_MCP_PROBE_ENCRYPTION_KEY", ""))
        except ValueError:
            raise ValueError("Set a valid LMS_MCP_PROBE_ENCRYPTION_KEY") from None
        if len(key) != 32:
            raise ValueError("Set a valid LMS_MCP_PROBE_ENCRYPTION_KEY")
        origin = checked_origin(os.getenv("APP_URL", ""))
        worker_origin = os.getenv("LMS_MCP_PROBE_WORKER_URL", "")
        if worker_origin:
            worker_origin = checked_origin(worker_origin)
        proxy_hosts = tuple(filter(None, os.getenv("LMS_MCP_PROBE_PROXY_HOSTS", "").split(",")))
        if any(not host.endswith(".run.app") or "/" in host or ":" in host or "@" in host
               for host in proxy_hosts):
            raise ValueError("Probe proxy hosts must be explicit Cloud Run hostnames")
        return cls(origin, bearer, key, worker_origin, proxy_hosts)


def issue_file_token(case, key, now=None):
    if case not in MIME_TYPES:
        raise ValueError("Unknown test document")
    issued = int(time.time() if now is None else now)
    payload = {"p": "mcp-probe", "case": case, "t": issued, "e": issued + 120}
    iv = os.urandom(12)
    encrypted = AESGCM(key).encrypt(iv, zlib.compress(json.dumps(payload).encode()), None)
    return base64.urlsafe_b64encode(iv + encrypted).decode().rstrip("=")


def read_file_token(token, key, now=None):
    try:
        if not isinstance(token, str) or len(token) > 2048:
            raise ValueError()
        raw = base64.urlsafe_b64decode(token + "=" * (-len(token) % 4))
        compressed = AESGCM(key).decrypt(raw[:12], raw[12:], None)
        inflater = zlib.decompressobj()
        data = inflater.decompress(compressed, 1025)
        if len(data) > 1024 or not inflater.eof or inflater.unused_data:
            raise ValueError()
        payload = json.loads(data)
        clock = time.time() if now is None else now
        issued, expires = payload["t"], payload["e"]
        if (type(issued) is not int or type(expires) is not int
                or expires - issued != 120 or issued > clock + 5 or clock >= expires
                or payload["p"] != "mcp-probe" or payload["case"] not in MIME_TYPES):
            raise ValueError()
        return payload
    except Exception:
        raise ValueError("Invalid or expired test-file token") from None


def build_probe(settings):
    server = MCPServer(
        "LMS+ private file-ingestion probe",
        log_level="WARNING",
        instructions="Read the requested test document including its diagram and solve both questions. "
                     "If any content is inaccessible, state what could not be read. Do not guess diagram details.",
    )

    def resource_template(case):
        if settings.worker_origin:
            return f"{settings.worker_origin}/mcp-probe-download?case={case}&token={{token}}"
        return f"{settings.origin}/mcp-probe/files/{case}?token={{token}}"

    def register_document(case):
        @server.resource(resource_template(case), name=f"visual-assignment.{case}", mime_type=MIME_TYPES[case])
        def read_document(token: str) -> bytes:
            payload = read_file_token(token, settings.key)
            if payload["case"] != case:
                raise ValueError("Test-file format does not match link")
            return document_bytes(case)

    for case in MIME_TYPES:
        register_document(case)

    @server.tool(annotations=types.ToolAnnotations(read_only_hint=True, destructive_hint=False))
    def list_probe_cases() -> dict:
        """List synthetic documents available for testing file ingestion. No student data is exposed."""
        return {"cases": [{"id": case, "name": f"visual-assignment.{case}", "mime_type": mime}
                          for case, mime in MIME_TYPES.items()],
                "methods": ["embedded", "resource_link", "signed_url", "text_image", "document_content"],
                "streaming_edge_configured": bool(settings.worker_origin),
                "status": "not_validated_in_claude"}

    @server.tool(annotations=types.ToolAnnotations(read_only_hint=True, destructive_hint=False))
    def probe_file_delivery(
        case: Literal["pdf", "docx"],
        method: Literal["embedded", "resource_link", "signed_url", "text_image", "document_content"],
    ) -> types.CallToolResult:
        """Read the chosen visual assignment document and solve its two questions, including the diagram.

        This is a private compatibility test. Use exactly the requested delivery method.
        If the client cannot read the file or its diagram, report that limitation.
        """
        started = time.perf_counter()
        trial_id = uuid4().hex
        mime = MIME_TYPES[case]
        name = f"visual-assignment.{case}"
        if method == "document_content":
            from lms_mcp.document_content import prepare_document_content
            data = document_bytes(case)
            blocks = prepare_document_content(data, case)
            content = [types.TextContent(**item) if item["type"] == "text" else types.ImageContent(**item)
                       for item in blocks]
            byte_count = len(data)
        elif method == "text_image":
            # Modality probe only: the same text and raster used to build both
            # synthetic documents. Real document extraction is a separate gate.
            image = diagram_png()
            content = [types.TextContent(type="text", text=QUESTION),
                       types.ImageContent(type="image", mime_type="image/png",
                                          data=base64.b64encode(image).decode())]
            byte_count = len(QUESTION.encode()) + len(image)
        elif method == "embedded":
            data = document_bytes(case)
            content = [types.EmbeddedResource(resource=types.BlobResourceContents(
                uri=f"probe://visual-assignment/{trial_id}/{name}",
                mime_type=mime, blob=base64.b64encode(data).decode(),
            ))]
            byte_count = len(data)
        else:
            token = issue_file_token(case, settings.key)
            if settings.worker_origin:
                url = f"{settings.worker_origin}/mcp-probe-download?{urlencode({'case': case, 'token': token})}"
            else:
                url = f"{settings.origin}/mcp-probe/files/{case}?{urlencode({'token': token})}"
            if method == "resource_link":
                content = [types.ResourceLink(uri=url, name=name, mime_type=mime,
                                               description="Read the document and its diagram; solve both questions.")]
            else:
                content = [types.TextContent(type="text", text=(
                    "Open this test document, read its diagram, and solve both questions. "
                    f"Report inaccessible content instead of guessing.\n{url}"))]
            byte_count = None
        return types.CallToolResult(content=content, meta={"probe": {
            "trial_id": trial_id, "case": case, "method": method,
            "server_elapsed_ms": round((time.perf_counter() - started) * 1000, 3),
            "document_bytes": byte_count, "edge": bool(settings.worker_origin) and method in ("resource_link", "signed_url"),
            "synthetic_modality_only": method == "text_image",
            "claude_ingestion_verified": False,
        }})

    async def file_response(request):
        case = request.path_params["case"]
        try:
            payload = read_file_token(request.query_params.get("token"), settings.key)
            if payload["case"] != case:
                raise ValueError()
        except ValueError:
            return JSONResponse({"error": "Invalid or expired test-file link"}, status_code=401,
                                headers={"Cache-Control": "private, no-store"})
        return Response(document_bytes(case), media_type=MIME_TYPES[case], headers={
            "Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff",
            "Content-Disposition": f'inline; filename="visual-assignment.{case}"',
            "Referrer-Policy": "no-referrer",
        })

    host = urlsplit(settings.origin).netloc
    mcp_app = server.streamable_http_app(
        streamable_http_path="/", stateless_http=True, json_response=True,
        max_request_body_size=16384,
        transport_security=TransportSecuritySettings(
            allowed_hosts=[host, *settings.proxy_hosts], allowed_origins=[settings.origin]),
    )

    class RequireBearer:
        def __init__(self, app):
            self.app = app

        async def __call__(self, scope, receive, send):
            if scope["type"] != "http":
                return await self.app(scope, receive, send)
            supplied = dict(scope.get("headers", [])).get(b"authorization", b"")
            expected = f"Bearer {settings.bearer}".encode()
            # Some Claude accounts cannot set request headers. This capability
            # URL grants access only to synthetic fixtures, never student data.
            query = parse_qs(scope.get("query_string", b"").decode("utf-8", errors="replace"))
            url_key = query.get("probe_key", [""])[0].encode()
            if not (hmac.compare_digest(supplied, expected)
                    or hmac.compare_digest(url_key, settings.bearer.encode())):
                response = JSONResponse({"error": "Private developer connector requires a bearer token"},
                                        status_code=401, headers={"Cache-Control": "no-store",
                                                                  "WWW-Authenticate": "Bearer"})
                return await response(scope, receive, send)
            async def send_private(message):
                if message["type"] == "http.response.start":
                    headers = [(key, value) for key, value in message.get("headers", [])
                               if key.lower() != b"cache-control"]
                    message = {**message, "headers": [*headers, (b"cache-control", b"private, no-store")]}
                await send(message)
            return await self.app(scope, receive, send_private)

    @asynccontextmanager
    async def lifespan(app):
        async with server.session_manager.run():
            yield

    application = Starlette(routes=[
        Route("/files/{case}", file_response, methods=["GET"]),
        Mount("/mcp", RequireBearer(mcp_app)),
    ], lifespan=lifespan)
    return application, server
