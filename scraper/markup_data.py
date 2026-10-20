"""
markup_data.py

Real in-store-vs-Instacart markup percentages, broken out by store AND
category -- not one blended number per category. Source: the lab's real
price-comparison spreadsheet ("FINAL_MIX 8_25_26 v2.xlsx"), "Pivot table"
sheet, which averages "(Instacart - Store) / Store" across every real matched
item for that store+category.

Only 5 categories have enough matched samples (pooled across stores) to be
worth estimating at all: Cheese, Milk, Bread, Produce, Beverages. Nova is
restricted to these for the demo, since showing a price for a category we
have no markup data for would mean presenting an un-adjusted Instacart price
as if it were checked against a real store. See classify() below.

ALDI is a deliberate special case. The spreadsheet's per-category Matches-sheet
numbers for ALDI are noisy leftovers from an earlier, less rigorous pass. A
later, more careful direct check (live HTML copied via browser DevTools, not
the hand-entered PDF matches used elsewhere) found aldi.us and Instacart show
IDENTICAL prices to the cent across 485 matched items, 15 categories, zero
exceptions -- aldi.us runs on Instacart's own "Storefront" platform under the
hood, so there's no independent second price to compare against. ALDI is
pinned to 0% for every category instead of using its noisier per-category
numbers.
"""

from __future__ import annotations

import re

CATEGORIES = ("Cheese", "Milk", "Bread", "Produce", "Beverages")

_CHEESE_WORDS = re.compile(
    r"\b(cheese|cheddar|mozzarella|parmesan|parmigiano|feta|provolone|gouda|"
    r"brie|ricotta|colby|muenster|havarti|pepper\s*jack)\b",
    re.IGNORECASE,
)
_MILK_WORDS = re.compile(r"\bmilk\b", re.IGNORECASE)
_MILK_FALSE_POSITIVES = re.compile(r"milk\s*chocolate|milk\s*duds", re.IGNORECASE)
_BREAD_WORDS = re.compile(
    r"\b(bread|bagels?|baguette|pitas?|tortillas?|english\s*muffins?|sourdough|"
    r"ciabatta|naan|dinner\s*rolls?|hamburger\s*buns?|hot\s*dog\s*buns?)\b",
    re.IGNORECASE,
)
_BREAD_FALSE_POSITIVES = re.compile(r"bread\s*crumbs?|breadsticks?", re.IGNORECASE)


def classify(name: str, department: str | None) -> str | None:
    """
    Which of the 5 demo categories this product belongs to, or None.

    `department` alone is precise enough for Produce and Beverages, but
    "dairy" and "baked-goods" are too coarse -- dairy also holds yogurt,
    butter, and cream (no markup data, not part of the demo), and
    baked-goods also holds muffins, cookies, and cakes. Those two need a
    keyword check against the actual product name to split out just
    Milk/Cheese and just Bread.
    """
    department = (department or "").lower()
    name = name or ""

    if department == "produce":
        return "Produce"
    if department == "beverages":
        return "Beverages"

    if department == "dairy":
        if _MILK_WORDS.search(name) and not _MILK_FALSE_POSITIVES.search(name):
            return "Milk"
        if _CHEESE_WORDS.search(name):
            return "Cheese"
        return None

    if department == "baked-goods":
        if _BREAD_WORDS.search(name) and not _BREAD_FALSE_POSITIVES.search(name):
            return "Bread"
        return None

    return None


# store_name -> category -> (markup_pct, sample_size). Store names match
# exactly what Instacart renders (and what instacart_scraper.py saves) --
# whatever stores a given location actually resolves to.
_RAW_MARKUP: dict[str, dict[str, tuple[float, int]]] = {
    "Stop & Shop": {
        "Beverages": (0.2228, 18),
        "Bread": (0.4038, 25),
        "Cheese": (0.3106, 28),
        "Milk": (0.3352, 10),
        "Produce": (0.2108, 28),
    },
    "Shaw's": {
        "Beverages": (0.1539, 9),
        "Bread": (0.1353, 16),
        "Cheese": (0.1033, 31),
        "Milk": (0.1475, 14),
        "Produce": (0.1106, 12),  # weighted: Fruit (n=7) + Vegetables (n=5)
    },
    "Market Basket": {
        "Bread": (0.1605, 2),
        "Cheese": (-0.0127, 3),
        "Produce": (0.2246, 7),
        # No Milk or Beverages matches at all for Market Basket.
    },
    # Pinned to 0% for all 5 -- see module docstring.
    "ALDI": {c: (0.0, 485) for c in CATEGORIES},
}


def get_markup(store_name: str, category: str) -> tuple[float, int] | None:
    """
    (markup_pct, sample_size) for `store_name` + `category`, or None if
    there's no usable number -- an unknown store, or a store+category cell
    with no real samples and nothing to fall back on.

    Falls back to a sample-weighted average across the *other* stores that
    do have real data for `category` when this store's own cell is empty
    (e.g. Market Basket has no Milk matches). ALDI is never used as a
    fallback source: its 0% reflects measured price *identity* with
    Instacart (same backend), not a typical markup, so folding it in would
    wrongly drag down an estimate for an unrelated store.
    """
    store = _RAW_MARKUP.get(store_name)
    if store is None:
        return None
    if category in store:
        return store[category]

    weighted_sum = 0.0
    total_n = 0
    for other_store, cats in _RAW_MARKUP.items():
        if other_store in ("ALDI", store_name):
            continue
        pct, n = cats.get(category, (0.0, 0))
        weighted_sum += pct * n
        total_n += n

    if total_n == 0:
        return None
    return (weighted_sum / total_n, total_n)


def estimate_real_price(instacart_price: float, store_name: str, category: str) -> float | None:
    """Estimated real in-store price, or None if we have no markup for this cell."""
    markup = get_markup(store_name, category)
    if markup is None:
        return None
    pct, _n = markup
    return round(instacart_price / (1 + pct), 2)
