#!/usr/bin/env python3
"""
Diagnostic Script: Check Account Structure

This script checks which account structure each user is using:
- Old multi-account structure (cms_accounts dict)
- New single-account structure (cms_account object)
- Mixed or unknown structure
"""

import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

def check_account_structure():
    from app.models.account import User
    
    print("=" * 80)
    print("ACCOUNT STRUCTURE DIAGNOSTIC")
    print("=" * 80)
    print()
    
    all_users = User.list_all()
    print(f"Checking {len(all_users)} users...\n")
    
    stats = {
        'new_structure': 0,
        'old_structure': 0,
        'old_single': 0,
        'old_multiple': 0,
        'no_accounts': 0
    }
    
    for user in all_users:
        user_data = user.to_dict()
        
        print(f"User: {user.username} (ID: {user.id})")
        
        # Check for new structure
        has_new = 'cms_account' in user_data and user_data['cms_account']
        has_old = 'cms_accounts' in user_data and user_data['cms_accounts']
        
        if has_new:
            print(f"  ✓ Using NEW single-account structure")
            print(f"    Account ID: {user_data.get('account_id', 'N/A')}")
            if user_data['cms_account']:
                cms_username = user_data['cms_account'].get('cms_username', 'N/A')
                student_name = user_data['cms_account'].get('student_name', 'N/A')
                print(f"    CMS Username: {cms_username}")
                print(f"    Student Name: {student_name}")
            stats['new_structure'] += 1
            
        elif has_old:
            account_count = len(user_data['cms_accounts'])
            print(f"  ⚠️  Using OLD multi-account structure")
            print(f"    Number of accounts: {account_count}")
            
            if account_count == 1:
                stats['old_single'] += 1
                print(f"    Status: Single account (needs migration)")
            else:
                stats['old_multiple'] += 1
                print(f"    Status: Multiple accounts (NEEDS CLEANUP!)")
            
            stats['old_structure'] += 1
            
            # Show account details
            for acc_id, acc in user_data['cms_accounts'].items():
                cms_username = acc.get('cms_username', 'unknown')
                student_name = acc.get('student_name', 'N/A')
                is_active = acc_id == user_data.get('active_account_id')
                status = "ACTIVE" if is_active else "inactive"
                print(f"      - {cms_username} ({student_name}) [{status}]")
        else:
            print(f"  ⚠️  No CMS accounts found")
            stats['no_accounts'] += 1
        
        print()
    
    print("=" * 80)
    print("SUMMARY")
    print("=" * 80)
    print(f"Total users: {len(all_users)}")
    print(f"Using NEW structure (cms_account): {stats['new_structure']}")
    print(f"Using OLD structure (cms_accounts): {stats['old_structure']}")
    print(f"  - Old with single account: {stats['old_single']}")
    print(f"  - Old with multiple accounts: {stats['old_multiple']}")
    print(f"No accounts: {stats['no_accounts']}")
    print()
    
    if stats['old_structure'] > 0:
        print("⚠️  RECOMMENDATION:")
        print("   Some users are still using the old structure.")
        print("   Run: python scripts/migrate_to_single_account.py --dry-run")
        print()
    elif stats['new_structure'] == len(all_users):
        print("✓ All users are using the new single-account structure!")
        print()
    
    return stats


if __name__ == '__main__':
    try:
        check_account_structure()
    except Exception as e:
        print(f"\nERROR: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
