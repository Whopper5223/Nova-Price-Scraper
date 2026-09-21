# Intent: RAG-based product matching

Confirmed 2026-09-01.

- **Outcome:** Replace naive keyword product matching with embedding-based retrieval (RAG) so the right product gets found by name — no more "butter" matching "butter cookies" — used by both recipe-to-product matching and the chat assistant's price Q&A.
- **User:** You (and Zach), for the Nova lab feasibility project.
- **Why now:** Current `price_lookup.py`/`recipe_scraper.py` do plain keyword lookups, which break on ambiguous product names; this also lets the chat assistant answer price questions from real retrieved data instead of guessing.
- **Success:** Given an ingredient/product name, the correct product(s) come back across stores; chat assistant answers use retrieved records, not hallucinated ones.
- **Constraint:** Free/no paid infra — store product records + embeddings in MySQL, do similarity search in Python (no vector DB needed at this scale, since MySQL lacks native vector indexing). No model fine-tuning — just an Ollama Modelfile pinning temp=0 for determinism.
- **Out of scope for now:** Persistent server exposure, SSH port forwarding, and the reload/startup script — blocked on someone else giving you a port; revisit once that access exists.

## Not yet started
No implementation work has begun. Next step when picking this up: confirm MySQL is available on the Merrimack server, then design the product-record schema (name, store, price, unit, embedding) and pick an embedding approach (likely a local model via Ollama or a lightweight sentence-embedding library).
