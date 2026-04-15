"""
database.py — Lead database setup and helpers
Piney Digital Outreach System — Module 1
"""

import sqlite3
import logging
from datetime import datetime
from config.settings import DB_PATH

logger = logging.getLogger(__name__)


def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """Create all tables if they don't exist."""
    conn = get_connection()
    c = conn.cursor()

    c.executescript("""
        CREATE TABLE IF NOT EXISTS leads (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,

            -- Business info
            business_name   TEXT NOT NULL,
            category        TEXT,
            city            TEXT,
            address         TEXT,
            phone           TEXT,
            website         TEXT,
            google_maps_url TEXT,
            rating          REAL,
            review_count    INTEGER,

            -- Website qualification
            has_website     INTEGER DEFAULT NULL,   -- 0=none, 1=yes
            site_status     TEXT DEFAULT NULL,      -- 'none','parked','outdated','modern'
            site_last_updated TEXT DEFAULT NULL,

            -- Contact enrichment (Module 3)
            owner_name      TEXT,
            owner_email     TEXT,
            email_source    TEXT,                   -- 'scraped','hunter','manual'

            -- Outreach status (Module 4+)
            lead_score      INTEGER DEFAULT 0,
            outreach_status TEXT DEFAULT 'new',     -- new/queued/sent/replied/booked/dead
            email_sent_at   TEXT,
            sms_sent_at     TEXT,
            last_reply_at   TEXT,
            reply_intent    TEXT,                   -- interested/not_interested/question

            -- Call outreach (Vapi)
            call_status     TEXT DEFAULT NULL,      -- 'new','queued','called','voicemail','interested','transferred','declined','no_answer'
            call_sid        TEXT,                   -- Vapi call ID
            call_transcript TEXT,                   -- Full transcript from call
            call_summary    TEXT,                   -- AI-generated summary
            call_duration   INTEGER,                -- Duration in seconds
            call_attempts   INTEGER DEFAULT 0,      -- Number of call attempts
            last_call_at    TEXT,                   -- Timestamp of last call

            -- Meta
            scraped_at      TEXT DEFAULT (datetime('now')),
            updated_at      TEXT DEFAULT (datetime('now')),
            notes           TEXT
        );

        CREATE TABLE IF NOT EXISTS outreach_log (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            lead_id       INTEGER REFERENCES leads(id),
            channel       TEXT,       -- 'email' | 'sms' | 'call'
            direction     TEXT,       -- 'outbound' | 'inbound'
            subject       TEXT,
            body          TEXT,
            transcript    TEXT,       -- Call transcript (for voice calls)
            duration      INTEGER,    -- Call duration in seconds
            status        TEXT,       -- 'sent' | 'failed' | 'received' | 'voicemail' | 'transferred' | 'no_answer'
            external_id   TEXT,       -- Vapi call SID or Twilio message SID
            sent_at       TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS scrape_runs (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            city        TEXT,
            category    TEXT,
            results     INTEGER,
            new_leads   INTEGER,
            started_at  TEXT,
            finished_at TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_leads_status   ON leads(outreach_status);
        CREATE INDEX IF NOT EXISTS idx_leads_city     ON leads(city);
        CREATE INDEX IF NOT EXISTS idx_leads_category ON leads(category);
        CREATE INDEX IF NOT EXISTS idx_leads_call     ON leads(call_status);

        -- Business Users (Customer Portal)
        CREATE TABLE IF NOT EXISTS business_users (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            email           TEXT NOT NULL UNIQUE,
            password_hash   TEXT NOT NULL,
            business_id     TEXT,                       -- Links to loyalty_businesses.id
            owner_name      TEXT,
            email_verified  INTEGER DEFAULT 0,
            verification_token TEXT,
            two_fa_enabled  INTEGER DEFAULT 0,
            two_fa_secret   TEXT,
            two_fa_backup_codes TEXT,                   -- JSON array of backup codes
            plan            TEXT DEFAULT 'starter',     -- starter/growth/pro
            stripe_customer_id TEXT,
            subscription_id TEXT,
            subscription_status TEXT,                   -- active/past_due/canceled
            created_at      TEXT DEFAULT (datetime('now')),
            updated_at      TEXT DEFAULT (datetime('now')),
            last_login_at   TEXT
        );

        -- Admin Sessions (for 2FA tracking)
        CREATE TABLE IF NOT EXISTS admin_sessions (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            session_token   TEXT NOT NULL UNIQUE,
            created_at      TEXT DEFAULT (datetime('now')),
            expires_at      TEXT NOT NULL,
            ip_address      TEXT,
            user_agent      TEXT,
            two_fa_verified INTEGER DEFAULT 0,
            remember_device TEXT                       -- Token for "remember this device"
        );

        -- Password Reset Tokens
        CREATE TABLE IF NOT EXISTS password_reset_tokens (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            email           TEXT NOT NULL,
            token           TEXT NOT NULL UNIQUE,
            created_at      TEXT DEFAULT (datetime('now')),
            expires_at      TEXT NOT NULL,
            used            INTEGER DEFAULT 0
        );

        -- Audit Log (security events)
        CREATE TABLE IF NOT EXISTS audit_log (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            event_type      TEXT NOT NULL,              -- login/login_failed/logout/2fa/2fa_failed/password_change
            user_type       TEXT,                       -- admin/business
            user_id         INTEGER,
            email           TEXT,
            ip_address      TEXT,
            user_agent      TEXT,
            details         TEXT,                       -- JSON
            created_at      TEXT DEFAULT (datetime('now'))
        );

        -- Indexes for auth tables
        CREATE INDEX IF NOT EXISTS idx_business_users_email    ON business_users(email);
        CREATE INDEX IF NOT EXISTS idx_business_users_business ON business_users(business_id);
        CREATE INDEX IF NOT EXISTS idx_admin_sessions_token    ON admin_sessions(session_token);
        CREATE INDEX IF NOT EXISTS idx_audit_log_created       ON audit_log(created_at);

        -- Settings table (key-value store)
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT,
            updated_at TEXT DEFAULT (datetime('now'))
        );
    """)

    conn.commit()
    conn.close()
    logger.info("Database initialised at %s", DB_PATH)

    # Run migrations for existing databases
    _migrate_db()


def _migrate_db():
    """Add new columns to existing databases if they don't exist."""
    conn = get_connection()
    c = conn.cursor()

    # Check if call_status column exists
    c.execute("PRAGMA table_info(leads)")
    columns = [col[1] for col in c.fetchall()]

    migrations = []

    # Add call outreach columns if missing
    if "call_status" not in columns:
        migrations.append("ALTER TABLE leads ADD COLUMN call_status TEXT DEFAULT NULL")
    if "call_sid" not in columns:
        migrations.append("ALTER TABLE leads ADD COLUMN call_sid TEXT")
    if "call_transcript" not in columns:
        migrations.append("ALTER TABLE leads ADD COLUMN call_transcript TEXT")
    if "call_summary" not in columns:
        migrations.append("ALTER TABLE leads ADD COLUMN call_summary TEXT")
    if "call_duration" not in columns:
        migrations.append("ALTER TABLE leads ADD COLUMN call_duration INTEGER")
    if "call_attempts" not in columns:
        migrations.append("ALTER TABLE leads ADD COLUMN call_attempts INTEGER DEFAULT 0")
    if "last_call_at" not in columns:
        migrations.append("ALTER TABLE leads ADD COLUMN last_call_at TEXT")

    # Check outreach_log columns
    c.execute("PRAGMA table_info(outreach_log)")
    log_columns = [col[1] for col in c.fetchall()]

    if "transcript" not in log_columns:
        migrations.append("ALTER TABLE outreach_log ADD COLUMN transcript TEXT")
    if "duration" not in log_columns:
        migrations.append("ALTER TABLE outreach_log ADD COLUMN duration INTEGER")
    if "external_id" not in log_columns:
        migrations.append("ALTER TABLE outreach_log ADD COLUMN external_id TEXT")

    # Run migrations
    for migration in migrations:
        try:
            c.execute(migration)
            logger.info("Migration applied: %s", migration.split("ADD COLUMN")[1].split()[0])
        except sqlite3.OperationalError as e:
            if "duplicate column" not in str(e).lower():
                logger.warning("Migration failed: %s", e)

    if migrations:
        conn.commit()
        logger.info("Database migrations complete")

    conn.close()


def upsert_lead(data: dict) -> tuple[int, bool]:
    """
    Insert lead if not already in DB (matched on business_name + city).
    Returns (lead_id, is_new).
    """
    conn = get_connection()
    c = conn.cursor()

    c.execute("""
        SELECT id FROM leads
        WHERE LOWER(business_name) = LOWER(?)
          AND LOWER(city)          = LOWER(?)
    """, (data["business_name"], data["city"]))

    row = c.fetchone()

    if row:
        conn.close()
        return row["id"], False

    c.execute("""
        INSERT INTO leads
            (business_name, category, city, address, phone,
             website, google_maps_url, rating, review_count, scraped_at)
        VALUES
            (:business_name, :category, :city, :address, :phone,
             :website, :google_maps_url, :rating, :review_count,
             datetime('now'))
    """, data)

    lead_id = c.lastrowid
    conn.commit()
    conn.close()
    return lead_id, True


def get_leads(status: str = None, city: str = None, limit: int = 200) -> list:
    conn = get_connection()
    c = conn.cursor()

    query  = "SELECT * FROM leads WHERE 1=1"
    params = []

    if status:
        query += " AND outreach_status = ?"
        params.append(status)
    if city:
        query += " AND city = ?"
        params.append(city)

    query += " ORDER BY lead_score DESC, scraped_at DESC LIMIT ?"
    params.append(limit)

    c.execute(query, params)
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    return rows


def update_lead(lead_id: int, fields: dict):
    """Update any set of fields on a lead row."""
    fields["updated_at"] = datetime.now().isoformat()
    fields["id"]         = lead_id

    set_clause = ", ".join(f"{k} = :{k}" for k in fields if k != "id")
    conn = get_connection()
    conn.execute(f"UPDATE leads SET {set_clause} WHERE id = :id", fields)
    conn.commit()
    conn.close()


def db_stats() -> dict:
    """Quick summary of what's in the DB — used by dashboard."""
    conn = get_connection()
    c = conn.cursor()
    stats = {}

    for row in c.execute("""
        SELECT outreach_status, COUNT(*) as n FROM leads GROUP BY outreach_status
    """):
        stats[row["outreach_status"]] = row["n"]

    c.execute("SELECT COUNT(*) as n FROM leads")
    stats["total"] = c.fetchone()["n"]

    conn.close()
    return stats


# ── Business User Helpers ─────────────────────────────────────

def create_business_user(email: str, password_hash: str, owner_name: str = "",
                         plan: str = "starter") -> int:
    """Create a new business user. Returns user_id."""
    conn = get_connection()
    c = conn.cursor()

    c.execute("""
        INSERT INTO business_users (email, password_hash, owner_name, plan)
        VALUES (?, ?, ?, ?)
    """, (email.lower(), password_hash, owner_name, plan))

    user_id = c.lastrowid
    conn.commit()
    conn.close()
    return user_id


def get_business_user_by_email(email: str) -> dict | None:
    """Get business user by email."""
    conn = get_connection()
    c = conn.cursor()

    c.execute("SELECT * FROM business_users WHERE email = ?", (email.lower(),))
    row = c.fetchone()
    conn.close()

    return dict(row) if row else None


def get_business_user_by_id(user_id: int) -> dict | None:
    """Get business user by ID."""
    conn = get_connection()
    c = conn.cursor()

    c.execute("SELECT * FROM business_users WHERE id = ?", (user_id,))
    row = c.fetchone()
    conn.close()

    return dict(row) if row else None


def update_business_user(user_id: int, fields: dict):
    """Update business user fields."""
    fields["updated_at"] = datetime.now().isoformat()
    fields["id"] = user_id

    set_clause = ", ".join(f"{k} = ?" for k in fields if k != "id")
    values = [fields[k] for k in fields if k != "id"] + [user_id]

    conn = get_connection()
    conn.execute(f"UPDATE business_users SET {set_clause} WHERE id = ?", values)
    conn.commit()
    conn.close()


def set_user_email_verified(user_id: int, token: str):
    """Mark user email as verified."""
    conn = get_connection()
    conn.execute("""
        UPDATE business_users
        SET email_verified = 1, verification_token = NULL, updated_at = ?
        WHERE id = ? AND verification_token = ?
    """, (datetime.now().isoformat(), user_id, token))
    conn.commit()
    conn.close()


def set_verification_token(user_id: int, token: str):
    """Set email verification token for user."""
    conn = get_connection()
    conn.execute("""
        UPDATE business_users
        SET verification_token = ?, updated_at = ?
        WHERE id = ?
    """, (token, datetime.now().isoformat(), user_id))
    conn.commit()
    conn.close()


# ── Admin Session Helpers ─────────────────────────────────────

def create_admin_session(session_token: str, expires_at: str,
                          ip_address: str = None, user_agent: str = None) -> int:
    """Create an admin session."""
    conn = get_connection()
    c = conn.cursor()

    c.execute("""
        INSERT INTO admin_sessions (session_token, expires_at, ip_address, user_agent)
        VALUES (?, ?, ?, ?)
    """, (session_token, expires_at, ip_address, user_agent))

    session_id = c.lastrowid
    conn.commit()
    conn.close()
    return session_id


def get_admin_session(session_token: str) -> dict | None:
    """Get admin session by token."""
    conn = get_connection()
    c = conn.cursor()

    c.execute("SELECT * FROM admin_sessions WHERE session_token = ?", (session_token,))
    row = c.fetchone()
    conn.close()

    return dict(row) if row else None


def verify_admin_session_2fa(session_token: str):
    """Mark admin session as 2FA verified."""
    conn = get_connection()
    conn.execute("""
        UPDATE admin_sessions SET two_fa_verified = 1 WHERE session_token = ?
    """, (session_token,))
    conn.commit()
    conn.close()


def delete_admin_session(session_token: str):
    """Delete an admin session."""
    conn = get_connection()
    conn.execute("DELETE FROM admin_sessions WHERE session_token = ?", (session_token,))
    conn.commit()
    conn.close()


def cleanup_expired_sessions():
    """Remove expired admin sessions."""
    conn = get_connection()
    conn.execute("DELETE FROM admin_sessions WHERE expires_at < datetime('now')")
    conn.commit()
    conn.close()


# ── Password Reset Helpers ───────────────────────────────────

def create_password_reset_token(email: str, token: str, expires_hours: int = 24) -> int:
    """Create a password reset token."""
    conn = get_connection()
    c = conn.cursor()

    # Invalidate any existing tokens for this email
    conn.execute("DELETE FROM password_reset_tokens WHERE email = ?", (email.lower(),))

    c.execute("""
        INSERT INTO password_reset_tokens (email, token, expires_at)
        VALUES (?, ?, datetime('now', '+' || ? || ' hours'))
    """, (email.lower(), token, expires_hours))

    token_id = c.lastrowid
    conn.commit()
    conn.close()
    return token_id


def get_password_reset_token(token: str) -> dict | None:
    """Get password reset token if valid."""
    conn = get_connection()
    c = conn.cursor()

    c.execute("""
        SELECT * FROM password_reset_tokens
        WHERE token = ? AND used = 0 AND expires_at > datetime('now')
    """, (token,))
    row = c.fetchone()
    conn.close()

    return dict(row) if row else None


def mark_token_used(token: str):
    """Mark password reset token as used."""
    conn = get_connection()
    conn.execute("UPDATE password_reset_tokens SET used = 1 WHERE token = ?", (token,))
    conn.commit()
    conn.close()


# ── Audit Log Helpers ────────────────────────────────────────

def log_audit_event(event_type: str, user_type: str = None, user_id: int = None,
                     email: str = None, ip_address: str = None,
                     user_agent: str = None, details: str = None):
    """Log a security audit event."""
    conn = get_connection()
    conn.execute("""
        INSERT INTO audit_log (event_type, user_type, user_id, email, ip_address, user_agent, details)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (event_type, user_type, user_id, email, ip_address, user_agent, details))
    conn.commit()
    conn.close()


def get_recent_audit_events(limit: int = 100) -> list:
    """Get recent audit events."""
    conn = get_connection()
    c = conn.cursor()

    c.execute("""
        SELECT * FROM audit_log
        ORDER BY created_at DESC
        LIMIT ?
    """, (limit,))

    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    return rows


# ── Seed Data ─────────────────────────────────────────────────

def seed_leads_from_csv():
    """Import leads from CSV seed file if database is empty."""
    import csv
    import os
    from pathlib import Path

    try:
        # Check if leads already exist
        conn = get_connection()
        c = conn.cursor()
        c.execute("SELECT COUNT(*) FROM leads")
        count = c.fetchone()[0]

        if count > 0:
            logger.info(f"Database has {count} leads, skipping seed")
            conn.close()
            return

        # Find CSV file - try multiple paths
        possible_paths = [
            Path(__file__).parent.parent / "data" / "leads_seed.csv",  # modules/../data/
            Path("/app/data/leads_seed.csv"),  # Railway absolute path
            Path("data/leads_seed.csv"),  # Relative path
            Path.cwd() / "data" / "leads_seed.csv",  # Current working directory
        ]

        csv_path = None
        for path in possible_paths:
            if path.exists():
                csv_path = path
                logger.info(f"Found seed CSV at: {csv_path}")
                break

        if not csv_path:
            logger.warning(f"No seed CSV found. Tried: {possible_paths}")
            conn.close()
            return

        # Import leads
        with open(csv_path, 'r', newline='', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            leads = list(reader)

        logger.info(f"Seeding {len(leads)} leads from CSV...")

        for lead in leads:
            # Convert empty strings to None
            for key in lead:
                if lead[key] == '':
                    lead[key] = None

            c.execute("""
                INSERT INTO leads (
                    id, business_name, category, city, address, phone, website,
                    google_maps_url, rating, review_count, has_website, site_status,
                    site_last_updated, owner_name, owner_email, email_source, lead_score,
                    outreach_status, email_sent_at, sms_sent_at, last_reply_at,
                    reply_intent, scraped_at, updated_at, notes, call_status, call_sid,
                    call_transcript, call_summary, call_duration, call_attempts, last_call_at
                ) VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                )
            """, (
                lead.get('id'), lead.get('business_name'), lead.get('category'),
                lead.get('city'), lead.get('address'), lead.get('phone'),
                lead.get('website'), lead.get('google_maps_url'), lead.get('rating'),
                lead.get('review_count'), lead.get('has_website'), lead.get('site_status'),
                lead.get('site_last_updated'), lead.get('owner_name'), lead.get('owner_email'),
                lead.get('email_source'), lead.get('lead_score'), lead.get('outreach_status'),
                lead.get('email_sent_at'), lead.get('sms_sent_at'), lead.get('last_reply_at'),
                lead.get('reply_intent'), lead.get('scraped_at'), lead.get('updated_at'),
                lead.get('notes'), lead.get('call_status'), lead.get('call_sid'),
                lead.get('call_transcript'), lead.get('call_summary'), lead.get('call_duration'),
                lead.get('call_attempts'), lead.get('last_call_at')
            ))

        conn.commit()
        conn.close()
        logger.info(f"Successfully seeded {len(leads)} leads")

    except Exception as e:
        logger.error(f"Failed to seed leads: {e}")
        try:
            conn.close()
        except:
            pass


# ── Settings Helpers ─────────────────────────────────────

def get_setting(key: str, default: str = None) -> str:
    """Get a setting value from the database."""
    conn = get_connection()
    c = conn.cursor()
    c.execute("SELECT value FROM settings WHERE key = ?", (key,))
    row = c.fetchone()
    conn.close()
    return row["value"] if row else default


def set_setting(key: str, value: str):
    """Set a setting value in the database."""
    conn = get_connection()
    conn.execute("""
        INSERT INTO settings (key, value, updated_at)
        VALUES (?, ?, datetime('now'))
        ON CONFLICT(key) DO UPDATE SET value = ?, updated_at = datetime('now')
    """, (key, value, value))
    conn.commit()
    conn.close()
