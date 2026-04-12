"""
referrals_db.py — Referral Program Database Module
Piney Digital Outreach System — Referral Management

Manages referral codes, tracking, and rewards.
"""

import sqlite3
import logging
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

DB_PATH = Path(__file__).parent.parent / "data" / "leads.db"


def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_referral_tables():
    """Create referral program tables."""
    conn = get_connection()
    c = conn.cursor()

    c.executescript("""
        -- Referral settings per business
        CREATE TABLE IF NOT EXISTS referral_settings (
            id              TEXT PRIMARY KEY,
            business_id     TEXT REFERENCES loyalty_businesses(id),
            enabled         INTEGER DEFAULT 1,
            referrer_reward_type TEXT DEFAULT 'punches',  -- punches/discount/credits
            referrer_reward_value INTEGER DEFAULT 2,      -- e.g., 2 punches or $2 or 20%
            referee_reward_type TEXT DEFAULT 'punches',   -- punches/discount/credits
            referee_reward_value INTEGER DEFAULT 1,       -- Welcome bonus
            max_referrals   INTEGER DEFAULT NULL,         -- NULL = unlimited
            created_at      TEXT DEFAULT (datetime('now')),
            updated_at      TEXT DEFAULT (datetime('now'))
        );

        -- Referral codes (one per customer-business pair)
        CREATE TABLE IF NOT EXISTS referral_codes (
            id              TEXT PRIMARY KEY,
            customer_id     TEXT REFERENCES loyalty_customers(id),
            business_id     TEXT REFERENCES loyalty_businesses(id),
            code            TEXT UNIQUE NOT NULL,         -- Short unique code (e.g., MARIA-X7K9)
            clicks          INTEGER DEFAULT 0,
            conversions     INTEGER DEFAULT 0,
            created_at      TEXT DEFAULT (datetime('now')),
            last_used_at    TEXT
        );

        -- Referral tracking (when someone uses a code)
        CREATE TABLE IF NOT EXISTS referrals (
            id              TEXT PRIMARY KEY,
            code_id         TEXT REFERENCES referral_codes(id),
            referrer_id     TEXT REFERENCES loyalty_customers(id),
            referee_id      TEXT REFERENCES loyalty_customers(id),
            business_id     TEXT REFERENCES loyalty_businesses(id),
            status          TEXT DEFAULT 'pending',       -- pending/completed/rewarded
            referrer_reward_given INTEGER DEFAULT 0,
            referee_reward_given INTEGER DEFAULT 0,
            created_at      TEXT DEFAULT (datetime('now')),
            completed_at    TEXT,
            rewarded_at     TEXT
        );

        -- Referral clicks (for analytics)
        CREATE TABLE IF NOT EXISTS referral_clicks (
            id              TEXT PRIMARY KEY,
            code_id         TEXT REFERENCES referral_codes(id),
            clicked_at      TEXT DEFAULT (datetime('now')),
            ip_address      TEXT,
            user_agent      TEXT,
            converted       INTEGER DEFAULT 0             -- Did this click lead to signup?
        );

        -- Indexes for performance
        CREATE INDEX IF NOT EXISTS idx_referrals_code ON referral_codes(code);
        CREATE INDEX IF NOT EXISTS idx_referrals_referrer ON referrals(referrer_id);
        CREATE INDEX IF NOT EXISTS idx_referrals_referee ON referrals(referee_id);
        CREATE INDEX IF NOT EXISTS idx_referrals_business ON referrals(business_id);
    """)

    conn.commit()
    conn.close()
    logger.info("Referral tables initialised")


def generate_id(prefix: str) -> str:
    """Generate a unique ID with prefix."""
    import uuid
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def generate_referral_code(customer_name: str = None) -> str:
    """Generate a short, memorable referral code."""
    import random
    import string
    
    # Use first 4 letters of name (or random) + 4 char code
    if customer_name:
        prefix = customer_name.replace(" ", "").upper()[:4]
    else:
        prefix = ''.join(random.choices(string.ascii_uppercase, k=4))
    
    # Generate 4-char random code
    code = ''.join(random.choices(string.ascii_uppercase + string.digits, k=4))
    
    return f"{prefix}-{code}"


# ── Settings Operations ─────────────────────────────────────

def get_referral_settings(business_id: str) -> dict:
    """Get referral settings for a business."""
    conn = get_connection()
    c = conn.cursor()
    c.execute("""
        SELECT * FROM referral_settings WHERE business_id = ?
    """, (business_id,))
    row = c.fetchone()
    conn.close()
    
    if row:
        return dict(row)
    
    # Return defaults
    return {
        "id": None,
        "business_id": business_id,
        "enabled": 1,
        "referrer_reward_type": "punches",
        "referrer_reward_value": 2,
        "referee_reward_type": "punches",
        "referee_reward_value": 1,
        "max_referrals": None
    }


def save_referral_settings(business_id: str, settings: dict) -> str:
    """Save referral settings for a business."""
    conn = get_connection()
    c = conn.cursor()
    
    # Check if exists
    c.execute("SELECT id FROM referral_settings WHERE business_id = ?", (business_id,))
    row = c.fetchone()
    
    if row:
        # Update
        c.execute("""
            UPDATE referral_settings 
            SET enabled = ?, referrer_reward_type = ?, referrer_reward_value = ?,
                referee_reward_type = ?, referee_reward_value = ?, max_referrals = ?,
                updated_at = ?
            WHERE business_id = ?
        """, (
            settings.get("enabled", 1),
            settings.get("referrer_reward_type", "punches"),
            settings.get("referrer_reward_value", 2),
            settings.get("referee_reward_type", "punches"),
            settings.get("referee_reward_value", 1),
            settings.get("max_referrals"),
            datetime.now().isoformat(),
            business_id
        ))
        settings_id = row["id"]
    else:
        # Create
        settings_id = generate_id("rset")
        c.execute("""
            INSERT INTO referral_settings 
            (id, business_id, enabled, referrer_reward_type, referrer_reward_value,
             referee_reward_type, referee_reward_value, max_referrals)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            settings_id, business_id,
            settings.get("enabled", 1),
            settings.get("referrer_reward_type", "punches"),
            settings.get("referrer_reward_value", 2),
            settings.get("referee_reward_type", "punches"),
            settings.get("referee_reward_value", 1),
            settings.get("max_referrals")
        ))
    
    conn.commit()
    conn.close()
    return settings_id


# ── Referral Code Operations ─────────────────────────────────────

def get_or_create_referral_code(customer_id: str, business_id: str, 
                                 customer_name: str = None) -> str:
    """Get existing referral code or create new one."""
    conn = get_connection()
    c = conn.cursor()
    
    # Check for existing code
    c.execute("""
        SELECT code FROM referral_codes 
        WHERE customer_id = ? AND business_id = ?
    """, (customer_id, business_id))
    row = c.fetchone()
    
    if row:
        conn.close()
        return row["code"]
    
    # Create new code
    code = generate_referral_code(customer_name)
    code_id = generate_id("rcode")
    
    c.execute("""
        INSERT INTO referral_codes (id, customer_id, business_id, code)
        VALUES (?, ?, ?, ?)
    """, (code_id, customer_id, business_id, code))
    
    conn.commit()
    conn.close()
    return code


def track_referral_click(code: str, ip_address: str = None, user_agent: str = None):
    """Track when someone clicks a referral link."""
    conn = get_connection()
    c = conn.cursor()
    
    # Get code ID
    c.execute("SELECT id FROM referral_codes WHERE code = ?", (code,))
    row = c.fetchone()
    
    if not row:
        conn.close()
        return
    
    code_id = row["id"]
    
    # Increment clicks
    c.execute("""
        UPDATE referral_codes SET clicks = clicks + 1 WHERE id = ?
    """, (code_id,))
    
    # Log click
    click_id = generate_id("rclk")
    c.execute("""
        INSERT INTO referral_clicks (id, code_id, ip_address, user_agent)
        VALUES (?, ?, ?, ?)
    """, (click_id, code_id, ip_address, user_agent))
    
    conn.commit()
    conn.close()


# ── Referral Operations ─────────────────────────────────────

def create_referral(code: str, referrer_id: str, referee_id: str,
                    business_id: str) -> str:
    """Create a referral record when new customer signs up."""
    conn = get_connection()
    c = conn.cursor()
    
    # Get code ID
    c.execute("""
        SELECT id FROM referral_codes WHERE code = ?
    """, (code,))
    row = c.fetchone()
    
    if not row:
        conn.close()
        return None
    
    code_id = row["id"]
    referral_id = generate_id("ref")
    
    c.execute("""
        INSERT INTO referrals 
        (id, code_id, referrer_id, referee_id, business_id)
        VALUES (?, ?, ?, ?, ?)
    """, (referral_id, code_id, referrer_id, referee_id, business_id))
    
    # Update code conversions
    c.execute("""
        UPDATE referral_codes 
        SET conversions = conversions + 1, last_used_at = ?
        WHERE id = ?
    """, (datetime.now().isoformat(), code_id))
    
    # Mark click as converted
    c.execute("""
        UPDATE referral_clicks SET converted = 1 
        WHERE code_id = ? AND converted = 0
        ORDER BY clicked_at DESC LIMIT 1
    """, (code_id,))
    
    conn.commit()
    conn.close()
    return referral_id


def complete_referral(referral_id: str):
    """Mark referral as completed (both customers active)."""
    conn = get_connection()
    c = conn.cursor()
    c.execute("""
        UPDATE referrals 
        SET status = 'completed', completed_at = ?
        WHERE id = ?
    """, (datetime.now().isoformat(), referral_id))
    conn.commit()
    conn.close()


def reward_referral(referral_id: str, referrer_reward: int = 0,
                    referee_reward: int = 0):
    """Mark referral as rewarded and store reward amounts."""
    conn = get_connection()
    c = conn.cursor()
    c.execute("""
        UPDATE referrals 
        SET status = 'rewarded', rewarded_at = ?,
            referrer_reward_given = ?, referee_reward_given = ?
        WHERE id = ?
    """, (datetime.now().isoformat(), referrer_reward, referee_reward, referral_id))
    conn.commit()
    conn.close()


# ── Analytics & Dashboard ─────────────────────────────────────

def get_business_referral_stats(business_id: str) -> dict:
    """Get referral statistics for a business."""
    conn = get_connection()
    c = conn.cursor()
    
    # Total referrals
    c.execute("""
        SELECT COUNT(*) as n FROM referrals WHERE business_id = ?
    """, (business_id,))
    total_referrals = c.fetchone()["n"]
    
    # Total clicks
    c.execute("""
        SELECT SUM(clicks) as n FROM referral_codes 
        WHERE business_id = ?
    """, (business_id,))
    total_clicks = c.fetchone()["n"] or 0
    
    # Conversion rate
    conversion_rate = 0
    if total_clicks > 0:
        c.execute("""
            SELECT SUM(conversions) as n FROM referral_codes 
            WHERE business_id = ?
        """, (business_id,))
        total_conversions = c.fetchone()["n"] or 0
        conversion_rate = round(total_conversions / total_clicks * 100, 1)
    
    # Top referrers
    c.execute("""
        SELECT c.name as customer_name, c.phone, 
               COUNT(r.id) as referrals, 
               rc.code as referral_code
        FROM referrals r
        JOIN loyalty_customers c ON r.referrer_id = c.id
        JOIN referral_codes rc ON r.code_id = rc.id
        WHERE r.business_id = ?
        GROUP BY r.referrer_id
        ORDER BY referrals DESC
        LIMIT 10
    """, (business_id,))
    top_referrers = [dict(row) for row in c.fetchall()]
    
    # Recent referrals
    c.execute("""
        SELECT r.*, 
               ref.name as referrer_name,
               ref2.name as referee_name
        FROM referrals r
        JOIN loyalty_customers ref ON r.referrer_id = ref.id
        JOIN loyalty_customers ref2 ON r.referee_id = ref2.id
        WHERE r.business_id = ?
        ORDER BY r.created_at DESC
        LIMIT 10
    """, (business_id,))
    recent = [dict(row) for row in c.fetchall()]
    
    conn.close()
    
    return {
        "total_referrals": total_referrals,
        "total_clicks": total_clicks,
        "conversion_rate": conversion_rate,
        "top_referrers": top_referrers,
        "recent_referrals": recent
    }


def get_customer_referral_stats(customer_id: str, business_id: str) -> dict:
    """Get referral stats for a specific customer."""
    conn = get_connection()
    c = conn.cursor()
    
    # Get customer's code
    c.execute("""
        SELECT code, clicks, conversions FROM referral_codes
        WHERE customer_id = ? AND business_id = ?
    """, (customer_id, business_id))
    code_info = c.fetchone()
    
    if not code_info:
        conn.close()
        return {"code": None, "clicks": 0, "conversions": 0, "rewards_earned": 0}
    
    # Count rewards earned
    c.execute("""
        SELECT COUNT(*) as n, SUM(referrer_reward_given) as total
        FROM referrals 
        WHERE referrer_id = ? AND business_id = ? AND status = 'rewarded'
    """, (customer_id, business_id))
    rewards = c.fetchone()
    
    conn.close()
    
    return {
        "code": code_info["code"],
        "clicks": code_info["clicks"],
        "conversions": code_info["conversions"],
        "rewards_earned": rewards["total"] or 0
    }


def get_customer_referral_codes(customer_id: str) -> list:
    """Get all referral codes for a customer across all businesses."""
    conn = get_connection()
    c = conn.cursor()

    c.execute("""
        SELECT rc.code, rc.business_id, lb.name as business_name
        FROM referral_codes rc
        JOIN loyalty_businesses lb ON rc.business_id = lb.id
        WHERE rc.customer_id = ? AND lb.active = 1
        ORDER BY lb.name
    """, (customer_id,))

    codes = [dict(r) for r in c.fetchall()]
    conn.close()
    return codes
