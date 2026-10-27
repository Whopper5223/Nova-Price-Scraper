import json
import shutil
import subprocess
import unittest
from pathlib import Path

APP_JS = Path(__file__).parent.parent / "scraper" / "static" / "app.js"

# app.js is a plain browser script with no module system, so the one function under
# test is cut out of the real file and run in Node. escapeHtml is the only thing it
# needs from the page, and the real one goes through a DOM node -- Node has no DOM,
# so this stands in with the same five-character escape.
_HARNESS = """
const escapeHtml = (v) => String(v ?? "")
  .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
%s
process.stdout.write(JSON.stringify(renderMarkdown(JSON.parse(process.argv[1]))));
"""


def _render(text: str) -> str:
    source = APP_JS.read_text(encoding="utf-8")
    start = source.index("function renderMarkdown")
    end = source.index("\n}\n", start) + 3
    result = subprocess.run(
        ["node", "-e", _HARNESS % source[start:end], json.dumps(text)],
        capture_output=True, text=True, timeout=20, check=True,
    )
    return json.loads(result.stdout)


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


if __name__ == "__main__":
    unittest.main()
