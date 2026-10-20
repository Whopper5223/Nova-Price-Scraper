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


if __name__ == "__main__":
    unittest.main()
