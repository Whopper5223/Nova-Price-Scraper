# Nova-Price-Scraper

Playwright-based Instacart price scraper for Nova AI. Sets a delivery address, lists local grocery stores, scrapes product prices, and saves them to `scraper/prices_output.json`. Self-heals broken CSS selectors via the Gemini API.

Three tools, one shared core (`scraper/core.py`):
- **`run_scraper`** — full-catalog crawl of a store's departments
- **`product_search`** — search a specific product by name and get a price range per store, without crawling everything
- **`recipe_scraper`** — paste a recipe URL, get ingredients priced and compared across stores

---

## Quick start

Run everything from the **repo root** (`Nova-Price-Scraper/`), not the `scraper/` subfolder.

```bash
# 1. Clone
git clone https://github.com/Whopper5223/Nova-Price-Scraper.git
cd Nova-Price-Scraper

# 2. Create + activate a virtual environment
python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate

# 3. Install dependencies + browser
pip install -r scraper/requirements-scraper.txt
playwright install chromium

# 4. Create .env in the repo root
echo "GEMINI_API_KEY=your_actual_key_here" > .env       # optional — only for self-healing
echo "DELIVERY_ADDRESS=1 Main St, Boston, MA 02108" >> .env   # your delivery address

# 5. Log in once (opens a browser, saves session.json)
python -m scraper.run_scraper --login

# 6. Run it
python -m scraper.run_scraper                              # full run
python -m scraper.run_scraper --max-stores 2 --max-products 10   # small test run
```

### All commands

```bash
python -m scraper.run_scraper --help                      # usage
python -m scraper.run_scraper                             # 5 stores, every department, 150 products per department
python -m scraper.run_scraper --max-stores 2             # limit stores (0 = every store found)
python -m scraper.run_scraper --max-products 50          # cap per department (0 = unlimited)
python -m scraper.run_scraper --max-per-store 200        # total cap across a store (0 = unlimited, default)
python -m scraper.run_scraper --departments "produce,dairy"       # only these departments
python -m scraper.run_scraper --stores "ALDI,Wegmans"    # only these stores
python -m scraper.run_scraper --address "1 Main St, Boston, MA 02108"  # one-off address override
python -m scraper.run_scraper --headless                 # no visible window (more bot risk)
python -m scraper.run_scraper --quiet                    # less output
python -m scraper.run_scraper --output ~/Desktop/prices.json      # custom save location

python -m scraper.run_scraper --show-output              # summary of last run
python -m scraper.run_scraper --show-output --products   # include product lists
python -m scraper.run_scraper --show-output --store ALDI # filter to one store
python -m scraper.run_scraper --show-output --raw        # raw JSON

python -m scraper.run_scraper --heal-only product_price --html-file scraper/_debug_products_aldi.html
```

#### Product search (search by name, get a price range)

Instead of crawling a store's whole catalog, search for one product and see its price range across stores — much faster than a full scrape.

```bash
# Search the default top storefront stores
python -m scraper.product_search --products "butter"

# Multiple products, specific stores (any deliverable store works — see below)
python -m scraper.product_search --products "butter,olive oil" --stores "ALDI,Hannaford"

# More results per store (widens the price range)
python -m scraper.product_search --products "milk" --results 8

# Raw JSON output, custom save location
python -m scraper.product_search --products "eggs" --output ~/Desktop/eggs.json --raw
```

Results saved to `scraper/product_search_output.json` by default. Only products whose name actually contains your search terms count toward the range (so searching "butter" won't get skewed by "butter cookies").

**Store lookup**: `--stores` isn't limited to what's visible on Instacart's storefront page. It tries, in order: (1) the visible storefront list, (2) Instacart's own search bar, (3) a direct URL guess. A store not shown on the storefront page (e.g. Hannaford) can still be found and searched if it delivers to your address — you'll see a `not on the storefront list — trying Instacart search...` message when that happens.

#### Recipe price comparison

```bash
# Find the cheapest store to buy all ingredients for a recipe
python -m scraper.recipe_scraper --url "https://www.allrecipes.com/recipe/10813/"

# Multi-recipe pages (roundups, "10 best..." lists): pick by number or name.
# Without --recipe the first recipe is used and the full list is printed.
python -m scraper.recipe_scraper --url "..." --recipe 2
python -m scraper.recipe_scraper --url "..." --recipe "carbonara"

# Specific stores (same 3-stage store lookup as product_search)
python -m scraper.recipe_scraper --url "..." --stores "ALDI,Hannaford"

# Limit to 3 stores
python -m scraper.recipe_scraper --url "..." --max-stores 3

# Skip pantry items you already have; widen the range to 8 matches per ingredient
python -m scraper.recipe_scraper --url "..." --skip "water,salt" --results 8

# Quiet mode + raw JSON output
python -m scraper.recipe_scraper --url "..." --quiet --raw
```

Requires a saved session (`--login` first). If `prices_output.json` exists from a prior scrape, store URLs are reused as a fast path — otherwise (or for stores not in that file) stores are resolved live. Each ingredient's price is a min–max range across its top search matches, not a single guess. Results saved to `scraper/recipe_output.json`.

> If you skip `source .venv/bin/activate`, prefix every command with `.venv/bin/` (e.g. `.venv/bin/python -m scraper.run_scraper`).

---

## Details

### Requirements
- **Python 3.10+**
- A real **Instacart account** (for login)
- A **Gemini API key** — optional, only for self-healing selectors. Get one at <https://aistudio.google.com/apikey>.

### The `--login` step
A real browser window opens. Complete **all** of these, then return to the terminal and press ENTER:
1. Solve any CAPTCHA
2. Log into Instacart fully (email + password)
3. Wait until you see the homepage (not a login page)
4. Set your delivery address (the same one as `DELIVERY_ADDRESS` in `.env`)
5. Confirm you can see a list of stores

This saves your session to `scraper/session.json` so future runs skip login.

### On a new machine
`.env` and `session.json` are gitignored and never pushed. On every new machine, recreate `.env` and run `--login` again.

### Healable selector keys
`address_input`, `address_suggestion`, `confirm_address_button`, `store_card`, `store_name`, `product_grid`, `product_card`, `product_name`, `product_price`, `product_unit`, `load_more_button`, `department_nav`, `age_verify_button`, `modal_close`, `search_input`, `search_results_card`, `store_search_result`.

### Project layout
```
Nova-Price-Scraper/
├── .env                          # YOU create this (gitignored) — GEMINI_API_KEY, DELIVERY_ADDRESS
├── .gitignore
├── README.md
└── scraper/
    ├── run_scraper.py            # CLI: full store/department scrape
    ├── product_search.py         # CLI: search a product by name, get a price range
    ├── recipe_scraper.py         # CLI: recipe URL → ingredient price comparison
    ├── core.py                   # shared browser/selector/search/store-lookup logic
    ├── instacart_scraper.py      # full-catalog crawl logic (used by run_scraper)
    ├── selector_healer.py        # Gemini-powered selector self-healing
    ├── selectors.json            # current CSS selectors
    ├── requirements-scraper.txt  # Python dependencies
    ├── session.json              # saved login (gitignored, from --login)
    ├── prices_output.json        # run_scraper results (gitignored)
    ├── product_search_output.json # product_search results (gitignored)
    └── recipe_output.json        # recipe_scraper results (gitignored)
```

### Troubleshooting
- **`ModuleNotFoundError: playwright` / `bs4`** — venv not activated or deps not installed. Re-run step 3.
- **`externally-managed-environment` on `pip install`** — installing into system Python; use the venv (step 2).
- **Browser doesn't open / `Executable doesn't exist`** — run `playwright install chromium`.
- **`No stores found`** — session expired or address not set; re-run `--login`.
- **Getting blocked / CAPTCHAs mid-scrape** — lower `--max-stores`/`--max-products`, avoid `--headless`, don't run back-to-back.
