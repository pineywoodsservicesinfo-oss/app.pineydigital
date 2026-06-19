"""
sequence_manager.py — Multi-touch outreach sequence manager
Piney Digital Outreach System

Manages the progression of leads through multi-step campaigns:
  - Day 1: Initial email
  - Day 4: Follow-up 1
  - Day 7: Follow-up 2
  - Day 14: Final email

Tracks state per lead and handles:
  - Sequence progression
  - Reply detection (stops sequence)
  - Opt-out handling
"""

import sys
import json
import logging
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from modules.database import (
    get_connection,
    init_db,
    get_sequence,
    create_sequence,
    advance_sequence,
    pause_sequence,
)
from config.settings import SEQUENCE_TIMING

logger = logging.getLogger(__name__)

# Default sequence configuration
DEFAULT_SEQUENCE = [
    {"day": 1, "type": "initial", "subject_template": "Quick question about {business_name}"},
    {"day": 4, "type": "follow_up_1", "subject_template": "Following up: {business_name}"},
    {"day": 7, "type": "follow_up_2", "subject_template": "One more thing about {business_name}"},
    {"day": 14, "type": "breakup", "subject_template": "Last note on {business_name}"},
]


class SequenceManager:
    """Manages multi-touch outreach sequences for leads."""

    def __init__(self, sequence_config: list = None):
        """
        Initialize sequence manager.

        Args:
            sequence_config: List of sequence steps with 'day' and 'type' keys
        """
        self.sequence = sequence_config or DEFAULT_SEQUENCE

    def get_next_step(self, current_step: int) -> dict | None:
        """
        Get the next step in the sequence.

        Args:
            current_step: Current step index (0-indexed)

        Returns:
            Dict with step info or None if sequence complete
        """
        next_idx = current_step + 1
        if next_idx >= len(self.sequence):
            return None
        return self.sequence[next_idx]

    def get_step(self, step_index: int) -> dict | None:
        """Get step configuration by index."""
        if 0 <= step_index < len(self.sequence):
            return self.sequence[step_index]
        return None

    def calculate_next_send_date(self, step_index: int) -> str:
        """
        Calculate the next send date based on sequence timing.

        Args:
            step_index: Index of the next step

        Returns:
            ISO format date string
        """
        if step_index >= len(self.sequence):
            return None

        step = self.sequence[step_index]
        days_from_now = step["day"]
        next_date = datetime.now() + timedelta(days=days_from_now)
        return next_date.isoformat()

    def get_leads_ready_for_step(self, step_index: int = None, limit: int = 100) -> list:
        """
        Get leads that are ready for their next sequence step.

        Args:
            step_index: Specific step to get leads for (None = all ready)
            limit: Max leads to return

        Returns:
            List of lead dicts ready for outreach
        """
        conn = get_connection()
        c = conn.cursor()

        # Get leads with active sequences that are due for next step
        query = """
            SELECT l.id, l.business_name, l.owner_email, l.decision_makers,
                   l.lead_score, l.notes, s.current_step, s.next_send_at
            FROM leads l
            JOIN outreach_sequences s ON l.id = s.lead_id
            WHERE s.status = 'active'
              AND (s.next_send_at IS NULL OR s.next_send_at <= datetime('now'))
        """

        if step_index is not None:
            query += f" AND s.current_step = {step_index}"

        query += f" ORDER BY l.lead_score DESC LIMIT {limit}"

        c.execute(query)
        leads = [dict(zip([d[0] for d in c.description], r)) for r in c.fetchall()]
        conn.close()

        return leads

    def start_sequence(self, lead_id: int) -> int:
        """
        Start a new sequence for a lead.

        Args:
            lead_id: ID of the lead

        Returns:
            Sequence ID
        """
        return create_sequence(lead_id)

    def complete_step(self, lead_id: int) -> dict:
        """
        Mark current step complete and advance to next.

        Args:
            lead_id: ID of the lead

        Returns:
            Dict with next step info or None if complete
        """
        return advance_sequence(lead_id)

    def pause_sequence(self, lead_id: int, reason: str = "reply"):
        """
        Pause a sequence (e.g., after reply or opt-out).

        Args:
            lead_id: ID of the lead
            reason: Reason for pausing
        """
        pause_sequence(lead_id)
        logger.info("Paused sequence for lead %d: %s", lead_id, reason)

    def get_sequence_status(self, lead_id: int) -> dict:
        """
        Get current sequence status for a lead.

        Args:
            lead_id: ID of the lead

        Returns:
            Dict with sequence status info
        """
        seq = get_sequence(lead_id)
        if not seq:
            return {"status": "no_sequence", "current_step": None, "next_step": None}

        current_step = seq.get("current_step", 0)
        step_config = self.get_step(current_step)
        next_step = self.get_next_step(current_step)

        return {
            "status": seq.get("status"),
            "current_step": current_step,
            "current_type": step_config.get("type") if step_config else None,
            "next_step": next_step["day"] if next_step else None,
            "next_type": next_step["type"] if next_step else None,
            "last_sent_at": seq.get("last_sent_at"),
        }

    def estimate_sequence_length(self) -> int:
        """Get total days in the sequence."""
        if not self.sequence:
            return 0
        return max(step["day"] for step in self.sequence)


def get_leads_needing_initial_outreach(limit: int = 100) -> list:
    """
    Get leads that need initial outreach (no sequence started).

    Args:
        limit: Max leads to return

    Returns:
        List of lead dicts
    """
    conn = get_connection()
    c = conn.cursor()

    query = """
        SELECT id, business_name, city, owner_email, decision_makers,
               lead_score, notes, locations_count, category
        FROM leads
        WHERE pipeline_type = 'enterprise'
          AND outreach_status = 'new'
          AND lead_score >= 50
          AND id NOT IN (SELECT lead_id FROM outreach_sequences)
        ORDER BY lead_score DESC
        LIMIT ?
    """

    c.execute(query, (limit,))
    leads = [dict(zip([d[0] for d in c.description], r)) for r in c.fetchall()]
    conn.close()

    return leads


def get_leads_needing_followup(step_index: int, limit: int = 100) -> list:
    """
    Get leads that need follow-up at a specific sequence step.

    Args:
        step_index: Step index (0 = initial, 1 = follow_up_1, etc.)
        limit: Max leads to return

    Returns:
        List of lead dicts
    """
    conn = get_connection()
    c = conn.cursor()

    query = """
        SELECT l.id, l.business_name, l.city, l.owner_email, l.decision_makers,
               l.lead_score, l.notes, l.locations_count, l.category,
               s.current_step, s.last_sent_at
        FROM leads l
        JOIN outreach_sequences s ON l.id = s.lead_id
        WHERE s.status = 'active'
          AND s.current_step = ?
          AND s.next_send_at <= datetime('now')
        ORDER BY l.lead_score DESC
        LIMIT ?
    """

    c.execute(query, (step_index, limit))
    leads = [dict(zip([d[0] for d in c.description], r)) for r in c.fetchall()]
    conn.close()

    return leads


def run_sequence_manager(dry_run: bool = False, limit: int = 50):
    """
    Check and process leads ready for their next sequence step.

    This is typically called by a scheduled job or cron.

    Args:
        dry_run: If True, only report what would be done
        limit: Max leads to process

    Returns:
        Dict with counts of processed leads
    """
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler("logs/sequence_manager.log"),
        ],
    )

    init_db()

    manager = SequenceManager()

    logger.info("=" * 60)
    logger.info("Sequence Manager — Checking for due follow-ups")
    logger.info("=" * 60)

    # Get leads ready for each step
    results = {
        "initial": 0,
        "follow_up_1": 0,
        "follow_up_2": 0,
        "breakup": 0,
        "completed": 0,
    }

    # Check each step
    for step_idx, step in enumerate(manager.sequence):
        step_type = step["type"]
        leads = get_leads_needing_followup(step_idx, limit=limit)

        if leads:
            logger.info("  %s: %d leads ready", step_type, len(leads))

            for lead in leads:
                if dry_run:
                    logger.info("    [DRY RUN] Would process: %s", lead["business_name"])
                else:
                    # In production, this would trigger the email writer
                    # For now, just log it
                    logger.info("    Ready for %s: %s (score: %d)",
                               step_type, lead["business_name"], lead["lead_score"])

                results[step_type] += 1

    # Get leads needing initial outreach
    initial_leads = get_leads_needing_initial_outreach(limit=limit)
    if initial_leads:
        logger.info("  initial_outreach: %d leads need first email", len(initial_leads))
        results["initial"] = len(initial_leads)

    logger.info("=" * 60)
    logger.info("Summary:")
    for step_type, count in results.items():
        if count > 0:
            logger.info("  %-15s: %d", step_type, count)
    logger.info("=" * 60)

    return results


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Sequence manager")
    parser.add_argument("--dry-run", action="store_true", help="Preview without action")
    parser.add_argument("--limit", type=int, default=50, help="Max leads to process")

    args = parser.parse_args()

    run_sequence_manager(dry_run=args.dry_run, limit=args.limit)