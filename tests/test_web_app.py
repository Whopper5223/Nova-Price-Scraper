import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from scraper import price_lookup, web_app

_FIXTURE = {
    "scraped_at": datetime.now(timezone.utc).isoformat(),
    "location": "Test City",
    "stores": [
        {
            "name": "Test Store",
            "url": "https://example.com",
            "product_count": 1,
            "products": [
                {"name": "Whole Milk", "price": 3.00, "unit": None, "department": "dairy"},
            ],
        }
    ],
}


class TestApiPriceDedup(unittest.TestCase):
    """POST /api/price must treat case-variant duplicates of the same item as one
    item, not two -- otherwise _store_basket_totals double-counts its price in
    the store total, since it pivots on the item string as a dict key."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        fixture_path = Path(self._tmpdir.name) / "prices.json"
        fixture_path.write_text(json.dumps(_FIXTURE))
        price_lookup.set_prices_path(fixture_path)
        web_app.app.config["TESTING"] = True
        self.client = web_app.app.test_client()

    def tearDown(self):
        price_lookup.set_prices_path(None)
        self._tmpdir.cleanup()

    @patch("scraper.chat_assistant.ollama_client.chat")
    def test_case_variant_duplicate_items_are_not_double_counted(self, mock_chat):
        mock_chat.return_value = "stub summary"

        resp = self.client.post("/api/price", json={"items": ["milk", "Milk"]})

        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(list(data["findings"].keys()), ["milk"])
        store = data["store_totals"][0]
        self.assertEqual(store["items_total"], 1)
        self.assertEqual(store["total"], 3.00)

    @patch("scraper.chat_assistant.ollama_client.chat")
    def test_exact_duplicate_items_are_not_double_counted(self, mock_chat):
        mock_chat.return_value = "stub summary"

        resp = self.client.post("/api/price", json={"items": ["milk", "milk", "milk"]})

        data = resp.get_json()
        store = data["store_totals"][0]
        self.assertEqual(store["items_total"], 1)
        self.assertEqual(store["total"], 3.00)


if __name__ == "__main__":
    unittest.main()
