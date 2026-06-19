"""
scraper.py — Lead scraper (v2)
Piney Digital Outreach System — Module 1

Strategy: Google Places API (primary) or Playwright stealth (fallback).
"""

import sys, time, logging, re, os, requests
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from modules.database import init_db, upsert_lead, get_connection
from modules.utils import load_env
from config.settings import CITIES, CATEGORIES, MAX_RESULTS_PER_SEARCH, REQUEST_DELAY_SECONDS

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(), logging.FileHandler("logs/scraper.log")],
)
logger = logging.getLogger(__name__)

load_env()
GOOGLE_API_KEY = os.environ.get("GOOGLE_API_KEY", "")

def clean_phone(raw):
    if not raw: return ""
    digits = re.sub(r"\D", "", raw)
    if len(digits) == 10: return f"({digits[:3]}) {digits[3:6]}-{digits[6:]}"
    if len(digits) == 11 and digits[0] == "1": return f"({digits[1:4]}) {digits[4:7]}-{digits[7:]}"
    return raw
    return raw.strip()

def get_place_details(place_id):
    try:
        r = requests.get("https://maps.googleapis.com/maps/api/place/details/json",
            params={"place_id": place_id, "fields": "formatted_phone_number,website", "key": GOOGLE_API_KEY},
            timeout=10)
        res = r.json().get("result", {})
        return res.get("formatted_phone_number",""), res.get("website","")
    except Exception: return "", ""

def scrape_via_places_api(city, category):
    query = f"{category} in {city}"
    leads, page_token = [], None
    for page in range(3):
        params = {"query": query, "key": GOOGLE_API_KEY, "type": "establishment"}
        if page_token:
            params["pagetoken"] = page_token
            time.sleep(2)
        try:
            data = requests.get("https://maps.googleapis.com/maps/api/place/textsearch/json",
                params=params, timeout=15).json()
        except Exception as e:
            logger.error("API error: %s", e); break
        status = data.get("status","")
        if status == "REQUEST_DENIED":
            logger.error("API key invalid: %s", data.get("error_message")); return []
        if status not in ("OK","ZERO_RESULTS"): break
        for place in data.get("results",[])[:MAX_RESULTS_PER_SEARCH]:
            phone, website = get_place_details(place.get("place_id",""))
            leads.append({
                "business_name": place.get("name",""),
                "category": category,
                "city": city.replace(" TX",""),
                "address": place.get("formatted_address",""),
                "phone": clean_phone(phone),
                "website": website,
                "google_maps_url": f"https://www.google.com/maps/place/?q=place_id:{place.get('place_id','')}",
                "rating": place.get("rating"),
                "review_count": place.get("user_ratings_total"),
            })
        page_token = data.get("next_page_token")
        if not page_token: break
    return leads

def scrape_via_playwright(city, category):
    from playwright.sync_api import sync_playwright
    query = f"{category} in {city}"
    url   = f"https://www.google.com/maps/search/{query.replace(' ','+')}"
    leads = []
    logger.info("  [Playwright] %s", query)
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, args=[
            "--no-sandbox","--disable-blink-features=AutomationControlled","--disable-dev-shm-usage"])
        ctx = browser.new_context(
            user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
            viewport={"width":1366,"height":768}, locale="en-US")
        ctx.add_init_script("Object.defineProperty(navigator,'webdriver',{get:()=>undefined})")
        page = ctx.new_page()
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
            time.sleep(4)
            for btn in ["Accept all","Agree","I agree"]:
                try: page.click(f'button:has-text("{btn}")', timeout=2000); time.sleep(1); break
                except Exception: pass
            try: page.wait_for_selector('div[role="feed"]', timeout=15000)
            except Exception:
                logger.warning("  No results feed for: %s", query)
                browser.close(); return []
            for _ in range(10):
                page.evaluate('const f=document.querySelector(\'div[role="feed"]\');if(f)f.scrollTop+=800')
                time.sleep(2)
            results = page.evaluate("""() => {
                const items=[], seen=new Set();
                document.querySelectorAll('div[role="feed"] a[href*="/maps/place/"]').forEach(a=>{
                    const card=a.closest('[jsaction]')||a.parentElement;
                    const nameEl=card&&card.querySelector('.fontHeadlineSmall,.qBF1Pd,[class*="fontHeadline"]');
                    const name=(nameEl?nameEl.innerText:a.innerText||'').trim();
                    if(name&&name.length>1&&!seen.has(name)){seen.add(name);items.push({name,href:a.href||''});}
                });
                return items;
            }""")
            logger.info("  Found %d listings", len(results))
            for item in results[:MAX_RESULTS_PER_SEARCH]:
                leads.append({"business_name":item["name"],"category":category,
                    "city":city.replace(" TX",""),"address":"","phone":"","website":"",
                    "google_maps_url":item["href"],"rating":None,"review_count":None})
        except Exception as e:
            logger.error("  Playwright error: %s", e)
        browser.close()
    return leads

def log_run(city, category, total, new, started):
    conn = get_connection()
    conn.execute("INSERT INTO scrape_runs(city,category,results,new_leads,started_at,finished_at) VALUES(?,?,?,?,?,datetime('now'))",
        (city, category, total, new, started))
    conn.commit(); conn.close()

def run_scraper():
    logger.info("="*60)
    mode = "Google Places API" if GOOGLE_API_KEY else "Playwright (add GOOGLE_API_KEY to .env for best results)"
    logger.info("Mode: %s", mode)
    logger.info("="*60)
    init_db()
    total_new = total_found = 0
    for city in CITIES:
        for category in CATEGORIES:
            started = datetime.now().isoformat()
            raw = scrape_via_places_api(city, category) if GOOGLE_API_KEY else scrape_via_playwright(city, category)
            new_count = sum(1 for lead in raw if upsert_lead(lead)[1])
            log_run(city, category, len(raw), new_count, started)
            total_found += len(raw); total_new += new_count
            logger.info("  %-22s / %-25s → %d found, %d new", city, category, len(raw), new_count)
            time.sleep(REQUEST_DELAY_SECONDS)
    logger.info("="*60)
    logger.info("Done — %d found, %d new leads added", total_found, total_new)
    logger.info("="*60)
    return total_new

if __name__ == "__main__":
    run_scraper()
