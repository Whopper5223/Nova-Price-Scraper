import unittest

from scraper.snap_eligibility import is_snap_eligible


class TestSnapEligibility(unittest.TestCase):
    def test_ordinary_grocery_items_are_eligible(self):
        self.assertTrue(is_snap_eligible("Chicken Breast", "meat-and-seafood"))
        self.assertTrue(is_snap_eligible("Orange Juice", "beverages"))
        self.assertTrue(is_snap_eligible("Bananas", "produce"))

    def test_alcohol_is_ineligible_by_keyword(self):
        self.assertFalse(is_snap_eligible("Bud Light 12pk", "beverages"))
        self.assertFalse(is_snap_eligible("Cabernet Red Wine", "beverages"))
        self.assertFalse(is_snap_eligible("Hard Seltzer Variety Pack", "beverages"))

    def test_alcohol_plural_forms_are_ineligible(self):
        # Regression: "Beers"/"Wines" previously slipped past the singular-only regex.
        self.assertFalse(is_snap_eligible("Assorted Beers", "beverages"))
        self.assertFalse(is_snap_eligible("Fine Wines Gift Set", "beverages"))

    def test_tobacco_is_ineligible(self):
        self.assertFalse(is_snap_eligible("Marlboro Cigarettes", None))
        self.assertFalse(is_snap_eligible("Cuban Cigars", None))

    def test_supplements_are_ineligible(self):
        self.assertFalse(is_snap_eligible("Multivitamin Gummies", "health-care"))
        self.assertFalse(is_snap_eligible("Ibuprofen 200mg", None))
        self.assertFalse(is_snap_eligible("Daily Vitamins", "health-care"))

    def test_hot_prepared_food_is_ineligible(self):
        self.assertFalse(is_snap_eligible("Rotisserie Chicken", "deli"))

    def test_non_food_departments_are_ineligible(self):
        self.assertFalse(is_snap_eligible("Paper Towels", "household"))
        self.assertFalse(is_snap_eligible("Laundry Detergent", "laundry"))
        self.assertFalse(is_snap_eligible("Dog Food", "pets"))

    def test_baby_department_splits_food_from_non_food(self):
        self.assertTrue(is_snap_eligible("Baby Formula", "baby"))
        self.assertFalse(is_snap_eligible("Diapers Size 3", "baby"))
        self.assertFalse(is_snap_eligible("Baby Wipes", "baby"))

    def test_ambiguous_departments_return_none(self):
        self.assertIsNone(is_snap_eligible("Sliced Turkey", "deli"))
        self.assertIsNone(is_snap_eligible("Mac and Cheese Cup", "prepared-foods"))

    def test_missing_name_or_department_does_not_crash(self):
        self.assertTrue(is_snap_eligible("", None))
        self.assertTrue(is_snap_eligible("Milk", None))


if __name__ == "__main__":
    unittest.main()
