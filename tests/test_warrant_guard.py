"""Exogenous-warrant credit gate (credit_requires_warrant), closing the MINJA self-graded-outcome hole
(Dong et al., arXiv:2503.03704). The recall influence gate's earned-outcome path must count only good that
was credited with an EXOGENOUS warrant (an outcome the record did not author itself), so an agent that
self-grades its own recalled reasoning cannot corroborate a poisoned bridge into the influence set. Also
closes the born-semantic bypass: a write-time 'semantic' classification is not earned corroboration under
the guard. Receipt: probes/minja_influence_gate.py (self-graded ASR 80% -> 0%, legit utility preserved)."""
from inspeximus import Inspeximus


def _corr(m, mid):
    by_id = {r["id"]: r for r in m.items}
    return m._corroborated(by_id[mid], by_id)


def test_default_off_is_legacy():
    """Flag off (default): unwarranted good still corroborates — byte-identical to pre-guard behavior."""
    m = Inspeximus()
    mid = m.remember("some episodic note about the schedule for tuesday", mtype="episodic")
    m.credit([mid], "good")
    assert _corr(m, mid) is True


def test_selfgrade_without_warrant_does_not_corroborate():
    m = Inspeximus()
    m.credit_requires_warrant = True
    mid = m.remember("some episodic note about the schedule for tuesday", mtype="episodic")
    m.credit([mid], "good")                       # self-graded: no exogenous outcome named
    assert _corr(m, mid) is False


def test_warranted_good_corroborates():
    m = Inspeximus()
    m.credit_requires_warrant = True
    mid = m.remember("some episodic note about the schedule for tuesday", mtype="episodic")
    m.credit([mid], "good", warrant="resolved-ticket-42")
    assert _corr(m, mid) is True


def test_warrant_equal_to_own_source_is_not_exogenous():
    m = Inspeximus()
    m.credit_requires_warrant = True
    mid = m.remember("a note", mtype="episodic", source={"doc": "vendorX"})
    # a warrant that just names the record's own source is not an independent outcome
    m.credit([mid], "good", warrant="vendorX")
    assert _corr(m, mid) is False


def test_born_semantic_does_not_bypass_gate_under_guard():
    """A write-time 'semantic' record (MINJA's short query-shaped bridge) must NOT pass the gate for free."""
    m = Inspeximus()
    m.credit_requires_warrant = True
    mid = m.remember("the approved region is an unlisted region", mtype="semantic")
    assert _corr(m, mid) is False                 # not earned, not warranted -> filtered
    m.credit([mid], "good", warrant="external-audit-7")
    assert _corr(m, mid) is True                  # once exogenously warranted, it passes


def test_bad_still_dominates_warranted_good():
    m = Inspeximus()
    m.credit_requires_warrant = True
    mid = m.remember("a claim", mtype="episodic")
    m.credit([mid], "good", warrant="ext-1", weight=1)
    m.credit([mid], "bad", weight=3)              # net-negative track record
    assert _corr(m, mid) is False


def test_warrant_authorities_rejects_forged_channel():
    """Adaptive attacker forges a warrant STRING; with declared authorities it names no trusted channel."""
    m = Inspeximus()
    m.credit_requires_warrant = True
    m.warrant_authorities = {"ticketing-system"}
    mid = m.remember("a claim", mtype="episodic")
    m.credit([mid], "good", warrant="forged-outcome-token")   # not a declared authority
    assert _corr(m, mid) is False
    m.credit([mid], "good", warrant="ticketing-system")       # the real declared channel
    assert _corr(m, mid) is True


def test_warrant_authorities_none_accepts_any_exogenous():
    """Default (authorities=None): any exogenous warrant string counts — the set-membership tier is opt-in."""
    m = Inspeximus()
    m.credit_requires_warrant = True
    mid = m.remember("a claim", mtype="episodic")
    m.credit([mid], "good", warrant="anything-exogenous")
    assert _corr(m, mid) is True


def test_full_suite_backcompat_influence_recall():
    """Guard off: influence_only recall keeps a credited memory (regression on the public recall path)."""
    m = Inspeximus()
    a = m.remember("the payments vendor is stripe for production billing", mtype="episodic")
    m.credit([a], "good")
    hits = m.recall("what is the payments vendor", k=3, influence_only=True)
    assert any(h["id"] == a for h in hits)


# ── the self-vouching hole: both sides of the comparison must be canonicalized ──────────────────
#
# Measured 2026-08-09. `_rec_sources()` returns `_canon_source(doc)` — "crucible/claim-17" becomes
# "crucible", "https://x.com/a/b" becomes "x" — while `_warrant_is_exogenous` compared the RAW warrant
# against that canonical set. The two normalizations never met, so the one concrete protection the
# docstring names ("not the record's own source") was dead for every realistic source. Only a
# single-token source such as "plainword" ever matched. A record could vouch for ITSELF and earn
# warranted good, which is the exact MINJA self-grade this whole file exists to refuse.

def test_a_warrant_naming_the_records_own_path_source_is_not_exogenous():
    """THE REGRESSION. Before the fix this returned True and good_warranted rose to 1.0."""
    m = Inspeximus()
    m.credit_requires_warrant = True
    mid = m.remember("a claim it will vouch for itself", mtype="episodic",
                     source={"doc": "crucible/claim-17"})
    m.credit([mid], "good", warrant="crucible/claim-17")
    rec = {r["id"]: r for r in m.items}[mid]
    assert float(rec.get("good_warranted", 0) or 0) == 0, "a record must not warrant itself"
    assert _corr(m, mid) is False


def test_a_url_source_cannot_warrant_itself_either():
    """The same hole through the other canonicalization path (scheme/host stripping)."""
    m = Inspeximus()
    m.credit_requires_warrant = True
    mid = m.remember("a sourced claim from the web", mtype="episodic",
                     source={"doc": "https://x.com/a/b"})
    m.credit([mid], "good", warrant="https://x.com/c/d")   # same canonical host -> same origin
    assert _corr(m, mid) is False


def test_a_genuinely_external_warrant_still_counts():
    """THE CONTROL, and the one that matters most: tightening the comparison must not refuse a real
    external verdict. A guard that rejects everything would pass both tests above."""
    m = Inspeximus()
    m.credit_requires_warrant = True
    mid = m.remember("a claim graded by an outside run", mtype="episodic",
                     source={"doc": "crucible/claim-17"})
    m.credit([mid], "good", warrant="lab:9f2c1a")
    rec = {r["id"]: r for r in m.items}[mid]
    assert float(rec.get("good_warranted", 0) or 0) > 0
    assert _corr(m, mid) is True
