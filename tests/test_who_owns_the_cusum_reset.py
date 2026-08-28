"""A caller-supplied weight could erase the whole poison statistic in one call.

Prompted by OWASP/www-project-agent-memory-guard#87, where a self-reinforcement detector's
window is emptied by any non-agent write and the project's own threat model names that
source class as attacker-controlled. The shape is a mitigation whose RESET is reachable
from a hostile channel: the counter is fine, the rule is fine, and the path to the rule
belongs to the attacker.

Ours decays instead of clearing, which is the right shape. But the step was
`weight * (bad - k)` with `weight` taken from the caller, so a good outcome at weight 50
subtracted 50*k and floored S at zero. We never called clear(); a caller who owns the
outcome channel could buy one. monitor()'s own limit 3 says outcomes may be
attacker-influenceable, which is exactly that caller.

The fix is asymmetric: a bad outcome may still be weighted freely (accelerating detection
is the safe direction), a good one subtracts at most one call's worth.
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from inspeximus import Inspeximus

K, H = 0.3, 3.0


def _store(name="m"):
    s = Inspeximus(path=os.path.join(tempfile.mkdtemp(), name + ".json"))
    r = s.remember("a claim from the sole source", key="k1", source={"doc": "one-host"})
    return s, [r["id"] if isinstance(r, dict) else r]


def _s(res):
    c = res.get("cusum") or {}
    return max(c.values()) if c else 0.0


def test_honest_accrual_still_alarms():
    """The control. If this stops firing, the two tests below prove nothing."""
    s, ids = _store()
    fired = None
    for n in range(1, 9):
        if s.monitor(ids, "bad", k=K, h=H, weight=1.0)["alarms"] and fired is None:
            fired = n
    assert fired is not None, "the detector never fired on honest bad outcomes"
    assert fired <= 6, "detection got slower: alarmed only at call %s" % fired


def test_a_weighted_good_outcome_cannot_erase_the_statistic():
    s, ids = _store()
    for _ in range(4):
        res = s.monitor(ids, "bad", k=K, h=H, weight=1.0)
    before = _s(res)
    assert before > 1.0, "fixture did not accrue: S=%.3f" % before

    at_one = _s(s.monitor(ids, "good", k=K, h=H, weight=1.0))
    step = before - at_one
    assert step > 0, "a good outcome must still decay the statistic"

    for heavy in (5.0, 50.0, 5000.0):
        s2, ids2 = _store("h")
        for _ in range(4):
            res2 = s2.monitor(ids2, "bad", k=K, h=H, weight=1.0)
        b2 = _s(res2)
        after = _s(s2.monitor(ids2, "good", k=K, h=H, weight=heavy))
        assert abs((b2 - after) - step) < 1e-9, (
            "weight %s subtracted %.3f, one call's worth is %.3f" % (heavy, b2 - after, step))
        assert after > 0.0, "weight %s floored the statistic at zero" % heavy


def test_a_weighted_bad_outcome_is_still_free_to_accelerate():
    """The safe direction must NOT be clamped, or the fix would blunt the detector."""
    s, ids = _store()
    light = _s(s.monitor(ids, "bad", k=K, h=H, weight=1.0))
    s2, ids2 = _store("b")
    heavy = _s(s2.monitor(ids2, "bad", k=K, h=H, weight=10.0))
    assert heavy > light * 5, "a heavy bad outcome no longer accelerates: %.3f vs %.3f" % (heavy, light)
