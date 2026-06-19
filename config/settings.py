# ============================================================
# PINEY DIGITAL — Enterprise Outreach Config
# ============================================================

# --- Target cities (East Texas + nearby metros) ---
CITIES = [
    # Texas metros
    "Houston TX",
    "Dallas TX",
    "Austin TX",
    "San Antonio TX",
    "Fort Worth TX",
    # East Texas
    "Lufkin TX",
    "Nacogdoches TX",
    "Tyler TX",
    "Longview TX",
    # Nearby states
    "Shreveport LA",
    "Baton Rouge LA",
    "Little Rock AR",
    "Oklahoma City OK",
]

# --- Target industries with search terms ---
INDUSTRIES = {
    "restaurant_chain": [
        "restaurant",
        "food service",
        "dining",
        "barbecue restaurant",
        "mexican restaurant",
        "italian restaurant",
        "seafood restaurant",
    ],
    "hospitality_group": [
        "hotel",
        "motel",
        "resort",
        "lodging",
        "inn",
        "bed and breakfast",
    ],
    "professional_services": [
        "dental practice",
        "medical clinic",
        "law firm",
        "accounting firm",
        "consulting firm",
        "financial services",
    ],
    "franchise_auto": [
        "auto service",
        "oil change",
        "tire shop",
        "car wash",
        "auto repair",
    ],
    "private_services": [
        "HVAC company",
        "plumbing company",
        "electrical contractor",
        "roofing company",
    ],
}

# --- Legacy categories for backward compatibility ---
CATEGORIES = [
    "HVAC",
    "plumber",
    "electrician",
    "roofing contractor",
    "auto repair shop",
    "auto mechanic",
]

# --- Multi-location detection keywords ---
CHAIN_INDICATORS = [
    "locations",
    "franchise",
    "chain",
    "group",
    "family of",
    "multiple locations",
    "serving",
    "branch",
    "regional",
    "area",
]

# --- Enterprise scoring weights ---
SCORING_WEIGHTS = {
    "no_modern_tech": 30,
    "outdated_website": 20,
    "multiple_locations": 20,  # 3-10 locations = sweet spot
    "hiring_signal": 15,
    "growth_indicator": 15,
}

# --- Minimum thresholds for enterprise leads ---
MIN_LOCATIONS = 3  # Minimum locations to qualify as enterprise
MIN_SCORE = 50  # Minimum score to be worth outreach

# --- Scraper behavior ---
MAX_RESULTS_PER_SEARCH = 20  # per city+industry combo
SCROLL_PAUSE_SECONDS = 2.5  # wait between scrolls on Maps
REQUEST_DELAY_SECONDS = 3  # polite delay between requests
HEADLESS = True  # set False to watch browser

import os

# --- Database ---
DB_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "leads.db")

# --- Logging ---
LOG_PATH = "logs/scraper.log"

# --- Email outreach (SendGrid) ---
SENDGRID_API_KEY = os.environ.get("SENDGRID_API_KEY", "")
FROM_EMAIL = os.environ.get("FROM_EMAIL", "joel@pineydigital.com")
FROM_NAME = os.environ.get("FROM_NAME", "Joel Escoto")

# --- Enrichment APIs ---
APOLLO_API_KEY = os.environ.get("APOLLO_API_KEY", "")
CRUNCHBASE_API_KEY = os.environ.get("CRUNCHBASE_API_KEY", "")

# --- Outreach sequence timing (days between emails) ---
SEQUENCE_TIMING = {
    "initial_outreach": 1,  # Day 1
    "follow_up_1": 4,  # Day 4 (3 days after initial)
    "follow_up_2": 10,  # Day 10 (6 days after follow_up_1)
    "breakup": 17,  # Day 17 (7 days after follow_up_2)
}