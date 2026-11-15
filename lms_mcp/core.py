"""Shared limits and public failures for the read-only assignment connector."""

import os
import hashlib
import re
import time
from contextlib import contextmanager
from dataclasses import dataclass
from threading import Lock
from urllib.parse import urlsplit

from lms_mcp.probe import checked_origin


SCOPE = "assignments:read"
CHATGPT_CALLBACK = "https://chatgpt.com/connector_platform_oauth_redirect"
CLAUDE_CALLBACK = "https://claude.ai/api/mcp/auth_callback"
VSCODE_CALLBACK = "https://vscode.dev/redirect"
ANTIGRAVITY_CALLBACK = "https://antigravity.google/oauth-callback"
ANTIGRAVITY_CLIENT_ID = "https://antigravity.google/oauth/client-metadata.json"
PROVIDER_LABELS = {'claude': 'Claude', 'chatgpt': 'ChatGPT', 'antigravity': 'Google Antigravity',
                   'vscode': 'VS Code', 'other': 'Other MCP app'}


def callback_provider(callback):
    return {CLAUDE_CALLBACK: 'claude', CHATGPT_CALLBACK: 'chatgpt', VSCODE_CALLBACK: 'vscode',
            ANTIGRAVITY_CALLBACK: 'antigravity'}.get(callback, 'other')


class ConnectorError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message


class Deadline:
    def __init__(self, seconds=50):
        self.ends = time.monotonic() + seconds
        self.cancelled = False
        self.timing = RequestTiming()

    def remaining(self, maximum=None):
        remaining = self.ends - time.monotonic()
        if remaining <= 0 or self.cancelled:
            raise ConnectorError("processing_limit", "Assignment processing timed out. Please retry.")
        return min(remaining, maximum) if maximum else remaining


class RequestTiming:
    """Snapshot request metrics safely even while a cancelled worker unwinds."""

    def __init__(self):
        self.started = time.monotonic()
        self._lock = Lock()
        self._active = {}
        self._elapsed = {}
        self._states = {}
        self._bytes = {}

    @contextmanager
    def measure(self, stage):
        with self._lock:
            self._active[stage] = time.monotonic()
        state = 'completed'
        try:
            yield
        except BaseException:
            state = 'failed'
            raise
        finally:
            with self._lock:
                elapsed = time.monotonic() - self._active.pop(stage)
                self._elapsed[stage] = self._elapsed.get(stage, 0) + elapsed
                self._states[stage] = state

    def record_bytes(self, name, size):
        with self._lock:
            self._bytes[name] = size

    def snapshot(self):
        with self._lock:
            now = time.monotonic()
            elapsed, states = self._elapsed.copy(), self._states.copy()
            for stage, started in self._active.items():
                elapsed[stage] = elapsed.get(stage, 0) + now - started
                states[stage] = 'interrupted'
            return {
                'tool_elapsed_ms': round((now - self.started) * 1000),
                'stage_elapsed_ms': {stage: round(seconds * 1000) for stage, seconds in elapsed.items()},
                'stage_status': states,
                'bytes': self._bytes.copy(),
            }


@dataclass(frozen=True)
class ConnectorSettings:
    origin: str
    worker_origin: str
    key: bytes
    accounts: frozenset[str] | None
    proxy_hosts: tuple[str, ...] = ()
    file_hosts: tuple[str, ...] = ("lms.bahria.edu.pk",)

    @property
    def resource(self):
        return self.origin + "/mcp"

    @property
    def proxy_headers(self):
        return {'X-LMS-MCP-Proxy-Key': hashlib.sha256(('mcp-proxy:' + self.key.hex()).encode()).hexdigest()}

    @classmethod
    def from_environment(cls):
        if os.getenv("LMS_MCP_ENABLED", "false").lower() != "true":
            return None
        key = bytes.fromhex(os.environ.get("LMS_MCP_DOWNLOAD_KEY", ""))
        if len(key) != 32:
            raise ValueError("LMS_MCP_DOWNLOAD_KEY must be a dedicated 32-byte secret")
        accounts = None
        if os.getenv("APP_ENV") != "production":
            accounts = frozenset(filter(None, os.getenv("LMS_MCP_ALLOWED_ACCOUNTS", "").split(",")))
            if not accounts or len(accounts) >= 100:
                raise ValueError("Non-production MCP requires an explicit allowlist of fewer than 100 accounts")
        proxy_hosts = tuple(os.getenv("LMS_MCP_PROXY_HOSTS", "").replace(",", " ").split())
        if any(not re.fullmatch(r"[a-z0-9.-]+\.run\.app", host) for host in proxy_hosts):
            raise ValueError("MCP proxy hosts must be explicit Cloud Run hostnames")
        return cls(checked_origin(os.environ.get("APP_URL", "")),
                   checked_origin(os.environ.get("LMS_MCP_WORKER_URL", "")), key, accounts, proxy_hosts)

    def allows_account(self, account_id):
        return (bool(account_id)
                and os.getenv("LMS_MCP_ENABLED", "true").lower() == "true"
                and (self.accounts is None or account_id in self.accounts))

    def check_account(self, account_id):
        if not self.allows_account(account_id):
            raise ConnectorError("connection_unavailable", "This account does not have connector access.")

    def check_file_url(self, url):
        parsed = urlsplit(url)
        if (parsed.scheme != "https" or parsed.hostname not in self.file_hosts
                or parsed.port not in (None, 443) or parsed.username or parsed.password or parsed.fragment):
            raise ConnectorError("university_failure", "The university supplied an unsupported file address.")
        return url
