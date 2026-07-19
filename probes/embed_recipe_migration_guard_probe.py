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

print(f"\n{'ALL PASS' if not FAILS else 'FAILED: ' + ', '.join(FAILS)}")
sys.exit(1 if FAILS else 0)
