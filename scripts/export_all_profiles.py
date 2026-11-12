#!/usr/bin/env python3
"""
Export all user profiles to a text file
Retrieves all registered users and their profile information from the database
"""
import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app.models.account import User
from app.utils.data_manager import data_manager
from datetime import datetime
import json

def format_profile_section(user_id, user_data, profile_data):
    """Format a single user's profile information"""
    lines = []
    lines.append("=" * 80)
    lines.append(f"USER: {user_id}")
    lines.append("=" * 80)
    
    # Basic user information
    lines.append("\n--- ACCOUNT INFORMATION ---")
    lines.append(f"Username: {user_data.get('username', 'N/A')}")
    lines.append(f"Email: {user_data.get('email', 'N/A')}")
    lines.append(f"Created: {user_data.get('created_at', 'N/A')}")
    lines.append(f"Last Login: {user_data.get('last_login', 'N/A')}")
    
    # CMS Account information (single account mode)
    cms_account = user_data.get('cms_account')
    if cms_account:
        lines.append("\n--- CMS ACCOUNT ---")
        lines.append(f"Account ID: {user_data.get('account_id', 'N/A')}")
        lines.append(f"CMS Username: {cms_account.get('cms_username', 'N/A')}")
        lines.append(f"Last Login: {cms_account.get('last_login', 'N/A')}")
        lines.append(f"Created: {cms_account.get('created_at', 'N/A')}")
    
    # CMS Accounts information (multi-account mode - legacy)
    cms_accounts = user_data.get('cms_accounts', {})
    if cms_accounts:
        lines.append("\n--- CMS ACCOUNTS ---")
        for account_id, account_data in cms_accounts.items():
            lines.append(f"\nAccount ID: {account_id}")
            lines.append(f"  CMS Username: {account_data.get('cms_username', 'N/A')}")
            lines.append(f"  Last Login: {account_data.get('last_login', 'N/A')}")
            lines.append(f"  Created: {account_data.get('created_at', 'N/A')}")
    
    # Profile information
    if profile_data:
        lines.append("\n--- PROFILE INFORMATION ---")
        
        # Personal Information
        personal_fields = {
            'Enrollment': profile_data.get('enrollment'),
            'Registration No.': profile_data.get('registration_no'),
            'Student Name': profile_data.get('name'),
            'Father\'s Name': profile_data.get('father_name'),
            'CNIC': profile_data.get('cnic'),
            'Date of Birth': profile_data.get('date_of_birth'),
            'Gender': profile_data.get('gender'),
            'Blood Group': profile_data.get('blood_group'),
            'Religion': profile_data.get('religion'),
            'Nationality': profile_data.get('nationality'),
            'Domicile': profile_data.get('domicile')
        }
        
        # Only show personal details if at least one field has data
        personal_has_data = any(v for v in personal_fields.values() if v)
        if personal_has_data:
            lines.append("\nPersonal Details:")
            for label, value in personal_fields.items():
                if value:  # Only show fields with actual data
                    lines.append(f"  {label}: {value}")
        
        # Contact Information
        contact_fields = {
            'Personal Email': profile_data.get('personal_email'),
            'University Email': profile_data.get('university_email'),
            'Mobile No.': profile_data.get('mobile_no'),
            'Phone No.': profile_data.get('phone_no'),
            'Current Address': profile_data.get('current_address'),
            'Permanent Address': profile_data.get('permanent_address')
        }
        
        # Only show contact info if at least one field has data
        contact_has_data = any(v for v in contact_fields.values() if v)
        if contact_has_data:
            lines.append("\nContact Information:")
            for label, value in contact_fields.items():
                if value:  # Only show fields with actual data
                    lines.append(f"  {label}: {value}")
        
        # Academic Information
        academic_fields = {
            'Program': profile_data.get('program'),
            'Degree Duration': profile_data.get('degree_duration'),
            'Intake Semester': profile_data.get('intake_semester'),
            'Max Semester': profile_data.get('max_semester'),
            'Campus': profile_data.get('campus'),
            'Department': profile_data.get('department'),
            'Batch': profile_data.get('batch'),
            'Section': profile_data.get('section'),
            'Advisor': profile_data.get('advisor'),
            'Status': profile_data.get('status')
        }
        
        # Only show academic info if at least one field has data
        academic_has_data = any(v for v in academic_fields.values() if v)
        if academic_has_data:
            lines.append("\nAcademic Information:")
            for label, value in academic_fields.items():
                if value:  # Only show fields with actual data
                    lines.append(f"  {label}: {value}")
        
        # Metadata
        last_updated = profile_data.get('last_updated', profile_data.get('scraped_at'))
        if last_updated:
            lines.append("\nProfile Metadata:")
            lines.append(f"  Last Updated: {last_updated}")
    else:
        lines.append("\n--- PROFILE INFORMATION ---")
        lines.append("No profile data available")
    
    lines.append("\n")
    return "\n".join(lines)

def export_all_profiles():
    """Export all user profiles to a text file"""
    print("=" * 80)
    print("EXPORTING ALL USER PROFILES")
    print("=" * 80)
    
    # Get all users
    print("\n1. Retrieving all users...")
    users = User.list_all()
    
    if not users:
        print("No users found in the system.")
        return
    
    print(f"Found {len(users)} user(s)")
    
    # Prepare output
    output_lines = []
    output_lines.append("=" * 80)
    output_lines.append("UNITRACK - USER PROFILES EXPORT")
    output_lines.append("=" * 80)
    output_lines.append(f"Export Date: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    output_lines.append(f"Total Users: {len(users)}")
    output_lines.append("=" * 80)
    output_lines.append("\n\n")
    
    # Process each user
    print("\n2. Processing user profiles...")
    users_with_profiles = 0
    users_without_profiles = 0
    
    for user in users:
        # Convert User object to dict
        user_id = user.id
        user_data = user.to_dict()
        
        print(f"\n   Processing: {user_id}")
        
        # Get profile data for this user's account
        profile_data = None
        
        # Check for single account (new mode)
        if hasattr(user, 'cms_account') and user.cms_account:
            account_id = user.account_id
            if account_id:
                # Load profile data
                profile_data = data_manager.load_account_data(account_id, 'profile')
                
                if profile_data:
                    users_with_profiles += 1
                    print(f"      ✅ Profile data found")
                else:
                    users_without_profiles += 1
                    print(f"      ⚠️  No profile data")
            else:
                users_without_profiles += 1
                print(f"      ⚠️  No account ID")
        # Check for multi-account mode (old mode)
        elif 'cms_accounts' in user_data:
            cms_accounts = user_data.get('cms_accounts', {})
            if cms_accounts:
                # Get the first account's profile (or active account)
                active_account_id = user_data.get('active_account_id')
                
                if active_account_id and active_account_id in cms_accounts:
                    account_id = active_account_id
                else:
                    # Get first account
                    account_id = list(cms_accounts.keys())[0]
                
                # Load profile data
                profile_data = data_manager.load_account_data(account_id, 'profile')
                
                if profile_data:
                    users_with_profiles += 1
                    print(f"      ✅ Profile data found")
                else:
                    users_without_profiles += 1
                    print(f"      ⚠️  No profile data")
            else:
                users_without_profiles += 1
                print(f"      ⚠️  No CMS accounts")
        else:
            users_without_profiles += 1
            print(f"      ⚠️  No CMS account")
        
        # Format and add to output
        section = format_profile_section(user_id, user_data, profile_data)
        output_lines.append(section)
    
    # Add summary at the end
    output_lines.append("\n\n")
    output_lines.append("=" * 80)
    output_lines.append("EXPORT SUMMARY")
    output_lines.append("=" * 80)
    output_lines.append(f"Total Users: {len(users)}")
    output_lines.append(f"Users with Profile Data: {users_with_profiles}")
    output_lines.append(f"Users without Profile Data: {users_without_profiles}")
    output_lines.append(f"Export Date: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    output_lines.append("=" * 80)
    
    # Write to file
    output_file = f"user_profiles_export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
    print(f"\n3. Writing to file: {output_file}")
    
    with open(output_file, 'w', encoding='utf-8') as f:
        f.write("\n".join(output_lines))
    
    print(f"\n✅ Export completed successfully!")
    print(f"\n📄 File saved: {output_file}")
    print(f"\n📊 Summary:")
    print(f"   Total Users: {len(users)}")
    print(f"   With Profile Data: {users_with_profiles}")
    print(f"   Without Profile Data: {users_without_profiles}")
    print("\n" + "=" * 80)
    
    return output_file

if __name__ == "__main__":
    try:
        export_all_profiles()
    except Exception as e:
        print(f"\n❌ Error during export: {e}")
        import traceback
        traceback.print_exc()
