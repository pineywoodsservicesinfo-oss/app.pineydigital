"""
billing.py - Stripe Billing Integration for FieldPulse
Multi-tenant SaaS subscription management
"""

import os
import logging
from typing import Optional, Dict, Any
from datetime import datetime

logger = logging.getLogger(__name__)

# Stripe configuration
STRIPE_SECRET_KEY = os.environ.get('STRIPE_SECRET_KEY')
STRIPE_PUBLISHABLE_KEY = os.environ.get('STRIPE_PUBLISHABLE_KEY')
STRIPE_WEBHOOK_SECRET = os.environ.get('STRIPE_WEBHOOK_SECRET')

# Pricing tiers (matching the plan)
PRICING_TIERS = {
    'starter': {
        'name': 'Starter',
        'price': 79,
        'interval': 'month',
        'features': [
            '1-3 crews',
            'Online booking',
            'SMS reminders',
            'Job photos',
            'Customer portal',
        ],
        'limits': {
            'crews': 3,
            'locations': 1,
            'sms_per_month': 500,
        }
    },
    'professional': {
        'name': 'Professional',
        'price': 159,
        'interval': 'month',
        'features': [
            'Unlimited crews',
            'Estimates & quotes',
            'Invoicing',
            'Stripe payments',
            'Advanced reporting',
        ],
        'limits': {
            'crews': None,  # Unlimited
            'locations': 1,
            'sms_per_month': 2000,
        }
    },
    'enterprise': {
        'name': 'Enterprise',
        'price': 299,
        'interval': 'month',
        'features': [
            'Route optimization',
            'Crew mobile app',
            'API access',
            'White-label branding',
            'Priority support',
        ],
        'limits': {
            'crews': None,
            'locations': None,
            'sms_per_month': 10000,
        }
    }
}

# Add-on pricing
ADDONS = {
    'additional_location': {'price': 49, 'name': 'Additional Location'},
    'sms_overage': {'price': 0.02, 'name': 'SMS Overage (per message)'},
    'custom_domain': {'price': 20, 'name': 'Custom Domain'},
}


class StripeNotConfiguredError(Exception):
    """Raised when Stripe is not properly configured."""
    pass


def get_stripe_client():
    """Get Stripe client, raising error if not configured."""
    if not STRIPE_SECRET_KEY:
        raise StripeNotConfiguredError(
            "STRIPE_SECRET_KEY not set. Add it to your .env file."
        )

    import stripe
    stripe.api_key = STRIPE_SECRET_KEY
    return stripe


# ── Customer Management ─────────────────────────────────────────────────

def create_customer(email: str, name: str, business_id: str,
                    phone: str = None, metadata: dict = None) -> Dict[str, Any]:
    """
    Create a Stripe customer for a business.

    Args:
        email: Customer email
        name: Business/owner name
        business_id: Internal business UUID
        phone: Optional phone number
        metadata: Additional metadata

    Returns:
        Dict with customer_id and stripe_customer object

    Raises:
        ValueError: If email is invalid
    """
    # Input validation
    try:
        from modules.security import validate_email, sanitize_metadata
    except ImportError:
        from security import validate_email, sanitize_metadata

    if not validate_email(email):
        raise ValueError(f"Invalid email format: {email}")

    # Sanitize metadata
    safe_metadata = sanitize_metadata(metadata, allowed_keys=['source', 'plan', 'referral'])
    safe_metadata['business_id'] = business_id
    safe_metadata['source'] = 'fieldpulse'

    stripe = get_stripe_client()

    # Generate idempotency key for safe retries
    try:
        from modules.security import generate_idempotency_key
    except ImportError:
        from security import generate_idempotency_key
    idempotency_key = generate_idempotency_key('customer_create', business_id)

    customer = stripe.Customer.create(
        email=email.lower().strip(),
        name=name[:100],  # Stripe limit
        phone=phone,
        metadata=safe_metadata,
        idempotency_key=idempotency_key
    )

    logger.info(f"Created Stripe customer {customer.id} for business {business_id}")

    return {
        'customer_id': customer.id,
        'customer': customer
    }


def get_customer(customer_id: str) -> Optional[Dict[str, Any]]:
    """Get Stripe customer by ID."""
    stripe = get_stripe_client()

    try:
        customer = stripe.Customer.retrieve(customer_id)
        return {
            'customer_id': customer.id,
            'email': customer.email,
            'name': customer.name,
            'phone': customer.phone,
            'metadata': customer.metadata,
        }
    except stripe.error.InvalidRequestError:
        return None


def update_customer(customer_id: str, **kwargs) -> Dict[str, Any]:
    """Update Stripe customer details."""
    stripe = get_stripe_client()

    customer = stripe.Customer.modify(customer_id, **kwargs)

    return {
        'customer_id': customer.id,
        'customer': customer
    }


# ── Product & Price Management ─────────────────────────────────────────

def ensure_products_and_prices() -> Dict[str, str]:
    """
    Ensure Stripe products and prices exist for all tiers.
    Creates them if they don't exist.

    Returns:
        Dict mapping tier names to Stripe price IDs
    """
    stripe = get_stripe_client()

    price_ids = {}

    for tier_key, tier_config in PRICING_TIERS.items():
        # Check if product exists
        try:
            products = stripe.Product.search(
                query=f'metadata["tier"]:"{tier_key}" AND active:"true"',
                limit=1
            )
        except Exception:
            # Fallback: list all products and filter
            products = stripe.Product.list(active=True, limit=100)
            products.data = [p for p in products.data if p.metadata.get('tier') == tier_key]

        if products.data:
            # Product exists, get its default price
            product = products.data[0]
            if product.default_price:
                price_ids[tier_key] = product.default_price
                logger.info(f"Found existing product for {tier_key}: {product.id}")
                continue
        else:
            # Create product
            product = stripe.Product.create(
                name=f"FieldPulse {tier_config['name']}",
                description=f"FieldPulse {tier_config['name']} plan - {', '.join(tier_config['features'][:2])}",
                metadata={
                    'tier': tier_key,
                    'source': 'fieldpulse'
                }
            )
            logger.info(f"Created product for {tier_key}: {product.id}")

        # Check if price exists for this product
        prices = stripe.Price.list(
            product=product.id,
            active=True,
            limit=1
        )

        if prices.data:
            price_ids[tier_key] = prices.data[0].id
        else:
            # Create price
            price = stripe.Price.create(
                product=product.id,
                unit_amount=tier_config['price'] * 100,  # Convert to cents
                currency='usd',
                recurring={'interval': tier_config['interval']},
                metadata={
                    'tier': tier_key,
                }
            )
            price_ids[tier_key] = price.id
            logger.info(f"Created price for {tier_key}: {price.id}")

            # Set as default price
            stripe.Product.modify(product.id, default_price=price.id)

    return price_ids


# ── Subscription Management ────────────────────────────────────────────

def create_subscription(customer_id: str, tier: str = 'starter',
                        trial_days: int = 14, metadata: dict = None,
                        business_id: str = None) -> Dict[str, Any]:
    """
    Create a subscription for a customer.

    Args:
        customer_id: Stripe customer ID
        tier: Pricing tier (starter/professional/enterprise)
        trial_days: Number of trial days (default 14)
        metadata: Additional metadata
        business_id: Business UUID for idempotency key (recommended)

    Returns:
        Dict with subscription_id and status

    Raises:
        ValueError: If tier is invalid
    """
    # Input validation
    try:
        from modules.security import sanitize_metadata, generate_idempotency_key
    except ImportError:
        from security import sanitize_metadata, generate_idempotency_key

    # Validate tier BEFORE making any API calls
    valid_tiers = list(PRICING_TIERS.keys())
    if tier not in valid_tiers:
        raise ValueError(f"Invalid tier: {tier}. Must be one of {valid_tiers}")

    # Sanitize metadata
    safe_metadata = sanitize_metadata(metadata, allowed_keys=['source', 'referral', 'promo'])
    safe_metadata['tier'] = tier
    safe_metadata['source'] = 'fieldpulse'

    stripe = get_stripe_client()

    # Ensure products exist
    price_ids = ensure_products_and_prices()
    price_id = price_ids[tier]

    # Generate idempotency key for safe retries
    if business_id:
        idempotency_key = generate_idempotency_key('sub_create', business_id)
    else:
        # Fallback to customer_id if business_id not provided
        idempotency_key = generate_idempotency_key('sub_create', customer_id)

    # Create subscription with trial
    subscription = stripe.Subscription.create(
        customer=customer_id,
        items=[{'price': price_id}],
        trial_period_days=trial_days,
        metadata=safe_metadata,
        idempotency_key=idempotency_key,
        payment_behavior='default_incomplete',
        payment_settings={'save_default_payment_method': 'on_subscription'},
    )

    logger.info(f"Created subscription {subscription.id} for customer {customer_id} (tier: {tier})")

    return {
        'subscription_id': subscription.id,
        'status': subscription.status,
        'tier': tier,
        'trial_end': datetime.fromtimestamp(subscription.trial_end) if subscription.trial_end else None,
        'current_period_end': datetime.fromtimestamp(subscription.current_period_end),
        'subscription': subscription
    }


def get_subscription(subscription_id: str) -> Optional[Dict[str, Any]]:
    """Get subscription details."""
    stripe = get_stripe_client()

    try:
        subscription = stripe.Subscription.retrieve(subscription_id)

        return {
            'subscription_id': subscription.id,
            'status': subscription.status,
            'tier': subscription.metadata.get('tier'),
            'trial_end': datetime.fromtimestamp(subscription.trial_end) if subscription.trial_end else None,
            'current_period_end': datetime.fromtimestamp(subscription.current_period_end),
            'cancel_at_period_end': subscription.cancel_at_period_end,
            'customer_id': subscription.customer,
        }
    except stripe.error.InvalidRequestError:
        return None


def update_subscription(subscription_id: str, new_tier: str = None,
                       cancel_at_period_end: bool = None) -> Dict[str, Any]:
    """
    Update a subscription (upgrade/downgrade or cancel).

    Args:
        subscription_id: Stripe subscription ID
        new_tier: New tier to switch to (optional)
        cancel_at_period_end: Set to True to cancel at period end

    Returns:
        Updated subscription details
    """
    stripe = get_stripe_client()

    subscription = stripe.Subscription.retrieve(subscription_id)

    if new_tier and new_tier != subscription.metadata.get('tier'):
        # Tier change - update the price
        price_ids = ensure_products_and_prices()

        if new_tier not in price_ids:
            raise ValueError(f"Invalid tier: {new_tier}")

        # Update subscription item to new price
        stripe.Subscription.modify(
            subscription_id,
            items=[{
                'id': subscription['items']['data'][0].id,
                'price': price_ids[new_tier]
            }],
            metadata={'tier': new_tier}
        )

        logger.info(f"Updated subscription {subscription_id} to tier {new_tier}")

    if cancel_at_period_end is not None:
        stripe.Subscription.modify(subscription_id, cancel_at_period_end=cancel_at_period_end)
        logger.info(f"Set subscription {subscription_id} cancel_at_period_end={cancel_at_period_end}")

    # Retrieve updated subscription
    return get_subscription(subscription_id)


def cancel_subscription(subscription_id: str, immediately: bool = False) -> Dict[str, Any]:
    """
    Cancel a subscription.

    Args:
        subscription_id: Stripe subscription ID
        immediately: If True, cancel immediately. If False, cancel at period end.

    Returns:
        Cancellation status
    """
    stripe = get_stripe_client()

    if immediately:
        subscription = stripe.Subscription.cancel(subscription_id)
        logger.info(f"Cancelled subscription {subscription_id} immediately")
    else:
        subscription = stripe.Subscription.modify(subscription_id, cancel_at_period_end=True)
        logger.info(f"Cancelled subscription {subscription_id} at period end")

    return {
        'subscription_id': subscription.id,
        'status': subscription.status,
        'cancel_at_period_end': subscription.cancel_at_period_end,
    }


# ── Payment Methods ─────────────────────────────────────────────────────

def create_setup_intent(customer_id: str) -> Dict[str, Any]:
    """
    Create a setup intent to collect payment method.

    Returns client_secret for frontend to use with Stripe.js
    """
    stripe = get_stripe_client()

    setup_intent = stripe.SetupIntent.create(
        customer=customer_id,
        payment_method_types=['card'],
        metadata={'source': 'fieldpulse'}
    )

    return {
        'setup_intent_id': setup_intent.id,
        'client_secret': setup_intent.client_secret,
    }


def get_payment_methods(customer_id: str) -> list:
    """Get all payment methods for a customer."""
    stripe = get_stripe_client()

    payment_methods = stripe.PaymentMethod.list(
        customer=customer_id,
        type='card'
    )

    return [
        {
            'id': pm.id,
            'type': pm.type,
            'card': {
                'brand': pm.card.brand,
                'last4': pm.card.last4,
                'exp_month': pm.card.exp_month,
                'exp_year': pm.card.exp_year,
            }
        }
        for pm in payment_methods.data
    ]


def set_default_payment_method(customer_id: str, payment_method_id: str) -> Dict[str, Any]:
    """Set the default payment method for a customer."""
    stripe = get_stripe_client()

    customer = stripe.Customer.modify(
        customer_id,
        invoice_settings={'default_payment_method': payment_method_id}
    )

    return {
        'customer_id': customer.id,
        'default_payment_method': payment_method_id
    }


# ── Invoice Management ──────────────────────────────────────────────────

def get_invoices(customer_id: str, limit: int = 10) -> list:
    """Get invoices for a customer."""
    stripe = get_stripe_client()

    invoices = stripe.Invoice.list(
        customer=customer_id,
        limit=limit
    )

    return [
        {
            'id': inv.id,
            'number': inv.number,
            'status': inv.status,
            'amount_due': inv.amount_due / 100,
            'currency': inv.currency,
            'created': datetime.fromtimestamp(inv.created),
            'due_date': datetime.fromtimestamp(inv.due_date) if inv.due_date else None,
            'paid': inv.paid,
            'invoice_pdf': inv.invoice_pdf,
        }
        for inv in invoices.data
    ]


# ── Webhook Handling ───────────────────────────────────────────────────

def verify_webhook_signature(payload: bytes, sig_header: str) -> dict:
    """
    Verify and parse a Stripe webhook.

    Args:
        payload: Raw request body bytes
        sig_header: Stripe-Signature header value

    Returns:
        Parsed event dict

    Raises:
        ValueError: If signature verification fails
    """
    stripe = get_stripe_client()

    if not STRIPE_WEBHOOK_SECRET:
        raise ValueError("STRIPE_WEBHOOK_SECRET not configured")

    try:
        event = stripe.Webhook.construct_event(
            payload, sig_header, STRIPE_WEBHOOK_SECRET
        )
        return event
    except stripe.error.SignatureVerificationError as e:
        raise ValueError(f"Invalid signature: {e}")


def handle_webhook_event(event: dict) -> dict:
    """
    Handle a Stripe webhook event.

    Args:
        event: Stripe event dict

    Returns:
        Dict with action taken
    """
    event_type = event.get('type')
    data = event.get('data', {}).get('object', {})

    handlers = {
        'customer.created': handle_customer_created,
        'customer.updated': handle_customer_updated,
        'customer.subscription.created': handle_subscription_created,
        'customer.subscription.updated': handle_subscription_updated,
        'customer.subscription.deleted': handle_subscription_deleted,
        'invoice.paid': handle_invoice_paid,
        'invoice.payment_failed': handle_invoice_payment_failed,
        'checkout.session.completed': handle_checkout_completed,
    }

    handler = handlers.get(event_type)

    if handler:
        return handler(data)
    else:
        logger.info(f"Unhandled webhook event type: {event_type}")
        return {'handled': False, 'event_type': event_type}


# ── Webhook Handlers ────────────────────────────────────────────────────

def handle_customer_created(data: dict) -> dict:
    """Handle customer.created webhook."""
    logger.info(f"Customer created: {data.get('id')}")
    # Update local database with stripe_customer_id
    return {'handled': True, 'action': 'customer_created', 'customer_id': data.get('id')}


def handle_customer_updated(data: dict) -> dict:
    """Handle customer.updated webhook."""
    logger.info(f"Customer updated: {data.get('id')}")
    return {'handled': True, 'action': 'customer_updated', 'customer_id': data.get('id')}


def handle_subscription_created(data: dict) -> dict:
    """Handle subscription created webhook."""
    sub_id = data.get('id')
    customer_id = data.get('customer')
    tier = data.get('metadata', {}).get('tier', 'starter')

    logger.info(f"Subscription created: {sub_id} for customer {customer_id} (tier: {tier})")

    # Update local database with subscription details
    return {
        'handled': True,
        'action': 'subscription_created',
        'subscription_id': sub_id,
        'customer_id': customer_id,
        'tier': tier,
    }


def handle_subscription_updated(data: dict) -> dict:
    """Handle subscription updated webhook."""
    sub_id = data.get('id')
    status = data.get('status')
    tier = data.get('metadata', {}).get('tier')

    logger.info(f"Subscription updated: {sub_id} (status: {status}, tier: {tier})")

    return {
        'handled': True,
        'action': 'subscription_updated',
        'subscription_id': sub_id,
        'status': status,
        'tier': tier,
    }


def handle_subscription_deleted(data: dict) -> dict:
    """Handle subscription deleted webhook."""
    sub_id = data.get('id')
    customer_id = data.get('customer')

    logger.info(f"Subscription deleted: {sub_id} for customer {customer_id}")

    # Mark business as canceled in local database
    return {
        'handled': True,
        'action': 'subscription_deleted',
        'subscription_id': sub_id,
        'customer_id': customer_id,
    }


def handle_invoice_paid(data: dict) -> dict:
    """Handle invoice.paid webhook."""
    invoice_id = data.get('id')
    customer_id = data.get('customer')

    logger.info(f"Invoice paid: {invoice_id} for customer {customer_id}")

    # Extend business subscription period
    return {
        'handled': True,
        'action': 'invoice_paid',
        'invoice_id': invoice_id,
        'customer_id': customer_id,
    }


def handle_invoice_payment_failed(data: dict) -> dict:
    """Handle invoice.payment_failed webhook."""
    invoice_id = data.get('id')
    customer_id = data.get('customer')
    attempt_count = data.get('attempt_count', 0)

    logger.warning(f"Invoice payment failed: {invoice_id} for customer {customer_id} (attempt {attempt_count})")

    # Mark business as past_due, send notification
    return {
        'handled': True,
        'action': 'invoice_payment_failed',
        'invoice_id': invoice_id,
        'customer_id': customer_id,
        'attempt_count': attempt_count,
    }


def handle_checkout_completed(data: dict) -> dict:
    """Handle checkout.session.completed webhook."""
    session_id = data.get('id')
    customer_id = data.get('customer')

    logger.info(f"Checkout completed: {session_id} for customer {customer_id}")

    return {
        'handled': True,
        'action': 'checkout_completed',
        'session_id': session_id,
        'customer_id': customer_id,
    }


# ── Usage & Billing ────────────────────────────────────────────────────

def get_usage_summary(business_id: str) -> Dict[str, Any]:
    """
    Get usage summary for a business.
    Used for billing calculations and limits enforcement.

    Returns:
        Dict with usage counts for crews, locations, SMS, etc.
    """
    # This would query the database for actual usage
    # Placeholder for now - implement when integrating with database

    return {
        'crews': 0,
        'locations': 1,
        'sms_sent_this_month': 0,
        'storage_used_mb': 0,
    }


def check_tier_limits(business_id: str, tier: str) -> Dict[str, bool]:
    """
    Check if business is within tier limits.

    Returns:
        Dict with limit check results
    """
    usage = get_usage_summary(business_id)
    limits = PRICING_TIERS.get(tier, {}).get('limits', {})

    return {
        'crews_ok': limits.get('crews') is None or usage['crews'] <= limits['crews'],
        'locations_ok': limits.get('locations') is None or usage['locations'] <= limits['locations'],
        'sms_ok': limits.get('sms_per_month') is None or usage['sms_sent_this_month'] <= limits['sms_per_month'],
    }


# ── Initialization ───────────────────────────────────────────────────────

def init_stripe():
    """
    Initialize Stripe products and prices.
    Call this once when setting up a new FieldPulse instance.
    """
    logger.info("Initializing Stripe products and prices...")

    try:
        price_ids = ensure_products_and_prices()
        logger.info(f"Stripe initialized. Price IDs: {price_ids}")
        return price_ids
    except Exception as e:
        logger.error(f"Failed to initialize Stripe: {e}")
        raise


if __name__ == '__main__':
    # Test Stripe connection and create products
    print("Testing Stripe connection...")

    try:
        price_ids = init_stripe()
        print("\n✓ Stripe initialized successfully!")
        print("\nPrice IDs:")
        for tier, price_id in price_ids.items():
            print(f"  {tier}: {price_id}")
    except StripeNotConfiguredError as e:
        print(f"\n✗ {e}")
        print("\nAdd your Stripe keys to .env:")
        print("  STRIPE_SECRET_KEY=sk_test_xxx")
        print("  STRIPE_PUBLISHABLE_KEY=pk_test_xxx")
    except Exception as e:
        print(f"\n✗ Error: {e}")