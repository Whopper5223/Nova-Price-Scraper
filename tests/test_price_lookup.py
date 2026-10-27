import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scraper import price_lookup


_FIXTURE = {
    "scraped_at": "2026-01-01T00:00:00+00:00",
    "location": "Test City",
    "stores": [
        {
            "name": "Test Store",
            "url": "https://example.com",
            "product_count": 2,
            "products": [
                # Both dairy, both genuinely match "cheese" lexically, and
                # both pass markup_data.classify() into the Cheese category --
                # exercises lexical-only matching without the category filter
                # (which only looks at department + keyword, not "is this
                # really a block of cheese") getting in the way.
                {"name": "Cheese Dip", "price": 2.50, "unit": None, "department": "dairy"},
                {"name": "Sharp Cheddar Cheese", "price": 4.00, "unit": None, "department": "dairy"},
            ],
        }
    ],
}

_MULTI_STORE_FIXTURE = {
    "scraped_at": "2026-01-01T00:00:00+00:00",
    "location": "Test City",
    "stores": [
        {
            "name": "Store A",
            "url": "https://a.example.com",
            "product_count": 2,
            "products": [
                {"name": "Cheese Dip", "price": 1.00, "unit": None, "department": "dairy"},
                {"name": "Sharp Cheddar Cheese", "price": 5.00, "unit": None, "department": "dairy"},
            ],
        },
        {
            "name": "Store B",
            "url": "https://b.example.com",
            "product_count": 2,
            "products": [
                {"name": "Cheese Dip", "price": 1.50, "unit": None, "department": "dairy"},
                {"name": "Sharp Cheddar Cheese", "price": 3.00, "unit": None, "department": "dairy"},
            ],
        },
    ],
}


class TestQueryPricesLexicalOnly(unittest.TestCase):
    """Stage 2 (product_embeddings.filter_by_relevance, a semantic embedding
    filter on top of lexical matching) has been unwired from query_prices()
    and cheapest_by_store() per docs/specs/rag-product-matching-spec.md —
    proven unreliable via live testing (Stage 1 alone scored ~19/21 vs Stage
    2's net-negative 17/21 on the project's live test set).

    With Stage 2 gone, a "cheese" query lexically (whole-word) matches both
    "Cheese Dip" and "Sharp Cheddar Cheese" and now intentionally returns
    both — this is the accepted tradeoff, not a bug. The function itself and
    its own unit tests remain in product_embeddings.py /
    test_product_embeddings.py for a possible future call site.
    """

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        fixture_path = Path(self._tmpdir.name) / "prices.json"
        fixture_path.write_text(json.dumps(_FIXTURE))
        self.fixture_path = fixture_path

    def tearDown(self):
        self._tmpdir.cleanup()

    @patch("scraper.product_embeddings.ollama_client.embed")
    @patch("scraper.product_embeddings.get_embedding")
    def test_cheese_query_returns_both_lexical_matches_cheapest_first(self, mock_get_embedding, mock_embed):
        results = price_lookup.query_prices("cheese", path=self.fixture_path)

        self.assertEqual([r["name"] for r in results], ["Cheese Dip", "Sharp Cheddar Cheese"])
        # Nothing left to mock: query_prices() no longer calls into the
        # embedding path at all.
        mock_embed.assert_not_called()
        mock_get_embedding.assert_not_called()

    def test_cheapest_by_store_returns_lexical_matches_at_both_stores(self):
        # Both products lexically match "cheese", and at both stores "Cheese
        # Dip" is the cheaper of the two -- so it's the per-store winner at
        # each store (Stage 2 would have dropped it; Stage 1 alone keeps it,
        # per the accepted tradeoff).
        multi_store_path = Path(self._tmpdir.name) / "multi.json"
        multi_store_path.write_text(json.dumps(_MULTI_STORE_FIXTURE))

        results = price_lookup.cheapest_by_store("cheese", path=multi_store_path)

        names = {r["name"] for r in results}
        stores = {r["store_name"] for r in results}
        self.assertEqual(names, {"Cheese Dip"})
        self.assertEqual(stores, {"Store A", "Store B"})

    def test_snap_eligibility_and_department_present(self):
        results = price_lookup.query_prices("cheese", path=self.fixture_path)

        by_name = {r["name"]: r for r in results}
        self.assertEqual(by_name["Sharp Cheddar Cheese"]["department"], "dairy")
        self.assertEqual(by_name["Cheese Dip"]["department"], "dairy")
        self.assertIn("snap_eligible", by_name["Sharp Cheddar Cheese"])


_NON_GROCERY_FIXTURE = {
    "scraped_at": "2026-01-01T00:00:00+00:00",
    "location": "Test City",
    "stores": [
        {
            "name": "Test Store",
            "url": "https://example.com",
            "product_count": 4,
            "products": [
                # Real bug, found live: a bare "milk"/"cheese" query lexically
                # matches non-grocery products that reuse the word as flavor
                # branding, and (being cheap) wins over the real grocery item.
                {"name": "CareOne Liquid Hand Soap Milk & Honey Pump", "price": 1.39,
                 "unit": None, "department": "personal-care"},
                {"name": "Nature's Promise Whole Milk", "price": 3.99,
                 "unit": None, "department": "dairy"},
                {"name": "Purina Friskies Wet Cat Food, Turkey & Cheese Dinner", "price": 0.99,
                 "unit": None, "department": "cat"},
                {"name": "Store Brand Cheddar Cheese Block", "price": 4.49,
                 "unit": None, "department": "dairy"},
            ],
        }
    ],
}


class TestNonGroceryDepartmentsExcluded(unittest.TestCase):
    """Regression test for the hand-soap-as-milk / cat-food-as-cheese bug."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        fixture_path = Path(self._tmpdir.name) / "prices.json"
        fixture_path.write_text(json.dumps(_NON_GROCERY_FIXTURE))
        self.fixture_path = fixture_path

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_milk_query_does_not_match_hand_soap(self):
        results = price_lookup.query_prices("milk", path=self.fixture_path)
        names = [r["name"] for r in results]
        self.assertNotIn("CareOne Liquid Hand Soap Milk & Honey Pump", names)
        self.assertIn("Nature's Promise Whole Milk", names)

    def test_cheese_query_does_not_match_cat_food(self):
        results = price_lookup.query_prices("cheese", path=self.fixture_path)
        names = [r["name"] for r in results]
        self.assertNotIn("Purina Friskies Wet Cat Food, Turkey & Cheese Dinner", names)
        self.assertIn("Store Brand Cheddar Cheese Block", names)

    def test_cheapest_by_store_returns_the_real_grocery_item(self):
        # Before the fix, this would have returned the $1.39 hand soap --
        # cheaper, but not milk.
        results = price_lookup.cheapest_by_store("milk", path=self.fixture_path)
        self.assertEqual([r["name"] for r in results], ["Nature's Promise Whole Milk"])



def _row(name, price, category=None, store="Test Store"):
    return {"store_name": store, "name": name, "price": price, "category": category}


class TestHeadMatches(unittest.TestCase):
    """Does the search phrase end the product's name -- is it the thing being
    sold, rather than an ingredient or flavor mentioned along the way?"""

    def test_phrase_at_the_end_of_the_name_matches(self):
        self.assertTrue(price_lookup.head_matches("bread", "L'oven Fresh White Bread"))
        self.assertTrue(price_lookup.head_matches("milk", "Friendly Farms 2% Milk"))
        self.assertTrue(price_lookup.head_matches("orange juice", "Store Brand Orange Juice, Original"))

    def test_size_and_count_suffixes_are_ignored(self):
        self.assertTrue(price_lookup.head_matches("bananas", "Bananas, per lb"))
        self.assertTrue(price_lookup.head_matches("rice", "Long Grain White Rice 2 lb"))
        self.assertTrue(price_lookup.head_matches("bread", "Sandwich Bread - 24 oz"))
        self.assertTrue(price_lookup.head_matches("yogurt", "Vanilla Greek Yogurt (32 oz)"))

    def test_word_followed_by_another_noun_does_not_match(self):
        self.assertFalse(price_lookup.head_matches("bread", "Italian Style Bread Crumbs"))
        self.assertFalse(price_lookup.head_matches("milk", "SNICKERS Milk Chocolate Candy Bar"))
        self.assertFalse(price_lookup.head_matches("chips", "Chocolate Chip Chewy Granola Bars"))

    def test_anything_after_a_with_clause_is_ignored(self):
        self.assertFalse(price_lookup.head_matches(
            "cheese", "Chef's Cupboard Potatoes Au Gratin with Cheddar Cheese Sauce"))
        self.assertFalse(price_lookup.head_matches(
            "butter", "Chef's Cupboard Buttery Mashed Potatoes with Real Butter"))

    def test_a_flavor_or_mix_name_is_not_the_plain_item(self):
        self.assertFalse(price_lookup.head_matches(
            "rice", "Earthly Grains Chicken Flavored Rice & Vermicelli Mix"))
        self.assertFalse(price_lookup.head_matches(
            "olive oil", "Earthly Grains Roasted Garlic & Olive Oil Couscous Mix"))


    def test_a_packing_medium_is_not_the_product(self):
        # "Tuna in Olive Oil" is tuna; the oil it's packed in is not what's being sold.
        self.assertFalse(price_lookup.head_matches(
            "olive oil", "Fremont Fish Market Yellowfin Tuna in Olive Oil, Lemon"))
        self.assertFalse(price_lookup.head_matches("olive oil", "Sardines Packed in Olive Oil"))
        self.assertFalse(price_lookup.head_matches("water", "Sliced Peaches in Water"))

    def test_the_product_itself_still_matches_when_it_has_an_in_elsewhere(self):
        self.assertTrue(price_lookup.head_matches("olive oil", "Extra Virgin Olive Oil"))
        self.assertTrue(price_lookup.head_matches("oil", "Cooking Oil, Made in Italy"))


class TestRankCandidates(unittest.TestCase):
    """Rows are ranked best-first for the judge's short candidate list. Plain
    cheapest-first let cheap junk crowd the real item out: with every category in
    play, "cheese" is a $0.65 mac-and-cheese, "milk" a candy bar, "bread" crumbs."""

    def test_the_real_category_beats_a_cheaper_look_alike(self):
        rows = [
            _row("Cheese Club Original Mac & Cheese", 0.65, "Pasta & Dry Goods"),
            _row("Sharp Cheddar Cheese", 4.00, "Cheese"),
        ]
        ranked = price_lookup.rank_candidates("cheese", rows)
        self.assertEqual(ranked[0]["name"], "Sharp Cheddar Cheese")

    def test_milk_ranks_real_milk_above_candy_and_evaporated_milk(self):
        rows = [
            _row("SNICKERS Milk Chocolate Candy Bar", 0.99, "Snacks"),
            _row("Baker's Corner Evaporated Milk", 1.05, "Baking Essentials"),
            _row("Friendly Farms Whole Milk", 2.37, "Milk"),
        ]
        ranked = price_lookup.rank_candidates("milk", rows)
        self.assertEqual(ranked[0]["name"], "Friendly Farms Whole Milk")

    def test_bread_ranks_bread_above_bread_crumbs(self):
        rows = [
            _row("Italian Style Bread Crumbs", 1.29, "Oils & Spices"),
            _row("White Sandwich Bread", 1.99, "Bread"),
        ]
        ranked = price_lookup.rank_candidates("bread", rows)
        self.assertEqual(ranked[0]["name"], "White Sandwich Bread")

    def test_without_a_category_hint_the_name_still_separates_the_item_from_a_mix(self):
        rows = [
            _row("Roasted Garlic & Olive Oil Couscous Mix", 1.99, "Pasta & Dry Goods"),
            _row("Extra Virgin Olive Oil", 5.99, "Oils & Spices"),
        ]
        ranked = price_lookup.rank_candidates("olive oil", rows)
        self.assertEqual(ranked[0]["name"], "Extra Virgin Olive Oil")

    def test_olive_oil_ranks_a_bottle_of_oil_above_cheaper_tuna_packed_in_it(self):
        rows = [
            _row("Fremont Fish Market Yellowfin Tuna in Olive Oil, Lemon", 2.75, "Canned Goods"),
            _row("Extra Virgin Olive Oil", 7.99, "Oils & Spices"),
        ]
        ranked = price_lookup.rank_candidates("olive oil", rows)
        self.assertEqual(ranked[0]["name"], "Extra Virgin Olive Oil")

    def test_chips_ranks_potato_chips_above_chocolate_chip_bars(self):
        rows = [
            _row("Chocolate Chip Chewy Dipped Granola Bars", 1.89, "Snacks"),
            _row("Kettle Potato Chips", 2.99, "Snacks"),
        ]
        ranked = price_lookup.rank_candidates("chips", rows)
        self.assertEqual(ranked[0]["name"], "Kettle Potato Chips")

    def test_price_still_orders_rows_within_the_same_tier(self):
        rows = [
            _row("Sharp Cheddar Cheese", 4.00, "Cheese"),
            _row("Mild Swiss Cheese", 2.50, "Cheese"),
        ]
        ranked = price_lookup.rank_candidates("cheese", rows)
        self.assertEqual([r["name"] for r in ranked], ["Mild Swiss Cheese", "Sharp Cheddar Cheese"])

    def test_with_no_signal_at_all_it_is_plain_cheapest_first(self):
        rows = [_row("Zeta Widget Stuff", 3.00, "Frozen"), _row("Alpha Widget Stuff", 1.00, "Frozen")]
        ranked = price_lookup.rank_candidates("widget", rows)
        self.assertEqual([r["price"] for r in ranked], [1.00, 3.00])

    def test_no_rows_are_dropped_or_added(self):
        rows = [
            _row("Cheese Club Original Mac & Cheese", 0.65, "Pasta & Dry Goods"),
            _row("Sharp Cheddar Cheese", 4.00, "Cheese"),
            _row("Cheese Pizza", 3.00, "Frozen"),
        ]
        ranked = price_lookup.rank_candidates("cheese", rows)
        self.assertEqual(sorted(r["name"] for r in ranked), sorted(r["name"] for r in rows))


class TestCheapestPerStoreUsesTheEstimatedPrice(unittest.TestCase):
    def test_stores_are_ordered_by_estimated_in_store_price(self):
        rows = [
            {"store_name": "Stop & Shop", "name": "Whole Milk", "price": 3.00, "estimated_real_price": 2.00},
            {"store_name": "ALDI", "name": "Whole Milk", "price": 2.50, "estimated_real_price": 2.50},
        ]
        ordered = price_lookup.cheapest_per_store(rows)
        self.assertEqual([r["store_name"] for r in ordered], ["Stop & Shop", "ALDI"])

    def test_effective_price_prefers_the_estimate_and_falls_back_to_the_listed_price(self):
        self.assertEqual(price_lookup.effective_price({"price": 3.16, "estimated_real_price": 2.37}), 2.37)
        self.assertEqual(price_lookup.effective_price({"price": 1.29, "estimated_real_price": None}), 1.29)
        self.assertEqual(price_lookup.effective_price({"price": 1.89}), 1.89)


class TestRankCandidatesPrefersThePlainProduct(unittest.TestCase):
    """For a bare word like butter or rice the plain product should lead, even when a
    flavored, seasoned or frozen version is cheaper. Variants are demoted, not dropped."""

    def test_butter_ranks_plain_butter_above_cheaper_garlic_parmesan_butter(self):
        rows = [
            _row("Specially Selected Garlic Parmesan Butter", 2.75, "Butter & Creamer"),
            _row("Kates Butter Homemade Unsalted Butter", 2.99, "Butter & Creamer"),
        ]
        ranked = price_lookup.rank_candidates("butter", rows)
        self.assertEqual(ranked[0]["name"], "Kates Butter Homemade Unsalted Butter")

    def test_rice_ranks_plain_rice_above_cheaper_cheddar_broccoli_rice(self):
        rows = [
            _row("Earthly Grains Cheddar Broccoli Rice", 1.09, "Pasta & Dry Goods"),
            _row("Long Grain White Rice", 2.49, "Pasta & Dry Goods"),
        ]
        ranked = price_lookup.rank_candidates("rice", rows)
        self.assertEqual(ranked[0]["name"], "Long Grain White Rice")

    def test_orange_juice_ranks_bottled_juice_above_cheaper_frozen_concentrate(self):
        rows = [
            _row("Market Basket Frozen Concentrated Orange Juice", 1.99, "Frozen"),
            _row("Tropicana Orange Juice", 3.49, "Beverages"),
        ]
        ranked = price_lookup.rank_candidates("orange juice", rows)
        self.assertEqual(ranked[0]["name"], "Tropicana Orange Juice")

    def test_plain_milk_ranks_above_cheaper_almond_milk_and_chocolate_milk(self):
        rows = [
            _row("Silk Unsweetened Almond Milk", 1.99, "Milk"),
            _row("Hershey's Chocolate Milk", 2.19, "Milk"),
            _row("Friendly Farms Whole Milk", 2.89, "Milk"),
        ]
        ranked = price_lookup.rank_candidates("milk", rows)
        self.assertEqual(ranked[0]["name"], "Friendly Farms Whole Milk")

    def test_a_flavor_the_shopper_asked_for_is_not_held_against_the_product(self):
        rows = [
            _row("Hershey's Chocolate Almond Milk", 1.99, "Milk"),
            _row("Hershey's Chocolate Milk", 2.49, "Milk"),
        ]
        ranked = price_lookup.rank_candidates("chocolate milk", rows)
        # "chocolate" was asked for, so only "almond" counts against the first row.
        self.assertEqual(ranked[0]["name"], "Hershey's Chocolate Milk")

    def test_a_real_cheese_is_not_demoted_for_naming_a_kind_of_cheese(self):
        rows = [
            _row("Sharp Cheddar Cheese", 4.00, "Cheese"),
            _row("Mild Swiss Cheese", 2.50, "Cheese"),
        ]
        ranked = price_lookup.rank_candidates("cheese", rows)
        self.assertEqual([r["name"] for r in ranked], ["Mild Swiss Cheese", "Sharp Cheddar Cheese"])

    def test_variants_are_still_in_the_list_after_the_plain_products(self):
        rows = [
            _row("Garlic Parmesan Butter", 2.75, "Butter & Creamer"),
            _row("Salted Butter", 3.99, "Butter & Creamer"),
        ]
        ranked = price_lookup.rank_candidates("butter", rows)
        self.assertEqual([r["name"] for r in ranked], ["Salted Butter", "Garlic Parmesan Butter"])

    def test_ordering_within_plain_products_is_by_estimated_in_store_price(self):
        rows = [
            {**_row("Brand A Whole Milk", 3.00, "Milk"), "estimated_real_price": 2.00},
            {**_row("Brand B Whole Milk", 2.50, "Milk"), "estimated_real_price": 2.50},
        ]
        ranked = price_lookup.rank_candidates("milk", rows)
        self.assertEqual(ranked[0]["name"], "Brand A Whole Milk")


class TestClosestPerStore(unittest.TestCase):
    """The judge picks ONE product for an item; every other store should then show
    its own closest equivalent, so the stores can actually be compared."""

    def _milk_rows(self):
        return [
            _row("Friendly Farms Vitamin D Whole Milk", 1.59, "Milk", store="ALDI"),
            _row("Store Brand 2% Reduced Fat Milk", 2.10, "Milk", store="Stop & Shop"),
            _row("Store Brand Chocolate Milk", 1.50, "Milk", store="Stop & Shop"),
            _row("Store Brand Whole Milk", 3.16, "Milk", store="Stop & Shop"),
            _row("SNICKERS Milk Chocolate Candy Bar", 0.99, "Snacks", store="Stop & Shop"),
            _row("Shaw's 1% Lowfat Milk", 2.40, "Milk", store="Shaw's"),
        ]

    def test_each_store_gets_exactly_one_row_for_the_item(self):
        rows = self._milk_rows()
        chosen = rows[0]

        picked = price_lookup.closest_per_store("milk", chosen, rows)

        stores = [r["store_name"] for r in picked]
        self.assertEqual(sorted(stores), sorted(set(stores)))

    def test_another_store_shows_the_version_most_like_the_chosen_product(self):
        rows = self._milk_rows()

        picked = price_lookup.closest_per_store("milk", rows[0], rows)

        stop_and_shop = next(r for r in picked if r["store_name"] == "Stop & Shop")
        self.assertEqual(stop_and_shop["name"], "Store Brand Whole Milk")

    def test_the_chosen_product_is_kept_for_its_own_store(self):
        rows = self._milk_rows()

        picked = price_lookup.closest_per_store("milk", rows[0], rows)

        aldi = next(r for r in picked if r["store_name"] == "ALDI")
        self.assertEqual(aldi["name"], "Friendly Farms Vitamin D Whole Milk")

    def test_a_store_selling_the_exact_same_product_is_matched_exactly(self):
        rows = [
            _row("Chobani Greek Yogurt", 1.29, "Yogurt", store="Market Basket"),
            _row("Chobani Greek Yogurt", 1.49, "Yogurt", store="Stop & Shop"),
            _row("Store Brand Plain Yogurt", 0.89, "Yogurt", store="Stop & Shop"),
        ]

        picked = price_lookup.closest_per_store("yogurt", rows[0], rows)

        stop_and_shop = next(r for r in picked if r["store_name"] == "Stop & Shop")
        self.assertEqual(stop_and_shop["name"], "Chobani Greek Yogurt")

    def test_other_stores_only_offer_products_from_the_chosen_products_category(self):
        rows = [
            _row("Friendly Farms Whole Milk", 1.59, "Milk", store="ALDI"),
            _row("SNICKERS Milk Chocolate Candy Bar", 0.99, "Snacks", store="Shaw's"),
        ]

        picked = price_lookup.closest_per_store("milk", rows[0], rows)

        self.assertEqual([r["store_name"] for r in picked], ["ALDI"])

    def test_results_are_ordered_cheapest_in_store_first(self):
        rows = [
            {**_row("Store Brand Whole Milk", 3.16, "Milk", store="Stop & Shop"), "estimated_real_price": 2.37},
            {**_row("Friendly Farms Whole Milk", 2.50, "Milk", store="ALDI"), "estimated_real_price": 2.50},
        ]

        picked = price_lookup.closest_per_store("milk", rows[1], rows)

        self.assertEqual([r["store_name"] for r in picked], ["Stop & Shop", "ALDI"])


if __name__ == "__main__":
    unittest.main()
