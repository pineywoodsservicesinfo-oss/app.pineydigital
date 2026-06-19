"""
loyalty_auth.py — Authentication for Loyalty Program
Piney Digital Outreach System — LoyaltyLoop

Handles login/session management for:
- Admin (dashboard users)
- Business owners
- Customers
"""

import os
import secrets
from datetime import datetime, timedelta
from functools import wraps
from flask import session, redirect, url_for, request, jsonify
from modules.loyalty_db import get_connection, generate_id


def init_auth_tables():
    """Create authentication tables."""
    conn = get_connection()
    c = conn.cursor()

    c.executescript("""
        -- Business owner accounts
        CREATE TABLE IF NOT EXISTS loyalty_business_accounts (
            id              TEXT PRIMARY KEY,
            business_id     TEXT REFERENCES loyalty_businesses(id),
            email           TEXT UNIQUE NOT NULL,
            password_hash   TEXT NOT NULL,
            role            TEXT DEFAULT 'owner',  -- 'owner', 'staff'
            created_at      TEXT DEFAULT (datetime('now')),
            last_login      TEXT
        );

        -- Customer accounts (optional - can use cards without account)
        CREATE TABLE IF NOT EXISTS loyalty_customer_accounts (
            id              TEXT PRIMARY KEY,
            customer_id     TEXT REFERENCES loyalty_customers(id),
            email           TEXT UNIQUE,
            phone           TEXT UNIQUE,
            password_hash   TEXT,
            created_at      TEXT DEFAULT (datetime('now')),
            last_login      TEXT
        );

        -- Session tokens (for API auth)
        CREATE TABLE IF NOT EXISTS loyalty_sessions (
            id              TEXT PRIMARY KEY,
            user_type       TEXT,  -- 'admin', 'business', 'customer'
            user_id         TEXT,
            token           TEXT UNIQUE,
            expires_at      TEXT,
            created_at      TEXT DEFAULT (datetime('now'))
        );

        CREATE INDEX IF NOT EXISTS idx_sessions_token ON loyalty_sessions(token);
        CREATE INDEX IF NOT EXISTS idx_sessions_expires ON loyalty_sessions(expires_at);
    """)

    conn.commit()
    conn.close()


def hash_password(password: str) -> str:
    """Secure password hashing using bcrypt."""
    import bcrypt
    return bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')


def verify_password(password: str, password_hash: str) -> bool:
    """Verify a password against its bcrypt hash."""
    import bcrypt
    try:
        return bcrypt.checkpw(password.encode('utf-8'), password_hash.encode('utf-8'))
    except Exception:
        return False


def create_business_account(business_id: str, email: str, password: str, role: str = "owner") -> str:
    """Create a business owner account."""
    import sqlite3
    conn = get_connection()
    c = conn.cursor()

    account_id = generate_id("acct")
    password_hash = hash_password(password)

    try:
        c.execute("""
            INSERT INTO loyalty_business_accounts (id, business_id, email, password_hash, role)
            VALUES (?, ?, ?, ?, ?)
        """, (account_id, business_id, email, password_hash, role))
        conn.commit()
        return account_id
    except sqlite3.IntegrityError:
        conn.close()
        return None  # Email already exists
    finally:
        conn.close()


def create_business_account_with_signup(name: str, business_type: str, city: str,
                                        email: str, password: str, punches: int = 5,
                                        discount: int = 15) -> dict:
    """Create a new business and owner account in one step."""
    from modules.loyalty_db import create_loyalty_business
    
    # Create business first
    biz_id = create_loyalty_business(
        name=name,
        business_type=business_type,
        city=city,
        punches=punches,
        discount=discount
    )
    
    if not biz_id:
        return {"success": False, "error": "Failed to create business"}
    
    # Create account
    account_id = create_business_account(biz_id, email, password)
    
    if not account_id:
        return {"success": False, "error": "Email already registered"}
    
    return {"success": True, "business_id": biz_id, "account_id": account_id}


def create_customer_account(customer_id: str, email: str = None, phone: str = None, 
                           password: str = None) -> str:
    """Create a customer account."""
    import sqlite3
    conn = get_connection()
    c = conn.cursor()

    account_id = generate_id("cact")
    password_hash = hash_password(password) if password else None

    try:
        c.execute("""
            INSERT INTO loyalty_customer_accounts (id, customer_id, email, phone, password_hash)
            VALUES (?, ?, ?, ?, ?)
        """, (account_id, customer_id, email, phone, password_hash))
        conn.commit()
        return account_id
    except sqlite3.IntegrityError:
        conn.close()
        return None  # Email/phone already exists
    finally:
        conn.close()


def create_customer_account_with_signup(name: str, email: str = None, phone: str = None,
                                        password: str = None) -> dict:
    """Create a new customer and account in one step."""
    from modules.loyalty_db import create_customer
    
    # Create customer first
    cust_id = create_customer(name=name, email=email, phone=phone)
    
    if not cust_id:
        return {"success": False, "error": "Failed to create customer"}
    
    # Create account
    account_id = create_customer_account(cust_id, email, phone, password)
    
    if not account_id:
        return {"success": False, "error": "Email or phone already registered"}
    
    return {"success": True, "customer_id": cust_id, "account_id": account_id}


def create_customer_account(customer_id: str, email: str = None, phone: str = None, 
                           password: str = None) -> str:
    """Create a customer account."""
    conn = get_connection()
    c = conn.cursor()

    account_id = generate_id("cact")
    password_hash = hash_password(password) if password else None

    try:
        c.execute("""
            INSERT INTO loyalty_customer_accounts (id, customer_id, email, phone, password_hash)
            VALUES (?, ?, ?, ?, ?)
        """, (account_id, customer_id, email, phone, password_hash))
        conn.commit()
        return account_id
    except sqlite3.IntegrityError:
        conn.close()
        return None  # Email/phone already exists
    finally:
        conn.close()


def authenticate_business(email: str, password: str) -> dict:
    """Authenticate a business owner."""
    conn = get_connection()
    c = conn.cursor()

    # Get user by email first
    c.execute("""
        SELECT a.*, b.name as business_name, b.id as business_id
        FROM loyalty_business_accounts a
        JOIN loyalty_businesses b ON a.business_id = b.id
        WHERE a.email = ?
    """, (email,))

    row = c.fetchone()

    if row and verify_password(password, row["password_hash"]):
        # Update last login
        c.execute("""
            UPDATE loyalty_business_accounts
            SET last_login = ? WHERE id = ?
        """, (datetime.now().isoformat(), row["id"]))
        conn.commit()
        conn.close()
        return dict(row)

    conn.close()
    return None


def authenticate_customer(email: str = None, phone: str = None, password: str = None) -> dict:
    """Authenticate a customer."""
    conn = get_connection()
    c = conn.cursor()

    query = "SELECT a.*, c.name as customer_name, c.id as customer_id FROM loyalty_customer_accounts a JOIN loyalty_customers c ON a.customer_id = c.id WHERE "
    params = []

    if email:
        query += "a.email = ?"
        params = [email]
    elif phone:
        query += "a.phone = ?"
        params = [phone]
    else:
        conn.close()
        return None

    c.execute(query, params)
    row = c.fetchone()

    if row and password and verify_password(password, row["password_hash"]):
        c.execute("""
            UPDATE loyalty_customer_accounts
            SET last_login = ? WHERE id = ?
        """, (datetime.now().isoformat(), row["id"]))
        conn.commit()
        conn.close()
        return dict(row)

    conn.close()
    return None


def create_session(user_type: str, user_id: str, expires_hours: int = 24) -> str:
    """Create a session token."""
    conn = get_connection()
    c = conn.cursor()

    token = secrets.token_urlsafe(32)
    expires_at = (datetime.now() + timedelta(hours=expires_hours)).isoformat()

    c.execute("""
        INSERT INTO loyalty_sessions (id, user_type, user_id, token, expires_at)
        VALUES (?, ?, ?, ?, ?)
    """, (generate_id("sess"), user_type, user_id, token, expires_at))

    conn.commit()
    conn.close()

    return token


def validate_session(token: str) -> dict:
    """Validate a session token."""
    conn = get_connection()
    c = conn.cursor()

    c.execute("""
        SELECT * FROM loyalty_sessions 
        WHERE token = ? AND expires_at > ?
    """, (token, datetime.now().isoformat()))

    row = c.fetchone()
    conn.close()

    return dict(row) if row else None


def destroy_session(token: str):
    """Invalidate a session token."""
    conn = get_connection()
    c = conn.cursor()
    c.execute("DELETE FROM loyalty_sessions WHERE token = ?", (token,))
    conn.commit()
    conn.close()


# ── Flask Session Helpers ─────────────────────────────────────

def login_required_admin(f):
    """Require admin (dashboard) login."""
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("logged_in"):
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated


def loyalty_login_required(f):
    """Require any loyalty system login (business or customer)."""
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("loyalty_user"):
            return redirect(url_for("loyalty_landing"))
        return f(*args, **kwargs)
    return decorated


def business_login_required(f):
    """Require business owner login."""
    @wraps(f)
    def decorated(*args, **kwargs):
        user = session.get("loyalty_user")
        if not user or user.get("user_type") != "business":
            return redirect(url_for("loyalty_landing"))
        return f(*args, **kwargs)
    return decorated


def customer_login_required(f):
    """Require customer login."""
    @wraps(f)
    def decorated(*args, **kwargs):
        user = session.get("loyalty_user")
        if not user or user.get("user_type") != "customer":
            return redirect(url_for("loyalty_landing"))
        return f(*args, **kwargs)
    return decorated
