"""
booking_reminders.py — Automated SMS Reminders for Bookings
Piney Digital Outreach System — Booking Management

Sends automated reminders 24 hours and 1 hour before appointments.
Run via cron job or scheduled task every 15 minutes.
"""

import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from modules.bookings_db import get_connection, get_business_bookings, get_booking
from modules.bookings_notifications import send_booking_reminder
from modules.loyalty_db import get_loyalty_business


def send_due_reminders():
    """Find and send all due reminders. Run every 15 minutes."""
    conn = get_connection()
    c = conn.cursor()
    
    now = datetime.now()
    
    # Calculate time windows
    # For 24-hour reminders: bookings in next 24-25 hours
    time_24h_start = now + timedelta(hours=24)
    time_24h_end = now + timedelta(hours=25)
    
    # For 1-hour reminders: bookings in next 1-2 hours  
    time_1h_start = now + timedelta(hours=1)
    time_1h_end = now + timedelta(hours=2)
    
    sent_count = 0
    
    # ── Send 24-hour reminders ────────────────────────────────
    c.execute("""
        SELECT id, business_id, customer_name, customer_phone,
               booking_date, booking_time, service_id
        FROM bookings
        WHERE status = 'confirmed'
        AND reminder_sent < 1
        AND booking_date = ?
        AND booking_time BETWEEN ? AND ?
    """, (
        time_24h_start.strftime("%Y-%m-%d"),
        time_24h_start.strftime("%H:%M"),
        time_24h_end.strftime("%H:%M")
    ))
    
    bookings_24h = c.fetchall()
    
    for booking in bookings_24h:
        booking = dict(booking)
        
        # Get business info
        biz = get_loyalty_business(booking["business_id"])
        if not biz or not booking.get("customer_phone"):
            continue
        
        # Get service name
        c.execute("SELECT name FROM booking_services WHERE id = ?", (booking["service_id"],))
        service_row = c.fetchone()
        service_name = service_row["name"] if service_row else "Appointment"
        
        # Send reminder
        result = send_booking_reminder(
            customer_phone=booking["customer_phone"],
            customer_name=booking["customer_name"],
            business_name=biz["name"],
            service_name=service_name,
            booking_date=booking["booking_date"],
            booking_time=booking["booking_time"],
            hours_before=24
        )
        
        if result.get("success"):
            # Mark 24hr reminder as sent
            c.execute("""
                UPDATE bookings SET reminder_sent = 1
                WHERE id = ?
            """, (booking["id"],))
            conn.commit()
            sent_count += 1
            print(f"✓ 24h reminder sent to {booking['customer_name']}")
    
    # ── Send 1-hour reminders ─────────────────────────────────
    c.execute("""
        SELECT id, business_id, customer_name, customer_phone,
               booking_date, booking_time, service_id
        FROM bookings
        WHERE status = 'confirmed'
        AND reminder_sent < 2
        AND booking_date = ?
        AND booking_time BETWEEN ? AND ?
    """, (
        time_1h_start.strftime("%Y-%m-%d"),
        time_1h_start.strftime("%H:%M"),
        time_1h_end.strftime("%H:%M")
    ))
    
    bookings_1h = c.fetchall()
    
    for booking in bookings_1h:
        booking = dict(booking)
        
        # Get business info
        biz = get_loyalty_business(booking["business_id"])
        if not biz or not booking.get("customer_phone"):
            continue
        
        # Get service name
        c.execute("SELECT name FROM booking_services WHERE id = ?", (booking["service_id"],))
        service_row = c.fetchone()
        service_name = service_row["name"] if service_row else "Appointment"
        
        # Send reminder
        result = send_booking_reminder(
            customer_phone=booking["customer_phone"],
            customer_name=booking["customer_name"],
            business_name=biz["name"],
            service_name=service_name,
            booking_date=booking["booking_date"],
            booking_time=booking["booking_time"],
            hours_before=1
        )
        
        if result.get("success"):
            # Mark 1hr reminder as sent (reminder_sent = 2 = both sent)
            c.execute("""
                UPDATE bookings SET reminder_sent = 2
                WHERE id = ?
            """, (booking["id"],))
            conn.commit()
            sent_count += 1
            print(f"✓ 1h reminder sent to {booking['customer_name']}")
    
    conn.close()
    
    print(f"\n✅ Reminder job complete: {sent_count} reminders sent")
    return sent_count


def reset_reminders_for_date(date_str: str = None):
    """Reset reminder flags for testing. Optional date (default: today)."""
    if not date_str:
        date_str = datetime.now().strftime("%Y-%m-%d")
    
    conn = get_connection()
    conn.execute("""
        UPDATE bookings SET reminder_sent = 0
        WHERE booking_date = ?
    """, (date_str,))
    conn.commit()
    conn.close()
    print(f"✓ Reset reminders for {date_str}")


if __name__ == "__main__":
    # Run manually for testing
    print(f"Running reminder check at {datetime.now().isoformat()}\n")
    send_due_reminders()
