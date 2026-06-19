"""
auth_security.py — Authentication and Security Module
Piney Digital Outreach System

Security features:
  - Password validation (strong password requirements)
  - Email verification tokens
  - 2FA code generation and validation
  - Session management with timeout
  - Admin 2FA verification
  - Rate limiting for brute force prevention

Usage:
  from modules.auth_security import (
      validate_password, hash_password, verify_password,
      generate_2fa_code, verify_2fa_code,
      generate_email_token, verify_email_token,
      create_session, validate_session, destroy_session
  )
"""

import os
import re
import secrets
import hashlib
import time
from datetime import datetime, timedelta
from typing import Optional, Tuple
import bcrypt


# ── Password Requirements ─────────────────────────────────────
MIN_PASSWORD_LENGTH = 8
REQUIRE_UPPERCASE = True
REQUIRE_LOWERCASE = True
REQUIRE_NUMBER = True
REQUIRE_SPECIAL = False  # Optional for better UX
SPECIAL_CHARS = "!@#$%^&*()_+-=[]{}|;:,.<>?"

# ── Session Settings ──────────────────────────────────────────
SESSION_DURATION_HOURS = 24
REMEMBER_ME_DAYS = 7

# ── 2FA Settings ──────────────────────────────────────────────
TWO_FA_CODE_LENGTH = 6
TWO_FA_EXPIRY_MINUTES = 5
ADMIN_REMEMBER_DAYS = 30


# ── Password Validation ────────────────────────────────────────

def validate_password(password: str) -> Tuple[bool, str]:
    """
    Validate password meets security requirements.

    Returns:
        (is_valid, error_message)
    """
    errors = []

    if len(password) < MIN_PASSWORD_LENGTH:
        errors.append(f"At least {MIN_PASSWORD_LENGTH} characters")

    if REQUIRE_UPPERCASE and not re.search(r'[A-Z]', password):
        errors.append("At least one uppercase letter")

    if REQUIRE_LOWERCASE and not re.search(r'[a-z]', password):
        errors.append("At least one lowercase letter")

    if REQUIRE_NUMBER and not re.search(r'\d', password):
        errors.append("At least one number")

    if REQUIRE_SPECIAL and not re.search(r'[!@#$%^&*()_+\-=\[\]{}|;:,.<>?]', password):
        errors.append("At least one special character (!@#$%^&* etc.)")

    # Check for common weak patterns
    weak_patterns = [
        r'(.)\1{2,}',  # Same character repeated 3+ times
        r'123|234|345|456|567|678|789|890',  # Sequential numbers
        r'abc|bcd|cde|def|efg|fgh|ghi|hij|ijk|jkl|klm|lmn|mno|nop|opq|pqr|qrs|rst|stu|tuv|uvw|vwx|wxy|xyz',  # Sequential letters
        r'password|qwerty|letmein|welcome|monkey|dragon',  # Common passwords
    ]

    for pattern in weak_patterns:
        if re.search(pattern, password.lower()):
            errors.append("Contains weak pattern - try something more unique")
            break

    if errors:
        return False, "Password requires: " + ", ".join(errors)

    return True, ""


def get_password_requirements() -> dict:
    """Return password requirements for UI display."""
    return {
        "min_length": MIN_PASSWORD_LENGTH,
        "require_uppercase": REQUIRE_UPPERCASE,
        "require_lowercase": REQUIRE_LOWERCASE,
        "require_number": REQUIRE_NUMBER,
        "require_special": REQUIRE_SPECIAL,
        "special_chars": SPECIAL_CHARS,
    }


def check_password_strength(password: str) -> dict:
    """
    Check password strength and return score for UI.
    Returns dict with score (0-100) and checklist of requirements.
    """
    checks = {
        "length": len(password) >= MIN_PASSWORD_LENGTH,
        "uppercase": bool(re.search(r'[A-Z]', password)) if REQUIRE_UPPERCASE else True,
        "lowercase": bool(re.search(r'[a-z]', password)) if REQUIRE_LOWERCASE else True,
        "number": bool(re.search(r'\d', password)) if REQUIRE_NUMBER else True,
        "special": bool(re.search(r'[!@#$%^&*()_+\-=\[\]{}|;:,.<>?]', password)) if REQUIRE_SPECIAL else True,
    }

    passed = sum(1 for v in checks.values() if v)
    total = len(checks)
    score = int((passed / total) * 100)

    return {
        "score": score,
        "checks": checks,
        "passed": passed,
        "total": total,
    }


# ── Password Hashing ───────────────────────────────────────────

def hash_password(password: str) -> str:
    """Hash password with bcrypt."""
    salt = bcrypt.gensalt(rounds=12)
    return bcrypt.hashpw(password.encode('utf-8'), salt).decode('utf-8')


def verify_password(password: str, password_hash: str) -> bool:
    """Verify password against hash."""
    try:
        return bcrypt.checkpw(password.encode('utf-8'), password_hash.encode('utf-8'))
    except Exception:
        return False


# ── 2FA Codes ──────────────────────────────────────────────────

# In-memory store for 2FA codes (in production, use Redis or DB)
_2fa_codes = {}  # {email: {"code": "123456", "expires": timestamp, "attempts": 0}}


def generate_2fa_code(email: str) -> str:
    """
    Generate a 6-digit 2FA code.
    Code expires in 5 minutes.
    """
    code = ''.join(secrets.choice('0123456789') for _ in range(TWO_FA_CODE_LENGTH))
    expires = time.time() + (TWO_FA_EXPIRY_MINUTES * 60)

    _2fa_codes[email] = {
        "code": code,
        "expires": expires,
        "attempts": 0,
    }

    return code


def verify_2fa_code(email: str, code: str) -> Tuple[bool, str]:
    """
    Verify a 2FA code.

    Returns:
        (is_valid, message)
    """
    if email not in _2fa_codes:
        return False, "No code found. Please request a new one."

    stored = _2fa_codes[email]

    # Check expiry
    if time.time() > stored["expires"]:
        del _2fa_codes[email]
        return False, "Code expired. Please request a new one."

    # Check attempts (prevent brute force)
    if stored["attempts"] >= 5:
        del _2fa_codes[email]
        return False, "Too many attempts. Please request a new code."

    stored["attempts"] += 1

    # Verify code
    if secrets.compare_digest(stored["code"], code):
        del _2fa_codes[email]  # One-time use
        return True, "Verified"

    return False, f"Invalid code. {5 - stored['attempts']} attempts remaining."


def invalidate_2fa_code(email: str):
    """Remove a 2FA code."""
    _2fa_codes.pop(email, None)


# ── Email Verification Tokens ─────────────────────────────────

_email_tokens = {}  # {email: {"token": "...", "expires": timestamp, "type": "verify|reset"}}


def generate_email_token(email: str, token_type: str = "verify") -> str:
    """
    Generate an email verification or password reset token.
    Token expires in 24 hours.
    """
    token = secrets.token_urlsafe(32)
    expires = time.time() + (24 * 60 * 60)  # 24 hours

    _email_tokens[f"{email}:{token_type}"] = {
        "token": token,
        "expires": expires,
    }

    return token


def verify_email_token(email: str, token: str, token_type: str = "verify") -> bool:
    """Verify an email token."""
    key = f"{email}:{token_type}"

    if key not in _email_tokens:
        return False

    stored = _email_tokens[key]

    if time.time() > stored["expires"]:
        del _email_tokens[key]
        return False

    if secrets.compare_digest(stored["token"], token):
        del _email_tokens[key]
        return True

    return False


# ── Session Management ────────────────────────────────────────

def create_session(session_id: str, user_id: int, user_type: str,
                   remember: bool = False) -> dict:
    """
    Create a new session.

    Args:
        session_id: Unique session identifier
        user_id: User's database ID
        user_type: 'admin' or 'business'
        remember: If True, session lasts 7 days instead of 24 hours

    Returns:
        Session data dict
    """
    if remember:
        expires = datetime.now() + timedelta(days=REMEMBER_ME_DAYS)
    else:
        expires = datetime.now() + timedelta(hours=SESSION_DURATION_HOURS)

    return {
        "session_id": session_id,
        "user_id": user_id,
        "user_type": user_type,
        "created_at": datetime.now().isoformat(),
        "expires_at": expires.isoformat(),
        "remember": remember,
    }


def validate_session(session_data: dict) -> Tuple[bool, str]:
    """
    Validate a session.

    Returns:
        (is_valid, message)
    """
    if not session_data:
        return False, "No session"

    try:
        expires = datetime.fromisoformat(session_data.get("expires_at", ""))
        if datetime.now() > expires:
            return False, "Session expired"
    except (ValueError, TypeError):
        return False, "Invalid session"

    return True, "Valid"


def session_expires_at(session_data: dict) -> datetime:
    """Get session expiration datetime."""
    return datetime.fromisoformat(session_data.get("expires_at", datetime.now().isoformat()))


# ── Rate Limiting ──────────────────────────────────────────────

_login_attempts = {}  # {ip_or_email: {"count": N, "first_attempt": timestamp}}


def check_rate_limit(identifier: str, max_attempts: int = 5,
                      window_minutes: int = 15) -> Tuple[bool, int]:
    """
    Check if login attempts exceed rate limit.

    Args:
        identifier: IP address or email
        max_attempts: Maximum allowed attempts in window
        window_minutes: Time window in minutes

    Returns:
        (allowed, remaining_seconds)
    """
    now = time.time()
    window_seconds = window_minutes * 60

    if identifier in _login_attempts:
        attempts = _login_attempts[identifier]

        # Reset if window expired
        if now - attempts["first_attempt"] > window_seconds:
            del _login_attempts[identifier]
        elif attempts["count"] >= max_attempts:
            remaining = int(window_seconds - (now - attempts["first_attempt"]))
            return False, remaining

    return True, 0


def record_login_attempt(identifier: str, success: bool = False):
    """Record a login attempt."""
    now = time.time()

    if success:
        # Clear attempts on successful login
        _login_attempts.pop(identifier, None)
    else:
        if identifier not in _login_attempts:
            _login_attempts[identifier] = {"count": 0, "first_attempt": now}
        _login_attempts[identifier]["count"] += 1


def clear_rate_limit(identifier: str):
    """Clear rate limit for an identifier."""
    _login_attempts.pop(identifier, None)


# ── Security Helpers ───────────────────────────────────────────

def generate_secure_id() -> str:
    """Generate a secure random ID."""
    return secrets.token_urlsafe(16)


def is_valid_email(email: str) -> bool:
    """Validate email format."""
    pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    return bool(re.match(pattern, email))


def sanitize_input(text: str, max_length: int = 255) -> str:
    """Sanitize user input."""
    if not text:
        return ""
    # Remove potential XSS
    text = re.sub(r'<[^>]*>', '', text)
    # Limit length
    return text[:max_length].strip()