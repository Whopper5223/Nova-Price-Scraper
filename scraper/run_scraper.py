"""
run_scraper.py

CLI entry point for the Instacart price scraper.

Usage (from nova-backend/ directory):
    python -m scraper.run_scraper
    python -m scraper.run_scraper --headless
    python -m scraper.run_scraper --max-stores 3
    python -m scraper.run_scraper --heal-only product_price

Environment variables required:
    GEMINI_API_KEY  — needed for self-healing selectors (optional if not healing)
"""

import argparse
import asyncio
import json
import os
import re
import sys
from pathlib import Path

# Ensure the nova-backend root is on sys.path when running as a script
sys.path.insert(0, str(Path(__file__).parent.parent))

# Load .env from nova-backend/ if present
_env_file = Path(__file__).parent.parent / ".env"
if _env_file.exists():
    for _line in _env_file.read_text().splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _v = _line.split("=", 1)
            os.environ.setdefault(_k.strip(), _v.strip())

import scraper.instacart_scraper as _scraper_module
from scraper.selector_healer import heal_selector, HEALABLE_KEYS


def _check_env() -> list[str]:
    """Return a list of warnings about missing environment config."""
    warnings = []
    if not os.environ.get("GEMINI_API_KEY"):
        warnings.append(
            "GEMINI_API_KEY is not set. Self-healing selectors will be disabled."
        )
    return warnings


def _print_summary(result: dict) -> None:
    print("\n" + "=" * 60)
    print("SCRAPE SUMMARY")
    print("=" * 60)
    print(f"  Scraped at : {result.get('scraped_at', 'unknown')}")
    print(f"  Location   : {result.get('location', 'unknown')}")
    print(f"  Stores     : {len(result.get('stores', []))}")

    total = sum(s.get("product_count", 0) for s in result.get("stores", []))
    print(f"  Products   : {total}")

    if result.get("errors"):
        print(f"\n  Errors ({len(result['errors'])}):")
        for err in result["errors"]:
            print(f"    - {err}")

    print("\n  Per-store breakdown:")
    for store in result.get("stores", []):
        status = "OK" if not store.get("error") else f"ERROR: {store['error']}"
        print(f"    {store['name']:<30} {store.get('product_count', 0):>4} products  [{status}]")

    output_path = Path(__file__).parent / "prices_output.json"
    print(f"\n  Output file: {output_path}")
    print("=" * 60)


def _parse_store_filter(stores_arg: str | None) -> list[str] | None:
    if not stores_arg:
        return None
    return [s.strip() for s in stores_arg.split(",")]


def cmd_list_stores(_args: argparse.Namespace) -> None:
    """Print stores from the last scrape run, or a hint to run the scraper first."""
    output_path = Path(__file__).parent / "prices_output.json"
    if not output_path.exists():
        print("No saved stores found. Run the scraper first:")
        print("  python -m scraper.run_scraper")
        return
    data = json.loads(output_path.read_text())
    stores = data.get("stores", [])
    if not stores:
        print("prices_output.json exists but has no stores.")
        return
    print("Available stores (from last scrape):")
    for i, s in enumerate(stores, 1):
        print(f"  {i}. {s['name']}")


def cmd_scrape(args: argparse.Namespace) -> None:
    """Run the full scraper."""
    warnings = _check_env()
    for w in warnings:
        print(f"[warning] {w}")

    store_filter = _parse_store_filter(getattr(args, "stores", None))

    if args.address:
        _scraper_module.DELIVERY_ADDRESS = args.address
    if args.max_stores is not None:
        _scraper_module.MAX_STORES = args.max_stores
    if args.max_products is not None:
        _scraper_module.MAX_PRODUCTS_PER_CATEGORY = args.max_products
    if args.max_per_store is not None:
        _scraper_module.MAX_PRODUCTS_PER_STORE = args.max_per_store
    if args.departments:
        _scraper_module.TARGET_DEPARTMENTS = [
            d.strip().lower() for d in args.departments.split(",") if d.strip()
        ]

    if args.output:
        _scraper_module.OUTPUT_PATH = Path(args.output).expanduser()
    if args.headless:
        print("[run] Running in headless mode (higher bot detection risk).")

    if args.headless:
        import playwright.async_api as _pw_api
        _original_launch = _pw_api.BrowserType.launch

        async def _headless_launch(self, **kwargs):
            kwargs["headless"] = True
            return await _original_launch(self, **kwargs)

        _pw_api.BrowserType.launch = _headless_launch

    result = asyncio.run(_scraper_module.run_scraper(verbose=not args.quiet, store_filter=store_filter))
    _print_summary(result)


def cmd_heal(args: argparse.Namespace) -> None:
    """Manually trigger healing for a specific selector key using provided HTML."""
    key = args.selector_key
    if key not in HEALABLE_KEYS:
        print(f"[error] '{key}' is not a valid healable selector key.")
        print(f"Valid keys: {', '.join(sorted(HEALABLE_KEYS))}")
        sys.exit(1)

    if not os.environ.get("GEMINI_API_KEY"):
        print("[error] GEMINI_API_KEY must be set to use --heal-only.")
        sys.exit(1)

    if args.html_file:
        html = Path(args.html_file).read_text(encoding="utf-8")
    else:
        print("Paste HTML (end with EOF on a new line, or Ctrl+D):")
        lines = []
        try:
            while True:
                line = input()
                if line == "EOF":
                    break
                lines.append(line)
        except EOFError:
            pass
        html = "\n".join(lines)

    result = heal_selector(key, html, verbose=True)
    if result:
        print(f"\nHealing succeeded. New selector: {result}")
    else:
        print("\nHealing failed. Check output above for details.")
        sys.exit(1)


def cmd_show_output(args: argparse.Namespace) -> None:
    """Pretty-print the current prices_output.json."""
    output_path = Path(__file__).parent / "prices_output.json"
    if not output_path.exists():
        print("[error] prices_output.json not found. Run the scraper first.")
        sys.exit(1)

    data = json.loads(output_path.read_text())

    if args.store:
        stores = [s for s in data.get("stores", []) if args.store.lower() in s["name"].lower()]
        if not stores:
            print(f"[error] No store matching '{args.store}' found in output.")
            sys.exit(1)
        data["stores"] = stores

    if args.raw:
        print(json.dumps(data, indent=2))
    else:
        _print_summary(data)
        if args.products:
            for store in data.get("stores", []):
                print(f"\n  {store['name']} — products:")
                for p in store.get("products", []):
                    unit = f" ({p['unit']})" if p.get("unit") else ""
                    print(f"    ${p['price']:.2f}{unit}  {p['name']}")


def cmd_login(_args: argparse.Namespace) -> None:
    """
    Open a visible browser so the user can log in and set their address, then save the session.

    Uses a persistent on-disk Chrome profile (scraper/browser_profile/) rather than a
    throwaway context. Instacart fingerprints fresh automated contexts and silently
    disables the login button — a profile that persists across runs reads as a real
    returning user and gets through. The profile also keeps you logged in on its own,
    so session.json is really just a convenience export for the scraping runs.
    """
    from playwright.async_api import async_playwright

    from scraper.core import BROWSER_ARGS, BROWSER_CHANNEL, CONTEXT_KWARGS, STEALTH_AVAILABLE

    session_path = Path(__file__).parent / "session.json"
    profile_dir = Path(__file__).parent / "browser_profile"

    async def do_login() -> None:
        async with async_playwright() as pw:
            # launch_persistent_context writes a real Chrome profile to disk and reuses
            # it every run. Combined with the automation flags stripped below, this is
            # what gets past the login-button block that a plain new_context() hits.
            persistent_kwargs: dict = {
                "headless": False,
                "args": BROWSER_ARGS + [
                    # Chrome shows an "automated software" infobar and sets extra
                    # automation bits without these; both are strong bot signals.
                    "--disable-infobars",
                    "--exclude-switches=enable-automation",
                ],
                "ignore_default_args": ["--enable-automation"],
                **CONTEXT_KWARGS,
            }
            if BROWSER_CHANNEL:
                persistent_kwargs["channel"] = BROWSER_CHANNEL
                print(f"[login] Browser channel: {BROWSER_CHANNEL}")

            try:
                context = await pw.chromium.launch_persistent_context(
                    str(profile_dir), **persistent_kwargs
                )
            except Exception as e:
                if not BROWSER_CHANNEL:
                    raise
                print(f"[login] Channel '{BROWSER_CHANNEL}' unavailable ({e}); using bundled Chromium.")
                persistent_kwargs.pop("channel", None)
                context = await pw.chromium.launch_persistent_context(
                    str(profile_dir), **persistent_kwargs
                )
            page = context.pages[0] if context.pages else await context.new_page()

            if STEALTH_AVAILABLE:
                from playwright_stealth import Stealth
                await Stealth().apply_stealth_async(page)
                print("[login] Stealth mode active.")
            else:
                print("[login] WARNING: playwright-stealth not installed — Instacart will likely")
                print("[login]          block the login button. Install it first:")
                print("[login]          pip install playwright-stealth")

            print(f"[login] Using persistent browser profile: {profile_dir}")
            await page.goto("https://www.instacart.com/login")

            print("\n[login] Browser opened to the Instacart login page.")
            print("[login] Complete ALL of these steps in the browser window:")
            print("  1. Solve any CAPTCHA")
            print("  2. Enter your email and password and log in fully")
            print("  3. Wait until you can see the Instacart homepage (not a login page)")
            print(f"  4. Set your delivery address to: {_scraper_module.DELIVERY_ADDRESS}")
            print("  5. Confirm you can see a list of stores")
            print("\n[login] Tip: prefer the email + password form over Google/Facebook sign-in —")
            print("[login]      third-party popups are the most aggressively bot-checked path.")
            print("\n[login] The browser will stay open until you press ENTER here.")
            print("[login] DO NOT press ENTER until step 5 is done.\n")

            input("[login] Press ENTER when you can see stores on screen...")

            # Instacart opens a new tab after login — grab whichever page is active
            all_pages = context.pages
            active_page = all_pages[-1] if len(all_pages) > 1 else page
            print(f"[login] Active tab URL: {active_page.url}")

            state = await context.storage_state(path=str(session_path))
            await context.close()

        # Tracking cookies alone mean the login didn't take — say so rather than
        # letting the next scrape fail with a confusing "No stores found".
        names = {c["name"] for c in state.get("cookies", [])}
        if not any(re.search(r"sess|auth|token|_ic_|user", n, re.I) for n in names):
            print("\n[login] WARNING: no login cookies were saved — you may not be logged in.")
            print(f"[login]          Cookies found: {', '.join(sorted(names)) or 'none'}")
            print("[login]          Re-run --login and make sure you reach the store list first.")
            return

        print(f"[login] Session saved to {session_path}")
        print("[login] You can now run the scraper normally.")

    asyncio.run(do_login())


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Nova Instacart Price Scraper",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python -m scraper.run_scraper --login                 Log in once and save session
  python -m scraper.run_scraper                         All departments, 150 products each
  python -m scraper.run_scraper --departments "produce,dairy"   Only these departments
  python -m scraper.run_scraper --max-stores 0          Every store found
  python -m scraper.run_scraper --max-products 0        No per-department cap
  python -m scraper.run_scraper --address "1 Main St, Boston, MA 02108"  Different address
  python -m scraper.run_scraper --headless              Run without browser window
  python -m scraper.run_scraper --heal-only product_price  Manually heal a selector
  python -m scraper.run_scraper --show-output --products   Show last scraped data
        """,
    )

    parser.add_argument(
        "--login",
        action="store_true",
        help="Open browser to log in manually and save the session. Run this once before scraping.",
    )
    parser.add_argument(
        "--list-stores",
        action="store_true",
        help="Show all stores from the last scrape run and exit.",
    )
    parser.add_argument(
        "--stores",
        metavar="NAMES",
        help='Comma-separated store names to scrape. Partial match, case-insensitive. e.g. "ALDI,Target"',
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        help="Run browser in headless mode (no visible window). Higher bot detection risk.",
    )
    parser.add_argument(
        "--address",
        metavar="ADDR",
        default=None,
        help="Delivery address for this run. Defaults to the DELIVERY_ADDRESS env var (set it in .env).",
    )
    parser.add_argument(
        "--departments",
        metavar="NAMES",
        default=None,
        help='Comma-separated departments to scrape, e.g. "produce,dairy". Default: every department the store has.',
    )
    parser.add_argument(
        "--max-stores",
        type=int,
        default=None,
        metavar="N",
        help=f"Max number of stores to scrape; 0 = all stores found (default: {_scraper_module.MAX_STORES})",
    )
    parser.add_argument(
        "--max-products",
        type=int,
        default=None,
        metavar="N",
        help=f"Max products per department; 0 = unlimited (default: {_scraper_module.MAX_PRODUCTS_PER_CATEGORY})",
    )
    parser.add_argument(
        "--max-per-store",
        type=int,
        default=None,
        metavar="N",
        help="Total product cap across a whole store; 0 = unlimited (default: 0). The per-department cap still applies.",
    )
    parser.add_argument(
        "--output",
        metavar="PATH",
        default=None,
        help="Where to save the JSON output (default: scraper/prices_output.json). e.g. ~/Desktop/prices.json",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress verbose progress output.",
    )
    parser.add_argument(
        "--heal-only",
        metavar="SELECTOR_KEY",
        help="Manually trigger healing for a specific selector key. Requires --html-file or stdin HTML.",
    )
    parser.add_argument(
        "--html-file",
        metavar="PATH",
        help="Path to an HTML file to use as input for --heal-only.",
    )
    parser.add_argument(
        "--show-output",
        action="store_true",
        help="Pretty-print the last prices_output.json and exit.",
    )
    parser.add_argument(
        "--store",
        metavar="NAME",
        help="Filter output to a specific store name (used with --show-output).",
    )
    parser.add_argument(
        "--products",
        action="store_true",
        help="Include product listings in --show-output.",
    )
    parser.add_argument(
        "--raw",
        action="store_true",
        help="Output raw JSON (used with --show-output).",
    )

    args = parser.parse_args()

    if args.login:
        cmd_login(args)
    elif args.list_stores:
        cmd_list_stores(args)
    elif args.show_output:
        cmd_show_output(args)
    elif args.heal_only:
        args.selector_key = args.heal_only
        cmd_heal(args)
    else:
        cmd_scrape(args)


if __name__ == "__main__":
    main()
