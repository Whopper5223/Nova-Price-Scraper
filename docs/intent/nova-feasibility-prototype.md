# Nova Grocery Assistant — Feasibility Prototype

Confirmed intent, captured via `/interview-me` on 2026-08-02.

- **Outcome**: A working local prototype — chat-based grocery assistant with budget
  adherence, multi-store price comparison, and SNAP/EBT eligibility — that proves the
  core concept is technically feasible.
- **User**: Right now, just the developer (testing/exploring); eventually SNAP/EBT
  grocery shoppers, once the lab turns this into a real app.
- **Why now**: A university lab is already on board and specifically wants this explored
  with free/local tools (Ollama) rather than paid infrastructure, as an early feasibility
  phase before real engineering begins. Whether this repo becomes the production app or
  stays a demo that informs a fresh build is still undecided.
- **Success**: Correctness and reliability matter more than speed right now — the
  reasoning (budget math, multi-store logic, SNAP tagging) needs to demonstrably work,
  even if slow, so it can be shown to the lab as technically sound.
- **Constraint**: No personal spending on servers/cloud GPUs — stick to what's free and
  local, or eventually whatever compute the lab provides directly.
- **Out of scope for now**: Setting up paid cloud infrastructure, production hosting/
  scaling, and optimizing for speed/polish beyond what's needed to demonstrate the
  concept works.

## Implications this should keep shaping

- Prefer the most *reliable* local model over the fastest one (e.g. `deepseek-r1:8b`
  showed zero contradictions across test runs vs. `llama3.1:8b` and `qwen2.5:14b`,
  despite being the slowest — that trade favors correctness here).
- Don't suggest or set up paid compute (cloud GPUs, hosted inference) unless the lab
  explicitly provides it.
- Treat scraper coverage, live-lookup speed, and UI polish as secondary to proving the
  chat → budget/SNAP/multi-store reasoning pipeline is sound.
