# Implementation Plan: RAG-based product matching (v3 — LLM-judge)

Spec: `docs/specs/rag-product-matching-spec.md` (see Revision History for why
this plan supersedes the earlier margin-filter approach)

## Overview

Two changes from the current shipped state:
1. **Unwire Stage 2** (`filter_by_relevance`) from `core.py` and
   `price_lookup.py` — proven not to work reliably (see spec). Keep the code
   and its tests, just stop calling it.
2. **Build Stage 3** — a new `product_matcher.py` LLM-judge module, mirroring
   `product_categorizer.py`'s pattern, wired into `recipe_scraper.py`'s
   `run_recipe_scraper()` right after its `search_products()` call.

## Architecture Decisions

- **Recall-biased retrieval, judgment-based selection.** Stage 1 (lexical)
  stays recall-biased (no Stage 2 filtering ahead of the judge) so Stage 3
  never has to choose among candidates that were already wrongly dropped.
- **Context-gated: only where context exists.** `recipe_scraper.py` has dish
  name + full ingredient list in scope at the match point; `price_lookup.py`
  doesn't have comparable context, so it gets Stage 1 only, not a judge call.
- **Fallback discipline matches `product_categorizer.py` exactly**: any
  invalid/mismatched LLM output for an ingredient falls back to today's
  behavior (cheapest of Stage-1 candidates) for that one ingredient — never
  crash, never guess, never let one bad batch response drop other ingredients'
  results.

## Task List

### Phase 1: Unwire Stage 2 (small, low-risk, do first)

- [ ] Task 1: Remove `filter_by_relevance` calls from `core.py` and
      `price_lookup.py`; keep the function + its tests in `product_embeddings.py`

### Checkpoint: Unwire
- [ ] `python -m unittest discover -s tests -v` passes
- [ ] `grep -rn "filter_by_relevance" scraper/core.py scraper/price_lookup.py` returns nothing

### Phase 2: Build Stage 3 judge module

- [ ] Task 2: Create `scraper/product_matcher.py` (batched LLM-judge, mirrors
      `product_categorizer.py`)
- [ ] Task 3: Unit tests for `product_matcher.py` (mocked `chat_json`,
      mirrors `test_product_categorizer.py`)

### Checkpoint: Judge module standalone
- [ ] `python -m unittest tests.test_product_matcher -v` passes
- [ ] Fallback behavior verified for: malformed JSON, wrong-length response,
      out-of-range index, `index: null`

### Phase 3: Wire into recipe_scraper.py

- [ ] Task 4: Call the judge after `search_products()` in
      `run_recipe_scraper()`, using `recipe["name"]` + full ingredient list
      already in scope; update `basket_total` to use the judge's pick instead
      of `res["min"]`

### Checkpoint: Integration
- [ ] `python -m unittest discover -s tests -v` passes (full suite)
- [ ] Live check on the server: run the recipe scraper against a real recipe
      containing "milk" (or another flavor-noise-prone ingredient) and
      confirm the judge picks the plain product, not a flavored variant
- [ ] Human review before considering this feature done

## Risks and Mitigations

| Risk | Impact | Mitigation |
|------|--------|------------|
| LLM judge picks wrong product with high confidence | Medium — worse than today's silent cheapest-price pick in a way that's harder to notice | Fallback validation is strict (index must be in range); live-test with real recipes before trusting basket totals |
| Added latency per recipe (one Ollama call per ingredient batch) | Low-medium — shared GPU, recipe scraping is not real-time-latency-sensitive | Batch across ingredients per store (open question in spec) to keep call count down |
| `index: null` handling doesn't match existing `not_found` path | Low | Confirm against `store_result["not_found"]` handling before wiring in (open question in spec) |

## Open Questions

See spec's Open Questions section — batching granularity and null-handling
need to be settled while editing `run_recipe_scraper()`, not guessed upfront.
