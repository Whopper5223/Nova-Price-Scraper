"""
chat_assistant.py

Conversational grocery assistant backed by a local Ollama model.

You describe what you're cooking or shopping for; the model asks whatever it still
needs to know, turns the conversation into a shopping list, and then prices that list
against real scraped data — saved prices from prices_output.json when they're fresh,
a live Instacart search when they aren't.

The model never invents prices. It only ever summarizes the rows this module hands it.

Usage (from the repo root):
    python -m scraper.chat_assistant
    python -m scraper.chat_assistant --max-age-hours 24
    python -m scraper.chat_assistant --live            # always scrape live, ignore saved data

Requires Ollama running locally — see scraper/ollama_client.py for setup.
"""

import argparse
import asyncio
import json
import os
import re
import sys
from pathlib import Path

# Ensure repo root is on sys.path when running as a script
sys.path.insert(0, str(Path(__file__).parent.parent))

# Load .env from repo root if present
_env_file = Path(__file__).parent.parent / ".env"
if _env_file.exists():
    for _line in _env_file.read_text().splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _v = _line.split("=", 1)
            os.environ.setdefault(_k.strip(), _v.strip())

from scraper import ollama_client, price_lookup
from scraper.ollama_client import OllamaError

DEFAULT_MAX_STORES = 4
DEFAULT_RESULTS = 5

_URL_RE = re.compile(r"https?://\S+")

CHAT_SYSTEM_PROMPT = """You are Nova, a friendly grocery shopping assistant.

You help the user figure out what groceries they need — for a meal, a week of dinners,
a party, a diet, whatever they describe. Ask short, natural follow-up questions when
something important is unclear (how many people, any allergies or dislikes, do they
already have staples). Keep replies to a few sentences.

You do NOT know any prices. Never state, guess, or estimate a price, and never name a
store as cheapest. Once the list is settled, tell the user you'll go look up real prices.
The system looks up actual prices separately and shows them to the user."""

EXTRACT_SYSTEM_PROMPT = """You extract a grocery shopping list from a conversation.

Return ONLY a JSON object of this exact shape:
{"ready": true|false, "items": ["milk", "eggs"], "missing": "what is still unknown"}

READ THE ENTIRE CONVERSATION. Items come from everything the user has said across all
their messages, not just the most recent one. If the user says "chicken parm" in their
first message and "also need pasta" in a later one, the list must include BOTH the
ingredients for chicken parm AND pasta.

Include every ingredient a normal cook needs to make the dishes mentioned, even if the
user never listed them individually. For "chicken parm" that means chicken, marinara
sauce, mozzarella, parmesan, and breadcrumbs — not just the words the user typed.

Exclude anything the user says they already have or do not want.

NEVER invent items the user did not ask for or imply. If the user has not mentioned any
food, dish, meal, or grocery at all, return {"ready": false, "items": [], "missing": "..."}.
Greetings and small talk ("hi", "hey what's up", "thanks") contain no groceries — return
an empty list for those. An empty list is always correct when no food was discussed;
guessing common groceries is always wrong.

When the user HAS named food, set "ready" to true and list its ingredients. A question
the assistant asked EARLIER that the user has since ANSWERED is not a reason to wait;
only set "ready" to false if the user's most recent message asks the assistant a
question, or no food has been mentioned yet.

"items" must be plain grocery search terms a store search box would understand:
lowercase, no quantities, no units, no preparation notes, no brand names.
Good: "olive oil", "chicken breast", "yellow onion"
Bad: "2 tbsp olive oil", "1 lb chicken breast, diced", "onions (finely chopped)" """

SUMMARY_SYSTEM_PROMPT = """You are Nova, a grocery shopping assistant reporting price results.

You will be given the user's request and a JSON block of REAL price data that was just
scraped or read from saved scrape results.

Rules:
- Use ONLY the numbers in the JSON. Never invent, adjust, or estimate a price.
- If an item has no results, say plainly that you couldn't find a price for it.
- Call out the cheapest store per item, and the best overall store if one is clearly best.
- Give an approximate basket total using the cheapest price found for each item, and say
  it's approximate.
- Mention the store name and the specific product name behind each price.
- Be concise and conversational. Short paragraphs or a compact list. No markdown tables."""


# ---------------------------------------------------------------------------
# Turning the conversation into a shopping list
# ---------------------------------------------------------------------------

def _extract_list(history: list[dict]) -> dict:
    """
    Ask the model, in a separate JSON-mode call, whether the conversation has settled
    into a shopping list. Kept apart from the visible chat turn because small local
    models are much more reliable when each call has exactly one job.

    Returns {"ready": bool, "items": list[str], "missing": str}.
    """
    transcript = "\n".join(
        f"{'USER' if m['role'] == 'user' else 'ASSISTANT'}: {m['content']}" for m in history
    )
    # Restating everything the user asked for keeps the model from anchoring on only the
    # last turn, which is how it drops items mentioned earlier in the conversation.
    wants = " | ".join(m["content"] for m in history if m["role"] == "user")
    result = ollama_client.chat_json(
        [{
            "role": "user",
            "content": (
                f"Full conversation:\n{transcript}\n\n"
                f"Everything the user has asked for, across all their messages:\n{wants}\n\n"
                "Extract the complete shopping list JSON covering ALL of it."
            ),
        }],
        system=EXTRACT_SYSTEM_PROMPT,
    )

    if not isinstance(result, dict):
        return {"ready": False, "items": [], "missing": ""}

    items = [str(i).strip() for i in result.get("items", []) if str(i).strip()]
    # Cheap dedupe that also collapses case variants ("Milk" vs "milk").
    seen: set[str] = set()
    deduped = []
    for item in items:
        key = item.lower()
        if key not in seen:
            seen.add(key)
            deduped.append(item.lower())

    return {
        "ready": bool(result.get("ready")) and bool(deduped),
        "items": deduped,
        "missing": str(result.get("missing") or ""),
    }


# ---------------------------------------------------------------------------
# Pricing a confirmed list
# ---------------------------------------------------------------------------

def _price_from_saved(items: list[str], max_age_hours: int) -> tuple[dict, list[str]]:
    """
    Price what we can from prices_output.json.
    Returns (findings, unresolved) — unresolved items need a live lookup.
    """
    if price_lookup.is_stale(max_age_hours=max_age_hours):
        age = price_lookup.data_age()
        why = "no saved scrape found" if age is None else f"saved scrape is {age.days} day(s) old"
        print(f"[nova] Skipping saved prices ({why}).")
        return {}, list(items)

    findings: dict[str, list[dict]] = {}
    unresolved: list[str] = []

    for item in items:
        rows = price_lookup.cheapest_by_store(item)
        if rows:
            findings[item] = rows
        else:
            unresolved.append(item)

    if findings:
        print(f"[nova] Found {len(findings)} item(s) in saved prices.")
    return findings, unresolved


async def _price_live(items: list[str], max_stores: int, n_results: int) -> dict:
    """Live Instacart lookup for items saved data couldn't answer."""
    if not items:
        return {}

    # Imported here so the module loads (and --help works) without Playwright installed.
    from scraper.product_search import run_product_search

    print(f"[nova] Looking up {len(items)} item(s) live on Instacart — this opens a browser...")
    result = await run_product_search(
        items,
        n_results=n_results,
        max_stores=max_stores,
        verbose=False,
    )

    findings: dict[str, list[dict]] = {}
    for store in result.get("stores", []):
        for item, res in store.get("results", {}).items():
            if not res or res.get("count", 0) == 0:
                continue
            cheapest = min(res["products"], key=lambda p: p["price"]) if res.get("products") else None
            findings.setdefault(item, []).append({
                "store_name": store["name"],
                "store_url": store.get("url"),
                "name": cheapest["name"] if cheapest else item,
                "price": res["min"],
                "unit": cheapest.get("unit") if cheapest else None,
                "department": None,
            })

    for item in findings:
        findings[item].sort(key=lambda r: r["price"])
    return findings


def _summarize(user_request: str, findings: dict, missing: list[str]) -> str:
    """Have the model narrate the real numbers. Falls back to a plain table on failure."""
    payload = {
        "request": user_request,
        "priced_items": findings,
        "items_with_no_results": missing,
    }
    try:
        return ollama_client.chat(
            [{"role": "user", "content": json.dumps(payload, indent=2)}],
            system=SUMMARY_SYSTEM_PROMPT,
            temperature=0.3,
        )
    except OllamaError as e:
        print(f"[nova] Could not reach Ollama for the summary: {e}")
        return _plain_summary(findings, missing)


def _plain_summary(findings: dict, missing: list[str]) -> str:
    """Deterministic fallback so results are never lost to an LLM failure."""
    lines = []
    total = 0.0
    for item, rows in findings.items():
        best = rows[0]
        total += best["price"]
        lines.append(f"  {item:<24} ${best['price']:.2f}  at {best['store_name']}  ({best['name']})")
    if lines:
        lines.append(f"\n  Approximate basket total: ${total:.2f}")
    for item in missing:
        lines.append(f"  {item:<24} no price found")
    return "\n".join(lines) if lines else "  No prices found."


def _print_findings(findings: dict, missing: list[str]) -> None:
    """Show the raw rows behind the model's summary so prices are always verifiable."""
    if not findings and not missing:
        return
    print("\n" + "-" * 60)
    print("PRICE DATA")
    print("-" * 60)
    for item, rows in findings.items():
        print(f"  {item}")
        for row in rows[:5]:
            unit = f"  ({row['unit']})" if row.get("unit") else ""
            print(f"      ${row['price']:>7.2f}  {row['store_name']:<22} {row['name']}{unit}")
    for item in missing:
        print(f"  {item}\n      no results")
    print("-" * 60)


# ---------------------------------------------------------------------------
# Recipe URLs
# ---------------------------------------------------------------------------

async def _handle_recipe_url(url: str, max_stores: int) -> None:
    """A pasted recipe URL goes straight to the recipe pipeline — no list extraction needed."""
    from scraper.recipe_scraper import run_recipe_scraper, _print_comparison

    print(f"[nova] That's a recipe link — pulling its ingredients and pricing them...\n")
    result = await run_recipe_scraper(url, max_stores=max_stores, verbose=False)

    if result.get("errors"):
        for err in result["errors"]:
            print(f"[nova] {err}")
    if not result.get("stores"):
        return

    _print_comparison(result)

    findings: dict[str, list[dict]] = {}
    for store in result["stores"]:
        for ingredient, res in store.get("results", {}).items():
            findings.setdefault(ingredient, []).append({
                "store_name": store["name"],
                "name": res["products"][0]["name"] if res.get("products") else ingredient,
                "price": res["price_min"],
                "unit": res["products"][0].get("unit") if res.get("products") else None,
            })
    for item in findings:
        findings[item].sort(key=lambda r: r["price"])

    not_found = sorted({i for s in result["stores"] for i in s.get("not_found", [])})
    request = f"Price the ingredients for this recipe: {result.get('recipe_name') or url}"
    print("\n" + _summarize(request, findings, not_found) + "\n")


# ---------------------------------------------------------------------------
# Main conversation loop
# ---------------------------------------------------------------------------

def _confirm_list(items: list[str]) -> list[str] | None:
    """
    Show the extracted list and let the user accept, edit, or cancel it.
    Returns the final list, or None if the user wants to keep talking.
    """
    print("\n[nova] Here's the shopping list I've got:")
    for item in items:
        print(f"    - {item}")
    print("\n[nova] Look right? (y = price it / n = keep talking / or type a corrected,comma,separated,list)")

    answer = input("you > ").strip()
    if not answer or answer.lower() in {"y", "yes", "ok", "sure", "go", "yep"}:
        return items
    if answer.lower() in {"n", "no", "wait", "not yet"}:
        return None

    edited = [i.strip().lower() for i in answer.split(",") if i.strip()]
    return edited or None


async def _run_chat(max_age_hours: int, max_stores: int, n_results: int, force_live: bool) -> None:
    history: list[dict] = []

    print("=" * 60)
    print("NOVA GROCERY ASSISTANT")
    print("=" * 60)
    print(f"  Model  : {ollama_client.OLLAMA_MODEL}")
    print(f"  Prices : {price_lookup.describe_freshness()}")
    print("\n  Tell me what you're cooking or shopping for.")
    print("  Paste a recipe URL to price a whole recipe. Type 'quit' to exit.\n")

    while True:
        try:
            user_input = input("you > ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n[nova] Bye.")
            return

        if not user_input:
            continue
        if user_input.lower() in {"quit", "exit", "bye", "q"}:
            print("[nova] Bye.")
            return

        # A pasted recipe link bypasses list-building entirely.
        url_match = _URL_RE.search(user_input)
        if url_match:
            try:
                await _handle_recipe_url(url_match.group(0), max_stores)
            except Exception as e:
                print(f"[nova] Recipe lookup failed: {e}")
            history.clear()
            continue

        history.append({"role": "user", "content": user_input})

        # Visible conversational reply.
        try:
            reply = ollama_client.chat(history, system=CHAT_SYSTEM_PROMPT, temperature=0.7)
        except OllamaError as e:
            print(f"\n[nova] {e}\n")
            return
        history.append({"role": "assistant", "content": reply})
        print(f"\nnova > {reply}\n")

        # Hidden extraction call: has the list settled?
        extracted = _extract_list(history)
        if not extracted["ready"]:
            continue

        confirmed = _confirm_list(extracted["items"])
        if confirmed is None:
            print()
            continue

        if force_live:
            saved_findings, unresolved = {}, list(confirmed)
        else:
            saved_findings, unresolved = _price_from_saved(confirmed, max_age_hours)

        try:
            live_findings = await _price_live(unresolved, max_stores, n_results)
        except Exception as e:
            print(f"[nova] Live lookup failed: {e}")
            live_findings = {}

        findings = {**saved_findings, **live_findings}
        missing = [i for i in confirmed if i not in findings]

        _print_findings(findings, missing)

        request = next((m["content"] for m in history if m["role"] == "user"), "")
        print("\nnova > " + _summarize(request, findings, missing) + "\n")

        # Fresh slate so the next request isn't priced against this one's context.
        history.clear()
        print("[nova] Anything else you need?\n")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Nova Grocery Assistant — chat with a local Ollama model, get real prices",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python -m scraper.chat_assistant
  python -m scraper.chat_assistant --max-age-hours 24
  python -m scraper.chat_assistant --live
  python -m scraper.chat_assistant --model mistral

Setup:
  1. Install Ollama:  https://ollama.com
  2. Pull a model:    ollama pull llama3.1
  3. Get prices:      python -m scraper.scheduled_scrape
        """,
    )
    parser.add_argument("--model", metavar="NAME", default=None,
                        help=f"Ollama model to use (default: {ollama_client.OLLAMA_MODEL}).")
    parser.add_argument("--max-age-hours", type=int, default=price_lookup.DEFAULT_MAX_AGE_HOURS,
                        metavar="N",
                        help=f"Use saved prices only if newer than this (default: {price_lookup.DEFAULT_MAX_AGE_HOURS}).")
    parser.add_argument("--live", action="store_true",
                        help="Always scrape live, ignoring saved prices.")
    parser.add_argument("--max-stores", type=int, default=DEFAULT_MAX_STORES, metavar="N",
                        help=f"Stores to check on live lookups (default: {DEFAULT_MAX_STORES}).")
    parser.add_argument("--results", type=int, default=DEFAULT_RESULTS, metavar="N",
                        help=f"Products per item feeding live price ranges (default: {DEFAULT_RESULTS}).")

    args = parser.parse_args()

    if args.model:
        ollama_client.OLLAMA_MODEL = args.model

    warnings = ollama_client.check_ready()
    if warnings:
        print("[nova] Ollama isn't ready:\n")
        for w in warnings:
            print(f"  {w}\n")
        sys.exit(1)

    try:
        asyncio.run(_run_chat(
            max_age_hours=args.max_age_hours,
            max_stores=args.max_stores,
            n_results=args.results,
            force_live=args.live,
        ))
    except KeyboardInterrupt:
        print("\n[nova] Bye.")


if __name__ == "__main__":
    main()
