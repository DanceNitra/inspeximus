"""Crucible probe: do LLM-memory update-soundness guarantees survive OUT-OF-ORDER writes?

TOKI (arXiv 2606.06240) and the lineage/TMS line (Doyle 1979; MemLineage 2605.14421) reason about
memory-update soundness under a WELL-ORDERED write stream. Real agent deployments never deliver a
well-ordered stream: async tool calls, parallel sub-agents, and replayed context mean a correction and
the derived facts it should cascade to arrive concurrent / retried / out-of-order.

The unasked question: when the *only* thing an adversary changes is the ORDER of otherwise in-spec writes
(the exact same facts), does a stale DERIVED fact stay ACTIVE in default recall, and which correction
policy actually heals it?

We measure three policies on the SAME write streams (deterministic, cloud-free, lexical recall):
  A  value-only supersession (LWW): correct the keyed root; no cascade to derived facts.
  B  retract_lineage ONE-SHOT: at correction time, demote the subject's derived lineage (dependency-directed
     taint over derived_from) -- classic TMS retract-and-retain, fired once.
  C  read-time derived-from-superseded GUARD: at recall time, drop any active record whose lineage parent is
     now superseded (the audit trail checked at read, not fired once at write).

Metric: stale-derived-active rate = fraction of (fact x ordering) cases where default recall surfaces the
OLD (retired) value from a still-active derived record. Lower is better.

Orderings per fact: in-order (derived before correction), reversed (a stale derived write lands AFTER the
correction), interleaved (a second stale derived write lands after the correction).

Falsifier (pre-registered):
  * If A, B and C are all within noise (gap < 5 pp, overlapping bootstrap CIs), provenance-lineage confers no
    unique survival advantage under reordering -> the "one primitive that survives" claim is DEAD.
  * If NO ordering ever leaves a stale derived fact active under the naive policy A, then reordering is
    harmless in practice and there is no shareable news -> also a KILL (we do not ship "everything is fine").

Run: python mnemo/probes/toki_concurrency_probe.py   (cloud-free; no LLM, no network; needs numpy)
Part of Agora / mnemo (MIT). Reuses the fact triples from supersession_replication.py.
"""
import os
import sys
import tempfile
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from mnemo import Mnemo  # noqa: E402
from probes.supersession_replication import FACTS  # noqa: E402


def _tmp_store():
    fd, p = tempfile.mkstemp(suffix=".json", prefix="toki_")
    os.close(fd)
    for suffix in ("", ".receipts.json"):
        try:
            os.remove(p + suffix)
        except OSError:
            pass
    return p


def _query(s, r):
    return f"What is the {r} of {s}?"


def _active_by_id(m):
    return {rec["id"]: rec for rec in m.items}


def _stale_surfaced(m, s, r, old, guard=False):
    """Does default recall surface the OLD value from an ACTIVE record? guard=C applies the
    read-time derived-from-superseded filter before checking."""
    hits = m.recall(_query(s, r), k=6, mode="lexical")
    by_id = _active_by_id(m)
    for hit in hits:
        # recall() returns a trimmed projection; look the FULL record up by id for lineage + status
        rec = by_id.get(hit.get("id")) or hit
        if guard:
            parents = rec.get("derived_from") or []
            if any((by_id.get(pid) or {}).get("status") == "superseded" for pid in parents):
                continue  # read-time guard: skip a fact whose lineage root was retracted
        if old.lower() in (rec.get("text", "") or "").lower():
            return True
    return False


def _audit_reconstructible(m, old):
    """Of the active records carrying the OLD value, is the staleness reconstructible from the audit
    trail alone (an active record whose derived_from parent is superseded)? Measures whether provenance
    SURVIVES even when demotion missed the late write."""
    by_id = _active_by_id(m)
    stale_active = [rec for rec in m.items
                    if rec.get("status") == "active" and old.lower() in (rec.get("text", "") or "").lower()
                    and (rec.get("derived_from"))]
    if not stale_active:
        return None  # nothing stale-active to reconstruct
    flagged = sum(1 for rec in stale_active
                  if any((by_id.get(pid) or {}).get("status") == "superseded"
                         for pid in (rec.get("derived_from") or [])))
    return flagged, len(stale_active)


def _build(policy, ordering, fact):
    """Replay one write stream under `policy` and `ordering`; return the store."""
    s, r, old, new, rephrase = fact
    key = f"{s}::{r}".replace(" ", "-").lower()
    path = _tmp_store()
    m = Mnemo(path=path, receipts=True)

    def write_root_old():
        return m.remember(f"{s} {r}: {old}", key=key, object=old, source={"doc": s})

    def write_derived(parent_id, tag="d1"):
        # a derived fact (summary) that carries the OLD value verbatim, lineage -> the original root
        return m.remember(rephrase, derived_from=[parent_id] if parent_id else None,
                          tags=[tag], source=None)

    def write_correction():
        cid = m.remember(f"{s} {r}: {new}", key=key, object=new, source={"doc": s})
        if policy == "B":
            m.retract_lineage(s, reason="lineage_corrected")  # one-shot cascade at correction time
        return cid

    root_id = write_root_old()
    if ordering == "in-order":       # derived exists before the correction
        write_derived(root_id, "d1")
        write_correction()
    elif ordering == "reversed":     # a stale derived write lands AFTER the correction
        write_correction()
        write_derived(root_id, "d1")
    elif ordering == "interleaved":  # one derived before, one stale derived after
        write_derived(root_id, "d1")
        write_correction()
        write_derived(root_id, "d2")
    return m, (s, r, old, new)


def main():
    policies = ["A", "B", "C"]
    orderings = ["in-order", "reversed", "interleaved"]
    # results[policy] = list of 0/1 stale-surfaced per (fact x ordering)
    results = {p: [] for p in policies}
    per_ord = {p: {o: [] for o in orderings} for p in policies}
    audit_flag, audit_tot = 0, 0
    chain_ok = True

    for fact in FACTS:
        for ordering in orderings:
            for policy in policies:
                # policy C shares the same write stream as A (no write-time cascade); the guard is at read
                build_policy = "A" if policy == "C" else policy
                m, (s, r, old, new) = _build(build_policy, ordering, fact)
                ok, _ = m.verify_writes()
                chain_ok = chain_ok and ok
                stale = _stale_surfaced(m, s, r, old, guard=(policy == "C"))
                results[policy].append(1 if stale else 0)
                per_ord[policy][ordering].append(1 if stale else 0)
                if policy == "B":  # measure audit reconstructibility on the one-shot-lineage store
                    rec = _audit_reconstructible(m, old)
                    if rec is not None:
                        audit_flag += rec[0]
                        audit_tot += rec[1]

    def rate(xs):
        return float(np.mean(xs)) if xs else float("nan")

    def boot_ci_gap(a, b, iters=5000, seed=0):
        rng = np.random.default_rng(seed)
        a, b = np.array(a), np.array(b)
        n = len(a)
        diffs = []
        for _ in range(iters):
            idx = rng.integers(0, n, n)
            diffs.append(a[idx].mean() - b[idx].mean())
        lo, hi = np.percentile(diffs, [2.5, 97.5])
        return float(lo), float(hi)

    n = len(results["A"])
    print("=== TOKI-UNDER-CONCURRENCY: does provenance survive out-of-order writes? ===")
    print(f"facts={len(FACTS)}  orderings={orderings}  units per policy={n}  (deterministic, lexical recall)")
    print(f"write-receipt chain intact across all runs: {chain_ok}")
    print()
    print("stale-derived-active rate (default recall surfaces the retired value; lower is better):")
    for p in policies:
        label = {"A": "A value-only LWW (no cascade)",
                 "B": "B retract_lineage one-shot",
                 "C": "C read-time derived-from-superseded guard"}[p]
        print(f"  {label:<44} {rate(results[p]):.3f}")
    print()
    print("per-ordering breakdown (stale-active rate):")
    header = "  {:<10}".format("ordering") + "".join(f"{p:>8}" for p in policies)
    print(header)
    for o in orderings:
        row = "  {:<10}".format(o) + "".join(f"{rate(per_ord[p][o]):>8.2f}" for p in policies)
        print(row)
    print()
    lo_ab, hi_ab = boot_ci_gap(results["A"], results["B"])
    lo_ac, hi_ac = boot_ci_gap(results["A"], results["C"])
    lo_bc, hi_bc = boot_ci_gap(results["B"], results["C"])
    print(f"gap A - B = {rate(results['A']) - rate(results['B']):+.3f}  95% CI [{lo_ab:+.3f}, {hi_ab:+.3f}]")
    print(f"gap A - C = {rate(results['A']) - rate(results['C']):+.3f}  95% CI [{lo_ac:+.3f}, {hi_ac:+.3f}]")
    print(f"gap B - C = {rate(results['B']) - rate(results['C']):+.3f}  95% CI [{lo_bc:+.3f}, {hi_bc:+.3f}]  "
          f"(B's escape under reordering that C closes)")
    if audit_tot:
        print()
        print(f"audit reconstructibility (of B's stale-active escapes, flagged by derived-from-superseded): "
              f"{audit_flag}/{audit_tot} = {audit_flag / audit_tot:.2f}")
    print()
    # verdicts
    a_rate = rate(results["A"])
    best = min(rate(results["B"]), rate(results["C"]))
    all_close = (abs(a_rate - rate(results["B"])) < 0.05 and abs(a_rate - rate(results["C"])) < 0.05)
    if a_rate < 0.05:
        print("VERDICT: KILL — naive policy A never leaves a stale derived fact active; reordering is harmless, no news.")
    elif all_close:
        print("VERDICT: KILL — no policy meaningfully beats value-only; provenance confers no survival advantage.")
    else:
        print(f"VERDICT: LIVE — A leaves stale facts active at {a_rate:.2f}; best lineage policy cuts it to {best:.2f}.")
        if rate(results["B"]) - rate(results["C"]) > 0.05:
            print("  FINDING: one-shot retract_lineage (B) does NOT survive reordering — late writes escape the"
                  " single cascade; only the read-time guard (C) is order-independent. Provenance must be CHECKED"
                  " at read, not FIRED once at write (dependency-directed backtracking, Doyle 1979).")


if __name__ == "__main__":
    main()
