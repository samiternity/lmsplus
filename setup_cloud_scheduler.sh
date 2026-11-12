#!/bin/bash

# Cloud Scheduler Setup Script
# Sets up daily multi-slot batch notification checks every 1 minute during 2:00 PM and 8:00 PM hours
# Processes users in 60 micro-batches at 2:00 PM - 2:59 PM and 8:00 PM - 8:59 PM Pakistan time

set -e

echo "=========================================="
echo "Cloud Scheduler Setup for Multi-Slot Batch Notifications"
echo "=========================================="
echo ""

# Configuration
PROJECT_ID="unitrack-61e76"
REGION="us-central1"
SERVICE_URL="https://unitrack-61e76.web.app"
: "${SCHEDULER_SECRET:?Set SCHEDULER_SECRET from your private secret store before running this script.}"

# Check if gcloud is installed
if ! command -v gcloud &> /dev/null; then
    echo "❌ Error: gcloud CLI is not installed"
    echo "Please install it from: https://cloud.google.com/sdk/docs/install"
    exit 1
fi

# Check if logged in
if ! gcloud auth list --filter=status:ACTIVE --format="value(account)" &> /dev/null; then
    echo "❌ Error: Not logged in to gcloud"
    echo "Please run: gcloud auth login"
    exit 1
fi

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
          --schedule="* 14,20 * * *" \
          --uri="$SERVICE_URL/api/internal/run-notifications" \
          --http-method=POST \
          --headers="Authorization=Bearer $SCHEDULER_SECRET" \
          --location=$REGION \
          --time-zone="Asia/Karachi" \
          --description="Daily batch notifications - 60 batches at 2:00 PM and 8:00 PM PKT" \
          --quiet
        echo "✅ Job updated successfully!"
    else
        echo "ℹ️  Keeping existing job unchanged"
    fi
else
    echo "🚀 Creating Cloud Scheduler job..."
    gcloud scheduler jobs create http evening-batch-notifications \
      --schedule="* 14,20 * * *" \
      --uri="$SERVICE_URL/api/internal/run-notifications" \
      --http-method=POST \
      --headers="Authorization=Bearer $SCHEDULER_SECRET" \
      --location=$REGION \
      --time-zone="Asia/Karachi" \
      --description="Daily batch notifications - 60 batches at 2:00 PM and 8:00 PM PKT" \
      --quiet
    echo "✅ Job created successfully!"
fi

# Keep cache-only deadline checks separate from the daily scrape batches.
if gcloud scheduler jobs describe unitrack-urgent-check --location="$REGION" >/dev/null 2>&1; then
    URGENT_ACTION=update
else
    URGENT_ACTION=create
fi
gcloud scheduler jobs "$URGENT_ACTION" http unitrack-urgent-check \
    --schedule="0 * * * *" \
    --uri="$SERVICE_URL/api/internal/urgent-check" \
    --http-method=POST \
    --headers="Authorization=Bearer $SCHEDULER_SECRET" \
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
echo "  Schedule: * 14,20 * * * (Every minute during 2:00 PM and 8:00 PM hours)"
echo "  Endpoint: $SERVICE_URL/api/internal/run-notifications"
echo "  Region: $REGION"
echo ""
echo "🕒 Daily Multi-Slot Batch Schedule (Pakistan Time):"
echo "  Slot 1: 2:00 PM to 2:59 PM - 60 micro-batches"
echo "  Slot 2: 8:00 PM to 8:59 PM - 60 micro-batches"
echo ""
echo "🔍 Verify the job:"
echo "  gcloud scheduler jobs describe evening-batch-notifications --location=$REGION"
echo ""
echo "🧪 Test the job manually:"
echo "  gcloud scheduler jobs run evening-batch-notifications --location=$REGION"
echo ""
echo "📊 View job logs:"
echo "  gcloud scheduler jobs logs evening-batch-notifications --location=$REGION --limit=10"
echo ""
echo "💰 Free Tier Compliance:"
echo "  - 2 runs/day x 60 min = 120 invocations/day (still well within 50,000 free operations/day)"
echo "  - Two Cloud Scheduler jobs"
echo ""
echo "🎉 Multi-slot batch notifications configured!"
