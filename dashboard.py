#!/usr/bin/env python3
"""
dashboard.py — FieldPulse Field Service Management
Cleaned up version - focuses on SaaS functionality

Run with: python dashboard.py
Visit:    http://localhost:5000/login

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
import uuid
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

# Import Clerk authentication (optional - falls back to session auth if not configured)
try:
    from modules.clerk_auth import (
        clerk_login_required,
        is_clerk_configured,
        get_current_user as get_clerk_user,
        require_business as require_clerk_business
    )
    CLERK_AVAILABLE = True
except ImportError:
    CLERK_AVAILABLE = False
    clerk_login_required = None
    is_clerk_configured = lambda: False
    get_clerk_user = lambda: None
    require_clerk_business = None

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
            # Insert user with password hash for 'MasKatana@1'
            query_db("""
                INSERT INTO users (id, business_id, email, password_hash, name, role, active)
                VALUES ('634e6557-7baf-4894-8324-00058482c290', 'a1631c27-4b0d-4ecb-a684-2554c0acaa0e',
                        'owner@demolandscaping.com', '$2b$12$u5HC892kN3KIE7NXfD09PO2SFGLj4O.KBZ7EuCkjrOw.1dGpIXcDW', 'Demo Owner', 'owner', true)
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
        else:
            # Ensure demo user has a password (for existing databases)
            query_db("""
                UPDATE users SET password_hash = '$2b$12$u5HC892kN3KIE7NXfD09PO2SFGLj4O.KBZ7EuCkjrOw.1dGpIXcDW'
                WHERE email = 'owner@demolandscaping.com' AND (password_hash IS NULL OR password_hash = '')
            """)
    except Exception as e:
        logger.error(f"Failed to seed demo data: {e}")

# seed_fieldpulse_demo() - MOVED to after query_db is defined

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
# CACHE HELPERS
# ═════════════════════════════════════════════════════════════════

_cache_store = {}

def invalidate_cache(key_pattern):
    """Invalidate cached data by key pattern."""
    global _cache_store
    keys_to_delete = [k for k in _cache_store.keys() if key_pattern in k]
    for k in keys_to_delete:
        del _cache_store[k]


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


# Now that query_db is defined, seed demo data
seed_fieldpulse_demo()


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
    """FieldPulse login decorator - checks Clerk JWT first, falls back to session auth."""
    @wraps(f)
    def decorated(*args, **kwargs):
        from flask import g

        # Try Clerk auth first if available and configured
        if CLERK_AVAILABLE and is_clerk_configured():
            from modules.clerk_auth import get_auth_token_from_request, verify_clerk_jwt

            token = get_auth_token_from_request()
            if token:
                claims = verify_clerk_jwt(token)
                if claims:
                    g.clerk_user = claims
                    g.user_id = claims.get("sub")

                    # Look up user and business from database
                    clerk_user_id = claims.get("sub")
                    email = claims.get("email", "")

                    if clerk_user_id:
                        user = query_db(
                            "SELECT * FROM users WHERE clerk_user_id = %s OR email = %s",
                            (clerk_user_id, email), one=True
                        )

                        if user:
                            g.current_user = user
                            g.current_business_id = user.get('business_id')

                            # Get business details
                            if user.get('business_id'):
                                business = query_db(
                                    "SELECT * FROM businesses WHERE id = %s AND active = true",
                                    (user['business_id'],), one=True
                                )
                                g.current_business = business

                            return f(*args, **kwargs)

            # No valid Clerk token - check if we have legacy session auth
            # (User might have logged in via onboarding and have a session)
            if session.get("fp_logged_in"):
                # User has valid session, continue to fallback section below
                pass
            else:
                # No Clerk token AND no session - redirect to Clerk login
                return redirect(url_for("clerk_login_page"))

        # Fallback to legacy session auth (Clerk not configured or no Clerk token but has session)
        if not session.get("fp_logged_in"):
            return redirect(url_for("fieldpulse_login"))

        # Legacy auth - get business from session
        business_id = session.get("fp_business_id")
        if business_id:
            business = query_db(
                "SELECT * FROM businesses WHERE id = %s AND active = true",
                (business_id,), one=True
            )
            g.current_business = business
            g.current_business_id = business_id

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
        logger.warning("get_business_from_session: No fp_business_id in session")
        return None

    business = query_db(
        "SELECT * FROM businesses WHERE id = %s",
        (business_id,),
        one=True
    )

    if not business:
        logger.error(f"get_business_from_session: Business {business_id} not found in database")
    else:
        logger.info(f"get_business_from_session: Found business {business_id}")

    return business


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

# Landing Page Template
LANDING_PAGE_TEMPLATE = """
<!DOCTYPE html>
<html lang="en" class="scroll-smooth">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>FieldPulse - Crew Management for Service Businesses</title>
    <meta name="description" content="FieldPulse helps landscaping, HVAC, plumbing, and service businesses manage crews, schedule jobs, and delight customers.">
    <script src="https://cdn.tailwindcss.com"></script>
    <script>
        tailwind.config = {{
            theme: {{
                extend: {{
                    colors: {{
                        primary: {{ 50: '#ecfdf5', 100: '#d1fae5', 200: '#a7f3d0', 300: '#6ee7b7', 400: '#34d399', 500: '#10b981', 600: '#059669', 700: '#047857', 800: '#065f46', 900: '#064e3b' }},
                        dark: {{ 900: '#0f172a', 800: '#1e293b', 700: '#334155', 600: '#475569' }}
                    }}
                }}
            }}
        }}
    </script>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800&display=swap" rel="stylesheet">
    <style>
        body {{ font-family: 'Inter', sans-serif; }}
        .gradient-text {{ background: linear-gradient(135deg, #10b981 0%, #059669 100%); -webkit-background-clip: text; -webkit-text-fill-color: transparent; }}
        .hero-glow {{ position: absolute; width: 600px; height: 600px; background: radial-gradient(circle, rgba(16,185,129,0.15) 0%, transparent 70%); border-radius: 50%; top: 50%; left: 50%; transform: translate(-50%, -50%); pointer-events: none; }}
        .card-hover {{ transition: all 0.3s ease; }}
        .card-hover:hover {{ transform: translateY(-4px); box-shadow: 0 20px 40px -15px rgba(16,185,129,0.2); }}
    </style>
</head>
<body class="bg-dark-900 text-white antialiased">
    <!-- Navigation -->
    <nav class="fixed top-0 left-0 right-0 z-50 bg-dark-900/80 backdrop-blur-md border-b border-white/10">
        <div class="max-w-7xl mx-auto px-4 sm:px-6 py-3 sm:py-4 flex items-center justify-between">
            <div class="flex items-center gap-2 sm:gap-3">
                <div class="w-8 h-8 sm:w-10 sm:h-10 bg-gradient-to-br from-primary-400 to-primary-600 rounded-xl flex items-center justify-center flex-shrink-0">
                    <svg class="w-5 h-5 sm:w-6 sm:h-6 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 10V3L4 14h7v7l9-11h-7z"/>
                    </svg>
                </div>
                <span class="text-lg sm:text-xl font-bold">FieldPulse</span>
            </div>
            <div class="hidden md:flex items-center gap-8">
                <a href="#features" class="text-gray-300 hover:text-white transition">Features</a>
                <a href="#pricing" class="text-gray-300 hover:text-white transition">Pricing</a>
                <a href="#faq" class="text-gray-300 hover:text-white transition">FAQ</a>
            </div>

            <div class="flex items-center gap-2 sm:gap-4">
                <a href="/clerk-login" class="text-sm sm:text-base text-gray-300 hover:text-white transition whitespace-nowrap">Sign In</a>
                <button onclick="openWaitlistModal()" class="px-3 sm:px-5 py-2 sm:py-2.5 bg-primary-500 hover:bg-primary-600 text-white text-sm sm:text-base font-medium rounded-lg transition whitespace-nowrap">Join Waitlist</button>
            </div>
        </div>
    </nav>

    <!-- Waitlist Success Confirmation -->
    <div id="waitlist-confirmation" style="display: {show_confirmation};" class="fixed top-20 left-0 right-0 z-40 px-4">
        <div class="max-w-4xl mx-auto">
            <div class="bg-primary-500/10 border border-primary-500/30 rounded-2xl p-6 text-center backdrop-blur-sm">
                <div class="flex items-center justify-center gap-3 mb-2">
                    <div class="w-8 h-8 bg-primary-500 rounded-full flex items-center justify-center">
                        <svg class="w-5 h-5 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 13l4 4L19 7"/>
                        </svg>
                    </div>
                    <h3 class="text-xl font-semibold text-white">You're on the list!</h3>
                </div>
                <p class="text-gray-300">Thanks for joining the FieldPulse waitlist. We'll email you when beta access is ready.</p>
                <button onclick="document.getElementById('waitlist-confirmation').style.display='none'" class="mt-4 text-primary-400 hover:text-primary-300 text-sm font-medium">
                    Dismiss
                </button>
            </div>
        </div>
    </div>

    <!-- Hero Section -->
    <section class="relative pt-32 pb-20 lg:pt-48 lg:pb-32 overflow-hidden">
        <div class="hero-glow"></div>
        <div class="max-w-7xl mx-auto px-6 relative">
            <div class="text-center max-w-4xl mx-auto">
                <div class="inline-flex items-center gap-2 px-4 py-2 bg-primary-500/10 border border-primary-500/20 rounded-full mb-8">
                    <span class="w-2 h-2 bg-primary-400 rounded-full animate-pulse"></span>
                    <span class="text-primary-400 text-sm font-medium">Beta Launching September 2026</span>
                </div>

                <h1 class="text-4xl sm:text-5xl lg:text-7xl font-bold leading-tight mb-6">
                    <span class="block">Crew Management</span>
                    <span class="block">for <span class="gradient-text">Service Businesses</span></span>
                </h1>

                <p class="text-xl text-gray-400 mb-10 max-w-2xl mx-auto">
                    FieldPulse helps landscaping, HVAC, plumbing, and field service businesses schedule crews, manage jobs, and delight customers—all from one platform.
                </p>

                <div class="flex flex-col sm:flex-row items-center justify-center gap-4">
                    <button onclick="openWaitlistModal()" class="w-full sm:w-auto px-8 py-4 bg-primary-500 hover:bg-primary-600 text-white font-semibold rounded-xl transition shadow-lg shadow-primary-500/25">
                        Join the Waitlist
                    </button>
                    <a href="#features" class="w-full sm:w-auto px-8 py-4 bg-dark-800 hover:bg-dark-700 text-white font-semibold rounded-xl transition border border-white/10">
                        Learn More
                    </a>
                </div>

                <p class="mt-6 text-sm text-gray-500">
                    Free tier available • No credit card required
                </p>
            </div>
        </div>
    </section>

    <!-- Features Section -->
    <section id="features" class="py-24 bg-dark-800">
        <div class="max-w-7xl mx-auto px-6">
            <div class="text-center mb-16">
                <h2 class="text-3xl lg:text-4xl font-bold mb-4">Everything You Need to Run Your Crews</h2>
                <p class="text-gray-400 text-lg">Powerful tools designed specifically for field service businesses</p>
            </div>

            <div class="grid md:grid-cols-2 lg:grid-cols-3 gap-8">
                <!-- Feature 1 -->
                <div class="p-6 bg-dark-900 rounded-2xl border border-white/5 card-hover">
                    <div class="w-12 h-12 bg-primary-500/10 rounded-xl flex items-center justify-center mb-4">
                        <svg class="w-6 h-6 text-primary-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 4.354a4 4 0 110 5.292M15 21H3v-1a6 6 0 0112 0v1zm0 0h6v-1a6 6 0 00-9-5.197M13 7a4 4 0 11-8 0 4 4 0 018 0z"/>
                        </svg>
                    </div>
                    <h3 class="text-xl font-semibold mb-2">Crew Management</h3>
                    <p class="text-gray-400">Organize crews by skills, track availability, and assign jobs with one click.</p>
                </div>

                <!-- Feature 2 -->
                <div class="p-6 bg-dark-900 rounded-2xl border border-white/5 card-hover">
                    <div class="w-12 h-12 bg-primary-500/10 rounded-xl flex items-center justify-center mb-4">
                        <svg class="w-6 h-6 text-primary-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8 7V3m8 4V3m-9 8h10M5 21h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v12a2 2 0 002 2z"/>
                        </svg>
                    </div>
                    <h3 class="text-xl font-semibold mb-2">Smart Scheduling</h3>
                    <p class="text-gray-400">Visual calendar view, drag-and-drop scheduling, and automatic conflict detection.</p>
                </div>

                <!-- Feature 3 -->
                <div class="p-6 bg-dark-900 rounded-2xl border border-white/5 card-hover">
                    <div class="w-12 h-12 bg-primary-500/10 rounded-xl flex items-center justify-center mb-4">
                        <svg class="w-6 h-6 text-primary-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M17.657 16.657L13.414 20.9a1.998 1.998 0 01-2.827 0l-4.244-4.243a8 8 0 1111.314 0z"/>
                            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15 11a3 3 0 11-6 0 3 3 0 016 0z"/>
                        </svg>
                    </div>
                    <h3 class="text-xl font-semibold mb-2">Job Tracking</h3>
                    <p class="text-gray-400">Track jobs from scheduled to completed. Add notes, photos, and time tracking.</p>
                </div>

                <!-- Feature 4 -->
                <div class="p-6 bg-dark-900 rounded-2xl border border-white/5 card-hover">
                    <div class="w-12 h-12 bg-primary-500/10 rounded-xl flex items-center justify-center mb-4">
                        <svg class="w-6 h-6 text-primary-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M3 8l7.89 5.26a2 2 0 002.22 0L21 8M5 19h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v10a2 2 0 002 2z"/>
                        </svg>
                    </div>
                    <h3 class="text-xl font-semibold mb-2">Email Notifications</h3>
                    <p class="text-gray-400">Automatic booking confirmations, reminders, and status updates for customers and crews.</p>
                </div>

                <!-- Feature 5 -->
                <div class="p-6 bg-dark-900 rounded-2xl border border-white/5 card-hover">
                    <div class="w-12 h-12 bg-primary-500/10 rounded-xl flex items-center justify-center mb-4">
                        <svg class="w-6 h-6 text-primary-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 18h.01M8 21h8a2 2 0 002-2V5a2 2 0 00-2-2H8a2 2 0 00-2 2v14a2 2 0 002 2z"/>
                        </svg>
                    </div>
                    <h3 class="text-xl font-semibold mb-2">Mobile Optimized</h3>
                    <p class="text-gray-400">Works perfectly on any device. Crews can view jobs and update status from the field.</p>
                </div>

                <!-- Feature 6 -->
                <div class="p-6 bg-dark-900 rounded-2xl border border-white/5 card-hover">
                    <div class="w-12 h-12 bg-primary-500/10 rounded-xl flex items-center justify-center mb-4">
                        <svg class="w-6 h-6 text-primary-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12l2 2 4-4m5.618-4.016A11.955 11.955 0 0112 2.944a11.955 11.955 0 01-8.618 3.04A12.02 12.02 0 003 9c0 5.591 3.824 10.29 9 11.622 5.176-1.332 9-6.03 9-11.622 0-1.042-.133-2.052-.382-3.016z"/>
                        </svg>
                    </div>
                    <h3 class="text-xl font-semibold mb-2">Manager-Only Access</h3>
                    <p class="text-gray-400">Pay for crew managers, not every technician. Crews access jobs via secure links.</p>
                </div>
            </div>
        </div>
    </section>

    <!-- Pricing Section -->
    <section id="pricing" class="py-24">
        <div class="max-w-7xl mx-auto px-6">
            <div class="text-center mb-16">
                <h2 class="text-3xl lg:text-4xl font-bold mb-4">Simple, Transparent Pricing</h2>
                <p class="text-gray-400 text-lg">Start free, scale as you grow. No hidden fees.</p>
            </div>

            <div class="grid md:grid-cols-3 gap-8 max-w-6xl mx-auto">
                <!-- Free Plan -->
                <div class="p-8 bg-dark-800 rounded-2xl border border-white/5">
                    <h3 class="text-xl font-semibold mb-2">Free</h3>
                    <div class="flex items-baseline gap-1 mb-4">
                        <span class="text-4xl font-bold">$0</span>
                        <span class="text-gray-400">/month</span>
                    </div>

                    <p class="text-gray-400 mb-6">Perfect for getting started</p>

                    <ul class="space-y-3 mb-8">
                        <li class="flex items-center gap-3">
                            <svg class="w-5 h-5 text-primary-400" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 13l4 4L19 7"/></svg>
                            <span>1 Crew</span>
                        </li>
                        <li class="flex items-center gap-3">
                            <svg class="w-5 h-5 text-primary-400" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 13l4 4L19 7"/></svg>
                            <span>10 Jobs/Tasks</span>
                        </li>
                        <li class="flex items-center gap-3">
                            <svg class="w-5 h-5 text-primary-400" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 13l4 4L19 7"/></svg>
                            <span>Client Booking Portal</span>
                        </li>
                        <li class="flex items-center gap-3">
                            <svg class="w-5 h-5 text-primary-400" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 13l4 4L19 7"/></svg>
                            <span>Email Notifications</span>
                        </li>
                    </ul>

                    <button onclick="openWaitlistModal()" class="block w-full py-3 text-center border border-white/20 rounded-lg font-medium hover:bg-white/5 transition">
                        Get Started Free
                    </button>
                </div>

                <!-- Starter Plan -->
                <div class="p-8 bg-primary-500/10 rounded-2xl border border-primary-500/30 relative">
                    <div class="absolute -top-4 left-1/2 -translate-x-1/2">
                        <span class="px-4 py-1 bg-primary-500 text-white text-sm font-medium rounded-full">Popular</span>
                    </div>

                    <h3 class="text-xl font-semibold mb-2">Starter</h3>
                    <div class="flex items-baseline gap-1 mb-4">
                        <span class="text-4xl font-bold">$49</span>
                        <span class="text-gray-400">/month</span>
                    </div>

                    <p class="text-gray-400 mb-6">For growing businesses</p>

                    <ul class="space-y-3 mb-8">
                        <li class="flex items-center gap-3">
                            <svg class="w-5 h-5 text-primary-400" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 13l4 4L19 7"/></svg>
                            <span>5 Crews</span>
                        </li>
                        <li class="flex items-center gap-3">
                            <svg class="w-5 h-5 text-primary-400" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 13l4 4L19 7"/></svg>
                            <span>Unlimited Jobs</span>
                        </li>
                        <li class="flex items-center gap-3">
                            <svg class="w-5 h-5 text-primary-400" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 13l4 4L19 7"/></svg>
                            <span>Calendar View</span>
                        </li>
                        <li class="flex items-center gap-3">
                            <svg class="w-5 h-5 text-primary-400" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 13l4 4L19 7"/></svg>
                            <span>Priority Support</span>
                        </li>
                    </ul>

                    <button onclick="openWaitlistModal()" class="block w-full py-3 text-center bg-primary-500 hover:bg-primary-600 text-white rounded-lg font-medium transition">
                        Join Waitlist
                    </button>
                </div>

                <!-- Pro Plan -->
                <div class="p-8 bg-dark-800 rounded-2xl border border-white/5">
                    <h3 class="text-xl font-semibold mb-2">Pro</h3>
                    <div class="flex items-baseline gap-1 mb-4">
                        <span class="text-4xl font-bold">$99</span>
                        <span class="text-gray-400">/month</span>
                    </div>

                    <p class="text-gray-400 mb-6">For established operations</p>

                    <ul class="space-y-3 mb-8">
                        <li class="flex items-center gap-3">
                            <svg class="w-5 h-5 text-primary-400" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 13l4 4L19 7"/></svg>
                            <span>Unlimited Crews</span>
                        </li>
                        <li class="flex items-center gap-3">
                            <svg class="w-5 h-5 text-primary-400" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 13l4 4L19 7"/></svg>
                            <span>Unlimited Jobs</span>
                        </li>
                        <li class="flex items-center gap-3">
                            <svg class="w-5 h-5 text-primary-400" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 13l4 4L19 7"/></svg>
                            <span>Route Optimization</span>
                        </li>
                        <li class="flex items-center gap-3">
                            <svg class="w-5 h-5 text-primary-400" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 13l4 4L19 7"/></svg>
                            <span>White-Label Options</span>
                        </li>
                    </ul>

                    <button onclick="openWaitlistModal()" class="block w-full py-3 text-center border border-white/20 rounded-lg font-medium hover:bg-white/5 transition">
                        Join Waitlist
                    </a>
                </div>
            </div>


            <p class="text-center text-gray-500 mt-8">
                Beta pricing: 50% off for the first 6 months
            </p>
        </div>
    </section>

    <!-- FAQ Section -->
    <section id="faq" class="py-24 bg-dark-800">
        <div class="max-w-3xl mx-auto px-6">
            <div class="text-center mb-16">
                <h2 class="text-3xl lg:text-4xl font-bold mb-4">Frequently Asked Questions</h2>
                <p class="text-gray-400">Everything you need to know about FieldPulse</p>
            </div>

            <div class="space-y-6">
                <!-- FAQ 1 -->
                <div class="p-6 bg-dark-900 rounded-xl border border-white/5">
                    <h3 class="font-semibold mb-2">When will FieldPulse launch?</h3>
                    <p class="text-gray-400">We're launching our beta in September 2026. Join the waitlist to get early access and lock in 50% off pricing for your first 6 months.</p>
                </div>

                <!-- FAQ 2 -->
                <div class="p-6 bg-dark-900 rounded-xl border border-white/5">
                    <h3 class="font-semibold mb-2">What industries is FieldPulse for?</h3>
                    <p class="text-gray-400">FieldPulse is designed for any service business with crews in the field: landscaping, lawn care, HVAC, plumbing, electrical, cleaning services, general contracting, and more.</p>
                </div>

                <!-- FAQ 3 -->
                <div class="p-6 bg-dark-900 rounded-xl border border-white/5">
                    <h3 class="font-semibold mb-2">Do my crew members need to log in?</h3>
                    <p class="text-gray-400">No! FieldPulse uses a unique manager-only access model. Crew managers log in to manage schedules, while crews access their assigned jobs via secure links or QR codes—no passwords required.</p>
                </div>

                <!-- FAQ 4 -->
                <div class="p-6 bg-dark-900 rounded-xl border border-white/5">
                    <h3 class="font-semibold mb-2">Can customers book appointments online?</h3>
                    <p class="text-gray-400">Yes! Every FieldPulse account includes a client booking portal. Customers can see your availability and book directly. You can also integrate it into your existing website.</p>
                </div>

                <!-- FAQ 5 -->
                <div class="p-6 bg-dark-900 rounded-xl border border-white/5">
                    <h3 class="font-semibold mb-2">Is there a free trial?</h3>
                    <p class="text-gray-400">Yes! Our Free tier includes 1 crew and up to 10 jobs—perfect for getting started. No credit card required. When you're ready to grow, upgrade to Starter or Pro.</p>
                </div>

                <!-- FAQ 6 -->
                <div class="p-6 bg-dark-900 rounded-xl border border-white/5">
                    <h3 class="font-semibold mb-2">Is FieldPulse available in my area?</h3>
                    <p class="text-gray-400">We're starting with East Texas and will expand throughout Texas and beyond. Join the waitlist and let us know where you're located—we'll notify you when we launch in your area.</p>
                </div>
            </div>
        </div>
    </section>

    <!-- CTA Section -->
    <section class="py-24">
        <div class="max-w-4xl mx-auto px-6 text-center">
            <h2 class="text-3xl lg:text-5xl font-bold mb-6">Ready to streamline your field service operations?</h2>

            <p class="text-xl text-gray-400 mb-10">Join the waitlist and be the first to try FieldPulse. Get 50% off your first 6 months as a beta user.</p>

            <button onclick="openWaitlistModal()" class="inline-flex items-center gap-2 px-8 py-4 bg-primary-500 hover:bg-primary-600 text-white font-semibold rounded-xl transition shadow-lg shadow-primary-500/25">
                Join the Waitlist
                <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M17 8l4 4m0 0l-4 4m4-4H3"/>
                </svg>
            </button>
        </div>
    </section>

    <!-- Footer -->
    <footer class="py-12 border-t border-white/5">
        <div class="max-w-7xl mx-auto px-6">
            <div class="flex flex-col md:flex-row items-center justify-between gap-6">
                <div class="flex items-center gap-3">
                    <div class="w-8 h-8 bg-gradient-to-br from-primary-400 to-primary-600 rounded-lg flex items-center justify-center">
                        <svg class="w-5 h-5 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 10V3L4 14h7v7l9-11h-7z"/>
                        </svg>
                    </div>

                    <span class="font-semibold">FieldPulse</span>
                </div>

                <p class="text-gray-500">
                    <a href="https://pineydigital.com" target="_blank" class="text-primary-400 hover:text-primary-300">A Product of Piney Digital</a>
                </p>

                <p class="text-gray-600 text-sm">© 2026 FieldPulse. All rights reserved.</p>
            </div>
        </div>
    </footer>

    <!-- Waitlist Modal -->
    <div id="waitlist-modal" class="fixed inset-0 z-[60] hidden overflow-y-auto">
        <!-- Backdrop -->
        <div class="fixed inset-0 bg-black/60 backdrop-blur-sm" onclick="closeWaitlistModal()"></div>

        <!-- Modal Content -->
        <div class="relative min-h-screen flex items-start sm:items-center justify-center p-4 py-8">
            <div class="bg-dark-800 rounded-2xl border border-white/10 w-full max-w-md p-6 sm:p-8 shadow-2xl transform transition-all my-auto">
                <!-- Header -->
                <div class="text-center mb-6">
                    <div class="w-12 h-12 bg-gradient-to-br from-primary-400 to-primary-600 rounded-xl mx-auto mb-4 flex items-center justify-center">
                        <svg class="w-6 h-6 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 10V3L4 14h7v7l9-11h-7z"/>
                        </svg>
                    </div>
                    <h2 class="text-2xl font-bold text-white">Join the Waitlist</h2>
                    <p class="text-gray-400 mt-2">Get early access + 50% off for 6 months</p>
                </div>

                <!-- Form -->
                <form id="waitlist-form" onsubmit="submitWaitlist(event)">
                    <div class="space-y-4">
                        <!-- Email (Required) -->
                        <div>
                            <label class="block text-sm font-medium text-gray-300 mb-1">Email *</label>
                            <input type="email" name="email" required
                                class="w-full px-4 py-3 bg-dark-900 border border-white/10 rounded-lg text-white placeholder-gray-500 focus:outline-none focus:border-primary-500 focus:ring-1 focus:ring-primary-500 transition"
                                placeholder="you@company.com">
                        </div>

                        <!-- Name -->
                        <div>
                            <label class="block text-sm font-medium text-gray-300 mb-1">Name</label>
                            <input type="text" name="name"
                                class="w-full px-4 py-3 bg-dark-900 border border-white/10 rounded-lg text-white placeholder-gray-500 focus:outline-none focus:border-primary-500 focus:ring-1 focus:ring-primary-500 transition"
                                placeholder="John Smith">
                        </div>

                        <!-- Company Name -->
                        <div>
                            <label class="block text-sm font-medium text-gray-300 mb-1">Company Name</label>
                            <input type="text" name="company_name"
                                class="w-full px-4 py-3 bg-dark-900 border border-white/10 rounded-lg text-white placeholder-gray-500 focus:outline-none focus:border-primary-500 focus:ring-1 focus:ring-primary-500 transition"
                                placeholder="Acme Landscaping">
                        </div>

                        <!-- Industry -->
                        <div>
                            <label class="block text-sm font-medium text-gray-300 mb-1">Industry</label>
                            <select name="industry"
                                class="w-full px-4 py-3 bg-dark-900 border border-white/10 rounded-lg text-white focus:outline-none focus:border-primary-500 focus:ring-1 focus:ring-primary-500 transition"
                            >
                                <option value="">Select your industry</option>
                                <option value="landscaping">Landscaping / Lawn Care</option>
                                <option value="hvac">HVAC</option>
                                <option value="plumbing">Plumbing</option>
                                <option value="electrical">Electrical</option>
                                <option value="cleaning">Cleaning Services</option>
                                <option value="pest_control">Pest Control</option>
                                <option value="general_contracting">General Contracting</option>
                                <option value="other">Other</option>
                            </select>
                        </div>

                        <!-- Company Size -->
                        <div>
                            <label class="block text-sm font-medium text-gray-300 mb-1">Company Size</label>
                            <select name="company_size"
                                class="w-full px-4 py-3 bg-dark-900 border border-white/10 rounded-lg text-white focus:outline-none focus:border-primary-500 focus:ring-1 focus:ring-primary-500 transition"
                            >
                                <option value="">How many crew members?</option>
                                <option value="1-5">1-5</option>
                                <option value="6-10">6-10</option>
                                <option value="11-25">11-25</option>
                                <option value="25+">25+</option>
                            </select>
                        </div>
                    </div>

                    <!-- Error Message -->
                    <div id="waitlist-error" class="hidden mt-4 p-3 bg-red-500/10 border border-red-500/20 rounded-lg text-red-400 text-sm"></div>

                    <!-- Submit Button -->
                    <button type="submit" id="waitlist-submit"
                        class="w-full mt-6 py-3 bg-primary-500 hover:bg-primary-600 text-white font-semibold rounded-xl transition flex items-center justify-center gap-2"
                    >
                        <span id="waitlist-btn-text">Join Waitlist</span>
                        <svg id="waitlist-spinner" class="hidden w-5 h-5 animate-spin" fill="none" viewBox="0 0 24 24">
                            <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>
                            <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
                        </svg>
                    </button>
                </form>

                <!-- Close Button -->
                <button onclick="closeWaitlistModal()"
                    class="absolute top-4 right-4 text-gray-400 hover:text-white transition p-1"
                >
                    <svg class="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"/>
                    </svg>
                </button>
            </div>
        </div>
    </div>

    <!-- Waitlist JavaScript -->
    <script>
        function openWaitlistModal() {{
            document.getElementById('waitlist-modal').classList.remove('hidden');
            document.body.style.overflow = 'hidden';
            // Focus email field
            setTimeout(() => document.querySelector('input[name="email"]')?.focus(), 100);
        }}

        function closeWaitlistModal() {{
            document.getElementById('waitlist-modal').classList.add('hidden');
            document.body.style.overflow = '';
            // Reset form
            document.getElementById('waitlist-form').reset();
            document.getElementById('waitlist-error').classList.add('hidden');
        }}

        async function submitWaitlist(event) {{
            event.preventDefault();

            const form = document.getElementById('waitlist-form');
            const btnText = document.getElementById('waitlist-btn-text');
            const spinner = document.getElementById('waitlist-spinner');
            const errorDiv = document.getElementById('waitlist-error');

            // Show loading state
            btnText.textContent = 'Joining...';
            spinner.classList.remove('hidden');
            errorDiv.classList.add('hidden');

            // Gather form data
            const formData = {{
                email: form.email.value,
                name: form.name.value,
                company_name: form.company_name.value,
                industry: form.industry.value,
                company_size: form.company_size.value,
                source: 'website'
            }};

            try {{
                const response = await fetch('/api/waitlist', {{
                    method: 'POST',
                    headers: {{ 'Content-Type': 'application/json' }},
                    body: JSON.stringify(formData)
                }});

                const result = await response.json();

                if (result.success) {{
                    // Close modal and show confirmation
                    closeWaitlistModal();
                    document.getElementById('waitlist-confirmation').style.display = 'block';

                    // Scroll to top to see confirmation
                    window.scrollTo({{ top: 0, behavior: 'smooth' }});

                    // Auto-hide confirmation after 5 seconds
                    setTimeout(() => {{
                        document.getElementById('waitlist-confirmation').style.display = 'none';
                    }}, 5000);
                }} else {{
                    throw new Error(result.error || 'Something went wrong');
                }}
            }} catch (err) {{
                errorDiv.textContent = err.message || 'Failed to join waitlist. Please try again.';
                errorDiv.classList.remove('hidden');
            }} finally {{
                btnText.textContent = 'Join Waitlist';
                spinner.classList.remove('hidden');
            }}
        }}

        // Close modal on Escape key
        document.addEventListener('keydown', (e) => {{
            if (e.key === 'Escape') closeWaitlistModal();
        }});
    </script>
</body>
</html>
"""

@app.route("/")
def fieldpulse_redirect():
    """Redirect to FieldPulse dashboard if logged in, otherwise show landing page."""
    if session.get("fp_logged_in"):
        return redirect(url_for("fieldpulse_dashboard"))

    # Check if user just joined waitlist (for confirmation banner)
    waitlist_success = request.args.get('waitlist') == 'success'

    return render_template_string(
        LANDING_PAGE_TEMPLATE.format(
            show_confirmation='block' if waitlist_success else 'none'
        )
    )


@app.route("/login")
def fieldpulse_login():
    """FieldPulse login - shows Clerk login page directly."""
    return clerk_login_page()


@app.route("/legacy-login", methods=["GET", "POST"])
def fieldpulse_legacy_login():
    """Legacy login form - bypasses Clerk redirect for admin access."""
    error = ""

    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")

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
    <title>FieldPulse — Legacy Login</title>
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
                <p class="text-slate-400 mt-1">Legacy Login (Admin Only)</p>
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
                <a href="/clerk-login" class="text-emerald-400 hover:text-emerald-300">← Back to Clerk login</a>
            </p>
        </div>
    </div>
</body>
</html>""")
    error = ""

    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")

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
    <title>FieldPulse — Legacy Login</title>
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
                <p class="text-slate-400 mt-1">Legacy Login (Admin Only)</p>
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
                <a href="/clerk-login" class="text-emerald-400 hover:text-emerald-300">← Back to Clerk login</a>
            </p>
        </div>
    </div>
</body>
</html>""")


# ── CLERK AUTHENTICATION ───────────────────────────────────────────

@app.route("/clerk-login")
def clerk_login_page():
    """Clerk authentication - uses JWT token pattern with modal sign-in."""
    try:
        clerk_pub_key = os.environ.get("CLERK_PUBLISHABLE_KEY", "")

        logger.info(f"Clerk login page - key present: {bool(clerk_pub_key)}")

        if not clerk_pub_key:
            logger.warning("No CLERK_PUBLISHABLE_KEY, redirecting to legacy login")
            return redirect(url_for("fieldpulse_legacy_login"))

        app_domain = os.environ.get("APP_DOMAIN", request.host_url.rstrip('/'))
        logger.info(f"App domain: {app_domain}")

        # Use the fixed Clerk login template that properly loads UI components
        from modules.clerk_login import render_clerk_login_page

        return render_clerk_login_page(
            clerk_pub_key=clerk_pub_key,
            app_domain=app_domain,
            tailwind_cdn=TAILWIND_CDN,
            custom_css=FIELD_PULSE_CSS
        )
    except Exception as e:
        logger.error(f"Error in clerk_login_page: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return redirect(url_for("fieldpulse_legacy_login"))


@app.route("/clerk-signup")
def clerk_signup_page():
    """Clerk sign-up - redirects to login page with sign-up mode."""
    # Sign up uses the same page as login, just opens sign-up modal
    return redirect(url_for("clerk_login_page"))


@app.route("/api/clerk-verify", methods=["POST"])
def clerk_verify():
    """Verify Clerk JWT token and create Flask session."""
    import jwt
    import requests
    import base64
    import json

    data = request.get_json()
    if not data:
        return jsonify({"success": False, "error": "No data received"}), 400

    token = data.get("token")
    if not token:
        return jsonify({"success": False, "error": "No token provided"}), 400

    clerk_pub_key = os.environ.get("CLERK_PUBLISHABLE_KEY", "")
    if not clerk_pub_key:
        return jsonify({"success": False, "error": "Clerk not configured"}), 500

    try:
        # Extract Clerk instance URL from publishable key
        if clerk_pub_key.startswith("pk_test_"):
            encoded = clerk_pub_key[8:]
        elif clerk_pub_key.startswith("pk_live_"):
            encoded = clerk_pub_key[8:]
        else:
            encoded = clerk_pub_key

        padding = 4 - len(encoded) % 4
        if padding != 4:
            encoded += "=" * padding

        domain = base64.b64decode(encoded).decode("utf-8")
        domain = domain.replace("\x00", "").rstrip("$")
        domain = domain.replace(".clerk.accounts.dev", ".accounts.dev")
        clerk_issuer = f"https://{domain}"

        # Fetch Clerk's JWKS (JSON Web Key Set)
        jwks_url = f"{clerk_issuer}/.well-known/jwks.json"
        jwks_response = requests.get(jwks_url, timeout=10)

        if jwks_response.status_code != 200:
            # Fallback: try without verification for development
            # In production, you should verify the token
            logger.warning(f"Could not fetch JWKS from {jwks_url}, using unverified token")
            unverified = jwt.decode(token, options={"verify_signature": False}, algorithms=["RS256"])
            claims = unverified
        else:
            jwks = jwks_response.json()

            # Get the key ID from token header
            token_header = jwt.get_unverified_header(token)
            kid = token_header.get("kid")

            if not kid:
                return jsonify({"success": False, "error": "Token missing key ID"}), 400

            # Find the matching key
            signing_key = None
            for key in jwks.get("keys", []):
                if key.get("kid") == kid:
                    signing_key = key
                    break

            if not signing_key:
                return jsonify({"success": False, "error": "Signing key not found"}), 400

            # Convert JWK to PEM format
            from jwt.utils import base64url_decode

            def jwk_to_pem(jwk):
                # Simple JWK to PEM conversion for RSA keys
                e = base64url_decode(jwk["e"].encode())
                n = base64url_decode(jwk["n"].encode())

                # Build PEM (simplified - in production use proper library)
                from cryptography.hazmat.primitives import serialization
                from cryptography.hazmat.primitives.asymmetric import rsa
                from cryptography.hazmat.backends import default_backend

                public_numbers = rsa.RSAPublicNumbers(
                    int.from_bytes(e, "big"),
                    int.from_bytes(n, "big")
                )
                public_key = public_numbers.public_key(default_backend())
                pem = public_key.public_bytes(
                    encoding=serialization.Encoding.PEM,
                    format=serialization.PublicFormat.SubjectPublicKeyInfo
                )
                return pem

            try:
                pem_key = jwk_to_pem(signing_key)
                claims = jwt.decode(
                    token,
                    pem_key,
                    algorithms=["RS256"],
                    issuer=clerk_issuer,
                    audience=None  # Or your specific audience if set
                )
            except Exception as e:
                logger.error(f"JWT verification error: {e}")
                # Fallback to unverified for development
                claims = jwt.decode(token, options={"verify_signature": False}, algorithms=["RS256"])

        # Extract user info from claims
        # Note: 'sub' is reserved by Clerk and automatically contains the user ID
        clerk_user_id = claims.get("sub")
        email = claims.get("email", "")
        first_name = claims.get("first_name", "")
        last_name = claims.get("last_name", "")

        # Debug logging - log all available claims
        logger.info(f"Clerk JWT claims: sub={clerk_user_id}, email={email}")
        logger.info(f"Available claims: {list(claims.keys())}")
        logger.debug(f"Full claims: {claims}")

        if not clerk_user_id:
            return jsonify({"success": False, "error": "Invalid token: no user ID"}), 400

        # Look up or create user in database
        user = query_db(
            "SELECT * FROM users WHERE clerk_user_id = %s OR email = %s",
            (clerk_user_id, email),
            one=True
        )

        if user:
            # Existing user - update Clerk ID if needed
            if not user.get("clerk_user_id"):
                query_db(
                    "UPDATE users SET clerk_user_id = %s WHERE id = %s",
                    (clerk_user_id, user["id"])
                )

            # Create session
            session["fp_logged_in"] = True
            session["fp_user_id"] = user["id"]
            session["fp_business_id"] = user.get("business_id")
            session["fp_user_name"] = user.get("name", email.split("@")[0] if email else "User")
            session["clerk_token"] = token

            return jsonify({
                "success": True,
                "user": {
                    "id": user["id"],
                    "name": session["fp_user_name"],
                    "email": email
                }
            })

        else:
            # New user - need to create account
            # Store temp data for onboarding
            session["clerk_pending"] = True
            session["clerk_user_id"] = clerk_user_id
            session["clerk_email"] = email
            session["clerk_name"] = f"{first_name} {last_name}".strip() or email.split("@")[0]

            return jsonify({
                "success": True,
                "new_user": True,
                "redirect": "/clerk-onboarding"
            })

    except jwt.ExpiredSignatureError:
        return jsonify({"success": False, "error": "Token expired"}), 401
    except jwt.InvalidTokenError as e:
        return jsonify({"success": False, "error": f"Invalid token: {str(e)}"}), 401
    except Exception as e:
        logger.error(f"Clerk verification error: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/clerk-callback")
def clerk_callback():
    """Handle callback from Clerk hosted auth - syncs user and creates session."""
    # Get the Clerk session token from cookie or query param
    clerk_token = request.args.get('__session') or request.cookies.get('__session')

    if not clerk_token:
        # No token - redirect to login
        return redirect(url_for('clerk_login_page'))

    # Verify the token and get user info
    from modules.clerk_auth import verify_clerk_jwt
    claims = verify_clerk_jwt(clerk_token)

    if not claims:
        return redirect(url_for('clerk_login_page'))

    clerk_user_id = claims.get('sub')
    email = claims.get('email', '')

    if not clerk_user_id:
        return redirect(url_for('clerk_login_page'))

    # Look up or create user in database
    user = query_db(
        "SELECT * FROM users WHERE clerk_user_id = %s OR email = %s",
        (clerk_user_id, email), one=True
    )

    if user:
        # Existing user - set session
        session['fp_logged_in'] = True
        session['fp_user_id'] = user['id']
        session['fp_business_id'] = user.get('business_id')
        session['fp_user_name'] = user.get('name', email.split('@')[0])

        # Redirect to dashboard
        return redirect('/dashboard')
    else:
        # New user - need onboarding
        # Store temp session data
        session['clerk_user_id'] = clerk_user_id
        session['clerk_email'] = email
        return redirect('/clerk-onboarding')


@app.route("/clerk-onboarding")
def clerk_onboarding():
    """Onboarding page for new Clerk users - create business profile."""
    return render_template_string(f"""<!DOCTYPE html>
<html lang="en" class="dark">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>FieldPulse — Welcome</title>
    {TAILWIND_CDN}
    {FIELD_PULSE_CSS}
</head>
<body class="bg-slate-900 min-h-screen flex items-center justify-center">
    <div class="w-full max-w-lg px-6">
        <div class="bg-slate-800 rounded-2xl shadow-2xl p-8 fade-in">
            <div class="text-center mb-8">
                <div class="w-16 h-16 bg-gradient-to-br from-emerald-400 to-emerald-600 rounded-xl mx-auto mb-4 flex items-center justify-center">
                    <svg class="w-8 h-8 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 10V3L4 14h7v7l9-11h-7z"/>
                    </svg>
                </div>
                <h1 class="text-2xl font-bold text-white">Welcome to FieldPulse!</h1>
                <p class="text-slate-400 mt-2">Let's set up your business profile</p>
            </div>

            <form method="POST" action="/api/create-business" class="space-y-5">
                <div>
                    <label class="block text-sm font-medium text-slate-300 mb-2">Business Name</label>
                    <input type="text" name="business_name" required
                        class="w-full px-4 py-3 bg-slate-900 border border-slate-700 rounded-xl text-white placeholder-slate-500 focus:ring-2 focus:ring-emerald-500 focus:border-transparent transition"
                        placeholder="Acme Landscaping">
                </div>

                <div>
                    <label class="block text-sm font-medium text-slate-300 mb-2">Your Name</label>
                    <input type="text" name="user_name" required
                        class="w-full px-4 py-3 bg-slate-900 border border-slate-700 rounded-xl text-white placeholder-slate-500 focus:ring-2 focus:ring-emerald-500 focus:border-transparent transition"
                        placeholder="John Smith">
                </div>

                <div>
                    <label class="block text-sm font-medium text-slate-300 mb-2">Phone Number</label>
                    <input type="tel" name="phone"
                        class="w-full px-4 py-3 bg-slate-900 border border-slate-700 rounded-xl text-white placeholder-slate-500 focus:ring-2 focus:ring-emerald-500 focus:border-transparent transition"
                        placeholder="(555) 123-4567">
                </div>

                <button type="submit"
                    class="w-full py-3 bg-emerald-500 hover:bg-emerald-600 text-white font-semibold rounded-xl transition shadow-lg shadow-emerald-500/25">
                    Get Started
                </button>
            </form>
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


@app.route("/dashboard")
@fp_login_required
def fieldpulse_dashboard():
    """Main FieldPulse dashboard with status filter tabs."""
    business = get_business_from_session()
    if not business:
        return redirect(url_for("fieldpulse_logout"))

    business_id = business['id']
    stats, recent_jobs, crews = get_dashboard_data(business_id)
    user_name = session.get("fp_user_name", "User")
    user_id = session.get("fp_user_id")

    # Get user's photo
    user = query_db("SELECT photo_url FROM users WHERE id = %s", (user_id,), one=True)
    photo_url = user.get('photo_url', '') if user else ''

    # Build avatar HTML (with URL validation to prevent XSS)
    if photo_url:
        # Validate photo_url is HTTP/HTTPS only
        if photo_url.startswith(('http://', 'https://')):
            from markupsafe import escape
            # Use backend proxy endpoint to serve photos from private S3 bucket
            safe_url = escape(f"/api/profile-photo/{user_id}")
            avatar_html = f'<img src="{safe_url}" alt="Profile" class="w-full h-full object-cover">'
        else:
            avatar_html = user_name[:1].upper()
    else:
        avatar_html = user_name[:1].upper()

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
            quick_actions = f'''<form method="POST" action="/jobs/{job['id']}" class="inline">
                <input type="hidden" name="action" value="start">
                <input type="hidden" name="redirect_to" value="/dashboard">
                <button type="submit" class="text-xs bg-amber-500 hover:bg-amber-600 text-white px-2 py-1 rounded font-medium transition">▶ Start</button>
            </form>'''
        elif job['status'] == 'in_progress':
            quick_actions = f'''<form method="POST" action="/jobs/{job['id']}" class="inline">
                <input type="hidden" name="action" value="complete">
                <input type="hidden" name="redirect_to" value="/dashboard">
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
                    <a href="/jobs/{job['id']}" class="text-xs text-slate-400 hover:text-white px-2 py-1">Edit →</a>
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
                    <a href="/dashboard" class="sidebar-link active flex items-center gap-3 px-4 py-3 rounded-lg text-sm font-medium text-white">
                        <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 6a2 2 0 012-2h2a2 2 0 012 2v2a2 2 0 01-2 2H6a2 2 0 01-2-2V6zM14 6a2 2 0 012-2h2a2 2 0 012 2v2a2 2 0 01-2 2h-2a2 2 0 01-2-2V6zM4 16a2 2 0 012-2h2a2 2 0 012 2v2a2 2 0 01-2 2H6a2 2 0 01-2-2v-2zM14 16a2 2 0 012-2h2a2 2 0 012 2v2a2 2 0 01-2 2h-2a2 2 0 01-2-2v-2z"/>
                        </svg>
                        Dashboard
                    </a>
                    <a href="/jobs" class="sidebar-link flex items-center gap-3 px-4 py-3 rounded-lg text-sm font-medium text-slate-400 hover:text-white">
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
                    <a href="/crews" class="sidebar-link flex items-center gap-3 px-4 py-3 rounded-lg text-sm font-medium text-slate-400 hover:text-white">
                        <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M17 20h5v-2a3 3 0 00-5.356-1.857M17 20H7m10 0v-2c0-.656-.126-1.283-.356-1.857M7 20H2v-2a3 3 0 015.356-1.857M7 20v-2c0-.656.126-1.283.356-1.857m0 0a5.002 5.002 0 019.288 0M15 7a3 3 0 11-6 0 3 3 0 016 0z"/>
                        </svg>
                        Crews
                    </a>
                </nav>
            </div>

            <div class="absolute bottom-0 left-0 right-0 p-4 border-t border-slate-800">
                <a href="/profile" class="flex items-center gap-3 px-4 py-2 rounded-lg hover:bg-slate-800 transition group">
                    <div class="w-8 h-8 rounded-full bg-gradient-to-br from-emerald-400 to-emerald-600 flex items-center justify-center text-sm font-medium text-white overflow-hidden">
                        {avatar_html}
                    </div>
                    <div class="flex-1 min-w-0">
                        <p class="text-sm font-medium text-white truncate group-hover:text-emerald-400 transition">{user_name}</p>
                        <p class="text-xs text-slate-500 truncate">{business.get('subscription_tier', 'Starter').title()} Plan</p>
                    </div>
                    <svg class="w-4 h-4 text-slate-500 group-hover:text-white transition" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 5l7 7-7 7"/>
                    </svg>
                </a>
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
                        <a href="/jobs/new" class="bg-emerald-500 hover:bg-emerald-600 text-white px-4 py-2 rounded-lg text-sm font-medium transition">
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
                            <a href="/jobs" class="text-emerald-400 hover:text-emerald-300 text-sm">View all →</a>
                        </div>

                        <!-- Status Filter Tabs -->
                        <div class="flex gap-2 mb-6 overflow-x-auto pb-2">
                            <a href="/dashboard?status=all" class="px-4 py-2 rounded-lg text-sm font-medium transition whitespace-nowrap {'bg-slate-700 text-white border border-slate-600' if status_filter == 'all' else 'bg-slate-800 text-slate-400 hover:text-white border border-slate-700'}">
                                All ({all_count})
                            </a>
                            <a href="/dashboard?status=scheduled" class="px-4 py-2 rounded-lg text-sm font-medium transition whitespace-nowrap {'bg-blue-500/20 text-blue-400 border border-blue-500/30' if status_filter == 'scheduled' else 'bg-slate-800 text-slate-400 hover:text-white border border-slate-700'}">
                                Queue ({scheduled_count})
                            </a>
                            <a href="/dashboard?status=in_progress" class="px-4 py-2 rounded-lg text-sm font-medium transition whitespace-nowrap {'bg-amber-500/20 text-amber-400 border border-amber-500/30' if status_filter == 'in_progress' else 'bg-slate-800 text-slate-400 hover:text-white border border-slate-700'}">
                                In Progress ({in_progress_count})
                            </a>
                            <a href="/dashboard?status=completed" class="px-4 py-2 rounded-lg text-sm font-medium transition whitespace-nowrap {'bg-emerald-500/20 text-emerald-400 border border-emerald-500/30' if status_filter == 'completed' else 'bg-slate-800 text-slate-400 hover:text-white border border-slate-700'}">
                                Done ({completed_count})
                            </a>
                        </div>

                        <div class="space-y-4">
                            {job_cards if job_cards else '<p class="text-slate-500 text-center py-8">No jobs in this category. <a href="/jobs/new" class="text-emerald-400 hover:text-emerald-300">Create a new job →</a></p>'}
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


@app.route("/profile", methods=["GET", "POST"])
@fp_login_required
def fieldpulse_profile():
    """User profile settings page."""
    business = get_business_from_session()
    if not business:
        return redirect(url_for("fieldpulse_logout"))

    business_id = business["id"]
    user_id = session.get("fp_user_id")
    user_name = session.get("fp_user_name", "User")
    error = None
    success = None

    # Get current user data
    user = query_db(
        "SELECT * FROM users WHERE id = %s AND business_id = %s",
        (user_id, business_id),
        one=True
    )

    if not user:
        return redirect(url_for("fieldpulse_logout"))

    if request.method == "POST":
        action = request.form.get("action", "")

        if action == "update_profile":
            name = request.form.get("name", "").strip()
            email = request.form.get("email", "").strip().lower()
            phone = request.form.get("phone", "").strip()

            if not name:
                error = "Name is required"
            elif not email or "@" not in email:
                error = "Valid email is required"
            else:
                # Check if email is already taken by another user
                existing = query_db(
                    "SELECT id FROM users WHERE email = %s AND id != %s",
                    (email, user_id),
                    one=True
                )
                if existing:
                    error = "Email is already in use by another user"
                else:
                    query_db("""
                        UPDATE users SET name = %s, email = %s, phone = %s, updated_at = NOW()
                        WHERE id = %s AND business_id = %s
                    """, (name, email, phone or None, user_id, business_id))

                    # Update session
                    session["fp_user_name"] = name
                    success = "Profile updated successfully"

                    # Refresh user data
                    user = query_db(
                        "SELECT * FROM users WHERE id = %s",
                        (user_id,),
                        one=True
                    )

        elif action == "update_password":
            current_password = request.form.get("current_password", "")
            new_password = request.form.get("new_password", "")
            confirm_password = request.form.get("confirm_password", "")

            if not current_password or not new_password:
                error = "All password fields are required"
            elif new_password != confirm_password:
                error = "New passwords do not match"
            elif len(new_password) < 8:
                error = "Password must be at least 8 characters"
            else:
                # Verify current password (only for legacy auth users)
                from modules.security import verify_password, hash_password

                if user.get("password_hash") and verify_password(current_password, user["password_hash"]):
                    new_hash = hash_password(new_password)
                    query_db("""
                        UPDATE users SET password_hash = %s, updated_at = NOW()
                        WHERE id = %s AND business_id = %s
                    """, (new_hash, user_id, business_id))
                    success = "Password updated successfully"
                else:
                    error = "Current password is incorrect"

        elif action == "upload_photo":
            # Handle profile photo upload
            if 'photo' in request.files:
                photo = request.files['photo']
                if photo and photo.filename:
                    try:
                        from modules.storage import upload_file

                        # Read file content and upload to S3
                        file_content = photo.read()
                        content_type = photo.content_type or 'image/jpeg'
                        file_url = upload_file(
                            file_content,
                            filename=photo.filename,
                            content_type=content_type,
                            folder="profile-photos"
                        )

                        if file_url:
                            # Update user record with photo URL
                            query_db("""
                                UPDATE users SET photo_url = %s, updated_at = NOW()
                                WHERE id = %s AND business_id = %s
                            """, (file_url, user_id, business_id))

                            logger.info(f"Profile photo uploaded for user {user_id}: {file_url}")
                            success = f"Profile photo updated successfully. URL: {file_url[:50]}..."

                            # Refresh user data
                            user = query_db(
                                "SELECT * FROM users WHERE id = %s",
                                (user_id,),
                                one=True
                            )
                        else:
                            error = "Failed to upload photo"
                    except Exception as e:
                        logger.error(f"Failed to upload profile photo: {e}")
                        error = "Failed to upload photo. Please try again."
                else:
                    error = "Please select a photo to upload"
            else:
                error = "No photo provided"

    # Get profile photo URL
    photo_url = user.get('photo_url', '') if user else ''
    logger.info(f"Profile page - user_id: {user_id}, photo_url: {photo_url}")

    # Build profile photo HTML
    if photo_url:
        avatar_html = f'<img src="{photo_url}" alt="Profile" class="w-full h-full object-cover">'
    else:
        avatar_html = user_name[:1].upper()

    profile_photo_html = f"""
    <div class="flex items-center gap-6">
        <div class="relative">
            <div class="w-24 h-24 rounded-full bg-gradient-to-br from-emerald-400 to-emerald-600 flex items-center justify-center text-3xl font-bold text-white overflow-hidden">
                {avatar_html}
            </div>
            <!-- Debug: photo_url={photo_url} -->
            <label for="photo-upload" class="absolute -bottom-2 -right-2 w-8 h-8 bg-slate-700 hover:bg-slate-600 rounded-full flex items-center justify-center cursor-pointer transition border-2 border-slate-800">
                <svg class="w-4 h-4 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M3 9a2 2 0 012-2h.93a2 2 0 001.664-.89l.812-1.22A2 2 0 0110.07 4h3.86a2 2 0 011.664.89l.812 1.22A2 2 0 0018.07 7H19a2 2 0 012 2v9a2 2 0 01-2 2H5a2 2 0 01-2-2V9z"/>
                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15 13a3 3 0 11-6 0 3 3 0 016 0z"/>
                </svg>
            </label>
        </div>
        <div>
            <h4 class="font-medium text-white">Profile Photo</h4>
            <p class="text-sm text-slate-400">JPG, PNG or GIF. Max 5MB.</p>
        </div>
    </div>
    """

    # Password form HTML for legacy auth users
    password_form_html = """
    <form method="POST" class="space-y-4">
        <input type="hidden" name="action" value="update_password">

        <div>
            <label class="block text-sm font-medium text-slate-300 mb-2">Current Password</label>
            <input type="password" name="current_password" required
                class="w-full px-4 py-3 bg-slate-900 border border-slate-700 rounded-lg text-white focus:ring-2 focus:ring-emerald-500 focus:border-transparent"
                placeholder="Enter your current password">
        </div>

        <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
            <div>
                <label class="block text-sm font-medium text-slate-300 mb-2">New Password</label>
                <input type="password" name="new_password" required minlength="8"
                    class="w-full px-4 py-3 bg-slate-900 border border-slate-700 rounded-lg text-white focus:ring-2 focus:ring-emerald-500 focus:border-transparent"
                    placeholder="At least 8 characters">
            </div>

            <div>
                <label class="block text-sm font-medium text-slate-300 mb-2">Confirm New Password</label>
                <input type="password" name="confirm_password" required minlength="8"
                    class="w-full px-4 py-3 bg-slate-900 border border-slate-700 rounded-lg text-white focus:ring-2 focus:ring-emerald-500 focus:border-transparent"
                    placeholder="Re-enter new password">
            </div>
        </div>

        <div class="pt-4 border-t border-slate-700">
            <button type="submit" class="bg-slate-700 hover:bg-slate-600 text-white px-6 py-3 rounded-lg font-medium transition">
                Update Password
            </button>
        </div>
    </form>
    """

    clerk_message_html = """
    <div class="text-slate-400 text-sm">
        <p>Your account uses Clerk authentication. Password management is handled through Clerk.</p>
        <p class="mt-2">To change your password, please use the Clerk account settings.</p>
    </div>
    """

    return render_template_string(f"""<!DOCTYPE html>
<html lang="en" class="dark">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>FieldPulse — Profile Settings</title>
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
                    <a href="/dashboard" class="sidebar-link flex items-center gap-3 px-4 py-3 rounded-lg text-sm font-medium text-slate-400 hover:text-white">
                        <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 6a2 2 0 012-2h2a2 2 0 012 2v2a2 2 0 01-2 2H6a2 2 0 01-2-2V6zM14 6a2 2 0 012-2h2a2 2 0 012 2v2a2 2 0 01-2 2h-2a2 2 0 01-2-2V6zM4 16a2 2 0 012-2h2a2 2 0 012 2v2a2 2 0 01-2 2H6a2 2 0 01-2-2v-2zM14 16a2 2 0 012-2h2a2 2 0 012 2v2a2 2 0 01-2 2h-2a2 2 0 01-2-2v-2z"/>
                        </svg>
                        Dashboard
                    </a>
                    <a href="/jobs" class="sidebar-link flex items-center gap-3 px-4 py-3 rounded-lg text-sm font-medium text-slate-400 hover:text-white">
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
                    <a href="/crews" class="sidebar-link flex items-center gap-3 px-4 py-3 rounded-lg text-sm font-medium text-slate-400 hover:text-white">
                        <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M17 20h5v-2a3 3 0 00-5.356-1.857M17 20H7m10 0v-2c0-.656-.126-1.283-.356-1.857M7 20H2v-2a3 3 0 015.356-1.857M7 20v-2c0-.656.126-1.283.356-1.857m0 0a5.002 5.002 0 019.288 0M15 7a3 3 0 11-6 0 3 3 0 016 0z"/>
                        </svg>
                        Crews
                    </a>
                </nav>
            </div>

            <div class="absolute bottom-0 left-0 right-0 p-4 border-t border-slate-800">
                <a href="/profile" class="flex items-center gap-3 px-4 py-2 rounded-lg hover:bg-slate-800 transition group">
                    <div class="w-8 h-8 rounded-full bg-gradient-to-br from-emerald-400 to-emerald-600 flex items-center justify-center text-sm font-medium text-white overflow-hidden">
                        {avatar_html}
                    </div>
                    <div class="flex-1 min-w-0">
                        <p class="text-sm font-medium text-white truncate group-hover:text-emerald-400 transition">{user_name}</p>
                        <p class="text-xs text-slate-500 truncate">{business.get('subscription_tier', 'Starter').title()} Plan</p>
                    </div>
                    <svg class="w-4 h-4 text-slate-500 group-hover:text-white transition" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 5l7 7-7 7"/>
                    </svg>
                </a>
            </div>
        </aside>

        <!-- Main Content -->
        <main class="flex-1 ml-64">
            <header class="bg-slate-900 border-b border-slate-800 px-8 py-4 sticky top-0 z-10">
                <div class="flex items-center justify-between">
                    <h2 class="text-xl font-semibold">Profile Settings</h2>
                    <a href="/dashboard" class="text-slate-400 hover:text-white flex items-center gap-2">
                        <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M10 19l-7-7m0 0l7-7m-7 7h18"/>
                        </svg>
                        Back to Dashboard
                    </a>
                </div>
            </header>

            <div class="p-8 max-w-3xl">
                {f'<div class="mb-6 p-4 bg-emerald-500/10 border border-emerald-500/30 rounded-lg text-emerald-400">{escape(success)}</div>' if success else ''}
                {f'<div class="mb-6 p-4 bg-red-500/10 border border-red-500/30 rounded-lg text-red-400">{escape(error)}</div>' if error else ''}

                <!-- Profile Photo Section -->
                <div class="bg-slate-800 rounded-xl border border-slate-700 p-6 mb-6">
                    <h3 class="text-lg font-semibold mb-4 flex items-center gap-2">
                        <svg class="w-5 h-5 text-emerald-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 16l4.586-4.586a2 2 0 012.828 0L16 16m-2-2l1.586-1.586a2 2 0 012.828 0L20 14m-6-6h.01M6 20h12a2 2 0 002-2V6a2 2 0 00-2-2H6a2 2 0 00-2 2v12a2 2 0 002 2z"/>
                        </svg>
                        Profile Photo
                    </h3>

                    <form method="POST" enctype="multipart/form-data" class="space-y-4">
                        <input type="hidden" name="action" value="upload_photo">
                        {profile_photo_html}
                        <input type="file" id="photo-upload" name="photo" accept="image/*" class="hidden" onchange="this.form.submit()">
                    </form>
                </div>

                <!-- Profile Info Section -->
                <div class="bg-slate-800 rounded-xl border border-slate-700 p-6 mb-6">
                    <h3 class="text-lg font-semibold mb-4 flex items-center gap-2">
                        <svg class="w-5 h-5 text-emerald-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M16 7a4 4 0 11-8 0 4 4 0 018 0zM12 14a7 7 0 00-7 7h14a7 7 0 00-7-7z"/>
                        </svg>
                        Personal Information
                    </h3>

                    <form method="POST" class="space-y-4">
                        <input type="hidden" name="action" value="update_profile">

                        <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
                            <div>
                                <label class="block text-sm font-medium text-slate-300 mb-2">Full Name *</label>
                                <input type="text" name="name" value="{escape(user.get('name', ''))}" required
                                    class="w-full px-4 py-3 bg-slate-900 border border-slate-700 rounded-lg text-white focus:ring-2 focus:ring-emerald-500 focus:border-transparent"
                                    placeholder="Your full name">
                            </div>

                            <div>
                                <label class="block text-sm font-medium text-slate-300 mb-2">Email Address *</label>
                                <input type="email" name="email" value="{escape(user.get('email', ''))}" required
                                    class="w-full px-4 py-3 bg-slate-900 border border-slate-700 rounded-lg text-white focus:ring-2 focus:ring-emerald-500 focus:border-transparent"
                                    placeholder="you@example.com">
                            </div>
                        </div>

                        <div>
                            <label class="block text-sm font-medium text-slate-300 mb-2">Phone Number</label>
                            <input type="tel" name="phone" value="{escape(user.get('phone', '') or '')}"
                                class="w-full px-4 py-3 bg-slate-900 border border-slate-700 rounded-lg text-white focus:ring-2 focus:ring-emerald-500 focus:border-transparent"
                                placeholder="(555) 123-4567">
                        </div>

                        <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
                            <div>
                                <label class="block text-sm font-medium text-slate-300 mb-2">Role</label>
                                <input type="text" value="{escape(user.get('role', 'Owner'))}" disabled
                                    class="w-full px-4 py-3 bg-slate-950 border border-slate-800 rounded-lg text-slate-400 cursor-not-allowed">
                                <p class="text-xs text-slate-500 mt-1">Role can only be changed by an administrator</p>
                            </div>

                            <div>
                                <label class="block text-sm font-medium text-slate-300 mb-2">Business</label>
                                <input type="text" value="{escape(business.get('name', ''))}" disabled
                                    class="w-full px-4 py-3 bg-slate-950 border border-slate-800 rounded-lg text-slate-400 cursor-not-allowed">
                            </div>
                        </div>

                        <div class="pt-4 border-t border-slate-700">
                            <button type="submit" class="bg-emerald-500 hover:bg-emerald-600 text-white px-6 py-3 rounded-lg font-medium transition">
                                Save Changes
                            </button>
                        </div>
                    </form>
                </div>

                <!-- Change Password Section (Legacy auth only) -->
                <div class="bg-slate-800 rounded-xl border border-slate-700 p-6 mb-6">
                    <h3 class="text-lg font-semibold mb-4 flex items-center gap-2">
                        <svg class="w-5 h-5 text-emerald-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 15v2m-6 4h12a2 2 0 002-2v-6a2 2 0 00-2-2H6a2 2 0 00-2 2v6a2 2 0 002 2zm10-10V7a4 4 0 00-8 0v4h8z"/>
                        </svg>
                        Change Password
                    </h3>

                    {password_form_html if user.get('password_hash') else clerk_message_html}
                </div>

                <!-- Account Info Section -->
                <div class="bg-slate-800 rounded-xl border border-slate-700 p-6">
                    <h3 class="text-lg font-semibold mb-4 flex items-center gap-2">
                        <svg class="w-5 h-5 text-emerald-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"/>
                        </svg>
                        Account Information
                    </h3>

                    <div class="space-y-3 text-sm">
                        <div class="flex justify-between py-2 border-b border-slate-700">
                            <span class="text-slate-400">Account ID</span>
                            <span class="text-slate-300 font-mono">{user.get('id', 'N/A')[:8]}...</span>
                        </div>
                        <div class="flex justify-between py-2 border-b border-slate-700">
                            <span class="text-slate-400">Member Since</span>
                            <span class="text-slate-300">{user.get('created_at', 'N/A')}</span>
                        </div>
                        <div class="flex justify-between py-2 border-b border-slate-700">
                            <span class="text-slate-400">Last Updated</span>
                            <span class="text-slate-300">{user.get('updated_at', 'N/A')}</span>
                        </div>
                        <div class="flex justify-between py-2">
                            <span class="text-slate-400">Authentication</span>
                            <span class="text-slate-300">{user.get('clerk_user_id') and 'Clerk' or 'Legacy'}</span>
                        </div>
                    </div>
                </div>
            </div>
        </main>
    </div>
</body>
</html>""")


@app.route("/logout")
def fieldpulse_logout():
    """Logout from FieldPulse - handles both Clerk and legacy session auth."""
    # Clear legacy session
    session.pop("fp_logged_in", None)
    session.pop("fp_user_id", None)
    session.pop("fp_business_id", None)
    session.pop("fp_user_name", None)

    # If Clerk is configured, redirect to Clerk sign-out
    if CLERK_AVAILABLE and is_clerk_configured():
        clerk_pub_key = os.environ.get("CLERK_PUBLISHABLE_KEY", "")
        return render_template_string(f"""
        <!DOCTYPE html>
        <html>
        <head>
        </head>
        <body>
            <script type="module">
                import {{ Clerk }} from 'https://cdn.jsdelivr.net/npm/@clerk/clerk-js@latest/dist/clerk.browser.js';
                const clerk = new Clerk("{clerk_pub_key}");
                await clerk.load();
                await clerk.signOut();
                window.location.href = '/clerk-login';
            </script>
        </body>
        </html>""")

    return redirect(url_for("fieldpulse_login"))


# ═════════════════════════════════════════════════════════════════
# CLERK API ROUTES
# ═════════════════════════════════════════════════════════════════

@app.route("/api/create-business", methods=["POST"])
def api_create_business():
    """Create business profile for new Clerk user."""
    import uuid

    # This should be called after Clerk authentication
    business_name = request.form.get("business_name", "").strip()
    user_name = request.form.get("user_name", "").strip()
    phone = request.form.get("phone", "").strip()

    if not business_name or not user_name:
        return jsonify({"error": "Business name and user name are required"}), 400

    # Get Clerk user info from session (set during Clerk callback)
    clerk_user_id = session.get('clerk_user_id')
    clerk_email = session.get('clerk_email')

    # If not in session, try to get from JWT token in request
    if not clerk_user_id and CLERK_AVAILABLE and is_clerk_configured():
        from modules.clerk_auth import get_auth_token_from_request, verify_clerk_jwt
        token = get_auth_token_from_request()
        if token:
            claims = verify_clerk_jwt(token)
            if claims:
                clerk_user_id = claims.get('sub')
                clerk_email = claims.get('email', '')

    try:
        business_id = str(uuid.uuid4())
        user_id = str(uuid.uuid4())

        # Create business with unique slug
        import uuid
        slug = business_name.lower().replace(" ", "-")[:50] + "-" + str(uuid.uuid4())[:8]
        logger.info(f"Creating business: {business_name} (ID: {business_id}, slug: {slug})")
        query_db("""
            INSERT INTO businesses (id, name, slug, phone, plan, active, created_at)
            VALUES (%s, %s, %s, %s, 'starter', true, NOW())
        """, (business_id, business_name, slug, phone))

        # Create user with Clerk info (CRITICAL: store clerk_user_id and real email)
        # Note: users table doesn't have 'active' column - schema mismatch
        user_email = clerk_email if clerk_email else f"user_{user_id[:8]}@fieldpulse.local"
        logger.info(f"Creating user: {user_name} (ID: {user_id}, email: {user_email}, clerk_id: {clerk_user_id})")
        query_db("""
            INSERT INTO users (id, business_id, clerk_user_id, email, password_hash, name, role)
            VALUES (%s, %s, %s, %s, 'clerk_auth', %s, 'owner')
        """, (user_id, business_id, clerk_user_id, user_email, user_name))

        # Set session for legacy auth
        session["fp_logged_in"] = True
        session["fp_user_id"] = user_id
        session["fp_business_id"] = business_id
        session["fp_user_name"] = user_name

        # Mark session as modified to ensure it persists
        session.modified = True

        logger.info(f"Session set: user_id={user_id}, business_id={business_id}")

        # Clear temporary Clerk session data
        session.pop('clerk_user_id', None)
        session.pop('clerk_email', None)

        logger.info("Redirecting to /dashboard")
        return redirect("/dashboard")

    except Exception as e:
        logger.error(f"Failed to create business: {e}")
        return jsonify({"error": "Failed to create business"}), 500


@app.route("/api/clerk-webhook", methods=["POST"])
def clerk_webhook():
    """Handle Clerk webhooks for user events (signup, update, delete)."""
    # Verify webhook signature
    from modules.security import verify_webhook_signature

    payload = request.get_data()
    signature = request.headers.get("Svix-Signature", "")
    webhook_secret = os.environ.get("CLERK_WEBHOOK_SECRET", "")

    if not webhook_secret:
        return jsonify({"error": "Webhook not configured"}), 500

    if not verify_webhook_signature(payload, signature, webhook_secret):
        return jsonify({"error": "Invalid signature"}), 401

    event = request.json
    event_type = event.get("type", "")

    if event_type == "user.created":
        # New user signed up via Clerk
        user_data = event.get("data", {})
        clerk_user_id = user_data.get("id")
        email = user_data.get("email_addresses", [{}])[0].get("email_address", "")
        first_name = user_data.get("first_name", "")
        last_name = user_data.get("last_name", "")

        logger.info(f"New Clerk user: {clerk_user_id} ({email})")

        # Check if this is admin user
        is_admin = email.lower() == "joel@pineydigital.com"

        try:
            # Check if user already exists
            existing = query_db(
                "SELECT * FROM users WHERE email = %s OR clerk_user_id = %s",
                (email, clerk_user_id),
                one=True
            )

            if existing:
                # Update with Clerk ID
                query_db(
                    "UPDATE users SET clerk_user_id = %s, email_verified_clerk = true WHERE id = %s",
                    (clerk_user_id, existing['id'])
                )
            else:
                # Create new user
                if is_admin:
                    # Admin user - create admin business if not exists
                    business = query_db(
                        "SELECT * FROM businesses WHERE slug = 'admin' OR email = %s",
                        (email,), one=True
                    )

                    if not business:
                        # Create admin business
                        business_id = str(uuid.uuid4())
                        query_db("""
                            INSERT INTO businesses (id, name, slug, email, plan, active, clerk_user_id)
                            VALUES (%s, 'Admin Business', 'admin', %s, 'enterprise', true, %s)
                        """, (business_id, email, clerk_user_id))
                    else:
                        business_id = business['id']
                        # Link to Clerk
                        query_db(
                            "UPDATE businesses SET clerk_user_id = %s WHERE id = %s",
                            (clerk_user_id, business_id)
                        )

                    # Create admin user
                    user_id = str(uuid.uuid4())
                    query_db("""
                        INSERT INTO users (id, business_id, email, clerk_user_id, name, role, active, email_verified_clerk)
                        VALUES (%s, %s, %s, %s, %s, 'admin', true, true)
                    """, (user_id, business_id, email, clerk_user_id, f"{first_name} {last_name}".strip() or "Admin"))

                    logger.info(f"Created admin user for {email}")
                else:
                    # Regular user - will complete onboarding
                    user_id = str(uuid.uuid4())
                    query_db("""
                        INSERT INTO users (id, email, clerk_user_id, name, role, active, email_verified_clerk)
                        VALUES (%s, %s, %s, %s, 'owner', true, true)
                    """, (user_id, email, clerk_user_id, f"{first_name} {last_name}".strip() or email.split('@')[0]))

                    logger.info(f"Created regular user for {email}")

        except Exception as e:
            logger.error(f"Failed to process Clerk user.created: {e}")

    elif event_type == "session.created":
        # User signed in - update last login
        user_data = event.get("data", {}).get("user", {})
        clerk_user_id = user_data.get("id")
        if clerk_user_id:
            try:
                query_db(
                    "UPDATE users SET last_login_at = NOW() WHERE clerk_user_id = %s",
                    (clerk_user_id,)
                )
            except Exception as e:
                logger.error(f"Failed to update last_login: {e}")

    return jsonify({"status": "ok"})


# ═════════════════════════════════════════════════════════════════
# PROFILE PHOTO API (proxy for private S3 bucket)
# ═════════════════════════════════════════════════════════════════

@app.route("/api/profile-photo/<user_id>")
def api_profile_photo(user_id):
    """Proxy profile photos from private S3 bucket.

    Returns the user's profile photo with proper content-type.
    Falls back to initials if no photo exists.
    """
    try:
        # Get user's photo URL from database
        user = query_db(
            "SELECT photo_url FROM users WHERE id = %s",
            (user_id,), one=True
        )

        photo_url = user.get('photo_url', '') if user else ''

        if not photo_url:
            return "", 404

        # Get the file from S3
        from modules.storage import get_file
        content, content_type = get_file(photo_url)

        if content:
            from flask import make_response
            response = make_response(content)
            response.headers['Content-Type'] = content_type or 'image/jpeg'
            response.headers['Cache-Control'] = 'private, max-age=3600'
            return response
        else:
            return "", 404

    except Exception as e:
        logger.error(f"Error serving profile photo for user {user_id}: {e}")
        return "", 404


# ═════════════════════════════════════════════════════════════════
# WAITLIST API
# ═════════════════════════════════════════════════════════════════

@app.route("/api/waitlist", methods=["POST"])
def api_waitlist_signup():
    """Handle waitlist signup - save to DB and send emails."""
    try:
        data = request.get_json() or {}

        # Extract fields
        email = data.get('email', '').strip().lower()
        name = data.get('name', '').strip()
        company_name = data.get('company_name', '').strip()
        phone = data.get('phone', '').strip()
        industry = data.get('industry', '').strip()
        company_size = data.get('company_size', '').strip()

        # Validate email
        if not email or '@' not in email:
            return jsonify({"error": "Valid email is required"}), 400

        # Check if already in waitlist
        existing = query_db(
            "SELECT id, status FROM waitlist_entries WHERE email = %s",
            (email,), one=True
        )

        if existing:
            return jsonify({
                "success": True,
                "message": "You're already on the waitlist!",
                "already_exists": True
            })

        # Get metadata
        source = data.get('source', 'website')
        ip_address = request.remote_addr
        user_agent = request.headers.get('User-Agent', '')[:500]

        # Insert into database
        waitlist_id = str(uuid.uuid4())
        query_db("""
            INSERT INTO waitlist_entries (
                id, email, name, company_name, phone, industry, company_size,
                status, source, ip_address, user_agent
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, (
            waitlist_id, email, name, company_name, phone, industry, company_size,
            'pending', source, ip_address, user_agent
        ))

        logger.info(f"New waitlist signup: {email} ({name}, {company_name})")

        # Send confirmation email to user
        send_waitlist_confirmation_email(email, name)

        # Send notification to admin
        send_waitlist_notification_email(email, name, company_name, industry, company_size)

        return jsonify({
            "success": True,
            "message": "Thanks for joining the waitlist!",
            "waitlist_id": waitlist_id
        })

    except Exception as e:
        logger.error(f"Waitlist signup error: {e}")
        return jsonify({"error": "Something went wrong. Please try again."}), 500


def send_waitlist_confirmation_email(email: str, name: str = ""):
    """Send confirmation email to waitlist user."""
    from modules.email_sender import send_email

    greeting = f"Hi {name}," if name else "Hi there,"

    subject = "You're on the FieldPulse waitlist!"

    body = f"""{greeting}

Thanks for joining the FieldPulse waitlist! We're excited to help you streamline your field service operations.

What to expect:
• Beta launches September 2026
• You'll get early access + 50% off for 6 months
• We'll email you when it's ready

Have questions? Reply to this email anytime.

- The FieldPulse Team
https://fieldpulse.pineydigital.com
"""

    html_body = f"""
<!DOCTYPE html>
<html>
<head>
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background: #f5f5f5; padding: 40px 20px; }}
    .container {{ max-width: 500px; margin: 0 auto; background: white; border-radius: 16px; padding: 40px; box-shadow: 0 4px 20px rgba(0,0,0,0.1); }}
    .logo {{ width: 48px; height: 48px; background: linear-gradient(135deg, #10b981, #059669); border-radius: 12px; margin-bottom: 24px; display: flex; align-items: center; justify-content: center; }}
    .logo svg {{ width: 28px; height: 28px; color: white; }}
    h1 {{ color: #0f172a; font-size: 24px; margin: 0 0 16px; }}
    p {{ color: #475569; line-height: 1.6; margin: 0 0 16px; }}
    .features {{ background: #f0fdf4; border-radius: 12px; padding: 20px; margin: 24px 0; }}
    .features h3 {{ color: #065f46; margin: 0 0 12px; font-size: 16px; }}
    .features ul {{ margin: 0; padding-left: 20px; color: #065f46; }}
    .features li {{ margin-bottom: 8px; }}
    .cta {{ display: inline-block; background: #10b981; color: white; padding: 12px 24px; border-radius: 8px; text-decoration: none; margin-top: 8px; }}
    .footer {{ margin-top: 32px; padding-top: 24px; border-top: 1px solid #e2e8f0; color: #94a3b8; font-size: 14px; }}
  </style>
</head>
<body>
  <div class="container">
    <div class="logo">
      <svg fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 10V3L4 14h7v7l9-11h-7z"/></svg>
    </div>
    <h1>You're on the waitlist!</h1>
    <p>{greeting}</p>
    <p>Thanks for joining the FieldPulse waitlist! We're excited to help you streamline your field service operations.</p>

    <div class="features">
      <h3>What to expect:</h3>
      <ul>
        <li>Beta launches <strong>September 2026</strong></li>
        <li>Early access + <strong>50% off for 6 months</strong></li>
        <li>We'll email you when it's ready</li>
      </ul>
    </div>

    <p>Have questions? Reply to this email anytime.</p>

    <div class="footer">
      <p>- The FieldPulse Team</p>
      <p><a href="https://fieldpulse.pineydigital.com" style="color: #10b981;">fieldpulse.pineydigital.com</a></p>
    </div>
  </div>
</body>
</html>
"""

    success, msg = send_email(email, subject, body, html_body)
    if success:
        logger.info(f"Waitlist confirmation email sent to {email}")
    else:
        logger.error(f"Failed to send waitlist confirmation: {msg}")

    return success


def send_waitlist_notification_email(email: str, name: str = "", company_name: str = "",
                                        industry: str = "", company_size: str = ""):
    """Send notification email to admin about new waitlist signup."""
    from modules.email_sender import send_email

    admin_email = os.environ.get("ADMIN_EMAIL", "joel@pineydigital.com")

    subject = f"🎉 New FieldPulse Waitlist Signup: {email}"

    body = f"""New Waitlist Signup

Email: {email}
Name: {name or 'N/A'}
Company: {company_name or 'N/A'}
Industry: {industry or 'N/A'}
Company Size: {company_size or 'N/A'}

View in dashboard: https://fieldpulse.pineydigital.com/admin/waitlist

- FieldPulse System
"""

    html_body = f"""
<!DOCTYPE html>
<html>
<head>
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background: #f5f5f5; padding: 40px 20px; }}
    .container {{ max-width: 500px; margin: 0 auto; background: white; border-radius: 16px; padding: 40px; box-shadow: 0 4px 20px rgba(0,0,0,0.1); }}
    h1 {{ color: #0f172a; font-size: 24px; margin: 0 0 8px; }}
    .subtitle {{ color: #64748b; margin: 0 0 24px; }}
    .details {{ background: #f8fafc; border-radius: 12px; padding: 20px; margin: 24px 0; }}
    .detail-row {{ display: flex; margin-bottom: 12px; }}
    .detail-label {{ width: 120px; color: #64748b; font-weight: 500; }}
    .detail-value {{ color: #0f172a; }}
    .cta {{ display: inline-block; background: #10b981; color: white; padding: 12px 24px; border-radius: 8px; text-decoration: none; margin-top: 8px; }}
  </style>
</head>
<body>
  <div class="container">
    <h1>🎉 New Waitlist Signup</h1>
    <p class="subtitle">Someone just joined the FieldPulse waitlist!</p>

    <div class="details">
      <div class="detail-row">
        <span class="detail-label">Email:</span>
        <span class="detail-value">{email}</span>
      </div>
      <div class="detail-row">
        <span class="detail-label">Name:</span>
        <span class="detail-value">{name or 'N/A'}</span>
      </div>
      <div class="detail-row">
        <span class="detail-label">Company:</span>
        <span class="detail-value">{company_name or 'N/A'}</span>
      </div>
      <div class="detail-row">
        <span class="detail-label">Industry:</span>
        <span class="detail-value">{industry or 'N/A'}</span>
      </div>
      <div class="detail-row">
        <span class="detail-label">Size:</span>
        <span class="detail-value">{company_size or 'N/A'}</span>
      </div>
    </div>

    <a href="https://fieldpulse.pineydigital.com/admin/waitlist" class="cta">View Waitlist</a>
  </div>
</body>
</html>
"""

    success, msg = send_email(admin_email, subject, body, html_body)
    if success:
        logger.info(f"Waitlist notification sent to admin about {email}")
    else:
        logger.error(f"Failed to send admin notification: {msg}")

    return success


# ═════════════════════════════════════════════════════════════════
# ADMIN AUTH ROUTES
# ═════════════════════════════════════════════════════════════════

@app.route("/admin/login", methods=["GET","POST"])
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


@app.route("/admin/logout")
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

            # Migration: Create crews table if it doesn't exist, then add columns
            try:
                query_db("""
                    CREATE TABLE IF NOT EXISTS crews (
                        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                        business_id UUID REFERENCES businesses(id) ON DELETE CASCADE,
                        name VARCHAR(255) NOT NULL,
                        role VARCHAR(255),
                        email VARCHAR(255),
                        phone VARCHAR(50),
                        color VARCHAR(50) DEFAULT 'emerald',
                        active BOOLEAN DEFAULT TRUE,
                        created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
                    )
                """)
                query_db("CREATE INDEX IF NOT EXISTS idx_crews_business ON crews(business_id)")
                query_db("CREATE INDEX IF NOT EXISTS idx_crews_active ON crews(active)")
            except Exception as e:
                return jsonify({"status": "error", "message": f"Crews migration failed: {str(e)}"}), 500

            return jsonify({"status": "success", "message": "Migration completed successfully!"})
        except Exception as e:
            return jsonify({"status": "error", "message": str(e)}), 500

    return """<!DOCTYPE html>
<html><head><title>Admin Tools</title></head>
<body style="font-family:sans-serif;max-width:600px;margin:40px auto;padding:20px">
<h1>Admin Tools</h1>

<div style="background:#f3f4f6;padding:20px;border-radius:8px;margin-bottom:20px">
    <h3>Database Migration</h3>
    <p>Creates: job_notes table, job_photos table, extends crews table (color, role, email, phone).</p>
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
</head><body><div><h1>404</h1><p>Page not found</p><a href="/dashboard" style="color:#10b981">← Go to Dashboard</a></div></body></html>""", 404


@app.errorhandler(500)
def server_error(error):
    """Handle 500 errors."""
    logger.error(f"500 error: {error}")
    return """<!DOCTYPE html>
<html><head><title>500 — Server Error</title>
<style>body{font-family:sans-serif;background:#0f172a;color:#e2e8f0;min-height:100vh;display:flex;align-items:center;justify-content:center;text-align:center}</style>
</head><body><div><h1>500</h1><p>Server error</p><a href="/dashboard" style="color:#10b981">← Go to Dashboard</a></div></body></html>""", 500


# ═════════════════════════════════════════════════════════════════
# RUN
# ═════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    port = int(os.environ.get("DASHBOARD_PORT", 5000))
    print(f"\n  FieldPulse Dashboard")
    print(f"  Running at: http://localhost:{port}")
    print(f"  Login:      http://localhost:{port}/login\n")
    app.run(host="0.0.0.0", port=port, debug=False)

# ═════════════════════════════════════════════════════════════════
# FIELD PULSE JOB ROUTES
# ═════════════════════════════════════════════════════════════════

@app.route("/jobs")
@fp_login_required
def fieldpulse_jobs():
    """Job list page."""
    business = get_business_from_session()
    if not business:
        return redirect(url_for("fieldpulse_logout"))

    business_id = business['id']
    user_name = session.get("fp_user_name", "User")
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
                <form method="POST" action="/jobs/{job['id']}" class="inline">
                    <input type="hidden" name="action" value="start">
                    <input type="hidden" name="redirect_to" value="/jobs">
                    <button type="submit" class="text-xs bg-amber-500 hover:bg-amber-600 text-white px-2 py-1 rounded font-medium transition mr-2">▶ Start</button>
                </form>'''
        elif job['status'] == 'in_progress':
            quick_actions = f'''
                <form method="POST" action="/jobs/{job['id']}" class="inline">
                    <input type="hidden" name="action" value="complete">
                    <input type="hidden" name="redirect_to" value="/jobs">
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
                <a href="/jobs/{job['id']}" class="text-emerald-400 hover:text-emerald-300 font-medium text-xs">Edit →</a>
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
                    <a href="/dashboard" class="sidebar-link flex items-center gap-3 px-4 py-3 rounded-lg text-sm font-medium text-slate-400 hover:text-white">
                        <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 6a2 2 0 012-2h2a2 2 0 012 2v2a2 2 0 01-2 2H6a2 2 0 01-2-2V6z"/>
                        </svg>
                        Dashboard
                    </a>
                    <a href="/jobs" class="sidebar-link active flex items-center gap-3 px-4 py-3 rounded-lg text-sm font-medium text-white">
                        <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2"/>
                        </svg>
                        Jobs
                    </a>
                    <a href="#" class="sidebar-link flex items-center gap-3 px-4 py-3 rounded-lg text-sm font-medium text-slate-400 hover:text-white opacity-60 cursor-not-allowed" title="Coming soon">
                        <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8 7V3m8 4V3m-9 8h10M5 21h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v12a2 2 0 002 2z"/>
                        </svg>
                        Schedule
                    </a>
                    <a href="/crews" class="sidebar-link flex items-center gap-3 px-4 py-3 rounded-lg text-sm font-medium text-slate-400 hover:text-white">
                        <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M17 20h5v-2a3 3 0 00-5.356-1.857M17 20H7m10 0v-2c0-.656-.126-1.283-.356-1.857M7 20H2v-2a3 3 0 015.356-1.857M7 20v-2c0-.656.126-1.283.356-1.857m0 0a5.002 5.002 0 019.288 0M15 7a3 3 0 11-6 0 3 3 0 016 0z"/>
                        </svg>
                        Crews
                    </a>
                </nav>
            </div>

            <div class="absolute bottom-0 left-0 right-0 p-4 border-t border-slate-800">
                <a href="/profile" class="flex items-center gap-3 px-4 py-2 rounded-lg hover:bg-slate-800 transition group">
                    <div class="w-8 h-8 rounded-full bg-gradient-to-br from-emerald-400 to-emerald-600 flex items-center justify-center text-sm font-medium text-white overflow-hidden">
                        {user_name[:1].upper()}
                    </div>
                    <div class="flex-1 min-w-0">
                        <p class="text-sm font-medium text-white truncate group-hover:text-emerald-400 transition">{user_name}</p>
                        <p class="text-xs text-slate-500 truncate">{business.get('subscription_tier', 'Starter').title()} Plan</p>
                    </div>
                    <svg class="w-4 h-4 text-slate-500 group-hover:text-white transition" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 5l7 7-7 7"/>
                    </svg>
                </a>
            </div>
        </aside>

        <!-- Main Content -->
        <main class="flex-1 ml-64">
            <header class="bg-slate-900 border-b border-slate-800 px-8 py-4 sticky top-0 z-10">
                <div class="flex items-center justify-between">
                    <h2 class="text-xl font-semibold">Jobs</h2>
                    <a href="/jobs/new" class="bg-emerald-500 hover:bg-emerald-600 text-white px-4 py-2 rounded-lg font-medium transition">+ New Job</a>
                </div>
            </header>

            <div class="p-8">
                <!-- Filters -->
                <div class="mb-6 flex gap-2">
                    <a href="/jobs" class="px-4 py-2 rounded-lg text-sm font-medium {'bg-emerald-500 text-white' if not status_filter else 'bg-slate-800 text-slate-300 hover:text-white'}">All</a>
                    <a href="/jobs?status=scheduled" class="px-4 py-2 rounded-lg text-sm font-medium {'bg-emerald-500 text-white' if status_filter == 'scheduled' else 'bg-slate-800 text-slate-300 hover:text-white'}">Scheduled</a>
                    <a href="/jobs?status=in_progress" class="px-4 py-2 rounded-lg text-sm font-medium {'bg-emerald-500 text-white' if status_filter == 'in_progress' else 'bg-slate-800 text-slate-300 hover:text-white'}">In Progress</a>
                    <a href="/jobs?status=completed" class="px-4 py-2 rounded-lg text-sm font-medium {'bg-emerald-500 text-white' if status_filter == 'completed' else 'bg-slate-800 text-slate-300 hover:text-white'}">Completed</a>
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


@app.route("/jobs/new", methods=["GET", "POST"])
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
                    <a href="/dashboard" class="sidebar-link flex items-center gap-3 px-4 py-3 rounded-lg text-sm font-medium text-slate-400 hover:text-white">
                        <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 6a2 2 0 012-2h2a2 2 0 012 2v2a2 2 0 01-2 2H6a2 2 0 01-2-2V6z"/>
                        </svg>
                        Dashboard
                    </a>
                    <a href="/jobs" class="sidebar-link active flex items-center gap-3 px-4 py-3 rounded-lg text-sm font-medium text-white">
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
                    <a href="/jobs" class="text-slate-400 hover:text-white">← Back to Jobs</a>
                    <h2 class="text-xl font-semibold">New Job</h2>
                </div>
            </header>

            <div class="p-8 max-w-2xl mx-auto">
                {f'<div class="mb-6 p-4 bg-red-500/10 border border-red-500/20 rounded-lg text-red-400">{error}</div>' if error else ''}

                <!-- Progress Steps -->
                <div class="flex items-center justify-center gap-2 mb-8" id="progressSteps">
                    <div class="step-indicator flex items-center gap-2" data-step="1">
                        <div class="w-8 h-8 rounded-full bg-emerald-500 text-white flex items-center justify-center text-sm font-medium transition">1</div>
                        <span class="text-sm text-white font-medium">Job Details</span>
                    </div>
                    <div class="flex-1 h-px bg-slate-700 max-w-20"></div>
                    <div class="step-indicator flex items-center gap-2" data-step="2">
                        <div class="w-8 h-8 rounded-full bg-slate-700 text-slate-400 flex items-center justify-center text-sm font-medium transition">2</div>
                        <span class="text-sm text-slate-400">Client Info</span>
                    </div>
                    <div class="flex-1 h-px bg-slate-700 max-w-20"></div>
                    <div class="step-indicator flex items-center gap-2" data-step="3">
                        <div class="w-8 h-8 rounded-full bg-slate-700 text-slate-400 flex items-center justify-center text-sm font-medium transition">3</div>
                        <span class="text-sm text-slate-400">Schedule</span>
                    </div>
                </div>

                <form method="POST" id="jobForm" class="relative min-h-[400px]">
                    <!-- Step 1: Job Details -->
                    <div id="step1" class="step-content">
                        <div class="bg-slate-800 rounded-xl p-6 border border-slate-700">
                            <div class="flex items-center gap-3 mb-6">
                                <div class="w-10 h-10 rounded-lg bg-emerald-500/20 flex items-center justify-center">
                                    <svg class="w-5 h-5 text-emerald-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2"/>
                                    </svg>
                                </div>
                                <div>
                                    <h3 class="text-lg font-medium text-white">What type of job?</h3>
                                    <p class="text-sm text-slate-400">Select a service or enter custom title</p>
                                </div>
                            </div>

                            <!-- Quick Select Job Types -->
                            <div class="mb-6">
                                <div class="grid grid-cols-2 gap-2">
                                    <button type="button" onclick="setJobType('Lawn Mowing')" class="job-type-btn px-4 py-3 bg-slate-700 hover:bg-slate-600 text-slate-300 rounded-lg transition text-left">
                                        <div class="font-medium">Lawn Mowing</div>
                                        <div class="text-xs text-slate-400">Regular maintenance</div>
                                    </button>
                                    <button type="button" onclick="setJobType('Hedge Trimming')" class="job-type-btn px-4 py-3 bg-slate-700 hover:bg-slate-600 text-slate-300 rounded-lg transition text-left">
                                        <div class="font-medium">Hedge Trimming</div>
                                        <div class="text-xs text-slate-400">Shaping & maintenance</div>
                                    </button>
                                    <button type="button" onclick="setJobType('Garden Cleanup')" class="job-type-btn px-4 py-3 bg-slate-700 hover:bg-slate-600 text-slate-300 rounded-lg transition text-left">
                                        <div class="font-medium">Garden Cleanup</div>
                                        <div class="text-xs text-slate-400">Seasonal cleaning</div>
                                    </button>
                                    <button type="button" onclick="setJobType('Mulching')" class="job-type-btn px-4 py-3 bg-slate-700 hover:bg-slate-600 text-slate-300 rounded-lg transition text-left">
                                        <div class="font-medium">Mulching</div>
                                        <div class="text-xs text-slate-400">Bed preparation</div>
                                    </button>
                                    <button type="button" onclick="setJobType('Fertilization')" class="job-type-btn px-4 py-3 bg-slate-700 hover:bg-slate-600 text-slate-300 rounded-lg transition text-left">
                                        <div class="font-medium">Fertilization</div>
                                        <div class="text-xs text-slate-400">Lawn treatment</div>
                                    </button>
                                    <button type="button" onclick="setJobType('Weed Control')" class="job-type-btn px-4 py-3 bg-slate-700 hover:bg-slate-600 text-slate-300 rounded-lg transition text-left">
                                        <div class="font-medium">Weed Control</div>
                                        <div class="text-xs text-slate-400">Chemical treatment</div>
                                    </button>
                                </div>
                            </div>

                            <div class="space-y-4">
                                <div>
                                    <label class="block text-sm font-medium text-slate-300 mb-2">Job Title *</label>
                                    <input type="text" name="title" id="jobTitle" required class="w-full px-4 py-3 bg-slate-900 border border-slate-700 rounded-lg text-white focus:ring-2 focus:ring-emerald-500 focus:border-transparent" placeholder="Enter job title...">
                                </div>
                                <div>
                                    <label class="block text-sm font-medium text-slate-300 mb-2">Description</label>
                                    <textarea name="description" rows="3" class="w-full px-4 py-3 bg-slate-900 border border-slate-700 rounded-lg text-white focus:ring-2 focus:ring-emerald-500 focus:border-transparent" placeholder="Add any special instructions or details..."></textarea>
                                </div>
                            </div>
                        </div>

                        <div class="flex justify-end mt-6">
                            <button type="button" onclick="nextStep(2)" class="bg-emerald-500 hover:bg-emerald-600 text-white px-6 py-3 rounded-lg font-medium transition flex items-center gap-2">
                                Next
                                <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 5l7 7-7 7"/>
                                </svg>
                            </button>
                        </div>
                    </div>

                    <!-- Step 2: Customer Information -->
                    <div id="step2" class="step-content hidden">
                        <div class="bg-slate-800 rounded-xl p-6 border border-slate-700">
                            <div class="flex items-center gap-3 mb-6">
                                <div class="w-10 h-10 rounded-lg bg-blue-500/20 flex items-center justify-center">
                                    <svg class="w-5 h-5 text-blue-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M16 7a4 4 0 11-8 0 4 4 0 018 0zM12 14a7 7 0 00-7 7h14a7 7 0 00-7-7z"/>
                                    </svg>
                                </div>
                                <div>
                                    <h3 class="text-lg font-medium text-white">Who is the client?</h3>
                                    <p class="text-sm text-slate-400">Enter customer contact information</p>
                                </div>
                            </div>

                            <div class="space-y-4">
                                <div>
                                    <label class="block text-sm font-medium text-slate-300 mb-2">Customer Name *</label>
                                    <input type="text" name="customer_name" id="customerName" required class="w-full px-4 py-3 bg-slate-900 border border-slate-700 rounded-lg text-white focus:ring-2 focus:ring-emerald-500 focus:border-transparent" placeholder="e.g., John Smith">
                                </div>

                                <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
                                    <div>
                                        <label class="block text-sm font-medium text-slate-300 mb-2">Phone</label>
                                        <input type="tel" name="customer_phone" class="w-full px-4 py-3 bg-slate-900 border border-slate-700 rounded-lg text-white focus:ring-2 focus:ring-emerald-500 focus:border-transparent" placeholder="(555) 123-4567">
                                    </div>
                                    <div>
                                        <label class="block text-sm font-medium text-slate-300 mb-2">Email</label>
                                        <input type="email" name="customer_email" class="w-full px-4 py-3 bg-slate-900 border border-slate-700 rounded-lg text-white focus:ring-2 focus:ring-emerald-500 focus:border-transparent" placeholder="john@example.com">
                                    </div>
                                </div>

                                <div class="pt-4 border-t border-slate-700">
                                    <label class="block text-sm font-medium text-slate-300 mb-3">Service Address</label>
                                    <input type="text" name="address" class="w-full px-4 py-3 bg-slate-900 border border-slate-700 rounded-lg text-white focus:ring-2 focus:ring-emerald-500 focus:border-transparent mb-3" placeholder="Street address">
                                    <div class="grid grid-cols-2 gap-4">
                                        <input type="text" name="city" class="w-full px-4 py-3 bg-slate-900 border border-slate-700 rounded-lg text-white focus:ring-2 focus:ring-emerald-500 focus:border-transparent" placeholder="City">
                                        <input type="text" name="zip_code" class="w-full px-4 py-3 bg-slate-900 border border-slate-700 rounded-lg text-white focus:ring-2 focus:ring-emerald-500 focus:border-transparent" placeholder="ZIP Code">
                                    </div>
                                </div>
                            </div>
                        </div>

                        <div class="flex justify-between mt-6">
                            <button type="button" onclick="prevStep(1)" class="bg-slate-700 hover:bg-slate-600 text-white px-6 py-3 rounded-lg font-medium transition flex items-center gap-2">
                                <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15 19l-7-7 7-7"/>
                                </svg>
                                Back
                            </button>
                            <button type="button" onclick="nextStep(3)" class="bg-emerald-500 hover:bg-emerald-600 text-white px-6 py-3 rounded-lg font-medium transition flex items-center gap-2">
                                Next
                                <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 5l7 7-7 7"/>
                                </svg>
                            </button>
                        </div>
                    </div>

                    <!-- Step 3: Scheduling -->
                    <div id="step3" class="step-content hidden">
                        <div class="bg-slate-800 rounded-xl p-6 border border-slate-700">
                            <div class="flex items-center gap-3 mb-6">
                                <div class="w-10 h-10 rounded-lg bg-amber-500/20 flex items-center justify-center">
                                    <svg class="w-5 h-5 text-amber-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8 7V3m8 4V3m-9 8h10M5 21h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v12a2 2 0 002 2z"/>
                                    </svg>
                                </div>
                                <div>
                                    <h3 class="text-lg font-medium text-white">When should we schedule?</h3>
                                    <p class="text-sm text-slate-400">Select date, time, and assign crew</p>
                                </div>
                            </div>

                            <!-- Date Selection -->
                            <div class="mb-6">
                                <label class="block text-sm font-medium text-slate-300 mb-3">Date *</label>
                                <input type="date" name="scheduled_date" id="scheduledDate" required
                                    class="w-full px-4 py-3 bg-slate-900 border border-slate-700 rounded-lg text-white focus:ring-2 focus:ring-emerald-500 focus:border-transparent text-lg"
                                    style="color-scheme: dark;"
                                    min="{datetime.now().strftime('%Y-%m-%d')}">
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
                            </div>

                            <!-- Duration & Crew -->
                            <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
                                <div>
                                    <label class="block text-sm font-medium text-slate-300 mb-2">Est. Duration</label>
                                    <select name="estimated_duration" class="w-full px-4 py-3 bg-slate-900 border border-slate-700 rounded-lg text-white focus:ring-2 focus:ring-emerald-500 focus:border-transparent">
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
                                    <label class="block text-sm font-medium text-slate-300 mb-2">Assign Crew</label>
                                    <select name="crew_id" class="w-full px-4 py-3 bg-slate-900 border border-slate-700 rounded-lg text-white focus:ring-2 focus:ring-emerald-500 focus:border-transparent">{crew_options}</select>
                                </div>
                            </div>
                        </div>

                        <div class="flex justify-between mt-6">
                            <button type="button" onclick="prevStep(2)" class="bg-slate-700 hover:bg-slate-600 text-white px-6 py-3 rounded-lg font-medium transition flex items-center gap-2">
                                <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15 19l-7-7 7-7"/>
                                </svg>
                                Back
                            </button>
                            <button type="submit" class="bg-emerald-500 hover:bg-emerald-600 text-white px-8 py-3 rounded-lg font-medium transition flex items-center gap-2">
                                <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 13l4 4L19 7"/>
                                </svg>
                                Finish & Create Job
                            </button>
                        </div>
                    </div>
                </form>

                <!-- Toast Notification -->
                <div id="toast" class="fixed bottom-6 right-6 bg-emerald-500 text-white px-6 py-4 rounded-xl shadow-lg transform translate-y-20 opacity-0 transition-all duration-300 flex items-center gap-3 z-50">
                    <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 13l4 4L19 7"/>
                    </svg>
                    <span id="toastMessage">Job created successfully!</span>
                </div>

                <script>
                    // Step navigation
                    function showStep(stepNum) {{
                        // Hide all steps
                        document.querySelectorAll('.step-content').forEach(function(el) {{
                            el.classList.add('hidden');
                        }});

                        // Show current step
                        document.getElementById('step' + stepNum).classList.remove('hidden');

                        // Update progress indicators
                        document.querySelectorAll('.step-indicator').forEach(function(el) {{
                            var step = el.getAttribute('data-step');
                            var circle = el.querySelector('div');
                            var label = el.querySelector('span');

                            if (step == stepNum) {{
                                circle.className = 'w-8 h-8 rounded-full bg-emerald-500 text-white flex items-center justify-center text-sm font-medium transition';
                                label.className = 'text-sm text-white font-medium';
                            }} else if (step < stepNum) {{
                                circle.className = 'w-8 h-8 rounded-full bg-emerald-500/50 text-white flex items-center justify-center text-sm font-medium transition';
                                label.className = 'text-sm text-slate-400';
                            }} else {{
                                circle.className = 'w-8 h-8 rounded-full bg-slate-700 text-slate-400 flex items-center justify-center text-sm font-medium transition';
                                label.className = 'text-sm text-slate-400';
                            }}
                        }});
                    }}

                    function nextStep(step) {{
                        // Validation
                        if (step === 2) {{
                            var title = document.getElementById('jobTitle').value.trim();
                            if (!title) {{
                                showToast('Please enter a job title', 'error');
                                document.getElementById('jobTitle').focus();
                                return;
                            }}
                        }}
                        if (step === 3) {{
                            var customer = document.getElementById('customerName').value.trim();
                            if (!customer) {{
                                showToast('Please enter customer name', 'error');
                                document.getElementById('customerName').focus();
                                return;
                            }}
                        }}
                        showStep(step);
                    }}

                    function prevStep(step) {{
                        showStep(step);
                    }}

                    // Toast notification
                    function showToast(message, type) {{
                        var toast = document.getElementById('toast');
                        var toastMessage = document.getElementById('toastMessage');
                        toastMessage.textContent = message;

                        if (type === 'error') {{
                            toast.className = 'fixed bottom-6 right-6 bg-red-500 text-white px-6 py-4 rounded-xl shadow-lg transform translate-y-0 opacity-100 transition-all duration-300 flex items-center gap-3 z-50';
                        }} else {{
                            toast.className = 'fixed bottom-6 right-6 bg-emerald-500 text-white px-6 py-4 rounded-xl shadow-lg transform translate-y-0 opacity-100 transition-all duration-300 flex items-center gap-3 z-50';
                        }}

                        setTimeout(function() {{
                            toast.classList.add('translate-y-20', 'opacity-0');
                        }}, 3000);
                    }}

                    // Set job type helper
                    function setJobType(title) {{
                        document.getElementById('jobTitle').value = title;
                        // Highlight the button briefly
                        event.target.closest('button').classList.add('ring-2', 'ring-emerald-400');
                        setTimeout(function() {{
                            event.target.closest('button').classList.remove('ring-2', 'ring-emerald-400');
                        }}, 300);
                    }}

                    // Initialize first step
                    showStep(1);
                </script>
            </div>
        </main>
    </div>
</body>
</html>''')


# Job Detail Route

@app.route("/jobs/<job_id>", methods=["GET", "POST"])
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
    user_id = session.get("fp_user_id")

    # Get user's photo
    user = query_db("SELECT photo_url FROM users WHERE id = %s", (user_id,), one=True)
    photo_url = user.get('photo_url', '') if user else ''

    # Build avatar HTML (with URL validation to prevent XSS)
    if photo_url:
        # Validate photo_url is HTTP/HTTPS only
        if photo_url.startswith(('http://', 'https://')):
            from markupsafe import escape
            # Use backend proxy endpoint to serve photos from private S3 bucket
            safe_url = escape(f"/api/profile-photo/{user_id}")
            avatar_html = f'<img src="{safe_url}" alt="Profile" class="w-full h-full object-cover">'
        else:
            avatar_html = user_name[:1].upper()
    else:
        avatar_html = user_name[:1].upper()

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
        redirect_to = request.form.get("redirect_to") or request.headers.get("Referer", "/jobs")
        # Security: Only allow relative redirects to our own domain
        ALLOWED_REDIRECTS = ['/dashboard', '/jobs', '/schedule', '/crew']
        if redirect_to not in ALLOWED_REDIRECTS:
            redirect_to = "/jobs"

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
                    <a href="/dashboard" class="sidebar-link flex items-center gap-3 px-4 py-3 rounded-lg text-sm font-medium text-slate-400 hover:text-white">
                        <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 6a2 2 0 012-2h2a2 2 0 012 2v2a2 2 0 01-2 2H6a2 2 0 01-2-2V6z"/>
                        </svg>
                        Dashboard
                    </a>
                    <a href="/jobs" class="sidebar-link active flex items-center gap-3 px-4 py-3 rounded-lg text-sm font-medium text-white">
                        <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2"/>
                        </svg>
                        Jobs
                    </a>
                    <a href="#" class="sidebar-link flex items-center gap-3 px-4 py-3 rounded-lg text-sm font-medium text-slate-400 hover:text-white opacity-60 cursor-not-allowed" title="Coming soon">
                        <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8 7V3m8 4V3m-9 8h10M5 21h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v12a2 2 0 002 2z"/>
                        </svg>
                        Schedule
                    </a>
                    <a href="/crews" class="sidebar-link flex items-center gap-3 px-4 py-3 rounded-lg text-sm font-medium text-slate-400 hover:text-white">
                        <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M17 20h5v-2a3 3 0 00-5.356-1.857M17 20H7m10 0v-2c0-.656-.126-1.283-.356-1.857M7 20H2v-2a3 3 0 015.356-1.857M7 20v-2c0-.656.126-1.283.356-1.857m0 0a5.002 5.002 0 019.288 0M15 7a3 3 0 11-6 0 3 3 0 016 0z"/>
                        </svg>
                        Crews
                    </a>
                </nav>
            </div>

            <div class="absolute bottom-0 left-0 right-0 p-4 border-t border-slate-800">
                <a href="/profile" class="flex items-center gap-3 px-4 py-2 rounded-lg hover:bg-slate-800 transition group">
                    <div class="w-8 h-8 rounded-full bg-gradient-to-br from-emerald-400 to-emerald-600 flex items-center justify-center text-sm font-medium text-white overflow-hidden">
                        {avatar_html}
                    </div>
                    <div class="flex-1 min-w-0">
                        <p class="text-sm font-medium text-white truncate group-hover:text-emerald-400 transition">{user_name}</p>
                        <p class="text-xs text-slate-500 truncate">{business.get('subscription_tier', 'Starter').title()} Plan</p>
                    </div>
                    <svg class="w-4 h-4 text-slate-500 group-hover:text-white transition" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 5l7 7-7 7"/>
                    </svg>
                </a>
            </div>
        </aside>

        <!-- Main Content -->
        <main class="flex-1 ml-64">
            <header class="bg-slate-900 border-b border-slate-800 px-8 py-4 sticky top-0 z-10">
                <div class="flex items-center justify-between">
                    <div class="flex items-center gap-4">
                        <a href="/jobs" class="text-slate-400 hover:text-white">← Back to Jobs</a>
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
                        <a href="/jobs" class="bg-slate-700 hover:bg-slate-600 text-white px-6 py-3 rounded-lg font-medium transition">Cancel</a>
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


@app.route("/crews")
@fp_login_required
def fieldpulse_crews():
    """Crew management - list all crews."""
    business = get_business_from_session()
    if not business:
        return redirect(url_for("fieldpulse_logout"))

    business_id = business["id"]
    user_name = session.get("fp_user_name", "User")
    user_id = session.get("fp_user_id")

    # Get user's photo
    user = query_db("SELECT photo_url FROM users WHERE id = %s", (user_id,), one=True)
    photo_url = user.get('photo_url', '') if user else ''

    # Build avatar HTML (with URL validation to prevent XSS)
    if photo_url:
        # Validate photo_url is HTTP/HTTPS only
        if photo_url.startswith(('http://', 'https://')):
            from markupsafe import escape
            # Use backend proxy endpoint to serve photos from private S3 bucket
            safe_url = escape(f"/api/profile-photo/{user_id}")
            avatar_html = f'<img src="{safe_url}" alt="Profile" class="w-full h-full object-cover">'
        else:
            avatar_html = user_name[:1].upper()
    else:
        avatar_html = user_name[:1].upper()

    # Get all crews for this business
    crews = query_db(
        """SELECT c.*,
               (SELECT COUNT(*) FROM jobs WHERE crew_id = c.id AND status IN ('scheduled', 'in_progress')) as active_jobs
           FROM crews c
           WHERE c.business_id = %s AND c.active = true
           ORDER BY c.name""",
        (business_id,)
    )

    # Build crew cards
    crew_cards = ""
    color_options = [
        ("emerald", "Emerald"),
        ("blue", "Blue"),
        ("purple", "Purple"),
        ("amber", "Amber"),
        ("rose", "Rose"),
        ("cyan", "Cyan"),
        ("orange", "Orange"),
        ("pink", "Pink")
    ]

    for crew in crews:
        color = crew.get("color", "emerald")
        active_jobs = crew.get("active_jobs", 0)
        crew_cards += f'''
        <div class="bg-slate-800 rounded-xl border border-slate-700 overflow-hidden hover:border-slate-600 transition">
            <div class="p-6">
                <div class="flex items-start justify-between mb-4">
                    <div class="flex items-center gap-4">
                        <div class="w-14 h-14 rounded-xl bg-{color}-500/20 flex items-center justify-center">
                            <span class="text-2xl font-bold text-{color}-400">{crew.get("name", "C")[:1].upper()}</span>
                        </div>
                        <div>
                            <h3 class="text-lg font-semibold text-white">{crew.get("name", "Unnamed Crew")}</h3>
                            <p class="text-sm text-slate-400">{crew.get("role", "Team Member")}</p>
                        </div>
                    </div>
                    <div class="flex gap-2">
                        <a href="/crews/{crew["id"]}/edit"
                           class="p-2 bg-slate-700 hover:bg-slate-600 text-slate-300 rounded-lg transition">
                            <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M11 5H6a2 2 0 00-2 2v11a2 2 0 002 2h11a2 2 0 002-2v-5m-1.414-9.414a2 2 0 112.828 2.828L11.828 15H9v-2.828l8.586-8.586z"/>
                            </svg>
                        </a>
                    </div>
                </div>
                <div class="space-y-2 text-sm">
                    {f'<div class="flex items-center gap-2 text-slate-400"><svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M3 8l7.89 5.26a2 2 0 002.22 0L21 8M5 19h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v10a2 2 0 002 2z"/></svg>{crew.get("email")}</div>' if crew.get("email") else ''}
                    {f'<div class="flex items-center gap-2 text-slate-400"><svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M3 5a2 2 0 012-2h3.28a1 1 0 01.948.684l1.498 4.493a1 1 0 01-.502 1.21l-2.257 1.13a11.042 11.042 0 005.516 5.516l1.13-2.257a1 1 0 011.21-.502l4.493 1.498a1 1 0 01.684.949V19a2 2 0 01-2 2h-1C9.716 21 3 14.284 3 6V5z"/></svg>{crew.get("phone")}</div>' if crew.get("phone") else ''}
                </div>
            </div>
            <div class="px-6 py-3 bg-slate-900/50 border-t border-slate-700 flex items-center justify-between">
                <span class="text-sm text-slate-400">{active_jobs} active job{"s" if active_jobs != 1 else ""}</span>
                <a href="/jobs?crew={crew["id"]}" class="text-sm text-{color}-400 hover:text-{color}-300 transition">View jobs →</a>
            </div>
        </div>
        '''

    return render_template_string(f"""<!DOCTYPE html>
<html lang="en" class="dark">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>FieldPulse — Crews</title>
    {TAILWIND_CDN}
    {FIELD_PULSE_CSS}
</head>
<body class="bg-slate-900 text-white">
    <div class="flex h-screen">
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
                    <a href="/dashboard" class="sidebar-link flex items-center gap-3 px-4 py-3 rounded-lg text-sm font-medium text-slate-400 hover:text-white">
                        <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 6a2 2 0 012-2h2a2 2 0 012 2v2a2 2 0 01-2 2H6a2 2 0 01-2-2V6zM14 6a2 2 0 012-2h2a2 2 0 012 2v2a2 2 0 01-2 2h-2a2 2 0 01-2-2V6zM4 16a2 2 0 012-2h2a2 2 0 012 2v2a2 2 0 01-2 2H6a2 2 0 01-2-2v-2zM14 16a2 2 0 012-2h2a2 2 0 012 2v2a2 2 0 01-2 2h-2a2 2 0 01-2-2v-2z"/>
                        </svg>
                        Dashboard
                    </a>
                    <a href="/jobs" class="sidebar-link flex items-center gap-3 px-4 py-3 rounded-lg text-sm font-medium text-slate-400 hover:text-white">
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
                    <a href="/crews" class="sidebar-link active flex items-center gap-3 px-4 py-3 rounded-lg text-sm font-medium text-white">
                        <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M17 20h5v-2a3 3 0 00-5.356-1.857M17 20H7m10 0v-2c0-.656-.126-1.283-.356-1.857M7 20H2v-2a3 3 0 015.356-1.857M7 20v-2c0-.656.126-1.283.356-1.857m0 0a5.002 5.002 0 019.288 0M15 7a3 3 0 11-6 0 3 3 0 016 0z"/>
                        </svg>
                        Crews
                    </a>
                </nav>
            </div>

            <div class="absolute bottom-0 left-0 right-0 p-4 border-t border-slate-800">
                <div class="flex items-center gap-3 px-4 py-2">
                    <div class="w-8 h-8 rounded-full bg-slate-700 flex items-center justify-center text-sm font-medium overflow-hidden">
                        {avatar_html}
                    </div>
                    <div class="flex-1 min-w-0">
                        <p class="text-sm font-medium text-white truncate">{user_name}</p>
                        <a href="/logout" class="text-xs text-slate-500 hover:text-slate-400">Sign out</a>
                    </div>
                </div>
            </div>
        </aside>

        <!-- Main Content -->
        <main class="flex-1 ml-64">
            <!-- Header -->
            <div class="bg-slate-900 border-b border-slate-800 sticky top-0 z-10">
                <div class="px-8 py-4 flex items-center justify-between">
                    <div>
                        <h2 class="text-2xl font-bold text-white">Crew Management</h2>
                        <p class="text-slate-400">Manage your team members and assignments</p>
                    </div>
                    <a href="/crews/new" class="bg-emerald-500 hover:bg-emerald-600 text-white px-4 py-2 rounded-lg font-medium transition flex items-center gap-2">
                        <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 4v16m8-8H4"/>
                        </svg>
                        Add Crew
                    </a>
                </div>
            </div>

            <div class="p-8">
                {f'<div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">{crew_cards}</div>' if crew_cards else '''
                <div class="text-center py-16">
                    <div class="w-20 h-20 mx-auto mb-6 rounded-full bg-slate-800 flex items-center justify-center">
                        <svg class="w-10 h-10 text-slate-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M17 20h5v-2a3 3 0 00-5.356-1.857M17 20H7m10 0v-2c0-.656-.126-1.283-.356-1.857M7 20H2v-2a3 3 0 015.356-1.857M7 20v-2c0-.656.126-1.283.356-1.857m0 0a5.002 5.002 0 019.288 0M15 7a3 3 0 11-6 0 3 3 0 016 0z"/>
                        </svg>
                    </div>
                    <h3 class="text-xl font-semibold text-white mb-2">No crews yet</h3>
                    <p class="text-slate-400 mb-6">Add your first crew member to start assigning jobs</p>
                    <a href="/crews/new" class="bg-emerald-500 hover:bg-emerald-600 text-white px-6 py-3 rounded-lg font-medium transition inline-flex items-center gap-2">
                        <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 4v16m8-8H4"/>
                        </svg>
                        Add Your First Crew
                    </a>
                </div>
                '''}
            </div>
        </main>
    </div>
</body>
</html>""")


@app.route("/crews/new", methods=["GET", "POST"])
@fp_login_required
def fieldpulse_crew_new():
    """Create a new crew member."""
    business = get_business_from_session()
    if not business:
        return redirect(url_for("fieldpulse_logout"))

    business_id = business["id"]
    user_name = session.get("fp_user_name", "User")
    user_id = session.get("fp_user_id")

    # Get user's photo
    user = query_db("SELECT photo_url FROM users WHERE id = %s", (user_id,), one=True)
    photo_url = user.get('photo_url', '') if user else ''

    # Build avatar HTML (with URL validation to prevent XSS)
    if photo_url:
        # Validate photo_url is HTTP/HTTPS only
        if photo_url.startswith(('http://', 'https://')):
            from markupsafe import escape
            # Use backend proxy endpoint to serve photos from private S3 bucket
            safe_url = escape(f"/api/profile-photo/{user_id}")
            avatar_html = f'<img src="{safe_url}" alt="Profile" class="w-full h-full object-cover">'
        else:
            avatar_html = user_name[:1].upper()
    else:
        avatar_html = user_name[:1].upper()

    error = None

    color_options = [
        ("emerald", "Emerald"),
        ("blue", "Blue"),
        ("purple", "Purple"),
        ("amber", "Amber"),
        ("rose", "Rose"),
        ("cyan", "Cyan"),
        ("orange", "Orange"),
        ("pink", "Pink")
    ]

    if request.method == "POST":
        name = request.form.get("name", "").strip()
        role = request.form.get("role", "").strip()
        email = request.form.get("email", "").strip()
        phone = request.form.get("phone", "").strip()
        color = request.form.get("color", "emerald")

        if not name:
            error = "Crew name is required"
        else:
            crew_id = str(uuid.uuid4())
            query_db("""
                INSERT INTO crews (id, business_id, name, role, email, phone, color, active, created_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, true, NOW())
            """, (crew_id, business_id, name, role or None, email or None, phone or None, color))

            invalidate_cache(f"crews:{business_id}")
            return redirect("/crews")

    color_options_html = ""
    for val, label in color_options:
        selected = "selected" if val == "emerald" else ""
        color_options_html += f'<option value="{val}" {selected}>{label}</option>'

    return render_template_string(f"""<!DOCTYPE html>
<html lang="en" class="dark">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>FieldPulse — Add Crew</title>
    {TAILWIND_CDN}
    {FIELD_PULSE_CSS}
</head>
<body class="bg-slate-900 text-white">
    <div class="flex h-screen">
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
                    <a href="/dashboard" class="sidebar-link flex items-center gap-3 px-4 py-3 rounded-lg text-sm font-medium text-slate-400 hover:text-white">
                        <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 6a2 2 0 012-2h2a2 2 0 012 2v2a2 2 0 01-2 2H6a2 2 0 01-2-2V6zM14 6a2 2 0 012-2h2a2 2 0 012 2v2a2 2 0 01-2 2h-2a2 2 0 01-2-2V6zM4 16a2 2 0 012-2h2a2 2 0 012 2v2a2 2 0 01-2 2H6a2 2 0 01-2-2v-2zM14 16a2 2 0 012-2h2a2 2 0 012 2v2a2 2 0 01-2 2h-2a2 2 0 01-2-2v-2z"/>
                        </svg>
                        Dashboard
                    </a>
                    <a href="/jobs" class="sidebar-link flex items-center gap-3 px-4 py-3 rounded-lg text-sm font-medium text-slate-400 hover:text-white">
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
                    <a href="/crews" class="sidebar-link active flex items-center gap-3 px-4 py-3 rounded-lg text-sm font-medium text-white">
                        <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M17 20h5v-2a3 3 0 00-5.356-1.857M17 20H7m10 0v-2c0-.656-.126-1.283-.356-1.857M7 20H2v-2a3 3 0 015.356-1.857M7 20v-2c0-.656.126-1.283.356-1.857m0 0a5.002 5.002 0 019.288 0M15 7a3 3 0 11-6 0 3 3 0 016 0z"/>
                        </svg>
                        Crews
                    </a>
                </nav>
            </div>

            <div class="absolute bottom-0 left-0 right-0 p-4 border-t border-slate-800">
                <div class="flex items-center gap-3 px-4 py-2">
                    <div class="w-8 h-8 rounded-full bg-slate-700 flex items-center justify-center text-sm font-medium overflow-hidden">
                        {avatar_html}
                    </div>
                    <div class="flex-1 min-w-0">
                        <p class="text-sm font-medium text-white truncate">{user_name}</p>
                        <a href="/logout" class="text-xs text-slate-500 hover:text-slate-400">Sign out</a>
                    </div>
                </div>
            </div>
        </aside>

        <!-- Main Content -->
        <main class="flex-1 ml-64">
            <!-- Header -->
            <div class="bg-slate-900 border-b border-slate-800 sticky top-0 z-10">
                <div class="px-8 py-4 flex items-center justify-between">
                    <div class="flex items-center gap-4">
                        <a href="/crews" class="text-slate-400 hover:text-white transition">
                            <svg class="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15 19l-7-7 7-7"/>
                            </svg>
                        </a>
                        <div>
                            <h2 class="text-2xl font-bold text-white">Add New Crew</h2>
                            <p class="text-slate-400">Create a new team member</p>
                        </div>
                    </div>
                </div>
            </div>

            <div class="p-8 max-w-2xl">
                {f'<div class="mb-6 p-4 bg-red-500/10 border border-red-500/30 rounded-lg text-red-400">{error}</div>' if error else ''}

                <form method="POST" class="bg-slate-800 rounded-xl border border-slate-700 p-6 space-y-6">
                    <div>
                        <label class="block text-sm font-medium text-slate-300 mb-2">Crew Name *</label>
                        <input type="text" name="name" required
                            class="w-full px-4 py-3 bg-slate-900 border border-slate-700 rounded-lg text-white focus:ring-2 focus:ring-emerald-500 focus:border-transparent"
                            placeholder="e.g., Mike's Maintenance Team">
                    </div>

                    <div>
                        <label class="block text-sm font-medium text-slate-300 mb-2">Role</label>
                        <input type="text" name="role"
                            class="w-full px-4 py-3 bg-slate-900 border border-slate-700 rounded-lg text-white focus:ring-2 focus:ring-emerald-500 focus:border-transparent"
                            placeholder="e.g., Lead Landscaper, Technician">
                    </div>

                    <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
                        <div>
                            <label class="block text-sm font-medium text-slate-300 mb-2">Email</label>
                            <input type="email" name="email"
                                class="w-full px-4 py-3 bg-slate-900 border border-slate-700 rounded-lg text-white focus:ring-2 focus:ring-emerald-500 focus:border-transparent"
                                placeholder="crew@example.com">
                        </div>
                        <div>
                            <label class="block text-sm font-medium text-slate-300 mb-2">Phone</label>
                            <input type="tel" name="phone"
                                class="w-full px-4 py-3 bg-slate-900 border border-slate-700 rounded-lg text-white focus:ring-2 focus:ring-emerald-500 focus:border-transparent"
                                placeholder="(555) 123-4567">
                        </div>
                    </div>

                    <div>
                        <label class="block text-sm font-medium text-slate-300 mb-2">Calendar Color</label>
                        <select name="color"
                            class="w-full px-4 py-3 bg-slate-900 border border-slate-700 rounded-lg text-white focus:ring-2 focus:ring-emerald-500 focus:border-transparent">
                            {color_options_html}
                        </select>
                        <p class="text-xs text-slate-500 mt-1">This color will be used for the crew's assignments on the calendar</p>
                    </div>

                    <div class="flex justify-end gap-3 pt-4 border-t border-slate-700">
                        <a href="/crews" class="bg-slate-700 hover:bg-slate-600 text-white px-6 py-3 rounded-lg font-medium transition">Cancel</a>
                        <button type="submit" class="bg-emerald-500 hover:bg-emerald-600 text-white px-6 py-3 rounded-lg font-medium transition">Create Crew</button>
                    </div>
                </form>
            </div>
        </main>
    </div>
</body>
</html>""")


@app.route("/crews/<crew_id>/edit", methods=["GET", "POST"])
@fp_login_required
def fieldpulse_crew_edit(crew_id):
    """Edit a crew member."""
    business = get_business_from_session()
    if not business:
        return redirect(url_for("fieldpulse_logout"))

    business_id = business["id"]
    user_name = session.get("fp_user_name", "User")
    user_id = session.get("fp_user_id")

    # Get user's photo
    user = query_db("SELECT photo_url FROM users WHERE id = %s", (user_id,), one=True)
    photo_url = user.get('photo_url', '') if user else ''

    # Build avatar HTML (with URL validation to prevent XSS)
    if photo_url:
        # Validate photo_url is HTTP/HTTPS only
        if photo_url.startswith(('http://', 'https://')):
            from markupsafe import escape
            # Use backend proxy endpoint to serve photos from private S3 bucket
            safe_url = escape(f"/api/profile-photo/{user_id}")
            avatar_html = f'<img src="{safe_url}" alt="Profile" class="w-full h-full object-cover">'
        else:
            avatar_html = user_name[:1].upper()
    else:
        avatar_html = user_name[:1].upper()

    error = None

    # Get crew details
    crew = query_db(
        "SELECT * FROM crews WHERE id = %s AND business_id = %s AND active = true",
        (crew_id, business_id),
        one=True
    )

    if not crew:
        return redirect("/crews")

    color_options = [
        ("emerald", "Emerald"),
        ("blue", "Blue"),
        ("purple", "Purple"),
        ("amber", "Amber"),
        ("rose", "Rose"),
        ("cyan", "Cyan"),
        ("orange", "Orange"),
        ("pink", "Pink")
    ]

    if request.method == "POST":
        action = request.form.get("action", "")

        if action == "delete":
            query_db(
                "UPDATE crews SET active = false WHERE id = %s AND business_id = %s",
                (crew_id, business_id)
            )
            invalidate_cache(f"crews:{business_id}")
            return redirect("/crews")

        name = request.form.get("name", "").strip()
        role = request.form.get("role", "").strip()
        email = request.form.get("email", "").strip()
        phone = request.form.get("phone", "").strip()
        color = request.form.get("color", "emerald")

        if not name:
            error = "Crew name is required"
        else:
            query_db("""
                UPDATE crews SET name = %s, role = %s, email = %s, phone = %s, color = %s
                WHERE id = %s AND business_id = %s
            """, (name, role or None, email or None, phone or None, color, crew_id, business_id))

            invalidate_cache(f"crews:{business_id}")
            return redirect("/crews")

    color_options_html = ""
    current_color = crew.get("color", "emerald")
    for val, label in color_options:
        selected = "selected" if val == current_color else ""
        color_options_html += f'<option value="{val}" {selected}>{label}</option>'

    return render_template_string(f"""<!DOCTYPE html>
<html lang="en" class="dark">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>FieldPulse — Edit Crew</title>
    {TAILWIND_CDN}
    {FIELD_PULSE_CSS}
</head>
<body class="bg-slate-900 text-white">
    <div class="flex h-screen">
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
                    <a href="/dashboard" class="sidebar-link flex items-center gap-3 px-4 py-3 rounded-lg text-sm font-medium text-slate-400 hover:text-white">
                        <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 6a2 2 0 012-2h2a2 2 0 012 2v2a2 2 0 01-2 2H6a2 2 0 01-2-2V6zM14 6a2 2 0 012-2h2a2 2 0 012 2v2a2 2 0 01-2 2h-2a2 2 0 01-2-2V6zM4 16a2 2 0 012-2h2a2 2 0 012 2v2a2 2 0 01-2 2H6a2 2 0 01-2-2v-2zM14 16a2 2 0 012-2h2a2 2 0 012 2v2a2 2 0 01-2 2h-2a2 2 0 01-2-2v-2z"/>
                        </svg>
                        Dashboard
                    </a>
                    <a href="/jobs" class="sidebar-link flex items-center gap-3 px-4 py-3 rounded-lg text-sm font-medium text-slate-400 hover:text-white">
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
                    <a href="/crews" class="sidebar-link active flex items-center gap-3 px-4 py-3 rounded-lg text-sm font-medium text-white">
                        <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M17 20h5v-2a3 3 0 00-5.356-1.857M17 20H7m10 0v-2c0-.656-.126-1.283-.356-1.857M7 20H2v-2a3 3 0 015.356-1.857M7 20v-2c0-.656.126-1.283.356-1.857m0 0a5.002 5.002 0 019.288 0M15 7a3 3 0 11-6 0 3 3 0 016 0z"/>
                        </svg>
                        Crews
                    </a>
                </nav>
            </div>

            <div class="absolute bottom-0 left-0 right-0 p-4 border-t border-slate-800">
                <div class="flex items-center gap-3 px-4 py-2">
                    <div class="w-8 h-8 rounded-full bg-slate-700 flex items-center justify-center text-sm font-medium overflow-hidden">
                        {avatar_html}
                    </div>
                    <div class="flex-1 min-w-0">
                        <p class="text-sm font-medium text-white truncate">{user_name}</p>
                        <a href="/logout" class="text-xs text-slate-500 hover:text-slate-400">Sign out</a>
                    </div>
                </div>
            </div>
        </aside>

        <!-- Main Content -->
        <main class="flex-1 ml-64">
            <!-- Header -->
            <div class="bg-slate-900 border-b border-slate-800 sticky top-0 z-10">
                <div class="px-8 py-4 flex items-center justify-between">
                    <div class="flex items-center gap-4">
                        <a href="/crews" class="text-slate-400 hover:text-white transition">
                            <svg class="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15 19l-7-7 7-7"/>
                            </svg>
                        </a>
                        <div>
                            <h2 class="text-2xl font-bold text-white">Edit Crew</h2>
                            <p class="text-slate-400">Update crew member details</p>
                        </div>
                    </div>
                </div>
            </div>

            <div class="p-8 max-w-2xl">
                {f'<div class="mb-6 p-4 bg-red-500/10 border border-red-500/30 rounded-lg text-red-400">{error}</div>' if error else ''}

                <form method="POST" class="bg-slate-800 rounded-xl border border-slate-700 p-6 space-y-6">
                    <div>
                        <label class="block text-sm font-medium text-slate-300 mb-2">Crew Name *</label>
                        <input type="text" name="name" value="{crew.get('name', '')}" required
                            class="w-full px-4 py-3 bg-slate-900 border border-slate-700 rounded-lg text-white focus:ring-2 focus:ring-emerald-500 focus:border-transparent"
                            placeholder="e.g., Mike's Maintenance Team">
                    </div>

                    <div>
                        <label class="block text-sm font-medium text-slate-300 mb-2">Role</label>
                        <input type="text" name="role" value="{crew.get('role', '') or ''}"
                            class="w-full px-4 py-3 bg-slate-900 border border-slate-700 rounded-lg text-white focus:ring-2 focus:ring-emerald-500 focus:border-transparent"
                            placeholder="e.g., Lead Landscaper, Technician">
                    </div>

                    <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
                        <div>
                            <label class="block text-sm font-medium text-slate-300 mb-2">Email</label>
                            <input type="email" name="email" value="{crew.get('email', '') or ''}"
                                class="w-full px-4 py-3 bg-slate-900 border border-slate-700 rounded-lg text-white focus:ring-2 focus:ring-emerald-500 focus:border-transparent"
                                placeholder="crew@example.com">
                        </div>
                        <div>
                            <label class="block text-sm font-medium text-slate-300 mb-2">Phone</label>
                            <input type="tel" name="phone" value="{crew.get('phone', '') or ''}"
                                class="w-full px-4 py-3 bg-slate-900 border border-slate-700 rounded-lg text-white focus:ring-2 focus:ring-emerald-500 focus:border-transparent"
                                placeholder="(555) 123-4567">
                        </div>
                    </div>

                    <div>
                        <label class="block text-sm font-medium text-slate-300 mb-2">Calendar Color</label>
                        <select name="color"
                            class="w-full px-4 py-3 bg-slate-900 border border-slate-700 rounded-lg text-white focus:ring-2 focus:ring-emerald-500 focus:border-transparent">
                            {color_options_html}
                        </select>
                        <p class="text-xs text-slate-500 mt-1">This color will be used for the crew's assignments on the calendar</p>
                    </div>

                    <div class="flex justify-between items-center pt-4 border-t border-slate-700">
                        <button type="submit" name="action" value="delete"
                            class="text-red-400 hover:text-red-300 px-4 py-2 rounded-lg transition flex items-center gap-2"
                            onclick="return confirm('Are you sure you want to remove this crew member?')">
                            <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16"/>
                            </svg>
                            Remove Crew
                        </button>
                        <div class="flex gap-3">
                            <a href="/crews" class="bg-slate-700 hover:bg-slate-600 text-white px-6 py-3 rounded-lg font-medium transition">Cancel</a>
                            <button type="submit" name="action" value="update" class="bg-emerald-500 hover:bg-emerald-600 text-white px-6 py-3 rounded-lg font-medium transition">Save Changes</button>
                        </div>
                    </div>
                </form>
            </div>
        </main>
    </div>
</body>
</html>""")
