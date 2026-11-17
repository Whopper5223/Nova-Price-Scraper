import unittest
from unittest.mock import patch

from scraper import substitutions
from scraper.ollama_client import OllamaError


def _ask(items, reply, context="a stir fry", **kwargs):
    with patch("scraper.substitutions.ollama_client.chat_json", return_value=reply) as mock_json:
        return substitutions.propose_substitutes(items, context, **kwargs), mock_json


class TestProposeSubstitutes(unittest.TestCase):
    """One batched model call: for ingredients we have no price for, which other
    grocery items could really stand in for them. Anything malformed collapses to
    "no substitute" -- this never raises."""

    def test_returns_the_options_for_each_ingredient_lowercased(self):
        out, _ = _ask(["grapeseed oil"], {"substitutes": [
            {"ingredient": "grapeseed oil", "options": ["Canola Oil", "vegetable oil"]}]})

        self.assertEqual(out, {"grapeseed oil": ["canola oil", "vegetable oil"]})

    def test_at_most_two_options_are_kept(self):
        out, _ = _ask(["grapeseed oil"], {"substitutes": [
            {"ingredient": "grapeseed oil", "options": ["canola oil", "vegetable oil", "corn oil"]}]})

        self.assertEqual(out["grapeseed oil"], ["canola oil", "vegetable oil"])

    def test_an_empty_options_list_means_no_substitute(self):
        out, _ = _ask(["eggs"], {"substitutes": [{"ingredient": "eggs", "options": []}]})

        self.assertEqual(out, {})

    def test_the_ingredients_own_name_duplicates_and_non_strings_are_dropped(self):
        out, _ = _ask(["scallions"], {"substitutes": [
            {"ingredient": "scallions", "options": ["Scallions", "green onions", "green onions", 7, None, "  "]}]})

        self.assertEqual(out, {"scallions": ["green onions"]})

    def test_entries_for_ingredients_nobody_asked_about_are_ignored(self):
        out, _ = _ask(["scallions"], {"substitutes": [
            {"ingredient": "caviar", "options": ["tuna"]},
            {"ingredient": "scallions", "options": ["green onions"]}]})

        self.assertEqual(out, {"scallions": ["green onions"]})

    def test_malformed_replies_collapse_to_no_substitutes(self):
        for bad in (None, "text", [], {"substitutes": "x"}, {"substitutes": ["x", 3]},
                    {"substitutes": [{"ingredient": "scallions", "options": "green onions"}]}, {}):
            out, _ = _ask(["scallions"], bad)
            self.assertEqual(out, {}, msg=repr(bad))

    def test_a_model_failure_collapses_to_no_substitutes(self):
        with patch("scraper.substitutions.ollama_client.chat_json", side_effect=OllamaError("down")):
            self.assertEqual(substitutions.propose_substitutes(["scallions"], "dinner"), {})

    def test_no_ingredients_means_no_model_call(self):
        out, mock_json = _ask([], {"substitutes": []})

        self.assertEqual(out, {})
        mock_json.assert_not_called()

    def test_the_model_is_given_the_ingredients_the_meal_and_the_chosen_model(self):
        _out, mock_json = _ask(["grapeseed oil", "shallots"], {"substitutes": []},
                               context="pad thai for two", model="qwen3.8:27b")

        last_turn = mock_json.call_args[0][0][-1]["content"]
        self.assertIn("grapeseed oil", last_turn)
        self.assertIn("shallots", last_turn)
        self.assertIn("pad thai for two", last_turn)
        self.assertEqual(mock_json.call_args.kwargs["model"], "qwen3.8:27b")

    def test_there_is_one_call_for_the_whole_batch(self):
        _out, mock_json = _ask(["a", "b", "c"], {"substitutes": []})

        self.assertEqual(mock_json.call_count, 1)


if __name__ == "__main__":
    unittest.main()
