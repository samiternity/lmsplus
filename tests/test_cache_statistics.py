#!/usr/bin/env python3
"""
Test script for cache statistics and monitoring features.
Verifies that all statistics are being tracked correctly.
"""

import sys
import time
from datetime import timedelta
from pathlib import Path
from tempfile import TemporaryDirectory

# Add app to path
sys.path.insert(0, '.')

from app.services.lms_cache_service import LMSCacheService


def test_cache_statistics(tmp_path):
    """Test cache statistics tracking"""
    print("=" * 60)
    print("Testing Cache Statistics and Monitoring")
    print("=" * 60)
    
    # Create cache service
    cache = LMSCacheService(cache_dir=str(tmp_path))
    
    print("\n1. Testing basic cache operations...")
    
    # Test SET operations
    cache.set('test_key_1', {'data': 'value1'}, ttl=timedelta(minutes=5))
    cache.set('test_key_2', {'data': 'value2'}, ttl=timedelta(minutes=5))
    cache.set('test_key_3', {'data': 'value3'}, ttl=timedelta(minutes=5))
    print("   ✓ Performed 3 SET operations")
    
    # Test GET operations (hits)
    result1 = cache.get('test_key_1')
    result2 = cache.get('test_key_2')
    result3 = cache.get('test_key_3')
    print("   ✓ Performed 3 GET operations (hits)")
    
    # Test GET operations (misses)
    miss1 = cache.get('nonexistent_key_1')
    miss2 = cache.get('nonexistent_key_2')
    print("   ✓ Performed 2 GET operations (misses)")
    
    # Test DELETE operation
    cache.delete('test_key_1')
    print("   ✓ Performed 1 DELETE operation")
    
    # Test CLEAR operation
    cache.clear('test_key_*')
    print("   ✓ Performed 1 CLEAR operation")
    
    print("\n2. Retrieving statistics...")
    stats = cache.get_stats()
    
    print("\n3. Cache Statistics:")
    print("-" * 60)
    print(f"   Total GET operations:    {stats['total_gets']}")
    print(f"   Total SET operations:    {stats['total_sets']}")
    print(f"   Total DELETE operations: {stats['total_deletes']}")
    print(f"   Total CLEAR operations:  {stats['total_clears']}")
    print(f"   Cache hits:              {stats['hits']}")
    print(f"   Cache misses:            {stats['misses']}")
    print(f"   Stale hits:              {stats['stale_hits']}")
    print(f"   Hit rate:                {stats['hit_rate_percent']}%")
    print(f"   Memory entries:          {stats['memory_entries']}")
    print(f"   Memory size:             {stats['memory_size_mb']} MB")
    print(f"   Disk size:               {stats['disk_size_mb']} MB")
    
    print("\n4. Performance Metrics:")
    print("-" * 60)
    print(f"   Avg GET time:            {stats['avg_get_time_ms']:.2f} ms")
    print(f"   Avg SET time:            {stats['avg_set_time_ms']:.2f} ms")
    print(f"   Max GET time:            {stats['max_get_time_ms']:.2f} ms")
    print(f"   Max SET time:            {stats['max_set_time_ms']:.2f} ms")
    
    print("\n5. Service Information:")
    print("-" * 60)
    print(f"   Uptime:                  {stats['uptime_hours']:.2f} hours")
    print(f"   Cache directory:         {stats['cache_dir']}")
    print(f"   Max memory entries:      {stats['max_memory_entries']}")
    print(f"   Disk cache enabled:      {stats['enable_disk_cache']}")
    print(f"   Stale-while-revalidate:  {stats['enable_stale_while_revalidate']}")
    print(f"   Stale threshold:         {stats['stale_threshold_minutes']} minutes")
    
    print("\n6. Error Tracking:")
    print("-" * 60)
    print(f"   Disk read errors:        {stats['disk_read_errors']}")
    print(f"   Disk write errors:       {stats['disk_write_errors']}")
    print(f"   Corrupted entries:       {stats['corrupted_entries']}")
    print(f"   Evictions:               {stats['evictions']}")
    
    # Verify expected values
    print("\n7. Verification:")
    print("-" * 60)
    
    checks = [
        (stats['total_gets'] == 5, "Total GETs should be 5"),
        (stats['total_sets'] == 3, "Total SETs should be 3"),
        (stats['total_deletes'] >= 1, "Total DELETEs should be >= 1 (CLEAR calls DELETE internally)"),
        (stats['total_clears'] == 1, "Total CLEARs should be 1"),
        (stats['hits'] == 3, "Cache hits should be 3"),
        (stats['misses'] == 2, "Cache misses should be 2"),
        (stats['hit_rate_percent'] == 60.0, "Hit rate should be 60%"),
        (stats['avg_get_time_ms'] >= 0, "Avg GET time should be >= 0"),
        (stats['avg_set_time_ms'] >= 0, "Avg SET time should be >= 0"),
    ]
    
    all_passed = True
    for passed, message in checks:
        status = "✓" if passed else "✗"
        print(f"   {status} {message}")
        if not passed:
            all_passed = False
    
    print("\n" + "=" * 60)
    if all_passed:
        print("✓ All tests passed!")
        print("=" * 60)
        return 0
    else:
        print("✗ Some tests failed!")
        print("=" * 60)
        return 1


def test_performance_tracking(tmp_path):
    """Test performance metrics tracking"""
    print("\n" + "=" * 60)
    print("Testing Performance Metrics Tracking")
    print("=" * 60)
    
    cache = LMSCacheService(cache_dir=str(tmp_path))
    
    print("\n1. Performing multiple operations to test rolling average...")
    
    # Perform many operations to test rolling window
    for i in range(10):
        cache.set(f'perf_test_{i}', {'data': f'value{i}'})
        cache.get(f'perf_test_{i}')
    
    print("   ✓ Performed 10 SET and 10 GET operations")
    
    stats = cache.get_stats()
    
    print("\n2. Performance Metrics After 10 Operations:")
    print("-" * 60)
    print(f"   Avg GET time:            {stats['avg_get_time_ms']:.2f} ms")
    print(f"   Avg SET time:            {stats['avg_set_time_ms']:.2f} ms")
    print(f"   Max GET time:            {stats['max_get_time_ms']:.2f} ms")
    print(f"   Max SET time:            {stats['max_set_time_ms']:.2f} ms")
    
    # Verify metrics are reasonable
    print("\n3. Verification:")
    print("-" * 60)
    
    checks = [
        (stats['avg_get_time_ms'] < 100, "Avg GET time should be < 100ms"),
        (stats['avg_set_time_ms'] < 100, "Avg SET time should be < 100ms"),
        (stats['max_get_time_ms'] >= stats['avg_get_time_ms'], "Max GET >= Avg GET"),
        (stats['max_set_time_ms'] >= stats['avg_set_time_ms'], "Max SET >= Avg SET"),
    ]
    
    all_passed = True
    for passed, message in checks:
        status = "✓" if passed else "✗"
        print(f"   {status} {message}")
        if not passed:
            all_passed = False
    
    print("\n" + "=" * 60)
    if all_passed:
        print("✓ Performance tracking tests passed!")
        print("=" * 60)
        return 0
    else:
        print("✗ Some performance tests failed!")
        print("=" * 60)
        return 1


if __name__ == '__main__':
    try:
        with TemporaryDirectory() as cache_dir:
            result1 = test_cache_statistics(Path(cache_dir) / "statistics")
            result2 = test_performance_tracking(Path(cache_dir) / "performance")
        
        print("\n" + "=" * 60)
        print("FINAL RESULTS")
        print("=" * 60)
        
        if result1 == 0 and result2 == 0:
            print("✓ All tests passed successfully!")
            print("\nCache statistics and monitoring features are working correctly.")
            print("\nNext steps:")
            print("1. Start the Flask application")
            print("2. Navigate to /lms/monitoring to view the dashboard")
            print("3. Test the API endpoint: GET /lms/api/dashboard/cache-stats")
            sys.exit(0)
        else:
            print("✗ Some tests failed!")
            print("\nPlease review the output above for details.")
            sys.exit(1)
            
    except Exception as e:
        print(f"\n✗ Error running tests: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
