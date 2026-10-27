import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scraper import ollama_client, price_lookup
from scraper.chat_assistant import (
    JUDGE_MODEL, SUMMARY_SYSTEM_PROMPT, _extract_list, _parse_budget, _plain_summary, _price_from_saved,
    _store_basket_totals, judge_model_warning, set_judge_model,
)
from scraper.ollama_client import OllamaError


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

    def test_when_nobody_covers_everything_more_items_beats_a_lower_total(self):
        # A store that covers 1 of 3 items for $1 isn't a bargain next to stores
        # that cover 2 of 3 -- its total is low only because it has less on it.
        findings = {
            "milk": [
                {"store_name": "Sparse Store", "price": 1.00},
                {"store_name": "Mid Store", "price": 2.00},
                {"store_name": "Pricier Store", "price": 2.50},
            ],
            "bread": [
                {"store_name": "Mid Store", "price": 1.50},
                {"store_name": "Pricier Store", "price": 2.00},
            ],
        }
        totals = _store_basket_totals(findings, ["milk", "bread", "eggs"])

        self.assertEqual(
            [s["store_name"] for s in totals], ["Mid Store", "Pricier Store", "Sparse Store"],
        )
        self.assertEqual([s["items_covered"] for s in totals], [2, 2, 1])

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


class TestStoreBasketTotalsUseTheEstimatedInStorePrice(unittest.TestCase):
    """Stores are compared on what the shopper would actually pay in the store:
    the markup-adjusted estimate where a store has one, the Instacart price where it
    doesn't. Comparing sticker prices overstated high-markup stores against ALDI."""

    def test_total_uses_the_estimate_not_the_instacart_price(self):
        findings = {"milk": [{"store_name": "Stop & Shop", "price": 3.16, "estimated_real_price": 2.37}]}

        totals = _store_basket_totals(findings, ["milk"])

        self.assertEqual(totals[0]["total"], 2.37)

    def test_a_row_with_no_estimate_falls_back_to_its_instacart_price(self):
        findings = {
            "milk": [{"store_name": "Market Basket", "price": 3.00, "estimated_real_price": 2.50}],
            "yogurt": [{"store_name": "Market Basket", "price": 1.29, "estimated_real_price": None}],
        }

        totals = _store_basket_totals(findings, ["milk", "yogurt"])

        self.assertEqual(totals[0]["total"], 3.79)
        self.assertEqual(totals[0]["sticker_priced_items"], ["yogurt"])

    def test_rows_without_an_estimate_key_at_all_still_total(self):
        findings = {"rice": [{"store_name": "ALDI", "price": 1.89}]}

        totals = _store_basket_totals(findings, ["rice"])

        self.assertEqual(totals[0]["total"], 1.89)
        self.assertEqual(totals[0]["sticker_priced_items"], ["rice"])

    def test_stores_are_ranked_on_estimates_so_a_marked_up_store_can_win(self):
        # Stop & Shop's sticker price is higher, but its estimated in-store price is lower.
        findings = {
            "milk": [
                {"store_name": "Stop & Shop", "price": 3.00, "estimated_real_price": 2.00},
                {"store_name": "ALDI", "price": 2.50, "estimated_real_price": 2.50},
            ],
        }

        totals = _store_basket_totals(findings, ["milk"])

        self.assertEqual([s["store_name"] for s in totals], ["Stop & Shop", "ALDI"])

    def test_a_fully_estimated_store_lists_no_sticker_items(self):
        findings = {"milk": [{"store_name": "Stop & Shop", "price": 3.16, "estimated_real_price": 2.37}]}

        totals = _store_basket_totals(findings, ["milk"])

        self.assertEqual(totals[0]["sticker_priced_items"], [])


class TestSummaryDescribesTheEstimatedPrices(unittest.TestCase):
    def test_plain_fallback_summary_quotes_the_estimated_in_store_price(self):
        findings = {"milk": [{"store_name": "Stop & Shop", "name": "Store Brand Whole Milk",
                              "price": 3.16, "estimated_real_price": 2.37}]}

        text = _plain_summary(findings, [], ["milk"])

        self.assertIn("$2.37", text)
        self.assertIn("est. in-store", text)

    def test_plain_fallback_summary_labels_a_row_with_no_estimate_as_the_instacart_price(self):
        findings = {"yogurt": [{"store_name": "Market Basket", "name": "Chobani Yogurt",
                                "price": 1.29, "estimated_real_price": None}]}

        text = _plain_summary(findings, [], ["yogurt"])

        self.assertIn("$1.29", text)
        self.assertIn("Instacart price", text)

    def test_the_summary_model_is_told_what_the_totals_are_made_of(self):
        # Totals are estimated in-store prices; without this the model quotes the
        # sticker price next to a total that doesn't add up from it.
        self.assertIn("estimated_real_price", SUMMARY_SYSTEM_PROMPT)
        self.assertIn("sticker_priced_items", SUMMARY_SYSTEM_PROMPT)


class TestJudgeModelWarning(unittest.TestCase):
    """The judge runs on its own model, separate from the chat model. If that model
    isn't pulled, every lookup silently degrades to "best-ranked candidate" -- so the
    server should say so at startup instead of nobody noticing."""

    @patch("scraper.chat_assistant.ollama_client.list_models")
    def test_a_pulled_judge_model_gives_no_warning(self, mock_list):
        mock_list.return_value = [f"{JUDGE_MODEL}:latest", "qwen3.8:27b"]

        self.assertIsNone(judge_model_warning())

    @patch("scraper.chat_assistant.ollama_client.list_models")
    def test_a_missing_judge_model_says_how_to_pull_it(self, mock_list):
        mock_list.return_value = ["qwen3.8:27b", "llama3.2:latest"]

        warning = judge_model_warning()

        self.assertIn(JUDGE_MODEL, warning)
        self.assertIn(f"ollama pull {JUDGE_MODEL}", warning)


class TestJudgeModelIsConfigurable(unittest.TestCase):
    """Which model judges products is a server-start choice (--judge-model), so it can
    be changed without a code push, e.g. to the chat model."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        fixture_path = Path(self._tmpdir.name) / "prices.json"
        fixture_path.write_text(json.dumps(_MULTI_ITEM_FIXTURE))
        price_lookup.set_prices_path(fixture_path)

    def tearDown(self):
        set_judge_model(JUDGE_MODEL)
        price_lookup.set_prices_path(None)
        self._tmpdir.cleanup()

    @patch("scraper.chat_assistant.product_matcher.pick_best_matches")
    def test_the_judge_call_uses_the_configured_model(self, mock_pick):
        mock_pick.return_value = [0]
        set_judge_model("qwen3.8:27b")

        _price_from_saved(["bread"], max_age_hours=999999)

        self.assertEqual(mock_pick.call_args.kwargs["model"], "qwen3.8:27b")

    @patch("scraper.chat_assistant.ollama_client.list_models")
    def test_the_startup_warning_checks_the_configured_model(self, mock_list):
        mock_list.return_value = ["qwen3.8:27b"]
        set_judge_model("qwen3.8:27b")
        self.assertIsNone(judge_model_warning())

        set_judge_model("llama3.1")
        self.assertIn("llama3.1", judge_model_warning())


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
        self.assertEqual(len(messages), 13)
        for i in (1, 3, 5, 7, 9, 11):
            self.assertEqual(messages[i]["role"], "assistant")
        self.assertIn("vegan", messages[-1]["content"])

    @patch("scraper.chat_assistant.ollama_client.chat_json")
    def test_extraction_call_includes_a_no_budget_short_item_demo(self, mock_chat_json):
        # Locks in the fifth demo added to stop "I need milk"-shaped inputs from
        # pattern-matching demo1 (the only budget-bearing example) and copying its
        # literal 15.0 even when no money was mentioned.
        mock_chat_json.return_value = {"ready": False, "items": [], "missing": "", "budget": None}
        _extract_list([{"role": "user", "content": "hello"}])

        messages = mock_chat_json.call_args[0][0]
        demo5_input, demo5_output = messages[8], messages[9]
        self.assertEqual(demo5_input["role"], "user")
        self.assertIn("I need milk", demo5_input["content"])
        self.assertEqual(demo5_output["role"], "assistant")
        self.assertIn('"budget": null', demo5_output["content"])
        self.assertIn('"milk"', demo5_output["content"])

    @patch("scraper.chat_assistant.ollama_client.chat_json")
    def test_extraction_call_includes_a_named_item_with_dish_as_context_demo(self, mock_chat_json):
        # Locks in the sixth demo added to stop "cheese for a sandwich" from
        # pattern-matching demo3's dish-expansion shape and adding "bread" the user
        # never asked for -- the dish word here is context for the named item, not a
        # request to provision the whole meal.
        mock_chat_json.return_value = {"ready": False, "items": [], "missing": "", "budget": None}
        _extract_list([{"role": "user", "content": "hello"}])

        messages = mock_chat_json.call_args[0][0]
        demo6_input, demo6_output = messages[10], messages[11]
        self.assertEqual(demo6_input["role"], "user")
        self.assertIn("cheese for a sandwich", demo6_input["content"])
        self.assertEqual(demo6_output["role"], "assistant")
        self.assertIn('"items": ["cheese"]', demo6_output["content"])
        self.assertNotIn("bread", demo6_output["content"])

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


class TestExtractListFencesOffTheWorkedExamples(unittest.TestCase):
    """Live testing found "I need bread" coming back as items [milk, cheese, bread]
    with budget 15.0 -- the milk, the cheese and the 15 are each the answer from a
    DIFFERENT worked example. The examples are shown as earlier chat turns, and the
    prompt tells the model to combine everything across every message, so it read
    the example users' facts as part of the real user's conversation."""

    @patch("scraper.chat_assistant.ollama_client.chat_json")
    def test_real_turn_says_it_is_a_separate_conversation(self, mock_chat_json):
        mock_chat_json.return_value = {"ready": False, "items": [], "missing": "", "budget": None}
        _extract_list([{"role": "user", "content": "I need bread"}])

        messages = mock_chat_json.call_args[0][0]
        self.assertIn("unrelated to the examples above", messages[-1]["content"])
        self.assertIn("I need bread", messages[-1]["content"])

    @patch("scraper.chat_assistant.ollama_client.chat_json")
    def test_worked_example_turns_are_not_fenced(self, mock_chat_json):
        mock_chat_json.return_value = {"ready": False, "items": [], "missing": "", "budget": None}
        _extract_list([{"role": "user", "content": "I need bread"}])

        messages = mock_chat_json.call_args[0][0]
        for i in range(0, len(messages) - 1, 2):
            self.assertNotIn("unrelated to the examples above", messages[i]["content"])


class TestExtractListBudgetMustBeStated(unittest.TestCase):
    """A budget is only real if the user's own words contain an amount. The model
    kept inventing 15.0 (the number from a worked example) for messages with no
    money in them at all, so no amount in the words means no budget, whatever the
    model said."""

    def _extract(self, user_text, model_budget, mock_chat_json):
        mock_chat_json.return_value = {
            "ready": True, "items": ["bread"], "missing": "", "budget": model_budget,
        }
        return _extract_list([{"role": "user", "content": user_text}])

    @patch("scraper.chat_assistant.ollama_client.chat_json")
    def test_invented_budget_is_dropped_when_the_user_mentioned_no_amount(self, mock_chat_json):
        self.assertIsNone(self._extract("I need bread", 15.0, mock_chat_json)["budget"])

    @patch("scraper.chat_assistant.ollama_client.chat_json")
    def test_a_number_word_with_no_money_cue_is_not_a_budget(self, mock_chat_json):
        self.assertIsNone(self._extract("I need one loaf of bread", 15.0, mock_chat_json)["budget"])

    @patch("scraper.chat_assistant.ollama_client.chat_json")
    def test_budget_stated_in_digits_is_kept(self, mock_chat_json):
        result = self._extract("I need bread, I have 12 dollars", 12.0, mock_chat_json)
        self.assertEqual(result["budget"], 12.0)

    @patch("scraper.chat_assistant.ollama_client.chat_json")
    def test_budget_stated_with_a_dollar_sign_is_kept(self, mock_chat_json):
        result = self._extract("bread for under $8", 8.0, mock_chat_json)
        self.assertEqual(result["budget"], 8.0)

    @patch("scraper.chat_assistant.ollama_client.chat_json")
    def test_budget_stated_in_words_is_kept(self, mock_chat_json):
        result = self._extract("I've got fifteen dollars and need bread", 15.0, mock_chat_json)
        self.assertEqual(result["budget"], 15.0)

    @patch("scraper.chat_assistant.ollama_client.chat_json")
    def test_budget_from_an_earlier_user_message_is_kept(self, mock_chat_json):
        mock_chat_json.return_value = {"ready": True, "items": ["bread"], "missing": "", "budget": 20.0}
        result = _extract_list([
            {"role": "user", "content": "I have 20 dollars to spend"},
            {"role": "assistant", "content": "Great, what do you need?"},
            {"role": "user", "content": "I need bread"},
        ])
        self.assertEqual(result["budget"], 20.0)


class TestExtractListMovesVagueItemsToMissing(unittest.TestCase):
    """"a drink to go with it" isn't something a store search box can find. It must
    surface as an open question (the UI shows `missing` as a note), not ride along
    as an item that then prices as whatever "drink" happens to match."""

    @patch("scraper.chat_assistant.ollama_client.chat_json")
    def test_vague_drink_is_removed_from_items_and_named_in_missing(self, mock_chat_json):
        mock_chat_json.return_value = {
            "ready": True, "items": ["cheese", "bread", "drink"], "missing": "", "budget": None,
        }
        result = _extract_list([{"role": "user", "content": "a sandwich with cheese and bread plus a drink"}])

        self.assertEqual(result["items"], ["cheese", "bread"])
        self.assertTrue(result["ready"])
        self.assertIn("drink", result["missing"])

    @patch("scraper.chat_assistant.ollama_client.chat_json")
    def test_a_list_of_only_vague_items_is_not_ready(self, mock_chat_json):
        mock_chat_json.return_value = {
            "ready": True, "items": ["snack"], "missing": "", "budget": None,
        }
        result = _extract_list([{"role": "user", "content": "I want a snack"}])

        self.assertEqual(result["items"], [])
        self.assertFalse(result["ready"])
        self.assertIn("snack", result["missing"])

    @patch("scraper.chat_assistant.ollama_client.chat_json")
    def test_a_model_supplied_missing_note_is_kept_alongside(self, mock_chat_json):
        mock_chat_json.return_value = {
            "ready": True, "items": ["bread", "dessert"], "missing": "how many people", "budget": None,
        }
        result = _extract_list([{"role": "user", "content": "bread and a dessert"}])

        self.assertIn("how many people", result["missing"])
        self.assertIn("dessert", result["missing"])

    @patch("scraper.chat_assistant.ollama_client.chat_json")
    def test_specific_items_are_never_treated_as_vague(self, mock_chat_json):
        mock_chat_json.return_value = {
            "ready": True, "items": ["orange juice", "potato chips", "chocolate cake"],
            "missing": "", "budget": None,
        }
        result = _extract_list([{"role": "user", "content": "orange juice, potato chips and chocolate cake"}])

        self.assertEqual(result["items"], ["orange juice", "potato chips", "chocolate cake"])
        self.assertEqual(result["missing"], "")


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
        # Candidates are ranked, not just cheapest-first: "Sharp Cheddar Cheese"
        # ends in the word "cheese" and "Cheese Dip" doesn't, so the real cheese is
        # candidate 0 even though the dip is $1.50 cheaper. The judge says so
        # explicitly here; the real disambiguation judgment is covered by the live
        # test below.
        mock_pick.return_value = [0]

        findings, unresolved = _price_from_saved(["cheese"], max_age_hours=999999, context="dinner")

        self.assertEqual(unresolved, [])
        self.assertEqual(findings["cheese"][0]["name"], "Sharp Cheddar Cheese")
        candidate_names = [c["name"] for c in mock_pick.call_args[0][2][0]["candidates"]]
        self.assertEqual(candidate_names, ["Sharp Cheddar Cheese", "Cheese Dip"])
        # Context and the item list both reach the judge, not just the term.
        args, kwargs = mock_pick.call_args
        self.assertEqual(args[0], "dinner")
        self.assertEqual(args[1], ["cheese"])

    @patch("scraper.chat_assistant.product_matcher.pick_best_matches")
    def test_the_judges_pick_wins_over_the_ranking(self, mock_pick):
        # Ranking only decides who gets into the judge's short list and in what
        # order; the judge still has the final say.
        mock_pick.return_value = [1]

        findings, _unresolved = _price_from_saved(["cheese"], max_age_hours=999999, context="a party dip")

        self.assertEqual(findings["cheese"][0]["name"], "Cheese Dip")

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


_MULTI_ITEM_FIXTURE = {
    "scraped_at": "2026-01-01T00:00:00+00:00",
    "location": "Test City",
    "stores": [
        {
            "name": "Test Store",
            "url": "https://example.com",
            "product_count": 3,
            "products": [
                {"name": "White Bread", "price": 2.00, "unit": None, "department": "baked-goods"},
                {"name": "Cheddar Cheese", "price": 3.00, "unit": None, "department": "dairy"},
                {"name": "Orange Juice", "price": 4.00, "unit": None, "department": "beverages"},
            ],
        }
    ],
}


class TestPriceFromSavedRetriesUnresolvedItemsSolo(unittest.TestCase):
    """Caught live: a judge call batching 3 items said "no match" for orange juice
    despite a real candidate existing, while the exact same candidate judged fine
    when orange juice was the only item in the batch (temperature=0, so this is
    deterministic, not sampling noise) -- a smaller judge model getting distracted
    juggling multiple items at once, not a real "nothing fits" verdict."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        fixture_path = Path(self._tmpdir.name) / "prices.json"
        fixture_path.write_text(json.dumps(_MULTI_ITEM_FIXTURE))
        price_lookup.set_prices_path(fixture_path)

    def tearDown(self):
        price_lookup.set_prices_path(None)
        self._tmpdir.cleanup()

    @patch("scraper.chat_assistant.product_matcher.pick_best_matches")
    def test_item_with_real_candidates_marked_unresolved_in_a_batch_gets_retried_solo(self, mock_pick):
        mock_pick.side_effect = [
            [0, 0, None],  # batch call: bread, cheese, orange juice
            [0],           # solo retry call: just orange juice
        ]

        findings, unresolved = _price_from_saved(
            ["bread", "cheese", "orange juice"], max_age_hours=999999,
        )

        self.assertEqual(unresolved, [])
        self.assertIn("orange juice", findings)
        self.assertEqual(findings["orange juice"][0]["name"], "Orange Juice")
        self.assertEqual(mock_pick.call_count, 2)
        # The retry call judges ONLY the unresolved item, not the whole batch again.
        retry_items_arg = mock_pick.call_args_list[1][0][1]
        self.assertEqual(retry_items_arg, ["orange juice"])

    @patch("scraper.chat_assistant.product_matcher.pick_best_matches")
    def test_single_item_batch_is_not_retried_on_explicit_no_match(self, mock_pick):
        # A single-item request that gets None back is a real "no good candidate"
        # judgment, not a batch-distraction artifact -- retrying the identical solo
        # call again would just reproduce the same deterministic (temperature=0)
        # answer, so don't bother. Also guards the existing no-match contract.
        mock_pick.return_value = [None]

        findings, unresolved = _price_from_saved(["cheese"], max_age_hours=999999)

        self.assertEqual(findings, {})
        self.assertEqual(unresolved, ["cheese"])
        self.assertEqual(mock_pick.call_count, 1)

    @patch("scraper.chat_assistant.product_matcher.pick_best_matches")
    def test_genuine_no_match_in_a_batch_still_ends_unresolved_after_retry(self, mock_pick):
        # The retry is a second chance, not a guarantee -- if the solo retry also
        # comes back None, respect it and leave the item unresolved rather than
        # looping or forcing a pick.
        mock_pick.side_effect = [
            [0, 0, None],
            [None],
        ]

        findings, unresolved = _price_from_saved(
            ["bread", "cheese", "orange juice"], max_age_hours=999999,
        )

        self.assertEqual(unresolved, ["orange juice"])
        self.assertNotIn("orange juice", findings)
        self.assertEqual(mock_pick.call_count, 2)


def _crowded_cheese_fixture():
    # Nine cheap "cheese" look-alikes (a prepared-foods mac-and-cheese line) and two
    # real, pricier cheeses. Cheapest-first, the judge's 8-candidate window is all junk.
    junk = [
        {"name": f"Cheese Club Mac & Cheese Flavor {i}", "price": 0.50 + i * 0.05,
         "unit": None, "department": "prepared-foods"}
        for i in range(9)
    ]
    real = [
        {"name": "Sharp Cheddar Cheese", "price": 4.00, "unit": None, "department": "dairy"},
        {"name": "Mild Swiss Cheese", "price": 3.50, "unit": None, "department": "dairy"},
    ]
    return {
        "scraped_at": "2026-01-01T00:00:00+00:00",
        "location": "Test City",
        "stores": [{"name": "Test Store", "url": "https://example.com",
                    "product_count": len(junk) + len(real), "products": junk + real}],
    }


class TestPriceFromSavedShowsTheJudgeTheRealItemsFirst(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        fixture_path = Path(self._tmpdir.name) / "prices.json"
        fixture_path.write_text(json.dumps(_crowded_cheese_fixture()))
        price_lookup.set_prices_path(fixture_path)

    def tearDown(self):
        price_lookup.set_prices_path(None)
        self._tmpdir.cleanup()

    @patch("scraper.chat_assistant.product_matcher.pick_best_matches")
    def test_real_cheese_is_in_the_candidate_list_despite_cheaper_look_alikes(self, mock_pick):
        mock_pick.return_value = [0]

        findings, _unresolved = _price_from_saved(["cheese"], max_age_hours=999999)

        candidate_names = [c["name"] for c in mock_pick.call_args[0][2][0]["candidates"]]
        self.assertEqual(candidate_names[:2], ["Mild Swiss Cheese", "Sharp Cheddar Cheese"])
        self.assertEqual(findings["cheese"][0]["name"], "Mild Swiss Cheese")


def _three_store_milk_fixture():
    def store(name, products):
        return {"name": name, "url": "https://example.com", "product_count": len(products), "products": products}

    return {
        "scraped_at": "2026-01-01T00:00:00+00:00",
        "location": "Test City",
        "stores": [
            store("ALDI", [
                {"name": "Friendly Farms Vitamin D Whole Milk", "price": 1.59, "unit": None, "department": "dairy"},
            ]),
            store("Stop & Shop", [
                {"name": "Store Brand 2% Reduced Fat Milk", "price": 2.10, "unit": None, "department": "dairy"},
                {"name": "Store Brand Whole Milk", "price": 3.16, "unit": None, "department": "dairy"},
            ]),
            store("Shaw's", [
                {"name": "Shaw's Whole Milk", "price": 3.20, "unit": None, "department": "dairy"},
            ]),
        ],
    }


class TestPriceFromSavedShowsEveryStoresClosestMatch(unittest.TestCase):
    """The judge picks one product; the other stores must show their own closest
    version of it instead of vanishing because they don't stock that exact SKU."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        fixture_path = Path(self._tmpdir.name) / "prices.json"
        fixture_path.write_text(json.dumps(_three_store_milk_fixture()))
        price_lookup.set_prices_path(fixture_path)

    def tearDown(self):
        price_lookup.set_prices_path(None)
        self._tmpdir.cleanup()

    @patch("scraper.chat_assistant.product_matcher.pick_best_matches")
    def test_every_store_with_an_equivalent_gets_a_row(self, mock_pick):
        names = None

        def pick(_context, _items, batch, model=None):
            nonlocal names
            names = [c["name"] for c in batch[0]["candidates"]]
            return [names.index("Friendly Farms Vitamin D Whole Milk")]

        mock_pick.side_effect = pick

        findings, unresolved = _price_from_saved(["milk"], max_age_hours=999999)

        self.assertEqual(unresolved, [])
        by_store = {r["store_name"]: r["name"] for r in findings["milk"]}
        self.assertEqual(by_store, {
            "ALDI": "Friendly Farms Vitamin D Whole Milk",
            "Stop & Shop": "Store Brand Whole Milk",
            "Shaw's": "Shaw's Whole Milk",
        })

    @patch("scraper.chat_assistant.product_matcher.pick_best_matches")
    def test_store_totals_now_cover_the_whole_list_at_every_store(self, mock_pick):
        mock_pick.return_value = [0]

        findings, _ = _price_from_saved(["milk"], max_age_hours=999999)
        totals = _store_basket_totals(findings, ["milk"])

        self.assertEqual(len(totals), 3)
        self.assertTrue(all(t["covers_all_items"] for t in totals))


class TestPriceFromSavedSurvivesAFailingJudge(unittest.TestCase):
    """Caught live: a 10-item list returned an HTML 500 after 300 seconds -- the
    judge call timed out and nothing caught the OllamaError. A judge that can't
    answer should degrade to its own fallback (the best-ranked candidate), not
    take the whole request down."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        fixture_path = Path(self._tmpdir.name) / "prices.json"
        fixture_path.write_text(json.dumps(_MULTI_ITEM_FIXTURE))
        price_lookup.set_prices_path(fixture_path)

    def tearDown(self):
        price_lookup.set_prices_path(None)
        self._tmpdir.cleanup()

    @patch("scraper.chat_assistant.product_matcher.pick_best_matches")
    def test_judge_timeout_falls_back_to_the_best_ranked_candidate(self, mock_pick):
        mock_pick.side_effect = OllamaError("Ollama timed out after 300s.")

        findings, unresolved = _price_from_saved(["bread", "cheese", "orange juice"], max_age_hours=999999)

        self.assertEqual(unresolved, [])
        self.assertEqual(findings["bread"][0]["name"], "White Bread")
        self.assertEqual(findings["cheese"][0]["name"], "Cheddar Cheese")
        self.assertEqual(findings["orange juice"][0]["name"], "Orange Juice")
        # One failed batch call, and no solo retries piled on top of a dead judge.
        self.assertEqual(mock_pick.call_count, 1)

    @patch("scraper.chat_assistant.product_matcher.pick_best_matches")
    def test_items_with_no_candidates_are_still_unresolved_when_the_judge_fails(self, mock_pick):
        mock_pick.side_effect = OllamaError("boom")

        findings, unresolved = _price_from_saved(["bread", "diapers"], max_age_hours=999999)

        self.assertIn("bread", findings)
        self.assertEqual(unresolved, ["diapers"])


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
