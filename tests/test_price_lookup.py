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
                {"name": "Butter Cookies", "price": 2.50, "unit": None, "department": "bakery"},
                {"name": "Salted Butter", "price": 4.00, "unit": None, "department": "dairy"},
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
                {"name": "Butter Cookies", "price": 1.00, "unit": None, "department": "bakery"},
                {"name": "Salted Butter", "price": 5.00, "unit": None, "department": "dairy"},
            ],
        },
        {
            "name": "Store B",
            "url": "https://b.example.com",
            "product_count": 2,
            "products": [
                {"name": "Butter Cookies", "price": 1.50, "unit": None, "department": "bakery"},
                {"name": "Salted Butter", "price": 3.00, "unit": None, "department": "dairy"},
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

    With Stage 2 gone, a "butter" query lexically (whole-word) matches both
    "Butter Cookies" and "Salted Butter" and now intentionally returns both —
    this is the accepted tradeoff, not a bug. The function itself and its own
    unit tests remain in product_embeddings.py / test_product_embeddings.py
    for a possible future call site.
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
    def test_butter_query_returns_both_lexical_matches_cheapest_first(self, mock_get_embedding, mock_embed):
        results = price_lookup.query_prices("butter", path=self.fixture_path)

        self.assertEqual([r["name"] for r in results], ["Butter Cookies", "Salted Butter"])
        # Nothing left to mock: query_prices() no longer calls into the
        # embedding path at all.
        mock_embed.assert_not_called()
        mock_get_embedding.assert_not_called()

    def test_cheapest_by_store_returns_lexical_matches_at_both_stores(self):
        # Both products lexically match "butter", and at both stores "Butter
        # Cookies" is the cheaper of the two -- so it's the per-store winner
        # at each store (Stage 2 would have dropped it; Stage 1 alone keeps
        # it, per the accepted tradeoff).
        multi_store_path = Path(self._tmpdir.name) / "multi.json"
        multi_store_path.write_text(json.dumps(_MULTI_STORE_FIXTURE))

        results = price_lookup.cheapest_by_store("butter", path=multi_store_path)

        names = {r["name"] for r in results}
        stores = {r["store_name"] for r in results}
        self.assertEqual(names, {"Butter Cookies"})
        self.assertEqual(stores, {"Store A", "Store B"})

    def test_snap_eligibility_and_department_present(self):
        results = price_lookup.query_prices("butter", path=self.fixture_path)

        by_name = {r["name"]: r for r in results}
        self.assertEqual(by_name["Salted Butter"]["department"], "dairy")
        self.assertEqual(by_name["Butter Cookies"]["department"], "bakery")
        self.assertIn("snap_eligible", by_name["Salted Butter"])


if __name__ == "__main__":
    unittest.main()
