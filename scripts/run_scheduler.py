#!/usr/bin/env python3
"""
Background Notification Scheduler
Runs as a separate process and triggers notifications every 30 minutes.
"""
import os
import sys
import time
import requests
import schedule
from datetime import datetime
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

# Configuration
SCHEDULER_SECRET = os.getenv('SCHEDULER_SECRET', 'dev-secret-key-change-in-production')
BASE_URL = os.getenv('BASE_URL', 'http://localhost:5000')
NOTIFICATION_ENDPOINT = f'{BASE_URL}/api/internal/run-notifications'


def trigger_notifications():
    """Trigger the background notification service"""
    try:
        print(f"[{datetime.now().isoformat()}] Triggering notifications...")
        
        response = requests.post(
            NOTIFICATION_ENDPOINT,
            headers={
                'Authorization': f'Bearer {SCHEDULER_SECRET}',
                'Content-Type': 'application/json'
            },
            timeout=300  # 5 minute timeout
        )
        
        if response.status_code == 200:
            result = response.json()
            summary = result.get('summary', {})
            print(f"✓ Success! Users: {summary.get('users', 0)}, "
                  f"Accounts: {summary.get('accounts', 0)}, "
                  f"Notifications: {summary.get('notifications', 0)}")
            
            if summary.get('errors'):
                print(f"  Errors: {len(summary['errors'])}")
                for error in summary['errors'][:3]:  # Show first 3 errors
                    print(f"    - {error}")
        else:
            print(f"✗ Failed with status {response.status_code}")
            print(f"  Response: {response.text[:200]}")
            
    except requests.exceptions.Timeout:
        print("✗ Request timed out (took more than 5 minutes)")
    except requests.exceptions.ConnectionError:
        print("✗ Connection error - is the server running?")
    except Exception as e:
        print(f"✗ Error: {e}")


def main():
    """Main scheduler loop"""
    print("=" * 60)
    print("Background Notification Scheduler")
    print("=" * 60)
    print(f"Endpoint: {NOTIFICATION_ENDPOINT}")
    print(f"Schedule: Every 30 minutes")
    print(f"Started at: {datetime.now().isoformat()}")
    print("=" * 60)
    print()
    
    # Schedule the job
    schedule.every(30).minutes.do(trigger_notifications)
    
    # Run immediately on startup
    print("Running initial notification check...")
    trigger_notifications()
    print()
    
    # Keep running
    print("Scheduler is running. Press Ctrl+C to stop.")
    print()
    
    try:
        while True:
            schedule.run_pending()
            time.sleep(60)  # Check every minute
    except KeyboardInterrupt:
        print("\nScheduler stopped.")


if __name__ == '__main__':
    main()
