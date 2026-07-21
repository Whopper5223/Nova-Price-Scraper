"""
recipe_scraper.py

Takes a recipe URL, extracts the ingredient list, then searches Instacart for each
ingredient across all available stores and outputs a price comparison.

Usage (from Nova-Price-Scraper/ root):
    python -m scraper.recipe_scraper --url "https://www.allrecipes.com/recipe/..."
    python -m scraper.recipe_scraper --url "..." --max-stores 3 --quiet
"""

import argparse
import asyncio
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests
from bs4 import BeautifulSoup

# Load .env from repo root if present
_env_file = Path(__file__).parent.parent / ".env"
if _env_file.exists():
    for _line in _env_file.read_text().splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _v = _line.split("=", 1)
            os.environ.setdefault(_k.strip(), _v.strip())

from playwright.async_api import Page

from scraper.core import (
    launch_browser,
    _human_delay,
    _dismiss_modals,
    _load_selectors,
    _get_store_list,
    resolve_store,
    search_products,
    SCRAPER_DIR,
)
from scraper.instacart_scraper import MAX_STORES

RECIPE_OUTPUT_PATH = SCRAPER_DIR / "recipe_output.json"

_REQUEST_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )
}


# ---------------------------------------------------------------------------
# Step 1: Fetch recipes from the URL
#
# A page may hold several recipes (roundups, "10 best..." lists). Every parser
# returns a list of {"name": str | None, "ingredients": list[str]} dicts; the
# caller picks one via _select_recipe.
# ---------------------------------------------------------------------------

def _parse_recipes(html: str) -> list[dict]:
    """All recipes from a page: JSON-LD first, then single-recipe fallbacks."""
    recipes = _parse_jsonld_recipes(html)
    if recipes:
        return recipes
    ingredients = _parse_microdata(html) or _parse_css_heuristic(html)
    if ingredients:
        # Microdata/CSS can't tell recipes apart — treat the page as one recipe
        return [{"name": None, "ingredients": ingredients}]
    return []


def fetch_recipes_sync(url: str) -> list[dict]:
    """
    Fetch recipes via requests only (sync, safe to call outside an event loop).
    Returns empty list if the page is JS-rendered — caller should use fetch_recipes_async instead.
    """
    html = _fetch_html_requests(url)
    return _parse_recipes(html) if html else []


async def fetch_recipes_async(url: str, page: Page) -> list[dict]:
    """
    Fetch recipes using a Playwright page that's already open (avoids nested asyncio.run).
    Tries requests first, then navigates the existing page if needed, then Gemini.
    """
    # Try requests first (fast, no browser nav needed)
    html = _fetch_html_requests(url)
    if html:
        recipes = _parse_recipes(html)
        if recipes:
            return recipes

    # JS-rendered — use the existing browser page
    print("[recipe] No recipes via requests — fetching with browser...")
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=20000)
        await page.wait_for_timeout(2000)
        html = await page.content()
        recipes = _parse_recipes(html)
        if recipes:
            return recipes
    except Exception as e:
        print(f"[recipe] Browser fetch of recipe URL failed: {e}")
        html = None

    # Last resort: Gemini
    if not html:
        print("[recipe] Could not fetch page HTML — cannot extract ingredients.")
        return []

    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        print("[recipe] No JSON-LD/microdata found. Set GEMINI_API_KEY for a Gemini fallback.")
        return []

    print("[recipe] Asking Gemini to extract ingredients from page HTML...")
    ingredients = _gemini_extract_ingredients(html[:100_000], api_key)
    return [{"name": None, "ingredients": ingredients}] if ingredients else []


def _select_recipe(recipes: list[dict], choice: str | None) -> dict | None:
    """
    Pick one recipe from what the page offered. With one recipe there is nothing
    to choose. With several: --recipe N picks by number, --recipe "name" by name
    match, and no flag defaults to the first with a printed notice.
    """
    if not recipes:
        return None
    if len(recipes) == 1:
        return recipes[0]

    names = [r["name"] or f"Recipe {i}" for i, r in enumerate(recipes, 1)]
    print(f"[recipe] This page contains {len(recipes)} recipes:")
    for i, name in enumerate(names, 1):
        print(f"  {i}. {name}  ({len(recipes[i - 1]['ingredients'])} ingredients)")

    if choice:
        if choice.isdigit():
            idx = int(choice) - 1
            if 0 <= idx < len(recipes):
                print(f"[recipe] Using recipe {idx + 1}: {names[idx]}")
                return recipes[idx]
            print(f"[recipe] --recipe {choice} is out of range (1-{len(recipes)}).")
            return None
        matches = [i for i, r in enumerate(recipes) if r["name"] and choice.lower() in r["name"].lower()]
        if not matches:
            print(f"[recipe] No recipe name matches '{choice}'.")
            return None
        if len(matches) > 1:
            print(f"[recipe] '{choice}' matches {len(matches)} recipes — using the first match.")
        print(f"[recipe] Using recipe: {names[matches[0]]}")
        return recipes[matches[0]]

    print(f"[recipe] No --recipe given — using the first: {names[0]}")
    print('[recipe] Pick a different one with --recipe N or --recipe "name".')
    return recipes[0]


def _fetch_html_requests(url: str) -> str | None:
    try:
        resp = requests.get(url, headers=_REQUEST_HEADERS, timeout=15)
        resp.raise_for_status()
        return resp.text
    except Exception as e:
        print(f"[recipe] requests fetch failed: {e}")
        return None


def _parse_jsonld_recipes(html: str) -> list[dict]:
    """Collect every Recipe object across all JSON-LD blocks on the page."""
    soup = BeautifulSoup(html, "html.parser")
    recipes = []
    seen = set()
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or "")
        except Exception:
            continue
        for r in _extract_recipes(data):
            key = (r["name"], tuple(r["ingredients"]))
            if key not in seen:
                seen.add(key)
                recipes.append(r)
    return recipes


def _parse_microdata(html: str) -> list[str]:
    """Extract ingredients from microdata (itemprop='recipeIngredient') — used by WPRM and many WP plugins."""
    soup = BeautifulSoup(html, "html.parser")
    items = soup.find_all(attrs={"itemprop": "recipeIngredient"})
    return [el.get_text(" ", strip=True) for el in items if el.get_text(strip=True)]


def _parse_css_heuristic(html: str) -> list[str]:
    """Last-resort: find elements with 'ingredient' in their class name and extract text."""
    soup = BeautifulSoup(html, "html.parser")
    found = soup.find_all(class_=re.compile(r"ingredient", re.I))
    results = []
    for el in found:
        text = el.get_text(" ", strip=True)
        if text and 3 < len(text) < 200 and not el.find(class_=re.compile(r"ingredient", re.I)):
            results.append(text)
    return results


def _extract_recipes(data) -> list[dict]:
    """
    Recursively collect every Recipe object in a JSON-LD tree.
    Handles @graph containers, nested lists, and @type given as a list
    (e.g. ["Recipe", "NewsArticle"]).
    """
    recipes = []
    if isinstance(data, dict):
        rtype = data.get("@type")
        types = rtype if isinstance(rtype, list) else [rtype]
        if "Recipe" in types and data.get("recipeIngredient"):
            recipes.append({
                "name": (data.get("name") or "").strip() or None,
                "ingredients": [str(s) for s in data.get("recipeIngredient", [])],
            })
        else:
            for value in data.values():
                if isinstance(value, (dict, list)):
                    recipes.extend(_extract_recipes(value))
    elif isinstance(data, list):
        for item in data:
            recipes.extend(_extract_recipes(item))
    return recipes


# gemini-2.0-flash was shut down June 2026 — keep this list to live models only
_GEMINI_MODELS = ["gemini-2.5-flash-lite", "gemini-2.5-flash"]


def _gemini_generate(api_key: str, prompt: str) -> str | None:
    """Try Gemini models in order, falling back on 503/overload errors."""
    from google import genai
    client = genai.Client(api_key=api_key)
    for model in _GEMINI_MODELS:
        try:
            response = client.models.generate_content(model=model, contents=prompt)
            return response.text.strip()
        except Exception as e:
            err = str(e)
            if "503" in err or "UNAVAILABLE" in err or "overload" in err.lower():
                print(f"[recipe] Gemini model {model} unavailable, trying next...")
                continue
            print(f"[recipe] Gemini error ({model}): {e}")
            return None
    print("[recipe] All Gemini models unavailable.")
    return None


def _gemini_extract_ingredients(html: str, api_key: str) -> list[str]:
    prompt = (
        "Extract the ingredient list from this recipe page HTML.\n"
        "Return ONLY a JSON array of ingredient strings, exactly as written on the page.\n"
        "Example: [\"2 cups flour\", \"1/2 tsp salt\", \"3 large eggs\"]\n"
        "No explanation, no markdown — just the JSON array.\n\n"
        f"HTML:\n{html}"
    )
    raw = _gemini_generate(api_key, prompt)
    if not raw:
        return []
    try:
        cleaned = re.sub(r"```(?:json)?\s*|\s*```", "", raw).strip()
        return json.loads(cleaned)
    except Exception as e:
        print(f"[recipe] Failed to parse Gemini ingredient response: {e}")
        return []


# ---------------------------------------------------------------------------
# Step 2: Normalize raw strings to clean search terms
# ---------------------------------------------------------------------------

_UNIT_RE = re.compile(
    r"^\s*[\d½¼¾⅓⅔⅛⅜⅝⅞]+[\d/.\s-]*"
    r"(cups?|tbsps?|tablespoons?|tsps?|teaspoons?|oz|ounces?|lbs?|pounds?|g|grams?"
    r"|ml|liters?|l|large|small|medium|cans?|cloves?|bunches?|bunch|pinch"
    r"|slices?|pieces?|stalks?|heads?|sprigs?|pkg|packages?|jars?|bottles?)?\s*",
    re.IGNORECASE,
)


def normalize_ingredients(raw_list: list[str]) -> list[str]:
    """
    Strip quantities and units from ingredient strings to produce clean search terms.
    Uses Gemini if available; falls back to regex.
    """
    if not raw_list:
        return []

    api_key = os.environ.get("GEMINI_API_KEY")
    if api_key:
        result = _gemini_normalize(raw_list, api_key)
        if result:
            return result

    normalized = []
    for raw in raw_list:
        cleaned = _UNIT_RE.sub("", raw).strip()
        cleaned = re.sub(r"\s*\([^)]*\)", "", cleaned).strip()  # strip (notes like this)
        cleaned = re.sub(r"^[^a-zA-Z]+", "", cleaned).strip()  # strip leading punctuation
        cleaned = cleaned.rstrip(",").strip()
        if cleaned:
            normalized.append(cleaned)
    return normalized


def _gemini_normalize(raw_list: list[str], api_key: str) -> list[str]:
    prompt = (
        "Convert these recipe ingredient strings to plain grocery search terms.\n"
        "Strip all quantities, measurements, and preparation notes. Keep only the core ingredient name.\n"
        "Return a JSON array in the same order. No explanation, no markdown.\n\n"
        f"Input: {json.dumps(raw_list)}\nOutput:"
    )
    raw = _gemini_generate(api_key, prompt)
    if not raw:
        return []
    try:
        cleaned = re.sub(r"```(?:json)?\s*|\s*```", "", raw).strip()
        result = json.loads(cleaned)
        if isinstance(result, list) and len(result) == len(raw_list):
            return [str(s) for s in result]
    except Exception as e:
        print(f"[recipe] Gemini normalization parse failed: {e}")
    return []


# ---------------------------------------------------------------------------
# Stores
# ---------------------------------------------------------------------------

def list_stores() -> None:
    """Print all stores from prices_output.json, or a message if none are saved."""
    stores = _load_stores_from_output()
    if not stores:
        print("No saved stores found. Run the main scraper first:")
        print("  python -m scraper.run_scraper")
        return
    print("Available stores:")
    for i, s in enumerate(stores, 1):
        print(f"  {i}. {s['name']}")


# ---------------------------------------------------------------------------
# Step 4: Orchestrate full pipeline
# ---------------------------------------------------------------------------

def _load_stores_from_output() -> list[dict]:
    """Fast path: reuse store URLs from a previous prices_output.json run."""
    output_path = SCRAPER_DIR / "prices_output.json"
    if not output_path.exists():
        return []
    try:
        data = json.loads(output_path.read_text())
        return [{"name": s["name"], "url": s["url"]} for s in data.get("stores", []) if s.get("url")]
    except Exception:
        return []


async def run_recipe_scraper(
    recipe_url: str,
    max_stores: int = MAX_STORES,
    verbose: bool = True,
    store_filter: list[str] | None = None,
    n_results: int = 5,
    skip: list[str] | None = None,
    recipe_choice: str | None = None,
) -> dict:
    """
    Full pipeline: fetch recipes → pick one → normalize → Instacart search per store → compare.
    recipe_choice picks a recipe on multi-recipe pages (number or name substring).
    Saves results to scraper/recipe_output.json.
    """
    result = {
        "recipe_url": recipe_url,
        "scraped_at": datetime.now(timezone.utc).isoformat(),
        "recipe_name": None,
        "recipes_on_page": [],
        "raw_ingredients": [],
        "ingredients": [],
        "stores": [],
        "errors": [],
    }

    print(f"[recipe] Fetching recipe: {recipe_url}")
    selectors = _load_selectors()

    async with launch_browser(verbose=verbose) as (browser, context, page):
        # Fetch recipes inside the async context so JS-rendered pages can use this browser
        recipes = await fetch_recipes_async(recipe_url, page)
        if not recipes:
            print("[recipe] No recipes found. Exiting.")
            result["errors"].append("No recipes found at the given URL.")
            return result

        result["recipes_on_page"] = [r["name"] or "(unnamed)" for r in recipes]

        recipe = _select_recipe(recipes, recipe_choice)
        if recipe is None:
            result["errors"].append(
                f"Recipe selection failed — page has {len(recipes)} recipes; pass --recipe N or --recipe \"name\"."
            )
            return result

        result["recipe_name"] = recipe["name"]
        raw_ingredients = recipe["ingredients"]
        result["raw_ingredients"] = raw_ingredients
        print(f"[recipe] Found {len(raw_ingredients)} ingredients. Normalizing...")
        ingredients = normalize_ingredients(raw_ingredients)

        if skip:
            skipped = [i for i in ingredients if any(s.lower() in i.lower() for s in skip)]
            if skipped:
                print(f"[recipe] Skipping: {', '.join(skipped)}")
                ingredients = [i for i in ingredients if i not in skipped]
            result["skipped"] = skipped

        result["ingredients"] = ingredients
        print(f"[recipe] Searching for: {', '.join(ingredients)}\n")

        cached = _load_stores_from_output()

        if store_filter:
            # Resolve each requested store: cached list first, then live lookup
            # (storefront list → Instacart search → direct URL) so stores absent
            # from the storefront page can still be searched.
            stores = []
            live_list = None
            for name in store_filter:
                match = next((s for s in cached if name.lower() in s["name"].lower()), None)
                if match:
                    stores.append(match)
                    continue
                if live_list is None:
                    live_list = await _get_store_list(page, selectors, verbose=False)
                resolved = await resolve_store(page, name, selectors, store_list=live_list, verbose=verbose)
                if resolved:
                    stores.append(resolved)
                else:
                    result["errors"].append(f"Store not available: {name}")
            if not stores:
                print(f"[recipe] No requested stores are available: {store_filter}")
                return result
        else:
            if cached:
                print(f"[recipe] Reusing {len(cached)} stores from prices_output.json.")
                stores = cached
            else:
                print("[recipe] No prior scrape found — discovering stores on Instacart...")
                stores = await _get_store_list(page, selectors)

        if max_stores > 0:
            stores = stores[:max_stores]
        if not stores:
            result["errors"].append("No stores found.")
            return result

        print(f"[recipe] Searching {len(stores)} stores × {len(ingredients)} ingredients...\n")

        for store in stores:
            store_result: dict = {
                "name": store["name"],
                "url": store["url"],
                "results": {},
                "basket_total": 0.0,
                "not_found": [],
            }

            if verbose:
                print(f"[recipe] Store: {store['name']}")

            await page.goto(store["url"], wait_until="domcontentloaded")
            await _human_delay(long=True)
            await _dismiss_modals(page, selectors)

            store_slug = store["url"].rstrip("/").split("/")[-2]

            for ingredient in ingredients:
                if verbose:
                    print(f"[recipe]   Searching: {ingredient}")
                res = await search_products(page, ingredient, selectors, n=n_results, debug_slug=store_slug)
                store_slug = ""  # only save debug HTML on first ingredient per store
                if res["count"] > 0:
                    store_result["results"][ingredient] = {
                        "price_min": res["min"],
                        "price_max": res["max"],
                        "price_median": res["median"],
                        "count": res["count"],
                        "products": res["products"],
                    }
                    store_result["basket_total"] += res["min"]
                    if verbose:
                        if res["min"] == res["max"]:
                            print(f"[recipe]     -> ${res['min']:.2f}  ({res['count']} results)")
                        else:
                            print(f"[recipe]     -> ${res['min']:.2f}–${res['max']:.2f}  ({res['count']} results)")
                else:
                    store_result["not_found"].append(ingredient)
                    if verbose:
                        print(f"[recipe]     -> not found")
                await _human_delay(short=True)

            result["stores"].append(store_result)
            await _human_delay()

    RECIPE_OUTPUT_PATH.write_text(json.dumps(result, indent=2, ensure_ascii=False))
    return result


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

def _print_comparison(result: dict) -> None:
    stores = result["stores"]
    ingredients = result["ingredients"]
    if not stores or not ingredients:
        print("[recipe] No results to display.")
        return

    store_names = [s["name"] for s in stores]
    col_w = max((len(n) for n in store_names), default=10) + 3
    col_w = max(col_w, 16)
    ing_w = max(max((len(i) for i in ingredients), default=10), len("Ingredient")) + 2

    print("\n" + "=" * 60)
    title = result.get("recipe_name")
    print(f"PRICE COMPARISON — {title}" if title else "PRICE COMPARISON")
    print("=" * 60)

    header = f"{'Ingredient':<{ing_w}}" + "".join(f"{n:<{col_w}}" for n in store_names)
    print(header)
    print("-" * len(header))

    for ingredient in ingredients:
        min_prices = [s["results"].get(ingredient, {}).get("price_min") for s in stores]
        valid_mins = [p for p in min_prices if p is not None]
        cheapest_min = min(valid_mins) if valid_mins else None

        row = f"{ingredient:<{ing_w}}"
        for i, store in enumerate(stores):
            entry = store["results"].get(ingredient)
            if entry is None:
                cell = "N/A"
            else:
                lo = entry["price_min"]
                hi = entry["price_max"]
                marker = "*" if lo == cheapest_min else ""
                if lo == hi:
                    cell = f"${lo:.2f}{marker}"
                else:
                    cell = f"${lo:.2f}-${hi:.2f}{marker}"
            row += f"{cell:<{col_w}}"
        print(row)

    print("\nCHEAPEST FULL BASKET")
    print("-" * 40)
    ranked = sorted(stores, key=lambda s: (s["basket_total"] == 0.0, s["basket_total"]))
    for i, store in enumerate(ranked):
        total = store["basket_total"]
        missing = len(store["not_found"])
        note = f"  ({missing} item(s) not found)" if missing else ""
        marker = " *" if i == 0 and total > 0 else ""
        print(f"  {store['name']:<30} ${total:.2f}{marker}{note}")

    print(f"\n  Output saved to: {RECIPE_OUTPUT_PATH}")
    print("=" * 60)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Nova Recipe Price Scraper — find the cheapest store for a recipe",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python -m scraper.recipe_scraper --list-stores
  python -m scraper.recipe_scraper --url "https://www.allrecipes.com/recipe/10813/"
  python -m scraper.recipe_scraper --url "..." --recipe 2            # 2nd recipe on a multi-recipe page
  python -m scraper.recipe_scraper --url "..." --recipe "carbonara"  # pick by name
  python -m scraper.recipe_scraper --url "..." --stores "ALDI,Target"
  python -m scraper.recipe_scraper --url "..." --max-stores 3
  python -m scraper.recipe_scraper --url "..." --quiet --raw
        """,
    )
    parser.add_argument("--url", metavar="URL", help="Recipe URL to scrape ingredients from.")
    parser.add_argument("--recipe", metavar="N_OR_NAME", default=None,
                        help="On pages with several recipes: pick by number (1-based) or name substring. Default: first recipe.")
    parser.add_argument("--list-stores", action="store_true", help="Show all available stores and exit.")
    parser.add_argument("--stores", metavar="NAMES", help='Comma-separated store names to search. Partial match, case-insensitive. e.g. "ALDI,Target"')
    parser.add_argument("--max-stores", type=int, default=None, metavar="N", help=f"Max stores to search; 0 = all (default: {MAX_STORES}).")
    parser.add_argument("--results", type=int, default=5, metavar="N", help="Matching products per ingredient that feed the price range (default: 5).")
    parser.add_argument("--skip", metavar="NAMES", help='Comma-separated ingredients to leave out, e.g. "water,salt". Partial match.')
    parser.add_argument("--quiet", action="store_true", help="Suppress verbose output.")
    parser.add_argument("--raw", action="store_true", help="Print raw JSON result.")

    args = parser.parse_args()

    if args.list_stores:
        list_stores()
        return

    if not args.url:
        parser.error("--url is required unless using --list-stores")

    max_stores = args.max_stores if args.max_stores is not None else MAX_STORES
    store_filter = [s.strip() for s in args.stores.split(",")] if args.stores else None
    skip = [s.strip() for s in args.skip.split(",") if s.strip()] if args.skip else None

    result = asyncio.run(run_recipe_scraper(
        args.url,
        max_stores=max_stores,
        verbose=not args.quiet,
        store_filter=store_filter,
        n_results=args.results,
        skip=skip,
        recipe_choice=args.recipe,
    ))

    if args.raw:
        print(json.dumps(result, indent=2))
    else:
        _print_comparison(result)


if __name__ == "__main__":
    main()
