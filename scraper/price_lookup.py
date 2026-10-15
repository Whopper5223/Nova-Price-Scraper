"""
price_lookup.py

Read-only view over the scraper's saved output.

The full scraper (run_scraper.py -> instacart_scraper.run_scraper) already writes
prices_output.json after every run:

    {"scraped_at": ISO8601, "location": str,
     "stores": [{"name", "url", "product_count",
                 "products": [{"name", "price", "unit", "department"}]}]}

The chat assistant reads that file through this module so its answers are grounded
in prices we actually scraped. No database — the JSON the scraper already produces
is the source of truth.
"""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from scraper import product_embeddings
from scraper.snap_eligibility import is_snap_eligible

# Deliberately does not import scraper.core: that pulls in Playwright, and reading
# saved prices should work anywhere (schedulers, quick checks) without the browser
# stack installed. SCRAPER_DIR and the matching rule are small enough to restate.
# product_embeddings has no Playwright dependency, so importing it here is safe.
SCRAPER_DIR = Path(__file__).parent
PRICES_PATH = SCRAPER_DIR / "prices_output.json"

# How old saved prices may be before the assistant prefers a live scrape.
DEFAULT_MAX_AGE_HOURS = 72

# Overridable at startup (--prices-file) so a sample multi-store dataset can be
# swapped in for testing without touching the real scrape output.
_ACTIVE_PATH: Path | None = None


def set_prices_path(path: Path | None) -> None:
    global _ACTIVE_PATH
    _ACTIVE_PATH = path


def load_latest(path: Path | None = None) -> dict | None:
    """Parsed prices_output.json, or None if it's missing or unreadable."""
    src = path or _ACTIVE_PATH or PRICES_PATH
    if not src.exists():
        return None
    try:
        return json.loads(src.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"[prices] Could not read {src}: {e}")
        return None


# Departments that never contain real groceries but whose products often reuse
# a food word as flavor/scent branding — e.g. "Milk & Honey" hand soap, "Turkey
# & Cheese" cat food. Word-boundary matching alone can't tell "flavor name"
# from "actual grocery item", so a bare query like "milk" or "cheese" would
# otherwise match these ahead of (and cheaper than) the real product. Excluded
# up front rather than relying on relevance-scoring after the fact.
_NON_GROCERY_DEPARTMENTS = {
    "household", "pets", "cat", "dog", "personal-care", "health-care",
    "baby", "kitchen-supplies", "office-craft", "laundry", "party-gifts", "floral",
}


def query_prices(term: str, limit: int = 20, path: Path | None = None) -> list[dict]:
    """
    Every saved product matching `term`, cheapest first.

    Matching is the same word-boundary+stemming rule search_products() uses on
    live pages (product_embeddings.matches_query), so cached and live lookups
    agree on what counts as a hit. Non-grocery departments (see
    _NON_GROCERY_DEPARTMENTS) are excluded before matching.
    Each row: {store_name, store_url, name, price, unit, department}.
    """
    data = load_latest(path)
    if not data:
        return []

    matches = []
    for store in data.get("stores", []):
        for product in store.get("products", []):
            department = product.get("department")
            if department in _NON_GROCERY_DEPARTMENTS:
                continue
            name = product.get("name") or ""
            price = product.get("price")
            if price is None or not product_embeddings.matches_query(name, term):
                continue
            matches.append({
                "store_name": store.get("name"),
                "store_url": store.get("url"),
                "name": name,
                "price": price,
                "unit": product.get("unit"),
                "department": department,
                "snap_eligible": is_snap_eligible(name, department),
            })

    matches.sort(key=lambda p: p["price"])
    return matches[:limit] if limit > 0 else matches


def cheapest_per_store(rows: list[dict]) -> list[dict]:
    """Collapse `rows` (any order) to one cheapest entry per store."""
    best: dict[str, dict] = {}
    for row in sorted(rows, key=lambda p: p["price"]):
        store = row["store_name"]
        if store not in best:
            best[store] = row
    return sorted(best.values(), key=lambda p: p["price"])


def cheapest_by_store(term: str, path: Path | None = None) -> list[dict]:
    """One cheapest match per store for `term`, cheapest store first."""
    return cheapest_per_store(query_prices(term, limit=0, path=path))


def data_age(path: Path | None = None) -> timedelta | None:
    """How long ago the saved scrape ran, or None if there's no readable timestamp."""
    data = load_latest(path)
    if not data:
        return None
    raw = data.get("scraped_at")
    if not raw:
        return None
    try:
        scraped_at = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if scraped_at.tzinfo is None:
        scraped_at = scraped_at.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) - scraped_at


def is_stale(max_age_hours: int = DEFAULT_MAX_AGE_HOURS, path: Path | None = None) -> bool:
    """True when there's no saved scrape, or it's older than max_age_hours."""
    age = data_age(path)
    if age is None:
        return True
    return age > timedelta(hours=max_age_hours)


def describe_freshness(path: Path | None = None) -> str:
    """One-line human summary of the saved data, for CLI startup output."""
    data = load_latest(path)
    if not data:
        return "No saved prices yet — run: python -m scraper.scheduled_scrape"

    stores = data.get("stores", [])
    products = sum(s.get("product_count", 0) for s in stores)
    age = data_age(path)

    if age is None:
        when = "unknown age"
    elif age < timedelta(hours=1):
        when = f"{int(age.total_seconds() // 60)} min ago"
    elif age < timedelta(days=1):
        when = f"{int(age.total_seconds() // 3600)} hr ago"
    else:
        when = f"{age.days} day(s) ago"

    note = "  (stale — consider re-scraping)" if is_stale(path=path) else ""
    return f"{products} products across {len(stores)} store(s), scraped {when}{note}"


def list_stores(path: Path | None = None) -> list[str]:
    """Store names present in the saved scrape."""
    data = load_latest(path)
    if not data:
        return []
    return [s.get("name", "") for s in data.get("stores", []) if s.get("name")]
