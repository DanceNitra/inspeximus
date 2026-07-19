"""embed_recipe_migration_guard_probe.py — persisted vectors are re-embedded when the embed recipe changes.

An asymmetric-embedder upgrade (e.g. adding nomic search_document:/search_query: prefixes) would otherwise
compare a NEW-space query against OLD-space stored vectors -> silent recall degradation. The guard: pass
embed_id (a recipe fingerprint); it is written to a <path>.embedid sidecar on save (persist_vectors only); on
open with a different embed_id, the persisted vectors are re-embedded with the current embedder. Asserts:
  1. persist_vectors store records embed_id in a sidecar on save.
  2. reopening with a DIFFERENT embed_id re-embeds the stored vectors (realigns the space).
  3. reopening with the SAME embed_id does NOT re-embed (idempotent).
  4. default RAM-only store (persist_vectors=False) never creates the sidecar / never pays the guard.
"""
import sys, os, tempfile
sys.path.insert(0, ".")
from mnemo import Mnemo

FAILS = []
def check(n, c):
    print(f"  [{'OK ' if c else 'XXX'}] {n}")
    if not c: FAILS.append(n)

d = tempfile.mkdtemp(); p = os.path.join(d, "s.json")
def embA(t): return [1.0, 0.0, 0.0]
def embB(t): return [0.0, 1.0, 0.0]

m = Mnemo(path=p, embed=embA, persist_vectors=True, embed_id="A")
m.remember("hello world", key="k"); m._save(force=True)
check("1 embed_id sidecar written on save", os.path.exists(p + ".embedid") and open(p + ".embedid").read() == "A")

m2 = Mnemo(path=p, embed=embB, persist_vectors=True, embed_id="B")
v2 = [r["vec"] for r in m2.items if r.get("key") == "k"][0]
check("2 recipe change re-embeds persisted vectors", v2 == [0.0, 1.0, 0.0])
m2._save(force=True)
check("2b sidecar updated to new recipe on save", open(p + ".embedid").read() == "B")

# same recipe B stored; pass embed=embA but embed_id="B" -> must NOT re-embed (vec stays B)
m3 = Mnemo(path=p, embed=embA, persist_vectors=True, embed_id="B")
v3 = [r["vec"] for r in m3.items if r.get("key") == "k"][0]
check("3 same recipe = no re-embed (idempotent)", v3 == [0.0, 1.0, 0.0])

p2 = os.path.join(d, "ram.json")
mm = Mnemo(path=p2, embed=embA, embed_id="A")   # persist_vectors=False (default)
mm.remember("x", key="k"); mm._save(force=True)
check("4 non-persist store never creates the embedid sidecar", not os.path.exists(p2 + ".embedid"))

# ── regressions for the 2026-07-19 "re-embed storm" (a hook-driven store froze Claude Code) ──────────
# The guard re-embedded EVERY record (not just the vec-bearing ones) and, because the sidecar is only
# written by _save(), a read-only caller redid the whole thing on every single open: 1214 records x one
# network call each, per hook, forever.
calls = {"n": 0}
def embC(t):
    calls["n"] += 1
    return [0.0, 0.0, 1.0]

p3 = os.path.join(d, "mixed.json")
m4 = Mnemo(path=p3, embed=embA, persist_vectors=True, embed_id="A")
m4.remember("has a vector", key="v")
for i in range(50):                                  # vec-less records (an embedder-down capture, or lexical era)
    m4.remember(f"no vector {i}", key=f"n{i}")
    m4.items[-1]["vec"] = None
m4._save(force=True)

calls["n"] = 0
m5 = Mnemo(path=p3, embed=embC, persist_vectors=True, embed_id="C")
check("5 realign embeds ONLY vec-bearing records (not the whole store)", calls["n"] == 1)
check("5b vec-less records stay vec-less", all(r["vec"] is None for r in m5.items if r.get("key") != "v"))

# The killer: m5 was never saved by the caller. Opening again must NOT redo the realignment.
calls["n"] = 0
m6 = Mnemo(path=p3, embed=embC, persist_vectors=True, embed_id="C")
check("6 realignment is persisted, so a read-only open never repeats it", calls["n"] == 0)
check("6b sidecar records the new recipe without an explicit save",
      open(p3 + ".embedid").read().strip() == "C")
check("6c the realigned vector actually reached disk",
      [r["vec"] for r in m6.items if r.get("key") == "v"][0] == [0.0, 0.0, 1.0])

# Bounded: a big store must never pay an unbounded synchronous re-embed on the load path.
p4 = os.path.join(d, "big.json")
m7 = Mnemo(path=p4, embed=embA, persist_vectors=True, embed_id="A")
for i in range(12):
    m7.remember(f"rec {i}", key=f"b{i}")
m7._save(force=True)
os.environ["MNEMO_REALIGN_MAX"] = "5"
calls["n"] = 0
m8 = Mnemo(path=p4, embed=embC, persist_vectors=True, embed_id="C")
check("7 past MNEMO_REALIGN_MAX the guard drops vectors instead of stalling", calls["n"] == 0)
check("7b dropped vectors degrade to lexical (vec=None), never a stale-space mismatch",
      all(r.get("vec") is None for r in m8.items))
os.environ.pop("MNEMO_REALIGN_MAX", None)

print(f"\n{'ALL PASS' if not FAILS else 'FAILED: ' + ', '.join(FAILS)}")
sys.exit(1 if FAILS else 0)
