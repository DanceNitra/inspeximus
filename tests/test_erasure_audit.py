"""`erasure_audit()` — after an erasure, what does the store's lineage say survived, and how much did it see?

The tests that matter here are the ones that pin what this does NOT do:
  - capacity eviction and consolidation hard-delete for size reasons; they must land in `advisory`, never be
    reported as erasure residue, or `residue_found` is noise in any bounded store;
  - a store with no declared lineage must come back `unaudited`, never a pass — the checks walk declared
    `derived_from` edges, so zero edges means nothing was inspected;
  - a derivative whose writer never declared its parents is invisible to every structural check;
  - a surviving taint whose origin ALSO survives must not fire (the negative control that stops the check
    from degenerating into "has a taint field at all").
"""
import os, sys, subprocess, tempfile, json

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from inspeximus import Inspeximus


def _store(**kw):
    return Inspeximus(path=os.path.join(tempfile.mkdtemp(), "m.json"), receipts=True, **kw)


def _kinds(bucket):
    return {f["kind"] for f in bucket}


def test_a_full_cascade_erasure_leaves_no_residue():
    m = _store()
    parent = m.remember("alice bought a red bicycle", source={"doc": "user-42"})
    m.remember("summary: customer prefers red", derived=True, derived_from=[parent],
               source={"doc": "digest"})
    m.remember("unrelated note about billing", source={"doc": "runbook"})

    assert m.erasure_audit(subject="user-42")["verdict"] == "residue_found"   # before: subject present

    assert m.forget_subject("user-42", request_id="REQ-1")["erased"] == 2, \
        "taint must carry the erasure into the derived summary"

    after = m.erasure_audit(subject="user-42", values=["red bicycle"])
    assert after["residue"] == []
    # The cascade also erased the only record that DECLARED lineage, so the derivative question is no longer
    # inspectable and the verdict says so rather than flattering itself. Consistent with the base-rate case
    # below: whenever nothing declares lineage, a pass is reported as `unaudited`.
    assert after["verdict"] == "unaudited"
    assert after["coverage"]["with_declared_lineage"] == 0


def test_a_naive_delete_of_only_the_source_is_reported_as_residue():
    """What a text-match delete does — and the failure mode a summarizing store is prone to."""
    m = _store()
    parent = m.remember("alice bought a red bicycle", source={"doc": "user-42"})
    m.remember("summary: customer prefers red", derived=True, derived_from=[parent],
               source={"doc": "digest"})

    m.forget(ids=[parent], request_id="REQ-9", basis="art17")   # a deliberate erasure of just the record
    audit = m.erasure_audit(subject="user-42")

    assert audit["verdict"] == "residue_found"
    assert _kinds(audit["residue"]) == {"subject_still_attributable", "taint_without_origin",
                                        "dangling_lineage"}
    assert all(f["id"] and f["detail"] for f in audit["residue"])


def test_capacity_eviction_is_advisory_not_erasure_residue():
    """Eviction hard-deletes via forget() for SIZE reasons, with no erasure request. If it counted as
    residue, `residue_found` would fire constantly on any bounded store and mean nothing."""
    m = _store(capacity=4)
    parent = m.remember("parent note", source={"doc": "user-42"})
    m.remember("derived summary", derived=True, derived_from=[parent], source={"doc": "digest"},
               value=9.0)                                     # high value so it survives eviction
    for i in range(6):
        m.remember(f"filler {i}", source={"doc": "noise"})

    audit = m.erasure_audit()
    assert audit["verdict"] != "residue_found", "eviction must never read as erasure residue"
    assert not audit["residue"]
    assert audit["advisory"], "the eviction should still be REPORTED, just not counted"
    for f in audit["advisory"]:
        assert f["cause"], "an advisory finding must say why it is not being counted as residue"


def test_a_store_with_no_declared_lineage_is_unaudited_not_clean():
    """The base-rate trap: most writers never thread lineage. Reporting 'nothing found' when nothing was
    inspected is a false assurance on an erasure operation, so the verdict must say so."""
    m = _store()
    m.remember("alice bought a red bicycle", source={"doc": "user-42"})
    m.remember("digest note: alice likes red bicycle", source={"doc": "digest"})   # no derived_from

    m.forget_subject("user-42", request_id="REQ-1")
    audit = m.erasure_audit(subject="user-42")

    assert audit["verdict"] == "unaudited"
    assert audit["coverage"]["with_declared_lineage"] == 0
    assert audit["coverage"]["declared_ratio"] == 0.0
    assert any("read `coverage` before trusting a pass" in lim for lim in audit["limits"])


def test_an_undeclared_derivative_is_NOT_found_structurally():
    """The stated limit, demonstrated. If this ever started failing, the docstring would be UNDERstating
    what we do. The heuristic is the only thing that surfaces it, and it never moves the verdict."""
    m = _store()
    m.remember("alice bought a red bicycle", source={"doc": "user-42"})
    m.remember("digest note: alice likes red bicycle", source={"doc": "digest"})
    m.remember("summary of billing", derived=True,
               derived_from=[m.remember("billing raw", source={"doc": "runbook"})])   # unrelated lineage
    m.forget_subject("user-42", request_id="REQ-1")

    audit = m.erasure_audit(subject="user-42", values=["red bicycle"])
    assert audit["residue"] == [], "structurally invisible -- exactly the blind spot we document"
    assert audit["verdict"] == "no_declared_residue"          # lineage exists elsewhere, so not 'unaudited'
    assert "value_possibly_recoverable" in _kinds(audit["advisory"])
    assert any("HEURISTIC" in lim for lim in audit["limits"])


def test_taint_whose_origin_still_survives_does_not_fire():
    """NEGATIVE CONTROL. Without this, `taint_without_origin` could degenerate into 'this record has a
    taint field' and the whole suite would still pass — a mutation that deletes the origin check."""
    m = _store()
    parent = m.remember("alice bought a red bicycle", source={"doc": "user-42"})
    m.remember("summary: customer prefers red", derived=True, derived_from=[parent],
               source={"doc": "digest"})

    audit = m.erasure_audit()                # nothing erased; the origin is right there
    assert "taint_without_origin" not in _kinds(audit["residue"])
    assert "taint_without_origin" not in _kinds(audit["advisory"])
    assert audit["verdict"] == "no_declared_residue"


def test_value_scan_matches_on_word_boundaries_not_substrings():
    """Substring matching has burned this project twice; 'UTC' must not fire on 'UTC-8'."""
    m = _store()
    m.remember("meeting timezone is UTC-8 for the west coast team", source={"doc": "runbook"})
    assert m.erasure_audit(values=["UTC"])["advisory"] == []

    m.remember("the server clock is UTC", source={"doc": "runbook"})
    assert "value_possibly_recoverable" in _kinds(m.erasure_audit(values=["UTC"])["advisory"])


def test_value_scan_still_matches_a_value_that_ends_a_sentence():
    """Regression: excluding a bare '.' to stop 'v1.2.3' also swallowed every value at the end of a
    sentence, so the heuristic silently missed the most ordinary phrasing there is."""
    m = _store()
    m.remember("the server clock is UTC.", source={"doc": "runbook"})
    assert "value_possibly_recoverable" in _kinds(m.erasure_audit(values=["UTC"])["advisory"])

    m2 = _store()
    m2.remember("we pinned release v1.2.3 last week", source={"doc": "runbook"})
    assert m2.erasure_audit(values=["1"])["advisory"] == [], "an interior dot must still exclude"


def test_a_removed_record_with_no_tombstone_at_all_is_residue():
    m = _store()
    rid = m.remember("a receipted fact", source={"doc": "runbook"})
    m._items = [r for r in m._items if r["id"] != rid]    # out-of-band delete, no tombstone (the real list)

    audit = m.erasure_audit()
    assert "tombstone_gap" in _kinds(audit["residue"]) and audit["verdict"] == "residue_found"


def test_cli_erasure_audit_exit_codes():
    path = os.path.join(tempfile.mkdtemp(), "m.json")
    m = Inspeximus(path=path, receipts=True)
    parent = m.remember("alice bought a red bicycle", source={"doc": "user-42"})
    m.remember("summary: customer prefers red", derived=True, derived_from=[parent],
               source={"doc": "digest"})

    env = dict(os.environ, INSPEXIMUS_PATH=path, PYTHONPATH=os.path.dirname(
        os.path.dirname(os.path.abspath(__file__))))

    def cli(*args):
        return subprocess.run([sys.executable, "-m", "inspeximus.cli", *args],
                              capture_output=True, text=True, env=env)

    m.forget(ids=[parent], request_id="REQ-9", basis="art17")
    bad = cli("erasure-audit", "--subject", "user-42")
    assert bad.returncode == 1, "residue must be a non-zero exit so CI can gate on it"
    assert "RESIDUE" in bad.stdout and "dangling_lineage" in bad.stdout
    assert bad.stdout.isascii(), "CLI output must stay ASCII (non-UTF-8 consoles)"

    m.forget_subject("user-42", request_id="REQ-2")
    good = cli("erasure-audit", "--subject", "user-42")
    assert good.returncode == 0
    assert json.loads(cli("--json", "erasure-audit", "--subject", "user-42").stdout)["verdict"] \
        in ("no_declared_residue", "unaudited")
    assert "coverage" in cli("erasure-audit", "--subject", "user-42").stdout


def test_cli_write_extends_an_existing_receipt_chain():
    """Regression: the CLI opened stores with receipts OFF, so a shell `remember` against a receipted store
    silently did NOT extend the chain — the CLI punched a hole in the evidence it exists to produce."""
    path = os.path.join(tempfile.mkdtemp(), "m.json")
    m = Inspeximus(path=path, receipts=True)
    m.remember("first fact, written from python", key="k::1", object="one")
    assert len(m._receipts) == 1

    env = dict(os.environ, INSPEXIMUS_PATH=path, PYTHONPATH=os.path.dirname(
        os.path.dirname(os.path.abspath(__file__))))
    subprocess.run([sys.executable, "-m", "inspeximus.cli", "remember", "second fact, from the shell",
                    "--key", "k::2"], capture_output=True, text=True, env=env, check=True)

    reopened = Inspeximus(path=path, receipts=True)
    assert len(reopened._receipts) == 2, "the CLI write must extend the chain, not skip it"
    ok, problems = reopened.verify_writes()
    assert ok, problems


# ---------------------------------------------------------------------------------------------------
# PARTIAL COVERAGE IS NOT A PASS (2.6.0)
#
# Reported against us by Thomas Willner in the LLM Errata PRIOR_ART.md, while we were reviewing his
# spec: "Its tests force `unaudited` when declared lineage is zero, but a nonzero incomplete ratio can
# still return `no_declared_residue`." The demotion was a cliff at exactly zero, so ONE resolvable edge
# bought the pass verdict for a store that had announced four hundred derivations and resolved none.
# ---------------------------------------------------------------------------------------------------

def test_one_resolvable_edge_does_not_buy_a_pass_for_a_store_full_of_orphans():
    """THE DEFECT. Pre-fix this asserted `no_declared_residue` -- a pass, on a walk with 20 known holes."""
    m = _store()
    m.remember("alice bought a red bicycle", source={"doc": "user-42"})
    # The surviving declared edge is deliberately OFF-subject: a cascade erases the subject's own
    # derivative along with its root, so an edge built on user-42 cannot be the one left standing.
    billing = m.remember("billing raw", source={"doc": "runbook"})
    m.remember("summary of billing", derived=True, derived_from=[billing])
    for i in range(20):
        m.remember(f"undeclared summary {i}", derived=True)      # announces derivation, resolves nothing

    m.forget_subject("user-42", request_id="REQ-1")
    audit = m.erasure_audit(subject="user-42")

    assert audit["coverage"]["with_declared_lineage"] > 0, "the cliff must not be reachable via zero"
    assert audit["coverage"]["undeclared_derived"] == 20
    assert audit["verdict"] == "partially_audited", (
        "a store that announced 20 derivations and resolved none must not report the pass verdict just "
        "because one unrelated edge happened to resolve: got %r" % audit["verdict"])


def test_CONTROL_complete_lineage_still_earns_the_pass():
    """NEGATIVE CONTROL, and the reason the test above measures anything.

    `partially_audited` gates on the orphan COUNT. A gate that fires on every store is worth exactly as
    much as one that never fires, and this is the arm that fails if the new state degenerates into
    always-on. Same shape as the fixture above, minus the orphans."""
    m = _store()
    m.remember("alice bought a red bicycle", source={"doc": "user-42"})
    billing = m.remember("billing raw", source={"doc": "runbook"})
    m.remember("summary of billing", derived=True, derived_from=[billing])

    m.forget_subject("user-42", request_id="REQ-1")
    audit = m.erasure_audit(subject="user-42")

    assert audit["coverage"]["undeclared_derived"] == 0
    assert audit["verdict"] == "no_declared_residue", (
        "every derived record resolved its parents, so the walk had no hole and the pass is honest: "
        "got %r" % audit["verdict"])


def test_a_ratio_threshold_would_have_been_the_wrong_gate():
    """Why the gate is the orphan count and NOT `declared_ratio`.

    Most records are roots and derive from nothing, so a healthy store sits at a low ratio permanently.
    Here 1 record in 22 declares lineage -- ratio 0.045, indistinguishable from the broken store above --
    and every derived record resolved its parents. Any absolute cut on the ratio fires here, on a store
    with no hole at all. `orphan` is evidence; a proportion is not."""
    m = _store()
    m.remember("alice bought a red bicycle", source={"doc": "user-42"})
    billing = m.remember("billing raw", source={"doc": "runbook"})
    m.remember("summary of billing", derived=True, derived_from=[billing])
    for i in range(20):
        m.remember(f"independent observation {i}", source={"doc": f"sensor-{i}"})   # roots, not derived

    m.forget_subject("user-42", request_id="REQ-1")
    audit = m.erasure_audit(subject="user-42")

    assert audit["coverage"]["declared_ratio"] < 0.1, "a low ratio is the healthy base rate, not a defect"
    assert audit["coverage"]["undeclared_derived"] == 0
    assert audit["verdict"] == "no_declared_residue"


# ---------------------------------------------------------------------------------------------------
# STORE-WIDE COVERAGE CANNOT VOUCH FOR ONE SUBJECT
# Found while reviewing his spec for the mirror-image defect. Our own assertion comment carried it:
# "lineage exists elsewhere, so not 'unaudited'" -- the lineage that existed was about billing.
# ---------------------------------------------------------------------------------------------------

def test_lineage_about_someone_else_reaches_zero_records_for_this_subject():
    m = _store()
    m.remember("alice bought a red bicycle", source={"doc": "user-42"})
    billing = m.remember("billing raw", source={"doc": "runbook"})
    m.remember("summary of billing", derived=True, derived_from=[billing])   # real edge, wrong subject

    m.forget_subject("user-42", request_id="REQ-1")
    audit = m.erasure_audit(subject="user-42")

    assert audit["coverage"]["with_declared_lineage"] == 1, "the store does declare lineage"
    assert audit["coverage"]["subject_reachable_records"] == 0, (
        "no surviving record declares a tombstoned parent or carries user-42 taint, so not one edge the "
        "walk followed could have reached the erased material")
    assert any("never vouches for one subject" in lim for lim in audit["limits"])


def test_CONTROL_when_lineage_does_reach_the_subject_the_reach_is_nonzero():
    """The arm that fails if `subject_reachable_records` is hard-wired to 0 and measures nothing."""
    m = _store()
    root = m.remember("alice bought a red bicycle", source={"doc": "user-42"})
    m.remember("digest built from it", derived=True, derived_from=[root], source={"doc": "digest"})

    m.forget(ids=[root], request_id="REQ-7")        # erase ONLY the root; the derivative survives
    audit = m.erasure_audit(subject="user-42")

    assert audit["coverage"]["subject_reachable_records"] >= 1, (
        "a survivor declaring the tombstoned root is exactly what the walk CAN follow to this subject")
    assert "dangling_lineage" in _kinds(audit["residue"])


def test_subject_reach_is_absent_when_no_subject_was_asked_about():
    """A store-wide audit has no subject, so the field must be None rather than a misleading 0."""
    m = _store()
    m.remember("a note", source={"doc": "runbook"})
    assert m.erasure_audit()["coverage"]["subject_reachable_records"] is None
