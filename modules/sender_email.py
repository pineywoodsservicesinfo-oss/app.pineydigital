"""
sender_email.py — Enterprise email sending engine
Piney Digital Outreach System

Sends professional emails via Resend API for enterprise leads.

Features:
  - Uses existing Resend integration
  - Multi-touch sequence support
  - Professional email formatting
  - Dry run mode for testing
"""

import sys
import os
import time
import json
import logging
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

# Load environment variables BEFORE importing modules that use them
from modules.utils import load_env
load_env()

from modules.database import (
    get_connection,
    update_lead,
    init_db,
    create_sequence,
    get_sequence,
    advance_sequence,
)
from modules.email_sender import send_email as send_email_resend, is_email_configured
from config.settings import FROM_EMAIL, FROM_NAME, SEQUENCE_TIMING

logger = logging.getLogger(__name__)

# ── Sending schedule (Central Time) ───────────────────────────────
TIMEZONE = "America/Chicago"
SEND_HOUR_START = 8  # 8:00 AM Central
SEND_HOUR_END = 18  # 6:00 PM Central
SEND_WEEKDAYS = [0, 1, 2, 3, 4]  # Mon-Fri

# ── Rate limiting ─────────────────────────────────────────────────
EMAILS_PER_HOUR = 50  # Conservative for deliverability
DELAY_SECONDS = 3600 / EMAILS_PER_HOUR


def is_sending_window() -> tuple[bool, str]:
    """
    Returns (allowed, reason_string).
    Checks current Central Time against allowed window.
    """
    try:
        import pytz

        ct_zone = pytz.timezone(TIMEZONE)
        now_ct = datetime.now(ct_zone)
    except ImportError:
        from datetime import timezone, timedelta

        now_utc = datetime.now(timezone.utc)
        month = now_utc.month
        is_dst = 3 <= month <= 10
        offset = timedelta(hours=-5 if is_dst else -6)
        now_ct = datetime.now(timezone(offset))

    hour = now_ct.hour
    weekday = now_ct.weekday()
    ts = now_ct.strftime("%I:%M %p CT, %A")

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


def send_email_via_resend(
    to_email: str,
    subject: str,
    body: str,
    dry_run: bool = False,
    from_email: str = None,
    from_name: str = None,
    html_body: str = None,
) -> tuple[bool, str]:
    """
    Send a single email via Resend API.
    Uses the existing email_sender module.

    Returns (success, message_id_or_error).
    """
    if dry_run:
        return True, "DRY_RUN_NO_SEND"

    if not is_email_configured():
        return False, "Email not configured. Add RESEND_API_KEY to .env"

    # Use configured defaults
    from_email = from_email or FROM_EMAIL or "joel@pineydigital.com"
    from_name = from_name or FROM_NAME or "Joel Escoto"

    # Import HTML template generator
    from modules.email_template import text_to_paragraphs, get_initial_email_html

    # Generate HTML if not provided
    if not html_body:
        paragraphs = text_to_paragraphs(body)
        html_body = get_initial_email_html(
            business_name="",  # Will be replaced by first paragraph
            city="",
            body_paragraphs=paragraphs,
            cta_url="https://pineydigital.com",
            cta_text="Learn More",
        )

    # Call the existing Resend-based send function
    return send_email_resend(
        to_email=to_email,
        subject=subject,
        body=body,
        html_body=html_body,
    )


def log_email_outreach(lead_id: int, subject: str, body: str, status: str, message_id: str, sequence_step: int = 1):
    """Log email to outreach_log table."""
    try:
        conn = get_connection()
        conn.execute(
            """
            INSERT INTO outreach_log
                (lead_id, channel, direction, subject, body, status, external_id, sequence_step, sent_at)
            VALUES (?, 'email', 'outbound', ?, ?, ?, ?, ?, datetime('now'))
            """,
            (lead_id, subject, body, status, message_id, sequence_step),
        )
        conn.commit()
        conn.close()
    except Exception as e:
        logger.error("Failed to log email outreach: %s", e)


def get_email_for_lead(lead: dict) -> str:
    """Get the best email address for a lead."""
    # First check decision_makers for primary contact
    decision_makers = lead.get("decision_makers")
    if decision_makers:
        try:
            dms = json.loads(decision_makers) if isinstance(decision_makers, str) else decision_makers
            for dm in dms:
                if dm.get("is_primary") and dm.get("email"):
                    return dm["email"]
        except:
            pass

    # Fall back to owner_email
    if lead.get("owner_email"):
        return lead["owner_email"]

    return None


def run_email_sender(
    limit: int = None,
    dry_run: bool = False,
    force: bool = False,
    min_score: int = 50,
    sequence_step: int = None,
):
    """
    Send queued emails to enterprise leads.

    limit        : max emails to send this run (None = all queued)
    dry_run      : log everything, send nothing
    force        : ignore time window check (testing only)
    min_score    : minimum lead score to include
    sequence_step: send only leads at this sequence step (None = all)
    """
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler("logs/email_sender.log"),
        ],
    )

    init_db()

    mode = "DRY RUN" if dry_run else "LIVE"
    logger.info("=" * 60)
    logger.info("Piney Digital — Email Sender (Enterprise)")
    logger.info("Mode     : %s", mode)
    logger.info("Time     : %s", get_central_time_str())
    logger.info(
        "Window   : %d:00 AM – %d:00 PM CT, Mon–Fri",
        SEND_HOUR_START,
        SEND_HOUR_END - 12 if SEND_HOUR_END > 12 else SEND_HOUR_END,
    )
    logger.info("Rate     : %d emails/hour (%ds between sends)", EMAILS_PER_HOUR, int(DELAY_SECONDS))
    logger.info("=" * 60)

    # ── Time window check ──────────────────────────────────────
    if not force:
        allowed, reason = is_sending_window()
        if not allowed:
            logger.warning("Outside sending window: %s", reason)
            logger.info("Use --force to override (testing only)")
            return {"sent": 0, "failed": 0, "skipped": 0, "reason": reason}
        else:
            logger.info("Sending window: %s", reason)

    # ── Load queued leads ──────────────────────────────────────
    conn = get_connection()
    c = conn.cursor()

    query = """
        SELECT id, business_name, city, owner_email, decision_makers,
               lead_score, notes, locations_count, category
        FROM leads
        WHERE outreach_status = 'queued'
          AND pipeline_type = 'enterprise'
          AND lead_score >= ?
          AND (email_quality IS NULL OR email_quality = 'personal')
        ORDER BY lead_score DESC
    """
    params = [min_score]
    if limit:
        query += f" LIMIT {limit}"

    c.execute(query, params)
    leads = [dict(zip([d[0] for d in c.description], r)) for r in c.fetchall()]

    total = len(leads)
    logger.info("Enterprise leads queued: %d", total)

    if total == 0:
        conn.close()
        logger.info("Nothing to send. Run: python run.py write --type enterprise")
        return {"sent": 0, "failed": 0, "skipped": 0}

    sent = 0
    failed = 0
    skipped = 0
    duplicates_skipped = 0

    # Track emails already sent to (deduplication)
    sent_emails = set()

    # Load already-sent emails from database to prevent re-sending
    c2 = conn.cursor()
    c2.execute("""
        SELECT DISTINCT owner_email
        FROM leads
        WHERE pipeline_type = 'enterprise'
          AND outreach_status = 'sent'
          AND owner_email IS NOT NULL
    """)
    for row in c2.fetchall():
        sent_emails.add(row[0].lower())
    logger.info("Already sent to %d unique emails (from previous runs)", len(sent_emails))

    conn.close()

    for i, lead in enumerate(leads, 1):
        lead_id = lead["id"]
        name = lead["business_name"]
        score = lead.get("lead_score", 0)

        # Get email address
        to_email = get_email_for_lead(lead)
        if not to_email:
            logger.warning("  [%d/%d] %s — no email found, skipping", i, total, name)
            skipped += 1
            continue

        # ── Deduplication check ─────────────────────────────────
        email_lower = to_email.lower()
        if email_lower in sent_emails:
            logger.warning("  [%d/%d] %s — duplicate email %s, skipping", i, total, name, to_email)
            duplicates_skipped += 1
            skipped += 1
            continue

        # Parse stored email from notes field
        try:
            notes = json.loads(lead.get("notes") or "{}")
            subject = notes.get("subject", f"Quick question about {name}")
            body = notes.get("body", "")
            email_type = notes.get("type", "initial")
        except (json.JSONDecodeError, TypeError):
            subject = f"Quick question about {name}"
            body = ""
            email_type = "initial"

        if not body:
            logger.warning("  [%d/%d] %s — no email body found, skipping", i, total, name)
            skipped += 1
            continue

        # Generate HTML email with branding and UTM tracking
        from modules.email_template import text_to_paragraphs, get_initial_email_html
        paragraphs = text_to_paragraphs(body)
        html_body = get_initial_email_html(
            business_name=name,
            city=lead.get("city", ""),
            body_paragraphs=paragraphs,
            cta_url="https://pineydigital.com",
            cta_text="Learn More",
            campaign="enterprise_outreach",
            email_type=email_type,
        )

        # Determine sequence step
        seq = get_sequence(lead_id)
        current_step = seq["current_step"] if seq else 0
        sequence_step_num = current_step + 1

        logger.info("  [%d/%d] %s | %s | score:%d | step:%d", i, total, name, to_email, score, sequence_step_num)
        logger.info("    Subject: %s", subject)
        logger.info("    Body preview: %s...", body[:100])

        # ── Re-check window before each send ───────────────────
        if not force:
            allowed, reason = is_sending_window()
            if not allowed:
                logger.warning("  Sending window closed mid-run: %s", reason)
                logger.info("  Stopping — %d sent so far. Resume tomorrow.", sent)
                break

        # ── Send ───────────────────────────────────────────────
        success, result = send_email_via_resend(to_email, subject, body, dry_run=dry_run, html_body=html_body)

        if success:
            sent += 1
            status_label = "dry_run" if dry_run else "sent"
            logger.info("    ✓ %s | ID: %s", status_label.upper(), result)

            # Track this email as sent (for deduplication within this run)
            sent_emails.add(email_lower)

            # Only update database if not dry run
            if not dry_run:
                # Update lead status
                update_lead(lead_id, {
                    "outreach_status": "sent",
                    "email_sent_at": datetime.now().isoformat(),
                })

                # Create/update sequence tracking
                if not seq:
                    create_sequence(lead_id)
                else:
                    advance_sequence(lead_id)

                # Log to outreach_log
                log_email_outreach(lead_id, subject, body, status_label, result, sequence_step_num)

        else:
            failed += 1
            logger.error("    ✗ FAILED: %s", result)
            update_lead(lead_id, {"outreach_status": "failed"})
            log_email_outreach(lead_id, subject, body, "failed", result, sequence_step_num)

        # ── Rate limit delay ───────────────────────────────────
        if i < total:
            if dry_run:
                time.sleep(0.1)
            else:
                logger.info("    Waiting %ds (rate limit)…", int(DELAY_SECONDS))
                time.sleep(DELAY_SECONDS)

    # ── Summary ────────────────────────────────────────────────
    logger.info("=" * 60)
    logger.info("Email send session complete")
    logger.info("  Sent            : %d", sent)
    logger.info("  Failed          : %d", failed)
    logger.info("  Skipped         : %d", skipped)
    logger.info("  Duplicates skipped: %d", duplicates_skipped)
    logger.info("=" * 60)

    return {"sent": sent, "failed": failed, "skipped": skipped, "duplicates": duplicates_skipped}


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Enterprise email sender")
    parser.add_argument("--dry-run", action="store_true", help="Preview without sending")
    parser.add_argument("--force", action="store_true", help="Ignore time window")
    parser.add_argument("--limit", type=int, help="Max emails to send")

    args = parser.parse_args()

    run_email_sender(dry_run=args.dry_run, force=args.force, limit=args.limit)