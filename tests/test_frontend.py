import json
import shutil
import subprocess
import unittest
from pathlib import Path

APP_JS = Path(__file__).parent.parent / "scraper" / "static" / "app.js"

# app.js is a plain browser script with no module system, so the pure functions under
# test are cut out of the real file and run in Node. escapeHtml is the only thing they
# need from the page, and the real one goes through a DOM node -- Node has no DOM, so
# this stands in with the same three-character escape.
_HARNESS = """
const escapeHtml = (v) => String(v ?? "")
  .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
%s
const args = JSON.parse(process.argv[1]);
process.stdout.write(JSON.stringify(%s(...args)));
"""


def _call(function: str, *args, helpers: tuple[str, ...] = ()):
    """Run app.js's `function` (plus any `helpers` it calls) in Node and return its result."""
    source = APP_JS.read_text(encoding="utf-8")
    parts = []
    for name in (*helpers, function):
        start = source.index(f"function {name}")
        end = source.index("\n}\n", start) + 3
        parts.append(source[start:end])
    result = subprocess.run(
        ["node", "-e", _HARNESS % ("\n".join(parts), function), json.dumps(list(args))],
        capture_output=True, text=True, timeout=20, check=True,
    )
    return json.loads(result.stdout)


def _render(text: str) -> str:
    return _call("renderMarkdown", text)


@unittest.skipUnless(shutil.which("node"), "node is not installed")
class TestRenderMarkdown(unittest.TestCase):
    """renderMarkdown turns LLM text into innerHTML for the chat bubbles and the
    price summary. Both are model output, so escaping has to come first."""

    def test_html_in_the_text_is_escaped_not_rendered(self):
        html = _render("<script>alert(1)</script> and <img src=x onerror=y>")

        self.assertNotIn("<script", html)
        self.assertNotIn("<img", html)
        self.assertIn("&lt;script&gt;", html)

    def test_bold_markers_become_strong(self):
        self.assertEqual(_render("**Cheapest:** Aldi"), "<p><strong>Cheapest:</strong> Aldi</p>")

    def test_dash_bullets_become_a_list(self):
        self.assertEqual(_render("- milk\n- bread"), "<ul><li>milk</li><li>bread</li></ul>")

    def test_asterisk_bullets_become_a_list_too(self):
        # The model sometimes writes "* milk"; shown raw, the asterisk leaks into the summary.
        self.assertEqual(_render("* milk\n* bread"), "<ul><li>milk</li><li>bread</li></ul>")

    def test_bold_at_the_start_of_a_line_is_not_mistaken_for_a_bullet(self):
        html = _render("**Total** is $5")

        self.assertNotIn("<ul>", html)
        self.assertIn("<strong>Total</strong>", html)

    def test_a_list_closes_before_the_following_paragraph(self):
        self.assertEqual(
            _render("- milk\nDone"),
            "<ul><li>milk</li></ul><p>Done</p>",
        )


@unittest.skipUnless(shutil.which("node"), "node is not installed")
class TestSummaryIsRenderedAsMarkdown(unittest.TestCase):
    def test_price_summary_goes_through_the_markdown_renderer(self):
        # It used to be escapeHtml(summary) in a pre-wrap box, so every "**" and
        # "* " the model wrote showed up literally under the prices.
        source = APP_JS.read_text(encoding="utf-8")

        self.assertTrue(
            '<div class="summary">${renderMarkdown(summary)}</div>' in source,
            "renderResults no longer renders the summary with renderMarkdown",
        )


_STOP_AND_SHOP_MILK = {
    "store_name": "Stop & Shop", "name": "Store Brand Whole Milk",
    "price": 3.16, "estimated_real_price": 2.37,
}
_ALDI_MILK = {
    "store_name": "ALDI", "name": "Friendly Farms Whole Milk",
    "price": 1.59, "estimated_real_price": 1.59,
}
_MARKET_BASKET_YOGURT = {
    "store_name": "Market Basket", "name": "Chobani Greek Yogurt",
    "price": 1.29, "estimated_real_price": None,
}


@unittest.skipUnless(shutil.which("node"), "node is not installed")
class TestRenderItemRow(unittest.TestCase):
    """An item row leads with the estimated in-store price (what the totals are built
    from) and keeps the Instacart price small; other stores follow as 'Also at' lines."""

    def _row(self, item, rows):
        return _call("renderItemRow", item, rows, helpers=("formatPrice",))

    def test_the_estimate_is_the_big_number_with_the_instacart_price_beside_it(self):
        html = self._row("milk", [_STOP_AND_SHOP_MILK])

        self.assertIn("~$2.37", html)
        self.assertIn("Instacart $3.16", html)

    def test_a_store_with_no_estimate_shows_its_instacart_price_labelled_as_such(self):
        html = self._row("yogurt", [_MARKET_BASKET_YOGURT])

        self.assertIn("$1.29", html)
        self.assertIn("Instacart price", html)
        self.assertNotIn("~$", html)

    def test_other_stores_are_listed_under_also_at(self):
        html = self._row("milk", [_ALDI_MILK, _STOP_AND_SHOP_MILK])

        self.assertIn("Also at", html)
        also = html[html.index("Also at"):]
        self.assertIn("Stop & Shop".replace("&", "&amp;"), also)
        self.assertIn("Store Brand Whole Milk", also)
        self.assertIn("~$2.37", also)

    def test_a_single_store_has_no_also_at_section(self):
        html = self._row("milk", [_ALDI_MILK])

        self.assertNotIn("Also at", html)

    def test_product_and_store_names_are_escaped(self):
        evil = {"store_name": "<b>S</b>", "name": "<img src=x onerror=y>", "price": 1.0,
                "estimated_real_price": None}

        html = self._row("milk", [evil, {**evil, "name": "<script>z</script>"}])

        self.assertNotIn("<img", html)
        self.assertNotIn("<script", html)
        self.assertNotIn("<b>S", html)


@unittest.skipUnless(shutil.which("node"), "node is not installed")
class TestRenderStoreTotals(unittest.TestCase):
    def _totals(self, store_totals, budget=None):
        return _call("renderStoreTotals", store_totals, budget)

    def _store(self, name, total, covers_all=True, sticker=(), missing=()):
        return {"store_name": name, "total": total, "covers_all_items": covers_all,
                "items_covered": 2, "items_total": 2 + len(missing),
                "missing_items": list(missing), "sticker_priced_items": list(sticker)}

    def test_totals_are_labelled_as_estimated_in_store_prices(self):
        html = self._totals([self._store("ALDI", 5.38)])

        self.assertIn("estimated in-store", html)

    def test_items_counted_at_their_instacart_price_are_called_out(self):
        html = self._totals([self._store("Market Basket", 3.79, sticker=["yogurt"])])

        self.assertIn("yogurt", html)
        self.assertIn("Instacart price", html)

    def test_a_store_priced_entirely_on_estimates_has_no_such_note(self):
        html = self._totals([self._store("Stop & Shop", 2.37)])

        self.assertNotIn("Instacart price", html)

    def test_only_a_store_with_everything_can_be_tagged_cheapest(self):
        html = self._totals([
            self._store("Full", 5.00),
            self._store("Partial", 1.00, covers_all=False, missing=["eggs"]),
        ])

        self.assertEqual(html.count("cheapest"), 1)


if __name__ == "__main__":
    unittest.main()
