#!/usr/bin/env python3
"""
Test Web Push Notifications
Diagnoses issues with FCM web-push notifications on mobile and desktop.
"""
import os
import sys
import requests
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
load_dotenv()

def test_firebase_config():
    """Test if Firebase configuration is properly set"""
    print("\n" + "="*60)
    print("1. TESTING FIREBASE CONFIGURATION")
    print("="*60)
    
    required_vars = [
        'FIREBASE_API_KEY',
        'FIREBASE_APP_ID',
        'FIREBASE_VAPID_KEY',
        'FIREBASE_PROJECT_ID',
        'FIREBASE_MESSAGING_SENDER_ID'
    ]
    
    missing = []
    for var in required_vars:
        value = os.environ.get(var)
        if not value:
            missing.append(var)
            print(f"❌ {var}: MISSING")
        else:
            # Show first 20 chars for security
            display_value = value[:20] + "..." if len(value) > 20 else value
            print(f"✅ {var}: {display_value}")
    
    if missing:
        print(f"\n⚠️  Missing environment variables: {', '.join(missing)}")
        print("   Set these in your .env file")
        return False
    
    print("\n✅ All Firebase environment variables are set")
    return True


def test_firebase_config_endpoint():
    """Test if the Firebase config endpoint is accessible"""
    print("\n" + "="*60)
    print("2. TESTING FIREBASE CONFIG ENDPOINT")
    print("="*60)
    
    try:
        # Test local server
        response = requests.get('http://localhost:5000/api/firebase-config', timeout=5)
        
        if response.status_code == 200:
            config = response.json()
            print(f"✅ Config endpoint accessible: {response.status_code}")
            print(f"\nReturned config:")
            for key, value in config.items():
                if value:
                    display_value = value[:20] + "..." if len(value) > 20 else value
                    print(f"  {key}: {display_value}")
                else:
                    print(f"  {key}: EMPTY ❌")
            
            # Check if critical fields are present
            if not config.get('apiKey') or not config.get('appId') or not config.get('vapidKey'):
                print("\n⚠️  Critical fields are empty in config response")
                return False
            
            return True
        else:
            print(f"❌ Config endpoint returned status: {response.status_code}")
            return False
            
    except requests.exceptions.ConnectionError:
        print("❌ Cannot connect to http://localhost:5000")
        print("   Make sure Flask server is running: python run.py")
        return False
    except Exception as e:
        print(f"❌ Error testing config endpoint: {e}")
        return False


def test_fcm_token_registration():
    """Test if FCM token registration endpoint works"""
    print("\n" + "="*60)
    print("3. TESTING FCM TOKEN REGISTRATION ENDPOINT")
    print("="*60)
    
    print("⚠️  This test requires authentication")
    print("   You need to be logged in to test token registration")
    print("   Test this manually in the browser console:")
    print("\n   1. Open browser DevTools (F12)")
    print("   2. Go to Console tab")
    print("   3. Run this command:")
    print("""
   fetch('/api/register-fcm-token', {
       method: 'POST',
       headers: {'Content-Type': 'application/json'},
       body: JSON.stringify({token: 'test-token-123'})
   }).then(r => r.json()).then(console.log)
   """)
    print("\n   Expected response: {success: true}")
    
    return None  # Cannot test without auth


def test_service_worker():
    """Test if service worker is accessible"""
    print("\n" + "="*60)
    print("4. TESTING SERVICE WORKER")
    print("="*60)
    
    try:
        response = requests.get('http://localhost:5000/firebase-messaging-sw.js', timeout=5)
        
        if response.status_code == 200:
            print(f"✅ Service worker accessible: {response.status_code}")
            
            # Check if it contains Firebase initialization
            content = response.text
            if 'firebase' in content.lower() and 'messaging' in content.lower():
                print("✅ Service worker contains Firebase messaging code")
            else:
                print("⚠️  Service worker may not have Firebase messaging setup")
            
            return True
        else:
            print(f"❌ Service worker returned status: {response.status_code}")
            return False
            
    except Exception as e:
        print(f"❌ Error testing service worker: {e}")
        return False


def test_fcm_service():
    """Test if FCM service is properly initialized"""
    print("\n" + "="*60)
    print("5. TESTING FCM SERVICE")
    print("="*60)
    
    try:
        from app.services.fcm_service import get_fcm_service
        
        fcm_service = get_fcm_service()
        print("✅ FCM service initialized successfully")
        
        # Check if Firebase Admin SDK is initialized
        import firebase_admin
        if firebase_admin._apps:
            print("✅ Firebase Admin SDK is initialized")
        else:
            print("⚠️  Firebase Admin SDK not initialized")
            print("   This is normal in local development mode")
        
        return True
        
    except Exception as e:
        print(f"❌ Error initializing FCM service: {e}")
        import traceback
        traceback.print_exc()
        return False


def print_browser_testing_guide():
    """Print instructions for testing in browser"""
    print("\n" + "="*60)
    print("BROWSER TESTING GUIDE")
    print("="*60)
    
    print("""
To test web-push notifications in your browser:

1. OPEN BROWSER DEVTOOLS (F12)
   - Go to Console tab

2. CHECK NOTIFICATION PERMISSION
   Run: Notification.permission
   Expected: "granted" or "default"
   If "denied": Reset in browser settings

3. CHECK SERVICE WORKER REGISTRATION
   - Go to Application tab > Service Workers
   - Should see: /firebase-messaging-sw.js (activated)

4. CHECK FIREBASE INITIALIZATION
   Run in console: window._pushStatus
   Expected: {state: "active", message: "Push notifications active"}
   
   If error, check the message for details

5. TEST NOTIFICATION MANUALLY
   Run in console:
   fetch('/fcm/test-push', {method: 'POST'})
     .then(r => r.json())
     .then(console.log)
   
   Expected: {status: "success"}
   You should see a test notification

6. CHECK BROWSER CONSOLE FOR ERRORS
   Look for errors starting with [Push]

COMMON ISSUES:

❌ "Browser does not support push notifications"
   → Use Chrome, Firefox, or Edge (not Safari on iOS)

❌ "Notifications are blocked in browser settings"
   → Go to browser settings and allow notifications for localhost

❌ "Firebase config missing"
   → Check .env file has all FIREBASE_* variables set

❌ "No FCM token received"
   → Check FIREBASE_VAPID_KEY is correct
   → Check service worker is registered

❌ "Failed to send push"
   → Check Firebase Admin SDK credentials
   → In local mode, this is expected (no Firestore)

MOBILE TESTING:

For mobile browsers:
1. Use ngrok or similar to expose localhost
2. Access via HTTPS (required for service workers)
3. Test on Chrome Android or Firefox Android
4. iOS Safari does NOT support web push (use PWA instead)
""")


def main():
    print("\n" + "="*60)
    print("WEB-PUSH NOTIFICATION DIAGNOSTIC TOOL")
    print("="*60)
    
    results = []
    
    # Run tests
    results.append(("Firebase Config", test_firebase_config()))
    results.append(("Config Endpoint", test_firebase_config_endpoint()))
    results.append(("Token Registration", test_fcm_token_registration()))
    results.append(("Service Worker", test_service_worker()))
    results.append(("FCM Service", test_fcm_service()))
    
    # Print summary
    print("\n" + "="*60)
    print("TEST SUMMARY")
    print("="*60)
    
    for test_name, result in results:
        if result is True:
            print(f"✅ {test_name}: PASS")
        elif result is False:
            print(f"❌ {test_name}: FAIL")
        else:
            print(f"⚠️  {test_name}: MANUAL TEST REQUIRED")
    
    # Print browser testing guide
    print_browser_testing_guide()
    
    # Final recommendations
    print("\n" + "="*60)
    print("RECOMMENDATIONS")
    print("="*60)
    
    failed_tests = [name for name, result in results if result is False]
    
    if failed_tests:
        print(f"\n⚠️  {len(failed_tests)} test(s) failed:")
        for test_name in failed_tests:
            print(f"   - {test_name}")
        print("\nFix these issues before testing in browser")
    else:
        print("\n✅ All automated tests passed!")
        print("   Follow the browser testing guide above to test notifications")
    
    print("\n" + "="*60)


if __name__ == '__main__':
    main()
