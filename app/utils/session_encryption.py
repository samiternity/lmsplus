"""Encrypt Firestore session documents with the existing account key."""

import json


def encrypt_session(document_id: str, session_data: dict) -> dict:
    from app.models.account import account_manager

    payload = json.dumps({"document_id": document_id, "session": session_data})
    return {"session_encrypted": account_manager.encrypt_data(payload)}


def decrypt_session(document_id: str, document: dict | None) -> dict | None:
    from app.models.account import account_manager

    if not document or not document.get("session_encrypted"):
        return None

    payload = json.loads(account_manager.decrypt_data(document["session_encrypted"]))
    if payload["document_id"] != document_id or not isinstance(payload["session"], dict):
        return None
    return payload["session"]
