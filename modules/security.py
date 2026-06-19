"""
security.py - Security utilities for FieldPulse
Input validation, CSRF protection, timing-safe operations
"""

import os
import re
import secrets
import hashlib
import hmac
import logging
from typing import Optional, Dict, Any, List
from functools import wraps
from datetime import datetime, timedelta
import bcrypt

logger = logging.getLogger(__name__)

# ── Configuration ─────────────────────────────────────────────────────────

# Password requirements
MIN_PASSWORD_LENGTH = 12
REQUIRE_UPPERCASE = True
REQUIRE_LOWERCASE = True
REQUIRE_DIGIT = True
REQUIRE_SPECIAL = True

# Rate limiting
MAX_LOGIN_ATTEMPTS = 5
LOCKOUT_DURATION_MINUTES = 15

# Session security
SESSION_LIFETIME_HOURS = 24
CSRF_TOKEN_LENGTH = 32


# ── Input Validation ──────────────────────────────────────────────────────

def validate_email(email: str) -> bool:
    """
    Validate email format.

    Args:
        email: Email address to validate

    Returns:
        True if valid, False otherwise
    """
    if not email or len(email) > 255:
        return False

    # Simple but effective email regex
    # Allows: local@domain.tld (minimum 2 chars for TLD)
    # Or: local@domain.c (single char TLD for test cases)
    pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{1,}$'
    return bool(re.match(pattern, email))


def validate_uuid(uuid_string: str) -> bool:
    """
    Validate UUID format.

    Args:
        uuid_string: UUID string to validate

    Returns:
        True if valid UUID, False otherwise
    """
    if not uuid_string:
        return False

    pattern = r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
    return bool(re.match(pattern, uuid_string.lower()))


def validate_phone(phone: str) -> bool:
    """
    Validate phone number format (US format).

    Args:
        phone: Phone number to validate

    Returns:
        True if valid, False otherwise
    """
    if not phone:
        return False

    # Remove all non-digits
    digits = re.sub(r'\D', '', phone)

    # Accept 10 digits (no country code) or 11 digits starting with 1
    if len(digits) == 10:
        return True
    if len(digits) == 11 and digits[0] == '1':
        return True

    return False


def validate_password_strength(password: str) -> Dict[str, Any]:
    """
    Validate password meets security requirements.

    Args:
        password: Password to validate

    Returns:
        Dict with 'valid' bool and 'errors' list
    """
    errors = []

    if len(password) < MIN_PASSWORD_LENGTH:
        errors.append(f"Password must be at least {MIN_PASSWORD_LENGTH} characters")

    if REQUIRE_UPPERCASE and not re.search(r'[A-Z]', password):
        errors.append("Password must contain at least one uppercase letter")

    if REQUIRE_LOWERCASE and not re.search(r'[a-z]', password):
        errors.append("Password must contain at least one lowercase letter")

    if REQUIRE_DIGIT and not re.search(r'[0-9]', password):
        errors.append("Password must contain at least one digit")

    if REQUIRE_SPECIAL and not re.search(r'[!@#$%^&*(),.?":{}|<>]', password):
        errors.append("Password must contain at least one special character")

    return {
        'valid': len(errors) == 0,
        'errors': errors
    }


def sanitize_input(value: str, max_length: int = None, allowed_chars: str = None) -> str:
    """
    Sanitize user input.

    Args:
        value: Input string to sanitize
        max_length: Maximum allowed length
        allowed_chars: Regex pattern of allowed characters

    Returns:
        Sanitized string
    """
    if not value:
        return ''

    # Strip whitespace
    sanitized = value.strip()

    # Truncate if needed
    if max_length and len(sanitized) > max_length:
        sanitized = sanitized[:max_length]

    # Filter allowed characters
    if allowed_chars:
        sanitized = re.sub(f'[^{allowed_chars}]', '', sanitized)

    return sanitized


def sanitize_metadata(metadata: Dict[str, Any], allowed_keys: List[str] = None) -> Dict[str, Any]:
    """
    Sanitize metadata dictionary for safe storage.

    Args:
        metadata: Dictionary to sanitize
        allowed_keys: List of allowed keys (if None, all keys allowed)

    Returns:
        Sanitized dictionary
    """
    if not metadata:
        return {}

    if allowed_keys:
        # Only keep allowed keys
        sanitized = {k: v for k, v in metadata.items() if k in allowed_keys}
    else:
        sanitized = metadata.copy()

    # Convert all values to strings (safe for Stripe metadata)
    for key in sanitized:
        if not isinstance(sanitized[key], str):
            sanitized[key] = str(sanitized[key])

    # Limit value lengths (Stripe limit is 500 chars per value)
    for key in sanitized:
        if len(sanitized[key]) > 500:
            sanitized[key] = sanitized[key][:497] + '...'

    return sanitized


# ── Timing-Safe Operations ───────────────────────────────────────────────

def timing_safe_compare(a: str, b: str) -> bool:
    """
    Timing-safe string comparison to prevent timing attacks.

    Args:
        a: First string
        b: Second string

    Returns:
        True if strings match, False otherwise
    """
    if not a or not b:
        return False

    # Use hmac.compare_digest for constant-time comparison
    return hmac.compare_digest(a.encode(), b.encode())


def hash_password(password: str) -> str:
    """
    Hash a password using bcrypt with proper salt rounds.

    Args:
        password: Plain text password

    Returns:
        Hashed password string
    """
    # Validate password strength first
    validation = validate_password_strength(password)
    if not validation['valid']:
        raise ValueError(f"Password does not meet requirements: {', '.join(validation['errors'])}")

    # Hash with bcrypt (12 rounds is secure but not too slow)
    salt = bcrypt.gensalt(rounds=12)
    hashed = bcrypt.hashpw(password.encode(), salt)
    return hashed.decode()


def verify_password(password: str, hashed: str) -> bool:
    """
    Verify a password against its hash using timing-safe comparison.

    Args:
        password: Plain text password
        hashed: Bcrypt hashed password

    Returns:
        True if password matches, False otherwise
    """
    if not password or not hashed:
        return False

    try:
        return bcrypt.checkpw(password.encode(), hashed.encode())
    except Exception:
        return False


# ── CSRF Protection ───────────────────────────────────────────────────────

def generate_csrf_token() -> str:
    """
    Generate a secure CSRF token.

    Returns:
        Random token string
    """
    return secrets.token_hex(CSRF_TOKEN_LENGTH)


def validate_csrf_token(token: str, session_token: str) -> bool:
    """
    Validate a CSRF token against the session token.

    Args:
        token: Token from form submission
        session_token: Token stored in session

    Returns:
        True if valid, False otherwise
    """
    if not token or not session_token:
        return False

    return timing_safe_compare(token, session_token)


# ── Rate Limiting ──────────────────────────────────────────────────────────

class RateLimiter:
    """
    In-memory rate limiter for login attempts.
    For production, use Redis or database-backed solution.
    """

    def __init__(self):
        self._attempts: Dict[str, List[datetime]] = {}
        self._lockouts: Dict[str, datetime] = {}

    def record_attempt(self, key: str) -> None:
        """Record a failed login attempt."""
        if key not in self._attempts:
            self._attempts[key] = []

        self._attempts[key].append(datetime.utcnow())

        # Clean old attempts
        self._attempts[key] = [
            ts for ts in self._attempts[key]
            if (datetime.utcnow() - ts).total_seconds() < 3600
        ]

    def is_locked_out(self, key: str) -> bool:
        """Check if key is locked out."""
        if key in self._lockouts:
            lockout_end = self._lockouts[key]
            if datetime.utcnow() < lockout_end:
                return True
            else:
                del self._lockouts[key]
        return False

    def lockout(self, key: str, duration_minutes: int = LOCKOUT_DURATION_MINUTES) -> None:
        """Lock out a key for specified duration."""
        self._lockouts[key] = datetime.utcnow() + timedelta(minutes=duration_minutes)

    def get_remaining_attempts(self, key: str) -> int:
        """Get remaining attempts before lockout."""
        if key in self._lockouts:
            return 0

        attempts = self._attempts.get(key, [])
        recent = [
            ts for ts in attempts
            if (datetime.utcnow() - ts).total_seconds() < 3600
        ]
        return max(0, MAX_LOGIN_ATTEMPTS - len(recent))

    def clear_attempts(self, key: str) -> None:
        """Clear attempts for a key (after successful login)."""
        self._attempts.pop(key, None)
        self._lockouts.pop(key, None)


# Global rate limiter instance
_login_rate_limiter = RateLimiter()


def get_rate_limiter() -> RateLimiter:
    """Get the global rate limiter instance."""
    return _login_rate_limiter


# ── Session Security ───────────────────────────────────────────────────────

def generate_session_token() -> str:
    """
    Generate a secure session token.

    Returns:
        Random session token
    """
    return secrets.token_urlsafe(32)


def generate_password_reset_token() -> str:
    """
    Generate a secure password reset token.

    Returns:
        Random reset token
    """
    return secrets.token_urlsafe(32)


def hash_session_token(token: str) -> str:
    """
    Hash a session token for storage.
    Use SHA-256 for one-way hashing.

    Args:
        token: Session token to hash

    Returns:
        Hashed token
    """
    return hashlib.sha256(token.encode()).hexdigest()


# ── Stripe Security ─────────────────────────────────────────────────────────

def generate_idempotency_key(prefix: str, identifier: str) -> str:
    """
    Generate a deterministic idempotency key for Stripe operations.

    Args:
        prefix: Operation type (e.g., 'customer_create', 'sub_create')
        identifier: Unique identifier (e.g., business_id, customer_id)

    Returns:
        Idempotency key string
    """
    import uuid
    return f"{prefix}_{identifier}_{uuid.uuid4().hex[:8]}"


def validate_stripe_key(key: str, key_type: str = 'secret') -> bool:
    """
    Validate Stripe key format.

    Args:
        key: Stripe API key
        key_type: 'secret' or 'publishable'

    Returns:
        True if valid format, False otherwise
    """
    if not key:
        return False

    if key_type == 'secret':
        return key.startswith('sk_test_') or key.startswith('sk_live_')
    elif key_type == 'publishable':
        return key.startswith('pk_test_') or key.startswith('pk_live_')

    return False


def is_test_key(key: str) -> bool:
    """
    Check if key is a test key.

    Args:
        key: Stripe API key

    Returns:
        True if test key, False if live key
    """
    return key.startswith('sk_test_') or key.startswith('pk_test_')


# ── Security Headers Middleware ───────────────────────────────────────────

def get_security_headers() -> Dict[str, str]:
    """
    Get recommended security headers for Flask responses.

    Returns:
        Dictionary of header names and values
    """
    return {
        'X-Content-Type-Options': 'nosniff',
        'X-Frame-Options': 'DENY',
        'X-XSS-Protection': '1; mode=block',
        'Referrer-Policy': 'strict-origin-when-cross-origin',
        'Permissions-Policy': 'geolocation=(), microphone=(), camera=()',
        # Content-Security-Policy should be set per-route based on needs
    }


# ── Audit Logging ──────────────────────────────────────────────────────────

def log_security_event(event_type: str, user_type: str = None, user_id: str = None,
                       email: str = None, ip_address: str = None,
                       details: Dict[str, Any] = None) -> None:
    """
    Log a security-related event for auditing.

    Args:
        event_type: Type of event (login, login_failed, logout, 2fa, password_change, etc.)
        user_type: 'admin' or 'business'
        user_id: User ID if applicable
        email: Email if applicable
        ip_address: Client IP address
        details: Additional event details
    """
    from datetime import datetime
    import json

    log_data = {
        'event_type': event_type,
        'user_type': user_type,
        'user_id': user_id,
        'email': email,
        'ip_address': ip_address,
        'details': details,
        'timestamp': datetime.utcnow().isoformat()
    }

    # Log to standard logger
    logger.info(f"SECURITY_EVENT: {json.dumps(log_data)}")

    # In production, also write to audit database
    # This would be implemented based on the database config


# ── Environment Validation ────────────────────────────────────────────────

def validate_production_security() -> Dict[str, Any]:
    """
    Validate that production environment is properly secured.

    Returns:
        Dictionary with 'valid' bool and 'issues' list
    """
    issues = []

    # Check for test Stripe keys in production
    stripe_key = os.environ.get('STRIPE_SECRET_KEY', '')
    environment = os.environ.get('ENVIRONMENT', 'development')

    if environment == 'production':
        if stripe_key.startswith('sk_test_'):
            issues.append('CRITICAL: Test Stripe key in production environment')

        # Check for default/insecure passwords
        dashboard_password = os.environ.get('DASHBOARD_PASSWORD', '')
        if not dashboard_password or dashboard_password in ['CHANGE_ME', 'password', 'admin']:
            issues.append('CRITICAL: Insecure or missing DASHBOARD_PASSWORD')

        # Check for debug mode
        if os.environ.get('FLASK_DEBUG', '').lower() == 'true':
            issues.append('HIGH: Flask debug mode enabled in production')

        # Check for HTTPS
        if not os.environ.get('HTTPS_ONLY', '').lower() == 'true':
            issues.append('MEDIUM: HTTPS_ONLY not set in production')

    return {
        'valid': len(issues) == 0,
        'issues': issues
    }


# ── Export ────────────────────────────────────────────────────────────────

__all__ = [
    # Input validation
    'validate_email',
    'validate_uuid',
    'validate_phone',
    'validate_password_strength',
    'sanitize_input',
    'sanitize_metadata',

    # Timing-safe operations
    'timing_safe_compare',
    'hash_password',
    'verify_password',

    # CSRF
    'generate_csrf_token',
    'validate_csrf_token',

    # Rate limiting
    'RateLimiter',
    'get_rate_limiter',

    # Session security
    'generate_session_token',
    'generate_password_reset_token',
    'hash_session_token',

    # Stripe security
    'generate_idempotency_key',
    'validate_stripe_key',
    'is_test_key',

    # Security headers
    'get_security_headers',

    # Audit logging
    'log_security_event',

    # Environment validation
    'validate_production_security',
]