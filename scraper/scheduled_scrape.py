"""
scheduled_scrape.py

Unattended refresh of prices_output.json — the file the chat assistant reads.

run_scraper.run_scraper() already writes prices_output.json itself, so this is a thin
wrapper that runs it non-interactively, logs a one-line result, and exits with a status
code a task scheduler can act on (0 = ok, 1 = failed).

Usage (from the repo root):
    python -m scraper.scheduled_scrape
    python -m scraper.scheduled_scrape --max-stores 0 --headless
    python -m scraper.scheduled_scrape --departments "produce,dairy"

Schedule it daily on Windows (adjust the paths to your machine):
    schtasks /create /tn "NovaPriceScrape" /sc daily /st 03:00 ^
      /tr "cmd /c cd /d C:\\Users\\unmes\\nova-scraper && python -m scraper.scheduled_scrape --quiet"

Note: the scraper runs a visible browser by default and needs a saved Instacart session
(python -m scraper.run_scraper --login). Log in once before relying on the schedule.
"""

import argparse
import asyncio
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

import scraper.instacart_scraper as _scraper_module
from scraper import price_lookup


def _log(msg: str) -> None:
    """Timestamped line so scheduler logs are readable after the fact."""
    print(f"[{datetime.now(timezone.utc).isoformat(timespec='seconds')}] [scheduled] {msg}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Refresh prices_output.json for the chat assistant (scheduler-friendly).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python -m scraper.scheduled_scrape
  python -m scraper.scheduled_scrape --max-stores 0
  python -m scraper.scheduled_scrape --departments "produce,dairy" --quiet
        """,
    )
    parser.add_argument("--address", metavar="ADDR", default=None,
                        help="Delivery address for this run (default: DELIVERY_ADDRESS env var).")
    parser.add_argument("--stores", metavar="NAMES", default=None,
                        help='Comma-separated store names to scrape. Default: all discovered stores.')
    parser.add_argument("--departments", metavar="NAMES", default=None,
                        help='Comma-separated departments, e.g. "produce,dairy". Default: every department.')
    parser.add_argument("--max-stores", type=int, default=None, metavar="N",
                        help=f"Max stores; 0 = all (default: {_scraper_module.MAX_STORES}).")
    parser.add_argument("--max-products", type=int, default=None, metavar="N",
                        help=f"Max products per department; 0 = unlimited (default: {_scraper_module.MAX_PRODUCTS_PER_CATEGORY}).")
    parser.add_argument("--headless", action="store_true",
                        help="Run without a visible browser window (higher bot detection risk).")
    parser.add_argument("--quiet", action="store_true", help="Suppress per-product scraper output.")

    args = parser.parse_args()

    if args.address:
        _scraper_module.DELIVERY_ADDRESS = args.address
    if args.max_stores is not None:
        _scraper_module.MAX_STORES = args.max_stores
    if args.max_products is not None:
        _scraper_module.MAX_PRODUCTS_PER_CATEGORY = args.max_products
    if args.departments:
        _scraper_module.TARGET_DEPARTMENTS = [
            d.strip().lower() for d in args.departments.split(",") if d.strip()
        ]

    if args.headless:
        # Same override run_scraper.cmd_scrape uses — Playwright's launch() takes headless
        # per-call, so patch the default rather than threading a flag through core.py.
        import playwright.async_api as _pw_api
        _original_launch = _pw_api.BrowserType.launch

        async def _headless_launch(self, **kwargs):
            kwargs["headless"] = True
            return await _original_launch(self, **kwargs)

        _pw_api.BrowserType.launch = _headless_launch

    store_filter = [s.strip() for s in args.stores.split(",")] if args.stores else None

    _log("Starting scheduled price refresh...")
    try:
        result = asyncio.run(_scraper_module.run_scraper(
            verbose=not args.quiet,
            store_filter=store_filter,
        ))
    except Exception as e:
        _log(f"FAILED: {e}")
        sys.exit(1)

    stores = result.get("stores", [])
    products = sum(s.get("product_count", 0) for s in stores)
    errors = result.get("errors", [])

    _log(f"Done: {len(stores)} store(s), {products} product(s).")
    for err in errors:
        _log(f"  warning: {err}")
    _log(f"Assistant data now: {price_lookup.describe_freshness()}")

    # No products means the run produced nothing usable for the assistant, even if the
    # scraper itself didn't raise — surface that to the scheduler as a failure.
    if products == 0:
        _log("No products scraped — treating as failure.")
        sys.exit(1)


if __name__ == "__main__":
    main()
