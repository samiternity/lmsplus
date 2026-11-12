#!/usr/bin/env python3
"""
Cleanup Script: Remove Cross-Linked Accounts

This script identifies and removes cases where Account A was somehow linked
with Account B in the database (the security vulnerability we're fixing).

It will:
1. Find all users in the database
2. Check if they have the old cms_accounts structure with multiple accounts
3. Show which accounts are linked together
4. Remove the linkage by keeping only one account per user

Usage:
    # See what would be cleaned up (safe, no changes)
    python scripts/cleanup_linked_accounts.py --dry-run
    
    # Actually clean up the database
    python scripts/cleanup_linked_accounts.py
"""

import sys
import os
import json
from datetime import datetime
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

def cleanup_linked_accounts(dry_run=False):
    """Find and remove cross-linked accounts."""
    from app.models.account import User
    
    print("=" * 80)
    print("CROSS-LINKED ACCOUNTS CLEANUP")
    print("=" * 80)
    print(f"Mode: {'DRY RUN (no changes)' if dry_run else 'LIVE (will make changes)'}")
    print()
    
    # Get all users
    all_users = User.list_all()
    print(f"Scanning {len(all_users)} users...\n")
    
    issues_found = []
    clean_users = 0
    
    for user in all_users:
        user_data = user.to_dict()
        
        # Check for old multi-account structure
        if 'cms_accounts' in user_data and user_data['cms_accounts']:
            cms_accounts = user_data['cms_accounts']
            account_count = len(cms_accounts)
            
            if account_count > 1:
                # Found a user with multiple linked accounts!
                print(f"⚠️  ISSUE FOUND: User '{user.username}' has {account_count} linked accounts")
                print(f"   User ID: {user.id}")
                print(f"   Linked accounts:")
                
                account_details = []
                for acc_id, acc in cms_accounts.items():
                    cms_username = acc.get('cms_username', 'unknown')
                    student_name = acc.get('student_name', 'N/A')
                    is_active = acc_id == user_data.get('active_account_id')
                    
                    status = "ACTIVE" if is_active else "inactive"
                    print(f"     - {cms_username} ({student_name}) [{status}]")
                    
                    account_details.append({
                        'account_id': acc_id,
                        'cms_username': cms_username,
                        'student_name': student_name,
                        'is_active': is_active
                    })
                
                print()
                
                issues_found.append({
                    'user_id': user.id,
                    'username': user.username,
                    'account_count': account_count,
                    'accounts': account_details,
                    'user_obj': user
                })
            else:
                clean_users += 1
        elif hasattr(user, 'cms_account') and user.cms_account:
            # Already migrated to single account
            clean_users += 1
        else:
            # No accounts
            clean_users += 1
    
    print("=" * 80)
    print("SCAN RESULTS")
    print("=" * 80)
    print(f"Total users scanned: {len(all_users)}")
    print(f"Users with cross-linked accounts: {len(issues_found)}")
    print(f"Clean users: {clean_users}")
    print()
    
    if not issues_found:
        print("✓ No cross-linked accounts found. Database is clean!")
        return
    
    # Show summary of what will be done
    print("=" * 80)
    print("CLEANUP PLAN")
    print("=" * 80)
    print("For each user with multiple accounts, we will:")
    print("  1. Keep the ACTIVE account (or first account if no active)")
    print("  2. Remove all other linked accounts")
    print("  3. Convert to single-account structure")
    print()
    
    for issue in issues_found:
        active_account = next(
            (acc for acc in issue['accounts'] if acc['is_active']),
            issue['accounts'][0]
        )
        removed_accounts = [
            acc for acc in issue['accounts']
            if acc['account_id'] != active_account['account_id']
        ]
        
        print(f"User: {issue['username']}")
        print(f"  KEEP: {active_account['cms_username']} ({active_account['student_name']})")
        for acc in removed_accounts:
            print(f"  REMOVE: {acc['cms_username']} ({acc['student_name']})")
        print()
    
    if dry_run:
        print("=" * 80)
        print("DRY RUN COMPLETE - No changes were made")
        print("=" * 80)
        print("To apply cleanup, run without --dry-run flag")
        return
    
    # Confirm before proceeding
    print("=" * 80)
    print("⚠️  WARNING: This will permanently remove linked accounts!")
    print("=" * 80)
    response = input("Do you want to proceed? (yes/no): ").strip().lower()
    
    if response != 'yes':
        print("Cleanup cancelled.")
        return
    
    # Create backup
    backup_dir = Path('./backups')
    backup_dir.mkdir(exist_ok=True)
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    backup_file = backup_dir / f'linked_accounts_backup_{timestamp}.json'
    
    backup_data = []
    for issue in issues_found:
        user_data = issue['user_obj'].to_dict()
        backup_data.append({
            'user_id': issue['user_id'],
            'username': issue['username'],
            'backup_date': datetime.now().isoformat(),
            'old_data': user_data
        })
    
    with open(backup_file, 'w') as f:
        json.dump(backup_data, f, indent=2, default=str)
    
    print(f"\n✓ Backup saved to: {backup_file}\n")
    
    # Apply cleanup
    print("=" * 80)
    print("APPLYING CLEANUP")
    print("=" * 80)
    
    success_count = 0
    error_count = 0
    
    for issue in issues_found:
        user = issue['user_obj']
        user_data = user.to_dict()
        cms_accounts = user_data['cms_accounts']
        
        # Determine which account to keep
        active_account_id = user_data.get('active_account_id')
        if active_account_id and active_account_id in cms_accounts:
            keep_account_id = active_account_id
        else:
            keep_account_id = list(cms_accounts.keys())[0]
        
        keep_account = cms_accounts[keep_account_id]
        
        try:
            print(f"Cleaning up user: {user.username}")
            
            # Convert to CMSAccount object
            from app.models.account import CMSAccount
            if isinstance(keep_account, dict):
                cms_account_obj = CMSAccount.from_dict(keep_account)
            else:
                cms_account_obj = keep_account
            
            # Set single account
            user.set_cms_account(
                account_id=keep_account_id,
                cms_account=cms_account_obj
            )
            
            # Remove old structure
            if hasattr(user, 'cms_accounts'):
                delattr(user, 'cms_accounts')
            if hasattr(user, 'active_account_id'):
                delattr(user, 'active_account_id')
            
            # Save
            if user.save():
                print(f"  ✓ Successfully cleaned up")
                success_count += 1
            else:
                print(f"  ✗ Failed to save")
                error_count += 1
                
        except Exception as e:
            print(f"  ✗ Error: {e}")
            error_count += 1
    
    print()
    print("=" * 80)
    print("CLEANUP COMPLETE")
    print("=" * 80)
    print(f"Successfully cleaned: {success_count} users")
    print(f"Errors: {error_count}")
    print(f"Backup: {backup_file}")
    print()
    print("✓ All cross-linked accounts have been removed!")
    print("✓ Each user now has only one account.")


if __name__ == '__main__':
    import argparse
    
    parser = argparse.ArgumentParser(
        description='Remove cross-linked accounts from database'
    )
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Show what would be changed without making changes'
    )
    
    args = parser.parse_args()
    
    try:
        cleanup_linked_accounts(dry_run=args.dry_run)
    except KeyboardInterrupt:
        print("\n\nCleanup cancelled by user")
        sys.exit(1)
    except Exception as e:
        print(f"\n\nERROR: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
