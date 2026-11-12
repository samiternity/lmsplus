# app/services/lms_cache_service.py
"""
Advanced caching service for LMS data with multi-layer strategy.
Provides in-memory and persistent caching with TTL management.
"""

import json
import os
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any, Tuple
import logging
from pathlib import Path
import threading

from app.utils.lms_logger import log_info, log_warning, log_debug, log_error, log_performance

logger = logging.getLogger(__name__)


class CacheEntry:
    """Represents a cached data entry with metadata"""
    
    def __init__(self, key: str, data: Any, ttl: timedelta, 
                 created_at: Optional[datetime] = None):
        self.key = key
        self.data = data
        self.ttl = ttl
        self.created_at = created_at or datetime.now()
        self.expires_at = self.created_at + ttl
        self.access_count = 0
        self.last_accessed = self.created_at
    
    def is_expired(self) -> bool:
        """Check if cache entry has expired"""
        return datetime.now() > self.expires_at
    
    def is_stale(self, stale_threshold: timedelta) -> bool:
        """Check if cache entry is stale (old but not expired)"""
        age = datetime.now() - self.created_at
        return age > stale_threshold
    
    def access(self):
        """Mark entry as accessed"""
        self.access_count += 1
        self.last_accessed = datetime.now()
    
    def get_age(self) -> timedelta:
        """Get age of cache entry"""
        return datetime.now() - self.created_at
    
    def get_ttl_remaining(self) -> timedelta:
        """Get remaining TTL"""
        remaining = self.expires_at - datetime.now()
        return remaining if remaining.total_seconds() > 0 else timedelta(0)
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for persistence"""
        return {
            'key': self.key,
            'data': self.data,
            'ttl_seconds': self.ttl.total_seconds(),
            'created_at': self.created_at.isoformat(),
            'expires_at': self.expires_at.isoformat(),
            'access_count': self.access_count,
            'last_accessed': self.last_accessed.isoformat()
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'CacheEntry':
        """Create from dictionary"""
        entry = cls(
            key=data['key'],
            data=data['data'],
            ttl=timedelta(seconds=data['ttl_seconds']),
            created_at=datetime.fromisoformat(data['created_at'])
        )
        entry.expires_at = datetime.fromisoformat(data['expires_at'])
        entry.access_count = data.get('access_count', 0)
        entry.last_accessed = datetime.fromisoformat(data.get('last_accessed', data['created_at']))
        return entry


class LMSCacheService:
    """
    Multi-layer caching service for LMS data.
    
    Layers:
    1. Memory cache (fastest, volatile)
    2. Disk cache (persistent, slower)
    3. Stale cache (serve stale while refreshing)
    """
    
    # Cache TTL configurations
    TTL_INSTANT = timedelta(seconds=30)      # For rapid requests
    TTL_SHORT = timedelta(minutes=5)         # For active session
    TTL_MEDIUM = timedelta(minutes=30)       # For dashboard widgets
    TTL_LONG = timedelta(hours=2)            # For metadata/options
    TTL_VERY_LONG = timedelta(hours=24)      # For rarely changing data
    
    # Stale threshold (serve stale data while refreshing)
    STALE_THRESHOLD = timedelta(minutes=20)
    
    def __init__(self, cache_dir: str = 'app/data/cache'):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        
        # In-memory cache
        self.memory_cache: Dict[str, CacheEntry] = {}
        self.cache_lock = threading.RLock()
        
        # Cache statistics
        self.stats = {
            'hits': 0,
            'misses': 0,
            'stale_hits': 0,
            'evictions': 0,
            'disk_reads': 0,
            'disk_writes': 0,
            'disk_read_errors': 0,
            'disk_write_errors': 0,
            'corrupted_entries': 0,
            'total_sets': 0,
            'total_gets': 0,
            'total_deletes': 0,
            'total_clears': 0
        }
        
        # Performance metrics
        self.performance_metrics = {
            'get_times': [],  # Last 100 get operation times
            'set_times': [],  # Last 100 set operation times
            'avg_get_time_ms': 0.0,
            'avg_set_time_ms': 0.0,
            'max_get_time_ms': 0.0,
            'max_set_time_ms': 0.0
        }
        
        # Service start time
        self.start_time = datetime.now()
        
        # Configuration
        self.max_memory_entries = 1000
        self.enable_disk_cache = True
        self.enable_stale_while_revalidate = True
        
        # Start cleanup thread
        self._start_cleanup_thread()
    
    def get(self, key: str, allow_stale: bool = False) -> Optional[Tuple[Any, bool]]:
        """
        Get data from cache.
        
        Args:
            key: Cache key
            allow_stale: Whether to return stale data
            
        Returns:
            Tuple of (data, is_stale) or None if not found
        """
        start_time = datetime.now()
        try:
            with self.cache_lock:
                self.stats['total_gets'] += 1
                
                # Try memory cache first
                entry = self.memory_cache.get(key)
                
                if entry:
                    if not entry.is_expired():
                        # Fresh data
                        entry.access()
                        self.stats['hits'] += 1
                        log_debug(f"Cache HIT (memory): {key}", 
                                 age_seconds=entry.get_age().total_seconds())
                        return (entry.data, False)
                    
                    elif allow_stale and self.enable_stale_while_revalidate:
                        # Stale but acceptable
                        entry.access()
                        self.stats['stale_hits'] += 1
                        log_debug(f"Cache STALE HIT (memory): {key}",
                                 age_seconds=entry.get_age().total_seconds())
                        return (entry.data, True)
                    
                    else:
                        # Expired, remove from memory
                        del self.memory_cache[key]
                
                # Try disk cache
                if self.enable_disk_cache:
                    disk_entry = self._load_from_disk(key)
                    if disk_entry:
                        if not disk_entry.is_expired():
                            # Fresh data from disk, promote to memory
                            self.memory_cache[key] = disk_entry
                            disk_entry.access()
                            self.stats['hits'] += 1
                            self.stats['disk_reads'] += 1
                            log_debug(f"Cache HIT (disk): {key}",
                                     age_seconds=disk_entry.get_age().total_seconds())
                            return (disk_entry.data, False)
                        
                        elif allow_stale and self.enable_stale_while_revalidate:
                            # Stale disk data
                            self.memory_cache[key] = disk_entry
                            disk_entry.access()
                            self.stats['stale_hits'] += 1
                            self.stats['disk_reads'] += 1
                            log_debug(f"Cache STALE HIT (disk): {key}",
                                     age_seconds=disk_entry.get_age().total_seconds())
                            return (disk_entry.data, True)
                
                # Cache miss
                self.stats['misses'] += 1
                log_debug(f"Cache MISS: {key}")
                return None
        
        except Exception as e:
            log_error(f"Error retrieving from cache: {e}",
                     error_code="CACHE_GET_ERROR", key=key, exc_info=True)
            # Return None on error to allow fallback to fresh fetch
            return None
        finally:
            # Track performance
            duration_ms = (datetime.now() - start_time).total_seconds() * 1000
            self._record_performance('get', duration_ms)
    
    def set(self, key: str, data: Any, ttl: timedelta = TTL_MEDIUM):
        """
        Store data in cache.
        
        Args:
            key: Cache key
            data: Data to cache
            ttl: Time to live
        """
        start_time = datetime.now()
        try:
            with self.cache_lock:
                self.stats['total_sets'] += 1
                
                # Create cache entry
                entry = CacheEntry(key, data, ttl)
                
                # Store in memory
                self.memory_cache[key] = entry
                
                # Evict old entries if needed
                self._evict_if_needed()
                
                # Store on disk
                if self.enable_disk_cache:
                    self._save_to_disk(entry)
                    self.stats['disk_writes'] += 1
                
                log_debug(f"Cache SET: {key}", ttl_seconds=ttl.total_seconds())
        
        except Exception as e:
            log_error(f"Error storing in cache: {e}",
                     error_code="CACHE_SET_ERROR", key=key, exc_info=True)
            # Don't raise - caching failure shouldn't break the application
        finally:
            # Track performance
            duration_ms = (datetime.now() - start_time).total_seconds() * 1000
            self._record_performance('set', duration_ms)
    
    def delete(self, key: str):
        """Delete entry from cache"""
        try:
            with self.cache_lock:
                self.stats['total_deletes'] += 1
                
                # Remove from memory
                if key in self.memory_cache:
                    del self.memory_cache[key]
                
                # Remove from disk
                if self.enable_disk_cache:
                    self._delete_from_disk(key)
                
                log_debug(f"Cache DELETE: {key}")
        
        except Exception as e:
            log_error(f"Error deleting from cache: {e}",
                     error_code="CACHE_DELETE_ERROR", key=key, exc_info=True)
    
    def clear(self, pattern: Optional[str] = None):
        """
        Clear cache entries.
        
        Args:
            pattern: Optional pattern to match keys (e.g., "pending_*")
        """
        try:
            with self.cache_lock:
                self.stats['total_clears'] += 1
                
                if pattern:
                    # Clear matching keys
                    keys_to_delete = [k for k in self.memory_cache.keys() 
                                     if self._matches_pattern(k, pattern)]
                    for key in keys_to_delete:
                        self.delete(key)
                    log_info(f"Cleared {len(keys_to_delete)} cache entries matching '{pattern}'")
                else:
                    # Clear all
                    count = len(self.memory_cache)
                    self.memory_cache.clear()
                    
                    # Clear disk cache
                    if self.enable_disk_cache:
                        for cache_file in self.cache_dir.glob('*.json'):
                            try:
                                cache_file.unlink()
                            except Exception as e:
                                log_warning(f"Failed to delete cache file {cache_file}: {e}")
                    
                    log_info(f"Cleared all {count} cache entries")
        
        except Exception as e:
            log_error(f"Error clearing cache: {e}",
                     error_code="CACHE_CLEAR_ERROR", pattern=pattern, exc_info=True)
    
    def get_age(self, key: str) -> Optional[timedelta]:
        """Get age of cached data"""
        with self.cache_lock:
            entry = self.memory_cache.get(key)
            if entry:
                return entry.get_age()
            
            # Check disk
            if self.enable_disk_cache:
                disk_entry = self._load_from_disk(key)
                if disk_entry:
                    return disk_entry.get_age()
            
            return None
    
    def is_stale(self, key: str) -> bool:
        """Check if cached data is stale"""
        age = self.get_age(key)
        if age is None:
            return True
        return age > self.STALE_THRESHOLD
    
    def get_stats(self) -> Dict[str, Any]:
        """Get comprehensive cache statistics"""
        with self.cache_lock:
            total_requests = self.stats['hits'] + self.stats['misses']
            hit_rate = (self.stats['hits'] / total_requests * 100) if total_requests > 0 else 0
            stale_hit_rate = (self.stats['stale_hits'] / total_requests * 100) if total_requests > 0 else 0
            
            # Calculate uptime
            uptime = datetime.now() - self.start_time
            uptime_hours = uptime.total_seconds() / 3600
            
            # Calculate disk cache size
            disk_size_mb = self._estimate_disk_size()
            
            return {
                # Basic stats
                **self.stats,
                'memory_entries': len(self.memory_cache),
                'total_requests': total_requests,
                'hit_rate_percent': round(hit_rate, 2),
                'stale_hit_rate_percent': round(stale_hit_rate, 2),
                'memory_size_mb': self._estimate_memory_size(),
                'disk_size_mb': disk_size_mb,
                
                # Performance metrics
                'avg_get_time_ms': round(self.performance_metrics['avg_get_time_ms'], 2),
                'avg_set_time_ms': round(self.performance_metrics['avg_set_time_ms'], 2),
                'max_get_time_ms': round(self.performance_metrics['max_get_time_ms'], 2),
                'max_set_time_ms': round(self.performance_metrics['max_set_time_ms'], 2),
                
                # Configuration
                'max_memory_entries': self.max_memory_entries,
                'enable_disk_cache': self.enable_disk_cache,
                'enable_stale_while_revalidate': self.enable_stale_while_revalidate,
                'stale_threshold_minutes': self.STALE_THRESHOLD.total_seconds() / 60,
                
                # Service info
                'uptime_hours': round(uptime_hours, 2),
                'start_time': self.start_time.isoformat(),
                'cache_dir': str(self.cache_dir)
            }
    
    def _evict_if_needed(self):
        """Evict old entries if cache is full"""
        if len(self.memory_cache) <= self.max_memory_entries:
            return
        
        # Evict least recently used entries
        entries = sorted(self.memory_cache.values(), 
                        key=lambda e: e.last_accessed)
        
        evict_count = len(self.memory_cache) - self.max_memory_entries
        for entry in entries[:evict_count]:
            del self.memory_cache[entry.key]
            self.stats['evictions'] += 1
        
        log_debug(f"Evicted {evict_count} cache entries")
    
    def _save_to_disk(self, entry: CacheEntry):
        """Save cache entry to disk"""
        try:
            cache_file = self.cache_dir / f"{self._sanitize_key(entry.key)}.json"
            with open(cache_file, 'w') as f:
                json.dump(entry.to_dict(), f)
        except Exception as e:
            self.stats['disk_write_errors'] += 1
            log_error(f"Failed to save cache to disk: {e}",
                     error_code="CACHE_DISK_WRITE_ERROR", key=entry.key, exc_info=True)
            # Don't raise - disk cache failure shouldn't break the application
    
    def _load_from_disk(self, key: str) -> Optional[CacheEntry]:
        """Load cache entry from disk"""
        try:
            cache_file = self.cache_dir / f"{self._sanitize_key(key)}.json"
            if not cache_file.exists():
                return None
            
            with open(cache_file, 'r') as f:
                data = json.load(f)
            
            return CacheEntry.from_dict(data)
        except json.JSONDecodeError as e:
            self.stats['corrupted_entries'] += 1
            log_error(f"Corrupted cache file detected: {e}",
                     error_code="CACHE_CORRUPTED", key=key)
            # Delete corrupted cache file
            try:
                cache_file = self.cache_dir / f"{self._sanitize_key(key)}.json"
                if cache_file.exists():
                    cache_file.unlink()
                    log_info(f"Deleted corrupted cache file for key: {key}")
            except Exception as del_error:
                log_warning(f"Failed to delete corrupted cache file: {del_error}")
            return None
        except Exception as e:
            self.stats['disk_read_errors'] += 1
            log_error(f"Failed to load cache from disk: {e}",
                     error_code="CACHE_DISK_READ_ERROR", key=key, exc_info=True)
            return None
    
    def _delete_from_disk(self, key: str):
        """Delete cache entry from disk"""
        try:
            cache_file = self.cache_dir / f"{self._sanitize_key(key)}.json"
            if cache_file.exists():
                cache_file.unlink()
        except Exception as e:
            log_error(f"Failed to delete cache from disk: {e}",
                     error_code="CACHE_DISK_DELETE_ERROR", key=key, exc_info=True)
    
    def _sanitize_key(self, key: str) -> str:
        """Sanitize cache key for use as filename"""
        return key.replace(':', '_').replace('/', '_').replace('\\', '_')
    
    def _matches_pattern(self, key: str, pattern: str) -> bool:
        """Check if key matches pattern (simple wildcard matching)"""
        if '*' not in pattern:
            return key == pattern
        
        # Simple wildcard matching
        pattern_parts = pattern.split('*')
        if not key.startswith(pattern_parts[0]):
            return False
        if not key.endswith(pattern_parts[-1]):
            return False
        return True
    
    def _estimate_memory_size(self) -> float:
        """Estimate memory cache size in MB"""
        try:
            import sys
            total_size = sum(sys.getsizeof(entry.data) for entry in self.memory_cache.values())
            return round(total_size / (1024 * 1024), 2)
        except:
            return 0.0
    
    def _estimate_disk_size(self) -> float:
        """Estimate disk cache size in MB"""
        try:
            total_size = 0
            for cache_file in self.cache_dir.glob('*.json'):
                try:
                    total_size += cache_file.stat().st_size
                except:
                    pass
            return round(total_size / (1024 * 1024), 2)
        except:
            return 0.0
    
    def _record_performance(self, operation: str, duration_ms: float):
        """
        Record performance metrics for cache operations.
        
        Args:
            operation: 'get' or 'set'
            duration_ms: Duration in milliseconds
        """
        try:
            if operation == 'get':
                times_list = self.performance_metrics['get_times']
                times_list.append(duration_ms)
                
                # Keep only last 100 measurements
                if len(times_list) > 100:
                    times_list.pop(0)
                
                # Update averages and max
                self.performance_metrics['avg_get_time_ms'] = sum(times_list) / len(times_list)
                self.performance_metrics['max_get_time_ms'] = max(times_list)
                
            elif operation == 'set':
                times_list = self.performance_metrics['set_times']
                times_list.append(duration_ms)
                
                # Keep only last 100 measurements
                if len(times_list) > 100:
                    times_list.pop(0)
                
                # Update averages and max
                self.performance_metrics['avg_set_time_ms'] = sum(times_list) / len(times_list)
                self.performance_metrics['max_set_time_ms'] = max(times_list)
        except Exception as e:
            # Don't let performance tracking break the cache
            log_debug(f"Error recording performance metric: {e}")
    
    def _cleanup_expired(self):
        """Remove expired entries from cache"""
        with self.cache_lock:
            expired_keys = [key for key, entry in self.memory_cache.items() 
                           if entry.is_expired()]
            
            for key in expired_keys:
                del self.memory_cache[key]
            
            if expired_keys:
                log_debug(f"Cleaned up {len(expired_keys)} expired cache entries")
            
            # Cleanup disk cache
            if self.enable_disk_cache:
                for cache_file in self.cache_dir.glob('*.json'):
                    try:
                        with open(cache_file, 'r') as f:
                            data = json.load(f)
                        
                        expires_at = datetime.fromisoformat(data['expires_at'])
                        if datetime.now() > expires_at:
                            cache_file.unlink()
                    except:
                        pass
    
    def _start_cleanup_thread(self):
        """Start background thread for periodic cleanup"""
        def cleanup_loop():
            while True:
                try:
                    import time
                    time.sleep(300)  # Run every 5 minutes
                    self._cleanup_expired()
                except Exception as e:
                    log_warning(f"Error in cleanup thread: {e}")
        
        cleanup_thread = threading.Thread(target=cleanup_loop, daemon=True)
        cleanup_thread.start()
        log_info("Cache cleanup thread started")


# Global cache instance
_cache_service: Optional[LMSCacheService] = None


def get_cache_service() -> LMSCacheService:
    """Get or create global cache service instance"""
    global _cache_service
    if _cache_service is None:
        _cache_service = LMSCacheService()
    return _cache_service
