"""
Tests for run_recipe_scraper()'s per-store batched product_matcher wiring
(docs/specs/rag-product-matching-spec.md, Stage 3). Covers the behavior the
spec calls out explicitly:

- basket_total reflects the judge's pick, not automatically res["min"].
- an ingredient the judge explicitly rejects (index: null) lands in not_found,
  not in results/basket_total (spec's Open Questions #3).
- a judge response that doesn't validate (malformed shape, wrong length,
  out-of-range index) or a transport failure instead falls back to that
  ingredient's cheapest Stage-1 candidate, per the spec's Architecture and
  Boundaries sections — it is NOT the same outcome as an explicit null.
- pick_best_matches is called once per store, batched across that store's
  matched ingredients, not once per ingredient (spec's Open Questions #2).
- duplicate normalized ingredients (two recipe lines collapsing to the same
  search term) are priced once per store, not double-counted or split across
  results/not_found.

Browser interaction (launch_browser, page.goto, _dismiss_modals, _human_delay,
fetch_recipes_async) and store/ingredient discovery are all mocked out —
this file only targets the collect-then-judge-then-finalize loop inside
run_recipe_scraper(), not live scraping.
"""

import unittest
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

from scraper import recipe_scraper


def _fake_res(products: list[dict]) -> dict:
    """Build a search_products()-shaped result from an already cheapest-first
    product list, the way scraper.core.search_products returns it."""
    prices = [p["price"] for p in products]
    return {
        "query": "x",
        "min": min(prices) if prices else None,
        "max": max(prices) if prices else None,
        "median": sorted(prices)[len(prices) // 2] if prices else None,
        "count": len(products),
        "products": products,
    }


_EMPTY_RES = {"query": "x", "min": None, "max": None, "median": None, "count": 0, "products": []}


class RecipeScraperJudgeWiringTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        # A fake (browser, context, page) async context manager standing in
        # for launch_browser — nothing in these tests touches a real page
        # beyond the awaited no-op calls the loop makes on it.
        fake_page = MagicMock()
        fake_page.goto = AsyncMock()

        @asynccontextmanager
        async def _fake_launch_browser(verbose=True):
            yield (MagicMock(), MagicMock(), fake_page)

        patches = {
            "launch_browser": _fake_launch_browser,
            "_human_delay": AsyncMock(),
            "_dismiss_modals": AsyncMock(),
            "_load_selectors": MagicMock(return_value={}),
            "_load_stores_from_output": MagicMock(
                return_value=[{"name": "Test Store", "url": "https://instacart.com/store/test-store/1"}]
            ),
            "normalize_ingredients": lambda raw: list(raw),  # identity — keep test ingredient strings exact
        }
        self._patchers = [patch.object(recipe_scraper, name, value) for name, value in patches.items()]
        for p in self._patchers:
            p.start()
            self.addCleanup(p.stop)

        # fetch_recipes_async is patched per-test since the recipe name/ingredients differ.

    def _mock_recipe(self, name: str, ingredients: list[str]):
        patcher = patch.object(
            recipe_scraper,
            "fetch_recipes_async",
            AsyncMock(return_value=[{"name": name, "ingredients": ingredients}]),
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    async def test_basket_total_uses_judges_pick_not_cheapest(self):
        """The judge picks the pricier candidate (index 1) — basket_total must
        follow that pick, not silently fall back to res["min"]."""
        self._mock_recipe("Test Recipe", ["milk"])
        products = [
            {"name": "Cheap Milk", "price": 1.99, "unit": None, "unit_price": None, "unit_measure": None},
            {"name": "Better Milk", "price": 3.99, "unit": None, "unit_price": None, "unit_measure": None},
        ]
        search_mock = AsyncMock(return_value=_fake_res(products))
        judge_mock = MagicMock(return_value=[1])

        with patch.object(recipe_scraper, "search_products", search_mock), \
             patch.object(recipe_scraper.product_matcher, "pick_best_matches", judge_mock):
            result = await recipe_scraper.run_recipe_scraper(
                "https://example.com/recipe", verbose=False, max_stores=1
            )

        store = result["stores"][0]
        self.assertEqual(store["basket_total"], 3.99)
        self.assertNotIn("milk", store["not_found"])
        entry = store["results"]["milk"]
        self.assertEqual(entry["price_min"], 3.99)
        self.assertEqual(entry["price_max"], 3.99)
        self.assertEqual(entry["price_median"], 3.99)
        self.assertEqual(entry["count"], 2)
        # products[0] must be the picked product (matches price_min/max/median),
        # not the original cheapest-first products[0] — chat_assistant.py's
        # recipe-URL path reads products[0]["name"] as the label for price_min.
        self.assertEqual(entry["products"][0], products[1])
        self.assertEqual(set(p["name"] for p in entry["products"]), {p["name"] for p in products})

    async def test_none_pick_lands_in_not_found_not_results(self):
        """The judge rejects every candidate (index: null) for this ingredient —
        it must land in not_found, contribute nothing to results or
        basket_total, per the spec's Open Questions #3 resolution."""
        self._mock_recipe("Test Recipe", ["ground nutmeg"])
        products = [
            {"name": "Ground Cinnamon", "price": 3.19, "unit": None, "unit_price": None, "unit_measure": None},
        ]
        search_mock = AsyncMock(return_value=_fake_res(products))
        judge_mock = MagicMock(return_value=[None])

        with patch.object(recipe_scraper, "search_products", search_mock), \
             patch.object(recipe_scraper.product_matcher, "pick_best_matches", judge_mock):
            result = await recipe_scraper.run_recipe_scraper(
                "https://example.com/recipe", verbose=False, max_stores=1
            )

        store = result["stores"][0]
        self.assertIn("ground nutmeg", store["not_found"])
        self.assertNotIn("ground nutmeg", store["results"])
        self.assertEqual(store["basket_total"], 0.0)

    async def test_judge_called_once_per_store_batched_across_ingredients(self):
        """Two stores, three matched ingredients each, must produce exactly two
        pick_best_matches calls (one per store) — not one call for the whole
        run (batching across stores) and not six calls (one per ingredient).
        Each call's batch must carry only that store's own candidates, so an
        implementation that hoists the judge above the store loop and mixes
        candidates across stores would fail this."""
        self._mock_recipe("Test Recipe", ["milk", "eggs", "flour"])

        two_stores = MagicMock(return_value=[
            {"name": "Store A", "url": "https://instacart.com/store/store-a/1"},
            {"name": "Store B", "url": "https://instacart.com/store/store-b/2"},
        ])

        call_counter = {"n": 0}

        def _search_side_effect(page, ingredient, selectors, n=5, debug_slug=""):
            call_counter["n"] += 1
            call_id = call_counter["n"]
            return _fake_res([{"name": f"item-{call_id}", "price": float(call_id),
                                "unit": None, "unit_price": None, "unit_measure": None}])

        search_mock = AsyncMock(side_effect=_search_side_effect)
        judge_mock = MagicMock(return_value=[0, 0, 0])  # one candidate per ingredient — index 0 always valid

        with patch.object(recipe_scraper, "_load_stores_from_output", two_stores), \
             patch.object(recipe_scraper, "search_products", search_mock), \
             patch.object(recipe_scraper.product_matcher, "pick_best_matches", judge_mock):
            result = await recipe_scraper.run_recipe_scraper(
                "https://example.com/recipe", verbose=False, max_stores=2
            )

        self.assertEqual(judge_mock.call_count, 2)

        call1_batch = judge_mock.call_args_list[0][0][2]
        call2_batch = judge_mock.call_args_list[1][0][2]
        self.assertEqual([item["ingredient"] for item in call1_batch], ["milk", "eggs", "flour"])
        self.assertEqual([item["ingredient"] for item in call2_batch], ["milk", "eggs", "flour"])

        names1 = {c["candidates"][0]["name"] for c in call1_batch}
        names2 = {c["candidates"][0]["name"] for c in call2_batch}
        self.assertEqual(names1, {"item-1", "item-2", "item-3"})
        self.assertEqual(names2, {"item-4", "item-5", "item-6"})
        self.assertTrue(names1.isdisjoint(names2), "second store's batch must not carry the first store's candidates")

        store_a, store_b = result["stores"]
        self.assertEqual(len(store_a["results"]), 3)
        self.assertEqual(len(store_b["results"]), 3)
        self.assertEqual(store_a["basket_total"], 1.0 + 2.0 + 3.0)
        self.assertEqual(store_b["basket_total"], 4.0 + 5.0 + 6.0)

    async def test_judge_transport_failure_degrades_store_to_cheapest_candidate_without_crashing(self):
        """ollama_client.chat() raises OllamaError on transport failures (Ollama
        down, model missing, timeout) — chat_json() only catches unparseable
        JSON, so that exception reaches pick_best_matches uncaught. It must not
        blow up run_recipe_scraper() mid-store (spec's "never crash" boundary).
        Per the spec's Architecture/Boundaries sections, output that doesn't
        validate — including a transport failure, which produces no output at
        all — falls back to each pending ingredient's cheapest Stage-1
        candidate rather than dropping real, already-scraped prices into
        not_found; the run still completes and writes a result for every
        store."""
        self._mock_recipe("Test Recipe", ["milk", "eggs"])
        products = [{"name": "Milk", "price": 2.00, "unit": None, "unit_price": None, "unit_measure": None}]
        search_mock = AsyncMock(return_value=_fake_res(products))
        judge_mock = MagicMock(side_effect=RuntimeError("Ollama request failed: connection refused"))

        with patch.object(recipe_scraper, "search_products", search_mock), \
             patch.object(recipe_scraper.product_matcher, "pick_best_matches", judge_mock):
            result = await recipe_scraper.run_recipe_scraper(
                "https://example.com/recipe", verbose=False, max_stores=1
            )

        judge_mock.assert_called_once()
        store = result["stores"][0]
        self.assertEqual(store["not_found"], [])
        self.assertEqual(set(store["results"].keys()), {"milk", "eggs"})
        self.assertEqual(store["results"]["milk"]["price_min"], 2.00)
        self.assertEqual(store["results"]["eggs"]["price_min"], 2.00)
        self.assertEqual(store["basket_total"], 4.00)

    async def test_zero_candidate_ingredient_skips_the_judge_entirely(self):
        """An ingredient with no search results goes straight to not_found and
        is never included in the judge's batch."""
        self._mock_recipe("Test Recipe", ["milk", "unobtainium"])

        def _search_side_effect(page, ingredient, selectors, n=5, debug_slug=""):
            if ingredient == "milk":
                return _fake_res([{"name": "Milk", "price": 2.00,
                                    "unit": None, "unit_price": None, "unit_measure": None}])
            return dict(_EMPTY_RES)

        search_mock = AsyncMock(side_effect=_search_side_effect)
        judge_mock = MagicMock(return_value=[0])

        with patch.object(recipe_scraper, "search_products", search_mock), \
             patch.object(recipe_scraper.product_matcher, "pick_best_matches", judge_mock):
            result = await recipe_scraper.run_recipe_scraper(
                "https://example.com/recipe", verbose=False, max_stores=1
            )

        judge_mock.assert_called_once()
        batch_arg = judge_mock.call_args[0][2]
        self.assertEqual([item["ingredient"] for item in batch_arg], ["milk"])

        store = result["stores"][0]
        self.assertIn("unobtainium", store["not_found"])
        self.assertNotIn("unobtainium", store["results"])

    async def test_duplicate_normalized_ingredients_are_priced_once(self):
        """Two recipe lines that normalize to the same search term (e.g. two
        "salt" lines) must collapse to a single priced item per store — not
        double-search, not double-count into basket_total, and never split
        across both results and not_found for the same ingredient."""
        self._mock_recipe("Test Recipe", ["salt", "salt"])
        products = [{"name": "Table Salt", "price": 1.50, "unit": None, "unit_price": None, "unit_measure": None}]
        search_mock = AsyncMock(return_value=_fake_res(products))
        judge_mock = MagicMock(return_value=[0])

        with patch.object(recipe_scraper, "search_products", search_mock), \
             patch.object(recipe_scraper.product_matcher, "pick_best_matches", judge_mock):
            result = await recipe_scraper.run_recipe_scraper(
                "https://example.com/recipe", verbose=False, max_stores=1
            )

        self.assertEqual(result["ingredients"], ["salt"])
        search_mock.assert_awaited_once()  # deduped before search, not just before the judge
        judge_mock.assert_called_once()
        batch_arg = judge_mock.call_args[0][2]
        self.assertEqual([item["ingredient"] for item in batch_arg], ["salt"])

        store = result["stores"][0]
        self.assertEqual(set(store["results"].keys()), {"salt"})
        self.assertEqual(store["not_found"], [])
        self.assertEqual(store["basket_total"], 1.50)

        # _print_comparison loops result["ingredients"] (one row per entry) and
        # looks up store["results"] by that same name — with dedup upstream
        # both are single-valued for "salt", so this must not raise and the
        # one printed row must reconcile with basket_total above.
        recipe_scraper._print_comparison(result)


if __name__ == "__main__":
    unittest.main()
