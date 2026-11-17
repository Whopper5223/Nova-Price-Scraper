# Plan: Recipe pricing fallbacks (spec: docs/specs/recipe-pricing-fallbacks-spec.md)

Two vertical slices, each shippable and test-first. Order matters: Slice 2 uses Slice 1's `price_source`.

## Slice 1 -- source labels + Instacart-price fallback (ships alone; fixes eggs/bacon/pork)
Dependency order: markup_data (which departments) -> price_lookup (keep + tag rows) -> ranking checks
-> chat_assistant (summary wording) -> UI labels -> API passthrough -> full suite.
Risks: (a) keeping uncategorized rows adds junk candidates to the judge's 8-name window -- ranking already
demotes variants/off-topic; verify with eggs/bacon/pork on real data after deploy. (b) `category` is now
sometimes None -- every consumer already uses `.get("category")`; add tests to prove it.

## Slice 2 -- substitution (after Slice 1 is live and checked)
substitutions.py (one batched model call, validated) -> _price_from_saved step (only when an item has no
database price at any store) -> rows keyed under the original ingredient, `price_source="substitute"`,
`substitute_for` -> totals `substituted_items` -> UI + summary wording. Risk: extra model latency on the
shared GPU; cap at one batched call, only when needed.

## Later (not planned here)
"Different recipe" last resort: unresolved items -> tell the chat model "couldn't find X".

## Checkpoints
After each slice: full suite green, `node --check`, local commit, ask before push, then live check
(<= 3 agents, 1 in Chrome) on real data.
