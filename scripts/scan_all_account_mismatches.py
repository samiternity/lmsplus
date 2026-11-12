#!/usr/bin/env python3
"""
Scan Script: Find All Account Mismatches

Scans all users to find cases where the user ID doesn't match
the CMS username in their account data.
"""

import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

def scan_all_mismatches():
    from app.models.account import User
    
    print("=" * 80)
    print("SCANNING FOR ACCOUNT MISMATCHES")
    print("=" * 80)
    print()
    
    all_users = User.list_all()
    print(f"Scanning {len(all_users)} users...\n")
    
    mismatches = []
    correct = []
    no_account = []
    
    for user in all_users:
        user_data = user.to_dict()
        username = user.username
        
        if 'cms_account' in user_data and user_data['cms_account']:
            cms_username = user_data['cms_account'].get('cms_username', '')
            
            if cms_username != username:
                # MISMATCH FOUND!
                mismatches.append({
                    'username': username,
                    'cms_username': cms_username,
                    'student_name': user_data['cms_account'].get('student_name', 'N/A'),
                    'account_id': user_data.get('account_id', 'N/A')
                })
                print(f"⚠️  MISMATCH: User '{username}'")
                print(f"   Shows data from: '{cms_username}'")
                print(f"   Student Name: {user_data['cms_account'].get('student_name', 'N/A')}")
                print()
            else:
                correct.append(username)
        else:
            no_account.append(username)
    
    print("=" * 80)
    print("SCAN RESULTS")
    print("=" * 80)
    print(f"Total users: {len(all_users)}")
    print(f"Correct accounts: {len(correct)}")
    print(f"Mismatched accounts: {len(mismatches)}")
    print(f"No account data: {len(no_account)}")
    print()
    
    if mismatches:
        print("=" * 80)
        print("MISMATCHED ACCOUNTS DETAILS")
        print("=" * 80)
        for m in mismatches:
            print(f"\nUser ID: {m['username']}")
            print(f"  Showing data from: {m['cms_username']}")
            print(f"  Student Name: {m['student_name']}")
            print(f"  Account ID: {m['account_id']}")
        
        print("\n" + "=" * 80)
        print("RECOMMENDED ACTION")
        print("=" * 80)
        print("Run the following command for each mismatched user:")
        print("  python scripts/fix_wrong_account_data.py")
        print()
        print("Or manually clear their account data so they can re-authenticate.")
    else:
        print("✓ No mismatches found! All users have correct account data.")
    
    if no_account:
        print("\n" + "=" * 80)
        print("USERS WITH NO ACCOUNT DATA")
        print("=" * 80)
        for username in no_account:
            print(f"  - {username}")
        print("\nThese users need to log in to fetch their data.")
    
    return mismatches


if __name__ == '__main__':
    try:
        mismatches = scan_all_mismatches()
        sys.exit(1 if mismatches else 0)
    except Exception as e:
        print(f"\nERROR: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
