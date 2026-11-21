#!/bin/bash

# Cloud Scheduler Setup Script
# Processes the regular batches during the 2 PM and 8 PM hours, then
# catches up from 3:00–3:09 PM and 9:00–9:09 PM Pakistan time.

set -e

echo "=========================================="
echo "Cloud Scheduler Setup for Multi-Slot Batch Notifications"
echo "=========================================="
echo ""

# Configuration
PROJECT_ID="unitrack-61e76"
REGION="us-central1"
BATCH_SCHEDULE="* 14,15,20,21 * * *"
BATCH_DESCRIPTION="Daily notification batches with 10 minutes of catch-up after each window (PKT)"

# Check if gcloud is installed
if ! command -v gcloud &> /dev/null; then
    echo "❌ Error: gcloud CLI is not installed"
    echo "Please install it from: https://cloud.google.com/sdk/docs/install"
    exit 1
fi

# Check if logged in
if ! ACTIVE_ACCOUNT=$(gcloud auth list --filter=status:ACTIVE --format="value(account)" 2>/dev/null) ||
   [[ -z $ACTIVE_ACCOUNT ]]; then
    echo "❌ Error: Not logged in to gcloud"
    echo "Please run: gcloud auth login"
    exit 1
fi

# Use after deploying the catch-up handler. Keep the existing target and authentication.
if [[ ${1:-} == --update-batch-schedule ]]; then
    gcloud scheduler jobs update http evening-batch-notifications \
        --schedule="$BATCH_SCHEDULE" \
        --time-zone="Asia/Karachi" \
        --location="$REGION" \
        --project="$PROJECT_ID" \
        --quiet
    exit
fi

: "${SCHEDULER_SECRET:?Set SCHEDULER_SECRET from your private secret store before running this script.}"
SERVICE_URL=${SCHEDULER_SERVICE_URL:-}
if [[ -z $SERVICE_URL ]]; then
    SERVICE_URL=$(gcloud run services describe university-portal \
        --region="$REGION" --project="$PROJECT_ID" --format="value(status.url)")
fi
: "${SERVICE_URL:?Cloud Run service has no URL.}"

# Set project
echo "📋 Setting project to $PROJECT_ID..."
gcloud config set project $PROJECT_ID

# Enable Cloud Scheduler API (if not already enabled)
echo "🔧 Enabling Cloud Scheduler API..."
gcloud services enable cloudscheduler.googleapis.com --quiet

# Check if job already exists
if gcloud scheduler jobs describe evening-batch-notifications --location=$REGION &> /dev/null; then
    echo "⚠️  Cloud Scheduler job 'evening-batch-notifications' already exists"
    read -p "Do you want to update it? (y/n) " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        echo "🔄 Updating existing job..."
        gcloud scheduler jobs update http evening-batch-notifications \
          --schedule="$BATCH_SCHEDULE" \
          --uri="$SERVICE_URL/api/internal/run-notifications" \
          --http-method=POST \
          --update-headers="Authorization=Bearer $SCHEDULER_SECRET" \
          --location=$REGION \
          --time-zone="Asia/Karachi" \
          --description="$BATCH_DESCRIPTION" \
          --quiet
        echo "✅ Job updated successfully!"
    else
        echo "ℹ️  Keeping existing job unchanged"
    fi
else
    echo "🚀 Creating Cloud Scheduler job..."
    gcloud scheduler jobs create http evening-batch-notifications \
      --schedule="$BATCH_SCHEDULE" \
      --uri="$SERVICE_URL/api/internal/run-notifications" \
      --http-method=POST \
      --headers="Authorization=Bearer $SCHEDULER_SECRET" \
      --location=$REGION \
      --time-zone="Asia/Karachi" \
      --description="$BATCH_DESCRIPTION" \
      --quiet
    echo "✅ Job created successfully!"
fi

# Keep cache-only deadline checks separate from the daily scrape batches.
if gcloud scheduler jobs describe unitrack-urgent-check --location="$REGION" >/dev/null 2>&1; then
    URGENT_ACTION=update
    URGENT_HEADERS=--update-headers
else
    URGENT_ACTION=create
    URGENT_HEADERS=--headers
fi
gcloud scheduler jobs "$URGENT_ACTION" http unitrack-urgent-check \
    --schedule="0 * * * *" \
    --uri="$SERVICE_URL/api/internal/urgent-check" \
    --http-method=POST \
    "$URGENT_HEADERS=Authorization=Bearer $SCHEDULER_SECRET" \
    --location="$REGION" \
    --time-zone="Asia/Karachi" \
    --description="Hourly cached deadline reminders and overdue warnings" \
    --quiet

echo ""
echo "=========================================="
echo "✅ Cloud Scheduler Setup Complete!"
echo "=========================================="
echo ""
echo "📋 Job Details:"
echo "  Name: evening-batch-notifications"
echo "  Schedule: $BATCH_SCHEDULE"
echo "  Endpoint: $SERVICE_URL/api/internal/run-notifications"
echo "  Region: $REGION"
echo ""
echo "🕒 Daily Multi-Slot Batch Schedule (Pakistan Time):"
echo "  Slot 1: 2:00 PM to 2:59 PM - 60 micro-batches"
echo "  Slot 2: 8:00 PM to 8:59 PM - 60 micro-batches"
echo "  Catch-up: 3:00 PM to 3:09 PM and 9:00 PM to 9:09 PM"
echo "  The remaining triggers in the 3 PM and 9 PM hours return without processing."
echo ""
echo "🔍 Verify the job:"
echo "  gcloud scheduler jobs describe evening-batch-notifications --location=$REGION"
echo ""
echo "🧪 Test the job manually:"
echo "  gcloud scheduler jobs run evening-batch-notifications --location=$REGION"
echo ""
echo "📊 View job logs:"
echo "  gcloud logging read 'resource.type=\"cloud_scheduler_job\" AND resource.labels.job_id=\"evening-batch-notifications\"' --project=$PROJECT_ID --limit=10"
echo ""
echo "  - 240 daily batch triggers, including 100 that return without processing"
echo "  - Two Cloud Scheduler jobs"
echo ""
echo "🎉 Multi-slot batch notifications configured!"
