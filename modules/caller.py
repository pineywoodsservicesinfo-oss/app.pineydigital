"""
caller.py — AI voice calling via Vapi
Piney Digital Outreach System — Module 8

Makes outbound calls to leads using Vapi AI voice platform.
Personalizes scripts with business data and transfers hot leads.

Safety features:
  - Calls ONLY between 9:00am–7:00pm Central Time (caller-friendly hours)
  - Weekdays only (Mon–Fri) — no weekend calls
  - Rate limited to prevent spamming
  - Dry run mode — logs everything but calls nothing
  - Skips leads with no phone number
  - Transfers interested leads to Joel's phone
  - Logs all outcomes to database

Setup:
  1. Get Vapi API key from https://vapi.ai
  2. Add VAPI_API_KEY to .env
  3. Add VAPI_PHONE_NUMBER (your Twilio number for caller ID)
  4. Add JOEL_PHONE for hot lead transfers
  5. Run: python run.py call --dry
"""

import sys
import os
import json
import logging
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from modules.database import get_connection, update_lead, init_db
from modules.utils import load_env

logger = logging.getLogger(__name__)

load_env()

# ── Configuration ───────────────────────────────────────────
VAPI_API_KEY     = os.environ.get("VAPI_API_KEY", "")
VAPI_PHONE_ID    = os.environ.get("VAPI_PHONE_ID", "83d11999-99e7-4bc1-9880-79cc81e83d25")  # Vapi Phone Number ID
JOEL_PHONE       = os.environ.get("JOEL_PHONE", "")          # For hot lead transfers
VAPI_ASSISTANT_ID = os.environ.get("VAPI_ASSISTANT_ID", "f886225b-79eb-4c04-9266-2f3fd0239218")  # Vapi Assistant ID

# ── Calling schedule (Central Time) ────────────────────────
TIMEZONE         = "America/Chicago"   # Lufkin, TX — Central Time
CALL_HOUR_START  = 9                    # 9:00 AM Central
CALL_HOUR_END    = 19                   # 7:00 PM Central (19:00)
CALL_WEEKDAYS    = [0, 1, 2, 3, 4]     # Mon=0 Tue=1 Wed=2 Thu=3 Fri=4

# ── Rate limiting ────────────────────────────────────────────
CALLS_PER_HOUR   = 15                   # Max calls per hour
DELAY_SECONDS    = 3600 / CALLS_PER_HOUR  # ~240 seconds between calls

# ── Vapi API ─────────────────────────────────────────────────
VAPI_BASE_URL    = "https://api.vapi.ai"


# ── Time check ─────────────────────────────────────────────
def is_calling_window() -> tuple[bool, str]:
    """
    Returns (allowed, reason_string).
    Checks current Central Time against allowed calling window.
    """
    try:
        import pytz
        ct_zone = pytz.timezone(TIMEZONE)
        now_ct  = datetime.now(ct_zone)
    except ImportError:
        from datetime import timezone, timedelta
        now_utc  = datetime.now(timezone.utc)
        month    = now_utc.month
        is_dst   = 3 <= month <= 10
        offset   = timedelta(hours=-5 if is_dst else -6)
        now_ct   = datetime.now(timezone(offset))

    hour    = now_ct.hour
    weekday = now_ct.weekday()
    ts      = now_ct.strftime("%I:%M %p CT, %A")

    if weekday not in CALL_WEEKDAYS:
        return False, f"Weekend — no calls. Current time: {ts}"

    if hour < CALL_HOUR_START:
        return False, f"Too early ({ts}) — calls start at 9:00 AM CT"

    if hour >= CALL_HOUR_END:
        return False, f"Too late ({ts}) — calls stopped at 7:00 PM CT"

    return True, f"In window — {ts}"


def get_central_time_str() -> str:
    """Return current Central Time as a readable string."""
    try:
        import pytz
        ct_zone = pytz.timezone(TIMEZONE)
        return datetime.now(ct_zone).strftime("%Y-%m-%d %I:%M %p CT")
    except ImportError:
        return datetime.now().strftime("%Y-%m-%d %H:%M") + " (local)"


# ── Phone formatting ─────────────────────────────────────────
def format_e164(phone: str) -> str:
    """Convert (936) 123-4567 to +19361234567"""
    import re
    digits = re.sub(r"\D", "", phone)
    if len(digits) == 10:
        return f"+1{digits}"
    if len(digits) == 11 and digits[0] == "1":
        return f"+{digits}"
    return ""


# ── Vapi API client ─────────────────────────────────────────
def create_vapi_call(
    to_number: str,
    lead_data: dict,
    dry_run: bool = False
) -> tuple[bool, str]:
    """
    Initiate an outbound call via Vapi API using a pre-configured Assistant.

    Args:
        to_number: Phone number in E.164 format (+1XXXXXXXXXX)
        lead_data: Dict with business_name, city, category, owner_name, etc.
        dry_run: If True, log but don't actually call

    Returns:
        (success, call_id_or_error)
    """
    if dry_run:
        logger.info("  [DRY RUN] Would call %s for %s using Assistant %s", 
                    to_number, lead_data.get("business_name"), VAPI_ASSISTANT_ID)
        return True, "DRY_RUN_NO_CALL"

    if not VAPI_API_KEY:
        return False, "Missing VAPI_API_KEY in .env"

    try:
        import requests
    except ImportError:
        return False, "requests library not installed"

    # Build the Vapi call payload using the Assistant ID
    # We pass lead data as assistantOverrides so the AI knows who it's calling
    payload = {
        "assistantId": VAPI_ASSISTANT_ID,
        "phoneNumberId": VAPI_PHONE_ID,
        "customer": {
            "number": to_number
        },
        "assistantOverrides": {
            "variableValues": {
                "business_name": lead_data.get("business_name", "your business"),
                "city": lead_data.get("city", "East Texas"),
                "category": lead_data.get("category", "business"),
                "owner_name": lead_data.get("owner_name", "Unknown")
            }
        }
    }

    headers = {
        "Authorization": f"Bearer {VAPI_API_KEY}",
        "Content-Type": "application/json"
    }

    try:
        response = requests.post(
            f"{VAPI_BASE_URL}/call",
            headers=headers,
            json=payload,
            timeout=30
        )

        if response.status_code == 201:
            data = response.json()
            call_id = data.get("id", "unknown")
            logger.info("  ✓ Call initiated: %s", call_id)
            return True, call_id
        else:
            error = response.text[:200]
            logger.error("  ✗ Vapi error: %s", error)
            return False, f"Vapi error: {response.status_code}"

    except requests.exceptions.RequestException as e:
        logger.error("  ✗ Request failed: %s", str(e))
        return False, str(e)


# ── Log call to database ─────────────────────────────────────
def log_call(
    lead_id: int,
    call_id: str,
    status: str,
    transcript: str = None,
    duration: int = None
):
    """Log a call attempt to outreach_log."""
    try:
        conn = get_connection()
        conn.execute("""
            INSERT INTO outreach_log
                (lead_id, channel, direction, body, transcript, duration, status, external_id, sent_at)
            VALUES (?, 'call', 'outbound', ?, ?, ?, ?, ?, datetime('now'))
        """, (lead_id, f"AI call to lead", transcript, duration, status, call_id))
        conn.commit()
        conn.close()
    except Exception as e:
        logger.error("Failed to log call: %s", e)


# ── Main calling runner ──────────────────────────────────────
def run_caller(
    limit:     int  = None,
    dry_run:   bool = False,
    force:     bool = False,   # skip time window check (for testing)
    min_score: int  = 60,
    call_status: str = None,   # filter by call_status
):
    """
    Make AI calls to all qualifying leads.

    Args:
        limit     : max calls to make this run (None = all queued)
        dry_run   : log everything, call nothing
        force     : ignore time window check (testing only)
        min_score : minimum lead score to include
        call_status: filter leads by call_status (None = new leads only)
    """
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(os.path.join(os.path.dirname(__file__), "..", "logs", "caller.log"),),
        ]
    )

    init_db()

    mode = "DRY RUN" if dry_run else "LIVE"
    logger.info("="*60)
    logger.info("Piney Digital — AI Voice Caller")
    logger.info("Mode     : %s", mode)
    logger.info("Time     : %s", get_central_time_str())
    logger.info("Window   : %d:00 AM – %d:00 PM CT, Mon–Fri",
                CALL_HOUR_START, CALL_HOUR_END - 12 if CALL_HOUR_END > 12 else CALL_HOUR_END)
    logger.info("Rate     : %d calls/hour (%ds between calls)", CALLS_PER_HOUR, int(DELAY_SECONDS))
    logger.info("="*60)

    # ── Time window check ──────────────────────────────────
    if not force:
        allowed, reason = is_calling_window()
        if not allowed:
            logger.warning("Outside calling window: %s", reason)
            logger.info("Use --force to override (testing only)")
            return {"called": 0, "failed": 0, "skipped": 0, "reason": reason}
        else:
            logger.info("Calling window: %s", reason)

    # ── Load leads to call ───────────────────────────────────
    conn = get_connection()
    c    = conn.cursor()

    # Get leads that haven't been called yet, or need retry
    query = """
        SELECT id, business_name, city, category, phone, owner_name,
               lead_score, site_status, notes, call_status, call_attempts
        FROM leads
        WHERE phone IS NOT NULL
          AND phone != ''
          AND lead_score >= ?
    """
    params = [min_score]

    # Filter by call status
    if call_status:
        query += " AND call_status = ?"
        params.append(call_status)
    else:
        # Default: only call leads that haven't been called
        query += " AND (call_status IS NULL OR call_status IN ('new', 'queued', 'no_answer'))"

    query += " ORDER BY lead_score DESC"

    if limit:
        query += f" LIMIT {limit}"

    c.execute(query, params)
    leads = [dict(zip([d[0] for d in c.description], r)) for r in c.fetchall()]
    conn.close()

    total = len(leads)
    logger.info("Leads ready to call: %d", total)

    if total == 0:
        logger.info("No leads to call. Run: python run.py scrape")
        return {"called": 0, "failed": 0, "skipped": 0}

    called   = 0
    failed   = 0
    skipped  = 0

    for i, lead in enumerate(leads, 1):
        lead_id     = lead["id"]
        name        = lead["business_name"]
        phone       = lead.get("phone", "")
        city        = lead.get("city", "")
        score       = lead.get("lead_score", 0)
        attempts    = lead.get("call_attempts", 0)

        # Format phone to E.164
        e164 = format_e164(phone)
        if not e164:
            logger.warning("  [%d/%d] %s — invalid phone %s, skipping", i, total, name, phone)
            skipped += 1
            continue

        logger.info("  [%d/%d] %s | %s | %s | score:%d | attempts:%d",
                   i, total, name, city, e164, score, attempts)

        # ── Re-check window before each call ──────────────
        if not force:
            allowed, reason = is_calling_window()
            if not allowed:
                logger.warning("  Calling window closed mid-run: %s", reason)
                logger.info("  Stopping — %d calls so far. Resume tomorrow.", called)
                break

        # ── Make the call ───────────────────────────────────
        success, result = create_vapi_call(e164, lead, dry_run=dry_run)

        if success:
            called += 1
            status = "queued" if dry_run else "called"

            # Update lead
            update_lead(lead_id, {
                "call_status": status,
                "call_sid": result if not dry_run else None,
                "call_attempts": attempts + 1,
                "last_call_at": datetime.now().isoformat(),
            })

            log_call(lead_id, result, status)

        else:
            failed += 1
            logger.error("  ✗ FAILED: %s", result)
            update_lead(lead_id, {
                "call_status": "failed",
                "call_attempts": attempts + 1,
            })
            log_call(lead_id, result, "failed")

        # ── Rate limit delay ───────────────────────────────
        if i < total and not dry_run:
            logger.info("    Waiting %ds (rate limit)…", int(DELAY_SECONDS))
            time.sleep(DELAY_SECONDS)

    # ── Summary ────────────────────────────────────────────
    logger.info("="*60)
    logger.info("Call session complete")
    logger.info("  Called  : %d", called)
    logger.info("  Failed  : %d", failed)
    logger.info("  Skipped : %d", skipped)
    if not dry_run and called > 0:
        logger.info("  Est. cost: $%.2f (Vapi ~$0.13/min)", called * 0.13)
    logger.info("="*60)

    return {"called": called, "failed": failed, "skipped": skipped}


# ── View call history ───────────────────────────────────────
def print_call_history(limit: int = 30):
    """Print recent call history."""
    init_db()
    conn = get_connection()
    c = conn.cursor()

    c.execute("""
        SELECT l.business_name, l.city, l.phone, l.call_status,
               l.call_attempts, l.last_call_at, l.lead_score
        FROM leads l
        WHERE l.call_status IS NOT NULL
        ORDER BY l.last_call_at DESC
        LIMIT ?
    """, (limit,))

    rows = [dict(zip([d[0] for d in c.description], r)) for r in c.fetchall()]
    conn.close()

    if not rows:
        print("\n  No calls made yet. Run: python run.py call\n")
        return

    print(f"\n── {len(rows)} recent calls ──────────────────────────────")
    print(f"  {'#':<4} {'Business':<28} {'City':<12} {'Status':<12} {'Att':<5} {'Phone'}")
    print("  " + "─"*80)
    for i, r in enumerate(rows, 1):
        print(f"  {i:<4} {(r['business_name'] or '')[:27]:<28} "
              f"{(r['city'] or '')[:11]:<12} "
              f"{(r['call_status'] or ''):<12} "
              f"{r['call_attempts']:<5} "
              f"{r['phone'] or ''}")
    print()


# ── CLI entry point ─────────────────────────────────────────
if __name__ == "__main__":
    import sys
    dry   = "--dry"   in sys.argv
    force = "--force" in sys.argv
    run_caller(dry_run=dry, force=force)