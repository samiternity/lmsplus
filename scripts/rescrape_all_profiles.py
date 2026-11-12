#!/usr/bin/env python3
"""
Re-scrape all user profiles using the working scraper
This will update all profile data with correct information
"""
import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app.models.account import User
from app.cms_scraper import BahriaCMSScraper
from app.utils.data_manager import data_manager
import time

def rescrape_all_profiles():
    """Re-scrape profiles for all users"""
    print("=" * 80)
    print("RE-SCRAPING ALL USER PROFILES")
    print("=" * 80)
    
    # Get all users
    print("\n1. Retrieving all users...")
    users = User.list_all()
    
    if not users:
        print("No users found in the system.")
        return
    
    print(f"Found {len(users)} user(s)")
    
    success_count = 0
    failed_count = 0
    skipped_count = 0
    
    for user in users:
        user_id = user.id
        print(f"\n{'='*80}")
        print(f"Processing user: {user_id}")
        print(f"{'='*80}")
        
        # Check if user has CMS account
        if not hasattr(user, 'cms_account') or not user.cms_account:
            print(f"⚠️  User {user_id} has no CMS account, skipping...")
            skipped_count += 1
            continue
        
        account = user.get_active_account()
        if not account:
            print(f"⚠️  Could not get active account for {user_id}, skipping...")
            skipped_count += 1
            continue
        
        cms_username = account.cms_username
        cms_password = account.cms_password
        account_id = user.account_id
        
        print(f"CMS Username: {cms_username}")
        print(f"Account ID: {account_id}")
        
        # Create scraper and login
        print(f"\nLogging in...")
        scraper = BahriaCMSScraper()
        
        if not scraper.login(cms_username, cms_password):
            print(f"❌ Login failed for {cms_username}")
            failed_count += 1
            continue
        
        print(f"✅ Login successful")
        
        # Scrape profile
        print(f"Scraping profile...")
        profile_data = scraper.scrape_profile(cms_username)
        
        if not profile_data:
            print(f"❌ Profile scraping failed for {cms_username}")
            failed_count += 1
            continue
        
        print(f"✅ Profile data scraped")
        
        # Save to Firebase using data_manager
        print(f"Saving to Firebase...")
        save_success = data_manager.save_account_data(account_id, 'profile', profile_data)
        
        if save_success:
            print(f"✅ Profile data saved to Firebase")
            success_count += 1
            
            # Display key fields
            print(f"\nProfile Summary:")
            print(f"  Name: {profile_data.get('name', 'N/A')}")
            print(f"  Enrollment: {profile_data.get('enrollment', 'N/A')}")
            print(f"  Program: {profile_data.get('program', 'N/A')}")
            print(f"  Email: {profile_data.get('personal_email', 'N/A')}")
        else:
            print(f"❌ Failed to save profile data")
            failed_count += 1
        
        # Small delay between users to avoid rate limiting
        time.sleep(2)
    
    # Summary
    print(f"\n{'='*80}")
    print("RE-SCRAPING SUMMARY")
    print(f"{'='*80}")
    print(f"Total Users: {len(users)}")
    print(f"✅ Successfully Re-scraped: {success_count}")
    print(f"❌ Failed: {failed_count}")
    print(f"⚠️  Skipped (no account): {skipped_count}")
    print(f"{'='*80}")

if __name__ == "__main__":
    try:
        rescrape_all_profiles()
    except Exception as e:
        print(f"\n❌ Error during re-scraping: {e}")
        import traceback
        traceback.print_exc()
