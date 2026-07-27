# Graph Report - C:\Users\unmes\nova-scraper  (2026-07-26)

## Corpus Check
- Corpus is ~16,348 words - fits in a single context window. You may not need a graph.

## Summary
- 230 nodes · 441 edges · 16 communities (15 shown, 1 thin omitted)
- Extraction: 96% EXTRACTED · 4% INFERRED · 0% AMBIGUOUS · INFERRED: 16 edges (avg confidence: 0.85)
- Token cost: 41,041 input · 0 output

## Community Hubs (Navigation)
- Chat Assistant Flow
- Recipe Scraping Pipeline
- Saved Price Lookup
- Project Documentation
- Scraper CLI Runner
- Selector Self-Healing
- Price Parsing Helpers
- Product Search Flow
- Address & Modal Handling
- Store List Discovery
- Chrome CDP Login
- Graphify Workflow Docs
- Product Card Extraction
- Store Name Resolution
- HTTP Dependency

## God Nodes (most connected - your core abstractions)
1. `search_products()` - 16 edges
2. `run_recipe_scraper()` - 16 edges
3. `_get_store_list()` - 15 edges
4. `_human_delay()` - 14 edges
5. `_dismiss_modals()` - 14 edges
6. `resolve_store()` - 13 edges
7. `run_product_search()` - 13 edges
8. `heal_selector()` - 13 edges
9. `_run_chat()` - 11 edges
10. `_scrape_store_products()` - 11 edges

## Surprising Connections (you probably didn't know these)
- `_price_live()` --calls--> `run_product_search()`  [EXTRACTED]
  scraper/chat_assistant.py → scraper/product_search.py
- `_handle_recipe_url()` --calls--> `run_recipe_scraper()`  [EXTRACTED]
  scraper/chat_assistant.py → scraper/recipe_scraper.py
- `_run_chat()` --calls--> `describe_freshness()`  [EXTRACTED]
  scraper/chat_assistant.py → scraper/price_lookup.py
- `run_recipe_scraper()` --calls--> `launch_browser()`  [EXTRACTED]
  scraper/recipe_scraper.py → scraper/core.py
- `run_recipe_scraper()` --calls--> `_load_selectors()`  [EXTRACTED]
  scraper/recipe_scraper.py → scraper/core.py

## Import Cycles
- None detected.

## Hyperedges (group relationships)
- **graphify navigation command set** — claude_md_graphify_query, claude_md_graphify_path, claude_md_graphify_explain, claude_md_graphify_update [EXTRACTED 0.90]
- **Conversational pricing pipeline: chat to grounded price answer** — readme_chat_assistant, readme_ollama_client, readme_price_lookup, readme_prices_output_json, readme_product_search, readme_grounding [EXTRACTED 1.00]
- **Unattended price freshness loop** — readme_windows_task_scheduler, readme_scheduled_scrape, readme_run_scraper, readme_prices_output_json, readme_session_json, readme_max_age_hours [EXTRACTED 1.00]
- **Shared core scraping stack with self-healing selectors** — readme_core, readme_run_scraper, readme_product_search, readme_recipe_scraper, readme_selector_healer, readme_selectors_json, readme_playwright [EXTRACTED 1.00]

## Communities (16 total, 1 thin omitted)

### Community 0 - "Chat Assistant Flow"
Cohesion: 0.09
Nodes (33): RuntimeError, _confirm_list(), _extract_list(), _handle_recipe_url(), main(), _plain_summary(), _price_live(), _print_findings() (+25 more)

### Community 1 - "Recipe Scraping Pipeline"
Cohesion: 0.09
Nodes (30): _extract_recipes(), _fetch_html_requests(), fetch_recipes_async(), fetch_recipes_sync(), _gemini_extract_ingredients(), _gemini_generate(), _gemini_normalize(), list_stores() (+22 more)

### Community 2 - "Saved Price Lookup"
Cohesion: 0.13
Nodes (25): _price_from_saved(), Price what we can from prices_output.json.     Returns (findings, unresolved) —, cheapest_by_store(), data_age(), describe_freshness(), is_stale(), list_stores(), load_latest() (+17 more)

### Community 3 - "Project Documentation"
Cohesion: 0.15
Nodes (26): chat_assistant (CLI conversational grocery assistant), core.py (shared browser/selector/search/store-lookup logic), Gemini API (GEMINI_API_KEY), Price grounding (model never invents prices), Instacart (price data source), instacart_scraper (full-catalog crawl logic), llama3.1 model, --max-age-hours price freshness threshold (default 72) (+18 more)

### Community 4 - "Scraper CLI Runner"
Cohesion: 0.20
Nodes (17): Namespace, _check_env(), cmd_heal(), cmd_list_stores(), cmd_login(), cmd_scrape(), cmd_show_output(), main() (+9 more)

### Community 5 - "Selector Self-Healing"
Cohesion: 0.19
Nodes (14): google-genai>=1.0.0, _extract_relevant_html(), _extract_selector_from_response(), heal_selector(), _load_selectors(), selector_healer.py  When a CSS selector stops working against Instacart's HTML, Return True if the selector matches at least one element in the given HTML., If Gemini returns a child-combinator chain like `div.a > span.b > a.c` that (+6 more)

### Community 6 - "Price Parsing Helpers"
Cohesion: 0.21
Nodes (12): get_card_name(), _matches_query(), _median(), _parse_unit_price(), core.py  Shared foundation for all Nova Instacart scrapers. - Browser lifecyc, Extract product name from a product card.     img[data-testid='item-card-image', True if every word of the query appears in the product name (case-insensitive)., Parse '$0.12/oz' style unit-price text into (0.12, 'oz'). Returns (None, None) i (+4 more)

### Community 7 - "Product Search Flow"
Cohesion: 0.26
Nodes (11): launch_browser(), _load_selectors(), Yield (browser, context, page) for scraping.      Two modes:       - CDP atta, Run the full scrape: set address → get stores → scrape products → return result, run_scraper(), main(), _print_comparison(), Path (+3 more)

### Community 8 - "Address & Modal Handling"
Cohesion: 0.25
Nodes (11): _dismiss_modals(), _human_delay(), Sleep for a randomized human-like duration., Dismiss any overlays (age verify, location prompts, cookie banners)., Page, Instacart's address <input> starts inside a display:none container on the     h, Navigate to Instacart and set the delivery address to Lawrence, MA.     Returns, _reveal_address_input() (+3 more)

### Community 9 - "Store List Discovery"
Cohesion: 0.27
Nodes (11): _get_store_list(), _probe_card_selectors(), Page, Scroll down repeatedly until we have target elements or no new ones appear., Try to locate elements using the current selector for selector_key.     If noth, Before looping over cards, check each sub-selector against the first card., Return a list of stores available for the set delivery address.     Each entry:, _scroll_to_load_more() (+3 more)

### Community 10 - "Chrome CDP Login"
Cohesion: 0.33
Nodes (8): describe(), find_chrome(), is_reachable(), main(), _print_next_steps(), Path, chrome_login.py  Start a real Chrome with remote debugging so the scraper can at, Browser identity behind the debug port, for confirming what we attached to.

### Community 11 - "Graphify Workflow Docs"
Cohesion: 0.33
Nodes (7): GRAPH_REPORT.md, graphify knowledge graph workflow, graphify explain command, graphify path command, graphify query command, graphify update command, graphify-out/wiki/index.md

### Community 12 - "Product Card Extraction"
Cohesion: 0.33
Nodes (6): get_card_price(), _parse_price(), Extract a float price from strings like '$3.99', '3.99', '$1,299.00'.     Insta, Extract price from a product card.     span.screen-reader-only contains 'Curren, instacart_scraper.py  Full-catalog Instacart scraper. - Sets the delivery add, beautifulsoup4>=4.12.0

### Community 13 - "Store Name Resolution"
Cohesion: 0.33
Nodes (6): _name_matches(), Stop & Shop' -> 'stop-shop' — matches Instacart's store URL slugs., Loose match: every word of the wanted name appears in the candidate., Find a store by name, trying progressively harder:       1. The storefront list, resolve_store(), _slugify()

## Knowledge Gaps
- **11 isolated node(s):** `graphify path command`, `graphify explain command`, `graphify update command`, `graphify-out/wiki/index.md`, `playwright>=1.44.0` (+6 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **1 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `run_recipe_scraper()` connect `Address & Modal Handling` to `Chat Assistant Flow`, `Recipe Scraping Pipeline`, `Price Parsing Helpers`, `Product Search Flow`, `Store List Discovery`, `Store Name Resolution`?**
  _High betweenness centrality (0.114) - this node is a cross-community bridge._
- **Why does `run_product_search()` connect `Product Search Flow` to `Chat Assistant Flow`, `Price Parsing Helpers`, `Address & Modal Handling`, `Store List Discovery`, `Store Name Resolution`?**
  _High betweenness centrality (0.079) - this node is a cross-community bridge._
- **Why does `heal_selector()` connect `Selector Self-Healing` to `Store List Discovery`, `Scraper CLI Runner`, `Price Parsing Helpers`?**
  _High betweenness centrality (0.059) - this node is a cross-community bridge._
- **What connects `graphify path command`, `graphify explain command`, `graphify update command` to the rest of the system?**
  _11 weakly-connected nodes found - possible documentation gaps or missing edges._
- **Should `Chat Assistant Flow` be split into smaller, more focused modules?**
  _Cohesion score 0.09243697478991597 - nodes in this community are weakly interconnected._
- **Should `Recipe Scraping Pipeline` be split into smaller, more focused modules?**
  _Cohesion score 0.09462365591397849 - nodes in this community are weakly interconnected._
- **Should `Saved Price Lookup` be split into smaller, more focused modules?**
  _Cohesion score 0.1339031339031339 - nodes in this community are weakly interconnected._