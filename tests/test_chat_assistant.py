import unittest

from scraper.chat_assistant import _parse_budget, _store_basket_totals


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


if __name__ == "__main__":
    unittest.main()
