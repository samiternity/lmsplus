#!/usr/bin/env python3
"""
Re-scrape profile for a single user
"""
import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app.cms_scraper import BahriaCMSScraper
from app.models.account import User
from app.utils.data_manager import data_manager
import time

def rescrape_single_user(username):
    """Re-scrape profile for a single user"""
    print("=" * 80)
    print(f"RE-SCRAPING PROFILE FOR USER: {username}")
    print("=" * 80)
    
    # Get user
    user = User.get(username)
    if not user:
        print(f"❌ User {username} not found")
        return False
    
    # Get CMS account
    if not hasattr(user, 'cms_account') or not user.cms_account:
        print(f"❌ User {username} has no CMS account")
        return False
    
    cms_username = user.cms_account.cms_username
    cms_password = user.cms_account.cms_password
    account_id = user.account_id
    
    print(f"\nCMS Username: {cms_username}")
    print(f"Account ID: {account_id}")
    
    # Initialize scraper
    scraper = BahriaCMSScraper()
    
    # Login with retry
    max_attempts = 3
    for attempt in range(max_attempts):
        print(f"\nAttempt {attempt + 1}/{max_attempts}: Logging in...")
        
        if scraper.login(cms_username, cms_password):
            print("✅ Login successful")
            
            # Wait a bit to ensure session is stable
            time.sleep(2)
            
            # Scrape profile
            print("Scraping profile...")
            profile_data = scraper.scrape_profile(cms_username)
            
            if profile_data:
                print("✅ Profile data scraped")
                
                # Save to Firebase
                print("Saving to Firebase...")
                data_manager.save_account_data(account_id, 'profile', profile_data)
                print("✅ Profile data saved to Firebase")
                
                # Show summary
                print("\nProfile Summary:")
                print(f"  Name: {profile_data.get('name', 'N/A')}")
                print(f"  Enrollment: {profile_data.get('enrollment', 'N/A')}")
                print(f"  Registration No.: {profile_data.get('registration_no', 'N/A')}")
                print(f"  Father Name: {profile_data.get('father_name', 'N/A')}")
                print(f"  Program: {profile_data.get('program', 'N/A')}")
                print(f"  Degree Duration: {profile_data.get('degree_duration', 'N/A')}")
                print(f"  Max Semester: {profile_data.get('max_semester', 'N/A')}")
                print(f"  Phone No.: {profile_data.get('phone_no', 'N/A')}")
                print(f"  Email: {profile_data.get('personal_email', 'N/A')}")
                
                return True
            else:
                print(f"❌ Profile scraping failed")
                if attempt < max_attempts - 1:
                    print(f"Waiting 5 seconds before retry...")
                    time.sleep(5)
        else:
            print(f"❌ Login failed")
            if attempt < max_attempts - 1:
                print(f"Waiting 5 seconds before retry...")
                time.sleep(5)
    
    print(f"\n❌ Failed to re-scrape profile after {max_attempts} attempts")
    return False

if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(f"Usage: {sys.argv[0]} ENROLLMENT_ID")
    username = sys.argv[1]
    
    success = rescrape_single_user(username)
    sys.exit(0 if success else 1)
