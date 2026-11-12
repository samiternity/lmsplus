import sys
import os
import argparse

# Parse --env argument before loading environment
parser = argparse.ArgumentParser(description='UniTrack Server')
parser.add_argument('--env', choices=['local', 'testing', 'production'], default=None,
                    help='Override APP_ENV (default: from .env file)')
args, _ = parser.parse_known_args()

# Load environment configuration
if args.env:
    os.environ['APP_ENV'] = args.env

from app.config import get_config
config = get_config()

# Create the Flask app instance
from app import create_app
app = create_app()

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    debug = config.is_testing
    
    env_label = config.env_name.upper()
    print("=" * 60)
    print(f"[START] Starting UniTrack - {env_label} Environment")
    print("=" * 60)
    print(f"[DB] Firestore: {'ENABLED' if config.use_firestore else 'DISABLED'} (project: {config.firebase_project_id})")
    print(f"[NET] Port: {port}")
    print(f"[URL] URL: http://localhost:{port}")
    print(f"[SYS] Debug Mode: {debug}")
    print("=" * 60)
    
    app.run(
        host='127.0.0.1' if config.is_local else '0.0.0.0',
        port=port,
        debug=debug
    )
