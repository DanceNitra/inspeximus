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
    """Five probes against 24 surfaces. Reporting the covered ones without the denominator is the
    shape of every overclaim this repository has had to correct."""
    r = _store().audit_the_audits()
    assert r["surfaces_available"] >= 20
    assert any("UNTESTED" in x for x in r["limits"])
    assert len(r["surfaces_covered"]) < r["surfaces_available"]
