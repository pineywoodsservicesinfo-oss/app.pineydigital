"""
whitelabel_routes.py - White-label Routes for FieldPulse
Customer-facing pages with business branding
"""

from flask import Blueprint, render_template, g, request, redirect, url_for, jsonify
from typing import Optional

# Create blueprint
whitelabel_bp = Blueprint('whitelabel', __name__, url_prefix='')


def get_branding():
    """Get branding for current request."""
    from branding import get_branding_for_request
    business_id = getattr(g, 'business_id', None)
    domain = request.headers.get('Host', '').split(':')[0]
    return get_branding_for_request(domain=domain, business_id=business_id)


# ── Public Booking Page ──────────────────────────────────────────────────

@whitelabel_bp.route('/book/<business_slug>')
def booking_page(business_slug: str):
    """
    Public booking page for a business.
    Custom branded with business logo, colors, etc.
    """
    branding = get_branding()

    # Get business by slug
    from db_config import get_db_connection
    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        # Look up business by slug or ID
        cursor.execute("""
            SELECT id, name, logo_url, primary_color, secondary_color,
                   address, city, phone, email
            FROM businesses
            WHERE id = %s OR slug = %s
        """, (business_slug, business_slug))

        business = cursor.fetchone()

        if not business:
            return render_template('404.html', branding=branding), 404

        # Get services
        cursor.execute("""
            SELECT id, name, description, duration_min, price
            FROM booking_services
            WHERE business_id = %s AND active = TRUE
            ORDER BY name
        """, (business['id'],))

        services = cursor.fetchall()

        # Get staff
        cursor.execute("""
            SELECT id, name, role
            FROM booking_staff
            WHERE business_id = %s AND active = TRUE
            ORDER BY name
        """, (business['id'],))

        staff = cursor.fetchall()

        # Update branding with business info
        from branding import BusinessBranding
        branding = BusinessBranding(
            business_id=business['id'],
            name=business['name'],
            logo_url=business['logo_url'],
            primary_color=business['primary_color'] or '#4F46E5',
            secondary_color=business['secondary_color'] or '#818CF8',
        )

        return render_template('whitelabel/booking.html',
            branding=branding,
            business=business,
            services=services,
            staff=staff)

    finally:
        cursor.close()
        conn.close()


@whitelabel_bp.route('/book/<business_slug>/availability')
def get_availability(business_slug: str):
    """API endpoint to get available time slots."""
    from flask import request
    import json
    from datetime import datetime

    service_id = request.args.get('service_id')
    staff_id = request.args.get('staff_id')
    date = request.args.get('date')

    if not all([service_id, staff_id, date]):
        return jsonify({'error': 'Missing parameters'}), 400

    from db_config import get_db_connection
    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        # Get available slots (simplified - would use bookings_db logic)
        # This is a placeholder - real implementation would check existing bookings
        day_of_week = datetime.strptime(date, '%Y-%m-%d').weekday()

        # Get staff availability for this day
        cursor.execute("""
            SELECT start_time, end_time
            FROM staff_availability
            WHERE staff_id = %s AND day_of_week = %s AND is_working = TRUE
        """, (staff_id, day_of_week))

        availability = cursor.fetchone()

        if not availability:
            return jsonify({'slots': []})

        # Generate time slots (simplified)
        slots = []
        # Would need more complex logic for real implementation
        # including checking existing bookings

        return jsonify({
            'slots': slots,
            'availability': dict(availability) if availability else None
        })

    finally:
        cursor.close()
        conn.close()


@whitelabel_bp.route('/book/<business_slug>/submit', methods=['POST'])
def submit_booking(business_slug: str):
    """Handle booking form submission."""
    from flask import request
    import json

    data = request.get_json()

    # Validate required fields
    required = ['customer_name', 'customer_phone', 'service_id', 'booking_date', 'booking_time']
    for field in required:
        if not data.get(field):
            return jsonify({'error': f'Missing required field: {field}'}), 400

    from db_config import get_db_connection
    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        # Get business
        cursor.execute("""
            SELECT id FROM businesses
            WHERE id = %s OR slug = %s
        """, (business_slug, business_slug))

        business = cursor.fetchone()
        if not business:
            return jsonify({'error': 'Business not found'}), 404

        # Create booking
        booking_id = create_booking(
            business_id=business['id'],
            customer_name=data['customer_name'],
            customer_phone=data['customer_phone'],
            customer_email=data.get('customer_email'),
            service_id=data['service_id'],
            staff_id=data.get('staff_id'),
            booking_date=data['booking_date'],
            booking_time=data['booking_time'],
            notes=data.get('notes'),
            source='web'
        )

        return jsonify({
            'success': True,
            'booking_id': booking_id,
            'message': 'Booking confirmed!'
        })

    except Exception as e:
        return jsonify({'error': str(e)}), 500

    finally:
        cursor.close()
        conn.close()


# ── Customer Portal ──────────────────────────────────────────────────────

@whitelabel_bp.route('/portal/<business_slug>')
def customer_portal(business_slug: str):
    """
    Customer self-service portal.
    View bookings, manage appointments, see history.
    """
    branding = get_branding()

    # Would need authentication here in production
    # For now, just show the portal page

    return render_template('whitelabel/portal.html',
        branding=branding,
        business_slug=business_slug)


@whitelabel_bp.route('/portal/<business_slug>/bookings')
def customer_bookings(business_slug: str):
    """API endpoint for customer's bookings."""
    # Would need customer authentication
    # Placeholder for now
    return jsonify({'bookings': []})


# ── Loyalty Portal ────────────────────────────────────────────────────────

@whitelabel_bp.route('/loyalty/<business_slug>')
def loyalty_portal(business_slug: str):
    """
    Customer loyalty program portal.
    View punch cards, rewards, history.
    """
    branding = get_branding()

    return render_template('whitelabel/loyalty.html',
        branding=branding,
        business_slug=business_slug)


# ── Review Collection ────────────────────────────────────────────────────

@whitelabel_bp.route('/review/<request_id>')
def review_page(request_id: str):
    """
    Review collection page.
    Sent via SMS after service completion.
    """
    from db_config import get_db_connection
    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        # Get review request
        cursor.execute("""
            SELECT rr.*, b.name as business_name, b.logo_url, b.primary_color, b.secondary_color
            FROM review_requests rr
            JOIN businesses b ON rr.business_id = b.id
            WHERE rr.id = %s
        """, (request_id,))

        review_request = cursor.fetchone()

        if not review_request:
            return render_template('404.html'), 404

        # Mark as opened
        cursor.execute("""
            UPDATE review_requests
            SET opened_at = NOW(), status = 'opened'
            WHERE id = %s AND opened_at IS NULL
        """, (request_id,))
        conn.commit()

        from branding import BusinessBranding
        branding = BusinessBranding(
            business_id=review_request['business_id'],
            name=review_request['business_name'],
            logo_url=review_request['logo_url'],
            primary_color=review_request['primary_color'] or '#4F46E5',
            secondary_color=review_request['secondary_color'] or '#818CF8',
        )

        return render_template('whitelabel/review.html',
            branding=branding,
            request_id=request_id,
            business_name=review_request['business_name'])

    finally:
        cursor.close()
        conn.close()


@whitelabel_bp.route('/review/<request_id>/submit', methods=['POST'])
def submit_review(request_id: str):
    """Handle review submission."""
    from flask import request
    import json

    data = request.get_json()

    stars = data.get('stars')
    feedback = data.get('feedback', '')
    is_public = data.get('is_public', False)

    if not stars or stars < 1 or stars > 5:
        return jsonify({'error': 'Invalid rating'}), 400

    from db_config import get_db_connection
    from reviews_db import submit_rating

    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        # Submit rating
        rating_id = submit_rating(
            request_id=request_id,
            stars=stars,
            feedback=feedback,
            is_public=is_public,
            source='link'
        )

        return jsonify({
            'success': True,
            'rating_id': rating_id,
            'message': 'Thank you for your review!'
        })

    except Exception as e:
        return jsonify({'error': str(e)}), 500

    finally:
        cursor.close()
        conn.close()


# ── Referral Links ───────────────────────────────────────────────────────

@whitelabel_bp.route('/ref/<code>')
def referral_link(code: str):
    """
    Referral link landing page.
    Track click and redirect to signup.
    """
    from db_config import get_db_connection
    from referrals_db import track_referral_click

    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        # Get referral code
        cursor.execute("""
            SELECT rc.*, b.name as business_name, b.logo_url, b.primary_color
            FROM referral_codes rc
            JOIN businesses b ON rc.business_id = b.id
            WHERE rc.code = %s
        """, (code.upper(),))

        referral_code = cursor.fetchone()

        if not referral_code:
            return render_template('404.html'), 404

        # Track click
        track_referral_click(
            code=code,
            ip_address=request.remote_addr,
            user_agent=request.headers.get('User-Agent', '')
        )

        from branding import BusinessBranding
        branding = BusinessBranding(
            business_id=referral_code['business_id'],
            name=referral_code['business_name'],
            logo_url=referral_code['logo_url'],
            primary_color=referral_code['primary_color'] or '#4F46E5',
        )

        return render_template('whitelabel/referral.html',
            branding=branding,
            referral_code=code,
            business_name=referral_code['business_name'])

    finally:
        cursor.close()
        conn.close()


# ── Custom Domain Handler ────────────────────────────────────────────────

def handle_custom_domain(domain: str):
    """
    Handle requests to custom domains.
    Called before routing to identify business.
    """
    from branding import get_business_by_domain

    business = get_business_by_domain(domain)

    if business:
        # Set business context for this request
        g.business_id = business.business_id
        g.branding = business
        return True

    return False


# ── Helper Functions ─────────────────────────────────────────────────────

def create_booking(business_id: str, customer_name: str, customer_phone: str,
                   service_id: str, booking_date: str, booking_time: str,
                   staff_id: str = None, customer_email: str = None,
                   notes: str = None, source: str = 'web') -> str:
    """Create a booking in the database."""
    from db_config import get_db_connection
    import uuid

    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        booking_id = str(uuid.uuid4())

        # Get service duration
        cursor.execute("SELECT duration_min FROM booking_services WHERE id = %s", (service_id,))
        service = cursor.fetchone()

        duration = service['duration_min'] if service else 30

        cursor.execute("""
            INSERT INTO bookings (
                id, business_id, service_id, staff_id,
                customer_name, customer_phone, customer_email,
                booking_date, booking_time, duration_min,
                notes, source, status
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, (
            booking_id, business_id, service_id, staff_id,
            customer_name, customer_phone, customer_email,
            booking_date, booking_time, duration,
            notes, source, 'pending'
        ))

        conn.commit()
        return booking_id

    finally:
        cursor.close()
        conn.close()


# ── Template Filters ──────────────────────────────────────────────────────

def register_template_filters(app):
    """Register Jinja2 template filters for branding."""

    @app.template_filter('brand_color')
    def brand_color(color: str, amount: float = 0) -> str:
        """Adjust brand color brightness."""
        from branding import BusinessBranding
        # Simplified - would need full color manipulation
        return color

    @app.template_filter('format_currency')
    def format_currency(amount: float, currency: str = 'USD') -> str:
        """Format currency amount."""
        import locale
        # Simplified - would use locale module
        if currency == 'USD':
            return f'${amount:.2f}'
        return f'{amount:.2f} {currency}'

    @app.template_filter('format_phone')
    def format_phone(phone: str) -> str:
        """Format phone number."""
        if not phone:
            return ''
        # Simple US format
        digits = ''.join(c for c in phone if c.isdigit())
        if len(digits) == 10:
            return f'({digits[:3]}) {digits[3:6]}-{digits[6:]}'
        if len(digits) == 11 and digits[0] == '1':
            return f'+1 ({digits[1:4]}) {digits[4:7]}-{digits[7:]}'
        return phone


# ── Export ──────────────────────────────────────────────────────────────

__all__ = [
    'whitelabel_bp',
    'get_branding',
    'handle_custom_domain',
    'register_template_filters',
]