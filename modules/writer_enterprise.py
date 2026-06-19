"""
writer_enterprise.py — AI-powered enterprise outreach writer
Piney Digital Outreach System

Writes professional email sequences for enterprise leads:
  - Initial outreach
  - Follow-up emails
  - Industry-specific messaging
  - Multi-touch sequences

Uses Groq (Llama 3) or Anthropic Claude.
"""

import sys
import os
import time
import logging
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from modules.database import get_connection, update_lead, init_db
from modules.utils import load_env

logger = logging.getLogger(__name__)

load_env()
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")

# ── Industry pain points ──────────────────────────────────────
INDUSTRY_PAIN_POINTS = {
    "restaurant_chain": {
        "primary": "multi-location menu and ordering complexity",
        "secondary": [
            "Brand inconsistency across location websites",
            "No centralized reservation or ordering system",
            "Difficulty managing online reviews across locations",
            "Generic website builders that don't scale",
        ],
        "value_prop": "custom platform that handles multi-location menus, ordering, and loyalty in one system",
    },
    "hospitality_group": {
        "primary": "reliance on OTAs taking 15-25% commission",
        "secondary": [
            "Weak direct booking website pushing customers to OTAs",
            "No flexible pricing for seasonal rates",
            "Difficulty managing reviews across platforms",
            "No loyalty program for repeat guests",
        ],
        "value_prop": "direct booking platform with loyalty integration that reduces OTA dependency",
    },
    "professional_services": {
        "primary": "manual appointment scheduling across locations",
        "secondary": [
            "No unified client history system",
            "Manual reporting across locations",
            "Difficulty tracking client preferences",
            "Paper-based intake processes",
        ],
        "value_prop": "centralized client management with scheduling, history, and reporting",
    },
    "franchise_auto": {
        "primary": "brand inconsistency across franchise locations",
        "secondary": [
            "No centralized appointment booking",
            "Difficulty tracking customer lifetime value",
            "Manual follow-up for service reminders",
            "Each location managing their own outdated website",
        ],
        "value_prop": "franchise-wide platform with booking, reminders, and customer tracking",
    },
    "private_services": {
        "primary": "no online booking or customer management",
        "secondary": [
            "Missing out on online scheduling demand",
            "No customer history or preferences tracking",
            "Manual appointment reminders",
            "Difficulty managing multi-location operations",
        ],
        "value_prop": "platform that handles scheduling, reminders, and customer history",
    },
}

DEFAULT_PAIN_POINTS = {
    "primary": "outdated technology holding back growth",
    "secondary": [
        "Using generic tools that don't fit their operations",
        "Manual processes that don't scale",
        "No unified system for managing locations",
        "Difficulty making data-driven decisions",
    ],
    "value_prop": "custom platform built around their specific workflow",
}

# ── Email templates ───────────────────────────────────────────
EMAIL_SUBJECTS = {
    "initial": [
        "Quick question about {business_name}",
        "{business_name} operations — one thought",
        "Multi-location systems at {business_name}",
        "Saw something about {business_name}",
    ],
    "follow_up_1": [
        "Following up: {business_name} operations",
        "Quick follow-up on {business_name}",
        "One more thing about {business_name}",
    ],
    "follow_up_2": [
        "Last note on {business_name}",
        "Final thought for {business_name}",
        "Closing the loop: {business_name}",
    ],
    "breakup": [
        "Taking {business_name} off my list",
        "Removing {business_name} from outreach",
    ],
}

# ── System prompt ──────────────────────────────────────────────
SYSTEM_PROMPT = """You are a business development consultant at Piney Digital, a custom platform development agency specializing in multi-location businesses.

Your audience: Operations executives, owners of restaurant chains, hospitality groups, franchise operators with 3-50 locations.

Your goal: Write personalized, professional outreach emails that demonstrate understanding of their specific operational challenges.

TONE GUIDELINES:
- Professional but not corporate
- Consultative, not sales-y
- Demonstrate industry knowledge
- Lead with value, not features
- Reference specific signals you found (hiring, expansion, tech stack)
- Be concise — busy executives don't read long emails

EMAIL STRUCTURE:
1. Personalized opening referencing their business and location
2. One specific observation about their current situation
3. A relevant pain point their industry commonly faces
4. Brief mention of similar businesses you've helped (case study style)
5. Low-pressure call to action

NEVER:
- Use casual language ("Hey", "just reaching out", "checking in")
- Send generic templates
- Lead with pricing
- Pressure for meetings in first contact
- Make claims about specific results without context

Return ONLY valid JSON with no markdown fences:
{
  "subject": "subject line under 60 chars",
  "body": "email body 100-200 words",
  "personalization_signals": ["signal1", "signal2"]
}"""

# ── Build AI client ────────────────────────────────────────────
def get_client():
    """Returns (client, provider) tuple. Prefers Groq, falls back to Anthropic."""
    if GROQ_API_KEY:
        try:
            from groq import Groq

            return Groq(api_key=GROQ_API_KEY), "groq"
        except ImportError:
            logger.error("groq not installed. Run: pip install groq")

    if ANTHROPIC_API_KEY:
        try:
            import anthropic

            return anthropic.Anthropic(api_key=ANTHROPIC_API_KEY), "anthropic"
        except ImportError:
            logger.error("anthropic not installed. Run: pip install anthropic")

    return None, None


# ── Write email sequence ───────────────────────────────────────
def write_email(lead: dict, email_type: str, client, provider: str) -> dict | None:
    """
    Write a single email in the sequence.

    email_type: 'initial', 'follow_up_1', 'follow_up_2', 'breakup'
    """
    name = lead.get("business_name", "")
    city = lead.get("city", "")
    industry = lead.get("category", "")
    locations = lead.get("locations_count", 1) or 1
    tech_stack = lead.get("tech_stack", "")
    growth_signals = lead.get("growth_signals", "")

    # Parse tech stack and growth signals if JSON
    try:
        if tech_stack:
            tech_stack = json.loads(tech_stack) if isinstance(tech_stack, str) else tech_stack
        else:
            tech_stack = []
    except:
        tech_stack = []

    try:
        if growth_signals:
            growth_signals = json.loads(growth_signals) if isinstance(growth_signals, str) else growth_signals
        else:
            growth_signals = {}
    except:
        growth_signals = {}

    # Get industry-specific context
    ctx = INDUSTRY_PAIN_POINTS.get(industry, DEFAULT_PAIN_POINTS)

    # Build personalization hints
    personalization = []
    if locations > 1:
        personalization.append(f"Has {locations} locations")
    if tech_stack:
        personalization.append(f"Uses {', '.join(tech_stack[:3])}")
    if growth_signals.get("signals"):
        personalization.extend(growth_signals["signals"][:2])

    # Build prompt based on email type
    if email_type == "initial":
        prompt = f"""Write an initial outreach email for this enterprise lead:

Business: {name}
Industry: {industry}
Location: {city}
Locations: {locations}

Industry context:
- Primary pain point: {ctx['primary']}
- Value proposition: {ctx['value_prop']}

Personalization signals found: {', '.join(personalization) if personalization else 'Limited research available'}

Write a professional but conversational email that:
1. Opens with something specific about their business
2. Mentions one observation or signal
3. References a pain point relevant to {industry} businesses with {locations} locations
4. Briefly mentions Piney Digital helps multi-location businesses
5. Ends with a low-pressure CTA

Keep it under 180 words. Be specific but not presumptuous."""

    elif email_type == "follow_up_1":
        prompt = f"""Write a follow-up email (3 days after initial) for:

Business: {name}
Industry: {industry}
Locations: {locations}

Previous email was about {ctx['primary']}.

Write a brief follow-up that:
1. References the previous email
2. Adds one new piece of value or insight
3. Keeps it shorter than the first email
4. Low-pressure CTA

Keep it under 100 words."""

    elif email_type == "follow_up_2":
        prompt = f"""Write a second follow-up email (7 days after initial) for:

Business: {name}
Industry: {industry}

Write a brief follow-up that:
1. Adds value without being pushy
2. Maybe shares a relevant insight about {industry}
3. Simple CTA

Keep it under 80 words."""

    elif email_type == "breakup":
        prompt = f"""Write a breakup email for:

Business: {name}

This is sent 14 days after initial outreach with no response.
Write a polite "removing you from my list" email that:
1. Is respectful and professional
2. Leaves the door open
3. Is very brief

Keep it under 50 words."""

    try:
        if provider == "groq":
            response = client.chat.completions.create(
                model="llama-3.3-70b-versatile",
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": prompt},
                ],
                max_tokens=500,
                temperature=0.7,
            )
            raw = response.choices[0].message.content.strip()

        elif provider == "anthropic":
            response = client.messages.create(
                model="claude-sonnet-4-20250514",
                max_tokens=500,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": prompt}],
            )
            raw = response.content[0].text.strip()

        else:
            return None

        # Strip markdown fences if present
        if "```" in raw:
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        raw = raw.strip()

        # Extract JSON object
        start = raw.find("{")
        end = raw.rfind("}") + 1
        if start != -1 and end > start:
            raw = raw[start:end]

        result = json.loads(raw)

        # Add subject if not present
        if "subject" not in result:
            subjects = EMAIL_SUBJECTS.get(email_type, EMAIL_SUBJECTS["initial"])
            result["subject"] = subjects[0].replace("{business_name}", name)

        return result

    except json.JSONDecodeError as e:
        logger.error("    JSON error for %s: %s | raw: %s", name, e, raw[:120])
        return None
    except Exception as e:
        logger.error("    API error for %s: %s", name, e)
        return None


# ── Main runner ────────────────────────────────────────────────
def run_enterprise_writer(
    min_score: int = 50,
    limit: int = None,
    dry_run: bool = False,
    email_type: str = "initial",
):
    """Write emails for enterprise leads."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler("logs/enterprise_writer.log"),
        ],
    )

    client, provider = get_client()
    if not client:
        logger.error("No AI API key found. Add GROQ_API_KEY or ANTHROPIC_API_KEY to .env")
        return {"error": "no_api_key"}

    logger.info("AI provider: %s", provider)

    init_db()
    conn = get_connection()
    c = conn.cursor()

    query = """
        SELECT id, business_name, city, category, locations_count,
               tech_stack, growth_signals, lead_score, owner_email,
               decision_makers
        FROM leads
        WHERE lead_score >= ?
          AND pipeline_type = 'enterprise'
          AND outreach_status = 'new'
        ORDER BY lead_score DESC
    """
    params = [min_score]
    if limit:
        query += f" LIMIT {limit}"

    c.execute(query, params)
    leads = [dict(zip([d[0] for d in c.description], r)) for r in c.fetchall()]
    conn.close()

    total = len(leads)
    mode_lbl = "DRY RUN" if dry_run else "LIVE"

    logger.info("=" * 60)
    logger.info("Enterprise Email Writer")
    logger.info("Mode      : %s", mode_lbl)
    logger.info("Provider  : %s", provider)
    logger.info("Email type: %s", email_type)
    logger.info("Leads     : %d  (score >= %d)", total, min_score)
    logger.info("=" * 60)

    written = 0
    failed = 0
    previews = []

    for i, lead in enumerate(leads, 1):
        name = lead["business_name"]
        logger.info("  [%d/%d] %s — %s", i, total, name, lead["city"])

        result = write_email(lead, email_type, client, provider)

        if not result:
            failed += 1
            continue

        subject = result.get("subject", "")
        body = result.get("body", "")
        signals = result.get("personalization_signals", [])

        logger.info("    Subject: %s", subject)
        logger.info("    Body (%d words): %s...", len(body.split()), body[:100])

        if dry_run:
            previews.append({
                "business": name,
                "city": lead["city"],
                "locations": lead.get("locations_count", 1),
                "industry": lead.get("category", ""),
                "subject": subject,
                "body": body,
                "signals": signals,
            })
        else:
            # Store email in decision_makers or notes
            email_data = json.dumps({
                "subject": subject,
                "body": body,
                "type": email_type,
                "signals": signals,
            })
            update_lead(lead["id"], {
                "outreach_status": "queued",
                "notes": email_data,
            })

        written += 1
        time.sleep(0.3)

    logger.info("=" * 60)
    logger.info("Done — Written: %d  Failed: %d", written, failed)
    if not dry_run:
        logger.info("Emails queued. Run: python run.py send --channel email")
    logger.info("=" * 60)

    if dry_run and previews:
        print("\n" + "=" * 60)
        print(f"PREVIEW — {min(3, len(previews))} sample emails")
        print("=" * 60)
        for p in previews[:3]:
            print(f"\n  Business  : {p['business']} ({p['city']})")
            print(f"  Industry  : {p['industry']}")
            print(f"  Locations : {p['locations']}")
            print(f"  Subject   : {p['subject']}")
            print(f"  Body      : {p['body'][:200]}...")
            print("-" * 60)

    return {"total": total, "written": written, "failed": failed, "previews": previews}


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Enterprise email writer")
    parser.add_argument("--dry-run", action="store_true", help="Preview without saving")
    parser.add_argument("--limit", type=int, default=5, help="Max leads to process")
    parser.add_argument("--type", default="initial", choices=["initial", "follow_up_1", "follow_up_2", "breakup"])

    args = parser.parse_args()

    run_enterprise_writer(dry_run=args.dry_run, limit=args.limit, email_type=args.type)