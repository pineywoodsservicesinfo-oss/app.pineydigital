"""
scorer_enterprise.py — Enterprise lead scoring engine
Piney Digital Outreach System

Scoring for enterprise leads based on:
  - Tech stack signals (modern tech = lower score, they don't need help)
  - Multi-location presence (3-10 locations = sweet spot)
  - Growth indicators (hiring, expansion)
  - Website quality (outdated = needs help)
  - Business size indicators

Score 0-100, higher = hotter lead.
"""

import sys
import re
import time
import logging
import requests
import json
from pathlib import Path
from datetime import datetime
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).parent.parent))

from modules.database import get_connection, update_lead, init_db

logger = logging.getLogger(__name__)

# ── Tech stack detection ──────────────────────────────────────
# Modern tech = they have IT resources, lower score
MODERN_TECH_STACK = {
    # E-commerce platforms (modern)
    "shopify": -10,
    "bigcommerce": -10,
    "woocommerce": -5,  # WordPress, common but functional
    "magento": -8,
    "squarespace": -5,
    "webflow": -8,

    # Booking systems (modern)
    "calendly": -5,
    "acuity": -5,
    "mindbody": -8,
    "booker": -8,
    "fresha": -5,

    # CRM systems (they have their act together)
    "salesforce": -15,
    "hubspot": -10,
    "zoho": -8,
    "pipedrive": -8,

    # Marketing automation
    "mailchimp": -5,
    "klaviyo": -8,
    "activecampaign": -8,

    # Modern frameworks (custom dev)
    "next.js": -10,
    "react": -8,
    "vue": -8,
    "angular": -8,

    # Payment systems
    "stripe": -5,
    "square": -5,
    "toast": -8,  # Restaurant POS
}

# Outdated tech = opportunity
OUTDATED_TECH_STACK = {
    # Old tech
    "jquery-1.": 10,
    "jquery-2.": 10,
    "bootstrap/3.": 5,
    "bootstrap/2.": 8,
    "flash": 15,
    "macromedia": 15,

    # Old copyright years
    "© 2012": 8, "© 2013": 8, "© 2014": 8,
    "© 2015": 6, "© 2016": 6, "© 2017": 6,
    "© 2018": 4, "© 2019": 4, "© 2020": 2,
    "© 2021": 1,
}

# ── Growth signals ───────────────────────────────────────────
GROWTH_SIGNALS = {
    # Hiring indicators
    "careers": 10,
    "we're hiring": 10,
    "join our team": 10,
    "job openings": 10,
    "now hiring": 10,
    "apply now": 5,

    # Expansion indicators
    "new location": 8,
    "coming soon": 5,
    "expanding": 8,
    "grand opening": 5,
}

# ── Pain point signals ───────────────────────────────────────
PAIN_POINT_SIGNALS = {
    # Booking/scheduling issues
    "call for appointment": 5,
    "book now": -3,  # Has booking, lower score
    "reserve": -3,
    "schedule online": -3,

    # Generic website builders (potential pain point)
    "wix.com": 5,
    "godaddy": 8,
    "weebly": 5,
    "wordpress.com": 3,
}

# ── Requests session ─────────────────────────────────────────
SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
})


def normalize_url(url: str) -> str:
    """Ensure URL has a scheme."""
    if not url:
        return ""
    url = url.strip().rstrip("/")
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    return url


def fetch_page(url: str, timeout: int = 10) -> tuple[int, str]:
    """Fetch a URL. Returns (status_code, html_body_lowercase)."""
    try:
        resp = SESSION.get(url, timeout=timeout, allow_redirects=True)
        return resp.status_code, resp.text.lower()
    except requests.exceptions.SSLError:
        try:
            http_url = url.replace("https://", "http://")
            resp = SESSION.get(http_url, timeout=timeout, allow_redirects=True)
            return resp.status_code, resp.text.lower()
        except Exception:
            return 0, ""
    except Exception:
        return 0, ""


def detect_tech_stack(html: str) -> dict:
    """
    Analyze HTML to detect tech stack.
    Returns dict with detected technologies and their scores.
    """
    detected = {}

    # Check for modern tech
    for tech, score in MODERN_TECH_STACK.items():
        if tech in html:
            detected[tech] = score

    # Check for outdated tech
    for tech, score in OUTDATED_TECH_STACK.items():
        if tech in html:
            detected[tech] = score

    return detected


def detect_growth_signals(html: str) -> dict:
    """
    Detect growth indicators in HTML.
    Returns dict with detected signals.
    """
    signals = {}

    for signal, score in GROWTH_SIGNALS.items():
        if signal in html:
            signals[signal] = score

    return signals


def detect_pain_points(html: str) -> dict:
    """
    Detect potential pain points in HTML.
    Returns dict with detected pain points.
    """
    pain_points = {}

    for signal, score in PAIN_POINT_SIGNALS.items():
        if signal in html:
            pain_points[signal] = score

    return pain_points


def calculate_enterprise_score(lead: dict, html: str = "") -> tuple[int, dict]:
    """
    Calculate enterprise lead score from 0-100.

    Returns (score, breakdown_dict).
    """
    breakdown = {
        "tech_stack_score": 0,
        "locations_score": 0,
        "growth_score": 0,
        "pain_point_score": 0,
        "website_quality_score": 0,
        "email_score": 0,
        "email_quality_score": 0,
        "detected_tech": [],
        "detected_signals": [],
        "detected_pain_points": [],
    }

    score = 0

    # 1. Email presence & quality (max +25)
    owner_email = lead.get("owner_email", "")
    email_quality = lead.get("email_quality", "")

    if owner_email and owner_email.strip():
        # Has email: +10 base
        score += 10
        breakdown["email_score"] = 10

        # Email quality bonus
        if email_quality == "personal":
            # Personal email = decision maker = +15 bonus
            score += 15
            breakdown["email_quality_score"] = 15
        elif email_quality == "generic":
            # Generic email = likely won't convert = 0 bonus
            breakdown["email_quality_score"] = 0
        else:
            # Unknown quality, partial bonus
            score += 5
            breakdown["email_quality_score"] = 5

    # 2. Tech stack analysis (max impact: -30 to +30)
    if html:
        tech_stack = detect_tech_stack(html)
        breakdown["detected_tech"] = list(tech_stack.keys())
        tech_score = sum(tech_stack.values())
        breakdown["tech_stack_score"] = tech_score
        score += tech_score

    # 3. Multi-location score (0 to +25)
    locations = lead.get("locations_count", 1) or 1

    if locations >= 3 and locations <= 10:
        # Sweet spot: 3-10 locations
        locations_score = 25
    elif locations >= 11 and locations <= 50:
        # Larger enterprise
        locations_score = 20
    elif locations >= 51:
        # Very large, might be too complex
        locations_score = 10
    elif locations == 2:
        # Just starting to expand
        locations_score = 10
    else:
        # Single location
        locations_score = 0

    breakdown["locations_score"] = locations_score
    score += locations_score

    # 4. Growth signals (0 to +15)
    if html:
        growth_signals = detect_growth_signals(html)
        breakdown["detected_signals"] = list(growth_signals.keys())
        growth_score = min(sum(growth_signals.values()), 15)
        breakdown["growth_score"] = growth_score
        score += growth_score

    # 5. Pain points (0 to +10)
    if html:
        pain_points = detect_pain_points(html)
        breakdown["detected_pain_points"] = list(pain_points.keys())
        pain_score = min(sum(pain_points.values()), 10)
        breakdown["pain_point_score"] = pain_score
        score += pain_score

    # 6. Website quality (based on existing site_status)
    site_status = lead.get("site_status", "")

    if site_status == "none" or site_status == "parked":
        # No website or parked = they definitely need help
        website_score = 25
    elif site_status == "outdated":
        website_score = 20
    elif site_status == "modern":
        website_score = 0
    else:
        # Unknown, assume moderate
        website_score = 10

    breakdown["website_quality_score"] = website_score
    score += website_score

    # Clamp score to 0-100
    final_score = max(0, min(100, score))

    return final_score, breakdown


def classify_site(url: str) -> tuple[str, int, str]:
    """
    Classify a website URL for enterprise scoring.
    Returns (status, base_score, html_content).
    """
    if not url:
        return "none", 30, ""  # No website = opportunity, but not as high for enterprise

    clean_url = normalize_url(url)
    status_code, html = fetch_page(clean_url)

    if status_code == 0 or status_code >= 400:
        return "none", 25, ""

    # Check for parked signals
    parked_signals = [
        "domain for sale",
        "this domain is for sale",
        "buy this domain",
        "parked by",
        "godaddy.com",
        "sedoparking",
    ]
    for signal in parked_signals:
        if signal in html:
            return "parked", 20, html

    # Check for modern signals
    modern_signals = ["next.js", "react", "vue", "webflow", "squarespace", "shopify"]
    modern_hits = sum(1 for s in modern_signals if s in html)
    if modern_hits >= 2:
        return "modern", 0, html

    # Check for outdated signals
    outdated_signals = ["jquery-1.", "jquery-2.", "flash", "macromedia"]
    outdated_hits = sum(1 for s in outdated_signals if s in html)

    has_viewport = 'name="viewport"' in html
    if not has_viewport:
        outdated_hits += 1

    if outdated_hits >= 1:
        return "outdated", 15, html

    # Default: exists but unclear
    return "exists", 10, html


def run_enterprise_scorer(limit: int = None, min_locations: int = None):
    """
    Main runner. Scores enterprise leads.
    """
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler("logs/enterprise_scorer.log"),
        ],
    )

    init_db()

    conn = get_connection()
    c = conn.cursor()

    # Get enterprise leads
    query = "SELECT * FROM leads WHERE pipeline_type = 'enterprise' OR pipeline_type IS NULL"
    if min_locations:
        query += f" AND locations_count >= {min_locations}"
    if limit:
        query += f" LIMIT {limit}"

    c.execute(query)
    leads = [dict(r) for r in c.fetchall()]
    conn.close()

    total = len(leads)
    logger.info("=" * 60)
    logger.info("Enterprise Lead Scorer starting")
    logger.info("Leads to score: %d", total)
    logger.info("=" * 60)

    scored = {"hot": 0, "warm": 0, "cold": 0}

    for i, lead in enumerate(leads, 1):
        name = lead.get("business_name", "")
        url = lead.get("website", "")
        lead_id = lead["id"]
        locations = lead.get("locations_count", 1) or 1

        # Classify site and get HTML
        site_status, base_score, html = classify_site(url)

        # Calculate enterprise score
        final_score, breakdown = calculate_enterprise_score(lead, html)

        # Update lead with scoring
        update_lead(lead_id, {
            "site_status": site_status,
            "lead_score": final_score,
            "tech_stack": json.dumps(breakdown["detected_tech"]) if breakdown["detected_tech"] else None,
            "growth_signals": json.dumps({
                "tech": breakdown.get("detected_tech", []),
                "signals": breakdown.get("detected_signals", []),
                "pain_points": breakdown.get("detected_pain_points", []),
            }) if breakdown["detected_signals"] or breakdown["detected_pain_points"] else None,
        })

        # Categorize
        if final_score >= 60:
            scored["hot"] += 1
            category = "HOT"
        elif final_score >= 40:
            scored["warm"] += 1
            category = "WARM"
        else:
            scored["cold"] += 1
            category = "COLD"

        if i % 10 == 0 or i == total:
            pct = int(i / total * 100)
            logger.info(
                "  [%3d%%] %d/%d scored | HOT:%d WARM:%d COLD:%d",
                pct, i, total,
                scored["hot"], scored["warm"], scored["cold"]
            )
        else:
            email_q = (lead.get("email_quality") or "none")[:4]
            logger.info(
                "  %-35s | locs:%-2d | email:%-4s | score:%-3d | %s",
                name[:34], locations, email_q, final_score, category
            )

        time.sleep(0.5)

    logger.info("=" * 60)
    logger.info("Enterprise scoring complete")
    logger.info("  HOT leads  (score >= 60): %d", scored["hot"])
    logger.info("  WARM leads (score 40-59): %d", scored["warm"])
    logger.info("  COLD leads (score < 40):  %d", scored["cold"])
    logger.info("=" * 60)

    return scored


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Enterprise lead scorer")
    parser.add_argument("--limit", type=int, help="Max leads to score")
    parser.add_argument("--min-locations", type=int, help="Minimum locations to include")

    args = parser.parse_args()

    run_enterprise_scorer(limit=args.limit, min_locations=args.min_locations)