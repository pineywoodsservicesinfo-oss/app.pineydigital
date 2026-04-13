"""
dashboard.py — Piney Digital Admin Dashboard
Module 6 — Web UI for campaign management

Run with: python dashboard.py
Visit:    http://localhost:5000

Features:
  - Password-protected login
  - Live campaign stats from SQLite
  - Lead table with filters
  - Sending window status (Central Time)
  - Manual send trigger
  - Outreach log viewer
"""

import os
import sys
import json
import sqlite3
import logging
from datetime import datetime, timedelta
from pathlib import Path
from functools import wraps
from flask import (Flask, render_template_string, redirect,
                   url_for, request, session, jsonify)

logger = logging.getLogger(__name__)

# ── Call Scheduler State ─────────────────────────────────────
# Track when we last started calls to avoid duplicate starts
_last_call_start_date = None  # YYYY-MM-DD format
_call_scheduler_running = False

sys.path.insert(0, str(Path(__file__).parent))

# Import loyalty modules
from modules.loyalty_db import (
    init_loyalty_tables, get_loyalty_stats, get_all_loyalty_businesses,
    get_business_stats, get_all_customers, get_customer_cards,
    get_or_create_customer_card, create_customer, get_customer,
    get_loyalty_business, add_punch, get_connection
)
from modules.loyalty_auth import (
    init_auth_tables, authenticate_business, authenticate_customer,
    create_session, destroy_session, loyalty_login_required,
    business_login_required, customer_login_required
)
from modules.utils import load_env
from modules.loyalty_api import loyalty_api, generate_qr_code
from modules.loyalty_notifications import notify_on_reward_earned

# Import review modules
from modules.reviews_db import (
    init_review_tables, get_review_settings, create_review_request,
    should_send_review_request
)
from modules.reviews_notifications import send_review_request
from modules.reviews_routes import reviews_bp

# Import booking modules
from modules.bookings_db import init_booking_tables
from modules.bookings_routes import bookings_bp, get_business_bookings_json
from modules.bookings_routes_public import public_bookings_bp
from modules.bookings_self_service import self_service_bp

# Import referral modules
from modules.referrals_db import init_referral_tables
from modules.referrals_routes import referrals_bp

# Load environment variables
load_env()

app = Flask(__name__)

# Security: Require these to be set in environment
DASHBOARD_SECRET = os.environ.get("DASHBOARD_SECRET")
DASHBOARD_PASS = os.environ.get("DASHBOARD_PASSWORD")

if not DASHBOARD_SECRET:
    print("WARNING: DASHBOARD_SECRET not set. Generating temporary secret.")
    import secrets
    DASHBOARD_SECRET = secrets.token_hex(32)

if not DASHBOARD_PASS:
    print("WARNING: DASHBOARD_PASSWORD not set. Using insecure default. Set DASHBOARD_PASSWORD in .env!")
    DASHBOARD_PASS = "CHANGE_ME"

app.secret_key = DASHBOARD_SECRET

# Database path - use environment variable for production (Railway)
DB_PATH = Path(os.environ.get("DATABASE_PATH", Path(__file__).parent / "data" / "leads.db"))

# Ensure data directory exists
(DB_PATH.parent if isinstance(DB_PATH, Path) else Path(DB_PATH).parent).mkdir(parents=True, exist_ok=True)

# Initialize all tables
from modules.database import init_db, seed_leads_from_csv, update_lead
init_db()  # Core tables (admin_sessions, leads, etc.)
seed_leads_from_csv()  # Import leads from CSV if database is empty
init_loyalty_tables()
init_auth_tables()
init_review_tables()
init_booking_tables()
init_referral_tables()

# Register blueprints
app.register_blueprint(loyalty_api)
app.register_blueprint(reviews_bp)
app.register_blueprint(bookings_bp)
app.register_blueprint(public_bookings_bp)
app.register_blueprint(self_service_bp)
app.register_blueprint(referrals_bp)


# ── Auth ───────────────────────────────────────────────────
def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("logged_in"):
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated


# Redirect old admin overview to main dashboard
@app.route("/admin/overview")
@login_required
def admin_overview_redirect():
    return redirect(url_for("overview"))


# ── DB helpers ─────────────────────────────────────────────
def query_db(sql, params=(), one=False):
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        c.execute(sql, params)
        rv = c.fetchall()
        conn.close()
        return (rv[0] if rv else None) if one else rv
    except Exception as e:
        return None if one else []


def get_stats():
    total    = query_db("SELECT COUNT(*) as n FROM leads", one=True)
    by_status = query_db("SELECT outreach_status, COUNT(*) as n FROM leads GROUP BY outreach_status")
    by_site   = query_db("SELECT site_status, COUNT(*) as n FROM leads GROUP BY site_status")

    status_map = {r["outreach_status"]: r["n"] for r in (by_status or [])}
    site_map   = {r["site_status"]: r["n"] for r in (by_site or [])}

    sent    = status_map.get("sent", 0)
    queued  = status_map.get("queued", 0)
    replied = status_map.get("replied", 0)
    failed  = status_map.get("failed", 0)
    new     = status_map.get("new", 0)

    return {
        "total":   total["n"] if total else 0,
        "sent":    sent,
        "queued":  queued,
        "replied": replied,
        "failed":  failed,
        "new":     new,
        "none":    site_map.get("none", 0),
        "parked":  site_map.get("parked", 0),
        "outdated":site_map.get("outdated", 0),
        "modern":  site_map.get("modern", 0),
    }


def get_window_status():
    try:
        import pytz
        ct = pytz.timezone("America/Chicago")
        now = datetime.now(ct)
    except ImportError:
        from datetime import timezone, timedelta
        month = datetime.utcnow().month
        offset = timedelta(hours=-5 if 3 <= month <= 10 else -6)
        now = datetime.now(timezone(offset))

    hour    = now.hour
    weekday = now.weekday()
    ts      = now.strftime("%I:%M %p CT, %A")
    open_w  = (weekday < 5) and (8 <= hour < 18)
    return {"open": open_w, "time": ts}


# ── Templates ──────────────────────────────────────────────
# Mobile-ready viewport meta tag (use in all customer-facing pages)
MOBILE_HEAD = """<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1,user-scalable=no">"""

# Optimized shared styles for admin dashboard
BASE_CSS = """
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;
     background:#0f172a;color:#e2e8f0;min-height:100vh}
a{color:#60a5fa;text-decoration:none}
.layout{display:flex;min-height:100vh}

/* Sidebar */
.sidebar{width:220px;background:#1e293b;flex-shrink:0;
         display:flex;flex-direction:column;height:100vh;position:sticky;top:0}
.logo{padding:24px 20px;border-bottom:1px solid #334155}
.logo h1{font-size:18px;font-weight:700;color:#fff;display:flex;align-items:center;gap:8px}
.logo p{font-size:12px;color:#64748b;margin-top:4px}

/* Navigation */
.nav{padding:16px 0;flex:1;overflow-y:auto}
.nav a{display:flex;align-items:center;gap:12px;padding:12px 20px;
       font-size:14px;color:#94a3b8;transition:all .2s;border-left:3px solid transparent}
.nav a:hover{background:#0f172a;color:#cbd5e1}
.nav a.active{background:linear-gradient(90deg,#0f172a 0%,transparent 100%);
               color:#fff;border-left-color:#22c55e}
.nav-icon{width:20px;height:20px;display:flex;align-items:center;justify-content:center;
          font-size:16px;flex-shrink:0}
.nav-label{flex:1;font-weight:500}

/* Section divider */
.nav-divider{height:1px;background:#334155;margin:12px 20px}

/* Sidebar footer */
.sidebar-footer{padding:16px 20px;border-top:1px solid #334155;
                font-size:11px;color:#64748b}
.sidebar-footer a{color:#94a3b8;margin-left:4px}
.sidebar-footer a:hover{color:#fff}

/* Main content */
.main{flex:1;padding:28px;overflow-x:hidden;min-width:0}

/* Topbar */
.topbar{display:flex;justify-content:space-between;align-items:center;
        margin-bottom:24px;flex-wrap:wrap;gap:12px}
.topbar h2{font-size:22px;font-weight:600;color:#f1f5f9}
.topbar-right{display:flex;align-items:center;gap:12px;font-size:12px;color:#64748b}

/* Badges */
.badge{display:inline-block;font-size:10px;padding:2px 8px;
       border-radius:99px;font-weight:500}
.badge-green{background:#166534;color:#86efac}
.badge-amber{background:#78350f;color:#fcd34d}
.badge-blue{background:#1e3a5f;color:#93c5fd}
.badge-gray{background:#1e293b;color:#64748b;border:1px solid #334155}
.badge-red{background:#7f1d1d;color:#fca5a5}

/* Stats grid */
.stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));
       gap:14px;margin-bottom:24px}
.stat{background:#1e293b;border-radius:10px;padding:16px 18px;transition:transform .2s}
.stat:hover{transform:translateY(-2px)}
.stat label{display:block;font-size:12px;color:#64748b;margin-bottom:6px}
.stat .val{font-size:28px;font-weight:700}
.stat .sub{font-size:11px;color:#475569;margin-top:4px}
.green{color:#4ade80}.blue{color:#60a5fa}.amber{color:#fbbf24}.red{color:#f87171}

/* Panels */
.panel{background:#1e293b;border-radius:10px;padding:20px;margin-bottom:16px}
.panel h3{font-size:12px;color:#64748b;text-transform:uppercase;
          letter-spacing:.05em;margin-bottom:16px;font-weight:600}
.panel-header{display:flex;justify-content:space-between;align-items:center;
               margin-bottom:16px}
.panel-header h3{margin:0}
.panel-actions{display:flex;gap:8px}
.grid2{display:grid;grid-template-columns:repeat(auto-fit,minmax(320px,1fr));gap:16px}

/* Window status */
.window-box{display:flex;align-items:center;gap:12px;padding:12px 16px;
            border-radius:8px;margin-bottom:16px}
.window-open{background:#166534}.window-closed{background:#7f1d1d}
.window-dot{width:10px;height:10px;border-radius:50%;flex-shrink:0}
.window-open .window-dot{background:#4ade80;animation:pulse 2s infinite}
.window-closed .window-dot{background:#f87171}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.4}}
.window-text{font-size:13px;font-weight:500}
.window-open .window-text{color:#86efac}
.window-closed .window-text{color:#fca5a5}

/* Progress bars */
.bar-row{display:flex;align-items:center;gap:12px;margin-bottom:12px}
.bar-label{font-size:12px;color:#94a3b8;width:90px;flex-shrink:0}
.bar-track{flex:1;background:#0f172a;border-radius:4px;height:8px;overflow:hidden}
.bar-fill{height:8px;border-radius:4px;transition:width .5s}
.bar-count{font-size:12px;color:#64748b;width:35px;text-align:right;font-weight:500}

/* Tables */
.table-wrap{overflow-x:auto;border-radius:8px}
table{width:100%;border-collapse:collapse;font-size:13px;min-width:600px}
thead th{text-align:left;color:#64748b;font-weight:600;font-size:11px;
         padding:10px 12px;border-bottom:1px solid #334155;
         text-transform:uppercase;letter-spacing:.03em;background:#1e293b}
tbody tr{border-bottom:1px solid #1e293b;transition:background .15s}
tbody tr:hover{background:#263348}
tbody td{padding:10px 12px;color:#cbd5e1;vertical-align:middle}

/* Filters */
.filter-bar{display:flex;gap:10px;margin-bottom:16px;flex-wrap:wrap;align-items:center}
.filter-bar select,.filter-bar input{background:#1e293b;border:1px solid #334155;
  color:#e2e8f0;padding:8px 12px;border-radius:8px;font-size:13px}
.filter-bar select:focus,.filter-bar input:focus{outline:none;border-color:#22c55e}

/* Buttons */
.btn{display:inline-flex;align-items:center;gap:8px;padding:10px 18px;
     border-radius:8px;font-size:13px;font-weight:600;cursor:pointer;
     border:none;transition:all .2s}
.btn:hover{opacity:.9;transform:translateY(-1px)}
.btn:active{transform:translateY(0)}
.btn-green{background:#166534;color:#86efac}
.btn-blue{background:#1e40af;color:#93c5fd}
.btn-red{background:#7f1d1d;color:#fca5a5}
.btn-gray{background:#334155;color:#94a3b8}

/* Message box */
.msg-box{background:#0f172a;border-radius:8px;padding:12px 14px;
         font-size:12px;color:#94a3b8;font-family:monospace;
         max-height:80px;overflow:hidden;cursor:pointer;border:1px solid #334155}

/* Login */
.login-wrap{display:flex;align-items:center;justify-content:center;
            min-height:100vh;background:#0f172a}
.login-card{background:#1e293b;border-radius:16px;padding:40px;
            width:100%;max-width:360px;text-align:center;
            border:1px solid #334155}
.login-card h1{font-size:24px;font-weight:700;color:#fff;margin-bottom:6px}
.login-card p{font-size:14px;color:#64748b;margin-bottom:28px}
.login-card input{width:100%;background:#0f172a;border:1px solid #334155;
  color:#e2e8f0;padding:12px 16px;border-radius:10px;font-size:14px;
  margin-bottom:14px}
.login-card input:focus{outline:none;border-color:#22c55e}
.login-card button{width:100%;padding:12px;background:#166534;
  color:#86efac;border:none;border-radius:10px;font-size:14px;
  font-weight:600;cursor:pointer;transition:all .2s}
.login-card button:hover{background:#14532d}
.err{color:#f87171;font-size:12px;margin-top:10px}

/* Mobile responsive */
@media (max-width: 900px) {
  .sidebar{width:200px}
  .nav a{padding:10px 16px;font-size:13px}
}

@media (max-width: 768px) {
  .layout{flex-direction:column}
  .sidebar{width:100%;height:auto;position:relative}
  .logo{padding:16px;border-bottom:none}
  .logo h1{font-size:16px}
  .nav{display:flex;flex-wrap:wrap;padding:8px;gap:4px}
  .nav a{padding:8px 14px;font-size:12px;border-radius:6px;border-left:none}
  .nav a.active{background:#166534;border-left:none}
  .nav-icon{display:none}
  .nav-divider{display:none}
  .sidebar-footer{display:none}
  .main{padding:16px}
  .stats{grid-template-columns:repeat(2,1fr)}
  .grid2{grid-template-columns:1fr}
  .topbar h2{font-size:18px}
  .panel{padding:16px}
}

@media (max-width: 480px) {
  .nav{gap:2px}
  .nav a{padding:6px 10px;font-size:11px}
  .stats{grid-template-columns:1fr 1fr;gap:10px}
  .stat{padding:12px 14px}
  .stat .val{font-size:22px}
}
</style>
"""

def get_nav(page='overview'):
    """Generate navigation sidebar for admin dashboard."""
    def active(p): return 'active' if page == p else ''

    return f"""
<div class="sidebar">
  <div class="logo">
    <h1>🌲 Piney Digital</h1>
    <p>Admin Dashboard</p>
  </div>
  <nav class="nav">
    <a href="/" class="{active('overview')}">
      <span class="nav-icon">📊</span>
      <span class="nav-label">Dashboard</span>
    </a>
    <a href="/leads" class="{active('leads')}">
      <span class="nav-icon">📋</span>
      <span class="nav-label">Leads</span>
    </a>
    <a href="/loyalty" class="{active('loyalty')}">
      <span class="nav-icon">🎁</span>
      <span class="nav-label">Loyalty</span>
    </a>
    <a href="/call" class="{active('call')}">
      <span class="nav-icon">📞</span>
      <span class="nav-label">AI Calls</span>
    </a>
    <a href="/send" class="{active('send')}">
      <span class="nav-icon">📤</span>
      <span class="nav-label">Send SMS</span>
    </a>
    <a href="/log" class="{active('log')}">
      <span class="nav-icon">📜</span>
      <span class="nav-label">Log</span>
    </a>
  </nav>
  <div class="sidebar-footer">
    <a href="/logout">Sign out</a>
  </div>
</div>
"""


# ── Routes ─────────────────────────────────────────────────

@app.route("/login", methods=["GET","POST"])
def login():
    """Admin login with email + password."""
    error = ""
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        admin_email = os.environ.get("ADMIN_EMAIL", "joel@pineydigital.com").lower()

        # Validate email first
        if email != admin_email:
            error = "Invalid credentials."
        elif password != DASHBOARD_PASS:
            error = "Invalid credentials."
        else:
            # Credentials correct - redirect to 2FA
            from modules.auth_security import generate_2fa_code
            from modules.email_sender import send_2fa_code, is_email_configured

            code = generate_2fa_code("admin")
            session["pending_login"] = True

            # Send 2FA email
            email_sent = False
            if is_email_configured():
                success, msg = send_2fa_code(admin_email, code)
                email_sent = success
                if success:
                    logger.info(f"Admin 2FA code sent to {admin_email}")
                else:
                    logger.warning(f"Failed to send 2FA email: {msg}")

            # Store code in session
            session["2fa_code"] = code
            session["2fa_email_sent"] = email_sent

            return redirect(url_for("login_2fa"))

    return render_template_string(f"""<!DOCTYPE html><html><head>
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1,user-scalable=no">
<title>Piney Digital — Admin Login</title>{BASE_CSS}</head><body>
<div class="login-wrap">
  <div class="login-card">
    <h1>🌲 Piney Digital</h1>
    <p>Admin Dashboard</p>
    {f'<p style="color:#ef4444;margin-bottom:16px">{error}</p>' if error else ''}
    <form method="POST">
      <input type="email" name="email" placeholder="Admin email" required autofocus
             value="{request.form.get('email', '')}">
      <input type="password" name="password" placeholder="Password" required>
      <button type="submit">Sign in</button>
    </form>
  </div>
</div></body></html>""")


@app.route("/login/2fa", methods=["GET", "POST"])
def login_2fa():
    """Admin 2FA verification."""
    from modules.auth_security import verify_2fa_code, generate_2fa_code
    from modules.database import create_admin_session, log_audit_event
    from modules.email_sender import send_2fa_code, is_email_configured, ADMIN_EMAIL
    import secrets
    import os

    # Check if pending login
    if not session.get("pending_login"):
        return redirect(url_for("login"))

    error = ""
    code_from_session = session.get("2fa_code", "")
    email_sent = session.get("2fa_email_sent", False)

    if request.method == "POST":
        action = request.form.get("action", "verify")
        code = request.form.get("code", "").strip()

        if action == "resend":
            # Generate new code
            code_from_session = generate_2fa_code("admin")
            session["2fa_code"] = code_from_session

            # Send email
            if is_email_configured():
                success, msg = send_2fa_code(os.environ.get("ADMIN_EMAIL", ADMIN_EMAIL), code_from_session)
                email_sent = success
                session["2fa_email_sent"] = email_sent
                if success:
                    error = "New code sent to your email!"
                else:
                    error = f"Failed to send email: {msg}"
            else:
                error = "Email not configured. Please contact support."

        elif action == "verify":
            valid, msg = verify_2fa_code("admin", code)

            if valid:
                # Create session
                session_token = secrets.token_urlsafe(32)
                from datetime import datetime, timedelta
                expires = (datetime.now() + timedelta(hours=24)).isoformat()

                ip = request.headers.get("X-Forwarded-For", request.remote_addr)
                ua = request.headers.get("User-Agent", "")

                create_admin_session(session_token, expires, ip, ua)
                log_audit_event("login", user_type="admin", ip_address=ip, user_agent=ua)

                session["logged_in"] = True
                session["session_token"] = session_token
                session.pop("pending_login", None)
                session.pop("2fa_code", None)
                session.pop("2fa_email_sent", None)

                return redirect(url_for("overview"))
            else:
                error = msg

    # Show email message
    admin_email = os.environ.get("ADMIN_EMAIL", "joel@pineydigital.com")

    # If email failed and no code configured, show error
    if not email_sent and not is_email_configured():
        error = "Email not configured. Please contact support."

    return render_template_string(f"""<!DOCTYPE html><html><head>
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1,user-scalable=no">
<title>Piney Digital — 2FA Verification</title>{BASE_CSS}
<style>
.code-input{{letter-spacing:8px;font-size:24px;text-align:center;font-family:monospace;width:200px;padding:12px;background:#0f172a;border:2px solid #334155;border-radius:8px;color:#fff}}
.code-input:focus{{border-color:#22c55e;outline:none}}
.resend-link{{color:#64748b;font-size:12px;margin-top:16px}}
.resend-link a{{color:#3b82f6;cursor:pointer}}
.email-sent{{background:#1e293b;border:1px solid #334155;border-radius:8px;padding:20px;margin-bottom:20px;text-align:center}}
.email-sent .icon{{font-size:48px;margin-bottom:12px}}
.email-sent .msg{{color:#94a3b8;font-size:14px}}
.email-sent .email{{color:#22c55e;font-weight:600}}
</style></head><body>
<div class="login-wrap">
  <div class="login-card">
    <h1>Two-Factor Authentication</h1>

    <div class="email-sent">
      <div class="icon">Email</div>
      <div class="msg">A verification code was sent to</div>
      <div class="email">{admin_email}</div>
    </div>

    {f'<p class="err">{error}</p>' if error else ''}

    <form method="POST">
      <input type="hidden" name="action" value="verify">
      <div style="margin:24px 0">
        <input type="text" name="code" maxlength="6" pattern="[0-9]{{6}}" class="code-input" placeholder="000000" autofocus required>
      </div>
      <button type="submit" class="btn-primary" style="width:100%">Verify</button>
    </form>

    <div class="resend-link">
      Didn't receive a code? <a href="#" onclick="document.getElementById('resend-form').submit(); return false;">Resend code</a>
    </div>
    <form id="resend-form" method="POST" style="display:none">
      <input type="hidden" name="action" value="resend">
    </form>

    <p style="color:#64748b;font-size:11px;margin-top:24px">
      Code expires in 5 minutes.
    </p>
  </div>
</div>
<script>
document.querySelector('input[name="code"]').addEventListener('input', function(e) {{
  this.value = this.value.replace(/[^0-9]/g, '').slice(0, 6);
}});
</script>
</body></html>""")


# ═══════════════════════════════════════════════════════════════
# PORTAL — Customer Signup & Login (SaaS)
# ═══════════════════════════════════════════════════════════════

PORTAL_CSS = """
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1,user-scalable=no">
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;
     background:linear-gradient(135deg,#0a1628 0%,#1a3a2a 100%);
     min-height:100vh;color:#e2e8f0}
.portal-wrap{max-width:480px;margin:0 auto;padding:60px 20px}
.portal-logo{text-align:center;margin-bottom:40px}
.portal-logo h1{font-size:28px;font-weight:700;color:#fff;margin-bottom:8px}
.portal-logo p{font-size:14px;color:#94a3b8}
.portal-card{background:#1e293b;border:1px solid #334155;border-radius:16px;padding:32px}
.portal-card h2{font-size:20px;font-weight:600;margin-bottom:24px;text-align:center}
.form-group{margin-bottom:16px}
.form-group label{display:block;font-size:12px;color:#94a3b8;margin-bottom:6px}
.form-group input{width:100%;padding:12px 14px;background:#0f172a;border:1px solid #334155;
                  border-radius:8px;color:#e2e8f0;font-size:14px;min-height:44px}
.form-group input:focus{outline:none;border-color:#3b82f6}
.form-group input.error{border-color:#ef4444}
.password-hint{font-size:11px;color:#64748b;margin-top:6px}
.plan-grid{display:grid;grid-template-columns:1fr 1fr 1fr;gap:12px;margin-bottom:20px}
.plan-option{background:#0f172a;border:2px solid #334155;border-radius:12px;padding:16px;text-align:center;
             cursor:pointer;transition:all .2s}
.plan-option:hover{border-color:#475569}
.plan-option.selected{border-color:#22c55e;background:rgba(34,197,94,0.1)}
.plan-option .price{font-size:24px;font-weight:700;color:#fff}
.plan-option .price span{font-size:12px;color:#94a3b8}
.plan-option .name{font-size:13px;font-weight:600;margin-top:8px}
.plan-option .features{font-size:11px;color:#64748b;margin-top:8px;line-height:1.5}
.btn-primary{width:100%;padding:14px;background:linear-gradient(135deg,#22c55e,#16a34a);
             border:none;border-radius:8px;color:#fff;font-size:14px;font-weight:600;
             cursor:pointer;margin-top:8px;min-height:44px}
.btn-primary:hover{opacity:.9}
.btn-primary:disabled{opacity:.5;cursor:not-allowed}
.btn-secondary{width:100%;padding:12px;background:#334155;border:none;border-radius:8px;
                color:#94a3b8;font-size:13px;cursor:pointer;margin-top:12px;min-height:44px}
.divider{display:flex;align-items:center;margin:24px 0;color:#475569}
.divider::before,.divider::after{content:'';flex:1;height:1px;background:#334155}
.divider span{padding:0 12px;font-size:12px}
.err{color:#ef4444;font-size:12px;margin-top:8px}
.success{color:#22c55e;font-size:12px;margin-top:8px}
.password-strength{margin-top:8px}
.strength-bar{height:4px;background:#334155;border-radius:2px;overflow:hidden}
.strength-bar .fill{height:100%;transition:width .3s}
.strength-bar .fill.weak{background:#ef4444}
.strength-bar .fill.medium{background:#f59e0b}
.strength-bar .fill.strong{background:#22c55e}
.checklist{margin-top:12px;font-size:11px}
.checklist div{color:#64748b;margin:4px 0}
.checklist div.met{color:#22c55e}
.checklist div.met::before{content:'✓ '}
.checklist div:not(.met)::before{content:'○ '}
a{color:#3b82f6;text-decoration:none}
a:hover{text-decoration:underline}
@media (max-width: 480px) {
  .portal-wrap{padding:30px 16px}
  .portal-card{padding:24px 16px}
  .plan-grid{grid-template-columns:1fr;gap:8px}
}
</style>"""


@app.route("/portal")
def portal_landing():
    """Customer portal landing page."""
    return render_template_string(f"""<!DOCTYPE html><html><head>
<title>Piney Digital — Customer Portal</title>{PORTAL_CSS}</head><body>
<div class="portal-wrap">
  <div class="portal-logo">
    <h1>🌲 Piney Digital</h1>
    <p>Customer Portal</p>
  </div>
  <div class="portal-card" style="text-align:center">
    <h2>Welcome Back</h2>
    <p style="color:#94a3b8;margin-bottom:24px">Sign in to manage your loyalty program, view analytics, and track customers.</p>
    <form method="POST" action="/portal/login">
      <div class="form-group">
        <input type="email" name="email" placeholder="Email address" required>
      </div>
      <div class="form-group">
        <input type="password" name="password" placeholder="Password" required>
      </div>
      <button type="submit" class="btn-primary">Sign In</button>
    </form>
    <div class="divider"><span>or</span></div>
    <a href="/portal/signup"><button type="button" class="btn-secondary">Create an account</button></a>
    <p style="margin-top:20px;font-size:12px;color:#64748b">
      <a href="/loyalty-landing">← Back to LoyaltyLoop</a>
    </p>
  </div>
</div></body></html>""")


@app.route("/portal/signup", methods=["GET", "POST"])
def portal_signup():
    """Customer signup for Piney Outreach services."""
    from modules.auth_security import (
        validate_password, hash_password, generate_email_token,
        is_valid_email, check_password_strength
    )
    from modules.database import (
        create_business_user, get_business_user_by_email, set_verification_token
    )

    error = ""
    success = ""

    if request.method == "POST":
        owner_name = request.form.get("owner_name", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        plan = request.form.get("plan", "starter")
        agree = request.form.get("agree")

        # Validate
        if not owner_name or len(owner_name) < 2:
            error = "Please enter your name"
        elif not is_valid_email(email):
            error = "Please enter a valid email address"
        elif get_business_user_by_email(email):
            error = "An account with this email already exists"
        elif not agree:
            error = "You must agree to the Terms and Privacy Policy"
        else:
            valid, msg = validate_password(password)
            if not valid:
                error = msg

        if not error:
            # Create user
            password_hash = hash_password(password)
            user_id = create_business_user(email, password_hash, owner_name, plan)

            # Generate verification token
            token = generate_email_token(email)
            set_verification_token(user_id, token)

            # TODO: Send verification email
            success = f"Account created! Please check {email} to verify your email."
            # For now, redirect to login
            return redirect(url_for("portal_login", created="1"))

    return render_template_string(f"""<!DOCTYPE html><html><head>
<title>Sign Up — Piney Digital</title>{PORTAL_CSS}</head><body>
<div class="portal-wrap">
  <div class="portal-logo">
    <h1>🌲 Piney Digital</h1>
    <p>Create your account</p>
  </div>
  <div class="portal-card">
    <h2>Choose Your Plan</h2>

    <form method="POST" id="signup-form">
      <div class="plan-grid">
        <div class="plan-option" data-plan="starter" onclick="selectPlan('starter')">
          <div class="price">$99<span>/mo</span></div>
          <div class="name">Starter</div>
          <div class="features">Website<br>Hosting<br>Basic Support</div>
        </div>
        <div class="plan-option selected" data-plan="growth" onclick="selectPlan('growth')">
          <div class="price">$249<span>/mo</span></div>
          <div class="name">Growth</div>
          <div class="features">+ Loyalty System<br>+ SMS Marketing<br>+ Reviews</div>
        </div>
        <div class="plan-option" data-plan="pro" onclick="selectPlan('pro')">
          <div class="price">$449<span>/mo</span></div>
          <div class="name">Pro</div>
          <div class="features">+ Bookings<br>+ Gift Cards<br>+ Priority</div>
        </div>
      </div>
      <input type="hidden" name="plan" id="plan-input" value="growth">

      <div class="form-group">
        <label>Your Name</label>
        <input type="text" name="owner_name" placeholder="John Smith" required
               value="{request.form.get('owner_name', '')}">
      </div>

      <div class="form-group">
        <label>Email Address</label>
        <input type="email" name="email" placeholder="you@business.com" required
               value="{request.form.get('email', '')}">
      </div>

      <div class="form-group">
        <label>Password</label>
        <input type="password" name="password" id="password" placeholder="Create a strong password" required
               oninput="checkStrength(this.value)">
        <div class="password-hint">Min 12 characters with uppercase, lowercase, number, and special character</div>
        <div class="password-strength">
          <div class="strength-bar"><div class="fill" id="strength-fill" style="width:0%"></div></div>
        </div>
        <div class="checklist" id="password-checklist">
          <div id="check-length">At least 12 characters</div>
          <div id="check-upper">One uppercase letter</div>
          <div id="check-lower">One lowercase letter</div>
          <div id="check-number">One number</div>
          <div id="check-special">One special character (!@#$ etc.)</div>
        </div>
      </div>

      {f'<p class="err">{error}</p>' if error else ''}
      {f'<p class="success">{success}</p>' if success else ''}

      <div style="margin-top:16px">
        <label style="display:flex;align-items:flex-start;gap:10px;cursor:pointer;font-size:12px;color:#94a3b8">
          <input type="checkbox" name="agree" required style="margin-top:3px">
          <span>I agree to the <a href="/terms" style="color:#3b82f6">Terms of Service</a> and <a href="/privacy" style="color:#3b82f6">Privacy Policy</a></span>
        </label>
      </div>

      <button type="submit" class="btn-primary">Create Account</button>
    </form>

    <div class="divider"><span>or</span></div>
    <a href="/portal"><button type="button" class="btn-secondary">Already have an account?</button></a>
  </div>
</div>

<script>
function selectPlan(plan) {{
  document.querySelectorAll('.plan-option').forEach(el => el.classList.remove('selected'));
  document.querySelector('[data-plan="' + plan + '"]').classList.add('selected');
  document.getElementById('plan-input').value = plan;
}}

function checkStrength(pwd) {{
  let score = 0;
  const checks = {{
    length: pwd.length >= 12,
    upper: /[A-Z]/.test(pwd),
    lower: /[a-z]/.test(pwd),
    number: /[0-9]/.test(pwd),
    special: /[!@#$%^&*()_+\\-=\\[\\]{{}}|;:,.<>?]/.test(pwd)
  }};

  Object.keys(checks).forEach(k => {{
    const el = document.getElementById('check-' + k);
    if (checks[k]) {{ el.classList.add('met'); score++; }}
    else {{ el.classList.remove('met'); }}
  }});

  const fill = document.getElementById('strength-fill');
  const pct = (score / 5) * 100;
  fill.style.width = pct + '%';
  fill.className = 'fill ' + (pct < 40 ? 'weak' : pct < 80 ? 'medium' : 'strong');
}}
</script>
</body></html>""")


@app.route("/portal/login", methods=["GET", "POST"])
def portal_login():
    """Customer login."""
    from modules.auth_security import verify_password, create_session, check_rate_limit, record_login_attempt
    from modules.database import get_business_user_by_email, log_audit_event

    error = ""
    created = request.args.get("created") == "1"

    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        ip = request.remote_addr

        # Check rate limit
        allowed, remaining = check_rate_limit(email, max_attempts=5)
        if not allowed:
            error = f"Too many attempts. Try again in {remaining // 60} minutes."
        else:
            user = get_business_user_by_email(email)
            if user and verify_password(password, user["password_hash"]):
                # Check if email verified
                if not user.get("email_verified"):
                    error = "Please verify your email first. Check your inbox."
                else:
                    # Create session
                    session_id = os.urandom(32).hex()
                    session["portal_user_id"] = user["id"]
                    session["portal_email"] = email
                    session["portal_login_time"] = datetime.now().isoformat()

                    record_login_attempt(email, success=True)
                    log_audit_event("login", "business", user["id"], email, ip)

                    return redirect(url_for("portal_dashboard"))
            else:
                record_login_attempt(email, success=False)
                log_audit_event("login_failed", "business", None, email, ip)
                error = "Invalid email or password"

    return render_template_string(f"""<!DOCTYPE html><html><head>
<title>Login — Piney Digital</title>{PORTAL_CSS}</head><body>
<div class="portal-wrap">
  <div class="portal-logo">
    <h1>🌲 Piney Digital</h1>
    <p>Customer Portal</p>
  </div>
  <div class="portal-card">
    <h2>Sign In</h2>
    {f'<p class="success" style="text-align:center;margin-bottom:16px">Account created! Please check your email to verify.</p>' if created else ''}
    <form method="POST">
      <div class="form-group">
        <label>Email Address</label>
        <input type="email" name="email" placeholder="you@business.com" required
               value="{request.form.get('email', '')}">
      </div>
      <div class="form-group">
        <label>Password</label>
        <input type="password" name="password" placeholder="Your password" required>
      </div>
      {f'<p class="err">{error}</p>' if error else ''}
      <button type="submit" class="btn-primary">Sign In</button>
    </form>
    <div class="divider"><span>or</span></div>
    <a href="/portal/signup"><button type="button" class="btn-secondary">Create an account</button></a>
    <p style="margin-top:20px;font-size:12px;color:#64748b;text-align:center">
      <a href="/portal/forgot-password">Forgot password?</a>
    </p>
  </div>
</div></body></html>""")


def portal_login_required(f):
    """Decorator to require portal login."""
    from functools import wraps
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("portal_user_id"):
            return redirect(url_for("portal_login"))
        return f(*args, **kwargs)
    return decorated


@app.route("/portal/dashboard")
@portal_login_required
def portal_dashboard():
    """Customer dashboard - their business overview."""
    from modules.database import get_business_user_by_id

    user = get_business_user_by_id(session["portal_user_id"])
    if not user:
        session.clear()
        return redirect(url_for("portal_login"))

    return render_template_string(f"""<!DOCTYPE html><html><head>
<title>Dashboard — Piney Digital</title>{BASE_CSS}</head><body>
<div class="layout">
<div class="sidebar">
  <div class="logo">
    <h1>🌲 Piney Digital</h1>
    <p>{user['owner_name'] or 'Customer'}</p>
  </div>
  <nav class="nav">
    <a href="/portal/dashboard">
      <span class="nav-icon">📊</span>
      <span class="nav-label">Dashboard</span>
    </a>
    <a href="/portal/calendar">
      <span class="nav-icon">📅</span>
      <span class="nav-label">Calendar</span>
    </a>
    <a href="/portal/services">
      <span class="nav-icon">✂️</span>
      <span class="nav-label">Services</span>
    </a>
    <a href="/portal/staff">
      <span class="nav-icon">👩‍💼</span>
      <span class="nav-label">Staff</span>
    </a>
    <a href="/portal/customers">
      <span class="nav-icon">👥</span>
      <span class="nav-label">Customers</span>
    </a>
    <a href="/portal/sms">
      <span class="nav-icon">💬</span>
      <span class="nav-label">SMS</span>
    </a>
    <a href="/portal/calls">
      <span class="nav-icon">📞</span>
      <span class="nav-label">Calls</span>
    </a>
    <a href="/portal/leads">
      <span class="nav-icon">🎯</span>
      <span class="nav-label">Leads</span>
    </a>
    <a href="/portal/loyalty">
      <span class="nav-icon">🎁</span>
      <span class="nav-label">Loyalty</span>
    </a>
    <a href="/portal/settings">
      <span class="nav-icon">⚙️</span>
      <span class="nav-label">Settings</span>
    </a>
  </nav>
  <div class="sidebar-footer">
    {user['email']} · <a href="/portal/logout">Sign out</a>
  </div>
</div>
<div class="main">
  <div class="topbar">
    <h2>Welcome, {user['owner_name'] or 'there'}!</h2>
    <div class="topbar-right">
      <span class="badge badge-green">{user['plan'].title()} Plan</span>
    </div>
  </div>

  <div class="stats">
    <div class="stat">
      <label>Your Customers</label>
      <div class="val blue">0</div>
      <div class="sub">loyalty members</div>
    </div>
    <div class="stat">
      <label>Punches Given</label>
      <div class="val">0</div>
      <div class="sub">this month</div>
    </div>
    <div class="stat">
      <label>Rewards Redeemed</label>
      <div class="val green">0</div>
      <div class="sub">all time</div>
    </div>
    <div class="stat">
      <label>Active Since</label>
      <div class="val" style="font-size:16px">{user['created_at'][:10] if user.get('created_at') else 'Today'}</div>
      <div class="sub">account created</div>
    </div>
  </div>

  <div class="panel">
    <h3>Getting Started</h3>
    <div style="padding:20px">
      <p style="color:#94a3b8;margin-bottom:20px">Complete these steps to set up your loyalty program:</p>
      <div style="display:flex;flex-direction:column;gap:12px">
        <div style="display:flex;align-items:center;gap:12px;padding:12px;background:#0f172a;border-radius:8px">
          <span style="color:#22c55e;font-size:18px">✓</span>
          <span style="color:#e2e8f0">Account created</span>
        </div>
        <div style="display:flex;align-items:center;gap:12px;padding:12px;background:#0f172a;border-radius:8px">
          <span style="color:#64748b">○</span>
          <span style="color:#94a3b8">Set up your business profile</span>
        </div>
        <div style="display:flex;align-items:center;gap:12px;padding:12px;background:#0f172a;border-radius:8px">
          <span style="color:#64748b">○</span>
          <span style="color:#94a3b8">Configure loyalty program</span>
        </div>
        <div style="display:flex;align-items:center;gap:12px;padding:12px;background:#0f172a;border-radius:8px">
          <span style="color:#64748b">○</span>
          <span style="color:#94a3b8">Add your first customer</span>
        </div>
      </div>
    </div>
  </div>
</div>
</div></body></html>""")


@app.route("/portal/logout")
def portal_logout():
    """Logout from portal."""
    session.pop("portal_user_id", None)
    session.pop("portal_email", None)
    session.pop("portal_login_time", None)
    return redirect(url_for("portal_landing"))


@app.route("/portal/customers")
@portal_login_required
def portal_customers():
    """Business customer list - view all loyalty members."""
    from modules.database import get_business_user_by_id

    user = get_business_user_by_id(session["portal_user_id"])
    if not user:
        session.clear()
        return redirect(url_for("portal_login"))

    # Get business ID if linked
    business_id = user.get("business_id")

    # If no business linked yet, show setup prompt
    if not business_id:
        return render_template_string(f"""<!DOCTYPE html><html><head>
<title>Customers — Piney Digital</title>{BASE_CSS}</head><body>
<div class="layout">
<div class="sidebar">
  <div class="logo"><h1>🌲 Piney Digital</h1><p>{user['owner_name'] or 'Customer'}</p></div>
  <nav class="nav">
    <a href="/portal/dashboard">
      <span class="nav-icon">📊</span>
      <span class="nav-label">Dashboard</span>
    </a>
    <a href="/portal/calendar">
      <span class="nav-icon">📅</span>
      <span class="nav-label">Calendar</span>
    </a>
    <a href="/portal/services">
      <span class="nav-icon">✂️</span>
      <span class="nav-label">Services</span>
    </a>
    <a href="/portal/staff">
      <span class="nav-icon">👩‍💼</span>
      <span class="nav-label">Staff</span>
    </a>
    <a href="/portal/customers">
      <span class="nav-icon">👥</span>
      <span class="nav-label">Customers</span>
    </a>
    <a href="/portal/sms">
      <span class="nav-icon">💬</span>
      <span class="nav-label">SMS</span>
    </a>
    <a href="/portal/calls">
      <span class="nav-icon">📞</span>
      <span class="nav-label">Calls</span>
    </a>
    <a href="/portal/leads">
      <span class="nav-icon">🎯</span>
      <span class="nav-label">Leads</span>
    </a>
    <a href="/portal/loyalty">
      <span class="nav-icon">🎁</span>
      <span class="nav-label">Loyalty</span>
    </a>
    <a href="/portal/settings">
      <span class="nav-icon">⚙️</span>
      <span class="nav-label">Settings</span>
    </a>
  </nav>
  <div class="sidebar-footer">{user['email']} · <a href="/portal/logout">Sign out</a></div>
</div>
<div class="main">
  <div class="topbar"><h2>Customers</h2></div>
  <div class="panel">
    <div style="text-align:center;padding:60px 20px">
      <div style="font-size:48px;margin-bottom:16px">🏪</div>
      <h3 style="margin-bottom:8px">Set Up Your Business First</h3>
      <p style="color:#94a3b8;margin-bottom:24px">You need to link your business to start tracking customers.</p>
      <a href="/portal/settings" class="btn" style="background:#22c55e;color:#fff;padding:12px 24px;border-radius:8px;text-decoration:none">Configure Business →</a>
    </div>
  </div>
</div>
</div></body></html>""")

    # Get business stats and customers
    stats = get_business_stats(business_id)

    return render_template_string(f"""<!DOCTYPE html><html><head>
<title>Customers — Piney Digital</title>{BASE_CSS}
<style>
.customer-row{{display:flex;align-items:center;justify-content:space-between;padding:16px;border-bottom:1px solid #334155}}
.customer-row:hover{{background:#0f172a}}
.customer-info{{display:flex;align-items:center;gap:12px}}
.customer-avatar{{width:40px;height:40px;border-radius:50%;background:#334155;display:flex;align-items:center;justify-content:center;color:#22c55e;font-weight:600}}
.customer-details{{}}
.customer-name{{font-weight:600;color:#e2e8f0}}
.customer-meta{{font-size:12px;color:#64748b}}
.progress-bar{{width:100px;height:8px;background:#334155;border-radius:4px;overflow:hidden}}
.progress-fill{{height:100%;background:linear-gradient(90deg,#22c55e,#16a34a);transition:width .3s}}
.stats-mini{{display:flex;gap:24px;margin-bottom:24px}}
.stat-mini{{background:#1e293b;padding:16px;border-radius:8px;border:1px solid #334155}}
.stat-mini .val{{font-size:24px;font-weight:700;color:#fff}}
.stat-mini .label{{font-size:12px;color:#64748b;margin-top:4px}}
</style></head><body>
<div class="layout">
<div class="sidebar">
  <div class="logo"><h1>🌲 Piney Digital</h1><p>{user['owner_name'] or 'Customer'}</p></div>
  <nav class="nav">
    <a href="/portal/dashboard">
      <span class="nav-icon">📊</span>
      <span class="nav-label">Dashboard</span>
    </a>
    <a href="/portal/calendar">
      <span class="nav-icon">📅</span>
      <span class="nav-label">Calendar</span>
    </a>
    <a href="/portal/services">
      <span class="nav-icon">✂️</span>
      <span class="nav-label">Services</span>
    </a>
    <a href="/portal/staff">
      <span class="nav-icon">👩‍💼</span>
      <span class="nav-label">Staff</span>
    </a>
    <a href="/portal/customers">
      <span class="nav-icon">👥</span>
      <span class="nav-label">Customers</span>
    </a>
    <a href="/portal/sms">
      <span class="nav-icon">💬</span>
      <span class="nav-label">SMS</span>
    </a>
    <a href="/portal/calls">
      <span class="nav-icon">📞</span>
      <span class="nav-label">Calls</span>
    </a>
    <a href="/portal/leads">
      <span class="nav-icon">🎯</span>
      <span class="nav-label">Leads</span>
    </a>
    <a href="/portal/loyalty">
      <span class="nav-icon">🎁</span>
      <span class="nav-label">Loyalty</span>
    </a>
    <a href="/portal/settings">
      <span class="nav-icon">⚙️</span>
      <span class="nav-label">Settings</span>
    </a>
  </nav>
  <div class="sidebar-footer">{user['email']} · <a href="/portal/logout">Sign out</a></div>
</div>
<div class="main">
  <div class="topbar"><h2>Your Customers</h2></div>

  <div class="stats-mini">
    <div class="stat-mini">
      <div class="val">{stats['total_customers']}</div>
      <div class="label">Total Members</div>
    </div>
    <div class="stat-mini">
      <div class="val">{stats['total_punches']}</div>
      <div class="label">Punches Given</div>
    </div>
    <div class="stat-mini">
      <div class="val" style="color:#22c55e">{stats['total_rewards']}</div>
      <div class="label">Rewards Redeemed</div>
    </div>
  </div>

  <div class="panel">
    <h3 style="padding:16px 20px;border-bottom:1px solid #334155;margin:0">Customer List</h3>

    {''.join([f'''
    <div class="customer-row">
      <div class="customer-info">
        <div class="customer-avatar">{c['customer_name'][0].upper() if c.get('customer_name') else '?'}</div>
        <div class="customer-details">
          <div class="customer-name">{c.get('customer_name', 'Unknown')}</div>
          <div class="customer-meta">{c.get('punches', 0)} / {c.get('punches_needed', 5)} punches</div>
        </div>
      </div>
      <div style="display:flex;align-items:center;gap:16px">
        <div class="progress-bar">
          <div class="progress-fill" style="width:{min(100, round(c.get('punches', 0) / c.get('punches_needed', 5) * 100))}%"></div>
        </div>
        {'<span style="color:#22c55e;font-size:12px">🎁 Ready</span>' if c.get('punches', 0) >= c.get('punches_needed', 5) else ''}
      </div>
    </div>
    ''' for c in stats['customers']]) if stats['customers'] else '<div style="text-align:center;padding:40px;color:#64748b">No customers yet. Share your loyalty program to get started!</div>'}
  </div>
</div>
</div></body></html>""")


@app.route("/portal/loyalty", methods=["GET", "POST"])
@portal_login_required
def portal_loyalty():
    """Loyalty program settings - configure punch cards."""
    from modules.database import get_business_user_by_id
    from modules.loyalty_db import get_loyalty_business, update_loyalty_business

    user = get_business_user_by_id(session["portal_user_id"])
    if not user:
        session.clear()
        return redirect(url_for("portal_login"))

    business_id = user.get("business_id")
    success_msg = ""
    error_msg = ""

    # Handle form submission
    if request.method == "POST":
        try:
            punches_needed = int(request.form.get("punches_needed", 5))
            discount_percent = int(request.form.get("discount_percent", 15))

            if punches_needed < 1 or punches_needed > 20:
                error_msg = "Punches needed must be between 1 and 20"
            elif discount_percent < 5 or discount_percent > 100:
                error_msg = "Discount must be between 5% and 100%"
            elif business_id:
                update_loyalty_business(business_id, {
                    "punches_needed": punches_needed,
                    "discount_percent": discount_percent
                })
                success_msg = "Loyalty settings updated!"
        except ValueError:
            error_msg = "Please enter valid numbers"

    if not business_id:
        return render_template_string(f"""<!DOCTYPE html><html><head>
<title>Loyalty — Piney Digital</title>{BASE_CSS}</head><body>
<div class="layout">
<div class="sidebar">
  <div class="logo"><h1>🌲 Piney Digital</h1><p>{user['owner_name'] or 'Customer'}</p></div>
  <nav class="nav">
    <a href="/portal/dashboard">
      <span class="nav-icon">📊</span>
      <span class="nav-label">Dashboard</span>
    </a>
    <a href="/portal/calendar">
      <span class="nav-icon">📅</span>
      <span class="nav-label">Calendar</span>
    </a>
    <a href="/portal/services">
      <span class="nav-icon">✂️</span>
      <span class="nav-label">Services</span>
    </a>
    <a href="/portal/staff">
      <span class="nav-icon">👩‍💼</span>
      <span class="nav-label">Staff</span>
    </a>
    <a href="/portal/customers">
      <span class="nav-icon">👥</span>
      <span class="nav-label">Customers</span>
    </a>
    <a href="/portal/sms">
      <span class="nav-icon">💬</span>
      <span class="nav-label">SMS</span>
    </a>
    <a href="/portal/calls">
      <span class="nav-icon">📞</span>
      <span class="nav-label">Calls</span>
    </a>
    <a href="/portal/leads">
      <span class="nav-icon">🎯</span>
      <span class="nav-label">Leads</span>
    </a>
    <a href="/portal/loyalty">
      <span class="nav-icon">🎁</span>
      <span class="nav-label">Loyalty</span>
    </a>
    <a href="/portal/settings">
      <span class="nav-icon">⚙️</span>
      <span class="nav-label">Settings</span>
    </a>
  </nav>
  <div class="sidebar-footer">{user['email']} · <a href="/portal/logout">Sign out</a></div>
</div>
<div class="main">
  <div class="topbar"><h2>Loyalty Program</h2></div>
  <div class="panel">
    <div style="text-align:center;padding:60px 20px">
      <div style="font-size:48px;margin-bottom:16px">🎁</div>
      <h3 style="margin-bottom:8px">Set Up Your Loyalty Program</h3>
      <p style="color:#94a3b8;margin-bottom:24px">Configure your business in settings to start your loyalty program.</p>
      <a href="/portal/settings" class="btn" style="background:#22c55e;color:#fff;padding:12px 24px;border-radius:8px;text-decoration:none">Configure Business →</a>
    </div>
  </div>
</div>
</div></body></html>""")

    # Get business details
    business = get_loyalty_business(business_id)

    return render_template_string(f"""<!DOCTYPE html><html><head>
<title>Loyalty — Piney Digital</title>{BASE_CSS}
<style>
.loyalty-preview{{background:#1e293b;border-radius:12px;padding:24px;text-align:center;border:1px solid #334155;margin-bottom:24px}}
.preview-card{{background:linear-gradient(135deg,#1a3a2a,#0a1628);border-radius:12px;padding:20px;margin:20px auto;max-width:280px}}
.preview-punches{{display:flex;justify-content:center;gap:8px;margin:16px 0}}
.punch-circle{{width:32px;height:32px;border-radius:50%;background:#334155;display:flex;align-items:center;justify-content:center}}
.punch-circle.filled{{background:#22c55e}}
.settings-form{{display:grid;gap:20px;max-width:400px}}
.form-row{{display:flex;align-items:center;gap:16px}}
.form-row label{{min-width:120px;color:#94a3b8}}
.form-row input{{background:#0f172a;border:1px solid #334155;color:#e2e8f0;padding:8px 12px;border-radius:6px;width:100px}}
</style></head><body>
<div class="layout">
<div class="sidebar">
  <div class="logo"><h1>🌲 Piney Digital</h1><p>{user['owner_name'] or 'Customer'}</p></div>
  <nav class="nav">
    <a href="/portal/dashboard">
      <span class="nav-icon">📊</span>
      <span class="nav-label">Dashboard</span>
    </a>
    <a href="/portal/calendar">
      <span class="nav-icon">📅</span>
      <span class="nav-label">Calendar</span>
    </a>
    <a href="/portal/services">
      <span class="nav-icon">✂️</span>
      <span class="nav-label">Services</span>
    </a>
    <a href="/portal/staff">
      <span class="nav-icon">👩‍💼</span>
      <span class="nav-label">Staff</span>
    </a>
    <a href="/portal/customers">
      <span class="nav-icon">👥</span>
      <span class="nav-label">Customers</span>
    </a>
    <a href="/portal/sms">
      <span class="nav-icon">💬</span>
      <span class="nav-label">SMS</span>
    </a>
    <a href="/portal/calls">
      <span class="nav-icon">📞</span>
      <span class="nav-label">Calls</span>
    </a>
    <a href="/portal/leads">
      <span class="nav-icon">🎯</span>
      <span class="nav-label">Leads</span>
    </a>
    <a href="/portal/loyalty">
      <span class="nav-icon">🎁</span>
      <span class="nav-label">Loyalty</span>
    </a>
    <a href="/portal/settings">
      <span class="nav-icon">⚙️</span>
      <span class="nav-label">Settings</span>
    </a>
  </nav>
  <div class="sidebar-footer">{user['email']} · <a href="/portal/logout">Sign out</a></div>
</div>
<div class="main">
  <div class="topbar"><h2>Loyalty Program</h2></div>

  {f'<div style="background:#22c55e20;color:#22c55e;padding:12px;border-radius:8px;margin-bottom:20px">{success_msg}</div>' if success_msg else ''}
  {f'<div style="background:#ef444420;color:#ef4444;padding:12px;border-radius:8px;margin-bottom:20px">{error_msg}</div>' if error_msg else ''}

  <div class="loyalty-preview">
    <h3 style="margin-bottom:8px">Your Loyalty Card Preview</h3>
    <p style="color:#64748b;font-size:13px">This is what customers see when they join your program</p>
    <div class="preview-card">
      <div style="font-weight:700;font-size:18px;color:#fff">{business['name'] if business else 'Your Business'}</div>
      <div style="color:#94a3b8;font-size:12px;margin-top:4px">{business['type'] if business and business.get('type') else 'Local Business'}</div>
      <div class="preview-punches">
        {''.join([f'<div class="punch-circle filled">✓</div>' for _ in range(min(business['punches_needed'], 5)) if business])}
        {''.join([f'<div class="punch-circle"></div>' for _ in range(max(0, 5 - business['punches_needed']) if business else 5)])}
      </div>
      <div style="color:#22c55e;font-weight:600">🎁 {business['discount_percent'] if business else 15}% OFF</div>
    </div>
  </div>

  <div class="panel">
    <h3 style="margin:0 0 20px 0">Program Settings</h3>
    <form method="POST" class="settings-form">
      <div class="form-row">
        <label>Punches needed:</label>
        <input type="number" name="punches_needed" value="{business['punches_needed'] if business else 5}" min="1" max="20">
      </div>
      <div class="form-row">
        <label>Reward discount:</label>
        <input type="number" name="discount_percent" value="{business['discount_percent'] if business else 15}" min="5" max="100"> %
      </div>
      <div style="margin-top:12px">
        <button type="submit" style="background:#22c55e;color:#fff;padding:10px 20px;border:none;border-radius:8px;cursor:pointer;font-weight:600">Save Settings</button>
      </div>
    </form>
  </div>
</div>
</div></body></html>""")


@app.route("/portal/settings", methods=["GET", "POST"])
@portal_login_required
def portal_settings():
    """Business settings - profile and account configuration."""
    from modules.database import get_business_user_by_id, update_business_user
    from modules.auth_security import validate_password, hash_password, verify_password
    from modules.loyalty_db import create_loyalty_business, get_loyalty_business, update_loyalty_business

    user = get_business_user_by_id(session["portal_user_id"])
    if not user:
        session.clear()
        return redirect(url_for("portal_login"))

    success_msg = ""
    error_msg = ""

    business_id = user.get("business_id")
    business = get_loyalty_business(business_id) if business_id else None

    if request.method == "POST":
        action = request.form.get("action")

        if action == "create_business":
            # Create new business profile
            name = request.form.get("business_name", "").strip()
            biz_type = request.form.get("business_type", "").strip()
            city = request.form.get("city", "").strip()
            phone = request.form.get("phone", "").strip()
            description = request.form.get("description", "").strip()
            address = request.form.get("address", "").strip()
            website = request.form.get("website", "").strip()

            if not name:
                error_msg = "Business name is required"
            else:
                # Create loyalty business
                new_biz_id = create_loyalty_business(
                    name=name,
                    business_type=biz_type,
                    city=city,
                    phone=phone,
                    description=description,
                    address=address,
                    website=website
                )
                # Link to user
                update_business_user(user["id"], {"business_id": new_biz_id})
                success_msg = "Business profile created!"
                user = get_business_user_by_id(session["portal_user_id"])
                business_id = user.get("business_id")
                business = get_loyalty_business(business_id)

        elif action == "update_business":
            # Update business info
            if business_id:
                name = request.form.get("business_name", "").strip()
                biz_type = request.form.get("business_type", "").strip()
                city = request.form.get("city", "").strip()
                phone = request.form.get("phone", "").strip()
                description = request.form.get("description", "").strip()
                address = request.form.get("address", "").strip()
                website = request.form.get("website", "").strip()

                update_loyalty_business(business_id, {
                    "name": name,
                    "type": biz_type,
                    "city": city,
                    "phone": phone,
                    "description": description,
                    "address": address,
                    "website": website
                })
                business = get_loyalty_business(business_id)
                success_msg = "Business info updated!"

        elif action == "update_account":
            # Update account settings
            owner_name = request.form.get("owner_name", "").strip()

            if owner_name:
                update_business_user(user["id"], {"owner_name": owner_name})
                success_msg = "Account updated!"
                user = get_business_user_by_id(session["portal_user_id"])

            # Handle password change
            current_pass = request.form.get("current_password")
            new_pass = request.form.get("new_password")
            confirm_pass = request.form.get("confirm_password")

            if new_pass:
                if not current_pass or not verify_password(current_pass, user["password_hash"]):
                    error_msg = "Current password is incorrect"
                elif new_pass != confirm_pass:
                    error_msg = "New passwords don't match"
                else:
                    valid, msg = validate_password(new_pass)
                    if not valid:
                        error_msg = msg
                    else:
                        update_business_user(user["id"], {"password_hash": hash_password(new_pass)})
                        success_msg = "Password updated!"

    return render_template_string(f"""<!DOCTYPE html><html><head>
<title>Settings — Piney Digital</title>{BASE_CSS}
<style>
.settings-section{{background:#1e293b;border:1px solid #334155;border-radius:12px;padding:24px;margin-bottom:24px}}
.settings-section h3{{margin:0 0 16px 0;font-size:16px}}
.form-grid{{display:grid;gap:16px}}
.form-group{{}}
.form-group label{{display:block;font-size:13px;color:#94a3b8;margin-bottom:6px}}
.form-group input{{width:100%;background:#0f172a;border:1px solid #334155;color:#e2e8f0;padding:10px 12px;border-radius:6px}}
.form-group input:focus{{outline:none;border-color:#22c55e}}
.btn-group{{display:flex;gap:12px;margin-top:16px}}
.btn-primary{{background:#22c55e;color:#fff;padding:10px 20px;border:none;border-radius:8px;cursor:pointer;font-weight:600}}
.btn-secondary{{background:#334155;color:#e2e8f0;padding:10px 20px;border:none;border-radius:8px;cursor:pointer}}
</style></head><body>
<div class="layout">
<div class="sidebar">
  <div class="logo"><h1>🌲 Piney Digital</h1><p>{user['owner_name'] or 'Customer'}</p></div>
  <nav class="nav">
    <a href="/portal/dashboard">
      <span class="nav-icon">📊</span>
      <span class="nav-label">Dashboard</span>
    </a>
    <a href="/portal/calendar">
      <span class="nav-icon">📅</span>
      <span class="nav-label">Calendar</span>
    </a>
    <a href="/portal/services">
      <span class="nav-icon">✂️</span>
      <span class="nav-label">Services</span>
    </a>
    <a href="/portal/staff">
      <span class="nav-icon">👩‍💼</span>
      <span class="nav-label">Staff</span>
    </a>
    <a href="/portal/customers">
      <span class="nav-icon">👥</span>
      <span class="nav-label">Customers</span>
    </a>
    <a href="/portal/sms">
      <span class="nav-icon">💬</span>
      <span class="nav-label">SMS</span>
    </a>
    <a href="/portal/calls">
      <span class="nav-icon">📞</span>
      <span class="nav-label">Calls</span>
    </a>
    <a href="/portal/leads">
      <span class="nav-icon">🎯</span>
      <span class="nav-label">Leads</span>
    </a>
    <a href="/portal/loyalty">
      <span class="nav-icon">🎁</span>
      <span class="nav-label">Loyalty</span>
    </a>
    <a href="/portal/settings">
      <span class="nav-icon">⚙️</span>
      <span class="nav-label">Settings</span>
    </a>
  </nav>
  <div class="sidebar-footer">{user['email']} · <a href="/portal/logout">Sign out</a></div>
</div>
<div class="main">
  <div class="topbar"><h2>Settings</h2></div>

  {f'<div style="background:#22c55e20;color:#22c55e;padding:12px;border-radius:8px;margin-bottom:20px">{success_msg}</div>' if success_msg else ''}
  {f'<div style="background:#ef444420;color:#ef4444;padding:12px;border-radius:8px;margin-bottom:20px">{error_msg}</div>' if error_msg else ''}

  {"<!-- Business Profile Section -->" if business_id else ""}

  {"<!-- Create Business Section (if not linked) -->" if not business_id else ""}

  {f'''
  <div class="settings-section">
    <h3>Business Profile</h3>
    <form method="POST" class="form-grid">
      <input type="hidden" name="action" value="update_business">
      <div class="form-group">
        <label>Business Name</label>
        <input type="text" name="business_name" value="{business['name'] if business else ''}">
      </div>
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:16px">
        <div class="form-group">
          <label>Business Type</label>
          <input type="text" name="business_type" value="{business['type'] if business and business.get('type') else ''}" placeholder="e.g. Restaurant, Salon">
        </div>
        <div class="form-group">
          <label>City</label>
          <input type="text" name="city" value="{business['city'] if business and business.get('city') else ''}">
        </div>
      </div>
      <div class="form-group">
        <label>Phone</label>
        <input type="tel" name="phone" value="{business['phone'] if business and business.get('phone') else ''}">
      </div>
      <div class="form-group">
        <label>Description</label>
        <textarea name="description" rows="3" style="width:100%;background:#0f172a;border:1px solid #334155;color:#e2e8f0;padding:10px 12px;border-radius:6px;resize:vertical" placeholder="Tell customers about your business...">{business['description'] if business and business.get('description') else ''}</textarea>
      </div>
      <div class="form-group">
        <label>Address</label>
        <input type="text" name="address" value="{business['address'] if business and business.get('address') else ''}" placeholder="123 Main St, City">
      </div>
      <div class="form-group">
        <label>Website</label>
        <input type="url" name="website" value="{business['website'] if business and business.get('website') else ''}" placeholder="https://yourwebsite.com">
      </div>
      <div class="btn-group">
        <button type="submit" class="btn-primary">Update Business</button>
      </div>
    </form>
  </div>
  ''' if business_id else f'''
  <div class="settings-section">
    <h3>Create Your Business Profile</h3>
    <p style="color:#94a3b8;margin-bottom:16px">Set up your business to start accepting loyalty members.</p>
    <form method="POST" class="form-grid">
      <input type="hidden" name="action" value="create_business">
      <div class="form-group">
        <label>Business Name *</label>
        <input type="text" name="business_name" required placeholder="Your Business Name">
      </div>
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:16px">
        <div class="form-group">
          <label>Business Type</label>
          <input type="text" name="business_type" placeholder="e.g. Restaurant, Salon">
        </div>
        <div class="form-group">
          <label>City</label>
          <input type="text" name="city" placeholder="City">
        </div>
      </div>
      <div class="form-group">
        <label>Phone</label>
        <input type="tel" name="phone" placeholder="(555) 123-4567">
      </div>
      <div class="form-group">
        <label>Description</label>
        <textarea name="description" rows="3" style="width:100%;background:#0f172a;border:1px solid #334155;color:#e2e8f0;padding:10px 12px;border-radius:6px;resize:vertical" placeholder="Tell customers about your business..."></textarea>
      </div>
      <div class="form-group">
        <label>Address</label>
        <input type="text" name="address" placeholder="123 Main St, City">
      </div>
      <div class="form-group">
        <label>Website</label>
        <input type="url" name="website" placeholder="https://yourwebsite.com">
      </div>
      <div class="btn-group">
        <button type="submit" class="btn-primary">Create Business Profile</button>
      </div>
    </form>
  </div>
  '''}

  <div class="settings-section">
    <h3>Account Settings</h3>
    <form method="POST" class="form-grid">
      <input type="hidden" name="action" value="update_account">
      <div class="form-group">
        <label>Your Name</label>
        <input type="text" name="owner_name" value="{user['owner_name'] if user.get('owner_name') else ''}">
      </div>
      <div class="form-group">
        <label>Email</label>
        <input type="email" value="{user['email']}" disabled style="opacity:0.6">
        <p style="font-size:11px;color:#64748b;margin-top:4px">Email cannot be changed</p>
      </div>
      <hr style="border:none;border-top:1px solid #334155;margin:16px 0">
      <h4 style="margin:0 0 12px 0;font-size:14px">Change Password</h4>
      <div class="form-group">
        <label>Current Password</label>
        <input type="password" name="current_password">
      </div>
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:16px">
        <div class="form-group">
          <label>New Password</label>
          <input type="password" name="new_password">
        </div>
        <div class="form-group">
          <label>Confirm New Password</label>
          <input type="password" name="confirm_password">
        </div>
      </div>
      <p style="font-size:11px;color:#64748b">Password must be at least 8 characters with uppercase, lowercase, and a number.</p>
      <div class="btn-group">
        <button type="submit" class="btn-primary">Update Account</button>
      </div>
    </form>
  </div>

  <div class="settings-section">
    <h3>📅 Working Hours</h3>
    <p style="color:#94a3b8;font-size:13px;margin-bottom:16px">Set your staff's availability for appointments.</p>
    <a href="/portal/hours" style="display:inline-block;background:#3b82f6;color:#fff;padding:10px 20px;border-radius:8px;text-decoration:none;font-weight:600">Manage Hours →</a>
  </div>

  {f'''
  <div class="settings-section">
    <h3>🎁 Referral Program</h3>
    <p style="color:#94a3b8;font-size:13px;margin-bottom:16px">Let your customers refer friends and earn rewards. Set up your referral program to grow your business.</p>
    <a href="/referrals/business/{business_id}/settings" style="display:inline-block;background:#22c55e;color:#fff;padding:10px 20px;border-radius:8px;text-decoration:none;font-weight:600">Configure Referral Program →</a>
  </div>
  ''' if business_id else ''}

  <div class="settings-section" style="background:#1a1a2e">
    <h3 style="color:#64748b">Danger Zone</h3>
    <p style="color:#64748b;font-size:13px;margin-bottom:12px">Permanently delete your account and all data.</p>
    <button style="background:#ef4444;color:#fff;padding:8px 16px;border:none;border-radius:6px;cursor:pointer;opacity:0.5" disabled>Delete Account</button>
  </div>
</div>
</div></body></html>""")


@app.route("/portal/services", methods=["GET", "POST"])
@portal_login_required
def portal_services():
    """Manage booking services."""
    from modules.database import get_business_user_by_id
    from modules.bookings_db import get_business_services, create_service

    user = get_business_user_by_id(session["portal_user_id"])
    if not user:
        session.clear()
        return redirect(url_for("portal_login"))

    business_id = user.get("business_id")
    success_msg = ""
    error_msg = ""

    if not business_id:
        return render_template_string(f"""<!DOCTYPE html><html><head>
<title>Services — Piney Digital</title>{BASE_CSS}</head><body>
<div class="layout">
<div class="sidebar">
  <div class="logo"><h1>🌲 Piney Digital</h1><p>{user['owner_name'] or 'Customer'}</p></div>
  <nav class="nav">
    <a href="/portal/dashboard"><span class="nav-icon">📊</span><span class="nav-label">Dashboard</span></a>
    <a href="/portal/calendar"><span class="nav-icon">📅</span><span class="nav-label">Calendar</span></a>
    <a href="/portal/services"><span class="nav-icon">✂️</span><span class="nav-label">Services</span></a>
    <a href="/portal/staff"><span class="nav-icon">👩‍💼</span><span class="nav-label">Staff</span></a>
    <a href="/portal/customers"><span class="nav-icon">👥</span><span class="nav-label">Customers</span></a>
    <a href="/portal/sms"><span class="nav-icon">💬</span><span class="nav-label">SMS</span></a>
    <a href="/portal/calls"><span class="nav-icon">📞</span><span class="nav-label">Calls</span></a>
    <a href="/portal/leads"><span class="nav-icon">🎯</span><span class="nav-label">Leads</span></a>
    <a href="/portal/loyalty"><span class="nav-icon">🎁</span><span class="nav-label">Loyalty</span></a>
    <a href="/portal/settings"><span class="nav-icon">⚙️</span><span class="nav-label">Settings</span></a>
  </nav>
  <div class="sidebar-footer">{user['email']} · <a href="/portal/logout">Sign out</a></div>
</div>
<div class="main">
  <div class="topbar"><h2>Services</h2></div>
  <div class="panel" style="text-align:center;padding:60px 20px">
    <p style="color:#64748b">Set up your business in Settings to manage services.</p>
    <a href="/portal/settings" style="color:#22c55e">Go to Settings</a>
  </div>
</div>
</div></body></html>""")

    # Handle add service
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        duration = request.form.get("duration", "30")
        price = request.form.get("price", "0")
        description = request.form.get("description", "").strip()

        if not name:
            error_msg = "Service name is required"
        else:
            try:
                create_service(business_id, name, int(duration), float(price), description)
                success_msg = "Service added!"
            except Exception as e:
                error_msg = str(e)

    services = get_business_services(business_id)

    return render_template_string(f"""<!DOCTYPE html><html><head>
<title>Services — Piney Digital</title>{BASE_CSS}
<style>
.service-grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:16px}}
.service-card{{background:#1e293b;border:1px solid #334155;border-radius:12px;padding:20px}}
.service-card h4{{margin:0 0 8px 0;color:#fff}}
.service-card .meta{{color:#94a3b8;font-size:13px;margin-bottom:12px}}
.service-card .price{{color:#22c55e;font-weight:600}}
.add-form{{background:#1e293b;border:1px solid #334155;border-radius:12px;padding:24px;margin-bottom:24px}}
.add-form h3{{margin:0 0 16px 0}}
.form-row{{display:grid;grid-template-columns:1fr 100px 100px;gap:12px;margin-bottom:12px}}
.form-row input{{background:#0f172a;border:1px solid #334155;color:#e2e8f0;padding:10px;border-radius:6px}}
.form-row input:focus{{outline:none;border-color:#22c55e}}
.btn-add{{background:#22c55e;color:#fff;padding:10px 20px;border:none;border-radius:8px;cursor:pointer;font-weight:600}}
</style></head><body>
<div class="layout">
<div class="sidebar">
  <div class="logo"><h1>🌲 Piney Digital</h1><p>{user['owner_name'] or 'Customer'}</p></div>
  <nav class="nav">
    <a href="/portal/dashboard"><span class="nav-icon">📊</span><span class="nav-label">Dashboard</span></a>
    <a href="/portal/calendar"><span class="nav-icon">📅</span><span class="nav-label">Calendar</span></a>
    <a href="/portal/services" class="active"><span class="nav-icon">✂️</span><span class="nav-label">Services</span></a>
    <a href="/portal/staff"><span class="nav-icon">👩‍💼</span><span class="nav-label">Staff</span></a>
    <a href="/portal/customers"><span class="nav-icon">👥</span><span class="nav-label">Customers</span></a>
    <a href="/portal/sms"><span class="nav-icon">💬</span><span class="nav-label">SMS</span></a>
    <a href="/portal/calls"><span class="nav-icon">📞</span><span class="nav-label">Calls</span></a>
    <a href="/portal/leads"><span class="nav-icon">🎯</span><span class="nav-label">Leads</span></a>
    <a href="/portal/loyalty"><span class="nav-icon">🎁</span><span class="nav-label">Loyalty</span></a>
    <a href="/portal/settings"><span class="nav-icon">⚙️</span><span class="nav-label">Settings</span></a>
  </nav>
  <div class="sidebar-footer">{user['email']} · <a href="/portal/logout">Sign out</a></div>
</div>
<div class="main">
  <div class="topbar"><h2>Services</h2></div>

  {f'<div style="background:#22c55e20;color:#22c55e;padding:12px;border-radius:8px;margin-bottom:20px">{success_msg}</div>' if success_msg else ''}
  {f'<div style="background:#ef444420;color:#ef4444;padding:12px;border-radius:8px;margin-bottom:20px">{error_msg}</div>' if error_msg else ''}

  <div class="add-form">
    <h3>Add Service</h3>
    <form method="POST">
      <div class="form-row">
        <input type="text" name="name" placeholder="Service name (e.g., Haircut)" required>
        <input type="number" name="duration" value="30" min="5" max="480" title="Duration (minutes)">
        <input type="number" name="price" value="0" min="0" step="0.01" title="Price">
      </div>
      <input type="text" name="description" placeholder="Description (optional)" style="width:100%;background:#0f172a;border:1px solid #334155;color:#e2e8f0;padding:10px;border-radius:6px;margin-bottom:12px">
      <button type="submit" class="btn-add">Add Service</button>
    </form>
  </div>

  <div class="service-grid">
    {''.join([f'''
    <div class="service-card">
      <h4>{s.get('name', 'Service')}</h4>
      <div class="meta">{s.get('duration_min', 30)} min</div>
      <div class="price">${s.get('price', 0):.2f}</div>
      {f'<p style="color:#64748b;font-size:12px;margin-top:8px">{s.get("description", "")}</p>' if s.get('description') else ''}
    </div>
    ''' for s in services]) if services else '<p style="color:#64748b">No services yet. Add your first service above!</p>'}
  </div>
</div>
</div></body></html>""")


@app.route("/portal/staff", methods=["GET", "POST"])
@portal_login_required
def portal_staff():
    """Manage staff members."""
    from modules.database import get_business_user_by_id
    from modules.bookings_db import get_business_staff, create_staff

    user = get_business_user_by_id(session["portal_user_id"])
    if not user:
        session.clear()
        return redirect(url_for("portal_login"))

    business_id = user.get("business_id")
    success_msg = ""

    if not business_id:
        return redirect(url_for("portal_settings"))

    # Handle add staff
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        role = request.form.get("role", "").strip()
        phone = request.form.get("phone", "").strip()
        email = request.form.get("email", "").strip()

        if name:
            create_staff(business_id, name, role, email, phone)
            success_msg = "Staff member added!"

    staff = get_business_staff(business_id)

    return render_template_string(f"""<!DOCTYPE html><html><head>
<title>Staff — Piney Digital</title>{BASE_CSS}
<style>
.staff-grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:16px}}
.staff-card{{background:#1e293b;border:1px solid #334155;border-radius:12px;padding:20px;display:flex;justify-content:space-between;align-items:center}}
.staff-info h4{{margin:0 0 4px 0;color:#fff}}
.staff-info .role{{color:#94a3b8;font-size:13px}}
.staff-info .contact{{color:#64748b;font-size:12px;margin-top:4px}}
.add-form{{background:#1e293b;border:1px solid #334155;border-radius:12px;padding:24px;margin-bottom:24px}}
.form-grid{{display:grid;grid-template-columns:1fr 1fr;gap:12px}}
.form-grid input{{background:#0f172a;border:1px solid #334155;color:#e2e8f0;padding:10px;border-radius:6px}}
.btn-add{{background:#22c55e;color:#fff;padding:10px 20px;border:none;border-radius:8px;cursor:pointer;font-weight:600}}
.btn-hours{{background:#3b82f6;color:#fff;padding:6px 12px;border-radius:6px;text-decoration:none;font-size:12px}}
</style></head><body>
<div class="layout">
<div class="sidebar">
  <div class="logo"><h1>🌲 Piney Digital</h1><p>{user['owner_name'] or 'Customer'}</p></div>
  <nav class="nav">
    <a href="/portal/dashboard"><span class="nav-icon">📊</span><span class="nav-label">Dashboard</span></a>
    <a href="/portal/calendar"><span class="nav-icon">📅</span><span class="nav-label">Calendar</span></a>
    <a href="/portal/services"><span class="nav-icon">✂️</span><span class="nav-label">Services</span></a>
    <a href="/portal/staff" class="active"><span class="nav-icon">👩‍💼</span><span class="nav-label">Staff</span></a>
    <a href="/portal/customers"><span class="nav-icon">👥</span><span class="nav-label">Customers</span></a>
    <a href="/portal/sms"><span class="nav-icon">💬</span><span class="nav-label">SMS</span></a>
    <a href="/portal/calls"><span class="nav-icon">📞</span><span class="nav-label">Calls</span></a>
    <a href="/portal/leads"><span class="nav-icon">🎯</span><span class="nav-label">Leads</span></a>
    <a href="/portal/loyalty"><span class="nav-icon">🎁</span><span class="nav-label">Loyalty</span></a>
    <a href="/portal/settings"><span class="nav-icon">⚙️</span><span class="nav-label">Settings</span></a>
  </nav>
  <div class="sidebar-footer">{user['email']} · <a href="/portal/logout">Sign out</a></div>
</div>
<div class="main">
  <div class="topbar"><h2>Staff</h2></div>

  {f'<div style="background:#22c55e20;color:#22c55e;padding:12px;border-radius:8px;margin-bottom:20px">{success_msg}</div>' if success_msg else ''}

  <div class="add-form">
    <h3 style="margin:0 0 16px 0">Add Staff Member</h3>
    <form method="POST">
      <div class="form-grid">
        <input type="text" name="name" placeholder="Name *" required>
        <input type="text" name="role" placeholder="Role (e.g., Senior Stylist)">
        <input type="tel" name="phone" placeholder="Phone">
        <input type="email" name="email" placeholder="Email">
      </div>
      <button type="submit" class="btn-add" style="margin-top:12px">Add Staff</button>
    </form>
  </div>

  <div class="staff-grid">
    {''.join([f'''
    <div class="staff-card">
      <div class="staff-info">
        <h4>{s.get('name', 'Staff')}</h4>
        <div class="role">{s.get('role', 'Team Member')}</div>
        {f'<div class="contact">{s.get("phone")}</div>' if s.get('phone') else ''}
      </div>
      <a href="/portal/hours?staff={s.get('id')}" class="btn-hours">Set Hours</a>
    </div>
    ''' for s in staff]) if staff else '<p style="color:#64748b">No staff members yet. Add your first team member above!</p>'}
  </div>
</div>
</div></body></html>""")


@app.route("/portal/hours", methods=["GET", "POST"])
@portal_login_required
def portal_hours():
    """Manage staff working hours."""
    from modules.database import get_business_user_by_id
    from modules.bookings_db import get_business_staff, set_staff_availability, get_staff_availability

    user = get_business_user_by_id(session["portal_user_id"])
    if not user:
        session.clear()
        return redirect(url_for("portal_login"))

    business_id = user.get("business_id")
    success_msg = ""

    if not business_id:
        return redirect(url_for("portal_settings"))

    staff = get_business_staff(business_id)
    selected_staff_id = request.args.get("staff") or (staff[0].get("id") if staff else None)

    # Handle save hours
    if request.method == "POST" and selected_staff_id:
        days = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
        for i, day in enumerate(days):
            is_working = request.form.get(f"working_{i}") == "on"
            start = request.form.get(f"start_{i}", "09:00")
            end = request.form.get(f"end_{i}", "17:00")

            set_staff_availability(selected_staff_id, i, start if is_working else "00:00", end if is_working else "00:00", is_working)

        success_msg = "Hours updated!"

    # Get current availability
    availability = {}
    if selected_staff_id:
        availability = get_staff_availability(selected_staff_id)

    days = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]

    # Pre-build HTML to avoid nested f-string issues
    if staff:
        # Build staff select options
        staff_options = ''.join([f'<option value="{s.get("id")}" {"selected" if s.get("id") == selected_staff_id else ""}>{s.get("name")}</option>' for s in staff])

        # Build hours grid rows
        hours_rows = []
        for i, day in enumerate(days):
            avail = availability.get(i, {})
            is_working = avail.get("is_working", i < 5)  # Default: Mon-Fri working
            start = avail.get("start_time", "09:00")
            end = avail.get("end_time", "17:00")
            checked = "checked" if is_working else ""
            hours_rows.append(f'''<div class="hours-row"><div class="day">{day}</div><input type="checkbox" name="working_{i}" {checked}><input type="time" name="start_{i}" value="{start}"><input type="time" name="end_{i}" value="{end}"></div>''')

        hours_html = f'''
        <div class="staff-select">
          <form method="GET">
            <select name="staff" onchange="this.form.submit()">
              {staff_options}
            </select>
          </form>
        </div>

        <form method="POST">
          <div class="hours-grid">
            {''.join(hours_rows)}
          </div>
          <button type="submit" class="btn-save">Save Hours</button>
        </form>
        '''
    else:
        hours_html = '<div class="no-staff"><p>No staff members yet.</p><a href="/portal/staff" style="color:#22c55e">Add staff first</a></div>'

    return render_template_string(f"""<!DOCTYPE html><html><head>
<title>Working Hours — Piney Digital</title>{BASE_CSS}
<style>
.staff-select{{margin-bottom:24px}}
.staff-select select{{background:#0f172a;border:1px solid #334155;color:#e2e8f0;padding:10px 16px;border-radius:8px;font-size:14px;min-width:200px}}
.hours-grid{{background:#1e293b;border:1px solid #334155;border-radius:12px;padding:24px}}
.hours-row{{display:grid;grid-template-columns:120px 60px 100px 100px;gap:12px;align-items:center;padding:12px 0;border-bottom:1px solid #334155}}
.hours-row:last-child{{border-bottom:none}}
.hours-row .day{{font-weight:500;color:#fff}}
.hours-row input[type="time"]{{background:#0f172a;border:1px solid #334155;color:#e2e8f0;padding:8px;border-radius:6px}}
.hours-row input[type="checkbox"]{{width:20px;height:20px;accent-color:#22c55e}}
.btn-save{{background:#22c55e;color:#fff;padding:12px 24px;border:none;border-radius:8px;cursor:pointer;font-weight:600;margin-top:16px}}
.no-staff{{text-align:center;padding:40px;color:#64748b}}
</style></head><body>
<div class="layout">
<div class="sidebar">
  <div class="logo"><h1>🌲 Piney Digital</h1><p>{user['owner_name'] or 'Customer'}</p></div>
  <nav class="nav">
    <a href="/portal/dashboard"><span class="nav-icon">📊</span><span class="nav-label">Dashboard</span></a>
    <a href="/portal/calendar"><span class="nav-icon">📅</span><span class="nav-label">Calendar</span></a>
    <a href="/portal/services"><span class="nav-icon">✂️</span><span class="nav-label">Services</span></a>
    <a href="/portal/staff"><span class="nav-icon">👩‍💼</span><span class="nav-label">Staff</span></a>
    <a href="/portal/customers"><span class="nav-icon">👥</span><span class="nav-label">Customers</span></a>
    <a href="/portal/sms"><span class="nav-icon">💬</span><span class="nav-label">SMS</span></a>
    <a href="/portal/calls"><span class="nav-icon">📞</span><span class="nav-label">Calls</span></a>
    <a href="/portal/leads"><span class="nav-icon">🎯</span><span class="nav-label">Leads</span></a>
    <a href="/portal/loyalty"><span class="nav-icon">🎁</span><span class="nav-label">Loyalty</span></a>
    <a href="/portal/settings"><span class="nav-icon">⚙️</span><span class="nav-label">Settings</span></a>
  </nav>
  <div class="sidebar-footer">{user['email']} · <a href="/portal/logout">Sign out</a></div>
</div>
<div class="main">
  <div class="topbar"><h2>Working Hours</h2></div>

  {f'<div style="background:#22c55e20;color:#22c55e;padding:12px;border-radius:8px;margin-bottom:20px">{success_msg}</div>' if success_msg else ''}

  {hours_html}
</div>
</div></body></html>""")


@app.route("/portal/calendar")
@portal_login_required
def portal_calendar():
    """Business calendar - view and manage appointments."""
    from modules.database import get_business_user_by_id
    from modules.bookings_db import get_business_bookings, get_business_services, get_business_staff
    from datetime import datetime, timedelta

    user = get_business_user_by_id(session["portal_user_id"])
    if not user:
        session.clear()
        return redirect(url_for("portal_login"))

    business_id = user.get("business_id")

    # Get filter parameters
    filter_status = request.args.get('status', '')
    filter_date = request.args.get('date', '')

    # Get bookings
    if business_id:
        bookings = get_business_bookings(business_id)
        services = get_business_services(business_id)
        staff = get_business_staff(business_id)
    else:
        bookings = []
        services = []
        staff = []

    # Filter by status
    if filter_status:
        bookings = [b for b in bookings if b.get('status') == filter_status]

    # Filter by date range
    if filter_date == 'today':
        today = datetime.now().strftime('%Y-%m-%d')
        bookings = [b for b in bookings if b.get('booking_date') == today]
    elif filter_date == 'week':
        today = datetime.now()
        week_start = today.strftime('%Y-%m-%d')
        week_end = (today + timedelta(days=7)).strftime('%Y-%m-%d')
        bookings = [b for b in bookings if week_start <= b.get('booking_date', '') <= week_end]
    elif filter_date == 'upcoming':
        today = datetime.now().strftime('%Y-%m-%d')
        bookings = [b for b in bookings if b.get('booking_date', '') >= today]

    # Stats
    pending = len([b for b in bookings if b.get('status') == 'pending'])
    confirmed = len([b for b in bookings if b.get('status') == 'confirmed'])
    completed_today = len([b for b in bookings if b.get('status') == 'completed' and b.get('booking_date') == datetime.now().strftime('%Y-%m-%d')])

    return render_template_string(f"""<!DOCTYPE html><html><head>
<title>Calendar — Piney Digital</title>{BASE_CSS}
<style>
.calendar-header{{display:flex;justify-content:space-between;align-items:center;margin-bottom:24px}}
.filter-group{{display:flex;gap:8px}}
.filter-btn{{background:#334155;color:#94a3b8;padding:8px 16px;border-radius:6px;border:none;cursor:pointer;font-size:13px}}
.filter-btn.active{{background:#22c55e;color:#fff}}
.booking-list{{display:flex;flex-direction:column;gap:12px}}
.booking-card{{background:#1e293b;border:1px solid #334155;border-radius:12px;padding:16px;display:grid;grid-template-columns:1fr auto;gap:16px}}
.booking-card.pending{{border-left:3px solid #f59e0b}}
.booking-card.confirmed{{border-left:3px solid #22c55e}}
.booking-card.completed{{border-left:3px solid #64748b}}
.booking-card.cancelled{{border-left:3px solid #ef4444;opacity:0.7}}
.booking-info{{}}
.booking-customer{{font-weight:600;color:#fff;margin-bottom:4px}}
.booking-service{{color:#94a3b8;font-size:13px}}
.booking-meta{{color:#64748b;font-size:12px;margin-top:8px;display:flex;gap:16px}}
.booking-actions{{display:flex;flex-direction:column;gap:8px;justify-content:center}}
.action-btn{{padding:8px 16px;border-radius:6px;font-size:12px;cursor:pointer;border:none}}
.action-btn.confirm{{background:#22c55e;color:#fff}}
.action-btn.complete{{background:#3b82f6;color:#fff}}
.action-btn.cancel{{background:#ef4444;color:#fff}}
.status-badge{{display:inline-block;padding:4px 10px;border-radius:999px;font-size:11px;font-weight:600}}
.status-badge.pending{{background:#f59e0b20;color:#f59e0b}}
.status-badge.confirmed{{background:#22c55e20;color:#22c55e}}
.status-badge.completed{{background:#64748b20;color:#94a3b8}}
.status-badge.cancelled{{background:#ef444420;color:#ef4444}}
.empty-state{{text-align:center;padding:60px 20px;background:#1e293b;border-radius:12px;border:1px solid #334155}}
</style></head><body>
<div class="layout">
<div class="sidebar">
  <div class="logo"><h1>🌲 Piney Digital</h1><p>{user['owner_name'] or 'Customer'}</p></div>
  <nav class="nav">
    <a href="/portal/dashboard">
      <span class="nav-icon">📊</span>
      <span class="nav-label">Dashboard</span>
    </a>
    <a href="/portal/calendar">
      <span class="nav-icon">📅</span>
      <span class="nav-label">Calendar</span>
    </a>
    <a href="/portal/services">
      <span class="nav-icon">✂️</span>
      <span class="nav-label">Services</span>
    </a>
    <a href="/portal/staff">
      <span class="nav-icon">👩‍💼</span>
      <span class="nav-label">Staff</span>
    </a>
    <a href="/portal/customers">
      <span class="nav-icon">👥</span>
      <span class="nav-label">Customers</span>
    </a>
    <a href="/portal/sms">
      <span class="nav-icon">💬</span>
      <span class="nav-label">SMS</span>
    </a>
    <a href="/portal/calls">
      <span class="nav-icon">📞</span>
      <span class="nav-label">Calls</span>
    </a>
    <a href="/portal/leads">
      <span class="nav-icon">🎯</span>
      <span class="nav-label">Leads</span>
    </a>
    <a href="/portal/loyalty">
      <span class="nav-icon">🎁</span>
      <span class="nav-label">Loyalty</span>
    </a>
    <a href="/portal/settings">
      <span class="nav-icon">⚙️</span>
      <span class="nav-label">Settings</span>
    </a>
  </nav>
  <div class="sidebar-footer">{user['email']} · <a href="/portal/logout">Sign out</a></div>
</div>
<div class="main">
  <div class="calendar-header">
    <h2>Appointments</h2>
    <div class="filter-group">
      <a href="?date=today{'&status=' + filter_status if filter_status else ''}" class="filter-btn {'active' if filter_date == 'today' else ''}">Today</a>
      <a href="?date=week{'&status=' + filter_status if filter_status else ''}" class="filter-btn {'active' if filter_date == 'week' else ''}">This Week</a>
      <a href="?date=upcoming{'&status=' + filter_status if filter_status else ''}" class="filter-btn {'active' if filter_date == 'upcoming' else ''}">Upcoming</a>
      <a href="?{'status=' + filter_status if filter_status else ''}" class="filter-btn {'active' if not filter_date else ''}">All</a>
    </div>
  </div>

  <div class="stats">
    <div class="stat">
      <label>Pending</label>
      <div class="val" style="color:#f59e0b">{pending}</div>
    </div>
    <div class="stat">
      <label>Confirmed</label>
      <div class="val" style="color:#22c55e">{confirmed}</div>
    </div>
    <div class="stat">
      <label>Completed Today</label>
      <div class="val blue">{completed_today}</div>
    </div>
  </div>

  <div class="filter-group" style="margin-bottom:20px">
    <a href="?{'date=' + filter_date if filter_date else ''}" class="filter-btn {'active' if not filter_status else ''}">All Status</a>
    <a href="?status=pending{'&date=' + filter_date if filter_date else ''}" class="filter-btn {'active' if filter_status == 'pending' else ''}">Pending</a>
    <a href="?status=confirmed{'&date=' + filter_date if filter_date else ''}" class="filter-btn {'active' if filter_status == 'confirmed' else ''}">Confirmed</a>
    <a href="?status=completed{'&date=' + filter_date if filter_date else ''}" class="filter-btn {'active' if filter_status == 'completed' else ''}">Completed</a>
    <a href="?status=cancelled{'&date=' + filter_date if filter_date else ''}" class="filter-btn {'active' if filter_status == 'cancelled' else ''}">Cancelled</a>
  </div>

  {'<div class="booking-list">' + ''.join([f'''
  <div class="booking-card {b.get('status', 'pending')}">
    <div class="booking-info">
      <div class="booking-customer">{b.get('customer_name', 'Unknown')}</div>
      <div class="booking-service">{b.get('service_name', 'Service')} • {b.get('staff_name', 'Any Staff')}</div>
      <div class="booking-meta">
        <span>📅 {b.get('booking_date', '')} at {b.get('booking_time', '')}</span>
        <span>📞 {b.get('customer_phone', 'No phone')}</span>
        <span class="status-badge {b.get('status', 'pending')}">{b.get('status', 'pending').title()}</span>
      </div>
      {f'<div style="color:#94a3b8;font-size:12px;margin-top:8px">📝 {b.get("notes", "")}</div>' if b.get('notes') else ''}
    </div>
    <div class="booking-actions">
      {f'<button class="action-btn confirm" onclick="updateBooking(this, &quot;{b["id"]}&quot;, &quot;confirmed&quot;)">Confirm</button>' if b.get('status') == 'pending' else ''}
      {f'<button class="action-btn complete" onclick="updateBooking(this, &quot;{b["id"]}&quot;, &quot;completed&quot;)">Complete</button>' if b.get('status') == 'confirmed' else ''}
    </div>
  </div>
  ''' for b in bookings[:50]]) + '</div>' if bookings else '''<div class="empty-state">
    <div style="font-size:48px;margin-bottom:16px">📅</div>
    <h3>No appointments found</h3>
    <p style="color:#64748b;margin-top:8px">Appointments will appear here when customers book online</p>
  </div>''' if not business_id else '''<div class="empty-state">
    <div style="font-size:48px;margin-bottom:16px">🏪</div>
    <h3>Set Up Your Business First</h3>
    <p style="color:#64748b;margin-top:8px"><a href="/portal/settings" style="color:#22c55e">Configure your business</a> to start receiving appointments</p>
  </div>'''}
</div>
</div>
<script>
function updateBooking(btn, bookingId, status) {{
  btn.disabled = true;
  fetch('/api/booking/' + bookingId + '/status', {{
    method: 'POST',
    headers: {{'Content-Type': 'application/json'}},
    body: JSON.stringify({{status: status}})
  }}).then(r => r.json()).then(data => {{
    if (data.success) location.reload();
    else {{ alert('Error: ' + data.error); btn.disabled = false; }}
  }}).catch(err => {{ alert('Error updating booking'); btn.disabled = false; }});
}}
</script>
</body></html>""")


@app.route("/portal/sms")
@portal_login_required
def portal_sms():
    """Business SMS log view."""
    from modules.database import get_business_user_by_id, get_connection

    user = get_business_user_by_id(session["portal_user_id"])
    if not user:
        session.clear()
        return redirect(url_for("portal_login"))

    business_id = user.get("business_id")

    # Get SMS logs - check if this business is linked to a lead
    conn = get_connection()
    c = conn.cursor()

    # Try to find lead_id if business is linked to a lead
    lead_id = None
    if business_id:
        c.execute("SELECT lead_id FROM loyalty_businesses WHERE id = ?", (business_id,))
        row = c.fetchone()
        if row:
            lead_id = row["lead_id"]

    sms_logs = []
    if lead_id:
        c.execute("""
            SELECT ol.*, l.business_name
            FROM outreach_log ol
            JOIN leads l ON ol.lead_id = l.id
            WHERE ol.lead_id = ? AND ol.channel = 'sms'
            ORDER BY ol.sent_at DESC
            LIMIT 100
        """, (lead_id,))
        sms_logs = [dict(row) for row in c.fetchall()]

    conn.close()

    return render_template_string(f"""<!DOCTYPE html><html><head>
<title>SMS Log — Piney Digital</title>{BASE_CSS}
<style>
.sms-list{{display:flex;flex-direction:column;gap:12px}}
.sms-card{{background:#1e293b;border:1px solid #334155;border-radius:12px;padding:16px}}
.sms-card.outbound{{border-left:3px solid #22c55e}}
.sms-card.inbound{{border-left:3px solid #3b82f6}}
.sms-header{{display:flex;justify-content:space-between;margin-bottom:8px}}
.sms-direction{{font-size:11px;padding:2px 8px;border-radius:4px}}
.sms-direction.outbound{{background:#22c55e20;color:#22c55e}}
.sms-direction.inbound{{background:#3b82f620;color:#3b82f6}}
.sms-body{{color:#e2e8f0;font-size:14px;line-height:1.5}}
.sms-meta{{color:#64748b;font-size:11px;margin-top:12px}}
.status-sent{{color:#22c55e}}
.status-failed{{color:#ef4444}}
.status-received{{color:#3b82f6}}
.empty-state{{text-align:center;padding:60px 20px;background:#1e293b;border-radius:12px;border:1px solid #334155}}
</style></head><body>
<div class="layout">
<div class="sidebar">
  <div class="logo"><h1>🌲 Piney Digital</h1><p>{user['owner_name'] or 'Customer'}</p></div>
  <nav class="nav">
    <a href="/portal/dashboard">
      <span class="nav-icon">📊</span>
      <span class="nav-label">Dashboard</span>
    </a>
    <a href="/portal/calendar">
      <span class="nav-icon">📅</span>
      <span class="nav-label">Calendar</span>
    </a>
    <a href="/portal/services">
      <span class="nav-icon">✂️</span>
      <span class="nav-label">Services</span>
    </a>
    <a href="/portal/staff">
      <span class="nav-icon">👩‍💼</span>
      <span class="nav-label">Staff</span>
    </a>
    <a href="/portal/customers">
      <span class="nav-icon">👥</span>
      <span class="nav-label">Customers</span>
    </a>
    <a href="/portal/sms">
      <span class="nav-icon">💬</span>
      <span class="nav-label">SMS</span>
    </a>
    <a href="/portal/calls">
      <span class="nav-icon">📞</span>
      <span class="nav-label">Calls</span>
    </a>
    <a href="/portal/leads">
      <span class="nav-icon">🎯</span>
      <span class="nav-label">Leads</span>
    </a>
    <a href="/portal/loyalty">
      <span class="nav-icon">🎁</span>
      <span class="nav-label">Loyalty</span>
    </a>
    <a href="/portal/settings">
      <span class="nav-icon">⚙️</span>
      <span class="nav-label">Settings</span>
    </a>
  </nav>
  <div class="sidebar-footer">{user['email']} · <a href="/portal/logout">Sign out</a></div>
</div>
<div class="main">
  <div class="topbar"><h2>SMS Log</h2></div>

  <div class="stats">
    <div class="stat">
      <label>Total Sent</label>
      <div class="val blue">{len([s for s in sms_logs if s.get('direction') == 'outbound'])}</div>
    </div>
    <div class="stat">
      <label>Received</label>
      <div class="val">{len([s for s in sms_logs if s.get('direction') == 'inbound'])}</div>
    </div>
    <div class="stat">
      <label>Failed</label>
      <div class="val" style="color:#ef4444">{len([s for s in sms_logs if s.get('status') == 'failed'])}</div>
    </div>
  </div>

  {'<div class="sms-list">' + ''.join([f'''
  <div class="sms-card {s.get('direction', 'outbound')}">
    <div class="sms-header">
      <span class="sms-direction {s.get('direction', 'outbound')}">{'Outgoing' if s.get('direction') == 'outbound' else 'Incoming'}</span>
      <span class="status-{s.get('status', 'sent')}">{s.get('status', 'sent').title()}</span>
    </div>
    <div class="sms-body">{s.get('body', '') or s.get('subject', 'No content')}</div>
    <div class="sms-meta">{s.get('sent_at', '')[:19].replace('T', ' at ') if s.get('sent_at') else ''}</div>
  </div>
  ''' for s in sms_logs]) + '</div>' if sms_logs else '''<div class="empty-state">
    <div style="font-size:48px;margin-bottom:16px">💬</div>
    <h3>No SMS History</h3>
    <p style="color:#64748b;margin-top:8px">SMS messages sent to or from your business will appear here</p>
  </div>'''}
</div>
</div></body></html>""")


@app.route("/portal/calls")
@portal_login_required
def portal_calls():
    """Business call log view."""
    from modules.database import get_business_user_by_id, get_connection

    user = get_business_user_by_id(session["portal_user_id"])
    if not user:
        session.clear()
        return redirect(url_for("portal_login"))

    business_id = user.get("business_id")

    # Get call logs
    conn = get_connection()
    c = conn.cursor()

    lead_id = None
    if business_id:
        c.execute("SELECT lead_id FROM loyalty_businesses WHERE id = ?", (business_id,))
        row = c.fetchone()
        if row:
            lead_id = row["lead_id"]

    call_logs = []
    if lead_id:
        c.execute("""
            SELECT ol.*, l.business_name, l.phone
            FROM outreach_log ol
            JOIN leads l ON ol.lead_id = l.id
            WHERE ol.lead_id = ? AND ol.channel = 'call'
            ORDER BY ol.sent_at DESC
            LIMIT 100
        """, (lead_id,))
        call_logs = [dict(row) for row in c.fetchall()]

    conn.close()

    return render_template_string(f"""<!DOCTYPE html><html><head>
<title>Call Log — Piney Digital</title>{BASE_CSS}
<style>
.call-list{{display:flex;flex-direction:column;gap:12px}}
.call-card{{background:#1e293b;border:1px solid #334155;border-radius:12px;padding:16px}}
.call-card.completed{{border-left:3px solid #22c55e}}
.call-card.voicemail{{border-left:3px solid #f59e0b}}
.call-card.no_answer{{border-left:3px solid #64748b}}
.call-card.transferred{{border-left:3px solid #3b82f6}}
.call-header{{display:flex;justify-content:space-between;align-items:center;margin-bottom:12px}}
.call-business{{font-weight:600;color:#fff}}
.call-status{{font-size:11px;padding:4px 10px;border-radius:999px;font-weight:600}}
.call-status.completed{{background:#22c55e20;color:#22c55e}}
.call-status.voicemail{{background:#f59e0b20;color:#f59e0b}}
.call-status.no_answer{{background:#64748b20;color:#94a3b8}}
.call-status.transferred{{background:#3b82f620;color:#3b82f6}}
.call-transcript{{background:#0f172a;border-radius:8px;padding:12px;font-size:13px;color:#94a3b8;line-height:1.6;max-height:200px;overflow-y:auto}}
.call-meta{{display:flex;gap:20px;color:#64748b;font-size:12px;margin-top:12px}}
.empty-state{{text-align:center;padding:60px 20px;background:#1e293b;border-radius:12px;border:1px solid #334155}}
</style></head><body>
<div class="layout">
<div class="sidebar">
  <div class="logo"><h1>🌲 Piney Digital</h1><p>{user['owner_name'] or 'Customer'}</p></div>
  <nav class="nav">
    <a href="/portal/dashboard">
      <span class="nav-icon">📊</span>
      <span class="nav-label">Dashboard</span>
    </a>
    <a href="/portal/calendar">
      <span class="nav-icon">📅</span>
      <span class="nav-label">Calendar</span>
    </a>
    <a href="/portal/services">
      <span class="nav-icon">✂️</span>
      <span class="nav-label">Services</span>
    </a>
    <a href="/portal/staff">
      <span class="nav-icon">👩‍💼</span>
      <span class="nav-label">Staff</span>
    </a>
    <a href="/portal/customers">
      <span class="nav-icon">👥</span>
      <span class="nav-label">Customers</span>
    </a>
    <a href="/portal/sms">
      <span class="nav-icon">💬</span>
      <span class="nav-label">SMS</span>
    </a>
    <a href="/portal/calls">
      <span class="nav-icon">📞</span>
      <span class="nav-label">Calls</span>
    </a>
    <a href="/portal/leads">
      <span class="nav-icon">🎯</span>
      <span class="nav-label">Leads</span>
    </a>
    <a href="/portal/loyalty">
      <span class="nav-icon">🎁</span>
      <span class="nav-label">Loyalty</span>
    </a>
    <a href="/portal/settings">
      <span class="nav-icon">⚙️</span>
      <span class="nav-label">Settings</span>
    </a>
  </nav>
  <div class="sidebar-footer">{user['email']} · <a href="/portal/logout">Sign out</a></div>
</div>
<div class="main">
  <div class="topbar"><h2>Call Log</h2></div>

  <div class="stats">
    <div class="stat">
      <label>Total Calls</label>
      <div class="val blue">{len(call_logs)}</div>
    </div>
    <div class="stat">
      <label>Completed</label>
      <div class="val" style="color:#22c55e">{len([c for c in call_logs if c.get('status') == 'completed'])}</div>
    </div>
    <div class="stat">
      <label>Voicemails</label>
      <div class="val" style="color:#f59e0b">{len([c for c in call_logs if c.get('status') == 'voicemail'])}</div>
    </div>
    <div class="stat">
      <label>No Answer</label>
      <div class="val">{len([c for c in call_logs if c.get('status') == 'no_answer'])}</div>
    </div>
  </div>

  {'<div class="call-list">' + ''.join([f'''
  <div class="call-card {c.get('status', 'completed')}">
    <div class="call-header">
      <div class="call-business">{c.get('business_name', 'Unknown')}</div>
      <span class="call-status {c.get('status', 'completed')}">{c.get('status', 'completed').replace('_', ' ').title()}</span>
    </div>
    {f'<div class="call-transcript">{c.get("transcript", "No transcript available")}</div>' if c.get('transcript') else '<div style="color:#64748b;font-size:13px">No transcript available</div>'}
    <div class="call-meta">
      <span>📞 {c.get('phone', 'N/A')}</span>
      <span>⏱️ {c.get('duration', 0)}s</span>
      <span>📅 {c.get('sent_at', '')[:19].replace('T', ' at ') if c.get('sent_at') else ''}</span>
    </div>
  </div>
  ''' for c in call_logs]) + '</div>' if call_logs else '''<div class="empty-state">
    <div style="font-size:48px;margin-bottom:16px">📞</div>
    <h3>No Call History</h3>
    <p style="color:#64748b;margin-top:8px">AI calls made on behalf of your business will appear here</p>
  </div>'''}
</div>
</div></body></html>""")


@app.route("/portal/leads")
@portal_login_required
def portal_leads():
    """Business leads overview - read-only view of lead activity."""
    from modules.database import get_business_user_by_id, get_connection

    user = get_business_user_by_id(session["portal_user_id"])
    if not user:
        session.clear()
        return redirect(url_for("portal_login"))

    business_id = user.get("business_id")

    # Get lead data if this business is linked to a lead
    conn = get_connection()
    c = conn.cursor()

    lead_id = None
    lead_data = None
    if business_id:
        c.execute("SELECT lead_id FROM loyalty_businesses WHERE id = ?", (business_id,))
        row = c.fetchone()
        if row:
            lead_id = row["lead_id"]

    if lead_id:
        c.execute("SELECT * FROM leads WHERE id = ?", (lead_id,))
        lead_data = c.fetchone()

    # Get outreach stats for this lead
    outreach_stats = {"emails": 0, "sms": 0, "calls": 0}
    if lead_id:
        c.execute("""
            SELECT channel, COUNT(*) as cnt FROM outreach_log
            WHERE lead_id = ? GROUP BY channel
        """, (lead_id,))
        for row in c.fetchall():
            if row["channel"] == "email":
                outreach_stats["emails"] = row["cnt"]
            elif row["channel"] == "sms":
                outreach_stats["sms"] = row["cnt"]
            elif row["channel"] == "call":
                outreach_stats["calls"] = row["cnt"]

    conn.close()

    return render_template_string(f"""<!DOCTYPE html><html><head>
<title>Leads — Piney Digital</title>{BASE_CSS}
<style>
.lead-card{{background:#1e293b;border:1px solid #334155;border-radius:12px;padding:24px;margin-bottom:20px}}
.lead-header{{display:flex;justify-content:space-between;align-items:center;margin-bottom:20px}}
.lead-name{{font-size:20px;font-weight:600;color:#fff}}
.lead-status{{font-size:12px;padding:4px 12px;border-radius:999px;font-weight:600}}
.lead-status.new{{background:#3b82f620;color:#3b82f6}}
.lead-status.sent{{background:#f59e0b20;color:#f59e0b}}
.lead-status.replied{{background:#22c55e20;color:#22c55e}}
.lead-status.booked{{background:#22c55e;color:#fff}}
.lead-status.dead{{background:#64748b20;color:#94a3b8}}
.lead-info{{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:16px}}
.info-item{{}}
.info-label{{font-size:11px;color:#64748b;margin-bottom:4px}}
.info-value{{color:#e2e8f0;font-size:14px}}
.outreach-stats{{display:grid;grid-template-columns:repeat(3,1fr);gap:16px;margin-top:20px;padding-top:20px;border-top:1px solid #334155}}
.stat-item{{text-align:center;padding:16px;background:#0f172a;border-radius:8px}}
.stat-item .icon{{font-size:24px;margin-bottom:8px}}
.stat-item .count{{font-size:24px;font-weight:700;color:#fff}}
.stat-item .label{{font-size:11px;color:#64748b;margin-top:4px}}
.empty-state{{text-align:center;padding:60px 20px;background:#1e293b;border-radius:12px;border:1px solid #334155}}
.timeline{{margin-top:20px;padding-top:20px;border-top:1px solid #334155}}
.timeline-title{{font-size:14px;font-weight:600;margin-bottom:16px;color:#94a3b8}}
.timeline-item{{display:flex;gap:12px;padding:12px 0;border-bottom:1px solid #334155}}
.timeline-date{{color:#64748b;font-size:12px;min-width:140px}}
.timeline-action{{color:#e2e8f0;font-size:13px}}
</style></head><body>
<div class="layout">
<div class="sidebar">
  <div class="logo"><h1>🌲 Piney Digital</h1><p>{user['owner_name'] or 'Customer'}</p></div>
  <nav class="nav">
    <a href="/portal/dashboard">
      <span class="nav-icon">📊</span>
      <span class="nav-label">Dashboard</span>
    </a>
    <a href="/portal/calendar">
      <span class="nav-icon">📅</span>
      <span class="nav-label">Calendar</span>
    </a>
    <a href="/portal/services">
      <span class="nav-icon">✂️</span>
      <span class="nav-label">Services</span>
    </a>
    <a href="/portal/staff">
      <span class="nav-icon">👩‍💼</span>
      <span class="nav-label">Staff</span>
    </a>
    <a href="/portal/customers">
      <span class="nav-icon">👥</span>
      <span class="nav-label">Customers</span>
    </a>
    <a href="/portal/sms">
      <span class="nav-icon">💬</span>
      <span class="nav-label">SMS</span>
    </a>
    <a href="/portal/calls">
      <span class="nav-icon">📞</span>
      <span class="nav-label">Calls</span>
    </a>
    <a href="/portal/leads">
      <span class="nav-icon">🎯</span>
      <span class="nav-label">Leads</span>
    </a>
    <a href="/portal/loyalty">
      <span class="nav-icon">🎁</span>
      <span class="nav-label">Loyalty</span>
    </a>
    <a href="/portal/settings">
      <span class="nav-icon">⚙️</span>
      <span class="nav-label">Settings</span>
    </a>
  </nav>
  <div class="sidebar-footer">{user['email']} · <a href="/portal/logout">Sign out</a></div>
</div>
<div class="main">
  <div class="topbar"><h2>Lead Information</h2></div>

  {f'''<div class="lead-card">
    <div class="lead-header">
      <div class="lead-name">{lead_data['business_name'] if lead_data else 'Unknown Business'}</div>
      <span class="lead-status {lead_data['outreach_status'] if lead_data else 'new'}">{(lead_data['outreach_status'] if lead_data else 'new').title()}</span>
    </div>
    <div class="lead-info">
      <div class="info-item">
        <div class="info-label">Category</div>
        <div class="info-value">{lead_data['category'] if lead_data and lead_data.get('category') else 'N/A'}</div>
      </div>
      <div class="info-item">
        <div class="info-label">Location</div>
        <div class="info-value">{lead_data['city'] if lead_data and lead_data.get('city') else 'N/A'}</div>
      </div>
      <div class="info-item">
        <div class="info-label">Phone</div>
        <div class="info-value">{lead_data['phone'] if lead_data and lead_data.get('phone') else 'N/A'}</div>
      </div>
      <div class="info-item">
        <div class="info-label">Website Status</div>
        <div class="info-value">{lead_data['site_status'] if lead_data and lead_data.get('site_status') else 'Unknown'}</div>
      </div>
      <div class="info-item">
        <div class="info-label">Lead Score</div>
        <div class="info-value">{lead_data['lead_score'] if lead_data and lead_data.get('lead_score') else 0}</div>
      </div>
      <div class="info-item">
        <div class="info-label">Added</div>
        <div class="info-value">{lead_data['scraped_at'][:10] if lead_data and lead_data.get('scraped_at') else 'N/A'}</div>
      </div>
    </div>
    <div class="outreach-stats">
      <div class="stat-item">
        <div class="icon">📧</div>
        <div class="count">{outreach_stats['emails']}</div>
        <div class="label">Emails</div>
      </div>
      <div class="stat-item">
        <div class="icon">💬</div>
        <div class="count">{outreach_stats['sms']}</div>
        <div class="label">SMS</div>
      </div>
      <div class="stat-item">
        <div class="icon">📞</div>
        <div class="count">{outreach_stats['calls']}</div>
        <div class="label">Calls</div>
      </div>
    </div>
  </div>
  <div style="background:#1e293b;border:1px solid #334155;border-radius:12px;padding:16px;color:#64748b;font-size:13px">
    <strong style="color:#94a3b8">Note:</strong> This shows the lead data that was used to find you. Piney Digital manages all outreach on your behalf.
  </div>
  ''' if lead_data else '''<div class="empty-state">
    <div style="font-size:48px;margin-bottom:16px">🎯</div>
    <h3>No Lead Data Available</h3>
    <p style="color:#64748b;margin-top:8px">Lead information is available for businesses that were contacted through our outreach program.</p>
    <p style="color:#64748b;margin-top:8px">If you signed up directly, there may not be associated lead data.</p>
  </div>'''}
</div>
</div></body></html>""")


# API endpoint for booking status updates
@app.route("/api/booking/<booking_id>/status", methods=["POST"])
def update_booking_status(booking_id):
    """Update booking status via API."""
    from modules.bookings_db import get_booking, confirm_booking, cancel_booking, complete_booking

    data = request.get_json() or {}
    new_status = data.get("status")

    if new_status not in ["pending", "confirmed", "completed", "cancelled"]:
        return jsonify({"success": False, "error": "Invalid status"}), 400

    booking = get_booking(booking_id)
    if not booking:
        return jsonify({"success": False, "error": "Booking not found"}), 404

    if new_status == "confirmed":
        confirm_booking(booking_id)
    elif new_status == "completed":
        complete_booking(booking_id)
    elif new_status == "cancelled":
        cancel_booking(booking_id)

    return jsonify({"success": True})


# ═══════════════════════════════════════════════════════════════
# CUSTOMER APP - Browse Businesses & Manage Loyalty Cards
# ═══════════════════════════════════════════════════════════════

@app.route("/app")
def customer_app_landing():
    """Unified customer app with tabs - Discover and My Cards."""
    from modules.loyalty_db import get_all_loyalty_businesses, get_customer_cards, get_customer
    from modules.referrals_db import get_customer_referral_codes

    # Get active tab (default: discover)
    active_tab = request.args.get('tab', 'discover')
    search = request.args.get('search', '').lower()

    # Check if customer is logged in
    customer_id = session.get('customer_id')
    is_logged_in = bool(customer_id)

    # Get businesses for discover tab
    businesses = get_all_loyalty_businesses()
    if search:
        businesses = [b for b in businesses if
                    search in b.get('name', '').lower() or
                    search in b.get('city', '').lower() or
                    search in b.get('type', '').lower()]

    # Build tab navigation
    discover_active = 'active' if active_tab == 'discover' else ''
    cards_active = 'active' if active_tab == 'cards' else ''

    tab_nav = f'''
<div class="tab-bar">
  <a href="/app?tab=discover" class="tab-btn {discover_active}">Discover</a>
  <a href="/app?tab=cards" class="tab-btn {cards_active}">My Cards</a>
  <a href="/app/logout" class="tab-btn logout">Sign Out</a>
</div>'''

    # Guest header (not logged in)
    guest_header = f'''
<div class="tab-bar guest">
  <a href="/app?tab=discover" class="tab-btn {discover_active}">Discover</a>
  <a href="/app/login" class="tab-btn login">Sign In</a>
</div>'''

    # Build content based on tab
    if active_tab == 'cards' and is_logged_in:
        # My Cards Tab
        cards = get_customer_cards(customer_id)
        referral_codes = get_customer_referral_codes(customer_id)
        customer_name = session.get('customer_name', 'there')

        # Cards grid
        cards_html = ""
        if cards:
            card_items = []
            for card in cards:
                punches = card.get('punches', 0)
                needed = card.get('punches_needed', 5)
                punch_dots = ''.join([f'<div class="punch {"filled" if i < punches else ""}"></div>' for i in range(needed)])
                card_items.append(f'''
        <div class="loyalty-card">
          <div class="card-biz">{card.get('business_name', 'Business')}</div>
          <div class="card-punches">{punch_dots}</div>
          <div class="card-progress">{punches}/{needed} punches - {card.get('discount_percent', 15)}% reward</div>
          <a href="/app/card/{card.get('id')}" class="card-btn">Show QR Code</a>
        </div>''')
            cards_html = f'<div class="cards-grid">{"".join(card_items)}</div>'
        else:
            cards_html = '''
        <div class="empty-state">
          <h3>No loyalty cards yet</h3>
          <p>Join a business to start earning rewards!</p>
          <a href="/app?tab=discover" class="btn-primary">Discover Businesses</a>
        </div>'''

        # Referral section
        referral_html = ""
        if referral_codes:
            ref_items = []
            for rc in referral_codes:
                ref_items.append(f'''
          <div class="ref-item">
            <div class="ref-biz">{rc.get("business_name", "Business")}</div>
            <div class="ref-code">{rc.get("code", "N/A")}</div>
            <div class="ref-link-text">pineydigital.com/ref/{rc.get("business_id", "")}/{rc.get("code", "")}</div>
          </div>''')
            referral_html = f'''
      <div class="referral-section">
        <h3>Share and Earn</h3>
        <p>Share your referral codes. When friends join, you both get rewards!</p>
        <div class="ref-grid">{"".join(ref_items)}</div>
      </div>'''

        main_content = f'''
    <div class="tab-content">
      <p class="greeting">Hi, {customer_name}! Here are your loyalty cards:</p>
      {cards_html}
      {referral_html}
    </div>'''

    else:
        # Discover Tab (default)
        biz_cards = []
        for b in businesses:
            biz_cards.append(f'''
      <div class="biz-card">
        <div class="name">{b['name']}</div>
        <div class="type">{b.get('type', 'Local Business')}</div>
        {f'<div class="desc">{b.get("description", "")[:80]}...</div>' if b.get('description') else ''}
        <div class="reward">Gift: {b.get('punches_needed', 5)} punches = {b.get('discount_percent', 15)}% off</div>
        <div class="city">{b.get('city', 'East Texas')}</div>
        <a href="/app/join/{b['id']}" class="join-btn">Join Program</a>
      </div>''')

        businesses_grid = ''.join(biz_cards) if businesses else '<div class="empty">No businesses found</div>'

        login_prompt = ""
        if not is_logged_in:
            login_prompt = '''
      <div class="login-prompt">
        <h3>Create Your Free Account</h3>
        <p>Access all your loyalty cards in one place</p>
        <a href="/app/signup" class="btn-primary">Sign Up Free</a>
        <p style="margin-top:16px;font-size:13px;color:#64748b">
          Already have an account? <a href="/app/login" style="color:#3b82f6">Sign In</a>
        </p>
      </div>'''

        main_content = f'''
    <div class="tab-content">
      <div class="search-box">
        <form method="GET" style="display:flex;flex:1;gap:12px">
          <input type="hidden" name="tab" value="discover">
          <input type="text" name="search" placeholder="Search by name, city, or type..." value="{search}">
          <button type="submit">Search</button>
        </form>
      </div>

      {f'<div class="results-count">Showing {len(businesses)} business{"es" if len(businesses) != 1 else ""}</div>' if search else '<div class="section-title">Discover Local Businesses</div>'}

      <div class="biz-grid">
        {businesses_grid}
      </div>

      {login_prompt}
    </div>'''

    header_nav = tab_nav if is_logged_in else guest_header

    return render_template_string(f"""<!DOCTYPE html><html><head>
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1,user-scalable=no">
<title>Piney Rewards</title>
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;
     background:#0a0f1a;color:#e2e8f0;min-height:100vh}}
.app-header{{background:linear-gradient(135deg,#1a3a2a 0%,#0a1628 100%);
             padding:30px 20px 50px;text-align:center}}
.app-header h1{{font-size:28px;font-weight:700;margin-bottom:8px}}
.app-header h1 span{{color:#22c55e}}
.app-header p{{color:#94a3b8;font-size:14px}}
.tab-bar{{display:flex;justify-content:center;gap:8px;padding:16px;background:#1e293b;border-bottom:1px solid #334155}}
.tab-btn{{background:#334155;color:#94a3b8;padding:10px 20px;border-radius:8px;font-size:14px;text-decoration:none;font-weight:500}}
.tab-btn:hover{{background:#475569}}
.tab-btn.active{{background:#22c55e;color:#fff}}
.tab-btn.logout{{background:transparent;color:#ef4444}}
.tab-btn.login{{background:#22c55e;color:#fff}}
.tab-content{{max-width:800px;margin:0 auto;padding:20px}}
.greeting{{color:#94a3b8;font-size:14px;margin-bottom:20px}}
.search-box{{max-width:500px;margin:0 auto 20px;background:#1e293b;border-radius:12px;padding:8px 16px;
             display:flex;align-items:center;gap:12px;border:1px solid #334155}}
.search-box input{{flex:1;background:transparent;border:none;color:#e2e8f0;font-size:15px;outline:none}}
.search-box input::placeholder{{color:#64748b}}
.search-box button{{background:#334155;border:none;color:#94a3b8;padding:8px 12px;border-radius:6px;cursor:pointer}}
.section-title{{font-size:18px;font-weight:600;margin-bottom:20px}}
.results-count{{color:#64748b;font-size:13px;margin-bottom:16px}}
.cards-grid{{display:grid;gap:16px}}
.loyalty-card{{background:linear-gradient(135deg,#1a3a2a 0%,#0f1f1a 100%);border-radius:16px;padding:20px;border:1px solid #22c55e}}
.card-biz{{font-size:18px;font-weight:600;margin-bottom:12px}}
.card-punches{{display:flex;gap:6px;margin-bottom:12px}}
.punch{{width:24px;height:24px;border-radius:50%;background:#334155}}
.punch.filled{{background:#22c55e}}
.card-progress{{font-size:13px;color:#94a3b8;margin-bottom:12px}}
.card-btn{{display:inline-block;background:#22c55e;color:#fff;padding:10px 20px;border-radius:8px;text-decoration:none;font-weight:600}}
.empty-state{{text-align:center;padding:40px 20px;background:#1e293b;border-radius:16px;border:1px solid #334155}}
.empty-state h3{{font-size:16px;margin-bottom:8px}}
.empty-state p{{color:#64748b;font-size:13px;margin-bottom:16px}}
.referral-section{{background:#1e293b;border-radius:16px;padding:20px;margin-top:24px;border:1px solid #334155}}
.referral-section h3{{font-size:16px;font-weight:600;color:#22c55e;margin-bottom:8px}}
.referral-section p{{color:#64748b;font-size:13px;margin-bottom:16px}}
.ref-grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(200px,1fr));gap:12px}}
.ref-item{{background:#0f172a;border-radius:8px;padding:16px}}
.ref-biz{{font-size:12px;color:#94a3b8}}
.ref-code{{font-size:20px;font-weight:700;color:#fff;font-family:monospace;letter-spacing:2px;margin:8px 0}}
.ref-link-text{{font-size:11px;color:#64748b;word-break:break-all}}
.biz-grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(250px,1fr));gap:16px}}
.biz-card{{background:#1e293b;border:1px solid #334155;border-radius:12px;padding:20px;transition:all .2s}}
.biz-card:hover{{transform:translateY(-2px);box-shadow:0 8px 24px rgba(0,0,0,0.3)}}
.biz-card .name{{font-size:16px;font-weight:600;color:#fff;margin-bottom:4px}}
.biz-card .type{{font-size:12px;color:#64748b;margin-bottom:8px}}
.biz-card .desc{{color:#94a3b8;font-size:13px;margin:8px 0}}
.biz-card .reward{{display:inline-block;background:rgba(34,197,94,0.1);color:#22c55e;
                   padding:4px 10px;border-radius:999px;font-size:12px}}
.biz-card .city{{font-size:12px;color:#64748b;margin:8px 0}}
.join-btn{{display:inline-block;background:#22c55e;color:#fff;padding:8px 16px;
           border-radius:6px;font-size:13px;text-decoration:none;font-weight:600}}
.join-btn:hover{{background:#16a34a}}
.empty{{text-align:center;padding:40px 20px;color:#64748b}}
.login-prompt{{text-align:center;padding:40px 20px;background:#1e293b;
               border-radius:12px;border:1px solid #334155;margin-top:30px}}
.login-prompt h3{{font-size:18px;margin-bottom:8px}}
.login-prompt p{{color:#64748b;margin-bottom:20px}}
.btn-primary{{display:inline-block;background:linear-gradient(135deg,#22c55e,#16a34a);
              color:#fff;padding:12px 24px;border-radius:8px;text-decoration:none;font-weight:600}}
</style>
</head><body>

<div class="app-header">
  <h1>Piney <span>Rewards</span></h1>
  <p>One app for all your local loyalty cards</p>
</div>

{header_nav}

{main_content}

</body></html>""")


@app.route("/app/signup", methods=["GET", "POST"])
def customer_app_signup():
    """Customer signup for the rewards app."""
    from modules.loyalty_db import create_customer, get_customer_by_phone, get_customer_by_email
    from modules.auth_security import validate_password, hash_password, is_valid_email

    error = ""

    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip().lower()
        phone = request.form.get("phone", "").strip()
        password = request.form.get("password", "")
        agree = request.form.get("agree")

        # Validate
        if not name or len(name) < 2:
            error = "Please enter your name"
        elif not is_valid_email(email):
            error = "Please enter a valid email"
        elif get_customer_by_email(email):
            error = "An account with this email already exists"
        elif phone and get_customer_by_phone(phone):
            error = "An account with this phone already exists"
        elif not agree:
            error = "You must agree to the Terms and Privacy Policy"
        else:
            valid, msg = validate_password(password)
            if not valid:
                error = msg

        if not error:
            # Create customer account
            password_hash = hash_password(password)
            create_customer(name, email, phone or None, password_hash)

            # Log them in
            customer = get_customer_by_email(email)
            session["customer_id"] = customer["id"]
            session["customer_name"] = name
            session["customer_email"] = email

            return redirect(url_for("customer_app_dashboard"))

    return render_template_string(f"""<!DOCTYPE html><html><head>
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1,user-scalable=no">
<title>Sign Up — Piney Rewards</title>
{PORTAL_CSS}
</head><body>
<div class="portal-wrap">
  <div class="portal-logo">
    <h1>🌲 <span style="color:#22c55e">Piney Rewards</span></h1>
    <p>Create your free account</p>
  </div>
  <div class="portal-card">
    <h2>Join the Network</h2>
    <p style="color:#64748b;text-align:center;margin-bottom:20px;font-size:13px">
      One account for all local loyalty programs
    </p>

    <form method="POST">
      <div class="form-group">
        <label>Your Name</label>
        <input type="text" name="name" placeholder="Jane Smith" required
               value="{request.form.get('name', '')}">
      </div>

      <div class="form-group">
        <label>Email Address</label>
        <input type="email" name="email" placeholder="you@email.com" required
               value="{request.form.get('email', '')}">
      </div>

      <div class="form-group">
        <label>Phone (optional)</label>
        <input type="tel" name="phone" placeholder="(936) 123-4567"
               value="{request.form.get('phone', '')}">
        <div class="password-hint">For SMS notifications about rewards</div>
      </div>

      <div class="form-group">
        <label>Password</label>
        <input type="password" name="password" placeholder="Create a password" required>
        <div class="password-hint">Min 8 characters with uppercase, lowercase, and number</div>
      </div>

      {f'<p class="err">{error}</p>' if error else ''}

      <div style="margin-top:16px">
        <label style="display:flex;align-items:flex-start;gap:10px;cursor:pointer;font-size:12px;color:#94a3b8">
          <input type="checkbox" name="agree" required style="margin-top:3px">
          <span>I agree to the <a href="/terms" style="color:#3b82f6">Terms of Service</a> and <a href="/privacy" style="color:#3b82f6">Privacy Policy</a></span>
        </label>
      </div>

      <button type="submit" class="btn-primary">Create Free Account</button>
    </form>

    <div class="divider"><span>or</span></div>
    <a href="/app/login"><button type="button" class="btn-secondary">Already have an account?</button></a>
  </div>
</div>
</body></html>""")


@app.route("/app/login", methods=["GET", "POST"])
def customer_app_login():
    """Customer login for the rewards app."""
    from modules.loyalty_db import get_customer_by_email
    from modules.auth_security import verify_password

    error = ""

    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")

        customer = get_customer_by_email(email)
        if customer and verify_password(password, customer.get("password_hash", "")):
            session["customer_id"] = customer["id"]
            session["customer_name"] = customer["name"]
            session["customer_email"] = email
            return redirect(url_for("customer_app_dashboard"))
        else:
            error = "Invalid email or password"

    return render_template_string(f"""<!DOCTYPE html><html><head>
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1,user-scalable=no">
<title>Sign In — Piney Rewards</title>
{PORTAL_CSS}
</head><body>
<div class="portal-wrap">
  <div class="portal-logo">
    <h1>🌲 <span style="color:#22c55e">Piney Rewards</span></h1>
    <p>Welcome back!</p>
  </div>
  <div class="portal-card">
    <h2>Sign In</h2>
    <form method="POST">
      <div class="form-group">
        <label>Email Address</label>
        <input type="email" name="email" placeholder="you@email.com" required>
      </div>
      <div class="form-group">
        <label>Password</label>
        <input type="password" name="password" placeholder="Your password" required>
      </div>
      {f'<p class="err">{error}</p>' if error else ''}
      <button type="submit" class="btn-primary">Sign In</button>
    </form>
    <div class="divider"><span>or</span></div>
    <a href="/app/signup"><button type="button" class="btn-secondary">Create free account</button></a>
  </div>
</div>
</body></html>""")


def customer_login_required(f):
    """Decorator for customer app login."""
    from functools import wraps
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("customer_id"):
            return redirect(url_for("customer_app_login"))
        return f(*args, **kwargs)
    return decorated


@app.route("/app/dashboard")
def customer_app_dashboard_redirect():
    """Redirect to unified app with cards tab."""
    return redirect("/app?tab=cards")


@app.route("/app/card/<card_id>")
@customer_login_required
def customer_app_card(card_id):
    """Show QR code for a specific loyalty card."""
    from modules.loyalty_db import get_customer_cards
    from modules.loyalty_api import generate_qr_code

    cards = get_customer_cards(session["customer_id"])
    card = next((c for c in cards if str(c.get("id")) == str(card_id)), None)

    if not card:
        return "Card not found", 404

    qr_code = generate_qr_code(f"PUNCH:{card.get('id')}:{session['customer_id']}")

    return render_template_string(f"""<!DOCTYPE html><html><head>
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1,user-scalable=no">
<title>Show Card — Piney Rewards</title>
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#0a0f1a;color:#e2e8f0;min-height:100vh}}
.card-screen{{padding:40px 20px;text-align:center;max-width:400px;margin:0 auto}}
.biz-name{{font-size:24px;font-weight:700;margin-bottom:8px}}
.biz-type{{color:#64748b;font-size:14px;margin-bottom:32px}}
.qr-box{{background:#fff;padding:24px;border-radius:16px;display:inline-block;margin-bottom:24px}}
.qr-box img{{width:200px;height:200px}}
.instructions{{color:#94a3b8;font-size:13px;margin-bottom:32px}}
.back-btn{{display:inline-block;background:#334155;color:#94a3b8;padding:12px 24px;border-radius:8px;text-decoration:none}}
</style>
</head><body>

<div class="card-screen">
  <div class="biz-name">{card.get('business_name', 'Business')}</div>
  <div class="biz-type">{card.get('punches', 0)}/{card.get('punches_needed', 5)} punches earned</div>

  <div class="qr-box">
    <img src="data:image/png;base64,{qr_code}" alt="QR Code">
  </div>

  <div class="instructions">
    Show this QR code at checkout to earn your punch!
  </div>

  <a href="/app?tab=cards" class="back-btn">Back to my cards</a>
</div>

</body></html>""")


@app.route("/app/join/<biz_id>")
def customer_app_join_business(biz_id):
    """Join a business's loyalty program."""
    from modules.loyalty_db import get_loyalty_business, get_or_create_customer_card, get_customer

    biz = get_loyalty_business(biz_id)
    if not biz:
        return "Business not found", 404

    # If not logged in, redirect to signup with business context
    if not session.get("customer_id"):
        session["join_business_id"] = biz_id
        return redirect(url_for("customer_app_signup"))

    # Create card for this business
    customer_id = session["customer_id"]
    customer = get_customer(customer_id)
    card = get_or_create_customer_card(customer_id, biz_id)

    return render_template_string(f"""<!DOCTYPE html><html><head>
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1,user-scalable=no">
<title>Joined! — Piney Rewards</title>
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#0a0f1a;color:#e2e8f0;min-height:100vh}}
.success-screen{{padding:60px 20px;text-align:center;max-width:400px;margin:0 auto}}
.check{{font-size:64px;margin-bottom:24px}}
.biz-name{{font-size:28px;font-weight:700;margin-bottom:8px}}
.biz-city{{color:#64748b;font-size:14px;margin-bottom:32px}}
.reward-info{{background:#1e293b;padding:20px;border-radius:12px;margin-bottom:32px}}
.reward-info .reward{{color:#22c55e;font-size:18px;font-weight:600}}
.reward-info .detail{{color:#94a3b8;font-size:13px;margin-top:8px}}
.btn-primary{{display:inline-block;background:linear-gradient(135deg,#22c55e,#16a34a);color:#fff;padding:14px 28px;border-radius:8px;text-decoration:none;font-weight:600}}
</style>
</head><body>

<div class="success-screen">
  <div class="check">✅</div>
  <div class="biz-name">{biz['name']}</div>
  <div class="biz-city">📍 {biz.get('city', 'East Texas')}</div>

  <div class="reward-info">
    <div class="reward">You're enrolled!</div>
    <div class="detail">Get {biz.get('punches_needed', 5)} punches for {biz.get('discount_percent', 15)}% off your next visit</div>
  </div>

  <a href="/app/dashboard" class="btn-primary">View My Cards →</a>
</div>

</body></html>""")


@app.route("/app/logout")
def customer_app_logout():
    """Logout from customer app."""
    session.pop("customer_id", None)
    session.pop("customer_name", None)
    session.pop("customer_email", None)
    session.pop("join_business_id", None)
    return redirect(url_for("customer_app_landing"))


# ── Loyalty Landing & Auth ─────────────────────────────────

LOYALTY_LANDING_CSS = """
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;
     background:linear-gradient(135deg,#0f172a 0%,#1e293b 100%);
     min-height:100vh;color:#e2e8f0}
.landing-wrap{display:flex;flex-direction:column;align-items:center;
              padding:40px 20px;min-height:100vh}
.landing-header{text-align:center;margin-bottom:40px}
.landing-header h1{font-size:32px;font-weight:700;color:#fff;
                   margin-bottom:8px}
.landing-header p{font-size:16px;color:#94a3b8}
.landing-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));
              gap:24px;max-width:1000px;width:100%}
.landing-card{background:#1e293b;border:1px solid #334155;
              border-radius:12px;padding:28px;text-align:center;
              transition:transform .2s,box-shadow .2s}
.landing-card:hover{transform:translateY(-4px);
                    box-shadow:0 12px 24px rgba(0,0,0,0.3)}
.landing-card h3{font-size:18px;font-weight:600;color:#f1f5f9;
                 margin-bottom:8px}
.landing-card p{font-size:13px;color:#64748b;margin-bottom:20px;
                line-height:1.5}
.landing-icon{width:56px;height:56px;border-radius:12px;
              display:flex;align-items:center;justify-content:center;
              font-size:24px;margin:0 auto 16px}
.landing-icon.admin{background:#534AB7}
.landing-icon.business{background:#1D9E75}
.landing-icon.customer{background:#D85A30}
.btn-landing{display:inline-block;width:100%;padding:10px 16px;
             border-radius:8px;font-size:14px;font-weight:500;
             text-decoration:none;cursor:pointer;border:none}
.btn-admin{background:#534AB7;color:#EEEDFE}
.btn-business{background:#1D9E75;color:#fff}
.btn-customer{background:#D85A30;color:#fff}
.login-modal{display:none;position:fixed;top:0;left:0;width:100%;
             height:100%;background:rgba(0,0,0,0.8);
             align-items:center;justify-content:center;z-index:1000}
.login-modal.active{display:flex}
.login-box{background:#1e293b;border-radius:12px;padding:32px;
           width:100%;max-width:380px}
.login-box h2{font-size:20px;font-weight:600;color:#fff;
              margin-bottom:8px;text-align:center}
.login-box p{font-size:13px;color:#64748b;text-align:center;
             margin-bottom:24px}
.login-box input{width:100%;background:#0f172a;border:1px solid #334155;
  color:#e2e8f0;padding:12px 14px;border-radius:8px;font-size:14px;
  margin-bottom:12px}
.login-box button{width:100%;padding:12px;border-radius:8px;
  font-size:14px;font-weight:500;cursor:pointer;border:none}
.close-modal{position:absolute;top:20px;right:20px;font-size:24px;
             color:#64748b;cursor:pointer}
.err{color:#f87171;font-size:12px;margin-top:8px;text-align:center}
.back-link{display:block;text-align:center;margin-top:16px;
           font-size:13px;color:#64748b;text-decoration:none}
.back-link:hover{color:#94a3b8}

/* Mobile */
@media (max-width: 768px) {
  .landing-grid{grid-template-columns:1fr;gap:16px}
  .landing-header h1{font-size:24px}
  .landing-card{padding:20px}
}
</style>
"""


@app.route("/loyalty-landing")
def loyalty_landing():
    """Public landing page for loyalty program."""
    return render_template_string(f"""<!DOCTYPE html><html><head>
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1,user-scalable=no">
<title>LoyaltyLoop — Piney Digital</title>{LOYALTY_LANDING_CSS}</head><body>
<div class="landing-wrap">
  <div class="landing-header">
    <h1>🌲 LoyaltyLoop</h1>
    <p>Digital Loyalty Platform by Piney Digital</p>
  </div>
  <div class="landing-grid">
    <div class="landing-card">
      <div class="landing-icon admin">⚙️</div>
      <h3>Admin</h3>
      <p>Manage all loyalty programs, view analytics, and oversee businesses.</p>
      <a href="/login" class="btn-landing btn-admin">Dashboard Login</a>
    </div>
    <div class="landing-card">
      <div class="landing-icon business">🏪</div>
      <h3>Business Owner</h3>
      <p>Manage your loyalty program, scan QR codes, and track customer rewards.</p>
      <div style="display:flex;gap:8px;margin-top:16px">
        <a href="/portal/login" class="btn-landing btn-business" style="flex:1;text-align:center;text-decoration:none">Sign in</a>
        <a href="/portal/signup" class="btn-landing btn-business" style="flex:1;text-align:center;text-decoration:none;opacity:.9">Sign up</a>
      </div>
    </div>
    <div class="landing-card">
      <div class="landing-icon customer">🎯</div>
      <h3>Customer</h3>
      <p>View your loyalty cards, track punches, and redeem rewards.</p>
      <div style="display:flex;gap:8px;margin-top:16px">
        <a href="/app/login" class="btn-landing btn-customer" style="flex:1;text-align:center;text-decoration:none">Sign in</a>
        <a href="/app/signup" class="btn-landing btn-customer" style="flex:1;text-align:center;text-decoration:none;opacity:.9">Sign up</a>
      </div>
    </div>
  </div>
</div>
</body></html>""")


@app.route("/loyalty/business/login", methods=["POST"])
def loyalty_business_login():
    """Handle business owner login - redirect to portal."""
    email = request.form.get("email")
    password = request.form.get("password")

    user = authenticate_business(email, password)
    if user:
        session["portal_user_id"] = user["id"]
        return redirect(url_for("portal_dashboard"))

    return redirect(url_for("portal_login") + "?error=Invalid credentials")


@app.route("/loyalty/business/signup", methods=["POST"])
def loyalty_business_signup():
    """Handle business owner signup - redirect to portal."""
    from modules.loyalty_auth import create_business_account_with_signup
    from modules.database import create_business_user

    name = request.form.get("business_name")
    business_type = request.form.get("business_type")
    city = request.form.get("city")
    email = request.form.get("email")
    password = request.form.get("password")
    punches = int(request.form.get("punches", 5))
    discount = int(request.form.get("discount", 15))

    result = create_business_account_with_signup(
        name=name,
        business_type=business_type,
        city=city,
        email=email,
        password=password,
        punches=punches,
        discount=discount
    )

    if result["success"]:
        # Create portal user and log them in
        from modules.auth_security import hash_password
        user_id = create_business_user(email, hash_password(password), name)
        session["portal_user_id"] = user_id
        return redirect(url_for("portal_settings"))

    return redirect(url_for("portal_signup") + "?error=Failed to create account")


@app.route("/loyalty/customer/login", methods=["POST"])
def loyalty_customer_login():
    """Handle customer login - redirect to customer app."""
    email = request.form.get("email")
    phone = request.form.get("phone")
    password = request.form.get("password")

    user = authenticate_customer(email=email or None, phone=phone or None, password=password)
    if user:
        session["customer_id"] = user["customer_id"]
        session["customer_name"] = user.get("customer_name", "Customer")
        session["customer_email"] = user.get("email", "")
        return redirect(url_for("customer_app_dashboard"))

    return redirect(url_for("customer_app_login") + "?error=Invalid credentials")


@app.route("/loyalty/customer/signup", methods=["POST"])
def loyalty_customer_signup():
    """Handle customer signup - redirect to customer app."""
    from modules.loyalty_auth import create_customer_account_with_signup

    name = request.form.get("name")
    email = request.form.get("email")
    phone = request.form.get("phone")
    password = request.form.get("password")

    result = create_customer_account_with_signup(
        name=name,
        email=email,
        phone=phone,
        password=password
    )

    if result["success"]:
        session["customer_id"] = result["customer_id"]
        session["customer_name"] = name
        session["customer_email"] = email or ""
        return redirect(url_for("customer_app_dashboard"))

    return redirect(url_for("customer_app_signup") + "?error=Failed to create account")


@app.route("/loyalty/logout")
def loyalty_logout():
    """Logout from loyalty system."""
    session.pop("loyalty_user", None)
    return redirect(url_for("loyalty_landing"))


# ── Run ────────────────────────────────────────────────────


@app.route("/logout")
def logout():
    """Admin logout - clear session and log audit event."""
    from modules.database import delete_admin_session, log_audit_event

    session_token = session.get("session_token")
    if session_token:
        delete_admin_session(session_token)

    ip = request.headers.get("X-Forwarded-For", request.remote_addr)
    log_audit_event("logout", user_type="admin", ip_address=ip)

    session.clear()
    return redirect(url_for("login"))


@app.route("/admin/seed-test-data")
@login_required
def seed_test_data():
    """Seed test businesses and customers for development/demo."""
    from modules.loyalty_db import create_loyalty_business, create_customer, get_all_loyalty_businesses
    from modules.referrals_db import get_or_create_referral_code

    # Check if already seeded
    existing = get_all_loyalty_businesses()
    if len(existing) >= 3:
        return render_template_string(f"""<!DOCTYPE html><html><head>
<title>Seed Data — Piney Digital</title>{BASE_CSS}</head><body>
<div class="login-wrap">
  <div class="login-card" style="text-align:center">
    <h1>Seed Data</h1>
    <p style="color:#94a3b8;margin:20px 0">Database already has {len(existing)} businesses.</p>
    <a href="/" style="color:#22c55e">Return to Dashboard</a>
  </div>
</div></body></html>""")

    # Create test businesses
    businesses = [
        {"name": "Downtown Coffee Co", "type": "Coffee shop", "city": "Nacogdoches", "description": "Artisan coffee and pastries in historic downtown", "punches_needed": 5, "discount_percent": 15},
        {"name": "Mario's Hair Salon", "type": "Hair salon", "city": "Lufkin", "description": "Professional haircuts and styling for all ages", "punches_needed": 8, "discount_percent": 20},
        {"name": "Sparkle Nails", "type": "Nail salon", "city": "Nacogdoches", "description": "Manicures, pedicures, and nail art", "punches_needed": 10, "discount_percent": 25},
    ]

    created_biz = []
    for b in businesses:
        biz_id = create_loyalty_business(**b)
        created_biz.append(biz_id)

    # Create test customers
    customers = [
        {"name": "Maria Garcia", "email": "maria@test.com", "phone": "+19365550001"},
        {"name": "James Wilson", "email": "james@test.com", "phone": "+19365550002"},
        {"name": "Ana Martinez", "email": "ana@test.com", "phone": "+19365550003"},
    ]

    created_cust = []
    for c in customers:
        cust_id = create_customer(c["name"], c["email"], c["phone"])
        created_cust.append(cust_id)

    # Create referral codes
    if created_biz and created_cust:
        code = get_or_create_referral_code(created_cust[0], created_biz[0], "Maria")

    return render_template_string(f"""<!DOCTYPE html><html><head>
<title>Seed Data — Piney Digital</title>{BASE_CSS}</head><body>
<div class="login-wrap">
  <div class="login-card" style="text-align:center">
    <h1 style="color:#22c55e">Seed Data Created!</h1>
    <p style="color:#94a3b8;margin:20px 0">
      Created {len(created_biz)} businesses and {len(created_cust)} customers.
    </p>
    <div style="text-align:left;background:#0f172a;padding:16px;border-radius:8px;margin:20px 0;font-size:13px">
      <p style="color:#64748b;margin-bottom:8px">Test Businesses:</p>
      {''.join([f'<p style="color:#e2e8f0">• {b["name"]} ({b["city"]})</p>' for b in businesses])}
      <p style="color:#64748b;margin:16px 0 8px">Test Customers:</p>
      {''.join([f'<p style="color:#e2e8f0">• {c["name"]} ({c["email"]})</p>' for c in customers])}
    </div>
    <a href="/app" style="display:inline-block;background:#22c55e;color:#fff;padding:12px 24px;border-radius:8px;text-decoration:none">View Customer App</a>
    <p style="margin-top:16px"><a href="/" style="color:#64748b">Return to Dashboard</a></p>
  </div>
</div></body></html>""")


@app.route("/")
def landing():
    """Public landing page with login options for all user types."""
    # If admin logged in, redirect to dashboard
    if session.get("logged_in"):
        return redirect(url_for("overview"))

    return render_template_string(f"""<!DOCTYPE html><html><head>
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1,user-scalable=no">
<title>Piney Digital — Loyalty Platform</title>
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;
     background:linear-gradient(135deg,#0a1628 0%,#1a3a2a 100%);
     min-height:100vh;color:#e2e8f0;display:flex;flex-direction:column;align-items:center;justify-content:center;padding:40px 20px}}
.header{{text-align:center;margin-bottom:48px}}
.header h1{{font-size:36px;font-weight:700;color:#fff;margin-bottom:8px}}
.header h1 span{{color:#22c55e}}
.header p{{color:#94a3b8;font-size:16px}}
.cards{{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:24px;max-width:900px;width:100%}}
.card{{background:#1e293b;border:1px solid #334155;border-radius:16px;padding:32px;text-align:center;transition:all .2s}}
.card:hover{{transform:translateY(-4px);box-shadow:0 12px 40px rgba(0,0,0,0.4)}}
.card .icon{{font-size:48px;margin-bottom:16px}}
.card h2{{font-size:20px;font-weight:600;color:#fff;margin-bottom:8px}}
.card p{{color:#94a3b8;font-size:14px;margin-bottom:24px}}
.card .btn{{display:block;width:100%;padding:12px 24px;border-radius:8px;text-decoration:none;font-weight:600;margin-bottom:8px}}
.btn-admin{{background:#166534;color:#86efac}}
.btn-business{{background:#1e40af;color:#93c5fd}}
.btn-customer{{background:#7c2d12;color:#fdba74}}
.card .link{{color:#64748b;font-size:13px}}
.card .link a{{color:#3b82f6}}
@media (max-width: 640px) {{
  .header h1{{font-size:28px}}
  .cards{{grid-template-columns:1fr}}
  .card{{padding:24px}}
}}
</style></head><body>
<div class="header">
  <h1>Piney <span>Digital</span></h1>
  <p>Loyalty & Customer Engagement Platform</p>
</div>

<div class="cards">
  <div class="card">
    <div class="icon">Admin</div>
    <h2>Admin Dashboard</h2>
    <p>Manage leads, loyalty programs, and system settings</p>
    <a href="/login" class="btn btn-admin">Admin Login</a>
  </div>

  <div class="card">
    <div class="icon">Business</div>
    <h2>Business Portal</h2>
    <p>Manage your loyalty program, scan QR codes, track customers</p>
    <a href="/portal/login" class="btn btn-business">Business Login</a>
    <p class="link">New here? <a href="/portal/signup">Create account</a></p>
  </div>

  <div class="card">
    <div class="icon">Customer</div>
    <h2>Customer App</h2>
    <p>View loyalty cards, earn rewards, discover local businesses</p>
    <a href="/app" class="btn btn-customer">Browse Businesses</a>
    <p class="link">Have an account? <a href="/app/login">Sign in</a></p>
  </div>
</div>

</body></html>""")


@app.route("/admin")
@app.route("/dashboard")
@login_required
def overview():
    stats  = get_stats()
    window = get_window_status()
    loyalty_stats = get_loyalty_stats()
    recent = query_db("""
        SELECT business_name, city, category, phone,
               site_status, outreach_status, lead_score
        FROM leads ORDER BY id DESC LIMIT 8
    """)

    total = max(stats["total"], 1)
    none_pct    = round(stats["none"]    / total * 100)
    outdated_pct= round(stats["outdated"]/ total * 100)
    parked_pct  = round(stats["parked"]  / total * 100)
    modern_pct  = round(stats["modern"]  / total * 100)

    win_class = "window-open" if window["open"] else "window-closed"
    win_text  = f"Open · {window['time']} · Mon–Fri 8am–6pm CT" if window["open"] else f"Closed · {window['time']} · Reopens 8am CT"

    rows = ""
    for r in (recent or []):
        badge_map = {
            "sent":"badge-green","queued":"badge-blue",
            "replied":"badge-amber","failed":"badge-red","new":"badge-gray"
        }
        badge = badge_map.get(r["outreach_status"], "badge-gray")
        rows += f"""<tr>
          <td>{r['business_name']}</td>
          <td>{r['city']}</td>
          <td>{r['category']}</td>
          <td>{r['phone'] or '—'}</td>
          <td><span class="badge {badge}">{r['outreach_status']}</span></td>
          <td style="color:#64748b">{r['lead_score']}</td>
        </tr>"""

    return render_template_string(f"""<!DOCTYPE html><html><head>
<title>Piney Digital — Overview</title>{BASE_CSS}</head><body>
<div class="layout">""" + get_nav('overview') + f"""<div class="main">
    <div class="topbar">
      <h2>Campaign overview <span class="badge badge-green">Live</span></h2>
      <div class="topbar-right">{window['time']}</div>
    </div>

    <div class="stats">
      <div class="stat"><label>Total leads</label>
        <div class="val">{stats['total']}</div>
        <div class="sub">3 cities · 6 categories</div></div>
      <div class="stat"><label>Messages sent</label>
        <div class="val green">{stats['sent']}</div>
        <div class="sub">{stats['failed']} failed</div></div>
      <div class="stat"><label>Loyalty customers</label>
        <div class="val blue">{loyalty_stats['total_customers']}</div>
        <div class="sub">{loyalty_stats['active_businesses']} businesses</div></div>
      <div class="stat"><label>Loyalty punches</label>
        <div class="val amber">{loyalty_stats['total_punches']}</div>
        <div class="sub">{loyalty_stats['total_rewards_redeemed']} rewards redeemed</div></div>
    </div>

    <div class="grid2">
      <div class="panel">
        <h3>Sending window</h3>
        <div class="window-box {win_class}">
          <div class="window-dot"></div>
          <div class="window-text">{win_text}</div>
        </div>
        <h3>Lead breakdown</h3>
        <div class="bar-row">
          <div class="bar-label">No website</div>
          <div class="bar-track"><div class="bar-fill" style="width:{none_pct}%;background:#4ade80"></div></div>
          <div class="bar-count">{stats['none']}</div>
        </div>
        <div class="bar-row">
          <div class="bar-label">Outdated</div>
          <div class="bar-track"><div class="bar-fill" style="width:{outdated_pct}%;background:#60a5fa"></div></div>
          <div class="bar-count">{stats['outdated']}</div>
        </div>
        <div class="bar-row">
          <div class="bar-label">Parked</div>
          <div class="bar-track"><div class="bar-fill" style="width:{parked_pct}%;background:#fbbf24"></div></div>
          <div class="bar-count">{stats['parked']}</div>
        </div>
        <div class="bar-row">
          <div class="bar-label">Modern</div>
          <div class="bar-track"><div class="bar-fill" style="width:{modern_pct}%;background:#475569"></div></div>
          <div class="bar-count">{stats['modern']}</div>
        </div>
      </div>

      <div class="panel">
        <h3>Quick actions</h3>
        <div style="display:flex;flex-direction:column;gap:10px">
          <a href="/send" class="btn btn-green">Send queued messages</a>
          <a href="/leads?status=replied" class="btn btn-blue">View replies</a>
          <a href="/loyalty" class="btn btn-blue">Manage loyalty programs</a>
          <a href="/log" class="btn btn-gray">View send log</a>
        </div>
      </div>
    </div>

    <div class="panel">
      <h3>Recent leads</h3>
      <div class="table-wrap">
      <table>
        <thead><tr>
          <th>Business</th><th>City</th><th>Category</th>
          <th>Phone</th><th>Status</th><th>Score</th>
        </tr></thead>
        <tbody>{rows}</tbody>
      </table>
      </div>
    </div>
  </div>
</div></body></html>""")


@app.route("/leads")
@login_required
def leads():
    status_filter   = request.args.get("status", "")
    city_filter     = request.args.get("city", "")
    site_filter     = request.args.get("site", "")
    search          = request.args.get("q", "")

    where = ["1=1"]
    params = []
    if status_filter:
        where.append("outreach_status = ?"); params.append(status_filter)
    if city_filter:
        where.append("city = ?"); params.append(city_filter)
    if site_filter:
        where.append("site_status = ?"); params.append(site_filter)
    if search:
        where.append("business_name LIKE ?"); params.append(f"%{search}%")

    leads_data = query_db(f"""
        SELECT id, business_name, city, category, phone,
               site_status, outreach_status, lead_score, owner_name, notes
        FROM leads WHERE {' AND '.join(where)}
        ORDER BY lead_score DESC LIMIT 100
    """, params)

    rows = ""
    for r in (leads_data or []):
        badge_map = {"sent":"badge-green","queued":"badge-blue",
            "replied":"badge-amber","failed":"badge-red","new":"badge-gray"}
        site_map  = {"none":"badge-red","parked":"badge-amber",
            "outdated":"badge-blue","modern":"badge-green"}
        b1 = badge_map.get(r["outreach_status"], "badge-gray")
        b2 = site_map.get(r["site_status"], "badge-gray")

        # Parse SMS from notes
        try:
            sms = json.loads(r["notes"] or "{}").get("sms","")[:80]
        except Exception:
            sms = ""

        rows += f"""<tr>
          <td><strong style="color:#f1f5f9">{r['business_name']}</strong></td>
          <td>{r['city']}</td>
          <td style="color:#64748b">{r['category']}</td>
          <td>{r['phone'] or '—'}</td>
          <td><span class="badge {b2}">{r['site_status'] or '—'}</span></td>
          <td><span class="badge {b1}">{r['outreach_status']}</span></td>
          <td style="color:#fbbf24">{r['lead_score']}</td>
          <td style="max-width:180px;overflow:hidden;text-overflow:ellipsis;
              white-space:nowrap;color:#475569;font-size:11px">{sms}</td>
        </tr>"""

    return render_template_string(f"""<!DOCTYPE html><html><head>
<title>Piney Digital — Leads</title>{BASE_CSS}</head><body>
<div class="layout">""" + get_nav('leads') + f"""<div class="main">
    <div class="topbar">
      <h2>Lead database</h2>
      <div class="topbar-right">Showing {len(leads_data or [])} leads</div>
    </div>
    <div class="panel">
      <form method="GET" class="filter-bar">
        <input name="q" placeholder="Search business name..." value="{search}" style="flex:1;min-width:160px">
        <select name="status">
          <option value="">All statuses</option>
          {''.join(f'<option value="{s}" {"selected" if status_filter==s else ""}>{s}</option>'
            for s in ['new','queued','sent','replied','failed'])}
        </select>
        <select name="city">
          <option value="">All cities</option>
          {''.join(f'<option value="{c}" {"selected" if city_filter==c else ""}>{c}</option>'
            for c in ['Lufkin','Nacogdoches','Diboll'])}
        </select>
        <select name="site">
          <option value="">All site types</option>
          {''.join(f'<option value="{s}" {"selected" if site_filter==s else ""}>{s}</option>'
            for s in ['none','parked','outdated','modern'])}
        </select>
        <button type="submit" class="btn btn-blue">Filter</button>
        <a href="/leads" class="btn btn-gray">Clear</a>
      </form>
      <div class="table-wrap">
      <table>
        <thead><tr>
          <th>Business</th><th>City</th><th>Category</th><th>Phone</th>
          <th>Site</th><th>Status</th><th>Score</th><th>Message preview</th>
        </tr></thead>
        <tbody>{rows}</tbody>
      </table>
      </div>
    </div>
  </div>
</div></body></html>""")


@app.route("/log")
@login_required
def log():
    logs = query_db("""
        SELECT ol.sent_at, l.business_name, l.city, l.phone,
               ol.channel, ol.direction, ol.status, ol.body
        FROM outreach_log ol
        JOIN leads l ON ol.lead_id = l.id
        ORDER BY ol.sent_at DESC LIMIT 100
    """)

    rows = ""
    for r in (logs or []):
        status_map = {"sent":"badge-green","failed":"badge-red",
                      "dry_run":"badge-blue","received":"badge-amber"}
        badge = status_map.get(r["status"], "badge-gray")
        rows += f"""<tr>
          <td style="color:#64748b;font-size:11px">{r['sent_at'][:16]}</td>
          <td><strong style="color:#f1f5f9">{r['business_name']}</strong></td>
          <td>{r['city']}</td>
          <td>{r['phone'] or '—'}</td>
          <td><span class="badge {badge}">{r['status']}</span></td>
          <td style="max-width:240px;overflow:hidden;text-overflow:ellipsis;
              white-space:nowrap;color:#64748b;font-size:11px">{r['body']}</td>
        </tr>"""

    if not rows:
        rows = '<tr><td colspan="6" style="text-align:center;color:#475569;padding:24px">No messages sent yet</td></tr>'

    return render_template_string(f"""<!DOCTYPE html><html><head>
<title>Piney Digital — Outreach Log</title>{BASE_CSS}</head><body>
<div class="layout">""" + get_nav('log') + f"""<div class="main">
    <div class="topbar"><h2>Outreach Log</h2></div>
    <div class="panel">
      <h3>All sent messages</h3>
      <div class="table-wrap">
      <table>
        <thead><tr>
          <th>Time</th><th>Business</th><th>City</th>
          <th>Phone</th><th>Status</th><th>Message</th>
        </tr></thead>
        <tbody>{rows}</tbody>
      </table>
      </div>
    </div>
  </div>
</div></body></html>""")


@app.route("/send", methods=["GET","POST"])
@login_required
def send_page():
    window  = get_window_status()
    stats   = get_stats()
    message = ""
    result  = None

    if request.method == "POST":
        action  = request.form.get("action")
        dry_run = action == "dry"
        force   = request.form.get("force") == "1"
        limit   = request.form.get("limit", "")
        limit   = int(limit) if limit.isdigit() else None

        try:
            from modules.sender import run_sender
            result  = run_sender(limit=limit, dry_run=dry_run, force=force)
            message = f"Done — Sent: {result['sent']} · Failed: {result['failed']} · Skipped: {result['skipped']}"
        except Exception as e:
            message = f"Error: {str(e)}"

    win_class = "window-open" if window["open"] else "window-closed"
    win_text  = f"Open · {window['time']}" if window["open"] else f"Closed · {window['time']}"

    return render_template_string(f"""<!DOCTYPE html><html><head>
<title>Piney Digital — Send</title>{BASE_CSS}</head><body>
<div class="layout">""" + get_nav('send') + f"""<div class="main">
    <div class="topbar"><h2>📤 Send Messages</h2></div>

    <div class="grid2">
      <div class="panel">
        <h3>Sending window</h3>
        <div class="window-box {win_class}">
          <div class="window-dot"></div>
          <div class="window-text">{win_text} · Mon–Fri 8am–6pm CT</div>
        </div>
        <h3 style="margin-top:14px">Ready to send</h3>
        <div style="font-size:28px;font-weight:600;color:#60a5fa;margin:8px 0">{stats['queued']}</div>
        <div style="font-size:12px;color:#64748b">messages queued · {stats['sent']} sent so far</div>
      </div>

      <div class="panel">
        <h3>Launch campaign</h3>
        <form method="POST" style="display:flex;flex-direction:column;gap:12px">
          <div>
            <label style="font-size:12px;color:#64748b;display:block;margin-bottom:6px">
              Max messages (leave blank for all)
            </label>
            <input type="number" name="limit" placeholder="e.g. 20"
              style="background:#0f172a;border:1px solid #334155;color:#e2e8f0;
              padding:8px 12px;border-radius:6px;font-size:13px;width:100%">
          </div>
          <div style="display:flex;align-items:center;gap:8px">
            <input type="checkbox" name="force" value="1" id="force" style="width:auto">
            <label for="force" style="font-size:12px;color:#64748b">
              Force send (ignore time window) — testing only
            </label>
          </div>
          <div style="display:flex;gap:10px">
            <button type="submit" name="action" value="dry" class="btn btn-blue" style="flex:1">
              Dry run preview
            </button>
            <button type="submit" name="action" value="live" class="btn btn-green" style="flex:1"
              onclick="return confirm('Send real SMS messages?')">
              Send for real
            </button>
          </div>
        </form>
        {f'<div style="margin-top:14px;padding:10px 14px;background:#0f172a;border-radius:6px;font-size:12px;color:#94a3b8">{message}</div>' if message else ''}
      </div>
    </div>
  </div>
</div></body></html>""")


# ── AI Calls Route ─────────────────────────────────────────────

def get_call_stats():
    """Get statistics for AI calling."""
    conn = get_connection()
    c = conn.cursor()

    # Count leads by call status (SQLite compatible)
    c.execute("""
        SELECT
            COUNT(*) as total_with_phone
        FROM leads
        WHERE phone IS NOT NULL AND phone != '' AND lead_score >= 60
    """)
    with_phone = c.fetchone()[0]

    c.execute("""
        SELECT call_status, COUNT(*) as cnt
        FROM leads
        WHERE call_status IS NOT NULL
        GROUP BY call_status
    """)
    status_counts = {row[0]: row[1] for row in c.fetchall()}

    conn.close()

    # 'queued' status means ready to call, so include it in 'new'
    queued_count = status_counts.get('queued', 0)
    processed_count = sum(v for k, v in status_counts.items() if k != 'queued')

    return {
        'new': (with_phone - processed_count) if with_phone > processed_count else queued_count,
        'queued': queued_count,
        'called': status_counts.get('called', 0),
        'interested': status_counts.get('interested', 0),
        'transferred': status_counts.get('transferred', 0),
        'voicemail': status_counts.get('voicemail', 0),
        'declined': status_counts.get('declined', 0),
        'no_answer': status_counts.get('no_answer', 0),
        'with_phone': with_phone,
    }


@app.route("/call", methods=["GET", "POST"])
@login_required
def call_page():
    """AI voice calling dashboard."""
    from modules.caller import is_calling_window, get_central_time_str, run_caller

    message = ""
    stats = get_call_stats()
    window = is_calling_window()
    ct_time = get_central_time_str()

    if request.method == "POST":
        action = request.form.get("action")
        limit = request.form.get("limit")
        force = request.form.get("force") == "1"

        limit_int = int(limit) if limit else None

        if action == "dry":
            result = run_caller(limit=limit_int, dry_run=True, force=True)
            message = f"Dry run complete: {result['called']} would be called, {result['failed']} failed"
        elif action == "live":
            result = run_caller(limit=limit_int, dry_run=False, force=force)
            message = f"Calls initiated: {result['called']} called, {result['failed']} failed"

    win_class = "window-open" if window[0] else "window-closed"
    win_text = f"Open · {window[1]}" if window[0] else f"Closed · {window[1]}"

    return render_template_string(f"""<!DOCTYPE html><html><head>
<title>Piney Digital — AI Calls</title>{BASE_CSS}</head><body>
<div class="layout">""" + get_nav('call') + f"""<div class="main">
    <div class="topbar"><h2>📞 AI Voice Calls</h2></div>

    <div class="stats">
      <div class="stat">
        <label>Ready to Call</label>
        <div class="val blue">{stats['new']}</div>
        <div class="sub">leads with phone</div>
      </div>
      <div class="stat">
        <label>Called</label>
        <div class="val">{stats['called'] + stats['voicemail']}</div>
        <div class="sub">{stats['voicemail']} voicemails</div>
      </div>
      <div class="stat">
        <label>Hot Leads</label>
        <div class="val green">{stats['interested'] + stats['transferred']}</div>
        <div class="sub">{stats['transferred']} transferred</div>
      </div>
      <div class="stat">
        <label>Declined</label>
        <div class="val red">{stats['declined']}</div>
        <div class="sub">{stats['no_answer']} no answer</div>
      </div>
    </div>

    <div class="grid2">
      <div class="panel">
        <h3>Calling Window</h3>
        <div class="window-box {win_class}">
          <div class="window-dot"></div>
          <div class="window-text">{win_text}</div>
        </div>
        <div style="margin-top:14px;font-size:13px;color:#94a3b8">
          <strong>Hours:</strong> Mon–Fri, 9am–7pm CT<br>
          <strong>Rate:</strong> 15 calls/hour max<br>
          <strong>Current time:</strong> {ct_time}
        </div>
      </div>

      <div class="panel">
        <h3>Launch AI Calls</h3>
        <form method="POST" style="display:flex;flex-direction:column;gap:12px">
          <div>
            <label style="font-size:12px;color:#64748b;display:block;margin-bottom:6px">
              Max calls (leave blank for all)
            </label>
            <input type="number" name="limit" placeholder="e.g. 10"
              style="background:#0f172a;border:1px solid #334155;color:#e2e8f0;
              padding:8px 12px;border-radius:6px;font-size:13px;width:100%">
          </div>
          <div style="display:flex;align-items:center;gap:8px">
            <input type="checkbox" name="force" value="1" id="callforce" style="width:auto">
            <label for="callforce" style="font-size:12px;color:#64748b">
              Force call (ignore time window) — testing only
            </label>
          </div>
          <div style="display:flex;gap:10px">
            <button type="submit" name="action" value="dry" class="btn btn-blue" style="flex:1">
              🔍 Dry run preview
            </button>
            <button type="submit" name="action" value="live" class="btn btn-green" style="flex:1"
              onclick="return confirm('Start AI voice calls?')">
              📞 Start calling
            </button>
          </div>
        </form>
        {f'<div style="margin-top:14px;padding:10px 14px;background:#0f172a;border-radius:6px;font-size:12px;color:#94a3b8">{message}</div>' if message else ''}
      </div>
    </div>

    <div class="panel" style="margin-top:20px">
      <h3>Recent Calls</h3>
      <div style="font-size:13px;color:#94a3b8;padding:20px;text-align:center">
        Run 'python run.py calls' in terminal to view call history, or check your email for hot lead notifications.
      </div>
    </div>
  </div>
</div></body></html>""")


@app.route("/api/stats")
@login_required
def api_stats():
    return jsonify({**get_stats(), **get_window_status()})


# ── Loyalty Routes ─────────────────────────────────────────

@app.route("/loyalty")
@login_required
def loyalty():
    """Loyalty program admin overview."""
    stats = get_loyalty_stats()
    businesses = get_all_loyalty_businesses()

    return render_template_string("""<!DOCTYPE html><html><head>
<title>Piney Digital - Loyalty</title>""" + BASE_CSS + """</head><body>
<div class="layout">""" + get_nav('loyalty') + """<div class="main">
    <div class="topbar">
      <h2>🎁 Loyalty Program</h2>
      <div class="topbar-right">{{ num_businesses }} businesses enrolled</div>
    </div>

    <div class="stats">
      <div class="stat"><label>Businesses</label>
        <div class="val blue">{{ stats.active_businesses }}</div>
        <div class="sub">enrolled in loyalty</div></div>
      <div class="stat"><label>Customers</label>
        <div class="val green">{{ stats.total_customers }}</div>
        <div class="sub">loyalty members</div></div>
      <div class="stat"><label>Active cards</label>
        <div class="val amber">{{ stats.total_cards }}</div>
        <div class="sub">cards in circulation</div></div>
      <div class="stat"><label>Rewards</label>
        <div class="val">{{ stats.total_rewards_redeemed }}</div>
        <div class="sub">{{ stats.total_punches }} punches given</div></div>
    </div>

    <div class="panel">
      <h3>Registered Businesses</h3>
      <div class="table-wrap">
      <table>
        <thead><tr>
          <th>Business</th><th>Type</th><th>City</th>
          <th>Setup</th><th>Reward</th><th>Action</th>
        </tr></thead>
        <tbody>
        {% for b in businesses %}
        <tr>
          <td><strong style="color:#f1f5f9">{{ b.name }}</strong></td>
          <td style="color:#64748b">{{ b.type or '-' }}</td>
          <td>{{ b.city or '-' }}</td>
          <td>{{ b.punches_needed }} punches</td>
          <td><span class="badge badge-amber">{{ b.discount_percent }}% off</span></td>
          <td><a href="/loyalty/business/{{ b.id }}" class="btn btn-blue" style="padding:6px 12px;font-size:12px">Manage</a></td>
        </tr>
        {% else %}
        <tr><td colspan="6" style="text-align:center;color:#475569;padding:24px">No businesses enrolled yet</td></tr>
        {% endfor %}
        </tbody>
      </table>
      </div>
    </div>
  </div>
</div></body></html>
""", stats=stats, businesses=businesses, num_businesses=len(businesses))


@app.route("/loyalty/business/<biz_id>")
@login_required
def loyalty_business(biz_id: str):
    """Business dashboard for managing their loyalty program."""
    from modules.loyalty_db import get_loyalty_business
    biz = get_loyalty_business(biz_id)
    if not biz:
        return "Business not found", 404

    stats = get_business_stats(biz_id)

    return render_template_string("""<!DOCTYPE html><html><head>
<title>{{ biz_name }} - Piney Digital</title>""" + BASE_CSS + """</head><body>
<div class="layout">""" + get_nav('loyalty') + """<div class="main">
    <div class="topbar">
      <h2>{{ biz_name }} <span class="badge badge-blue">{{ biz_type or 'Business' }}</span></h2>
      <div class="topbar-right">
        <a href="/loyalty" class="btn btn-gray" style="font-size:12px;padding:6px 12px">← All Businesses</a>
      </div>
    </div>

    <div class="stats">
      <div class="stat"><label>Total customers</label>
        <div class="val green">{{ stats.total_customers }}</div>
        <div class="sub">enrolled in program</div></div>
      <div class="stat"><label>Punches given</label>
        <div class="val">{{ stats.total_punches }}</div>
        <div class="sub">total punches awarded</div></div>
      <div class="stat"><label>Rewards earned</label>
        <div class="val amber">{{ stats.total_rewards }}</div>
        <div class="sub">rewards redeemed</div></div>
      <div class="stat"><label>Program setup</label>
        <div class="val">{{ punches_needed }} punches</div>
        <div class="sub">→ {{ discount_percent }}% reward</div></div>
    </div>

    <div class="panel">
      <div class="panel-header">
        <h3>Customer Cards</h3>
        <a href="/business/{{ biz_id }}/scan" class="btn btn-green">Scan QR Code</a>
      </div>
      <div class="table-wrap">
      <table>
        <thead><tr>
          <th>Customer</th><th>Punches</th><th>Progress</th>
          <th>Last punch</th><th>Status</th>
        </tr></thead>
        <tbody>
        {% for c in customers %}
        <tr>
          <td><strong style="color:#f1f5f9">{{ c.customer_name }}</strong></td>
          <td>{{ c.punches }} / {{ c.punches_needed }}</td>
          <td>
            <div style="display:flex;align-items:center;gap:6px;">
              <div style="flex:1;height:6px;background:#0f172a;border-radius:4px;">
                <div style="width:{% if c.punches >= c.punches_needed %}100{% else %}{{ (c.punches/c.punches_needed*100)|int }}{% endif %}%;height:100%;background:{{ '#BA7517' if c.punches>=c.punches_needed else '#1D9E75' }};border-radius:4px;"></div>
              </div>
            </div>
          </td>
          <td style="color:#64748b;font-size:12px">{{ (c.last_punch_at or '-')[:16] }}</td>
          <td>{% if c.punches >= c.punches_needed %}<span class="badge badge-amber">Reward ready</span>{% else %}<span class="badge badge-green">Active</span>{% endif %}</td>
        </tr>
        {% else %}
        <tr><td colspan="5" style="text-align:center;color:#475569;padding:24px">No customers yet</td></tr>
        {% endfor %}
        </tbody>
      </table>
      </div>
    </div>

    <div class="grid2" style="margin-top:20px">
      <div class="panel">
        <h3>⭐ Review Requests</h3>
        <p style="font-size:13px;color:#94a3b8;margin-bottom:16px">Auto-send review requests after customer visits</p>
        <a href="/reviews/business/{{ biz_id }}/settings" class="btn btn-blue" style="width:100%;text-align:center">Manage Reviews</a>
      </div>
      <div class="panel">
        <h3>📅 Online Booking</h3>
        <p style="font-size:13px;color:#94a3b8;margin-bottom:16px">Let customers book appointments online</p>
        <a href="/bookings/business/{{ biz_id }}/calendar" class="btn btn-blue" style="width:100%;text-align:center">Open Calendar</a>
      </div>
    </div>

    <div class="grid2" style="margin-top:16px">
      <div class="panel">
        <h3>👥 Referrals</h3>
        <p style="font-size:13px;color:#94a3b8;margin-bottom:16px">Set up a referral program for your customers</p>
        <a href="/referrals/business/{{ biz_id }}/settings" class="btn btn-blue" style="width:100%;text-align:center">Manage Referrals</a>
      </div>
      <div class="panel">
        <h3>📊 Statistics</h3>
        <p style="font-size:13px;color:#94a3b8;margin-bottom:16px">View detailed business analytics</p>
        <a href="/admin/overview" class="btn btn-gray" style="width:100%;text-align:center">Admin Overview</a>
      </div>
    </div>
  </div>
</div></body></html>
""", biz_id=biz_id, biz_name=biz['name'], biz_type=biz['type'], num_customers=stats['total_customers'],
    punches_needed=biz['punches_needed'], discount_percent=biz['discount_percent'],
    customers=stats['customers'], stats=stats)


@app.route("/loyalty/customer/<cust_id>")
@login_required
def loyalty_customer(cust_id: str):
    """View a customer's loyalty cards."""
    from modules.loyalty_db import get_customer
    cust = get_customer(cust_id)
    if not cust:
        return "Customer not found", 404

    cards = get_customer_cards(cust_id)

    return render_template_string("""<!DOCTYPE html><html><head>
<title>{{ cust_name }} - Piney Digital</title>""" + BASE_CSS + """</head><body>
<div class="layout">""" + get_nav('loyalty') + """<div class="main">
    <div class="topbar">
      <h2>{{ cust_name }}</h2>
      <div class="topbar-right">{{ cards|length }} loyalty cards</div>
    </div>

    <div class="grid2">
    {% for c in cards %}
    <div class="panel">
      <h3>{{ c.business_name }} <span class="badge badge-blue">{{ c.business_type or '' }}</span></h3>
      <div style="margin:12px 0;">
        <div class="label">Punches: {{ c.punches }} / {{ c.punches_needed }}</div>
        <div style="display:flex;gap:4px;flex-wrap:wrap;margin-top:8px;">
        {% for i in range(c.punches_needed) %}
          <div style="width:24px;height:24px;border-radius:50%;border:1.5px solid #334155;display:flex;align-items:center;justify-content:center;font-size:11px;background:{{ '#1D9E75' if i<c.punches else 'transparent' }};color:{{ '#fff' if i<c.punches else '#64748b' }}">✓</div>
        {% endfor %}
        </div>
      </div>
      <div style="display:flex;justify-content:space-between;align-items:center;margin-top:12px;">
        <div style="font-size:11px;color:#64748b;">
          {% if c.total_rewards_earned and c.total_rewards_earned > 0 %}
            🏆 {{ c.total_rewards_earned }} reward{{ 's' if c.total_rewards_earned != 1 else '' }} earned
          {% else %}
            {{ c.punches_needed - c.punches }} more to first reward
          {% endif %}
        </div>
        {% if c.punches >= c.punches_needed %}<span class="badge badge-amber">{{ c.discount_percent }}% ready!</span>{% endif %}
      </div>
    </div>
    {% else %}
    <div class="panel"><p style="color:#64748b;padding:24px;text-align:center;">No loyalty cards yet</p></div>
    {% endfor %}
    </div>
  </div>
</div></body></html>
""", cust_name=cust['name'], cards=cards)


# ── Customer Portal Routes ─────────────────────────────────

CUSTOMER_PORTAL_CSS = """
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;
     background:#0f172a;color:#e2e8f0;min-height:100vh}
.layout{display:flex;min-height:100vh}
.sidebar{width:210px;background:#1e293b;padding:0;flex-shrink:0;
         display:flex;flex-direction:column}
.logo{padding:20px;border-bottom:1px solid #334155}
.logo h1{font-size:15px;font-weight:600;color:#fff}
.logo p{font-size:11px;color:#64748b;margin-top:2px}
.nav{padding:12px 0;flex:1}
.nav a{display:flex;align-items:center;gap:10px;padding:9px 20px;
       font-size:13px;color:#94a3b8;border-left:2px solid transparent}
.nav a:hover{background:#0f172a80;color:#cbd5e1}
.nav a.active{background:#0f172a;color:#fff;border-left-color:#22c55e}
.dot{width:7px;height:7px;border-radius:50%;background:#334155;flex-shrink:0}
.nav a.active .dot{background:#22c55e}
.sidebar-footer{padding:16px 20px;border-top:1px solid #334155;
                font-size:11px;color:#475569}
.main{flex:1;padding:28px;overflow-x:hidden}
.topbar{display:flex;justify-content:space-between;align-items:center;
        margin-bottom:24px}
.topbar h2{font-size:18px;font-weight:500;color:#f1f5f9}
.topbar-right{display:flex;align-items:center;gap:12px;font-size:12px;color:#64748b}
.badge{display:inline-block;font-size:10px;padding:2px 8px;
       border-radius:99px;font-weight:500}
.badge-green{background:#166534;color:#86efac}
.badge-amber{background:#78350f;color:#fcd34d}
.badge-blue{background:#1e3a5f;color:#93c5fd}
.badge-gray{background:#1e293b;color:#64748b;border:1px solid #334155}
.panel{background:#1e293b;border-radius:8px;padding:18px;margin-bottom:16px}
.panel h3{font-size:11px;color:#64748b;text-transform:uppercase;
          letter-spacing:.05em;margin-bottom:14px;font-weight:500}
.grid3{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:16px}
.biz-card{background:#1e293b;border:1px solid #334155;border-radius:8px;
          padding:16px;transition:transform .15s}
.biz-card:hover{transform:translateY(-2px);border-color:#475569}
.biz-card h4{font-size:15px;font-weight:500;color:#f1f5f9;margin-bottom:4px}
.biz-card p{font-size:12px;color:#64748b;margin-bottom:12px}
.biz-meta{display:flex;justify-content:space-between;align-items:center}
.biz-meta .tag{font-size:11px;color:#475569}
.btn{display:inline-flex;align-items:center;gap:6px;padding:8px 16px;
     border-radius:6px;font-size:13px;font-weight:500;cursor:pointer;
     border:none;transition:opacity .15s;text-decoration:none}
.btn:hover{opacity:.85}
.btn-green{background:#166534;color:#86efac}
.btn-blue{background:#1e3a5f;color:#93c5fd}
.card-view{background:#1e293b;border-radius:12px;padding:24px;max-width:420px}
.card-header{display:flex;justify-content:space-between;align-items:flex-start;
             margin-bottom:20px}
.card-title{font-size:18px;font-weight:600;color:#fff}
.card-type{font-size:12px;color:#64748b}
.punch-row{display:flex;gap:8px;flex-wrap:wrap;margin:16px 0}
.punch{width:32px;height:32px;border-radius:50%;border:2px solid #334155;
       display:flex;align-items:center;justify-content:center;
       font-size:14px;font-weight:600}
.punch.filled{background:#1D9E75;border-color:#0F6E56;color:#fff}
.punch.reward{background:#BA7517;border-color:#854F0B;color:#fff}
.qr-section{background:#0f172a;border-radius:8px;padding:16px;
            margin-top:20px;text-align:center}
.qr-section img{border-radius:8px}
.qr-section p{font-size:11px;color:#64748b;margin-top:8px}
.progress-bar{height:8px;background:#0f172a;border-radius:4px;
              overflow:hidden;margin:12px 0}
.progress-fill{height:100%;background:#1D9E75;transition:width .3s}
.scan-area{background:#1e293b;border-radius:12px;padding:24px;
           text-align:center;max-width:400px;margin:0 auto}
.scan-display{background:#0f172a;border-radius:8px;padding:20px;
              margin:16px 0;min-height:120px;display:flex;
              align-items:center;justify-content:center}
.scan-result{font-size:14px;color:#64748b}
.scan-result.success{color:#86efac}
.scan-result.error{color:#f87171}

/* Mobile Responsive */
@media (max-width: 768px) {
  .layout{flex-direction:column}
  .sidebar{width:100%;flex-shrink:0;border-bottom:1px solid #334155}
  .nav{display:flex;flex-direction:row;overflow-x:auto;padding:8px 0}
  .nav a{white-space:nowrap;padding:8px 12px;font-size:12px;border-left:none;border-bottom:2px solid transparent}
  .nav a.active{border-left:none;border-bottom-color:#22c55e;background:transparent}
  .sidebar-footer{display:none}
  .main{padding:16px}
  .topbar{flex-direction:column;align-items:flex-start;gap:8px}
  .topbar h2{font-size:16px}
  .stats{grid-template-columns:repeat(2,1fr)}
  .grid3{grid-template-columns:1fr;gap:12px}
  .card-view{padding:16px}
  .scan-area{padding:16px}
  #qr-video{max-width:100%}
  .punch{width:28px;height:28px;font-size:12px}
  .btn{padding:6px 12px;font-size:12px}
}
</style>
"""


@app.route("/customer/portal")
def customer_portal():
    """Customer portal to browse and join loyalty programs."""
    cust_id = request.args.get("cust_id")
    if not cust_id:
        # Create anonymous customer
        cust_id = create_customer(name="Guest Customer")
        return redirect(f"/customer/portal?cust_id={cust_id}")
    
    cust = get_customer(cust_id)
    if not cust:
        return redirect("/customer/portal")
    
    businesses = get_all_loyalty_businesses()
    my_cards = get_customer_cards(cust_id)
    my_biz_ids = {c["business_id"] for c in my_cards}
    
    available = [b for b in businesses if b["id"] not in my_biz_ids]
    
    return render_template_string("""<!DOCTYPE html><html><head>
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1,user-scalable=no">
<title>My Loyalty Cards</title>""" + CUSTOMER_PORTAL_CSS + """</head><body>
<div class="layout">
  <div class="sidebar">
    <div class="logo"><h1>🌲 LoyaltyLoop</h1><p>Customer Portal</p></div>
    <nav class="nav">
    <a href="/portal/dashboard">
      <span class="nav-icon">📊</span>
      <span class="nav-label">Dashboard</span>
    </a>
    <a href="/portal/calendar">
      <span class="nav-icon">📅</span>
      <span class="nav-label">Calendar</span>
    </a>
    <a href="/portal/services">
      <span class="nav-icon">✂️</span>
      <span class="nav-label">Services</span>
    </a>
    <a href="/portal/staff">
      <span class="nav-icon">👩‍💼</span>
      <span class="nav-label">Staff</span>
    </a>
    <a href="/portal/customers">
      <span class="nav-icon">👥</span>
      <span class="nav-label">Customers</span>
    </a>
    <a href="/portal/sms">
      <span class="nav-icon">💬</span>
      <span class="nav-label">SMS</span>
    </a>
    <a href="/portal/calls">
      <span class="nav-icon">📞</span>
      <span class="nav-label">Calls</span>
    </a>
    <a href="/portal/leads">
      <span class="nav-icon">🎯</span>
      <span class="nav-label">Leads</span>
    </a>
    <a href="/portal/loyalty">
      <span class="nav-icon">🎁</span>
      <span class="nav-label">Loyalty</span>
    </a>
    <a href="/portal/settings">
      <span class="nav-icon">⚙️</span>
      <span class="nav-label">Settings</span>
    </a>
  </nav>
    <div class="sidebar-footer">{{ cust_name }} &nbsp;·&nbsp; ID: {{ cust_id[:12] }}...</div>
  </div>
  <div class="main">
    <div class="topbar">
      <h2>My Loyalty Cards</h2>
      <div class="topbar-right">{{ cards|length }} programs joined</div>
    </div>
    
    {% if cards %}
    <div class="grid3">
    {% for c in cards %}
    <div class="card-view">
      <div class="card-header">
        <div>
          <div class="card-title">{{ c.business_name }}</div>
          <div class="card-type">{{ c.business_type or 'Business' }}</div>
        </div>
        <span class="badge badge-blue">{{ c.punches }}/{{ c.punches_needed }}</span>
      </div>
      
      <div class="progress-bar">
        <div class="progress-fill" style="width:{% if c.punches >= c.punches_needed %}100{% else %}{{ (c.punches/c.punches_needed*100)|int }}{% endif %}%"></div>
      </div>
      
      <div class="punch-row">
      {% for i in range(c.punches_needed) %}
        <div class="punch {{ 'filled' if i < c.punches else '' }} {{ 'reward' if i >= c.punches and c.punches >= c.punches_needed else '' }}">
          {{ '★' if i < c.punches or (i >= c.punches and c.punches >= c.punches_needed) else '' }}
        </div>
      {% endfor %}
      </div>
      
      {% if c.punches >= c.punches_needed %}
      <div style="margin:12px 0;padding:10px;background:#78350f;border-radius:6px;
                  font-size:13px;color:#fcd34d;text-align:center;">
        🎉 Reward ready! {{ c.discount_percent }}% off
      </div>
      {% else %}
      <div style="font-size:12px;color:#64748b;text-align:center;margin:12px 0;">
        {{ c.punches_needed - c.punches }} more visits to unlock {{ c.discount_percent }}% off
      </div>
      {% endif %}
      
      <div class="qr-section">
        <img src="data:image/png;base64,{{ c.qr_code }}" alt="QR Code" style="width:120px;height:120px;">
        <p>Show this QR code to scan</p>
      </div>
      
      <div style="display:flex;justify-content:space-between;align-items:center;margin-top:12px;">
        <span style="font-size:11px;color:#64748b;">{{ c.total_rewards_earned or 0 }} rewards earned</span>
        <a href="/customer/card/{{ c.id }}?cust_id={{ cust_id }}" class="btn btn-blue" style="font-size:12px;padding:6px 12px;">View</a>
      </div>
    </div>
    {% endfor %}
    </div>
    {% else %}
    <div class="panel" style="text-align:center;padding:48px;">
      <p style="font-size:16px;color:#94a3b8;margin-bottom:16px;">You haven't joined any loyalty programs yet</p>
      <a href="/customer/browse?cust_id={{ cust_id }}" class="btn btn-green">Browse Programs</a>
    </div>
    {% endif %}
  </div>
</div></body></html>
""", cust_id=cust_id, cust_name=cust['name'], cards=[{**c, 'qr_code': generate_qr_code(f"LOYALTY:{c['id']}")} for c in my_cards])


@app.route("/customer/browse")
def customer_browse():
    """Browse available loyalty programs."""
    cust_id = request.args.get("cust_id")
    if not cust_id:
        return redirect("/customer/portal")
    
    cust = get_customer(cust_id)
    businesses = get_all_loyalty_businesses()
    my_cards = get_customer_cards(cust_id)
    my_biz_ids = {c["business_id"] for c in my_cards}
    
    available = [b for b in businesses if b["id"] not in my_biz_ids]
    
    return render_template_string("""<!DOCTYPE html><html><head>
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1,user-scalable=no">
<title>Browse Programs</title>""" + CUSTOMER_PORTAL_CSS + """</head><body>
<div class="layout">
  <div class="sidebar">
    <div class="logo"><h1>🌲 LoyaltyLoop</h1><p>Customer Portal</p></div>
    <nav class="nav">
    <a href="/portal/dashboard">
      <span class="nav-icon">📊</span>
      <span class="nav-label">Dashboard</span>
    </a>
    <a href="/portal/calendar">
      <span class="nav-icon">📅</span>
      <span class="nav-label">Calendar</span>
    </a>
    <a href="/portal/services">
      <span class="nav-icon">✂️</span>
      <span class="nav-label">Services</span>
    </a>
    <a href="/portal/staff">
      <span class="nav-icon">👩‍💼</span>
      <span class="nav-label">Staff</span>
    </a>
    <a href="/portal/customers">
      <span class="nav-icon">👥</span>
      <span class="nav-label">Customers</span>
    </a>
    <a href="/portal/sms">
      <span class="nav-icon">💬</span>
      <span class="nav-label">SMS</span>
    </a>
    <a href="/portal/calls">
      <span class="nav-icon">📞</span>
      <span class="nav-label">Calls</span>
    </a>
    <a href="/portal/leads">
      <span class="nav-icon">🎯</span>
      <span class="nav-label">Leads</span>
    </a>
    <a href="/portal/loyalty">
      <span class="nav-icon">🎁</span>
      <span class="nav-label">Loyalty</span>
    </a>
    <a href="/portal/settings">
      <span class="nav-icon">⚙️</span>
      <span class="nav-label">Settings</span>
    </a>
  </nav>
    <div class="sidebar-footer">{{ cust_name }}</div>
  </div>
  <div class="main">
    <div class="topbar">
      <h2>Browse Loyalty Programs</h2>
      <div class="topbar-right">{{ available|length }} programs available</div>
    </div>
    
    <div class="grid3">
    {% for b in available %}
    <div class="biz-card">
      <h4>{{ b.name }}</h4>
      <p>{{ b.type or 'Local Business' }} · {{ b.city or 'Local' }}</p>
      <div class="biz-meta">
        <span class="tag">{{ b.punches_needed }} punches</span>
        <span class="badge badge-amber">{{ b.discount_percent }}% off</span>
      </div>
      <form method="POST" action="/customer/join" style="margin-top:16px;">
        <input type="hidden" name="cust_id" value="{{ cust_id }}">
        <input type="hidden" name="biz_id" value="{{ b.id }}">
        <button type="submit" class="btn btn-green" style="width:100%">Join Program</button>
      </form>
    </div>
    {% else %}
    <div class="panel" style="grid-column:1/-1;text-align:center;padding:48px;">
      <p style="color:#64748b;">No new programs available. Check back later!</p>
    </div>
    {% endfor %}
    </div>
  </div>
</div></body></html>
""", cust_id=cust_id, cust_name=cust['name'], available=available)


@app.route("/customer/join", methods=["POST"])
def customer_join_program():
    """Customer joins a loyalty program."""
    cust_id = request.form.get("cust_id")
    biz_id = request.form.get("biz_id")
    
    if not cust_id or not biz_id:
        return redirect("/customer/browse")
    
    card = get_or_create_customer_card(cust_id, biz_id)
    
    return redirect(f"/customer/portal?cust_id={cust_id}")


@app.route("/customer/card/<card_id>")
def customer_view_card(card_id: str):
    """View single card details."""
    cust_id = request.args.get("cust_id")
    from modules.loyalty_db import get_connection
    
    conn = get_connection()
    c = conn.cursor()
    c.execute("""
        SELECT lc.*, lb.name as business_name, lb.type as business_type,
               lb.punches_needed, lb.discount_percent, lb.city, lb.phone
        FROM loyalty_cards lc
        JOIN loyalty_businesses lb ON lc.business_id = lb.id
        WHERE lc.id = ?
    """, (card_id,))
    card = c.fetchone()
    conn.close()
    
    if not card:
        return "Card not found", 404
    
    card = dict(card)
    card['qr_code'] = generate_qr_code(f"LOYALTY:{card['id']}")
    
    return render_template_string("""<!DOCTYPE html><html><head>
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1,user-scalable=no">
<title>{{ card.business_name }} Card</title>""" + CUSTOMER_PORTAL_CSS + """</head><body>
<div class="layout">
  <div class="sidebar">
    <div class="logo"><h1>🌲 LoyaltyLoop</h1><p>Customer Portal</p></div>
    <nav class="nav">
    <a href="/portal/dashboard">
      <span class="nav-icon">📊</span>
      <span class="nav-label">Dashboard</span>
    </a>
    <a href="/portal/calendar">
      <span class="nav-icon">📅</span>
      <span class="nav-label">Calendar</span>
    </a>
    <a href="/portal/services">
      <span class="nav-icon">✂️</span>
      <span class="nav-label">Services</span>
    </a>
    <a href="/portal/staff">
      <span class="nav-icon">👩‍💼</span>
      <span class="nav-label">Staff</span>
    </a>
    <a href="/portal/customers">
      <span class="nav-icon">👥</span>
      <span class="nav-label">Customers</span>
    </a>
    <a href="/portal/sms">
      <span class="nav-icon">💬</span>
      <span class="nav-label">SMS</span>
    </a>
    <a href="/portal/calls">
      <span class="nav-icon">📞</span>
      <span class="nav-label">Calls</span>
    </a>
    <a href="/portal/leads">
      <span class="nav-icon">🎯</span>
      <span class="nav-label">Leads</span>
    </a>
    <a href="/portal/loyalty">
      <span class="nav-icon">🎁</span>
      <span class="nav-label">Loyalty</span>
    </a>
    <a href="/portal/settings">
      <span class="nav-icon">⚙️</span>
      <span class="nav-label">Settings</span>
    </a>
  </nav>
    <div class="sidebar-footer">{{ cust_name }}</div>
  </div>
  <div class="main">
    <div class="topbar">
      <h2>{{ card.business_name }}</h2>
      <span class="badge badge-blue">{{ card.punches }}/{{ card.punches_needed }}</span>
    </div>
    
    <div class="card-view" style="max-width:500px;margin:0 auto;">
      <div style="font-size:12px;color:#64748b;margin-bottom:8px;">{{ card.business_type or 'Business' }} · {{ card.city or 'Local' }}</div>
      
      <div class="progress-bar" style="height:12px;margin:16px 0;">
        <div class="progress-fill" style="width:{% if card.punches >= card.punches_needed %}100{% else %}{{ (card.punches/card.punches_needed*100)|int }}{% endif %}%"></div>
      </div>
      
      <div class="punch-row" style="justify-content:center;">
      {% for i in range(card.punches_needed) %}
        <div class="punch {{ 'filled' if i < card.punches else '' }} {{ 'reward' if i >= card.punches and card.punches >= card.punches_needed else '' }}"
             style="width:40px;height:40px;font-size:16px;">
          {{ '★' if i < card.punches or (i >= card.punches and card.punches >= card.punches_needed) else '' }}
        </div>
      {% endfor %}
      </div>
      
      {% if card.punches >= card.punches_needed %}
      <div style="margin:16px 0;padding:14px;background:#78350f;border-radius:8px;
                  font-size:15px;color:#fcd34d;text-align:center;">
        🎉 Reward unlocked! Show this card to get {{ card.discount_percent }}% off
      </div>
      {% else %}
      <div style="font-size:13px;color:#64748b;text-align:center;margin:16px 0;">
        {{ card.punches_needed - card.punches }} more visit{{ 's' if card.punches_needed - card.punches != 1 else '' }} to unlock {{ card.discount_percent }}% off
      </div>
      {% endif %}
      
      {% if card.total_rewards_earned and card.total_rewards_earned > 0 %}
      <div style="margin:12px 0;padding:10px;background:#1e293b;border-radius:6px;
                  font-size:12px;color:#94a3b8;text-align:center;">
        🏆 You've earned {{ card.total_rewards_earned }} reward{{ 's' if card.total_rewards_earned != 1 else '' }} here!
      </div>
      {% endif %}
      
      <div class="qr-section">
        <img src="data:image/png;base64,{{ card.qr_code }}" alt="QR Code" style="width:150px;height:150px;">
        <p style="font-size:12px;">Show this QR code at {{ card.business_name }} to scan</p>
      </div>
      
      <div style="margin-top:20px;padding:16px;background:#16653420;border:1px solid #166534;
                  border-radius:8px;text-align:center;">
        <div style="font-size:14px;font-weight:600;color:#86efac;margin-bottom:8px">🎁 Refer Friends</div>
        <p style="font-size:12px;color:#94a3b8;margin-bottom:12px">
          Share your referral link, earn rewards together!
        </p>
        <a href="/referrals/customer/{{ cust_id }}/card?biz_id={{ card.business_id }}" 
           class="btn btn-blue" style="display:inline-block;padding:8px 16px;text-decoration:none">
          Get Your Referral Link
        </a>
      </div>
      
      {% if card.phone %}
      <div style="margin-top:20px;padding-top:20px;border-top:1px solid #334155;
                  text-align:center;font-size:12px;color:#64748b;">
        📞 {{ card.phone }}
      </div>
      {% endif %}
    </div>
  </div>
</div></body></html>
""", card=card, cust_id=cust_id, cust_name=get_customer(cust_id)['name'] if get_customer(cust_id) else 'Customer')


# ── Business Scanner Route ─────────────────────────────────

@app.route("/business/<biz_id>/scan")
@login_required  # Use admin dashboard login for now
def business_scanner(biz_id: str):
    """Business QR scanner interface."""
    biz = get_loyalty_business(biz_id)
    if not biz:
        return "Business not found", 404
    
    # Parse success/error messages from query params
    success = request.args.get("success")
    error = request.args.get("error")
    rewards = request.args.get("rewards", "1")
    punches = request.args.get("punches", "0")
    
    result_html = ""
    result_class = "scan-result"
    
    if success == "cycle":
        result_html = f"🎉 <strong>Card Completed!</strong><br>{rewards} rewards earned total. Starting new card!"
        result_class += " success"
    elif success == "reward":
        result_html = f"✅ <strong>Reward Ready!</strong><brCustomer earned {biz['discount_percent']}% discount"
        result_class += " success"
    elif success == "punch":
        result_html = f"✓ Punch added! ({punches}/{biz['punches_needed']})"
        result_class += " success"
    elif error == "missing_card":
        result_html = "❌ Please enter a card ID"
        result_class += " error"
    elif error == "card_not_found":
        result_html = "❌ Card not found. Check the ID and try again."
        result_class += " error"
    
    return render_template_string("""<!DOCTYPE html><html><head>
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1,user-scalable=no">
<title>Scan QR - {{ biz_name }}</title>""" + CUSTOMER_PORTAL_CSS + """</head><body>
<div class="layout">
  <div class="sidebar">
    <div class="logo"><h1>🌲 LoyaltyLoop</h1><p>Business Dashboard</p></div>
    <nav class="nav">
    <a href="/portal/dashboard">
      <span class="nav-icon">📊</span>
      <span class="nav-label">Dashboard</span>
    </a>
    <a href="/portal/calendar">
      <span class="nav-icon">📅</span>
      <span class="nav-label">Calendar</span>
    </a>
    <a href="/portal/services">
      <span class="nav-icon">✂️</span>
      <span class="nav-label">Services</span>
    </a>
    <a href="/portal/staff">
      <span class="nav-icon">👩‍💼</span>
      <span class="nav-label">Staff</span>
    </a>
    <a href="/portal/customers">
      <span class="nav-icon">👥</span>
      <span class="nav-label">Customers</span>
    </a>
    <a href="/portal/sms">
      <span class="nav-icon">💬</span>
      <span class="nav-label">SMS</span>
    </a>
    <a href="/portal/calls">
      <span class="nav-icon">📞</span>
      <span class="nav-label">Calls</span>
    </a>
    <a href="/portal/leads">
      <span class="nav-icon">🎯</span>
      <span class="nav-label">Leads</span>
    </a>
    <a href="/portal/loyalty">
      <span class="nav-icon">🎁</span>
      <span class="nav-label">Loyalty</span>
    </a>
    <a href="/portal/settings">
      <span class="nav-icon">⚙️</span>
      <span class="nav-label">Settings</span>
    </a>
  </nav>
    <div class="sidebar-footer">{{ biz_name }}</div>
  </div>
  <div class="main">
    <div class="topbar">
      <h2>Scan Customer QR Code</h2>
      <span class="badge badge-blue">{{ biz.punches_needed }} punches → {{ biz.discount_percent }}% off</span>
    </div>
    
    <div class="scan-area">
      <h3 style="font-size:14px;color:#64748b;margin-bottom:12px;">Camera Scanner</h3>
      <p style="font-size:12px;color:#64748b;margin-bottom:16px;">
        Point camera at customer's QR code
      </p>
      
      <!-- Camera Scanner -->
      <div id="camera-container" style="margin-bottom:20px;">
        <div style="position:relative;max-width:400px;margin:0 auto;">
          <video id="qr-video" style="width:100%;border-radius:12px;display:block;" autoplay playsinline></video>
          <div id="scan-overlay" style="position:absolute;top:50%;left:50%;transform:translate(-50%,-50%);
               width:200px;height:200px;border:2px solid #1D9E75;border-radius:12px;
               box-shadow:0 0 0 999px rgba(0,0,0,0.5);pointer-events:none;display:none;"></div>
        </div>
        <button id="start-camera" class="btn btn-blue" onclick="startCamera()" 
                style="margin-top:16px;width:100%;max-width:400px;padding:12px;font-size:14px;">
          📷 Start Camera
        </button>
        <button id="stop-camera" class="btn btn-gray" onclick="stopCamera()" 
                style="margin-top:12px;width:100%;max-width:400px;padding:12px;font-size:14px;display:none;">
          Stop Camera
        </button>
      </div>
      
      <!-- Manual Entry -->
      <div style="border-top:1px solid #334155;padding-top:20px;margin-top:20px;">
        <h3 style="font-size:14px;color:#64748b;margin-bottom:12px;">Or Enter Manually</h3>
        
        <form method="POST" action="/business/{{ biz_id }}/punch" style="display:flex;gap:8px;flex-wrap:wrap;">
          <input type="text" name="card_id" id="manual-card-id" placeholder="Card ID" 
                 style="flex:1;min-width:200px;background:#0f172a;border:1px solid #334155;color:#e2e8f0;
                        padding:12px 14px;border-radius:8px;font-size:16px;" required>
          <button type="submit" class="btn btn-green" style="padding:12px 20px;font-size:14px;">Add Punch</button>
        </form>
      </div>
      
      <!-- Result Display -->
      <div class="scan-display" style="margin-top:24px;{% if result_html %}display:block;{% else %}display:none;{% endif %}">
        <p class="{{ result_class }}" id="scan-result" style="font-size:16px;">{{ result_html | safe }}</p>
        {% if success %}
        <button onclick="location.reload()" class="btn btn-blue" style="margin-top:12px;">Scan Another</button>
        {% endif %}
      </div>
      
      <div style="margin-top:24px;padding-top:24px;border-top:1px solid #334155;">
        <a href="/loyalty/business/{{ biz_id }}" class="btn btn-blue" style="width:100%;text-align:center;">
          ← Back to Dashboard
        </a>
      </div>
    </div>
  </div>
</div>

<script src="https://unpkg.com/html5-qrcode@2.3.8/html5-qrcode.min.js"></script>
<script>
let html5QrcodeScanner = null;
let isScanning = false;
let hasScanned = false;

function startCamera() {
  const startBtn = document.getElementById('start-camera');
  const stopBtn = document.getElementById('stop-camera');
  const overlay = document.getElementById('scan-overlay');
  
  // Hide buttons during init
  startBtn.disabled = true;
  startBtn.textContent = 'Starting...';
  
  html5QrcodeScanner = new Html5Qrcode("qr-video");
  
  const config = {
    fps: 10,
    qrbox: { width: 250, height: 250 },
    aspectRatio: 1.0
  };
  
  html5QrcodeScanner.start(
    { facingMode: "environment" },
    config,
    onScanSuccess,
    onScanError
  ).then(() => {
    startBtn.style.display = 'none';
    stopBtn.style.display = 'block';
    overlay.style.display = 'block';
    isScanning = true;
    hasScanned = false;
  }).catch(err => {
    console.error("Failed to start camera", err);
    startBtn.disabled = false;
    startBtn.textContent = '📷 Start Camera';
    document.getElementById('scan-result').innerHTML = '❌ Camera access denied. Please allow permissions and refresh.';
    document.getElementById('scan-result').parentElement.style.display = 'block';
  });
}

function stopCamera() {
  if (html5QrcodeScanner && isScanning) {
    html5QrcodeScanner.stop().then(() => {
      document.getElementById('start-camera').style.display = 'block';
      document.getElementById('start-camera').disabled = false;
      document.getElementById('start-camera').textContent = '📷 Restart Camera';
      document.getElementById('stop-camera').style.display = 'none';
      document.getElementById('scan-overlay').style.display = 'none';
      isScanning = false;
    }).catch(err => {
      console.error("Failed to stop camera", err);
    });
  }
}

function onScanSuccess(decodedText, decodedResult) {
  // Prevent multiple scans
  if (hasScanned) return;
  hasScanned = true;
  
  // Extract card ID from QR code (format: LOYALTY:card_xxx)
  const cardId = decodedText.replace('LOYALTY:', '').trim();
  
  // Validate card ID
  if (!cardId.startsWith('card_')) {
    document.getElementById('scan-result').innerHTML = '❌ Invalid QR code. Please scan customer loyalty QR.';
    document.getElementById('scan-result').parentElement.style.display = 'block';
    hasScanned = false;
    return;
  }
  
  // Show scanning feedback
  const resultDiv = document.getElementById('scan-result').parentElement;
  resultDiv.style.display = 'block';
  document.getElementById('scan-result').innerHTML = '📡 Processing...';
  
  // Stop camera
  if (isScanning) {
    stopCamera();
  }
  
  // Submit the punch via fetch
  fetch('/business/{{ biz_id }}/punch', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/x-www-form-urlencoded',
    },
    body: `card_id=${encodeURIComponent(cardId)}`
  })
  .then(response => response.text())
  .then(html => {
    // Parse result from redirected page or just reload
    window.location.href = window.location.href.split('?')[0] + '?scanned=1';
  })
  .catch(error => {
    console.error('Error:', error);
    document.getElementById('scan-result').innerHTML = '❌ Error processing punch. Try again.';
    hasScanned = false;
    // Restart camera after delay
    setTimeout(() => {
      if (!isScanning) startCamera();
    }, 2000);
  });
}

function onScanError(error) {
  // Ignore scan errors (happens frequently when no QR in view)
}

// Auto-submit manual entry on Enter key
document.getElementById('manual-card-id').addEventListener('keypress', function(e) {
  if (e.key === 'Enter') {
    e.preventDefault();
    this.form.submit();
  }
});

// Prevent zoom on iOS double-tap
document.addEventListener('dblclick', function(event) {
  event.preventDefault();
}, { passive: false });
</script>
</body></html>
""", biz_id=biz_id, biz_name=biz['name'], biz=biz, result_html=result_html, result_class=result_class)


@app.route("/business/<biz_id>/punch", methods=["POST"])
@login_required  # Use admin dashboard login for now
def business_add_punch(biz_id: str):
    """Add punch to customer card (from scanner or manual)."""
    card_id = request.form.get("card_id")
    
    if not card_id:
        return redirect(f"/business/{biz_id}/scan?error=missing_card")
    
    result = add_punch(card_id, punched_by="business", auto_reward=True)
    
    if not result:
        return redirect(f"/business/{biz_id}/scan?error=card_not_found")
    
    # Send SMS notification if reward earned
    if result.get("card_completed") or result["reward_earned"]:
        # Get customer phone for SMS
        conn = get_connection()
        c = conn.cursor()
        c.execute("""
            SELECT c.phone, c.name as customer_name
            FROM loyalty_cards lc
            JOIN loyalty_customers c ON lc.customer_id = c.id
            WHERE lc.id = ?
        """, (card_id,))
        cust = c.fetchone()
        conn.close()
        
        if cust and cust["phone"]:
            notify_on_reward_earned({
                "customer_phone": cust["phone"],
                "customer_name": cust["customer_name"],
                "business_name": result["business_name"],
                "discount_percent": result["discount_percent"],
                "total_rewards": result.get("total_rewards", 1)
            })
            
            # Check if we should send review request
            if should_send_review_request(biz_id, cust["customer_id"]):
                # Create review request
                request_id = create_review_request(biz_id, cust["customer_id"], card_id)
                review_link = f"/reviews/rate/{request_id}"
                
                # Get settings for custom message
                settings = get_review_settings(biz_id)
                
                # Send review request SMS
                send_review_request(
                    customer_phone=cust["phone"],
                    customer_name=cust["customer_name"],
                    business_name=result["business_name"],
                    review_link=review_link,
                    custom_message=settings.get("custom_message")
                )
    
    # Build success params
    params = f"card_id={card_id}"
    
    if result.get("card_completed"):
        # Auto-reward cycle triggered
        params += f"&success=cycle&rewards={result['total_rewards']}&biz_name={result['business_name']}"
    elif result["reward_earned"]:
        params += f"&success=reward&card_id={card_id}"
    else:
        params += f"&success=punch&punches={result['card']['punches']}"
    
    return redirect(f"/business/{biz_id}/scan?{params}")


# ═══════════════════════════════════════════════════════════════
# ADMIN BUSINESS MANAGEMENT - Manage all businesses
# ═══════════════════════════════════════════════════════════════

# ═══════════════════════════════════════════════════════════════
# LEGAL PAGES - Privacy Policy & Terms of Service
# ═══════════════════════════════════════════════════════════════

@app.route("/privacy")
def privacy_policy():
    """Privacy policy page."""
    return render_template_string(f"""<!DOCTYPE html><html><head>
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Privacy Policy — Piney Digital</title>
{BASE_CSS}
<style>
.legal-wrap{{max-width:800px;margin:0 auto;padding:40px 20px}}
.legal-header{{text-align:center;margin-bottom:40px}}
.legal-header h1{{font-size:28px;margin-bottom:8px}}
.legal-header p{{color:#64748b}}
.legal-content{{background:#1e293b;border-radius:12px;padding:32px;border:1px solid #334155}}
.legal-content h2{{font-size:20px;margin:32px 0 16px;color:#fff}}
.legal-content h2:first-child{{margin-top:0}}
.legal-content p{{color:#94a3b8;line-height:1.7;margin-bottom:16px}}
.legal-content ul{{color:#94a3b8;margin:16px 0 16px 24px;line-height:1.7}}
.legal-content li{{margin-bottom:8px}}
.legal-content strong{{color:#e2e8f0}}
.back-link{{display:inline-block;margin-top:24px;color:#3b82f6}}
</style>
</head><body>
<div class="legal-wrap">
  <div class="legal-header">
    <h1>🌲 Piney Digital</h1>
    <p>Privacy Policy</p>
  </div>
  <div class="legal-content">
    <p><strong>Last Updated:</strong> April 2026</p>

    <h2>1. Information We Collect</h2>
    <p>Piney Digital collects information you provide directly to us, including:</p>
    <ul>
      <li><strong>Account Information:</strong> Name, email address, phone number when you sign up for our services</li>
      <li><strong>Business Information:</strong> Business name, type, location, and contact details</li>
      <li><strong>Customer Data:</strong> Information about your customers that you add to loyalty programs</li>
      <li><strong>Usage Data:</strong> How you use our services, features accessed, and interactions</li>
    </ul>

    <h2>2. How We Use Your Information</h2>
    <p>We use the information we collect to:</p>
    <ul>
      <li>Provide, maintain, and improve our services</li>
      <li>Process transactions and send related communications</li>
      <li>Send promotional communications (with your consent)</li>
      <li>Respond to your comments, questions, and support requests</li>
      <li>Monitor and analyze trends, usage, and activities</li>
    </ul>

    <h2>3. SMS Communications</h2>
    <p>By using our services, you consent to receive SMS messages including:</p>
    <ul>
      <li>Loyalty program updates and reward notifications</li>
      <li>Appointment reminders and confirmations</li>
      <li>Review requests from businesses you've visited</li>
      <li>Account verification and security codes</li>
    </ul>
    <p>Message and data rates may apply. You can opt out at any time by replying STOP to any message.</p>

    <h2>4. Information Sharing</h2>
    <p>We do not sell your personal information. We may share your information:</p>
    <ul>
      <li>With businesses you've joined through our loyalty program</li>
      <li>With service providers who assist in our operations</li>
      <li>To comply with legal obligations or protect our rights</li>
    </ul>

    <h2>5. Data Security</h2>
    <p>We implement appropriate security measures including encryption, secure servers, and regular security audits. However, no method of transmission over the Internet is 100% secure.</p>

    <h2>6. Your Rights</h2>
    <p>You have the right to:</p>
    <ul>
      <li>Access and update your personal information</li>
      <li>Delete your account and associated data</li>
      <li>Opt out of marketing communications</li>
      <li>Request a copy of your data</li>
    </ul>

    <h2>7. Contact Us</h2>
    <p>For questions about this Privacy Policy, contact us at:</p>
    <p><strong>Email:</strong> privacy@pineydigital.com</p>

    <a href="/" class="back-link">← Back to Home</a>
  </div>
</div>
</body></html>""")


@app.route("/terms")
def terms_of_service():
    """Terms of service page."""
    return render_template_string(f"""<!DOCTYPE html><html><head>
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Terms of Service — Piney Digital</title>
{BASE_CSS}
<style>
.legal-wrap{{max-width:800px;margin:0 auto;padding:40px 20px}}
.legal-header{{text-align:center;margin-bottom:40px}}
.legal-header h1{{font-size:28px;margin-bottom:8px}}
.legal-header p{{color:#64748b}}
.legal-content{{background:#1e293b;border-radius:12px;padding:32px;border:1px solid #334155}}
.legal-content h2{{font-size:20px;margin:32px 0 16px;color:#fff}}
.legal-content h2:first-child{{margin-top:0}}
.legal-content p{{color:#94a3b8;line-height:1.7;margin-bottom:16px}}
.legal-content ul{{color:#94a3b8;margin:16px 0 16px 24px;line-height:1.7}}
.legal-content li{{margin-bottom:8px}}
.legal-content strong{{color:#e2e8f0}}
.back-link{{display:inline-block;margin-top:24px;color:#3b82f6}}
</style>
</head><body>
<div class="legal-wrap">
  <div class="legal-header">
    <h1>🌲 Piney Digital</h1>
    <p>Terms of Service</p>
  </div>
  <div class="legal-content">
    <p><strong>Last Updated:</strong> April 2026</p>

    <h2>1. Acceptance of Terms</h2>
    <p>By accessing or using Piney Digital's services, you agree to be bound by these Terms of Service. If you do not agree, please do not use our services.</p>

    <h2>2. Description of Service</h2>
    <p>Piney Digital provides:</p>
    <ul>
      <li>Loyalty program management for businesses</li>
      <li>Customer engagement and rewards tracking</li>
      <li>SMS and AI-powered outreach services</li>
      <li>Appointment scheduling and reminders</li>
      <li>Review collection and management</li>
    </ul>

    <h2>3. User Accounts</h2>
    <p>You are responsible for:</p>
    <ul>
      <li>Maintaining the confidentiality of your account credentials</li>
      <li>All activities that occur under your account</li>
      <li>Providing accurate and complete information</li>
      <li>Notifying us immediately of any unauthorized use</li>
    </ul>

    <h2>4. Acceptable Use</h2>
    <p>You agree not to:</p>
    <ul>
      <li>Use our services for unlawful purposes</li>
      <li>Violate any applicable laws or regulations</li>
      <li>Send spam or unsolicited communications</li>
      <li>Attempt to gain unauthorized access to our systems</li>
      <li>Interfere with other users' use of our services</li>
    </ul>

    <h2>5. Subscription & Payment</h2>
    <p>Paid subscriptions are billed monthly. You may cancel at any time. Refunds are provided at our discretion. Prices are subject to change with 30 days notice.</p>

    <h2>6. Data & Content</h2>
    <p>You retain ownership of your data. You grant us a license to process your data solely for providing our services. We may delete your data after account termination.</p>

    <h2>7. Limitation of Liability</h2>
    <p>Piney Digital shall not be liable for any indirect, incidental, or consequential damages. Our total liability shall not exceed the amount paid by you in the preceding 12 months.</p>

    <h2>8. Termination</h2>
    <p>We may terminate or suspend your account at any time for any reason. You may terminate your account by contacting us or through your account settings.</p>

    <h2>9. Changes to Terms</h2>
    <p>We may update these terms at any time. Continued use after changes constitutes acceptance. We will notify you of material changes.</p>

    <h2>10. Contact</h2>
    <p>For questions about these Terms, contact us at:</p>
    <p><strong>Email:</strong> legal@pineydigital.com</p>

    <a href="/" class="back-link">← Back to Home</a>
  </div>
</div>
</body></html>""")


# ═══════════════════════════════════════════════════════════════
# HEALTH CHECK & ERROR HANDLERS
# ═══════════════════════════════════════════════════════════════

@app.route("/health")
def health_check():
    """Health check endpoint for Railway monitoring."""
    try:
        # Test database connection
        conn = sqlite3.connect(DB_PATH)
        conn.execute("SELECT 1")
        conn.close()
        return jsonify({"status": "healthy", "database": "connected"}), 200
    except Exception as e:
        return jsonify({"status": "unhealthy", "error": str(e)}), 500


@app.route("/api/leads/count")
def leads_count():
    """Get lead count - public endpoint for debugging."""
    conn = get_connection()
    c = conn.cursor()

    # Total leads
    c.execute("SELECT COUNT(*) FROM leads")
    total = c.fetchone()[0]

    # Leads with phone
    c.execute("SELECT COUNT(*) FROM leads WHERE phone IS NOT NULL AND phone != ''")
    with_phone = c.fetchone()[0]

    # Leads ready to call (phone + score >= 60)
    c.execute("SELECT COUNT(*) FROM leads WHERE phone IS NOT NULL AND phone != '' AND lead_score >= 60")
    ready_to_call = c.fetchone()[0]

    conn.close()
    return jsonify({
        "total": total,
        "with_phone": with_phone,
        "ready_to_call": ready_to_call
    })


@app.route("/api/leads/seed")
@login_required
def seed_leads_manually():
    """Manually trigger lead seeding."""
    from modules.database import seed_leads_from_csv
    seed_leads_from_csv()
    return redirect(url_for("leads_count"))


@app.errorhandler(404)
def not_found(error):
    """Handle 404 errors."""
    return render_template_string(f"""<!DOCTYPE html><html><head>
<title>404 — Page Not Found</title>{BASE_CSS}</head><body>
<div class="login-wrap">
  <div class="login-card" style="text-align:center">
    <h1 style="font-size:64px;margin-bottom:16px">404</h1>
    <h2>Page Not Found</h2>
    <p style="color:#94a3b8;margin:16px 0 24px">The page you're looking for doesn't exist.</p>
    <a href="/" style="color:#22c55e">← Go Home</a>
  </div>
</div>
</body></html>"""), 404


@app.errorhandler(500)
def server_error(error):
    """Handle 500 errors."""
    logger.error(f"500 error: {error}")
    return render_template_string(f"""<!DOCTYPE html><html><head>
<title>500 — Server Error</title>{BASE_CSS}</head><body>
<div class="login-wrap">
  <div class="login-card" style="text-align:center">
    <h1 style="font-size:64px;margin-bottom:16px">500</h1>
    <h2>Server Error</h2>
    <p style="color:#94a3b8;margin:16px 0 24px">Something went wrong. Please try again later.</p>
    <a href="/" style="color:#22c55e">← Go Home</a>
  </div>
</div>
</body></html>"""), 500


# ═══════════════════════════════════════════════════════════════
# VAPI WEBHOOKS — AI Voice Call Callbacks
# ═══════════════════════════════════════════════════════════════

@app.route("/webhook/vapi/call-ended", methods=["POST"])
def vapi_call_ended():
    """
    Vapi POSTs here when an AI call ends.

    Payload includes:
    - call.id: Vapi call ID
    - call.status: ended, voicemail, transferred, no-answer
    - call.transcript: Full conversation transcript
    - call.summary: AI-generated summary
    - call.duration: Duration in seconds
    - customer.number: The lead's phone number
    """
    import re
    data = request.json or {}
    logger.info("="*50)
    logger.info("Vapi call-ended webhook received")
    logger.info("Payload: %s", json.dumps(data, indent=2)[:500])

    # Extract call data
    call_data    = data.get("call", {})
    call_id      = call_data.get("id", "unknown")
    call_status  = call_data.get("status", "ended")
    transcript   = call_data.get("transcript", "")
    summary      = call_data.get("summary", "")
    duration     = call_data.get("durationSeconds", 0)
    customer_num = data.get("customer", {}).get("number", "")

    logger.info("  Call ID   : %s", call_id)
    logger.info("  Status    : %s", call_status)
    logger.info("  Duration  : %s seconds", duration)
    logger.info("  Customer  : %s", customer_num)

    if not customer_num:
        logger.warning("  No customer number in payload")
        return {"status": "error", "message": "No customer number"}, 400

    # ── Find the lead by phone ──────────────────────────────
    # Normalize phone number
    digits = re.sub(r"\D", "", customer_num)
    if len(digits) == 11 and digits[0] == "1":
        digits = digits[1:]

    conn = get_connection()
    c = conn.cursor()
    c.execute("""
        SELECT id, business_name, city, category, outreach_status, lead_score
        FROM leads
        WHERE REPLACE(REPLACE(REPLACE(REPLACE(phone,' ',''),'-',''),'(',''),')','')
              LIKE ?
        LIMIT 1
    """, (f"%{digits}%",))
    row = c.fetchone()

    if not row:
        conn.close()
        logger.warning("  Unknown number: %s — not in leads DB", customer_num)
        return {"status": "ok", "message": "Number not in leads database"}

    lead = {
        "id": row[0],
        "business_name": row[1],
        "city": row[2],
        "category": row[3],
        "outreach_status": row[4],
        "lead_score": row[5]
    }
    conn.close()

    logger.info("  Lead found: %s (%s)", lead["business_name"], lead["city"])

    # ── Map Vapi status to our status ───────────────────────
    status_map = {
        "ended":       "called",
        "voicemail":   "voicemail",
        "transferred": "transferred",
        "no-answer":   "no_answer",
        "failed":      "failed",
    }
    our_status = status_map.get(call_status, "called")

    # ── Check transcript for intent ─────────────────────────
    if transcript:
        transcript_lower = transcript.lower()
        # Detect interest signals
        if any(phrase in transcript_lower for phrase in [
            "yes, connect me", "sure, that sounds good",
            "i'm interested", "tell me more", "how much",
            "what's the price", "let's talk"
        ]):
            our_status = "interested"
        # Detect disinterest
        elif any(phrase in transcript_lower for phrase in [
            "not interested", "no thanks", "don't call",
            "remove me", "stop calling"
        ]):
            our_status = "declined"

    # ── Update lead ────────────────────────────────────────
    update_fields = {
        "call_status":   our_status,
        "call_sid":      call_id,
        "call_transcript": transcript[:5000] if transcript else None,
        "call_summary":  summary[:1000] if summary else None,
        "call_duration": duration,
        "last_call_at":  datetime.now().isoformat(),
    }
    update_lead(lead["id"], update_fields)

    # ── Log to outreach_log ────────────────────────────────
    conn = get_connection()
    conn.execute("""
        INSERT INTO outreach_log
            (lead_id, channel, direction, body, transcript, duration, status, external_id, sent_at)
        VALUES (?, 'call', 'outbound', ?, ?, ?, ?, ?, datetime('now'))
    """, (lead["id"], f"AI call - {our_status}", transcript[:2000] if transcript else None,
          duration, our_status, call_id))
    conn.commit()
    conn.close()

    # ── Alert Joel for interested leads ────────────────────
    JOEL_PHONE = os.environ.get("JOEL_PHONE", "")
    if our_status in ["interested", "transferred"] and JOEL_PHONE:
        from modules.sms import send_sms
        alert_msg = (
            f"CALL HOT LEAD! {lead['business_name']} ({lead['city']}) "
            f"Status:{our_status} Duration:{duration}s "
            f"Call ID:{call_id}"
        )
        send_sms(JOEL_PHONE, alert_msg[:160])
        logger.info("  Alert sent to Joel for hot lead")

    logger.info("  DB updated — call_status: %s", our_status)

    return {"status": "ok", "lead_id": lead["id"], "call_status": our_status}


@app.route("/webhook/vapi/transcript", methods=["POST"])
def vapi_transcript():
    """
    Vapi POSTs here with real-time transcript updates during a call.
    Useful for logging conversation as it happens.
    """
    data = request.json or {}
    call_id = data.get("call", {}).get("id", "")
    transcript = data.get("transcript", "")

    logger.info("Vapi transcript update for call %s", call_id)
    logger.debug("Transcript: %s", transcript[:200])

    return {"status": "ok"}


@app.route("/webhook/vapi/status", methods=["POST"])
def vapi_status():
    """
    Vapi POSTs here for call status updates (ringing, in-progress, etc.)
    """
    data = request.json or {}
    call_id = data.get("call", {}).get("id", "")
    status = data.get("call", {}).get("status", "")

    logger.info("Vapi status update: call %s → %s", call_id, status)

    return {"status": "ok"}


# ═══════════════════════════════════════════════════════════════
# AUTOMATIC CALL SCHEDULER
# ═══════════════════════════════════════════════════════════════

def start_scheduled_calls():
    """
    Automatically start calling when the calling window opens.
    Runs on a schedule: 9:00 AM CT, Mon-Fri
    """
    global _last_call_start_date

    try:
        import pytz
        ct_zone = pytz.timezone("America/Chicago")
        now_ct = datetime.now(ct_zone)
    except ImportError:
        # Fallback for UTC offset
        now_ct = datetime.now()

    today_str = now_ct.strftime("%Y-%m-%d")

    # Check if we already started calls today
    if _last_call_start_date == today_str:
        logger.debug("Calls already started today: %s", today_str)
        return

    # Check if it's a weekday
    if now_ct.weekday() >= 5:  # Sat=5, Sun=6
        logger.debug("Skipping call scheduler - weekend")
        return

    # Check if we're in the calling window (9 AM - 2 PM CT)
    hour = now_ct.hour
    if hour < 9 or hour >= 14:
        logger.debug("Outside calling window - hour: %d", hour)
        return

    # Check if there are leads to call
    stats = get_call_stats()
    ready_to_call = stats.get('queued', 0) + stats.get('new', 0)

    if ready_to_call == 0:
        logger.info("No leads ready to call")
        return

    logger.info("="*50)
    logger.info("AUTO CALL SCHEDULER: Starting calls")
    logger.info("Time: %s CT", now_ct.strftime("%Y-%m-%d %H:%M"))
    logger.info("Leads ready: %d", ready_to_call)

    # Run the caller
    from modules.caller import run_caller
    try:
        result = run_caller(limit=None, dry_run=False, force=False)
        logger.info("Call batch completed: %s", result)
        _last_call_start_date = today_str
    except Exception as e:
        logger.error("Auto call scheduler error: %s", e)


def init_scheduler():
    """Initialize the APScheduler for automatic calling."""
    global _call_scheduler_running

    if _call_scheduler_running:
        return

    try:
        from apscheduler.schedulers.background import BackgroundScheduler
        from apscheduler.triggers.interval import IntervalTrigger

        scheduler = BackgroundScheduler(daemon=True)
        scheduler.add_executor('processpool')

        # Check every 5 minutes during calling window
        scheduler.add_job(
            start_scheduled_calls,
            trigger=IntervalTrigger(minutes=5),
            id='auto_call_scheduler',
            name='Auto Call Scheduler',
            replace_existing=True
        )

        scheduler.start()
        _call_scheduler_running = True
        logger.info("Call scheduler initialized - will auto-start at 9 AM CT on weekdays")
        print("  [Scheduler] Auto-call scheduler started - checks every 5 minutes")

    except Exception as e:
        logger.warning("Could not initialize scheduler: %s", e)
        print(f"  [Scheduler] Warning: Could not start auto-call scheduler: {e}")


# ── Run ────────────────────────────────────────────────────
# Initialize scheduler for production (Railway/gunicorn)
# This runs when the app is imported by gunicorn
import atexit
try:
    init_scheduler()
    atexit.register(lambda: None)  # Keep scheduler alive
except Exception as e:
    logger.warning("Scheduler init skipped: %s", e)

if __name__ == "__main__":
    port = int(os.environ.get("DASHBOARD_PORT", 5000))
    print(f"\n  Piney Digital Dashboard")
    print(f"  Running at: http://localhost:{port}")
    print(f"  Password:   {DASHBOARD_PASS}\n")
    app.run(host="0.0.0.0", port=port, debug=False)
