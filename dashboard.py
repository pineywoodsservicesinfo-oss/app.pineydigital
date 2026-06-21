#!/usr/bin/env python3
"""
dashboard.py — FieldPulse Field Service Management
Cleaned up version - focuses on SaaS functionality

Run with: python dashboard.py
Visit:    http://localhost:5000/fieldpulse

Features:
  - Business management
  - Job scheduling
  - Crew management
  - Customer portal
  - PostgreSQL multi-tenant
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
from markupsafe import escape

logger = logging.getLogger(__name__)

# ── Call Scheduler State ─────────────────────────────────────
_call_scheduler_running = False

sys.path.insert(0, str(Path(__file__).parent))

# Load environment variables FIRST (before any DB imports)
from modules.utils import load_env
load_env()

# Import database configuration AFTER loading env
from migrations.db_config import db_config, get_db_connection

# Import security modules
from modules.security import (
    generate_csrf_token, validate_csrf_token,
    get_security_headers, verify_password
)

# Import storage module for S3 uploads
from modules.storage import upload_file, is_configured as storage_configured, get_presigned_url

app = Flask(__name__)

# Security: Require these to be set in environment
DASHBOARD_SECRET = os.environ.get("DASHBOARD_SECRET")
DASHBOARD_PASS = os.environ.get("DASHBOARD_PASSWORD")

if not DASHBOARD_SECRET:
    print("WARNING: DASHBOARD_SECRET not set. Generating temporary secret.")
    import secrets
    DASHBOARD_SECRET = secrets.token_hex(32)

if not DASHBOARD_PASS:
    raise ValueError("DASHBOARD_PASSWORD environment variable must be set. Application cannot start without a secure password.")

app.secret_key = DASHBOARD_SECRET

# Session cookie security settings
app.config['SESSION_COOKIE_SECURE'] = os.environ.get('FLASK_ENV') == 'production'
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'

# Apply security headers to all responses
@app.after_request
def apply_security_headers(response):
    headers = get_security_headers()
    for header, value in headers.items():
        response.headers[header] = value
    return response

# Database path - use db_config for SQLite or PostgreSQL support
if db_config.is_sqlite:
    DB_PATH = db_config.get_sqlite_path()
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
else:
    DB_PATH = None

# Initialize all tables
from modules.database import init_db, seed_leads_from_csv
init_db()
seed_leads_from_csv()

# Seed FieldPulse demo data if empty
def seed_fieldpulse_demo():
    """Seed demo data for FieldPulse if users table is empty."""
    try:
        user_count = query_db("SELECT COUNT(*) as n FROM users", one=True)
        if user_count and user_count.get('n', 0) == 0:
            logger.info("Seeding FieldPulse demo data...")
            # Insert business
            query_db("""
                INSERT INTO businesses (id, name, slug, email, phone, plan, active)
                VALUES ('a1631c27-4b0d-4ecb-a684-2554c0acaa0e', 'Demo Landscaping Co', 'demo-landscaping',
                        'owner@demolandscaping.com', '555-0100', 'trial', true)
                ON CONFLICT (id) DO NOTHING
            """)
            # Insert user
            query_db("""
                INSERT INTO users (id, business_id, email, name, role, active)
                VALUES ('634e6557-7baf-4894-8324-00058482c290', 'a1631c27-4b0d-4ecb-a684-2554c0acaa0e',
                        'owner@demolandscaping.com', 'Demo Owner', 'owner', true)
                ON CONFLICT (id) DO NOTHING
            """)
            # Insert sample job
            query_db("""
                INSERT INTO jobs (id, business_id, title, description, customer_name, customer_phone,
                                  address, city, scheduled_date, status, estimated_duration_min)
                VALUES ('749ede4e-e0d3-4822-9697-d86ad92bfc65', 'a1631c27-4b0d-4ecb-a684-2554c0acaa0e',
                        'Lawn Maintenance - Oak St', 'Weekly lawn maintenance', 'Jane Smith',
                        '555-1234', '123 Oak St', 'Springfield', NOW(), 'scheduled', 60)
                ON CONFLICT (id) DO NOTHING
            """)
            logger.info("Demo data seeded successfully!")
    except Exception as e:
        logger.error(f"Failed to seed demo data: {e}")

seed_fieldpulse_demo()

# Register blueprints
from modules.reviews_routes import reviews_bp
from modules.bookings_routes import bookings_bp
from modules.bookings_routes_public import public_bookings_bp
from modules.bookings_self_service import self_service_bp
from modules.referrals_routes import referrals_bp

app.register_blueprint(reviews_bp)
app.register_blueprint(bookings_bp)
app.register_blueprint(public_bookings_bp)
app.register_blueprint(self_service_bp)
app.register_blueprint(referrals_bp)


# ═════════════════════════════════════════════════════════════════
# DATABASE HELPERS
# ═════════════════════════════════════════════════════════════════

def query_db(sql, params=(), one=False):
    """
    Execute a SQL query against SQLite or PostgreSQL.
    Automatically uses the correct database based on DATABASE_TYPE.
    Handles both SELECT queries and WRITE operations (INSERT/UPDATE/DELETE).
    Uses connection pooling for PostgreSQL.
    """
    try:
        from migrations.db_config import get_db_connection, release_db_connection

        conn = get_db_connection()
        is_write = sql.strip().upper().startswith(('INSERT', 'UPDATE', 'DELETE', 'CREATE', 'ALTER', 'DROP'))

        if db_config.is_postgres:
            from psycopg2.extras import RealDictCursor
            cursor = conn.cursor(cursor_factory=RealDictCursor)
            pg_sql = sql.replace('?', '%s')
            cursor.execute(pg_sql, params)

            if is_write:
                conn.commit()
                cursor.close()
                release_db_connection(conn)
                return None

            rows = cursor.fetchall()
            cursor.close()
            release_db_connection(conn)
            if one:
                return dict(rows[0]) if rows else None
            return [dict(row) for row in rows]
        else:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute(sql, params)

            if is_write:
                conn.commit()
                cursor.close()
                release_db_connection(conn)
                return None

            rows = cursor.fetchall()
            cursor.close()
            release_db_connection(conn)
            if one:
                return rows[0] if rows else None
            return rows

    except Exception as e:
        logger.error(f"Database query error: {e}")
        return None if one else []


# ═════════════════════════════════════════════════════════════════
# AUTH DECORATORS
# ═════════════════════════════════════════════════════════════════

def login_required(f):
    """Admin login decorator."""
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("logged_in"):
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated


def fp_login_required(f):
    """FieldPulse login decorator - checks business session."""
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("fp_logged_in"):
            return redirect(url_for("fieldpulse_login"))
        return f(*args, **kwargs)
    return decorated


# ═════════════════════════════════════════════════════════════════
# FIELD PULSE HELPER FUNCTIONS
# ═════════════════════════════════════════════════════════════════

# Simple in-memory cache for expensive queries
_cache = {}
_cache_times = {}
CACHE_TTL = 30  # seconds

def cached_query(key_prefix, ttl=CACHE_TTL):
    """Decorator to cache function results."""
    def decorator(f):
        def wrapper(*args, **kwargs):
            cache_key = f"{key_prefix}:{args}"
            now = datetime.now().timestamp()

            # Check cache
            if cache_key in _cache:
                if now - _cache_times.get(cache_key, 0) < ttl:
                    return _cache[cache_key]

            # Execute and cache
            result = f(*args, **kwargs)
            _cache[cache_key] = result
            _cache_times[cache_key] = now
            return result
        return wrapper
    return decorator

def clear_cache():
    """Clear all cached data."""
    _cache.clear()
    _cache_times.clear()

def get_business_from_session():
    """Get current business from session."""
    business_id = session.get("fp_business_id")
    if not business_id:
        return None
    return query_db(
        "SELECT * FROM businesses WHERE id = %s",
        (business_id,),
        one=True
    )


def get_jobs_for_business(business_id, status=None, limit=10):
    """Get jobs for a business with optional status filter."""
    if status:
        return query_db(
            """SELECT j.*, c.name as crew_name
               FROM jobs j
               LEFT JOIN crews c ON j.crew_id = c.id
               WHERE j.business_id = %s AND j.status = %s
               ORDER BY j.scheduled_date DESC
               LIMIT %s""",
            (business_id, status, limit)
        )
    return query_db(
        """SELECT j.*, c.name as crew_name
           FROM jobs j
           LEFT JOIN crews c ON j.crew_id = c.id
           WHERE j.business_id = %s
           ORDER BY j.scheduled_date DESC
           LIMIT %s""",
        (business_id, limit)
    )


@cached_query("crews", ttl=60)
def get_crews_for_business(business_id):
    """Get crews for a business."""
    return query_db(
        "SELECT * FROM crews WHERE business_id = %s AND active = true",
        (business_id,)
    )


def check_crew_availability(crew_id, scheduled_datetime, duration_min, business_id, exclude_job_id=None):
    """Check if crew is available for a given time slot.

    Returns (is_available, conflict_job) tuple.
    is_available: True if crew is available, False if there's a conflict
    conflict_job: Dict with job details if conflict exists, None otherwise
    """
    if not crew_id:
        return True, None  # No crew assigned = always available

    # Parse the scheduled datetime
    try:
        from datetime import datetime, timedelta
        if isinstance(scheduled_datetime, str):
            slot_start = datetime.strptime(scheduled_datetime, '%Y-%m-%d %H:%M:%S')
        else:
            slot_start = scheduled_datetime
        slot_end = slot_start + timedelta(minutes=int(duration_min))
    except Exception as e:
        logger.error(f"Error parsing datetime in availability check: {e}")
        return True, None  # Fail open on parse error

    # Query for overlapping jobs
    # An overlap occurs when:
    # (job_start < slot_end) AND (job_end > slot_start)
    exclude_clause = "AND j.id != %s" if exclude_job_id else ""
    params = [crew_id, business_id, slot_start, slot_end, slot_start, slot_end]
    if exclude_job_id:
        params.append(exclude_job_id)

    conflict = query_db("""
        SELECT j.id, j.title, j.scheduled_date, j.estimated_duration_min,
               j.customer_name, c.name as crew_name
        FROM jobs j
        LEFT JOIN crews c ON j.crew_id = c.id
        WHERE j.crew_id = %s
          AND j.business_id = %s
          AND j.status NOT IN ('cancelled')
          AND j.scheduled_date < %s
          AND (j.scheduled_date + INTERVAL '1 minute' * j.estimated_duration_min) > %s
        """ + exclude_clause + " LIMIT 1",
        tuple(params),
        one=True
    )

    if conflict:
        # Calculate conflict job end time for display
        conflict_start = conflict['scheduled_date']
        conflict_end = conflict_start + timedelta(minutes=conflict['estimated_duration_min'])
        conflict['end_date'] = conflict_end
        return False, conflict

    return True, None


def get_job_notes(job_id):
    """Get all notes for a job."""
    return query_db(
        """SELECT * FROM job_notes
           WHERE job_id = %s
           ORDER BY created_at DESC""",
        (job_id,)
    )


def get_job_photos(job_id):
    """Get all photos for a job."""
    photos = query_db(
        """SELECT * FROM job_photos
           WHERE job_id = %s
           ORDER BY created_at DESC""",
        (job_id,)
    )
    logger.info(f"get_job_photos for job {job_id}: found {len(photos) if photos else 0} photos")
    return photos


@cached_query("job_stats", ttl=10)
def get_job_stats(business_id):
    """Get job statistics for dashboard - optimized single query."""
    today = datetime.now().date()
    this_week_start = today - timedelta(days=today.weekday())

    # Single query to get all stats
    stats = query_db(
        """SELECT
            COUNT(*) FILTER (WHERE scheduled_date::date = %s) as today,
            COUNT(*) FILTER (WHERE scheduled_date >= %s) as this_week,
            COUNT(*) FILTER (WHERE status = 'scheduled') as scheduled,
            COUNT(*) FILTER (WHERE status = 'in_progress') as in_progress,
            COUNT(*) FILTER (WHERE status = 'completed') as completed,
            COUNT(*) FILTER (WHERE status = 'cancelled') as cancelled
           FROM jobs
           WHERE business_id = %s""",
        (today, this_week_start, business_id),
        one=True
    )

    return {
        'today': stats.get('today', 0) if stats else 0,
        'this_week': stats.get('this_week', 0) if stats else 0,
        'scheduled': stats.get('scheduled', 0) if stats else 0,
        'in_progress': stats.get('in_progress', 0) if stats else 0,
        'completed': stats.get('completed', 0) if stats else 0,
        'cancelled': stats.get('cancelled', 0) if stats else 0,
    }


# ═════════════════════════════════════════════════════════════════
# FIELD PULSE TEMPLATES & STYLES
# ═════════════════════════════════════════════════════════════════

TAILWIND_CDN = """
<script src="https://cdn.tailwindcss.com"></script>
<script>
tailwind.config = {
    darkMode: 'class',
    theme: {
        extend: {
            colors: {
                primary: {
                    50: '#ecfdf5',
                    100: '#d1fae5',
                    200: '#a7f3d0',
                    300: '#6ee7b7',
                    400: '#34d399',
                    500: '#10b981',
                    600: '#059669',
                    700: '#047857',
                    800: '#065f46',
                    900: '#064e3b',
                }
            }
        }
    }
}
</script>"""

FIELD_PULSE_CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap');
body{font-family:'Inter',sans-serif;}
.fade-in{animation:fadeIn 0.3s ease-in}
@keyframes fadeIn{from{opacity:0;transform:translateY(10px)}to{opacity:1;transform:translateY(0)}}
.slide-in{animation:slideIn 0.3s ease-out}
@keyframes slideIn{from{transform:translateX(-20px);opacity:0}to{transform:translateX(0);opacity:1}}
.status-badge{display:inline-flex;align-items:center;gap:6px;padding:6px 12px;border-radius:9999px;font-size:12px;font-weight:500}
.status-scheduled{background:#dbeafe;color:#1e40af}
.status-in_progress{background:#fef3c7;color:#92400e}
.status-completed{background:#d1fae5;color:#065f46}
.status-cancelled{background:#fee2e2;color:#991b1b}
.job-card{transition:all 0.2s}
.job-card:hover{transform:translateY(-2px);box-shadow:0 10px 40px -10px rgba(0,0,0,0.15)}
.sidebar-link{transition:all 0.15s}
.sidebar-link:hover{background:rgba(255,255,255,0.05)}
.sidebar-link.active{background:linear-gradient(90deg,#10b98120 0%,transparent 100%);border-right:3px solid #10b981}
</style>"""


# ═════════════════════════════════════════════════════════════════
# FIELD PULSE ROUTES
# ═════════════════════════════════════════════════════════════════

@app.route("/fieldpulse")
def fieldpulse_redirect():
    """Redirect to FieldPulse dashboard."""
    return redirect(url_for("fieldpulse_dashboard"))


@app.route("/fieldpulse/login", methods=["GET", "POST"])
def fieldpulse_login():
    """FieldPulse business login."""
    error = ""

    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")

        # Find user by email
        user = query_db(
            "SELECT * FROM users WHERE email = %s",
            (email,),
            one=True
        )

        if user and verify_password(password, user.get('password_hash', '')):
            session["fp_logged_in"] = True
            session["fp_user_id"] = user['id']
            session["fp_business_id"] = user['business_id']
            session["fp_user_name"] = user.get('name', email.split('@')[0])
            session["csrf_token"] = generate_csrf_token()

            # Update last login
            query_db(
                "UPDATE users SET last_login_at = NOW() WHERE id = %s",
                (user['id'],)
            )

            return redirect(url_for("fieldpulse_dashboard"))
        else:
            error = "Invalid email or password."

    return render_template_string(f"""<!DOCTYPE html>
<html lang="en" class="dark">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>FieldPulse — Login</title>
    {TAILWIND_CDN}
    {FIELD_PULSE_CSS}
</head>
<body class="bg-slate-900 min-h-screen flex items-center justify-center">
    <div class="w-full max-w-md px-6">
        <div class="bg-slate-800 rounded-2xl shadow-2xl p-8 fade-in">
            <div class="text-center mb-8">
                <div class="w-16 h-16 bg-gradient-to-br from-emerald-400 to-emerald-600 rounded-xl mx-auto mb-4 flex items-center justify-center">
                    <svg class="w-8 h-8 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 10V3L4 14h7v7l9-11h-7z"/>
                    </svg>
                </div>
                <h1 class="text-2xl font-bold text-white">FieldPulse</h1>
                <p class="text-slate-400 mt-1">Field Service Management</p>
            </div>

            {f'<div class="mb-6 p-4 bg-red-500/10 border border-red-500/20 rounded-lg text-red-400 text-sm">{error}</div>' if error else ''}

            <form method="POST" class="space-y-5">
                <div>
                    <label class="block text-sm font-medium text-slate-300 mb-2">Email</label>
                    <input type="email" name="email" required
                        class="w-full px-4 py-3 bg-slate-900 border border-slate-700 rounded-xl text-white placeholder-slate-500 focus:ring-2 focus:ring-emerald-500 focus:border-transparent transition"
                        placeholder="owner@company.com"
                        value="owner@demolandscaping.com">
                </div>

                <div>
                    <label class="block text-sm font-medium text-slate-300 mb-2">Password</label>
                    <input type="password" name="password" required
                        class="w-full px-4 py-3 bg-slate-900 border border-slate-700 rounded-xl text-white placeholder-slate-500 focus:ring-2 focus:ring-emerald-500 focus:border-transparent transition"
                        placeholder="••••••••">
                </div>

                <button type="submit"
                    class="w-full py-3 bg-emerald-500 hover:bg-emerald-600 text-white font-semibold rounded-xl transition shadow-lg shadow-emerald-500/25">
                    Sign In
                </button>
            </form>

            <p class="text-center text-slate-500 text-sm mt-6">
                Don't have an account? <a href="#" class="text-emerald-400 hover:text-emerald-300">Start free trial</a>
            </p>
        </div>
    </div>
</body>
</html>""")


def get_dashboard_data(business_id):
    """Get all dashboard data in a single database connection."""
    from migrations.db_config import get_db_connection, release_db_connection
    from psycopg2.extras import RealDictCursor
    from datetime import datetime, timedelta

    today = datetime.now().date()
    this_week_start = today - timedelta(days=today.weekday())

    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)

    try:
        # Get stats
        cursor.execute("""SELECT
            COUNT(*) FILTER (WHERE scheduled_date::date = %s) as today,
            COUNT(*) FILTER (WHERE scheduled_date >= %s) as this_week,
            COUNT(*) FILTER (WHERE status = 'scheduled') as scheduled,
            COUNT(*) FILTER (WHERE status = 'in_progress') as in_progress,
            COUNT(*) FILTER (WHERE status = 'completed') as completed,
            COUNT(*) FILTER (WHERE status = 'cancelled') as cancelled
           FROM jobs WHERE business_id = %s""",
            (today, this_week_start, business_id))
        stats_row = cursor.fetchone()
        stats = dict(stats_row) if stats_row else {}

        # Get recent jobs
        cursor.execute("""SELECT j.*, c.name as crew_name
           FROM jobs j LEFT JOIN crews c ON j.crew_id = c.id
           WHERE j.business_id = %s
           ORDER BY j.scheduled_date DESC
           LIMIT 5""", (business_id,))
        recent_jobs = [dict(row) for row in cursor.fetchall()]

        # Get crews
        cursor.execute("SELECT * FROM crews WHERE business_id = %s AND active = true",
                      (business_id,))
        crews = [dict(row) for row in cursor.fetchall()]

        return stats, recent_jobs, crews
    finally:
        cursor.close()
        release_db_connection(conn)


@app.route("/fieldpulse/dashboard")
@fp_login_required
def fieldpulse_dashboard():
    """Main FieldPulse dashboard with status filter tabs."""
    business = get_business_from_session()
    if not business:
        return redirect(url_for("fieldpulse_logout"))

    business_id = business['id']
    stats, recent_jobs, crews = get_dashboard_data(business_id)
    user_name = session.get("fp_user_name", "User")

    # Get status filter from query params
    status_filter = request.args.get('status', 'all')

    # Calculate counts for each status
    all_count = len(recent_jobs) if recent_jobs else 0
    scheduled_count = sum(1 for j in recent_jobs if j['status'] == 'scheduled') if recent_jobs else 0
    in_progress_count = sum(1 for j in recent_jobs if j['status'] == 'in_progress') if recent_jobs else 0
    completed_count = sum(1 for j in recent_jobs if j['status'] == 'completed') if recent_jobs else 0

    # Build job cards HTML with optional filtering
    job_cards = ""
    filtered_jobs = recent_jobs
    if status_filter != 'all' and recent_jobs:
        filtered_jobs = [j for j in recent_jobs if j['status'] == status_filter]

    for job in (filtered_jobs or []):
        status_class = f"status-{job['status']}"
        date_str = job['scheduled_date'].strftime('%b %d') if hasattr(job['scheduled_date'], 'strftime') else str(job['scheduled_date'])[:10]

        # Build quick action buttons based on status
        quick_actions = ""
        if job['status'] == 'scheduled':
            quick_actions = f'''<form method="POST" action="/fieldpulse/jobs/{job['id']}" class="inline">
                <input type="hidden" name="action" value="start">
                <input type="hidden" name="redirect_to" value="/fieldpulse/dashboard">
                <button type="submit" class="text-xs bg-amber-500 hover:bg-amber-600 text-white px-2 py-1 rounded font-medium transition">▶ Start</button>
            </form>'''
        elif job['status'] == 'in_progress':
            quick_actions = f'''<form method="POST" action="/fieldpulse/jobs/{job['id']}" class="inline">
                <input type="hidden" name="action" value="complete">
                <input type="hidden" name="redirect_to" value="/fieldpulse/dashboard">
                <button type="submit" class="text-xs bg-emerald-500 hover:bg-emerald-600 text-white px-2 py-1 rounded font-medium transition">✓ Complete</button>
            </form>'''

        job_cards += f"""
        <div class="job-card bg-slate-800 rounded-xl p-5 border border-slate-700 fade-in">
            <div class="flex items-start justify-between mb-3">
                <div>
                    <h3 class="font-semibold text-white">{escape(job.get('title', 'Untitled Job'))}</h3>
                    <p class="text-sm text-slate-400 mt-1">{escape(job.get('customer_name', 'Unknown Customer'))}</p>
                </div>
                <span class="status-badge {status_class}">
                    <span class="w-2 h-2 rounded-full bg-current"></span>
                    {job['status'].replace('_', ' ').title()}
                </span>
            </div>
            <div class="flex items-center justify-between">
                <div class="flex items-center gap-4 text-sm text-slate-400">
                    <span class="flex items-center gap-1">
                        <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8 7V3m8 4V3m-9 8h10M5 21h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v12a2 2 0 002 2z"/>
                        </svg>
                        {date_str}
                    </span>
                    <span class="flex items-center gap-1">
                        <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M17.657 16.657L13.414 20.9a1.998 1.998 0 01-2.827 0l-4.244-4.243a8 8 0 1111.314 0z"/>
                            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15 11a3 3 0 11-6 0 3 3  0 016 0z"/>
                        </svg>
                        {escape(job.get('city', 'No location'))}
                    </span>
                </div>
                <div class="flex items-center gap-2">
                    {quick_actions}
                    <a href="/fieldpulse/jobs/{job['id']}" class="text-xs text-slate-400 hover:text-white px-2 py-1">Edit →</a>
                </div>
            </div>
        </div>
        """

    # Build crew cards
    crew_cards = ""
    for crew in (crews or []):
        crew_cards += f"""
        <div class="bg-slate-800 rounded-xl p-4 border border-slate-700 flex items-center gap-4">
            <div class="w-12 h-12 rounded-full bg-gradient-to-br from-emerald-400 to-emerald-600 flex items-center justify-center text-white font-semibold">
                {crew.get('name', 'C')[:1]}
            </div>
            <div>
                <h4 class="font-medium text-white">{crew.get('name', 'Unnamed Crew')}</h4>
                <p class="text-sm text-slate-400">Active crew</p>
            </div>
        </div>
        """

    return render_template_string(f"""<!DOCTYPE html>
<html lang="en" class="dark">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>FieldPulse — Dashboard</title>
    {TAILWIND_CDN}
    {FIELD_PULSE_CSS}
</head>
<body class="bg-slate-900 text-white">
    <div class="flex min-h-screen">
        <!-- Sidebar -->
        <aside class="w-64 bg-slate-950 border-r border-slate-800 fixed h-full">
            <div class="p-6">
                <div class="flex items-center gap-3 mb-8">
                    <div class="w-10 h-10 bg-gradient-to-br from-emerald-400 to-emerald-600 rounded-lg flex items-center justify-center">
                        <svg class="w-5 h-5 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 10V3L4 14h7v7l9-11h-7z"/>
                        </svg>
                    </div>
                    <div>
                        <h1 class="font-bold text-lg">FieldPulse</h1>
                        <p class="text-xs text-slate-500">{business.get('name', 'Business')}</p>
                    </div>
                </div>

                <nav class="space-y-1">
                    <a href="/fieldpulse/dashboard" class="sidebar-link active flex items-center gap-3 px-4 py-3 rounded-lg text-sm font-medium text-white">
                        <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 6a2 2 0 012-2h2a2 2 0 012 2v2a2 2 0 01-2 2H6a2 2 0 01-2-2V6zM14 6a2 2 0 012-2h2a2 2 0 012 2v2a2 2 0 01-2 2h-2a2 2 0 01-2-2V6zM4 16a2 2 0 012-2h2a2 2 0 012 2v2a2 2 0 01-2 2H6a2 2 0 01-2-2v-2zM14 16a2 2 0 012-2h2a2 2 0 012 2v2a2 2 0 01-2 2h-2a2 2 0 01-2-2v-2z"/>
                        </svg>
                        Dashboard
                    </a>
                    <a href="/fieldpulse/jobs" class="sidebar-link flex items-center gap-3 px-4 py-3 rounded-lg text-sm font-medium text-slate-400 hover:text-white">
                        <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2"/>
                        </svg>
                        Jobs
                    </a>
                    <a href="#" class="sidebar-link flex items-center gap-3 px-4 py-3 rounded-lg text-sm font-medium text-slate-400 hover:text-white">
                        <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8 7V3m8 4V3m-9 8h10M5 21h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v12a2 2 0 002 2z"/>
                        </svg>
                        Schedule
                    </a>
                    <a href="#" class="sidebar-link flex items-center gap-3 px-4 py-3 rounded-lg text-sm font-medium text-slate-400 hover:text-white">
                        <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M17 20h5v-2a3 3 0 00-5.356-1.857M17 20H7m10 0v-2c0-.656-.126-1.283-.356-1.857M7 20H2v-2a3 3 0 015.356-1.857M7 20v-2c0-.656.126-1.283.356-1.857m0 0a5.002 5.002 0 019.288 0M15 7a3 3 0 11-6 0 3 3 0 016 0z"/>
                        </svg>
                        Crews
                    </a>
                </nav>
            </div>

            <div class="absolute bottom-0 left-0 right-0 p-4 border-t border-slate-800">
                <div class="flex items-center gap-3 px-4 py-2">
                    <div class="w-8 h-8 rounded-full bg-slate-700 flex items-center justify-center text-sm font-medium">
                        {user_name[:1].upper()}
                    </div>
                    <div class="flex-1 min-w-0">
                        <p class="text-sm font-medium text-white truncate">{user_name}</p>
                        <p class="text-xs text-slate-500 truncate">{business.get('subscription_tier', 'Starter').title()} Plan</p>
                    </div>
                    <a href="/fieldpulse/logout" class="text-slate-400 hover:text-white">
                        <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M17 16l4-4m0 0l-4-4m4 4H7m6 4v1a3 3 0 01-3 3H6a3 3 0 01-3-3V7a3 3 0 013-3h4a3 3 0 013 3v1"/>
                        </svg>
                    </a>
                </div>
            </div>
        </aside>

        <!-- Main Content -->
        <main class="flex-1 ml-64">
            <header class="bg-slate-900 border-b border-slate-800 px-8 py-4 sticky top-0 z-10">
                <div class="flex items-center justify-between">
                    <h2 class="text-xl font-semibold">Dashboard</h2>
                    <div class="flex items-center gap-4">
                        <span class="px-3 py-1 bg-emerald-500/10 text-emerald-400 text-sm rounded-full border border-emerald-500/20">
                            Trial ends in 13 days
                        </span>
                        <a href="/fieldpulse/jobs/new" class="bg-emerald-500 hover:bg-emerald-600 text-white px-4 py-2 rounded-lg text-sm font-medium transition">
                            + New Job
                        </a>
                    </div>
                </div>
            </header>

            <div class="p-8">
                <!-- Stats Grid -->
                <div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-6 mb-8">
                    <div class="bg-slate-800 rounded-xl p-6 border border-slate-700">
                        <div class="flex items-center justify-between mb-2">
                            <p class="text-slate-400 text-sm">Today's Jobs</p>
                            <span class="text-emerald-400 text-xs font-medium">+2</span>
                        </div>
                        <p class="text-3xl font-bold text-white">{stats['today']}</p>
                        <p class="text-slate-500 text-sm mt-1">Scheduled today</p>
                    </div>
                    <div class="bg-slate-800 rounded-xl p-6 border border-slate-700">
                        <div class="flex items-center justify-between mb-2">
                            <p class="text-slate-400 text-sm">This Week</p>
                            <span class="text-emerald-400 text-xs font-medium">+12%</span>
                        </div>
                        <p class="text-3xl font-bold text-white">{stats['this_week']}</p>
                        <p class="text-slate-500 text-sm mt-1">Total scheduled</p>
                    </div>
                    <div class="bg-slate-800 rounded-xl p-6 border border-slate-700">
                        <div class="flex items-center justify-between mb-2">
                            <p class="text-slate-400 text-sm">In Progress</p>
                            <span class="w-2 h-2 bg-amber-400 rounded-full"></span>
                        </div>
                        <p class="text-3xl font-bold text-white">{stats['in_progress']}</p>
                        <p class="text-slate-500 text-sm mt-1">Active jobs</p>
                    </div>
                    <div class="bg-slate-800 rounded-xl p-6 border border-slate-700">
                        <div class="flex items-center justify-between mb-2">
                            <p class="text-slate-400 text-sm">Completed</p>
                            <span class="w-2 h-2 bg-emerald-400 rounded-full"></span>
                        </div>
                        <p class="text-3xl font-bold text-white">{stats['completed']}</p>
                        <p class="text-slate-500 text-sm mt-1">This month</p>
                    </div>
                </div>

                <div class="grid grid-cols-1 lg:grid-cols-3 gap-8">
                    <!-- Recent Jobs -->
                    <div class="lg:col-span-2">
                        <div class="flex items-center justify-between mb-6">
                            <h3 class="text-lg font-semibold">Recent Jobs</h3>
                            <a href="/fieldpulse/jobs" class="text-emerald-400 hover:text-emerald-300 text-sm">View all →</a>
                        </div>

                        <!-- Status Filter Tabs -->
                        <div class="flex gap-2 mb-6 overflow-x-auto pb-2">
                            <a href="/fieldpulse/dashboard?status=all" class="px-4 py-2 rounded-lg text-sm font-medium transition whitespace-nowrap {'bg-slate-700 text-white border border-slate-600' if status_filter == 'all' else 'bg-slate-800 text-slate-400 hover:text-white border border-slate-700'}">
                                All ({all_count})
                            </a>
                            <a href="/fieldpulse/dashboard?status=scheduled" class="px-4 py-2 rounded-lg text-sm font-medium transition whitespace-nowrap {'bg-blue-500/20 text-blue-400 border border-blue-500/30' if status_filter == 'scheduled' else 'bg-slate-800 text-slate-400 hover:text-white border border-slate-700'}">
                                Queue ({scheduled_count})
                            </a>
                            <a href="/fieldpulse/dashboard?status=in_progress" class="px-4 py-2 rounded-lg text-sm font-medium transition whitespace-nowrap {'bg-amber-500/20 text-amber-400 border border-amber-500/30' if status_filter == 'in_progress' else 'bg-slate-800 text-slate-400 hover:text-white border border-slate-700'}">
                                In Progress ({in_progress_count})
                            </a>
                            <a href="/fieldpulse/dashboard?status=completed" class="px-4 py-2 rounded-lg text-sm font-medium transition whitespace-nowrap {'bg-emerald-500/20 text-emerald-400 border border-emerald-500/30' if status_filter == 'completed' else 'bg-slate-800 text-slate-400 hover:text-white border border-slate-700'}">
                                Done ({completed_count})
                            </a>
                        </div>

                        <div class="space-y-4">
                            {job_cards if job_cards else '<p class="text-slate-500 text-center py-8">No jobs in this category. <a href="/fieldpulse/jobs/new" class="text-emerald-400 hover:text-emerald-300">Create a new job →</a></p>'}
                        </div>
                    </div>

                    <!-- Crews & Quick Actions -->
                    <div class="space-y-6">
                        <div>
                            <h3 class="text-lg font-semibold mb-4">Your Crews</h3>
                            <div class="space-y-3">
                                {crew_cards if crew_cards else '<p class="text-slate-500 text-sm">No crews configured</p>'}
                            </div>
                        </div>

                        <div class="bg-gradient-to-br from-emerald-500/10 to-emerald-600/10 rounded-xl p-6 border border-emerald-500/20">
                            <h4 class="font-semibold text-white mb-2">Professional Plan</h4>
                            <p class="text-slate-400 text-sm mb-4">You're on a 14-day trial. Upgrade to unlock all features.</p>
                            <button class="w-full py-2 bg-emerald-500 hover:bg-emerald-600 text-white rounded-lg text-sm font-medium transition">
                                Upgrade Now
                            </button>
                        </div>
                    </div>
                </div>
            </div>
        </main>
    </div>
</body>
</html>""")


@app.route("/fieldpulse/logout")
def fieldpulse_logout():
    """Logout from FieldPulse."""
    session.pop("fp_logged_in", None)
    session.pop("fp_user_id", None)
    session.pop("fp_business_id", None)
    session.pop("fp_user_name", None)
    return redirect(url_for("fieldpulse_login"))


# ═════════════════════════════════════════════════════════════════
# ADMIN AUTH ROUTES
# ═════════════════════════════════════════════════════════════════

@app.route("/login", methods=["GET","POST"])
def login():
    """Admin login with email + password."""
    error = ""

    if request.method == "POST":
        # Validate CSRF token
        session_token = session.get('csrf_token')
        form_token = request.form.get('csrf_token')
        if not form_token or not session_token or not validate_csrf_token(form_token, session_token):
            error = "Invalid or missing CSRF token."

        if not error:
            email = request.form.get("email", "").strip().lower()
            password = request.form.get("password", "")
            admin_email = os.environ.get("ADMIN_EMAIL", "joel@pineydigital.com").lower()

            if email != admin_email:
                error = "Invalid credentials."
            elif password != DASHBOARD_PASS:
                error = "Invalid credentials."
            else:
                session["logged_in"] = True
                session["csrf_token"] = generate_csrf_token()
                return redirect(url_for("fieldpulse_dashboard"))

    # Generate and store CSRF token in session for validation
    csrf_token = generate_csrf_token()
    session['csrf_token'] = csrf_token
    csrf_token_input = f'<input type="hidden" name="csrf_token" value="{csrf_token}">'

    # Build HTML response directly - avoid Jinja2 template issues with CSS braces
    error_html = f'<p class="error">{error}</p>' if error else ''
    html_content = f"""<!DOCTYPE html>
<html><head>
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Admin Login</title>
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:sans-serif;background:#0f172a;color:#e2e8f0;min-height:100vh;display:flex;align-items:center;justify-content:center}}
.card{{background:#1e293b;padding:40px;border-radius:12px;max-width:360px;width:90%}}
h1{{margin-bottom:24px}}
input{{width:100%;padding:12px;margin-bottom:16px;border:1px solid #334155;border-radius:6px;background:#0f172a;color:#fff}}
button{{width:100%;padding:12px;background:#10b981;color:#fff;border:none;border-radius:6px;cursor:pointer}}
.error{{color:#ef4444;margin-bottom:16px}}
</style></head><body>
<div class="card">
<h1>Admin Login</h1>
{error_html}
<form method="POST">
{csrf_token_input}
<input type="email" name="email" placeholder="Email" required>
<input type="password" name="password" placeholder="Password" required>
<button type="submit">Sign In</button>
</form>
</div></body></html>"""
    return html_content


@app.route("/logout")
def logout():
    """Admin logout."""
    session.pop("logged_in", None)
    return redirect(url_for("login"))


@app.route("/admin/migrate", methods=["GET", "POST"])
@login_required
def admin_migrate():
    """Run database migrations."""
    if request.method == "POST":
        try:
            # Create job_notes table
            query_db("""
                CREATE TABLE IF NOT EXISTS job_notes (
                    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    job_id UUID REFERENCES jobs(id) ON DELETE CASCADE,
                    note TEXT NOT NULL,
                    created_by VARCHAR(100),
                    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
                )
            """)
            query_db("CREATE INDEX IF NOT EXISTS idx_notes_job ON job_notes(job_id)")

            # Create job_photos table with TEXT for photo_url
            query_db("""
                CREATE TABLE IF NOT EXISTS job_photos (
                    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    job_id UUID REFERENCES jobs(id) ON DELETE CASCADE,
                    photo_url TEXT NOT NULL,
                    photo_type VARCHAR(50) DEFAULT 'progress',
                    caption TEXT,
                    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
                )
            """)
            query_db("CREATE INDEX IF NOT EXISTS idx_photos_job ON job_photos(job_id)")

            # Fix: Alter photo_url column to TEXT if it exists as VARCHAR (for existing tables)
            try:
                query_db("ALTER TABLE job_photos ALTER COLUMN photo_url TYPE TEXT")
            except Exception:
                pass  # Column may already be TEXT or table doesn't exist yet

            return jsonify({"status": "success", "message": "Migration completed successfully!"})
        except Exception as e:
            return jsonify({"status": "error", "message": str(e)}), 500

    return """<!DOCTYPE html>
<html><head><title>Admin Tools</title></head>
<body style="font-family:sans-serif;max-width:600px;margin:40px auto;padding:20px">
<h1>Admin Tools</h1>

<div style="background:#f3f4f6;padding:20px;border-radius:8px;margin-bottom:20px">
    <h3>Database Migration</h3>
    <p>Create job_notes and job_photos tables.</p>
    <form method="POST">
        <button type="submit" style="padding:10px 20px;background:#10b981;color:white;border:none;border-radius:6px;cursor:pointer">Run Migration</button>
    </form>
</div>

<div style="background:#f3f4f6;padding:20px;border-radius:8px">
    <h3>Fix Photo Access</h3>
    <p>Make S3 bucket public so uploaded photos are viewable.</p>
    <form action="/admin/make-bucket-public" method="POST">
        <button type="submit" style="padding:10px 20px;background:#3b82f6;color:white;border:none;border-radius:6px;cursor:pointer">Make Bucket Public</button>
    </form>
</div>

</body></html>"""


@app.route("/admin/check-photos/<job_id>")
@login_required
def admin_check_photos(job_id):
    """Debug endpoint to check photos for a job."""
    try:
        photos = query_db("SELECT id, photo_type, LENGTH(photo_url) as url_length, caption, created_at FROM job_photos WHERE job_id = %s", (job_id,))
        return jsonify({
            "job_id": job_id,
            "photo_count": len(photos) if photos else 0,
            "photos": [{"id": str(p["id"]), "type": p["photo_type"], "url_length": p["url_length"], "caption": p["caption"], "created_at": str(p["created_at"])} for p in photos] if photos else []
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/admin/make-bucket-public", methods=["POST"])
@login_required
def admin_make_bucket_public():
    """Make S3 bucket public for photo access."""
    from modules.storage import make_bucket_public
    success = make_bucket_public()
    if success:
        return jsonify({"status": "success", "message": "Bucket is now public"})
    else:
        return jsonify({"status": "error", "message": "Failed to make bucket public"}), 500


# ═════════════════════════════════════════════════════════════════
# HEALTH & ERROR HANDLERS
# ═════════════════════════════════════════════════════════════════

@app.route("/health")
def health_check():
    """Health check endpoint for Railway monitoring."""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        if db_config.is_postgres:
            cursor.execute("SELECT 1")
        else:
            cursor.execute("SELECT 1")
        cursor.close()
        conn.close()
        return jsonify({
            "status": "healthy",
            "database": "connected",
            "db_type": db_config.db_type
        }), 200
    except Exception as e:
        return jsonify({"status": "unhealthy", "error": str(e)}), 500


@app.errorhandler(404)
def not_found(error):
    """Handle 404 errors."""
    return """<!DOCTYPE html>
<html><head><title>404 — Not Found</title>
<style>body{font-family:sans-serif;background:#0f172a;color:#e2e8f0;min-height:100vh;display:flex;align-items:center;justify-content:center;text-align:center}</style>
</head><body><div><h1>404</h1><p>Page not found</p><a href="/fieldpulse" style="color:#10b981">← Go to Dashboard</a></div></body></html>""", 404


@app.errorhandler(500)
def server_error(error):
    """Handle 500 errors."""
    logger.error(f"500 error: {error}")
    return """<!DOCTYPE html>
<html><head><title>500 — Server Error</title>
<style>body{font-family:sans-serif;background:#0f172a;color:#e2e8f0;min-height:100vh;display:flex;align-items:center;justify-content:center;text-align:center}</style>
</head><body><div><h1>500</h1><p>Server error</p><a href="/fieldpulse" style="color:#10b981">← Go to Dashboard</a></div></body></html>""", 500


# ═════════════════════════════════════════════════════════════════
# RUN
# ═════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    port = int(os.environ.get("DASHBOARD_PORT", 5000))
    print(f"\n  FieldPulse Dashboard")
    print(f"  Running at: http://localhost:{port}")
    print(f"  Login:      http://localhost:{port}/fieldpulse/login\n")
    app.run(host="0.0.0.0", port=port, debug=False)

# ═════════════════════════════════════════════════════════════════
# FIELD PULSE JOB ROUTES
# ═════════════════════════════════════════════════════════════════

@app.route("/fieldpulse/jobs")
@fp_login_required
def fieldpulse_jobs():
    """Job list page."""
    business = get_business_from_session()
    if not business:
        return redirect(url_for("fieldpulse_logout"))

    business_id = business['id']
    status_filter = request.args.get("status", "")

    # Get jobs with optional status filter
    if status_filter:
        jobs = query_db(
            """SELECT j.*, c.name as crew_name
               FROM jobs j LEFT JOIN crews c ON j.crew_id = c.id
               WHERE j.business_id = %s AND j.status = %s
               ORDER BY j.scheduled_date ASC""",
            (business_id, status_filter)
        )
    else:
        jobs = query_db(
            """SELECT j.*, c.name as crew_name
               FROM jobs j LEFT JOIN crews c ON j.crew_id = c.id
               WHERE j.business_id = %s
               ORDER BY j.scheduled_date ASC""",
            (business_id,)
        )

    # Build job rows
    job_rows = ""
    status_colors = {
        'scheduled': 'bg-blue-500/10 text-blue-400 border-blue-500/20',
        'in_progress': 'bg-amber-500/10 text-amber-400 border-amber-500/20',
        'completed': 'bg-emerald-500/10 text-emerald-400 border-emerald-500/20',
        'cancelled': 'bg-red-500/10 text-red-400 border-red-500/20'
    }

    for job in (jobs or []):
        status_class = status_colors.get(job['status'], 'bg-slate-700 text-slate-400')
        status_text = job['status'].replace('_', ' ').title()
        scheduled = str(job['scheduled_date'])[:16] if job['scheduled_date'] else 'Not scheduled'
        crew = job.get('crew_name') or 'Unassigned'

        # Build quick action buttons based on status
        quick_actions = ""
        if job['status'] == 'scheduled':
            quick_actions = f'''
                <form method="POST" action="/fieldpulse/jobs/{job['id']}" class="inline">
                    <input type="hidden" name="action" value="start">
                    <input type="hidden" name="redirect_to" value="/fieldpulse/jobs">
                    <button type="submit" class="text-xs bg-amber-500 hover:bg-amber-600 text-white px-2 py-1 rounded font-medium transition mr-2">▶ Start</button>
                </form>'''
        elif job['status'] == 'in_progress':
            quick_actions = f'''
                <form method="POST" action="/fieldpulse/jobs/{job['id']}" class="inline">
                    <input type="hidden" name="action" value="complete">
                    <input type="hidden" name="redirect_to" value="/fieldpulse/jobs">
                    <button type="submit" class="text-xs bg-emerald-500 hover:bg-emerald-600 text-white px-2 py-1 rounded font-medium transition mr-2">✓ Complete</button>
                </form>'''

        job_rows += f'''<tr class="border-t border-slate-700 hover:bg-slate-800/50">
            <td class="py-4 px-4">
                <div class="font-medium text-white">{escape(job.get('title', 'Untitled'))}</div>
                <div class="text-sm text-slate-500">{escape(job.get('customer_name', 'No customer'))}</div>
            </td>
            <td class="py-4 px-4 text-slate-300">{scheduled}</td>
            <td class="py-4 px-4">
                <span class="px-2 py-1 rounded text-xs font-medium border {status_class}">{status_text}</span>
            </td>
            <td class="py-4 px-4 text-slate-300">{escape(crew)}</td>
            <td class="py-4 px-4">
                {quick_actions}
                <a href="/fieldpulse/jobs/{job['id']}" class="text-emerald-400 hover:text-emerald-300 font-medium text-xs">Edit →</a>
            </td>
        </tr>'''

    if not job_rows:
        job_rows = '<tr><td colspan="5" class="py-8 text-center text-slate-500">No jobs found. Create your first job!</td></tr>'

    return render_template_string(f'''<!DOCTYPE html>
<html lang="en" class="dark">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>FieldPulse — Jobs</title>
    {TAILWIND_CDN}
    {FIELD_PULSE_CSS}
</head>
<body class="bg-slate-900 text-white">
    <div class="flex min-h-screen">
        <!-- Sidebar -->
        <aside class="w-64 bg-slate-950 border-r border-slate-800 fixed h-full">
            <div class="p-6">
                <div class="flex items-center gap-3 mb-8">
                    <div class="w-10 h-10 bg-gradient-to-br from-emerald-400 to-emerald-600 rounded-lg flex items-center justify-center">
                        <svg class="w-5 h-5 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 10V3L4 14h7v7l9-11h-7z"/>
                        </svg>
                    </div>
                    <div>
                        <h1 class="font-bold text-lg">FieldPulse</h1>
                    </div>
                </div>
                <nav class="space-y-1">
                    <a href="/fieldpulse/dashboard" class="sidebar-link flex items-center gap-3 px-4 py-3 rounded-lg text-sm font-medium text-slate-400 hover:text-white">
                        <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 6a2 2 0 012-2h2a2 2 0 012 2v2a2 2 0 01-2 2H6a2 2 0 01-2-2V6z"/>
                        </svg>
                        Dashboard
                    </a>
                    <a href="/fieldpulse/jobs" class="sidebar-link active flex items-center gap-3 px-4 py-3 rounded-lg text-sm font-medium text-white">
                        <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2"/>
                        </svg>
                        Jobs
                    </a>
                </nav>
            </div>
        </aside>

        <!-- Main Content -->
        <main class="flex-1 ml-64">
            <header class="bg-slate-900 border-b border-slate-800 px-8 py-4 sticky top-0 z-10">
                <div class="flex items-center justify-between">
                    <h2 class="text-xl font-semibold">Jobs</h2>
                    <a href="/fieldpulse/jobs/new" class="bg-emerald-500 hover:bg-emerald-600 text-white px-4 py-2 rounded-lg font-medium transition">+ New Job</a>
                </div>
            </header>

            <div class="p-8">
                <!-- Filters -->
                <div class="mb-6 flex gap-2">
                    <a href="/fieldpulse/jobs" class="px-4 py-2 rounded-lg text-sm font-medium {'bg-emerald-500 text-white' if not status_filter else 'bg-slate-800 text-slate-300 hover:text-white'}">All</a>
                    <a href="/fieldpulse/jobs?status=scheduled" class="px-4 py-2 rounded-lg text-sm font-medium {'bg-emerald-500 text-white' if status_filter == 'scheduled' else 'bg-slate-800 text-slate-300 hover:text-white'}">Scheduled</a>
                    <a href="/fieldpulse/jobs?status=in_progress" class="px-4 py-2 rounded-lg text-sm font-medium {'bg-emerald-500 text-white' if status_filter == 'in_progress' else 'bg-slate-800 text-slate-300 hover:text-white'}">In Progress</a>
                    <a href="/fieldpulse/jobs?status=completed" class="px-4 py-2 rounded-lg text-sm font-medium {'bg-emerald-500 text-white' if status_filter == 'completed' else 'bg-slate-800 text-slate-300 hover:text-white'}">Completed</a>
                </div>

                <!-- Jobs Table -->
                <div class="bg-slate-800 rounded-xl border border-slate-700 overflow-hidden">
                    <table class="w-full">
                        <thead>
                            <tr class="text-left text-sm text-slate-400 border-b border-slate-700">
                                <th class="py-3 px-4 font-medium">Job / Customer</th>
                                <th class="py-3 px-4 font-medium">Scheduled</th>
                                <th class="py-3 px-4 font-medium">Status</th>
                                <th class="py-3 px-4 font-medium">Crew</th>
                                <th class="py-3 px-4 font-medium">Action</th>
                            </tr>
                        </thead>
                        <tbody class="text-sm">
                            {job_rows}
                        </tbody>
                    </table>
                </div>
            </div>
        </main>
    </div>
</body>
</html>''')


@app.route("/fieldpulse/jobs/new", methods=["GET", "POST"])
@fp_login_required
def fieldpulse_new_job():
    """Create new job page."""
    business = get_business_from_session()
    if not business:
        return redirect(url_for("fieldpulse_logout"))

    business_id = business['id']
    error = ""

    # Get available crews for dropdown
    crews = get_crews_for_business(business_id)
    crew_options = '<option value="">Unassigned</option>'
    for crew in (crews or []):
        crew_options += f'<option value="{crew["id"]}">{crew.get("name", "Unnamed Crew")}</option>'

    if request.method == "POST":
        title = request.form.get("title", "").strip()
        customer_name = request.form.get("customer_name", "").strip()
        customer_phone = request.form.get("customer_phone", "").strip()
        customer_email = request.form.get("customer_email", "").strip()
        address = request.form.get("address", "").strip()
        city = request.form.get("city", "").strip()
        scheduled_date = request.form.get("scheduled_date", "").strip()
        scheduled_time = request.form.get("scheduled_time", "").strip()
        estimated_duration = request.form.get("estimated_duration", "60").strip()
        crew_id = request.form.get("crew_id", "").strip()
        description = request.form.get("description", "").strip()

        if not title:
            error = "Job title is required"
        elif not customer_name:
            error = "Customer name is required"
        elif not scheduled_date:
            error = "Scheduled date is required"
        else:
            # Combine date and time
            if scheduled_time:
                scheduled_datetime = f"{scheduled_date} {scheduled_time}:00"
            else:
                scheduled_datetime = f"{scheduled_date} 09:00:00"

            # Check crew availability
            is_available, conflict = check_crew_availability(
                crew_id if crew_id else None,
                scheduled_datetime,
                int(estimated_duration) if estimated_duration else 60,
                business_id
            )

            if not is_available:
                from datetime import timedelta
                conflict_start = conflict['scheduled_date']
                conflict_end = conflict_start + timedelta(minutes=conflict['estimated_duration_min'])
                error = f"Crew is already booked during this time slot. Conflict: '{conflict['title']}' for {conflict['customer_name']} ({conflict_start.strftime('%I:%M %p')} - {conflict_end.strftime('%I:%M %p')})"
            else:
                try:
                    query_db("""INSERT INTO jobs
                        (business_id, title, description, customer_name, customer_phone, customer_email,
                         address, city, scheduled_date, status, crew_id, estimated_duration_min)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 'scheduled', %s, %s)""",
                        (business_id, title, description, customer_name, customer_phone, customer_email,
                         address, city, scheduled_datetime, crew_id if crew_id else None,
                         int(estimated_duration) if estimated_duration else 60))
                    return redirect(url_for("fieldpulse_jobs"))
                except Exception as e:
                    logger.error(f"Error creating job: {e}")
                    error = f"Error creating job: {str(e)}"

    return render_template_string(f'''<!DOCTYPE html>
<html lang="en" class="dark">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>FieldPulse — New Job</title>
    {TAILWIND_CDN}
    {FIELD_PULSE_CSS}
</head>
<body class="bg-slate-900 text-white">
    <div class="flex min-h-screen">
        <!-- Sidebar -->
        <aside class="w-64 bg-slate-950 border-r border-slate-800 fixed h-full">
            <div class="p-6">
                <div class="flex items-center gap-3 mb-8">
                    <div class="w-10 h-10 bg-gradient-to-br from-emerald-400 to-emerald-600 rounded-lg flex items-center justify-center">
                        <svg class="w-5 h-5 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 10V3L4 14h7v7l9-11h-7z"/>
                        </svg>
                    </div>
                    <div>
                        <h1 class="font-bold text-lg">FieldPulse</h1>
                    </div>
                </div>
                <nav class="space-y-1">
                    <a href="/fieldpulse/dashboard" class="sidebar-link flex items-center gap-3 px-4 py-3 rounded-lg text-sm font-medium text-slate-400 hover:text-white">
                        <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 6a2 2 0 012-2h2a2 2 0 012 2v2a2 2 0 01-2 2H6a2 2 0 01-2-2V6z"/>
                        </svg>
                        Dashboard
                    </a>
                    <a href="/fieldpulse/jobs" class="sidebar-link active flex items-center gap-3 px-4 py-3 rounded-lg text-sm font-medium text-white">
                        <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2"/>
                        </svg>
                        Jobs
                    </a>
                </nav>
            </div>
        </aside>

        <!-- Main Content -->
        <main class="flex-1 ml-64">
            <header class="bg-slate-900 border-b border-slate-800 px-8 py-4 sticky top-0 z-10">
                <div class="flex items-center gap-4">
                    <a href="/fieldpulse/jobs" class="text-slate-400 hover:text-white">← Back to Jobs</a>
                    <h2 class="text-xl font-semibold">New Job</h2>
                </div>
            </header>

            <div class="p-8 max-w-2xl">
                {f'<div class="mb-6 p-4 bg-red-500/10 border border-red-500/20 rounded-lg text-red-400">{error}</div>' if error else ''}

                <form method="POST" class="space-y-6">
                    <div class="bg-slate-800 rounded-xl p-6 border border-slate-700">
                        <h3 class="text-lg font-medium text-white mb-4">Job Details</h3>
                        <div class="space-y-4">
                            <div>
                                <label class="block text-sm font-medium text-slate-300 mb-2">Job Title *</label>
                                <input type="text" name="title" required class="w-full px-4 py-2 bg-slate-900 border border-slate-700 rounded-lg text-white focus:ring-2 focus:ring-emerald-500 focus:border-transparent">
                            </div>
                            <div>
                                <label class="block text-sm font-medium text-slate-300 mb-2">Description</label>
                                <textarea name="description" rows="3" class="w-full px-4 py-2 bg-slate-900 border border-slate-700 rounded-lg text-white focus:ring-2 focus:ring-emerald-500 focus:border-transparent"></textarea>
                            </div>
                        </div>
                    </div>

                    <div class="bg-slate-800 rounded-xl p-6 border border-slate-700">
                        <h3 class="text-lg font-medium text-white mb-4">Customer Information</h3>
                        <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
                            <div class="md:col-span-2">
                                <label class="block text-sm font-medium text-slate-300 mb-2">Customer Name *</label>
                                <input type="text" name="customer_name" required class="w-full px-4 py-2 bg-slate-900 border border-slate-700 rounded-lg text-white focus:ring-2 focus:ring-emerald-500 focus:border-transparent">
                            </div>
                            <div>
                                <label class="block text-sm font-medium text-slate-300 mb-2">Phone</label>
                                <input type="tel" name="customer_phone" class="w-full px-4 py-2 bg-slate-900 border border-slate-700 rounded-lg text-white focus:ring-2 focus:ring-emerald-500 focus:border-transparent">
                            </div>
                            <div>
                                <label class="block text-sm font-medium text-slate-300 mb-2">Email</label>
                                <input type="email" name="customer_email" class="w-full px-4 py-2 bg-slate-900 border border-slate-700 rounded-lg text-white focus:ring-2 focus:ring-emerald-500 focus:border-transparent">
                            </div>
                            <div class="md:col-span-2">
                                <label class="block text-sm font-medium text-slate-300 mb-2">Address</label>
                                <input type="text" name="address" class="w-full px-4 py-2 bg-slate-900 border border-slate-700 rounded-lg text-white focus:ring-2 focus:ring-emerald-500 focus:border-transparent">
                            </div>
                            <div class="md:col-span-2">
                                <label class="block text-sm font-medium text-slate-300 mb-2">City</label>
                                <input type="text" name="city" class="w-full px-4 py-2 bg-slate-900 border border-slate-700 rounded-lg text-white focus:ring-2 focus:ring-emerald-500 focus:border-transparent">
                            </div>
                        </div>
                    </div>

                    <div class="bg-slate-800 rounded-xl p-6 border border-slate-700">
                        <h3 class="text-lg font-medium text-white mb-4">Scheduling</h3>

                        <!-- Date Selection -->
                        <div class="mb-6">
                            <label class="block text-sm font-medium text-slate-300 mb-3">Date *</label>
                            <input type="date" name="scheduled_date" required
                                class="w-full px-4 py-3 bg-slate-900 border border-slate-700 rounded-lg text-white focus:ring-2 focus:ring-emerald-500 focus:border-transparent text-lg"
                                style="color-scheme: dark;"
                                min="{datetime.now().strftime('%Y-%m-%d')}">
                            <p class="text-xs text-slate-500 mt-2">Click to open calendar • Minimum date is today</p>
                        </div>

                        <!-- Time Selection -->
                        <div class="mb-6">
                            <label class="block text-sm font-medium text-slate-300 mb-3">Time</label>
                            <div class="grid grid-cols-4 gap-2">
                                <label class="cursor-pointer">
                                    <input type="radio" name="scheduled_time" value="08:00" class="peer sr-only">
                                    <div class="px-3 py-2 bg-slate-900 border border-slate-700 rounded-lg text-center text-sm text-slate-300 peer-checked:bg-emerald-500 peer-checked:border-emerald-500 peer-checked:text-white hover:border-slate-500 transition">
                                        8:00 AM
                                    </div>
                                </label>
                                <label class="cursor-pointer">
                                    <input type="radio" name="scheduled_time" value="09:00" class="peer sr-only" checked>
                                    <div class="px-3 py-2 bg-slate-900 border border-slate-700 rounded-lg text-center text-sm text-slate-300 peer-checked:bg-emerald-500 peer-checked:border-emerald-500 peer-checked:text-white hover:border-slate-500 transition">
                                        9:00 AM
                                    </div>
                                </label>
                                <label class="cursor-pointer">
                                    <input type="radio" name="scheduled_time" value="10:00" class="peer sr-only">
                                    <div class="px-3 py-2 bg-slate-900 border border-slate-700 rounded-lg text-center text-sm text-slate-300 peer-checked:bg-emerald-500 peer-checked:border-emerald-500 peer-checked:text-white hover:border-slate-500 transition">
                                        10:00 AM
                                    </div>
                                </label>
                                <label class="cursor-pointer">
                                    <input type="radio" name="scheduled_time" value="11:00" class="peer sr-only">
                                    <div class="px-3 py-2 bg-slate-900 border border-slate-700 rounded-lg text-center text-sm text-slate-300 peer-checked:bg-emerald-500 peer-checked:border-emerald-500 peer-checked:text-white hover:border-slate-500 transition">
                                        11:00 AM
                                    </div>
                                </label>
                                <label class="cursor-pointer">
                                    <input type="radio" name="scheduled_time" value="12:00" class="peer sr-only">
                                    <div class="px-3 py-2 bg-slate-900 border border-slate-700 rounded-lg text-center text-sm text-slate-300 peer-checked:bg-emerald-500 peer-checked:border-emerald-500 peer-checked:text-white hover:border-slate-500 transition">
                                        12:00 PM
                                    </div>
                                </label>
                                <label class="cursor-pointer">
                                    <input type="radio" name="scheduled_time" value="13:00" class="peer sr-only">
                                    <div class="px-3 py-2 bg-slate-900 border border-slate-700 rounded-lg text-center text-sm text-slate-300 peer-checked:bg-emerald-500 peer-checked:border-emerald-500 peer-checked:text-white hover:border-slate-500 transition">
                                        1:00 PM
                                    </div>
                                </label>
                                <label class="cursor-pointer">
                                    <input type="radio" name="scheduled_time" value="14:00" class="peer sr-only">
                                    <div class="px-3 py-2 bg-slate-900 border border-slate-700 rounded-lg text-center text-sm text-slate-300 peer-checked:bg-emerald-500 peer-checked:border-emerald-500 peer-checked:text-white hover:border-slate-500 transition">
                                        2:00 PM
                                    </div>
                                </label>
                                <label class="cursor-pointer">
                                    <input type="radio" name="scheduled_time" value="15:00" class="peer sr-only">
                                    <div class="px-3 py-2 bg-slate-900 border border-slate-700 rounded-lg text-center text-sm text-slate-300 peer-checked:bg-emerald-500 peer-checked:border-emerald-500 peer-checked:text-white hover:border-slate-500 transition">
                                        3:00 PM
                                    </div>
                                </label>
                                <label class="cursor-pointer">
                                    <input type="radio" name="scheduled_time" value="16:00" class="peer sr-only">
                                    <div class="px-3 py-2 bg-slate-900 border border-slate-700 rounded-lg text-center text-sm text-slate-300 peer-checked:bg-emerald-500 peer-checked:border-emerald-500 peer-checked:text-white hover:border-slate-500 transition">
                                        4:00 PM
                                    </div>
                                </label>
                                <label class="cursor-pointer">
                                    <input type="radio" name="scheduled_time" value="17:00" class="peer sr-only">
                                    <div class="px-3 py-2 bg-slate-900 border border-slate-700 rounded-lg text-center text-sm text-slate-300 peer-checked:bg-emerald-500 peer-checked:border-emerald-500 peer-checked:text-white hover:border-slate-500 transition">
                                        5:00 PM
                                    </div>
                                </label>
                            </div>
                            <p class="text-xs text-slate-500 mt-2">9:00 AM selected by default</p>
                        </div>

                        <!-- Duration & Crew -->
                        <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
                            <div>
                                <label class="block text-sm font-medium text-slate-300 mb-2">Est. Duration</label>
                                <select name="estimated_duration" class="w-full px-4 py-2 bg-slate-900 border border-slate-700 rounded-lg text-white focus:ring-2 focus:ring-emerald-500 focus:border-transparent">
                                    <option value="30">30 minutes</option>
                                    <option value="60" selected>1 hour</option>
                                    <option value="90">1.5 hours</option>
                                    <option value="120">2 hours</option>
                                    <option value="180">3 hours</option>
                                    <option value="240">4 hours</option>
                                    <option value="480">Full day (8 hours)</option>
                                </select>
                            </div>
                            <div>
                                <label class="block text-sm font-medium text-slate-300 mb-2">Crew</label>
                                <select name="crew_id" class="w-full px-4 py-2 bg-slate-900 border border-slate-700 rounded-lg text-white focus:ring-2 focus:ring-emerald-500 focus:border-transparent">{crew_options}</select>
                            </div>
                        </div>
                    </div>

                    <div class="flex gap-4">
                        <button type="submit" class="bg-emerald-500 hover:bg-emerald-600 text-white px-6 py-3 rounded-lg font-medium transition">Create Job</button>
                        <a href="/fieldpulse/jobs" class="bg-slate-700 hover:bg-slate-600 text-white px-6 py-3 rounded-lg font-medium transition">Cancel</a>
                    </div>
                </form>
            </div>
        </main>
    </div>
</body>
</html>''')


# Job Detail Route - to be appended to dashboard.py

@app.route("/fieldpulse/jobs/<job_id>", methods=["GET", "POST"])
@fp_login_required
def fieldpulse_job_detail(job_id):
    """View and edit job details with status actions."""
    business = get_business_from_session()
    if not business:
        return redirect(url_for("fieldpulse_logout"))

    business_id = business['id']
    error = ""
    success = ""
    user_name = session.get("fp_user_name", "User")

    # Get job details
    job = query_db(
        "SELECT j.*, c.name as crew_name FROM jobs j LEFT JOIN crews c ON j.crew_id = c.id WHERE j.id = %s AND j.business_id = %s",
        (job_id, business_id),
        one=True
    )

    if not job:
        return redirect(url_for("fieldpulse_jobs"))

    # Get available crews for dropdown
    crews = get_crews_for_business(business_id)
    crew_options = '<option value="">Unassigned</option>'
    for crew in (crews or []):
        selected = 'selected' if job.get('crew_id') == crew['id'] else ''
        crew_options += f'<option value="{crew["id"]}" {selected}>{crew.get("name", "Unnamed Crew")}</option>'

    # Handle status update (POST from buttons)
    if request.method == "POST":
        action = request.form.get("action", "")

        # Get redirect destination (from hidden field or referrer) - validate for open redirect
        redirect_to = request.form.get("redirect_to") or request.headers.get("Referer", "/fieldpulse/jobs")
        # Security: Only allow relative redirects to our own domain
        ALLOWED_REDIRECTS = ['/fieldpulse/dashboard', '/fieldpulse/jobs', '/fieldpulse/schedule', '/fieldpulse/crew']
        if redirect_to not in ALLOWED_REDIRECTS:
            redirect_to = "/fieldpulse/jobs"

        if action == "start":
            query_db("UPDATE jobs SET status = 'in_progress', started_at = NOW() WHERE id = %s", (job_id,))
            return redirect(redirect_to)

        elif action == "complete":
            query_db("UPDATE jobs SET status = 'completed', completed_at = NOW() WHERE id = %s", (job_id,))
            return redirect(redirect_to)

        elif action == "cancel":
            query_db("UPDATE jobs SET status = 'cancelled', updated_at = NOW() WHERE id = %s", (job_id,))
            return redirect(redirect_to)

        elif action == "update":
            # Update job details
            title = request.form.get("title", "").strip()
            customer_name = request.form.get("customer_name", "").strip()
            customer_phone = request.form.get("customer_phone", "").strip()
            customer_email = request.form.get("customer_email", "").strip()
            address = request.form.get("address", "").strip()
            city = request.form.get("city", "").strip()
            scheduled_date = request.form.get("scheduled_date", "").strip()
            scheduled_time = request.form.get("scheduled_time", "").strip()
            estimated_duration = request.form.get("estimated_duration", "").strip()
            crew_id = request.form.get("crew_id", "").strip()
            description = request.form.get("description", "").strip()

            if scheduled_date:
                if scheduled_time:
                    scheduled_datetime = f"{scheduled_date} {scheduled_time}:00"
                else:
                    scheduled_datetime = f"{scheduled_date} 09:00:00"
            else:
                scheduled_datetime = job['scheduled_date']

            # Check crew availability (exclude current job from conflict check)
            is_available, conflict = check_crew_availability(
                crew_id if crew_id else None,
                scheduled_datetime,
                int(estimated_duration) if estimated_duration else job['estimated_duration_min'],
                business_id,
                exclude_job_id=job_id
            )

            if not is_available:
                from datetime import timedelta
                conflict_start = conflict['scheduled_date']
                conflict_end = conflict_start + timedelta(minutes=conflict['estimated_duration_min'])
                error = f"Crew is already booked during this time slot. Conflict: '{conflict['title']}' for {conflict['customer_name']} ({conflict_start.strftime('%I:%M %p')} - {conflict_end.strftime('%I:%M %p')})"
            else:
                try:
                    query_db("""UPDATE jobs SET title = %s, customer_name = %s, customer_phone = %s, customer_email = %s, address = %s, city = %s, scheduled_date = %s, crew_id = %s, estimated_duration_min = %s, description = %s, updated_at = NOW() WHERE id = %s""",
                        (title or job['title'], customer_name or job['customer_name'], customer_phone or job['customer_phone'], customer_email or job['customer_email'], address or job['address'], city or job['city'], scheduled_datetime, crew_id if crew_id else None, int(estimated_duration) if estimated_duration else job['estimated_duration_min'], description or job['description'], job_id))
                    success = "Job updated successfully!"
                    # Refresh job data
                    job = query_db("SELECT j.*, c.name as crew_name FROM jobs j LEFT JOIN crews c ON j.crew_id = c.id WHERE j.id = %s AND j.business_id = %s", (job_id, business_id), one=True)
                except Exception as e:
                    logger.error(f"Error updating job: {e}")
                    error = f"Error updating job: {str(e)}"

        elif action == "add_note":
            note_text = request.form.get("note", "").strip()
            if note_text:
                try:
                    query_db("""INSERT INTO job_notes (job_id, note, created_by, created_at)
                               VALUES (%s, %s, %s, NOW())""",
                            (job_id, note_text, user_name))
                    success = "Note added successfully!"
                except Exception as e:
                    logger.error(f"Error adding note: {e}")
                    error = f"Error adding note: {str(e)}"

        elif action == "upload_photo":
            # Handle photo upload
            if 'photo' not in request.files:
                error = "No photo file provided"
            else:
                photo_file = request.files['photo']
                if photo_file.filename == '':
                    error = "No photo selected"
                elif photo_file:
                    # Validate file type
                    allowed_extensions = {'.jpg', '.jpeg', '.png', '.gif', '.webp'}
                    file_ext = '.' + photo_file.filename.rsplit('.', 1)[1].lower() if '.' in photo_file.filename else ''
                    if file_ext not in allowed_extensions:
                        error = f"Invalid file type. Allowed: {', '.join(allowed_extensions)}"
                    else:
                        # Read file data
                        photo_data = photo_file.read()
                        file_size = len(photo_data)

                        # Check file size (5MB limit)
                        if file_size > 5 * 1024 * 1024:
                            error = f"File too large ({file_size / 1024 / 1024:.1f}MB). Max size: 5MB"
                        else:
                            # Resize image if too large (max 1200px width/height)
                            try:
                                from PIL import Image
                                import io

                                img = Image.open(io.BytesIO(photo_data))
                                max_size = 1200
                                if img.width > max_size or img.height > max_size:
                                    ratio = min(max_size / img.width, max_size / img.height)
                                    new_size = (int(img.width * ratio), int(img.height * ratio))
                                    img = img.resize(new_size, Image.Resampling.LANCZOS)

                                # Convert to JPEG for consistency and smaller size
                                output = io.BytesIO()
                                if img.mode in ('RGBA', 'P'):
                                    img = img.convert('RGB')
                                img.save(output, format='JPEG', quality=85, optimize=True)
                                photo_data = output.getvalue()
                            except ImportError:
                                # PIL not available, use original
                                pass
                            except Exception as img_err:
                                logger.warning(f"Image resize failed: {img_err}, using original")

                            # Upload to S3/Object Storage instead of base64
                            photo_type = request.form.get("photo_type", "progress")
                            caption = request.form.get("photo_caption", "").strip()

                            # Determine content type based on processed image
                            content_type = 'image/jpeg'

                            if storage_configured():
                                photo_url = upload_file(
                                    photo_data,
                                    photo_file.filename,
                                    content_type=content_type,
                                    folder=f"jobs/{job_id}"
                                )
                            else:
                                # Fallback to base64 if S3 not configured
                                import base64
                                photo_base64 = base64.b64encode(photo_data).decode('utf-8')
                                photo_url = f"data:image/jpeg;base64,{photo_base64}"
                                logger.warning(f"S3 not configured, using base64 fallback for job {job_id}")

                            if photo_url:
                                try:
                                    query_db("""INSERT INTO job_photos
                                               (job_id, photo_url, photo_type, caption, created_at)
                                               VALUES (%s, %s, %s, %s, NOW())""",
                                            (job_id, photo_url, photo_type, caption if caption else None))
                                    success = f"Photo uploaded successfully! ({len(photo_data) / 1024:.0f}KB)"
                                    logger.info(f"Photo uploaded for job {job_id}: {photo_url}")
                                except Exception as e:
                                    logger.error(f"Error saving photo to database for job {job_id}: {e}")
                                    error = f"Error saving photo: {str(e)}"
                            else:
                                error = "Failed to upload photo to storage"

        elif action == "delete_photo":
            # Handle photo deletion
            photo_id = request.form.get("photo_id", "").strip()
            if not photo_id:
                error = "No photo ID provided"
            else:
                try:
                    # Get photo details before deleting
                    photo = query_db(
                        "SELECT id, photo_url FROM job_photos WHERE id = %s AND job_id = %s",
                        (photo_id, job_id),
                        one=True
                    )
                    if photo:
                        # Delete from S3 first
                        from modules.storage import delete_file
                        delete_file(photo['photo_url'])

                        # Delete from database
                        query_db("DELETE FROM job_photos WHERE id = %s", (photo_id,))
                        success = "Photo deleted successfully"
                        logger.info(f"Photo {photo_id} deleted from job {job_id}")
                    else:
                        error = "Photo not found or access denied"
                except Exception as e:
                    logger.error(f"Error deleting photo {photo_id}: {e}")
                    error = f"Error deleting photo: {str(e)}"

    # Get notes for this job
    notes = get_job_notes(job_id)
    notes_html = ""
    if notes:
        for note in notes:
            note_time = note['created_at'].strftime('%b %d, %Y at %I:%M %p') if note['created_at'] else ''
            notes_html += f'''
            <div class="bg-slate-700/50 rounded-lg p-4 border border-slate-600">
                <div class="flex items-center justify-between mb-2">
                    <span class="text-sm font-medium text-slate-300">{escape(note.get('created_by', 'Unknown'))}</span>
                    <span class="text-xs text-slate-500">{note_time}</span>
                </div>
                <p class="text-slate-200 whitespace-pre-wrap">{escape(note.get('note', ''))}</p>
            </div>'''
    else:
        notes_html = '<p class="text-slate-500 text-center py-4">No notes yet. Add the first note below.</p>'

    # Get photos for this job
    photos = get_job_photos(job_id)
    logger.info(f"Job {job_id}: {len(photos) if photos else 0} photos retrieved")
    photos_html = ""
    if photos:
        photo_type_colors = {
            'before': 'bg-blue-500/20 text-blue-400 border-blue-500/30',
            'after': 'bg-emerald-500/20 text-emerald-400 border-emerald-500/30',
            'progress': 'bg-amber-500/20 text-amber-400 border-amber-500/30',
            'issue': 'bg-red-500/20 text-red-400 border-red-500/30'
        }
        for photo in photos:
            photo_time = photo['created_at'].strftime('%b %d, %Y at %I:%M %p') if photo['created_at'] else ''
            type_class = photo_type_colors.get(photo.get('photo_type', 'progress'), 'bg-slate-700 text-slate-400')
            type_label = escape(photo.get('photo_type', 'progress').replace('_', ' ').title())
            caption = escape(photo.get('caption', '') or '')
            photo_id = photo.get('id', '')
            # Use presigned URL for viewing (bypasses access restrictions)
            photo_url = get_presigned_url(photo.get('photo_url', ''), expiration=3600)
            photos_html += f'''
            <div class="bg-slate-700/50 rounded-lg overflow-hidden border border-slate-600">
                <div class="relative group">
                    <a href="{photo_url}" target="_blank" class="block cursor-zoom-in">
                        <img src="{photo_url}" alt="Job photo" class="w-full h-48 object-cover transition transform group-hover:scale-105">
                        <div class="absolute inset-0 bg-black/0 group-hover:bg-black/20 transition flex items-center justify-center">
                            <span class="opacity-0 group-hover:opacity-100 text-white text-2xl">🔍</span>
                        </div>
                    </a>
                    <span class="absolute top-2 right-2 px-2 py-1 text-xs font-medium rounded border {type_class}">{type_label}</span>
                    <form method="POST" class="absolute top-2 left-2" onsubmit="return confirm('Delete this photo?');">
                        <input type="hidden" name="action" value="delete_photo">
                        <input type="hidden" name="photo_id" value="{photo_id}">
                        <button type="submit" class="bg-red-500/80 hover:bg-red-600 text-white text-xs px-2 py-1 rounded transition">🗑️</button>
                    </form>
                </div>
                <div class="p-3">
                    <p class="text-xs text-slate-500 mb-1">{photo_time}</p>
                    {f'<p class="text-sm text-slate-300">{caption}</p>' if caption else ''}
                </div>
            </div>'''
    else:
        photos_html = '<p class="text-slate-500 text-center py-4">No photos yet. Upload the first photo below.</p>'

    # Status display and actions
    status_colors = {
        'scheduled': 'bg-blue-500/10 text-blue-400 border-blue-500/20',
        'in_progress': 'bg-amber-500/10 text-amber-400 border-amber-500/20',
        'completed': 'bg-emerald-500/10 text-emerald-400 border-emerald-500/20',
        'cancelled': 'bg-red-500/10 text-red-400 border-red-500/20'
    }
    status_class = status_colors.get(job['status'], 'bg-slate-700 text-slate-400')

    # Build action buttons based on status
    action_buttons = ""
    if job['status'] == 'scheduled':
        action_buttons = '<form method="POST" class="inline"><input type="hidden" name="action" value="start"><button type="submit" class="bg-amber-500 hover:bg-amber-600 text-white px-4 py-2 rounded-lg font-medium transition">▶ Start Job</button></form>'
    elif job['status'] == 'in_progress':
        action_buttons = '<form method="POST" class="inline"><input type="hidden" name="action" value="complete"><button type="submit" class="bg-emerald-500 hover:bg-emerald-600 text-white px-4 py-2 rounded-lg font-medium transition">✓ Complete Job</button></form>'

    # Format dates
    date_str = str(job['scheduled_date'])[:10] if job['scheduled_date'] else ''
    time_str = str(job['scheduled_date'])[11:16] if job['scheduled_date'] and len(str(job['scheduled_date'])) > 10 else '09:00'

    return render_template_string(f"""
<!DOCTYPE html>
<html lang="en" class="dark">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>FieldPulse — Job Details</title>
    {TAILWIND_CDN}
    {FIELD_PULSE_CSS}
</head>
<body class="bg-slate-900 text-white">
    <div class="flex min-h-screen">
        <!-- Sidebar -->
        <aside class="w-64 bg-slate-950 border-r border-slate-800 fixed h-full">
            <div class="p-6">
                <div class="flex items-center gap-3 mb-8">
                    <div class="w-10 h-10 bg-gradient-to-br from-emerald-400 to-emerald-600 rounded-lg flex items-center justify-center">
                        <svg class="w-5 h-5 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 10V3L4 14h7v7l9-11h-7z"/>
                        </svg>
                    </div>
                    <div>
                        <h1 class="font-bold text-lg">FieldPulse</h1>
                    </div>
                </div>
                <nav class="space-y-1">
                    <a href="/fieldpulse/dashboard" class="sidebar-link flex items-center gap-3 px-4 py-3 rounded-lg text-sm font-medium text-slate-400 hover:text-white">
                        <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 6a2 2 0 012-2h2a2 2 0 012 2v2a2 2 0 01-2 2H6a2 2 0 01-2-2V6z"/>
                        </svg>
                        Dashboard
                    </a>
                    <a href="/fieldpulse/jobs" class="sidebar-link active flex items-center gap-3 px-4 py-3 rounded-lg text-sm font-medium text-white">
                        <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2"/>
                        </svg>
                        Jobs
                    </a>
                </nav>
            </div>
        </aside>

        <!-- Main Content -->
        <main class="flex-1 ml-64">
            <header class="bg-slate-900 border-b border-slate-800 px-8 py-4 sticky top-0 z-10">
                <div class="flex items-center justify-between">
                    <div class="flex items-center gap-4">
                        <a href="/fieldpulse/jobs" class="text-slate-400 hover:text-white">← Back to Jobs</a>
                        <h2 class="text-xl font-semibold">Job Details</h2>
                    </div>
                    <div class="flex items-center gap-3">
                        <span class="px-3 py-1 rounded-full text-sm font-medium border {status_class}">
                            {job['status'].replace('_', ' ').title()}
                        </span>
                        {action_buttons}
                    </div>
                </div>
            </header>

            <div class="p-8 max-w-4xl">
                {f'<div class="mb-6 p-4 bg-emerald-500/10 border border-emerald-500/20 rounded-lg text-emerald-400">{success}</div>' if success else ''}
                {f'<div class="mb-6 p-4 bg-red-500/10 border border-red-500/20 rounded-lg text-red-400">{error}</div>' if error else ''}

                <form method="POST" class="space-y-6">
                    <input type="hidden" name="action" value="update">

                    <!-- Job Info -->
                    <div class="bg-slate-800 rounded-xl p-6 border border-slate-700">
                        <h3 class="text-lg font-medium text-white mb-4">Job Details</h3>
                        <div class="grid grid-cols-1 md:grid-cols-2 gap-6">
                            <div class="md:col-span-2">
                                <label class="block text-sm font-medium text-slate-300 mb-2">Job Title</label>
                                <input type="text" name="title" value="{escape(job.get('title', ''))}" class="w-full px-4 py-2 bg-slate-900 border border-slate-700 rounded-lg text-white focus:ring-2 focus:ring-emerald-500 focus:border-transparent">
                            </div>
                            <div class="md:col-span-2">
                                <label class="block text-sm font-medium text-slate-300 mb-2">Description</label>
                                <textarea name="description" rows="3" class="w-full px-4 py-2 bg-slate-900 border border-slate-700 rounded-lg text-white focus:ring-2 focus:ring-emerald-500 focus:border-transparent">{escape(job.get('description', ''))}</textarea>
                            </div>
                        </div>
                    </div>

                    <!-- Customer Info -->
                    <div class="bg-slate-800 rounded-xl p-6 border border-slate-700">
                        <h3 class="text-lg font-medium text-white mb-4">Customer Information</h3>
                        <div class="grid grid-cols-1 md:grid-cols-2 gap-6">
                            <div>
                                <label class="block text-sm font-medium text-slate-300 mb-2">Customer Name</label>
                                <input type="text" name="customer_name" value="{escape(job.get('customer_name', ''))}" class="w-full px-4 py-2 bg-slate-900 border border-slate-700 rounded-lg text-white focus:ring-2 focus:ring-emerald-500 focus:border-transparent">
                            </div>
                            <div>
                                <label class="block text-sm font-medium text-slate-300 mb-2">Phone</label>
                                <input type="tel" name="customer_phone" value="{escape(job.get('customer_phone', ''))}" class="w-full px-4 py-2 bg-slate-900 border border-slate-700 rounded-lg text-white focus:ring-2 focus:ring-emerald-500 focus:border-transparent">
                            </div>
                            <div class="md:col-span-2">
                                <label class="block text-sm font-medium text-slate-300 mb-2">Email</label>
                                <input type="email" name="customer_email" value="{escape(job.get('customer_email', ''))}" class="w-full px-4 py-2 bg-slate-900 border border-slate-700 rounded-lg text-white focus:ring-2 focus:ring-emerald-500 focus:border-transparent">
                            </div>
                            <div class="md:col-span-2">
                                <label class="block text-sm font-medium text-slate-300 mb-2">Address</label>
                                <input type="text" name="address" value="{escape(job.get('address', ''))}" class="w-full px-4 py-2 bg-slate-900 border border-slate-700 rounded-lg text-white focus:ring-2 focus:ring-emerald-500 focus:border-transparent">
                            </div>
                            <div>
                                <label class="block text-sm font-medium text-slate-300 mb-2">City</label>
                                <input type="text" name="city" value="{escape(job.get('city', ''))}" class="w-full px-4 py-2 bg-slate-900 border border-slate-700 rounded-lg text-white focus:ring-2 focus:ring-emerald-500 focus:border-transparent">
                            </div>
                        </div>
                    </div>

                    <!-- Scheduling -->
                    <div class="bg-slate-800 rounded-xl p-6 border border-slate-700">
                        <h3 class="text-lg font-medium text-white mb-4">Scheduling</h3>

                        <!-- Date Selection -->
                        <div class="mb-6">
                            <label class="block text-sm font-medium text-slate-300 mb-3">Date</label>
                            <input type="date" name="scheduled_date" value="{date_str}" required
                                class="w-full px-4 py-3 bg-slate-900 border border-slate-700 rounded-lg text-white focus:ring-2 focus:ring-emerald-500 focus:border-transparent text-lg"
                                style="color-scheme: dark;">
                        </div>

                        <!-- Time Selection -->
                        <div class="mb-6">
                            <label class="block text-sm font-medium text-slate-300 mb-3">Time</label>
                            <div class="grid grid-cols-4 gap-2">
                                <label class="cursor-pointer">
                                    <input type="radio" name="scheduled_time" value="08:00" class="peer sr-only" {'checked' if time_str == '08:00' else ''}>
                                    <div class="px-3 py-2 bg-slate-900 border border-slate-700 rounded-lg text-center text-sm text-slate-300 peer-checked:bg-emerald-500 peer-checked:border-emerald-500 peer-checked:text-white hover:border-slate-500 transition">8:00 AM</div>
                                </label>
                                <label class="cursor-pointer">
                                    <input type="radio" name="scheduled_time" value="09:00" class="peer sr-only" {'checked' if time_str == '09:00' or not time_str else ''}>
                                    <div class="px-3 py-2 bg-slate-900 border border-slate-700 rounded-lg text-center text-sm text-slate-300 peer-checked:bg-emerald-500 peer-checked:border-emerald-500 peer-checked:text-white hover:border-slate-500 transition">9:00 AM</div>
                                </label>
                                <label class="cursor-pointer">
                                    <input type="radio" name="scheduled_time" value="10:00" class="peer sr-only" {'checked' if time_str == '10:00' else ''}>
                                    <div class="px-3 py-2 bg-slate-900 border border-slate-700 rounded-lg text-center text-sm text-slate-300 peer-checked:bg-emerald-500 peer-checked:border-emerald-500 peer-checked:text-white hover:border-slate-500 transition">10:00 AM</div>
                                </label>
                                <label class="cursor-pointer">
                                    <input type="radio" name="scheduled_time" value="11:00" class="peer sr-only" {'checked' if time_str == '11:00' else ''}>
                                    <div class="px-3 py-2 bg-slate-900 border border-slate-700 rounded-lg text-center text-sm text-slate-300 peer-checked:bg-emerald-500 peer-checked:border-emerald-500 peer-checked:text-white hover:border-slate-500 transition">11:00 AM</div>
                                </label>
                                <label class="cursor-pointer">
                                    <input type="radio" name="scheduled_time" value="12:00" class="peer sr-only" {'checked' if time_str == '12:00' else ''}>
                                    <div class="px-3 py-2 bg-slate-900 border border-slate-700 rounded-lg text-center text-sm text-slate-300 peer-checked:bg-emerald-500 peer-checked:border-emerald-500 peer-checked:text-white hover:border-slate-500 transition">12:00 PM</div>
                                </label>
                                <label class="cursor-pointer">
                                    <input type="radio" name="scheduled_time" value="13:00" class="peer sr-only" {'checked' if time_str == '13:00' else ''}>
                                    <div class="px-3 py-2 bg-slate-900 border border-slate-700 rounded-lg text-center text-sm text-slate-300 peer-checked:bg-emerald-500 peer-checked:border-emerald-500 peer-checked:text-white hover:border-slate-500 transition">1:00 PM</div>
                                </label>
                                <label class="cursor-pointer">
                                    <input type="radio" name="scheduled_time" value="14:00" class="peer sr-only" {'checked' if time_str == '14:00' else ''}>
                                    <div class="px-3 py-2 bg-slate-900 border border-slate-700 rounded-lg text-center text-sm text-slate-300 peer-checked:bg-emerald-500 peer-checked:border-emerald-500 peer-checked:text-white hover:border-slate-500 transition">2:00 PM</div>
                                </label>
                                <label class="cursor-pointer">
                                    <input type="radio" name="scheduled_time" value="15:00" class="peer sr-only" {'checked' if time_str == '15:00' else ''}>
                                    <div class="px-3 py-2 bg-slate-900 border border-slate-700 rounded-lg text-center text-sm text-slate-300 peer-checked:bg-emerald-500 peer-checked:border-emerald-500 peer-checked:text-white hover:border-slate-500 transition">3:00 PM</div>
                                </label>
                                <label class="cursor-pointer">
                                    <input type="radio" name="scheduled_time" value="16:00" class="peer sr-only" {'checked' if time_str == '16:00' else ''}>
                                    <div class="px-3 py-2 bg-slate-900 border border-slate-700 rounded-lg text-center text-sm text-slate-300 peer-checked:bg-emerald-500 peer-checked:border-emerald-500 peer-checked:text-white hover:border-slate-500 transition">4:00 PM</div>
                                </label>
                                <label class="cursor-pointer">
                                    <input type="radio" name="scheduled_time" value="17:00" class="peer sr-only" {'checked' if time_str == '17:00' else ''}>
                                    <div class="px-3 py-2 bg-slate-900 border border-slate-700 rounded-lg text-center text-sm text-slate-300 peer-checked:bg-emerald-500 peer-checked:border-emerald-500 peer-checked:text-white hover:border-slate-500 transition">5:00 PM</div>
                                </label>
                            </div>
                        </div>

                        <!-- Duration & Crew -->
                        <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
                            <div>
                                <label class="block text-sm font-medium text-slate-300 mb-2">Est. Duration</label>
                                <select name="estimated_duration" class="w-full px-4 py-2 bg-slate-900 border border-slate-700 rounded-lg text-white focus:ring-2 focus:ring-emerald-500 focus:border-transparent">
                                    <option value="30" {'selected' if job.get('estimated_duration_min') == 30 else ''}>30 minutes</option>
                                    <option value="60" {'selected' if job.get('estimated_duration_min', 60) == 60 else ''}>1 hour</option>
                                    <option value="90" {'selected' if job.get('estimated_duration_min') == 90 else ''}>1.5 hours</option>
                                    <option value="120" {'selected' if job.get('estimated_duration_min') == 120 else ''}>2 hours</option>
                                    <option value="180" {'selected' if job.get('estimated_duration_min') == 180 else ''}>3 hours</option>
                                    <option value="240" {'selected' if job.get('estimated_duration_min') == 240 else ''}>4 hours</option>
                                    <option value="480" {'selected' if job.get('estimated_duration_min') == 480 else ''}>Full day (8 hours)</option>
                                </select>
                            </div>
                            <div>
                                <label class="block text-sm font-medium text-slate-300 mb-2">Crew</label>
                                <select name="crew_id" class="w-full px-4 py-2 bg-slate-900 border border-slate-700 rounded-lg text-white focus:ring-2 focus:ring-emerald-500 focus:border-transparent">{crew_options}</select>
                            </div>
                        </div>
                    </div>

                    <!-- Actions (end of main form) -->
                    <div class="flex gap-4">
                        <button type="submit" class="bg-emerald-500 hover:bg-emerald-600 text-white px-6 py-3 rounded-lg font-medium transition">Save Changes</button>
                        <a href="/fieldpulse/jobs" class="bg-slate-700 hover:bg-slate-600 text-white px-6 py-3 rounded-lg font-medium transition">Cancel</a>
                    </div>
                </form>

                <!-- Job Notes & Photos (separate forms - not nested) -->
                <div class="bg-slate-800 rounded-xl p-6 border border-slate-700 mt-6">
                    <h3 class="text-lg font-medium text-white mb-4">Job Notes & Photos</h3>

                    <!-- Existing Notes -->
                    <div class="space-y-3 mb-6 max-h-64 overflow-y-auto">
                        {notes_html}
                    </div>

                    <!-- Add Note Form (separate, not nested) -->
                    <form method="POST" class="space-y-3 mb-8 pb-8 border-b border-slate-700">
                        <input type="hidden" name="action" value="add_note">
                        <div>
                            <label class="block text-sm font-medium text-slate-300 mb-2">Add a Note</label>
                            <textarea name="note" rows="3" placeholder="Enter note details..." required
                                class="w-full px-4 py-2 bg-slate-900 border border-slate-700 rounded-lg text-white focus:ring-2 focus:ring-emerald-500 focus:border-transparent"></textarea>
                        </div>
                        <button type="submit" class="bg-blue-500 hover:bg-blue-600 text-white px-4 py-2 rounded-lg font-medium transition">Add Note</button>
                    </form>

                    <!-- Photo Gallery -->
                    <div class="grid grid-cols-2 md:grid-cols-4 gap-4 mb-6 max-h-96 overflow-y-auto">
                        {photos_html}
                    </div>

                    <!-- Upload Photo Form (separate, not nested) -->
                    <form method="POST" enctype="multipart/form-data" class="space-y-3 border-t border-slate-700 pt-6">
                        <input type="hidden" name="action" value="upload_photo">
                        <h4 class="text-sm font-medium text-slate-300 mb-3">Upload Photo</h4>
                        <div class="grid grid-cols-1 md:grid-cols-3 gap-4">
                            <div>
                                <label class="block text-xs font-medium text-slate-400 mb-1">Photo Type</label>
                                <select name="photo_type" class="w-full px-3 py-2 bg-slate-900 border border-slate-700 rounded-lg text-white text-sm focus:ring-2 focus:ring-emerald-500 focus:border-transparent">
                                    <option value="progress">Progress</option>
                                    <option value="before">Before</option>
                                    <option value="after">After</option>
                                    <option value="issue">Issue</option>
                                </select>
                            </div>
                            <div class="md:col-span-2">
                                <label class="block text-xs font-medium text-slate-400 mb-1">Caption (optional)</label>
                                <input type="text" name="photo_caption" placeholder="Describe the photo..."
                                    class="w-full px-3 py-2 bg-slate-900 border border-slate-700 rounded-lg text-white text-sm focus:ring-2 focus:ring-emerald-500 focus:border-transparent">
                            </div>
                        </div>
                        <div class="flex items-end gap-4">
                            <div class="flex-1">
                                <input type="file" name="photo" accept="image/*" required
                                    class="w-full text-sm text-slate-300 file:mr-4 file:py-2 file:px-4 file:rounded file:border-0 file:text-sm file:bg-emerald-500 file:text-white hover:file:bg-emerald-600">
                                <p class="text-xs text-slate-500 mt-1">Max 5MB. JPG, PNG, GIF, WebP</p>
                            </div>
                            <button type="submit" class="bg-purple-500 hover:bg-purple-600 text-white px-4 py-2 rounded-lg font-medium transition">Upload</button>
                        </div>
                    </form>
                </div>
            </div>
        </main>
    </div>
</body>
</html>
""")
