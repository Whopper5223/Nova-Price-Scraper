# Tasks -- Slice 1 (source labels + Instacart-price fallback)

- [x] T1 markup_data: expose the set of grocery departments the markup code knows
  - Acceptance: `GROCERY_DEPARTMENTS` = direct departments + dairy, baked-goods, breakfast-foods, meat-and-seafood
  - Verify: tests/test_markup_data.py
- [x] T2 price_lookup.query_prices keeps grocery-department rows with no markup category, tags every row
  - Acceptance: eggs (dairy dept) returned with category None, estimated_real_price None, price_source "instacart";
    rows with an estimate get "database"; non-grocery/unknown departments still dropped
  - Verify: tests/test_price_lookup.py, tests/test_markup_data.py (update the old "still dropped" test to the new rule)
- [x] T3 ranking / closest_per_store work with category None rows
  - Acceptance: plain eggs outrank hard-boiled; one row per store; no crash
  - Verify: tests/test_price_lookup.py
- [x] T4 summary wording names the source of each price
  - Acceptance: SUMMARY prompt mentions price_source; plain fallback says "Instacart price" / "our database"
  - Verify: tests/test_chat_assistant.py
- [x] T5 UI shows the source on lead and "Also at" rows
  - Acceptance: database rows say "our database of real prices"; Instacart rows say "Instacart price"
  - Verify: tests/test_frontend.py, node --check
- [x] T6 API passes price_source through /api/price
  - Acceptance: every findings row has price_source in {database, instacart}
  - Verify: tests/test_web_app.py
- [x] T7 full suite + local commit (do not push without asking)
  - Verify: ./.venv/bin/python3 -m unittest discover -s tests

# Tasks -- Slice 2 (substitution)

- [x] T8 scraper/substitutions.py: one batched model call, validated, never raises
  - Verify: tests/test_substitutions.py; probed on llama3.1 and qwen2.5:14b (eggs/bacon/saffron -> no substitute)
- [x] T9 _price_from_saved: substitute only when no store has a database price; rows labelled "substitute"
  with substitute_for / substitute_term; one batched call, none when every item has a database price
  - Verify: tests/test_chat_assistant.py::TestPriceFromSavedSubstitution
- [x] T10 store_totals.substituted_items; summary prompt + plain fallback say "substitute"
- [x] T11 UI: "Substitute for <ingredient>" on lead and "Also at" rows (escaped)
  - Verify: tests/test_frontend.py::TestSubstituteLabels, node --check
