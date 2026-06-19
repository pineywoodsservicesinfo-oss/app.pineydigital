"""
writer.py — AI-powered outreach message writer
Piney Digital Outreach System — Module 4

Uses Groq (Llama 3) to write personalized SMS for every lead.
Falls back to Anthropic Claude if ANTHROPIC_API_KEY is set.
Messages stored in DB — nothing sent here. Module 5 handles sending.

Message rules:
  - Under 160 characters (single SMS segment)
  - Mentions business by name
  - References their situation (no website / outdated)
  - Local East Texas feel — not corporate
  - One clear CTA — reply or visit pineydigital.com
  - Opt-out appended automatically
"""

import sys, os, time, logging, json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from modules.database import get_connection, update_lead, init_db
from modules.utils import load_env

logger = logging.getLogger(__name__)

load_env()
GROQ_API_KEY      = os.environ.get("GROQ_API_KEY", "")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")

# ── Category context ───────────────────────────────────────
CATEGORY_CONTEXT = {
    "HVAC": {
        "urgency":    "summer is 6 weeks away and AC calls are about to spike",
        "lost_money": "homeowners Google HVAC near me when their AC breaks — competitors with websites get those calls",
        "proof":      "97% of people search online before calling a contractor",
    },
    "plumber": {
        "urgency":    "plumbing emergencies happen at 2am — people Google, not the phonebook",
        "lost_money": "every burst pipe call going to a competitor with a website is money out of your pocket",
        "proof":      "4 out of 5 people search online before calling a plumber",
    },
    "electrician": {
        "urgency":    "homeowners search Google before calling any electrician",
        "lost_money": "without a website you are invisible to anyone searching electrician in Lufkin",
        "proof":      "a simple website with your license number and reviews doubles call volume",
    },
    "roofing contractor": {
        "urgency":    "storm season is coming — homeowners search for roofers right after bad weather",
        "lost_money": "after every East Texas storm, dozens of homeowners Google roofers and you are not showing up",
        "proof":      "roofing is the top category where customers check online before calling",
    },
    "auto repair shop": {
        "urgency":    "people search auto repair near me every single day on their phones",
        "lost_money": "Google Maps shows businesses with websites first — shops without one get skipped",
        "proof":      "over 80% of people check a shop online before driving there",
    },
    "auto mechanic": {
        "urgency":    "drivers search for a mechanic they can trust before their car breaks down completely",
        "lost_money": "without a website you rely 100% on word of mouth — a site works for you 24/7",
        "proof":      "a simple website with hours, location and services gets you found on Google Maps",
    },
}

DEFAULT_CONTEXT = {
    "urgency":    "customers search Google before calling any local business",
    "lost_money": "without a website you are invisible to anyone searching online",
    "proof":      "most people search online before calling a local business",
}

# ── System prompt ──────────────────────────────────────────
SYSTEM_PROMPT = """You are Joel Escoto, owner of Piney Digital in Lufkin, Texas.
You write short personal SMS messages to East Texas small business owners.
You are a neighbor, not a salesman. Keep it real and local.

Your goal: write a message so specific the owner thinks you researched them personally.

STRICT RULES:
1. Start with: Hey, Joel with Piney Digital in Lufkin.
2. Name the specific business in the message
3. State their exact problem clearly (no website or outdated site)
4. Give ONE reason this costs them money right now (use the context provided)
5. Include this link: pineydigital.com
6. End with low-pressure CTA: Worth a quick chat? or Happy to help.
7. UNDER 155 characters TOTAL — count carefully
8. No exclamation marks
9. No corporate phrases — no "I hope", "just reaching out", "checking in"
10. Sound like a real person texting, not a marketing blast

Follow-up SMS (sent 3 days later if no reply):
- Different angle from the first message
- Mention season or local pain point
- Casual friendly reminder not a second sales pitch
- Also under 155 characters

GOOD EXAMPLE (143 chars):
Hey, Joel with Piney Digital in Lufkin. Smith Plumbing has no site — pipes burst at 2am, people Google. $800 gets you found. pineydigital.com

BAD EXAMPLE:
Smith Plumbing needs a website, I can help with that, call me

Return ONLY valid JSON with no markdown fences and nothing outside the JSON object:
{
  "sms": "message under 155 chars",
  "follow_up_sms": "follow-up under 155 chars"
}"""

# ── Build AI client ────────────────────────────────────────
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


# ── Write one message ──────────────────────────────────────
def write_message(lead: dict, client, provider: str) -> dict | None:
    name     = lead.get("business_name", "")
    city     = lead.get("city", "")
    category = lead.get("category", "")
    status   = lead.get("site_status", "none")
    owner    = lead.get("owner_name", "")

    if status == "none":
        situation = f"{name} in {city} has no website at all"
    elif status == "parked":
        situation = f"{name} in {city} has a parked domain but no real website"
    else:
        situation = f"{name} in {city} has an outdated website that's not mobile-friendly"

    ctx        = CATEGORY_CONTEXT.get(category, DEFAULT_CONTEXT)
    owner_line = f"Owner name is {owner} — address them if it feels natural." if owner else "Owner name unknown — do not guess."

    prompt = f"""Write outreach SMS messages for this lead:

Business : {name}
Category : {category}
City     : {city}, East Texas
Situation: {situation}
{owner_line}

Why this costs them money: {ctx['lost_money']}
Urgency right now: {ctx['urgency']}
Credibility fact: {ctx['proof']}

Remember:
- Start with: Hey, Joel with Piney Digital in Lufkin.
- Include: pineydigital.com
- Under 155 characters
- Do NOT include opt-out text — added automatically
- Return ONLY the JSON object, nothing else"""

    try:
        if provider == "groq":
            response = client.chat.completions.create(
                model="llama-3.3-70b-versatile",
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user",   "content": prompt},
                ],
                max_tokens=300,
                temperature=0.7,
            )
            raw = response.choices[0].message.content.strip()

        elif provider == "anthropic":
            response = client.messages.create(
                model="claude-sonnet-4-20250514",
                max_tokens=300,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": prompt}],
            )
            raw = response.content[0].text.strip()

        else:
            return None

        # Strip markdown fences if model added them
        if "```" in raw:
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        raw = raw.strip()

        # Sometimes models add text before/after JSON — extract just the object
        start = raw.find("{")
        end   = raw.rfind("}") + 1
        if start != -1 and end > start:
            raw = raw[start:end]

        result = json.loads(raw)

        # Enforce 155 char limit
        for key in ("sms", "follow_up_sms"):
            if len(result.get(key, "")) > 155:
                result[key] = result[key][:152] + "..."

        return result

    except json.JSONDecodeError as e:
        logger.error("    JSON error for %s: %s | raw: %s", name, e, raw[:120])
        return None
    except Exception as e:
        logger.error("    API error for %s: %s", name, e)
        return None


# ── Main runner ────────────────────────────────────────────
def run_writer(min_score: int = 60, limit: int = None, dry_run: bool = False):
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler("logs/writer.log"),
        ]
    )

    client, provider = get_client()
    if not client:
        logger.error("No AI API key found. Add GROQ_API_KEY or ANTHROPIC_API_KEY to .env")
        return {"error": "no_api_key"}

    logger.info("AI provider: %s", provider)

    init_db()
    conn = get_connection()
    c    = conn.cursor()

    query = """
        SELECT id, business_name, city, category, phone,
               site_status, lead_score, owner_name, owner_email
        FROM leads
        WHERE lead_score >= ?
          AND outreach_status = 'new'
          AND site_status IN ('none','parked','outdated')
        ORDER BY lead_score DESC
    """
    params = [min_score]
    if limit:
        query += f" LIMIT {limit}"

    c.execute(query, params)
    leads = [dict(zip([d[0] for d in c.description], r)) for r in c.fetchall()]
    conn.close()

    total    = len(leads)
    mode_lbl = "DRY RUN" if dry_run else "LIVE"

    logger.info("="*60)
    logger.info("Piney Digital — AI Message Writer")
    logger.info("Mode     : %s", mode_lbl)
    logger.info("Provider : %s", provider)
    logger.info("Leads    : %d  (score >= %d)", total, min_score)
    logger.info("="*60)

    written  = 0
    failed   = 0
    previews = []

    for i, lead in enumerate(leads, 1):
        name = lead["business_name"]
        logger.info("  [%d/%d] %s — %s", i, total, name, lead["city"])

        result = write_message(lead, client, provider)

        if not result:
            failed += 1
            continue

        sms       = result.get("sms", "")
        follow_up = result.get("follow_up_sms", "")

        full_sms      = sms      + " Reply STOP to opt out."
        full_followup = follow_up + " Reply STOP to opt out."

        logger.info("    SMS (%d chars): %s", len(full_sms), full_sms)
        logger.info("    Follow-up    : %s", full_followup)

        if dry_run:
            previews.append({
                "business":  name,
                "city":      lead["city"],
                "phone":     lead.get("phone", ""),
                "status":    lead["site_status"],
                "score":     lead["lead_score"],
                "sms":       full_sms,
                "follow_up": full_followup,
            })
        else:
            msg_data = json.dumps({
                "sms":       full_sms,
                "follow_up": full_followup,
            })
            update_lead(lead["id"], {
                "outreach_status": "queued",
                "notes":           msg_data,
            })

        written += 1
        time.sleep(0.3)

    logger.info("="*60)
    logger.info("Done — Written: %d  Failed: %d", written, failed)
    if not dry_run:
        logger.info("Messages queued. Run: python run.py send")
    logger.info("="*60)

    if dry_run and previews:
        print("\n" + "="*60)
        print(f"PREVIEW — {min(5, len(previews))} sample messages")
        print("="*60)
        for p in previews[:5]:
            print(f"\n  Business  : {p['business']} ({p['city']})")
            print(f"  Phone     : {p['phone']}")
            print(f"  Status    : {p['status']}  Score: {p['score']}")
            print(f"  SMS       : {p['sms']}")
            print(f"  Chars     : {len(p['sms'])}")
            print(f"  Follow-up : {p['follow_up']}")
            print("-"*60)

    return {"total": total, "written": written, "failed": failed, "previews": previews}


if __name__ == "__main__":
    run_writer(dry_run=True, limit=5)
