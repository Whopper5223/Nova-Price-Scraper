"""
web_app.py

Local browser front end for chat_assistant.py — same conversation, extraction,
and pricing logic, just easier to click through than a terminal prompt.

Each visitor gets their own conversation, tracked via a signed session cookie
(SESSIONS, keyed by a per-visitor id) — settings like --max-stores are shared
(CONFIG). All in memory: restarting the process clears every session.

Usage:
    python -m scraper.web_app
    python -m scraper.web_app --model llama3.1 --max-age-hours 200
"""

import argparse
import asyncio
import secrets
import sys
import threading
from datetime import datetime, timezone
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

from flask import Flask, jsonify, request, send_from_directory, session

from scraper import instacart_scraper, ollama_client, price_lookup
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
# Signs the session cookie. Regenerated on every process start — fine, since all
# session state is in-memory anyway and a restart already clears everything.
app.secret_key = secrets.token_hex(32)

# Shared across every visitor — set once at startup from CLI args, never per-request.
CONFIG = {"max_age_hours": price_lookup.DEFAULT_MAX_AGE_HOURS, "max_stores": DEFAULT_MAX_STORES,
          "n_results": DEFAULT_RESULTS, "force_live": False, "live_fallback": False}

# Per-visitor conversation state, keyed by the id in their session cookie. No
# cleanup/expiry — acceptable for a demo, would need attention for long-lived use.
SESSIONS: dict[str, dict] = {}


def _get_session() -> dict:
    sid = session.get("sid")
    if not sid:
        sid = secrets.token_hex(16)
        session["sid"] = sid
    return SESSIONS.setdefault(sid, {"history": [], "budget": None})

# Admin scrape trigger — unlisted route, not linked from static/index.html. "Hidden"
# here just means the URL isn't published anywhere, not real auth (see /admin/scrape).
DEFAULT_SCRAPE_STORES = ["Stop & Shop", "ALDI", "Wegmans"]
SCRAPE_STATE = {"status": "idle", "started_at": None, "finished_at": None,
                 "result": None, "error": None}
_SCRAPE_LOCK = threading.Lock()


def _run_scrape(store_filter: list[str], skip_categorize: bool) -> None:
    """Runs on a background thread, kicked off by POST /admin/scrape — never called
    directly from a request thread, since a full scrape can take several minutes."""
    try:
        result = asyncio.run(instacart_scraper.run_scraper(
            verbose=True, store_filter=store_filter, skip_categorize=skip_categorize,
        ))
        stores = result.get("stores", [])
        products = sum(s.get("product_count", 0) for s in stores)
        errors = result.get("errors", [])
        SCRAPE_STATE["result"] = {"stores": len(stores), "products": products, "errors": errors}
        if products == 0:
            SCRAPE_STATE["status"] = "error"
            SCRAPE_STATE["error"] = errors[0] if errors else "Scrape completed but found 0 products."
        else:
            SCRAPE_STATE["status"] = "done"
    except Exception as e:
        SCRAPE_STATE["status"] = "error"
        SCRAPE_STATE["error"] = str(e)
    finally:
        SCRAPE_STATE["finished_at"] = datetime.now(timezone.utc).isoformat()


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
    message = (request.get_json(silent=True) or {}).get("message", "").strip()
    if not message:
        return jsonify({"error": "empty message"}), 400

    sess = _get_session()
    sess["history"].append({"role": "user", "content": message})

    try:
        reply = ollama_client.chat(sess["history"], system=CHAT_SYSTEM_PROMPT, temperature=0.7)
    except OllamaError as e:
        sess["history"].pop()
        return jsonify({"error": str(e)}), 503

    sess["history"].append({"role": "assistant", "content": reply})

    extracted = _extract_list(sess["history"])
    sess["budget"] = extracted["budget"]
    return jsonify({
        "reply": reply,
        "ready": extracted["ready"],
        "items": extracted["items"],
        "missing": extracted["missing"],
        "budget": extracted["budget"],
    })


@app.post("/api/price")
def price():
    body = request.get_json(silent=True) or {}
    sess = _get_session()
    items = [str(i).strip().lower() for i in body.get("items", []) if str(i).strip()]
    budget = body.get("budget", sess.get("budget"))
    if not items:
        return jsonify({"error": "no items"}), 400

    if CONFIG["force_live"]:
        saved_findings, unresolved = {}, list(items)
    else:
        saved_findings, unresolved = _price_from_saved(items, CONFIG["max_age_hours"])

    live_findings = {}
    live_error = None
    if unresolved and (CONFIG["force_live"] or CONFIG["live_fallback"]):
        try:
            live_findings = asyncio.run(_price_live(unresolved, CONFIG["max_stores"], CONFIG["n_results"]))
        except Exception as e:
            live_error = str(e)
    elif unresolved:
        live_error = (f"{len(unresolved)} item(s) not in saved prices — live lookup is off by "
                      f"default (start with --live-fallback to enable it).")

    findings = {**saved_findings, **live_findings}
    missing = [i for i in items if i not in findings]

    request_text = next((m["content"] for m in sess["history"] if m["role"] == "user"), "")
    summary = _summarize(request_text, findings, missing, items, budget)
    store_totals = _store_basket_totals(findings, items)

    sess["history"].clear()
    sess["budget"] = None

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
    sess = _get_session()
    sess["history"].clear()
    sess["budget"] = None
    return jsonify({"ok": True})


@app.route("/admin/scrape", methods=["GET", "POST"])
def admin_scrape():
    if request.method == "GET":
        return jsonify(SCRAPE_STATE)

    body = request.get_json(silent=True) or {}
    stores = body.get("stores") or DEFAULT_SCRAPE_STORES
    skip_categorize = bool(body.get("skip_categorize", False))

    with _SCRAPE_LOCK:
        if SCRAPE_STATE["status"] == "running":
            return jsonify({"error": "A scrape is already running.", **SCRAPE_STATE}), 409
        SCRAPE_STATE.update({"status": "running", "started_at": datetime.now(timezone.utc).isoformat(),
                              "finished_at": None, "result": None, "error": None})
        threading.Thread(target=_run_scrape, args=(stores, skip_categorize), daemon=True).start()

    return jsonify({"status": "running", "stores": stores}), 202


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
    parser.add_argument("--host", default="127.0.0.1",
                        help="Bind address. Use 0.0.0.0 to accept connections from outside this machine (e.g. behind an opened firewall port). Default 127.0.0.1 (local only).")
    parser.add_argument("--prices-file", metavar="PATH", default=None,
                         help="Use a different saved-prices JSON instead of prices_output.json "
                              "(e.g. a sample multi-store dataset for testing).")
    parser.add_argument("--headless", action="store_true",
                         help="Force headless Playwright for the /admin/scrape trigger and any "
                              "live-fallback lookups — needed on a server with no display. "
                              "Higher bot-detection risk (mirrors run_scraper.py --headless).")
    args = parser.parse_args()

    if args.headless:
        print("[nova] Playwright forced headless (higher bot-detection risk).")
        import playwright.async_api as _pw_api
        _original_launch = _pw_api.BrowserType.launch

        async def _headless_launch(self, **kwargs):
            kwargs["headless"] = True
            return await _original_launch(self, **kwargs)

        _pw_api.BrowserType.launch = _headless_launch

    if args.model:
        ollama_client.OLLAMA_MODEL = args.model
    if args.prices_file:
        price_lookup.set_prices_path(Path(args.prices_file))
    CONFIG["max_age_hours"] = args.max_age_hours
    CONFIG["max_stores"] = args.max_stores
    CONFIG["n_results"] = args.results
    CONFIG["force_live"] = args.live
    CONFIG["live_fallback"] = args.live_fallback

    warnings = ollama_client.check_ready()
    if warnings:
        print("[nova] Ollama isn't ready:\n")
        for w in warnings:
            print(f"  {w}\n")
        sys.exit(1)

    print(f"[nova] Serving on http://{args.host}:{args.port} (model: {ollama_client.OLLAMA_MODEL})")
    app.run(host=args.host, port=args.port, debug=False, threaded=True)


if __name__ == "__main__":
    main()
