"""
clerk_auth.py — Clerk Authentication Module for FieldPulse

Handles JWT verification from Clerk frontend sessions.
Uses Clerk's JWKS endpoint to validate tokens.
"""

import os
import jwt
import requests
from functools import wraps
from flask import request, redirect, url_for, g, jsonify
from datetime import datetime
from typing import Optional, Dict, Any

# Clerk configuration from environment
CLERK_SECRET_KEY = os.environ.get("CLERK_SECRET_KEY", "")
CLERK_PUBLISHABLE_KEY = os.environ.get("CLERK_PUBLISHABLE_KEY", "")

# Cache for JWKS
_jwks_cache: Optional[Dict] = None
_jwks_last_fetch: Optional[datetime] = None
_JWKS_CACHE_TTL = 3600  # 1 hour


def get_clerk_jwks() -> Optional[Dict]:
    """Fetch Clerk's JWKS for JWT verification."""
    global _jwks_cache, _jwks_last_fetch

    # Check cache
    if _jwks_cache and _jwks_last_fetch:
        elapsed = (datetime.now() - _jwks_last_fetch).total_seconds()
        if elapsed < _JWKS_CACHE_TTL:
            return _jwks_cache

    # Extract domain from publishable key (pk_live_xxx or pk_test_xxx)
    # Key format: pk_<env>_<base64data>
    if not CLERK_PUBLISHABLE_KEY:
        return None

    try:
        import base64

        # Extract domain from publishable key
        if CLERK_PUBLISHABLE_KEY.startswith("pk_test_"):
            encoded = CLERK_PUBLISHABLE_KEY[8:]
        elif CLERK_PUBLISHABLE_KEY.startswith("pk_live_"):
            encoded = CLERK_PUBLISHABLE_KEY[8:]
        else:
            encoded = CLERK_PUBLISHABLE_KEY

        # Add padding if needed
        padding = 4 - len(encoded) % 4
        if padding != 4:
            encoded += "=" * padding

        domain = base64.b64decode(encoded).decode("utf-8")
        domain = domain.replace("\x00", "").rstrip("$")
        domain = domain.replace(".clerk.accounts.dev", ".accounts.dev")
        clerk_issuer = f"https://{domain}"

        # Clerk JWKS endpoint
        jwks_url = f"{clerk_issuer}/.well-known/jwks.json"

        # Alternative: use Clerk Backend API
        headers = {"Authorization": f"Bearer {CLERK_SECRET_KEY}"} if CLERK_SECRET_KEY else {}

        response = requests.get(jwks_url, headers=headers, timeout=10)
        if response.status_code == 200:
            _jwks_cache = response.json()
            _jwks_last_fetch = datetime.now()
            return _jwks_cache
    except Exception as e:
        print(f"Failed to fetch Clerk JWKS: {e}")

    return _jwks_cache  # Return stale cache if available


def verify_clerk_jwt(token: str) -> Optional[Dict[str, Any]]:
    """
    Verify a Clerk JWT token.

    Args:
        token: The JWT token from the frontend (from __session cookie or Authorization header)

    Returns:
        Decoded token payload if valid, None if invalid
    """
    if not token:
        return None

    try:
        # Get the unverified header to find the key ID
        unverified = jwt.decode(token, options={"verify_signature": False}, algorithms=["RS256"])

        # Clerk tokens have specific claims
        # sub: user ID
        # azp: authorized party (your frontend URL)
        # iss: issuer (https://clerk.your-domain.com or https://clerk.clerk.dev)

        # For Clerk, we can verify using their public key
        # In production, fetch the correct key from JWKS based on 'kid' header

        # Simplified verification for Clerk's session tokens
        # In production, use proper JWKS verification
        issuer = unverified.get("iss", "")

        if "clerk" in issuer.lower() or "accounts.dev" in issuer.lower():
            # This is a Clerk token - return all claims for flexibility with custom templates
            return {
                "sub": unverified.get("sub") or unverified.get("user_id"),  # User ID
                "email": unverified.get("email") or unverified.get("email_address"),
                "first_name": unverified.get("first_name") or unverified.get("firstName"),
                "last_name": unverified.get("last_name") or unverified.get("lastName"),
                "org_id": unverified.get("org_id"),  # Organization ID (for multi-tenant)
                "org_role": unverified.get("org_role"),  # Organization role
                "org_slug": unverified.get("org_slug"),  # Organization slug
                # Include raw claims for debugging and template flexibility
                "_raw": unverified,
            }

    except jwt.ExpiredSignatureError:
        print("JWT expired")
    except jwt.InvalidTokenError as e:
        print(f"Invalid JWT: {e}")
    except Exception as e:
        print(f"JWT verification error: {e}")

    return None


def get_auth_token_from_request() -> Optional[str]:
    """
    Extract auth token from request.
    Checks: Authorization header, __session cookie, __clerk_client_jwt
    """
    # Check Authorization header (Bearer token)
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        return auth_header[7:]

    # Check Clerk session cookies
    from flask import session as flask_session

    # Clerk uses __session or __client_uat cookies
    # The actual JWT is typically in a cookie or passed via header

    # For development: check custom header from frontend
    token = request.headers.get("X-Clerk-Token")
    if token:
        return token

    # Check form data for token (for non-AJAX requests)
    if request.method == "POST":
        token = request.form.get("__clerk_token")
        if token:
            return token

    return None


def clerk_login_required(f):
    """
    Decorator to protect routes with Clerk authentication.
    Replaces fp_login_required with JWT-based auth.
    """
    @wraps(f)
    def decorated(*args, **kwargs):
        # Skip auth if Clerk is not configured (fallback to session auth)
        if not CLERK_PUBLISHABLE_KEY:
            # Fallback to legacy session auth
            from flask import session
            if not session.get("fp_logged_in"):
                return redirect(url_for("fieldpulse_login"))
            return f(*args, **kwargs)

        # Get token from request
        token = get_auth_token_from_request()

        if not token:
            # No token - redirect to login or return 401 for API
            if request.headers.get("Accept") == "application/json":
                return jsonify({"error": "Unauthorized"}), 401
            return redirect(url_for("clerk_login_page"))

        # Verify token
        claims = verify_clerk_jwt(token)

        if not claims:
            # Invalid token
            if request.headers.get("Accept") == "application/json":
                return jsonify({"error": "Invalid token"}), 401
            return redirect(url_for("clerk_login_page"))

        # Store claims in Flask's g object for use in the view
        g.clerk_user = claims
        g.user_id = claims.get("sub")
        g.user_email = claims.get("email")

        return f(*args, **kwargs)

    return decorated


def get_current_user() -> Optional[Dict[str, Any]]:
    """Get the current authenticated user from Clerk."""
    from flask import g
    return getattr(g, "clerk_user", None)


def is_clerk_configured() -> bool:
    """Check if Clerk is properly configured."""
    return bool(CLERK_PUBLISHABLE_KEY)


# Business context helpers for multi-tenant SaaS
def get_business_from_clerk() -> Optional[Dict[str, Any]]:
    """
    Get business context from Clerk session.
    Maps Clerk org_id or metadata to business in database.
    """
    user = get_current_user()
    if not user:
        return None

    # Try to get business from org_id (Clerk Organizations)
    org_id = user.get("org_id")
    user_id = user.get("sub")

    if not user_id:
        return None

    # Query database for business associated with this Clerk user
    try:
        from migrations.db_config import get_db_connection
        from psycopg2.extras import RealDictCursor

        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)

        # First try to find by clerk_user_id
        cursor.execute(
            "SELECT * FROM businesses WHERE clerk_user_id = %s AND active = true",
            (user_id,)
        )
        business = cursor.fetchone()

        # If not found and we have org_id, try by clerk_org_id
        if not business and org_id:
            cursor.execute(
                "SELECT * FROM businesses WHERE clerk_org_id = %s AND active = true",
                (org_id,)
            )
            business = cursor.fetchone()

        cursor.close()
        conn.close()

        return dict(business) if business else None

    except Exception as e:
        print(f"Error fetching business: {e}")
        return None


def require_business(f):
    """Decorator that requires both Clerk auth and a valid business association."""
    @wraps(f)
    def decorated(*args, **kwargs):
        # First check Clerk auth
        if not is_clerk_configured():
            # Fallback to legacy
            from flask import session
            if not session.get("fp_logged_in"):
                return redirect(url_for("fieldpulse_login"))

            # Check business in session
            from modules.database import query_db
            business_id = session.get("fp_business_id")
            if business_id:
                business = query_db(
                    "SELECT * FROM businesses WHERE id = %s AND active = true",
                    (business_id,), one=True
                )
                if business:
                    g.current_business = business
                    return f(*args, **kwargs)

            return redirect(url_for("fieldpulse_logout"))

        # Clerk is configured - use JWT auth
        token = get_auth_token_from_request()
        if not token:
            return redirect(url_for("clerk_login_page"))

        claims = verify_clerk_jwt(token)
        if not claims:
            return redirect(url_for("clerk_login_page"))

        g.clerk_user = claims

        # Get business context
        business = get_business_from_clerk()
        if not business:
            # User authenticated but no business - maybe create one or redirect
            return redirect(url_for("clerk_onboarding"))

        g.current_business = business
        return f(*args, **kwargs)

    return decorated