# ============================================================
# PINEY DIGITAL — Automation System Config
# ============================================================

# --- Target cities ---
CITIES = ["Lufkin TX", "Nacogdoches TX", "Diboll TX"]

# --- Target categories (Google Maps search terms) ---
CATEGORIES = [
    "HVAC",
    "plumber",
    "electrician",
    "roofing contractor",
    "auto repair shop",
    "auto mechanic",
]

# --- Scraper behavior ---
MAX_RESULTS_PER_SEARCH = 20      # per city+category combo
SCROLL_PAUSE_SECONDS   = 2.5     # wait between scrolls on Maps
REQUEST_DELAY_SECONDS  = 3       # polite delay between requests
HEADLESS               = True    # set False to watch browser

import os

# --- Database ---
DB_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "leads.db")

# --- Logging ---
LOG_PATH = "logs/scraper.log"
