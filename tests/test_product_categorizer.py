import unittest
from unittest.mock import patch

from scraper.product_categorizer import categorize_products, _batches, BATCH_SIZE


class TestBatches(unittest.TestCase):
    def test_splits_into_correct_batch_sizes(self):
        rows = [{"name": f"item{i}"} for i in range(60)]
        batches = list(_batches(rows, size=25))
        self.assertEqual([len(b) for b in batches], [25, 25, 10])

    def test_empty_input_yields_no_batches(self):
        self.assertEqual(list(_batches([])), [])


class TestCategorizeProducts(unittest.TestCase):
    @patch("scraper.product_categorizer.ollama_client.chat_json")
    def test_adds_category_and_subcategory(self, mock_chat_json):
        mock_chat_json.return_value = {"classifications": [
            {"subcategory": "Poultry Products"},
            {"subcategory": "Dairy and Egg Products"},
        ]}
        rows = [
            {"name": "Chicken Breast", "department": "meat-and-seafood"},
            {"name": "Whole Milk", "department": "dairy"},
        ]

        result = categorize_products(rows)

        self.assertEqual(result[0]["subcategory"], "Poultry Products")
        self.assertEqual(result[0]["category"], "Meats & Seafood")
        self.assertEqual(result[1]["subcategory"], "Dairy and Egg Products")
        self.assertEqual(result[1]["category"], "Dairy")

    @patch("scraper.product_categorizer.ollama_client.chat_json")
    def test_mismatched_response_length_falls_back_to_none(self, mock_chat_json):
        mock_chat_json.return_value = {"classifications": [{"subcategory": "Poultry Products"}]}  # only 1, but 2 rows
        rows = [{"name": "Chicken Breast"}, {"name": "Whole Milk"}]

        result = categorize_products(rows)

        self.assertIsNone(result[0]["category"])
        self.assertIsNone(result[1]["category"])

    @patch("scraper.product_categorizer.ollama_client.chat_json")
    def test_unrecognized_subcategory_falls_back_to_none(self, mock_chat_json):
        mock_chat_json.return_value = {"classifications": [{"subcategory": "Not A Real Category"}]}
        rows = [{"name": "Mystery Item"}]

        result = categorize_products(rows)

        self.assertIsNone(result[0]["subcategory"])
        self.assertIsNone(result[0]["category"])

    @patch("scraper.product_categorizer.ollama_client.chat_json")
    def test_non_list_response_falls_back_to_none(self, mock_chat_json):
        mock_chat_json.return_value = None  # unparseable JSON, per chat_json's own contract
        rows = [{"name": "Chicken Breast"}]

        result = categorize_products(rows)

        self.assertIsNone(result[0]["category"])

    @patch("scraper.product_categorizer.ollama_client.chat_json")
    def test_calls_are_batched_not_one_per_product(self, mock_chat_json):
        mock_chat_json.return_value = {"classifications": [{"subcategory": "Beverages"} for _ in range(BATCH_SIZE)]}
        rows = [{"name": f"item{i}"} for i in range(BATCH_SIZE)]

        categorize_products(rows)

        self.assertEqual(mock_chat_json.call_count, 1)

    @patch("scraper.product_categorizer.ollama_client.chat_json")
    def test_includes_a_worked_example_before_the_real_batch(self, mock_chat_json):
        mock_chat_json.return_value = {"classifications": [{"subcategory": "Beverages"}]}
        categorize_products([{"name": "Orange Soda"}])

        messages = mock_chat_json.call_args[0][0]
        self.assertEqual(len(messages), 3)
        self.assertEqual(messages[1]["role"], "assistant")


if __name__ == "__main__":
    unittest.main()
