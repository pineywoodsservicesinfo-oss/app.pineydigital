"""
enrichment.py — Contact enrichment engine
Piney Digital Outreach System — Module 3

For every hot lead, attempts to find:
  - owner_name  : business owner / manager name
  - owner_email : contact email address
  - email_source: where the email came from

Sources tried (all free):
  1. Business's own website — contact/about pages
  2. Yelp listing scrape
  3. BBB listing scrape
  4. Facebook business page
  5. Google search snippets
  6. Hunter.io API (optional, 25/mo free)
  7. Construct email from name + domain

Leads with no email but valid phone → marked 'sms_only' for SMS outreach.
"""

import sys, re, time, logging, os, requests
from pathlib import Path
from urllib.parse import urlparse, quote_plus
from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).parent.parent))
from modules.database import get_connection, update_lead, init_db
from modules.utils import load_env

logger = logging.getLogger(__name__)

load_env()
HUNTER_API_KEY = os.environ.get("HUNTER_API_KEY", "")

SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
})

EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")
EMAIL_BLACKLIST = ["example.com","domain.com","sentry","wix.com","wordpress.org",
    "squarespace.com","godaddy","noreply","no-reply","privacy","schemas","w3.org",
    "jquery","googleapis","facebook.com","twitter.com","instagram.com","yelp.com"]

def is_valid_email(email):
    if not email or len(email) < 6: return False
    if any(bad in email.lower() for bad in EMAIL_BLACKLIST): return False
    parts = email.split("@")
    if len(parts) != 2: return False
    return "." in parts[1]

def extract_emails(text):
    return [e.lower() for e in EMAIL_RE.findall(text) if is_valid_email(e)]

def clean_name(raw):
    raw = re.sub(r"\b(mr|mrs|ms|dr|owner|manager|president|contact)\b\.?","",raw,flags=re.I)
    raw = re.sub(r"\s+"," ",raw).strip()
    words = raw.split()
    if len(words) >= 2 and all(w[0].isupper() for w in words[:2] if w):
        return " ".join(words[:3])
    return ""

def fetch(url, timeout=8):
    try:
        r = SESSION.get(url, timeout=timeout, allow_redirects=True)
        return r.status_code, r.text
    except requests.exceptions.SSLError:
        try:
            r = SESSION.get(url.replace("https://","http://"), timeout=timeout, allow_redirects=True)
            return r.status_code, r.text
        except Exception: return 0, ""
    except Exception: return 0, ""

# ── Source 1: Own website ──────────────────────────────────
def scrape_website(url):
    if not url: return "", ""
    if not url.startswith(("http://","https://")): url = "https://" + url
    base = url.rstrip("/")
    pages = [base, base+"/contact", base+"/contact-us", base+"/about", base+"/about-us"]
    found_email = found_name = ""
    for page in pages[:4]:
        code, html = fetch(page)
        if code != 200: continue
        soup = BeautifulSoup(html, "html.parser")
        text = soup.get_text(separator=" ")
        emails = extract_emails(text)
        if emails and not found_email:
            # Prefer non-generic emails
            biz = [e for e in emails if not any(g in e for g in ["info@","contact@","admin@","hello@"])]
            found_email = biz[0] if biz else emails[0]
        if not found_name:
            for trigger in ["owner","founder","president","manager","operated by","contact"]:
                idx = text.lower().find(trigger)
                if idx == -1: continue
                snippet = text[max(0,idx-20):idx+80]
                m = re.search(r"\b([A-Z][a-z]+\s+[A-Z][a-z]+)\b", snippet)
                if m:
                    n = clean_name(m.group(1))
                    if n: found_name = n; break
        if found_email and found_name: break
        time.sleep(0.4)
    return found_name, found_email

# ── Source 2: Yelp ─────────────────────────────────────────
def scrape_yelp(business_name, city):
    try:
        q = quote_plus(f"{business_name} {city} TX")
        code, html = fetch(f"https://www.yelp.com/search?find_desc={q}&find_loc={city}+TX")
        if code != 200: return "", ""
        soup = BeautifulSoup(html, "html.parser")
        # Find first business link
        link = soup.select_one('a[href*="/biz/"]')
        if not link: return "", ""
        biz_url = "https://www.yelp.com" + link["href"].split("?")[0]
        code2, html2 = fetch(biz_url)
        if code2 != 200: return "", ""
        text = BeautifulSoup(html2, "html.parser").get_text(separator=" ")
        emails = extract_emails(text)
        return "", emails[0] if emails else ""
    except Exception: return "", ""

# ── Source 3: BBB ──────────────────────────────────────────
def scrape_bbb(business_name, city):
    try:
        q = quote_plus(f"{business_name} {city} TX")
        code, html = fetch(f"https://www.bbb.org/search?find_text={q}&find_loc={city}%2C+TX")
        if code != 200: return "", ""
        soup = BeautifulSoup(html, "html.parser")
        link = soup.select_one('a[href*="/profile/"]')
        if not link: return "", ""
        biz_url = "https://www.bbb.org" + link["href"]
        code2, html2 = fetch(biz_url)
        if code2 != 200: return "", ""
        soup2 = BeautifulSoup(html2, "html.parser")
        text = soup2.get_text(separator=" ")
        emails = extract_emails(text)
        # Try to get owner name from BBB
        owner = ""
        m = re.search(r"(Principal|Owner|President)[:\s]+([A-Z][a-z]+\s+[A-Z][a-z]+)", text)
        if m: owner = clean_name(m.group(2))
        return owner, emails[0] if emails else ""
    except Exception: return "", ""

# ── Source 4: Facebook ─────────────────────────────────────
def scrape_facebook(business_name, city):
    try:
        q = quote_plus(f"{business_name} {city} TX")
        code, html = fetch(f"https://www.facebook.com/search/pages/?q={q}")
        if code != 200: return "", ""
        emails = extract_emails(BeautifulSoup(html,"html.parser").get_text())
        return "", emails[0] if emails else ""
    except Exception: return "", ""

# ── Source 5: Google search ────────────────────────────────
def google_search(business_name, city):
    queries = [
        f'"{business_name}" "{city}" TX email contact',
        f'"{business_name}" {city} TX owner email',
        f'"{business_name}" {city} site:yelp.com OR site:bbb.org OR site:facebook.com',
    ]
    found_email = found_name = ""
    for query in queries:
        try:
            code, html = fetch(f"https://www.google.com/search?q={quote_plus(query)}&num=8")
            if code == 429:
                logger.warning("    Google rate limit — pausing 20s")
                time.sleep(20); continue
            if code != 200: continue
            soup = BeautifulSoup(html, "html.parser")
            text = soup.get_text(separator=" ")
            emails = extract_emails(text)
            if emails and not found_email:
                # Skip emails clearly from google/schema domains
                real = [e for e in emails if "google" not in e and "schema" not in e]
                if real: found_email = real[0]
            if not found_name:
                for trigger in ["owner","founded by","operated by"]:
                    idx = text.lower().find(trigger)
                    if idx != -1:
                        snippet = text[max(0,idx-10):idx+60]
                        m = re.search(r"\b([A-Z][a-z]+\s+[A-Z][a-z]+)\b", snippet)
                        if m:
                            n = clean_name(m.group(1))
                            if n: found_name = n; break
            if found_email: break
            time.sleep(2.5)
        except Exception as e:
            logger.debug("    Google error: %s", e); continue
    return found_name, found_email

# ── Source 6: Hunter.io ────────────────────────────────────
def hunter_lookup(domain):
    if not HUNTER_API_KEY or not domain: return "", ""
    try:
        r = requests.get("https://api.hunter.io/v2/domain-search",
            params={"domain":domain,"api_key":HUNTER_API_KEY,"limit":3}, timeout=10)
        emails = r.json().get("data",{}).get("emails",[])
        if emails:
            for role in ["owner","founder","director","manager","president"]:
                for e in emails:
                    if role in (e.get("position") or "").lower():
                        name = f"{e.get('first_name','')} {e.get('last_name','')}".strip()
                        return name, e.get("value","")
            first = emails[0]
            return f"{first.get('first_name','')} {first.get('last_name','')}".strip(), first.get("value","")
    except Exception: pass
    return "", ""

# ── Source 7: Construct email ──────────────────────────────
def construct_email(name, website):
    if not name or not website: return ""
    try:
        parsed = urlparse(website if "://" in website else "https://"+website)
        domain = (parsed.netloc or parsed.path).replace("www.","")
        if not domain or "." not in domain: return ""
        first = name.split()[0].lower()
        return f"{first}@{domain}"
    except Exception: return ""

# ── Main runner ────────────────────────────────────────────
def run_enrichment(min_score=60, limit=None):
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[logging.StreamHandler(), logging.FileHandler("logs/enrichment.log")]
    )
    init_db()
    conn = get_connection()
    c = conn.cursor()
    q = """SELECT id, business_name, city, phone, website, site_status, lead_score
           FROM leads
           WHERE lead_score >= ? AND (owner_email IS NULL OR owner_email = '')
           AND outreach_status = 'new' ORDER BY lead_score DESC"""
    if limit: q += f" LIMIT {limit}"
    c.execute(q, [min_score])
    leads = [dict(zip([d[0] for d in c.description], r)) for r in c.fetchall()]
    conn.close()

    total = len(leads)
    logger.info("="*60)
    logger.info("Piney Digital — Contact Enrichment v2")
    logger.info("Leads to process: %d  (score >= %d)", total, min_score)
    logger.info("="*60)

    found_email = found_name = 0

    for i, lead in enumerate(leads, 1):
        lid     = lead["id"]
        name    = lead["business_name"]
        city    = lead["city"]
        website = lead.get("website") or ""
        phone   = lead.get("phone") or ""
        score   = lead.get("lead_score", 0)
        status  = lead.get("site_status", "none")

        logger.info("  [%d/%d] %s — %s (%s)", i, total, name, city, status)

        owner_name = owner_email = source = ""

        # 1. Scrape own website (only if they have one)
        if website and status not in ("none", "parked"):
            owner_name, owner_email = scrape_website(website)
            if owner_email: source = "website"

        # 2. Yelp
        if not owner_email:
            _, yelp_email = scrape_yelp(name, city)
            if yelp_email: owner_email = yelp_email; source = "yelp"
            time.sleep(1)

        # 3. BBB
        if not owner_email:
            bbb_name, bbb_email = scrape_bbb(name, city)
            if bbb_email: owner_email = bbb_email; source = "bbb"
            if bbb_name and not owner_name: owner_name = bbb_name
            time.sleep(1)

        # 4. Google search
        if not owner_email:
            g_name, g_email = google_search(name, city)
            if g_email: owner_email = g_email; source = "google"
            if g_name and not owner_name: owner_name = g_name
            time.sleep(1)

        # 5. Hunter.io (top leads + has domain)
        if not owner_email and HUNTER_API_KEY and score >= 95 and website:
            parsed = urlparse(website if "://" in website else "https://"+website)
            domain = (parsed.netloc or "").replace("www.","")
            if domain:
                h_name, h_email = hunter_lookup(domain)
                if h_email: owner_email = h_email; source = "hunter"
                if h_name and not owner_name: owner_name = h_name

        # 6. Construct from name + domain
        if not owner_email and owner_name and website and status not in ("none","parked"):
            constructed = construct_email(owner_name, website)
            if constructed: owner_email = constructed; source = "constructed"

        # 7. If still no email but have phone → flag for SMS only
        if not owner_email and phone:
            source = "sms_only"
            logger.info("    → No email found — flagged for SMS outreach (phone: %s)", phone)
        elif owner_email:
            found_email += 1
            logger.info("    ✓ [%s] %s <%s>", source, owner_name or "?", owner_email)
        else:
            logger.info("    — No contact info found")

        if owner_name: found_name += 1

        update_lead(lid, {
            "owner_name":   owner_name  or None,
            "owner_email":  owner_email or None,
            "email_source": source      or None,
        })

        time.sleep(1)

    sms_only = sum(1 for l in leads if l.get("phone")) - found_email
    logger.info("="*60)
    logger.info("Enrichment complete")
    logger.info("  Total processed : %d", total)
    logger.info("  Emails found    : %d  (%.0f%%)", found_email, found_email/max(total,1)*100)
    logger.info("  Names found     : %d  (%.0f%%)", found_name,  found_name/max(total,1)*100)
    logger.info("  SMS-only ready  : %d  (phone, no email)", max(sms_only,0))
    logger.info("  Ready for email : %d", found_email)
    logger.info("="*60)
    return {"total": total, "emails": found_email, "names": found_name}

if __name__ == "__main__":
    run_enrichment()
