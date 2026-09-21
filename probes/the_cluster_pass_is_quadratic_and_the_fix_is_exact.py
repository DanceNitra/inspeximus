"""_cluster_active() before and after 3.4.0, on the same prefixes of a live store: the time, and whether
the clusters are the SAME.

WHY. Crew OS measured Inspeximus.sleep() at 305 to 378 s on a 3,343-record store (2026-09-21) and
the daemon stopped ticking for four minutes. Their baseline (TASKS/bm_cluster_active.py, commit
6327d6e): 8.3 / 33.8 / 116.9 / 300.4 s on prefixes of 500 / 1,000 / 2,000 / 3,343 active records,
clusters 239 / 606 / 1,365 / 2,426, so #clusters ~ 0.73 n and the pass is quadratic. Two causes: the
centroid text was re-tokenised on every comparison, and every cluster was scored even when it could
not reach the threshold.

WHAT THIS RUNS. The pre-3.4.0 algorithm, copied verbatim below as `_old`, and the shipped
`_cluster_active`, on the same value-sorted prefixes of the same store, lexical mode. For each size:
seconds for each, and whether the two outputs are identical as lists of id lists. The fix is
described as exact, so a single differing cluster refutes it; that is the control.

CONTROL. `--sizes 200` with a deliberately wrong threshold in the new pass would differ; the probe
instead asserts identity, and the mutation set carries the case where the index bound is loosened.

    python probes/the_cluster_pass_is_quadratic_and_the_fix_is_exact.py --store <path> --sizes 500,1000,2000,3343
"""
from __future__ import annotations

import argparse
import io
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))


def _old(store, active, sim_threshold=0.5):
    """The 3.3.0 pass, verbatim except that `active` is passed in."""
    cents = []
    for r in active:
        rvec = store._qvec(r["text"])
        best = None
        for c in cents:
            s = store._similarity(c["rec"]["text"], r, c["vec"])
            if s >= sim_threshold and (best is None or s > best[1]):
                best = (c, s)
        if best:
            best[0]["members"].append(r)
        else:
            cents.append({"rec": r, "vec": rvec, "members": [r]})
    return [c["members"] for c in cents]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--store", required=True)
    ap.add_argument("--sizes", default="500,1000,2000")
    ap.add_argument("--sim", type=float, default=0.5)
    ap.add_argument("--skip-old-above", type=int, default=2000,
                    help="the old pass is quadratic; above this size only the new pass is timed")
    a = ap.parse_args()
    from inspeximus import Inspeximus
    m = Inspeximus(a.store)
    active = sorted([r for r in m.items if r.get("status") == "active"
                     and (m.tenant is None or r.get("tenant") == m.tenant)], key=lambda r: -r["value"])
    out = {"probe": os.path.basename(__file__), "store": a.store, "sim": a.sim, "active": len(active),
           "embed": bool(m.embed), "rows": []}
    print(f"store {a.store}: {len(m.items)} records, {len(active)} active, embed={bool(m.embed)}")
    for n in [int(x) for x in a.sizes.split(",") if x.strip()]:
        prefix = active[:n]
        row = {"n": len(prefix)}
        # the new pass, on a store whose items are exactly this prefix
        m.items[:] = prefix
        m._tok_cache.clear()
        t0 = time.perf_counter(); new = m._cluster_active(a.sim); row["new_s"] = round(time.perf_counter() - t0, 2)
        row["clusters"] = len(new)
        if n <= a.skip_old_above:
            m._tok_cache.clear()
            t0 = time.perf_counter(); old = _old(m, prefix, a.sim); row["old_s"] = round(time.perf_counter() - t0, 2)
            row["identical"] = [[r["id"] for r in c] for c in old] == [[r["id"] for r in c] for c in new]
            row["speedup"] = round(row["old_s"] / row["new_s"], 1) if row["new_s"] else None
        out["rows"].append(row)
        print("  n=%5d  new %7.2f s  old %s  clusters %d  identical %s" % (
            row["n"], row["new_s"], ("%7.2f s" % row["old_s"]) if "old_s" in row else "   (skipped)",
            row["clusters"], row.get("identical", "-")))
        m.items[:] = active + [r for r in m.items if r.get("status") != "active"]
    out["CONTROL_every_compared_prefix_is_identical"] = all(r.get("identical", True) for r in out["rows"])
    json.dump(out, io.open(os.path.join(HERE, os.path.basename(__file__).replace(".py", ".result.json")), "w",
                           encoding="utf-8"), indent=2)
    return 0 if out["CONTROL_every_compared_prefix_is_identical"] else 1


if __name__ == "__main__":
    sys.exit(main())
