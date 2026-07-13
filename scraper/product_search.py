"""
product_search.py

Search Instacart stores for specific products by name and compare price ranges.

Usage (from Nova-Price-Scraper/ root):
    python -m scraper.product_search --products "butter"
    python -m scraper.product_search --products "butter,olive oil" --stores "ALDI,Hannaford" --results 8
"""

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

# Ensure repo root is on sys.path when running as a script
sys.path.insert(0, str(Path(__file__).parent.parent))

# Load .env from repo root if present
_env_file = Path(__file__).parent.parent / ".env"
if _env_file.exists():
    for _line in _env_file.read_text().splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _v = _line.split("=", 1)
            os.environ.setdefault(_k.strip(), _v.strip())

from scraper.core import (
    launch_browser,
    _load_selectors,
    _human_delay,
    _dismiss_modals,
    _get_store_list,
    resolve_store,
    search_products,
    SCRAPER_DIR,
)

OUTPUT_PATH = SCRAPER_DIR / "product_search_output.json"
DEFAULT_MAX_STORES = 5
DEFAULT_RESULTS = 5


async def run_product_search(
    queries: list[str],
    store_names: list[str] | None = None,
    n_results: int = DEFAULT_RESULTS,
    max_stores: int = DEFAULT_MAX_STORES,
    verbose: bool = True,
    output_path: Path | None = None,
) -> dict:
    """
    For each store: resolve it by name (or take the top storefront stores), then
    search each product query and record the price range across the top matches.
    """
    result = {
        "queries": queries,
        "scraped_at": datetime.now(timezone.utc).isoformat(),
        "stores": [],
        "unavailable_stores": [],
        "errors": [],
    }

    selectors = _load_selectors()

    async with launch_browser(verbose=verbose) as (browser, context, page):
        store_list = await _get_store_list(page, selectors, verbose=verbose)

        if store_names:
            targets = []
            for name in store_names:
                store = await resolve_store(page, name, selectors, store_list=store_list, verbose=verbose)
                if store:
                    targets.append(store)
                else:
                    result["unavailable_stores"].append(name)
        else:
            targets = store_list[:max_stores]

        if not targets:
            result["errors"].append("No stores available to search.")
            return result

        print(f"[search] Searching {len(targets)} store(s) for {len(queries)} product(s)...\n")

        for store in targets:
            store_result: dict = {"name": store["name"], "url": store["url"], "results": {}}
            if verbose:
                print(f"[search] Store: {store['name']}")

            await page.goto(store["url"], wait_until="domcontentloaded")
            await _human_delay(long=True)
            await _dismiss_modals(page, selectors)

            store_slug = store["url"].rstrip("/").split("/")[-2] if "/" in store["url"] else ""

            for query in queries:
                if verbose:
                    print(f"[search]   Searching: {query}")
                res = await search_products(page, query, selectors, n=n_results, debug_slug=store_slug)
                store_slug = ""  # only save debug HTML on first search per store
                store_result["results"][query] = res
                if verbose:
                    if res["count"] == 0:
                        print(f"[search]     -> not found")
                    elif res["min"] == res["max"]:
                        print(f"[search]     -> ${res['min']:.2f}  ({res['count']} results)")
                    else:
                        print(f"[search]     -> ${res['min']:.2f}-${res['max']:.2f}  ({res['count']} results)")
                await _human_delay(short=True)

            result["stores"].append(store_result)
            await _human_delay()

    out = output_path or OUTPUT_PATH
    out.write_text(json.dumps(result, indent=2, ensure_ascii=False))
    result["_output_path"] = str(out)
    return result


def _print_comparison(result: dict) -> None:
    stores = result["stores"]
    queries = result["queries"]
    if not stores:
        print("[search] No results to display.")
        if result["unavailable_stores"]:
            print(f"[search] Unavailable stores: {', '.join(result['unavailable_stores'])}")
        return

    store_names = [s["name"] for s in stores]
    col_w = max(max((len(n) for n in store_names), default=10) + 3, 16)
    q_w = max(max((len(q) for q in queries), default=10), len("Product")) + 2

    print("\n" + "=" * 60)
    print("PRODUCT PRICE COMPARISON")
    print("=" * 60)

    header = f"{'Product':<{q_w}}" + "".join(f"{n:<{col_w}}" for n in store_names)
    print(header)
    print("-" * len(header))

    for query in queries:
        mins = [s["results"].get(query, {}).get("min") for s in stores]
        valid = [m for m in mins if m is not None]
        cheapest = min(valid) if valid else None

        row = f"{query:<{q_w}}"
        for store in stores:
            entry = store["results"].get(query)
            if not entry or entry["count"] == 0:
                cell = "N/A"
            else:
                lo, hi = entry["min"], entry["max"]
                marker = "*" if lo == cheapest else ""
                cell = f"${lo:.2f}{marker}" if lo == hi else f"${lo:.2f}-${hi:.2f}{marker}"
            row += f"{cell:<{col_w}}"
        print(row)

    if result["unavailable_stores"]:
        print(f"\n  Not available for this address: {', '.join(result['unavailable_stores'])}")

    if "_output_path" in result:
        print(f"\n  Output saved to: {result['_output_path']}")
    print("=" * 60)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Nova Product Price Search — compare a product's price range across stores",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python -m scraper.product_search --products "butter"
  python -m scraper.product_search --products "butter,olive oil" --stores "ALDI,Market Basket"
  python -m scraper.product_search --products "milk" --stores "Hannaford" --results 8
        """,
    )
    parser.add_argument("--products", required=True, metavar="NAMES",
                        help='Comma-separated product names to search. e.g. "butter,olive oil"')
    parser.add_argument("--stores", metavar="NAMES",
                        help="Comma-separated store names. Any store deliverable to your address works, "
                             "even if it isn't on the storefront page. Default: top storefront stores.")
    parser.add_argument("--results", type=int, default=DEFAULT_RESULTS, metavar="N",
                        help=f"How many matching products per store feed the price range (default: {DEFAULT_RESULTS}).")
    parser.add_argument("--max-stores", type=int, default=DEFAULT_MAX_STORES, metavar="N",
                        help=f"Max stores when --stores is not given (default: {DEFAULT_MAX_STORES}).")
    parser.add_argument("--output", metavar="PATH", default=None,
                        help=f"Where to save the JSON output (default: {OUTPUT_PATH}).")
    parser.add_argument("--quiet", action="store_true", help="Suppress verbose output.")
    parser.add_argument("--raw", action="store_true", help="Print raw JSON result.")

    args = parser.parse_args()

    queries = [q.strip() for q in args.products.split(",") if q.strip()]
    store_names = [s.strip() for s in args.stores.split(",")] if args.stores else None
    output_path = Path(args.output).expanduser() if args.output else None

    result = asyncio.run(run_product_search(
        queries,
        store_names=store_names,
        n_results=args.results,
        max_stores=args.max_stores,
        verbose=not args.quiet,
        output_path=output_path,
    ))

    if args.raw:
        print(json.dumps(result, indent=2))
    else:
        _print_comparison(result)


if __name__ == "__main__":
    main()
