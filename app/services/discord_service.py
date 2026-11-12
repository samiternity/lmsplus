import os
import requests
import json
import logging
from concurrent.futures import ThreadPoolExecutor

logger = logging.getLogger(__name__)

DISCORD_API_BASE_URL = 'https://discord.com/api/v10'

# Best-effort background executor for informational webhook logs.
# User reminders must finish synchronously or be accepted by Cloud Tasks.
_local_executor = ThreadPoolExecutor(max_workers=5)

class DiscordService:
    def __init__(self):
        self.bot_token = os.environ.get('DISCORD_BOT_TOKEN')
        self.headers = {
            'Authorization': f"Bot {self.bot_token}",
            'Content-Type': 'application/json'
        }

    def is_configured(self):
        return bool(self.bot_token)

    def create_dm_channel(self, discord_id: str) -> str:
        """Create a DM channel with a user and return the channel ID."""
        if not self.is_configured():
            logger.warning("DiscordService is not configured (missing bot token).")
            return None

        url = f"{DISCORD_API_BASE_URL}/users/@me/channels"
        data = {'recipient_id': discord_id}

        try:
            response = requests.post(url, headers=self.headers, json=data, timeout=10)
            response.raise_for_status()
            return response.json().get('id')
        except requests.exceptions.RequestException as e:
            logger.error(f"Failed to create Discord DM channel for {discord_id}: {e}")
            if hasattr(e.response, 'text'):
                logger.error(f"Response: {e.response.text}")
            return None

    def send_message(self, channel_id: str, title: str, description: str, color: int = 0x5865F2, url: str = None, fields: list = None):
        """Send an embedded message to a Discord channel."""
        if not self.is_configured():
            return False

        if not channel_id:
            logger.error("Cannot send Discord message: missing channel_id")
            return False

        api_url = f"{DISCORD_API_BASE_URL}/channels/{channel_id}/messages"

        embed = {
            'title': title,
            'description': description,
            'color': color
        }

        if url:
            embed['url'] = url

        if fields:
            embed['fields'] = fields

        data = {
            'embeds': [embed]
        }

        try:
            response = requests.post(api_url, headers=self.headers, json=data, timeout=10)

            # Handle rate limiting explicitly
            if response.status_code == 429:
                retry_after = response.json().get('retry_after', 1)
                logger.warning(f"Discord rate limited! Retry after {retry_after}s.")
                # We could retry here, but in a queue architecture, we raise so the queue handles retries.
                response.raise_for_status()

            response.raise_for_status()
            return True
        except requests.exceptions.RequestException as e:
            logger.error(f"Failed to send Discord message to channel {channel_id}: {e}")
            if hasattr(e, 'response') and e.response is not None:
                logger.error(f"Response: {e.response.text}")
            return False

def get_discord_service():
    return DiscordService()

def enqueue_discord_notification(discord_id: str, title: str, description: str, color: int = 0x5865F2, url: str = None, fields: list = None):
    """
    Queue a Discord notification.
    Attempts to use Google Cloud Tasks for robust rate limiting (max 4/sec).
    Falls back to synchronous delivery so request completion cannot interrupt DMs.
    """
    if not get_discord_service().is_configured():
        logger.warning("Cannot deliver Discord notification: missing bot token")
        return False

    project_id = os.environ.get('FIREBASE_PROJECT_ID')
    queue_name = os.environ.get('DISCORD_QUEUE_NAME', 'discord-notifications')
    location = os.environ.get('GCP_LOCATION', 'us-central1') # Adjust as needed

    # Payload to send to our worker
    payload = {
        'discord_id': discord_id,
        'title': title,
        'description': description,
        'color': color,
        'url': url,
        'fields': fields or []
    }

    worker_url = os.environ.get('WORKER_URL')
    scheduler_secret = os.environ.get('SCHEDULER_SECRET')
    if not (worker_url and project_id and scheduler_secret):
        # Finish delivery before Cloud Run can suspend CPU after the response.
        return _process_discord_notification_sync(payload)

    try:
        from google.cloud import tasks_v2
        client = tasks_v2.CloudTasksClient()
        parent = client.queue_path(project_id, location, queue_name)

        target_url = f"{worker_url.rstrip('/')}/api/internal/discord/send_dm"

        task = {
            'http_request': {
                'http_method': tasks_v2.HttpMethod.POST,
                'url': target_url,
                'headers': {
                    'Content-Type': 'application/json',
                    'Authorization': f'Bearer {scheduler_secret}',
                    'X-Scheduler-Secret': scheduler_secret
                },
                'body': json.dumps(payload).encode()
            }
        }

        service_account_email = os.environ.get('WORKER_SERVICE_ACCOUNT')
        if service_account_email:
            # Cloud Tasks uses Authorization for the platform's identity token.
            # Keep the application's shared secret in a separate header.
            task['http_request']['headers'].pop('Authorization')
            task['http_request']['oidc_token'] = {
                'service_account_email': service_account_email,
                'audience': worker_url.rstrip('/')
            }

        response = client.create_task(request={"parent": parent, "task": task})
        logger.info(f"Enqueued Discord DM task: {response.name}")
        return True
    except Exception as e:
        logger.warning("Cloud Tasks enqueue failed (%s); sending Discord DM synchronously", type(e).__name__)

    return _process_discord_notification_sync(payload)

def _process_discord_notification_sync(payload: dict):
    """Synchronous worker function that actually sends the Discord message."""
    service = get_discord_service()
    if not service.is_configured():
        return False

    discord_id = payload.get('discord_id')
    channel_id = service.create_dm_channel(discord_id)

    if channel_id:
        return service.send_message(
            channel_id=channel_id,
            title=payload.get('title'),
            description=payload.get('description'),
            color=payload.get('color'),
            url=payload.get('url'),
            fields=payload.get('fields')
        )
    return False

def send_webhook_message(webhook_url: str, title: str, color: int = 0x5865F2, fields: list = None, description: str = None, footer: str = None):
    """Synchronously send a webhook message to Discord."""
    if not webhook_url:
        return False

    embed = {
        'title': title,
        'color': color
    }
    if description:
        embed['description'] = description
    if fields:
        embed['fields'] = fields
    if footer:
        embed['footer'] = {'text': footer}

    data = {'embeds': [embed]}
    try:
        response = requests.post(webhook_url, json=data, timeout=10)
        response.raise_for_status()
        return True
    except Exception as e:
        logger.error("Failed to send Discord webhook (%s)", type(e).__name__)
        return False

def enqueue_webhook_log(title: str, color: int = 0x5865F2, fields: list = None, description: str = None, footer: str = None, sync: bool = False):
    """Asynchronously enqueue a Discord webhook log if DISCORD_LOG_WEBHOOK_URL is set. If sync is True, send synchronously."""
    webhook_url = os.environ.get('DISCORD_LOG_WEBHOOK_URL')
    if not webhook_url:
        return False

    if sync:
        return send_webhook_message(webhook_url, title, color, fields, description, footer)
    else:
        _local_executor.submit(send_webhook_message, webhook_url, title, color, fields, description, footer)
        return True


def enqueue_mcp_authorization(account_id, provider):
    """Send a registration-channel notification after a connector exchanges its first code."""
    webhook_url = os.environ.get('DISCORD_LOG_WEBHOOK_URL')
    if not webhook_url:
        return

    def send():
        try:
            from app.routes.auth import BAHRIA_CAMPUS_NAMES
            from app.utils.data_manager import data_manager
            from lms_mcp.runtime import load_account
            from lms_mcp.core import PROVIDER_LABELS

            profile = data_manager.load_account_data(account_id, 'profile') or {}
            name, campus = profile.get('name'), profile.get('campus')
            if not name or not campus:
                account = load_account(account_id)
                if account:
                    name = name or account.student_name
                    campus = campus or BAHRIA_CAMPUS_NAMES.get(str(account.institute_id))
            fields = {'Name': name, 'Program': profile.get('program'), 'Campus': campus,
                      'App': PROVIDER_LABELS.get(provider, PROVIDER_LABELS['other'])}
            send_webhook_message(webhook_url, 'New MCP Authorization', color=0x5865F2, fields=[
                {'name': label, 'value': str(value or 'N/A')[:1024], 'inline': True}
                for label, value in fields.items()
            ])
        except Exception:
            logger.warning('Could not prepare the MCP authorization notification.')

    _local_executor.submit(send)

