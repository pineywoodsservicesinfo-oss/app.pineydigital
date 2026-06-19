"""
scraper_enterprise.py — Enterprise lead discovery
Piney Digital Outreach System

Multi-source scraping for established businesses:
  - Google Maps (location data for chains)
  - Chain detection across cities
  - Enterprise metadata enrichment

Key differences from small business scraper:
  - Filters for multi-location businesses
  - Enriches with revenue/employee data
  - Identifies decision makers
  - Scores based on tech stack + growth signals
"""

import sys
import time
import logging
import re
import os
import json
import requests
from datetime import datetime
from pathlib import Path
from collections import defaultdict

sys.path.insert(0, str(Path(__file__).parent.parent))

from modules.database import (
    init_db,
    upsert_lead,
    get_connection,
    update_lead,
)
from modules.utils import load_env
from config.settings import (
    CITIES,
    INDUSTRIES,
    CHAIN_INDICATORS,
    MAX_RESULTS_PER_SEARCH,
    REQUEST_DELAY_SECONDS,
    MIN_LOCATIONS,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("logs/scraper_enterprise.log"),
    ],
)
logger = logging.getLogger(__name__)

load_env()
GOOGLE_API_KEY = os.environ.get("GOOGLE_API_KEY", "")

# Track business names across cities to detect chains
_chain_tracker = defaultdict(list)


def clean_phone(raw):
    """Normalize phone number format."""
    if not raw:
        return ""
    digits = re.sub(r"\D", "", raw)
    if len(digits) == 10:
        return f"({digits[:3]}) {digits[3:6]}-{digits[6:]}"
    if len(digits) == 11 and digits[0] == "1":
        return f"({digits[1:4]}) {digits[4:7]}-{digits[7:]}"
    return raw.strip()


def normalize_business_name(name):
    """Normalize business name for chain detection."""
    # Remove common suffixes
    suffixes = [
        " LLC",
        " Inc",
        " Inc.",
        " Corp",
        " Corp.",
        " Ltd",
        " Ltd.",
        " Company",
        " Co.",
        " - ",
        " | ",
    ]
    normalized = name.lower().strip()
    for suffix in suffixes:
        normalized = normalized.replace(suffix.lower(), "")
    # Remove location-specific suffixes
    location_patterns = [
        r"\s+of\s+[a-z]+\s*$",
        r"\s+-\s+[a-z]+\s*$",
        r"\s*\([a-z]+\)\s*$",
    ]
    for pattern in location_patterns:
        normalized = re.sub(pattern, "", normalized)
    return normalized.strip()


def detect_chain_potential(business_name, description=""):
    """Check if business name suggests a chain."""
    text = f"{business_name} {description}".lower()

    for indicator in CHAIN_INDICATORS:
        if indicator in text:
            return True

    # Check for numbered locations (e.g., "Store #5", "Location 3")
    if re.search(r"#\d+|location\s*\d+|store\s*\d+", text, re.IGNORECASE):
        return True

    return False


def get_place_details(place_id):
    """Get detailed info from Google Places API."""
    try:
        r = requests.get(
            "https://maps.googleapis.com/maps/api/place/details/json",
            params={
                "place_id": place_id,
                "fields": "formatted_phone_number,website,formatted_address,name,rating,user_ratings_total,types",
                "key": GOOGLE_API_KEY,
            },
            timeout=10,
        )
        res = r.json().get("result", {})
        return {
            "phone": clean_phone(res.get("formatted_phone_number", "")),
            "website": res.get("website", ""),
            "address": res.get("formatted_address", ""),
            "name": res.get("name", ""),
            "rating": res.get("rating"),
            "review_count": res.get("user_ratings_total"),
            "types": res.get("types", []),
        }
    except Exception:
        return {}


def extract_location_count(business_name, website_content=""):
    """Try to determine number of locations from business name or website."""
    # Check for numbered indicators in name
    match = re.search(r"#(\d+)|location\s*(\d+)", business_name, re.IGNORECASE)
    if match:
        return int(match.group(1) or match.group(2))

    # If website content provided, look for "X locations" text
    if website_content:
        patterns = [
            r"(\d+)\s+locations?",
            r"(\d+)\s+stores?",
            r"serving\s+(\d+)\s+cities",
            r"(\d+)\s+branches?",
        ]
        for pattern in patterns:
            match = re.search(pattern, website_content, re.IGNORECASE)
            if match:
                return int(match.group(1))

    return 1  # Default to single location


def scrape_industry_via_api(city, industry_name, search_terms):
    """Scrape an industry in a city using Google Places API."""
    query = f"{search_terms[0]} in {city}"
    leads = []
    page_token = None

    for page in range(3):
        params = {
            "query": query,
            "key": GOOGLE_API_KEY,
            "type": "establishment",
        }
        if page_token:
            params["pagetoken"] = page_token
            time.sleep(2)

        try:
            data = requests.get(
                "https://maps.googleapis.com/maps/api/place/textsearch/json",
                params=params,
                timeout=15,
            ).json()
        except Exception as e:
            logger.error("API error: %s", e)
            break

        status = data.get("status", "")
        if status == "REQUEST_DENIED":
            logger.error("API key invalid: %s", data.get("error_message"))
            return []
        if status not in ("OK", "ZERO_RESULTS"):
            break

        for place in data.get("results", [])[:MAX_RESULTS_PER_SEARCH]:
            details = get_place_details(place.get("place_id", ""))

            # Determine if this could be a chain
            chain_potential = detect_chain_potential(
                place.get("name", ""), ""
            )

            lead = {
                "business_name": place.get("name", "") or details.get("name", ""),
                "category": industry_name,
                "city": city.replace(" TX", "").replace(" LA", "").replace(" AR", "").replace(" OK", ""),
                "address": details.get("address", place.get("formatted_address", "")),
                "phone": details.get("phone", ""),
                "website": details.get("website", ""),
                "google_maps_url": f"https://www.google.com/maps/place/?q=place_id:{place.get('place_id', '')}",
                "rating": details.get("rating") or place.get("rating"),
                "review_count": details.get("review_count") or place.get("user_ratings_total"),
                "pipeline_type": "enterprise",
                "locations_count": 1,  # Will be updated by chain detection
                "lead_source": "google_maps",
            }

            # Track for chain detection
            normalized = normalize_business_name(lead["business_name"])
            _chain_tracker[normalized].append(lead)

            leads.append(lead)

        page_token = data.get("next_page_token")
        if not page_token:
            break

    return leads


def scrape_industry_via_playwright(city, industry_name, search_terms):
    """Scrape an industry in a city using Playwright (fallback)."""
    from playwright.sync_api import sync_playwright

    query = f"{search_terms[0]} in {city}"
    url = f"https://www.google.com/maps/search/{query.replace(' ', '+')}"
    leads = []

    logger.info("  [Playwright] %s", query)

    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-blink-features=AutomationControlled",
                "--disable-dev-shm-usage",
            ],
        )
        ctx = browser.new_context(
            user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
            viewport={"width": 1366, "height": 768},
            locale="en-US",
        )
        ctx.add_init_script(
            "Object.defineProperty(navigator,'webdriver',{get:()=>undefined})"
        )
        page = ctx.new_page()

        try:
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
            time.sleep(4)

            # Handle cookie consent
            for btn in ["Accept all", "Agree", "I agree"]:
                try:
                    page.click(f'button:has-text("{btn}")', timeout=2000)
                    time.sleep(1)
                    break
                except Exception:
                    pass

            try:
                page.wait_for_selector('div[role="feed"]', timeout=15000)
            except Exception:
                logger.warning("  No results feed for: %s", query)
                browser.close()
                return []

            # Scroll to load more results
            for _ in range(10):
                page.evaluate(
                    "const f=document.querySelector('div[role=\"feed\"]');if(f)f.scrollTop+=800"
                )
                time.sleep(2)

            results = page.evaluate(
                """() => {
                    const items = [], seen = new Set();
                    document.querySelectorAll('div[role="feed"] a[href*="/maps/place/"]').forEach(a => {
                        const card = a.closest('[jsaction]') || a.parentElement;
                        const nameEl = card && card.querySelector('.fontHeadlineSmall,.qBF1Pd,[class*="fontHeadline"]');
                        const name = (nameEl ? nameEl.innerText : a.innerText || '').trim();
                        if (name && name.length > 1 && !seen.has(name)) {
                            seen.add(name);
                            items.push({name, href: a.href || ''});
                        }
                    });
                    return items;
                }"""
            )

            logger.info("  Found %d listings", len(results))

            for item in results[:MAX_RESULTS_PER_SEARCH]:
                lead = {
                    "business_name": item["name"],
                    "category": industry_name,
                    "city": city.replace(" TX", "").replace(" LA", "").replace(" AR", "").replace(" OK", ""),
                    "address": "",
                    "phone": "",
                    "website": "",
                    "google_maps_url": item["href"],
                    "rating": None,
                    "review_count": None,
                    "pipeline_type": "enterprise",
                    "locations_count": 1,
                    "lead_source": "google_maps",
                }

                # Track for chain detection
                normalized = normalize_business_name(lead["business_name"])
                _chain_tracker[normalized].append(lead)

                leads.append(lead)

        except Exception as e:
            logger.error("  Playwright error: %s", e)

        browser.close()

    return leads


def process_chain_detection():
    """Analyze tracked businesses to identify chains."""
    chain_updates = []

    for normalized_name, locations in _chain_tracker.items():
        if len(locations) >= MIN_LOCATIONS:
            # This is a chain!
            location_count = len(locations)

            for loc in locations:
                chain_updates.append({
                    "business_name": loc["business_name"],
                    "locations_count": location_count,
                    "parent_company": normalized_name.title(),
                    "franchise_brand": None,
                })

            logger.info(
                "  Chain detected: %s (%d locations)",
                normalized_name.title(),
                location_count,
            )

    return chain_updates


def update_chain_info(chain_updates):
    """Update leads with chain information."""
    conn = get_connection()

    for update in chain_updates:
        conn.execute(
            """
            UPDATE leads SET
                locations_count = ?,
                parent_company = ?,
                franchise_brand = ?
            WHERE business_name = ?
            """,
            (
                update["locations_count"],
                update["parent_company"],
                update.get("franchise_brand"),
                update["business_name"],
            ),
        )

    conn.commit()
    conn.close()


def log_run(city, industry, total, new, started):
    """Log scrape run to database."""
    conn = get_connection()
    conn.execute(
        """
        INSERT INTO scrape_runs(city, category, results, new_leads, started_at, finished_at)
        VALUES(?,?,?,?,?,datetime('now'))
        """,
        (city, industry, total, new, started),
    )
    conn.commit()
    conn.close()


def run_enterprise_scraper(industry_filter=None, city_filter=None, limit=None):
    """
    Run enterprise scraper.

    Args:
        industry_filter: Specific industry to scrape (None = all)
        city_filter: Specific city to scrape (None = all)
        limit: Max results per search (None = default)
    """
    logger.info("=" * 60)
    mode = (
        "Google Places API"
        if GOOGLE_API_KEY
        else "Playwright (add GOOGLE_API_KEY to .env for best results)"
    )
    logger.info("Enterprise Scraper — Mode: %s", mode)
    logger.info("=" * 60)

    init_db()

    total_new = 0
    total_found = 0
    industries_to_scrape = (
        {industry_filter: INDUSTRIES[industry_filter]}
        if industry_filter
        else INDUSTRIES
    )
    cities_to_scrape = [city_filter] if city_filter else CITIES

    for city in cities_to_scrape:
        for industry_name, search_terms in industries_to_scrape.items():
            started = datetime.now().isoformat()

            # Scrape using first search term for this industry
            scrape_func = (
                scrape_industry_via_api
                if GOOGLE_API_KEY
                else scrape_industry_via_playwright
            )
            raw = scrape_func(city, industry_name, search_terms)

            # Insert leads
            new_count = 0
            for lead in raw:
                _, is_new = upsert_lead(lead)
                if is_new:
                    new_count += 1

            log_run(city, industry_name, len(raw), new_count, started)
            total_found += len(raw)
            total_new += new_count

            logger.info(
                "  %-22s / %-25s → %d found, %d new",
                city,
                industry_name,
                len(raw),
                new_count,
            )

            time.sleep(REQUEST_DELAY_SECONDS)

    # Process chain detection after all scraping
    logger.info("Processing chain detection...")
    chain_updates = process_chain_detection()

    if chain_updates:
        update_chain_info(chain_updates)
        logger.info("Updated %d chain businesses", len(chain_updates))

    logger.info("=" * 60)
    logger.info("Done — %d found, %d new leads, %d chains detected",
                total_found, total_new, len(chain_updates))
    logger.info("=" * 60)

    return total_new


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Enterprise lead scraper")
    parser.add_argument("--industry", help="Specific industry to scrape")
    parser.add_argument("--city", help="Specific city to scrape")
    parser.add_argument("--limit", type=int, help="Max results per search")

    args = parser.parse_args()

    run_enterprise_scraper(
        industry_filter=args.industry,
        city_filter=args.city,
        limit=args.limit,
    )