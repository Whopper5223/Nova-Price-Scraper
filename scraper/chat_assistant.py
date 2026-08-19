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
from scraper.snap_eligibility import is_snap_eligible

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
{"ready": <true or false>, "items": [<lowercase grocery search terms, or empty list>],
 "missing": <one short sentence on what's still unclear, or empty string>,
 "budget": <a plain number with no dollar sign, or null>}

That is a description of the SHAPE only, not sample content — no placeholder word in it
is a real grocery item, so never copy anything from this instruction block into "items".
Every item in your answer must trace back to something the user actually wrote below.

"budget" is a dollar amount ONLY if the user stated a spending limit ("under $30",
"I've got about 40 bucks", "keep it cheap, budget is 25"). If no budget was mentioned,
use null.

You will be given only the USER's own messages, concatenated across the whole
conversation — the assistant's replies are deliberately left out. Build the list from
these user messages alone. A dish or ingredient named in an earlier message counts just
as much as one named in the latest message — combine everything the user has asked for
across every message into one list, not just the most recent one.

If the assistant previously suggested dishes, ingredients, or options, those suggestions
are NOT shown to you here and must NOT appear in "items" unless the user's own words
also named them. A dish the user has not personally named is not on the list yet — this
is what stops half-picked assistant suggestions from being priced before the user agrees
to them.

If the user names a prepared dish or meal, include every ingredient a normal cook would
need to make it, not only the words the user typed — draw on your own general cooking
knowledge to expand the dish into its typical components. Do not reuse any example
ingredient list, because none is given here on purpose.

Exclude anything the user says they already have or do not want.

Budget amounts, headcounts ("2 of us", "for 4 people"), and diet words (vegan,
vegetarian, gluten-free, keto, dairy-free, an allergy, etc.) describe the shopping trip
but are never themselves grocery items — never put a diet word or a number of people
into "items". If a message contains ONLY that kind of context and names no actual dish,
meal, or ingredient, that message has not mentioned any food yet.

NEVER invent items the user did not ask for or imply — this includes not filling in
"obvious" pantry staples (rice, beans, oil, spices, etc.) the user never named. If the
user has not mentioned any specific food, dish, meal, or ingredient by name, set "ready"
to false and "items" to an empty list, even if they gave you budget, diet, or headcount
info. Greetings and small talk ("hi", "hey what's up", "thanks") also contain no
groceries — return an empty list for those. An empty list is always correct when no food
was discussed; inventing items when none were mentioned is always wrong, even if a word
feels familiar from earlier in this prompt.

When the user HAS named food, set "ready" to true and list its ingredients. Only set
"ready" to false if the user's most recent message asks a question rather than stating
what they want, or if no food has been mentioned yet.

"items" must be plain grocery search terms a store search box would understand:
lowercase product names only — no quantities, no units, no preparation notes, no brand
names. Describe what the item IS, not how much of it there is or how it's prepared."""

SUMMARY_SYSTEM_PROMPT = """You are Nova, a grocery shopping assistant reporting price results.

You will be given the user's request and a JSON block of REAL price data that was just
scraped or read from saved scrape results.

Rules:
- Use ONLY the numbers in the JSON. Never invent, adjust, or estimate a price.
- "priced_items" is a dict keyed by item name — every key in it HAS at least one real
  price row. Only say "no price found" / "no results" for an item if it appears in
  "items_with_no_results", never for an item that is a key in "priced_items".
- Mention the store name and the specific product name behind each price.
- Each row has a snap_eligible field: true (SNAP/EBT eligible), false (not eligible —
  e.g. alcohol, hot prepared food, household goods, supplements), or null (uncertain,
  user should double-check at checkout). Flag any item that is false or null so an
  EBT shopper isn't surprised at checkout.
- Note that whether a store or order actually accepts EBT payment depends on that
  retailer, not on this list — this only tells you which items would qualify.
- You are also given "store_totals": one entry per store, with the total cost of
  buying everything there in one trip (covers_all_items = true means that store has
  every item), and which items (if any) that store is missing. Recommend the cheapest
  store with covers_all_items = true as the one-stop option and give its total. If no
  store covers everything, say so plainly and name the closest option and what it's
  missing — do not pretend a partial store covers the whole list.
- Do NOT suggest splitting the trip across multiple stores or compute any cross-store
  total yourself. Only ever report a single store's total (from "store_totals"). This
  is a one-stop-shop recommendation only.
- If "budget" is not null, compare it against the one-stop total. Say clearly whether
  it fits, and by how much. If it doesn't fit anywhere, say so and suggest which item(s)
  to drop to get under budget (pick the priciest ones).
- Be concise and conversational. Short paragraphs or a compact list. No markdown tables."""


# ---------------------------------------------------------------------------
# Turning the conversation into a shopping list
# ---------------------------------------------------------------------------

def _parse_budget(raw) -> float | None:
    """Coerce whatever the model put in "budget" into a clean float, or None."""
    try:
        return round(float(raw), 2) if raw is not None else None
    except (TypeError, ValueError):
        return None


def _extract_list(history: list[dict]) -> dict:
    """
    Ask the model, in a separate JSON-mode call, whether the conversation has settled
    into a shopping list. Kept apart from the visible chat turn because small local
    models are much more reliable when each call has exactly one job.

    Returns {"ready": bool, "items": list[str], "missing": str, "budget": float | None}.
    """
    # Only the user's own messages are sent — never the assistant's. If the assistant
    # suggests a dish the user hasn't picked yet, it must not be extractable as an item;
    # restricting the model's input to user text enforces that structurally instead of
    # relying on it to infer whose suggestion is whose from a mixed transcript.
    wants = " | ".join(m["content"] for m in history if m["role"] == "user")

    def _turn(user_wants: str) -> dict:
        return {
            "role": "user",
            "content": (
                f"Everything the user has asked for, across all their messages:\n{user_wants}\n\n"
                "Extract the complete shopping list JSON covering ALL of it."
            ),
        }

    # A worked example, as an actual prior turn rather than prose in the system prompt.
    # Small local models pattern-match a shown input/output pair far more reliably than
    # a written rule — and a rule alone previously caused "vegan" itself (and invented
    # staples like "rice") to leak into "items" for budget/diet/headcount-only messages.
    demo_input = "I've got fifteen dollars, no dairy, and it's just me eating"
    demo_output = (
        '{"ready": false, "items": [], '
        '"missing": "no specific dish or ingredient named yet", "budget": 15.0}'
    )

    result = ollama_client.chat_json(
        [
            _turn(demo_input),
            {"role": "assistant", "content": demo_output},
            _turn(wants),
        ],
        system=EXTRACT_SYSTEM_PROMPT,
    )

    if not isinstance(result, dict):
        return {"ready": False, "items": [], "missing": "", "budget": None}

    items = [str(i).strip() for i in result.get("items", []) if str(i).strip()]
    # Cheap dedupe that also collapses case variants ("Milk" vs "milk").
    seen: set[str] = set()
    deduped = []
    for item in items:
        key = item.lower()
        if key not in seen:
            seen.add(key)
            deduped.append(item.lower())

    budget = _parse_budget(result.get("budget"))

    return {
        "ready": bool(result.get("ready")) and bool(deduped),
        "items": deduped,
        "missing": str(result.get("missing") or ""),
        "budget": budget,
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
            name = cheapest["name"] if cheapest else item
            findings.setdefault(item, []).append({
                "store_name": store["name"],
                "store_url": store.get("url"),
                "name": name,
                "price": res["min"],
                "unit": cheapest.get("unit") if cheapest else None,
                "department": None,
                "snap_eligible": is_snap_eligible(name),
            })

    for item in findings:
        findings[item].sort(key=lambda r: r["price"])
    return findings


def _store_basket_totals(findings: dict, all_items: list[str]) -> list[dict]:
    """
    For each store that appears in any item's rows, total up what buying everything
    it stocks would cost — the "one-stop shop" view a real EBT/budget shopper needs,
    as opposed to a per-item cheapest that might mean visiting four different stores.

    Each row in findings[item] is already one-per-store (price_lookup.cheapest_by_store
    or the live-lookup equivalent), so this is just a pivot: item->store->price
    becomes store->total, with coverage tracked so a store missing half the list
    doesn't masquerade as a bargain.
    """
    store_prices: dict[str, dict[str, float]] = {}
    for item, rows in findings.items():
        for row in rows:
            store_prices.setdefault(row["store_name"], {})[item] = row["price"]

    totals = []
    for store, prices in store_prices.items():
        covered = [i for i in all_items if i in prices]
        totals.append({
            "store_name": store,
            "covers_all_items": len(covered) == len(all_items),
            "items_covered": len(covered),
            "items_total": len(all_items),
            "missing_items": [i for i in all_items if i not in prices],
            "total": round(sum(prices[i] for i in covered), 2),
        })
    totals.sort(key=lambda r: (not r["covers_all_items"], r["total"]))
    return totals


def _summarize(user_request: str, findings: dict, missing: list[str], all_items: list[str],
                budget: float | None = None) -> str:
    """Have the model narrate the real numbers. Falls back to a plain table on failure."""
    payload = {
        "request": user_request,
        "budget": budget,
        "priced_items": findings,
        "items_with_no_results": missing,
        "store_totals": _store_basket_totals(findings, all_items),
    }
    try:
        return ollama_client.chat(
            [{"role": "user", "content": json.dumps(payload, indent=2)}],
            system=SUMMARY_SYSTEM_PROMPT,
            temperature=0.1,
        )
    except OllamaError as e:
        print(f"[nova] Could not reach Ollama for the summary: {e}")
        return _plain_summary(findings, missing, all_items, budget)


def _snap_tag(eligible: bool | None) -> str:
    if eligible is True:
        return ""
    if eligible is False:
        return "  [not SNAP/EBT eligible]"
    return "  [SNAP eligibility unclear — verify at checkout]"


def _plain_summary(findings: dict, missing: list[str], all_items: list[str],
                    budget: float | None = None) -> str:
    """Deterministic fallback so results are never lost to an LLM failure."""
    lines = []
    for item, rows in findings.items():
        best = rows[0]
        tag = _snap_tag(best.get("snap_eligible"))
        lines.append(f"  {item:<24} ${best['price']:.2f}  at {best['store_name']}  ({best['name']}){tag}")

    store_totals = _store_basket_totals(findings, all_items)
    one_stop = next((s for s in store_totals if s["covers_all_items"]), None)
    if one_stop:
        lines.append(f"\n  Best one-stop store: {one_stop['store_name']} — ${one_stop['total']:.2f} for everything")
    elif store_totals:
        best_partial = store_totals[0]
        lines.append(
            f"\n  No single store has everything. Closest: {best_partial['store_name']} "
            f"(${best_partial['total']:.2f}, missing {', '.join(best_partial['missing_items'])})"
        )

    if budget is not None and (one_stop or store_totals):
        reference = one_stop["total"] if one_stop else store_totals[0]["total"]
        if reference <= budget:
            lines.append(f"  Within budget: ${reference:.2f} of ${budget:.2f}")
        else:
            lines.append(f"  Over budget: ${reference:.2f} vs ${budget:.2f} (${reference - budget:.2f} over)")

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
            tag = _snap_tag(row.get("snap_eligible"))
            print(f"      ${row['price']:>7.2f}  {row['store_name']:<22} {row['name']}{unit}{tag}")
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
            name = res["products"][0]["name"] if res.get("products") else ingredient
            findings.setdefault(ingredient, []).append({
                "store_name": store["name"],
                "name": name,
                "price": res["price_min"],
                "unit": res["products"][0].get("unit") if res.get("products") else None,
                "snap_eligible": is_snap_eligible(name),
            })
    for item in findings:
        findings[item].sort(key=lambda r: r["price"])

    not_found = sorted({i for s in result["stores"] for i in s.get("not_found", [])})
    request = f"Price the ingredients for this recipe: {result.get('recipe_name') or url}"
    all_items = list(findings.keys()) + not_found
    print("\n" + _summarize(request, findings, not_found, all_items) + "\n")


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


async def _run_chat(max_age_hours: int, max_stores: int, n_results: int, force_live: bool,
                     live_fallback: bool) -> None:
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

        live_findings = {}
        if unresolved and (force_live or live_fallback):
            try:
                live_findings = await _price_live(unresolved, max_stores, n_results)
            except Exception as e:
                print(f"[nova] Live lookup failed: {e}")
        elif unresolved:
            print(f"[nova] {len(unresolved)} item(s) not in saved prices — "
                  f"skipping live lookup (pass --live-fallback to enable it).")

        findings = {**saved_findings, **live_findings}
        missing = [i for i in confirmed if i not in findings]

        _print_findings(findings, missing)

        request = next((m["content"] for m in history if m["role"] == "user"), "")
        print("\nnova > " + _summarize(request, findings, missing, confirmed, extracted["budget"]) + "\n")

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
    parser.add_argument("--live-fallback", action="store_true",
                        help="Scrape live for items missing from saved prices (default: report them "
                             "as not found — the scraper is meant to run on its own schedule via "
                             "scheduled_scrape.py, not per-chat).")
    parser.add_argument("--max-stores", type=int, default=DEFAULT_MAX_STORES, metavar="N",
                        help=f"Stores to check on live lookups (default: {DEFAULT_MAX_STORES}).")
    parser.add_argument("--results", type=int, default=DEFAULT_RESULTS, metavar="N",
                        help=f"Products per item feeding live price ranges (default: {DEFAULT_RESULTS}).")
    parser.add_argument("--prices-file", metavar="PATH", default=None,
                        help="Use a different saved-prices JSON instead of prices_output.json "
                             "(e.g. a sample multi-store dataset for testing).")

    args = parser.parse_args()

    if args.model:
        ollama_client.OLLAMA_MODEL = args.model
    if args.prices_file:
        price_lookup.set_prices_path(Path(args.prices_file))

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
            live_fallback=args.live_fallback,
        ))
    except KeyboardInterrupt:
        print("\n[nova] Bye.")


if __name__ == "__main__":
    main()
