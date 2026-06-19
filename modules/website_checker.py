"""
website_checker.py — Website qualification engine
Piney Digital Outreach System — Module 2

For every lead in the DB, determines:
  - has_website  : 0 (none) or 1 (yes)
  - site_status  : 'none' | 'parked' | 'outdated' | 'modern'
  - lead_score   : 0-100 (higher = hotter lead for outreach)

Scoring logic:
  no website   → score 95  (perfect prospect)
  parked       → score 80  (nearly as good)
  outdated     → score 60  (strong prospect)
  modern       → score 10  (skip)
"""

import sys
import re
import time
import logging
import requests
from pathlib import Path
from datetime import datetime
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).parent.parent))

from modules.database import get_leads, update_lead, init_db

logger = logging.getLogger(__name__)

# ── Parked / placeholder page signals ─────────────────────
PARKED_SIGNALS = [
    "domain for sale",
    "this domain is for sale",
    "buy this domain",
    "parked by",
    "godaddy.com",
    "sedoparking",
    "hugedomains",
    "dan.com",
    "afternic",
    "namecheap parking",
    "under construction",
    "coming soon",
    "this site is under construction",
    "website coming soon",
    "placeholder page",
]

# ── Outdated site signals ──────────────────────────────────
OUTDATED_SIGNALS = [
    # Old tech fingerprints
    "jquery-1.",
    "jquery-2.",
    "bootstrap/3.",
    "bootstrap/2.",
    "wp-content",          # WordPress alone isn't outdated but check further
    "flash",
    "macromedia",
    # Old copyright years in footer
    "© 2012", "© 2013", "© 2014", "© 2015",
    "© 2016", "© 2017", "© 2018",
    "copyright 2012","copyright 2013","copyright 2014",
    "copyright 2015","copyright 2016","copyright 2017","copyright 2018",
]

MODERN_SIGNALS = [
    # Modern frameworks / builders
    "next.js", "nextjs", "nuxt",
    "react", "vue.js", "angular",
    "webflow", "squarespace", "wix.com",
    "shopify",
    "framer",
    # Modern copyright
    "© 2023", "© 2024", "© 2025", "© 2026",
    "copyright 2023","copyright 2024","copyright 2025",
]

# ── Requests session ───────────────────────────────────────
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
    """
    Fetch a URL. Returns (status_code, html_body_lowercase).
    Returns (0, '') on connection error.
    """
    try:
        resp = SESSION.get(url, timeout=timeout, allow_redirects=True)
        return resp.status_code, resp.text.lower()
    except requests.exceptions.SSLError:
        # Try http fallback
        try:
            http_url = url.replace("https://", "http://")
            resp = SESSION.get(http_url, timeout=timeout, allow_redirects=True)
            return resp.status_code, resp.text.lower()
        except Exception:
            return 0, ""
    except Exception:
        return 0, ""


def classify_site(url: str) -> tuple[str, int]:
    """
    Classify a website URL.
    Returns (status, score) where status is one of:
      'none' | 'parked' | 'outdated' | 'modern'
    """
    if not url:
        return "none", 95

    clean_url = normalize_url(url)
    status_code, html = fetch_page(clean_url)

    # Unreachable / dead domain
    if status_code == 0 or status_code >= 400:
        return "none", 90

    html_snippet = html[:80000]  # only check first 80KB

    # Check parked signals
    for signal in PARKED_SIGNALS:
        if signal in html_snippet:
            return "parked", 80

    # Check modern signals (check these before outdated)
    modern_hits = sum(1 for s in MODERN_SIGNALS if s in html_snippet)
    if modern_hits >= 2:
        return "modern", 10

    # Check outdated signals
    outdated_hits = sum(1 for s in OUTDATED_SIGNALS if s in html_snippet)

    # Extra checks for outdated: no viewport meta = not mobile responsive
    has_viewport = 'name="viewport"' in html_snippet or "name='viewport'" in html_snippet
    if not has_viewport:
        outdated_hits += 2

    # No HTTPS = outdated
    if clean_url.startswith("http://"):
        outdated_hits += 1

    if outdated_hits >= 2:
        return "outdated", 60

    # Single modern signal or nothing conclusive — treat as modern
    if modern_hits >= 1:
        return "modern", 10

    # Default: exists but unclear — treat as outdated (they still need help)
    return "outdated", 50


def score_lead(lead: dict, site_status: str) -> int:
    """
    Final score combining site status + other lead signals.
    Max 100.
    """
    base_scores = {
        "none":     95,
        "parked":   80,
        "outdated": 60,
        "modern":   10,
    }
    score = base_scores.get(site_status, 50)

    # Boost: has a phone number (easier to reach)
    if lead.get("phone"):
        score = min(score + 3, 100)

    # Boost: lower review count = smaller business = more likely needs help
    reviews = lead.get("review_count") or 0
    if reviews < 20:
        score = min(score + 2, 100)

    return score


def run_website_checker(limit: int = None, skip_modern: bool = True):
    """
    Main runner. Checks all leads that haven't been qualified yet.
    """
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler("logs/checker.log"),
        ]
    )

    init_db()

    # Get all leads where site_status is not yet set
    from modules.database import get_connection
    conn = get_connection()
    c = conn.cursor()
    query = "SELECT * FROM leads WHERE site_status IS NULL"
    if limit:
        query += f" LIMIT {limit}"
    c.execute(query)
    leads = [dict(r) for r in c.fetchall()]
    conn.close()

    total = len(leads)
    logger.info("="*60)
    logger.info("Piney Digital — Website Checker starting")
    logger.info("Leads to check: %d", total)
    logger.info("="*60)

    counts = {"none": 0, "parked": 0, "outdated": 0, "modern": 0}

    for i, lead in enumerate(leads, 1):
        name    = lead.get("business_name", "")
        url     = lead.get("website", "")
        lead_id = lead["id"]

        status, base_score = classify_site(url)
        final_score        = score_lead(lead, status)
        has_website        = 0 if status == "none" else 1

        update_lead(lead_id, {
            "has_website":  has_website,
            "site_status":  status,
            "lead_score":   final_score,
        })

        counts[status] += 1

        # Progress log every 10 leads
        if i % 10 == 0 or i == total:
            pct = int(i / total * 100)
            logger.info(
                "  [%3d%%] %d/%d checked | none:%d parked:%d outdated:%d modern:%d",
                pct, i, total,
                counts["none"], counts["parked"], counts["outdated"], counts["modern"]
            )
        else:
            url_display = url[:40] if url else "(no website)"
            logger.info(
                "  %-35s %-42s → %-8s score:%d",
                name[:34], url_display, status, final_score
            )

        # Polite delay — don't hammer every site
        time.sleep(0.8)

    logger.info("="*60)
    logger.info("Website check complete")
    logger.info("  No website : %d  (score ~95)", counts["none"])
    logger.info("  Parked     : %d  (score ~80)", counts["parked"])
    logger.info("  Outdated   : %d  (score ~60)", counts["outdated"])
    logger.info("  Modern     : %d  (score ~10)", counts["modern"])
    logger.info("  HOT leads (no site + parked): %d", counts["none"] + counts["parked"])
    logger.info("="*60)

    return counts


if __name__ == "__main__":
    run_website_checker()
