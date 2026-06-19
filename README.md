# Nova-Price-Scraper

Playwright-based Instacart price scraper for Nova AI. Sets a delivery address, lists local grocery stores, scrapes product prices, and saves them to `scraper/prices_output.json`. Self-heals broken CSS selectors via the Gemini API.

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

# 4. Create .env in the repo root (optional — only for self-healing)
echo "GEMINI_API_KEY=your_actual_key_here" > .env

# 5. Log in once (opens a browser, saves session.json)
python -m scraper.run_scraper --login

# 6. Run it
python -m scraper.run_scraper                              # full run
python -m scraper.run_scraper --max-stores 2 --max-products 10   # small test run
```

### All commands

```bash
python -m scraper.run_scraper --help                      # usage
python -m scraper.run_scraper                             # full scrape (5 stores x 40 products)
python -m scraper.run_scraper --max-stores 2             # limit stores
python -m scraper.run_scraper --max-products 10          # limit products per store
python -m scraper.run_scraper --headless                 # no visible window (more bot risk)
python -m scraper.run_scraper --quiet                    # less output

python -m scraper.run_scraper --show-output              # summary of last run
python -m scraper.run_scraper --show-output --products   # include product lists
python -m scraper.run_scraper --show-output --store ALDI # filter to one store
python -m scraper.run_scraper --show-output --raw        # raw JSON

python -m scraper.run_scraper --heal-only product_price --html-file scraper/_debug_products_aldi.html
```

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
4. Set delivery address to `50 Island St, Lawrence, MA 01840`
5. Confirm you can see a list of stores

This saves your session to `scraper/session.json` so future runs skip login.

### On a new machine
`.env` and `session.json` are gitignored and never pushed. On every new machine, recreate `.env` and run `--login` again.

### Healable selector keys
`address_input`, `address_suggestion`, `confirm_address_button`, `store_card`, `store_name`, `product_grid`, `product_card`, `product_name`, `product_price`, `product_unit`, `load_more_button`, `department_nav`, `age_verify_button`, `modal_close`.

### Project layout
```
Nova-Price-Scraper/
├── .env                          # YOU create this (gitignored) — GEMINI_API_KEY
├── .gitignore
├── README.md
└── scraper/
    ├── run_scraper.py            # CLI entry point
    ├── instacart_scraper.py      # Playwright scraping logic
    ├── selector_healer.py        # Gemini-powered selector self-healing
    ├── selectors.json            # current CSS selectors
    ├── requirements-scraper.txt  # Python dependencies
    ├── session.json              # saved login (gitignored, from --login)
    └── prices_output.json        # scrape results (gitignored)
```

### Troubleshooting
- **`ModuleNotFoundError: playwright` / `bs4`** — venv not activated or deps not installed. Re-run step 3.
- **`externally-managed-environment` on `pip install`** — installing into system Python; use the venv (step 2).
- **Browser doesn't open / `Executable doesn't exist`** — run `playwright install chromium`.
- **`No stores found`** — session expired or address not set; re-run `--login`.
- **Getting blocked / CAPTCHAs mid-scrape** — lower `--max-stores`/`--max-products`, avoid `--headless`, don't run back-to-back.
