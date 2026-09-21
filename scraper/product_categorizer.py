"""
product_categorizer.py

Groups scraped products into the lab's USDA FoodData Central-based category
scheme, batching products through the local Ollama model so a handful of LLM
calls cover a whole store instead of one call per product.
"""

import json

from scraper import ollama_client

BATCH_SIZE = 25

CATEGORIES = {
    "Produce & Plant-Based": [
        "Vegetables and Vegetable Products",
        "Fruits and Fruit Juices",
        "Legumes and Legume Products",
        "Nut and Seed Products",
    ],
    "Meats & Seafood": [
        "Beef Products",
        "Pork Products",
        "Poultry Products",
        "Lamb, Veal, and Game Products",
        "Finfish and Shellfish Products",
        "Sausages and Luncheon Meats",
    ],
    "Dairy": ["Dairy and Egg Products"],
    "Grains": ["Cereal Grains and Pasta", "Baked Products"],
    "Miscellaneous": [
        "Beverages",
        "Fats and Oils",
        "Soups, Sauces, and Gravies",
        "Sweets",
        "Spices and Herbs",
    ],
    "Non-Food / Not Applicable": ["Non-Food / Not Applicable"],
}

# subcategory -> top-level group, so the model only ever has to name the specific
# USDA line — the broader group is looked up, never separately model-guessed,
# so the two can't come back inconsistent with each other.
_SUBCATEGORY_TO_GROUP = {sub: group for group, subs in CATEGORIES.items() for sub in subs}

_CATEGORY_LIST = "\n".join(f"- {sub}" for subs in CATEGORIES.values() for sub in subs)

CATEGORIZE_SYSTEM_PROMPT = f"""You classify grocery products into USDA FoodData Central categories.

Valid categories — use EXACTLY one of these strings, verbatim, for every product:
{_CATEGORY_LIST}

Return ONLY a JSON object of this exact shape:
{{"classifications": [{{"subcategory": "<one of the categories above, exactly as written>"}}, ...]}}

The "classifications" array must have exactly one entry per product, in the SAME
ORDER as the input list — always an array, even for a single product. Never skip
an entry, and never invent a category name that isn't in the list above.

Use "Non-Food / Not Applicable" only for products that clearly aren't a grocery
food item — electronics, tools, pet supplies, tobacco, alcohol, household goods,
office/craft supplies, and similar. Otherwise pick the closest real food
category, even if it's an imperfect fit."""

_DEMO_PRODUCTS = [
    {"name": "Boneless Chicken Breast", "department": "meat-and-seafood"},
    {"name": "Whole Milk, 1 Gallon", "department": "dairy"},
    {"name": "Sourdough Bread Loaf", "department": "bakery"},
]
_DEMO_OUTPUT = json.dumps({
    "classifications": [
        {"subcategory": "Poultry Products"},
        {"subcategory": "Dairy and Egg Products"},
        {"subcategory": "Baked Products"},
    ]
})


def _listing(rows: list[dict]) -> str:
    return "\n".join(
        f"{i + 1}. {row.get('name', '')}"
        + (f" (department: {row['department']})" if row.get("department") else "")
        for i, row in enumerate(rows)
    )


def _batches(rows: list[dict], size: int = BATCH_SIZE):
    for i in range(0, len(rows), size):
        yield rows[i : i + size]


def _categorize_batch(batch: list[dict]) -> list[str | None]:
    result = ollama_client.chat_json(
        [
            {"role": "user", "content": f"Classify these {len(_DEMO_PRODUCTS)} products:\n{_listing(_DEMO_PRODUCTS)}"},
            {"role": "assistant", "content": _DEMO_OUTPUT},
            {"role": "user", "content": f"Classify these {len(batch)} products:\n{_listing(batch)}"},
        ],
        system=CATEGORIZE_SYSTEM_PROMPT,
    )

    classifications = result.get("classifications") if isinstance(result, dict) else None
    if not isinstance(classifications, list) or len(classifications) != len(batch):
        return [None] * len(batch)

    subcats = []
    for entry in classifications:
        sub = entry.get("subcategory") if isinstance(entry, dict) else None
        subcats.append(sub if sub in _SUBCATEGORY_TO_GROUP else None)
    return subcats


def categorize_products(rows: list[dict]) -> list[dict]:
    """
    Add "category" (top-level group) and "subcategory" (USDA line) keys to each
    product row, in place. Batches BATCH_SIZE products per LLM call. A row whose
    batch failed, mismatched length, or returned an unrecognized subcategory gets
    both set to None — left for a human to check rather than guessed.
    """
    for batch in _batches(rows):
        subcats = _categorize_batch(batch)
        for row, sub in zip(batch, subcats):
            row["subcategory"] = sub
            row["category"] = _SUBCATEGORY_TO_GROUP.get(sub)
    return rows
