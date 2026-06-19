"""
loyalty_db.py — Loyalty Program Database Module
Piney Digital Outreach System — LoyaltyLoop Integration

Manages businesses, customers, punch cards, and rewards.
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


def init_loyalty_tables():
    """Create loyalty program tables if they don't exist."""
    conn = get_connection()
    c = conn.cursor()

    c.executescript("""
        -- Loyalty businesses (can link to existing leads)
        CREATE TABLE IF NOT EXISTS loyalty_businesses (
            id              TEXT PRIMARY KEY,
            lead_id         INTEGER REFERENCES leads(id),
            name            TEXT NOT NULL,
            type            TEXT,
            description     TEXT,
            address         TEXT,
            city            TEXT,
            phone           TEXT,
            website         TEXT,
            logo_url        TEXT,
            punches_needed  INTEGER DEFAULT 5,
            discount_percent INTEGER DEFAULT 15,
            active          INTEGER DEFAULT 1,
            created_at      TEXT DEFAULT (datetime('now')),
            updated_at      TEXT DEFAULT (datetime('now'))
        );

        -- Loyalty customers
        CREATE TABLE IF NOT EXISTS loyalty_customers (
            id              TEXT PRIMARY KEY,
            name            TEXT NOT NULL,
            email           TEXT,
            phone           TEXT,
            password_hash   TEXT,
            created_at      TEXT DEFAULT (datetime('now')),
            updated_at      TEXT DEFAULT (datetime('now'))
        );

        -- Customer loyalty cards (one per customer-business pair)
        CREATE TABLE IF NOT EXISTS loyalty_cards (
            id              TEXT PRIMARY KEY,
            customer_id     TEXT REFERENCES loyalty_customers(id),
            business_id     TEXT REFERENCES loyalty_businesses(id),
            punches         INTEGER DEFAULT 0,
            rewards_earned  INTEGER DEFAULT 0,
            last_punch_at   TEXT,
            created_at      TEXT DEFAULT (datetime('now')),
            updated_at      TEXT DEFAULT (datetime('now')),
            UNIQUE(customer_id, business_id)
        );

        -- Punch history (audit trail)
        CREATE TABLE IF NOT EXISTS punch_history (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            card_id         TEXT REFERENCES loyalty_cards(id),
            business_id     TEXT REFERENCES loyalty_businesses(id),
            customer_id     TEXT REFERENCES loyalty_customers(id),
            punched_at      TEXT DEFAULT (datetime('now')),
            punched_by      TEXT,
            notes           TEXT
        );

        -- Reward redemptions
        CREATE TABLE IF NOT EXISTS reward_redemptions (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            card_id         TEXT REFERENCES loyalty_cards(id),
            business_id     TEXT REFERENCES loyalty_businesses(id),
            customer_id     TEXT REFERENCES loyalty_customers(id),
            discount_percent INTEGER,
            redeemed_at     TEXT DEFAULT (datetime('now')),
            redeemed_by     TEXT,
            notes           TEXT
        );

        -- Indexes for performance
        CREATE INDEX IF NOT EXISTS idx_cards_customer ON loyalty_cards(customer_id);
        CREATE INDEX IF NOT EXISTS idx_cards_business ON loyalty_cards(business_id);
        CREATE INDEX IF NOT EXISTS idx_punch_card ON punch_history(card_id);
        CREATE INDEX IF NOT EXISTS idx_redemptions_card ON reward_redemptions(card_id);
    """)

    # Run migrations for existing databases
    _migrate_loyalty_tables(conn)

    conn.commit()
    conn.close()
    logger.info("Loyalty tables initialised")


def _migrate_loyalty_tables(conn):
    """Add new columns to existing loyalty_businesses table."""
    c = conn.cursor()
    c.execute("PRAGMA table_info(loyalty_businesses)")
    columns = [col[1] for col in c.fetchall()]

    migrations = []
    if "description" not in columns:
        migrations.append("ALTER TABLE loyalty_businesses ADD COLUMN description TEXT")
    if "address" not in columns:
        migrations.append("ALTER TABLE loyalty_businesses ADD COLUMN address TEXT")
    if "website" not in columns:
        migrations.append("ALTER TABLE loyalty_businesses ADD COLUMN website TEXT")
    if "logo_url" not in columns:
        migrations.append("ALTER TABLE loyalty_businesses ADD COLUMN logo_url TEXT")

    for migration in migrations:
        try:
            c.execute(migration)
            logger.info(f"Migration applied: {migration}")
        except sqlite3.OperationalError as e:
            if "duplicate column" not in str(e).lower():
                logger.warning(f"Migration failed: {e}")


def generate_id(prefix: str) -> str:
    """Generate a unique ID with prefix."""
    import uuid
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


# ── Business Operations ─────────────────────────────────────

def create_loyalty_business(lead_id: int = None, name: str = None,
                            business_type: str = None, city: str = None,
                            phone: str = None, description: str = None,
                            address: str = None, website: str = None,
                            punches: int = 5, discount: int = 15) -> str:
    """Create a new loyalty business or convert an existing lead."""
    conn = get_connection()
    c = conn.cursor()

    biz_id = generate_id("biz")

    # If lead_id provided, pull data from leads table
    if lead_id:
        c.execute("SELECT business_name, city, phone, website FROM leads WHERE id = ?", (lead_id,))
        row = c.fetchone()
        if row:
            name = name or row["business_name"]
            city = city or row["city"]
            phone = phone or row["phone"]
            website = website or row["website"]

    c.execute("""
        INSERT INTO loyalty_businesses
        (id, lead_id, name, type, description, address, city, phone, website, punches_needed, discount_percent)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (biz_id, lead_id, name, business_type, description, address, city, phone, website, punches, discount))

    conn.commit()
    conn.close()
    return biz_id


def get_all_loyalty_businesses(active_only: bool = True) -> list:
    """Get all businesses in the loyalty program."""
    conn = get_connection()
    c = conn.cursor()

    query = "SELECT * FROM loyalty_businesses"
    if active_only:
        query += " WHERE active = 1"
    query += " ORDER BY name"

    c.execute(query)
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    return rows


def get_loyalty_business(biz_id: str) -> dict:
    """Get a single loyalty business by ID."""
    conn = get_connection()
    c = conn.cursor()
    c.execute("SELECT * FROM loyalty_businesses WHERE id = ?", (biz_id,))
    row = c.fetchone()
    conn.close()
    return dict(row) if row else None


def update_loyalty_business(biz_id: str, fields: dict):
    """Update loyalty business settings."""
    fields["updated_at"] = datetime.now().isoformat()
    fields["id"] = biz_id

    set_clause = ", ".join(f"{k} = :{k}" for k in fields if k != "id")
    conn = get_connection()
    conn.execute(f"UPDATE loyalty_businesses SET {set_clause} WHERE id = :id", fields)
    conn.commit()
    conn.close()


# ── Customer Operations ─────────────────────────────────────

def create_customer(name: str, email: str = None, phone: str = None,
                    password_hash: str = None) -> str:
    """Create a new loyalty customer."""
    conn = get_connection()
    c = conn.cursor()

    cust_id = generate_id("cust")
    c.execute("""
        INSERT INTO loyalty_customers (id, name, email, phone, password_hash)
        VALUES (?, ?, ?, ?, ?)
    """, (cust_id, name, email, phone, password_hash))

    conn.commit()
    conn.close()
    return cust_id


def get_customer_by_email(email: str) -> dict:
    """Get customer by email."""
    conn = get_connection()
    c = conn.cursor()
    c.execute("SELECT * FROM loyalty_customers WHERE email = ?", (email.lower(),))
    row = c.fetchone()
    conn.close()
    return dict(row) if row else None


def get_customer_by_phone(phone: str) -> dict:
    """Get customer by phone."""
    import re
    digits = re.sub(r"\D", "", phone)
    conn = get_connection()
    c = conn.cursor()
    c.execute("SELECT * FROM loyalty_customers WHERE REPLACE(REPLACE(phone,'-',''),' ','') LIKE ?", (f"%{digits}%",))
    row = c.fetchone()
    conn.close()
    return dict(row) if row else None


def get_all_customers() -> list:
    """Get all loyalty customers."""
    conn = get_connection()
    c = conn.cursor()
    c.execute("SELECT * FROM loyalty_customers ORDER BY name")
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    return rows


def get_customer(cust_id: str) -> dict:
    """Get a single customer by ID."""
    conn = get_connection()
    c = conn.cursor()
    c.execute("SELECT * FROM loyalty_customers WHERE id = ?", (cust_id,))
    row = c.fetchone()
    conn.close()
    return dict(row) if row else None


def get_or_create_customer_card(customer_id: str, business_id: str) -> dict:
    """Get existing card or create new one for customer-business pair."""
    conn = get_connection()
    c = conn.cursor()

    c.execute("""
        SELECT * FROM loyalty_cards 
        WHERE customer_id = ? AND business_id = ?
    """, (customer_id, business_id))

    row = c.fetchone()

    if row:
        conn.close()
        return dict(row)

    # Create new card
    card_id = generate_id("card")
    c.execute("""
        INSERT INTO loyalty_cards (id, customer_id, business_id)
        VALUES (?, ?, ?)
    """, (card_id, customer_id, business_id))

    conn.commit()

    c.execute("SELECT * FROM loyalty_cards WHERE id = ?", (card_id,))
    row = c.fetchone()
    conn.close()

    return dict(row) if row else None


def get_customer_cards(customer_id: str) -> list:
    """Get all loyalty cards for a customer."""
    conn = get_connection()
    c = conn.cursor()

    c.execute("""
        SELECT lc.*, lb.name as business_name, lb.type as business_type,
               lb.punches_needed, lb.discount_percent,
               (lc.rewards_earned + CASE WHEN lc.punches >= lb.punches_needed THEN 1 ELSE 0 END) as total_rewards_earned
        FROM loyalty_cards lc
        JOIN loyalty_businesses lb ON lc.business_id = lb.id
        WHERE lc.customer_id = ?
        ORDER BY lb.name
    """, (customer_id,))

    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    return rows


# ── Punch Operations ─────────────────────────────────────

def add_punch(card_id: str, punched_by: str = "business", notes: str = None, auto_reward: bool = True) -> dict:
    """Add a punch to a loyalty card. Returns updated card."""
    conn = get_connection()
    c = conn.cursor()

    # Get card info
    c.execute("""
        SELECT lc.*, lb.punches_needed, lb.discount_percent, lb.name as business_name
        FROM loyalty_cards lc
        JOIN loyalty_businesses lb ON lc.business_id = lb.id
        WHERE lc.id = ?
    """, (card_id,))
    card = c.fetchone()

    if not card:
        conn.close()
        return None

    # Store original values before any modifications
    original_punches_needed = card["punches_needed"]
    original_business_name = card["business_name"]
    original_discount = card["discount_percent"]
    
    # Check if card is already complete (for auto-reward cycle)
    was_complete = card["punches"] >= card["punches_needed"]
    
    if was_complete and auto_reward:
        # Record the reward redemption automatically
        c.execute("""
            INSERT INTO reward_redemptions 
            (card_id, business_id, customer_id, discount_percent, redeemed_by, notes)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (card_id, card["business_id"], card["customer_id"], 
              card["discount_percent"], "auto_cycle", notes or "Auto-reward: started new card"))
        
        # Increment rewards_earned and reset punches before adding new one
        c.execute("""
            UPDATE loyalty_cards 
            SET rewards_earned = rewards_earned + 1, punches = 0, updated_at = ?
            WHERE id = ?
        """, (datetime.now().isoformat(), card_id))
        
        conn.commit()
        
        # Now add the new punch to the fresh card
        new_punches = 1
    else:
        # Normal punch addition
        new_punches = card["punches"] + 1

    now = datetime.now().isoformat()

    c.execute("""
        UPDATE loyalty_cards 
        SET punches = ?, last_punch_at = ?, updated_at = ?
        WHERE id = ?
    """, (new_punches, now, now, card_id))

    # Record in history
    c.execute("""
        INSERT INTO punch_history (card_id, business_id, customer_id, punched_by, notes)
        VALUES (?, ?, ?, ?, ?)
    """, (card_id, card["business_id"], card["customer_id"], punched_by, notes))

    conn.commit()

    # Get updated card
    c.execute("SELECT * FROM loyalty_cards WHERE id = ?", (card_id,))
    updated = c.fetchone()
    conn.close()

    reward_earned = updated["punches"] >= original_punches_needed
    card_completed = was_complete and auto_reward  # Track if we just completed a cycle
    
    return {
        "card": dict(updated),
        "reward_earned": reward_earned,
        "punches_needed": original_punches_needed,
        "card_completed": card_completed,
        "total_rewards": updated["rewards_earned"] + (1 if card_completed else 0),
        "business_name": original_business_name,
        "discount_percent": original_discount
    }


def redeem_reward(card_id: str, redeemed_by: str = "business", notes: str = None) -> dict:
    """Redeem a reward (reset punches, record redemption)."""
    conn = get_connection()
    c = conn.cursor()

    # Get card and business info
    c.execute("""
        SELECT lc.*, lb.discount_percent 
        FROM loyalty_cards lc
        JOIN loyalty_businesses lb ON lc.business_id = lb.id
        WHERE lc.id = ?
    """, (card_id,))
    card = c.fetchone()

    if not card or card["punches"] < card["punches_needed"]:
        conn.close()
        return {"success": False, "error": "Not enough punches"}

    now = datetime.now().isoformat()

    # Record redemption
    c.execute("""
        INSERT INTO reward_redemptions 
        (card_id, business_id, customer_id, discount_percent, redeemed_by, notes)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (card_id, card["business_id"], card["customer_id"], 
          card["discount_percent"], redeemed_by, notes))

    # Reset punches, increment rewards_earned
    c.execute("""
        UPDATE loyalty_cards 
        SET punches = 0, rewards_earned = rewards_earned + 1, updated_at = ?
        WHERE id = ?
    """, (now, card_id))

    conn.commit()

    return {
        "success": True,
        "discount_percent": card["discount_percent"],
        "new_punches": 0
    }


# ── Business Dashboard Stats ─────────────────────────────────────

def get_business_stats(business_id: str) -> dict:
    """Get stats for a business dashboard."""
    conn = get_connection()
    c = conn.cursor()

    # Total customers
    c.execute("""
        SELECT COUNT(DISTINCT customer_id) as n 
        FROM loyalty_cards 
        WHERE business_id = ?
    """, (business_id,))
    total_customers = c.fetchone()["n"]

    # Total punches given
    c.execute("""
        SELECT SUM(punches) as n FROM loyalty_cards WHERE business_id = ?
    """, (business_id,))
    total_punches = c.fetchone()["n"] or 0

    # Rewards earned
    c.execute("""
        SELECT COUNT(*) as n FROM reward_redemptions WHERE business_id = ?
    """, (business_id,))
    total_rewards = c.fetchone()["n"]

    # Customer list with progress
    c.execute("""
        SELECT lc.*, c.name as customer_name, lb.punches_needed
        FROM loyalty_cards lc
        JOIN loyalty_customers c ON lc.customer_id = c.id
        JOIN loyalty_businesses lb ON lc.business_id = lb.id
        WHERE lc.business_id = ?
        ORDER BY lc.punches DESC
    """, (business_id,))
    customers = [dict(r) for r in c.fetchall()]

    conn.close()

    return {
        "total_customers": total_customers,
        "total_punches": total_punches,
        "total_rewards": total_rewards,
        "customers": customers
    }


# ── Admin Stats ─────────────────────────────────────

def get_loyalty_stats() -> dict:
    """Get overall loyalty program statistics."""
    conn = get_connection()
    c = conn.cursor()

    stats = {}

    c.execute("SELECT COUNT(*) as n FROM loyalty_businesses WHERE active = 1")
    stats["active_businesses"] = c.fetchone()["n"]

    c.execute("SELECT COUNT(*) as n FROM loyalty_customers")
    stats["total_customers"] = c.fetchone()["n"]

    c.execute("SELECT COUNT(*) as n FROM loyalty_cards")
    stats["total_cards"] = c.fetchone()["n"]

    c.execute("SELECT SUM(punches) as n FROM loyalty_cards")
    stats["total_punches"] = c.fetchone()["n"] or 0

    c.execute("SELECT COUNT(*) as n FROM reward_redemptions")
    stats["total_rewards_redeemed"] = c.fetchone()["n"]

    conn.close()
    return stats


# ── Integration with Leads ─────────────────────────────────────

def convert_lead_to_loyalty_business(lead_id: int, punches: int = 5, 
                                     discount: int = 15) -> str:
    """Convert an existing lead to a loyalty business."""
    conn = get_connection()
    c = conn.cursor()

    c.execute("""
        SELECT business_name, city, phone, category 
        FROM leads WHERE id = ?
    """, (lead_id,))
    lead = c.fetchone()
    conn.close()

    if not lead:
        return None

    return create_loyalty_business(
        lead_id=lead_id,
        name=lead["business_name"],
        business_type=lead["category"],
        city=lead["city"],
        phone=lead["phone"],
        punches=punches,
        discount=discount
    )
