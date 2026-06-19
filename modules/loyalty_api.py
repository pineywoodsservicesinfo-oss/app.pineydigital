"""
loyalty_api.py — API endpoints for Loyalty Program operations
Piney Digital Outreach System — LoyaltyLoop

RESTful API for:
- Customer card management
- Punch operations
- Reward redemptions
- QR code generation
"""

import qrcode
import io
import base64
from flask import Blueprint, jsonify, request, session, make_response
from modules.loyalty_db import (
    get_all_loyalty_businesses, get_loyalty_business,
    create_customer, get_customer, get_or_create_customer_card,
    get_customer_cards, add_punch, redeem_reward,
    get_business_stats, get_loyalty_stats
)

loyalty_api = Blueprint('loyalty_api', __name__, url_prefix='/api/loyalty')


def generate_qr_code(data: str, size: int = 200) -> str:
    """Generate QR code as base64 PNG."""
    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_L,
        box_size=10,
        border=2,
    )
    qr.add_data(data)
    qr.make(fit=True)
    
    img = qr.make_image(fill_color="black", back_color="white")
    buffered = io.BytesIO()
    img.save(buffered, format="PNG")
    
    return base64.b64encode(buffered.getvalue()).decode()


# ── Public Endpoints ─────────────────────────────────────

@loyalty_api.route("/businesses", methods=["GET"])
def api_get_businesses():
    """Get all active loyalty businesses."""
    businesses = get_all_loyalty_businesses()
    return jsonify({
        "success": True,
        "businesses": [{
            "id": b["id"],
            "name": b["name"],
            "type": b["type"],
            "city": b["city"],
            "punches_needed": b["punches_needed"],
            "discount_percent": b["discount_percent"]
        } for b in businesses]
    })


@loyalty_api.route("/business/<biz_id>", methods=["GET"])
def api_get_business(biz_id: str):
    """Get single business details."""
    biz = get_loyalty_business(biz_id)
    if not biz:
        return jsonify({"success": False, "error": "Business not found"}), 404
    
    return jsonify({
        "success": True,
        "business": {
            "id": biz["id"],
            "name": biz["name"],
            "type": biz["type"],
            "city": biz["city"],
            "phone": biz["phone"],
            "punches_needed": biz["punches_needed"],
            "discount_percent": biz["discount_percent"]
        }
    })


# ── Customer Endpoints ─────────────────────────────────────

@loyalty_api.route("/customer/create", methods=["POST"])
def api_create_customer():
    """Create a new customer (anonymous or with account)."""
    data = request.get_json() or {}
    name = data.get("name")
    email = data.get("email")
    phone = data.get("phone")
    
    if not name:
        return jsonify({"success": False, "error": "Name required"}), 400
    
    cust_id = create_customer(name=name, email=email, phone=phone)
    
    return jsonify({
        "success": True,
        "customer_id": cust_id,
        "name": name
    })


@loyalty_api.route("/customer/<cust_id>", methods=["GET"])
def api_get_customer(cust_id: str):
    """Get customer details and all their cards."""
    cust = get_customer(cust_id)
    if not cust:
        return jsonify({"success": False, "error": "Customer not found"}), 404
    
    cards = get_customer_cards(cust_id)
    
    return jsonify({
        "success": True,
        "customer": {
            "id": cust["id"],
            "name": cust["name"],
            "email": cust["email"],
            "phone": cust["phone"]
        },
        "cards": [{
            "id": c["id"],
            "business_id": c["business_id"],
            "business_name": c["business_name"],
            "business_type": c["business_type"],
            "punches": c["punches"],
            "punches_needed": c["punches_needed"],
            "discount_percent": c["discount_percent"],
            "rewards_earned": c["rewards_earned"],
            "last_punch_at": c["last_punch_at"],
            "qr_data": f"LOYALTY:{c['id']}",
            "qr_code": generate_qr_code(f"LOYALTY:{c['id']}")
        } for c in cards]
    })


@loyalty_api.route("/customer/<cust_id>/join/<biz_id>", methods=["POST"])
def api_join_program(cust_id: str, biz_id: str):
    """Customer joins a loyalty program (creates card)."""
    # Verify business exists
    biz = get_loyalty_business(biz_id)
    if not biz:
        return jsonify({"success": False, "error": "Business not found"}), 404
    
    # Create or get card
    card = get_or_create_customer_card(cust_id, biz_id)
    
    return jsonify({
        "success": True,
        "card": {
            "id": card["id"],
            "business_id": biz_id,
            "business_name": biz["name"],
            "punches": card["punches"],
            "punches_needed": biz["punches_needed"],
            "discount_percent": biz["discount_percent"]
        }
    })


@loyalty_api.route("/card/<card_id>", methods=["GET"])
def api_get_card(card_id: str):
    """Get single card details with QR code."""
    from modules.loyalty_db import get_connection
    conn = get_connection()
    c = conn.cursor()
    
    c.execute("""
        SELECT lc.*, lb.name as business_name, lb.type as business_type,
               lb.punches_needed, lb.discount_percent, lb.city, lb.phone
        FROM loyalty_cards lc
        JOIN loyalty_businesses lb ON lc.business_id = lb.id
        WHERE lc.id = ?
    """, (card_id,))
    
    row = c.fetchone()
    conn.close()
    
    if not row:
        return jsonify({"success": False, "error": "Card not found"}), 404
    
    card = dict(row)
    
    return jsonify({
        "success": True,
        "card": {
            "id": card["id"],
            "business_id": card["business_id"],
            "business_name": card["business_name"],
            "business_type": card["business_type"],
            "city": card["city"],
            "phone": card["phone"],
            "punches": card["punches"],
            "punches_needed": card["punches_needed"],
            "discount_percent": card["discount_percent"],
            "rewards_earned": card["rewards_earned"],
            "last_punch_at": card["last_punch_at"],
            "progress_percent": min(100, round(card["punches"] / card["punches_needed"] * 100)),
            "reward_ready": card["punches"] >= card["punches_needed"],
            "qr_data": f"LOYALTY:{card['id']}",
            "qr_code": generate_qr_code(f"LOYALTY:{card['id']}")
        }
    })


# ── Punch & Reward Endpoints ─────────────────────────────────────

@loyalty_api.route("/card/<card_id>/punch", methods=["POST"])
def api_add_punch(card_id: str):
    """Add a punch to a card (business only)."""
    data = request.get_json() or {}
    punched_by = data.get("punched_by", "business")
    notes = data.get("notes")
    auto_reward = data.get("auto_reward", True)
    
    result = add_punch(card_id, punched_by=punched_by, notes=notes, auto_reward=auto_reward)
    
    if not result:
        return jsonify({"success": False, "error": "Card not found"}), 404
    
    message = "Punch added!"
    if result.get("card_completed"):
        message = f"🎉 Card completed! {result['total_rewards']} rewards earned. Starting new card!"
    elif result["reward_earned"]:
        message = "Reward unlocked! Show this to redeem."
    
    return jsonify({
        "success": True,
        "reward_earned": result["reward_earned"],
        "card_completed": result.get("card_completed", False),
        "punches": result["card"]["punches"],
        "punches_needed": result["punches_needed"],
        "total_rewards": result.get("total_rewards", 0),
        "message": message
    })


@loyalty_api.route("/card/<card_id>/redeem", methods=["POST"])
def api_redeem_reward(card_id: str):
    """Redeem a reward (reset punches, record redemption)."""
    data = request.get_json() or {}
    redeemed_by = data.get("redeemed_by", "business")
    notes = data.get("notes")
    
    result = redeem_reward(card_id, redeemed_by=redeemed_by, notes=notes)
    
    if not result["success"]:
        return jsonify(result), 400
    
    return jsonify({
        "success": True,
        "discount_percent": result["discount_percent"],
        "new_punches": 0,
        "message": f"Reward redeemed! {result['discount_percent']}% discount applied."
    })


# ── Business Stats Endpoint ─────────────────────────────────────

@loyalty_api.route("/business/<biz_id>/stats", methods=["GET"])
def api_business_stats(biz_id: str):
    """Get business loyalty stats."""
    stats = get_business_stats(biz_id)
    biz = get_loyalty_business(biz_id)
    
    if not biz:
        return jsonify({"success": False, "error": "Business not found"}), 404
    
    return jsonify({
        "success": True,
        "business": {
            "id": biz["id"],
            "name": biz["name"],
            "punches_needed": biz["punches_needed"],
            "discount_percent": biz["discount_percent"]
        },
        "stats": {
            "total_customers": stats["total_customers"],
            "total_punches": stats["total_punches"],
            "total_rewards": stats["total_rewards"]
        },
        "customers": [{
            "customer_id": c["customer_id"],
            "customer_name": c["customer_name"],
            "punches": c["punches"],
            "punches_needed": c["punches_needed"],
            "last_punch_at": c["last_punch_at"],
            "reward_ready": c["punches"] >= c["punches_needed"]
        } for c in stats["customers"]]
    })
