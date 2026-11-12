#!/usr/bin/env python3
"""Clear LMS cache manually"""

import sys
import os
import shutil

# Cache directories to clear
cache_dirs = [
    'data/lms_cache',
    'data/cache',
    '__pycache__',
    'app/__pycache__',
    'app/services/__pycache__',
    'app/routes/__pycache__',
    'app/models/__pycache__'
]

print("Clearing LMS Cache:")
print("=" * 80)

cleared_count = 0
for cache_dir in cache_dirs:
    if os.path.exists(cache_dir):
        try:
            if os.path.isdir(cache_dir):
                shutil.rmtree(cache_dir)
                print(f"✅ Cleared: {cache_dir}")
                cleared_count += 1
        except Exception as e:
            print(f"❌ Error clearing {cache_dir}: {e}")
    else:
        print(f"⚠️  Not found: {cache_dir}")

print("\n" + "=" * 80)
print(f"Cleared {cleared_count} cache directories")
print("\nNext steps:")
print("1. Restart Flask server (Ctrl+C then 'flask run')")
print("2. Reload the assignments page")
print("3. Check browser console for logs")
