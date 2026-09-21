import unittest
from unittest.mock import patch

from scraper.product_matcher import pick_best_matches, _batches, BATCH_SIZE


class TestBatches(unittest.TestCase):
    def test_splits_into_correct_batch_sizes(self):
        rows = [{"ingredient": f"item{i}"} for i in range(60)]
        batches = list(_batches(rows, size=25))
        self.assertEqual([len(b) for b in batches], [25, 25, 10])

    def test_empty_input_yields_no_batches(self):
        self.assertEqual(list(_batches([])), [])


class TestPickBestMatches(unittest.TestCase):
    @patch("scraper.product_matcher.ollama_client.chat_json")
    def test_returns_indices_for_clean_successful_pick(self, mock_chat_json):
        mock_chat_json.return_value = {"choices": [{"index": 1}, {"index": 0}]}
        batch = [
            {
                "ingredient": "milk",
                "candidates": [
                    {"name": "Chocolate Milk, Half Gallon"},
                    {"name": "Whole Milk, 1 Gallon"},
                ],
            },
            {
                "ingredient": "egg noodles",
                "candidates": [{"name": "Wide Egg Noodles, 12 oz"}],
            },
        ]

        result = pick_best_matches("Beef Stroganoff", ["milk", "egg noodles"], batch)

        self.assertEqual(result, [1, 0])

    @patch("scraper.product_matcher.ollama_client.chat_json")
    def test_mismatched_response_length_falls_back_to_cheapest(self, mock_chat_json):
        # Spec's Architecture/Boundaries: a response that doesn't validate falls
        # back to the cheapest Stage-1 candidate per ingredient (index 0, since
        # candidates arrive cheapest-first) — not to None/not_found, which is
        # reserved for a validated response that explicitly says "index: null"
        # (Open Questions #3).
        mock_chat_json.return_value = {"choices": [{"index": 0}]}  # only 1, but 2 ingredients
        batch = [
            {"ingredient": "milk", "candidates": [{"name": "A"}, {"name": "B"}]},
            {"ingredient": "egg noodles", "candidates": [{"name": "C"}]},
        ]

        result = pick_best_matches("Beef Stroganoff", ["milk", "egg noodles"], batch)

        self.assertEqual(result, [0, 0])

    @patch("scraper.product_matcher.ollama_client.chat_json")
    def test_out_of_range_index_falls_back_to_cheapest_for_that_entry_only(self, mock_chat_json):
        # First ingredient only has 2 candidates (valid indices 0-1); the judge
        # hallucinates index 5. The second ingredient's valid pick must be
        # unaffected by the first entry's bad index.
        mock_chat_json.return_value = {"choices": [{"index": 5}, {"index": 0}]}
        batch = [
            {"ingredient": "milk", "candidates": [{"name": "A"}, {"name": "B"}]},
            {"ingredient": "egg noodles", "candidates": [{"name": "C"}]},
        ]

        result = pick_best_matches("Beef Stroganoff", ["milk", "egg noodles"], batch)

        self.assertEqual(result, [0, 0])

    @patch("scraper.product_matcher.ollama_client.chat_json")
    def test_malformed_entry_falls_back_to_cheapest_not_none(self, mock_chat_json):
        # A choices entry that isn't a dict, or is missing "index" entirely, is
        # a malformed response for that item — not the same thing as the judge
        # validly saying "index: null". Must not be silently read as the latter.
        mock_chat_json.return_value = {"choices": [{}, "not-a-dict"]}
        batch = [
            {"ingredient": "milk", "candidates": [{"name": "A"}, {"name": "B"}]},
            {"ingredient": "egg noodles", "candidates": [{"name": "C"}]},
        ]

        result = pick_best_matches("Beef Stroganoff", ["milk", "egg noodles"], batch)

        self.assertEqual(result, [0, 0])

    @patch("scraper.product_matcher.ollama_client.chat_json")
    def test_fallback_with_no_candidates_yields_none(self, mock_chat_json):
        # The cheapest-candidate fallback can't produce an index for an item
        # with zero candidates — must degrade to None (not IndexError downstream).
        mock_chat_json.return_value = {"choices": [{"index": 99}]}
        batch = [{"ingredient": "unobtainium", "candidates": []}]

        result = pick_best_matches("Beef Stroganoff", ["unobtainium"], batch)

        self.assertEqual(result, [None])

    @patch("scraper.product_matcher.ollama_client.chat_json")
    def test_explicit_null_index_becomes_none_without_raising(self, mock_chat_json):
        # A legitimate "no good candidate" (index: null) sits between two
        # entries with real integer indices. An implementation that compares
        # the index numerically (e.g. `0 <= index < n`) before checking for
        # None would raise TypeError on this batch instead of returning None.
        mock_chat_json.return_value = {
            "choices": [{"index": 1}, {"index": None}, {"index": 0}]
        }
        batch = [
            {"ingredient": "beef stew meat", "candidates": [{"name": "A"}, {"name": "B"}]},
            {"ingredient": "ground nutmeg", "candidates": [{"name": "C"}, {"name": "D"}]},
            {"ingredient": "egg noodles", "candidates": [{"name": "E"}]},
        ]

        result = pick_best_matches(
            "Beef Stroganoff",
            ["beef stew meat", "ground nutmeg", "egg noodles"],
            batch,
        )

        self.assertEqual(result, [1, None, 0])

    @patch("scraper.product_matcher.ollama_client.chat_json")
    def test_non_dict_response_falls_back_to_cheapest(self, mock_chat_json):
        mock_chat_json.return_value = None  # unparseable JSON, per chat_json's own contract
        batch = [{"ingredient": "milk", "candidates": [{"name": "A"}]}]

        result = pick_best_matches("Beef Stroganoff", ["milk"], batch)

        self.assertEqual(result, [0])

    @patch("scraper.product_matcher.ollama_client.chat_json")
    def test_calls_are_batched_not_one_per_ingredient(self, mock_chat_json):
        mock_chat_json.return_value = {"choices": [{"index": 0} for _ in range(BATCH_SIZE)]}
        batch = [{"ingredient": f"item{i}", "candidates": [{"name": "X"}]} for i in range(BATCH_SIZE)]

        pick_best_matches("Recipe", [f"item{i}" for i in range(BATCH_SIZE)], batch)

        self.assertEqual(mock_chat_json.call_count, 1)

    @patch("scraper.product_matcher.ollama_client.chat_json")
    def test_includes_a_worked_example_before_the_real_batch(self, mock_chat_json):
        mock_chat_json.return_value = {"choices": [{"index": 0}]}
        batch = [{"ingredient": "egg noodles", "candidates": [{"name": "Wide Egg Noodles, 12 oz"}]}]

        pick_best_matches("Weeknight Pasta", ["egg noodles"], batch)

        messages = mock_chat_json.call_args[0][0]
        self.assertEqual(len(messages), 3)
        self.assertEqual(messages[0]["role"], "user")
        self.assertEqual(messages[1]["role"], "assistant")
        self.assertEqual(messages[2]["role"], "user")
        # the demo turn is the worked example, not the real recipe
        self.assertIn("Beef Stroganoff", messages[0]["content"])
        self.assertIn("Weeknight Pasta", messages[2]["content"])


if __name__ == "__main__":
    unittest.main()
