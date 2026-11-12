from firebase_functions import pubsub_fn
from firebase_admin import initialize_app
import base64
import json
import os
import logging
from googleapiclient import discovery
from google.auth import default

# Initialize Firebase Admin
initialize_app()

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# NOTE: Billing auto-shutdown temporarily disabled
# TOPIC_NAME = "budget-alerts"
# 
# @pubsub_fn.on_message_published(topic=TOPIC_NAME)
# def stop_billing(event: pubsub_fn.CloudEvent[pubsub_fn.MessagePublishedData]) -> None:
#     """
#     Cloud Function triggered by Pub/Sub budget alerts.
#     Disables billing if budget is exceeded.
#     """
#     try:
#         # Decode the Pub/Sub message
#         message_data = base64.b64decode(event.data.message.data).decode('utf-8')
#         budget_notification = json.loads(message_data)
#         logger.info(f"Received budget notification: {budget_notification}")
#         
#         # In this strict safety mode, any alert triggers shutdown
#         # You could add logic here to check specific thresholds e.g.
#         # if budget_notification.get('alertThresholdExceeded', 0) > 0.9:
#         
#         _disable_billing()
#             
#     except Exception as e:
#         logger.error(f"Error in stop_billing: {e}")
# 
# def _disable_billing():
#     """
#     Disable billing for the project.
#     """
#     PROJECT_ID = os.environ.get('GCP_PROJECT') or os.environ.get('GCLOUD_PROJECT')
#     if not PROJECT_ID:
#         logger.error("No PROJECT_ID environment variable found")
#         return
# 
#     PROJECT_NAME = f'projects/{PROJECT_ID}'
# 
#     try:
#         credentials, _ = default()
#         service = discovery.build('cloudbilling', 'v1', credentials=credentials)
# 
#         # 1. Get billing info
#         billing_info = service.projects().getBillingInfo(name=PROJECT_NAME).execute()
#         
#         if billing_info.get('billingEnabled'):
#             logger.warning(f"Disabling billing for {PROJECT_NAME}...")
#             
#             # 2. Disable billing
#             body = {'billingAccountName': ''} 
#             response = service.projects().updateBillingInfo(name=PROJECT_NAME, body=body).execute()
#             
#             logger.info(f"Billing disabled successfully: {response}")
#         else:
#             logger.info("Billing already disabled.")
#             
#     except Exception as e:
#         logger.critical(f"FAILED TO DISABLE BILLING: {e}")

