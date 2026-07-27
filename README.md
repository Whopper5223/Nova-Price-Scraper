# Nova-Price-Scraper

Playwright-based Instacart price scraper for Nova AI. Sets a delivery address, lists local grocery stores, scrapes product prices, and saves them to `scraper/prices_output.json`. Self-heals broken CSS selectors via the Gemini API.

Three scraping tools, one shared core (`scraper/core.py`):
- **`run_scraper`** — full-catalog crawl of a store's departments
- **`product_search`** — search a specific product by name and get a price range per store, without crawling everything
- **`recipe_scraper`** — paste a recipe URL, get ingredients priced and compared across stores

Plus a conversational front end:
- **`chat_assistant`** — chat with a local Ollama model about what you need; it builds a shopping list and prices it against real scraped data

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
python -m playwright install chromium    # `python -m` works even if Scripts/ isn't on PATH

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

## Chat assistant (local Ollama)

Talk to a local LLM about what you're cooking or shopping for. It asks follow-up questions, turns the conversation into a shopping list, then prices that list against **real scraped data** — saved prices when they're fresh, a live Instacart search when they aren't.

The model never invents prices. It only summarizes rows the scraper produced, and those raw rows are printed alongside every answer so you can check them.

### Setup

```bash
# 1. Install Ollama (one time)          https://ollama.com
# 2. Pull a model (one time, ~5 GB)
ollama pull llama3.1

# 3. Fill the price cache (see scheduling below to automate this)
python -m scraper.scheduled_scrape

# 4. Chat
python -m scraper.chat_assistant
```

Ollama runs entirely on your machine — no API key, no data leaving the computer. It starts automatically after install; `ollama serve` starts it manually if needed.

### Usage

```bash
python -m scraper.chat_assistant                      # normal chat
python -m scraper.chat_assistant --max-age-hours 24   # only trust saved prices <24h old
python -m scraper.chat_assistant --live               # always scrape live, ignore saved prices
python -m scraper.chat_assistant --model mistral      # use a different pulled model
python -m scraper.chat_assistant --max-stores 6       # check more stores on live lookups
```

Example session:

```
you > making chicken parm for 4 tonight, I have olive oil already

nova > Got it — chicken parm for four. Do you need pasta to serve
       alongside, and do you have breadcrumbs and parmesan on hand?

you > yeah need pasta, breadcrumbs and cheese too

nova > Here's the shopping list I've got:
    - chicken breast
    - marinara sauce
    - mozzarella cheese
    - parmesan cheese
    - breadcrumbs
    - spaghetti

Look right? (y = price it / n = keep talking / or type a corrected,comma,separated,list)
you > y
```

It then prints every matching price row per store, followed by a plain-language summary naming the cheapest store per item and an approximate basket total.

Paste a **recipe URL** at any point and it skips list-building entirely, routing straight through `recipe_scraper`.

### Where the prices come from

1. **Saved data first** — reads `scraper/prices_output.json` (whatever `run_scraper` last wrote). Fast, no browser.
2. **Live fallback** — anything missing from saved data, or saved data older than `--max-age-hours` (default 72), triggers a live `product_search` lookup. This opens a browser and is slower.

There's no database — the assistant reads the same JSON the scraper already writes.

### Keeping prices fresh

`scheduled_scrape` is a scheduler-friendly wrapper around the full scraper. It exits non-zero on failure so a task scheduler can alert you.

```bash
python -m scraper.scheduled_scrape                          # default settings
python -m scraper.scheduled_scrape --max-stores 0 --quiet   # every store, minimal output
python -m scraper.scheduled_scrape --departments "produce,dairy"
```

To run it daily on Windows at 3 AM (adjust the path to your checkout):

```
schtasks /create /tn "NovaPriceScrape" /sc daily /st 03:00 ^
  /tr "cmd /c cd /d C:\Users\unmes\nova-scraper && python -m scraper.scheduled_scrape --quiet"
```

Log in once first (`python -m scraper.run_scraper --login`) — the scheduled run needs that saved session, and it opens a real browser window by default.

### Config

Optional `.env` entries (both have working defaults):

```
OLLAMA_URL=http://localhost:11434
OLLAMA_MODEL=llama3.1
```

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
    ├── chat_assistant.py         # CLI: conversational assistant (Ollama + real prices)
    ├── scheduled_scrape.py       # CLI: unattended price refresh for the assistant
    ├── core.py                   # shared browser/selector/search/store-lookup logic
    ├── instacart_scraper.py      # full-catalog crawl logic (used by run_scraper)
    ├── selector_healer.py        # Gemini-powered selector self-healing
    ├── price_lookup.py           # read/search saved prices_output.json (no browser needed)
    ├── ollama_client.py          # local Ollama HTTP wrapper
    ├── selectors.json            # current CSS selectors
    ├── requirements-scraper.txt  # Python dependencies
    ├── session.json              # saved login (gitignored, from --login)
    ├── prices_output.json        # run_scraper results (gitignored) — the assistant's price cache
    ├── product_search_output.json # product_search results (gitignored)
    └── recipe_output.json        # recipe_scraper results (gitignored)
```

### Troubleshooting
- **`ModuleNotFoundError: playwright` / `bs4`** — venv not activated or deps not installed. Re-run step 3.
- **`externally-managed-environment` on `pip install`** — installing into system Python; use the venv (step 2).
- **Browser doesn't open / `Executable doesn't exist`** — run `python -m playwright install chromium`.
- **`playwright: The term ... is not recognized` (Windows)** — pip installed to your user directory but that `Scripts\` folder isn't on PATH. Use `python -m playwright ...` instead, which never depends on PATH. (Same fix applies to any pip-installed CLI.)
- **`ollama: The term ... is not recognized` right after installing** — PATH updates don't reach already-open terminals. Open a new terminal, or call it by full path: `& "$env:LOCALAPPDATA\Programs\Ollama\ollama.exe" list`.
- **`No stores found`** — session expired or address not set; re-run `--login`.
- **Getting blocked / CAPTCHAs mid-scrape** — lower `--max-stores`/`--max-products`, avoid `--headless`, don't run back-to-back.
- **`Cannot reach the Ollama server`** — Ollama isn't running. Start it with `ollama serve`, or check `OLLAMA_URL` if you changed the port.
- **`Model 'llama3.1' is not pulled`** — run `ollama pull llama3.1`, or point `OLLAMA_MODEL` at a model you already have (`ollama list` shows them).
- **Assistant says "No saved prices yet"** — run `python -m scraper.scheduled_scrape` to populate `prices_output.json`, or use `--live` to scrape on demand.
- **Assistant is slow to reply** — normal on CPU-only machines. A smaller model (`ollama pull llama3.2:3b`, then `--model llama3.2:3b`) responds much faster.
