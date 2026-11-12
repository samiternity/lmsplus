import os
import sys
import logging
import argparse
from datetime import datetime
from werkzeug.security import generate_password_hash

# Add project root to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services.firestore_service import get_firestore_service
from app.cms_scraper import BahriaCMSScraper
from app.models.account import CMSAccount
from app.utils.enhanced_scraper import scraper_manager

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def sync_data(username, password):
    """
    1. Login to CMS locally (bypass cloud block)
    2. Scrape data
    3. Upload User + Hash + Data to Firestore
    """
    logger.info(f"Starting Hybrid Sync for {username}...")

    # 1. Initialize access
    fs = get_firestore_service()
    if not fs.is_available():
        logger.error("Firestore not available. Please check credentials.")
        return False

    # 2. Login & Scrape
    logger.info("Authenticating with CMS...")
    # Create temporary account for scraper
    temp_account = CMSAccount(
        account_id='sync_temp',
        cms_username=username,
        cms_password=password
    )
    scraper = scraper_manager.get_scraper(temp_account)
    if not scraper.login(username, password):
        # Retry once just in case
        if not scraper.login(username, password):
            logger.error("CMS Login failed. Check credentials or internet.")
            return False

    logger.info("Login successful. Scraping data...")

    # 3. Create/Update User Document (Login Validation)
    user_data = {
        'username': username,
        'email': f"{username}@student.bahria.edu.pk",
        'cms_username': username,
        'password_hash': generate_password_hash(password), # Store hash to verify in cloud
        'cms_password': password, # Optional: Store encrypted if we want auto-refresh (skip for now for security/simplicity)
        'synced_at': datetime.utcnow().isoformat()
    }
    
    # Save User Profile
    fs.save_document('users', username, user_data)
    logger.info("User profile & credentials synced.")

    # 4. Scrape & Upload Account Data
    # Profile
    profile = scraper.scrape_profile(username)
    if profile:
        fs.save_document('account_data', f'{username}_profile', profile)
        logger.info("Profile data synced.")

    # Attendance
    attendance = scraper.scrape_attendance(username)
    if attendance:
        fs.save_document('account_data', f'{username}_attendance', attendance)
        logger.info("Attendance data synced.")

    # Transcript / Marks (If available in scraper, simplified here)
    # Add other scrapers as needed
    
    logger.info("Hybrid Sync Complete! You can now login on the deployed app.")
    return True

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Sync CMS data to Firestore for Cloud Run access')
    parser.add_argument('username', help='CMS Username')
    parser.add_argument('password', help='CMS Password')
    
    args = parser.parse_args()
    
    sync_data(args.username, args.password)
