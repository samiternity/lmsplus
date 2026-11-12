"""
One-time migration script to add `batch_bucket` to all existing users in Firestore.
Run this script manually from the terminal.
"""
import os
import sys
import hashlib

# Add parent directory to path so we can import app
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

def migrate_users():
    from app.services.firestore_service import get_firestore_service
    
    fs = get_firestore_service()
    if not fs or not fs.is_available():
        print("Error: Firestore is not available. Please check credentials.")
        return

    try:
        # Get all users
        users_ref = fs.db.collection('users')
        docs = users_ref.stream()
        
        count = 0
        batch = fs.db.batch()
        batch_count = 0
        
        for doc in docs:
            user_data = doc.to_dict()
            user_id = doc.id
            
            # Skip if already migrated
            if 'batch_bucket' in user_data:
                print(f"Skipping user {user_id} (already has batch_bucket: {user_data['batch_bucket']})")
                continue
                
            # Calculate bucket
            bucket = int(hashlib.md5(user_id.encode('utf-8')).hexdigest(), 16) % 60
            
            # Add to batch
            doc_ref = users_ref.document(user_id)
            batch.update(doc_ref, {'batch_bucket': bucket})
            batch_count += 1
            count += 1
            
            print(f"Assigning user {user_id} to bucket {bucket}")
            
            # Commit batch every 400 operations (Firestore limit is 500)
            if batch_count >= 400:
                batch.commit()
                batch = fs.db.batch()
                batch_count = 0
                print(f"--- Committed batch of 400 ---")
                
        # Commit remaining
        if batch_count > 0:
            batch.commit()
            
        print(f"Migration complete! Updated {count} users.")
        
    except Exception as e:
        print(f"Error during migration: {e}")

if __name__ == '__main__':
    print("Starting User Batch Bucket Migration...")
    migrate_users()
