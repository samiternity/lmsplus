import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services.firestore_service import get_firestore_service

def verify_user(username):
    print(f"Checking Firestore for user: {username}")
    fs = get_firestore_service()
    
    if not fs.is_available():
        print("❌ Firestore not available locally.")
        return

    data = fs.get_document('users', username)
    if data:
        print("✅ User found in Firestore!")
        print(f"Username: {data.get('username')}")
        print(f"Has Password Hash: {'Yes' if data.get('password_hash') else 'No'}")
        print(f"Synced At: {data.get('synced_at')}")
    else:
        print("❌ User NOT found in Firestore.")

if __name__ == "__main__":
    if len(sys.argv) > 1:
        verify_user(sys.argv[1])
    else:
        print("Usage: python verify_firestore.py <username>")
