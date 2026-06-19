"""
sender.py — SMS sending engine
Piney Digital Outreach System — Module 5

Reads all queued leads from DB and sends personalized SMS via Twilio.

Safety features:
  - Sends ONLY between 8:00am–6:00pm Central Time (America/Chicago)
  - Weekdays only (Mon–Fri) — no Saturday or Sunday sends
  - Rate limited to 20 SMS per hour (carrier-safe)
  - Dry run mode — logs everything but sends nothing
  - Skips leads with no phone number
  - Marks each lead sent/failed in DB after attempt
  - Full send log in outreach_log table
"""

import sys
import os
import time
import json
import logging
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from modules.database import get_connection, update_lead, init_db
from modules.utils import load_env

logger = logging.getLogger(__name__)

load_env()

TWILIO_SID      = os.environ.get("TWILIO_ACCOUNT_SID", "")
TWILIO_TOKEN    = os.environ.get("TWILIO_AUTH_TOKEN", "")
TWILIO_FROM     = os.environ.get("TWILIO_PHONE_NUMBER", "")
TWILIO_MSG_SID  = os.environ.get("TWILIO_MESSAGING_SID", "")

# ── Sending schedule (Central Time) ───────────────────────
TIMEZONE        = "America/Chicago"   # Lufkin, TX — Central Time
SEND_HOUR_START = 8                   # 8:00 AM Central
SEND_HOUR_END   = 18                  # 6:00 PM Central (18:00)
SEND_WEEKDAYS   = [0, 1, 2, 3, 4]    # Mon=0 Tue=1 Wed=2 Thu=3 Fri=4

# ── Rate limiting ──────────────────────────────────────────
SENDS_PER_HOUR  = 20                  # max SMS per hour — carrier safe
DELAY_SECONDS   = 3600 / SENDS_PER_HOUR  # 180 seconds between sends


# ── Time check ─────────────────────────────────────────────
def is_sending_window() -> tuple[bool, str]:
    """
    Returns (allowed, reason_string).
    Checks current Central Time against allowed window.
    """
    try:
        import pytz
        ct_zone = pytz.timezone(TIMEZONE)
        now_ct  = datetime.now(ct_zone)
    except ImportError:
        # pytz not installed — fall back to UTC offset (-6 CST / -5 CDT)
        from datetime import timezone, timedelta
        # Determine if DST is active (rough check: Mar 2nd Sun to Nov 1st Sun)
        now_utc  = datetime.now(timezone.utc)
        month    = now_utc.month
        is_dst   = 3 <= month <= 10
        offset   = timedelta(hours=-5 if is_dst else -6)
        now_ct   = datetime.now(timezone(offset))

    hour    = now_ct.hour
    weekday = now_ct.weekday()
    ts      = now_ct.strftime("%I:%M %p CT, %A")

    if weekday not in SEND_WEEKDAYS:
        return False, f"Weekend — no sends. Current time: {ts}"

    if hour < SEND_HOUR_START:
        return False, f"Too early ({ts}) — sends start at 8:00 AM CT"

    if hour >= SEND_HOUR_END:
        return False, f"Too late ({ts}) — sends stopped at 6:00 PM CT"

    return True, f"In window — {ts}"


def get_central_time_str() -> str:
    """Return current Central Time as a readable string."""
    try:
        import pytz
        ct_zone = pytz.timezone(TIMEZONE)
        return datetime.now(ct_zone).strftime("%Y-%m-%d %I:%M %p CT")
    except ImportError:
        return datetime.now().strftime("%Y-%m-%d %H:%M") + " (local)"


# ── Twilio sender ──────────────────────────────────────────
def send_sms(to_number: str, body: str, dry_run: bool = False) -> tuple[bool, str]:
    """
    Send a single SMS via Twilio.
    Returns (success, message_sid_or_error).
    """
    if dry_run:
        return True, "DRY_RUN_NO_SEND"

    if not all([TWILIO_SID, TWILIO_TOKEN, TWILIO_FROM]):
        return False, "Missing Twilio credentials in .env"

    try:
        from twilio.rest import Client
        client = Client(TWILIO_SID, TWILIO_TOKEN)

        # Use Messaging Service if configured (required for 10DLC)
        if TWILIO_MSG_SID:
            msg = client.messages.create(
                messaging_service_sid=TWILIO_MSG_SID,
                to=to_number,
                body=body,
            )
        else:
            msg = client.messages.create(
                from_=TWILIO_FROM,
                to=to_number,
                body=body,
            )

        return True, msg.sid

    except Exception as e:
        return False, str(e)


# ── Log to outreach_log table ──────────────────────────────
def log_outreach(lead_id: int, body: str, status: str, sid: str):
    try:
        conn = get_connection()
        conn.execute("""
            INSERT INTO outreach_log
                (lead_id, channel, direction, body, status, sent_at)
            VALUES (?, 'sms', 'outbound', ?, ?, datetime('now'))
        """, (lead_id, body, status))
        conn.commit()
        conn.close()
    except Exception as e:
        logger.error("Failed to log outreach: %s", e)


# ── Format phone number ────────────────────────────────────
def format_e164(phone: str) -> str:
    """Convert (936) 123-4567 to +19361234567"""
    import re
    digits = re.sub(r"\D", "", phone)
    if len(digits) == 10:
        return f"+1{digits}"
    if len(digits) == 11 and digits[0] == "1":
        return f"+{digits}"
    return ""


# ── Main sending runner ────────────────────────────────────
def run_sender(
    limit:     int  = None,
    dry_run:   bool = False,
    force:     bool = False,   # skip time window check (for testing)
    min_score: int  = 60,
):
    """
    Send queued SMS messages to all qualifying leads.

    limit     : max messages to send this run (None = all queued)
    dry_run   : log everything, send nothing
    force     : ignore time window check (testing only)
    min_score : minimum lead score to include
    """
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler("logs/sender.log"),
        ]
    )

    init_db()

    mode = "DRY RUN" if dry_run else "LIVE"
    logger.info("="*60)
    logger.info("Piney Digital — SMS Sender")
    logger.info("Mode     : %s", mode)
    logger.info("Time     : %s", get_central_time_str())
    logger.info("Window   : %d:00 AM – %d:00 PM CT, Mon–Fri",
                SEND_HOUR_START, SEND_HOUR_END - 12 if SEND_HOUR_END > 12 else SEND_HOUR_END)
    logger.info("Rate     : %d SMS/hour (%ds between sends)", SENDS_PER_HOUR, int(DELAY_SECONDS))
    logger.info("="*60)

    # ── Time window check ──────────────────────────────────
    if not force:
        allowed, reason = is_sending_window()
        if not allowed:
            logger.warning("Outside sending window: %s", reason)
            logger.info("Use --force to override (testing only)")
            return {"sent": 0, "failed": 0, "skipped": 0, "reason": reason}
        else:
            logger.info("Sending window: %s", reason)

    # ── Load queued leads ──────────────────────────────────
    conn = get_connection()
    c    = conn.cursor()

    query = """
        SELECT id, business_name, city, phone, lead_score,
               site_status, notes
        FROM leads
        WHERE outreach_status = 'queued'
          AND lead_score >= ?
          AND phone IS NOT NULL
          AND phone != ''
        ORDER BY lead_score DESC
    """
    params = [min_score]
    if limit:
        query += f" LIMIT {limit}"

    c.execute(query, params)
    leads = [dict(zip([d[0] for d in c.description], r)) for r in c.fetchall()]
    conn.close()

    total = len(leads)
    logger.info("Leads queued and ready: %d", total)

    if total == 0:
        logger.info("Nothing to send. Run: python run.py write")
        return {"sent": 0, "failed": 0, "skipped": 0}

    sent    = 0
    failed  = 0
    skipped = 0

    for i, lead in enumerate(leads, 1):
        lead_id = lead["id"]
        name    = lead["business_name"]
        phone   = lead.get("phone", "")
        score   = lead.get("lead_score", 0)

        # Parse stored message from notes field
        try:
            notes    = json.loads(lead.get("notes") or "{}")
            sms_body = notes.get("sms", "")
        except (json.JSONDecodeError, TypeError):
            sms_body = ""

        if not sms_body:
            logger.warning("  [%d/%d] %s — no message found, skipping", i, total, name)
            skipped += 1
            continue

        # Format phone to E.164
        e164 = format_e164(phone)
        if not e164:
            logger.warning("  [%d/%d] %s — invalid phone %s, skipping", i, total, name, phone)
            skipped += 1
            continue

        logger.info("  [%d/%d] %s | %s | score:%d", i, total, name, e164, score)
        logger.info("    MSG: %s", sms_body)

        # ── Re-check window before each send ──────────────
        if not force:
            allowed, reason = is_sending_window()
            if not allowed:
                logger.warning("  Sending window closed mid-run: %s", reason)
                logger.info("  Stopping — %d sent so far. Resume tomorrow.", sent)
                break

        # ── Send ───────────────────────────────────────────
        success, result = send_sms(e164, sms_body, dry_run=dry_run)

        if success:
            sent += 1
            status_label = "dry_run" if dry_run else "sent"
            logger.info("    ✓ %s | SID: %s", status_label.upper(), result)

            update_lead(lead_id, {
                "outreach_status": "sent",
                "email_sent_at":   datetime.now().isoformat(),
            })
            log_outreach(lead_id, sms_body, status_label, result)

        else:
            failed += 1
            logger.error("    ✗ FAILED: %s", result)
            update_lead(lead_id, {"outreach_status": "failed"})
            log_outreach(lead_id, sms_body, "failed", result)

        # ── Rate limit delay ───────────────────────────────
        if i < total:
            if dry_run:
                time.sleep(0.1)  # fast in dry run
            else:
                logger.info("    Waiting %ds (rate limit)…", int(DELAY_SECONDS))
                time.sleep(DELAY_SECONDS)

    # ── Summary ────────────────────────────────────────────
    logger.info("="*60)
    logger.info("Send session complete")
    logger.info("  Sent    : %d", sent)
    logger.info("  Failed  : %d", failed)
    logger.info("  Skipped : %d", skipped)
    if not dry_run and sent > 0:
        logger.info("  Est. cost: $%.4f (Twilio)", sent * 0.0079)
    logger.info("="*60)

    return {"sent": sent, "failed": failed, "skipped": skipped}


if __name__ == "__main__":
    import sys
    dry   = "--dry"   in sys.argv
    force = "--force" in sys.argv
    run_sender(dry_run=dry, force=force)
