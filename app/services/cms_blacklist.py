import logging
import os
from typing import List

logger = logging.getLogger(__name__)

class CMSBlacklistService:
    """
    Manages the verification of CMS IDs against an approved blacklist via text files.
    """
    
    def __init__(self):
        # Setup paths for blacklist file in the project root
        self.base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        self.blacklist_file = os.path.join(self.base_dir, 'blacklist.txt')
        
    def _read_list_file(self, filepath: str) -> List[str]:
        """Read the list of IDs from the text file. Creates it with instructions if it doesn't exist."""
        if not os.path.exists(filepath):
            try:
                with open(filepath, 'w') as f:
                    f.write("# BLACKLIST\n")
                    f.write("# Add CMS IDs that are NOT allowed to login (one per line).\n")
                    f.write("# Users listed here will be completely blocked.\n")
                    f.write("# Example:\n")
                    f.write("# 02-000000-001\n")
            except Exception as e:
                logger.error(f"Could not create file {filepath}: {e}")
                return []
            
        try:
            with open(filepath, 'r') as f:
                # Handle inline comments and case-insensitivity
                return [line.split('#')[0].strip().lower() for line in f if line.strip() and not line.strip().startswith('#')]
        except Exception as e:
            logger.error(f"Could not read file {filepath}: {e}")
            return []

    def check_access(self, cms_id: str) -> tuple[bool, str]:
        """Check if a CMS ID is allowed to access the system and return the reason if blocked."""
        # 1. Check Blacklist
        blacklist = self._read_list_file(self.blacklist_file)
        if cms_id.strip().lower() in blacklist:
            logger.warning(f"Login blocked: {cms_id} is in blacklist.")
            return False, "Your account has been blacklisted and cannot access the system."
            
        # 2. Allow everyone else
        return True, ""

    def is_allowed(self, cms_id: str) -> bool:
        """Check if a CMS ID is allowed to access the system (backward compatibility)"""
        allowed, _ = self.check_access(cms_id)
        return allowed

_blacklist_service = CMSBlacklistService()

def get_blacklist_service():
    return _blacklist_service
