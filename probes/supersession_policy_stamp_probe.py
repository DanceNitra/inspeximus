"""supersession_policy_stamp_probe.py — every supersession is stamped with its adjudicating policy.

MINED FROM: TOKI (arXiv:2606.06240) observes that memory systems resolve write-time contradictions
with one of a small family of operators (last-writer-wins, evidence-weighted, await-confirmation,
per-rule policy) but "every audited baseline omits" logging WHICH operator adjudicated each conflict —
so a store's history says WHAT was retired but not WHY, and an audit cannot distinguish a legitimate
update from a guard block or a budget eviction after the fact.

mnemo already had partial flags (echo_blocked, objectless_blocked). 0.6.18 makes the judge log
UNIFORM: every code path that retires a record stamps meta['superseded_by_policy'], history() exposes
it per row, and supersession_report() aggregates counts per policy. Zero-dependency, additive-only
(no behavior change to any resolution decision — pre-registered check H below).

Pre-registered checks — each path stamps its own name:
  A keyed_lww            plain keyed update retires the older value
  B keyed_lww_backfill   a back-filled older write is retired stale-on-arrival
  C keyed_reaffirm       reaffirm=True write retires the current value
  D echo_guard           restatement-of-superseded retired on arrival (echo_guard on)
  E objectless_guard     object-less write onto a value ledger blocked (echo_guard on)
  F state_toggle         consolidate() polarity/value clash retires the older (no gates)
  G toggle_corroborated  same, with supersede_requires_corroboration=True (corroborated newer)
  H keep_budget          consolidate(keep=N) demotes surplus
  I report               supersession_report() aggregates == per-record stamps; history() carries policy
  J regression           resolution DECISIONS identical to pre-stamp behavior (statuses unchanged
                         vs the same scenario logic — stamps are additive metadata only)
"""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from mnemo import Mnemo


def _rec(m, rid):
    return next(r for r in m.items if r["id"] == rid)


def _pol(m, rid):
    return (_rec(m, rid).get("meta") or {}).get("superseded_by_policy")


def main():
    ok = {}

    # A keyed_lww
    m = Mnemo(path=None)
    a1 = m.remember("timeout is 30", key="k", object="30", valid_from=1.0)
    m.remember("timeout is 45", key="k", object="45", valid_from=2.0)
    ok["A keyed_lww"] = (_rec(m, a1)["status"] == "superseded" and _pol(m, a1) == "keyed_lww")

    # B keyed_lww_backfill (incoming is older by valid_from)
    m = Mnemo(path=None)
    m.remember("timeout is 45", key="k", object="45", valid_from=2.0)
    b2 = m.remember("timeout is 30", key="k", object="30", valid_from=1.0)   # back-fill
    ok["B keyed_lww_backfill"] = (_rec(m, b2)["status"] == "superseded"
                                  and _pol(m, b2) == "keyed_lww_backfill")

    # C keyed_reaffirm
    m = Mnemo(path=None)
    m.remember("timeout is 30", key="k", object="30", valid_from=1.0)
    c2 = m.remember("timeout is 45", key="k", object="45", valid_from=2.0)
    m.remember("timeout is 30", key="k", object="30", valid_from=3.0, reaffirm=True)
    ok["C keyed_reaffirm"] = (_rec(m, c2)["status"] == "superseded" and _pol(m, c2) == "keyed_reaffirm")

    # D echo_guard
    m = Mnemo(path=None); m.echo_guard = True
    m.remember("timeout is 30", key="k", object="30", valid_from=1.0)
    m.remember("timeout is 45", key="k", object="45", valid_from=2.0)
    d3 = m.remember("timeout is 30 as mentioned", key="k", object="30", valid_from=3.0)  # echo of superseded
    ok["D echo_guard"] = (_rec(m, d3)["status"] == "superseded" and _pol(m, d3) == "echo_guard")

    # E objectless_guard
    m = Mnemo(path=None); m.echo_guard = True
    m.remember("timeout is 45", key="k", object="45", valid_from=1.0)
    e2 = m.remember("go back to the old one", key="k", valid_from=2.0)       # object-less onto a ledger
    ok["E objectless_guard"] = (_rec(m, e2)["status"] == "superseded" and _pol(m, e2) == "objectless_guard")

    # F state_toggle (consolidate, no gates)
    m = Mnemo(path=None)
    f1 = m.remember("the API limit is 100 requests", valid_from=1.0)
    m.remember("the API limit is 200 requests", valid_from=2.0)
    m.consolidate(dup_threshold=0.6)
    ok["F state_toggle"] = (_rec(m, f1)["status"] == "superseded" and _pol(m, f1) == "state_toggle")

    # G toggle_corroborated (gate on; newer is corroborated via earned good)
    m = Mnemo(path=None); m.supersede_requires_corroboration = True
    g1 = m.remember("the API limit is 100 requests", valid_from=1.0)
    g2 = m.remember("the API limit is 200 requests", valid_from=2.0)
    m.credit([g2], True)
    m.consolidate(dup_threshold=0.6)
    ok["G toggle_corroborated"] = (_rec(m, g1)["status"] == "superseded"
                                   and _pol(m, g1) == "toggle_corroborated")

    # H keep_budget
    m = Mnemo(path=None)
    ids = [m.remember(f"note number {i} about topic {i}", value=float(i)) for i in range(6)]
    m.consolidate(keep=3, link_duplicates=False)
    demoted = [i for i in ids if _rec(m, i)["status"] == "superseded"]
    ok["H keep_budget"] = (len(demoted) == 3 and all(_pol(m, i) == "keep_budget" for i in demoted))

    # I report aggregates == stamps; history carries policy
    m = Mnemo(path=None)
    i1 = m.remember("v is 1", key="k", object="1", valid_from=1.0)
    m.remember("v is 2", key="k", object="2", valid_from=2.0)
    rep = m.supersession_report()
    hist = m.history("k")
    ok["I report+history"] = (rep["by_policy"].get("keyed_lww") == 1
                              and rep["superseded_total"] == 1
                              and any(h["policy"] == "keyed_lww" for h in hist))

    # J regression: stamps are additive — decisions identical (statuses match expected exactly)
    m = Mnemo(path=None); m.echo_guard = True
    j1 = m.remember("timeout is 30", key="k", object="30", valid_from=1.0)
    j2 = m.remember("timeout is 45", key="k", object="45", valid_from=2.0)
    j3 = m.remember("timeout is 30 again", key="k", object="30", valid_from=3.0)  # echo -> blocked
    st = [(_rec(m, j)["status"]) for j in (j1, j2, j3)]
    ok["J regression decisions"] = (st == ["superseded", "active", "superseded"])

    print("=" * 72)
    print("Supersession policy stamps (TOKI-gap audit log) — mnemo 0.6.18")
    print("=" * 72)
    for k, v in ok.items():
        print(f"  [{'PASS' if v else 'FAIL'}] {k}")
    print("-" * 72)
    print("RECEIPT:", "VALID — all pre-registered checks hold" if all(ok.values())
          else "INVALID — do not ship")
    return 0 if all(ok.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
