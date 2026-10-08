"""embed_recipe_migration_guard_probe.py — an open never re-embeds; `reembed()` is the one way to realign.

An asymmetric-embedder upgrade (e.g. adding nomic search_document:/search_query: prefixes) would otherwise
compare a NEW-space query against OLD-space stored vectors -> silent recall degradation. The guard: pass
embed_id (a recipe fingerprint); it is written to a <path>.embedid sidecar on save (persist_vectors only).

Until 3.16 an open with a different embed_id re-embedded the stored vectors, or dropped them past
INSPEXIMUS_REALIGN_MAX, and rewrote the sidecar. A project's settings reach the MCP server's environment, so one
INSPEXIMUS_EMBED_MODEL made the user's server embed record text and rewrite or drop their vectors (AUDIT-A P-1).
Since 3.17.0 an open changes nothing on disk and embeds nothing: a vector under another recipe is held out of ranking
in memory, `index_coherence()` counts it, and `reembed()` replaces it. Asserts:
  1. persist_vectors store records embed_id in a sidecar on save.
  2. reopening with a DIFFERENT embed_id embeds nothing and leaves the disk byte-identical.
  3. the held-back vector is not ranked, and index_coherence() counts it.
  4. reembed() realigns the space, persists the new vectors and updates the sidecar.
  5. reopening with the SAME embed_id does NOT re-embed (idempotent).
  6. default RAM-only store (persist_vectors=False) never creates the sidecar.
  7. past the old cap (INSPEXIMUS_REALIGN_MAX) nothing is dropped from disk either.
  8. a LEXICAL open of a semantic store is a pure bystander.
"""
import sys, os, tempfile
sys.path.insert(0, ".")
from inspeximus import Inspeximus

FAILS = []
def check(n, c):
    print(f"  [{'OK ' if c else 'XXX'}] {n}")
    if not c: FAILS.append(n)

d = tempfile.mkdtemp(); p = os.path.join(d, "s.json")
calls = {"n": 0}
def embA(t): return [1.0, 0.0, 0.0]
def embB(t): return [0.0, 1.0, 0.0]
def embC(t):
    calls["n"] += 1
    return [0.0, 0.0, 1.0]

m = Inspeximus(path=p, embed=embA, persist_vectors=True, embed_id="A")
m.remember("hello world", key="k"); m._save(force=True)
check("1 embed_id sidecar written on save", os.path.exists(p + ".embedid") and open(p + ".embedid").read() == "A")

before = (open(p, "rb").read(), open(p + ".embedid").read())
n_b = {"n": 0}
def embB_counted(t):
    n_b["n"] += 1
    return embB(t)
m2 = Inspeximus(path=p, embed=embB_counted, persist_vectors=True, embed_id="B")
check("2 recipe change: the open embeds nothing", n_b["n"] == 0)
check("2b ... and leaves the store file and the sidecar as they were",
      (open(p, "rb").read(), open(p + ".embedid").read()) == before)
rec = [r for r in m2.items if r.get("key") == "k"][0]
check("3 the vector made under recipe A is not ranked under recipe B", not rec.get("vec"))
check("3b index_coherence() counts it", m2.index_coherence()["foreign_recipe_vecs"] == 1)

r4 = m2.reembed()
check("4 reembed() realigns the space", r4["reembedded"] == 1 and r4["remaining"] == 0)
m2b = Inspeximus(path=p, embed=embB, persist_vectors=True, embed_id="B")
check("4b the realigned vector is on disk", [r["vec"] for r in m2b.items if r.get("key") == "k"][0] == [0.0, 1.0, 0.0])
check("4c the sidecar names the new recipe", open(p + ".embedid").read() == "B")

# same recipe B stored; pass embed=embA but embed_id="B" -> must NOT re-embed (vec stays B)
m3 = Inspeximus(path=p, embed=embA, persist_vectors=True, embed_id="B")
v3 = [r["vec"] for r in m3.items if r.get("key") == "k"][0]
check("5 same recipe = no re-embed (idempotent)", v3 == [0.0, 1.0, 0.0])

p2 = os.path.join(d, "ram.json")
mm = Inspeximus(path=p2, embed=embA, embed_id="A")   # persist_vectors=False (default)
mm.remember("x", key="k"); mm._save(force=True)
check("6 non-persist store never creates the embedid sidecar", not os.path.exists(p2 + ".embedid"))

# 7: past the old cap nothing is dropped from disk: the open does not touch the store at all.
p4 = os.path.join(d, "big.json")
m7 = Inspeximus(path=p4, embed=embA, persist_vectors=True, embed_id="A")
for i in range(12):
    m7.remember(f"rec {i}", key=f"b{i}")
m7._save(force=True)
os.environ["INSPEXIMUS_REALIGN_MAX"] = "5"
calls["n"] = 0
before4 = (open(p4, "rb").read(), open(p4 + ".embedid").read())
m8 = Inspeximus(path=p4, embed=embC, persist_vectors=True, embed_id="C")
check("7 the variable that used to cap the realign has no effect: nothing embedded, nothing dropped from disk",
      calls["n"] == 0 and (open(p4, "rb").read(), open(p4 + ".embedid").read()) == before4)
r8 = m8.reembed()
check("7b reembed() is the deliberate way to rebuild them", r8["reembedded"] == 12 and r8["remaining"] == 0)
m9 = Inspeximus(path=p4, embed=embC, persist_vectors=True, embed_id="C")
check("7c the rebuilt vectors are persisted", all(r.get("vec") == [0.0, 0.0, 1.0] for r in m9.items))
os.environ.pop("INSPEXIMUS_REALIGN_MAX", None)

# 8: a LEXICAL open of a semantic store must be a pure bystander. The Claude Code hooks default to
# embed=None (GPU-free hot path) while the store may hold vectors from a semantic session:
# persist_vectors=True + embed_id=None must (a) keep the persisted vectors across a save, and
# (b) leave the .embedid sidecar untouched.
sidecar_before = open(p4 + ".embedid").read().strip()
mL = Inspeximus(path=p4, embed=None, persist_vectors=True)          # lexical open, no recipe
mL.remember("captured lexically", key="lex1")
mL._save(force=True)
mM = Inspeximus(path=p4, embed=embC, persist_vectors=True, embed_id="C")
check("8 lexical open+save preserves the persisted vectors",
      all(r.get("vec") == [0.0, 0.0, 1.0] for r in mM.items if r.get("key") != "lex1"))
check("8b lexical save leaves the embedid sidecar untouched",
      open(p4 + ".embedid").read().strip() == sidecar_before == "C")

print(f"\n{'ALL PASS' if not FAILS else 'FAILED: ' + ', '.join(FAILS)}")
sys.exit(1 if FAILS else 0)
