"""
Fixture vectors here are hand-built, not real Ollama embeddings — they exercise
the ranking math (cosine_similarity / find_best_matches) in isolation. Whether
nomic-embed-text's real embeddings actually separate these product names this
cleanly is verified separately by a live smoke test on the server, not here.
"""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scraper import product_embeddings
from scraper.product_embeddings import (
    cosine_similarity,
    filter_by_relevance,
    find_best_matches,
    get_embedding,
    matches_query,
)


class TestMatchesQuery(unittest.TestCase):
    """Word-boundary + light stemming -- the deterministic fix for two of the
    three edge-case categories, with no model calls involved."""

    def test_word_is_substring_of_a_different_word_does_not_match(self):
        self.assertFalse(matches_query("Eggplant", "egg"))
        self.assertFalse(matches_query("Peanut Butter", "pea"))

    def test_plural_still_matches_singular_query(self):
        self.assertTrue(matches_query("Large Eggs, Dozen", "egg"))
        self.assertTrue(matches_query("Fresh Tomatoes", "tomato"))

    def test_whole_word_match_still_works(self):
        self.assertTrue(matches_query("Salted Butter", "butter"))
        self.assertTrue(matches_query("Butter Cookies", "butter"))  # semantic layer's job to exclude

    def test_multi_word_query_requires_all_words(self):
        self.assertTrue(matches_query("Chicken Broth, 32oz", "chicken broth"))
        self.assertFalse(matches_query("Chicken Breast Tenders", "chicken broth"))

    def test_short_words_are_not_stemmed(self):
        # "gas" must not become "ga"; a query for "gas" should not match "Ga".
        self.assertFalse(matches_query("Ga Filler", "gas"))
        self.assertTrue(matches_query("Natural Gas Grill", "gas"))

    def test_word_already_ending_in_e_is_not_over_stemmed(self):
        # Regression: an earlier "es" -> "" rule turned "Apples" into "Appl",
        # losing the match entirely. "Apples" must reduce to "apple", not "appl".
        self.assertTrue(matches_query("Gala Apples", "apple"))
        self.assertTrue(matches_query("Fresh Oranges", "orange"))

    def test_word_ending_in_ie_is_not_corrupted_by_plural_handling(self):
        # Regression: a since-removed "y"->"ies" rule would have turned
        # "Cookies" into "Cooky" instead of "Cookie" -- "cookie"/"cookies" and
        # "movie"/"movies" already end in "ie", they don't come from a "y".
        self.assertTrue(matches_query("Butter Cookies", "cookie"))
        self.assertTrue(matches_query("Action Movies", "movie"))

    def test_consonant_plus_es_plural_still_works(self):
        self.assertTrue(matches_query("Fresh Tomatoes", "tomato"))
        self.assertTrue(matches_query("Cardboard Boxes", "box"))


class TestCosineSimilarity(unittest.TestCase):
    def test_identical_vectors_are_1(self):
        self.assertAlmostEqual(cosine_similarity([1, 2, 3], [1, 2, 3]), 1.0)

    def test_orthogonal_vectors_are_0(self):
        self.assertAlmostEqual(cosine_similarity([1, 0], [0, 1]), 0.0)

    def test_opposite_vectors_are_negative_1(self):
        self.assertAlmostEqual(cosine_similarity([1, 0], [-1, 0]), -1.0)

    def test_zero_vector_returns_0(self):
        self.assertEqual(cosine_similarity([0, 0], [1, 1]), 0.0)

    def test_mismatched_lengths_raise_value_error(self):
        with self.assertRaises(ValueError):
            cosine_similarity([1, 2, 3], [1, 2])


class TestFindBestMatches(unittest.TestCase):
    def test_ranks_by_similarity_descending(self):
        query = [1, 0]
        candidates = [
            {"name": "far", "embedding": [0, 1]},
            {"name": "near", "embedding": [0.9, 0.1]},
            {"name": "mid", "embedding": [0.5, 0.5]},
        ]
        result = find_best_matches(query, candidates)
        self.assertEqual([r["name"] for r in result], ["near", "mid", "far"])

    def test_respects_top_k(self):
        query = [1, 0]
        candidates = [{"name": str(i), "embedding": [1, 0]} for i in range(10)]
        self.assertEqual(len(find_best_matches(query, candidates, top_k=3)), 3)

    def test_adds_similarity_key_without_mutating_original(self):
        candidate = {"name": "butter", "embedding": [1, 0]}
        result = find_best_matches([1, 0], [candidate])
        self.assertIn("similarity", result[0])
        self.assertNotIn("similarity", candidate)


# ---------------------------------------------------------------------------
# Edge-case fixtures — the failure patterns of the old substring matcher
# ---------------------------------------------------------------------------
#
# Each fixture uses a toy embedding space with a few semantic axes (e.g.
# [dairy, sweet/cookie, produce, meat]) so the "correct" ranking is meaningful
# rather than arbitrary, while still being hand-built rather than real model
# output.

class TestFlavorNoiseEdgeCase(unittest.TestCase):
    """'butter' should not be conflated with 'butter cookies'; 'milk' with flavored milk."""

    def test_butter_ranks_above_butter_cookies(self):
        query = [1.0, 0.0]  # pure dairy/fat axis
        candidates = [
            {"name": "Butter Cookies", "embedding": [0.3, 0.95]},  # mostly "sweet/cookie"
            {"name": "Salted Butter", "embedding": [0.95, 0.1]},   # mostly "dairy/fat"
        ]
        result = find_best_matches(query, candidates, top_k=1)
        self.assertEqual(result[0]["name"], "Salted Butter")

    def test_plain_milk_ranks_above_flavored_milk(self):
        query = [1.0, 0.0]  # plain dairy axis, no flavor
        candidates = [
            {"name": "Chocolate Milk", "embedding": [0.6, 0.8]},
            {"name": "Almond Milk", "embedding": [0.4, 0.9]},
            {"name": "Whole Milk", "embedding": [0.98, 0.05]},
        ]
        result = find_best_matches(query, candidates, top_k=1)
        self.assertEqual(result[0]["name"], "Whole Milk")


class TestSubstringInLongerWordEdgeCase(unittest.TestCase):
    """'egg' should not match 'eggplant'; 'pea' should not match 'peanut butter'."""

    def test_egg_does_not_rank_eggplant_first(self):
        query = [1.0, 0.0]  # egg/protein axis
        candidates = [
            {"name": "Eggplant", "embedding": [0.1, 0.95]},  # produce axis
            {"name": "Large Eggs, Dozen", "embedding": [0.97, 0.1]},
        ]
        result = find_best_matches(query, candidates, top_k=1)
        self.assertEqual(result[0]["name"], "Large Eggs, Dozen")

    def test_pea_does_not_rank_peanut_butter_first(self):
        query = [1.0, 0.0]  # pea/vegetable axis
        candidates = [
            {"name": "Peanut Butter", "embedding": [0.15, 0.9]},  # nut butter axis
            {"name": "Frozen Peas", "embedding": [0.95, 0.05]},
        ]
        result = find_best_matches(query, candidates, top_k=1)
        self.assertEqual(result[0]["name"], "Frozen Peas")


class TestMultiWordOrderEdgeCase(unittest.TestCase):
    """'chicken broth' should outrank reordered/unrelated chicken phrasing."""

    def test_chicken_broth_ranks_above_reordered_and_unrelated(self):
        query = [1.0, 0.0, 0.0]  # broth-specific axis
        candidates = [
            {"name": "Chicken Breast Tenders", "embedding": [0.05, 0.1, 0.95]},  # unrelated chicken cut
            {"name": "Broth Flavored Chicken Chips", "embedding": [0.3, 0.9, 0.1]},  # reordered/unrelated product
            {"name": "Chicken Broth, 32oz", "embedding": [0.97, 0.05, 0.05]},
        ]
        result = find_best_matches(query, candidates, top_k=1)
        self.assertEqual(result[0]["name"], "Chicken Broth, 32oz")


# ---------------------------------------------------------------------------
# SQLite cache
# ---------------------------------------------------------------------------

class TestGetEmbeddingCache(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        product_embeddings.set_db_path(Path(self._tmpdir.name) / "test.db")

    def tearDown(self):
        product_embeddings.set_db_path(None)
        self._tmpdir.cleanup()

    @patch("scraper.product_embeddings.ollama_client.embed")
    def test_computes_and_caches_new_name(self, mock_embed):
        mock_embed.return_value = [0.1, 0.2]
        result = get_embedding("Butter")
        self.assertEqual(result, [0.1, 0.2])
        mock_embed.assert_called_once_with(
            "search_document: Butter", model=product_embeddings.DEFAULT_EMBED_MODEL
        )

    @patch("scraper.product_embeddings.ollama_client.embed")
    def test_second_lookup_uses_cache_not_ollama(self, mock_embed):
        mock_embed.return_value = [0.1, 0.2]
        get_embedding("Butter")
        get_embedding("Butter")
        self.assertEqual(mock_embed.call_count, 1)

    @patch("scraper.product_embeddings.ollama_client.embed")
    def test_different_names_both_call_ollama(self, mock_embed):
        mock_embed.side_effect = [[0.1, 0.2], [0.3, 0.4]]
        get_embedding("Butter")
        get_embedding("Milk")
        self.assertEqual(mock_embed.call_count, 2)

    @patch("scraper.product_embeddings.ollama_client.embed")
    def test_same_name_different_model_both_call_ollama(self, mock_embed):
        # A model switch must re-embed, not return a stale vector from the old
        # model under the same product-name key (would silently corrupt
        # similarity math if dimensions differ between models).
        mock_embed.side_effect = [[0.1, 0.2], [0.3, 0.4, 0.5]]
        get_embedding("Butter", model="model-a")
        get_embedding("Butter", model="model-b")
        self.assertEqual(mock_embed.call_count, 2)

    @patch("scraper.product_embeddings.ollama_client.embed")
    def test_cache_persists_across_separate_connections(self, mock_embed):
        # Not just an in-process memo -- verify it actually round-trips through
        # the SQLite file, simulating a fresh process picking the cache back up.
        mock_embed.return_value = [0.7, 0.8]
        get_embedding("Butter")

        conn = product_embeddings._connect()
        try:
            row = conn.execute(
                "SELECT embedding FROM product_embeddings WHERE product_name = ?", ("Butter",)
            ).fetchone()
        finally:
            conn.close()
        self.assertIsNotNone(row)
        self.assertEqual(json.loads(row[0]), [0.7, 0.8])


# ---------------------------------------------------------------------------
# filter_by_relevance (shared filter used by both price_lookup.py and core.py)
# ---------------------------------------------------------------------------

def _embed_map(query_term: str, query_vec, **name_vecs):
    """Builds the {prefixed_text: vector} map a mocked ollama_client.embed
    side_effect needs, given get_embedding() sends "search_document: {name}"
    and filter_by_relevance() sends "search_query: {term}"."""
    mapping = {f"search_query: {query_term}": query_vec}
    mapping.update({f"search_document: {name}": vec for name, vec in name_vecs.items()})
    return mapping


class TestFilterByRelevance(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        product_embeddings.set_db_path(Path(self._tmpdir.name) / "test.db")

    def tearDown(self):
        product_embeddings.set_db_path(None)
        self._tmpdir.cleanup()

    @patch("scraper.product_embeddings.ollama_client.embed")
    def test_uses_search_query_prefix_for_the_term(self, mock_embed):
        mock_embed.return_value = [1.0, 0.0]
        filter_by_relevance("butter", [{"name": "x"}])
        called_text = mock_embed.call_args_list[0][0][0]
        self.assertEqual(called_text, "search_query: butter")

    @patch("scraper.product_embeddings.ollama_client.embed")
    def test_preserves_input_order_of_survivors(self, mock_embed):
        # Both candidates score within margin of each other; order must stay
        # as given (a caller's price sort, e.g., must not be disturbed).
        mapping = _embed_map("query", [1.0, 0.0], b=[0.9, 0.1], a=[0.95, 0.05])
        mock_embed.side_effect = lambda term, **_: mapping[term]
        candidates = [{"name": "b", "price": 1}, {"name": "a", "price": 2}]
        result = filter_by_relevance("query", candidates, margin=0.5)
        self.assertEqual([c["name"] for c in result], ["b", "a"])

    @patch("scraper.product_embeddings.ollama_client.embed")
    def test_drops_candidates_far_below_the_best_score_in_this_search(self, mock_embed):
        mapping = _embed_map(
            "butter", [1.0, 0.0],
            **{"Butter Cookies": [0.3, 0.95], "Salted Butter": [0.95, 0.1]},
        )
        mock_embed.side_effect = lambda term, **_: mapping[term]
        candidates = [{"name": "Butter Cookies"}, {"name": "Salted Butter"}]
        result = filter_by_relevance("butter", candidates, margin=0.1)
        self.assertEqual([c["name"] for c in result], ["Salted Butter"])

    @patch("scraper.product_embeddings.ollama_client.embed")
    def test_close_scores_within_margin_all_survive(self, mock_embed):
        # Mirrors the real "milk" vs "chocolate milk" finding: a small gap
        # (here 0.02) within a generous margin (0.05) means both survive --
        # margin-based filtering is a known limitation for close calls, not
        # a bug (see DEFAULT_RELATIVE_MARGIN's docstring).
        mapping = _embed_map(
            "milk", [1.0, 0.0],
            **{"Whole Milk": [0.95, 0.1], "Chocolate Milk": [0.93, 0.12]},
        )
        mock_embed.side_effect = lambda term, **_: mapping[term]
        candidates = [{"name": "Whole Milk"}, {"name": "Chocolate Milk"}]
        result = filter_by_relevance("milk", candidates, margin=0.05)
        self.assertEqual([c["name"] for c in result], ["Whole Milk", "Chocolate Milk"])

    @patch("scraper.product_embeddings.ollama_client.embed")
    def test_falls_back_to_all_candidates_when_nothing_meets_the_margin(self, mock_embed):
        # An impossible-to-meet margin (negative) empties the filtered list
        # even for a single candidate -- must fall back to `candidates`.
        mapping = _embed_map("query", [1.0, 0.0], x=[-1.0, 0.0])
        mock_embed.side_effect = lambda term, **_: mapping[term]
        candidates = [{"name": "x"}]
        result = filter_by_relevance("query", candidates, margin=-1.0)
        self.assertEqual(result, candidates)

    def test_empty_candidates_returns_empty(self):
        self.assertEqual(filter_by_relevance("query", []), [])

    @patch("scraper.product_embeddings.ollama_client.embed")
    def test_best_scoring_candidate_always_survives(self, mock_embed):
        mapping = _embed_map("query", [1.0, 0.0], x=[1.0, 0.0])
        mock_embed.side_effect = lambda term, **_: mapping[term]
        candidates = [{"name": "x"}]
        result = filter_by_relevance("query", candidates, margin=0.0)
        self.assertEqual(result, candidates)

    @patch("scraper.product_embeddings.ollama_client.embed")
    def test_respects_custom_name_key(self, mock_embed):
        mapping = _embed_map("query", [1.0, 0.0], **{"Real Butter": [0.95, 0.1]})
        mock_embed.side_effect = lambda term, **_: mapping[term]
        candidates = [{"product_name": "Real Butter", "price": 4.0}]
        result = filter_by_relevance("query", candidates, name_key="product_name", margin=0.1)
        self.assertEqual(result, candidates)

    @patch("scraper.product_embeddings.get_embedding")
    @patch("scraper.product_embeddings.ollama_client.embed")
    def test_falls_back_to_all_candidates_if_a_candidate_embedding_fails_midway(
        self, mock_embed, mock_get_embedding
    ):
        # First candidate's embedding lookup succeeds, second raises (e.g. Ollama
        # went down between calls) -- the whole filter must fall back to
        # returning everything unfiltered, not a partially-filtered list.
        from scraper import ollama_client
        mock_embed.return_value = [1.0, 0.0]
        mock_get_embedding.side_effect = [[0.95, 0.1], ollama_client.OllamaError("down")]
        candidates = [{"name": "a"}, {"name": "b"}]

        result = filter_by_relevance("query", candidates, margin=0.1)

        self.assertEqual(result, candidates)


if __name__ == "__main__":
    unittest.main()
