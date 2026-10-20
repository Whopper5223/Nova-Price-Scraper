import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scraper import ollama_client, price_lookup
from scraper.chat_assistant import _extract_list, _parse_budget, _price_from_saved, _store_basket_totals


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
        # No user reply follows the proposal yet here, so the "surface the last
        # proposal" exception below doesn't apply — nothing to confirm yet.
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
        # Once the user HAS replied to a proposal, that proposal is deliberately
        # surfaced too (see _extract_list) so a bare "yep" has something to confirm —
        # the un-picked alternative appearing in the sent text is now expected, not a
        # leak, since the system prompt instructs the model to only keep what the
        # user's own reply actually confirmed. What actually stops the wrong item
        # from being kept is prompt behavior, covered separately by the live tests
        # in TestExtractListConfirmationAgainstRealModel.
        mock_chat_json.return_value = {"ready": True, "items": ["black beans"], "missing": "", "budget": None}
        history = [
            {"role": "user", "content": "I have 20 dollars i eat vegan and theres 2 of us"},
            {"role": "assistant", "content": "Tacos or pasta primavera — what do you think?"},
            {"role": "user", "content": "let's do the tacos"},
        ]

        _extract_list(history)

        sent_content = mock_chat_json.call_args[0][0][-1]["content"]
        self.assertIn("let's do the tacos", sent_content)
        self.assertIn("Tacos or pasta primavera", sent_content)

    @patch("scraper.chat_assistant.ollama_client.chat_json")
    def test_no_proposal_surfaced_on_the_very_first_turn(self, mock_chat_json):
        # A single user message with nothing before it has no prior assistant turn to
        # possibly be confirming — the proposal block must not appear at all.
        mock_chat_json.return_value = {"ready": False, "items": [], "missing": "", "budget": None}
        _extract_list([{"role": "user", "content": "hello"}])

        sent_content = mock_chat_json.call_args[0][0][-1]["content"]
        self.assertNotIn("most recent suggestion", sent_content)

    @patch("scraper.chat_assistant.ollama_client.chat_json")
    def test_extraction_call_includes_worked_examples_before_the_real_turn(self, mock_chat_json):
        # Locks in the few-shot structure: fixed demo input/output pairs go first, the
        # real request last. Prose rules alone weren't enough to stop the model from
        # copying literal words out of the system prompt (e.g. "milk", "eggs", or the
        # diet word "vegan" itself) into "items" — worked examples fixed it.
        mock_chat_json.return_value = {"ready": False, "items": [], "missing": "", "budget": None}
        _extract_list([{"role": "user", "content": "I have 20 dollars i eat vegan and theres 2 of us"}])

        messages = mock_chat_json.call_args[0][0]
        self.assertEqual(len(messages), 9)
        for i in (1, 3, 5, 7):
            self.assertEqual(messages[i]["role"], "assistant")
        self.assertIn("vegan", messages[-1]["content"])

    @patch("scraper.chat_assistant.ollama_client.chat_json")
    def test_extraction_call_includes_a_bare_greeting_demo(self, mock_chat_json):
        # A plain "hello" with no budget/diet/headcount context was still leaking an
        # invented item (e.g. "rice") into "items" even with the vegan/budget demo
        # above in place — that demo's input always has *some* context to reason
        # about, so it didn't cover the truly-empty-input case. This locks in the
        # second demo added specifically for that gap.
        mock_chat_json.return_value = {"ready": False, "items": [], "missing": "", "budget": None}
        _extract_list([{"role": "user", "content": "hello"}])

        messages = mock_chat_json.call_args[0][0]
        demo2_input, demo2_output = messages[2], messages[3]
        self.assertEqual(demo2_input["role"], "user")
        self.assertIn("hello", demo2_input["content"])
        self.assertEqual(demo2_output["role"], "assistant")
        self.assertIn('"items": []', demo2_output["content"])

    @patch("scraper.chat_assistant.ollama_client.chat_json")
    def test_extraction_call_includes_a_confirmation_demo(self, mock_chat_json):
        # Locks in the pair of demos added for the reported bug: confirming a full
        # proposal with a bare "sounds good" must include its items, and rejecting one
        # must not — the reject demo exists so the model doesn't just learn "always
        # copy the suggestion" from the accept demo alone.
        mock_chat_json.return_value = {"ready": False, "items": [], "missing": "", "budget": None}
        _extract_list([{"role": "user", "content": "hello"}])

        messages = mock_chat_json.call_args[0][0]
        accept_input, accept_output = messages[4], messages[5]
        reject_input, reject_output = messages[6], messages[7]
        self.assertIn("sounds good", accept_input["content"])
        self.assertIn("chicken stir-fry", accept_input["content"])
        self.assertIn('"chicken breast"', accept_output["content"])
        self.assertIn("chicken stir-fry", reject_input["content"])
        self.assertIn('"items": []', reject_output["content"])


class TestExtractListConfirmationAgainstRealModel(unittest.TestCase):
    """
    Live tests against the real local Ollama model (not mocked) — this is exactly the
    class of reasoning (accept vs. reject vs. partial-pick a proposal) that a mocked
    return value can't actually verify, and prose-only instructions have already
    proven unreliable for once in this file (see the "rice" bug). Skipped automatically
    if Ollama isn't reachable, e.g. in CI.
    """

    @classmethod
    def setUpClass(cls):
        if not ollama_client.is_available():
            raise unittest.SkipTest("Ollama not reachable — skipping live model tests.")

    def test_bare_confirmation_of_a_full_proposal_includes_its_items(self):
        history = [
            {"role": "user", "content": "what should i make for dinner tonight, just me"},
            {"role": "assistant", "content": (
                "How about roasted chicken with rice and vegetables? You'd need: "
                "chicken thighs, rice, broccoli, and carrots."
            )},
            {"role": "user", "content": "yep sounds good"},
        ]
        result = _extract_list(history)
        self.assertTrue(result["ready"])
        self.assertIn("chicken thighs", result["items"])
        self.assertIn("rice", result["items"])

    def test_rejecting_a_proposal_does_not_leak_its_items(self):
        history = [
            {"role": "user", "content": "what should i make for dinner tonight, just me"},
            {"role": "assistant", "content": (
                "How about roasted chicken with rice and vegetables? You'd need: "
                "chicken thighs, rice, broccoli, and carrots."
            )},
            {"role": "user", "content": "hmm, no, let's do something else, not sure what yet"},
        ]
        result = _extract_list(history)
        self.assertEqual(result["items"], [])


_CHEESE_FIXTURE = {
    "scraped_at": "2026-01-01T00:00:00+00:00",
    "location": "Test City",
    "stores": [
        {
            "name": "Test Store",
            "url": "https://example.com",
            "product_count": 2,
            "products": [
                # Both genuinely cheese, same department, so department+keyword
                # filtering can't tell them apart -- this is exactly what the
                # Stage 3 judge is for. (Dairy, not bakery -- Cheese/Milk/Bread/
                # Produce/Beverages are the only categories query_prices() keeps,
                # since those are the only ones with real markup data behind them.)
                {"name": "Cheese Dip", "price": 2.50, "unit": None, "department": "dairy"},
                {"name": "Sharp Cheddar Cheese", "price": 4.00, "unit": None, "department": "dairy"},
            ],
        }
    ],
}


class TestPriceFromSavedUsesJudge(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        fixture_path = Path(self._tmpdir.name) / "prices.json"
        fixture_path.write_text(json.dumps(_CHEESE_FIXTURE))
        price_lookup.set_prices_path(fixture_path)

    def tearDown(self):
        price_lookup.set_prices_path(None)
        self._tmpdir.cleanup()

    @patch("scraper.chat_assistant.product_matcher.pick_best_matches")
    def test_judge_picks_the_real_grocery_item_not_the_cheaper_dip(self, mock_pick):
        # "Sharp Cheddar Cheese" is candidate index 1 in cheapest-first order
        # (Cheese Dip at $2.50 comes first) -- the judge says so explicitly
        # here; the real disambiguation judgment is covered by the live test
        # below.
        mock_pick.return_value = [1]

        findings, unresolved = _price_from_saved(["cheese"], max_age_hours=999999, context="dinner")

        self.assertEqual(unresolved, [])
        self.assertEqual(findings["cheese"][0]["name"], "Sharp Cheddar Cheese")
        # Context and the item list both reach the judge, not just the term.
        args, kwargs = mock_pick.call_args
        self.assertEqual(args[0], "dinner")
        self.assertEqual(args[1], ["cheese"])

    @patch("scraper.chat_assistant.product_matcher.pick_best_matches")
    def test_judge_saying_no_good_match_marks_item_unresolved(self, mock_pick):
        mock_pick.return_value = [None]

        findings, unresolved = _price_from_saved(["cheese"], max_age_hours=999999)

        self.assertEqual(findings, {})
        self.assertEqual(unresolved, ["cheese"])

    def test_stale_data_skips_the_judge_call_entirely(self):
        # Staleness is checked before anything else -- no candidates gathered,
        # no judge call made, same as before this change.
        findings, unresolved = _price_from_saved(["cheese"], max_age_hours=0)
        self.assertEqual(findings, {})
        self.assertEqual(unresolved, ["cheese"])


class TestPriceFromSavedJudgeAgainstRealModel(unittest.TestCase):
    """
    Live test against the real judge model (llama3.2) -- the actual
    disambiguation judgment (is this a cheese dip or a real block of cheese)
    is exactly the kind of call a mock can't verify. Skipped if the model
    isn't pulled on this machine (it's a separate, smaller model from the
    main chat model, not guaranteed to be present everywhere this suite
    runs).
    """

    @classmethod
    def setUpClass(cls):
        from scraper.chat_assistant import JUDGE_MODEL
        if not ollama_client.is_available():
            raise unittest.SkipTest("Ollama not reachable — skipping live judge test.")
        base_names = {m.split(":")[0] for m in ollama_client.list_models()}
        if JUDGE_MODEL not in ollama_client.list_models() and JUDGE_MODEL not in base_names:
            raise unittest.SkipTest(f"{JUDGE_MODEL} not pulled — skipping live judge test.")

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        fixture_path = Path(self._tmpdir.name) / "prices.json"
        fixture_path.write_text(json.dumps(_CHEESE_FIXTURE))
        price_lookup.set_prices_path(fixture_path)

    def tearDown(self):
        price_lookup.set_prices_path(None)
        self._tmpdir.cleanup()

    def test_cheese_for_sandwiches_does_not_return_cheese_dip(self):
        findings, unresolved = _price_from_saved(
            ["cheese"], max_age_hours=999999, context="dinner, need cheese for grilled cheese sandwiches",
        )
        self.assertEqual(unresolved, [])
        self.assertEqual(findings["cheese"][0]["name"], "Sharp Cheddar Cheese")


if __name__ == "__main__":
    unittest.main()
