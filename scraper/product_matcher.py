"""
product_matcher.py

Stage 3 of the RAG product-matching pipeline: an LLM judge that picks the best
scraped-product match for a recipe ingredient out of a short list of Stage-1
lexical candidates, using the recipe name and full ingredient list as
disambiguating context (e.g. "milk" should prefer a plain-milk candidate over
a flavored one in a savory recipe).
"""

import json

from scraper import ollama_client

BATCH_SIZE = 25

PICK_BEST_MATCH_SYSTEM_PROMPT = """You are a grocery-matching judge. For each grocery ingredient from a recipe, you are given a numbered list of candidate products (already narrowed down by a lexical search) and must pick the single candidate that is the best real-world match for that ingredient — or say that none of them fit.

You also get the recipe's name and its full ingredient list. Use that context to disambiguate: if two candidates are both plausible for the ingredient word alone, pick whichever fits the dish. For example, "milk" in a savory recipe's ingredient list should prefer a plain milk candidate over a flavored one (chocolate milk, strawberry milk, etc.) if both are present — a baking recipe that also lists "cocoa powder" might go the other way. Never pick a candidate that is really a different food just because it's the closest word match; if nothing on the list is truly the ingredient, say so.

Return ONLY a JSON object of this exact shape:
{"choices": [{"index": <int or null>}, ...]}

The "choices" array must have exactly one entry per ingredient, in the SAME
ORDER as the input list — always an array, even for a single ingredient. Never
skip an entry. "index" is the 0-based position of the chosen candidate in THAT
ingredient's own candidate list (candidates are numbered starting at 0). Use
null for "index" when no candidate is a good match for the ingredient — do not
guess or pick the least-bad option just to fill the field."""

_DEMO_RECIPE_NAME = "Beef Stroganoff"
_DEMO_ALL_INGREDIENTS = ["beef stew meat", "milk", "egg noodles", "ground nutmeg"]
_DEMO_BATCH = [
    {
        "ingredient": "milk",
        "candidates": [
            {"name": "Chocolate Milk, Half Gallon", "price": 3.49},
            {"name": "Whole Milk, 1 Gallon", "price": 3.99},
        ],
    },
    {
        "ingredient": "egg noodles",
        "candidates": [
            {"name": "Wide Egg Noodles, 12 oz", "price": 2.29},
        ],
    },
    {
        "ingredient": "ground nutmeg",
        "candidates": [
            {"name": "Ground Cinnamon, 2.5 oz", "price": 3.19},
            {"name": "Ground Ginger, 1.5 oz", "price": 2.99},
        ],
    },
]
# Beef Stroganoff is savory, so plain milk (index 1) beats chocolate milk (index
# 0) even though both are lexically "milk". Egg noodles has one candidate — a
# trivial pick. Neither ground-nutmeg candidate is actually nutmeg, so it's null
# rather than a least-bad guess.
_DEMO_OUTPUT = json.dumps({"choices": [{"index": 1}, {"index": 0}, {"index": None}]})


def _candidates_listing(candidates: list[dict]) -> str:
    lines = []
    for i, c in enumerate(candidates):
        name = c.get("name", "") if isinstance(c, dict) else ""
        price = c.get("price") if isinstance(c, dict) else None
        unit = c.get("unit") if isinstance(c, dict) else None
        details = []
        if price is not None:
            details.append(f"${price}")
        if unit:
            details.append(str(unit))
        suffix = f" ({', '.join(details)})" if details else ""
        lines.append(f"   {i}. {name}{suffix}")
    return "\n".join(lines)


def _listing(recipe_name: str, all_ingredients: list[str], batch: list[dict]) -> str:
    lines = [
        f"Recipe: {recipe_name}",
        f"Full ingredient list: {', '.join(all_ingredients)}",
        "",
        f"Pick the best match for each of these {len(batch)} ingredients:",
    ]
    for item in batch:
        ingredient = item.get("ingredient", "") if isinstance(item, dict) else ""
        candidates = item.get("candidates") if isinstance(item, dict) else None
        candidates = candidates if isinstance(candidates, list) else []
        lines.append(f"Ingredient: {ingredient}")
        lines.append(_candidates_listing(candidates) if candidates else "   (no candidates)")
    return "\n".join(lines)


def _batches(rows: list[dict], size: int = BATCH_SIZE):
    for i in range(0, len(rows), size):
        yield rows[i : i + size]


def _judge_batch(recipe_name: str, all_ingredients: list[str], batch: list[dict]) -> list[int | None]:
    result = ollama_client.chat_json(
        [
            {"role": "user", "content": _listing(_DEMO_RECIPE_NAME, _DEMO_ALL_INGREDIENTS, _DEMO_BATCH)},
            {"role": "assistant", "content": _DEMO_OUTPUT},
            {"role": "user", "content": _listing(recipe_name, all_ingredients, batch)},
        ],
        system=PICK_BEST_MATCH_SYSTEM_PROMPT,
    )

    def _cheapest(item) -> int | None:
        # Fallback for output that doesn't validate (spec Architecture/Boundaries:
        # "fall back to today's behavior for that ingredient — cheapest of the
        # Stage-1 candidates"). search_products() sorts candidates cheapest-first
        # (see its docstring in core.py), so that candidate is always index 0.
        # Guard against a candidate-less item so this never IndexErrors upstream.
        candidates = item.get("candidates") if isinstance(item, dict) else None
        return 0 if isinstance(candidates, list) and candidates else None

    choices = result.get("choices") if isinstance(result, dict) else None
    if not isinstance(choices, list) or len(choices) != len(batch):
        return [_cheapest(item) for item in batch]

    picks = []
    for entry, item in zip(choices, batch):
        candidates = item.get("candidates") if isinstance(item, dict) else None
        num_candidates = len(candidates) if isinstance(candidates, list) else 0
        if not isinstance(entry, dict) or "index" not in entry:
            # Malformed entry (wrong shape, or missing the field entirely) —
            # indistinguishable from a valid response only by accident, so
            # don't let it silently read as "index: null". Fall back instead.
            picks.append(_cheapest(item))
            continue
        index = entry["index"]
        if index is None:
            # Explicit "no candidate fits" (spec Open Questions #3) — a real
            # judgment, not a validation failure. Routes to not_found.
            picks.append(None)
        elif isinstance(index, int) and 0 <= index < num_candidates:
            picks.append(index)
        else:
            # Wrong type or out of range — same "doesn't validate" fallback
            # as a malformed entry, not the judge's actual verdict.
            picks.append(_cheapest(item))
    return picks


def pick_best_matches(recipe_name: str, all_ingredients: list[str], batch: list[dict]) -> list[int | None]:
    """
    Pick the best-matching candidate for each ingredient in `batch`, using the
    recipe name and full ingredient list as disambiguating context.

    batch is a list of {"ingredient": str, "candidates": [product dict, ...]}.
    Batches BATCH_SIZE ingredients per LLM call (a recipe's own ingredient list
    is always far smaller than that in practice).

    Returns one entry per item in `batch`, aligned by position: an int index
    into THAT item's own "candidates" list, or None — and the two are NOT
    interchangeable:
      - None means the judge explicitly found no good candidate (its response
        validated and said `"index": null`) — spec's Open Questions #3.
      - An output that doesn't validate at all (wrong shape, wrong length,
        missing/non-int "index", or an out-of-range index) instead falls back
        to that ingredient's cheapest Stage-1 candidate — index 0, since
        candidates arrive cheapest-first — per the spec's Architecture and
        Boundaries sections ("never crash, never guess" beyond that fallback).
        It's still an int in the return list, same as a real pick; callers
        that only care about "did this ingredient get priced" don't need to
        tell it apart from a genuine judge pick.
    """
    picks: list[int | None] = []
    for chunk in _batches(batch):
        picks.extend(_judge_batch(recipe_name, all_ingredients, chunk))
    return picks
