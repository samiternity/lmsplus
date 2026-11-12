#!/usr/bin/env python3
"""
Migration Script: Convert Multi-Account to Single-Account Mode

This script migrates user data from the old multi-account structure to the new
single-account structure. It:

1. Identifies users with multiple accounts in cms_accounts dict
2. Keeps only the active account (or first account if no active)
3. Removes all other linked accounts from the user object
4. Updates the user object to use the new single-account structure
5. Creates a backup of the old data before migration

Usage:
    python scripts/migrate_to_single_account.py [--dry-run] [--backup-dir PATH]

Options:
    --dry-run       Show what would be changed without making changes
    --backup-dir    Directory to store backup files (default: ./backups)
"""

import sys
import os
import json
from datetime import datetime
from pathlib import Path

# Add parent directory to path to import app modules
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

def migrate_to_single_account(dry_run=False, backup_dir='./backups'):
    """
    Migrate all users from multi-account to single-account structure.
    
    Args:
        dry_run: If True, only show what would be changed
        backup_dir: Directory to store backup files
    """
    from app.models.account import User
    
    # Create backup directory
    backup_path = Path(backup_dir)
    backup_path.mkdir(parents=True, exist_ok=True)
    
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    backup_file = backup_path / f'multi_account_backup_{timestamp}.json'
    
    print("=" * 70)
    print("MULTI-ACCOUNT TO SINGLE-ACCOUNT MIGRATION")
    print("=" * 70)
    print(f"Mode: {'DRY RUN (no changes will be made)' if dry_run else 'LIVE (changes will be applied)'}")
    print(f"Backup file: {backup_file}")
    print()
    
    # Get all users
    all_users = User.list_all()
    print(f"Total users in database: {len(all_users)}")
    print()
    
    # Statistics
    stats = {
        'total_users': len(all_users),
        'users_with_old_structure': 0,
        'users_with_multiple_accounts': 0,
        'users_already_migrated': 0,
        'accounts_removed': 0,
        'errors': 0
    }
    
    backup_data = []
    users_to_update = []
    
    # Analyze all users
    print("Analyzing users...")
    print("-" * 70)
    
    for user in all_users:
        user_data = user.to_dict()
        
        # Check if user has old multi-account structure
        if 'cms_accounts' in user_data and user_data['cms_accounts']:
            stats['users_with_old_structure'] += 1
            
            cms_accounts = user_data['cms_accounts']
            account_count = len(cms_accounts)
            
            print(f"\nUser: {user.username} (ID: {user.id})")
            print(f"  Old structure: {account_count} account(s) in cms_accounts")
            
            if account_count > 1:
                stats['users_with_multiple_accounts'] += 1
                print(f"  ⚠️  Multiple accounts detected!")
            
            # Determine which account to keep
            active_account_id = user_data.get('active_account_id')
            
            if active_account_id and active_account_id in cms_accounts:
                keep_account_id = active_account_id
                keep_account = cms_accounts[active_account_id]
                print(f"  ✓ Keeping active account: {keep_account.get('cms_username', 'unknown')}")
            else:
                # No active account or active account not found, use first account
                keep_account_id = list(cms_accounts.keys())[0]
                keep_account = cms_accounts[keep_account_id]
                print(f"  ✓ Keeping first account: {keep_account.get('cms_username', 'unknown')}")
            
            # List accounts that will be removed
            if account_count > 1:
                removed_accounts = [
                    acc.get('cms_username', 'unknown')
                    for acc_id, acc in cms_accounts.items()
                    if acc_id != keep_account_id
                ]
                print(f"  ✗ Removing {len(removed_accounts)} account(s): {', '.join(removed_accounts)}")
                stats['accounts_removed'] += len(removed_accounts)
            
            # Backup this user's data
            backup_data.append({
                'user_id': user.id,
                'username': user.username,
                'backup_date': datetime.now().isoformat(),
                'old_data': user_data
            })
            
            # Prepare migration
            users_to_update.append({
                'user': user,
                'keep_account_id': keep_account_id,
                'keep_account': keep_account
            })
            
        elif hasattr(user, 'cms_account') and user.cms_account:
            # Already using new single-account structure
            stats['users_already_migrated'] += 1
            print(f"\nUser: {user.username} (ID: {user.id})")
            print(f"  ✓ Already migrated to single-account structure")
        else:
            # No accounts at all
            print(f"\nUser: {user.username} (ID: {user.id})")
            print(f"  ⚠️  No CMS accounts found")
    
    print()
    print("=" * 70)
    print("MIGRATION SUMMARY")
    print("=" * 70)
    print(f"Total users: {stats['total_users']}")
    print(f"Users with old multi-account structure: {stats['users_with_old_structure']}")
    print(f"Users with multiple accounts: {stats['users_with_multiple_accounts']}")
    print(f"Users already migrated: {stats['users_already_migrated']}")
    print(f"Total accounts to be removed: {stats['accounts_removed']}")
    print()
    
    if not users_to_update:
        print("✓ No users need migration. All users are already using single-account structure.")
        return stats
    
    # Save backup
    if backup_data:
        print(f"Saving backup to {backup_file}...")
        with open(backup_file, 'w') as f:
            json.dump(backup_data, f, indent=2, default=str)
        print(f"✓ Backup saved ({len(backup_data)} users)")
        print()
    
    if dry_run:
        print("=" * 70)
        print("DRY RUN COMPLETE - No changes were made")
        print("=" * 70)
        print(f"To apply these changes, run without --dry-run flag")
        return stats
    
    # Apply migration
    print("=" * 70)
    print("APPLYING MIGRATION")
    print("=" * 70)
    
    for item in users_to_update:
        user = item['user']
        keep_account_id = item['keep_account_id']
        keep_account = item['keep_account']
        
        try:
            print(f"\nMigrating user: {user.username}")
            
            # Convert keep_account dict to CMSAccount object if needed
            from app.models.account import CMSAccount
            if isinstance(keep_account, dict):
                cms_account_obj = CMSAccount.from_dict(keep_account)
            else:
                cms_account_obj = keep_account
            
            # Set single account using the new structure
            user.set_cms_account(
                account_id=keep_account_id,
                cms_account=cms_account_obj
            )
            
            # Clear old multi-account structure if it exists
            if hasattr(user, 'cms_accounts'):
                delattr(user, 'cms_accounts')
            if hasattr(user, 'active_account_id'):
                delattr(user, 'active_account_id')
            
            # Save to database
            if user.save():
                print(f"  ✓ Successfully migrated")
            else:
                print(f"  ✗ Failed to save to database")
                stats['errors'] += 1
                
        except Exception as e:
            print(f"  ✗ Error migrating user: {e}")
            stats['errors'] += 1
    
    print()
    print("=" * 70)
    print("MIGRATION COMPLETE")
    print("=" * 70)
    print(f"Successfully migrated: {len(users_to_update) - stats['errors']} users")
    print(f"Errors: {stats['errors']}")
    print(f"Backup saved to: {backup_file}")
    print()
    
    return stats


if __name__ == '__main__':
    import argparse
    
    parser = argparse.ArgumentParser(
        description='Migrate users from multi-account to single-account structure'
    )
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Show what would be changed without making changes'
    )
    parser.add_argument(
        '--backup-dir',
        default='./backups',
        help='Directory to store backup files (default: ./backups)'
    )
    
    args = parser.parse_args()
    
    try:
        stats = migrate_to_single_account(
            dry_run=args.dry_run,
            backup_dir=args.backup_dir
        )
        
        # Exit with error code if there were errors
        sys.exit(1 if stats['errors'] > 0 else 0)
        
    except KeyboardInterrupt:
        print("\n\nMigration cancelled by user")
        sys.exit(1)
    except Exception as e:
        print(f"\n\nFATAL ERROR: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
