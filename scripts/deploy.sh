#!/bin/bash
# UniTrack Deployment Script
# Usage: ./scripts/deploy.sh [testing|production]

set -e

ENV=${1:-testing}

if [ "$ENV" = "production" ]; then
    PROJECT_ID="unitrack-61e76"
    SERVICE_NAME="university-portal"
    ENV_FILE=".env.production"
    echo "🚀 Deploying to PRODUCTION ($PROJECT_ID)..."
    echo "⚠️  Are you sure? Press Ctrl+C to cancel, Enter to continue..."
    read
elif [ "$ENV" = "testing" ]; then
    PROJECT_ID="unitrack-testing"
    SERVICE_NAME="university-portal-test"
    ENV_FILE=".env.testing"
    echo "🧪 Deploying to TESTING ($PROJECT_ID)..."
else
    echo "❌ Unknown environment: $ENV"
    echo "Usage: $0 [testing|production]"
    exit 1
fi

# Load env vars for deployment
set -a
source "$ENV_FILE"
set +a

testing_env=""
if [ "$ENV" = testing ]; then
    allowlist_file="$(dirname "$0")/../testing-login-allowlist.txt"
    TESTING_LOGIN_ALLOWLIST=$(python3 - "$allowlist_file" <<'PYTHON'
import os
from pathlib import Path
import re
import sys

entries = (os.environ.get('TESTING_LOGIN_ALLOWLIST') or Path(sys.argv[1]).read_text()).split()
if not entries or any(not re.fullmatch(r'(0[1-9]|1[0-5])-[0-9]{6}-[0-9]{3}', item) for item in entries):
    raise SystemExit('Configure valid enrollment IDs for the testing allowlist.')
print(' '.join(entries))
PYTHON
)
    testing_env=",TESTING_LOGIN_ALLOWLIST=$TESTING_LOGIN_ALLOWLIST"
fi

echo ""
echo "📦 Building and deploying Cloud Run service: $SERVICE_NAME"
gcloud run deploy "$SERVICE_NAME" \
    --project "$PROJECT_ID" \
    --source . \
    --region us-central1 \
    --allow-unauthenticated \
    --set-env-vars "APP_ENV=$ENV,GA4_MEASUREMENT_ID=${GA4_MEASUREMENT_ID:-},GA4_USER_ID_SECRET=${GA4_USER_ID_SECRET:-},USE_FIRESTORE=true,COST_SAFETY_ENABLED=true,FIREBASE_PROJECT_ID=$PROJECT_ID,FIREBASE_API_KEY=$FIREBASE_API_KEY,FIREBASE_AUTH_DOMAIN=$FIREBASE_AUTH_DOMAIN,FIREBASE_STORAGE_BUCKET=$FIREBASE_STORAGE_BUCKET,FIREBASE_MESSAGING_SENDER_ID=$FIREBASE_MESSAGING_SENDER_ID,FIREBASE_APP_ID=$FIREBASE_APP_ID,FIREBASE_VAPID_KEY=$FIREBASE_VAPID_KEY,APP_URL=$APP_URL,SECRET_KEY=$SECRET_KEY,SCHEDULER_SECRET=$SCHEDULER_SECRET,DISCORD_CLIENT_ID=$DISCORD_CLIENT_ID,DISCORD_CLIENT_SECRET=$DISCORD_CLIENT_SECRET,DISCORD_BOT_TOKEN=$DISCORD_BOT_TOKEN,DISCORD_PUBLIC_KEY=$DISCORD_PUBLIC_KEY,DISCORD_GUILD_ID=$DISCORD_GUILD_ID,DISCORD_LOG_WEBHOOK_URL=$DISCORD_LOG_WEBHOOK_URL,DOWNLOAD_ENCRYPTION_KEY=$DOWNLOAD_ENCRYPTION_KEY,TURNSTILE_SITE_KEY=${TURNSTILE_SITE_KEY:-},TURNSTILE_SECRET_KEY=${TURNSTILE_SECRET_KEY:-}$testing_env"

echo ""
echo "🌐 Deploying Firebase Hosting: $ENV"
firebase use "$ENV"
firebase deploy --only "hosting:$ENV"

echo ""
echo "✅ Deployment to $ENV complete!"
if [ "$ENV" = "testing" ]; then
    echo "🔗 URL: https://unitrack-testing.web.app"
else
    echo "🔗 URL: https://unitrack-61e76.web.app"
fi
