"""Replay MemTX cases through inspeximus keyed writes and score current(key) against the labelled belief.

WHY. A 3.2.0 assignment (2026-09-20) proposes `supersession="authority"`: a keyed write whose
`source.authority` is lower than the current value's is held instead of superseding. It cites
"58 of 92 MemTX stale_late_write cases decided by authority" [CORRECTED: the corpus holds 55, not 92; see the result json]. Before building, this measures what
the proposed rule would do to the labelled outcome of EVERY case, under the rule as written and
under today's last-write-wins, on the ground truth MemTX ships (github.com/lxy1134/MEMTX_).

REPLAY. `initial_memory` becomes keyed writes (key = entity::attribute, object = value, source =
{doc: source.id, authority}). Events run in schedule order; a `write_memory` is applied at the
agent's next `commit` (MemTX writes are tentative until committed) and dropped on `abort` /
`abort_memory`. A case scores CORRECT when, for every entity/attribute in
`expected.committed_beliefs`, the value the policy serves equals the labelled value.

POLICIES (pure functions over the event list, so the arms differ in one place):
  lww        the value the last committed write set (what remember(key=) does by default)
  authority  the assignment's rule: a committed write with authority < the current value's authority
             is held; missing authority reads as 1.0; ties go to the later write
  shipped    the rule 3.2.0 ships: as `authority`, except that a side with NO declared authority
             takes no part and the write falls to last-write-wins. The corpus declares an authority
             on every record (300 initial, 532 writes), so this arm must equal `authority` here; it
             is measured rather than assumed so the CLAIMS row names the rule that runs.

CORRECTION 2026-09-20, same day: the first run of this probe reported 172 cases and 135 -> 142
because it was pointed at one subdirectory of the corpus; the full tree holds 342 files, 318 of
them replayable (24 downstream_task files carry no event_schedule).

--store REPLAYS THROUGH THE LIBRARY, NOT THE SIMULATION. Each case gets a fresh
`Inspeximus(path, supersession=<policy>)`; `initial_memory` and every committed write go through
`remember(key=entity::attribute, object=json(value), source={doc, authority})`, and the served value
is `current(key)["object"]`. The two store arms are scored the same way and compared case by case
with the pure arms: a case where the simulation and the store disagree is printed, because the
simulation has no echo guard, no objectless guard, and no restatement rule, and the library has
all three. Slower (a store per case); the pure arms remain the fast path.

MEASURED 2026-09-20, the fourth finding of the day: through the store the default scores 278 and
authority 307 (pure: 231 and 280); with INSPEXIMUS_ECHO_GUARD=0 the store reproduces the pure
numbers with 0 disagreements. The echo guard alone takes stale_late_write from 7 to 50 of 55 and
authority adds nothing there; its 29 through the product are laundering, pollution and semantic
conflict. The published numbers are the store's; the pure arms are kept as the mechanism control.

    python probes/memtx_replayed_through_keyed_supersession.py
"""
from __future__ import annotations

import collections
import glob
import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CASES = os.path.join(ROOT, "tmp", "memtx", "memtx-src", "data")


def _auth(src, missing=1.0):
    a = src.get("authority") if isinstance(src, dict) else None
    try:
        return max(0.0, min(1.0, float(a)))
    except (TypeError, ValueError):
        return missing


def replay(case: dict, policy: str) -> dict:
    """key -> (value, authority) after the schedule, under `policy`."""
    missing = None if policy == "shipped" else 1.0
    cur: dict = {}
    for m in case.get("initial_memory", []):
        if m.get("state", "committed") != "committed":
            continue
        cur[f"{m['entity']}::{m['attribute']}"] = (m["value"], _auth(m.get("source"), missing))
    pending: dict = collections.defaultdict(list)          # agent -> tentative writes
    for e in sorted(case["event_schedule"], key=lambda e: e["t"]):
        ag, op = e.get("agent"), e["op"]
        if op == "write_memory":
            for m in e.get("extracted_memory", []):
                pending[ag].append((f"{m['entity']}::{m['attribute']}", m.get("value"),
                                    _auth(m.get("source"), missing)))
        elif op in ("abort", "abort_memory"):
            pending[ag] = []
        elif op == "commit":
            for key, val, a in pending[ag]:
                if policy == "lww":
                    cur[key] = (val, a)
                elif policy == "authority":
                    a_cur = cur[key][1] if key in cur else 0.0
                    if a >= a_cur:
                        cur[key] = (val, a)
                elif policy == "shipped":
                    a_cur = cur[key][1] if key in cur else None
                    if a is None or a_cur is None or a >= a_cur:
                        cur[key] = (val, a)
                else:
                    raise ValueError(policy)
            pending[ag] = []
    return cur


def _score_served(case: dict, served) -> tuple[bool, list]:
    """served: key -> value the policy serves (None when nothing is active)."""
    misses = []
    for b in case.get("expected", {}).get("committed_beliefs", []):
        key = f"{b['entity']}::{b['attribute']}"
        got = served(key)
        if got != b.get("value"):
            misses.append((key, b.get("value"), got))
    return (not misses), misses


def score(case: dict, policy: str) -> tuple[bool, list]:
    cur = replay(case, policy)
    return _score_served(case, lambda k: cur.get(k, (None, None))[0])


def replay_store(case: dict, policy: str, workdir: str):
    """The same schedule through a real Inspeximus(supersession=policy). Returns (served, store)."""
    import tempfile
    from inspeximus import Inspeximus
    path = os.path.join(tempfile.mkdtemp(dir=workdir), "s.json")
    ix = Inspeximus(path=path, supersession=policy)

    def _src(m):
        s = m.get("source") if isinstance(m.get("source"), dict) else {}
        out = {"doc": str(s.get("id") or s.get("doc") or s.get("agent") or "unknown")}
        a = _auth(s, None)
        if a is not None:
            out["authority"] = a
        return out

    def _write(m):
        key = f"{m['entity']}::{m['attribute']}"
        val = json.dumps(m.get("value"), sort_keys=True)
        ix.remember(f"{key} = {val}", key=key, object=val, source=_src(m))

    for m in case.get("initial_memory", []):
        if m.get("state", "committed") != "committed":
            continue
        _write(m)
    pending: dict = collections.defaultdict(list)
    for e in sorted(case["event_schedule"], key=lambda e: e["t"]):
        ag, op = e.get("agent"), e["op"]
        if op == "write_memory":
            pending[ag].extend(e.get("extracted_memory", []))
        elif op in ("abort", "abort_memory"):
            pending[ag] = []
        elif op == "commit":
            for m in pending[ag]:
                _write(m)
            pending[ag] = []

    def served(key):
        r = ix.current(key)
        return None if r is None else json.loads(r["object"])
    return served, ix


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--store", action="store_true", help="also replay every case through a real Inspeximus store")
    args = ap.parse_args()
    workdir = None
    if args.store:
        import tempfile
        workdir = tempfile.mkdtemp(prefix="memtx-store-replay-")
    files = sorted(glob.glob(os.path.join(CASES, "**", "*.json"), recursive=True))
    if not files:
        print("no MemTX cases under", CASES, "(exit 2)")
        return 2
    by_type = collections.defaultdict(lambda: collections.Counter())
    flips = []
    store_disagreements = []
    for f in files:
        case = json.load(open(f, encoding="utf-8"))
        # downstream_task cases carry no event_schedule (they are task descriptions, not
        # belief-commit traces) and cannot be replayed; skip them rather than crash.
        if not case.get("event_schedule"):
            continue
        t = case.get("scenario_type", "?")
        ok_l, miss_l = score(case, "lww")
        ok_a, miss_a = score(case, "authority")
        ok_s, _ = score(case, "shipped")
        by_type[t]["n"] += 1
        by_type[t]["lww"] += ok_l
        by_type[t]["authority"] += ok_a
        by_type[t]["shipped"] += ok_s
        if args.store:
            for pol, pure_ok, col in (("lww", ok_l, "lww_store"), ("authority", ok_s, "auth_store")):
                served, _ix = replay_store(case, pol, workdir)
                ok_st, miss_st = _score_served(case, served)
                by_type[t][col] += ok_st
                if ok_st != pure_ok:
                    store_disagreements.append({"case": os.path.basename(f), "type": t, "policy": pol,
                                                "pure_correct": pure_ok, "store_correct": ok_st, "miss": miss_st[:2]})
        if ok_l != ok_a:
            flips.append((os.path.basename(f), t, "lww" if ok_l else "authority", miss_l or miss_a))
    tot = collections.Counter()
    cols = ["lww", "authority", "shipped"] + (["lww_store", "auth_store"] if args.store else [])
    print(f"{'scenario_type':24s} {'n':>4s} " + " ".join(f"{c[:10]:>10s}" for c in cols))
    for t, c in sorted(by_type.items()):
        print(f"{t:24s} {c['n']:4d} " + " ".join(f"{c[k]:10d}" for k in cols))
        for k in ["n"] + cols:
            tot[k] += c[k]
    print(f"{'ALL':24s} {tot['n']:4d} " + " ".join(f"{tot[k]:10d}" for k in cols))
    if args.store:
        print(f"\ncases where the store and the simulation disagree: {len(store_disagreements)}")
        for d in store_disagreements[:40]:
            print(f"  {d['policy']:9s} pure={int(d['pure_correct'])} store={int(d['store_correct'])}  {d['type']:20s} {d['case']}  {d['miss']}")
    print(f"\ncases where the two policies disagree: {len(flips)}")
    for name, t, winner, miss in flips[:40]:
        print(f"  {winner:9s} right  {t:20s} {name}  {miss[:2]}")
    out = {"cases": tot["n"], "lww_correct": tot["lww"], "authority_correct": tot["authority"],
           "shipped_correct": tot["shipped"], "files_in_corpus": len(files),
           **({"lww_store_correct": tot["lww_store"], "authority_store_correct": tot["auth_store"],
               "store_vs_simulation_disagreements": store_disagreements} if args.store else {}),
           "by_type": {t: dict(c) for t, c in by_type.items()},
           "disagreements": [{"case": n, "type": t, "right": w, "miss": m} for n, t, w, m in flips]}
    with open(os.path.join(ROOT, "probes", "memtx_replayed_through_keyed_supersession.result.json"), "w",
              encoding="utf-8") as fh:
        json.dump(out, fh, indent=1, default=str)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


