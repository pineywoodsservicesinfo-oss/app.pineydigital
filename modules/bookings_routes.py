"""
bookings_routes.py — Booking Routes for Dashboard
Piney Digital Outreach System — Booking Management
"""

from flask import Blueprint, render_template_string, request, redirect, url_for, jsonify, session
from modules.bookings_db import (
    init_booking_tables, get_business_services, create_service, update_service, delete_service,
    get_business_staff, create_staff, set_staff_availability, get_staff_availability,
    create_booking, confirm_booking, cancel_booking, complete_booking, get_booking,
    get_business_bookings, get_available_slots, get_connection
)
from modules.bookings_notifications import (
    send_booking_confirmation, send_booking_request_notification, send_booking_reminder
)
from modules.loyalty_db import get_loyalty_business, get_connection

bookings_bp = Blueprint('bookings', __name__, url_prefix='/bookings')


@bookings_bp.route("/business/<biz_id>/manage")
def booking_manage(biz_id: str):
    """Business booking management dashboard."""
    if not session.get("logged_in"):
        return redirect(url_for("login"))
    
    biz = get_loyalty_business(biz_id)
    if not biz:
        return "Business not found", 404
    
    services = get_business_services(biz_id)
    staff = get_business_staff(biz_id)
    bookings = get_business_bookings(biz_id)
    
    # Get upcoming bookings
    from datetime import datetime, timedelta
    today = datetime.now().strftime("%Y-%m-%d")
    week_from_now = (datetime.now() + timedelta(days=7)).strftime("%Y-%m-%d")
    upcoming = get_business_bookings(biz_id, start_date=today, end_date=week_from_now)
    pending_count = len([b for b in bookings if b.status == 'pending'])
    
    return render_template_string("""<!DOCTYPE html><html><head>
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Bookings - {{ biz_name }}</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#0f172a;color:#e2e8f0;min-height:100vh}
.layout{display:flex;min-height:100vh}
.sidebar{width:210px;background:#1e293b;padding:0;flex-shrink:0;display:flex;flex-direction:column}
.logo{padding:20px;border-bottom:1px solid #334155}
.logo h1{font-size:15px;font-weight:600;color:#fff}
.logo p{font-size:11px;color:#64748b;margin-top:2px}
.nav{padding:12px 0;flex:1}
.nav a{display:flex;align-items:center;gap:10px;padding:9px 20px;font-size:13px;color:#94a3b8;border-left:2px solid transparent;text-decoration:none}
.nav a:hover{background:#0f172a80;color:#cbd5e1}
.nav a.active{background:#0f172a;color:#fff;border-left-color:#22c55e}
.dot{width:7px;height:7px;border-radius:50%;background:#334155;flex-shrink:0}
.nav a.active .dot{background:#22c55e}
.sidebar-footer{padding:16px 20px;border-top:1px solid #334155;font-size:11px;color:#475569}
.main{flex:1;padding:28px}
.topbar{display:flex;justify-content:space-between;align-items:center;margin-bottom:24px}
.topbar h2{font-size:18px;font-weight:500;color:#f1f5f9}
.btn{display:inline-flex;align-items:center;gap:6px;padding:8px 16px;border-radius:6px;font-size:13px;font-weight:500;cursor:pointer;border:none;transition:opacity .15s;text-decoration:none}
.btn:hover{opacity:.85}
.btn-green{background:#166534;color:#86efac}
.btn-blue{background:#1e3a5f;color:#93c5fd}
.btn-red{background:#7f1d1d;color:#fca5a5}
.panel{background:#1e293b;border-radius:8px;padding:18px;margin-bottom:16px}
.panel h3{font-size:11px;color:#64748b;text-transform:uppercase;letter-spacing:.05em;margin-bottom:14px;font-weight:500}
.grid2{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px}
.badge{display:inline-block;font-size:10px;padding:2px 8px;border-radius:99px;font-weight:500}
.badge-pending{background:#78350f;color:#fcd34d}
.badge-confirmed{background:#166534;color:#86efac}
.badge-completed{background:#1e3a5f;color:#93c5fd}
.badge-cancelled{background:#7f1d1d;color:#fca5a5}
.booking-item{background:#0f172a;border-radius:6px;padding:12px;margin-bottom:8px;border-left:3px solid #334155}
.booking-item.confirmed{border-left-color:#166534}
.booking-item.pending{border-left-color:#fbbf24}
.booking-header{display:flex;justify-content:space-between;align-items:center;margin-bottom:8px}
.booking-customer{font-weight:500;color:#f1f5f9}
.booking-time{font-size:12px;color:#94a3b8}
.booking-service{font-size:13px;color:#cbd5e1}
.booking-actions{display:flex;gap:8px;margin-top:8px}
.booking-actions button{padding:4px 8px;font-size:11px;border-radius:4px;border:none;cursor:pointer}
.btn-confirm{background:#166534;color:#86efac}
.btn-cancel{background:#7f1d1d;color:#fca5a5}
.btn-complete{background:#1e3a5f;color:#93c5fd}
.stat-card{background:#1e293b;border-radius:8px;padding:16px;text-align:center}
.stat-num{font-size:28px;font-weight:600;color:#f1f5f9}
.stat-label{font-size:12px;color:#64748b;margin-top:4px}
@media(max-width:768px){
  .layout{flex-direction:column}
  .sidebar{width:100%;flex-shrink:0;border-bottom:1px solid #334155}
  .nav{display:flex;flex-direction:row;overflow-x:auto;padding:8px 0}
  .nav a{white-space:nowrap;padding:8px 12px;font-size:12px;border-left:none;border-bottom:2px solid transparent}
  .nav a.active{border-left:none;border-bottom-color:#22c55e;background:transparent}
  .sidebar-footer{display:none}
  .main{padding:16px}
  .grid2{grid-template-columns:1fr}
}
</style>
</head><body>
<div class="layout">
  <div class="sidebar">
    <div class="logo"><h1>🌲 {{ biz_name }}</h1><p>Booking Manager</p></div>
    <nav class="nav">
      <a href="/loyalty/business/{{ biz_id }}"><div class="dot"></div>Loyalty Dashboard</a>
      <a href="/bookings/business/{{ biz_id }}/manage" class="active"><div class="dot"></div>Bookings</a>
      <a href="/bookings/business/{{ biz_id }}/services"><div class="dot"></div>Services</a>
      <a href="/bookings/business/{{ biz_id }}/staff"><div class="dot"></div>Staff & Hours</a>
      <a href="/book/{{ biz_id }}" target="_blank"><div class="dot"></div>Booking Page ↗</a>
    </nav>
    <div class="sidebar-footer">Powered by LoyaltyLoop</div>
  </div>
  <div class="main">
    <div class="topbar">
      <h2>Booking Management</h2>
      <div style="display:flex;gap:12px">
        <a href="/book/{{ biz_id }}" class="btn btn-blue" target="_blank">📅 Booking Page</a>
        <a href="/bookings/business/{{ biz_id }}/calendar" class="btn btn-green">📆 Calendar View</a>
      </div>
    </div>
    
    <div class="grid2" style="margin-bottom:24px">
      <div class="stat-card">
        <div class="stat-num">{{ upcoming|length }}</div>
        <div class="stat-label">Upcoming This Week</div>
      </div>
      <div class="stat-card">
        <div class="stat-num">{{ pending_count }}</div>
        <div class="stat-label">Pending Confirmation</div>
      </div>
      <div class="stat-card">
        <div class="stat-num">{{ services|length }}</div>
        <div class="stat-label">Active Services</div>
      </div>
      <div class="stat-card">
        <div class="stat-num">{{ staff|length }}</div>
        <div class="stat-label">Staff Members</div>
      </div>
    </div>
    
    <div class="panel">
      <h3>Upcoming Bookings</h3>
      {% if upcoming %}
      <div style="display:flex;flex-direction:column;gap:8px">
      {% for b in upcoming %}
        <div class="booking-item {{ b.status }}">
          <div class="booking-header">
            <div class="booking-customer">{{ b.customer_name }}</div>
            <span class="badge badge-{{ b.status }}">{{ b.status }}</span>
          </div>
          <div class="booking-time">
            📅 {{ b.booking_date }} at {{ b.booking_time }} · {{ b.duration_min }}min
          </div>
          <div class="booking-service">
            ✂️ {{ b.service_name }} {% if b.staff_name %}with {{ b.staff_name }}{% endif %}
          </div>
          {% if b.status == 'pending' %}
          <div class="booking-actions">
            <button class="btn-confirm" onclick="location.href='/bookings/business/{{ biz_id }}/confirm/{{ b.id }}'">✓ Confirm</button>
            <button class="btn-cancel" onclick="location.href='/bookings/business/{{ biz_id }}/cancel/{{ b.id }}'">✕ Cancel</button>
          </div>
          {% elif b.status == 'confirmed' %}
          <div class="booking-actions">
            <button class="btn-complete" onclick="location.href='/bookings/business/{{ biz_id }}/complete/{{ b.id }}'">✓ Mark Complete</button>
          </div>
          {% endif %}
        </div>
      {% endfor %}
      </div>
      {% else %}
      <p style="color:#64748b;text-align:center;padding:24px">No upcoming bookings</p>
      {% endif %}
    </div>
  </div>
</div>
</body></html>
""", biz_id=biz_id, biz_name=biz['name'], services=services, staff=staff, 
    bookings=bookings, upcoming=upcoming)


@bookings_bp.route("/business/<biz_id>/confirm/<booking_id>")
def confirm_booking_route(biz_id: str, booking_id: str):
    """Confirm a booking."""
    if not session.get("logged_in"):
        return redirect(url_for("login"))
    
    booking = get_booking(booking_id)
    if booking:
        confirm_booking(booking_id)
        # Send confirmation SMS
        if booking.get("customer_phone"):
            send_booking_confirmation(
                customer_phone=booking["customer_phone"],
                customer_name=booking["customer_name"],
                business_name=biz['name'],
                service_name=booking["service_name"],
                staff_name=booking.get("staff_name"),
                booking_date=booking["booking_date"],
                booking_time=booking["booking_time"]
            )
    
    return redirect(f"/bookings/business/{biz_id}/manage")


@bookings_bp.route("/business/<biz_id>/cancel/<booking_id>")
def cancel_booking_route(biz_id: str, booking_id: str):
    """Cancel a booking."""
    if not session.get("logged_in"):
        return redirect(url_for("login"))
    
    cancel_booking(booking_id)
    return redirect(f"/bookings/business/{biz_id}/manage")


@bookings_bp.route("/business/<biz_id>/complete/<booking_id>")
def complete_booking_route(biz_id: str, booking_id: str):
    """Mark booking as complete and add loyalty punch."""
    if not session.get("logged_in"):
        return redirect(url_for("login"))
    
    from modules.bookings_db import get_connection
    from modules.loyalty_db import get_or_create_customer_card, add_punch
    
    booking = get_booking(booking_id)
    if booking:
        complete_booking(booking_id)
        
        # Add loyalty punch if customer exists
        if booking.get("customer_id"):
            card = get_or_create_customer_card(booking["customer_id"], biz_id)
            if card:
                add_punch(card["id"], punched_by="booking", notes=f"Booking completed: {booking['service_name']}")
    
    return redirect(f"/bookings/business/{biz_id}/manage")


@bookings_bp.route("/business/<biz_id>/services")
def booking_services(biz_id: str):
    """Manage services."""
    if not session.get("logged_in"):
        return redirect(url_for("login"))
    
    biz = get_loyalty_business(biz_id)
    services = get_business_services(biz_id, active_only=False)
    
    return render_template_string("""<!DOCTYPE html><html><head>
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Services - {{ biz_name }}</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#0f172a;color:#e2e8f0;min-height:100vh}
.layout{display:flex;min-height:100vh}
.sidebar{width:210px;background:#1e293b;padding:0;flex-shrink:0;display:flex;flex-direction:column}
.logo{padding:20px;border-bottom:1px solid #334155}
.logo h1{font-size:15px;font-weight:600;color:#fff}
.nav a{display:flex;align-items:center;gap:10px;padding:9px 20px;font-size:13px;color:#94a3b8;border-left:2px solid transparent;text-decoration:none}
.nav a.active{background:#0f172a;color:#fff;border-left-color:#22c55e}
.dot{width:7px;height:7px;border-radius:50%;background:#334155}
.nav a.active .dot{background:#22c55e}
.main{flex:1;padding:28px}
.topbar{display:flex;justify-content:space-between;align-items:center;margin-bottom:24px}
.panel{background:#1e293b;border-radius:8px;padding:18px;margin-bottom:16px}
.form-group{margin-bottom:16px}
.form-group label{display:block;font-size:12px;color:#64748b;margin-bottom:6px}
.form-group input,.form-group textarea{width:100%;background:#0f172a;border:1px solid #334155;color:#e2e8f0;padding:10px;border-radius:6px;font-size:14px}
.btn{padding:8px 16px;border-radius:6px;font-size:13px;font-weight:500;cursor:pointer;border:none}
.btn-green{background:#166534;color:#86efac}
.service-item{background:#0f172a;padding:12px;border-radius:6px;margin-bottom:8px;display:flex;justify-content:space-between;align-items:center}
</style>
</head><body>
<div class="layout">
  <div class="sidebar">
    <div class="logo"><h1>🌲 {{ biz_name }}</h1><p>Services</p></div>
    <nav class="nav">
      <a href="/bookings/business/{{ biz_id }}/manage"><div class="dot"></div>Bookings</a>
      <a href="/bookings/business/{{ biz_id }}/services" class="active"><div class="dot"></div>Services</a>
      <a href="/bookings/business/{{ biz_id }}/staff"><div class="dot"></div>Staff</a>
    </nav>
  </div>
  <div class="main">
    <div class="topbar"><h2>Manage Services</h2></div>
    
    <div class="grid2">
      <div class="panel">
        <h3>Add Service</h3>
        <form method="POST" action="/bookings/business/{{ biz_id }}/services/add">
          <div class="form-group">
            <label>Service Name</label>
            <input type="text" name="name" required placeholder="e.g., Haircut">
          </div>
          <div class="form-group">
            <label>Duration (minutes)</label>
            <input type="number" name="duration" value="30" min="5" step="5">
          </div>
          <div class="form-group">
            <label>Price ($)</label>
            <input type="number" name="price" value="0" min="0" step="0.01">
          </div>
          <div class="form-group">
            <label>Description (optional)</label>
            <textarea name="description" rows="2"></textarea>
          </div>
          <button type="submit" class="btn btn-green">Add Service</button>
        </form>
      </div>
      
      <div class="panel">
        <h3>Current Services</h3>
        {% for s in services %}
        <div class="service-item">
          <div>
            <div style="font-weight:500;color:#f1f5f9">{{ s.name }}</div>
            <div style="font-size:12px;color:#64748b">{{ s.duration_min }}min · ${{ s.price or 0 }}</div>
          </div>
          <div style="font-size:11px;color:{{ '#86efac' if s.active else '#f87171' }}">
            {{ 'Active' if s.active else 'Inactive' }}
          </div>
        </div>
        {% else %}
        <p style="color:#64748b">No services yet</p>
        {% endfor %}
      </div>
    </div>
  </div>
</div>
</body></html>
""", biz_id=biz_id, biz_name=biz['name'], services=services)


@bookings_bp.route("/business/<biz_id>/services/add", methods=["POST"])
def add_service(biz_id: str):
    """Add a new service."""
    if not session.get("logged_in"):
        return redirect(url_for("login"))
    
    name = request.form.get("name")
    duration = int(request.form.get("duration", 30))
    price = float(request.form.get("price", 0))
    description = request.form.get("description")
    
    if name:
        create_service(biz_id, name, duration, price, description)
    
    return redirect(f"/bookings/business/{biz_id}/services")


@bookings_bp.route("/business/<biz_id>/calendar")
def booking_calendar(biz_id: str):
    """Visual calendar view with FullCalendar.js"""
    if not session.get("logged_in"):
        return redirect(url_for("login"))
    
    biz = get_loyalty_business(biz_id)
    if not biz:
        return "Business not found", 404
    
    services = get_business_services(biz_id)
    staff = get_business_staff(biz_id)
    
    return render_template_string("""<!DOCTYPE html><html><head>
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Calendar - {{ biz_name }}</title>
<script src='https://cdn.jsdelivr.net/npm/fullcalendar@6.1.10/index.global.min.js'></script>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#0f172a;color:#e2e8f0;min-height:100vh}
.layout{display:flex;min-height:100vh}
.sidebar{width:210px;background:#1e293b;padding:0;flex-shrink:0;display:flex;flex-direction:column}
.logo{padding:20px;border-bottom:1px solid #334155}
.logo h1{font-size:15px;font-weight:600;color:#fff}
.nav a{display:flex;align-items:center;gap:10px;padding:9px 20px;font-size:13px;color:#94a3b8;border-left:2px solid transparent;text-decoration:none}
.nav a.active{background:#0f172a;color:#fff;border-left-color:#22c55e}
.dot{width:7px;height:7px;border-radius:50%;background:#334155}
.nav a.active .dot{background:#22c55e}
.main{flex:1;padding:20px}
.topbar{display:flex;justify-content:space-between;align-items:center;margin-bottom:16px}
.panel{background:#1e293b;border-radius:8px;padding:16px;margin-bottom:16px}
#calendar{background:#1e293b;border-radius:8px;padding:16px;min-height:600px}
.fc{background:#1e293b;color:#e2e8f0}
.fc-theme-standard .fc-scrollgrid{border-color:#334155}
.fc-col-header-cell{background:#0f172a;color:#94a3b8;padding:8px}
.fc-daygrid-day{border-color:#334155}
.fc-daygrid-day.fc-day-today{background:#1e293b50}
.fc-event{border:none;border-radius:4px;font-size:12px}
.fc-event-pending{background:#fbbf24;color:#000}
.fc-event-confirmed{background:#166534}
.fc-event-completed{background:#1e3a5f}
.fc-event-cancelled{background:#7f1d1d;opacity:.6}
.fc-toolbar-title{font-size:16px!important}
.fc-button{background:#334155!important;border:none!important}
.fc-button-active{background:#166534!important}
@media(max-width:768px){
  .layout{flex-direction:column}
  .sidebar{width:100%;flex-shrink:0;border-bottom:1px solid #334155}
  .nav{display:flex;flex-direction:row;overflow-x:auto;padding:8px 0}
  .nav a{white-space:nowrap;padding:8px 12px;font-size:12px;border-left:none;border-bottom:2px solid transparent}
  .nav a.active{border-left:none;border-bottom-color:#22c55e;background:transparent}
  .main{padding:12px}
  #calendar{padding:8px;min-height:400px}
}
</style>
</head><body>
<div class="layout">
  <div class="sidebar">
    <div class="logo"><h1>🌲 {{ biz_name }}</h1><p>Calendar</p></div>
    <nav class="nav">
      <a href="/bookings/business/{{ biz_id }}/manage"><div class="dot"></div>List View</a>
      <a href="/bookings/business/{{ biz_id }}/calendar" class="active"><div class="dot"></div>Calendar</a>
      <a href="/bookings/business/{{ biz_id }}/services"><div class="dot"></div>Services</a>
      <a href="/bookings/business/{{ biz_id }}/staff"><div class="dot"></div>Staff</a>
    </nav>
  </div>
  <div class="main">
    <div class="topbar">
      <h2>Booking Calendar</h2>
      <div style="display:flex;gap:8px">
        <select id="staff-filter" onchange="loadCalendar()" style="background:#0f172a;border:1px solid #334155;color:#e2e8f0;padding:8px 12px;border-radius:6px">
          <option value="">All Staff</option>
          {% for s in staff %}<option value="{{ s.id }}">{{ s.name }}</option>{% endfor %}
        </select>
        <button onclick="location.href='/book/{{ biz_id }}'" class="btn btn-blue" style="padding:8px 16px;border:none;border-radius:6px;background:#1e3a5f;color:#93c5fd;cursor:pointer">📅 Booking Page</button>
      </div>
    </div>
    
    <div id="calendar"></div>
  </div>
</div>

<script>
let calendar;
let allBookings = {{ bookings_json | safe }};

function loadCalendar() {
  const staffFilter = document.getElementById('staff-filter').value;
  const filtered = staffFilter ? allBookings.filter(b => b.staff_id === staffFilter) : allBookings;
  
  const events = filtered.map(b => ({
    id: b.id,
    title: b.service_name + ' - ' + b.customer_name,
    start: b.booking_date + 'T' + b.booking_time,
    end: b.booking_date + 'T' + b.end_time,
    backgroundColor: getStatusColor(b.status),
    borderColor: getStatusColor(b.status),
    extendedProps: {
      status: b.status,
      customer: b.customer_name,
      phone: b.customer_phone,
      service: b.service_name,
      notes: b.notes
    }
  }));
  
  calendar.removeAllEvents();
  calendar.addEventSource(events);
}

function getStatusColor(status) {
  const colors = {
    'pending': '#fbbf24',
    'confirmed': '#166534',
    'completed': '#1e3a5f',
    'cancelled': '#7f1d1d'
  };
  return colors[status] || '#334155';
}

document.addEventListener('DOMContentLoaded', function() {
  const calendarEl = document.getElementById('calendar');
  
  calendar = new FullCalendar.Calendar(calendarEl, {
    initialView: 'dayGridMonth',
    headerToolbar: {
      left: 'prev,next today',
      center: 'title',
      right: 'dayGridMonth,timeGridWeek,timeGridDay'
    },
    height: 'auto',
    selectable: true,
    eventClick: function(info) {
      const event = info.event;
      const props = event.extendedProps;
      
      const actions = confirm(
        `${event.title}\\n` +
        `${event.start.toLocaleString()}\\n\\n` +
        `Customer: ${props.customer}\\n` +
        `Phone: ${props.phone || 'N/A'}\\n` +
        `Status: ${props.status}\\n\\n` +
        `Actions:\\nOK = View Details\\nCancel = Close`
      );
      
      if (actions) {
        window.location.href = `/bookings/business/{{ biz_id }}/booking/${event.id}`;
      }
    },
    eventDrop: function(info) {
      // Handle drag-to-reschedule
      const newDate = info.event.start.toISOString().split('T')[0];
      const newTime = info.event.start.toTimeString().split(' ')[0].substring(0, 5);
      
      if (confirm(`Reschedule booking to ${newDate} ${newTime}?`)) {
        fetch(`/bookings/business/{{ biz_id }}/reschedule/${info.event.id}`, {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({date: newDate, time: newTime})
        }).then(r => r.json()).then(data => {
          if (!data.success) {
            info.revert();
            alert('Failed to reschedule: ' + data.error);
          }
        });
      } else {
        info.revert();
      }
    }
  });
  
  calendar.render();
  loadCalendar();
});
</script>
</body></html>
""", biz_id=biz_id, biz_name=biz['name'], services=services, staff=staff,
    bookings_json=get_business_bookings_json(biz_id))


def get_business_bookings_json(biz_id: str) -> str:
    """Get bookings as JSON for calendar."""
    import json
    from datetime import datetime, timedelta
    today = datetime.now().strftime("%Y-%m-%d")
    three_months = (datetime.now() + timedelta(days=90)).strftime("%Y-%m-%d")
    
    bookings = get_business_bookings(biz_id, start_date=today, end_date=three_months)
    
    # Convert to serializable format
    serializable = []
    for b in bookings:
        serializable.append({
            'id': b['id'],
            'business_id': b['business_id'],
            'staff_id': b.get('staff_id'),
            'service_name': b.get('service_name', 'Service'),
            'customer_name': b['customer_name'],
            'customer_phone': b.get('customer_phone'),
            'booking_date': b['booking_date'],
            'booking_time': b['booking_time'],
            'end_time': b.get('end_time'),
            'status': b['status'],
            'notes': b.get('notes')
        })
    
    return json.dumps(serializable)


@bookings_bp.route("/business/<biz_id>/booking/<booking_id>")
def view_booking(biz_id: str, booking_id: str):
    """View single booking details."""
    if not session.get("logged_in"):
        return redirect(url_for("login"))
    
    booking = get_booking(booking_id)
    if not booking:
        return "Booking not found", 404
    
    return render_template_string("""<!DOCTYPE html><html><head>
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Booking Details</title>
<style>
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#0f172a;color:#e2e8f0;min-height:100vh;padding:20px}
.card{background:#1e293b;border-radius:8px;padding:24px;max-width:500px;margin:0 auto}
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
.btn{display:inline-block;padding:8px 16px;border-radius:6px;text-decoration:none;margin-right:8px}
.btn-green{background:#166534;color:#86efac}
.btn-red{background:#7f1d1d;color:#fca5a5}
.btn-blue{background:#1e3a5f;color:#93c5fd}
.actions{margin-top:20px;display:flex;gap:8px}
</style>
</head><body>
<div class="card">
  <h1>📅 Booking Details</h1>
  
  <div class="row"><span class="label">Customer</span><span class="value">{{ booking.customer_name }}</span></div>
  <div class="row"><span class="label">Phone</span><span class="value">{{ booking.customer_phone or 'N/A' }}</span></div>
  <div class="row"><span class="label">Email</span><span class="value">{{ booking.customer_email or 'N/A' }}</span></div>
  <div class="row"><span class="label">Service</span><span class="value">{{ booking.service_name }}</span></div>
  {% if booking.staff_name %}<div class="row"><span class="label">Staff</span><span class="value">{{ booking.staff_name }}</span></div>{% endif %}
  <div class="row"><span class="label">Date</span><span class="value">{{ booking.booking_date }}</span></div>
  <div class="row"><span class="label">Time</span><span class="value">{{ booking.booking_time }} - {{ booking.end_time }} ({{ booking.duration_min }}min)</span></div>
  <div class="row"><span class="label">Status</span><span class="badge badge-{{ booking.status }}">{{ booking.status }}</span></div>
  {% if booking.notes %}<div class="row"><span class="label">Notes</span><span class="value">{{ booking.notes }}</span></div>{% endif %}
  
  <div class="actions">
    {% if booking.status == 'pending' %}
    <a href="/bookings/business/{{ biz_id }}/confirm/{{ booking.id }}" class="btn btn-green">✓ Confirm</a>
    <a href="/bookings/business/{{ biz_id }}/cancel/{{ booking.id }}" class="btn btn-red">✕ Cancel</a>
    {% elif booking.status == 'confirmed' %}
    <a href="/bookings/business/{{ biz_id }}/complete/{{ booking.id }}" class="btn btn-blue">✓ Mark Complete</a>
    {% endif %}
    <a href="/bookings/business/{{ biz_id }}/calendar" class="btn btn-blue">← Back</a>
  </div>
</div>
</body></html>
""", biz_id=biz_id, booking=booking)


@bookings_bp.route("/business/<biz_id>/reschedule/<booking_id>", methods=["POST"])
def reschedule_booking(biz_id: str, booking_id: str):
    """Reschedule a booking via calendar drag-drop."""
    if not session.get("logged_in"):
        return jsonify({"success": False, "error": "Unauthorized"}), 401
    
    data = request.get_json()
    new_date = data.get("date")
    new_time = data.get("time")
    
    if not new_date or not new_time:
        return jsonify({"success": False, "error": "Missing date/time"}), 400
    
    conn = get_connection()
    c = conn.cursor()
    c.execute("""
        UPDATE bookings SET booking_date = ?, booking_time = ?
        WHERE id = ?
    """, (new_date, new_time, booking_id))
    conn.commit()
    conn.close()
    
    return jsonify({"success": True})


@bookings_bp.route("/business/<biz_id>/staff")
def booking_staff(biz_id: str):
    """Manage staff and view staff calendars."""
    if not session.get("logged_in"):
        return redirect(url_for("login"))
    
    biz = get_loyalty_business(biz_id)
    staff = get_business_staff(biz_id)
    
    return render_template_string("""<!DOCTYPE html><html><head>
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Staff - {{ biz_name }}</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#0f172a;color:#e2e8f0;min-height:100vh}
.layout{display:flex;min-height:100vh}
.sidebar{width:210px;background:#1e293b;padding:0;flex-shrink:0;display:flex;flex-direction:column}
.logo{padding:20px;border-bottom:1px solid #334155}
.logo h1{font-size:15px;font-weight:600;color:#fff}
.nav a{display:flex;align-items:center;gap:10px;padding:9px 20px;font-size:13px;color:#94a3b8;border-left:2px solid transparent;text-decoration:none}
.nav a.active{background:#0f172a;color:#fff;border-left-color:#22c55e}
.dot{width:7px;height:7px;border-radius:50%;background:#334155}
.nav a.active .dot{background:#22c55e}
.main{flex:1;padding:28px}
.topbar{display:flex;justify-content:space-between;align-items:center;margin-bottom:24px}
.panel{background:#1e293b;border-radius:8px;padding:18px;margin-bottom:16px}
.form-group{margin-bottom:16px}
.form-group label{display:block;font-size:12px;color:#64748b;margin-bottom:6px}
.form-group input{width:100%;background:#0f172a;border:1px solid #334155;color:#e2e8f0;padding:10px;border-radius:6px;font-size:14px}
.btn{padding:8px 16px;border-radius:6px;font-size:13px;font-weight:500;cursor:pointer;border:none}
.btn-green{background:#166534;color:#86efac}
.btn-blue{background:#1e3a5f;color:#93c5fd}
.staff-item{background:#0f172a;padding:12px;border-radius:6px;margin-bottom:8px;display:flex;justify-content:space-between;align-items:center}
</style>
</head><body>
<div class="layout">
  <div class="sidebar">
    <div class="logo"><h1>🌲 {{ biz_name }}</h1><p>Staff</p></div>
    <nav class="nav">
      <a href="/bookings/business/{{ biz_id }}/manage"><div class="dot"></div>Bookings</a>
      <a href="/bookings/business/{{ biz_id }}/calendar"><div class="dot"></div>Calendar</a>
      <a href="/bookings/business/{{ biz_id }}/services"><div class="dot"></div>Services</a>
      <a href="/bookings/business/{{ biz_id }}/staff" class="active"><div class="dot"></div>Staff</a>
    </nav>
  </div>
  <div class="main">
    <div class="topbar"><h2>Staff Management</h2></div>
    
    <div class="grid2">
      <div class="panel">
        <h3>Add Staff Member</h3>
        <form method="POST" action="/bookings/business/{{ biz_id }}/staff/add">
          <div class="form-group">
            <label>Name</label>
            <input type="text" name="name" required placeholder="e.g., Jane Doe">
          </div>
          <div class="form-group">
            <label>Role</label>
            <input type="text" name="role" placeholder="e.g., Senior Stylist">
          </div>
          <div class="form-group">
            <label>Phone</label>
            <input type="tel" name="phone" placeholder="+1234567890">
          </div>
          <div class="form-group">
            <label>Email</label>
            <input type="email" name="email" placeholder="jane@example.com">
          </div>
          <button type="submit" class="btn btn-green">Add Staff</button>
        </form>
      </div>
      
      <div class="panel">
        <h3>Team Members</h3>
        {% for s in staff %}
        <div class="staff-item">
          <div>
            <div style="font-weight:500;color:#f1f5f9">{{ s.name }}</div>
            <div style="font-size:12px;color:#64748b">{{ s.role or 'Staff' }}{% if s.phone %} · {{ s.phone }}{% endif %}</div>
          </div>
          <a href="/bookings/business/{{ biz_id }}/staff/{{ s.id }}/calendar" class="btn btn-blue" style="padding:4px 8px;font-size:11px">View Schedule</a>
        </div>
        {% else %}
        <p style="color:#64748b">No staff members yet</p>
        {% endfor %}
      </div>
    </div>
  </div>
</div>
</body></html>
""", biz_id=biz_id, biz_name=biz['name'], staff=staff)


@bookings_bp.route("/business/<biz_id>/staff/add", methods=["POST"])
def add_staff_member(biz_id: str):
    """Add a new staff member."""
    if not session.get("logged_in"):
        return redirect(url_for("login"))
    
    name = request.form.get("name")
    role = request.form.get("role")
    phone = request.form.get("phone")
    email = request.form.get("email")
    
    if name:
        create_staff(biz_id, name, role, email, phone)
    
    return redirect(f"/bookings/business/{biz_id}/staff")
