"""
chrome_login.py

Start a real Chrome with remote debugging so the scraper can attach to it.

Instacart blocks logins in any browser Playwright launches, no matter how much
stealth is applied — the sign-in button just never activates. The way through is
to not automate the login at all: you log in yourself, in a normal Chrome window,
and the scraper attaches afterward and reuses that session.

This uses a dedicated profile directory (not your everyday Chrome profile), so your
normal browsing, tabs, and logins are untouched and Chrome doesn't need to be closed.

Usage (from the repo root):
    python -m scraper.chrome_login              # start Chrome + print next steps
    python -m scraper.chrome_login --check      # just report whether it's reachable

Then, once you're logged into Instacart in that window:
    set CHROME_CDP_URL=http://localhost:9222    (PowerShell: $env:CHROME_CDP_URL="http://localhost:9222")
    python -m scraper.product_search --products "butter"
"""

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

DEBUG_PORT = int(os.environ.get("CHROME_DEBUG_PORT", "9222"))
CDP_URL = f"http://localhost:{DEBUG_PORT}"

# Separate from the user's real profile so their everyday Chrome keeps running.
PROFILE_DIR = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "nova-chrome-profile"

CHROME_CANDIDATES = [
    Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Google/Chrome/Application/chrome.exe",
    Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "Google/Chrome/Application/chrome.exe",
    Path(os.environ.get("LOCALAPPDATA", "")) / "Google/Chrome/Application/chrome.exe",
    Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
    Path("/usr/bin/google-chrome"),
]


def find_chrome() -> Path | None:
    for path in CHROME_CANDIDATES:
        try:
            if path and path.exists():
                return path
        except OSError:
            continue
    return None


def is_reachable(url: str = CDP_URL, timeout: int = 2) -> bool:
    try:
        with urllib.request.urlopen(f"{url}/json/version", timeout=timeout) as resp:
            return resp.status == 200
    except (urllib.error.URLError, OSError):
        return False


def describe() -> str:
    """Browser identity behind the debug port, for confirming what we attached to."""
    try:
        with urllib.request.urlopen(f"{CDP_URL}/json/version", timeout=3) as resp:
            info = json.loads(resp.read())
        return info.get("Browser", "unknown")
    except Exception:
        return "unknown"


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Start a real Chrome with remote debugging for the scraper to attach to.",
    )
    parser.add_argument("--check", action="store_true",
                        help="Only check whether a debuggable Chrome is already running.")
    parser.add_argument("--url", default="https://www.instacart.com",
                        help="Page to open on start (default: Instacart).")
    args = parser.parse_args()

    if is_reachable():
        print(f"[chrome] Already running and reachable at {CDP_URL}")
        print(f"[chrome] Browser: {describe()}")
        _print_next_steps()
        return

    if args.check:
        print(f"[chrome] Nothing listening on {CDP_URL}.")
        print("[chrome] Start it with: python -m scraper.chrome_login")
        sys.exit(1)

    chrome = find_chrome()
    if not chrome:
        print("[chrome] Could not find Chrome. Install it from https://www.google.com/chrome/")
        print("[chrome] Or start any Chromium-based browser manually with:")
        print(f'  --remote-debugging-port={DEBUG_PORT} --user-data-dir="{PROFILE_DIR}"')
        sys.exit(1)

    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    print(f"[chrome] Chrome:  {chrome}")
    print(f"[chrome] Profile: {PROFILE_DIR}  (separate from your everyday Chrome)")
    print(f"[chrome] Port:    {DEBUG_PORT}")

    subprocess.Popen(
        [
            str(chrome),
            f"--remote-debugging-port={DEBUG_PORT}",
            f"--user-data-dir={PROFILE_DIR}",
            "--no-first-run",
            "--no-default-browser-check",
            args.url,
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    print("[chrome] Starting...")
    for _ in range(20):
        time.sleep(0.5)
        if is_reachable():
            print(f"[chrome] Ready at {CDP_URL}  ({describe()})")
            _print_next_steps()
            return

    print(f"[chrome] Started, but nothing is answering on {CDP_URL} yet.")
    print("[chrome] If a Chrome window opened, give it a moment and run:")
    print("  python -m scraper.chrome_login --check")


def _print_next_steps() -> None:
    print("\n" + "=" * 62)
    print("NEXT STEPS")
    print("=" * 62)
    print("  1. In the Chrome window that just opened, log into Instacart")
    print("     normally — email + password, solve any CAPTCHA.")
    print("  2. Set your delivery address and confirm you can see stores.")
    print("  3. Leave that window OPEN.")
    print("  4. Then run the scraper with CDP enabled:\n")
    print(f'     PowerShell:  $env:CHROME_CDP_URL="{CDP_URL}"')
    print(f"     cmd:         set CHROME_CDP_URL={CDP_URL}\n")
    print('     python -m scraper.product_search --products "butter"')
    print("=" * 62)


if __name__ == "__main__":
    main()
