"""Why did the mnemo arm trend WORSE on stale_value (0.211 vs naive 0.125)?

Two rival explanations, and they have opposite consequences:
  (A) NULL — the retriever simply never returns the corrected value, both arms guess from history,
      and the difference is 3 probes of noise. Nothing to fix.
  (B) BUG — mnemo's keyed layer (supersession + read-time conflict resolution) actively drops or
      demotes the CURRENT value while keeping an older one. That would be the integrity layer
      producing the exact failure it exists to prevent.

This tells them apart with zero LLM calls. For every Update file we take the confirmed operation
chain, derive the final value and the superseded ones, and ask of each arm's retrieved context:
is the current value present? are stale values present? is it stale-ONLY (the state that makes a
stale answer near-inevitable)?
"""
import collections
import json
import os
import pathlib
import re
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
os.environ.setdefault("MEMOPS_TOPK", "150")
HERE = pathlib.Path(__file__).resolve().parent

import pilot  # noqa: E402


def norm(s):
    return re.sub(r"\s+", " ", (s or "").lower()).strip()


def chain_values(ev):
    """Final confirmed value per chain + the values it superseded.

    'tentative' operations are excluded from the final state: MemOps marks them as announced-but-not-
    in-effect, and the gold answers treat the last CONFIRMED value as current.
    """
    chains = collections.defaultdict(list)
    for op in ev.get("operations", []):
        if op.get("validity") != "confirmed":
            continue
        chains[op.get("chain_id") or "c"].append(op)
    cur, stale = [], []
    for ops in chains.values():
        ops.sort(key=lambda o: o.get("chain_step", 0))
        vals = [o.get("new_value") for o in ops if o.get("new_value")]
        if not vals:
            continue
        cur.append(norm(vals[-1]))
        stale += [norm(v) for v in vals[:-1]]
    return [c for c in cur if c], [s for s in stale if s and s not in cur]


def main():
    files = sorted({r["file"] for r in json.loads((HERE / "pilot_raw_k150.json").read_text(encoding="utf-8"))
                    if r["op"] == "update"})
    acc = collections.defaultdict(lambda: collections.Counter())
    print(f"update files: {files}\n")
    for name in files:
        lc = json.loads((HERE / "data_lc" / name).read_text(encoding="utf-8"))
        ev = json.loads((HERE / "data" / name).read_text(encoding="utf-8"))
        cur, stale = chain_values(ev)
        probes = [a for a in (lc.get("answer") or []) if a.get("question") and a.get("expected_answer")]
        stores = {"mnemo": pilot.build_mnemo(lc, True), "naive": pilot.build_mnemo(lc, False)}
        # does the integrity layer even see this chain? (a store where nothing was superseded
        # cannot be blamed for a stale answer)
        sup = [r for r in stores["mnemo"].items if r.get("status") == "superseded"]
        print(f"{name:20} current={cur} stale={stale} superseded_records={len(sup)}")
        for a in probes:
            for arm in ("mnemo", "naive"):
                ctx = norm(pilot.recall_mnemo(stores[arm], a["question"], arm == "mnemo")[:12000])
                has_cur = any(v in ctx for v in cur)
                has_stale = any(v in ctx for v in stale)
                acc[arm]["n"] += 1
                acc[arm]["cur"] += has_cur
                acc[arm]["stale"] += has_stale
                acc[arm]["stale_only"] += (has_stale and not has_cur)
                acc[arm]["neither"] += (not has_stale and not has_cur)
    print("\n" + "=" * 66)
    print(f"{'arm':8} {'n':>4} {'has_current':>12} {'has_stale':>10} {'STALE_ONLY':>11} {'neither':>8}")
    for arm, c in acc.items():
        n = c["n"]
        print(f"{arm:8} {n:>4} {c['cur']/n:12.3f} {c['stale']/n:10.3f} "
              f"{c['stale_only']/n:11.3f} {c['neither']/n:8.3f}")
    (HERE / "stale_diagnostic.json").write_text(json.dumps({k: dict(v) for k, v in acc.items()}, indent=1),
                                                encoding="utf-8")


if __name__ == "__main__":
    main()
