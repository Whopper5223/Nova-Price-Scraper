# Graph Report - .  (2026-07-12)

## Corpus Check
- Corpus is ~32,990 words - fits in a single context window. You may not need a graph.

## Summary
- 149 nodes · 299 edges · 11 communities
- Extraction: 97% EXTRACTED · 3% INFERRED · 0% AMBIGUOUS · INFERRED: 10 edges (avg confidence: 0.81)
- Token cost: 63,572 input · 0 output

## Community Hubs (Navigation)
- Recipe Ingredient Extraction
- Project Overview & Dependencies
- Scraper CLI Command Dispatch
- Product Search & Pricing
- Instacart Browser Automation
- Selector Self-Healing
- Scrape Report Data
- Store & Card Selector Probing
- Product Search CLI
- Store Name Resolution

## God Nodes (most connected - your core abstractions)
1. `search_products()` - 16 edges
2. `_get_store_list()` - 15 edges
3. `_human_delay()` - 14 edges
4. `_dismiss_modals()` - 14 edges
5. `resolve_store()` - 13 edges
6. `run_recipe_scraper()` - 13 edges
7. `heal_selector()` - 13 edges
8. `_scrape_store_products()` - 11 edges
9. `run_product_search()` - 11 edges
10. `_try_select()` - 10 edges

## Surprising Connections (you probably didn't know these)
- `graphify (project knowledge graph)` --conceptually_related_to--> `Nova-Price-Scraper (project)`  [INFERRED]
  CLAUDE.md → README.md
- `Instacart Price Scrape Report (2026-07-04)` --shares_data_with--> `scraper/run_scraper.py (run_scraper CLI)`  [INFERRED]
  scraper/scrape_report.md → README.md
- `Gemini API selector self-healing` --shares_data_with--> `google-genai (Gemini API client)`  [INFERRED]
  README.md → scraper/requirements-scraper.txt
- `Nova-Price-Scraper (project)` --references--> `scraper/requirements-scraper.txt (Python dependencies)`  [EXTRACTED]
  README.md → scraper/requirements-scraper.txt
- `run_scraper()` --calls--> `launch_browser()`  [EXTRACTED]
  scraper/instacart_scraper.py → scraper/core.py

## Import Cycles
- None detected.

## Hyperedges (group relationships)
- **Three CLI tools sharing scraper/core.py** — scraper_run_scraper_py, scraper_product_search_py, scraper_recipe_scraper_py, scraper_core_py [EXTRACTED 1.00]
- **Stores covered in the 2026-07-04 scrape report** — store_stop_and_shop, store_aldi, store_shaws, store_market_basket [EXTRACTED 1.00]
- **Scraper Python dependencies** — concept_playwright, concept_playwright_stealth, concept_google_genai, concept_beautifulsoup4, concept_requests_lib [EXTRACTED 1.00]

## Communities (11 total, 0 thin omitted)

### Community 0 - "Recipe Ingredient Extraction"
Cohesion: 0.11
Nodes (29): _extract_recipe_ingredients(), _fetch_html_requests(), fetch_ingredients_async(), fetch_ingredients_sync(), _gemini_extract_ingredients(), _gemini_generate(), _gemini_normalize(), list_stores() (+21 more)

### Community 1 - "Project Overview & Dependencies"
Cohesion: 0.10
Nodes (22): graphify (project knowledge graph), beautifulsoup4 (HTML parsing), Gemini API selector self-healing, google-genai (Gemini API client), Instacart (target site), Playwright (browser automation), playwright-stealth (bot-detection evasion), requests (HTTP client library) (+14 more)

### Community 2 - "Scraper CLI Command Dispatch"
Cohesion: 0.21
Nodes (18): Namespace, Path, _check_env(), cmd_heal(), cmd_list_stores(), cmd_login(), cmd_scrape(), cmd_show_output() (+10 more)

### Community 3 - "Product Search & Pricing"
Cohesion: 0.19
Nodes (14): get_card_name(), get_card_price(), _matches_query(), _median(), _parse_price(), _parse_unit_price(), core.py  Shared foundation for all Nova Instacart scrapers. - Browser lifecycle, Extract a float price from strings like '$3.99', '3.99', '$1,299.00'.     Instac (+6 more)

### Community 4 - "Instacart Browser Automation"
Cohesion: 0.24
Nodes (14): _dismiss_modals(), _human_delay(), Sleep for a randomized human-like duration., Dismiss any overlays (age verify, location prompts, cookie banners)., Page, instacart_scraper.py  Full-catalog Instacart scraper. - Sets a hardcoded Lawrenc, Visit a store and scrape products from target departments.     Returns a list of, Run the full scrape: set address → get stores → scrape products → return result (+6 more)

### Community 5 - "Selector Self-Healing"
Cohesion: 0.21
Nodes (13): _extract_relevant_html(), _extract_selector_from_response(), heal_selector(), _load_selectors(), selector_healer.py  When a CSS selector stops working against Instacart's HTML,, Return True if the selector matches at least one element in the given HTML., If Gemini returns a child-combinator chain like `div.a > span.b > a.c` that, Gemini is prompted to return only the selector, but sometimes wraps it in     ma (+5 more)

### Community 6 - "Scrape Report Data"
Cohesion: 0.47
Nodes (9): Dairy (department category), Frozen (department category), Produce (department category), Delivery address: 50 Island St, Lawrence, MA 01840, Instacart Price Scrape Report (2026-07-04), ALDI (store), Market Basket (store), Shaw's (store) (+1 more)

### Community 7 - "Store & Card Selector Probing"
Cohesion: 0.31
Nodes (9): _get_store_list(), _probe_card_selectors(), Page, Scroll down repeatedly until we have target elements or no new ones appear., Try to locate elements using the current selector for selector_key.     If nothi, Before looping over cards, check each sub-selector against the first card.     I, Return a list of stores available for the set delivery address.     Each entry:, _scroll_to_load_more() (+1 more)

### Community 8 - "Product Search CLI"
Cohesion: 0.33
Nodes (8): launch_browser(), _load_selectors(), Launch a headful stealth browser with the saved session (if any) and yield     (, main(), _print_comparison(), product_search.py  Search Instacart stores for specific products by name and com, For each store: resolve it by name (or take the top storefront stores), then, run_product_search()

### Community 9 - "Store Name Resolution"
Cohesion: 0.33
Nodes (6): _name_matches(), Stop & Shop' -> 'stop-shop' — matches Instacart's store URL slugs., Loose match: every word of the wanted name appears in the candidate., Find a store by name, trying progressively harder:       1. The storefront list, resolve_store(), _slugify()

## Knowledge Gaps
- **10 isolated node(s):** `graphify (project knowledge graph)`, `scraper/instacart_scraper.py (full-catalog crawl logic)`, `scraper/selectors.json (current CSS selectors)`, `Instacart (target site)`, `beautifulsoup4 (HTML parsing)` (+5 more)
  These have ≤1 connection - possible missing edges or undocumented components.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `heal_selector()` connect `Selector Self-Healing` to `Scraper CLI Command Dispatch`, `Product Search & Pricing`, `Store & Card Selector Probing`?**
  _High betweenness centrality (0.089) - this node is a cross-community bridge._
- **Why does `search_products()` connect `Product Search & Pricing` to `Product Search CLI`, `Recipe Ingredient Extraction`, `Instacart Browser Automation`, `Store & Card Selector Probing`?**
  _High betweenness centrality (0.043) - this node is a cross-community bridge._
- **Why does `run_product_search()` connect `Product Search CLI` to `Scraper CLI Command Dispatch`, `Product Search & Pricing`, `Instacart Browser Automation`, `Store & Card Selector Probing`, `Store Name Resolution`?**
  _High betweenness centrality (0.034) - this node is a cross-community bridge._
- **What connects `graphify (project knowledge graph)`, `scraper/instacart_scraper.py (full-catalog crawl logic)`, `scraper/selectors.json (current CSS selectors)` to the rest of the system?**
  _10 weakly-connected nodes found - possible documentation gaps or missing edges._
- **Should `Recipe Ingredient Extraction` be split into smaller, more focused modules?**
  _Cohesion score 0.11264367816091954 - nodes in this community are weakly interconnected._
- **Should `Project Overview & Dependencies` be split into smaller, more focused modules?**
  _Cohesion score 0.1038961038961039 - nodes in this community are weakly interconnected._