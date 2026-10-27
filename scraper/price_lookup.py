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
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

from scraper import markup_data, product_embeddings
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

    Results are further restricted to markup_data.CATEGORIES -- the categories
    some store has a real in-store markup number for. A product in no such
    category is dropped rather than shown with an un-adjusted Instacart price.
    A product whose own store has no number for its category is kept, with
    estimated_real_price None -- no estimate is invented for the gap.

    Each row: {store_name, store_url, name, price, unit, department,
    category, estimated_real_price}.
    """
    data = load_latest(path)
    if not data:
        return []

    matches = []
    for store in data.get("stores", []):
        store_name = store.get("name")
        for product in store.get("products", []):
            department = product.get("department")
            if department in _NON_GROCERY_DEPARTMENTS:
                continue
            name = product.get("name") or ""
            price = product.get("price")
            if price is None or not product_embeddings.matches_query(name, term):
                continue
            category = markup_data.classify(name, department)
            if category is None:
                continue
            matches.append({
                "store_name": store_name,
                "store_url": store.get("url"),
                "name": name,
                "price": price,
                "unit": product.get("unit"),
                "department": department,
                "category": category,
                "estimated_real_price": markup_data.estimate_real_price(price, store_name, category),
                "snap_eligible": is_snap_eligible(name, department),
            })

    matches.sort(key=lambda p: p["price"])
    return matches[:limit] if limit > 0 else matches


# Trailing size/count noise on a product name ("2 lb", "- 24 oz", "(32 oz)").
_SIZE_SUFFIX = re.compile(
    r"\s+\d+(?:\.\d+)?\s*(?:fl\s*oz|oz|lbs?|ct|count|pk|pack|gal|gallon|ml|kg|l|g)\b.*$",
    re.IGNORECASE,
)


def _head_of(name: str) -> str:
    """The part of a product name that says what the product IS: before the first
    comma, before any "with ..." or "in ..." clause ("Tuna in Olive Oil" is tuna),
    minus trailing sizes."""
    head = name.split(",")[0]
    head = re.split(r"\s+(?:with|in)\s+", head, maxsplit=1, flags=re.IGNORECASE)[0]
    head = re.sub(r"\s*\(.*$", "", head)
    head = re.sub(r"\s*[-–]\s*\d.*$", "", head)
    return _SIZE_SUFFIX.sub("", head)


def head_matches(term: str, name: str) -> bool:
    """
    True if the search phrase ends the product's name, i.e. it's the thing being
    sold rather than an ingredient or flavor mentioned along the way: "White
    Bread" is bread, "Italian Style Bread Crumbs" is crumbs; "Orange Juice" is
    juice, "Garlic & Olive Oil Couscous Mix" is a couscous mix.
    """
    wanted = product_embeddings.word_stems(term)
    if not wanted:
        return False
    return product_embeddings.word_stems(_head_of(name))[-len(wanted):] == wanted


# Words that make a product a flavored, prepared or alternative version of the plain
# thing. Deliberately short: they only demote a candidate (never drop it), and a word
# the shopper typed themselves ("chocolate milk") is never held against a product.
_VARIANT_MARKERS = re.compile(
    r"\b(flavou?red|flavou?r|seasoned|mix|instant|microwav\w*|ready|concentrate[d]?|"
    r"hard[- ]boiled|boiled|cooked|spread|dip|almond|oat|soy|coconut|cashew|"
    r"garlic|herbs?|ranch|bbq|barbecue|chocolate|strawberry|vanilla|cinnamon|honey|maple)\b",
    re.IGNORECASE,
)


def _ranking(term: str):
    """(tier, is_variant) functions for ordering rows against `term`."""
    named = markup_data.categories_for_term(term)
    term_foods = markup_data.foods_named(term)
    term_words = set(re.findall(r"[a-z0-9']+", term.lower()))

    def tier(row: dict) -> int:
        in_category = row.get("category") in named
        is_head = head_matches(term, row["name"])
        if in_category and is_head:
            return 0
        if in_category:
            return 1
        return 2 if is_head else 3

    def is_variant(row: dict) -> bool:
        head = _head_of(row["name"])
        if markup_data.foods_named(head) - term_foods:
            return True
        return any(m.lower() not in term_words for m in _VARIANT_MARKERS.findall(head))

    return tier, is_variant


def rank_candidates(term: str, rows: list[dict]) -> list[dict]:
    """
    `rows` (every match for `term`) ordered best-first, for the judge's short
    candidate list. Plain cheapest-first let cheap look-alikes crowd the real
    item out -- with every category in play, "cheese" is a $0.65 mac-and-cheese,
    "milk" a candy bar, "bread" a box of crumbs. Tiers, best first:
      0  in the category the word itself names (cheese -> Cheese) AND the word
         ends the product's name
      1  in that category
      2  the word ends the product's name
      3  everything else
    Within a tier the plain product comes before a flavored, prepared or
    alternative one (butter before garlic parmesan butter, rice before cheddar
    broccoli rice), then cheapest estimated in-store price first. Rows are only
    reordered, never dropped.
    """
    tier, is_variant = _ranking(term)
    return sorted(rows, key=lambda r: (tier(r), is_variant(r), effective_price(r)))


def closest_per_store(term: str, chosen: dict, rows: list[dict]) -> list[dict]:
    """
    One row per store for `term`, each the closest equivalent of `chosen` (the
    product the judge picked), cheapest in-store price first. A store carrying the
    exact product gets it; any other store gets, from the chosen product's own
    category, the plain product sharing the most descriptive words with it ("Store
    Brand Whole Milk" for "Friendly Farms Vitamin D Whole Milk"), cheapest on a tie.
    A store with nothing in that category is left out rather than shown a different food.
    """
    tier, is_variant = _ranking(term)
    term_stems = set(product_embeddings.word_stems(term))
    wanted = set(product_embeddings.word_stems(_head_of(chosen["name"]))) - term_stems

    def shared_words(row: dict) -> int:
        return len((set(product_embeddings.word_stems(_head_of(row["name"]))) - term_stems) & wanted)

    by_store: dict[str, list[dict]] = {}
    for row in rows:
        by_store.setdefault(row["store_name"], []).append(row)

    picked = []
    for store_rows in by_store.values():
        exact = [r for r in store_rows if r["name"] == chosen["name"]]
        if exact:
            picked.append(min(exact, key=effective_price))
            continue
        same_category = [r for r in store_rows if r.get("category") == chosen.get("category")]
        if same_category:
            picked.append(min(
                same_category,
                key=lambda r: (tier(r), is_variant(r), -shared_words(r), effective_price(r)),
            ))
    return sorted(picked, key=effective_price)


def effective_price(row: dict) -> float:
    """What a row costs for comparison: the estimated in-store price when the store
    has a markup number for its category, otherwise the Instacart price as listed."""
    estimate = row.get("estimated_real_price")
    return row["price"] if estimate is None else estimate


def cheapest_per_store(rows: list[dict]) -> list[dict]:
    """Collapse `rows` (any order) to one cheapest entry per store, cheapest store
    first -- by effective_price, since that's what a shopper would pay."""
    best: dict[str, dict] = {}
    for row in sorted(rows, key=effective_price):
        store = row["store_name"]
        if store not in best:
            best[store] = row
    return sorted(best.values(), key=effective_price)


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
