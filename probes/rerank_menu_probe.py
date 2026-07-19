"""rerank_menu_probe.py — recall(rerank_by=...) named reranker menu (deterministic, zero-LLM).

A discoverable set of reorderings over the top relevant pool (like Zep's rerank menu, no LLM): recency / value /
reliability / relevance. Three items are engineered so each strategy picks a DIFFERENT top-1 — proving the menu
actually switches behavior, not a cosmetic alias. Asserts (each can FAIL):
  itemA = oldest, value=9 (highest)        -> 'value' should rank it #1
  itemB = middle, credited good (best track)-> 'reliability' should rank it #1
  itemC = newest                            -> 'recency' should rank it #1
  1. rerank_by='recency' top-1 = C ; 2. ='value' top-1 = A ; 3. ='reliability' top-1 = B
  4. those three top-1s are all DIFFERENT (the menu discriminates)
  5. rerank_by='relevance' == default order (explicit no-op)
"""
import sys
sys.path.insert(0, ".")
from mnemo import Mnemo

FAILS = []
def check(n, c):
    print(f"  [{'OK ' if c else 'XXX'}] {n}")
    if not c: FAILS.append(n)

m = Mnemo(path=None)                                   # lexical, no GPU
ida = m.remember("signal report about AAA", value=9.0, valid_from=1000)     # oldest, highest value
idb = m.remember("signal report about BBB", value=1.0, valid_from=2000)     # middle, will be credited
idc = m.remember("signal report about CCC", value=1.0, valid_from=3000)     # newest
m.credit([idb], True, warrant="external")                  # BBB gets the best track record

def top(**kw):
    h = m.recall("signal report", k=5, reinforce=False, **kw)
    return h[0]["text"] if h else ""

t_rec = top(rerank_by="recency")
t_val = top(rerank_by="value")
t_rel = top(rerank_by="reliability")
check("1 recency -> newest (CCC) first", "CCC" in t_rec)
check("2 value -> highest-value (AAA) first", "AAA" in t_val)
check("3 reliability -> best-track (BBB) first", "BBB" in t_rel)
check("4 the three strategies give DIFFERENT top-1 (menu discriminates)",
      len({t_rec, t_val, t_rel}) == 3)

default = [h["text"] for h in m.recall("signal report", k=5, reinforce=False)]
relevance = [h["text"] for h in m.recall("signal report", k=5, reinforce=False, rerank_by="relevance")]
check("5 rerank_by='relevance' is a no-op (== default order)", default == relevance)

print(f"\n  recency={t_rec!r}\n  value={t_val!r}\n  reliability={t_rel!r}")
print(f"{'ALL PASS' if not FAILS else 'FAILED: ' + ', '.join(FAILS)}")
sys.exit(1 if FAILS else 0)
