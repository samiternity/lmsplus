import os
import sys

# Ensure the app module can be imported
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app.services.firestore_service import get_firestore_service

def run_cleanup():
    fs = get_firestore_service()
    if not fs.is_available():
        print("Firestore is not available. Ensure credentials are set up.")
        return

    print("Scanning account_data collection for legacy documents...")
    
    try:
        docs = fs.db.collection('account_data').stream()
        
        legacy_suffixes = [
            '_attendance',
            '_assignments_snapshot',
            '_yesterday_attendance',
            '_notified_assignments',
            '_assignments',
            '_assignment_tracking',
            '_notification_assignments_snapshot'
        ]
        
        total_deleted = 0
        
        for doc in docs:
            doc_id = doc.id
            is_legacy = False
            for suffix in legacy_suffixes:
                if doc_id.endswith(suffix):
                    is_legacy = True
                    break
            
            if is_legacy:
                try:
                    fs.db.collection('account_data').document(doc_id).delete()
                    total_deleted += 1
                    print(f"  Deleted: {doc_id}")
                except Exception as e:
                    print(f"  Error deleting {doc_id}: {e}")

        print(f"\nCleanup complete. Total orphaned legacy documents deleted: {total_deleted}")
        
    except Exception as e:
        print(f"Error scanning Firestore: {e}")

if __name__ == '__main__':
    run_cleanup()
