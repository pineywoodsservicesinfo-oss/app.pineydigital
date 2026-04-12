"""
reply_handler.py — Inbound SMS & Voice Webhook Handler
Piney Digital Outreach System — Module 7

How it works:
  1. Twilio receives an inbound SMS reply from a lead
  2. Twilio POSTs it to /webhook/sms on this Flask server
  3. We look up which lead the number belongs to
  4. Groq/Claude classifies the intent:
       'interested'     → send pricing info + book a call
       'question'       → answer the question intelligently
       'not_interested' → polite opt-out, mark lead dead
       'stop'           → immediate opt-out, never contact again
       'unknown'        → forward to Joel for manual handling
  5. Auto-reply is sent back via Twilio
  6. Joel gets an instant text alert for hot leads (interested)
  7. Everything logged to DB

Vapi Voice Call Webhooks:
  - /webhook/vapi/call-ended — Called when AI call finishes
  - /webhook/vapi/transcript — Real-time transcript updates
  - /webhook/vapi/status    — Call status updates (ringing, etc.)

Setup:
  - Run: python reply_handler.py
  - Expose port 5001 via ngrok: ngrok http 5001
  - Set Twilio webhook URL to: https://your-ngrok-url.ngrok.io/webhook/sms
  - Set Vapi webhook URL to: https://your-ngrok-url.ngrok.io/webhook/vapi/call-ended
  - In Twilio console → Phone Numbers → your 936 number
    → Messaging → A message comes in → Webhook → paste URL
  - In Vapi dashboard → Settings → Webhooks → add call-ended webhook
"""

import os
import sys
import json
import logging
from datetime import datetime
from pathlib import Path
from flask import Flask, request, Response

sys.path.insert(0, str(Path(__file__).parent))

from modules.utils import load_env

load_env()

from modules.database import get_connection, update_lead

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("logs/replies.log"),
    ]
)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# ── Config ─────────────────────────────────────────────────
TWILIO_SID      = os.environ.get("TWILIO_ACCOUNT_SID", "")
TWILIO_TOKEN    = os.environ.get("TWILIO_AUTH_TOKEN", "")
TWILIO_FROM     = os.environ.get("TWILIO_PHONE_NUMBER", "")
TWILIO_MSG_SID  = os.environ.get("TWILIO_MESSAGING_SID", "")
GROQ_API_KEY    = os.environ.get("GROQ_API_KEY", "")
ANTHROPIC_KEY   = os.environ.get("ANTHROPIC_API_KEY", "")
JOEL_PHONE      = os.environ.get("JOEL_PHONE", "")   # your personal number for hot lead alerts

# ── Response templates ─────────────────────────────────────
RESPONSES = {
    "interested": (
        "Hey! Great to hear from you. I build professional websites for East Texas "
        "businesses starting at $800, delivered in 7 days. I'd love to show you some "
        "examples and give you a free quote. What's a good time for a quick 15-min call? "
        "Or visit pineydigital.com anytime."
    ),
    "question_website_cost": (
        "Great question. Websites start at $800 for a clean professional site — "
        "includes mobile-friendly design, contact form, and Google setup. "
        "Done in 7 days. Want me to send you some examples of my work first?"
    ),
    "question_timeline": (
        "I deliver most sites in 3–7 days from when we kick off. "
        "I just need your business info, logo if you have one, and your content. "
        "Want to get started? I can do a free quote call anytime."
    ),
    "not_interested": (
        "No problem at all — I appreciate you taking the time to reply. "
        "If you ever need a website down the road, I'm at pineydigital.com. "
        "Take care and have a great day!"
    ),
    "stop": "",  # Twilio handles STOP automatically — no reply needed
    "unknown": (
        "Hey, thanks for reaching back out! Joel from Piney Digital here. "
        "I'll get back to you personally within the hour. "
        "Or call me directly at (936) 299-9897."
    ),
}


# ── AI intent classifier ───────────────────────────────────
def classify_intent(message: str, business_name: str) -> tuple[str, str]:
    """
    Classify the intent of an inbound SMS reply.
    Returns (intent, ai_response) where intent is one of:
      interested | question | not_interested | stop | unknown

    Also returns a suggested reply from the AI.
    """
    msg_lower = message.lower().strip()

    # Hard-coded STOP detection — always handle this first
    if msg_lower in ("stop", "unsubscribe", "cancel", "quit", "end", "stopall"):
        return "stop", ""

    # Hard-coded not interested signals
    not_interested_signals = [
        "not interested", "no thanks", "no thank you", "leave me alone",
        "don't contact", "remove me", "take me off", "don't call",
    ]
    if any(s in msg_lower for s in not_interested_signals):
        return "not_interested", RESPONSES["not_interested"]

    # Use AI for everything else
    prompt = f"""A local East Texas business owner just replied to an outreach SMS from Joel at Piney Digital (web design, $800 websites).

Business: {business_name}
Their reply: "{message}"

Classify their intent as exactly ONE of these:
- interested: They want to learn more, get a quote, schedule a call, or are asking about the service positively
- question: They have a specific question (price, timeline, what's included, how it works)
- not_interested: They said no, not now, already have one, or declined
- stop: They want to stop receiving messages (opt-out)
- unknown: Unclear intent, gibberish, wrong number, or unrelated

Then write a warm, short, personal reply from Joel (under 120 chars) that fits their intent.
Reply from the SAME tone as Joel — local, friendly, not salesy.

Return ONLY valid JSON:
{{
  "intent": "one of the five above",
  "reply": "Joel's reply under 120 chars, or empty string if intent is stop"
}}"""

    try:
        if GROQ_API_KEY:
            from groq import Groq
            client = Groq(api_key=GROQ_API_KEY)
            resp = client.chat.completions.create(
                model="llama-3.3-70b-versatile",
                messages=[{"role": "user", "content": prompt}],
                max_tokens=200,
                temperature=0.4,
            )
            raw = resp.choices[0].message.content.strip()
        elif ANTHROPIC_KEY:
            import anthropic
            client = anthropic.Anthropic(api_key=ANTHROPIC_KEY)
            resp = client.messages.create(
                model="claude-sonnet-4-20250514",
                max_tokens=200,
                messages=[{"role": "user", "content": prompt}],
            )
            raw = resp.content[0].text.strip()
        else:
            logger.error("No AI API key configured")
            return "unknown", RESPONSES["unknown"]

        # Clean JSON
        if "```" in raw:
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        start = raw.find("{")
        end   = raw.rfind("}") + 1
        if start != -1 and end > start:
            raw = raw[start:end]

        result = json.loads(raw)
        intent = result.get("intent", "unknown")
        reply  = result.get("reply", "")

        # Validate intent
        valid_intents = {"interested", "question", "not_interested", "stop", "unknown"}
        if intent not in valid_intents:
            intent = "unknown"

        # Ensure reply has opt-out for non-stop intents
        if intent != "stop" and reply and "STOP" not in reply:
            reply = reply.rstrip(".") + ". Reply STOP to opt out."

        return intent, reply

    except Exception as e:
        logger.error("AI classification error: %s", e)
        return "unknown", RESPONSES["unknown"]


# ── DB helpers ─────────────────────────────────────────────
def find_lead_by_phone(phone: str) -> dict | None:
    """Find a lead by their phone number (handles various formats)."""
    import re
    digits = re.sub(r"\D", "", phone)
    if len(digits) == 11 and digits[0] == "1":
        digits = digits[1:]

    conn = get_connection()
    c    = conn.cursor()
    c.execute("""
        SELECT id, business_name, city, category, outreach_status, lead_score
        FROM leads
        WHERE REPLACE(REPLACE(REPLACE(REPLACE(phone,' ',''),'-',''),'(',''),')','')
              LIKE ?
        LIMIT 1
    """, (f"%{digits}%",))
    row = c.fetchone()
    conn.close()
    return dict(row) if row else None


def log_reply(lead_id: int, from_number: str, body: str,
              intent: str, auto_reply: str):
    """Log inbound reply and auto-response to outreach_log."""
    conn = get_connection()
    # Log inbound
    conn.execute("""
        INSERT INTO outreach_log
            (lead_id, channel, direction, body, status, sent_at)
        VALUES (?, 'sms', 'inbound', ?, 'received', datetime('now'))
    """, (lead_id, body))
    # Log auto-reply if sent
    if auto_reply:
        conn.execute("""
            INSERT INTO outreach_log
                (lead_id, channel, direction, body, status, sent_at)
            VALUES (?, 'sms', 'outbound', ?, 'sent', datetime('now'))
        """, (lead_id, auto_reply))
    conn.commit()
    conn.close()


def update_lead_status(lead_id: int, intent: str):
    """Update lead status based on reply intent."""
    status_map = {
        "interested":     "replied",
        "question":       "replied",
        "not_interested": "dead",
        "stop":           "dead",
        "unknown":        "replied",
    }
    new_status = status_map.get(intent, "replied")
    update_lead(lead_id, {
        "outreach_status": new_status,
        "last_reply_at":   datetime.now().isoformat(),
        "reply_intent":    intent,
    })


# ── Send SMS via Twilio ────────────────────────────────────
def send_sms(to: str, body: str) -> bool:
    if not body or not all([TWILIO_SID, TWILIO_TOKEN]):
        return False
    try:
        from twilio.rest import Client
        client = Client(TWILIO_SID, TWILIO_TOKEN)
        if TWILIO_MSG_SID:
            client.messages.create(
                messaging_service_sid=TWILIO_MSG_SID,
                to=to, body=body)
        else:
            client.messages.create(
                from_=TWILIO_FROM, to=to, body=body)
        return True
    except Exception as e:
        logger.error("SMS send error: %s", e)
        return False


def alert_joel(lead: dict, reply_body: str, intent: str):
    """
    Text Joel's personal phone when a hot lead replies.
    Only fires for 'interested' intent.
    """
    if not JOEL_PHONE or intent != "interested":
        return
    alert = (
        f"HOT LEAD replied! {lead['business_name']} ({lead['city']}) "
        f"Score:{lead['lead_score']} "
        f'Said: "{reply_body[:60]}" '
        f"— auto-reply sent, follow up!"
    )
    send_sms(JOEL_PHONE, alert[:160])
    logger.info("  Alert sent to Joel for hot lead: %s", lead['business_name'])


# ── Webhook endpoint ───────────────────────────────────────
@app.route("/webhook/sms", methods=["POST"])
def sms_webhook():
    """
    Twilio POSTs here when someone replies to our SMS.
    Must respond within 15 seconds or Twilio times out.
    """
    from_number = request.form.get("From", "")
    body        = request.form.get("Body", "").strip()
    to_number   = request.form.get("To", "")

    logger.info("="*50)
    logger.info("Inbound SMS from: %s", from_number)
    logger.info("Message: %s", body)

    if not from_number or not body:
        return Response("<?xml version='1.0'?><Response/>",
                        mimetype="text/xml")

    # ── Find the lead ──────────────────────────────────────
    lead = find_lead_by_phone(from_number)

    if not lead:
        logger.warning("  Unknown number: %s — not in leads DB", from_number)
        # Still respond politely to unknown numbers
        send_sms(from_number,
            "Hey, Joel with Piney Digital. You can reach me at "
            "(936) 299-9897 or joel@pineydigital.com. Thanks!")
        return Response("<?xml version='1.0'?><Response/>",
                        mimetype="text/xml")

    logger.info("  Lead found: %s (%s) — score: %d",
                lead["business_name"], lead["city"], lead["lead_score"])

    # ── Classify intent ────────────────────────────────────
    intent, auto_reply = classify_intent(body, lead["business_name"])
    logger.info("  Intent: %s", intent)
    logger.info("  Auto-reply: %s", auto_reply)

    # ── Send auto-reply ────────────────────────────────────
    if auto_reply and intent != "stop":
        sent = send_sms(from_number, auto_reply)
        logger.info("  Reply sent: %s", "OK" if sent else "FAILED")

    # ── Alert Joel for hot leads ───────────────────────────
    alert_joel(lead, body, intent)

    # ── Update DB ──────────────────────────────────────────
    update_lead_status(lead["id"], intent)
    log_reply(lead["id"], from_number, body, intent, auto_reply)

    logger.info("  DB updated — status: %s", intent)

    # ── Return empty TwiML (we sent reply via API, not TwiML) ──
    return Response("<?xml version='1.0'?><Response/>",
                    mimetype="text/xml", status=200)


@app.route("/webhook/health", methods=["GET"])
def health():
    """Health check — confirm webhook server is running."""
    return {"status": "ok", "service": "Piney Digital Reply Handler",
            "time": datetime.now().isoformat()}


@app.route("/replies", methods=["GET"])
def view_replies():
    """Quick view of all inbound replies."""
    conn = get_connection()
    c    = conn.cursor()
    c.execute("""
        SELECT l.business_name, l.city, l.phone, l.lead_score,
               l.reply_intent, l.last_reply_at, l.outreach_status,
               ol.body as reply_body
        FROM leads l
        LEFT JOIN outreach_log ol ON ol.lead_id = l.id
            AND ol.direction = 'inbound'
        WHERE l.reply_intent IS NOT NULL
        ORDER BY l.last_reply_at DESC
        LIMIT 50
    """)
    rows = [dict(zip([d[0] for d in c.description], r))
            for r in c.fetchall()]
    conn.close()
    return {"count": len(rows), "replies": rows}


# ═══════════════════════════════════════════════════════════════
# VAPI WEBHOOKS — AI Voice Call Callbacks
# ═══════════════════════════════════════════════════════════════

@app.route("/webhook/vapi/call-ended", methods=["POST"])
def vapi_call_ended():
    """
    Vapi POSTs here when an AI call ends.

    Payload includes:
    - call.id: Vapi call ID
    - call.status: ended, voicemail, transferred, no-answer
    - call.transcript: Full conversation transcript
    - call.summary: AI-generated summary
    - call.duration: Duration in seconds
    - customer.number: The lead's phone number
    """
    data = request.json or {}
    logger.info("="*50)
    logger.info("Vapi call-ended webhook received")
    logger.info("Payload: %s", json.dumps(data, indent=2)[:500])

    # Extract call data
    call_data    = data.get("call", {})
    call_id      = call_data.get("id", "unknown")
    call_status  = call_data.get("status", "ended")
    transcript   = call_data.get("transcript", "")
    summary      = call_data.get("summary", "")
    duration     = call_data.get("durationSeconds", 0)
    customer_num = data.get("customer", {}).get("number", "")

    logger.info("  Call ID   : %s", call_id)
    logger.info("  Status    : %s", call_status)
    logger.info("  Duration  : %s seconds", duration)
    logger.info("  Customer  : %s", customer_num)

    if not customer_num:
        logger.warning("  No customer number in payload")
        return {"status": "error", "message": "No customer number"}, 400

    # ── Find the lead by phone ──────────────────────────────
    lead = find_lead_by_phone(customer_num)

    if not lead:
        logger.warning("  Unknown number: %s — not in leads DB", customer_num)
        return {"status": "ok", "message": "Number not in leads database"}

    logger.info("  Lead found: %s (%s)", lead["business_name"], lead["city"])

    # ── Map Vapi status to our status ───────────────────────
    status_map = {
        "ended":       "called",
        "voicemail":   "voicemail",
        "transferred": "transferred",
        "no-answer":   "no_answer",
        "failed":      "failed",
    }
    our_status = status_map.get(call_status, "called")

    # ── Check transcript for intent ─────────────────────────
    intent = None
    if transcript:
        transcript_lower = transcript.lower()
        # Detect interest signals
        if any(phrase in transcript_lower for phrase in [
            "yes, connect me", "sure, that sounds good",
            "i'm interested", "tell me more", "how much",
            "what's the price", "let's talk"
        ]):
            intent = "interested"
            our_status = "interested"
        # Detect disinterest
        elif any(phrase in transcript_lower for phrase in [
            "not interested", "no thanks", "don't call",
            "remove me", "stop calling"
        ]):
            intent = "declined"
            our_status = "declined"

    # ── Update lead ────────────────────────────────────────
    update_fields = {
        "call_status":   our_status,
        "call_sid":      call_id,
        "call_transcript": transcript[:5000] if transcript else None,
        "call_summary":  summary[:1000] if summary else None,
        "call_duration": duration,
        "last_call_at":  datetime.now().isoformat(),
    }
    update_lead(lead["id"], update_fields)

    # ── Log to outreach_log ────────────────────────────────
    conn = get_connection()
    conn.execute("""
        INSERT INTO outreach_log
            (lead_id, channel, direction, body, transcript, duration, status, external_id, sent_at)
        VALUES (?, 'call', 'outbound', ?, ?, ?, ?, ?, datetime('now'))
    """, (lead["id"], f"AI call - {our_status}", transcript[:2000] if transcript else None,
          duration, our_status, call_id))
    conn.commit()
    conn.close()

    # ── Alert Joel for interested leads ────────────────────
    if our_status in ["interested", "transferred"]:
        alert_msg = (
            f"CALL HOT LEAD! {lead['business_name']} ({lead['city']}) "
            f"Status:{our_status} Duration:{duration}s "
            f"Call ID:{call_id}"
        )
        send_sms(JOEL_PHONE, alert_msg[:160])
        logger.info("  Alert sent to Joel for hot lead")

    logger.info("  DB updated — call_status: %s", our_status)

    return {"status": "ok", "lead_id": lead["id"], "call_status": our_status}


@app.route("/webhook/vapi/transcript", methods=["POST"])
def vapi_transcript():
    """
    Vapi POSTs here with real-time transcript updates during a call.
    Useful for logging conversation as it happens.
    """
    data = request.json or {}
    call_id     = data.get("call", {}).get("id", "")
    customer_num = data.get("customer", {}).get("number", "")
    transcript  = data.get("transcript", "")

    logger.info("Vapi transcript update for call %s", call_id)
    logger.debug("Transcript: %s", transcript[:200])

    # We don't update the DB in real-time — that happens in call-ended
    return {"status": "ok"}


@app.route("/webhook/vapi/status", methods=["POST"])
def vapi_status():
    """
    Vapi POSTs here for call status updates (ringing, in-progress, etc.)
    """
    data = request.json or {}
    call_id  = data.get("call", {}).get("id", "")
    status   = data.get("call", {}).get("status", "")

    logger.info("Vapi status update: call %s → %s", call_id, status)

    return {"status": "ok"}


# ── Run ────────────────────────────────────────────────────
if __name__ == "__main__":
    port = int(os.environ.get("REPLY_HANDLER_PORT", 5001))

    print(f"\n  Piney Digital — Webhook Handler (Module 7)")
    print(f"  Listening on: http://localhost:{port}")
    print(f"\n  Endpoints:")
    print(f"    POST /webhook/sms              — Twilio SMS replies")
    print(f"    POST /webhook/vapi/call-ended  — Vapi call completion")
    print(f"    POST /webhook/vapi/transcript  — Vapi real-time transcript")
    print(f"    POST /webhook/vapi/status      — Vapi call status")
    print(f"    GET  /webhook/health           — Health check")
    print(f"    GET  /replies                  — View recent replies")
    print(f"\n  Next steps:")
    print(f"  1. Run: ngrok http {port}")
    print(f"  2. Copy the ngrok HTTPS URL")
    print(f"  3. Twilio console → Phone Numbers → your 936 number")
    print(f"     → Messaging → A message comes in → Webhook")
    print(f"     → Paste: https://xxxx.ngrok.io/webhook/sms")
    print(f"  4. Vapi dashboard → Settings → Webhooks")
    print(f"     → Add: https://xxxx.ngrok.io/webhook/vapi/call-ended")
    print(f"\n  Joel alert phone: {JOEL_PHONE or 'NOT SET — add JOEL_PHONE to .env'}")
    print(f"  AI provider    : {'Groq' if GROQ_API_KEY else 'Anthropic' if ANTHROPIC_KEY else 'NOT SET'}\n")

    app.run(host="0.0.0.0", port=port, debug=False)
