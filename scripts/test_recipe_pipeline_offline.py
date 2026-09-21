"""
Offline end-to-end test of the recipe -> ingredient search -> Stage 3 judge ->
basket total pipeline, WITHOUT touching Instacart at all. Ingredient candidates
come from a pre-scraped dataset (scraper/test_dataset.json) instead of a live
search_products() call, so this can run anywhere Ollama is reachable and the
dataset file exists -- no browser, no login, no display needed.

Build the dataset first (needs a real interactive login, run this part locally
where you have a screen):
    python -m scraper.run_scraper --departments "produce,dairy,meat-and-seafood,baking-essentials,oils-vinegars-spices,condiments-sauces" --max-products 100 --output scraper/test_dataset.json

Then run this anywhere the dataset + Ollama are available (e.g. on the server,
against the real production model):
    python3 scripts/test_recipe_pipeline_offline.py

This mirrors scraper.recipe_scraper.run_recipe_scraper()'s own per-store logic
(collect all matched ingredients, one judge call per store, same None-vs-int
handling, same basket_total math) -- only the "how do I get candidates for an
ingredient" step is swapped from a live page search to a static dataset
lookup. Everything downstream (the judge call, the price/basket logic, the
printed comparison) is the real production code, imported and reused as-is.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from scraper import product_embeddings as pe
from scraper import product_matcher
from scraper.recipe_scraper import fetch_recipes_sync, normalize_ingredients, _select_recipe, _print_comparison

RECIPE_URL = "https://addapinch.com/best-mashed-potatoes-recipe/"
DATASET_PATH = Path(__file__).parent.parent / "scraper" / "test_dataset.json"
# Wider than search_products()'s live default (5): a live Instacart search is
# already relevance-ranked, so taking the first 5 is reasonable there. This
# dataset is just a flat department crawl with no relevance signal, so
# sorting by price and cutting to a small N risks silently excluding a
# genuine match that happens to be pricier than several irrelevant decoys.
# Wider pool here costs nothing and gives the judge a fairer set to reason
# over than an arbitrary price-based cutoff would.
N_RESULTS = 15


def _search_dataset(store_products: list[dict], query: str, n: int = N_RESULTS) -> dict:
    """Same return contract as core.py's search_products(), sourced from a static
    dataset instead of a live browser search."""
    matched = [
        p for p in store_products
        if p.get("name") and p.get("price") is not None and pe.matches_query(p["name"], query)
    ]
    matched.sort(key=lambda p: p["price"])
    results = matched[:n]
    if not results:
        return {"query": query, "min": None, "max": None, "median": None, "count": 0, "products": []}
    prices = [p["price"] for p in results]
    return {
        "query": query,
        "min": prices[0],
        "max": prices[-1],
        "median": prices[len(prices) // 2],
        "count": len(results),
        "products": results,
    }


def run():
    if not DATASET_PATH.exists():
        print(f"Dataset not found at {DATASET_PATH}")
        print("Build it first: python -m scraper.run_scraper --departments \"produce,dairy,meat-and-seafood,baking-essentials,oils-vinegars-spices,condiments-sauces\" --max-products 100 --output scraper/test_dataset.json")
        return

    dataset = json.loads(DATASET_PATH.read_text())
    stores = dataset.get("stores", [])
    print(f"Dataset: {len(stores)} store(s), scraped_at={dataset.get('scraped_at')}\n")

    recipes = fetch_recipes_sync(RECIPE_URL)
    if not recipes:
        print(f"Could not fetch/parse a recipe from {RECIPE_URL}")
        return
    recipe = _select_recipe(recipes, None)
    raw_ingredients = recipe["ingredients"]
    ingredients = normalize_ingredients(raw_ingredients)

    # De-dupe, same as run_recipe_scraper() does (two "salt" lines -> one search).
    seen = set()
    deduped = []
    for ing in ingredients:
        if ing not in seen:
            seen.add(ing)
            deduped.append(ing)
    ingredients = deduped

    print(f"Recipe: {recipe['name'] or '(unnamed)'}")
    print(f"Ingredients: {', '.join(ingredients)}\n")

    result = {
        "recipe_url": RECIPE_URL,
        "recipe_name": recipe["name"],
        "raw_ingredients": raw_ingredients,
        "ingredients": ingredients,
        "stores": [],
        "errors": [],
    }

    for store in stores:
        store_result = {
            "name": store["name"], "url": store.get("url", ""),
            "results": {}, "basket_total": 0.0, "not_found": [],
        }
        products = store.get("products", [])
        print(f"Store: {store['name']} ({len(products)} products in dataset)")

        pending = []
        for ingredient in ingredients:
            res = _search_dataset(products, ingredient)
            if res["count"] > 0:
                pending.append((ingredient, res))
                print(f"  {ingredient}: {res['count']} candidate(s) found")
            else:
                store_result["not_found"].append(ingredient)
                print(f"  {ingredient}: not found in dataset")

        if pending:
            batch = [{"ingredient": ing, "candidates": res["products"]} for ing, res in pending]
            try:
                picks = product_matcher.pick_best_matches(recipe["name"] or "(unnamed recipe)", ingredients, batch)
            except Exception as e:
                print(f"  Judge call failed ({e}) -- pricing at cheapest candidate.")
                picks = [0] * len(batch)

            for (ingredient, res), pick in zip(pending, picks):
                if pick is None:
                    store_result["not_found"].append(ingredient)
                    print(f"  {ingredient}: judge rejected all candidates -> not found")
                    continue
                product = res["products"][pick]
                price = product["price"]
                other_candidates = [p for i, p in enumerate(res["products"]) if i != pick]
                store_result["results"][ingredient] = {
                    "price_min": price, "price_max": price, "price_median": price,
                    "count": res["count"], "products": [product] + other_candidates,
                }
                store_result["basket_total"] += price
                print(f"  {ingredient}: -> ${price:.2f}  {product['name']}  ({res['count']} candidates)")

        result["stores"].append(store_result)
        print()

    _print_comparison(result)


if __name__ == "__main__":
    run()
