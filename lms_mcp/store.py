"""Transactional connector records. Production never falls back to local storage."""

import copy
import hashlib
import threading


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


class FirestoreStore:
    def __init__(self, db):
        self.db = db

    def get(self, key):
        return self.db.document(key).get(timeout=5).to_dict()

    def put(self, key, value):
        self.db.document(key).set(value, timeout=5)

    def run(self, keys, operation):
        from google.cloud import firestore

        @firestore.transactional
        def execute(transaction):
            docs = {key: self.db.document(key).get(transaction=transaction, timeout=5).to_dict()
                    for key in keys}
            changes, result = operation(docs)
            for key, value in changes.items():
                if key not in docs:
                    raise ValueError("Transaction attempted an unread record")
                if value is None:
                    transaction.delete(self.db.document(key))
                else:
                    transaction.set(self.db.document(key), value)
            return result

        return execute(self.db.transaction(max_attempts=3))

    def connections(self, user_id):
        from google.cloud.firestore_v1.base_query import FieldFilter
        return [dict(doc.to_dict(), id=doc.id) for doc in self.db.collection("mcp_connections")
                .where(filter=FieldFilter("user_id", "==", user_id)).limit(100).stream(timeout=5)]


class MemoryStore:
    """Explicit test double; never constructed by application configuration."""

    def __init__(self):
        self.docs = {}
        self.lock = threading.RLock()

    def get(self, key):
        with self.lock:
            return copy.deepcopy(self.docs.get(key))

    def put(self, key, value):
        with self.lock:
            self.docs[key] = copy.deepcopy(value)

    def run(self, keys, operation):
        with self.lock:
            changes, result = operation({key: self.get(key) for key in keys})
            for key, value in changes.items():
                if key not in keys:
                    raise ValueError("Transaction attempted an unread record")
                if value is None:
                    self.docs.pop(key, None)
                else:
                    self.put(key, value)
            return result

    def connections(self, user_id):
        with self.lock:
            return [dict(copy.deepcopy(value), id=key.split("/")[1])
                    for key, value in self.docs.items()
                    if key.startswith("mcp_connections/") and value["user_id"] == user_id]
