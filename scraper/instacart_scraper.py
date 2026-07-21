"""
instacart_scraper.py

Full-catalog Instacart scraper.
- Sets the delivery address (DELIVERY_ADDRESS env var / --address flag) so
  Instacart shows local stores
- Scrapes every department a store exposes (narrow with --departments)
- Automatically invokes selector_healer.py when a selector stops working
- Saves results to prices_output.json incrementally (per store)

Shared browser/selector/parsing logic lives in scraper/core.py.
"""

import json
import os
import random
import re
from datetime import datetime, timezone

from playwright.async_api import Page, TimeoutError as PWTimeoutError

from scraper.core import (
    launch_browser,
    _load_selectors,
    _human_delay,
    _dismiss_modals,
    _scroll_to_load_more,
    _try_select,
    _probe_card_selectors,
    _parse_price,
    _get_store_list,
    get_card_name,
    get_card_price,
    SCRAPER_DIR,
    SELECTORS_PATH,
    SESSION_PATH,
    INSTACART_URL,
    STEALTH_AVAILABLE,
)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
OUTPUT_PATH = SCRAPER_DIR / "prices_output.json"  # overridden by --output flag at runtime

# Delivery address: DELIVERY_ADDRESS env var (or .env) — override per run with --address
DELIVERY_ADDRESS = os.environ.get("DELIVERY_ADDRESS", "50 Island St, Lawrence, MA 01840")

# Cap per department (0 = unlimited). Keeps one giant department from eating the run.
MAX_PRODUCTS_PER_CATEGORY = 150

# Total cap across a whole store (0 = unlimited; the per-category cap still applies)
MAX_PRODUCTS_PER_STORE = 0

# Max stores to scrape in one run (0 = every store found)
MAX_STORES = 5

# Departments to visit per store. None = every department the store's nav exposes.
# Narrow with --departments, e.g. ["produce", "dairy"].
TARGET_DEPARTMENTS: list[str] | None = None


# ---------------------------------------------------------------------------
# Address setup
# ---------------------------------------------------------------------------

async def _reveal_address_input(page: Page) -> None:
    """
    Instacart's address <input> starts inside a display:none container on the
    homepage. Click whatever visible trigger reveals it before we try to type.
    """
    trigger_selectors = [
        "button:has-text('Enter your address')",
        "button:has-text('Get started')",
        "button:has-text('Start shopping')",
        "button:has-text('Deliver to')",
        "[role='button']:has-text('address')",
        "[class*='hero'] button",
        "[class*='Hero'] button",
        "[class*='Landing'] button",
    ]
    for sel in trigger_selectors:
        try:
            el = page.locator(sel).first
            if await el.is_visible(timeout=1500):
                await el.click()
                await _human_delay(short=True)
                return
        except Exception:
            pass


async def _set_delivery_address(page: Page, selectors: dict) -> bool:
    """
    Navigate to Instacart and set the delivery address to Lawrence, MA.
    Returns True if the address was set successfully.
    """
    print(f"[scraper] Setting delivery address: {DELIVERY_ADDRESS}")

    await page.goto(INSTACART_URL, wait_until="domcontentloaded")
    await _human_delay()
    await _dismiss_modals(page, selectors)

    # The address <input> is inside a hidden container until a trigger is clicked.
    await _reveal_address_input(page)
    await _human_delay(short=True)

    # Find and fill the address input
    try:
        address_locator = await _try_select(page, "address_input", selectors, timeout=12000)

        # Scroll into view then try a normal click; fall back to force if still hidden
        try:
            await address_locator.first.scroll_into_view_if_needed(timeout=3000)
            await address_locator.first.click(timeout=5000)
        except PWTimeoutError:
            print("[scraper] Address input not visible — trying force click")
            await address_locator.first.click(force=True)

        await _human_delay(short=True)

        # Type address character by character to look more human
        await address_locator.first.type(DELIVERY_ADDRESS, delay=random.randint(40, 120))
        await _human_delay()

        # Click the first autocomplete suggestion
        suggestion_locator = await _try_select(page, "address_suggestion", selectors, timeout=8000)
        count = await suggestion_locator.count()
        if count == 0:
            print("[scraper] No address suggestions appeared — address setting may have failed.")
            return False

        await suggestion_locator.first.click()
        await _human_delay()

        # Confirm address if a confirm button appears
        confirm_locator = page.locator(selectors.get("confirm_address_button", "button"))
        try:
            await confirm_locator.first.wait_for(state="visible", timeout=5000)
            await confirm_locator.first.click()
            await _human_delay()
        except PWTimeoutError:
            pass  # confirm button not always present

        print("[scraper] Delivery address set.")
        return True

    except Exception as e:
        print(f"[scraper] Failed to set address: {e}")
        return False


# ---------------------------------------------------------------------------
# Product scraping
# ---------------------------------------------------------------------------

async def _scrape_store_products(page: Page, store: dict, selectors: dict) -> list[dict]:
    """
    Visit a store and scrape products from target departments.
    Returns a list of product dicts: {name, price, unit, department}
    """
    products = []
    store_url = store["url"]

    print(f"[scraper] Scraping store: {store['name']} ({store_url})")
    await page.goto(store_url, wait_until="domcontentloaded")
    await _human_delay(long=True)
    await _dismiss_modals(page, selectors)

    # Save store page HTML for debugging product selectors
    store_slug = store_url.rstrip("/").split("/")[-2]
    debug_product_path = SCRAPER_DIR / f"_debug_products_{store_slug}.html"
    debug_product_path.write_text(await page.content())
    print(f"[scraper]   Product page HTML saved to {debug_product_path}")

    # Collect department nav links — all of them unless TARGET_DEPARTMENTS narrows the run
    dept_locator = await _try_select(page, "department_nav", selectors, timeout=8000)
    dept_count = await dept_locator.count()
    dept_urls = []

    for i in range(dept_count):
        link = dept_locator.nth(i)
        try:
            href = await link.get_attribute("href")
            text = (await link.inner_text()).strip().lower()
            if not href:
                continue
            if TARGET_DEPARTMENTS and not any(dep in text or dep in href for dep in TARGET_DEPARTMENTS):
                continue
            full = href if href.startswith("http") else f"{INSTACART_URL}{href}"
            if full not in dept_urls:
                dept_urls.append(full)
        except Exception:
            pass

    if dept_urls:
        print(f"[scraper]   {len(dept_urls)} departments to scrape.")

    # If no department links found, scrape the store's main page directly
    pages_to_scrape = dept_urls if dept_urls else [store_url]
    department_name = "general"
    seen_names: set[str] = set()  # same product often appears in several departments

    def _store_cap_reached() -> bool:
        return MAX_PRODUCTS_PER_STORE > 0 and len(products) >= MAX_PRODUCTS_PER_STORE

    for dept_url in pages_to_scrape:
        if _store_cap_reached():
            break

        if dept_url != store_url:
            # Department name from the URL slug, minus any numeric ID prefix
            slug = dept_url.rstrip("/").split("/")[-1]
            department_name = re.sub(r"^\d+-", "", slug)
            print(f"[scraper]   Department: {department_name} ({dept_url})")
            await page.goto(dept_url, wait_until="domcontentloaded")
            await _human_delay(long=True)
            await _dismiss_modals(page, selectors)

        # Scrape product cards on this page — scroll to lazy-load more before counting
        product_locator = await _try_select(page, "product_card", selectors, timeout=10000)
        limit = MAX_PRODUCTS_PER_CATEGORY if MAX_PRODUCTS_PER_CATEGORY > 0 else 10_000
        if MAX_PRODUCTS_PER_STORE > 0:
            limit = min(limit, MAX_PRODUCTS_PER_STORE - len(products))
        await _scroll_to_load_more(page, selectors["product_card"], target=limit)
        prod_count = await product_locator.count()
        print(f"[scraper]   Found {prod_count} product cards.")

        await _probe_card_selectors(page, product_locator, selectors, ["product_name", "product_price", "product_unit"])

        dept_collected = 0

        for i in range(prod_count):
            if _store_cap_reached():
                break
            if MAX_PRODUCTS_PER_CATEGORY > 0 and dept_collected >= MAX_PRODUCTS_PER_CATEGORY:
                break

            card = product_locator.nth(i)
            try:
                name = await get_card_name(card, selectors)
                price = await get_card_price(card, selectors)

                unit_el = card.locator(selectors["product_unit"])
                unit_raw = (await unit_el.first.inner_text()).strip() if await unit_el.count() > 0 else ""

                if name and price is not None:
                    if name in seen_names:
                        continue
                    seen_names.add(name)
                    products.append({
                        "name": name,
                        "price": price,
                        "unit": unit_raw or None,
                        "department": department_name,
                    })
                    dept_collected += 1
            except Exception as e:
                print(f"[scraper]   Could not parse product card {i}: {e}")

        print(f"[scraper]   Collected {dept_collected} products from {department_name}.")
        await _human_delay()

    print(f"[scraper]   Collected {len(products)} products from {store['name']}.")
    return products


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

async def run_scraper(verbose: bool = True, store_filter: list[str] | None = None) -> dict:
    """
    Run the full scrape: set address → get stores → scrape products → return result dict.
    Saves result to OUTPUT_PATH incrementally after each store (crash-safe).
    """
    result = {
        "scraped_at": datetime.now(timezone.utc).isoformat(),
        "location": DELIVERY_ADDRESS,
        "stores": [],
        "errors": [],
    }

    selectors = _load_selectors()

    def _save_partial():
        OUTPUT_PATH.write_text(json.dumps(result, indent=2, ensure_ascii=False))

    async with launch_browser(verbose=verbose) as (browser, context, page):
        if not SESSION_PATH.exists():
            address_ok = await _set_delivery_address(page, selectors)
            if not address_ok:
                result["errors"].append("Failed to set delivery address — prices may be inaccurate.")

        stores = await _get_store_list(page, selectors, verbose=verbose)
        if not stores:
            result["errors"].append("No stores found for the given address.")
            print("[scraper] No stores found. Exiting.")
            return result

        if store_filter:
            stores = [s for s in stores if any(f.lower() in s["name"].lower() for f in store_filter)]
            if not stores:
                print(f"[scraper] No stores matched filter: {store_filter}")
                return result

        if MAX_STORES > 0:
            stores = stores[:MAX_STORES]

        for store in stores:
            try:
                products = await _scrape_store_products(page, store, selectors)
                result["stores"].append({
                    "name": store["name"],
                    "url": store["url"],
                    "product_count": len(products),
                    "products": products,
                })
            except Exception as e:
                msg = f"Error scraping {store['name']}: {e}"
                print(f"[scraper] {msg}")
                result["errors"].append(msg)
                result["stores"].append({
                    "name": store["name"],
                    "url": store["url"],
                    "product_count": 0,
                    "products": [],
                    "error": str(e),
                })
            _save_partial()  # crash-safe: keep what we have so far

    _save_partial()
    total_products = sum(s["product_count"] for s in result["stores"])
    print(f"\n[scraper] Done. {len(result['stores'])} stores, {total_products} products total.")
    print(f"[scraper] Output saved to: {OUTPUT_PATH}")

    return result
