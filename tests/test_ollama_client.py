import unittest
from unittest.mock import Mock, patch

import requests

from scraper import ollama_client


class TestEmbed(unittest.TestCase):
    @patch("scraper.ollama_client.requests.post")
    def test_returns_embedding_vector(self, mock_post):
        mock_post.return_value = Mock(status_code=200, json=lambda: {"embedding": [0.1, 0.2, 0.3]})
        result = ollama_client.embed("butter")
        self.assertEqual(result, [0.1, 0.2, 0.3])

    @patch("scraper.ollama_client.requests.post")
    def test_sends_requested_model(self, mock_post):
        mock_post.return_value = Mock(status_code=200, json=lambda: {"embedding": [0.1]})
        ollama_client.embed("butter", model="custom-embed")
        _, kwargs = mock_post.call_args
        self.assertEqual(kwargs["json"]["model"], "custom-embed")
        self.assertEqual(kwargs["json"]["prompt"], "butter")

    @patch("scraper.ollama_client.requests.post")
    def test_raises_on_connection_failure(self, mock_post):
        mock_post.side_effect = requests.ConnectionError("refused")
        with self.assertRaises(ollama_client.OllamaError):
            ollama_client.embed("butter")

    @patch("scraper.ollama_client.requests.post")
    def test_raises_on_missing_embedding_key(self, mock_post):
        mock_post.return_value = Mock(status_code=200, json=lambda: {"unexpected": "shape"}, text="{}")
        with self.assertRaises(ollama_client.OllamaError):
            ollama_client.embed("butter")

    @patch("scraper.ollama_client.requests.post")
    def test_raises_on_empty_embedding(self, mock_post):
        mock_post.return_value = Mock(status_code=200, json=lambda: {"embedding": []})
        with self.assertRaises(ollama_client.OllamaError):
            ollama_client.embed("butter")


if __name__ == "__main__":
    unittest.main()
