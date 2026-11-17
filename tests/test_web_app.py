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


class TestApiPriceBudgetValidation(unittest.TestCase):
    """POST /api/price should reject a structurally invalid budget with a clear
    400, the same way it already rejects an empty items list -- instead of
    silently accepting a negative number (or a non-numeric value) and leaving
    the only hint that something's wrong buried in LLM-generated prose."""

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

    def test_negative_budget_is_rejected(self):
        resp = self.client.post("/api/price", json={"items": ["milk"], "budget": -5})

        self.assertEqual(resp.status_code, 400)
        self.assertIn("error", resp.get_json())

    def test_non_numeric_budget_is_rejected(self):
        resp = self.client.post("/api/price", json={"items": ["milk"], "budget": "abc"})

        self.assertEqual(resp.status_code, 400)
        self.assertIn("error", resp.get_json())

    @patch("scraper.chat_assistant.ollama_client.chat")
    def test_zero_budget_is_accepted_not_rejected(self, mock_chat):
        # $0 is a real, if extreme, case (someone with no money left) -- not
        # structurally invalid the way a negative number is. Must not 400.
        mock_chat.return_value = "stub summary"

        resp = self.client.post("/api/price", json={"items": ["milk"], "budget": 0})

        self.assertEqual(resp.status_code, 200)

    @patch("scraper.chat_assistant.ollama_client.chat")
    def test_omitted_budget_is_accepted_not_rejected(self, mock_chat):
        mock_chat.return_value = "stub summary"

        resp = self.client.post("/api/price", json={"items": ["milk"]})

        self.assertEqual(resp.status_code, 200)


_TWO_ITEM_FIXTURE = {
    "scraped_at": datetime.now(timezone.utc).isoformat(),
    "location": "Test City",
    "stores": [
        {
            "name": "Test Store",
            "url": "https://example.com",
            "product_count": 2,
            "products": [
                {"name": "Whole Milk", "price": 3.00, "unit": None, "department": "dairy"},
                {"name": "White Bread", "price": 2.00, "unit": None, "department": "baked-goods"},
            ],
        }
    ],
}


class TestApiPriceSummaryRequest(unittest.TestCase):
    """The summary model was handed the conversation's first message as "the
    user's request", so after a chip was removed it still talked about the
    removed item ("Bananas: I don't have a price..."), and an old turn's text
    (an HTML-injection attempt) leaked into an unrelated pricing's summary."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        fixture_path = Path(self._tmpdir.name) / "prices.json"
        fixture_path.write_text(json.dumps(_TWO_ITEM_FIXTURE))
        price_lookup.set_prices_path(fixture_path)
        web_app.app.config["TESTING"] = True
        self.client = web_app.app.test_client()

    def tearDown(self):
        price_lookup.set_prices_path(None)
        self._tmpdir.cleanup()
        web_app.SESSIONS.pop("test-sid", None)

    @patch("scraper.chat_assistant.product_matcher.pick_best_matches")
    @patch("scraper.chat_assistant.ollama_client.chat")
    def test_summary_is_told_the_priced_list_not_stale_chat_text(self, mock_chat, mock_pick):
        mock_chat.return_value = "stub summary"
        mock_pick.return_value = [0, 0]
        with self.client.session_transaction() as sess:
            sess["sid"] = "test-sid"
        web_app.SESSIONS["test-sid"] = {
            "history": [{"role": "user", "content": "I need milk, bread and bananas <script>x</script>"}],
            "budget": None,
        }

        resp = self.client.post("/api/price", json={"items": ["milk", "bread"]})

        self.assertEqual(resp.status_code, 200)
        sent = json.loads(mock_chat.call_args[0][0][0]["content"])
        self.assertNotIn("bananas", sent["request"])
        self.assertNotIn("script", sent["request"])
        self.assertIn("milk", sent["request"])
        self.assertIn("bread", sent["request"])


class TestApiPriceInputValidation(unittest.TestCase):
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

    def test_items_given_as_a_string_is_rejected(self):
        # Iterating the string priced the single letters m, i, l, k.
        resp = self.client.post("/api/price", json={"items": "milk"})
        self.assertEqual(resp.status_code, 400)
        self.assertIn("error", resp.get_json())

    def test_items_containing_non_strings_is_rejected(self):
        # [1, 2, 3] was coerced to "1", "2", "3" and fuzzy-matched real products.
        resp = self.client.post("/api/price", json={"items": [1, 2, 3]})
        self.assertEqual(resp.status_code, 400)
        self.assertIn("error", resp.get_json())

    def test_items_set_to_null_is_rejected_not_a_server_error(self):
        resp = self.client.post("/api/price", json={"items": None})
        self.assertEqual(resp.status_code, 400)

    def test_a_body_that_is_not_json_says_so(self):
        resp = self.client.post("/api/price", data="not json", content_type="application/json")
        self.assertEqual(resp.status_code, 400)
        self.assertIn("JSON", resp.get_json()["error"])

    def test_a_json_body_that_is_not_an_object_says_so(self):
        resp = self.client.post("/api/price", json=["milk"])
        self.assertEqual(resp.status_code, 400)
        self.assertIn("JSON", resp.get_json()["error"])


class TestConversationSurvivesPricing(unittest.TestCase):
    """Pricing used to wipe the conversation. The chat on screen still showed it, so
    a follow-up like "I have 20 bucks" or "for the same thing" reached a model that
    remembered nothing and asked what you were making."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        fixture_path = Path(self._tmpdir.name) / "prices.json"
        fixture_path.write_text(json.dumps(_FIXTURE))
        price_lookup.set_prices_path(fixture_path)
        web_app.app.config["TESTING"] = True
        self.client = web_app.app.test_client()
        with self.client.session_transaction() as sess:
            sess["sid"] = "keep-sid"
        self.history = [
            {"role": "user", "content": "Just something with meat for dinner tonight"},
            {"role": "assistant", "content": "Grilled chicken with sweet potatoes and broccoli?"},
        ]
        web_app.SESSIONS["keep-sid"] = {"history": list(self.history), "budget": 20.0}

    def tearDown(self):
        price_lookup.set_prices_path(None)
        self._tmpdir.cleanup()
        web_app.SESSIONS.pop("keep-sid", None)

    @patch("scraper.chat_assistant.product_matcher.pick_best_matches")
    @patch("scraper.chat_assistant.ollama_client.chat")
    def test_the_conversation_and_budget_are_still_there_after_pricing(self, mock_chat, mock_pick):
        mock_chat.return_value = "stub summary"
        mock_pick.return_value = [0]

        resp = self.client.post("/api/price", json={"items": ["milk"]})

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(web_app.SESSIONS["keep-sid"]["history"], self.history)
        self.assertEqual(web_app.SESSIONS["keep-sid"]["budget"], 20.0)

    def test_start_over_still_clears_the_conversation(self):
        resp = self.client.post("/api/reset")

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(web_app.SESSIONS["keep-sid"]["history"], [])
        self.assertIsNone(web_app.SESSIONS["keep-sid"]["budget"])


class TestAmountOnlyFollowUpReusesTheLastPricedList(unittest.TestCase):
    """After pricing, "I have 20 bucks" should offer the same list again with the budget.
    The list extractor only sees the user's words, so it often finds no items in a bare
    follow-up (caught live) and the confirm panel never came back."""

    def setUp(self):
        web_app.app.config["TESTING"] = True
        self.client = web_app.app.test_client()
        with self.client.session_transaction() as sess:
            sess["sid"] = "carry-sid"
        web_app.SESSIONS["carry-sid"] = {
            "history": [{"role": "user", "content": "meat for dinner"}],
            "budget": None,
            "last_items": ["salmon fillet", "broccoli"],
        }

    def tearDown(self):
        web_app.SESSIONS.pop("carry-sid", None)

    def _chat(self, message, extracted):
        with patch("scraper.web_app.ollama_client.chat", return_value="Got it."), \
             patch("scraper.web_app._extract_list", return_value=extracted):
            return self.client.post("/api/chat", json={"message": message}).get_json()

    def test_a_stated_amount_with_no_items_brings_back_the_priced_list(self):
        data = self._chat("I have 20 bucks", {"ready": False, "items": [], "missing": "nothing named", "budget": 20.0})

        self.assertTrue(data["ready"])
        self.assertEqual(data["items"], ["salmon fillet", "broccoli"])
        self.assertEqual(data["missing"], "")
        self.assertEqual(data["budget"], 20.0)

    def test_a_message_without_an_amount_does_not_resurrect_the_old_list(self):
        data = self._chat("what about dessert?", {"ready": False, "items": [], "missing": "x", "budget": None})

        self.assertFalse(data["ready"])
        self.assertEqual(data["items"], [])

    def test_items_the_extractor_did_find_are_left_alone(self):
        data = self._chat("I have 20 bucks and want tacos",
                          {"ready": True, "items": ["tortillas"], "missing": "", "budget": 20.0})

        self.assertEqual(data["items"], ["tortillas"])

    def test_nothing_is_carried_when_no_list_was_priced_yet(self):
        web_app.SESSIONS["carry-sid"].pop("last_items")

        data = self._chat("I have 20 bucks", {"ready": False, "items": [], "missing": "x", "budget": 20.0})

        self.assertEqual(data["items"], [])

    @patch("scraper.chat_assistant.product_matcher.pick_best_matches")
    @patch("scraper.chat_assistant.ollama_client.chat")
    def test_pricing_remembers_the_list_and_start_over_forgets_it(self, mock_chat, mock_pick):
        mock_chat.return_value = "stub"
        mock_pick.return_value = [0]
        tmp = tempfile.TemporaryDirectory()
        path = Path(tmp.name) / "prices.json"
        path.write_text(json.dumps(_FIXTURE))
        price_lookup.set_prices_path(path)
        try:
            self.client.post("/api/price", json={"items": ["milk"]})
            self.assertEqual(web_app.SESSIONS["carry-sid"]["last_items"], ["milk"])

            self.client.post("/api/reset")
            self.assertNotIn("last_items", web_app.SESSIONS["carry-sid"])
        finally:
            price_lookup.set_prices_path(None)
            tmp.cleanup()


_SOURCE_FIXTURE = {
    "scraped_at": datetime.now(timezone.utc).isoformat(),
    "location": "Test City",
    "stores": [{"name": "Shaw's", "url": "https://example.com", "product_count": 2, "products": [
        {"name": "Whole Milk", "price": 3.00, "unit": None, "department": "dairy"},
        {"name": "Large Eggs", "price": 4.00, "unit": None, "department": "dairy"},
    ]}],
}


class TestApiPriceReportsTheSourceOfEachPrice(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        path = Path(self._tmpdir.name) / "prices.json"
        path.write_text(json.dumps(_SOURCE_FIXTURE))
        price_lookup.set_prices_path(path)
        web_app.app.config["TESTING"] = True
        self.client = web_app.app.test_client()

    def tearDown(self):
        price_lookup.set_prices_path(None)
        self._tmpdir.cleanup()

    @patch("scraper.chat_assistant.product_matcher.pick_best_matches")
    @patch("scraper.chat_assistant.ollama_client.chat")
    def test_eggs_are_priced_not_missing_and_every_row_has_a_source(self, mock_chat, mock_pick):
        mock_chat.return_value = "stub"
        mock_pick.return_value = [0, 0]

        data = self.client.post("/api/price", json={"items": ["milk", "eggs"]}).get_json()

        self.assertEqual(data["missing"], [])
        self.assertEqual(data["findings"]["milk"][0]["price_source"], "database")
        self.assertEqual(data["findings"]["eggs"][0]["price_source"], "instacart")
        self.assertIsNone(data["findings"]["eggs"][0]["estimated_real_price"])


if __name__ == "__main__":
    unittest.main()
