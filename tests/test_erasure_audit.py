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
