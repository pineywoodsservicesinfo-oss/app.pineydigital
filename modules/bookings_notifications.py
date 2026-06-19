"""
bookings_notifications.py — SMS Notifications for Bookings
Piney Digital Outreach System — Booking Management

Sends booking confirmations, reminders, and cancellation notices via Twilio.
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
        logger.info("Twilio client initialized for bookings")
    except Exception as e:
        logger.error(f"Failed to initialize Twilio: {e}")


def send_sms(to_phone: str, message: str) -> dict:
    """Send SMS via Twilio. Returns result dict."""
    if not twilio_client:
        logger.warning("Twilio not configured. SMS not sent.")
        return {"success": False, "error": "Twilio not configured"}
    
    if not to_phone:
        return {"success": False, "error": "No phone number"}
    
    try:
        message_obj = twilio_client.messages.create(
            body=message,
            from_=TWILIO_FROM,
            to=to_phone
        )
        logger.info(f"Booking SMS sent to {to_phone}: {message_obj.sid}")
        return {"success": True, "sid": message_obj.sid}
    except Exception as e:
        logger.error(f"SMS failed: {e}")
        return {"success": False, "error": str(e)}


def send_booking_confirmation(customer_phone: str, customer_name: str,
                              business_name: str, service_name: str,
                              staff_name: str, booking_date: str,
                              booking_time: str) -> dict:
    """Send booking confirmation to customer."""
    date_str = datetime.strptime(booking_date, "%Y-%m-%d").strftime("%a, %b %d")
    
    message = (
        f"✓ Booking Confirmed!\n\n"
        f"{business_name}\n"
        f"{service_name} with {staff_name or 'our team'}\n"
        f"{date_str} at {booking_time}\n\n"
        f"Reply CANCEL to cancel or RESCHEDULE to change."
    )
    
    return send_sms(customer_phone, message)


def send_booking_request_notification(business_phone: str, customer_name: str,
                                      service_name: str, booking_date: str,
                                      booking_time: str, staff_name: str = None) -> dict:
    """Notify business of new booking request."""
    date_str = datetime.strptime(booking_date, "%Y-%m-%d").strftime("%a, %b %d")
    
    message = (
        f"📅 New Booking Request\n\n"
        f"Customer: {customer_name}\n"
        f"Service: {service_name}\n"
        f"When: {date_str} at {booking_time}\n"
        f"Staff: {staff_name or 'Any'}\n\n"
        f"Login to dashboard to confirm."
    )
    
    return send_sms(business_phone, message)


def send_booking_reminder(customer_phone: str, customer_name: str,
                          business_name: str, service_name: str,
                          booking_date: str, booking_time: str,
                          hours_before: int = 24) -> dict:
    """Send appointment reminder."""
    date_str = datetime.strptime(booking_date, "%Y-%m-%d").strftime("%a, %b %d")
    
    message = (
        f"⏰ Reminder: Your appointment is in {hours_before} hours!\n\n"
        f"{business_name}\n"
        f"{service_name}\n"
        f"{date_str} at {booking_time}\n\n"
        f"See you soon! Reply C to cancel."
    )
    
    return send_sms(customer_phone, message)


def send_booking_cancellation(customer_phone: str, customer_name: str,
                               business_name: str, service_name: str,
                               booking_date: str, booking_time: str,
                               reason: str = None) -> dict:
    """Notify customer of cancelled booking."""
    date_str = datetime.strptime(booking_date, "%Y-%m-%d").strftime("%a, %b %d")
    
    message = (
        f"❌ Booking Cancelled\n\n"
        f"{business_name}\n"
        f"{service_name} on {date_str} at {booking_time}\n\n"
    )
    
    if reason:
        message += f"Reason: {reason}\n\n"
    
    message += "Please contact us to reschedule."
    
    return send_sms(customer_phone, message)


def send_reschedule_confirmation(customer_phone: str, customer_name: str,
                                  business_name: str, service_name: str,
                                  old_date: str, old_time: str,
                                  new_date: str, new_time: str) -> dict:
    """Confirm booking reschedule."""
    old_date_str = datetime.strptime(old_date, "%Y-%m-%d").strftime("%a, %b %d")
    new_date_str = datetime.strptime(new_date, "%Y-%m-%d").strftime("%a, %b %d")
    
    message = (
        f"✓ Booking Rescheduled\n\n"
        f"{business_name}\n"
        f"{service_name}\n\n"
        f"OLD: {old_date_str} at {old_time}\n"
        f"NEW: {new_date_str} at {new_time}\n\n"
        f"See you then!"
    )
    
    return send_sms(customer_phone, message)
