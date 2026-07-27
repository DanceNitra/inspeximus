"""
inspeximus example 03 — semantic recall by bringing your own embedder.

    pip install inspeximus
    python 03_semantic_recall.py

inspeximus has no required dependencies, so it ships no embedder. Pass ANY text->vector function as `embed=` and
recall becomes semantic (and, once the store grows, a lexical+semantic hybrid). Without one, recall falls back
to lexical — so this file runs either way.

To use a real model, `pip install sentence-transformers` and swap `my_embed` for the SentenceTransformer line
below.
"""
from inspeximus import Inspeximus

# --- Option A: a real embedder (uncomment after `pip install sentence-transformers`) --------------------------
# from sentence_transformers import SentenceTransformer
# _model = SentenceTransformer("all-MiniLM-L6-v2")
# my_embed = lambda text: _model.encode(text).tolist()

# --- Option B: a tiny dependency-free stand-in so the example runs as-is -------------------------------------
# (a toy bag-of-chars vector — good enough to demonstrate the interface, NOT for real semantic quality)
def my_embed(text: str):
    v = [0.0] * 32
    for ch in text.lower():
        v[ord(ch) % 32] += 1.0
    norm = sum(x * x for x in v) ** 0.5 or 1.0
    return [x / norm for x in v]


m = Inspeximus(embed=my_embed)

for fact in [
    "The database connection pool maxes out at 20 connections",
    "We use PostgreSQL 16 in production",
    "The cache TTL is five minutes",
    "Rate limiting is handled by the gateway",
]:
    m.remember(fact)

# semantic recall: the query need not share words with the stored fact (with a real embedder)
q = "how many db connections can we open"
print(f"Q: {q}")
for r in m.recall(q, k=2):
    print(f"  [{r['relevance']:.2f}] {r['text']}")

print("\nrecall mode used:", m._last_mode, "(swap in a real embedder for production-quality semantic recall)")
