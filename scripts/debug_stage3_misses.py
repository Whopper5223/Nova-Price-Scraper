"""
One-off diagnostic: investigate the two live-test misses from the Stage 3
40-case battery (scripts/rag_matching_smoke_test.py) -- are they consistent
model behavior or one-off sampling flukes? Runs each case 5x and also asks
the model to explain its reasoning in free text (no JSON constraint).

Run on the server (real qwen3.8, not mocked):
    python3 scripts/debug_stage3_misses.py

Safe to delete once you're done -- this isn't part of the regular test suite.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from scraper import ollama_client, product_matcher as pm

CASES = [
    {
        "label": "Masa harina (expected: None -- corn flour/cornmeal/corn starch are NOT masa harina; "
                 "nixtamalization is what makes masa harina form a dough, plain corn flour won't)",
        "recipe_name": "Homemade Corn Tortillas",
        "all_ingredients": ["Masa harina", "Warm water", "Salt", "Vegetable oil for griddle"],
        "ingredient": "Masa harina",
        "candidates": ["Bob's Red Mill Corn Flour", "Quaker Yellow Corn Meal", "Argo Corn Starch"],
        "expected": None,
    },
    {
        "label": "Butterscotch sugar (expected: brown sugar -- butterscotch is culinarily defined by "
                 "brown sugar's molasses content, distinguishing it from caramel's white sugar)",
        "recipe_name": "Homemade Butterscotch Sauce",
        "all_ingredients": ["sugar", "butter", "heavy cream", "vanilla extract", "salt"],
        "ingredient": "sugar",
        "candidates": ["Domino Granulated Sugar, 4 lb", "Domino Light Brown Sugar, 2 lb",
                       "C&H Powdered Sugar, 2 lb", "Splenda Granulated Sweetener, 9.7 oz"],
        "expected": "Domino Light Brown Sugar, 2 lb",
    },
]


def run_case(case, n_runs=5):
    print(f"\n{'=' * 70}\n{case['label']}\n{'=' * 70}")
    batch = [{"ingredient": case["ingredient"], "candidates": [{"name": n} for n in case["candidates"]]}]

    print(f"\n--- Reproducing the structured judge call {n_runs}x ---\n")
    hits = 0
    for i in range(n_runs):
        picks = pm.pick_best_matches(case["recipe_name"], case["all_ingredients"], batch)
        idx = picks[0]
        got = None if idx is None else (case["candidates"][idx] if isinstance(idx, int) else idx)
        correct = got == case["expected"]
        hits += correct
        print(f"  run {i + 1}: got={got!r}  {'correct' if correct else 'WRONG'}")
    print(f"\n  {hits}/{n_runs} correct")

    print("\n--- Free-text reasoning (no JSON constraint) ---\n")
    messages = [
        {"role": "user", "content": pm._listing(pm._DEMO_RECIPE_NAME, pm._DEMO_ALL_INGREDIENTS, pm._DEMO_BATCH)},
        {"role": "assistant", "content": pm._DEMO_OUTPUT},
        {
            "role": "user",
            "content": pm._listing(case["recipe_name"], case["all_ingredients"], batch)
            + f"\n\nBefore giving the JSON, first explain in plain English, step by step, whether each "
              f"candidate really is the right match for \"{case['ingredient']}\" and why -- then give the JSON.",
        },
    ]
    explanation = ollama_client.chat(messages, system=pm.PICK_BEST_MATCH_SYSTEM_PROMPT)
    print(explanation)


for c in CASES:
    run_case(c)
