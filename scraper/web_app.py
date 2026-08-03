"""
web_app.py

Local browser front end for chat_assistant.py — same conversation, extraction,
and pricing logic, just easier to click through than a terminal prompt.

Single-user, single conversation in memory: this is a local testing tool, not a
multi-tenant server. Run it, open the browser, refresh to reset.

Usage:
    python -m scraper.web_app
    python -m scraper.web_app --model llama3.1 --max-age-hours 200
"""

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import os
_env_file = Path(__file__).parent.parent / ".env"
if _env_file.exists():
    for _line in _env_file.read_text().splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _v = _line.split("=", 1)
            os.environ.setdefault(_k.strip(), _v.strip())

from flask import Flask, jsonify, request, send_from_directory

from scraper import ollama_client, price_lookup
from scraper.ollama_client import OllamaError
from scraper.chat_assistant import (
    CHAT_SYSTEM_PROMPT,
    DEFAULT_MAX_STORES,
    DEFAULT_RESULTS,
    _extract_list,
    _price_from_saved,
    _price_live,
    _store_basket_totals,
    _summarize,
)

app = Flask(__name__, static_folder=str(Path(__file__).parent / "static"), static_url_path="")

# Single in-memory conversation — see module docstring.
STATE = {"history": [], "max_age_hours": price_lookup.DEFAULT_MAX_AGE_HOURS,
         "max_stores": DEFAULT_MAX_STORES, "n_results": DEFAULT_RESULTS, "force_live": False,
         "live_fallback": False, "budget": None}


@app.get("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


@app.get("/api/status")
def status():
    return jsonify({
        "model": ollama_client.OLLAMA_MODEL,
        "prices": price_lookup.describe_freshness(),
    })


@app.post("/api/chat")
def chat():
    message = (request.json or {}).get("message", "").strip()
    if not message:
        return jsonify({"error": "empty message"}), 400

    STATE["history"].append({"role": "user", "content": message})

    try:
        reply = ollama_client.chat(STATE["history"], system=CHAT_SYSTEM_PROMPT, temperature=0.7)
    except OllamaError as e:
        STATE["history"].pop()
        return jsonify({"error": str(e)}), 503

    STATE["history"].append({"role": "assistant", "content": reply})

    extracted = _extract_list(STATE["history"])
    STATE["budget"] = extracted["budget"]
    return jsonify({
        "reply": reply,
        "ready": extracted["ready"],
        "items": extracted["items"],
        "missing": extracted["missing"],
        "budget": extracted["budget"],
    })


@app.post("/api/price")
def price():
    body = request.json or {}
    items = [str(i).strip().lower() for i in body.get("items", []) if str(i).strip()]
    budget = body.get("budget", STATE.get("budget"))
    if not items:
        return jsonify({"error": "no items"}), 400

    if STATE["force_live"]:
        saved_findings, unresolved = {}, list(items)
    else:
        saved_findings, unresolved = _price_from_saved(items, STATE["max_age_hours"])

    live_findings = {}
    live_error = None
    if unresolved and (STATE["force_live"] or STATE["live_fallback"]):
        try:
            live_findings = asyncio.run(_price_live(unresolved, STATE["max_stores"], STATE["n_results"]))
        except Exception as e:
            live_error = str(e)
    elif unresolved:
        live_error = (f"{len(unresolved)} item(s) not in saved prices — live lookup is off by "
                      f"default (start with --live-fallback to enable it).")

    findings = {**saved_findings, **live_findings}
    missing = [i for i in items if i not in findings]

    request_text = next((m["content"] for m in STATE["history"] if m["role"] == "user"), "")
    summary = _summarize(request_text, findings, missing, items, budget)
    store_totals = _store_basket_totals(findings, items)

    STATE["history"].clear()
    STATE["budget"] = None

    return jsonify({
        "findings": findings,
        "missing": missing,
        "summary": summary,
        "live_error": live_error,
        "store_totals": store_totals,
        "budget": budget,
    })


@app.post("/api/reset")
def reset():
    STATE["history"].clear()
    STATE["budget"] = None
    return jsonify({"ok": True})


def main() -> None:
    parser = argparse.ArgumentParser(description="Nova Grocery Assistant — web UI")
    parser.add_argument("--model", metavar="NAME", default=None)
    parser.add_argument("--max-age-hours", type=int, default=price_lookup.DEFAULT_MAX_AGE_HOURS, metavar="N")
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--live-fallback", action="store_true",
                         help="Scrape live for items missing from saved prices (default: report "
                              "them as not found).")
    parser.add_argument("--max-stores", type=int, default=DEFAULT_MAX_STORES, metavar="N")
    parser.add_argument("--results", type=int, default=DEFAULT_RESULTS, metavar="N")
    parser.add_argument("--port", type=int, default=5050)
    parser.add_argument("--prices-file", metavar="PATH", default=None,
                         help="Use a different saved-prices JSON instead of prices_output.json "
                              "(e.g. a sample multi-store dataset for testing).")
    args = parser.parse_args()

    if args.model:
        ollama_client.OLLAMA_MODEL = args.model
    if args.prices_file:
        price_lookup.set_prices_path(Path(args.prices_file))
    STATE["max_age_hours"] = args.max_age_hours
    STATE["max_stores"] = args.max_stores
    STATE["n_results"] = args.results
    STATE["force_live"] = args.live
    STATE["live_fallback"] = args.live_fallback

    warnings = ollama_client.check_ready()
    if warnings:
        print("[nova] Ollama isn't ready:\n")
        for w in warnings:
            print(f"  {w}\n")
        sys.exit(1)

    print(f"[nova] Serving on http://localhost:{args.port} (model: {ollama_client.OLLAMA_MODEL})")
    app.run(host="127.0.0.1", port=args.port, debug=False)


if __name__ == "__main__":
    main()
