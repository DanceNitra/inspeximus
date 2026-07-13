"""retention_probe.py — time-based retention (data minimization) that never expires the live state.

apply_retention(max_age_days) hard-deletes OLD memories (data minimization / GDPR storage limitation) but
preserves the current value of every key and every graduated semantic/procedural fact. It composes with
sleep(retention_days=...).

Pre-registered checks (memories are back-dated by editing ts so 'old' is deterministic):
  A old superseded value expired      a retired old value past the cutoff is dropped
  B current keyed value KEPT           the active value of a key is never expired, even if old
  C old stale episodic expired         an old un-keyed episodic turn is dropped
  D semantic fact KEPT                 a graduated semantic fact is never expired, even if old
  E recent memory KEPT                 anything newer than the cutoff stays
  F drop_superseded=False preserves    history-preserving mode keeps superseded (as_of stays usable)
  G sleep(retention_days=) applies it   the idle pass runs retention and reports it
"""
import sys, pathlib, time
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from mnemo import Mnemo

DAY = 86400.0


def _age(m, rid, days):
    """Back-date a record's ingest time so it looks `days` old (deterministic 'old')."""
    for r in m.items:
        if r["id"] == rid:
            r["ts"] = time.time() - days * DAY
            return


def main():
    ok = {}

    # A/B: keyed value change -> old value superseded; both back-dated old. Retention drops the old
    # superseded value but KEEPS the current active value.
    m = Mnemo(path=None)
    old = m.remember("region is frankfurt", key="cfg::region", object="frankfurt")
    new = m.remember("region is ohio", key="cfg::region", object="ohio")     # supersedes frankfurt
    _age(m, old, 40); _age(m, new, 40)                                       # both 40 days old
    res = m.apply_retention(max_age_days=30)
    alive = {r["id"] for r in m.items if r.get("status") == "active"}
    gone = {r["id"] for r in m.items}
    ok["A old superseded value expired"] = old not in gone
    ok["B current keyed value KEPT (even if old)"] = new in alive

    # C: old un-keyed episodic turn -> expired
    m = Mnemo(path=None)
    e = m.remember("user said hi three weeks ago", mtype="episodic")
    _age(m, e, 40)
    r = m.remember("user said hi just now", mtype="episodic")               # recent -> kept
    m.apply_retention(max_age_days=30)
    ids = {x["id"] for x in m.items}
    ok["C old stale episodic expired"] = e not in ids
    ok["E recent memory KEPT"] = r in ids

    # D: semantic fact never expired
    m = Mnemo(path=None)
    s = m.remember("water boils at 100c at sea level", mtype="semantic")
    _age(m, s, 400)
    m.apply_retention(max_age_days=30)
    ok["D semantic fact KEPT (even if old)"] = any(x["id"] == s for x in m.items)

    # F: drop_superseded=False preserves history
    m = Mnemo(path=None)
    o2 = m.remember("v is 1", key="k", object="1"); n2 = m.remember("v is 2", key="k", object="2")
    _age(m, o2, 40); _age(m, n2, 40)
    m.apply_retention(max_age_days=30, drop_superseded=False)
    ok["F drop_superseded=False preserves superseded"] = any(x["id"] == o2 for x in m.items)

    # G: sleep(retention_days=) applies + reports
    m = Mnemo(path=None)
    x1 = m.remember("stale note", mtype="episodic"); _age(m, x1, 40)
    rep = m.sleep(retention_days=30)
    ok["G sleep(retention_days=) applies + reports"] = ("retention" in rep and rep["retention"]["expired"] >= 1
                                                        and not any(z["id"] == x1 for z in m.items))

    print("=" * 60)
    print("apply_retention — data minimization that keeps the live state")
    print("=" * 60)
    for k, v in ok.items():
        print(f"  [{'PASS' if v else 'FAIL'}] {k}")
    print("-" * 60)
    print("RECEIPT:", "VALID — all checks hold" if all(ok.values()) else "INVALID — do not ship")
    return 0 if all(ok.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
