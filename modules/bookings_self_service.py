"""
bookings_self_service.py — Customer Self-Service for Bookings
Piney Digital Outreach System — Booking Management

Allows customers to reschedule/cancel via SMS links.
"""

from flask import Blueprint, render_template_string, request, redirect, url_for, jsonify
from modules.bookings_db import get_booking, cancel_booking, create_booking
from modules.bookings_notifications import send_reschedule_confirmation, send_booking_cancellation
from modules.loyalty_db import get_connection

self_service_bp = Blueprint('self_service', __name__, url_prefix='/booking')


@self_service_bp.route("/manage/<booking_id>")
def manage_booking(booking_id: str):
    """Customer self-service page for managing their booking."""
    booking = get_booking(booking_id)
    
    if not booking:
        return render_template_string("""<!DOCTYPE html><html><head>
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Booking Not Found</title>
<style>
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#0f172a;color:#e2e8f0;min-height:100vh;display:flex;align-items:center;justify-content:center;text-align:center;padding:20px}
.card{background:#1e293b;border-radius:12px;padding:32px;max-width:400px}
h1{font-size:24px;color:#f87171;margin-bottom:12px}
p{color:#94a3b8;line-height:1.5}
</style>
</head><body>
<div class="card">
  <h1>❌ Booking Not Found</h1>
  <p>This booking doesn't exist or has been cancelled.</p>
</div>
</body></html>""", status=404)
    
    # Handle action from SMS link
    action = request.args.get("action")
    
    if action == "cancel":
        if booking["status"] not in ["cancelled", "completed"]:
            cancel_booking(booking_id, reason="Customer cancelled via self-service")
            
            # Send cancellation SMS
            if booking.get("customer_phone"):
                send_booking_cancellation(
                    customer_phone=booking["customer_phone"],
                    customer_name=booking["customer_name"],
                    business_name="Business",  # Would need to fetch business name
                    service_name=booking.get("service_name", "Service"),
                    booking_date=booking["booking_date"],
                    booking_time=booking["booking_time"]
                )
        
        return render_template_string("""<!DOCTYPE html><html><head>
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Booking Cancelled</title>
<style>
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#0f172a;color:#e2e8f0;min-height:100vh;display:flex;align-items:center;justify-content:center;text-align:center;padding:20px}
.card{background:#1e293b;border-radius:12px;padding:32px;max-width:400px}
h1{font-size:24px;color:#f87171;margin-bottom:12px}
p{color:#94a3b8;line-height:1.5;margin-bottom:16px}
.btn{display:inline-block;padding:12px 24px;background:#1e3a5f;color:#93c5fd;border-radius:8px;text-decoration:none}
</style>
</head><body>
<div class="card">
  <h1>✓ Booking Cancelled</h1>
  <p>Your booking has been cancelled. We've sent you a confirmation SMS.</p>
  <p style="font-size:13px">Booking: {{ booking.service_name }} on {{ booking.booking_date }} at {{ booking.booking_time }}</p>
  <a href="tel:{{ booking.customer_phone }}" class="btn">📞 Call Business</a>
</div>
</body></html>""", booking=booking)
    
    elif action == "reschedule":
        # Show reschedule form
        return render_template_string("""<!DOCTYPE html><html><head>
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Reschedule Booking</title>
<style>
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#0f172a;color:#e2e8f0;min-height:100vh;padding:20px}
.card{background:#1e293b;border-radius:12px;padding:24px;max-width:400px;margin:0 auto}
h1{font-size:20px;margin-bottom:16px}
.form-group{margin-bottom:16px}
.form-group label{display:block;font-size:12px;color:#64748b;margin-bottom:6px}
.form-group input{width:100%;background:#0f172a;border:1px solid #334155;color:#e2e8f0;padding:12px;border-radius:8px;font-size:14px}
.btn{width:100%;padding:12px;background:#166534;color:#86efac;border:none;border-radius:8px;font-size:14px;font-weight:600;cursor:pointer;margin-top:8px}
.btn-secondary{background:#334155;color:#e2e8f0}
.info{background:#0f172a;padding:12px;border-radius:6px;margin-bottom:16px;font-size:13px}
</style>
</head><body>
<div class="card">
  <h1>📅 Reschedule Booking</h1>
  
  <div class="info">
    <strong>Current:</strong> {{ booking.booking_date }} at {{ booking.booking_time }}<br>
    <strong>Service:</strong> {{ booking.service_name }}
  </div>
  
  <form method="POST" action="/booking/manage/{{ booking.id }}/reschedule">
    <div class="form-group">
      <label>New Date</label>
      <input type="date" name="new_date" required min="{{ today }}">
    </div>
    <div class="form-group">
      <label>New Time</label>
      <input type="time" name="new_time" required>
    </div>
    <button type="submit" class="btn">Confirm Reschedule</button>
    <a href="/booking/manage/{{ booking.id }}" class="btn btn-secondary" style="display:block;text-align:center;text-decoration:none;margin-top:8px">Cancel</a>
  </form>
</div>
</body></html>""", booking=booking, today=datetime.now().strftime("%Y-%m-%d"))
    
    # Default: show booking details
    return render_template_string("""<!DOCTYPE html><html><head>
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Manage Your Booking</title>
<style>
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#0f172a;color:#e2e8f0;min-height:100vh;padding:20px}
.card{background:#1e293b;border-radius:12px;padding:24px;max-width:400px;margin:0 auto}
h1{font-size:20px;margin-bottom:16px}
.row{display:flex;justify-content:space-between;padding:12px 0;border-bottom:1px solid #334155}
.row:last-child{border-bottom:none}
.label{color:#64748b}
.value{font-weight:500}
.badge{padding:4px 8px;border-radius:4px;font-size:11px}
.badge-pending{background:#78350f;color:#fcd34d}
.badge-confirmed{background:#166534;color:#86efac}
.badge-completed{background:#1e3a5f;color:#93c5fd}
.badge-cancelled{background:#7f1d1d;color:#fca5a5}
.btn{display:block;padding:12px;border-radius:8px;text-decoration:none;text-align:center;margin-bottom:8px}
.btn-green{background:#166534;color:#86efac}
.btn-red{background:#7f1d1d;color:#fca5a5}
.btn-blue{background:#1e3a5f;color:#93c5fd}
</style>
</head><body>
<div class="card">
  <h1>📅 Your Booking</h1>
  
  <div class="row"><span class="label">Service</span><span class="value">{{ booking.service_name }}</span></div>
  <div class="row"><span class="label">Date</span><span class="value">{{ booking.booking_date }}</span></div>
  <div class="row"><span class="label">Time</span><span class="value">{{ booking.booking_time }}</span></div>
  <div class="row"><span class="label">Status</span><span class="badge badge-{{ booking.status }}">{{ booking.status }}</span></div>
  
  <div style="margin-top:20px">
    {% if booking.status == 'pending' or booking.status == 'confirmed' %}
    <a href="/booking/manage/{{ booking.id }}?action=reschedule" class="btn btn-blue">📅 Reschedule</a>
    <a href="/booking/manage/{{ booking.id }}?action=cancel" class="btn btn-red" onclick="return confirm('Cancel this booking?')">✕ Cancel Booking</a>
    {% else %}
    <p style="color:#64748b;font-size:13px;text-align:center">This booking cannot be modified</p>
    {% endif %}
  </div>
  
  <div style="margin-top:16px;padding-top:16px;border-top:1px solid #334155;text-align:center;font-size:12px;color:#64748b">
    Questions? Call: {{ booking.customer_phone }}
  </div>
</div>
</body></html>""", booking=booking)


@self_service_bp.route("/manage/<booking_id>/reschedule", methods=["POST"])
def reschedule_booking_self_service(booking_id: str):
    """Handle customer reschedule request."""
    from datetime import datetime
    
    booking = get_booking(booking_id)
    if not booking:
        return "Booking not found", 404
    
    new_date = request.form.get("new_date")
    new_time = request.form.get("new_time")
    
    if not new_date or not new_time:
        return redirect(f"/booking/manage/{booking_id}?action=reschedule")
    
    # Update booking
    conn = get_connection()
    c = conn.cursor()
    c.execute("""
        UPDATE bookings SET booking_date = ?, booking_time = ?
        WHERE id = ?
    """, (new_date, new_time, booking_id))
    conn.commit()
    conn.close()
    
    # Send confirmation SMS
    if booking.get("customer_phone"):
        send_reschedule_confirmation(
            customer_phone=booking["customer_phone"],
            customer_name=booking["customer_name"],
            business_name="Business",
            service_name=booking.get("service_name", "Service"),
            old_date=booking["booking_date"],
            old_time=booking["booking_time"],
            new_date=new_date,
            new_time=new_time
        )
    
    return render_template_string("""<!DOCTYPE html><html><head>
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Booking Rescheduled</title>
<style>
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#0f172a;color:#e2e8f0;min-height:100vh;display:flex;align-items:center;justify-content:center;text-align:center;padding:20px}
.card{background:#1e293b;border-radius:12px;padding:32px;max-width:400px}
h1{font-size:24px;color:#86efac;margin-bottom:12px}
p{color:#94a3b8;line-height:1.5;margin-bottom:16px}
</style>
</head><body>
<div class="card">
  <h1>✓ Booking Rescheduled</h1>
  <p>Your booking has been rescheduled. Confirmation SMS sent.</p>
  <p style="font-size:13px"><strong>New Time:</strong> {{ new_date }} at {{ new_time }}</p>
</div>
</body></html>""", new_date=new_date, new_time=new_time)
