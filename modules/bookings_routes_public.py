"""
bookings_routes_public.py — Public Booking Routes for Customers
Piney Digital Outreach System — Booking Management
"""

from flask import Blueprint, render_template_string, request, redirect, url_for, jsonify
from modules.bookings_db import (
    get_business_services, get_business_staff, create_booking,
    get_available_slots, get_booking
)
from modules.loyalty_db import get_loyalty_business, create_customer, get_or_create_customer_card

public_bookings_bp = Blueprint('public_bookings', __name__, url_prefix='/book')


@public_bookings_bp.route("/<biz_id>")
def customer_booking_page(biz_id: str):
    """Public booking page for customers."""
    biz = get_loyalty_business(biz_id)
    if not biz:
        return "Business not found", 404
    
    services = get_business_services(biz_id)
    staff = get_business_staff(biz_id)
    
    return render_template_string("""<!DOCTYPE html><html><head>
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Book Online - {{ biz_name }}</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:linear-gradient(135deg,#0f172a 0%,#1e293b 100%);min-height:100vh;color:#e2e8f0}
.container{max-width:600px;margin:0 auto;padding:20px}
.header{text-align:center;padding:40px 0 30px}
.header h1{font-size:28px;font-weight:700;color:#fff;margin-bottom:8px}
.header p{font-size:14px;color:#94a3b8}
.card{background:#1e293b;border:1px solid #334155;border-radius:12px;padding:20px;margin-bottom:16px}
.card h3{font-size:14px;font-weight:600;color:#f1f5f9;margin-bottom:12px}
.form-group{margin-bottom:16px}
.form-group label{display:block;font-size:12px;color:#64748b;margin-bottom:6px}
.form-group select,.form-group input{width:100%;background:#0f172a;border:1px solid #334155;color:#e2e8f0;padding:12px;border-radius:8px;font-size:14px}
.form-group input:focus,.form-group select:focus{outline:none;border-color:#60a5fa}
.service-option{background:#0f172a;border:1px solid #334155;border-radius:8px;padding:12px;margin-bottom:8px;cursor:pointer;transition:all .15s}
.service-option:hover{border-color:#60a5fa}
.service-option.selected{border-color:#166534;background:#16653410}
.service-name{font-weight:500;color:#f1f5f9}
.service-meta{font-size:12px;color:#64748b;margin-top:4px}
.date-grid{display:grid;grid-template-columns:repeat(7,1fr);gap:4px;margin-bottom:16px}
.date-cell{padding:8px 4px;text-align:center;border-radius:6px;font-size:12px;cursor:pointer;border:1px solid transparent}
.date-cell:hover{background:#334155}
.date-cell.selected{background:#166534;border-color:#166534}
.date-cell.disabled{color:#475569;cursor:not-allowed}
.date-day{font-weight:600;margin-bottom:2px}
.date-num{font-size:11px;color:#94a3b8}
.time-grid{display:grid;grid-template-columns:repeat(4,1fr);gap:8px}
.time-slot{padding:10px;text-align:center;background:#0f172a;border:1px solid #334155;border-radius:6px;font-size:13px;cursor:pointer}
.time-slot:hover{border-color:#60a5fa}
.time-slot.selected{background:#166534;border-color:#166534}
.time-slot.disabled{opacity:.3;cursor:not-allowed}
.btn{width:100%;padding:14px;background:#166534;color:#86efac;border:none;border-radius:8px;font-size:15px;font-weight:600;cursor:pointer;margin-top:20px}
.btn:hover{opacity:.9}
.btn:disabled{opacity:.5;cursor:not-allowed}
.step{display:flex;align-items:center;gap:8px;margin-bottom:20px}
.step-num{width:24px;height:24px;border-radius:50%;background:#334155;color:#94a3b8;display:flex;align-items:center;justify-content:center;font-size:12px;font-weight:600}
.step.active .step-num{background:#166534;color:#86efac}
.hidden{display:none}
.success-box{background:#166534;border-radius:8px;padding:20px;text-align:center}
.success-icon{font-size:48px;margin-bottom:12px}
@media(max-width:480px){
  .time-grid{grid-template-columns:repeat(3,1fr)}
  .date-grid{grid-template-columns:repeat(7,1fr)}
}
</style>
</head><body>
<div class="container">
  <div class="header">
    <h1>{{ biz_name }}</h1>
    <p>{{ biz_type or 'Local Business' }} · {{ biz_city or 'Local' }}</p>
  </div>
  
  <form method="POST" action="/book/{{ biz_id }}/submit" id="booking-form">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Book Online - {{ biz_name }}</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:linear-gradient(135deg,#0f172a 0%,#1e293b 100%);min-height:100vh;color:#e2e8f0}
.container{max-width:600px;margin:0 auto;padding:20px}
.header{text-align:center;padding:40px 0 30px}
.header h1{font-size:28px;font-weight:700;color:#fff;margin-bottom:8px}
.header p{font-size:14px;color:#94a3b8}
.card{background:#1e293b;border:1px solid #334155;border-radius:12px;padding:20px;margin-bottom:16px}
.card h3{font-size:14px;font-weight:600;color:#f1f5f9;margin-bottom:12px}
.form-group{margin-bottom:16px}
.form-group label{display:block;font-size:12px;color:#64748b;margin-bottom:6px}
.form-group select,.form-group input{width:100%;background:#0f172a;border:1px solid #334155;color:#e2e8f0;padding:12px;border-radius:8px;font-size:14px}
.form-group input:focus,.form-group select:focus{outline:none;border-color:#60a5fa}
.service-option{background:#0f172a;border:1px solid #334155;border-radius:8px;padding:12px;margin-bottom:8px;cursor:pointer;transition:all .15s}
.service-option:hover{border-color:#60a5fa}
.service-option.selected{border-color:#166534;background:#16653410}
.service-name{font-weight:500;color:#f1f5f9}
.service-meta{font-size:12px;color:#64748b;margin-top:4px}
.date-grid{display:grid;grid-template-columns:repeat(7,1fr);gap:4px;margin-bottom:16px}
.date-cell{padding:8px 4px;text-align:center;border-radius:6px;font-size:12px;cursor:pointer;border:1px solid transparent}
.date-cell:hover{background:#334155}
.date-cell.selected{background:#166534;border-color:#166534}
.date-cell.disabled{color:#475569;cursor:not-allowed}
.date-day{font-weight:600;margin-bottom:2px}
.date-num{font-size:11px;color:#94a3b8}
.time-grid{display:grid;grid-template-columns:repeat(4,1fr);gap:8px}
.time-slot{padding:10px;text-align:center;background:#0f172a;border:1px solid #334155;border-radius:6px;font-size:13px;cursor:pointer}
.time-slot:hover{border-color:#60a5fa}
.time-slot.selected{background:#166534;border-color:#166534}
.time-slot.disabled{opacity:.3;cursor:not-allowed}
.btn{width:100%;padding:14px;background:#166534;color:#86efac;border:none;border-radius:8px;font-size:15px;font-weight:600;cursor:pointer;margin-top:20px}
.btn:hover{opacity:.9}
.btn:disabled{opacity:.5;cursor:not-allowed}
.step{display:flex;align-items:center;gap:8px;margin-bottom:20px}
.step-num{width:24px;height:24px;border-radius:50%;background:#334155;color:#94a3b8;display:flex;align-items:center;justify-content:center;font-size:12px;font-weight:600}
.step.active .step-num{background:#166534;color:#86efac}
.hidden{display:none}
.success-box{background:#166534;border-radius:8px;padding:20px;text-align:center}
.success-icon{font-size:48px;margin-bottom:12px}
@media(max-width:480px){
  .time-grid{grid-template-columns:repeat(3,1fr)}
  .date-grid{grid-template-columns:repeat(7,1fr)}
}
</style>
</head><body>
<div class="container">
  <div class="header">
    <h1>{{ biz_name }}</h1>
    <p>{{ biz_type or 'Local Business' }} · {{ biz_city or 'Local' }}</p>
  </div>
  
  <form method="POST" action="/book/{{ biz_id }}/submit" id="booking-form">
    <!-- Step 1: Service -->
    <div class="card" id="step1">
      <div class="step active"><div class="step-num">1</div><div>Choose Service</div></div>
      <div class="form-group">
      {% for s in services %}
        <div class="service-option" onclick="selectService('{{ s.id }}', '{{ s.name }}', {{ s.duration_min }})">
          <div class="service-name">{{ s.name }}</div>
          <div class="service-meta">{{ s.duration_min }} min{% if s.price %} · ${{ s.price }}{% endif %}</div>
        </div>
      {% else %}
        <p style="color:#64748b;text-align:center;padding:20px">No services available</p>
      {% endfor %}
      </div>
      <input type="hidden" name="service_id" id="service-id" required>
    </div>
    
    <!-- Step 2: Staff (optional) -->
    <div class="card hidden" id="step2">
      <div class="step"><div class="step-num">2</div><div>Choose Staff (Optional)</div></div>
      <div class="form-group">
        <select name="staff_id" id="staff-select" onchange="loadAvailableTimes()">
          <option value="">Any staff member</option>
          {% for s in staff %}
          <option value="{{ s.id }}">{{ s.name }}{% if s.role %} - {{ s.role }}{% endif %}</option>
          {% endfor %}
        </select>
      </div>
    </div>
    
    <!-- Step 3: Date & Time -->
    <div class="card hidden" id="step3">
      <div class="step"><div class="step-num">3</div><div>Select Date & Time</div></div>
      <div class="form-group">
        <label>Select Date</label>
        <div class="date-grid" id="date-grid"></div>
        <input type="hidden" name="booking_date" id="booking-date" required>
      </div>
      <div class="form-group">
        <label>Available Times</label>
        <div class="time-grid" id="time-grid">
          <p style="color:#64748b;grid-column:1/-1;text-align:center;padding:20px">Select a date first</p>
        </div>
        <input type="hidden" name="booking_time" id="booking-time" required>
      </div>
    </div>
    
    <!-- Step 4: Your Info -->
    <div class="card hidden" id="step4">
      <div class="step"><div class="step-num">4</div><div>Your Information</div></div>
      <div class="form-group">
        <label>Full Name *</label>
        <input type="text" name="customer_name" required placeholder="Your name">
      </div>
      <div class="form-group">
        <label>Phone Number *</label>
        <input type="tel" name="customer_phone" required placeholder="For booking confirmation">
      </div>
      <div class="form-group">
        <label>Email (optional)</label>
        <input type="email" name="customer_email" placeholder="For reminders">
      </div>
      <div class="form-group">
        <label>Notes (optional)</label>
        <input type="text" name="notes" placeholder="Special requests">
      </div>
    </div>
    
    <button type="submit" class="btn" id="submit-btn" disabled>Book Appointment</button>
  </form>
  
  <div class="card hidden" id="success-message">
    <div class="success-box">
      <div class="success-icon">✓</div>
      <h2 style="color:#fff;margin-bottom:8px">Booking Requested!</h2>
      <p style="color:#86efac;font-size:14px;line-height:1.5">
        We've received your booking request for <strong id="conf-service"></strong> on 
        <strong id="conf-date"></strong> at <strong id="conf-time"></strong>.
      </p>
      <p style="color:#94a3b8;font-size:13px;margin-top:16px">
        You'll receive a confirmation SMS shortly. The business will confirm your appointment.
      </p>
    </div>
  </div>
</div>

<script>
let selectedServiceId = '';
let selectedServiceName = '';
let selectedDuration = 30;
let selectedDate = '';
let selectedTime = '';

function selectService(id, name, duration) {
  document.querySelectorAll('.service-option').forEach(el => el.classList.remove('selected'));
  event.target.closest('.service-option').classList.add('selected');
  
  document.getElementById('service-id').value = id;
  selectedServiceId = id;
  selectedServiceName = name;
  selectedDuration = duration;
  
  document.getElementById('step2').classList.remove('hidden');
  document.getElementById('step3').classList.remove('hidden');
  document.getElementById('step4').classList.remove('hidden');
  document.getElementById('submit-btn').disabled = false;
  
  generateDates();
}

function generateDates() {
  const grid = document.getElementById('date-grid');
  grid.innerHTML = '';
  
  const today = new Date();
  const days = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
  
  for (let i = 0; i < 14; i++) {
    const date = new Date(today);
    date.setDate(today.getDate() + i);
    
    const dateStr = date.toISOString().split('T')[0];
    const dayName = days[date.getDay()];
    const dayNum = date.getDate();
    
    const cell = document.createElement('div');
    cell.className = 'date-cell' + (i === 0 ? ' selected' : '');
    cell.innerHTML = `<div class="date-day">${dayName}</div><div class="date-num">${dayNum}</div>`;
    cell.onclick = () => selectDate(dateStr, cell);
    
    if (i === 0) selectDate(dateStr, cell);
    grid.appendChild(cell);
  }
}

function selectDate(dateStr, cell) {
  document.querySelectorAll('.date-cell').forEach(el => el.classList.remove('selected'));
  cell.classList.add('selected');
  selectedDate = dateStr;
  document.getElementById('booking-date').value = dateStr;
  loadAvailableTimes();
}

async function loadAvailableTimes() {
  const staffId = document.getElementById('staff-select').value;
  const timeGrid = document.getElementById('time-grid');
  
  if (!selectedDate || !selectedServiceId) {
    timeGrid.innerHTML = '<p style="color:#64748b;grid-column:1/-1;text-align:center">Select date first</p>';
    return;
  }
  
  timeGrid.innerHTML = '<p style="color:#64748b;grid-column:1/-1;text-align:center;padding:20px">Loading...</p>';
  
  try {
    const response = await fetch(`/api/bookings/{{ biz_id }}/slots?date=${selectedDate}&service=${selectedServiceId}&staff=${staffId}`);
    const data = await response.json();
    
    if (data.slots && data.slots.length > 0) {
      timeGrid.innerHTML = '';
      data.slots.forEach(time => {
        const slot = document.createElement('div');
        slot.className = 'time-slot';
        slot.textContent = time;
        slot.onclick = () => selectTime(time, slot);
        timeGrid.appendChild(slot);
      });
    } else {
      timeGrid.innerHTML = '<p style="color:#64748b;grid-column:1/-1;text-align:center;padding:20px">No available times</p>';
    }
  } catch (error) {
    timeGrid.innerHTML = '<p style="color:#f87171;grid-column:1/-1;text-align:center;padding:20px">Error loading times</p>';
  }
}

function selectTime(time, slot) {
  document.querySelectorAll('.time-slot').forEach(el => el.classList.remove('selected'));
  slot.classList.add('selected');
  selectedTime = time;
  document.getElementById('booking-time').value = time;
}

// Handle form submission
document.getElementById('booking-form').onsubmit = async function(e) {
  e.preventDefault();
  
  const formData = new FormData(this);
  const data = Object.fromEntries(formData);
  data.business_id = '{{ biz_id }}';
  
  try {
    const response = await fetch('/book/{{ biz_id }}/submit', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(data)
    });
    
    const result = await response.json();
    
    if (result.success) {
      document.getElementById('booking-form').classList.add('hidden');
      document.getElementById('success-message').classList.remove('hidden');
      document.getElementById('conf-service').textContent = selectedServiceName;
      document.getElementById('conf-date').textContent = selectedDate;
      document.getElementById('conf-time').textContent = selectedTime;
    } else {
      alert('Booking failed: ' + (result.error || 'Unknown error'));
    }
  } catch (error) {
    alert('Error submitting booking. Please try again.');
  }
};
</script>
</body></html>
""", biz_id=biz_id, biz_name=biz['name'], biz_type=biz.get('type'), biz_city=biz.get('city'),
    services=services, staff=staff)


@public_bookings_bp.route("/<biz_id>/submit", methods=["POST"])
def submit_booking(biz_id: str):
    """Handle booking submission."""
    data = request.get_json() if request.is_json else request.form
    
    service_id = data.get("service_id")
    staff_id = data.get("staff_id") or None
    booking_date = data.get("booking_date")
    booking_time = data.get("booking_time")
    customer_name = data.get("customer_name")
    customer_phone = data.get("customer_phone")
    customer_email = data.get("customer_email")
    notes = data.get("notes")
    
    if not all([service_id, booking_date, booking_time, customer_name, customer_phone]):
        return jsonify({"success": False, "error": "Missing required fields"}), 400
    
    # Create or get customer
    from modules.loyalty_db import get_connection
    conn = get_connection()
    c = conn.cursor()
    c.execute("SELECT id FROM loyalty_customers WHERE phone = ?", (customer_phone,))
    row = c.fetchone()
    
    if row:
        customer_id = row["id"]
    else:
        customer_id = create_customer(name=customer_name, email=customer_email, phone=customer_phone)
    
    conn.close()
    
    # Create booking
    booking_id = create_booking(
        business_id=biz_id,
        customer_name=customer_name,
        booking_date=booking_date,
        booking_time=booking_time,
        service_id=service_id,
        staff_id=staff_id,
        customer_id=customer_id,
        customer_phone=customer_phone,
        customer_email=customer_email,
        notes=notes
    )
    
    if not booking_id:
        return jsonify({"success": False, "error": "Failed to create booking"}), 500
    
    # Send SMS notifications
    from modules.bookings_notifications import send_booking_request_notification
    from modules.loyalty_db import get_loyalty_business
    
    biz = get_loyalty_business(biz_id)
    if biz and biz.get("phone"):
        # Notify business
        send_booking_request_notification(
            business_phone=biz["phone"],
            customer_name=customer_name,
            service_name="Service",  # Could fetch service name
            booking_date=booking_date,
            booking_time=booking_time,
            staff_name=staff_id or "Any"
        )
    
    return jsonify({"success": True, "booking_id": booking_id})


@public_bookings_bp.route("/<biz_id>/slots")
def get_available_slots_api(biz_id: str):
    """API to get available time slots."""
    date = request.args.get("date")
    service_id = request.args.get("service")
    staff_id = request.args.get("staff") or None
    
    if not date or not service_id:
        return jsonify({"slots": []})
    
    # If no staff selected, get first available staff
    if not staff_id:
        staff_list = get_business_staff(biz_id)
        if staff_list:
            staff_id = staff_list[0]["id"]
    
    if not staff_id:
        return jsonify({"slots": []})
    
    slots = get_available_slots(biz_id, staff_id, service_id, date)
    return jsonify({"slots": slots})
