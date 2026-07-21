# Graph Report - .  (2026-07-13)

## Corpus Check
- 5 files · ~10,463 words
- Verdict: corpus is large enough that graph structure adds value.

## Summary
- 147 nodes · 248 edges · 11 communities
- Extraction: 94% EXTRACTED · 6% INFERRED · 0% AMBIGUOUS · INFERRED: 16 edges (avg confidence: 0.83)
- Token cost: 32,298 input · 0 output

## Community Hubs (Navigation)
- Recipe Ingredient Extraction
- Project Overview & Dependencies
- Scraper CLI Command Dispatch
- Selector Self-Healing
- Browser/Selector Core Utilities
- Instacart Browser Automation
- Product Card Parsing & Search
- Store & Card Selector Probing
- Human-like Delay & Modal Handling
- Product Search CLI

## God Nodes (most connected - your core abstractions)
1. `search_products()` - 12 edges
2. `Nova-Price-Scraper README` - 12 edges
3. `heal_selector()` - 11 edges
4. `_get_store_list()` - 9 edges
5. `resolve_store()` - 9 edges
6. `cmd_scrape()` - 8 edges
7. `scraper/recipe_scraper.py (CLI: recipe URL to ingredient price comparison)` - 8 edges
8. `_dismiss_modals()` - 7 edges
9. `_try_select()` - 7 edges
10. `_parse_recipes()` - 7 edges

## Surprising Connections (you probably didn't know these)
- `graphify (project knowledge graph)` --conceptually_related_to--> `Nova-Price-Scraper README`  [INFERRED]
  CLAUDE.md → README.md
- `Gemini API selector self-healing` --shares_data_with--> `google-genai (Gemini API client)`  [INFERRED]
  README.md → scraper/requirements-scraper.txt
- `Nova-Price-Scraper README` --references--> `beautifulsoup4 (HTML parsing)`  [EXTRACTED]
  README.md → scraper/requirements-scraper.txt
- `Nova-Price-Scraper README` --references--> `scraper/requirements-scraper.txt (Python dependencies)`  [EXTRACTED]
  README.md → scraper/requirements-scraper.txt
- `Nova-Price-Scraper README` --references--> `Playwright (browser automation)`  [EXTRACTED]
  README.md → scraper/requirements-scraper.txt

## Import Cycles
- None detected.

## Hyperedges (group relationships)
- **Three CLI tools built on one shared core** — scraper_run_scraper_py, scraper_product_search_py, scraper_recipe_scraper_py, scraper_core_py [EXTRACTED 1.00]
- **Gemini selector self-healing flow** — scraper_selector_healer_py, scraper_selectors_json, concept_gemini_api_self_healing, scraper_run_scraper_py [EXTRACTED 1.00]
- **3-stage store lookup shared by search and recipe tools** — scraper_core_py, scraper_product_search_py, scraper_recipe_scraper_py, concept_store_lookup_3_stage [EXTRACTED 1.00]
- **Scraper Python dependencies** — concept_playwright, concept_playwright_stealth, concept_google_genai, concept_beautifulsoup4, concept_requests_lib [EXTRACTED 1.00]

## Communities (11 total, 0 thin omitted)

### Community 0 - "Recipe Ingredient Extraction"
Cohesion: 0.09
Nodes (33): _extract_recipes(), _fetch_html_requests(), fetch_recipes_async(), fetch_recipes_sync(), _gemini_extract_ingredients(), _gemini_generate(), _gemini_normalize(), list_stores() (+25 more)

### Community 1 - "Project Overview & Dependencies"
Cohesion: 0.14
Nodes (25): graphify (project knowledge graph), beautifulsoup4 (HTML parsing), Env-driven delivery address (DELIVERY_ADDRESS / --address), Full-department crawl with product caps, Gemini API selector self-healing, google-genai (Gemini API client), Multi-recipe page selection (--recipe flag), Playwright (browser automation) (+17 more)

### Community 2 - "Scraper CLI Command Dispatch"
Cohesion: 0.21
Nodes (18): Namespace, Path, _check_env(), cmd_heal(), cmd_list_stores(), cmd_login(), cmd_scrape(), cmd_show_output() (+10 more)

### Community 3 - "Selector Self-Healing"
Cohesion: 0.21
Nodes (13): _extract_relevant_html(), _extract_selector_from_response(), heal_selector(), _load_selectors(), selector_healer.py  When a CSS selector stops working against Instacart's HTML,, Return True if the selector matches at least one element in the given HTML., If Gemini returns a child-combinator chain like `div.a > span.b > a.c` that, Gemini is prompted to return only the selector, but sometimes wraps it in     ma (+5 more)

### Community 4 - "Browser/Selector Core Utilities"
Cohesion: 0.20
Nodes (9): get_card_price(), launch_browser(), _name_matches(), _parse_price(), core.py  Shared foundation for all Nova Instacart scrapers. - Browser lifecycle, Extract a float price from strings like '$3.99', '3.99', '$1,299.00'.     Instac, Extract price from a product card.     span.screen-reader-only contains 'Current, Loose match: every word of the wanted name appears in the candidate. (+1 more)

### Community 5 - "Instacart Browser Automation"
Cohesion: 0.27
Nodes (10): Page, instacart_scraper.py  Full-catalog Instacart scraper. - Sets the delivery addres, Visit a store and scrape products from target departments.     Returns a list of, Run the full scrape: set address → get stores → scrape products → return result, Instacart's address <input> starts inside a display:none container on the     ho, Navigate to Instacart and set the delivery address to Lawrence, MA.     Returns, _reveal_address_input(), run_scraper() (+2 more)

### Community 6 - "Product Card Parsing & Search"
Cohesion: 0.22
Nodes (9): get_card_name(), _matches_query(), _median(), _parse_unit_price(), Extract product name from a product card.     img[data-testid='item-card-image'], True if every word of the query appears in the product name (case-insensitive)., Parse '$0.12/oz' style unit-price text into (0.12, 'oz'). Returns (None, None) i, Type a query into the store's search box and return up to n matching products (+1 more)

### Community 7 - "Store & Card Selector Probing"
Cohesion: 0.31
Nodes (9): _get_store_list(), _probe_card_selectors(), Page, Scroll down repeatedly until we have target elements or no new ones appear., Try to locate elements using the current selector for selector_key.     If nothi, Before looping over cards, check each sub-selector against the first card.     I, Return a list of stores available for the set delivery address.     Each entry:, _scroll_to_load_more() (+1 more)

### Community 8 - "Human-like Delay & Modal Handling"
Cohesion: 0.29
Nodes (8): _dismiss_modals(), _human_delay(), Sleep for a randomized human-like duration., Dismiss any overlays (age verify, location prompts, cookie banners)., Stop & Shop' -> 'stop-shop' — matches Instacart's store URL slugs., Find a store by name, trying progressively harder:       1. The storefront list, resolve_store(), _slugify()

### Community 9 - "Product Search CLI"
Cohesion: 0.47
Nodes (5): main(), _print_comparison(), product_search.py  Search Instacart stores for specific products by name and com, For each store: resolve it by name (or take the top storefront stores), then, run_product_search()

## Knowledge Gaps
- **4 isolated node(s):** `graphify (project knowledge graph)`, `requests (HTTP client library)`, `scraper/product_search_output.json (product_search results)`, `scraper/recipe_output.json (recipe_scraper results)`
  These have ≤1 connection - possible missing edges or undocumented components.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `heal_selector()` connect `Selector Self-Healing` to `Browser/Selector Core Utilities`, `Store & Card Selector Probing`?**
  _High betweenness centrality (0.059) - this node is a cross-community bridge._
- **Why does `search_products()` connect `Product Card Parsing & Search` to `Human-like Delay & Modal Handling`, `Browser/Selector Core Utilities`, `Store & Card Selector Probing`?**
  _High betweenness centrality (0.019) - this node is a cross-community bridge._
- **What connects `graphify (project knowledge graph)`, `requests (HTTP client library)`, `scraper/product_search_output.json (product_search results)` to the rest of the system?**
  _4 weakly-connected nodes found - possible documentation gaps or missing edges._
- **Should `Recipe Ingredient Extraction` be split into smaller, more focused modules?**
  _Cohesion score 0.0944741532976827 - nodes in this community are weakly interconnected._
- **Should `Project Overview & Dependencies` be split into smaller, more focused modules?**
  _Cohesion score 0.14 - nodes in this community are weakly interconnected._