"""
enrichment_apollo.py — Apollo.io enrichment for enterprise leads
Piney Digital Outreach System

Uses Apollo.io API to:
  - Find multi-location companies
  - Get decision-maker emails (owner, CEO, GM, etc.)
  - Enrich with company size, revenue, tech stack

API Docs: https://docs.apollo.io/docs
"""

import sys
import os
import time
import json
import logging
import requests
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from modules.database import (
    get_connection,
    update_lead,
    init_db,
    add_decision_maker,
)
from modules.utils import load_env

logger = logging.getLogger(__name__)

load_env()
APOLLO_API_KEY = os.environ.get("APOLLO_API_KEY", "")

# Apollo API base URL
APOLLO_BASE_URL = "https://api.apollo.io/api/v1"


def apollo_request(endpoint: str, method: str = "GET", data: dict = None) -> dict:
    """
    Make a request to Apollo API.

    Args:
        endpoint: API endpoint (e.g., "/organizations/search")
        method: HTTP method
        data: Request body for POST requests

    Returns:
        Response JSON or None on error
    """
    if not APOLLO_API_KEY:
        logger.error("APOLLO_API_KEY not set in .env")
        return None

    url = f"{APOLLO_BASE_URL}{endpoint}"
    headers = {
        "X-Api-Key": APOLLO_API_KEY,
        "Content-Type": "application/json",
        "Cache-Control": "no-cache",
    }

    try:
        if method == "GET":
            response = requests.get(url, headers=headers, params=data, timeout=30)
        else:
            response = requests.post(url, headers=headers, json=data, timeout=30)

        if response.status_code == 200:
            return response.json()
        elif response.status_code == 429:
            logger.warning("Apollo rate limit hit - waiting 60s")
            time.sleep(60)
            return apollo_request(endpoint, method, data)
        else:
            logger.error(f"Apollo API error {response.status_code}: {response.text[:200]}")
            return None

    except Exception as e:
        logger.error(f"Apollo request failed: {e}")
        return None


def search_companies(
    industry: str = None,
    location: str = None,
    min_employees: int = 10,
    max_employees: int = 1000,
    limit: int = 10,
) -> list:
    """
    Search for companies on Apollo.

    Args:
        industry: Industry filter (e.g., "restaurants", "hospitality")
        location: Location filter (e.g., "Houston, TX")
        min_employees: Minimum employee count
        max_employees: Maximum employee count
        limit: Max results

    Returns:
        List of company dicts
    """
    data = {
        "organization_num_employees_ranges": [f"{min_employees}-{max_employees}"],
        "per_page": limit,
        "page": 1,
    }

    if industry:
        # Map common industries to Apollo keywords
        industry_keywords = {
            "restaurant": ["restaurant", "food service", "hospitality"],
            "hospitality": ["hotel", "hospitality", "lodging"],
            "healthcare": ["healthcare", "medical", "health"],
            "professional": ["professional services", "consulting", "legal"],
        }
        keywords = industry_keywords.get(industry.lower(), [industry])
        data["q_organization_keyword_tags"] = keywords

    if location:
        data["organization_locations"] = [location]

    result = apollo_request("/organizations/search", method="POST", data=data)

    if result and "organizations" in result:
        companies = []
        for org in result["organizations"]:
            companies.append({
                "id": org.get("id"),
                "name": org.get("name"),
                "website": org.get("website_url"),
                "industry": org.get("industry"),
                "employee_count": org.get("employee_count"),
                "estimated_revenue": org.get("estimated_revenue"),
                "city": org.get("city"),
                "state": org.get("state"),
                "country": org.get("country"),
                "linkedin_url": org.get("linkedin_url"),
                "tech_stack": org.get("technology_names", []),
            })
        return companies

    return []


def get_decision_makers(
    organization_id: str = None,
    domain: str = None,
    titles: list = None,
    limit: int = 5,
) -> list:
    """
    Get decision makers for a company.

    Args:
        organization_id: Apollo organization ID
        domain: Company website domain (alternative to org ID)
        titles: Job titles to filter (e.g., ["CEO", "Owner", "General Manager"])
        limit: Max results

    Returns:
        List of decision maker dicts with email, name, title
    """
    if titles is None:
        titles = ["CEO", "Owner", "Founder", "General Manager", "COO", "President", "Director"]

    data = {
        "per_page": limit,
        "page": 1,
        "person_titles": titles,
    }

    if organization_id:
        data["organization_id"] = organization_id
    elif domain:
        # Search by domain
        data["q_organization_domains"] = [domain]
    else:
        return []

    result = apollo_request("/people/search", method="POST", data=data)

    if result and "people" in result:
        contacts = []
        for person in result["people"]:
            contacts.append({
                "id": person.get("id"),
                "name": person.get("name"),
                "first_name": person.get("first_name"),
                "last_name": person.get("last_name"),
                "title": person.get("title"),
                "email": person.get("email"),
                "email_status": person.get("email_status"),
                "phone": person.get("phone"),
                "linkedin_url": person.get("linkedin_url"),
                "organization": person.get("organization", {}).get("name"),
            })
        return contacts

    return []


def enrich_lead_from_domain(domain: str) -> dict:
    """
    Enrich a lead using their website domain.

    Args:
        domain: Website domain (e.g., "example.com")

    Returns:
        Dict with company info and decision makers
    """
    # Clean domain
    if not domain:
        return {}

    domain = domain.replace("https://", "").replace("http://", "").replace("www.", "")
    domain = domain.split("/")[0]  # Remove path

    # Get organization info
    org_data = apollo_request("/organizations/enrich", data={"domain": domain})

    if not org_data or "organization" not in org_data:
        # Try searching instead
        companies = search_companies(limit=1)
        if companies:
            org_data = {"organization": companies[0]}
        else:
            return {}

    org = org_data.get("organization", {})

    # Get decision makers
    contacts = get_decision_makers(domain=domain)

    return {
        "company": {
            "name": org.get("name"),
            "employee_count": org.get("employee_count"),
            "revenue": org.get("estimated_revenue"),
            "industry": org.get("industry"),
            "tech_stack": org.get("technology_names", []),
        },
        "decision_makers": contacts,
    }


def enrich_lead_from_name(business_name: str, city: str = None) -> dict:
    """
    Enrich a lead using their business name.

    Args:
        business_name: Name of the business
        city: Optional city for filtering

    Returns:
        Dict with company info and decision makers
    """
    data = {
        "q_organization_name": business_name,
        "per_page": 1,
    }

    if city:
        data["organization_locations"] = [city]

    result = apollo_request("/organizations/search", method="POST", data=data)

    if result and "organizations" in result and len(result["organizations"]) > 0:
        org = result["organizations"][0]

        # Get decision makers for this organization
        contacts = get_decision_makers(organization_id=org.get("id"))

        return {
            "company": {
                "name": org.get("name"),
                "employee_count": org.get("employee_count"),
                "revenue": org.get("estimated_revenue"),
                "industry": org.get("industry"),
                "tech_stack": org.get("technology_names", []),
                "website": org.get("website_url"),
            },
            "decision_makers": contacts,
        }

    return {}


def run_apollo_enrichment(min_score: int = 0, limit: int = 10, pipeline_type: str = "enterprise"):
    """
    Run Apollo enrichment on leads that don't have emails yet.

    Args:
        min_score: Minimum lead score to enrich
        limit: Max leads to process
        pipeline_type: Pipeline type filter
    """
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler("logs/apollo_enrichment.log"),
        ],
    )

    if not APOLLO_API_KEY:
        logger.error("APOLLO_API_KEY not set. Add to .env")
        return {"error": "no_api_key"}

    init_db()

    conn = get_connection()
    c = conn.cursor()

    # Get leads without emails
    c.execute("""
        SELECT id, business_name, city, website, lead_score
        FROM leads
        WHERE pipeline_type = ?
          AND lead_score >= ?
          AND (owner_email IS NULL OR owner_email = '')
        ORDER BY lead_score DESC
        LIMIT ?
    """, (pipeline_type, min_score, limit))

    leads = [dict(zip([d[0] for d in c.description], r)) for r in c.fetchall()]
    conn.close()

    total = len(leads)
    logger.info("=" * 60)
    logger.info("Apollo Enrichment starting")
    logger.info("Leads to process: %d", total)
    logger.info("=" * 60)

    enriched = 0
    emails_found = 0

    for i, lead in enumerate(leads, 1):
        lead_id = lead["id"]
        name = lead["business_name"]
        city = lead["city"]
        website = lead.get("website")

        logger.info("  [%d/%d] %s — %s", i, total, name, city)

        # Try enrichment by domain first, then by name
        enrichment = None

        if website:
            domain = website.replace("https://", "").replace("http://", "").replace("www.", "").split("/")[0]
            enrichment = enrich_lead_from_domain(domain)
            time.sleep(1)  # Rate limiting

        if not enrichment or not enrichment.get("decision_makers"):
            enrichment = enrich_lead_from_name(name, city)
            time.sleep(1)

        if enrichment:
            company = enrichment.get("company", {})
            contacts = enrichment.get("decision_makers", [])

            if company:
                # Update lead with company info
                update_data = {
                    "employee_count": str(company.get("employee_count", "")),
                    "estimated_revenue": company.get("revenue"),
                }
                if company.get("tech_stack"):
                    update_data["tech_stack"] = json.dumps(company["tech_stack"])

                update_lead(lead_id, update_data)
                enriched += 1

            if contacts:
                # Add decision makers
                for contact in contacts:
                    if contact.get("email"):
                        is_primary = contacts.index(contact) == 0
                        add_decision_maker(
                            lead_id=lead_id,
                            name=contact.get("name", ""),
                            title=contact.get("title"),
                            email=contact.get("email"),
                            phone=contact.get("phone"),
                            linkedin_url=contact.get("linkedin_url"),
                            is_primary=is_primary,
                        )
                        emails_found += 1

                        if is_primary:
                            # Update lead with primary contact
                            update_lead(lead_id, {
                                "owner_name": contact.get("name"),
                                "owner_email": contact.get("email"),
                                "email_source": "apollo",
                            })

                logger.info("    ✓ Found %d contacts: %s",
                          len(contacts),
                          ", ".join(c.get("title", "?") for c in contacts[:3]))
            else:
                logger.info("    — No contacts found")

        else:
            logger.info("    — Not found on Apollo")

        time.sleep(1)  # Rate limiting

    logger.info("=" * 60)
    logger.info("Apollo enrichment complete")
    logger.info("  Enriched: %d/%d", enriched, total)
    logger.info("  Emails found: %d", emails_found)
    logger.info("=" * 60)

    return {"total": total, "enriched": enriched, "emails": emails_found}


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Apollo.io enrichment")
    parser.add_argument("--limit", type=int, default=10, help="Max leads to process")
    parser.add_argument("--min-score", type=int, default=0, help="Minimum lead score")

    args = parser.parse_args()

    run_apollo_enrichment(min_score=args.min_score, limit=args.limit)