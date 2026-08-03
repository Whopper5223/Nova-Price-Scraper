"""
snap_eligibility.py

Rule-based SNAP/EBT eligibility classification for scraped products.

USDA SNAP rules (7 CFR 271.2 / 7 U.S.C. 2012(k)), simplified:
  - Eligible: any food or food product for home consumption, plus seeds/plants that
    produce food.
  - Ineligible: alcohol, tobacco, hot foods sold for immediate consumption, vitamins/
    supplements/medicines, and any non-food item (household goods, personal care,
    pet food, paper products, etc.).

This is a heuristic over department + product name — no live USDA database lookup.
It will occasionally misclassify an edge case (a cold deli sandwich, a borderline
"dietary supplement" energy bar). Treat `None` (uncertain) as "user should verify,"
not as ineligible.
"""

import re

# Departments that are entirely non-food or otherwise categorically ineligible.
_INELIGIBLE_DEPARTMENTS = {
    "household", "personal-care", "health-care", "laundry", "kitchen-supplies",
    "office-craft", "party-gifts", "pets", "back-to-school-essentials",
}

# Departments where eligibility depends on the specific item, not the category.
_AMBIGUOUS_DEPARTMENTS = {"deli", "prepared-foods", "baby"}

# Keyword coverage over brand names is inherently incomplete — this catches the
# common cases but a shopper should still check unfamiliar brands at checkout.
_ALCOHOL_RE = re.compile(
    r"\b(beers?|wines?|champagne|vodka|whiskey|whisky|bourbon|tequila|rum|gins?|"
    r"liqueurs?|hard seltzers?|hard ciders?|malt liquors?|ales?|ipas?|lagers?|"
    r"stouts?|pilsners?|porters?|bud light|budweiser|millers?|coors|coronas?|"
    r"heineken|guinness|modelo|stella artois|yuengling|michelob|pabst|busch)\b",
    re.IGNORECASE,
)
_TOBACCO_RE = re.compile(r"\b(cigarettes?|cigars?|tobacco|vapes?|e-cigarettes?)\b", re.IGNORECASE)
_HOT_PREPARED_RE = re.compile(
    r"\b(hot bar|rotisserie|heat[- ]?and[- ]?eat|ready[- ]?to[- ]?eat hot|"
    r"hot food|steam table|soup bar|hot deli)\b",
    re.IGNORECASE,
)
_SUPPLEMENT_RE = re.compile(
    r"\b(vitamins?|supplements?|multivitamins?|medicines?|medications?|otc\b|"
    r"aspirin|ibuprofen|acetaminophen|cold medicine)\b",
    re.IGNORECASE,
)
_NON_FOOD_BABY_RE = re.compile(r"\b(diapers?|wipes?|baby powder|baby lotion)\b", re.IGNORECASE)


def is_snap_eligible(name: str, department: str | None = None) -> bool | None:
    """
    Best-effort SNAP/EBT eligibility for one product.

    Returns True (eligible), False (ineligible), or None (ambiguous — flag it for
    the user rather than guessing).
    """
    name = name or ""
    dept = (department or "").lower()

    if _ALCOHOL_RE.search(name) or _TOBACCO_RE.search(name) or _SUPPLEMENT_RE.search(name):
        return False
    if _HOT_PREPARED_RE.search(name):
        return False

    if dept in _INELIGIBLE_DEPARTMENTS:
        return False

    if dept == "baby":
        return False if _NON_FOOD_BABY_RE.search(name) else True
    if dept in _AMBIGUOUS_DEPARTMENTS:
        return None

    return True


def annotate(rows: list[dict]) -> list[dict]:
    """Add a `snap_eligible` key (True/False/None) to each price row in place."""
    for row in rows:
        row["snap_eligible"] = is_snap_eligible(row.get("name", ""), row.get("department"))
    return rows
