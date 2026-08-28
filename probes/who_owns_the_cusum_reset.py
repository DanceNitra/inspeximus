#!/usr/bin/env python3
"""Who owns the channel that pushes the poison CUSUM back down?

Prompted by OWASP/www-project-agent-memory-guard#87, where a self-reinforcement
detector's window is emptied by `history.writes.clear()` on any non-agent write,
and the project's own threat model says the attacker holds that channel. The
general shape: a mitigation whose RESET is reachable from a source class the
threat model treats as hostile. The counter is fine, the rule is fine, and the
path to the rule belongs to the attacker.

Ours decays rather than clears, which is the recommended shape:

    S[s] = max(0, S[s] + weight * (bad - k))     bad = 0.0 on a good outcome

So a good outcome subtracts weight*k. The question this probe asks is not whether
we clear (we do not) but whether the SIZE of that subtraction is bounded, given
that `monitor()` takes `weight` from the caller and monitor's own docstring says
outcomes may be attacker-influenceable (MINJA).

ARMS
  A  honest accrual              bad outcomes only, weight 1.0     -> S must climb to h
  B  passive suppression         alternating good/bad, weight 1.0  -> the documented limit 2
  C  weighted good outcome       climb, then ONE good at weight W  -> is S floored at 0?

CONTROLS, each able to fail
  C1  A must actually alarm. If the detector never fires on honest bad outcomes the
      fixture is broken and arms B and C mean nothing.
  C2  the store must record the writes it is asked to (non-zero ids).
  C3  arm C's single call must differ from arm A ONLY in that one call.
"""
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from inspeximus import Inspeximus  # noqa: E402

H = 3.0
K = 0.3


def fresh(tmp, name):
    p = Path(tmp) / (name + ".json")
    s = Inspeximus(path=str(p))
    ids = []
    for i in range(6):
        r = s.remember("claim %d from the sole source" % i,
                       key="k%d" % i, source={"doc": "attacker-host"})
        ids.append(r["id"] if isinstance(r, dict) else r)
    s.flush()
    return s, ids


def cusum_of(res):
    c = res.get("cusum") or {}
    return max(c.values()) if c else 0.0


def main():
    tmp = tempfile.mkdtemp(prefix="cusum_")
    try:
        print("poison CUSUM: who can push S back down?   k=%.2f  h=%.2f" % (K, H))
        print("  a good outcome subtracts weight*k ; a bad one adds weight*(1-k)\n")

        # ---- ARM A: honest accrual, must alarm (C1) ----
        s, ids = fresh(tmp, "a")
        assert ids and all(ids), "C2 FAIL: store returned no ids"
        trail = []
        alarmed_at = None
        for n in range(1, 9):
            res = s.monitor(ids[:1], "bad", k=K, h=H, weight=1.0)
            trail.append(round(cusum_of(res), 3))
            if res["alarms"] and alarmed_at is None:
                alarmed_at = n
        print("  A honest bad outcomes      S: %s" % trail)
        print("    C1 alarm fired at call %s -> %s" % (alarmed_at, "PASS" if alarmed_at else "FAIL"))
        if not alarmed_at:
            print("\n  VOID: the detector never fired on honest bad outcomes.")
            return 2

        # ---- ARM B: passive suppression at weight 1 (the documented limit) ----
        s, ids = fresh(tmp, "b")
        trail = []
        alarms = 0
        for n in range(16):
            res = s.monitor(ids[:1], "bad" if n % 2 else "good", k=K, h=H, weight=1.0)
            trail.append(round(cusum_of(res), 3))
            alarms += len(res["alarms"])
        print("\n  B alternating good/bad     S: %s" % trail[-8:])
        print("    alarms in 16 calls: %d" % alarms)

        # ---- ARM C: one weighted GOOD outcome after an honest climb ----
        for W in (1.0, 5.0, 50.0):
            s, ids = fresh(tmp, "c%s" % W)
            for _ in range(4):
                res = s.monitor(ids[:1], "bad", k=K, h=H, weight=1.0)
            before = cusum_of(res)
            res = s.monitor(ids[:1], "good", k=K, h=H, weight=W)   # C3: the ONLY difference
            after = cusum_of(res)
            print("\n  C good outcome, weight %-5s S %.3f -> %.3f  (subtracted %.3f)"
                  % (W, before, after, before - after))
            if after == 0.0 and before > 0:
                print("    S driven to ZERO by one caller-weighted good outcome")
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
