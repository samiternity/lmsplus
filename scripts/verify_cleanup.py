import os
import sys

# Ensure the app module can be imported
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app.services.firestore_service import get_firestore_service

def verify_cleanup():
    fs = get_firestore_service()
    if not fs.is_available():
        print("Firestore is not available. Ensure credentials are set up.")
        return

    print("Scanning account_data collection for legacy documents...")
    
    # In Firestore, we can list all documents in a collection.
    # Since the userbase is small (11 users), fetching all is fine.
    try:
        docs = fs.db.collection('account_data').stream()
        
        legacy_suffixes = [
            '_attendance',
            '_assignments_snapshot',
            '_yesterday_attendance',
            '_notified_assignments',
            '_assignments',
            '_assignment_tracking'
        ]
        
        legacy_found = []
        valid_found = []
        
        for doc in docs:
            doc_id = doc.id
            is_legacy = False
            for suffix in legacy_suffixes:
                if doc_id.endswith(suffix):
                    legacy_found.append(doc_id)
                    is_legacy = True
                    break
            
            if not is_legacy:
                valid_found.append(doc_id)
                
        print(f"\nVerification Results:")
        print(f"Total valid documents kept: {len(valid_found)}")
        print(f"Total legacy fragmented documents found: {len(legacy_found)}")
        
        if legacy_found:
            print("\nWARNING: The following legacy documents still exist:")
            for d in legacy_found:
                print(f"  - {d}")
        else:
            print("\nSUCCESS: No legacy documents found! The database is clean.")
            
        # Verify the unified dashboard collection
        dashboards = list(fs.db.collection('user_dashboards').stream())
        print(f"\nTotal Unified Dashboards active: {len(dashboards)}")
        
    except Exception as e:
        print(f"Error scanning Firestore: {e}")

if __name__ == '__main__':
    verify_cleanup()
