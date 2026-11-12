"""Per-account distributed recovery with fenced, encrypted session publication."""

import base64
import json
import os
import secrets
import time
from contextlib import nullcontext
from dataclasses import dataclass

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from lms_mcp.core import ConnectorError
from lms_mcp.store import digest


@dataclass
class UniversitySession:
    cookies: dict
    generation: int = 0


class SessionCoordinator:
    def __init__(self, store, key, recover, initial=None):
        self.store, self.key, self.recover = store, key, recover
        self.initial = initial

    def _encode(self, account_id, cookies):
        nonce = os.urandom(12)
        data = AESGCM(self.key).encrypt(nonce, json.dumps(cookies).encode(),
                                        ("mcp-session:" + account_id).encode())
        return base64.b64encode(nonce + data).decode()

    def _decode(self, account_id, record):
        if not record or not record.get("cookies"):
            return UniversitySession({})
        raw = base64.b64decode(record["cookies"])
        data = AESGCM(self.key).decrypt(raw[:12], raw[12:], ("mcp-session:" + account_id).encode())
        return UniversitySession(json.loads(data), record["generation"])

    def current(self, account_id):
        record = self.store.get("mcp_sessions/" + digest(account_id))
        if not record and self.initial:
            return UniversitySession(self.initial(account_id) or {})
        return self._decode(account_id, record)

    def refresh(self, account_id, rejected_generation, deadline):
        key = "mcp_sessions/" + digest(account_id)
        owner = secrets.token_hex(16)
        while True:
            deadline.remaining()
            now = time.time()

            def claim(docs):
                record = docs[key] or {"generation": 0}
                if record["generation"] > rejected_generation and record.get("cookies"):
                    return {}, ("reuse", record)
                if record.get("lease_until", 0) > now:
                    return {}, ("wait", None)
                record.update(lease_owner=owner, lease_until=now + deadline.remaining())
                return {key: record}, ("recover", record)

            action, record = self.store.run([key], claim)
            if action == "reuse":
                return self._decode(account_id, record)
            if action == "recover":
                break
            time.sleep(min(0.15, deadline.remaining()))

        try:
            # Lease acquisition is the point at which persisted state was rechecked.
            # The login helper returns cookies without writing a shared legacy cache.
            cookies = self.recover(account_id, deadline)
            deadline.remaining()
            encrypted = self._encode(account_id, cookies)

            def publish(docs):
                current = docs[key] or {}
                if current.get("lease_owner") != owner or current.get("lease_until", 0) <= time.time():
                    raise ConnectorError("authentication_failure", "Session recovery was superseded. Please retry.")
                current.update(cookies=encrypted, generation=current.get("generation", 0) + 1,
                               lease_owner=None, lease_until=0)
                return {key: current}, self._decode(account_id, current)

            return self.store.run([key], publish)
        finally:
            def release(docs):
                current = docs[key] or {}
                if current.get("lease_owner") == owner:
                    current.update(lease_owner=None, lease_until=0)
                    return {key: current}, None
                return {}, None
            self.store.run([key], release)


class RequestSession:
    """One recovery budget shared by options, course scraping, and file download."""

    def __init__(self, coordinator, account_id, deadline):
        self.coordinator, self.account_id, self.deadline = coordinator, account_id, deadline
        self.state = coordinator.current(account_id)
        self.recovered = False

    def call(self, operation, *, stage=None):
        self.deadline.remaining()
        timing = self.deadline.timing

        def attempt():
            with timing.measure(stage) if stage else nullcontext():
                return operation(self.state.cookies)

        try:
            if not self.state.cookies:
                raise ConnectorError("authentication_failure", "University session is unavailable.")
            return attempt()
        except ConnectorError as error:
            if error.code != "authentication_failure" or self.recovered:
                raise
            self.recovered = True
            with timing.measure('session_recovery'):
                self.state = self.coordinator.refresh(self.account_id, self.state.generation, self.deadline)
            return attempt()
