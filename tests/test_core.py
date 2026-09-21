"""
Closes the "known gap" noted in tasks/plan.md: core.search_products() had zero
prior test coverage. This file targets specifically what Task 5 (RAG product
matching) changed — lexical (word-boundary + stemming) matching against the
search query — using lightweight async fakes instead of a real Playwright
page. Card-level scraping (get_card_name/get_card_price) is mocked out;
testing that extraction logic is a separate, pre-existing concern unrelated
to this feature.

Stage 2 (product_embeddings.filter_by_relevance, a semantic embedding filter
on top of the lexical match) was unwired from search_products() per
docs/specs/rag-product-matching-spec.md — proven unreliable via live testing.
The function itself and its own unit tests remain in product_embeddings.py /
test_product_embeddings.py for a possible future call site; this file only
covers the search_products() call site, which no longer calls it.
"""

import unittest
from unittest.mock import AsyncMock, patch

from scraper import core


class FakeElement:
    """Stand-in for a Playwright Locator scoped to a single element."""

    def __init__(self, text: str = "", visible: bool = False, count_value: int = 1):
        self._text = text
        self._visible = visible
        self._count_value = count_value

    @property
    def first(self):
        return self

    async def is_visible(self, timeout=None):
        return self._visible

    async def click(self):
        pass

    async def fill(self, value):
        pass

    async def type(self, value, delay=None):
        pass

    async def inner_text(self):
        return self._text

    async def wait_for(self, state=None, timeout=None):
        pass

    async def count(self):
        return self._count_value


class FakeCard:
    """Stand-in for a single product card Locator — only .locator() is used
    directly by search_products() (for the unit text); name/price go through
    the mocked get_card_name/get_card_price."""

    def __init__(self, unit_text: str | None = None):
        self._unit_text = unit_text

    def locator(self, selector):
        if self._unit_text is None:
            return FakeElement(count_value=0)
        return FakeElement(text=self._unit_text, count_value=1)


class FakeCardLocator:
    """Stand-in for the product_card Locator: many cards, indexed by .nth()."""

    def __init__(self, cards: list[FakeCard]):
        self._cards = cards

    def nth(self, i):
        return self._cards[i]

    @property
    def first(self):
        return self._cards[0] if self._cards else FakeElement()

    async def count(self):
        return len(self._cards)


def _patch_common(names_and_prices: list[tuple[str, float]], search_input_count=1):
    """
    Returns the patch context managers needed to drive search_products()
    through a fake three-card (or fewer) search result, with card scraping
    mocked to the given (name, price) pairs in order.
    """
    cards = [FakeCard() for _ in names_and_prices]
    card_locator = FakeCardLocator(cards)
    search_locator = FakeElement(visible=True, count_value=search_input_count)

    async def fake_try_select(page, selector_key, selectors, timeout=8000):
        return search_locator if selector_key == "search_input" else card_locator

    return card_locator, search_locator, fake_try_select


def _peek_name(card, names_and_prices, card_locator):
    idx = card_locator._cards.index(card)
    return names_and_prices[idx][0]


def _peek_price(card, names_and_prices, card_locator):
    idx = card_locator._cards.index(card)
    return names_and_prices[idx][1]


class TestSearchProductsLexicalOnly(unittest.IsolatedAsyncioTestCase):
    """Covers search_products()'s lexical matching step now that Stage 2
    (semantic embedding filtering) has been unwired — not the whole
    search_products() function (browser interaction, healing, scrolling are
    pre-existing, untouched behavior outside this feature's scope)."""

    def setUp(self):
        self.selectors = {
            "search_input": "input.search",
            "product_card": "div.card",
            "product_unit": "span.unit",
        }

    async def _run_search(self, names_and_prices, query="butter"):
        card_locator, search_locator, fake_try_select = _patch_common(names_and_prices)

        async def fake_get_card_name(card, selectors):
            return _peek_name(card, names_and_prices, card_locator)

        async def fake_get_card_price(card, selectors):
            return _peek_price(card, names_and_prices, card_locator)

        with patch("scraper.core._try_select", side_effect=fake_try_select), \
             patch("scraper.core.get_card_name", side_effect=fake_get_card_name), \
             patch("scraper.core.get_card_price", side_effect=fake_get_card_price), \
             patch("scraper.core._human_delay", new=AsyncMock()), \
             patch("scraper.core._dismiss_modals", new=AsyncMock()), \
             patch("scraper.core._scroll_to_load_more", new=AsyncMock()):
            page = AsyncMock()
            page.keyboard.press = AsyncMock()
            return await core.search_products(page, query, self.selectors, n=5)

    @patch("scraper.core.product_embeddings.filter_by_relevance")
    async def test_never_calls_relevance_filter(self, mock_filter):
        # Stage 2 is unwired from this call site entirely — it must not run
        # even when lexical matches exist for it to (hypothetically) narrow.
        result = await self._run_search([("Salted Butter", 4.00), ("Butter Cookies", 2.50)])

        mock_filter.assert_not_called()
        self.assertEqual(result["count"], 2)

    async def test_lexical_matches_become_final_products_unfiltered(self):
        # Both names lexically match "butter" as a whole word, and with Stage 2
        # gone neither is narrowed out — this is the intentional Stage-1-only
        # tradeoff, not a bug.
        result = await self._run_search([("Salted Butter", 4.00), ("Butter Cookies", 2.50)])

        self.assertEqual(result["count"], 2)
        self.assertEqual({p["name"] for p in result["products"]}, {"Salted Butter", "Butter Cookies"})

    async def test_results_sorted_cheapest_first(self):
        result = await self._run_search([("Salted Butter", 4.00), ("Butter Cookies", 2.50)])

        self.assertEqual([p["name"] for p in result["products"]], ["Butter Cookies", "Salted Butter"])

    async def test_falls_back_to_unmatched_when_no_lexical_match(self):
        # Query word "gadget" won't lexically match either product name, so
        # search_products falls back to `unmatched` — still unfiltered.
        result = await self._run_search(
            [("Salted Butter", 4.00), ("Butter Cookies", 2.50)], query="gadget"
        )

        self.assertEqual(result["count"], 2)  # unmatched fallback, unfiltered


if __name__ == "__main__":
    unittest.main()
