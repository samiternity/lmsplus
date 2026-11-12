#!/usr/bin/env python3
"""
Unit tests for LMS Cache Service.
Tests cache set/get operations, TTL expiration, stale detection, LRU eviction,
disk persistence, and thread safety.
"""

import pytest
import time
import threading
import tempfile
import shutil
from datetime import timedelta, datetime
from pathlib import Path

from app.services.lms_cache_service import LMSCacheService, CacheEntry


class TestCacheEntry:
    """Test CacheEntry class"""
    
    def test_cache_entry_creation(self):
        """Test creating a cache entry"""
        entry = CacheEntry('test_key', {'data': 'value'}, timedelta(minutes=5))
        
        assert entry.key == 'test_key'
        assert entry.data == {'data': 'value'}
        assert entry.ttl == timedelta(minutes=5)
        assert entry.access_count == 0
        assert isinstance(entry.created_at, datetime)
        assert isinstance(entry.expires_at, datetime)
    
    def test_cache_entry_expiration(self):
        """Test cache entry expiration detection"""
        # Create entry with 1 second TTL
        entry = CacheEntry('test_key', 'data', timedelta(seconds=1))
        
        # Should not be expired immediately
        assert not entry.is_expired()
        
        # Wait for expiration
        time.sleep(1.1)
        
        # Should be expired now
        assert entry.is_expired()
    
    def test_cache_entry_stale_detection(self):
        """Test stale detection"""
        entry = CacheEntry('test_key', 'data', timedelta(minutes=30))
        
        # Should not be stale with 20 minute threshold
        assert not entry.is_stale(timedelta(minutes=20))
        
        # Simulate old entry
        entry.created_at = datetime.now() - timedelta(minutes=25)
        entry.expires_at = entry.created_at + entry.ttl
        
        # Should be stale now
        assert entry.is_stale(timedelta(minutes=20))
    
    def test_cache_entry_access_tracking(self):
        """Test access count tracking"""
        entry = CacheEntry('test_key', 'data', timedelta(minutes=5))
        
        assert entry.access_count == 0
        
        entry.access()
        assert entry.access_count == 1
        
        entry.access()
        entry.access()
        assert entry.access_count == 3
    
    def test_cache_entry_age(self):
        """Test age calculation"""
        entry = CacheEntry('test_key', 'data', timedelta(minutes=5))
        
        age = entry.get_age()
        assert age.total_seconds() < 1  # Should be very recent
        
        # Simulate old entry
        entry.created_at = datetime.now() - timedelta(minutes=10)
        age = entry.get_age()
        assert age.total_seconds() >= 600  # At least 10 minutes
    
    def test_cache_entry_ttl_remaining(self):
        """Test TTL remaining calculation"""
        entry = CacheEntry('test_key', 'data', timedelta(seconds=10))
        
        remaining = entry.get_ttl_remaining()
        assert 9 <= remaining.total_seconds() <= 10
        
        # Wait a bit
        time.sleep(2)
        remaining = entry.get_ttl_remaining()
        assert 7 <= remaining.total_seconds() <= 9
    
    def test_cache_entry_serialization(self):
        """Test to_dict and from_dict"""
        original = CacheEntry('test_key', {'data': 'value'}, timedelta(minutes=5))
        original.access()
        original.access()
        
        # Serialize
        data_dict = original.to_dict()
        
        # Deserialize
        restored = CacheEntry.from_dict(data_dict)
        
        assert restored.key == original.key
        assert restored.data == original.data
        assert restored.ttl == original.ttl
        assert restored.access_count == original.access_count


class TestCacheServiceBasicOperations:
    """Test basic cache operations"""
    
    @pytest.fixture
    def cache_service(self):
        """Create a temporary cache service for testing"""
        temp_dir = tempfile.mkdtemp()
        service = LMSCacheService(cache_dir=temp_dir)
        yield service
        # Cleanup
        shutil.rmtree(temp_dir, ignore_errors=True)
    
    def test_cache_set_and_get(self, cache_service):
        """Test basic set and get operations"""
        test_data = {'key': 'value', 'number': 42}
        
        cache_service.set('test_key', test_data, ttl=timedelta(minutes=5))
        
        result = cache_service.get('test_key')
        assert result is not None
        
        data, is_stale = result
        assert data == test_data
        assert is_stale is False
    
    def test_cache_get_nonexistent(self, cache_service):
        """Test getting non-existent key"""
        result = cache_service.get('nonexistent_key')
        assert result is None
    
    def test_cache_delete(self, cache_service):
        """Test deleting cache entry"""
        cache_service.set('test_key', 'data', ttl=timedelta(minutes=5))
        
        # Verify it exists
        assert cache_service.get('test_key') is not None
        
        # Delete it
        cache_service.delete('test_key')
        
        # Verify it's gone
        assert cache_service.get('test_key') is None
    
    def test_cache_clear_all(self, cache_service):
        """Test clearing all cache entries"""
        cache_service.set('key1', 'data1', ttl=timedelta(minutes=5))
        cache_service.set('key2', 'data2', ttl=timedelta(minutes=5))
        cache_service.set('key3', 'data3', ttl=timedelta(minutes=5))
        
        # Verify entries exist
        assert cache_service.get('key1') is not None
        assert cache_service.get('key2') is not None
        
        # Clear all
        cache_service.clear()
        
        # Verify all gone
        assert cache_service.get('key1') is None
        assert cache_service.get('key2') is None
        assert cache_service.get('key3') is None
    
    def test_cache_clear_pattern(self, cache_service):
        """Test clearing cache entries by pattern"""
        cache_service.set('user:123:data', 'data1', ttl=timedelta(minutes=5))
        cache_service.set('user:456:data', 'data2', ttl=timedelta(minutes=5))
        cache_service.set('course:789:data', 'data3', ttl=timedelta(minutes=5))
        
        # Clear user entries
        cache_service.clear('user:*')
        
        # Verify user entries gone but course entry remains
        assert cache_service.get('user:123:data') is None
        assert cache_service.get('user:456:data') is None
        assert cache_service.get('course:789:data') is not None


class TestCacheTTLExpiration:
    """Test TTL expiration behavior"""
    
    @pytest.fixture
    def cache_service(self):
        """Create a temporary cache service for testing"""
        temp_dir = tempfile.mkdtemp()
        service = LMSCacheService(cache_dir=temp_dir)
        yield service
        shutil.rmtree(temp_dir, ignore_errors=True)
    
    def test_ttl_expiration(self, cache_service):
        """Test that entries expire after TTL"""
        cache_service.set('test_key', 'data', ttl=timedelta(seconds=1))
        
        # Should exist immediately
        result = cache_service.get('test_key')
        assert result is not None
        
        # Wait for expiration
        time.sleep(1.5)
        
        # Should be expired
        result = cache_service.get('test_key')
        assert result is None
    
    def test_different_ttl_values(self, cache_service):
        """Test different TTL configurations"""
        cache_service.set('instant', 'data', ttl=LMSCacheService.TTL_INSTANT)
        cache_service.set('short', 'data', ttl=LMSCacheService.TTL_SHORT)
        cache_service.set('medium', 'data', ttl=LMSCacheService.TTL_MEDIUM)
        
        # All should exist
        assert cache_service.get('instant') is not None
        assert cache_service.get('short') is not None
        assert cache_service.get('medium') is not None
    
    def test_get_age(self, cache_service):
        """Test getting age of cached data"""
        cache_service.set('test_key', 'data', ttl=timedelta(minutes=5))
        
        age = cache_service.get_age('test_key')
        assert age is not None
        assert age.total_seconds() < 1  # Should be very recent
        
        # Non-existent key
        age = cache_service.get_age('nonexistent')
        assert age is None


class TestStaleDetection:
    """Test stale-while-revalidate functionality"""
    
    @pytest.fixture
    def cache_service(self):
        """Create a temporary cache service for testing"""
        temp_dir = tempfile.mkdtemp()
        service = LMSCacheService(cache_dir=temp_dir)
        service.enable_stale_while_revalidate = True
        yield service
        shutil.rmtree(temp_dir, ignore_errors=True)
    
    def test_stale_detection(self, cache_service):
        """Test is_stale method"""
        cache_service.set('test_key', 'data', ttl=timedelta(minutes=30))
        
        # Should not be stale immediately
        assert not cache_service.is_stale('test_key')
        
        # Manually age the entry
        entry = cache_service.memory_cache['test_key']
        entry.created_at = datetime.now() - timedelta(minutes=25)
        entry.expires_at = entry.created_at + entry.ttl
        
        # Should be stale now
        assert cache_service.is_stale('test_key')
    
    def test_allow_stale_parameter(self, cache_service):
        """Test allow_stale parameter in get()"""
        # Set with long TTL but make it stale
        cache_service.set('test_key', 'data', ttl=timedelta(minutes=30))
        
        # Manually make it stale (old but not expired)
        entry = cache_service.memory_cache['test_key']
        entry.created_at = datetime.now() - timedelta(minutes=25)
        entry.expires_at = entry.created_at + timedelta(minutes=30)  # Still valid
        
        # Without allow_stale, should return fresh data (not expired yet)
        result = cache_service.get('test_key', allow_stale=False)
        assert result is not None
        data, is_stale = result
        assert is_stale is False  # Not expired, so returned as fresh
        
        # Now test with expired entry
        cache_service.set('expired_key', 'data', ttl=timedelta(seconds=1))
        time.sleep(1.5)
        
        # Should return None without allow_stale
        result = cache_service.get('expired_key', allow_stale=False)
        assert result is None
        
        # Should return stale data with allow_stale
        # First, put it back in cache as stale
        cache_service.set('stale_key', 'stale_data', ttl=timedelta(seconds=2))
        entry = cache_service.memory_cache['stale_key']
        entry.created_at = datetime.now() - timedelta(seconds=1.5)
        entry.expires_at = entry.created_at + timedelta(seconds=2)
        
        result = cache_service.get('stale_key', allow_stale=True)
        assert result is not None
    
    def test_stale_not_allowed(self, cache_service):
        """Test that stale data is not returned when allow_stale=False"""
        cache_service.set('test_key', 'data', ttl=timedelta(seconds=1))
        
        # Wait for expiration
        time.sleep(1.5)
        
        # Should not return expired data
        result = cache_service.get('test_key', allow_stale=False)
        assert result is None


class TestLRUEviction:
    """Test LRU eviction policy"""
    
    @pytest.fixture
    def cache_service(self):
        """Create a cache service with small max entries for testing"""
        temp_dir = tempfile.mkdtemp()
        service = LMSCacheService(cache_dir=temp_dir)
        service.max_memory_entries = 5  # Small limit for testing
        yield service
        shutil.rmtree(temp_dir, ignore_errors=True)
    
    def test_lru_eviction(self, cache_service):
        """Test that LRU entries are evicted when cache is full"""
        # Fill cache to limit
        for i in range(5):
            cache_service.set(f'key{i}', f'data{i}', ttl=timedelta(minutes=5))
        
        # All should exist
        for i in range(5):
            assert cache_service.get(f'key{i}') is not None
        
        # Access key0 to make it recently used
        cache_service.get('key0')
        
        # Add one more entry (should trigger eviction)
        cache_service.set('key5', 'data5', ttl=timedelta(minutes=5))
        
        # key1 should be evicted (least recently used)
        # key0 should still exist (recently accessed)
        assert cache_service.get('key0') is not None
        assert cache_service.get('key5') is not None
        
        # Check eviction stats
        stats = cache_service.get_stats()
        assert stats['evictions'] > 0
    
    def test_eviction_count(self, cache_service):
        """Test eviction statistics"""
        initial_stats = cache_service.get_stats()
        initial_evictions = initial_stats['evictions']
        
        # Fill cache beyond limit
        for i in range(10):
            cache_service.set(f'key{i}', f'data{i}', ttl=timedelta(minutes=5))
        
        # Check evictions occurred
        stats = cache_service.get_stats()
        assert stats['evictions'] > initial_evictions
        assert stats['memory_entries'] <= cache_service.max_memory_entries


class TestDiskPersistence:
    """Test disk cache persistence"""
    
    @pytest.fixture
    def cache_service(self):
        """Create a temporary cache service for testing"""
        temp_dir = tempfile.mkdtemp()
        service = LMSCacheService(cache_dir=temp_dir)
        service.enable_disk_cache = True
        yield service
        shutil.rmtree(temp_dir, ignore_errors=True)
    
    def test_disk_persistence(self, cache_service):
        """Test that data is persisted to disk"""
        cache_service.set('test_key', {'data': 'value'}, ttl=timedelta(minutes=5))
        
        # Check that file was created
        cache_files = list(Path(cache_service.cache_dir).glob('*.json'))
        assert len(cache_files) > 0
    
    def test_disk_cache_recovery(self, cache_service):
        """Test recovering data from disk cache"""
        test_data = {'important': 'data'}
        cache_service.set('test_key', test_data, ttl=timedelta(minutes=5))
        
        # Remove from memory cache
        del cache_service.memory_cache['test_key']
        
        # Should recover from disk
        result = cache_service.get('test_key')
        assert result is not None
        data, is_stale = result
        assert data == test_data
        
        # Should be promoted to memory
        assert 'test_key' in cache_service.memory_cache
    
    def test_disk_cache_disabled(self):
        """Test behavior when disk cache is disabled"""
        temp_dir = tempfile.mkdtemp()
        service = LMSCacheService(cache_dir=temp_dir)
        service.enable_disk_cache = False
        
        service.set('test_key', 'data', ttl=timedelta(minutes=5))
        
        # No files should be created
        cache_files = list(Path(service.cache_dir).glob('*.json'))
        assert len(cache_files) == 0
        
        shutil.rmtree(temp_dir, ignore_errors=True)
    
    def test_corrupted_cache_file_handling(self, cache_service):
        """Test handling of corrupted cache files"""
        # Create a corrupted cache file
        cache_file = Path(cache_service.cache_dir) / 'test_key.json'
        with open(cache_file, 'w') as f:
            f.write('invalid json {{{')
        
        # Should handle gracefully
        result = cache_service.get('test_key')
        assert result is None
        
        # Corrupted file should be deleted
        assert not cache_file.exists()
        
        # Check stats
        stats = cache_service.get_stats()
        assert stats['corrupted_entries'] > 0


class TestThreadSafety:
    """Test thread safety of cache operations"""
    
    @pytest.fixture
    def cache_service(self):
        """Create a temporary cache service for testing"""
        temp_dir = tempfile.mkdtemp()
        service = LMSCacheService(cache_dir=temp_dir)
        yield service
        shutil.rmtree(temp_dir, ignore_errors=True)
    
    def test_concurrent_writes(self, cache_service):
        """Test concurrent write operations"""
        def write_data(thread_id):
            for i in range(10):
                cache_service.set(f'thread{thread_id}_key{i}', 
                                f'data{i}', 
                                ttl=timedelta(minutes=5))
        
        # Create multiple threads
        threads = []
        for i in range(5):
            thread = threading.Thread(target=write_data, args=(i,))
            threads.append(thread)
            thread.start()
        
        # Wait for all threads
        for thread in threads:
            thread.join()
        
        # Verify data integrity
        stats = cache_service.get_stats()
        assert stats['total_sets'] == 50  # 5 threads * 10 writes
    
    def test_concurrent_reads(self, cache_service):
        """Test concurrent read operations"""
        # Populate cache
        for i in range(10):
            cache_service.set(f'key{i}', f'data{i}', ttl=timedelta(minutes=5))
        
        results = []
        
        def read_data():
            for i in range(10):
                result = cache_service.get(f'key{i}')
                results.append(result)
        
        # Create multiple threads
        threads = []
        for i in range(5):
            thread = threading.Thread(target=read_data)
            threads.append(thread)
            thread.start()
        
        # Wait for all threads
        for thread in threads:
            thread.join()
        
        # All reads should succeed
        assert all(r is not None for r in results)
    
    def test_concurrent_read_write(self, cache_service):
        """Test concurrent read and write operations"""
        def writer():
            for i in range(20):
                cache_service.set(f'key{i % 10}', f'data{i}', 
                                ttl=timedelta(minutes=5))
                time.sleep(0.01)
        
        def reader():
            for i in range(20):
                cache_service.get(f'key{i % 10}')
                time.sleep(0.01)
        
        # Create threads
        threads = []
        for i in range(3):
            threads.append(threading.Thread(target=writer))
            threads.append(threading.Thread(target=reader))
        
        # Start all threads
        for thread in threads:
            thread.start()
        
        # Wait for completion
        for thread in threads:
            thread.join()
        
        # No crashes = success
        stats = cache_service.get_stats()
        assert stats['total_sets'] > 0
        assert stats['total_gets'] > 0


class TestCacheStatistics:
    """Test cache statistics tracking"""
    
    @pytest.fixture
    def cache_service(self):
        """Create a temporary cache service for testing"""
        temp_dir = tempfile.mkdtemp()
        service = LMSCacheService(cache_dir=temp_dir)
        yield service
        shutil.rmtree(temp_dir, ignore_errors=True)
    
    def test_hit_miss_tracking(self, cache_service):
        """Test hit and miss statistics"""
        # Perform operations
        cache_service.set('key1', 'data1', ttl=timedelta(minutes=5))
        cache_service.get('key1')  # Hit
        cache_service.get('key2')  # Miss
        cache_service.get('key1')  # Hit
        cache_service.get('key3')  # Miss
        
        stats = cache_service.get_stats()
        assert stats['hits'] == 2
        assert stats['misses'] == 2
        assert stats['hit_rate_percent'] == 50.0
    
    def test_operation_counting(self, cache_service):
        """Test operation count statistics"""
        cache_service.set('key1', 'data1', ttl=timedelta(minutes=5))
        cache_service.set('key2', 'data2', ttl=timedelta(minutes=5))
        cache_service.get('key1')
        cache_service.delete('key1')
        cache_service.clear('key*')
        
        stats = cache_service.get_stats()
        assert stats['total_sets'] == 2
        assert stats['total_gets'] == 1
        assert stats['total_deletes'] >= 1
        assert stats['total_clears'] == 1
    
    def test_performance_metrics(self, cache_service):
        """Test performance metrics tracking"""
        # Perform operations
        for i in range(10):
            cache_service.set(f'key{i}', f'data{i}', ttl=timedelta(minutes=5))
            cache_service.get(f'key{i}')
        
        stats = cache_service.get_stats()
        assert stats['avg_get_time_ms'] >= 0
        assert stats['avg_set_time_ms'] >= 0
        assert stats['max_get_time_ms'] >= stats['avg_get_time_ms']
        assert stats['max_set_time_ms'] >= stats['avg_set_time_ms']
    
    def test_stats_structure(self, cache_service):
        """Test that stats contain all expected fields"""
        stats = cache_service.get_stats()
        
        expected_fields = [
            'hits', 'misses', 'stale_hits', 'evictions',
            'total_sets', 'total_gets', 'total_deletes', 'total_clears',
            'memory_entries', 'hit_rate_percent', 'memory_size_mb', 'disk_size_mb',
            'avg_get_time_ms', 'avg_set_time_ms', 'max_get_time_ms', 'max_set_time_ms',
            'max_memory_entries', 'enable_disk_cache', 'enable_stale_while_revalidate',
            'uptime_hours', 'cache_dir'
        ]
        
        for field in expected_fields:
            assert field in stats, f"Missing field: {field}"


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
