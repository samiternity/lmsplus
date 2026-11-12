#!/usr/bin/env python
"""
Test script to verify service initialization.
This script tests that all services are properly initialized in the app factory.
"""

import sys
import os

# Add the app directory to the path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

def test_service_initialization():
    """Test that all services initialize correctly"""
    print("=" * 60)
    print("Testing Service Initialization")
    print("=" * 60)
    
    try:
        # Import the app factory
        from app import create_app
        
        print("\n1. Creating Flask app...")
        app = create_app()
        print("   ✅ Flask app created successfully")
        
        # Test LMS Service
        print("\n2. Testing LMS Service...")
        if hasattr(app, 'lms_service') and app.lms_service is not None:
            print("   ✅ LMS Service initialized")
            print(f"   - Type: {type(app.lms_service).__name__}")
        else:
            print("   ❌ LMS Service NOT initialized")
            return False
        
        # Test Cache Service
        print("\n3. Testing Cache Service...")
        if hasattr(app, 'cache_service') and app.cache_service is not None:
            print("   ✅ Cache Service initialized")
            print(f"   - Type: {type(app.cache_service).__name__}")
            print(f"   - Cache directory: {app.cache_service.cache_dir}")
            print(f"   - Max entries: {app.cache_service.max_memory_entries}")
            print(f"   - Disk cache enabled: {app.cache_service.enable_disk_cache}")
            print(f"   - Stale-while-revalidate: {app.cache_service.enable_stale_while_revalidate}")
            
            # Test cache operations
            print("\n   Testing cache operations...")
            from datetime import timedelta
            app.cache_service.set("test_key", {"test": "data"}, ttl=timedelta(minutes=5))
            result = app.cache_service.get("test_key")
            if result:
                data, is_stale = result
                print(f"   ✅ Cache set/get works (data: {data}, stale: {is_stale})")
            else:
                print("   ❌ Cache set/get failed")
                return False
            
            # Get cache stats
            stats = app.cache_service.get_stats()
            print(f"   - Cache stats: {stats}")
        else:
            print("   ❌ Cache Service NOT initialized")
            return False
        
        # Test Background Service
        print("\n4. Testing Background Service...")
        if hasattr(app, 'background_service') and app.background_service is not None:
            print("   ✅ Background Service initialized")
            print(f"   - Type: {type(app.background_service).__name__}")
            print(f"   - Max workers: {app.background_service.max_workers}")
            print(f"   - Is running: {app.background_service.is_running}")
            
            # Get service stats
            stats = app.background_service.get_stats()
            print(f"   - Service stats: {stats}")
        else:
            print("   ❌ Background Service NOT initialized")
            return False
        
        # Test Dashboard API initialization
        print("\n5. Testing Dashboard API...")
        from app.routes.lms_dashboard_api import dashboard_service
        if dashboard_service is not None:
            print("   ✅ Dashboard Service initialized")
            print(f"   - Type: {type(dashboard_service).__name__}")
            print(f"   - Has LMS service: {dashboard_service.lms_service is not None}")
            print(f"   - Has background service: {dashboard_service.background_service is not None}")
            print(f"   - Has cache service: {dashboard_service.cache is not None}")
        else:
            print("   ❌ Dashboard Service NOT initialized")
            return False
        
        # Test configuration
        print("\n6. Testing Configuration...")
        config_keys = [
            'LMS_CACHE_DIR',
            'LMS_CACHE_MAX_ENTRIES',
            'LMS_CACHE_TTL_MINUTES',
            'LMS_BACKGROUND_WORKERS',
            'LMS_BACKGROUND_MAX_RETRIES',
            'LMS_BACKGROUND_TASK_TIMEOUT',
            'LMS_ENABLE_DISK_CACHE',
            'LMS_ENABLE_STALE_WHILE_REVALIDATE',
            'LMS_ENABLE_AUTO_REFRESH'
        ]
        
        all_present = True
        for key in config_keys:
            if key in app.config:
                print(f"   ✅ {key}: {app.config[key]}")
            else:
                print(f"   ❌ {key}: NOT SET")
                all_present = False
        
        if not all_present:
            return False
        
        # Test cache directory exists
        print("\n7. Testing Cache Directory...")
        cache_dir = app.config['LMS_CACHE_DIR']
        if os.path.exists(cache_dir):
            print(f"   ✅ Cache directory exists: {cache_dir}")
            print(f"   - Is directory: {os.path.isdir(cache_dir)}")
            print(f"   - Is writable: {os.access(cache_dir, os.W_OK)}")
        else:
            print(f"   ❌ Cache directory does not exist: {cache_dir}")
            return False
        
        print("\n" + "=" * 60)
        print("✅ ALL TESTS PASSED!")
        print("=" * 60)
        return True
        
    except Exception as e:
        print(f"\n❌ ERROR: {e}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == '__main__':
    success = test_service_initialization()
    sys.exit(0 if success else 1)
