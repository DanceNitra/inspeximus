"""`audit_the_audits` asks the question none of the other 24 surfaces asks about themselves.

Every `verify_*` and `check_*` here answers something about your data. None answers: *would this
have noticed if the thing it guards against had happened?* A check that cannot fail on your store is
not protecting you, it is producing a reassuring string -- and we have shipped that. `slash` resolved
its default scope on a field no writer ever set: 0.000% coverage across 261,673 records, returning ok
on every call, for months.

The tests below are mostly must-fail controls, because a feature that detects blind checks is exactly
the feature nobody would notice going blind.
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile

import pytest

from inspeximus import Inspeximus


def _store(with_source=True, **kw):
    d = tempfile.mkdtemp()
    ix = Inspeximus(path=os.path.join(d, "s.json"), embed=False, receipts=True, **kw)
    src = None
    if with_source:
        src = os.path.join(d, "policy.txt")
        with open(src, "wb") as fh:
            fh.write(b"two approvers")
        ix.remember("deployment needs two approvers", key="policy", object="two",
                    source={"doc": src,
                            "observed_sha256": hashlib.sha256(b"two approvers").hexdigest()})
    ix.remember("beta", key="b", object="b")
    ix.flush()
    return ix


# ───────────────────────────────────────────────── it works at all
def test_a_healthy_store_reports_its_checks_as_working():
    r = _store().audit_the_audits()
    assert r["noticed"] >= 4, r["probes"]
    assert not r["control_failed"], "a control that fails means the probe measured its own setup"
    assert {p["surface"] for p in r["probes"]} <= set(dir(Inspeximus))


def test_every_probe_names_what_it_corrupts_and_what_should_catch_it():
    """A probe with no stated target is the uncited expectation this file exists to prevent."""
    for p in _store().audit_the_audits()["probes"]:
        assert p["surface"] and p["probe"]
        if p["outcome"] != "CONTROL_FAILED":
            assert p["catches"], f"{p['probe']} does not say what it corrupts"


# ───────────────────────────────────────────────── the three verdicts, each on purpose
def test_a_blinded_surface_is_reported_as_MISSED():
    """MUST-FAIL CONTROL ON THE FEATURE. Blind one surface and the audit has to say so; if it does
    not, `audit_the_audits` has itself become a check that cannot fail, which is the joke that
    writes itself and the reason this test is here."""
    ix = _store()

    class Blinded(type(ix)):
        def verify_writes(self, *a, **k):
            return True, []                      # always clean, whatever happened

    blind = Blinded(path=str(ix.path), embed=False, receipts=True)
    out = blind.audit_the_audits()
    missed = {m["probe"] for m in out["missed"]}
    assert "value_tampered_after_write" in missed, out["probes"]
    assert "key_tampered_after_write" in missed


def test_a_surface_already_unhappy_is_NOT_scored():
    """CONTROL_FAILED, not MISSED and not NOTICED. A surface complaining before the corruption tells
    us nothing about the corruption -- this is the distinction that kept the first live run honest,
    where our own decision store had an empty receipt chain and three probes correctly refused to
    score."""
    ix = _store()

    class AlreadyBroken(type(ix)):
        def verify_writes(self, *a, **k):
            return False, ["unhappy before anyone touched anything"]

    out = AlreadyBroken(path=str(ix.path), embed=False, receipts=True).audit_the_audits()
    cf = {c["probe"] for c in out["control_failed"]}
    assert "value_tampered_after_write" in cf
    assert not any(m["probe"] == "value_tampered_after_write" for m in out["missed"]), \
        "an already-failing surface must not be scored as a miss"


def test_a_clean_boolean_over_an_unhappy_report_is_its_own_verdict():
    """SUMMARY_HIDES_DETAIL. `check_sources` on a store whose sources were all stripped returns
    ok=True -- deliberately, since a decisions-only store has nothing bindable -- while the same
    report carries "so this verified NOTHING". Monitoring reads booleans, so that gap gets a name
    instead of being scored as a catch or a miss."""
    out = _store().audit_the_audits()
    hides = {h["probe"]: h for h in out["summary_hides_detail"]}
    assert "every_source_stripped" in hides, out["probes"]
    assert "verified NOTHING" in hides["every_source_stripped"]["the_boolean_said_clean_but"]


# ───────────────────────────────────────────────── it must not touch the caller's data
def test_the_live_store_is_never_written():
    """The corruptions are real corruptions. Running them anywhere near the caller's file is the
    failure mode with the worst blast radius, and we have watched two of our own tools fight over
    one file on the same day this shipped."""
    ix = _store()
    path = str(ix.path)
    before = open(path, "rb").read()
    before_side = {p: open(p, "rb").read() for p in
                   (path + ".receipts.json",) if os.path.exists(p)}
    ix.audit_the_audits()
    assert open(path, "rb").read() == before, "the audit modified the live store"
    for p, b in before_side.items():
        assert open(p, "rb").read() == b, f"the audit modified {p}"


def test_an_empty_store_reports_nothing_rather_than_a_clean_bill():
    """Zero probes is not zero problems. The first defect in this feature's own docstring is a probe
    that scored 14/14 over a store that had received nothing."""
    ix = Inspeximus(path=os.path.join(tempfile.mkdtemp(), "s.json"), embed=False, receipts=True)
    r = ix.audit_the_audits()
    assert r["probes"] == [] and r["noticed"] == 0
    assert any("EMPTY" in x for x in r["limits"])


def test_it_states_how_much_of_the_surface_it_does_not_cover():
    """Six probes over three surfaces, against 24. Reporting the covered ones without the denominator
    is the shape of every overclaim this repository has had to correct -- and it happened FROM this
    return value: a handoff read the probe count as a surface count and wrote "covers 5 of 24
    surfaces" when the tool's own limits string said 2. Hence `surfaces` below, where the numbers are
    named individually and cannot be mistaken for one another."""
    r = _store().audit_the_audits()
    assert r["surfaces_available"] >= 20
    assert any("UNTESTED" in x for x in r["limits"])
    assert len(r["surfaces_covered"]) < r["surfaces_available"]


def test_the_coverage_numbers_cannot_disagree_with_each_other():
    """The previous shape returned three counts that all read like coverage -- a NOTICED-only list, a
    probed count buried in a prose limit, and the total -- and something downstream duly conflated
    two of them. Probed and unprobed must partition the available surfaces exactly."""
    s = _store().audit_the_audits()["surfaces"]
    assert s["probed"] + len(s["unprobed"]) == s["available"]
    assert s["probed"] >= 3, "verify_writes, check_sources and index_coherence all have probes"
    for name in s["demonstrated_on_your_store"]:
        assert name not in s["unprobed"]


# ───────────────────────────────────── unreachable-here is not unhealthy, and not a clean bill
def test_a_store_that_cannot_reach_a_surface_is_not_reported_as_unhappy():
    """Measured on our own 451-record decision store, which has receipts off and nothing re-checkable:
    all THREE of its CONTROL_FAILEDs were this. "Already reported a problem" reads as *your data is
    unhappy* when the truth was *this question cannot be asked here* -- so the precondition is asked
    first, and the answer is moved to a fixture where it can be answered."""
    ix = Inspeximus(path=os.path.join(tempfile.mkdtemp(), "s.json"), embed=False, receipts=False)
    ix.remember("a", key="a", object="a")
    ix.remember("b", key="b", object="b")
    ix.flush()
    out = ix.audit_the_audits()
    unreach = {p["probe"]: p for p in out["not_reachable_here"]}
    assert "value_tampered_after_write" in unreach, out["probes"]
    assert unreach["value_tampered_after_write"]["on_a_fixture"] == "NOTICED", \
        "the surface works; only this store cannot demonstrate it"
    assert not out["control_failed"], \
        "a store that merely lacks receipts has no health finding to report"
    assert "verify_writes" in out["surfaces"]["working_but_unreachable_here"]
    assert "verify_writes" not in out["surfaces"]["demonstrated_on_your_store"], \
        "a fixture pass must never be reported as demonstrated on the caller's data"


def test_a_surface_blind_even_on_a_fixture_is_the_loudest_verdict():
    """MUST-FAIL CONTROL. The whole point of the fixture tier is that it can convict the LIBRARY. If a
    blinded surface can hide behind "your store cannot reach this", the tier has turned every
    unreachable surface into an excuse -- which is this feature's own failure mode, one level up."""
    ix = Inspeximus(path=os.path.join(tempfile.mkdtemp(), "s.json"), embed=False, receipts=False)
    ix.remember("a", key="a", object="a")
    ix.flush()

    class Blinded(type(ix)):
        def verify_writes(self, *a, **k):
            return True, []                      # clean on the fixture too

    out = Blinded(path=str(ix.path), embed=False, receipts=False).audit_the_audits()
    blind = {b["probe"] for b in out["blind_even_on_a_fixture"]}
    assert "value_tampered_after_write" in blind, out["probes"]
    assert "verify_writes" in out["surfaces"]["blind_even_on_a_fixture"]
    assert "LIBRARY" in [p for p in out["probes"]
                         if p["probe"] == "value_tampered_after_write"][0]["read_this_as"]


def test_a_real_health_problem_is_not_laundered_into_a_coverage_gap():
    """CONTROL ON THE PRECONDITION ITSELF. A store whose sources have genuinely DRIFTED must still
    come back CONTROL_FAILED -- if a precondition can absorb a real finding and re-emit it as "this
    question cannot be asked here", it is a check that cannot fail wearing a new name. The first
    version of `_needs_bindable_source` asked whether anything was *bindable* (107 records on our
    store) rather than whether the surface had *checked* anything (0), so it passed the precondition
    and CONTROL_FAILED anyway: a criterion narrower than its property."""
    d = tempfile.mkdtemp()
    ix = Inspeximus(path=os.path.join(d, "s.json"), embed=False, receipts=True)
    src = os.path.join(d, "doc.txt")
    with open(src, "wb") as fh:
        fh.write(b"original bytes")
    ix.remember("bound", key="bound", object="x",
                source={"doc": src,
                        "observed_sha256": hashlib.sha256(b"original bytes").hexdigest()})
    ix.remember("beta", key="b", object="b")
    ix.flush()
    with open(src, "wb") as fh:                  # drifted BEFORE the audit runs
        fh.write(b"DRIFTED ALREADY")
    out = ix.audit_the_audits()
    row = [p for p in out["probes"] if p["probe"] == "every_source_stripped"][0]
    assert row["outcome"] == "CONTROL_FAILED", row
    assert row["probe"] not in {p["probe"] for p in out["not_reachable_here"]}


# ───────────────────────────────────── the harness must not be the thing that blinds a surface
def test_the_coherence_probe_is_not_defeated_by_the_harness_own_embedder():
    """`_open_copy` deliberately does NOT inherit the caller's embedder -- it may be a network call,
    and one per record is a stall. The consequence: with no embedder there is no index to fall behind,
    so a naive probe of `index_coherence` reports the SURFACE as blind when the blindness belongs to
    the harness. That is why this probe declares `_needs_embedder` and runs on a fixture. A reader
    written for this surface sat unwired in the file for a release; wiring it without this would have
    manufactured a library defect."""
    out = _store().audit_the_audits()
    row = [p for p in out["probes"] if p["surface"] == "index_coherence"]
    assert row, "index_coherence has no probe at all"
    row = row[0]
    assert row["outcome"] != "MISSED", \
        "the harness disabled the embedder and then blamed the surface for it"
    assert row["tier"] == "fixture" and row["on_a_fixture"] == "NOTICED", row


def test_embed_false_and_embed_none_mean_the_same_thing():
    """THE CLASS, not the instance. `embed=None` is the parameter default but `embed=False` is what
    this suite and the audit's copy-opener pass, and the class read the two differently: six sites
    truthily, four by identity. `index_coherence` therefore reported `embedder_configured: true` and
    `coherent: false` on a store deliberately opened WITHOUT an embedder -- an index convicted of
    lagging when none exists -- the load-path realign guard called `False(text)` and wrote `vec=None`
    over every persisted vector, and `reembed()` said `failed: N` instead of naming the real cause."""
    ix = Inspeximus(path=os.path.join(tempfile.mkdtemp(), "s.json"), embed=False)
    ix.remember("beta", key="b", object="b")
    ix.flush()
    assert ix.embed is None, "embed=False must normalise to the one sentinel for 'no embedder'"
    c = ix.index_coherence()
    assert c["embedder_configured"] is False
    assert c["coherent"] is True and c["missing_vecs"] == 0, \
        "a store with no embedder has no index that could be behind it"
    assert ix.reembed().get("error") == "no embedder configured"
