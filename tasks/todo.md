# Tasks: RAG-based product matching (v3 — LLM-judge)

Plan: `tasks/plan.md` | Spec: `docs/specs/rag-product-matching-spec.md`

## Task 1: Unwire Stage 2 from production call sites

**Description:** `core.py`'s `search_products()` and `price_lookup.py`'s
`query_prices()` currently call `product_embeddings.filter_by_relevance()`
after lexical matching. Remove those calls — proven unreliable (see spec
Revision History) — while keeping the function and its tests in
`product_embeddings.py` untouched (may be reusable for a future call site).

**Acceptance criteria:**
- [ ] `core.search_products()` no longer calls `filter_by_relevance`
- [ ] `price_lookup.query_prices()` no longer calls `filter_by_relevance`
- [ ] `filter_by_relevance` and its tests remain in the codebase, unmodified

**Verification:**
- [ ] Tests pass: `python -m unittest discover -s tests -v`
- [ ] `grep -rn "filter_by_relevance" scraper/core.py scraper/price_lookup.py` → no matches

**Dependencies:** None

**Files likely touched:**
- `scraper/core.py`
- `scraper/price_lookup.py`
- `tests/test_core.py`, `tests/test_price_lookup.py` (remove/update tests that asserted Stage 2 behavior at these call sites)

**Estimated scope:** Small (2-4 files)

---

## Task 2: Create `product_matcher.py` (Stage 3 judge)

**Description:** New module mirroring `product_categorizer.py`'s exact shape:
module-level `BATCH_SIZE`, a worked example as real chat turns, a
`_judge_batch()` function calling `ollama_client.chat_json()`, strict
`isinstance`+length+range validation collapsing to `None` per-item on any
mismatch, and a public entrypoint.

**Acceptance criteria:**
- [ ] `pick_best_matches(recipe_name, all_ingredients, batch)` (or similar
      signature — finalize while writing) returns one index-or-None per item
      in `batch`, where each item carries an ingredient name + its Stage-1
      candidate product list
- [ ] Prompt includes recipe name, full ingredient list, and each
      ingredient's numbered candidates
- [ ] Any malformed/wrong-length/out-of-range response → `None` for the
      affected ingredient(s), not a crash
- [ ] Batches multiple ingredients per Ollama call (same `BATCH_SIZE`
      convention as `product_categorizer.py`)

**Verification:**
- [ ] Tests pass: `python -m unittest tests.test_product_matcher -v`

**Dependencies:** None (pure module, no wiring yet)

**Files likely touched:**
- `scraper/product_matcher.py` (new)

**Estimated scope:** Small-Medium (1 file)

---

## Task 3: Unit tests for `product_matcher.py`

**Description:** Mirror `test_product_categorizer.py`'s test structure:
mocked `chat_json`, assert batching, worked-example presence, and every
fallback path (malformed JSON, wrong length, out-of-range index, explicit
`null`).

**Acceptance criteria:**
- [ ] Test for a clean successful pick
- [ ] Test for mismatched response length → all `None` for that batch
- [ ] Test for out-of-range index → `None` for that entry
- [ ] Test for `index: null` (explicit "no good candidate")
- [ ] Test confirming a worked example precedes the real batch in the message list

**Verification:**
- [ ] Tests pass: `python -m unittest tests.test_product_matcher -v`

**Dependencies:** Task 2

**Files likely touched:**
- `tests/test_product_matcher.py` (new)

**Estimated scope:** Small (1 file)

---

## Task 4: Wire into `recipe_scraper.py`

**Description:** In `run_recipe_scraper()`, after the `search_products()`
call (~line 490), call the Stage 3 judge with `recipe["name"]`, the full
`ingredients` list, and `res["products"]` as candidates. Use the judge's pick
(or fall back to `res["min"]`'s product on `None`) when building
`store_result["results"][ingredient]` and incrementing `basket_total`.

**Acceptance criteria:**
- [ ] Judge is called with real recipe context, not just the bare ingredient
- [ ] `basket_total` reflects the judge's pick, not automatically the
      cheapest Stage-1 candidate
- [ ] `None`/no-good-candidate falls back to existing `not_found`-style
      handling — confirm this against current behavior before finalizing
      (open question in spec)
- [ ] Existing recipe_scraper tests (if any) still pass

**Verification:**
- [ ] Tests pass: `python -m unittest discover -s tests -v` (full suite)
- [ ] Live/manual check on the server: real recipe containing a
      flavor-noise-prone ingredient (e.g. "milk"), confirm the judge picks
      the plain product

**Dependencies:** Task 2, Task 3

**Files likely touched:**
- `scraper/recipe_scraper.py`
- test file TBD — confirm existing recipe_scraper test coverage first

**Estimated scope:** Small-Medium (1-2 files)

---

## Checkpoint: Integration complete

- [ ] Full test suite passes: `python -m unittest discover -s tests -v`
- [ ] Live check on the server with a real recipe
- [ ] No MariaDB/port/startup-script work done (still out of scope)
- [ ] Human review before merge
