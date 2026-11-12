#!/usr/bin/env python3
"""
Manual Scrape Script: Trigger data scraping for a specific user

This script manually triggers the CMS scraper to fetch data for a user.
"""

import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

def manual_scrape(username):
    """Manually trigger scraping for a user"""
    from app.models.account import User
    from app.utils.enhanced_scraper import scraper_manager
    
    print("=" * 80)
    print(f"MANUAL SCRAPE FOR: {username}")
    print("=" * 80)
    print()
    
    # Get user
    user = User.get(username)
    if not user:
        print(f"❌ User not found: {username}")
        return False
    
    print(f"✓ User found: {username}")
    
    # Check CMS account
    if not user.cms_account:
        print(f"❌ User has no CMS account")
        return False
    
    print(f"✓ CMS Account found")
    print(f"  CMS Username: {user.cms_account.cms_username}")
    print(f"  Account ID: {user.account_id}")
    print()
    
    # Get credentials
    cms_username = user.cms_account.cms_username
    cms_password = user.cms_account.cms_password
    
    if not cms_password:
        print(f"❌ CMS password not available")
        return False
    
    print(f"✓ Credentials retrieved")
    print(f"  Username: {cms_username}")
    print(f"  Password: {'*' * len(cms_password)}")
    print()
    
    # Trigger scraping
    print("Starting scrape...")
    print("-" * 80)
    
    try:
        result = scraper_manager.scrape_account_data(
            account=user.cms_account
        )
        
        print()
        print("-" * 80)
        print("Scrape completed!")
        print()
        
        if result.get('success'):
            print("✅ SUCCESS")
            print(f"  Timetable: {'✓' if result.get('timetable') else '✗'}")
            print(f"  Attendance: {'✓' if result.get('attendance') else '✗'}")
            print(f"  GPA: {'✓' if result.get('gpa') else '✗'}")
            print(f"  Community Service: {'✓' if result.get('community_service') else '✗'}")
            print(f"  Profile: {'✓' if result.get('profile') else '✗'}")
            return True
        else:
            print("❌ FAILED")
            if 'error' in result:
                print(f"  Error: {result['error']}")
            return False
            
    except Exception as e:
        print()
        print("-" * 80)
        print(f"❌ Exception during scrape: {e}")
        import traceback
        traceback.print_exc()
        return False


if __name__ == '__main__':
    import argparse
    
    parser = argparse.ArgumentParser(description='Manually scrape data for a user')
    parser.add_argument('username', help='CMS username to scrape')
    
    args = parser.parse_args()
    
    try:
        success = manual_scrape(args.username)
        sys.exit(0 if success else 1)
    except KeyboardInterrupt:
        print("\n\nScrape cancelled by user")
        sys.exit(1)
    except Exception as e:
        print(f"\n\nERROR: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
