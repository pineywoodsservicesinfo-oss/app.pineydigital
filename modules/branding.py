"""
branding.py - White-label Branding Management for FieldPulse
Handles custom branding, domains, and email templates for multi-tenant SaaS
"""

import os
import logging
from typing import Optional, Dict, Any
from dataclasses import dataclass
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

# ── Branding Configuration ──────────────────────────────────────────────

@dataclass
class BusinessBranding:
    """Branding configuration for a business."""
    business_id: str
    name: str
    domain: Optional[str] = None
    logo_url: Optional[str] = None
    primary_color: str = '#4F46E5'  # Default indigo
    secondary_color: str = '#818CF8'
    timezone: str = 'America/Chicago'
    currency: str = 'USD'
    email: Optional[str] = None
    phone: Optional[str] = None
    website: Optional[str] = None

    # Email branding
    email_from_name: Optional[str] = None
    email_from_address: Optional[str] = None

    # Custom domain settings
    custom_domain_verified: bool = False

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            'business_id': self.business_id,
            'name': self.name,
            'domain': self.domain,
            'logo_url': self.logo_url,
            'primary_color': self.primary_color,
            'secondary_color': self.secondary_color,
            'timezone': self.timezone,
            'currency': self.currency,
            'email': self.email,
            'phone': self.phone,
            'website': self.website,
            'email_from_name': self.email_from_name or self.name,
            'email_from_address': self.email_from_address,
            'custom_domain_verified': self.custom_domain_verified,
        }

    @property
    def display_name(self) -> str:
        """Get display name for branding."""
        return self.name or 'FieldPulse'

    @property
    def email_sender(self) -> str:
        """Get email sender display."""
        return self.email_from_name or self.name

    def get_colors(self) -> Dict[str, str]:
        """Get brand colors with CSS variables."""
        return {
            'primary': self.primary_color,
            'secondary': self.secondary_color,
            'primary_light': self._lighten_color(self.primary_color, 0.1),
            'primary_dark': self._darken_color(self.primary_color, 0.1),
        }

    def _lighten_color(self, hex_color: str, amount: float) -> str:
        """Lighten a hex color."""
        # Remove # if present
        hex_color = hex_color.lstrip('#')

        # Convert to RGB
        r = int(hex_color[0:2], 16)
        g = int(hex_color[2:4], 16)
        b = int(hex_color[4:6], 16)

        # Lighten
        r = min(255, int(r + (255 - r) * amount))
        g = min(255, int(g + (255 - g) * amount))
        b = min(255, int(b + (255 - b) * amount))

        return f'#{r:02x}{g:02x}{b:02x}'

    def _darken_color(self, hex_color: str, amount: float) -> str:
        """Darken a hex color."""
        hex_color = hex_color.lstrip('#')

        r = int(hex_color[0:2], 16)
        g = int(hex_color[2:4], 16)
        b = int(hex_color[4:6], 16)

        r = max(0, int(r * (1 - amount)))
        g = max(0, int(g * (1 - amount)))
        b = max(0, int(b * (1 - amount)))

        return f'#{r:02x}{g:02x}{b:02x}'


# ── Default Branding ────────────────────────────────────────────────────

DEFAULT_BRANDING = BusinessBranding(
    business_id='default',
    name='FieldPulse',
    primary_color='#4F46E5',
    secondary_color='#818CF8',
    timezone='America/Chicago',
)


# ── Database Operations ────────────────────────────────────────────────

def get_business_branding(business_id: str) -> Optional[BusinessBranding]:
    """
    Get branding configuration for a business.

    Args:
        business_id: The business UUID

    Returns:
        BusinessBranding object or None if not found
    """
    from db_config import get_db_connection

    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        if hasattr(conn, 'row_factory'):
            # SQLite
            cursor.execute("""
                SELECT id, name, domain, logo_url, primary_color, secondary_color,
                       timezone, currency, email, phone, website
                FROM businesses
                WHERE id = ?
            """, (business_id,))
            row = cursor.fetchone()

            if row:
                return BusinessBranding(
                    business_id=row['id'],
                    name=row['name'],
                    domain=row['domain'],
                    logo_url=row['logo_url'],
                    primary_color=row['primary_color'] or '#4F46E5',
                    secondary_color=row['secondary_color'] or '#818CF8',
                    timezone=row['timezone'] or 'America/Chicago',
                    currency=row['currency'] or 'USD',
                    email=row['email'],
                    phone=row['phone'],
                    website=row['website'],
                )
        else:
            # PostgreSQL
            from psycopg2.extras import RealDictCursor
            cursor = conn.cursor(cursor_factory=RealDictCursor)
            cursor.execute("""
                SELECT id, name, domain, logo_url, primary_color, secondary_color,
                       timezone, currency, email, phone, website
                FROM businesses
                WHERE id = %s
            """, (business_id,))
            row = cursor.fetchone()

            if row:
                return BusinessBranding(
                    business_id=row['id'],
                    name=row['name'],
                    domain=row['domain'],
                    logo_url=row['logo_url'],
                    primary_color=row['primary_color'] or '#4F46E5',
                    secondary_color=row['secondary_color'] or '#818CF8',
                    timezone=row['timezone'] or 'America/Chicago',
                    currency=row['currency'] or 'USD',
                    email=row['email'],
                    phone=row['phone'],
                    website=row['website'],
                )

        return None

    finally:
        cursor.close()
        conn.close()


def update_business_branding(business_id: str, **kwargs) -> bool:
    """
    Update branding settings for a business.

    Args:
        business_id: The business UUID
        **kwargs: Branding fields to update (logo_url, primary_color, etc.)

    Returns:
        True if updated successfully
    """
    from db_config import get_db_connection

    allowed_fields = [
        'logo_url', 'primary_color', 'secondary_color',
        'timezone', 'currency', 'domain', 'email', 'phone', 'website'
    ]

    updates = {k: v for k, v in kwargs.items() if k in allowed_fields}

    if not updates:
        return False

    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        # Build update query
        set_clause = ', '.join(f"{k} = %s" for k in updates.keys())
        values = list(updates.values()) + [business_id]

        cursor.execute(f"""
            UPDATE businesses
            SET {set_clause}, updated_at = NOW()
            WHERE id = %s
        """, values)

        conn.commit()
        return cursor.rowcount > 0

    finally:
        cursor.close()
        conn.close()


def get_business_by_domain(domain: str) -> Optional[BusinessBranding]:
    """
    Get business by custom domain or subdomain.

    Args:
        domain: The domain/subdomain to look up

    Returns:
        BusinessBranding object or None
    """
    from db_config import get_db_connection

    # Extract subdomain if it's a *.fieldpulse.io domain
    if domain.endswith('.fieldpulse.io'):
        subdomain = domain.replace('.fieldpulse.io', '')
        # Look up by subdomain name (business slug)
        # This would require a 'slug' column in businesses table
        # For now, we'll just look up by domain
        pass

    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        # Look up by custom domain
        from psycopg2.extras import RealDictCursor
        cursor = conn.cursor(cursor_factory=RealDictCursor)

        cursor.execute("""
            SELECT id, name, domain, logo_url, primary_color, secondary_color,
                   timezone, currency, email, phone, website
            FROM businesses
            WHERE domain = %s
        """, (domain,))

        row = cursor.fetchone()

        if row:
            return BusinessBranding(
                business_id=row['id'],
                name=row['name'],
                domain=row['domain'],
                logo_url=row['logo_url'],
                primary_color=row['primary_color'] or '#4F46E5',
                secondary_color=row['secondary_color'] or '#818CF8',
                timezone=row['timezone'] or 'America/Chicago',
                currency=row['currency'] or 'USD',
                email=row['email'],
                phone=row['phone'],
                website=row['website'],
            )

        return None

    finally:
        cursor.close()
        conn.close()


# ── Email Template Rendering ─────────────────────────────────────────────

def render_email_template(template_name: str, branding: BusinessBranding,
                          context: Dict[str, Any] = None) -> str:
    """
    Render an email template with branding.

    Args:
        template_name: Name of template (e.g., 'welcome', 'booking_confirmation')
        branding: BusinessBranding object
        context: Additional template variables

    Returns:
        Rendered HTML email
    """
    context = context or {}

    # Base template context
    template_context = {
        'business_name': branding.display_name,
        'logo_url': branding.logo_url,
        'primary_color': branding.primary_color,
        'secondary_color': branding.secondary_color,
        'email_sender': branding.email_sender,
        'website_url': branding.website,
        'support_email': branding.email_from_address or f'support@{branding.domain or "fieldpulse.io"}',
        **context
    }

    # Get template HTML
    template_html = get_template_html(template_name)

    # Replace placeholders
    for key, value in template_context.items():
        placeholder = f'{{{{{key}}}}}'
        template_html = template_html.replace(placeholder, str(value or ''))

    return template_html


def get_template_html(template_name: str) -> str:
    """
    Get the HTML for an email template.

    Args:
        template_name: Name of the template

    Returns:
        HTML template string
    """
    templates = {
        'base': '''
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="utf-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <title>{{subject}}</title>
        </head>
        <body style="margin: 0; padding: 0; background-color: #f3f4f6; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif;">
            <table width="100%" cellpadding="0" cellspacing="0" style="background-color: #f3f4f6; padding: 40px 0;">
                <tr>
                    <td align="center">
                        <table width="600" cellpadding="0" cellspacing="0" style="background-color: #ffffff; border-radius: 8px; overflow: hidden;">
                            <!-- Header -->
                            <tr>
                                <td style="background-color: {{primary_color}}; padding: 30px; text-align: center;">
                                    {{#if logo_url}}
                                    <img src="{{logo_url}}" alt="{{business_name}}" style="max-width: 200px; height: auto;">
                                    {{else}}
                                    <h1 style="color: #ffffff; margin: 0; font-size: 24px;">{{business_name}}</h1>
                                    {{/if}}
                                </td>
                            </tr>
                            <!-- Content -->
                            <tr>
                                <td style="padding: 40px;">
                                    {{content}}
                                </td>
                            </tr>
                            <!-- Footer -->
                            <tr>
                                <td style="background-color: #f9fafb; padding: 20px; text-align: center; border-top: 1px solid #e5e7eb;">
                                    <p style="margin: 0; color: #6b7280; font-size: 14px;">
                                        © {{year}} {{business_name}}. All rights reserved.
                                    </p>
                                    <p style="margin: 10px 0 0 0; color: #9ca3af; font-size: 12px;">
                                        {{address}}
                                    </p>
                                </td>
                            </tr>
                        </table>
                    </td>
                </tr>
            </table>
        </body>
        </html>
        ''',

        'welcome': '''
        <h2 style="color: #111827; margin: 0 0 20px 0;">Welcome to {{business_name}}!</h2>
        <p style="color: #374151; margin: 0 0 20px 0; line-height: 1.6;">
            Thanks for signing up! Your account is ready to use.
        </p>
        <p style="color: #374151; margin: 0 0 30px 0; line-height: 1.6;">
            Here's what you can do next:
        </p>
        <ul style="color: #374151; margin: 0 0 30px 0; padding-left: 20px;">
            <li style="margin-bottom: 10px;">Complete your business profile</li>
            <li style="margin-bottom: 10px;">Add your services and staff</li>
            <li style="margin-bottom: 10px;">Start accepting bookings</li>
        </ul>
        <a href="{{login_url}}" style="display: inline-block; background-color: {{primary_color}}; color: #ffffff; padding: 12px 24px; text-decoration: none; border-radius: 6px; font-weight: 600;">
            Get Started
        </a>
        ''',

        'booking_confirmation': '''
        <h2 style="color: #111827; margin: 0 0 20px 0;">Booking Confirmed!</h2>
        <p style="color: #374151; margin: 0 0 20px 0; line-height: 1.6;">
            Hi {{customer_name}},
        </p>
        <p style="color: #374151; margin: 0 0 30px 0; line-height: 1.6;">
            Your appointment with {{business_name}} is confirmed for:
        </p>
        <table style="width: 100%; border-collapse: collapse; margin-bottom: 30px;">
            <tr>
                <td style="padding: 12px; background-color: #f9fafb; border-bottom: 1px solid #e5e7eb;"><strong>Service:</strong></td>
                <td style="padding: 12px; background-color: #f9fafb; border-bottom: 1px solid #e5e7eb;">{{service_name}}</td>
            </tr>
            <tr>
                <td style="padding: 12px; background-color: #f9fafb; border-bottom: 1px solid #e5e7eb;"><strong>Date:</strong></td>
                <td style="padding: 12px; background-color: #f9fafb; border-bottom: 1px solid #e5e7eb;">{{booking_date}}</td>
            </tr>
            <tr>
                <td style="padding: 12px; background-color: #f9fafb; border-bottom: 1px solid #e5e7eb;"><strong>Time:</strong></td>
                <td style="padding: 12px; background-color: #f9fafb; border-bottom: 1px solid #e5e7eb;">{{booking_time}}</td>
            </tr>
            <tr>
                <td style="padding: 12px; background-color: #f9fafb;"><strong>Location:</strong></td>
                <td style="padding: 12px; background-color: #f9fafb;">{{location}}</td>
            </tr>
        </table>
        <p style="color: #374151; margin: 0 0 30px 0; line-height: 1.6;">
            Need to reschedule? <a href="{{reschedule_url}}" style="color: {{primary_color}};">Click here</a>
        </p>
        ''',

        'booking_reminder': '''
        <h2 style="color: #111827; margin: 0 0 20px 0;">Upcoming Appointment Reminder</h2>
        <p style="color: #374151; margin: 0 0 20px 0; line-height: 1.6;">
            Hi {{customer_name}},
        </p>
        <p style="color: #374151; margin: 0 0 30px 0; line-height: 1.6;">
            This is a reminder about your upcoming appointment with {{business_name}}:
        </p>
        <table style="width: 100%; border-collapse: collapse; margin-bottom: 30px;">
            <tr>
                <td style="padding: 12px; background-color: #f9fafb; border-bottom: 1px solid #e5e7eb;"><strong>Service:</strong></td>
                <td style="padding: 12px; background-color: #f9fafb; border-bottom: 1px solid #e5e7eb;">{{service_name}}</td>
            </tr>
            <tr>
                <td style="padding: 12px; background-color: #f9fafb; border-bottom: 1px solid #e5e7eb;"><strong>Date:</strong></td>
                <td style="padding: 12px; background-color: #f9fafb; border-bottom: 1px solid #e5e7eb;">{{booking_date}}</td>
            </tr>
            <tr>
                <td style="padding: 12px; background-color: #f9fafb;"><strong>Time:</strong></td>
                <td style="padding: 12px; background-color: #f9fafb;">{{booking_time}}</td>
            </tr>
        </table>
        <p style="color: #374151; margin: 0 0 20px 0; line-height: 1.6;">
            See you soon!
        </p>
        ''',

        'invoice': '''
        <h2 style="color: #111827; margin: 0 0 20px 0;">Invoice #{{invoice_number}}</h2>
        <p style="color: #374151; margin: 0 0 20px 0; line-height: 1.6;">
            Hi {{customer_name}},
        </p>
        <p style="color: #374151; margin: 0 0 30px 0; line-height: 1.6;">
            Please find your invoice from {{business_name}} below:
        </p>
        <table style="width: 100%; border-collapse: collapse; margin-bottom: 30px;">
            <thead>
                <tr style="background-color: {{primary_color}}; color: #ffffff;">
                    <th style="padding: 12px; text-align: left;">Description</th>
                    <th style="padding: 12px; text-align: right;">Amount</th>
                </tr>
            </thead>
            <tbody>
                {{line_items}}
            </tbody>
            <tfoot>
                <tr style="background-color: #f9fafb;">
                    <td style="padding: 12px;"><strong>Total</strong></td>
                    <td style="padding: 12px; text-align: right;"><strong>{{total}}</strong></td>
                </tr>
            </tfoot>
        </table>
        <a href="{{payment_url}}" style="display: inline-block; background-color: {{primary_color}}; color: #ffffff; padding: 12px 24px; text-decoration: none; border-radius: 6px; font-weight: 600;">
            Pay Now
        </a>
        ''',

        'password_reset': '''
        <h2 style="color: #111827; margin: 0 0 20px 0;">Reset Your Password</h2>
        <p style="color: #374151; margin: 0 0 20px 0; line-height: 1.6;">
            Hi {{user_name}},
        </p>
        <p style="color: #374151; margin: 0 0 30px 0; line-height: 1.6;">
            We received a request to reset your password for your {{business_name}} account.
        </p>
        <a href="{{reset_url}}" style="display: inline-block; background-color: {{primary_color}}; color: #ffffff; padding: 12px 24px; text-decoration: none; border-radius: 6px; font-weight: 600;">
            Reset Password
        </a>
        <p style="color: #6b7280; margin: 30px 0 0 0; font-size: 14px;">
            This link will expire in 24 hours. If you didn't request this, you can safely ignore this email.
        </p>
        ''',

        'subscription_confirmation': '''
        <h2 style="color: #111827; margin: 0 0 20px 0;">Welcome to {{business_name}}!</h2>
        <p style="color: #374151; margin: 0 0 20px 0; line-height: 1.6;">
            Hi {{customer_name}},
        </p>
        <p style="color: #374151; margin: 0 0 30px 0; line-height: 1.6;">
            Your subscription has been confirmed. Here are your plan details:
        </p>
        <table style="width: 100%; border-collapse: collapse; margin-bottom: 30px;">
            <tr>
                <td style="padding: 12px; background-color: #f9fafb; border-bottom: 1px solid #e5e7eb;"><strong>Plan:</strong></td>
                <td style="padding: 12px; background-color: #f9fafb; border-bottom: 1px solid #e5e7eb;">{{plan_name}}</td>
            </tr>
            <tr>
                <td style="padding: 12px; background-color: #f9fafb; border-bottom: 1px solid #e5e7eb;"><strong>Price:</strong></td>
                <td style="padding: 12px; background-color: #f9fafb; border-bottom: 1px solid #e5e7eb;">{{plan_price}}/month</td>
            </tr>
            <tr>
                <td style="padding: 12px; background-color: #f9fafb;"><strong>Next billing:</strong></td>
                <td style="padding: 12px; background-color: #f9fafb;">{{next_billing_date}}</td>
            </tr>
        </table>
        <a href="{{login_url}}" style="display: inline-block; background-color: {{primary_color}}; color: #ffffff; padding: 12px 24px; text-decoration: none; border-radius: 6px; font-weight: 600;">
            Go to Dashboard
        </a>
        ''',
    }

    return templates.get(template_name, templates.get('base', ''))


# ── CSS Variables for Branding ───────────────────────────────────────────

def get_branding_css(branding: BusinessBranding) -> str:
    """
    Generate CSS variables for brand colors.

    Args:
        branding: BusinessBranding object

    Returns:
        CSS string with brand variables
    """
    colors = branding.get_colors()

    return f'''
:root {{
    --brand-primary: {colors['primary']};
    --brand-primary-light: {colors['primary_light']};
    --brand-primary-dark: {colors['primary_dark']};
    --brand-secondary: {colors['secondary']};
    --brand-name: "{branding.display_name}";
}}
'''


# ── Flask Integration ────────────────────────────────────────────────────

def get_branding_for_request(domain: str = None, business_id: str = None) -> BusinessBranding:
    """
    Get branding for a Flask request.

    Args:
        domain: Request domain (from Host header)
        business_id: Explicit business ID (takes priority)

    Returns:
        BusinessBranding object (or default if not found)
    """
    if business_id:
        branding = get_business_branding(business_id)
        if branding:
            return branding

    if domain:
        branding = get_business_by_domain(domain)
        if branding:
            return branding

    return DEFAULT_BRANDING


def init_branding_middleware(app):
    """
    Initialize Flask middleware for branding.

    Adds `g.branding` to each request based on domain.

    Usage:
        from flask import g
        branding = g.branding
    """
    from flask import g, request

    @app.before_request
    def set_branding():
        # Get domain from Host header
        host = request.headers.get('Host', '')

        # Remove port if present
        domain = host.split(':')[0] if ':' in host else host

        # Get business_id from session if available
        business_id = getattr(g, 'business_id', None)

        # Get branding
        g.branding = get_branding_for_request(domain=domain, business_id=business_id)


# ── Export ──────────────────────────────────────────────────────────────

__all__ = [
    'BusinessBranding',
    'DEFAULT_BRANDING',
    'get_business_branding',
    'update_business_branding',
    'get_business_by_domain',
    'render_email_template',
    'get_branding_css',
    'get_branding_for_request',
    'init_branding_middleware',
]