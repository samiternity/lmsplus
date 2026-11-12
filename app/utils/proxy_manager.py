import time
import logging
from app.services.firestore_service import get_firestore_service

from app.utils.proxy_transport import ProxyConfigurationError, proxy_workers

logger = logging.getLogger(__name__)


class FirestoreProxyManager:
    """
    Manages a pool of proxy workers with consistent hashing for session stickiness
    and a distributed circuit breaker backed by Firestore.
    """
    
    def __init__(self):
        self.fs = get_firestore_service()
        self.collection = "system_config"
        self.doc_id = "proxy_state"
        self.cooldown_period = 900  # 15 minutes
        
        # In-memory cache for fast reads, but we still poll Firestore occasionally
        # to catch bans from other Cloud Run instances.
        self._local_state = None
        self._last_poll = 0
        self._poll_interval = 60  # Poll Firestore every 60 seconds

    def _get_state(self) -> dict:
        """Fetch the latest proxy state from Firestore or initialize it."""
        now = time.time()
        
        # Use local cache if fresh
        if self._local_state and (now - self._last_poll) < self._poll_interval:
            return self._local_state

        state = None
        if self.fs.is_available():
            # Bypass firestore_service's internal 5min cache to get real-time state!
            state = self.fs.get_document(self.collection, self.doc_id, bypass_cache=True)
            
        if not state:
            state = {
                "workers": list(proxy_workers()),
                "dead_workers": {} # dict of worker_url -> unban_timestamp
            }
            
        self._local_state = state
        self._last_poll = now
        return state

    def _save_state(self, state: dict):
        """Save the updated proxy state to Firestore."""
        self._local_state = state
        self._last_poll = time.time()
        if self.fs.is_available():
            self.fs.save_document(self.collection, self.doc_id, state)

    def get_worker(self, account_id: str) -> str:
        """
        Get the assigned worker for an account using consistent hashing.
        Returns the worker URL, or raises an Exception if all are dead.
        """
        state = self._get_state()
        configured = proxy_workers()
        if not configured:
            raise ProxyConfigurationError("Configure a proxy origin and its service key.")
        workers = [worker for worker in state.get("workers", configured) if worker in configured]
        if not workers:
            workers = list(configured)
        dead_workers = state.get("dead_workers", {})
        
        now = time.time()
        healthy_workers = []
        state_changed = False
        
        for w in workers:
            if w in dead_workers:
                unban_time = dead_workers[w]
                if now >= unban_time:
                    # Half-open state: cooldown period finished, put it back in rotation!
                    logger.info(f"ProxyManager: Cooldown finished for {w}. Returning to rotation.")
                    del dead_workers[w]
                    state_changed = True
                    healthy_workers.append(w)
            else:
                healthy_workers.append(w)
                
        if state_changed:
            self._save_state(state)
            
        if not healthy_workers:
            logger.error("ProxyManager: ALL proxy workers are currently dead!")
            # Fallback: Just try the first one and hope for the best.
            return workers[0]
            
        # Consistent Hashing (Virtual Node Hash Ring)
        # We build a ring of virtual nodes based on the weight of each healthy worker.
        # This perfectly skews traffic toward high-capacity proxies like Cloudflare.
        import hashlib
        
        ring = []
        for w in healthy_workers:
            weight = configured[w]
            for i in range(weight):
                ring.append(f"{w}#vnode{i}")
                
        # Sorting guarantees a deterministic ring order
        ring.sort()
        
        if not ring:
            return workers[0]
        
        # Determine the user's preferred starting position on the ring
        hash_val = int(hashlib.md5(account_id.encode('utf-8')).hexdigest(), 16)
        preferred_index = hash_val % len(ring)
        
        chosen_vnode = ring[preferred_index]
        return chosen_vnode.split('#')[0]

    def mark_dead(self, worker_url: str):
        """
        Circuit breaker trips! Mark the worker as dead and set a cooldown timer.
        """
        state = self._get_state()
        dead_workers = state.get("dead_workers", {})
        
        # Put worker in timeout for 15 minutes
        dead_workers[worker_url] = time.time() + self.cooldown_period
        state["dead_workers"] = dead_workers
        
        logger.warning(f"ProxyManager: Worker {worker_url} marked DEAD until {time.time() + self.cooldown_period}")
        self._save_state(state)

# Singleton instance
_proxy_manager = None

def get_proxy_manager() -> FirestoreProxyManager:
    global _proxy_manager
    if _proxy_manager is None:
        _proxy_manager = FirestoreProxyManager()
    return _proxy_manager

def log_proxy_telemetry(worker_url: str, target_url: str, latency_ms: float, status_code: int):
    """
    Emit a strictly structured JSON string to stdout for Google Cloud Logging.
    Implements intelligent sampling to stay well below GCP limits.
    """
    import json
    import sys
    import random
    
    success = status_code < 400
    
    # INTELLIGENT SAMPLING
    # Sample 5% of successes, but 100% of failures
    sample_rate = 0.05 if success else 1.0
    if random.random() > sample_rate:
        return

    # Extract just the hostname for cleaner analytics (e.g., 'cloudflare', 'vercel')
    import urllib.parse
    parsed_worker = urllib.parse.urlparse(worker_url)
    worker_host = parsed_worker.netloc if parsed_worker.netloc else worker_url

    parsed_target = urllib.parse.urlparse(target_url)
    target_host = parsed_target.netloc if parsed_target.netloc else "unknown"

    telemetry = {
        "severity": "INFO" if success else "WARNING",
        "event_type": "PROXY_TELEMETRY",
        "worker": worker_host,
        "latency_ms": round(latency_ms, 2),
        "status_code": status_code,
        "target_host": target_host,
        "success": success,
        "sample_rate": sample_rate # Useful if you want to multiply log counts by 10 in GCP to estimate true total
    }
    
    # Using print to sys.stdout guarantees Cloud Run sees it as raw JSON
    # without any Python logging prefixes corrupting the JSON parsing.
    print(json.dumps(telemetry), flush=True, file=sys.stdout)
