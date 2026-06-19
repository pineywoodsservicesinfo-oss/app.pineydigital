"""
reviews_notifications.py — SMS Notifications for Review Requests
Piney Digital Outreach System — Review Management

Sends review request SMS via Twilio with smart routing.
"""

import os
import logging
from datetime import datetime
from pathlib import Path
from twilio.rest import Client

from modules.utils import load_env

logger = logging.getLogger(__name__)

load_env()

TWILIO_SID   = os.environ.get("TWILIO_ACCOUNT_SID", "")
TWILIO_TOKEN = os.environ.get("TWILIO_AUTH_TOKEN", "")
TWILIO_FROM  = os.environ.get("TWILIO_PHONE_NUMBER", "")

# Initialize Twilio client
twilio_client = None
if TWILIO_SID and TWILIO_TOKEN:
    try:
        twilio_client = Client(TWILIO_SID, TWILIO_TOKEN)
        logger.info("Twilio client initialized for reviews")
    except Exception as e:
        logger.error(f"Failed to initialize Twilio: {e}")


def send_sms(to_phone: str, message: str, link: str = None) -> dict:
    """Send SMS via Twilio. Returns result dict."""
    if not twilio_client:
        logger.warning("Twilio not configured. SMS not sent.")
        return {"success": False, "error": "Twilio not configured"}
    
    if not to_phone:
        return {"success": False, "error": "No phone number"}
    
    try:
        # Combine message and link
        full_message = message
        if link:
            full_message += f"\n{link}"
        
        message_obj = twilio_client.messages.create(
            body=full_message,
            from_=TWILIO_FROM,
            to=to_phone
        )
        logger.info(f"Review SMS sent to {to_phone}: {message_obj.sid}")
        return {"success": True, "sid": message_obj.sid}
    except Exception as e:
        logger.error(f"SMS failed: {e}")
        return {"success": False, "error": str(e)}


def send_review_request(customer_phone: str, customer_name: str,
                        business_name: str, review_link: str,
                        custom_message: str = None) -> dict:
    """Send review request SMS to customer."""
    if custom_message:
        message = custom_message.replace("{name}", customer_name or "there")
        message = message.replace("{business}", business_name)
    else:
        message = (
            f"Hi {customer_name or 'there'}! Thanks for visiting {business_name} today. "
            f"Could you spare 30 seconds to rate your experience? Your feedback helps us improve! 🌟"
        )
    
    return send_sms(customer_phone, message, review_link)


def send_review_reminder(customer_phone: str, customer_name: str,
                         business_name: str, review_link: str) -> dict:
    """Send reminder to customer who hasn't rated yet."""
    message = (
        f"Hi {customer_name or 'there'}! Just a friendly reminder from {business_name} - "
        f"we'd love to hear your feedback! It only takes 30 seconds: {review_link}"
    )
    
    return send_sms(customer_phone, message)


def send_thank_you(customer_phone: str, customer_name: str,
                   business_name: str, stars: int) -> dict:
    """Send thank you message after rating."""
    if stars >= 4:
        message = (
            f"Thank you so much for the {stars}-star review, {customer_name}! 🎉 "
            f"We're thrilled you had a great experience at {business_name}. "
            f"See you next time!"
        )
    else:
        message = (
            f"Thank you for your feedback, {customer_name}. We appreciate your honesty "
            f"and will use it to improve. Hope to see you again at {business_name}!"
        )
    
    return send_sms(customer_phone, message)
