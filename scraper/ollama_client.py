"""
ollama_client.py

Thin wrapper over a local Ollama server (https://ollama.com).

Ollama runs open-weight models on this machine and exposes an HTTP chat API on
localhost:11434 — no API key, no network round-trip once the model is pulled.

Setup:
    1. Install Ollama from https://ollama.com
    2. ollama pull llama3.1        (or set OLLAMA_MODEL to something else)
    3. Ollama serves in the background automatically; `ollama serve` starts it manually.

Config (env vars / .env):
    OLLAMA_URL    default http://localhost:11434
    OLLAMA_MODEL  default llama3.1
"""

import json
import os

import requests

OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434").rstrip("/")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "llama3.1")

# Generation can be slow on CPU-only machines; a long ceiling beats a spurious timeout.
DEFAULT_TIMEOUT = 300


class OllamaError(RuntimeError):
    """Ollama is unreachable, the model is missing, or the response was unusable."""


def _hint(detail: str) -> str:
    return (
        f"{detail}\n"
        f"  - Is Ollama installed? https://ollama.com\n"
        f"  - Is it running? Try: ollama serve\n"
        f"  - Is the model pulled? Try: ollama pull {OLLAMA_MODEL}\n"
        f"  - Current endpoint: {OLLAMA_URL} (override with OLLAMA_URL)\n"
        f"  - Current model: {OLLAMA_MODEL} (override with OLLAMA_MODEL)"
    )


def is_available() -> bool:
    """True if the Ollama server answers. Does not check that the model exists."""
    try:
        resp = requests.get(f"{OLLAMA_URL}/api/tags", timeout=5)
        return resp.status_code == 200
    except requests.RequestException:
        return False


def list_models() -> list[str]:
    """Names of models pulled on this machine. Empty list if the server is unreachable."""
    try:
        resp = requests.get(f"{OLLAMA_URL}/api/tags", timeout=5)
        resp.raise_for_status()
        return [m.get("name", "") for m in resp.json().get("models", [])]
    except requests.RequestException:
        return []


def check_ready() -> list[str]:
    """
    Warnings about the local Ollama setup — empty list means good to go.
    Mirrors run_scraper._check_env(): report problems, let the caller decide.
    """
    if not is_available():
        return [_hint(f"Cannot reach the Ollama server at {OLLAMA_URL}.")]

    models = list_models()
    if not models:
        return [_hint("Ollama is running but no models are pulled.")]

    # Ollama reports tagged names ("llama3.1:latest"); accept an untagged config value.
    base_names = {m.split(":")[0] for m in models}
    if OLLAMA_MODEL not in models and OLLAMA_MODEL.split(":")[0] not in base_names:
        return [
            f"Model '{OLLAMA_MODEL}' is not pulled. Available: {', '.join(models)}\n"
            f"  Pull it with: ollama pull {OLLAMA_MODEL}\n"
            f"  Or point OLLAMA_MODEL at one of the models above."
        ]

    return []


def chat(
    messages: list[dict],
    system: str | None = None,
    json_mode: bool = False,
    model: str | None = None,
    temperature: float | None = None,
    timeout: int = DEFAULT_TIMEOUT,
) -> str:
    """
    Send a conversation to Ollama and return the assistant's reply text.

    messages     [{"role": "user"|"assistant"|"system", "content": str}, ...]
    system       prepended as a system message (convenience over building it yourself)
    json_mode    ask Ollama to constrain output to valid JSON (for extraction calls)
    temperature  lower = more deterministic; left to the model default when None

    Raises OllamaError with setup hints if the server is unreachable or errors out.
    """
    payload: dict = {
        "model": model or OLLAMA_MODEL,
        "messages": ([{"role": "system", "content": system}] if system else []) + messages,
        "stream": False,
    }
    if json_mode:
        payload["format"] = "json"
    if temperature is not None:
        payload["options"] = {"temperature": temperature}

    try:
        resp = requests.post(f"{OLLAMA_URL}/api/chat", json=payload, timeout=timeout)
        resp.raise_for_status()
    except requests.Timeout as e:
        raise OllamaError(_hint(f"Ollama timed out after {timeout}s.")) from e
    except requests.RequestException as e:
        raise OllamaError(_hint(f"Ollama request failed: {e}")) from e

    try:
        return resp.json()["message"]["content"].strip()
    except (ValueError, KeyError) as e:
        raise OllamaError(f"Unexpected response shape from Ollama: {resp.text[:400]}") from e


def chat_json(
    messages: list[dict],
    system: str | None = None,
    model: str | None = None,
    timeout: int = DEFAULT_TIMEOUT,
) -> dict | list | None:
    """
    chat() in JSON mode, parsed. Returns None when the model emits unparseable output
    (small local models do this occasionally) so callers can fall back rather than crash.
    Temperature is pinned to 0 — extraction should be as deterministic as the model allows.
    """
    raw = chat(messages, system=system, json_mode=True, model=model, temperature=0, timeout=timeout)
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        # Occasionally the model wraps JSON in prose or a code fence despite format:json.
        start = min((i for i in (raw.find("{"), raw.find("[")) if i != -1), default=-1)
        end = max(raw.rfind("}"), raw.rfind("]"))
        if start != -1 and end > start:
            try:
                return json.loads(raw[start:end + 1])
            except json.JSONDecodeError:
                pass
        print(f"[ollama] Could not parse JSON response: {raw[:200]}")
        return None
