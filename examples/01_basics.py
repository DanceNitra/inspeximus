"""
inspeximus example 01 — the basics: remember, recall, correct, audit.

    pip install inspeximus
    python 01_basics.py

The whole loop most agents need, in one zero-dependency file. No embedder required — recall falls back to a
forgiving lexical match, so this runs anywhere today.
"""
from inspeximus import Inspeximus

m = Inspeximus("memory.json")          # persists to memory.json; drop the path for pure in-memory

# --- remember -----------------------------------------------------------------
# `key` is an optional (subject, relation) identifier. Writing the same key again supersedes the old value.
m.remember("The API rate limit is 1000 req/min", key="api::rate_limit", tags=["config"])
m.remember("User prefers dark mode", key="ui::theme", tags=["prefs"])
m.remember("Deploy target is us-east-1", key="infra::region", tags=["config"])

# --- recall (relevance x value) -----------------------------------------------
print("Q: what is the rate limit?")
print("  ", [r["text"] for r in m.recall("what is the rate limit")])

# --- correct: first-class, no LLM call, no similarity threshold ----------------
m.remember("The API rate limit is 5000 req/min", key="api::rate_limit", tags=["config"])
print("\nAfter correcting the rate limit:")
print("  ", [r["text"] for r in m.recall("rate limit")])      # only the CURRENT value comes back

# --- audit: the full history of a key -----------------------------------------
print("\nHistory of api::rate_limit (oldest -> newest):")
for h in m.history("api::rate_limit"):
    print("  ", h.get("status", "?"), "-", h["text"])

print("\nStored keys:", sorted({r["key"] for r in m.items if r.get("key")}))
