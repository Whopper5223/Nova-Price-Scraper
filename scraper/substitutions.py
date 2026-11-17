"""
substitutions.py

When an ingredient has no price in our database at any store, ask the model which
other grocery items could really stand in for it (one cooking oil for another,
scallions for green onions). One batched call for the whole list. The model only
names candidates; whether a candidate is usable is decided by the caller, which
accepts it only if it has a real database price.

Anything malformed -- a bad reply, a failed call -- collapses to "no substitute".
Nothing here raises.
"""

import json

from scraper import ollama_client
from scraper.ollama_client import OllamaError

# Like the product judge, a stuck call must not hold a pricing request for minutes.
SUBSTITUTES_TIMEOUT_SECONDS = 60
MAX_OPTIONS = 2

SUBSTITUTES_SYSTEM_PROMPT = """You help price a recipe's grocery list. For each ingredient below that has no price, name up to 2 grocery-store items that could REALLY replace it in this dish at about the same quantity without changing what the dish is: one cooking oil for another, scallions for green onions, bread flour for all-purpose flour.

Answer with an empty list when nothing truly works. A different food that merely appears in similar dishes is NOT a substitute. Foods with no real stand-in (eggs, bacon, saffron, caviar) get an empty list. Never repeat the same ingredient with extra words.

Return ONLY JSON of this exact shape, with one entry per ingredient:
{"substitutes": [{"ingredient": "<exactly as given>", "options": ["<name>", ...]}, ...]}
Option names are plain grocery search words: lowercase, no brands, no quantities."""

_DEMO_LISTING = "Meal: chicken stir fry\nIngredients with no price: safflower oil, chives, caviar"
_DEMO_OUTPUT = json.dumps({"substitutes": [
    {"ingredient": "safflower oil", "options": ["canola oil", "vegetable oil"]},
    {"ingredient": "chives", "options": ["green onions"]},
    {"ingredient": "caviar", "options": []},
]})


def propose_substitutes(items: list[str], context: str = "", model: str | None = None) -> dict[str, list[str]]:
    """
    {ingredient: [up to 2 candidate substitute names]} for the ingredients in `items`
    the model could name a real stand-in for. An ingredient with no usable proposal is
    simply absent. `context` is what the user is making, so "oil" is judged for the dish.
    """
    if not items:
        return {}

    wanted = {item.strip().lower(): item for item in items}
    listing = f"Meal: {context or 'a grocery shopping list'}\nIngredients with no price: {', '.join(items)}"
    try:
        result = ollama_client.chat_json(
            [
                {"role": "user", "content": _DEMO_LISTING},
                {"role": "assistant", "content": _DEMO_OUTPUT},
                {"role": "user", "content": listing},
            ],
            system=SUBSTITUTES_SYSTEM_PROMPT,
            model=model,
            timeout=SUBSTITUTES_TIMEOUT_SECONDS,
        )
    except OllamaError as e:
        print(f"[nova] Substitution lookup failed ({e}); no substitutes.")
        return {}

    entries = result.get("substitutes") if isinstance(result, dict) else None
    if not isinstance(entries, list):
        return {}

    proposals: dict[str, list[str]] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        ingredient, options = entry.get("ingredient"), entry.get("options")
        if not isinstance(ingredient, str) or not isinstance(options, list):
            continue
        key = ingredient.strip().lower()
        if key not in wanted:
            continue
        names: list[str] = []
        for option in options:
            if not isinstance(option, str):
                continue
            name = option.strip().lower()
            if name and name != key and name not in names:
                names.append(name)
            if len(names) >= MAX_OPTIONS:
                break
        if names:
            proposals[key] = names
    return proposals
