#!/bin/bash
# Setup cron job for background notifications
# Run this script once to set up automatic notifications

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

# Make trigger script executable
chmod +x "$SCRIPT_DIR/trigger_notifications.sh"

# Add cron job (runs every 30 minutes)
CRON_JOB="*/30 * * * * cd $PROJECT_DIR && $SCRIPT_DIR/trigger_notifications.sh dev >> $PROJECT_DIR/logs/cron.log 2>&1"

# Check if cron job already exists
if crontab -l 2>/dev/null | grep -q "trigger_notifications.sh"; then
    echo "Cron job already exists!"
    echo "Current cron jobs:"
    crontab -l | grep "trigger_notifications.sh"
else
    # Add to crontab
    (crontab -l 2>/dev/null; echo "$CRON_JOB") | crontab -
    echo "✓ Cron job added successfully!"
    echo "Notifications will run every 30 minutes"
    echo ""
    echo "To view cron jobs: crontab -l"
    echo "To remove: crontab -e (then delete the line)"
    echo "To view logs: tail -f $PROJECT_DIR/logs/cron.log"
fi

# Create logs directory if it doesn't exist
mkdir -p "$PROJECT_DIR/logs"

echo ""
echo "Testing the trigger script now..."
"$SCRIPT_DIR/trigger_notifications.sh" dev
