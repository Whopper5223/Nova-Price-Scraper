"""
core.py

Shared foundation for all Nova Instacart scrapers.
- Browser lifecycle (headful + stealth + saved session)
- Selector loading and self-healing lookups (_try_select / _probe_card_selectors)
- Price parsing (handles Instacart's split-span cents rendering)
- Product card extraction via stable accessibility attributes
- Product search primitive with name-relevance filtering
- Store resolution by name (storefront list → Instacart search → direct URL guess)

Nothing here hardcodes Instacart page structure outside of selectors.json and the
accessibility attributes (img alt / screen-reader text), which are the most stable
hooks Instacart exposes.
"""

import asyncio
import json
import os
import random
import re
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from playwright.async_api import async_playwright, Page, TimeoutError as PWTimeoutError

try:
    from playwright_stealth import Stealth as _PlaywrightStealth
    STEALTH_AVAILABLE = True
except ImportError:
    STEALTH_AVAILABLE = False
    print("[scraper] Warning: playwright-stealth not installed. Bot detection risk is higher.")

from scraper.selector_healer import heal_selector

# ---------------------------------------------------------------------------
# Paths & constants
# ---------------------------------------------------------------------------
SCRAPER_DIR = Path(__file__).parent
SELECTORS_PATH = SCRAPER_DIR / "selectors.json"
SESSION_PATH = SCRAPER_DIR / "session.json"

INSTACART_URL = "https://www.instacart.com"

# Delay ranges in seconds — randomized to mimic human behavior
DELAY_SHORT = (1.0, 2.5)
DELAY_MEDIUM = (2.5, 5.0)
DELAY_LONG = (5.0, 9.0)

BROWSER_ARGS = [
    "--disable-blink-features=AutomationControlled",
    "--no-sandbox",
    "--disable-setuid-sandbox",
]

CONTEXT_KWARGS = {
    "viewport": {"width": 1366, "height": 768},
    "user_agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "locale": "en-US",
    "timezone_id": "America/New_York",
}

# Attach to a real Chrome you started yourself instead of launching one.
# Instacart blocks logins in Playwright-launched browsers no matter how much stealth
# is applied, but a Chrome the user launched and logged into by hand passes — so we
# connect to that over the DevTools protocol and drive the session it already has.
# Set CHROME_CDP_URL (or use --cdp) to enable; unset means launch normally.
CHROME_CDP_URL = os.environ.get("CHROME_CDP_URL", "").strip()
DEFAULT_CDP_URL = "http://localhost:9222"

# Which Chromium build to launch. Playwright's bundled build (channel unset) is a
# test binary that bot detection flags on sight; the "chromium" channel is a stable
# Chromium release with a far more ordinary fingerprint. Set BROWSER_CHANNEL to
# "chromium" (recommended), "chrome"/"msedge" to drive an installed browser, or
# leave empty for the bundled build.
BROWSER_CHANNEL = os.environ.get("BROWSER_CHANNEL", "chromium").strip()


# ---------------------------------------------------------------------------
# Browser lifecycle
# ---------------------------------------------------------------------------

@asynccontextmanager
async def launch_browser(verbose: bool = True):
    """
    Yield (browser, context, page) for scraping.

    Two modes:
      - CDP attach (CHROME_CDP_URL set): reuse a Chrome the user started and logged
        into themselves. Nothing is launched and the browser is left open on exit,
        because it's the user's window, not ours.
      - Launch (default): start a stealth Chromium with the saved session.json.
    """
    async with async_playwright() as pw:
        if CHROME_CDP_URL:
            if verbose:
                print(f"[scraper] Attaching to your Chrome at {CHROME_CDP_URL}...")
            try:
                browser = await pw.chromium.connect_over_cdp(CHROME_CDP_URL)
            except Exception as e:
                raise RuntimeError(
                    f"Could not attach to Chrome at {CHROME_CDP_URL}: {e}\n"
                    "Start Chrome with remote debugging first:\n"
                    '  chrome.exe --remote-debugging-port=9222 '
                    '--user-data-dir="%LOCALAPPDATA%\\nova-chrome-profile"\n'
                    "then log into Instacart in that window before re-running."
                ) from e

            # An attached Chrome already has a context (its normal profile) — reuse it
            # so we inherit the cookies the user logged in with. Creating a new context
            # here would start logged-out and defeat the whole point.
            context = browser.contexts[0] if browser.contexts else await browser.new_context()
            page = context.pages[0] if context.pages else await context.new_page()

            if verbose:
                print(f"[scraper] Attached. Using existing tab: {page.url}")

            try:
                yield browser, context, page
            finally:
                # Detach only — closing would kill the user's browser and their login.
                await browser.close()
            return

        launch_kwargs: dict = {"headless": False, "args": BROWSER_ARGS}
        if BROWSER_CHANNEL:
            launch_kwargs["channel"] = BROWSER_CHANNEL
        try:
            browser = await pw.chromium.launch(**launch_kwargs)
            if verbose and BROWSER_CHANNEL:
                print(f"[scraper] Browser channel: {BROWSER_CHANNEL}")
        except Exception as e:
            # A channel that isn't installed shouldn't be fatal — the bundled build
            # still works, it's just more detectable.
            if not BROWSER_CHANNEL:
                raise
            print(f"[scraper] Channel '{BROWSER_CHANNEL}' unavailable ({e}); using bundled Chromium.")
            browser = await pw.chromium.launch(headless=False, args=BROWSER_ARGS)

        ctx_kwargs = dict(CONTEXT_KWARGS)
        if SESSION_PATH.exists():
            ctx_kwargs["storage_state"] = str(SESSION_PATH)
            if verbose:
                print("[scraper] Loaded saved session.")

        context = await browser.new_context(**ctx_kwargs)
        page = await context.new_page()

        if STEALTH_AVAILABLE:
            await _PlaywrightStealth().apply_stealth_async(page)
            if verbose:
                print("[scraper] Stealth mode active.")

        try:
            yield browser, context, page
        finally:
            await browser.close()


# ---------------------------------------------------------------------------
# Basic helpers
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


async def _scroll_to_load_more(page: Page, selector: str, target: int, max_scrolls: int = 20) -> None:
    """Scroll down repeatedly until we have target elements or no new ones appear."""
    prev_count = 0
    stale_rounds = 0
    for _ in range(max_scrolls):
        current_count = await page.locator(selector).count()
        if current_count >= target:
            break
        if current_count == prev_count:
            stale_rounds += 1
            if stale_rounds >= 2:  # two scrolls with no new items → we've hit the bottom
                break
        else:
            stale_rounds = 0
        prev_count = current_count
        await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        await page.wait_for_timeout(1800)


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


# ---------------------------------------------------------------------------
# Price parsing & card extraction
# ---------------------------------------------------------------------------

def _parse_price(raw: str) -> Optional[float]:
    """Extract a float price from strings like '$3.99', '3.99', '$1,299.00'.
    Instacart renders $2.15 as separate spans ('$' + '2' + '15'), so inner_text()
    yields '$215'. When the raw string has no decimal point, the trailing two
    digits are cents — divide by 100.
    """
    if not raw:
        return None
    cleaned = re.sub(r"[^\d.]", "", raw.replace(",", ""))
    try:
        price = float(cleaned)
        if "." not in cleaned:
            price = price / 100
        return price
    except ValueError:
        return None


async def get_card_name(card, selectors: dict | None = None) -> str:
    """
    Extract product name from a product card.
    img[data-testid='item-card-image'] alt text is the most stable hook (accessibility
    attribute, survives CSS class churn). Falls back to the product_name selector.
    """
    img = card.locator("img[data-testid='item-card-image']")
    if await img.count() > 0:
        alt = await img.first.get_attribute("alt")
        if alt and alt.strip():
            return alt.strip()

    if selectors:
        name_el = card.locator(selectors["product_name"])
        if await name_el.count() > 0:
            text = (await name_el.first.inner_text()).strip()
            # Take the first meaningful line — full inner_text can be a blob of
            # price/rating text on some card layouts.
            for line in text.splitlines():
                line = line.strip()
                if line and not re.match(r"^(current price|original price|reg\.|\$|★)", line, re.I):
                    return line
    return ""


async def get_card_price(card, selectors: dict) -> Optional[float]:
    """
    Extract price from a product card.
    span.screen-reader-only contains 'Current price: $X.XX' (stable accessibility
    text with a real decimal). Falls back to the product_price selector.
    """
    sr = card.locator("span.screen-reader-only")
    if await sr.count() > 0:
        text = (await sr.first.inner_text()).strip()
        m = re.search(r"\$([\d.,]+)", text)
        if m:
            price = _parse_price(m.group(0))
            if price is not None:
                return price

    price_el = card.locator(selectors["product_price"])
    if await price_el.count() > 0:
        raw = (await price_el.first.inner_text()).strip()
        return _parse_price(raw)
    return None


# ---------------------------------------------------------------------------
# Product search primitive
# ---------------------------------------------------------------------------

def _matches_query(name: str, query: str) -> bool:
    """True if every word of the query appears in the product name (case-insensitive)."""
    name_lower = name.lower()
    return all(tok in name_lower for tok in query.lower().split())


_UNIT_PRICE_RE = re.compile(
    r"\$?\s*(\d+(?:\.\d+)?)\s*/\s*(fl oz|oz|lb|each|ct|count|g|kg|ml|l)\b", re.IGNORECASE
)


def _parse_unit_price(unit_text: str | None) -> tuple[Optional[float], Optional[str]]:
    """Parse '$0.12/oz' style unit-price text into (0.12, 'oz'). Returns (None, None) if absent."""
    if not unit_text:
        return None, None
    m = _UNIT_PRICE_RE.search(unit_text)
    if not m:
        return None, None
    value = float(m.group(1))
    if "." not in m.group(1):  # split-span cents rendering, same rule as _parse_price
        value = value / 100
    return value, m.group(2).lower()


def _median(prices: list[float]) -> float:
    ordered = sorted(prices)
    mid = len(ordered) // 2
    if len(ordered) % 2 == 1:
        return ordered[mid]
    return round((ordered[mid - 1] + ordered[mid]) / 2, 2)


async def search_products(
    page: Page,
    query: str,
    selectors: dict,
    n: int = 5,
    relevance_filter: bool = True,
    debug_slug: str = "",
) -> dict:
    """
    Type a query into the store's search box and return up to n matching products
    with a price range.

    Returns {"query", "min", "max", "median", "count", "products"} — products may
    be empty. Products are sorted cheapest-first and carry a parsed per-unit price
    ($/oz etc.) when Instacart displays one.
    With relevance_filter, only products whose name contains every query word are
    kept; if that filters out everything, falls back to the unfiltered top n so a
    successful search never returns nothing.
    """
    empty = {"query": query, "min": None, "max": None, "median": None, "count": 0, "products": []}

    try:
        search_locator = await _try_select(page, "search_input", selectors, timeout=8000)
        if await search_locator.count() == 0:
            print(f"[scraper]   Search input not found for '{query}'")
            return empty

        await search_locator.first.click()
        await search_locator.first.fill("")  # clear existing text
        await search_locator.first.type(query, delay=60)
        await page.keyboard.press("Enter")
        await _human_delay()
        await _dismiss_modals(page, selectors)

        product_locator = await _try_select(page, "product_card", selectors, timeout=10000)
        count = await product_locator.count()
        if count == 0:
            return empty

        # Save debug HTML once per store for manual healer runs
        if debug_slug:
            debug_path = SCRAPER_DIR / f"_debug_search_{debug_slug}.html"
            if not debug_path.exists():
                debug_path.write_text(await page.content())

        # Scan more cards than needed so the relevance filter can still fill n slots;
        # scroll to lazy-load extra results if the first paint didn't render enough.
        want = max(n * 3, n)
        if count < want:
            await _scroll_to_load_more(page, selectors["product_card"], target=want, max_scrolls=4)
            count = await product_locator.count()
        scan_limit = min(count, want)
        matched: list[dict] = []
        unmatched: list[dict] = []

        for i in range(scan_limit):
            if len(matched) >= n:
                break
            card = product_locator.nth(i)
            try:
                name = await get_card_name(card, selectors)
                price = await get_card_price(card, selectors)
                unit_el = card.locator(selectors["product_unit"])
                unit = (await unit_el.first.inner_text()).strip() if await unit_el.count() > 0 else ""

                if not name or price is None:
                    continue

                unit_price, unit_measure = _parse_unit_price(unit)
                product = {
                    "name": name,
                    "price": price,
                    "unit": unit or None,
                    "unit_price": unit_price,
                    "unit_measure": unit_measure,
                }
                if not relevance_filter or _matches_query(name, query):
                    matched.append(product)
                elif len(unmatched) < n:
                    unmatched.append(product)
            except Exception:
                pass

        results = matched if matched else unmatched[:n]
        if not matched and unmatched:
            print(f"[scraper]   No exact name matches for '{query}' — using top results as-is.")

        if not results:
            return empty

        results.sort(key=lambda p: p["price"])
        prices = [p["price"] for p in results]
        return {
            "query": query,
            "min": prices[0],
            "max": prices[-1],
            "median": _median(prices),
            "count": len(results),
            "products": results,
        }

    except Exception as e:
        print(f"[scraper]   Error searching '{query}': {e}")
        return empty


# ---------------------------------------------------------------------------
# Store discovery & resolution
# ---------------------------------------------------------------------------

async def _get_store_list(page: Page, selectors: dict, verbose: bool = True) -> list[dict]:
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
    if verbose:
        print(f"[scraper] Store page HTML saved to {debug_path} (current URL: {page.url})")

    store_locator = await _try_select(page, "store_card", selectors, timeout=15000)
    # Scroll to reveal any stores below the fold
    await _scroll_to_load_more(page, selectors["store_card"], target=50, max_scrolls=8)
    count = await store_locator.count()
    if verbose:
        print(f"[scraper] Found {count} store cards.")

    await _probe_card_selectors(page, store_locator, selectors, ["store_name"])

    for i in range(count):
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
                if verbose:
                    print(f"[scraper]   Store: {name}")
        except Exception as e:
            if verbose:
                print(f"[scraper]   Could not parse store card {i}: {e}")

    return stores


def _slugify(name: str) -> str:
    """'Stop & Shop' -> 'stop-shop' — matches Instacart's store URL slugs."""
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return re.sub(r"-+", "-", slug)


def _name_matches(candidate: str, wanted: str) -> bool:
    """Loose match: every word of the wanted name appears in the candidate."""
    cand = re.sub(r"[^a-z0-9\s]", "", candidate.lower())
    return all(tok in cand for tok in re.sub(r"[^a-z0-9\s]", "", wanted.lower()).split())


async def resolve_store(
    page: Page,
    name: str,
    selectors: dict,
    store_list: list[dict] | None = None,
    verbose: bool = True,
) -> Optional[dict]:
    """
    Find a store by name, trying progressively harder:
      1. The storefront list (pass store_list to skip re-fetching it)
      2. Instacart's search bar — store links in the results
      3. Direct URL guess from the slugified name
    Returns {"name", "url"} or None if the store isn't available for this address.
    """
    # --- Stage 1: storefront list -----------------------------------------
    if store_list is None:
        store_list = await _get_store_list(page, selectors, verbose=False)
    for s in store_list:
        if name.lower() in s["name"].lower() or _name_matches(s["name"], name):
            return s

    if verbose:
        print(f"[scraper] '{name}' not on the storefront list — trying Instacart search...")

    # --- Stage 2: search bar ----------------------------------------------
    try:
        search_locator = await _try_select(page, "search_input", selectors, timeout=8000)
        if await search_locator.count() > 0:
            await search_locator.first.click()
            await search_locator.first.fill("")
            await search_locator.first.type(name, delay=60)
            await page.keyboard.press("Enter")
            await _human_delay()
            await _dismiss_modals(page, selectors)

            result_locator = await _try_select(page, "store_search_result", selectors, timeout=6000)
            result_count = await result_locator.count()
            slug = _slugify(name)
            for i in range(min(result_count, 20)):
                link = result_locator.nth(i)
                try:
                    href = await link.get_attribute("href") or ""
                    text = (await link.inner_text()).strip()
                    href_slug = href.split("/store/")[-1].split("/")[0] if "/store/" in href else ""
                    if _name_matches(text, name) or (href_slug and href_slug in (slug, slug.replace("-", ""))):
                        full_url = href if href.startswith("http") else f"{INSTACART_URL}{href}"
                        return {"name": text or name, "url": full_url}
                except Exception:
                    pass
    except Exception as e:
        if verbose:
            print(f"[scraper] Store search failed: {e}")

    if verbose:
        print(f"[scraper] '{name}' not found via search — trying direct URL...")

    # --- Stage 3: direct URL guess ------------------------------------------
    guess_url = f"{INSTACART_URL}/store/{_slugify(name)}/storefront"
    try:
        await page.goto(guess_url, wait_until="domcontentloaded")
        await _human_delay()
        await _dismiss_modals(page, selectors)

        # A real storefront keeps the slug in the URL and shows products/departments.
        if "/storefront" in page.url and _slugify(name).split("-")[0] in page.url:
            product_locator = page.locator(selectors["product_card"])
            dept_locator = page.locator(selectors["department_nav"])
            try:
                await product_locator.first.wait_for(state="attached", timeout=8000)
                has_content = True
            except PWTimeoutError:
                has_content = await dept_locator.count() > 0
            if has_content:
                return {"name": name, "url": guess_url}
    except Exception as e:
        if verbose:
            print(f"[scraper] Direct URL check failed: {e}")

    if verbose:
        print(f"[scraper] Store '{name}' is not available for this delivery address.")
    return None
