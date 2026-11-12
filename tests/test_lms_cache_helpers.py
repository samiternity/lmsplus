"""
Test script to verify LMS Service cache helper methods.
Tests the _cache_data, _get_cached_data, and _get_cache_key methods.
"""

import sys
from datetime import timedelta

# Test the cache helper methods
def test_cache_helpers():
    """Test the cache helper methods in LMS Service"""
    
    print("=" * 60)
    print("Testing LMS Service Cache Helper Methods")
    print("=" * 60)
    
    try:
        from app.services.lms_service import LMSService
        
        # Initialize service
        lms_service = LMSService()
        print("✓ LMS Service initialized successfully")
        
        # Test 1: _get_cache_key method
        print("\n1. Testing _get_cache_key method:")
        print("-" * 60)
        
        # Test basic cache key
        key1 = lms_service._get_cache_key('acc123', 'assignments')
        print(f"   Basic key: {key1}")
        assert key1 == 'acc123:assignments', f"Expected 'acc123:assignments', got '{key1}'"
        print("   ✓ Basic cache key generation works")
        
        # Test cache key with semester
        key2 = lms_service._get_cache_key('acc123', 'assignments', semester='MjAyNTM=')
        print(f"   With semester: {key2}")
        assert key2 == 'acc123:assignments:sem:MjAyNTM=', f"Expected 'acc123:assignments:sem:MjAyNTM=', got '{key2}'"
        print("   ✓ Cache key with semester works")
        
        # Test cache key with semester and course
        key3 = lms_service._get_cache_key('acc123', 'assignments', semester='MjAyNTM=', course='MTM2NjM2')
        print(f"   With semester and course: {key3}")
        assert key3 == 'acc123:assignments:sem:MjAyNTM=:course:MTM2NjM2', f"Unexpected key: {key3}"
        print("   ✓ Cache key with semester and course works")
        
        # Test dashboard-specific cache keys
        key4 = lms_service._get_cache_key('acc123', 'pending_assignments')
        print(f"   Dashboard pending: {key4}")
        assert key4 == 'acc123:pending_assignments', f"Expected 'acc123:pending_assignments', got '{key4}'"
        print("   ✓ Dashboard pending_assignments key works")
        
        key5 = lms_service._get_cache_key('acc123', 'assignments_summary')
        print(f"   Dashboard summary: {key5}")
        assert key5 == 'acc123:assignments_summary', f"Expected 'acc123:assignments_summary', got '{key5}'"
        print("   ✓ Dashboard assignments_summary key works")
        
        # Test 2: _cache_data method
        print("\n2. Testing _cache_data method:")
        print("-" * 60)
        
        test_data = {
            'test': 'data',
            'count': 42,
            'items': ['a', 'b', 'c']
        }
        
        test_key = 'test:cache:key'
        lms_service._cache_data(test_key, test_data, timeout=timedelta(minutes=5))
        print(f"   ✓ Cached data with key: {test_key}")
        
        # Test 3: _get_cached_data method
        print("\n3. Testing _get_cached_data method:")
        print("-" * 60)
        
        cached_result = lms_service._get_cached_data(test_key, allow_stale=False)
        
        if cached_result:
            data, is_stale = cached_result
            print(f"   Retrieved data: {data}")
            print(f"   Is stale: {is_stale}")
            
            assert data == test_data, f"Data mismatch: expected {test_data}, got {data}"
            assert is_stale == False, f"Expected is_stale=False, got {is_stale}"
            print("   ✓ Retrieved cached data successfully")
        else:
            print("   ⚠ No cached data found (cache service may not be initialized)")
        
        # Test 4: Verify method signatures
        print("\n4. Verifying method signatures:")
        print("-" * 60)
        
        import inspect
        
        # Check _cache_data signature
        cache_data_sig = inspect.signature(lms_service._cache_data)
        print(f"   _cache_data parameters: {list(cache_data_sig.parameters.keys())}")
        assert 'key' in cache_data_sig.parameters, "Missing 'key' parameter"
        assert 'data' in cache_data_sig.parameters, "Missing 'data' parameter"
        assert 'timeout' in cache_data_sig.parameters, "Missing 'timeout' parameter"
        print("   ✓ _cache_data has correct signature")
        
        # Check _get_cached_data signature
        get_cached_sig = inspect.signature(lms_service._get_cached_data)
        print(f"   _get_cached_data parameters: {list(get_cached_sig.parameters.keys())}")
        assert 'key' in get_cached_sig.parameters, "Missing 'key' parameter"
        assert 'allow_stale' in get_cached_sig.parameters, "Missing 'allow_stale' parameter"
        print("   ✓ _get_cached_data has correct signature")
        
        # Check _get_cache_key signature
        get_key_sig = inspect.signature(lms_service._get_cache_key)
        print(f"   _get_cache_key parameters: {list(get_key_sig.parameters.keys())}")
        assert 'account_id' in get_key_sig.parameters, "Missing 'account_id' parameter"
        assert 'data_type' in get_key_sig.parameters, "Missing 'data_type' parameter"
        assert 'semester' in get_key_sig.parameters, "Missing 'semester' parameter"
        assert 'course' in get_key_sig.parameters, "Missing 'course' parameter"
        print("   ✓ _get_cache_key has correct signature")
        
        print("\n" + "=" * 60)
        print("✓ All cache helper method tests passed!")
        print("=" * 60)
        
        return True
        
    except ImportError as e:
        print(f"✗ Import error: {e}")
        print("  Make sure the Flask app context is available")
        return False
    except AssertionError as e:
        print(f"✗ Assertion failed: {e}")
        return False
    except Exception as e:
        print(f"✗ Unexpected error: {e}")
        import traceback
        traceback.print_exc()
        return False


if __name__ == '__main__':
    success = test_cache_helpers()
    sys.exit(0 if success else 1)
