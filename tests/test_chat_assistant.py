import unittest
from unittest.mock import patch

from scraper.chat_assistant import _extract_list, _parse_budget, _store_basket_totals


class TestParseBudget(unittest.TestCase):
    def test_parses_plain_number(self):
        self.assertEqual(_parse_budget(30), 30.0)
        self.assertEqual(_parse_budget(19.999), 20.0)

    def test_none_stays_none(self):
        self.assertIsNone(_parse_budget(None))

    def test_unparseable_value_falls_back_to_none(self):
        self.assertIsNone(_parse_budget("not a number"))
        self.assertIsNone(_parse_budget([]))
        self.assertIsNone(_parse_budget({}))

    def test_numeric_string_is_parsed(self):
        self.assertEqual(_parse_budget("25.5"), 25.5)


class TestStoreBasketTotals(unittest.TestCase):
    def test_one_store_covers_everything_and_is_cheapest(self):
        findings = {
            "chicken": [
                {"store_name": "ALDI", "price": 3.49},
                {"store_name": "Target", "price": 4.49},
            ],
            "rice": [
                {"store_name": "ALDI", "price": 1.89},
                {"store_name": "Target", "price": 2.99},
            ],
        }
        totals = _store_basket_totals(findings, ["chicken", "rice"])

        self.assertEqual(totals[0]["store_name"], "ALDI")
        self.assertTrue(totals[0]["covers_all_items"])
        self.assertEqual(totals[0]["total"], 5.38)
        self.assertEqual(totals[0]["missing_items"], [])

        target = next(s for s in totals if s["store_name"] == "Target")
        self.assertTrue(target["covers_all_items"])
        self.assertEqual(target["total"], 7.48)

    def test_no_store_covers_everything_ranks_by_coverage_then_price(self):
        findings = {
            "chicken": [{"store_name": "ALDI", "price": 3.49}],
            "orange juice": [{"store_name": "Market Basket", "price": 3.29}],
        }
        totals = _store_basket_totals(findings, ["chicken", "orange juice"])

        self.assertTrue(all(not s["covers_all_items"] for s in totals))
        # Neither store covers both items, so ranking falls back to whichever
        # partial total is lowest — here ALDI's single item ($3.49) beats
        # Market Basket's single item ($3.29)? No — price still decides among
        # equal (non-)coverage, so the cheaper total sorts first regardless of
        # which item it covers.
        self.assertEqual(totals[0]["store_name"], "Market Basket")
        self.assertEqual(totals[0]["missing_items"], ["chicken"])

    def test_empty_findings_returns_empty_totals(self):
        self.assertEqual(_store_basket_totals({}, ["chicken"]), [])

    def test_covering_store_always_ranks_above_noncovering_even_if_pricier(self):
        findings = {
            "chicken": [
                {"store_name": "Cheap Partial", "price": 1.00},
                {"store_name": "Full Coverage", "price": 3.49},
            ],
            "rice": [
                {"store_name": "Full Coverage", "price": 1.89},
            ],
        }
        totals = _store_basket_totals(findings, ["chicken", "rice"])

        self.assertEqual(totals[0]["store_name"], "Full Coverage")
        self.assertTrue(totals[0]["covers_all_items"])


class TestExtractListOnlySendsUserText(unittest.TestCase):
    @patch("scraper.chat_assistant.ollama_client.chat_json")
    def test_assistant_suggested_dishes_are_not_sent_to_the_extractor(self, mock_chat_json):
        # Regression test for a bug where the assistant offering two alternative
        # meals ("black bean tacos, or pasta primavera — what do you think?") got
        # extracted as a combined 9-item list before the user picked either one.
        # The fix restricts the extraction call to user-authored text only, so the
        # assistant's suggestions can never leak into "items" un-confirmed.
        mock_chat_json.return_value = {"ready": False, "items": [], "missing": "", "budget": None}
        history = [
            {"role": "user", "content": "I have 20 dollars i eat vegan and theres 2 of us"},
            {"role": "assistant", "content": (
                "Let's get started on a list then. For black bean tacos, we'll need: "
                "black beans, tortillas... Or for pasta primavera, we'd need: pasta, "
                "marinara sauce... What do you think?"
            )},
        ]

        _extract_list(history)

        sent_content = mock_chat_json.call_args[0][0][-1]["content"]
        self.assertNotIn("black bean tacos", sent_content)
        self.assertNotIn("pasta primavera", sent_content)
        self.assertIn("vegan", sent_content)

    @patch("scraper.chat_assistant.ollama_client.chat_json")
    def test_user_confirmed_dish_still_reaches_the_extractor(self, mock_chat_json):
        mock_chat_json.return_value = {"ready": True, "items": ["black beans"], "missing": "", "budget": None}
        history = [
            {"role": "user", "content": "I have 20 dollars i eat vegan and theres 2 of us"},
            {"role": "assistant", "content": "Tacos or pasta primavera — what do you think?"},
            {"role": "user", "content": "let's do the tacos"},
        ]

        _extract_list(history)

        sent_content = mock_chat_json.call_args[0][0][-1]["content"]
        self.assertIn("let's do the tacos", sent_content)
        self.assertNotIn("pasta primavera", sent_content)

    @patch("scraper.chat_assistant.ollama_client.chat_json")
    def test_extraction_call_includes_a_worked_example_before_the_real_turn(self, mock_chat_json):
        # Locks in the few-shot structure: a fixed demo input/output pair goes first,
        # the real request last. Prose rules alone weren't enough to stop the model
        # from copying literal words out of the system prompt (e.g. "milk", "eggs",
        # or the diet word "vegan" itself) into "items" — a worked example fixed it.
        mock_chat_json.return_value = {"ready": False, "items": [], "missing": "", "budget": None}
        _extract_list([{"role": "user", "content": "I have 20 dollars i eat vegan and theres 2 of us"}])

        messages = mock_chat_json.call_args[0][0]
        self.assertEqual(len(messages), 3)
        self.assertEqual(messages[1]["role"], "assistant")
        self.assertIn("vegan", messages[-1]["content"])


if __name__ == "__main__":
    unittest.main()
