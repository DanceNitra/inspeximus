"""embed_query_asymmetric_probe.py — the asymmetric query embedder (nomic-style task prefixes).

nomic-embed-text is TRAINED to prefix stored text with 'search_document: ' and queries with 'search_query: ';
omitting the prefixes hurts retrieval. mnemo now takes an optional `embed_query` (defaults to `embed`) so the
recall QUERY can be embedded differently from stored TEXT. Asserts:
  1. recall() embeds the QUERY via embed_query; stored text via embed (document embedder).
  2. internal callers that embed STORED text (consolidation etc.) keep using embed, not embed_query.
  3. backward-compatible: with no embed_query, the query uses embed (byte-identical legacy path).

Measured impact (agora_output/lab/locomo_prefix_scale.py, LoCoMo n=1536): adding nomic prefixes lifts mnemo
recall_any@1 from 0.193 to 0.294 and @25 from 0.754 to 0.807 (overtaking mem0 0.798 at k=25); mem0 still leads
at small k. This is a correctness fix (we were using nomic without its required prefixes), not a "beats mem0" claim.
"""
import sys
sys.path.insert(0, ".")
from mnemo import Mnemo

FAILS = []
def check(name, cond):
    print(f"  [{'OK ' if cond else 'XXX'}] {name}")
    if not cond:
        FAILS.append(name)

# 1 + 2: query uses embed_query, stored text uses embed
doc_calls, q_calls = [], []
def d(t): doc_calls.append(t); return [1.0, 0.0] if "cat" in t else [0.0, 1.0]
def q(t): q_calls.append(t); return [1.0, 0.0] if "cat" in t else [0.0, 1.0]
m = Mnemo(path=None, embed=d, embed_query=q)
m.remember("the cat is black", key="a")
m.remember("the dog is brown", key="b")
hits = m.recall("tell me about the cat", k=1, mode="semantic")
check("1 recall returns the semantically-correct hit", hits and hits[0]["text"] == "the cat is black")
check("1b query embedded via embed_query", "tell me about the cat" in q_calls)
check("2 stored text embedded via embed (document embedder)", "the cat is black" in doc_calls)
check("2b query NOT sent to the document embedder", "tell me about the cat" not in doc_calls)

# 3: backward-compat — no embed_query => query uses embed
legacy = []
m2 = Mnemo(path=None, embed=lambda t: (legacy.append(t) or ([1.0] if "x" in t else [0.0])))
m2.remember("x marks the spot", key="k")
m2.recall("find x", k=1, mode="semantic")
check("3 backward-compat: query uses embed when embed_query is None", "find x" in legacy)

print(f"\n{'ALL PASS' if not FAILS else 'FAILED: ' + ', '.join(FAILS)}")
sys.exit(1 if FAILS else 0)
