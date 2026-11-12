import os
import logging
import firebase_admin
from firebase_admin import credentials, firestore
from pathlib import Path
from typing import Dict, Any, List, Optional, Union
import time

from app.config import get_config
from app.services.budget_monitor import get_budget_monitor

logger = logging.getLogger(__name__)

class FirestoreService:
    """
    Wrapper for Firestore operations with cost safety checks.
    """
    
    def __init__(self):
        self.db = None
        self._initialized = False
        self.monitor = get_budget_monitor()
        self.init_error = None
        self._cache = {}
        self._cache_ttl = 300  # 5 minutes
        if not get_config().is_local:
            self._initialize()

    def _initialize(self):
        """Initialize Firebase Admin SDK and Firestore client"""
        try:
            # Check if already initialized
            if not firebase_admin._apps:
                configured_key = os.environ.get('GOOGLE_APPLICATION_CREDENTIALS')
                if configured_key:
                    key_path = Path(__file__).resolve().parents[2] / configured_key
                    firebase_admin.initialize_app(credentials.Certificate(str(key_path)))
                else:
                    project_id = get_config().firebase_project_id
                    if not project_id:
                        return
                    firebase_admin.initialize_app(options={'projectId': project_id})

            self.db = firestore.client()
            self._initialized = True
            logger.info("FirestoreService: Initialized successfully")
            
        except Exception as e:
            logger.warning(f"FirestoreService: Failed to initialize ({str(e)}). Fallback to local might be needed.")
            self.init_error = str(e)
            self._initialized = False

    def is_available(self) -> bool:
        if os.environ.get('PYTEST_CURRENT_TEST'):
            return False
        return self._initialized

    def save_document(self, collection: str, doc_id: str, data: Dict[str, Any], merge: bool = False) -> bool:
        """Save a document to Firestore"""
        if not self.is_available():
            return False
            
        try:
            # Check budget
            self.monitor.check_can_proceed('write')
            
            # Use set() without merge by default so the entire document is replaced.
            # Pass merge=True for aggregate documents (e.g. user_dashboards) where
            # independent services update specific snapshot fields without overwriting each other.
            doc_ref = self.db.collection(collection).document(doc_id)
            if merge:
                doc_ref.set(data, merge=True)
            else:
                doc_ref.set(data)
            
            # Invalidate cache
            cache_key = f"{collection}/{doc_id}"
            if cache_key in self._cache:
                del self._cache[cache_key]
            
            count_key = f"count_{collection}"
            if count_key in self._cache:
                del self._cache[count_key]
            
            # Log cost
            self.monitor.log_usage('firestore_writes_daily')
            return True
            
        except Exception as e:
            logger.error(f"Firestore save error ({collection}/{doc_id}): {e}")
            return False

    def get_document(self, collection: str, doc_id: str, bypass_cache: bool = False) -> Optional[Dict[str, Any]]:
        """Get a document from Firestore"""
        if not self.is_available():
            return None
            
        try:
            cache_key = f"{collection}/{doc_id}"
            
            # Check cache
            if not bypass_cache and cache_key in self._cache:
                entry = self._cache[cache_key]
                if time.time() < entry['expires_at']:
                    return entry['data']
                else:
                    del self._cache[cache_key]
            
            # Check budget
            self.monitor.check_can_proceed('read')
            
            doc_ref = self.db.collection(collection).document(doc_id)
            doc = doc_ref.get()
            
            # Log cost
            self.monitor.log_usage('firestore_reads_daily')
            
            if doc.exists:
                data = doc.to_dict()
                
                # Update cache (only cache if we have room to prevent memory leaks)
                if len(self._cache) > 5000:
                    # Clear some space (remove 20% oldest entries)
                    keys_to_del = list(self._cache.keys())[:1000]
                    for k in keys_to_del:
                        if k in self._cache:
                            del self._cache[k]
                            
                self._cache[cache_key] = {
                    'data': data,
                    'expires_at': time.time() + self._cache_ttl
                }
                
                return data
            return None
            
        except Exception as e:
            logger.error(f"Firestore get error ({collection}/{doc_id}): {e}")
            return None

    def list_collection(self, collection: str) -> List[Dict[str, Any]]:
        """Return all documents in a collection as list of (id, data) tuples"""
        if not self.is_available():
            return []
        try:
            self.monitor.check_can_proceed('read')
            docs = self.db.collection(collection).stream()
            result = [(doc.id, doc.to_dict()) for doc in docs]
            self.monitor.log_usage('firestore_reads_daily')
            return result
        except Exception as e:
            logger.error(f"Firestore list error ({collection}): {e}")
            return []

    def delete_document(self, collection: str, doc_id: str) -> bool:
        """Delete a document"""
        if not self.is_available():
            return False
            
        try:
            self.monitor.check_can_proceed('write')
            self.db.collection(collection).document(doc_id).delete()
            
            # Invalidate cache
            cache_key = f"{collection}/{doc_id}"
            if cache_key in self._cache:
                del self._cache[cache_key]
            
            count_key = f"count_{collection}"
            if count_key in self._cache:
                del self._cache[count_key]
                
            self.monitor.log_usage('firestore_writes_daily')
            return True
        except Exception as e:
            logger.error(f"Firestore delete error: {e}")
            return False

    def get_collection_count(self, collection: str) -> int:
        """Get total number of documents in a collection"""
        if not self.is_available():
            return 0
            
        cache_key = f"count_{collection}"
        
        # Check cache
        if cache_key in self._cache:
            entry = self._cache[cache_key]
            if time.time() < entry['expires_at']:
                # Use cache if safe. Bypass ONLY between 495-499 to prevent race conditions.
                # If >= 500, USE cache to protect DB from spam when system is full.
                if entry['data'] < 495 or entry['data'] >= 500:
                    return entry['data']
            else:
                del self._cache[cache_key]
                
        try:
            self.monitor.check_can_proceed('read')
            query = self.db.collection(collection).count()
            results = query.get()
            
            count = 0
            for result in results:
                # Handle different python firestore SDK versions safely
                if hasattr(result, 'value'):
                    count = result.value
                elif isinstance(result, (list, tuple)) and len(result) > 0 and hasattr(result[0], 'value'):
                    count = result[0].value
                break
                
            self.monitor.log_usage('firestore_reads_daily')
            
            # Cache for 60 seconds
            self._cache[cache_key] = {
                'data': count,
                'expires_at': time.time() + 60
            }
            
            return count
        except Exception as e:
            logger.error(f"Firestore count error ({collection}): {e}")
            return -1

_firestore_service = FirestoreService()

def get_firestore_service():
    return _firestore_service
