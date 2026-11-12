#!/bin/bash
# Trigger background notifications
# Usage: ./scripts/trigger_notifications.sh [dev|prod]

ENV=${1:-dev}

if [ "$ENV" = "prod" ]; then
    URL="https://your-production-domain.com/api/internal/run-notifications"
    SECRET="${SCHEDULER_SECRET_PROD}"
else
    URL="http://localhost:5000/api/internal/run-notifications"
    SECRET="${SCHEDULER_SECRET:-dev-secret-key-change-in-production}"
fi

echo "Triggering notifications for $ENV environment..."
echo "URL: $URL"

response=$(curl -s -w "\n%{http_code}" -X POST "$URL" \
    -H "Authorization: Bearer $SECRET" \
    -H "Content-Type: application/json")

http_code=$(echo "$response" | tail -n1)
body=$(echo "$response" | head -n-1)

if [ "$http_code" = "200" ]; then
    echo "✓ Success!"
    echo "$body" | python3 -m json.tool 2>/dev/null || echo "$body"
else
    echo "✗ Failed with status $http_code"
    echo "$body"
    exit 1
fi
