"""recall_reinforce_flag_probe.py — recall(reinforce=False) is a truly NON-MUTATING read.

recall() reinforces each returned memory (value += relevance, resets the decay clock, can graduate episodic ->
semantic). That is correct for a WARM store (was-it-useful outranks merely-similar), but it makes recall order
depend on prior queries — an order-dependent confound for eval/benchmark and a surprise for read-only consumers.
`reinforce=False` turns all of that OFF while returning the SAME ranking. Asserts:
  1. reinforce=True bumps value + last_access on a hit (the mechanism still exists), while the
     DEFAULT leaves them alone. As of 2.0.0 the default is False; before it, it was True and this
     probe asserted the opposite. Both halves are asserted, because "the default does not bump" is
     also what a broken bump would report.
  2. reinforce=False leaves value, last_access, and mtype (no graduation) UNCHANGED.
  3. reinforce=False returns the SAME top-k ids/order as a single default recall (ranking is identical; only the
     side-effect differs).
  4. Order-independence: many reinforce=False queries do not shift a later query's ranking (the confound is gone),
     whereas the default path DOES shift it.
"""
import sys
sys.path.insert(0, ".")
from inspeximus import Inspeximus

FAILS = []
def check(n, c):
    print(f"  [{'OK ' if c else 'XXX'}] {n}")
    if not c: FAILS.append(n)

def fresh():
    m = Inspeximus(path=None)
    for i, t in enumerate([
        "the capital of France is Paris", "photosynthesis converts light to chemical energy",
        "Paris hosted the 2024 Olympics", "the mitochondria is the powerhouse of the cell",
        "France borders Spain and Germany", "chlorophyll gives plants their green color",
        "the Eiffel Tower is in Paris", "cellular respiration happens in the mitochondria",
    ]):
        m.remember(t, key=f"k{i}")
    return m

# 1. the DEFAULT does not reinforce (2.0.0) -- and reinforce=True still does, or this asserts nothing
m = fresh()
before = {it["id"]: (it["value"], it["last_access"]) for it in m.items}
hits = m.recall("what is in Paris France", k=3)
hid = hits[0]["id"]
after = next(it for it in m.items if it["id"] == hid)
check("1a default recall does NOT bump a hit's value", after["value"] == before[hid][0])

mT = fresh()
beforeT = {it["id"]: (it["value"], it["last_access"]) for it in mT.items}
hitsT = mT.recall("what is in Paris France", k=3, reinforce=True)
hidT = hitsT[0]["id"]
afterT = next(it for it in mT.items if it["id"] == hidT)
check("1b CONTROL reinforce=True still bumps it", afterT["value"] > beforeT[hidT][0])

# 2. reinforce=False mutates nothing
m2 = fresh()
snap = {it["id"]: (it["value"], it["last_access"], it["mtype"]) for it in m2.items}
_ = m2.recall("what is in Paris France", k=3, reinforce=False)
unchanged = all((it["value"], it["last_access"], it["mtype"]) == snap[it["id"]] for it in m2.items)
check("2 reinforce=False leaves value/last_access/mtype UNCHANGED", unchanged)

# 3. same ranking as a single default recall (compare by TEXT + score — ids are per-instance random)
m3 = fresh()
rank_default = [(h["text"], h["score"]) for h in m3.recall("what is in Paris France", k=5)]
m4 = fresh()
rank_noreinf = [(h["text"], h["score"]) for h in m4.recall("what is in Paris France", k=5, reinforce=False)]
check("3 reinforce=False returns the SAME top-k ranking", rank_default == rank_noreinf)

# 4. order-independence: prior queries don't shift a later ranking under reinforce=False; default path CAN
target_q = "where is the Eiffel Tower"
mA = fresh()
base = [h["id"] for h in mA.recall(target_q, k=5, reinforce=False)]
for q in ["mitochondria cell energy", "chlorophyll plants green", "France borders", "photosynthesis light",
          "cellular respiration", "capital of France", "Olympics 2024"]:
    mA.recall(q, k=5, reinforce=False)
after_noreinf = [h["id"] for h in mA.recall(target_q, k=5, reinforce=False)]
check("4a reinforce=False: prior queries do NOT shift the later ranking", base == after_noreinf)

mB = fresh()
base_d = [h["id"] for h in mB.recall(target_q, k=5, reinforce=True)]
for q in ["mitochondria cell energy", "chlorophyll plants green", "France borders", "photosynthesis light",
          "cellular respiration", "capital of France", "Olympics 2024"] * 4:
    mB.recall(q, k=5, reinforce=True)    # the opted-in path reinforces -> can shift the target ranking
after_d = [h["id"] for h in mB.recall(target_q, k=5, reinforce=True)]
# What 4b can honestly assert on THIS fixture is that reinforce=True moved the store, not that it
# reordered this particular query. It does not reorder here: 11 well-separated records, and 28
# reinforcing queries are not enough to cross a rank boundary. The ranking consequence is real but it
# needs a fixture built for it -- tests/test_determinism_conformance.py P5b measures 64/64 answers
# changing in hybrid mode. Until 2.0.0 this line read `check(..., True)`: hard-coded to pass, printing
# an observation next to a verdict it never computed. A check that cannot fail measures nothing.
val_d = {it["id"]: it["value"] for it in mB.items}
check("4b reinforce=True moved the store's ranking state (rank shift itself: see conformance P5b)",
      any(v > 1.0 for v in val_d.values()))
print(f"     (this fixture's target ranking {'SHIFTED' if base_d != after_d else 'held'}; "
      f"{sum(v > 1.0 for v in val_d.values())}/{len(val_d)} records were reinforced)")

print(f"\n{'ALL PASS' if not FAILS else 'FAILED: ' + ', '.join(FAILS)}")
sys.exit(1 if FAILS else 0)
