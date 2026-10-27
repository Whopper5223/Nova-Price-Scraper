# Intent: Full-category markup expansion

Confirmed 2026-10-26.

- **Outcome:** `markup_data.py` prices every store × category combination that has a real average-percent-change markup number in the lab's "FINAL_MIX 8_25_26 v2.xlsx" spreadsheet's Pivot table (Google Drive fileId `1Ygqe_X7oCuNL5E6_hylpkeYIuReccql7`) — not just the 5-category subset (Cheese/Milk/Bread/Produce/Beverages) the demo was restricted to.
- **User:** Nova, moving past the demo-specific "only show high-confidence categories" restriction now that the demo itself is over.
- **Why now:** The 5-category, 25+-pooled-sample threshold was a deliberate safety choice for the live demo (never show a price we weren't confident in); that constraint no longer applies — going back to applying everything the sheet actually gives real data for.
- **Success:** A scraped product gets priced if its `department` + keyword classification maps to a category where its store has a real Pivot-table average-markup number (the same "(Instacart − Store) / Store" average already used for the original 5), or it's ALDI, which stays pinned to 0% across every category (Storefront-platform finding, not category-specific). That store's own real number only.
- **Constraint:** Real data only. No pooled cross-store fallback when a store's cell for a category is empty (the old fallback-averaging logic in `get_markup()` is dropped entirely). No minimum sample-size floor — even n=1 counts as real. A category with zero real data anywhere (no store has a number for it) stays unpriced, same as today's categories outside the original 5.
- **Out of scope:** No new stores beyond the current 4 (Stop & Shop, ALDI, Shaw's, Market Basket) — that's all the Pivot table covers. No change to the live-Instacart-fallback-disabled decision, or the SNAP-eligibility tag/narration suppression — this is scoped to `markup_data.py`'s category/store coverage only.

## Implemented 2026-10-26

`markup_data.py` now carries 20 categories (was 5) across Stop & Shop, Shaw's, Market Basket, plus ALDI pinned to 0% for all of them. `get_markup()` is a plain lookup — the pooled fallback is gone. Tests: `tests/test_markup_data.py`.

Note: the "Pivot table" sheet is a live Google Sheets pivot that does NOT materialize in the .xlsx export (its data cells are empty). The numbers were recomputed from the raw `Matches` sheet (group by Store + Category, average the percent-change column), which is the pivot's exact arithmetic — cross-checked against the original 5-category values (exact match) and the author's own saved snapshot in the `OLD Markup Analysis` sheet (exact match, except the one cell below).

Decisions made where the sheet's labels or data didn't line up across stores:
- **Produce:** one category. Shaw's sheet splits Fruit (16.10%, n=7) / Vegetables (4.02%, n=5); kept as their weighted blend (11.06%, n=12), same as before.
- **Meat:** Beef / Chicken / Fish & Seafood, keyword-classified within `meat-and-seafood`. Market Basket only has one combined MEAT/FISH number (n=1, +25.05%), applied to all three at that store. Pork, turkey, lamb, bacon etc. have no data and are dropped.
- **Snacks:** one category over the whole `snacks-and-candy` department. Stop & Shop's number (+35.0%, n=14) comes from chips only but applies to every snack there.
- **Market Basket Pasta & Dry Goods:** −6.39% (n=4), not the raw pivot's +68.47% (n=5). The extra item is Goya Thai Jasmine Rice ($2.99 website vs $13.99 Instacart, +368%), a package-size mismatch the spreadsheet author's own review excluded.
- **Baby:** not a category. The department mixes baby food with diapers/wipes, and only Market Basket has a number (+21.69%, n=4).
- **Store gaps:** a product whose own store has no number for its category is still returned, with `estimated_real_price` None (raw Instacart price shown, no estimate). Only products in a category with no data at ANY store are dropped.
- Store names are matched with zero-width characters stripped, since Instacart renders "Shaw's" as "Shaw​'s".

Known thin data, included per the no-minimum decision: Stop & Shop Beef (n=1), Market Basket Deli and meat (n=1), Shaw's Cereal (n=2), Market Basket Condiments and Bread (n=2).

Open risk to watch: with every category back in play, same-word-different-item ambiguity returns ("butter" vs. peanut butter, "cheese" vs. frozen cheese pizza, "milk" vs. milk chocolate). The Stage 3 judge handles it but only sees 8 cheapest-first candidates per item — needs live checking.
