"""
loyalty_notifications.py — SMS Notifications for Loyalty Program
Piney Digital Outreach System — LoyaltyLoop

Sends SMS notifications via Twilio when:
- Customer earns a reward (card completed)
- Customer joins a program (optional welcome)
- Business redeems a reward
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
        logger.info("Twilio client initialized")
    except Exception as e:
        logger.error(f"Failed to initialize Twilio: {e}")


def send_sms(to_phone: str, message: str) -> dict:
    """Send SMS via Twilio. Returns result dict."""
    if not twilio_client:
        logger.warning("Twilio not configured. SMS not sent.")
        return {"success": False, "error": "Twilio not configured", "message": message}
    
    if not to_phone:
        return {"success": False, "error": "No phone number"}
    
    try:
        message_obj = twilio_client.messages.create(
            body=message,
            from_=TWILIO_FROM,
            to=to_phone
        )
        logger.info(f"SMS sent to {to_phone}: {message_obj.sid}")
        return {"success": True, "sid": message_obj.sid}
    except Exception as e:
        logger.error(f"SMS failed: {e}")
        return {"success": False, "error": str(e)}


def send_reward_earned(customer_phone: str, customer_name: str, 
                       business_name: str, discount_percent: int,
                       total_rewards: int = 1) -> dict:
    """Send SMS when customer completes a punch card."""
    message = (
        f"🎉 {customer_name}! You just earned a reward at {business_name}! "
        f"Show this message to get {discount_percent}% off. "
        f"This is reward #{total_rewards} - keep collecting!"
    )
    
    return send_sms(customer_phone, message)


def send_welcome_message(customer_phone: str, customer_name: str,
                         business_name: str) -> dict:
    """Send welcome SMS when customer joins a loyalty program."""
    message = (
        f"🌲 Welcome to LoyaltyLoop at {business_name}, {customer_name}! "
        f"Start collecting punches with every visit. "
        f"Show your QR code at checkout to scan. "
        f"Rewards await! 🎯"
    )
    
    return send_sms(customer_phone, message)


def send_card_progress(customer_phone: str, customer_name: str,
                       business_name: str, punches: int, 
                       punches_needed: int) -> dict:
    """Send progress update (optional, for milestone punches)."""
    if punches == punches_needed:
        return  # Don't send - reward message already sent
    
    # Only send at certain milestones (e.g., halfway)
    if punches != punches_needed // 2:
        return {"skipped": True}
    
    remaining = punches_needed - punches
    message = (
        f"📍 {customer_name}, you're halfway there at {business_name}! "
        f"{punches}/{punches_needed} punches collected. "
        f"{remaining} more visits to unlock your reward!"
    )
    
    return send_sms(customer_phone, message)


def send_reward_redeemed(customer_phone: str, customer_name: str,
                         business_name: str, discount_percent: int) -> dict:
    """Send confirmation when reward is redeemed."""
    message = (
        f"✅ Reward redeemed at {business_name}! "
        f"You saved {discount_percent}% on your purchase. "
        f"Your punch card has been reset - start collecting again! 🌲"
    )
    
    return send_sms(customer_phone, message)


# ── Integration with punch operations ─────────────────────

def notify_on_reward_earned(card_data: dict):
    """
    Called after a punch completes a card.
    card_data should contain: customer_phone, customer_name, business_name, 
                           discount_percent, total_rewards
    """
    if not card_data.get("customer_phone"):
        logger.warning("No customer phone for notification")
        return {"skipped": True, "reason": "no_phone"}
    
    return send_reward_earned(
        customer_phone=card_data["customer_phone"],
        customer_name=card_data["customer_name"],
        business_name=card_data["business_name"],
        discount_percent=card_data["discount_percent"],
        total_rewards=card_data.get("total_rewards", 1)
    )
