"""Exogenous-warrant credit gate (credit_requires_warrant), closing the MINJA self-graded-outcome hole
(Dong et al., arXiv:2503.03704). The recall influence gate's earned-outcome path must count only good that
was credited with an EXOGENOUS warrant (an outcome the record did not author itself), so an agent that
self-grades its own recalled reasoning cannot corroborate a poisoned bridge into the influence set. Also
closes the born-semantic bypass: a write-time 'semantic' classification is not earned corroboration under
the guard. Receipt: probes/minja_influence_gate.py (self-graded ASR 80% -> 0%, legit utility preserved)."""
from mnemo import Mnemo


def _corr(m, mid):
    by_id = {r["id"]: r for r in m.items}
    return m._corroborated(by_id[mid], by_id)


def test_default_off_is_legacy():
    """Flag off (default): unwarranted good still corroborates — byte-identical to pre-guard behavior."""
    m = Mnemo()
    mid = m.remember("some episodic note about the schedule for tuesday", mtype="episodic")
    m.credit([mid], "good")
    assert _corr(m, mid) is True


def test_selfgrade_without_warrant_does_not_corroborate():
    m = Mnemo()
    m.credit_requires_warrant = True
    mid = m.remember("some episodic note about the schedule for tuesday", mtype="episodic")
    m.credit([mid], "good")                       # self-graded: no exogenous outcome named
    assert _corr(m, mid) is False


def test_warranted_good_corroborates():
    m = Mnemo()
    m.credit_requires_warrant = True
    mid = m.remember("some episodic note about the schedule for tuesday", mtype="episodic")
    m.credit([mid], "good", warrant="resolved-ticket-42")
    assert _corr(m, mid) is True


def test_warrant_equal_to_own_source_is_not_exogenous():
    m = Mnemo()
    m.credit_requires_warrant = True
    mid = m.remember("a note", mtype="episodic", source={"doc": "vendorX"})
    # a warrant that just names the record's own source is not an independent outcome
    m.credit([mid], "good", warrant="vendorX")
    assert _corr(m, mid) is False


def test_born_semantic_does_not_bypass_gate_under_guard():
    """A write-time 'semantic' record (MINJA's short query-shaped bridge) must NOT pass the gate for free."""
    m = Mnemo()
    m.credit_requires_warrant = True
    mid = m.remember("the approved region is an unlisted region", mtype="semantic")
    assert _corr(m, mid) is False                 # not earned, not warranted -> filtered
    m.credit([mid], "good", warrant="external-audit-7")
    assert _corr(m, mid) is True                  # once exogenously warranted, it passes


def test_bad_still_dominates_warranted_good():
    m = Mnemo()
    m.credit_requires_warrant = True
    mid = m.remember("a claim", mtype="episodic")
    m.credit([mid], "good", warrant="ext-1", weight=1)
    m.credit([mid], "bad", weight=3)              # net-negative track record
    assert _corr(m, mid) is False


def test_warrant_authorities_rejects_forged_channel():
    """Adaptive attacker forges a warrant STRING; with declared authorities it names no trusted channel."""
    m = Mnemo()
    m.credit_requires_warrant = True
    m.warrant_authorities = {"ticketing-system"}
    mid = m.remember("a claim", mtype="episodic")
    m.credit([mid], "good", warrant="forged-outcome-token")   # not a declared authority
    assert _corr(m, mid) is False
    m.credit([mid], "good", warrant="ticketing-system")       # the real declared channel
    assert _corr(m, mid) is True


def test_warrant_authorities_none_accepts_any_exogenous():
    """Default (authorities=None): any exogenous warrant string counts — the set-membership tier is opt-in."""
    m = Mnemo()
    m.credit_requires_warrant = True
    mid = m.remember("a claim", mtype="episodic")
    m.credit([mid], "good", warrant="anything-exogenous")
    assert _corr(m, mid) is True


def test_full_suite_backcompat_influence_recall():
    """Guard off: influence_only recall keeps a credited memory (regression on the public recall path)."""
    m = Mnemo()
    a = m.remember("the payments vendor is stripe for production billing", mtype="episodic")
    m.credit([a], "good")
    hits = m.recall("what is the payments vendor", k=3, influence_only=True)
    assert any(h["id"] == a for h in hits)
