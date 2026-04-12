"""
reviews_db.py — Review Request Database Module
Piney Digital Outreach System — Review Management

Manages review settings, requests, and ratings for businesses.
"""

import sqlite3
import logging
from datetime import datetime, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)

DB_PATH = Path(__file__).parent.parent / "data" / "leads.db"


def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_review_tables():
    """Create review management tables."""
    conn = get_connection()
    c = conn.cursor()

    c.executescript("""
        -- Business review settings
        CREATE TABLE IF NOT EXISTS review_settings (
            id              TEXT PRIMARY KEY,
            business_id     TEXT REFERENCES loyalty_businesses(id),
            enabled         INTEGER DEFAULT 1,
            delay_hours     INTEGER DEFAULT 2,          -- Hours after visit to send
            google_url      TEXT,                       -- Google review link
            yelp_url        TEXT,                       -- Yelp review link
            custom_message  TEXT,                       -- Custom SMS message
            min_stars_public INTEGER DEFAULT 4,         -- Min stars for public review
            created_at      TEXT DEFAULT (datetime('now')),
            updated_at      TEXT DEFAULT (datetime('now'))
        );

        -- Review requests sent to customers
        CREATE TABLE IF NOT EXISTS review_requests (
            id              TEXT PRIMARY KEY,
            business_id     TEXT REFERENCES loyalty_businesses(id),
            customer_id     TEXT REFERENCES loyalty_customers(id),
            card_id         TEXT REFERENCES loyalty_cards(id),
            sent_at         TEXT DEFAULT (datetime('now')),
            opened_at       TEXT,
            rated_at        TEXT,
            status          TEXT DEFAULT 'sent',        -- sent/opened/rated/ignored
            channel         TEXT DEFAULT 'sms',         -- sms/email
            message_sid     TEXT                        -- Twilio message SID
        );

        -- Customer ratings/submissions
        CREATE TABLE IF NOT EXISTS review_ratings (
            id              TEXT PRIMARY KEY,
            request_id      TEXT REFERENCES review_requests(id),
            business_id     TEXT REFERENCES loyalty_businesses(id),
            customer_id     TEXT REFERENCES loyalty_customers(id),
            stars           INTEGER NOT NULL,           -- 1-5
            feedback        TEXT,                       -- Written feedback
            is_public       INTEGER DEFAULT 0,          -- Sent to Google/Yelp
            submitted_at    TEXT DEFAULT (datetime('now')),
            source          TEXT DEFAULT 'link'         -- link/qr/direct
        );

        -- Indexes for performance
        CREATE INDEX IF NOT EXISTS idx_requests_business ON review_requests(business_id);
        CREATE INDEX IF NOT EXISTS idx_requests_customer ON review_requests(customer_id);
        CREATE INDEX IF NOT EXISTS idx_requests_status ON review_requests(status);
        CREATE INDEX IF NOT EXISTS idx_ratings_business ON review_ratings(business_id);
        CREATE INDEX IF NOT EXISTS idx_ratings_stars ON review_ratings(stars);
    """)

    conn.commit()
    conn.close()
    logger.info("Review tables initialised")


def generate_id(prefix: str) -> str:
    """Generate a unique ID with prefix."""
    import uuid
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


# ── Settings Operations ─────────────────────────────────────

def get_review_settings(business_id: str) -> dict:
    """Get review settings for a business."""
    conn = get_connection()
    c = conn.cursor()
    c.execute("""
        SELECT * FROM review_settings WHERE business_id = ?
    """, (business_id,))
    row = c.fetchone()
    conn.close()
    
    if row:
        return dict(row)
    
    # Return defaults if no settings exist
    return {
        "id": None,
        "business_id": business_id,
        "enabled": 1,
        "delay_hours": 2,
        "google_url": "",
        "yelp_url": "",
        "custom_message": "",
        "min_stars_public": 4
    }


def save_review_settings(business_id: str, settings: dict) -> str:
    """Save or update review settings for a business."""
    conn = get_connection()
    c = conn.cursor()
    
    # Check if settings exist
    c.execute("SELECT id FROM review_settings WHERE business_id = ?", (business_id,))
    row = c.fetchone()
    
    if row:
        # Update existing
        c.execute("""
            UPDATE review_settings 
            SET enabled = ?, delay_hours = ?, google_url = ?, yelp_url = ?,
                custom_message = ?, min_stars_public = ?, updated_at = ?
            WHERE business_id = ?
        """, (
            settings.get("enabled", 1),
            settings.get("delay_hours", 2),
            settings.get("google_url", ""),
            settings.get("yelp_url", ""),
            settings.get("custom_message", ""),
            settings.get("min_stars_public", 4),
            datetime.now().isoformat(),
            business_id
        ))
        settings_id = row["id"]
    else:
        # Create new
        settings_id = generate_id("rset")
        c.execute("""
            INSERT INTO review_settings 
            (id, business_id, enabled, delay_hours, google_url, yelp_url, custom_message, min_stars_public)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            settings_id,
            business_id,
            settings.get("enabled", 1),
            settings.get("delay_hours", 2),
            settings.get("google_url", ""),
            settings.get("yelp_url", ""),
            settings.get("custom_message", ""),
            settings.get("min_stars_public", 4)
        ))
    
    conn.commit()
    conn.close()
    return settings_id


# ── Review Request Operations ─────────────────────────────────────

def create_review_request(business_id: str, customer_id: str, 
                          card_id: str = None, channel: str = "sms") -> str:
    """Create a review request record."""
    conn = get_connection()
    c = conn.cursor()
    
    request_id = generate_id("rreq")
    c.execute("""
        INSERT INTO review_requests 
        (id, business_id, customer_id, card_id, channel)
        VALUES (?, ?, ?, ?, ?)
    """, (request_id, business_id, customer_id, card_id, channel))
    
    conn.commit()
    conn.close()
    return request_id


def mark_request_opened(request_id: str):
    """Mark a review request as opened."""
    conn = get_connection()
    c = conn.cursor()
    c.execute("""
        UPDATE review_requests 
        SET opened_at = ?, status = 'opened'
        WHERE id = ? AND opened_at IS NULL
    """, (datetime.now().isoformat(), request_id))
    conn.commit()
    conn.close()


def mark_request_rated(request_id: str):
    """Mark a review request as rated."""
    conn = get_connection()
    c = conn.cursor()
    c.execute("""
        UPDATE review_requests 
        SET rated_at = ?, status = 'rated'
        WHERE id = ?
    """, (datetime.now().isoformat(), request_id))
    conn.commit()
    conn.close()


# ── Rating Operations ─────────────────────────────────────

def submit_rating(request_id: str, stars: int, feedback: str = None,
                  is_public: bool = False, source: str = "link") -> str:
    """Submit a customer rating."""
    conn = get_connection()
    c = conn.cursor()
    
    # Get request info
    c.execute("""
        SELECT business_id, customer_id FROM review_requests WHERE id = ?
    """, (request_id,))
    request = c.fetchone()
    
    if not request:
        conn.close()
        return None
    
    rating_id = generate_id("rrat")
    c.execute("""
        INSERT INTO review_ratings 
        (id, request_id, business_id, customer_id, stars, feedback, is_public, source)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (rating_id, request_id, request["business_id"], 
          request["customer_id"], stars, feedback, is_public, source))
    
    # Mark request as rated
    mark_request_rated(request_id)
    
    conn.commit()
    conn.close()
    return rating_id


# ── Business Stats & Dashboard ─────────────────────────────────────

def get_business_review_stats(business_id: str) -> dict:
    """Get review statistics for a business."""
    conn = get_connection()
    c = conn.cursor()
    
    # Total requests
    c.execute("""
        SELECT COUNT(*) as n FROM review_requests WHERE business_id = ?
    """, (business_id,))
    total_requests = c.fetchone()["n"]
    
    # Total ratings
    c.execute("""
        SELECT COUNT(*) as n FROM review_ratings WHERE business_id = ?
    """, (business_id,))
    total_ratings = c.fetchone()["n"]
    
    # Average rating
    c.execute("""
        SELECT AVG(stars) as avg FROM review_ratings WHERE business_id = ?
    """, (business_id,))
    avg_rating = c.fetchone()["avg"] or 0
    
    # Rating distribution
    c.execute("""
        SELECT stars, COUNT(*) as n FROM review_ratings 
        WHERE business_id = ? GROUP BY stars ORDER BY stars DESC
    """, (business_id,))
    distribution = {row["stars"]: row["n"] for row in c.fetchall()}
    
    # Public vs private
    c.execute("""
        SELECT is_public, COUNT(*) as n FROM review_ratings 
        WHERE business_id = ? GROUP BY is_public
    """, (business_id,))
    public_count = 0
    private_count = 0
    for row in c.fetchall():
        if row["is_public"]:
            public_count = row["n"]
        else:
            private_count = row["n"]
    
    # Recent ratings
    c.execute("""
        SELECT rr.*, c.name as customer_name
        FROM review_ratings rr
        JOIN loyalty_customers c ON rr.customer_id = c.id
        WHERE rr.business_id = ?
        ORDER BY rr.submitted_at DESC LIMIT 10
    """, (business_id,))
    recent = [dict(row) for row in c.fetchall()]
    
    conn.close()
    
    return {
        "total_requests": total_requests,
        "total_ratings": total_ratings,
        "average_rating": round(avg_rating, 2),
        "distribution": distribution,
        "public_count": public_count,
        "private_count": private_count,
        "response_rate": round(total_ratings / total_requests * 100, 1) if total_requests > 0 else 0,
        "recent_ratings": recent
    }


def get_private_feedback(business_id: str, limit: int = 20) -> list:
    """Get private feedback (1-3 stars) for a business."""
    conn = get_connection()
    c = conn.cursor()
    c.execute("""
        SELECT rr.*, c.name as customer_name, c.phone, c.email
        FROM review_ratings rr
        JOIN loyalty_customers c ON rr.customer_id = c.id
        WHERE rr.business_id = ? AND rr.stars <= 3
        ORDER BY rr.submitted_at DESC LIMIT ?
    """, (business_id, limit))
    rows = [dict(row) for row in c.fetchall()]
    conn.close()
    return rows


def get_public_reviews(business_id: str, limit: int = 20) -> list:
    """Get public reviews (4-5 stars) for a business."""
    conn = get_connection()
    c = conn.cursor()
    c.execute("""
        SELECT rr.*, c.name as customer_name
        FROM review_ratings rr
        JOIN loyalty_customers c ON rr.customer_id = c.id
        WHERE rr.business_id = ? AND rr.stars >= 4
        ORDER BY rr.submitted_at DESC LIMIT ?
    """, (business_id, limit))
    rows = [dict(row) for row in c.fetchall()]
    conn.close()
    return rows


# ── Integration Helpers ─────────────────────────────────────

def should_send_review_request(business_id: str, customer_id: str, 
                               hours_since_visit: int = None) -> bool:
    """Check if a review request should be sent."""
    conn = get_connection()
    c = conn.cursor()
    
    # Check if reviews are enabled
    c.execute("SELECT enabled, delay_hours FROM review_settings WHERE business_id = ?", (business_id,))
    settings = c.fetchone()
    conn.close()
    
    if not settings or not settings["enabled"]:
        return False
    
    # Check if customer already rated recently
    conn = get_connection()
    c = conn.cursor()
    c.execute("""
        SELECT submitted_at FROM review_ratings 
        WHERE business_id = ? AND customer_id = ?
        ORDER BY submitted_at DESC LIMIT 1
    """, (business_id, customer_id))
    last_rating = c.fetchone()
    conn.close()
    
    if last_rating:
        # Don't send if rated in last 30 days
        last_date = datetime.fromisoformat(last_rating["submitted_at"])
        if datetime.now() - last_date < timedelta(days=30):
            return False
    
    return True
