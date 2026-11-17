# Spec: Recipe pricing fallbacks (substitute, Instacart price) with a visible source

Status: DRAFT for review (Phase 1, Specify). Nothing below is built yet.
Intent: confirmed in conversation, 2026-11-16 (interview). Extends the chat
pricing path in `scraper/chat_assistant.py::_price_from_saved` and
`docs/specs/rag-product-matching-spec.md`.

## Objective

When NOVA prices a meal's ingredient list, **no ingredient is silently dropped
or left unpriced**, and **every price says where it came from**.

For each ingredient, in this order:

1. **Our database of real prices** -- a product with an estimated in-store
   price (`estimated_real_price`), i.e. the lab's real store-vs-Instacart
   markup applied. Source label: `database`.
2. **A substitute** -- if the ingredient has no database price at any store,
   the chat model may propose an *interchangeable* ingredient (e.g. one cooking
   oil for another); if that substitute has a database price we use it.
   Source label: `substitute`, always shown as "Substitute for <ingredient>".
3. **The Instacart price** -- the price as listed on Instacart in our saved
   scrape, **not adjusted** (we have no real markup for these foods). Source
   label: `instacart`. The label always reads "Instacart price", never just
   "price".
4. Otherwise the ingredient stays in `missing` ("no price found"), exactly as
   today. A later round adds the last resort: tell the chat model "couldn't
   find X, we need a different recipe". **Not in this round.**

Who it serves: shoppers using the NOVA chat, and the lab team showing NOVA as
a recipe-pricing tool. Why now: foods that exist in our scrape but have no
markup category (eggs, bacon, pork chops, sour cream, peanut butter, ...) are
dropped today by `markup_data.classify() -> None`, so recipes using them can't
be priced.

### Out of scope (this round)

- Recipe links / recipe-page scraping.
- Any **live** Instacart search (no browser, no scrape at request time).
- Chunking the database.
- The "different recipe" last-resort step (comes after this).
- Inventing or pooling a markup for Instacart-fallback prices (real data only).
- Per-ounce / package-size normalization (separate next step).

## Assumptions (please correct)

1. "Our database of real prices" means a price with a real in-store estimate;
   "Instacart price" means the listed Instacart price with no adjustment.
2. **The source label is per store.** Each store has its own row for an
   ingredient and each row carries its own label: if a store has no markup
   number for the product's category, that store's row shows its own
   "Instacart price", even when another store's row for the same ingredient
   is a database price (e.g. olive oil: Market Basket = database, Stop & Shop
   and Shaw's = Instacart price). That already happens today.
3. **Substitution is per ingredient, not per store.** A substitute swaps the
   ingredient itself (one oil for another), so every store must price the
   *same* substitute, otherwise one comparison would hold different products.
   It is only attempted when **no store at all** has a database price for the
   ingredient (all stores lack a number, or there are no rows). If even one
   store has a database price, no substitute is tried and the other stores
   show their own Instacart price.
4. The chat model decides what is "interchangeable", with the recipe/meal as
   context, and is told to answer *none* unless a swap would really work in the
   dish at a similar quantity. It proposes at most 2 names; each is priced
   through the normal pipeline and accepted only if it gets a database price.
5. Substitution is attempted **before** the Instacart-price fallback (the
   confirmed order). For foods with no sensible swap (eggs, bacon) the model
   says none and they fall through to the Instacart price.
6. Non-grocery departments stay excluded (`price_lookup._NON_GROCERY_DEPARTMENTS`,
   which already includes baby, household, pets, ...). The Instacart fallback
   covers only grocery departments.
7. Totals keep mixing estimated in-store prices with Instacart prices, with
   the mixing disclosed (`sticker_priced_items`, now also `substituted_items`).

## Tech Stack

- Python 3, Flask app (`scraper/web_app.py`), vanilla JS (`scraper/static/app.js`).
- Ollama: the chat model for substitution proposals via the existing
  `ollama_client.chat_json()`; the existing judge (`product_matcher`) for
  picking products. **No new dependencies.**
- Data: `scraper/prices_output.json` (schema unchanged) +
  `scraper/markup_data.py` (real markup numbers, unchanged).

## Commands

```
Test (full):    ./.venv/bin/python3 -m unittest discover -s tests
Test (one file):./.venv/bin/python3 -m unittest tests.test_price_lookup -v
JS syntax:      node --check scraper/static/app.js
Run server:     python3 -m scraper.web_app --host 0.0.0.0 --port 49155 \
                  --model qwen3.8:27b --judge-model qwen3.8:27b --headless --max-age-hours 999999
Live price API: curl -s -H 'Content-Type: application/json' \
                  -d '{"items":["eggs","bacon"]}' http://cs.merrimack.edu:49155/api/price
```

## Project Structure

```
scraper/price_lookup.py      keep uncategorized grocery rows; price_source(row)
scraper/chat_assistant.py    _price_from_saved: substitution step; _store_basket_totals: substituted_items;
                             SUMMARY prompt: say the source of each price
scraper/substitutions.py     NEW, small: propose_substitutes(items, context) -> {item: [names]} (one batched model call)
scraper/web_app.py           pass the new fields through /api/price
scraper/static/app.js        show the source on every row; style.css for the label
tests/                       test_price_lookup, test_chat_assistant, test_web_app, test_frontend,
                             test_substitutions (new)
docs/specs/                  this file
```

## Code Style

Match `price_lookup.effective_price`: small pure functions, docstring says what
and why, `.get()` on row keys (test and live rows differ).

```python
def price_source(row: dict) -> str:
    """Where a row's price comes from: "database" when the store has a real
    markup number for its category (an estimated in-store price), else
    "instacart" (the price as listed, unadjusted)."""
    return "instacart" if row.get("estimated_real_price") is None else "database"
```

Substitution validation copies `product_matcher`'s style: strict `isinstance` +
length checks, anything malformed collapses to "no substitute" -- never raises.

## Testing Strategy

- `unittest`, TDD: write the failing test first for every behavior below.
- Model calls are mocked in unit tests (`product_matcher.pick_best_matches`,
  `ollama_client.chat_json`); fixtures are tiny `prices.json` files.
- JS rendering is tested through the existing Node harness in
  `tests/test_frontend.py` (`_call("renderItemRow", ...)`).
- Live acceptance (real data, real model), paced at most 3 agents with one in
  Chrome: eggs, bacon, pork chops, sour cream (Instacart price); one oil swap
  and one item with no listing (substitute / missing); a normal 8-item meal
  (labels everywhere, totals unchanged for database-priced items).

## Boundaries

- **Always:** tests first; run the full suite before calling a task done;
  label the Instacart fallback "Instacart price"; keep one row per store per
  item; fall back to "no substitute" on any model output that doesn't validate.
- **Ask first:** changing `prices_output.json`'s schema; adding a dependency;
  widening which departments the fallback covers; any change to markup rules;
  anything touching git push/pull (project CLAUDE.md).
- **Never:** run a live Instacart search or browser in the pricing path;
  adjust Instacart-fallback prices with an invented or pooled markup; present a
  substitute without saying it is one; silently drop an ingredient; chunk the
  database; push or pull without asking.

## Success Criteria

1. Every row in `/api/price` `findings` has `price_source` in
   {`database`, `instacart`, `substitute`}; substitute rows also carry
   `substitute_for` (the original ingredient).
2. The UI shows the source on the lead row and every "Also at" row; the
   Instacart fallback reads "Instacart price"; a substitute reads
   "Substitute for <ingredient>"; database rows say they come from our
   database of real prices.
3. Foods present in the scrape with no markup category (eggs, bacon, pork
   chops, sour cream, peanut butter) now return rows labeled
   "Instacart price" with `estimated_real_price` null -- not `missing`.
4. For an ingredient with no scraped listing and no database price, if the
   model proposes an interchangeable item that has a database price, the
   result uses it, keyed under the **original** ingredient name and labeled
   as a substitute. (Fixture test with a fake model.)
5. With no listing and no accepted substitute, the item stays in `missing`
   and nothing crashes (the hook the later "different recipe" step needs).
6. `store_totals` gains `substituted_items` per store; every existing field
   and ordering rule is unchanged.
7. No new slow step: at most **one** extra model call per request, only when
   at least one item has no database price; zero extra calls when every item
   has one. Measured live.
8. No regressions: all existing tests pass; markup math, 20-category
   behavior, per-store ordering and totals unchanged for database-priced items.
9. The LLM summary names the source of each price (database / Instacart price
   / substitute) and never calls an Instacart price an in-store price.

## Build order (one capability, two slices)

1. **Source labels + Instacart fallback** (criteria 1-3, 6 partly, 8-9). Ships
   alone and already fixes eggs/bacon/pork.
2. **Substitution** (criteria 4-5, 7). Needs slice 1's `price_source`.

## Open Questions -- proposed defaults adopted 2026-11-16 ("ok lets start")

The user said "ok lets start" after the per-store / per-ingredient
clarification, so these proposed defaults are adopted until overruled:

1. Label on database rows: "Our database of real prices" (appears with the
   estimated in-store price). Instacart rows read exactly "Instacart price".
2. Substitution is decided per ingredient (see assumption 3). Confirmed.
3. Substitution is tried before the Instacart price, for foods that do have a
   listing too; the model is told to answer "none" unless a swap really works.
   (Slice 2.)
4. A substitute label shows on every store row (each row carries `substitute_for`).
   (Slice 2.)
5. The summary may run slightly longer so each ingredient names its source.

Still open: none blocking Slice 1.

## Slice 1 scope decision (grocery departments)

The Instacart fallback keeps a row only if its department is one the markup
code already knows (`markup_data` direct departments + the four keyword-split
ones: dairy, baked-goods, breakfast-foods, meat-and-seafood), but whose product
matched no markup category (eggs, pork chops, sour cream...). Rows from any
other department (household, pets, baby, personal-care, unknown) stay dropped,
exactly as today. No new department is introduced.
