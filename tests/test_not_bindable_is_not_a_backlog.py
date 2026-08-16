""""We failed to fingerprint this" and "this can never be fingerprinted" are different facts.

@Stratogain's argument on anthropics/claude-code#34556, conceded there and fixed here. One
`UNCHECKABLE` bucket and an all-records denominator gave a reader no way to separate a document we
never fingerprinted -- backfillable, worth chasing -- from a source with no addressable state at
all, which never shrinks. Inside one number, the second makes coverage unreachable forever, and a
team either chases it or quietly redefines the denominator to look good.

MEASURED ON OUR OWN DOGFOOD STORE, which is a sharper case than the thread's fixture: 11,165
records, 11,108 (99.5%) with no anchor at all. It is a store of DECISIONS. "We chose X because Y"
has no source document, and not having one is its correct state. The old number read 0% coverage
over 11,165 records and made a healthy store look permanently broken; the honest one is 0% over 57.

THE REFINEMENT IS OURS AND IT IS WHY THIS IS NOT A PER-RECORD LABEL. Bindability is a property of
the WINDOW, not of the source kind: a URL or a ticket is bindable for VERIFY->USE (pin the digest
now, re-fetch at use) and not for OBSERVE->CAPTURE (the past state is gone). So the only thing
removed from the denominator is the narrow class that is unbindable in EVERY window -- no document
anchor at all.
"""
from __future__ import annotations

import hashlib
import os
import tempfile

import pytest

from inspeximus import Inspeximus


def _store(**kw):
    d = tempfile.mkdtemp()
    return d, Inspeximus(path=os.path.join(d, "s.json"), **kw)


def _file(d, name, body):
    p = os.path.join(d, name)
    open(p, "wb").write(body)
    return p


# ───────────────────────────────────────────────── the split
def test_no_anchor_at_all_is_not_bindable_not_unchecked():
    """The whole point. These two used to share a bucket."""
    d, ix = _store(receipts=True)
    ix.remember("a decision, no source", key="a", object="1")
    ix.remember("names a doc, never fingerprinted", key="b", object="2", source={"doc": "PROJ-1234"})
    ix.flush()
    c = ix.check_sources()["counts"]
    assert c["NOT_BINDABLE"] == 1, "a record with no anchor is still being called unchecked"
    assert c["UNCHECKABLE"] == 1, "a named-but-unfingerprinted doc IS the backfillable backlog"


def test_the_denominator_excludes_what_can_never_be_bound():
    d, ix = _store(receipts=True)
    body = b"two approvers"
    ix.remember("observed", key="a", object="1",
                source={"doc": _file(d, "p.txt", body),
                        "observed_sha256": hashlib.sha256(body).hexdigest()})
    ix.remember("a decision", key="b", object="2")
    ix.remember("another decision", key="c", object="3")
    ix.flush()
    cov = ix.check_sources()["coverage"]
    assert cov["bindable"] == 1 and cov["not_bindable"] == 2
    assert cov["refetch_verification_coverage"] == 1.0, \
        "one of one bindable source is checked; the two decisions are not a shortfall"


def test_the_counts_travel_beside_the_ratio():
    """0.0 over 57 bindable records and 0.0 over 11,165 call for completely different reactions, and
    a bare ratio cannot tell them apart."""
    d, ix = _store(receipts=True)
    ix.remember("a decision", key="a", object="1")
    ix.remember("names a doc", key="b", object="2", source={"doc": "PROJ-1"})
    ix.flush()
    cov = ix.check_sources()["coverage"]
    assert cov["bindable"] == 1 and cov["not_bindable"] == 1


def test_a_store_of_pure_decisions_reports_undefined_not_zero():
    """A ratio with an empty denominator is undefined. Printing 0.0 there says "we measured, and it
    is bad" about a store where there was nothing to measure -- which is how our own dogfood store,
    99.5% decisions, read as a permanent failure."""
    d, ix = _store()
    for i in range(5):
        ix.remember(f"we chose X{i} because Y{i}", key=f"k{i}", object=str(i))
    ix.flush()
    out = ix.check_sources()
    assert out["counts"]["NOT_BINDABLE"] == 5
    assert out["coverage"]["refetch_verification_coverage"] is None
    assert out["coverage"]["declared_observation_binding_coverage"] is None
    assert out["ok"] is True, "a store with nothing to bind is not a broken store"


# ───────────────────────────────────────────────── window-relative, not source-relative
def test_a_url_is_bindable_and_a_resolver_proves_it():
    """OUR refinement, and the reason NOT_BINDABLE cannot be a per-record label keyed on anchor type.
    A URL has no recoverable PAST state -- so for OBSERVE->CAPTURE it is unbindable -- but for
    VERIFY->USE you pin the digest now and re-fetch at use, and the past is never needed. It stays
    in the denominator, because it is checkable."""
    d, ix = _store(receipts=True)
    body = b"two approvers"
    ix.remember("from a URL", key="a", object="1",
                source={"doc": "https://example.invalid/policy",
                        "observed_sha256": hashlib.sha256(body).hexdigest()})
    ix.flush()
    cov = ix.check_sources(resolver=lambda _doc: body)["coverage"]
    assert cov["not_bindable"] == 0 and cov["bindable"] == 1
    assert ix.check_sources(resolver=lambda _doc: body)["counts"]["FRESH"] == 1

    w = ix.witness(ix.recall("URL"), bind_sources=True)
    assert w["sources_bound"] == "1/1" and "sources_not_bindable" not in w
    assert ix.verify_witness(w, resolver=lambda _doc: body)["sources_match"] is True


# ───────────────────────────────────────────────── the witness half
def test_the_witness_separates_the_two_gaps_and_says_which_is_chaseable():
    d, ix = _store(receipts=True)
    body = b"two approvers"
    ix.remember("observed", key="a", object="1",
                source={"doc": _file(d, "p.txt", body),
                        "observed_sha256": hashlib.sha256(body).hexdigest()})
    ix.remember("names a doc, no fingerprint", key="b", object="2", source={"doc": "PROJ-1"})
    ix.remember("a decision", key="c", object="3")
    ix.flush()
    w = ix.witness(bind_sources=True)
    assert w["sources_bound"] == "1/2", "the decision must be out of the denominator"
    assert len(w["sources_not_bindable"]) == 1

    lim = ix.verify_witness(w)["limits"]
    assert any("backfillable kind" in x for x in lim), lim
    assert any("never be bound in any window" in x for x in lim), lim


# ───────────────────────────────────────────────── the controls
def test_control_an_empty_check_over_bindable_sources_is_still_not_a_pass():
    """THE RULE THAT MUST SURVIVE. `ok` was False whenever nothing was checkable, because "0 drifted"
    over 0 checked reads like a clean store. Relaxing it for the nothing-to-bind case must not
    relax it for a store that HAS documents and checked none of them."""
    d, ix = _store(receipts=True)
    ix.remember("names a doc, no fingerprint", key="a", object="1", source={"doc": "PROJ-1"})
    ix.flush()
    out = ix.check_sources()
    assert out["coverage"]["bindable"] == 1 and out["counts"]["UNCHECKABLE"] == 1
    assert out["ok"] is False, "a bindable store that checked nothing must not read clean"


def test_control_real_drift_still_fails():
    d, ix = _store(receipts=True)
    body = b"two approvers"
    p = _file(d, "p.txt", body)
    ix.remember("observed", key="a", object="1",
                source={"doc": p, "observed_sha256": hashlib.sha256(body).hexdigest()})
    ix.remember("a decision", key="b", object="2")
    ix.flush()
    assert ix.check_sources()["ok"] is True
    open(p, "wb").write(b"ONE approver")
    out = ix.check_sources()
    assert out["ok"] is False and out["counts"]["DRIFTED"] == 1


def test_control_an_orphan_is_still_an_orphan_not_suddenly_unbindable():
    """A source that WAS there and is gone is a different fact from one that never existed. If
    ORPHANED started collapsing into NOT_BINDABLE, a deleted source would stop being a problem."""
    d, ix = _store(receipts=True)
    body = b"two approvers"
    p = _file(d, "p.txt", body)
    ix.remember("observed", key="a", object="1",
                source={"doc": p, "observed_sha256": hashlib.sha256(body).hexdigest()})
    ix.flush()
    os.unlink(p)
    out = ix.check_sources()
    assert out["counts"]["ORPHANED"] == 1 and out["counts"]["NOT_BINDABLE"] == 0
    assert out["ok"] is False

def test_the_witness_tells_nothing_checked_from_nothing_checkable():
    """Two different empties, and collapsing them is the NOT_BINDABLE mistake one level down.

    An answer whose records NAME documents but carry no fingerprints is an unmet request: the caller
    asked to bind sources and got none -> False. An answer made of decisions never had anything to
    bind -> None, matching what check_sources now says about a pure-decision store. Reporting False
    there would make an honest answer read as a failed check forever."""
    d, ix = _store(receipts=True)
    ix.remember("a decision", key="a", object="1")
    ix.flush()
    w = ix.witness(bind_sources=True)
    out = ix.verify_witness(w)
    assert out["sources_match"] is None, "nothing was checkable; that is not a failed check"
    assert out["valid"] is True, "an undefined world-answer must not drag the store-answer down"
    assert any("undefined rather than False" in x for x in out["limits"]), out.get("limits")

    # ...and the OTHER empty still fails, which is the control that keeps the pair meaningful
    d2, ix2 = _store(receipts=True)
    ix2.remember("names a doc", key="b", object="2", source={"doc": "PROJ-1"})
    ix2.flush()
    w2 = ix2.witness(bind_sources=True)
    out2 = ix2.verify_witness(w2)
    assert out2["sources_match"] is False and out2["valid"] is False

def test_an_emptied_sources_map_cannot_impersonate_nothing_to_bind():
    """CAUGHT BY THE ADVERSARIAL ROUND, on the fix above, minutes after writing it.

    Once "nothing was checkable" became `None` instead of `False`, a courier who empties `sources`
    produced exactly that shape and bought a clean verdict. The two are distinguishable only because
    the witness declares its own denominator: an honest decisions-only witness says `0/0`, while the
    tampered one still claims `1/1` and presents zero pins. That contradiction is the tell, and
    without it this release would have traded one vacuous pass for another."""
    import copy
    d, ix = _store(receipts=True)
    body = b"two approvers"
    ix.remember("observed", key="a", object="1",
                source={"doc": _file(d, "p.txt", body),
                        "observed_sha256": hashlib.sha256(body).hexdigest()})
    ix.flush()
    w = ix.witness(ix.recall("observed"), bind_sources=True)
    assert w["sources_bound"] == "1/1"

    tampered = copy.deepcopy(w)
    tampered["sources"] = {}
    out = ix.verify_witness(tampered)
    assert out["sources_match"] is False and out["valid"] is False
    assert any("disagree" in x for x in out["limits"]), out.get("limits")

    # the honest empty is still honest -- the control that stops this becoming a blanket refusal
    d2, ix2 = _store(receipts=True)
    ix2.remember("a decision", key="b", object="2")
    ix2.flush()
    assert ix2.verify_witness(ix2.witness(bind_sources=True))["sources_match"] is None
