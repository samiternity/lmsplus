import logging
logger = logging.getLogger(__name__)

from flask_login import UserMixin
import json
import os
from werkzeug.security import generate_password_hash, check_password_hash

# Simple local user storage
class LocalUserStorage:
    def __init__(self):
        self.users_file = os.path.join(os.path.dirname(__file__), '..', 'data', 'users.json')
        self.ensure_users_file()
    
    def ensure_users_file(self):
        """Ensure users.json file exists"""
        os.makedirs(os.path.dirname(self.users_file), exist_ok=True)
        if not os.path.exists(self.users_file):
            with open(self.users_file, 'w') as f:
                json.dump({}, f)
    
    def load_users(self):
        """Load users from JSON file"""
        try:
            with open(self.users_file, 'r') as f:
                return json.load(f)
        except:
            return {}
    
    def save_users(self, users):
        """Save users to JSON file"""
        try:
            with open(self.users_file, 'w') as f:
                json.dump(users, f, indent=2)
            return True
        except:
            return False
    
    def get_user(self, user_id):
        """Get user by ID"""
        users = self.load_users()
        return users.get(user_id)
    
    def save_user(self, user_id, user_data):
        """Save user data"""
        users = self.load_users()
        users[user_id] = user_data
        return self.save_users(users)

# Initialize local storage
user_storage = LocalUserStorage()

class User(UserMixin):
    def __init__(self, id, username, email, password_hash=None, cms_username=None, cms_password=None):
        self.id = id
        self.username = username
        self.email = email
        self.password_hash = password_hash
        self.cms_username = cms_username
        self.cms_password = cms_password

    def to_dict(self):
        return {
            'username': self.username,
            'email': self.email,
            'password_hash': self.password_hash,
            'cms_username': self.cms_username,
            'cms_password': self.cms_password
        }

    # Phase 6: Sync Helper - Create User from Dict
    @staticmethod
    def from_dict(data):
        return User(
            id=data.get('username'),
            username=data.get('username'),
            email=data.get('email'),
            password_hash=data.get('password_hash'),
            cms_username=data.get('cms_username'),
            cms_password=data.get('cms_password')
        )


    @staticmethod
    def get(user_id):
        """Get user by ID from storage (Local or Firestore)"""
        try:
            # Try Firestore first (works in both testing and production)
            from app.services.firestore_service import get_firestore_service
            fs = get_firestore_service()
            if fs.is_available():
                data = fs.get_document('users', user_id)
                if data:
                    return User.from_dict(data)

            # Fallback to local JSON
            user_data = user_storage.get_user(user_id)
            if user_data:
                return User(id=user_id, **user_data)
            return None
        except Exception as e:
            logger.warning(f"Error getting user {user_id}: {e}")
            return None

    
    @staticmethod
    def create(username, email, password, cms_username=None, cms_password=None):
        """Create a new user"""
        try:
            user_data = {
                'username': username,
                'email': email,
                'password_hash': generate_password_hash(password),
                'cms_username': cms_username,
                'cms_password': cms_password
            }
            
            if user_storage.save_user(username, user_data):
                return User(id=username, **user_data)
            return None
        except Exception as e:
            logger.warning(f"Error creating user: {e}")
            return None
    
    def check_password(self, password):
        """Check if provided password matches user's password"""
        if self.password_hash:
            return check_password_hash(self.password_hash, password)
        return False
    
    def update_cms_credentials(self, cms_username, cms_password):
        """Update CMS credentials"""
        try:
            self.cms_username = cms_username
            self.cms_password = cms_password
            return user_storage.save_user(self.id, self.to_dict())
        except Exception as e:
            logger.warning(f"Error updating CMS credentials: {e}")
            return False


