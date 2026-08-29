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


# ─────────────────────────────── verify_attribution: `ok` does not fold in what `missing` reports
def _receipted(n=5):
    ix = Inspeximus(path=os.path.join(tempfile.mkdtemp(), "s.json"), receipts=True)
    for i in range(n):
        ix.remember("record number %d" % i, key="k%d" % i, object="o")
    ix.flush()
    return ix


def test_deleting_a_receipted_record_leaves_verify_attribution_ok_true():
    """The finding this probe exists to carry, asserted directly rather than through the harness.

    `verify_attribution` documents `missing` as "ids in the receipt chain no longer in the store".
    Delete one and `missing` fills correctly -- while `ok` stays True. That is not a lie and not a
    catch: a monitor polling the boolean sees nothing. Measured 5/5 trials before this was wired.
    If a later release folds `missing` into `ok`, this test fails and the probe below must be
    rescored to NOTICED, which is a fix rather than a regression -- update both together.
    """
    ix = _receipted()
    before = ix.verify_attribution()
    assert before["ok"] is True and not before["missing"], before

    rows = json.loads(open(ix.path, encoding="utf-8").read())
    items = rows["items"] if isinstance(rows, dict) and "items" in rows else rows
    keep = items[1:]
    if isinstance(rows, dict) and "items" in rows:
        rows["items"] = keep
    else:
        rows = keep
    with open(ix.path, "w", encoding="utf-8") as fh:
        json.dump(rows, fh, default=str)

    after = Inspeximus(path=ix.path, receipts=True).verify_attribution()
    assert len(after["missing"]) == 1, after
    assert after["ok"] is True, "if ok now folds in `missing`, rescore the probe to NOTICED"


def test_the_attribution_probe_reports_summary_hides_detail_not_missed():
    """Scoring this MISSED would be the reader trusting a summary over the contents it summarises --
    the exact defect this method exists to find. The reader therefore returns the boolean a monitor
    would read and names the gap as a `problem`."""
    out = _receipted().audit_the_audits()
    row = [p for p in out["probes"] if p["surface"] == "verify_attribution"]
    assert row, "verify_attribution has no probe at all"
    row = row[0]
    assert row["outcome"] == "SUMMARY_HIDES_DETAIL", row
    assert "missing" in (row.get("the_boolean_said_clean_but") or ""), row


def test_the_attribution_probe_refuses_a_corruption_that_cannot_land():
    """One record, so `rows[1:]` is empty and nothing is deleted. A probe that scored this as a
    result would be reporting its own setup."""
    ix = Inspeximus(path=os.path.join(tempfile.mkdtemp(), "s.json"), receipts=True)
    ix.remember("only one", key="k", object="o")
    ix.flush()
    row = [p for p in ix.audit_the_audits()["probes"]
           if p["surface"] == "verify_attribution"][0]
    assert row["outcome"] == "CONTROL_FAILED", row


def test_the_attribution_probe_is_unaskable_without_receipts_and_says_so():
    """No receipt chain means no committed attribution to delete out from under. That is a gap in
    what the store can demonstrate, not a defect in the surface -- and the fixture still answers."""
    ix = Inspeximus(path=os.path.join(tempfile.mkdtemp(), "s.json"))
    for i in range(4):
        ix.remember("r%d" % i, key="k%d" % i, object="o")
    ix.flush()
    row = [p for p in ix.audit_the_audits()["probes"]
           if p["surface"] == "verify_attribution"][0]
    assert row["outcome"] == "NOT_REACHABLE_HERE", row
    assert row["on_a_fixture"] == "SUMMARY_HIDES_DETAIL", row


# ───────────────────────────── PURE surfaces: the four that never read the store
import inspeximus.core as _core


PURE_SURFACES = [
    "verify_inclusion",
    "verify_cosigned_anchor",
    "detect_split_view",
    "check_self_narration",
]


def _pure_rows(ix):
    return {p["surface"]: p["outcome"] for p in ix.audit_the_audits()["probes"]
            if p.get("mode") == "pure function"}


def _receipted(n=5):
    ix = Inspeximus(path=os.path.join(tempfile.mkdtemp(), "s.json"), receipts=True)
    for i in range(n):
        ix.remember("record %d" % i, key="k%d" % i, object="probe")
    ix.flush()
    return ix


def test_every_pure_surface_has_a_probe_and_catches_its_corruption():
    """Four of the 24 surfaces take their subject as an argument, so corrupting a copy of the
    caller's store cannot reach them. Before PURE mode they were unprobed by construction and the
    coverage number said so forever."""
    got = _pure_rows(_receipted())
    assert sorted(got) == sorted(PURE_SURFACES), got
    assert set(got.values()) == {"NOTICED"}, got


def test_a_pure_surface_that_always_reports_clean_is_scored_MISSED():
    """The loudest verdict this method can reach, and the one that must not be reachable by
    accident. A blind surface is a defect in the library, not in anyone's data."""
    orig = {n: getattr(_core.Inspeximus, n) for n in PURE_SURFACES}
    try:
        _core.Inspeximus.verify_inclusion = staticmethod(lambda *a, **k: True)
        _core.Inspeximus.verify_cosigned_anchor = staticmethod(lambda *a, **k: {"ok": True})
        _core.Inspeximus.detect_split_view = staticmethod(lambda *a, **k: {"fork": False})
        _core.Inspeximus.check_self_narration = staticmethod(lambda *a, **k: {"self_narration": False})
        got = _pure_rows(_receipted())
        assert set(got.values()) == {"MISSED"}, got
    finally:
        for n, f in orig.items():
            setattr(_core.Inspeximus, n, f)


def test_a_pure_surface_that_rejects_the_valid_input_is_scored_CONTROL_FAILED():
    """The other direction. A surface that cries wolf at everything would score NOTICED on the
    corrupted input while proving nothing, so the valid input is checked first and its rejection
    disqualifies the run."""
    orig = {n: getattr(_core.Inspeximus, n) for n in PURE_SURFACES}
    try:
        _core.Inspeximus.verify_inclusion = staticmethod(lambda *a, **k: False)
        _core.Inspeximus.verify_cosigned_anchor = staticmethod(lambda *a, **k: {"ok": False})
        _core.Inspeximus.detect_split_view = staticmethod(lambda *a, **k: {"fork": True})
        _core.Inspeximus.check_self_narration = staticmethod(lambda *a, **k: {"self_narration": True})
        got = _pure_rows(_receipted())
        assert set(got.values()) == {"CONTROL_FAILED"}, got
    finally:
        for n, f in orig.items():
            setattr(_core.Inspeximus, n, f)


def test_the_split_view_fixture_carries_its_own_negative_case():
    """The fork builder hands the detector the SAME anchor twice as its valid input. Without that,
    a detector that always cries fork would pass the probe."""
    out = _receipted().audit_the_audits()
    row = [p for p in out["probes"] if p["surface"] == "detect_split_view"][0]
    assert row["clean_on_valid_input"] is True, row
    assert row["clean_on_corrupted_input"] is False, row


def test_pure_probes_count_toward_coverage():
    """A probe that does not move the coverage number is invisible to the reader who asks how much
    of the class is actually tested."""
    s = _receipted().audit_the_audits()["surfaces"]
    assert s["probed"] >= 8, s
    for name in PURE_SURFACES:
        assert name not in s["unprobed"], name


# ──────────────────── LEDGER surfaces: inventories, where the question is whether the number moves
LEDGER_SURFACES = [
    "memory_report",
    "supersession_report",
    "pii_report",
    "erasure_report",
    "governance_report",
    "influence_gate_report",
    "irreversible_budget_report",
]


def _ledger_rows(ix):
    return {p["surface"]: p for p in ix.audit_the_audits()["probes"]
            if p.get("mode") == "ledger"}


def test_every_inventory_counter_tracks_the_store():
    """A report has no pass/fail, so "would it notice a corruption" is the wrong question and would
    have scored a working report as blind. The question that fits is whether the number moves when
    the store does."""
    got = _ledger_rows(_receipted())
    assert sorted(got) == sorted(LEDGER_SURFACES), sorted(got)
    for name, row in got.items():
        assert row["outcome"] == "NOTICED", (name, row)
        assert row["counter_after"] - row["counter_before"] == row["expected_move"], row


def test_a_frozen_counter_is_scored_MISSED():
    """The defect this mode exists to find: the store changes and the number does not follow."""
    orig = _core.Inspeximus.memory_report
    try:
        _core.Inspeximus.memory_report = (
            lambda self, dup_threshold=0.9: {"total": 0, "active": 0, "superseded": 0})
        assert _ledger_rows(_receipted())["memory_report"]["outcome"] == "MISSED"
    finally:
        _core.Inspeximus.memory_report = orig


def test_a_change_that_did_not_land_is_CONTROL_FAILED_not_MISSED():
    """Found by getting it wrong by hand. Writing the same key twice with the SAME object supersedes
    nothing, because _supersede_by_key branches on object and asserts_change. Reading only the
    report, that looks exactly like a broken counter. So the probe reads the store's own count too,
    and a change that did not land disqualifies the fixture instead of convicting the report."""
    orig = _core.Inspeximus._supersede_by_key
    try:
        _core.Inspeximus._supersede_by_key = lambda self, rec, reaffirm=False: []
        row = _ledger_rows(_receipted())["supersession_report"]
        assert row["outcome"] == "CONTROL_FAILED", row
        assert "did not land" in row["why"], row
    finally:
        _core.Inspeximus._supersede_by_key = orig


def test_coverage_is_15_of_24_and_names_what_is_left():
    """The number a reader asks for. Untested is not the same as working, so the remaining nine are
    named rather than rounded away."""
    s = _receipted().audit_the_audits()["surfaces"]
    assert s["available"] == 24, s
    assert s["probed"] >= 15, s
    for name in LEDGER_SURFACES + PURE_SURFACES:
        assert name not in s["unprobed"], name


# ───────────── ARGUMENT surfaces: they read the store, but the subject arrives as an argument
ARGUMENT_SURFACES = [
    "verify_witness",
    "verify_consistency",
    "verify_claim",
    "check_conflict",
    "erasure_certificate",
]


def _argument_rows(ix):
    return {p["surface"]: p for p in ix.audit_the_audits()["probes"]
            if p.get("mode") == "argument"}


def test_every_argument_surface_accepts_the_honest_case_and_flags_the_corrupt_one():
    """Three of these capture their subject from the clean store first, which is the auditor's own
    workflow: witness a head out of band, come back later, check what you were shown against what
    is there now."""
    got = _argument_rows(_receipted())
    # a SUBSET check, not equality: this test names the first five and the catalogue has grown
    # past them. Asserting equality here made a later batch fail a test about an earlier one.
    assert set(ARGUMENT_SURFACES) <= set(got), sorted(got)
    got = {k: v for k, v in got.items() if k in ARGUMENT_SURFACES}
    for name, row in got.items():
        assert row["outcome"] == "NOTICED", (name, row)
        assert row["clean_on_honest_case"] is True, row
        assert row["clean_on_corrupt_case"] is False, row


def test_an_argument_surface_that_always_passes_is_scored_MISSED():
    orig = _core.Inspeximus.verify_witness
    try:
        _core.Inspeximus.verify_witness = lambda self, w, resolver=None: {"valid": True}
        assert _argument_rows(_receipted())["verify_witness"]["outcome"] == "MISSED"
    finally:
        _core.Inspeximus.verify_witness = orig


def test_an_argument_surface_that_always_flags_is_scored_CONTROL_FAILED():
    """A surface that cries wolf at everything would score NOTICED on the corrupt case while
    proving nothing, so the honest case is checked first."""
    orig = _core.Inspeximus.check_conflict
    try:
        _core.Inspeximus.check_conflict = lambda self, text, **k: [{"kind": "always"}]
        assert _argument_rows(_receipted())["check_conflict"]["outcome"] == "CONTROL_FAILED"
    finally:
        _core.Inspeximus.check_conflict = orig


def test_an_append_does_not_read_as_a_rollback():
    """verify_consistency proves the log is append-ONLY, not immutable. Growing it must stay clean,
    or the probe would convict the surface for doing its job."""
    ix = _receipted()
    a = ix.anchor()
    ix.remember("one more record", key="extra", object="x")
    ix.flush()
    ok, problems = ix.verify_consistency(a)
    assert ok is True, problems


def test_every_surface_has_a_probe():
    """24 of 24. The number a reader asks for, and the one that was 4 the morning this started.
    A surface added later lands in `unprobed` and fails this test, which is the point: a class that
    grows a verification surface without a probe for it goes back to claiming what it has not
    shown."""
    s = _receipted().audit_the_audits()["surfaces"]
    assert s["available"] == 24, s
    assert s["unprobed"] == [], s["unprobed"]
    assert s["probed"] == 24, s


ARGUMENT_SURFACES_FULL = ARGUMENT_SURFACES + [
    "verify_attestations",
    "selection_integrity",
    "erasure_audit",
    "convergence_report",
]


def test_the_last_four_surfaces_are_probed_and_pass():
    """Each needed a fixture nobody had built: a signed attestation, a trust root, an erasure whose
    value survives elsewhere, and a ratification by a second identity."""
    got = _argument_rows(_receipted())
    assert sorted(got) == sorted(ARGUMENT_SURFACES_FULL), sorted(got)
    for name, row in got.items():
        assert row["outcome"] == "NOTICED", (name, row)


@pytest.mark.parametrize("surface,fake,want", [
    ("verify_attestations",
     lambda self, expected_key=None: (True, []), "MISSED"),
    ("selection_integrity",
     lambda self, query, k=6, pool=50: {"stable": True}, "MISSED"),
    ("erasure_audit",
     lambda self, subject=None, values=None: {"residue": [], "advisory": []}, "MISSED"),
    ("convergence_report",
     lambda self, target, _by_id=None: {"adjudicated": True}, "MISSED"),
    ("convergence_report",
     lambda self, target, _by_id=None: {"adjudicated": False}, "CONTROL_FAILED"),
    ("erasure_audit",
     lambda self, subject=None, values=None: {"advisory": [{"kind": "always"}]}, "CONTROL_FAILED"),
])
def test_the_last_four_probes_can_fail_in_both_directions(surface, fake, want):
    """A surface that always passes must score MISSED. One that always flags must score
    CONTROL_FAILED, because its reaction to the corrupt case would otherwise prove nothing. Without
    both, 24 green rows are 24 rows nobody has tested."""
    orig = getattr(_core.Inspeximus, surface)
    try:
        setattr(_core.Inspeximus, surface, fake)
        assert _argument_rows(_receipted())[surface]["outcome"] == want
    finally:
        setattr(_core.Inspeximus, surface, orig)


def test_self_ratification_cannot_lift_a_claim():
    """The property the convergence probe guards. A claim ratified only by its own author must not
    read as adjudicated, or the corroboration ratchet is self-served."""
    ix = _receipted()
    rid = ix.remember("revenue is 800M", key="rev", object="800M", source={"doc": "auditor-c"})
    ix.flush()
    out = ix.ratify(rid, kind="audit", by_key="auditor-c")
    assert out["ok"] is False, out
    assert ix.convergence_report(rid).get("adjudicated") is not True


def test_a_fixture_verdict_is_not_reported_as_your_store_demonstrating_anything():
    """The field says "on your store", so only the store tier may populate it.

    Adding three modes without changing this summary made it claim 20 surfaces demonstrated on a
    store that had demonstrated 3. The pure, ledger and argument probes build their own fixtures and
    never read the caller's records, so their NOTICED is a fact about the library. Both numbers are
    worth having; merging them turns the honest one into the flattering one."""
    ix = Inspeximus(path=os.path.join(tempfile.mkdtemp(), "s.json"))   # no receipts, no embedder
    for i in range(3):
        ix.remember("record %d" % i, key="k%d" % i, object="o%d" % i)
    ix.flush()
    out = ix.audit_the_audits()
    s = out["surfaces"]

    tiers = {r["surface"]: r.get("tier") for r in out["probes"] if r["outcome"] == "NOTICED"}
    for name in s["demonstrated_on_your_store"]:
        assert tiers.get(name) == "your store", (name, tiers.get(name))
    for name in s["proved_on_a_fixture"]:
        assert tiers.get(name) != "your store", (name, tiers.get(name))
    assert not (set(s["demonstrated_on_your_store"]) & set(s["proved_on_a_fixture"]))

    # THE INVARIANT, restated after the tier stopped being fixed per mode. Every mode now builds on
    # a copy of the caller's store where it can, so "the fixture list cannot be empty" no longer
    # holds -- it described a limitation rather than a safety property, and a store that CAN host
    # every probe correctly reports an empty fixture list.
    #
    # What must still hold is the thing the old assertion was protecting: a probe that had to switch
    # on a setting this store does not run may not be reported as demonstrated on it. This fixture
    # keeps no receipts and has no embedder, so at least one probe must land outside the demonstrated
    # list, and none of the ones that did may appear inside it.
    outside = set(s["proved_on_a_fixture"]) | set(s["working_but_unreachable_here"])         | set(s["unanswerable_here"])
    assert outside, (
        "a store with no receipts and no embedder cannot demonstrate every surface, so something "
        "must sit outside the demonstrated list")
    assert not (outside & set(s["demonstrated_on_your_store"]))


def test_every_surface_lands_in_exactly_one_bucket():
    """A surface that falls out of all of them reports 24 as 22 plus one, with one just missing.

    That happened: the fixture bucket selected on the MODE names after the tiers became measured, so
    `pii_report` matched nothing and vanished from the summary while still being probed. A total
    that does not add up is the same defect this method exists to find, in its own output.
    """
    ix = _receipted()
    out = ix.audit_the_audits()
    s = out["surfaces"]
    buckets = ("demonstrated_on_your_store", "proved_on_a_fixture",
               "working_but_unreachable_here", "unanswerable_here")
    seen = set()
    for b in buckets:
        seen |= set(s[b])
    missing = {r["surface"] for r in out["probes"]} - seen
    assert not missing, "probed but reported in no bucket: %s" % sorted(missing)
    assert len(seen) == s["available"], (
        "%d surfaces accounted for, %d available" % (len(seen), s["available"]))
