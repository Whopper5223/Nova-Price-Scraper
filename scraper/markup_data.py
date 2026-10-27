"""
markup_data.py

Real in-store-vs-Instacart markup percentages, broken out by store AND
category. Source: the lab's real price-comparison spreadsheet
("FINAL_MIX 8_25_26 v2.xlsx"), "Pivot table" sheet, which averages
"(Instacart - Store) / Store" across every real matched item for that
store+category. Every cell below is that average for one store+category,
with the number of matched items behind it.

Real data only: a store+category cell with no real number stays empty (no
pooled estimate is invented from other stores), and there is no minimum
sample size -- even a single matched item counts. A category nobody has a
real number for is not a category at all, so products in it are dropped by
classify() rather than shown with an un-adjusted Instacart price. See
docs/intent/markup-full-category-expansion.md for the decisions behind this.

ALDI is a deliberate special case. Its Pivot-table numbers are noisy
leftovers from an earlier, less rigorous pass. A later, more careful direct
check (live HTML copied via browser DevTools, not the hand-entered PDF
matches used elsewhere) found aldi.us and Instacart show IDENTICAL prices to
the cent across 485 matched items, 15 categories, zero exceptions -- aldi.us
runs on Instacart's own "Storefront" platform under the hood, so there's no
independent second price to compare against. That's about ALDI's whole
backend, not any one category, so ALDI is pinned to 0% for every category.
"""

from __future__ import annotations

import re

# Nova's own category names. The spreadsheet labels categories its own way and
# not always consistently across stores ("PRODUCE" vs "PRODUCE - FRUIT" /
# "PRODUCE - VEGETABLES", "SNACKS" vs "SNACKS - CHIPS", one combined
# "MEAT/FISH" vs separate "BEEF" / "CHICKEN" / "FISH/SEAFOOD"), so each store's
# cells below are keyed by these names, with the reconciliation noted inline.
# "Baby" is deliberately absent: the scraper's baby department mixes baby food
# with diapers and wipes, and only Market Basket has a number for it.
CATEGORIES = (
    "Cheese", "Milk", "Butter & Creamer", "Yogurt",
    "Bread", "Cereal", "Produce", "Beverages", "Snacks",
    "Beef", "Chicken", "Fish & Seafood",
    "Frozen", "Deli", "Prepared Foods",
    "Pasta & Dry Goods", "Oils & Spices", "Baking Essentials", "Canned Goods", "Condiments",
)

# Departments that map straight to one category, no keyword check needed.
_DEPARTMENT_CATEGORY = {
    "produce": "Produce",
    "beverages": "Beverages",
    "snacks-and-candy": "Snacks",
    "frozen": "Frozen",
    "deli": "Deli",
    "prepared-foods": "Prepared Foods",
    "canned-goods": "Canned Goods",
    "dry-goods-pasta": "Pasta & Dry Goods",
    "condiments-sauces": "Condiments",
    "oils-vinegars-spices": "Oils & Spices",
    "baking-essentials": "Baking Essentials",
}

_CHEESE_WORDS = re.compile(
    r"\b(cheese|cheddar|mozzarella|parmesan|parmigiano|feta|provolone|gouda|"
    r"brie|ricotta|colby|muenster|havarti|pepper\s*jack)\b",
    re.IGNORECASE,
)
# Checked before cheese so a cheese-flavored butter ("Garlic Parmesan Butter")
# is butter, and before milk so a milk-based creamer is a creamer.
_BUTTER_CREAMER_WORDS = re.compile(
    r"\b(butter|creamers?|half\s*(?:and|&)\s*half)\b", re.IGNORECASE,
)
_YOGURT_WORDS = re.compile(r"\b(yogurts?|yoghurts?)\b", re.IGNORECASE)
_MILK_WORDS = re.compile(r"\bmilk\b", re.IGNORECASE)
_MILK_FALSE_POSITIVES = re.compile(r"milk\s*chocolate|milk\s*duds", re.IGNORECASE)

_BREAD_WORDS = re.compile(
    r"\b(bread|bagels?|baguette|pitas?|tortillas?|english\s*muffins?|sourdough|"
    r"ciabatta|naan|dinner\s*rolls?|hamburger\s*buns?|hot\s*dog\s*buns?)\b",
    re.IGNORECASE,
)
_BREAD_FALSE_POSITIVES = re.compile(r"bread\s*crumbs?|breadsticks?", re.IGNORECASE)

_CEREAL_WORDS = re.compile(r"\b(cereals?|granola|oatmeal|muesli)\b", re.IGNORECASE)
_BARS = re.compile(r"\bbars?\b", re.IGNORECASE)

_FISH_WORDS = re.compile(
    r"\b(fish|salmon|tuna|cod|tilapia|haddock|halibut|trout|shrimp|prawns?|crab|"
    r"lobster|scallops?|clams?|mussels?|oysters?|seafood|swordfish|mahi|catfish|"
    r"sole|flounder|snapper|bass|sardines?|anchovies|calamari|squid|octopus|"
    r"crawfish|crayfish)\b",
    re.IGNORECASE,
)
_CHICKEN_WORDS = re.compile(r"\bchicken\b", re.IGNORECASE)
_BEEF_NAMED = re.compile(r"\bbeef\b", re.IGNORECASE)
# Cuts that mean beef only when the name doesn't also say another meat
# ("Turkey Burgers", "Pork Steak").
_BEEF_CUTS = re.compile(
    r"\b(steaks?|sirloin|ribeye|rib[- ]eye|brisket|chuck|flank|t-bone|"
    r"porterhouse|filet\s*mignon|burgers?)\b",
    re.IGNORECASE,
)
_OTHER_MEATS = re.compile(
    r"\b(pork|turkey|lamb|bison|veal|ham|bacon|sausages?)\b", re.IGNORECASE,
)

# Instacart sometimes renders a store name with a zero-width space inside it
# ("Shaw​'s"), which would silently miss the exact-match lookup below.
_INVISIBLE_CHARS = re.compile("[​‌‍⁠﻿]")


def _classify_dairy(name: str) -> str | None:
    if _BUTTER_CREAMER_WORDS.search(name):
        return "Butter & Creamer"
    if _CHEESE_WORDS.search(name):
        return "Cheese"
    if _YOGURT_WORDS.search(name):
        return "Yogurt"
    if _MILK_WORDS.search(name) and not _MILK_FALSE_POSITIVES.search(name):
        return "Milk"
    return None


def _classify_meat_and_seafood(name: str) -> str | None:
    if _FISH_WORDS.search(name):
        return "Fish & Seafood"
    if _CHICKEN_WORDS.search(name):
        return "Chicken"
    if _BEEF_NAMED.search(name):
        return "Beef"
    if _BEEF_CUTS.search(name) and not _OTHER_MEATS.search(name):
        return "Beef"
    return None


def classify(name: str, department: str | None) -> str | None:
    """
    Which category this product belongs to, or None if no store has a real
    markup number for anything like it.

    Most departments are precise enough on their own. "dairy", "baked-goods",
    "breakfast-foods" and "meat-and-seafood" are too coarse -- dairy also holds
    eggs and sour cream, baked-goods also holds muffins and cakes, and so on --
    so those need a keyword check against the actual product name to split out
    just the categories that have data.
    """
    department = (department or "").lower()
    name = name or ""

    if department in _DEPARTMENT_CATEGORY:
        return _DEPARTMENT_CATEGORY[department]
    if department == "dairy":
        return _classify_dairy(name)
    if department == "baked-goods":
        if _BREAD_WORDS.search(name) and not _BREAD_FALSE_POSITIVES.search(name):
            return "Bread"
        return None
    if department == "breakfast-foods":
        if _CEREAL_WORDS.search(name) and not _BARS.search(name):
            return "Cereal"
        return None
    if department == "meat-and-seafood":
        return _classify_meat_and_seafood(name)
    return None


# Departments classify() splits by keyword rather than taking whole.
_KEYWORD_SPLIT_DEPARTMENTS = ("dairy", "baked-goods", "breakfast-foods", "meat-and-seafood")


def categories_for_term(term: str) -> set[str]:
    """
    The categories a search word itself points at, using the same keyword rules
    classify() applies to product names: "cheese" -> {"Cheese"}, "chicken breast"
    -> {"Chicken"}. Empty for words only a whole department covers ("bananas",
    "orange juice", "rice"), since nothing about the word names a category.
    """
    return {c for d in _KEYWORD_SPLIT_DEPARTMENTS if (c := classify(term, d))}


# Market Basket's sheet has one combined MEAT/FISH row (a single matched item)
# instead of separate beef/chicken/fish rows, so that one number covers each.
_MARKET_BASKET_MEAT_FISH = (0.2505, 1)

# ALDI's pinned-0% sample size: the 485 identical-price items behind the
# Storefront finding in the module docstring.
_ALDI_PINNED_N = 485

# store_name -> category -> (markup_pct, sample_size). Store names match
# exactly what Instacart renders (and what instacart_scraper.py saves) --
# whatever stores a given location actually resolves to.
_RAW_MARKUP: dict[str, dict[str, tuple[float, int]]] = {
    "Stop & Shop": {
        "Cheese": (0.3106, 28),
        "Produce": (0.2108, 28),
        "Bread": (0.4038, 25),
        "Beverages": (0.2228, 18),
        "Cereal": (0.5077, 18),
        "Snacks": (0.3500, 14),  # sheet label is "SNACKS - CHIPS"
        "Milk": (0.3352, 10),
        "Butter & Creamer": (0.3289, 7),
        "Fish & Seafood": (0.2464, 7),
        "Chicken": (0.0321, 4),
        "Yogurt": (0.0616, 4),
        "Beef": (0.1767, 1),
    },
    "Shaw's": {
        "Cheese": (0.1033, 31),
        "Bread": (0.1353, 16),
        "Milk": (0.1475, 14),
        "Fish & Seafood": (0.1508, 11),
        "Beverages": (0.1539, 9),
        "Chicken": (0.1755, 8),
        "Butter & Creamer": (0.1572, 7),
        "Produce": (0.1106, 12),  # sheet splits Fruit (16.10%, n=7) + Vegetables (4.02%, n=5); kept as their weighted blend
        "Beef": (0.1484, 7),
        "Yogurt": (0.1557, 6),
        "Snacks": (0.1259, 4),
        "Cereal": (0.0932, 2),
    },
    "Market Basket": {
        "Produce": (0.2246, 7),
        # The raw pivot says +68.47% (n=5), but one of the five is Goya Thai
        # Jasmine Rice, $2.99 on the website vs $13.99 on Instacart (+368%) -- a
        # package-size mismatch the spreadsheet author's own review excluded.
        "Pasta & Dry Goods": (-0.0639, 4),
        "Frozen": (0.2677, 5),
        "Oils & Spices": (0.1357, 5),
        "Prepared Foods": (0.1374, 5),
        "Baking Essentials": (-0.1191, 4),
        "Canned Goods": (0.1705, 4),
        "Cheese": (-0.0127, 3),
        "Bread": (0.1605, 2),
        "Condiments": (0.0267, 2),
        "Deli": (0.2392, 1),
        "Beef": _MARKET_BASKET_MEAT_FISH,
        "Chicken": _MARKET_BASKET_MEAT_FISH,
        "Fish & Seafood": _MARKET_BASKET_MEAT_FISH,
        # No Milk, Beverages, Yogurt, Butter & Creamer, Cereal or Snacks matches
        # at all for Market Basket -- those stay empty.
    },
    # Pinned to 0% for every category -- see module docstring.
    "ALDI": {c: (0.0, _ALDI_PINNED_N) for c in CATEGORIES},
}


def get_markup(store_name: str, category: str) -> tuple[float, int] | None:
    """
    (markup_pct, sample_size) for `store_name` + `category`, or None if that
    store has no real number for that category -- an unknown store, or a
    store+category cell the spreadsheet never sampled. Never estimated from
    other stores.
    """
    store = _RAW_MARKUP.get(_INVISIBLE_CHARS.sub("", store_name or ""))
    if store is None:
        return None
    return store.get(category)


def estimate_real_price(instacart_price: float, store_name: str, category: str) -> float | None:
    """Estimated real in-store price, or None if we have no markup for this cell."""
    markup = get_markup(store_name, category)
    if markup is None:
        return None
    pct, _n = markup
    return round(instacart_price / (1 + pct), 2)
