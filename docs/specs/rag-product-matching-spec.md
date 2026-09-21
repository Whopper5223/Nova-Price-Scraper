# Spec: RAG-based product matching

Status: living document, in progress. Extends `docs/intent/rag-product-matching.md`.
Revision history at the bottom — this spec changed twice after live testing on
the Merrimack server disproved earlier assumptions. Read the revision notes;
they explain *why* the architecture looks like this, not just what it is.

## Objective

Replace substring/keyword product matching with something that gets the right
product — "butter" should not match "butter cookies"; a recipe calling for
"milk" should not get charged for chocolate milk. This serves two call sites:

1. `recipe_scraper.py` — turning a normalized ingredient name into the correct
   product(s) to price out, **with actual recipe context available** (dish
   name + full ingredient list are already in scope where the match happens).
2. `price_lookup.py` (used by `chat_assistant.py`) — answering price questions
   grounded in real scraped data, with no recipe/task context available beyond
   the raw search term.

These two call sites ended up needing **different solutions**, because only
one of them has context worth reasoning over. See Architecture below.

## Architecture (as-built, after two rounds of live testing)

### Stage 1 — lexical matching (`product_embeddings.matches_query`)

Word-boundary + light stemming, deterministic, no model calls. Replaces the
old `_matches_query()` that existed as two independently-drifting private
copies in `core.py` and `price_lookup.py` (each had a comment saying "keep in
sync by hand" — exactly the kind of duplication this feature exists to fix).

- Word-boundary matching (not raw substring) fixes "egg" matching inside
  "eggplant", "pea" matching inside "peanut butter".
- A narrow custom stemmer handles common plurals ("egg" still matches "Eggs")
  without generic edit-distance tolerance, which would create false positives
  on short words ("egg"/"ego", "pea"/"tea" are edit-distance 1 apart but
  unrelated). Deliberately does not handle "y"->"ies" (candy/candies,
  berry/berries): a word already ending in "ie" pluralizes by just adding "s"
  (cookie -> cookies), and that surface form is indistinguishable from a real
  "y"->"ies" swap without a dictionary — a rule for one case corrupts the
  other. Two real bugs were found and fixed here during live testing (see
  Revision History).

**Live-tested and unconditionally correct** on every lexical case tried:
egg/eggplant, pea/peanut butter, ham/hamburger, corn/popcorn, apple/pineapple,
nut/coconut, tomato/tomatoes, apple/apples, cookie/cookies. Used by both call
sites, no known failures.

### Stage 2 — semantic embedding filter: BUILT, TESTED LIVE, **NOT WIRED IN**

`product_embeddings.filter_by_relevance()` and the embedding infrastructure
(`get_embedding()`, `cosine_similarity()`, Ollama `nomic-embed-text` with
required `search_query:`/`search_document:` prefixes, SQLite cache) still
exist in `product_embeddings.py` with full test coverage, but are **not
called from `core.py` or `price_lookup.py` in production**.

Why: two rounds of live testing against real embeddings proved that no single
global cutoff — absolute threshold or relative margin — can separate "same
product, different phrasing" (Tomato Sauce for a "tomato" query — should
stay) from "different product, similar words" (Butter Cookies for a "butter"
query — should go). Concrete proof: butter needs a margin < 0.0206 to drop
Butter Cookies; tomato needs a margin >= 0.0292 to keep Tomato Sauce. Those
directly contradict — there is no margin value that gets both right. Full
data in Revision History. A pure numeric-cutoff approach over raw cosine
similarity has hit its ceiling for this problem; the fix is judgment with
context (Stage 3), not a better number.

Kept in the codebase (not deleted) because the code is correct and tested for
what it does — it may be useful again if a future need doesn't have the
context Stage 3 requires (e.g. a differently-shaped call site).

### Stage 3 — LLM-judge reranking (`recipe_scraper.py` path only)

**New work, not yet built.** For recipe matching only — the one call site
with real context (dish name, full ingredient list) to judge candidates
against, unlike `price_lookup.py`'s bare search term.

Design: mirrors `product_categorizer.py`'s exact pattern (batched
`ollama_client.chat_json()` calls, worked example as real prior chat turns,
strict output-shape validation collapsing to `None`/fallback on any
mismatch — see that module for the reference implementation). New module
`scraper/product_matcher.py`:

- Batches ingredients (each carrying its Stage-1 candidate list) through one
  Ollama call per batch, same `BATCH_SIZE` convention.
- Prompt includes the recipe/dish name and the full ingredient list as
  context, plus each ingredient's numbered candidate list.
- Expected response shape: `{"choices": [{"index": <int or null>}, ...]}`,
  one entry per ingredient in the batch, `index` pointing into that
  ingredient's own candidate list (`null` if none fit).
- On any shape mismatch, wrong length, or out-of-range index: fall back to
  today's behavior for that ingredient (cheapest of the Stage-1 candidates) —
  never crash, never guess.
- Inserted into `run_recipe_scraper()` (`recipe_scraper.py`) right after the
  `search_products()` call (currently line ~490), where `recipe["name"]` and
  the full `ingredients` list are already local variables in scope.

**Retrieval stays recall-biased feeding into this stage**: Stage 1 only, no
Stage 2 filtering, so the judge sees the full candidate list and can't be
asked to choose correctly among options that were already silently dropped.

### price_lookup.py (chat assistant path): Stage 1 only

No LLM-judge planned here — there's no recipe/task context to reason over at
this call site, just a bare search term, so an LLM call would add latency and
nondeterminism without more information to judge with than Stage 1 already
has. Decided to drop Stage 2 here too (not just for recipe matching): on the
same 21-case live test set, Stage 1 alone scored ~19/21 vs. 17/21 with the
Stage 2 margin filter on — the filter was net negative even before factoring
in the LLM-judge question, since it wrongly hid real products (Tomato Sauce
style) as often as it correctly excluded noise.

## Tech Stack

- Python 3, no new language/framework.
- Ollama: `nomic-embed-text` for embeddings (Stage 2, built but unwired),
  existing chat model (`qwen3.8` per project env) for Stage 3's judge calls,
  via the existing `ollama_client.chat_json()`.
- Storage: SQLite (`product_embeddings.db`) for the embedding cache used by
  Stage 2's code, keyed by `(product_name, model)`. MariaDB migration still
  deferred — blocked on Merrimack server DB credentials (Zach/admin).
- No new Python dependencies.

## Commands

```
Test:  python -m unittest discover -s tests -v
Run scraper (produces prices_output.json): python -m scraper.run_scraper
Pull embedding model (once, on the server): ollama pull nomic-embed-text
Live smoke test (Stage 1 + 2, real Ollama, run manually on the server):
    python3 scripts/rag_matching_smoke_test.py
```

## Project Structure

```
scraper/
  product_embeddings.py   → matches_query() (Stage 1, wired in), plus
                             get_embedding()/cosine_similarity()/
                             filter_by_relevance() (Stage 2, built + tested,
                             not called from production code paths)
  product_matcher.py      → NEW (Stage 3): LLM-judge for recipe matching,
                             mirrors product_categorizer.py's pattern
  price_lookup.py         → uses matches_query() only (Stage 1)
  core.py                 → uses matches_query() only (Stage 1);
                             search_products() is called by recipe_scraper.py
  recipe_scraper.py       → MODIFIED: calls product_matcher after
                             search_products() to pick the best candidate
                             with recipe context
  ollama_client.py        → embed() (Stage 2) and existing chat_json() (Stage 3)
tests/
  test_product_embeddings.py → matches_query (Stage 1) + filter_by_relevance
                                (Stage 2) unit tests, all still passing
  test_product_matcher.py    → NEW: unit tests for the Stage 3 judge, mocked
                                chat_json(), following test_product_categorizer.py
scripts/
  rag_matching_smoke_test.py → live test script (real Ollama), checked in for
                                repeat runs since this is tuned against live
                                model output, not just unit-testable
docs/
  intent/rag-product-matching.md     → original intent, unchanged
  specs/rag-product-matching-spec.md → this file
```

## Code Style

`product_matcher.py` should look like `product_categorizer.py` — small pure
functions, module-level `BATCH_SIZE`, a worked example built as real chat
turns (not prose), strict `isinstance`+length+range validation collapsing to
`None`/fallback per-item on any mismatch. Do not invent a new validation
style; copy that one.

## Testing Strategy

- Framework: `python -m unittest discover -s tests`, consistent with the rest
  of the repo (stdlib `unittest`, not pytest — there is no pytest dependency
  in this project).
- `test_product_matcher.py`: mock `ollama_client.chat_json()`, assert the
  batching, worked-example structure, and fallback-on-mismatch behavior the
  same way `test_product_categorizer.py` does for its sibling module.
- Live testing: `scripts/rag_matching_smoke_test.py` is the live-data check
  for Stage 1/2 (kept for regression, even though Stage 2 is unwired, since
  it documents real embedding behavior). Stage 3 will need its own live
  smoke-test additions once built — real recipe context, real candidate
  lists, checked by eye since "did it pick the right product" isn't something
  a fixed assertion can fully capture the way Stage 1's word-boundary logic
  can.

## Boundaries

- **Always do:** run `python -m unittest discover -s tests` before considering
  a task done; keep Stage 3 falling back to current cheapest-of-candidates
  behavior on any LLM output that doesn't validate — never crash, never guess.
- **Ask first:** any change to `prices_output.json`'s schema; adding a new
  Python dependency; the SQLite -> MariaDB migration; anything touching git
  push/pull (per project CLAUDE.md).
- **Never do:** commit DB credentials once MariaDB access exists; touch the
  SSH port / persistent-server / startup-script work (out of scope, see
  intent doc); delete `filter_by_relevance`/Stage 2 code outright (kept,
  unwired, may be useful for a future call site with different constraints).

## Success Criteria

- `matches_query()` (Stage 1) continues to pass all lexical live-test cases
  with zero failures.
- `product_matcher.py` (Stage 3), given a recipe's dish name, full ingredient
  list, and a candidate list per ingredient, picks the product a person would
  pick given the same context — verified by hand-reviewed live test cases
  (e.g. "milk" in a savory dish's ingredient list should not become chocolate
  milk), not just unit tests of the plumbing.
- `recipe_scraper.py`'s basket totals reflect the judge's picks, not
  automatically the cheapest Stage-1 candidate.
- All existing tests continue to pass; `price_lookup.py` and `core.py` no
  longer call `filter_by_relevance` in their production code paths.

## Open Questions

1. MariaDB credentials — still blocked on Zach/admin.
2. Stage 3 batching granularity — batch judge calls across ingredients within
   one store (current lean, mirrors `product_categorizer`'s per-scrape
   batching), or across stores for one ingredient? Both are defensible;
   decide once `run_recipe_scraper()`'s loop is actually being edited and the
   real call-count tradeoff is visible.
3. What does the judge do when NO candidate is a good fit (all are junk)? Likely
   `index: null` in the schema, meaning "not found" — same as today's
   `store_result["not_found"]` path. Confirm this matches recipe_scraper's
   existing not-found handling before wiring it in.

## Revision History

- **v1 (initial):** absolute similarity threshold (`0.4`) for Stage 2,
  wired into both call sites. Live testing on the server revealed similarity
  scores cluster in a narrow, non-comparable band across different queries —
  the threshold filtered nothing in practice.
- **v2:** switched to a relative margin (best-in-search minus a constant) plus
  added Stage 1's word-boundary+stemming lexical layer. A 5-case live test
  looked good (4/5 passed). A follow-up 21-case test (user-requested, "extremely
  thorough") found: 1 real stemmer bug (fixed — "Apples" was mis-stemmed to
  "Appl"), and — more importantly — proved mathematically that no single
  margin value can separate all cases correctly (butter needs margin < 0.0206,
  tomato needs margin >= 0.0292, apple juice needs >= 0.1309 — mutually
  incompatible). 17/21 passed, with the 4 failures split between "lets noise
  through" (milk) and, more concerning, "wrongly hides real products" (tomato
  sauce, chicken bouillon broth mix, apple cinnamon juice blend).
- **v3 (this revision):** dropped Stage 2 from both production call sites
  (numeric cutoffs don't work here, proven not assumed). Added Stage 3
  (LLM-judge) for recipe matching only, where real context exists to judge
  with — user's proposal, reasoned as: don't ask a bare number to make a
  judgment call a person would only make with context; and bias retrieval
  toward recall so the judge never has to choose among candidates that were
  already wrongly discarded.
