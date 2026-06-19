"""
instacart_scraper.py

Playwright-based scraper for Instacart.
- Sets a hardcoded Lawrence, MA delivery address so Instacart shows local stores
- Scrapes available stores and their product prices
- Automatically invokes selector_healer.py when a selector stops working
- Saves results to prices_output.json (no Supabase, no existing DB touched)
"""

import asyncio
import json
import random
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from playwright.async_api import async_playwright, Page, BrowserContext, TimeoutError as PWTimeoutError

try:
    from playwright_stealth import Stealth as _PlaywrightStealth
    STEALTH_AVAILABLE = True
except ImportError:
    STEALTH_AVAILABLE = False
    print("[scraper] Warning: playwright-stealth not installed. Bot detection risk is higher.")

from scraper.selector_healer import heal_selector

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
SCRAPER_DIR = Path(__file__).parent
SELECTORS_PATH = SCRAPER_DIR / "selectors.json"
OUTPUT_PATH = SCRAPER_DIR / "prices_output.json"
SESSION_PATH = SCRAPER_DIR / "session.json"

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
INSTACART_URL = "https://www.instacart.com"

# Hardcoded Lawrence, MA address
DELIVERY_ADDRESS = "50 Island St, Lawrence, MA 01840"

# How many products to scrape per store (keep low to avoid bans during testing)
MAX_PRODUCTS_PER_STORE = 40

# Max stores to scrape in one run
MAX_STORES = 5

# Departments to visit per store — keeps scope tight
TARGET_DEPARTMENTS = ["produce", "dairy", "meat-seafood", "bakery", "frozen"]

# Delay ranges in seconds — randomized to mimic human behavior
DELAY_SHORT = (1.0, 2.5)
DELAY_MEDIUM = (2.5, 5.0)
DELAY_LONG = (5.0, 9.0)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_selectors() -> dict:
    with open(SELECTORS_PATH, "r") as f:
        return json.load(f)


async def _human_delay(short: bool = False, long: bool = False) -> None:
    """Sleep for a randomized human-like duration."""
    if short:
        lo, hi = DELAY_SHORT
    elif long:
        lo, hi = DELAY_LONG
    else:
        lo, hi = DELAY_MEDIUM
    await asyncio.sleep(random.uniform(lo, hi))


async def _dismiss_modals(page: Page, selectors: dict) -> None:
    """Dismiss any overlays (age verify, location prompts, cookie banners)."""
    close_sel = selectors.get("modal_close", "button[aria-label='Close']")
    age_sel = selectors.get("age_verify_button", "button:has-text('Yes, continue')")

    for sel in [age_sel, close_sel]:
        try:
            btn = page.locator(sel).first
            if await btn.is_visible(timeout=2000):
                await btn.click()
                await _human_delay(short=True)
        except Exception:
            pass


async def _try_select(page: Page, selector_key: str, selectors: dict, timeout: int = 8000):
    """
    Try to locate elements using the current selector for selector_key.
    If nothing is found, invoke the healer to get a new selector and retry once.
    Returns a Locator (may match zero elements — caller checks count).
    """
    selector = selectors[selector_key]
    locator = page.locator(selector)

    try:
        await locator.first.wait_for(state="attached", timeout=timeout)
        count = await locator.count()
        if count > 0:
            return locator
    except PWTimeoutError:
        pass

    # Selector found nothing — attempt healing
    print(f"[scraper] Selector '{selector_key}' matched nothing. Attempting self-heal...")
    html = await page.content()
    new_selector = heal_selector(selector_key, html)

    if new_selector:
        selectors[selector_key] = new_selector  # update in-memory copy too
        locator = page.locator(new_selector)
        try:
            await locator.first.wait_for(state="attached", timeout=timeout)
        except PWTimeoutError:
            pass

    return locator


async def _probe_card_selectors(page: Page, card_locator, selectors: dict, keys: list[str]) -> None:
    """
    Before looping over cards, check each sub-selector against the first card.
    If any find nothing, trigger healing so the full loop uses a working selector.
    Page HTML is fetched lazily — only if at least one selector needs healing.
    """
    if await card_locator.count() == 0:
        return

    first_card = card_locator.first
    html = None

    for key in keys:
        try:
            el = first_card.locator(selectors[key])
            if await el.count() == 0:
                print(f"[scraper] Sub-selector '{key}' matched nothing in card — attempting heal...")
                if html is None:
                    html = await page.content()
                new_sel = heal_selector(key, html)
                if new_sel:
                    selectors[key] = new_sel
        except Exception:
            pass


def _parse_price(raw: str) -> Optional[float]:
    """Extract a float price from strings like '$3.99', '3.99', '$1,299.00'."""
    if not raw:
        return None
    cleaned = re.sub(r"[^\d.]", "", raw.replace(",", ""))
    try:
        return float(cleaned)
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Core scraping logic
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


async def _get_store_list(page: Page, selectors: dict) -> list[dict]:
    """
    Return a list of stores available for the set delivery address.
    Each entry: {"name": str, "url": str}
    """
    stores = []

    # Navigate to the store picker
    await page.goto(f"{INSTACART_URL}/store", wait_until="domcontentloaded")
    await _human_delay(long=True)
    await _dismiss_modals(page, selectors)

    # Save page HTML for debugging selector issues
    debug_path = SCRAPER_DIR / "_debug_store.html"
    debug_path.write_text(await page.content())
    print(f"[scraper] Store page HTML saved to {debug_path} (current URL: {page.url})")

    store_locator = await _try_select(page, "store_card", selectors, timeout=15000)
    count = await store_locator.count()
    print(f"[scraper] Found {count} store cards.")

    await _probe_card_selectors(page, store_locator, selectors, ["store_name"])

    for i in range(min(count, MAX_STORES)):
        card = store_locator.nth(i)
        try:
            name_locator = card.locator(selectors["store_name"])
            name = (await name_locator.first.inner_text(timeout=3000)).strip()

            # Get the href of the store card or its first anchor
            href = await card.get_attribute("href")
            if not href:
                anchor = card.locator("a").first
                href = await anchor.get_attribute("href")

            if name and href:
                full_url = href if href.startswith("http") else f"{INSTACART_URL}{href}"
                stores.append({"name": name, "url": full_url})
                print(f"[scraper]   Store: {name}")
        except Exception as e:
            print(f"[scraper]   Could not parse store card {i}: {e}")

    return stores


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

    # Try to find department nav links
    dept_locator = await _try_select(page, "department_nav", selectors, timeout=8000)
    dept_count = await dept_locator.count()
    dept_urls = []

    for i in range(dept_count):
        link = dept_locator.nth(i)
        try:
            href = await link.get_attribute("href")
            text = (await link.inner_text()).strip().lower()
            if href and any(dep in text or dep in href for dep in TARGET_DEPARTMENTS):
                full = href if href.startswith("http") else f"{INSTACART_URL}{href}"
                if full not in dept_urls:
                    dept_urls.append(full)
        except Exception:
            pass

    # If no department links found, scrape the store's main page directly
    pages_to_scrape = dept_urls if dept_urls else [store_url]
    department_name = "general"

    for dept_url in pages_to_scrape:
        if len(products) >= MAX_PRODUCTS_PER_STORE:
            break

        if dept_url != store_url:
            # Derive department name from URL
            for dep in TARGET_DEPARTMENTS:
                if dep in dept_url:
                    department_name = dep
                    break
            print(f"[scraper]   Department: {department_name} ({dept_url})")
            await page.goto(dept_url, wait_until="domcontentloaded")
            await _human_delay(long=True)
            await _dismiss_modals(page, selectors)

        # Scrape product cards on this page
        product_locator = await _try_select(page, "product_card", selectors, timeout=10000)
        prod_count = await product_locator.count()
        print(f"[scraper]   Found {prod_count} product cards.")

        await _probe_card_selectors(page, product_locator, selectors, ["product_name", "product_price", "product_unit"])

        for i in range(prod_count):
            if len(products) >= MAX_PRODUCTS_PER_STORE:
                break

            card = product_locator.nth(i)
            try:
                name_el = card.locator(selectors["product_name"])
                price_el = card.locator(selectors["product_price"])
                unit_el = card.locator(selectors["product_unit"])

                name_raw = ""
                price_raw = ""
                unit_raw = ""

                if await name_el.count() > 0:
                    name_raw = (await name_el.first.inner_text()).strip()
                if await price_el.count() > 0:
                    price_raw = (await price_el.first.inner_text()).strip()
                if await unit_el.count() > 0:
                    unit_raw = (await unit_el.first.inner_text()).strip()

                parsed_price = _parse_price(price_raw)

                if name_raw and parsed_price is not None:
                    products.append({
                        "name": name_raw,
                        "price": parsed_price,
                        "price_raw": price_raw,
                        "unit": unit_raw or None,
                        "department": department_name,
                    })
            except Exception as e:
                print(f"[scraper]   Could not parse product card {i}: {e}")

        await _human_delay()

    print(f"[scraper]   Collected {len(products)} products from {store['name']}.")
    return products


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

async def run_scraper(verbose: bool = True) -> dict:
    """
    Run the full scrape: set address → get stores → scrape products → return result dict.
    Saves result to prices_output.json automatically.
    """
    result = {
        "scraped_at": datetime.now(timezone.utc).isoformat(),
        "location": DELIVERY_ADDRESS,
        "stores": [],
        "errors": [],
    }

    selectors = _load_selectors()

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=False,  # headful mode reduces bot detection
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-setuid-sandbox",
            ],
        )

        context_kwargs: dict = {
            "viewport": {"width": 1366, "height": 768},
            "user_agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            "locale": "en-US",
            "timezone_id": "America/New_York",
        }
        if SESSION_PATH.exists():
            context_kwargs["storage_state"] = str(SESSION_PATH)
            if verbose:
                print("[scraper] Loaded saved session (skipping address setup).")

        context: BrowserContext = await browser.new_context(**context_kwargs)

        page: Page = await context.new_page()

        # Apply stealth patches if available
        if STEALTH_AVAILABLE:
            await _PlaywrightStealth().apply_stealth_async(page)
            if verbose:
                print("[scraper] Stealth mode active.")

        try:
            if not SESSION_PATH.exists():
                address_ok = await _set_delivery_address(page, selectors)
                if not address_ok:
                    result["errors"].append("Failed to set delivery address — prices may be inaccurate.")

            stores = await _get_store_list(page, selectors)
            if not stores:
                result["errors"].append("No stores found for the given address.")
                print("[scraper] No stores found. Exiting.")
                return result

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

        finally:
            await browser.close()

    # Save output
    OUTPUT_PATH.write_text(json.dumps(result, indent=2, ensure_ascii=False))
    total_products = sum(s["product_count"] for s in result["stores"])
    print(f"\n[scraper] Done. {len(result['stores'])} stores, {total_products} products total.")
    print(f"[scraper] Output saved to: {OUTPUT_PATH}")

    return result
