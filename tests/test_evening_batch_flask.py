#!/usr/bin/env python3
"""
Flask app testing script for evening batch notification system.
Tests the scheduler integration with the Flask app.
"""
import sys
import time
from datetime import datetime
from unittest.mock import patch
import pytz

sys.path.insert(0, '.')

from app import create_app
from app.services.notification_scheduler import (
    get_notification_scheduler,
    PAKISTAN_TZ
)

def test_scheduler_initialization():
    """Test that scheduler initializes with Flask app"""
    print("\n" + "="*60)
    print("TEST: Scheduler Initialization")
    print("="*60)
    
    try:
        app = create_app()
        
        with app.app_context():
            scheduler = get_notification_scheduler()
            
            if scheduler.is_running:
                print("✅ PASS | Scheduler is running")
            else:
                print("❌ FAIL | Scheduler is not running")
                return False
            
            # Check scheduler status
            status = scheduler.get_status()
            print(f"\n📊 Scheduler Status:")
            print(f"  Running: {status['running']}")
            print(f"  Jobs: {len(status['jobs'])}")
            
            for job in status['jobs']:
                print(f"\n  Job: {job['name']}")
                print(f"    ID: {job['id']}")
                print(f"    Next run: {job['next_run']}")
            
            return True
            
    except Exception as e:
        print(f"❌ ERROR: {e}")
        import traceback
        traceback.print_exc()
        return False

def test_manual_trigger_outside_window():
    """Test manual trigger outside evening window (should skip)"""
    print("\n" + "="*60)
    print("TEST: Manual Trigger Outside Window")
    print("="*60)
    
    try:
        app = create_app()
        
        with app.app_context():
            scheduler = get_notification_scheduler()
            
            # Mock time to 2 PM Pakistan time (outside window)
            test_time = datetime(2026, 3, 25, 14, 0, 0, tzinfo=PAKISTAN_TZ)
            
            with patch('app.services.notification_scheduler.datetime') as mock_dt:
                mock_dt.now.return_value = test_time
                
                print(f"Simulating trigger at: {test_time.strftime('%I:%M %p')} Pakistan time")
                print("Expected: Should skip (outside evening window)")
                print("\nTriggering...")
                
                # Trigger the notification job
                scheduler._run_notifications()
                
                print("\n✅ PASS | Execution completed (check logs above for 'Skipping' message)")
            
            return True
            
    except Exception as e:
        print(f"❌ ERROR: {e}")
        import traceback
        traceback.print_exc()
        return False

def test_manual_trigger_inside_window():
    """Test manual trigger inside evening window (should execute)"""
    print("\n" + "="*60)
    print("TEST: Manual Trigger Inside Window")
    print("="*60)
    
    try:
        app = create_app()
        
        with app.app_context():
            scheduler = get_notification_scheduler()
            
            # Mock time to 8:00 PM Pakistan time (inside window)
            test_time = datetime(2026, 3, 25, 20, 0, 0, tzinfo=PAKISTAN_TZ)
            
            with patch('app.services.notification_scheduler.datetime') as mock_dt:
                mock_dt.now.return_value = test_time
                
                print(f"Simulating trigger at: {test_time.strftime('%I:%M %p')} Pakistan time")
                print("Expected: Should execute Batch 1")
                print("\n⚠️  WARNING: This will process real users if database is connected!")
                print("Press Ctrl+C within 5 seconds to cancel...")
                
                try:
                    time.sleep(5)
                except KeyboardInterrupt:
                    print("\n\n❌ Cancelled by user")
                    return False
                
                print("\nTriggering...")
                
                # Trigger the notification job
                scheduler._run_notifications()
                
                print("\n✅ PASS | Execution completed (check logs above for batch details)")
            
            return True
            
    except Exception as e:
        print(f"❌ ERROR: {e}")
        import traceback
        traceback.print_exc()
        return False

def test_all_batches_simulation():
    """Simulate all 4 batches in sequence"""
    print("\n" + "="*60)
    print("TEST: All 4 Batches Simulation")
    print("="*60)
    
    try:
        app = create_app()
        
        with app.app_context():
            scheduler = get_notification_scheduler()
            
            batch_times = [
                (1, datetime(2026, 3, 25, 20, 0, 0, tzinfo=PAKISTAN_TZ)),
                (2, datetime(2026, 3, 25, 20, 15, 0, tzinfo=PAKISTAN_TZ)),
                (3, datetime(2026, 3, 25, 20, 30, 0, tzinfo=PAKISTAN_TZ)),
                (4, datetime(2026, 3, 25, 20, 45, 0, tzinfo=PAKISTAN_TZ)),
            ]
            
            print("⚠️  WARNING: This will process real users 4 times if database is connected!")
            print("Press Ctrl+C within 5 seconds to cancel...")
            
            try:
                time.sleep(5)
            except KeyboardInterrupt:
                print("\n\n❌ Cancelled by user")
                return False
            
            for batch_num, test_time in batch_times:
                print(f"\n{'='*60}")
                print(f"Simulating Batch {batch_num} at {test_time.strftime('%I:%M %p')}")
                print('='*60)
                
                with patch('app.services.notification_scheduler.datetime') as mock_dt:
                    mock_dt.now.return_value = test_time
                    
                    scheduler._run_notifications()
                    
                    print(f"\n✅ Batch {batch_num} completed")
                
                # Small delay between batches
                time.sleep(2)
            
            print("\n" + "="*60)
            print("✅ PASS | All 4 batches simulated")
            print("="*60)
            
            return True
            
    except Exception as e:
        print(f"❌ ERROR: {e}")
        import traceback
        traceback.print_exc()
        return False

def main():
    """Run Flask app tests"""
    print("\n" + "="*60)
    print("EVENING BATCH NOTIFICATION - FLASK APP TESTS")
    print("="*60)
    
    print("\n⚠️  IMPORTANT NOTES:")
    print("1. These tests use the real Flask app and database")
    print("2. If Firestore is connected, real users will be processed")
    print("3. Use a test/staging environment, not production")
    print("4. Tests with real execution have 5-second cancellation window")
    print()
    
    tests = [
        ("Scheduler Initialization", test_scheduler_initialization),
        ("Manual Trigger Outside Window", test_manual_trigger_outside_window),
    ]
    
    # Ask user if they want to run tests with real execution
    print("Do you want to run tests that trigger real execution?")
    print("(This will process users if database is connected)")
    response = input("Type 'yes' to continue, anything else to skip: ")
    
    if response.lower() == 'yes':
        tests.append(("Manual Trigger Inside Window", test_manual_trigger_inside_window))
        tests.append(("All 4 Batches Simulation", test_all_batches_simulation))
    
    results = []
    for name, test_func in tests:
        try:
            result = test_func()
            results.append((name, result))
        except Exception as e:
            print(f"\n❌ Test '{name}' failed with error: {e}")
            results.append((name, False))
    
    # Summary
    print("\n" + "="*60)
    print("TEST SUMMARY")
    print("="*60)
    
    for name, result in results:
        status = "✅ PASS" if result else "❌ FAIL"
        print(f"{status} | {name}")
    
    passed = sum(1 for _, result in results if result)
    total = len(results)
    
    print(f"\nTotal: {passed}/{total} tests passed")
    
    if passed == total:
        print("\n✅ ALL TESTS PASSED")
        return 0
    else:
        print("\n❌ SOME TESTS FAILED")
        return 1

if __name__ == '__main__':
    sys.exit(main())
