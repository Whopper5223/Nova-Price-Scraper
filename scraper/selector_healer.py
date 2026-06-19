"""
selector_healer.py

When a CSS selector stops working against Instacart's HTML, this module sends
the broken HTML to Gemini and asks it to produce a replacement selector.
The new selector is validated locally before being saved back to selectors.json.
"""

import json
import os
import re
import time
from pathlib import Path
from typing import Optional

from google import genai
from bs4 import BeautifulSoup

SELECTORS_PATH = Path(__file__).parent / "selectors.json"

# Selector keys that the healer is allowed to update (prevents accidental overwrites)
HEALABLE_KEYS = {
    "address_input",
    "address_suggestion",
    "confirm_address_button",
    "store_card",
    "store_name",
    "product_grid",
    "product_card",
    "product_name",
    "product_price",
    "product_unit",
    "load_more_button",
    "department_nav",
    "age_verify_button",
    "modal_close",
}

# Human-readable descriptions for each selector — sent to Gemini so it understands intent
SELECTOR_DESCRIPTIONS = {
    "address_input": "the text input field where a delivery address is typed",
    "address_suggestion": "the first autocomplete suggestion that appears after typing an address",
    "confirm_address_button": "the button that confirms and sets the delivery address",
    "store_card": "a card/tile representing one grocery store in the store listing",
    "store_name": "the element containing the grocery store NAME TEXT inside a store card — must be a text-containing element like span, div, h2, p (NOT an img, svg, or icon)",
    "product_grid": "the container element holding the grid of product cards",
    "product_card": "an individual product card/tile in the product grid",
    "product_name": "the element containing the product NAME TEXT inside a product card — must be a text-containing element like h2, span, div, p (NOT an img, svg, or icon)",
    "product_price": "the element containing the PRICE TEXT (e.g. '$3.99') inside a product card — must be a text-containing element (NOT an img)",
    "product_unit": "the element containing the UNIT PRICE TEXT (e.g. '$1.20/lb') inside a product card — must be a text-containing element (NOT an img)",
    "load_more_button": "the button to load more products onto the page",
    "department_nav": "navigation links to grocery departments (produce, dairy, etc.)",
    "age_verify_button": "the button to confirm age on an age verification modal",
    "modal_close": "a generic modal close/dismiss button",
}

# Hint strings to locate the relevant section in the HTML for each selector key.
# The healer searches for these strings and extracts a window of HTML around the first match.
_SELECTOR_HINTS = {
    "address_input":          ["Enter your address", "search-bar-input", "aria-label=\"address\"", "name=\"address\""],
    "address_suggestion":     ["suggestion-item", "address-suggestion", "role=\"listbox\""],
    "confirm_address_button": ["confirm-address", "Start shopping", "Set address"],
    "store_card":             ["storefront", "store-card", "StoreCard"],
    "store_name":             ["storefront", "StoreCard", "store-card"],
    "product_grid":           ["product-grid", "ProductGrid", "ProductList"],
    "product_card":           ["item-card", "ItemCard", "data-item-card", "product-card"],
    "product_name":           ["item-card", "ItemCard", "data-item-card"],
    "product_price":          ["screen-reader-only", "item-card", "price"],
    "product_unit":           ["e-174pdgy", "unit-price", "per lb", "item-card"],
    "load_more_button":       ["load-more", "Load more", "Show more"],
    "department_nav":         ["department-link", "DepartmentNav", "/departments/"],
    "age_verify_button":      ["age-verify", "I am 21", "Yes, continue"],
    "modal_close":            ["modal-close", "aria-label=\"Close\"", "CloseButton"],
}

# Max characters to send to Gemini per heal request (~950K leaves headroom under 1M token limit)
_MAX_HTML_CHARS = 950_000

# Characters before the first hint match to include as context
_CONTEXT_BEFORE = 20_000


def _extract_relevant_html(html: str, selector_key: str) -> str:
    """
    Find where the relevant elements actually live in the HTML and extract a window
    around that region, rather than blindly taking the first N characters.

    Instacart pages are ~2.3 MB. The product/store elements are deep in the body,
    not at the top, so [:12000] would only ever show navigation and never the elements
    the healer needs to inspect.
    """
    if len(html) <= _MAX_HTML_CHARS:
        return html

    hints = _SELECTOR_HINTS.get(selector_key, [])
    best_pos = None

    for hint in hints:
        idx = html.find(hint)
        if idx != -1:
            if best_pos is None or idx < best_pos:
                best_pos = idx

    if best_pos is None:
        return html[:_MAX_HTML_CHARS]

    start = max(0, best_pos - _CONTEXT_BEFORE)
    end = start + _MAX_HTML_CHARS
    return html[start:end]


def _load_selectors() -> dict:
    with open(SELECTORS_PATH, "r") as f:
        return json.load(f)


def _save_selectors(selectors: dict) -> None:
    with open(SELECTORS_PATH, "w") as f:
        json.dump(selectors, f, indent=2)


def _validate_selector(selector: str, html: str) -> bool:
    """Return True if the selector matches at least one element in the given HTML."""
    try:
        soup = BeautifulSoup(html, "html.parser")
        results = soup.select(selector)
        return len(results) > 0
    except Exception:
        return False


def _simplify_chained_selector(selector: str) -> Optional[str]:
    """
    If Gemini returns a child-combinator chain like `div.a > span.b > a.c` that
    fails validation, extract just the last segment (`a.c`) and try that.
    Instacart's obfuscated class names mean the chain is often wrong, but the
    leaf element selector is usually correct.
    """
    parts = [p.strip() for p in re.split(r"\s*>\s*", selector)]
    if len(parts) > 1:
        return parts[-1]
    return None


def _extract_selector_from_response(response_text: str) -> Optional[str]:
    """
    Gemini is prompted to return only the selector, but sometimes wraps it in
    markdown or adds explanation. This strips all that out.
    """
    # Strip markdown code fences
    code_fence = re.search(r"```(?:css)?\s*(.*?)\s*```", response_text, re.DOTALL)
    if code_fence:
        return code_fence.group(1).strip()

    # Strip inline backticks
    backtick = re.search(r"`([^`]+)`", response_text)
    if backtick:
        return backtick.group(1).strip()

    # Take the first non-empty line that looks like a CSS selector
    for line in response_text.strip().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and not line.startswith("//"):
            if ":" in line and not line[0].isalpha():
                return line
            if any(c in line for c in [".", "#", "[", ">", "~", "+"]):
                return line
            if re.match(r"^[a-z][a-z0-9]*$", line):
                return line  # bare tag name like "div"

    return None


def heal_selector(selector_key: str, broken_html: str, verbose: bool = True) -> Optional[str]:
    """
    Ask Gemini to produce a new CSS selector for selector_key given the current HTML.

    Returns the new selector string if healing succeeded, None if it failed.
    Updates selectors.json on success.
    """
    if selector_key not in HEALABLE_KEYS:
        print(f"[healer] '{selector_key}' is not a healable key — skipping.")
        return None

    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        print("[healer] GEMINI_API_KEY not set — cannot heal selectors.")
        return None

    description = SELECTOR_DESCRIPTIONS.get(selector_key, selector_key)
    selectors = _load_selectors()
    old_selector = selectors.get(selector_key, "(none)")

    if verbose:
        print(f"[healer] Selector '{selector_key}' failed. Asking Gemini for a replacement...")
        print(f"[healer] Old selector: {old_selector}")

    html_chunk = _extract_relevant_html(broken_html, selector_key)
    if verbose:
        print(f"[healer] Sending {len(html_chunk):,} of {len(broken_html):,} HTML chars to Gemini.")

    prompt = f"""You are a web scraping expert. The following HTML is from the Instacart website.

I need a CSS selector that targets: **{description}**

The previous selector that stopped working was: `{old_selector}`

Here is the current HTML:
```html
{html_chunk}
```

Requirements for your response:
- Return ONLY the CSS selector string, nothing else
- No explanation, no markdown, no backticks — just the raw selector
- The selector must work with Python's BeautifulSoup `soup.select()` method
- Prefer in this order: (1) data-testid or other data-* attributes, (2) href/src/type/name attributes like `a[href*='storefront']`, (3) a single tag + class combo
- AVOID deeply-nested child-combinator chains like `div.e-xxx > span.e-yyy > a.e-zzz` — Instacart's class names are obfuscated and change on every deploy
- The target element is the OUTERMOST wrapper, not a deeply nested child — return the selector for the top-level element that represents the item
- If multiple selectors would work, join them with a comma (e.g. `a, b, c`) for resilience
- Do not use :has-text() — BeautifulSoup does not support it"""

    client = genai.Client(api_key=api_key)
    raw_response = None

    for attempt in range(2):
        try:
            response = client.models.generate_content(
                model="gemini-2.5-flash-lite",
                contents=prompt,
            )
            raw_response = response.text.strip()
            break
        except Exception as e:
            err_str = str(e)
            if attempt == 0 and ("429" in err_str or "RESOURCE_EXHAUSTED" in err_str):
                # Parse the retry delay the API suggests, default to 65s
                match = re.search(r"retryDelay.*?(\d+)s", err_str)
                delay = int(match.group(1)) + 5 if match else 65
                print(f"[healer] Rate limited — waiting {delay}s before retry...")
                time.sleep(delay)
            else:
                print(f"[healer] Gemini API error: {e}")
                return None

    if raw_response is None:
        print("[healer] Gemini did not return a response after retry.")
        return None

    if verbose:
        print(f"[healer] Gemini responded: {raw_response!r}")

    new_selector = _extract_selector_from_response(raw_response)
    if not new_selector:
        print("[healer] Could not extract a valid selector from Gemini's response.")
        return None

    # Validate against the chunk Gemini saw — it generated the selector from that HTML,
    # so that's the right surface to check. Validating against the full 1.6MB page with
    # BeautifulSoup is unreliable for deeply-nested selectors.
    if not _validate_selector(new_selector, html_chunk):
        # Gemini sometimes returns a reversed child chain. Try just the leaf segment.
        simplified = _simplify_chained_selector(new_selector)
        if simplified and _validate_selector(simplified, html_chunk):
            if verbose:
                print(f"[healer] Chain didn't match — simplified to leaf: {simplified}")
            new_selector = simplified
        else:
            print(f"[healer] New selector '{new_selector}' did not match anything in the HTML — discarding.")
            return None

    # Save back to selectors.json
    selectors[selector_key] = new_selector
    _save_selectors(selectors)

    if verbose:
        print(f"[healer] Healed! New selector saved: {new_selector}")

    return new_selector
