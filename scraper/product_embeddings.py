"""
product_embeddings.py

Two-stage product matching (a small "hybrid search" — the industry-standard
pattern for this problem: lexical matching + vector embeddings, not either
alone -- see docs/specs/rag-product-matching-spec.md):

1. matches_query() -- word-boundary + light stemming, deterministic, no model
   calls. Fixes substring-in-longer-word false positives ("egg" no longer
   matching inside "eggplant") and singular/plural gaps ("egg" still matches
   "Eggs") without the false-positive risk generic edit-distance tolerance
   would carry on short words ("egg"/"ego", "pea"/"tea" are edit-distance 1
   apart but unrelated).

2. filter_by_relevance() -- semantic filtering on top of whatever matches_query
   already found, for cases word-boundary matching can't distinguish (e.g.
   "Broth Flavored Chicken Chips" contains both words of "chicken broth" as
   legitimate whole words, but isn't broth). Uses relative filtering (within a
   margin of the best score in *this* search) rather than one global absolute
   threshold -- live testing against real nomic-embed-text output showed
   similarity scores aren't comparable across different queries (a "wrong"
   match for one query can score higher than a "right" match for another), so
   no single cutoff works across searches.

get_embedding() is the only function that touches Ollama or SQLite; the rest
(cosine_similarity, find_best_matches, matches_query, _stem) are pure and
testable against hand-built fixtures without a live model.

Storage: SQLite for now (zero setup/permissions on the Merrimack server,
where MariaDB access is still blocked on admin credentials). Embeddings are
cached by exact product-name string, stored as JSON text rather than a
packed BLOB — simpler to read/debug, and the data volumes here (a few
thousand short vectors) don't warrant the extra complexity.

Embedding text is prefixed per nomic-embed-text's documented usage
("search_query: " / "search_document: ") -- embedding bare strings is a
known misuse for this model family and was confirmed live to flatten
similarity scores into an undifferentiated band.
"""

import json
import math
import re
import sqlite3
from pathlib import Path

from scraper import ollama_client

SCRAPER_DIR = Path(__file__).parent
DEFAULT_DB_PATH = SCRAPER_DIR / "product_embeddings.db"
DEFAULT_EMBED_MODEL = "nomic-embed-text"

# How far below the best similarity score in a given search's candidate pool a
# result may fall and still survive filter_by_relevance(). Tuned against real
# nomic-embed-text output on grocery product names (see docs/specs' testing
# notes) -- not a universal constant, just what worked on the sample checked.
# Known limitation: very close calls (e.g. "milk" vs "chocolate milk", ~0.01
# apart) will still often survive; margin-based filtering only reliably
# excludes candidates that are clearly semantically distant, not close but
# genuinely different variants.
DEFAULT_RELATIVE_MARGIN = 0.02

# Overridable (mirrors price_lookup.set_prices_path) so tests/tools can point
# at a temp file without touching the real cache.
_ACTIVE_DB_PATH: Path | None = None


def set_db_path(path: Path | None) -> None:
    global _ACTIVE_DB_PATH
    _ACTIVE_DB_PATH = path


def _connect() -> sqlite3.Connection:
    db_path = _ACTIVE_DB_PATH or DEFAULT_DB_PATH
    conn = sqlite3.connect(db_path)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS product_embeddings ("
        "product_name TEXT NOT NULL, model TEXT NOT NULL, embedding TEXT NOT NULL, "
        "PRIMARY KEY (product_name, model))"
    )
    return conn


_WORD_RE = re.compile(r"[a-zA-Z0-9']+")

# Letters/digraphs that genuinely take an "-es" plural (box/boxes, watch/watches,
# tomato/tomatoes) as opposed to a word that already ends in "e" and just adds
# "s" (apple/apples, orange/oranges) -- stripping "es" from the latter would
# wrongly cut into the stem itself ("apples" -> "appl", not "apple").
_TAKES_ES_PLURAL = ("s", "x", "z", "ch", "sh", "o")


def _stem(word: str) -> str:
    """
    Reduce `word` to a crude singular/base form so "egg" still matches "Eggs"
    and "tomato" still matches "Tomatoes", without over-stemming words that
    already end in "e" ("apples" -> "apple", not "appl"). Words of length <= 3
    are returned unchanged ("gas" -> "ga" would be wrong, and short words are
    exactly where a stray suffix match is most likely to collide with an
    unrelated word).

    Deliberately does NOT handle "y" -> "ies" (candy/candies, berry/berries):
    a word already ending in "ie" pluralizes by just adding "s" (cookie ->
    cookies, movie -> movies), and that surface form is indistinguishable from
    a real "y"->"ies" swap without a real dictionary -- a regex rule for one
    case corrupts the other ("cookies" -> "cooky"). Not a full Porter stemmer,
    just enough for common grocery-product plurals without corrupting common
    words to cover a rarer case.
    """
    lower = word.lower()
    if len(lower) <= 3:
        return lower
    if lower.endswith("es") and lower[:-2].endswith(_TAKES_ES_PLURAL):
        return lower[:-2]
    if lower.endswith("s") and not lower.endswith("ss"):
        return lower[:-1]
    return lower


def matches_query(name: str, query: str) -> bool:
    """
    True if every word of `query` appears as a whole (stemmed) word in `name`,
    case-insensitive. Word-boundary + light stemming, not a raw substring
    check, so "egg" no longer matches inside "eggplant" while "egg" still
    matches "Eggs".

    Single shared implementation for both core.py (live Instacart search) and
    price_lookup.py (saved-JSON lookups) — each previously had its own private
    _matches_query() that had to be kept in sync by hand, which is exactly the
    kind of duplication this feature exists to fix.
    """
    name_words = {_stem(w) for w in _WORD_RE.findall(name)}
    return all(_stem(tok) in name_words for tok in _WORD_RE.findall(query))


def get_embedding(product_name: str, model: str = DEFAULT_EMBED_MODEL) -> list[float]:
    """
    Embedding for `product_name`, from the SQLite cache if present, otherwise
    computed via ollama_client.embed() and cached for next time. Cached per
    (product_name, model) — switching embedding models re-embeds rather than
    silently returning a stale, wrong-dimension vector from a prior model.
    Raises OllamaError (propagated from ollama_client) if Ollama is unavailable
    and the name isn't already cached under this model.

    Uses nomic-embed-text's documented "search_document: " prefix -- pair with
    a query embedded via the "search_query: " prefix (see filter_by_relevance).
    """
    conn = _connect()
    try:
        with conn:  # commits on success / rolls back on exception -- does not close
            row = conn.execute(
                "SELECT embedding FROM product_embeddings WHERE product_name = ? AND model = ?",
                (product_name, model),
            ).fetchone()
            if row is not None:
                return json.loads(row[0])

            embedding = ollama_client.embed(f"search_document: {product_name}", model=model)
            conn.execute(
                "INSERT INTO product_embeddings (product_name, model, embedding) VALUES (?, ?, ?)",
                (product_name, model, json.dumps(embedding)),
            )
            return embedding
    finally:
        conn.close()


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """
    Cosine similarity of two equal-length vectors, in [-1, 1]. 0.0 if either is
    a zero vector. Raises ValueError on a length mismatch rather than silently
    comparing a truncated prefix (which zip() would otherwise do) — a
    dimension mismatch means two different embedding models were mixed, which
    should fail loudly, not return a meaningless number.
    """
    if len(a) != len(b):
        raise ValueError(f"Cannot compare embeddings of different lengths: {len(a)} vs {len(b)}")
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def find_best_matches(query_embedding: list[float], candidates: list[dict], top_k: int = 5) -> list[dict]:
    """
    Rank `candidates` (each carrying an "embedding" key) by similarity to
    `query_embedding`, best first. Each returned row is the original candidate
    dict plus a "similarity" key. Does not call Ollama or touch a database —
    callers are responsible for producing the embeddings first.
    """
    scored = [
        {**candidate, "similarity": cosine_similarity(query_embedding, candidate["embedding"])}
        for candidate in candidates
    ]
    scored.sort(key=lambda row: row["similarity"], reverse=True)
    return scored[:top_k] if top_k > 0 else scored


def filter_by_relevance(
    term: str,
    candidates: list[dict],
    name_key: str = "name",
    margin: float = DEFAULT_RELATIVE_MARGIN,
) -> list[dict]:
    """
    Drop entries from `candidates` whose similarity to `term` falls more than
    `margin` below the best-scoring candidate IN THIS CALL (e.g. "Broth
    Flavored Chicken Chips" scoring well below real "Chicken Broth" for a
    "chicken broth" query). Relative to the best score in this specific
    search, not a global constant — live testing against real
    nomic-embed-text output showed similarity scores aren't comparable across
    different queries (a "wrong" match for one query scored higher than a
    "right" match for another), so no single absolute cutoff works across
    searches.

    Preserves the input order of survivors — this is a filter, not a re-rank,
    so a caller's own sort (e.g. cheapest-first) done before calling this is
    not disturbed. (Deliberately does not use find_best_matches() here, which
    sorts by similarity — that would silently undo a price sort applied
    beforehand.)

    Shared by price_lookup.py (saved-JSON products) and core.py (live-scraped
    Instacart search results) so there's exactly one place implementing this
    rule, instead of the kind of independently-drifting duplicate that
    `_matches_query()` became in both modules.

    Falls back to `candidates` unchanged if Ollama or the embedding cache is
    unavailable, or if filtering would remove every result — a smarter filter
    should never turn a successful lookup into an empty one.
    """
    if not candidates:
        return candidates
    try:
        query_embedding = ollama_client.embed(f"search_query: {term}")
        scored = [
            (c, cosine_similarity(query_embedding, get_embedding(c[name_key])))
            for c in candidates
        ]
        best = max(sim for _, sim in scored)
        filtered = [c for c, sim in scored if sim >= best - margin]
        return filtered or candidates
    except Exception as e:
        print(f"[product_embeddings] Semantic filtering unavailable, using substring matches only: {e}")
        return candidates
