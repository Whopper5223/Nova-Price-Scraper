import json
import tempfile
import unittest
from pathlib import Path

from scraper import markup_data, price_lookup


class TestClassifyDepartmentMapping(unittest.TestCase):
    """Departments that map straight to one category, no keyword check needed."""

    def test_produce_department_is_produce(self):
        self.assertEqual(markup_data.classify("Bananas", "produce"), "Produce")

    def test_beverages_department_is_beverages(self):
        self.assertEqual(markup_data.classify("Orange Juice", "beverages"), "Beverages")

    def test_snacks_and_candy_department_is_snacks(self):
        self.assertEqual(markup_data.classify("Kettle Chips", "snacks-and-candy"), "Snacks")

    def test_frozen_department_is_frozen(self):
        self.assertEqual(markup_data.classify("Cheese Pizza", "frozen"), "Frozen")

    def test_deli_department_is_deli(self):
        self.assertEqual(markup_data.classify("Sliced Turkey", "deli"), "Deli")

    def test_prepared_foods_department_is_prepared_foods(self):
        self.assertEqual(markup_data.classify("Mac and Cheese Cup", "prepared-foods"), "Prepared Foods")

    def test_canned_goods_department_is_canned_goods(self):
        self.assertEqual(markup_data.classify("Black Beans", "canned-goods"), "Canned Goods")

    def test_dry_goods_pasta_department_is_pasta_and_dry_goods(self):
        self.assertEqual(markup_data.classify("Spaghetti", "dry-goods-pasta"), "Pasta & Dry Goods")

    def test_condiments_sauces_department_is_condiments(self):
        self.assertEqual(markup_data.classify("Yellow Mustard", "condiments-sauces"), "Condiments")

    def test_oils_vinegars_spices_department_is_oils_and_spices(self):
        self.assertEqual(markup_data.classify("Olive Oil", "oils-vinegars-spices"), "Oils & Spices")

    def test_baking_essentials_department_is_baking_essentials(self):
        self.assertEqual(markup_data.classify("All-Purpose Flour", "baking-essentials"), "Baking Essentials")

    def test_department_with_no_markup_data_anywhere_is_dropped(self):
        self.assertIsNone(markup_data.classify("Dish Soap", "household"))
        self.assertIsNone(markup_data.classify("Diapers Size 3", "baby"))
        self.assertIsNone(markup_data.classify("Something", "miscellaneous-grocery"))
        self.assertIsNone(markup_data.classify("Something", "n-organic-90933"))

    def test_missing_department_is_dropped(self):
        self.assertIsNone(markup_data.classify("Mystery Item", None))


class TestClassifyDairy(unittest.TestCase):
    def test_plain_milk_is_milk(self):
        self.assertEqual(markup_data.classify("Friendly Farms Whole Milk", "dairy"), "Milk")

    def test_chocolate_milk_candy_is_not_milk(self):
        self.assertIsNone(markup_data.classify("Milk Chocolate Bar", "dairy"))

    def test_cheddar_is_cheese(self):
        self.assertEqual(markup_data.classify("Sharp Cheddar Cheese", "dairy"), "Cheese")

    def test_cream_cheese_is_cheese(self):
        self.assertEqual(markup_data.classify("Philadelphia Cream Cheese", "dairy"), "Cheese")

    def test_milk_based_cheese_is_cheese_not_milk(self):
        self.assertEqual(markup_data.classify("Whole Milk Mozzarella Cheese", "dairy"), "Cheese")

    def test_butter_is_butter_and_creamer(self):
        self.assertEqual(markup_data.classify("Salted Butter", "dairy"), "Butter & Creamer")

    def test_cheese_flavored_butter_is_butter_not_cheese(self):
        # Regression: this used to be misfiled as Cheese because of "parmesan".
        self.assertEqual(
            markup_data.classify("Specially Selected Garlic Parmesan Butter", "dairy"),
            "Butter & Creamer",
        )

    def test_creamer_is_butter_and_creamer(self):
        self.assertEqual(markup_data.classify("French Vanilla Coffee Creamer", "dairy"), "Butter & Creamer")

    def test_milk_based_creamer_is_creamer_not_milk(self):
        self.assertEqual(markup_data.classify("Almond Milk Creamer", "dairy"), "Butter & Creamer")

    def test_half_and_half_is_butter_and_creamer(self):
        self.assertEqual(markup_data.classify("Half & Half", "dairy"), "Butter & Creamer")
        self.assertEqual(markup_data.classify("Half and Half", "dairy"), "Butter & Creamer")

    def test_yogurt_is_yogurt(self):
        self.assertEqual(markup_data.classify("Vanilla Greek Yogurt", "dairy"), "Yogurt")

    def test_yogurt_made_with_milk_is_yogurt_not_milk(self):
        self.assertEqual(markup_data.classify("Lowfat Yogurt Made With Whole Milk", "dairy"), "Yogurt")

    def test_dairy_items_outside_every_category_are_dropped(self):
        self.assertIsNone(markup_data.classify("Large Eggs", "dairy"))
        self.assertIsNone(markup_data.classify("Sour Cream", "dairy"))
        self.assertIsNone(markup_data.classify("Buttermilk", "dairy"))


class TestClassifyBread(unittest.TestCase):
    def test_bread_in_baked_goods_is_bread(self):
        self.assertEqual(markup_data.classify("White Sandwich Bread", "baked-goods"), "Bread")

    def test_bagels_are_bread(self):
        self.assertEqual(markup_data.classify("Plain Bagels", "baked-goods"), "Bread")

    def test_breadcrumbs_are_not_bread(self):
        self.assertIsNone(markup_data.classify("Italian Bread Crumbs", "baked-goods"))

    def test_muffins_are_not_bread(self):
        self.assertIsNone(markup_data.classify("Blueberry Muffins", "baked-goods"))


class TestClassifyCereal(unittest.TestCase):
    def test_boxed_cereal_is_cereal(self):
        self.assertEqual(markup_data.classify("Honey Nut Toasted Oat Cereal", "breakfast-foods"), "Cereal")

    def test_oatmeal_is_cereal(self):
        self.assertEqual(markup_data.classify("Old Fashioned Oatmeal", "breakfast-foods"), "Cereal")

    def test_granola_is_cereal(self):
        self.assertEqual(markup_data.classify("Honey Almond Granola", "breakfast-foods"), "Cereal")

    def test_cereal_and_granola_bars_are_not_cereal(self):
        self.assertIsNone(markup_data.classify("Chewy Granola Bars", "breakfast-foods"))
        self.assertIsNone(markup_data.classify("Strawberry Cereal Bars", "breakfast-foods"))

    def test_other_breakfast_foods_are_dropped(self):
        self.assertIsNone(markup_data.classify("Pure Maple Syrup", "breakfast-foods"))
        self.assertIsNone(markup_data.classify("Buttermilk Pancake Mix", "breakfast-foods"))


class TestClassifyMeatAndSeafood(unittest.TestCase):
    def test_chicken_is_chicken(self):
        self.assertEqual(markup_data.classify("Boneless Chicken Breast", "meat-and-seafood"), "Chicken")

    def test_salmon_is_fish_and_seafood(self):
        self.assertEqual(markup_data.classify("Atlantic Salmon Fillet", "meat-and-seafood"), "Fish & Seafood")

    def test_shrimp_is_fish_and_seafood(self):
        self.assertEqual(markup_data.classify("Large Raw Shrimp", "meat-and-seafood"), "Fish & Seafood")

    def test_ground_beef_is_beef(self):
        self.assertEqual(markup_data.classify("80/20 Ground Beef", "meat-and-seafood"), "Beef")

    def test_ribeye_is_beef(self):
        self.assertEqual(markup_data.classify("Boneless Ribeye Steak", "meat-and-seafood"), "Beef")

    def test_salmon_steak_is_fish_not_beef(self):
        self.assertEqual(markup_data.classify("Salmon Steak", "meat-and-seafood"), "Fish & Seafood")

    def test_beef_hot_dogs_are_beef_because_the_name_says_beef(self):
        self.assertEqual(markup_data.classify("All Beef Hot Dogs", "meat-and-seafood"), "Beef")

    def test_other_meats_have_no_markup_data_and_are_dropped(self):
        self.assertIsNone(markup_data.classify("Pork Chops", "meat-and-seafood"))
        self.assertIsNone(markup_data.classify("Lean Turkey Burgers", "meat-and-seafood"))
        self.assertIsNone(markup_data.classify("Lamb Shoulder", "meat-and-seafood"))
        self.assertIsNone(markup_data.classify("Hickory Smoked Bacon", "meat-and-seafood"))


class TestCategoriesForTerm(unittest.TestCase):
    """The categories a search word itself points at, derived from the same
    keyword rules classify() uses -- so "cheese" means the Cheese category, not
    the mac-and-cheese that merely contains the word."""

    def test_staple_words_point_at_their_own_category(self):
        self.assertEqual(markup_data.categories_for_term("cheese"), {"Cheese"})
        self.assertEqual(markup_data.categories_for_term("milk"), {"Milk"})
        self.assertEqual(markup_data.categories_for_term("bread"), {"Bread"})
        self.assertEqual(markup_data.categories_for_term("butter"), {"Butter & Creamer"})
        self.assertEqual(markup_data.categories_for_term("yogurt"), {"Yogurt"})
        self.assertEqual(markup_data.categories_for_term("cereal"), {"Cereal"})
        self.assertEqual(markup_data.categories_for_term("chicken breast"), {"Chicken"})
        self.assertEqual(markup_data.categories_for_term("salmon"), {"Fish & Seafood"})

    def test_a_more_specific_phrase_still_finds_its_category(self):
        self.assertEqual(markup_data.categories_for_term("cheddar cheese"), {"Cheese"})
        self.assertEqual(markup_data.categories_for_term("whole milk"), {"Milk"})

    def test_words_that_only_a_whole_department_covers_point_nowhere(self):
        # produce, beverages, frozen etc. are whole departments with no keywords,
        # so nothing about the word itself says which category it is.
        self.assertEqual(markup_data.categories_for_term("bananas"), set())
        self.assertEqual(markup_data.categories_for_term("orange juice"), set())
        self.assertEqual(markup_data.categories_for_term("olive oil"), set())
        self.assertEqual(markup_data.categories_for_term("rice"), set())


class TestGetMarkupRealNumbers(unittest.TestCase):
    def test_stop_and_shop_beverages(self):
        self.assertEqual(markup_data.get_markup("Stop & Shop", "Beverages"), (0.2228, 18))

    def test_shaws_cereal_with_a_tiny_sample_is_still_used(self):
        self.assertEqual(markup_data.get_markup("Shaw's", "Cereal"), (0.0932, 2))

    def test_market_basket_deli_with_a_single_item_is_still_used(self):
        self.assertEqual(markup_data.get_markup("Market Basket", "Deli"), (0.2392, 1))

    def test_negative_markup_is_kept_as_is(self):
        self.assertEqual(markup_data.get_markup("Market Basket", "Baking Essentials"), (-0.1191, 4))
        self.assertEqual(markup_data.get_markup("Market Basket", "Cheese"), (-0.0127, 3))

    def test_shaws_produce_stays_the_fruit_and_vegetable_blend(self):
        self.assertEqual(markup_data.get_markup("Shaw's", "Produce"), (0.1106, 12))

    def test_market_basket_pasta_drops_the_size_mismatched_outlier(self):
        # The raw pivot says +68.47% (n=5), but one of those five is Goya Thai
        # Jasmine Rice, $2.99 on the website vs $13.99 on Instacart (+368%) --
        # a package-size mismatch the spreadsheet author's own review excluded.
        self.assertEqual(markup_data.get_markup("Market Basket", "Pasta & Dry Goods"), (-0.0639, 4))

    def test_market_basket_combined_meat_number_applies_to_each_meat_type(self):
        for category in ("Beef", "Chicken", "Fish & Seafood"):
            self.assertEqual(markup_data.get_markup("Market Basket", category), (0.2505, 1))

    def test_store_name_with_a_zero_width_space_still_resolves(self):
        # Instacart renders "Shaw's" as "Shaw​'s" in the scraped store name.
        self.assertEqual(markup_data.get_markup("Shaw​'s", "Cheese"), (0.1033, 31))


class TestGetMarkupAldi(unittest.TestCase):
    def test_aldi_is_pinned_to_zero_for_every_category(self):
        for category in markup_data.CATEGORIES:
            pct, _n = markup_data.get_markup("ALDI", category)
            self.assertEqual(pct, 0.0, category)


class TestGetMarkupHasNoFallback(unittest.TestCase):
    """A store+category cell with no real number stays empty -- no pooled
    average from other stores is invented to fill it."""

    def test_market_basket_milk_has_no_number(self):
        self.assertIsNone(markup_data.get_markup("Market Basket", "Milk"))

    def test_market_basket_beverages_has_no_number(self):
        self.assertIsNone(markup_data.get_markup("Market Basket", "Beverages"))

    def test_stop_and_shop_frozen_has_no_number(self):
        self.assertIsNone(markup_data.get_markup("Stop & Shop", "Frozen"))

    def test_unknown_store_has_no_number(self):
        self.assertIsNone(markup_data.get_markup("Wegmans", "Cheese"))

    def test_unknown_category_has_no_number(self):
        self.assertIsNone(markup_data.get_markup("Stop & Shop", "Household"))


class TestMarkupTableIntegrity(unittest.TestCase):
    def test_every_store_category_is_a_known_category(self):
        for store, cells in markup_data._RAW_MARKUP.items():
            for category in cells:
                self.assertIn(category, markup_data.CATEGORIES, f"{store}: {category}")

    def test_every_category_has_a_real_number_at_a_non_aldi_store(self):
        # A category nobody but ALDI's pinned 0% has data for would be priced
        # on no real evidence at all -- it shouldn't exist.
        for category in markup_data.CATEGORIES:
            real_stores = [
                store for store, cells in markup_data._RAW_MARKUP.items()
                if store != "ALDI" and category in cells
            ]
            self.assertTrue(real_stores, category)

    def test_baby_is_not_a_category(self):
        # The baby department mixes baby food with diapers and wipes, so it
        # stays excluded even though Market Basket has a number for it.
        self.assertNotIn("Baby", markup_data.CATEGORIES)


class TestEstimateRealPrice(unittest.TestCase):
    def test_divides_out_the_stores_markup(self):
        # 3.00 / 1.3106 = 2.289...
        self.assertEqual(markup_data.estimate_real_price(3.00, "Stop & Shop", "Cheese"), 2.29)

    def test_a_negative_markup_raises_the_estimate(self):
        # 3.00 / (1 - 0.0127) = 3.038...
        self.assertEqual(markup_data.estimate_real_price(3.00, "Market Basket", "Cheese"), 3.04)

    def test_aldi_estimate_equals_the_instacart_price(self):
        self.assertEqual(markup_data.estimate_real_price(4.15, "ALDI", "Beverages"), 4.15)

    def test_no_number_means_no_estimate(self):
        self.assertIsNone(markup_data.estimate_real_price(3.00, "Market Basket", "Milk"))
        self.assertIsNone(markup_data.estimate_real_price(3.00, "Wegmans", "Cheese"))


_FIXTURE = {
    "scraped_at": "2026-01-01T00:00:00+00:00",
    "location": "Test City",
    "stores": [
        {
            "name": "Market Basket",
            "url": "https://example.com/mb",
            "product_count": 3,
            "products": [
                {"name": "Frozen Pepperoni Pizza", "price": 5.00, "unit": None, "department": "frozen"},
                {"name": "Pizza Sauce", "price": 2.00, "unit": None, "department": "condiments-sauces"},
                {"name": "Pizza Party Plates", "price": 3.00, "unit": None, "department": "party-gifts"},
            ],
        },
        {
            "name": "Stop & Shop",
            "url": "https://example.com/ss",
            "product_count": 1,
            "products": [
                {"name": "Frozen Pepperoni Pizza", "price": 6.00, "unit": None, "department": "frozen"},
            ],
        },
    ],
}


class TestQueryPricesUsesTheFullCategorySet(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.fixture_path = Path(self._tmpdir.name) / "prices.json"
        self.fixture_path.write_text(json.dumps(_FIXTURE))

    def tearDown(self):
        self._tmpdir.cleanup()

    def _rows(self):
        results = price_lookup.query_prices("pizza", path=self.fixture_path)
        return {(r["store_name"], r["name"]): r for r in results}

    def test_a_category_beyond_the_original_five_is_now_priced(self):
        row = self._rows()[("Market Basket", "Frozen Pepperoni Pizza")]

        self.assertEqual(row["category"], "Frozen")
        # 5.00 / 1.2677 = 3.944...
        self.assertEqual(row["estimated_real_price"], 3.94)

    def test_store_without_a_number_for_the_category_keeps_the_row_with_no_estimate(self):
        row = self._rows()[("Stop & Shop", "Frozen Pepperoni Pizza")]

        self.assertEqual(row["category"], "Frozen")
        self.assertIsNone(row["estimated_real_price"])

    def test_departments_with_no_markup_data_are_still_dropped(self):
        names = {name for _store, name in self._rows()}

        self.assertNotIn("Pizza Party Plates", names)


if __name__ == "__main__":
    unittest.main()
