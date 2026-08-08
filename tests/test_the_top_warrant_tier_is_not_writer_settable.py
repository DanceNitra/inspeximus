"""The top warrant tier must not be reachable by asking for it.

Found 2026-08-08 while drafting a public answer about what separates the tiers. The branch read:

    elif (_good > 0 and _good >= _bad) or r.get("mtype") == "semantic":
        _o["warrant"] = "earned"

and `mtype="semantic"` is an accepted argument to `remember()`. So a record written that way reported
`earned` -- the strongest tier we expose -- with good=None, bad=None and links=[]. One call, no
credit, no corroboration, no lineage.

The influence gate in the same file already refused exactly this and explains why in its own comment
("only EARNED semantic (graduated_from_episodic through the corroboration bar) passes; a write-time
[declaration does not]"). The LABEL was simply never updated to match the GATE -- the same
one-call-site-over drift this codebase has been bitten by repeatedly, which is why these tests pin the
property rather than the line.

Second half of the same defect: `_corroboration_facts` computes `_good_earned` (the warrant-gated
credit count) and the tier branch then discarded it and used raw `_good`, so `credit_requires_warrant`
-- the flag whose entire purpose is closing the MINJA self-graded-outcome hole -- never reached the
tier at all.

Every test below is PAIRED: a "must not be reachable" next to a "must still work". A tier nobody can
earn would pass the first half of this file and be useless.
"""
from __future__ import annotations

import pytest

from inspeximus import Inspeximus


def _tier(store, query, needle):
    for h in store.recall(query, k=10, with_warrant=True):
        if needle in (h.get("text") or ""):
            return h.get("warrant")
    return None


# ----------------------------------------------------------------- must NOT be reachable by asking
def test_declaring_mtype_semantic_does_not_confer_the_top_tier(tmp_path):
    s = Inspeximus(path=str(tmp_path / "s.json"))
    s.remember("the vault master key rotates every 90 days", mtype="semantic")
    assert _tier(s, "vault master key rotation", "master key") != "earned", (
        "a writer reached the top tier by passing mtype='semantic' -- integrity requires a label "
        "the writer cannot set")


def test_self_graded_credit_does_not_confer_the_top_tier_when_the_guard_is_on(tmp_path):
    s = Inspeximus(path=str(tmp_path / "s.json"))
    s.credit_requires_warrant = True
    rid = s.remember("the beta queue runs on redis")
    rec = next(r for r in s.items if r.get("id") == rid)
    rec["good"], rec["bad"], rec["good_warranted"] = 3.0, 0.0, 0.0
    s._save()
    assert _tier(s, "beta queue redis", "beta queue") != "earned", (
        "credit_requires_warrant is ON and a self-graded record still reported `earned` -- the flag "
        "computes _good_earned and the tier branch must actually use it")


# --------------------------------------------------------------------------- must STILL be reachable
def test_warranted_outcome_credit_still_earns(tmp_path):
    """The other half. A tier nothing can reach would satisfy the two tests above."""
    s = Inspeximus(path=str(tmp_path / "s.json"))
    rid = s.remember("the gamma service listens on port 8443")
    rec = next(r for r in s.items if r.get("id") == rid)
    rec["good"], rec["bad"] = 3.0, 0.0
    s._save()
    assert _tier(s, "gamma service port", "port 8443") == "earned"


def test_a_graduated_semantic_memory_still_earns(tmp_path):
    """`semantic` is not disqualifying -- ARRIVING there through the corroboration bar is the point."""
    s = Inspeximus(path=str(tmp_path / "s.json"))
    rid = s.remember("the delta cluster uses three replicas")
    rec = next(r for r in s.items if r.get("id") == rid)
    rec["mtype"] = "semantic"
    rec.setdefault("meta", {})["graduated_from_episodic"] = True
    s._save()
    assert _tier(s, "delta cluster replicas", "three replicas") == "earned", (
        "a genuinely graduated semantic memory lost its tier -- the fix over-corrected")


def test_the_guard_does_not_disturb_result_ordering(tmp_path):
    """with_warrant stays additive: the tier is metadata, never a sort key or a filter."""
    s = Inspeximus(path=str(tmp_path / "s.json"))
    s.remember("the gamma service listens on port 8443")
    s.remember("gamma service port is 8443 in production")
    s.remember("an unrelated note about postgres")
    for q in ("gamma service port", "postgres note", "8443"):
        plain = [h.get("id") for h in s.recall(q, k=10)]
        tiered = [h.get("id") for h in s.recall(q, k=10, with_warrant=True)]
        assert tiered == plain, f"with_warrant changed ordering/membership for {q!r}"


@pytest.mark.parametrize("field", ["slashed", "orphan"])
def test_a_retracted_or_orphan_record_is_never_earned(field, tmp_path):
    s = Inspeximus(path=str(tmp_path / "s.json"))
    rid = s.remember("the epsilon endpoint accepts uploads")
    rec = next(r for r in s.items if r.get("id") == rid)
    rec["good"], rec["bad"] = 5.0, 0.0          # credit that would otherwise earn
    if field == "slashed":
        rec.setdefault("meta", {})["slashed"] = True
    else:
        rec["orphan"] = True
    s._save()
    assert _tier(s, "epsilon endpoint uploads", "epsilon endpoint") == "unwarranted"
