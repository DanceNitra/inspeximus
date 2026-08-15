"""An amendment records WHY it rewrote a committed field, and the reason is inside the hash.

WHERE THIS CAME FROM. yun520-1, on NousResearch/hermes-agent#34352, reviewing the 1.67.0 -> 1.68.0
trust-laundering fix:

    "Amendment forgives only the field it actually rewrites" is precise, but if the amendment record
    doesn't say why (corrected typo vs updated fact), the audit trail can't distinguish a correction
    from a quiet rewrite. Same family as our signed_at vs created_at split — the reason a record
    changed is as auditable as the change itself.

He is right, and the gap was real: `_emit_write_receipt` carried `amends` (WHICH committed field a
receipt legitimately rewrites) and nothing about WHY.

THE REASON IS COMMITTED, and that is the whole design. A reason living outside the hash would be
worse than none: anyone with file access could append a flattering one to a laundering amendment, or
strip an inconvenient one, and the chain would still verify. Committed, it can be CONTRADICTED but
never rewritten — which is the same property the split commitment gave `text`.

It also goes into `_chain_core`, the ONE shared preimage definition, for the reason that function's
own docstring records: 1.68.0 added `amends` to the emitter and the verifier while missing
`_recompute_tip` and `audit_bundle._rewalk`, and anchor() then committed to a truncated chain. Four
definitions of one preimage, and the fix reached two of them.
"""
from __future__ import annotations

import os
import tempfile

import pytest

from inspeximus import Inspeximus


def _store(**kw) -> Inspeximus:
    d = tempfile.mkdtemp()
    return Inspeximus(path=os.path.join(d, "s.json"), receipts=True, **kw)


def _amendments(ix) -> list[dict]:
    return [r for r in ix._receipts if r.get("amends")]


def _remember(ix, text: str) -> str:
    m = ix.remember(text, mtype="semantic")
    return m if isinstance(m, str) else m["id"]


def test_a_stated_reason_is_recorded_on_the_amendment():
    ix = _store()
    m = _remember(ix, "the retrieval gate does not separate poisoned from stale evidence")
    ix.slash([m], scope="memory", reason="corrected: the run used the wrong fixture")
    a = _amendments(ix)
    assert len(a) == 1, f"expected one amendment, got {len(a)}"
    assert a[0]["amends"] == ["mtype"]
    assert a[0]["amend_reason"] == "corrected: the run used the wrong fixture"
    assert ix.verify_writes() == (True, [])


def test_an_unstated_reason_is_recorded_as_unstated_not_omitted():
    """A missing key is indistinguishable from a caller that had nothing to say. An optional field
    nobody must fill is the adoption defect, not the feature — so the trail always says something."""
    ix = _store()
    m = _remember(ix, "a claim with no stated reason for its amendment")
    ix.slash([m], scope="memory")
    assert _amendments(ix)[0]["amend_reason"] == "unstated"
    assert ix.verify_writes() == (True, [])


def test_forging_the_reason_breaks_the_chain():
    """The load-bearing property. Outside the hash, anyone with file access could attach a
    flattering reason to a laundering amendment."""
    ix = _store()
    m = _remember(ix, "a claim someone would like to relabel quietly")
    ix.slash([m], scope="memory", reason="corrected: measurement error in the fixture")
    _amendments(ix)[0]["amend_reason"] = "routine cleanup"
    ok, problems = ix.verify_writes()
    assert ok is False and any("tampered" in p for p in problems), problems


def test_stripping_the_reason_breaks_the_chain():
    """The other direction, and the one a naive 'optional field' design gets wrong: an inconvenient
    reason must not be removable either."""
    ix = _store()
    m = _remember(ix, "a claim whose amendment reason is inconvenient")
    ix.slash([m], scope="memory", reason="rewritten because the earlier verdict was wrong")
    del _amendments(ix)[0]["amend_reason"]
    ok, problems = ix.verify_writes()
    assert ok is False and any("tampered" in p for p in problems), problems


def test_slash_and_restore_carry_their_own_distinct_reasons():
    """The pair is where a correction and a quiet rewrite are hardest to tell apart, so each leg
    states its own case rather than sharing one."""
    ix = _store()
    m = _remember(ix, "a claim that is slashed and then exonerated")
    ix.slash([m], scope="memory", reason="corrected: wrong fixture")
    ix.restore([m], scope="memory", reason="exonerated: the fixture was fine, the reader was not")
    reasons = [r["amend_reason"] for r in _amendments(ix)]
    assert reasons == ["corrected: wrong fixture",
                       "exonerated: the fixture was fine, the reader was not"], reasons
    assert ix.verify_writes() == (True, [])


def test_every_verifier_agrees_after_an_amendment_with_a_reason():
    """`_chain_core`'s own docstring records what happens when a preimage field reaches some
    verifiers and not others: verify_writes said clean, verify_bundle said the chain breaks, and
    anchor() committed to a TRUNCATED chain (n_writes=1 with two receipts present). So the check is
    that the count anchor sees equals the receipts that exist."""
    ix = _store()
    m = _remember(ix, "a claim under audit from three directions")
    ix.slash([m], scope="memory", reason="corrected: wrong fixture")
    ix.restore([m], scope="memory", reason="exonerated on review")
    assert ix.verify_writes() == (True, [])
    a = ix.anchor() or {}
    assert int(a.get("n_writes", 0)) == len(ix._receipts) == 3, (
        f"anchor sees {a.get('n_writes')} of {len(ix._receipts)} receipts — a preimage field reached "
        f"the emitter but not _recompute_tip")


def test_a_reason_is_bounded_and_normalised():
    """Free text from a caller enters a hashed structure, so it is trimmed and capped rather than
    taken as given — an unbounded field in a preimage is a way to make receipts arbitrarily large."""
    ix = _store()
    m = _remember(ix, "a claim with an enormous reason attached")
    ix.slash([m], scope="memory", reason="  " + ("x" * 500) + "  ")
    r = _amendments(ix)[0]["amend_reason"]
    assert len(r) == 200 and r == "x" * 200, len(r)


def test_a_non_amending_receipt_carries_no_reason():
    """The other half. A reason on an ordinary write would be noise, and would change every
    receipt's preimage for records that were never amended."""
    ix = _store()
    _remember(ix, "an ordinary write that amends nothing")
    assert all("amend_reason" not in r for r in ix._receipts)
    assert ix.verify_writes() == (True, [])
