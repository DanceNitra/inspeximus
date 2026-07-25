"""Tests written FROM a mutation run, for predicates that no existing test could distinguish.

A systematic pass over 400 single-point mutants killed 131 — a **32.8% mutation score**, and only 54.9% on
lines the suite executes at all. The survivors clustered on exactly the predicates this product is sold on:
attribution verification, bundle tamper checks, and tenant scoping. Each test below was written because a
specific mutant survived; the mutation is named in the docstring so the test cannot be "simplified" back into
one that passes either way.

The general lesson, which cost several rounds to learn: a suite that is green tells you nothing about the
predicates it never forces to decide.
"""
import json
import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from inspeximus import Inspeximus
from inspeximus.audit_bundle import build_bundle, verify_bundle


def _path():
    return os.path.join(tempfile.mkdtemp(), "m.json")


# ── attribution ─────────────────────────────────────────────────────────────────────────────────────
def test_verify_attribution_detects_a_relabelled_source():
    """SURVIVOR: core.py `verify_attribution`, `!=` → `==` — inverting the source-hash comparison left the
    suite green. That predicate is the whole of relabel detection: the receipt commits to a record's
    attribution precisely so a silent source rewrite is catchable."""
    m = Inspeximus(path=_path(), receipts=True)
    rid = m.remember("the db host is old.host", source={"doc": "runbook"})
    assert m.verify_attribution()["ok"] is True

    next(r for r in m.items if r["id"] == rid)["source"] = {"doc": "someone-elses-doc"}
    res = m.verify_attribution()
    assert res["ok"] is False
    assert rid in str(res.get("relabeled"))


def test_verify_attribution_does_not_cry_wolf_on_an_untouched_store():
    m = Inspeximus(path=_path(), receipts=True)
    for i in range(5):
        m.remember(f"fact {i}", source={"doc": f"src-{i}"})
    assert m.verify_attribution()["ok"] is True


# ── bundle tamper checks ────────────────────────────────────────────────────────────────────────────
def _tampered(mutate):
    m = Inspeximus(path=_path(), receipts=True)
    m.remember("a")
    m.remember("b")
    b = build_bundle(m)
    mutate(b)
    return verify_bundle(b)


def test_a_bundle_whose_write_chain_disagrees_with_its_anchor_fails():
    """SURVIVOR: audit_bundle.py `or` → `and` in the write-chain check. With `and`, a tip mismatch alone no
    longer failed — you had to break the COUNT as well. Dropping a receipt does exactly that: it changes the
    tip while the anchor still claims the old one."""
    # Tamper ONLY the tip, leaving the COUNT correct. Popping a receipt changes both, so `or` and `and`
    # behave identically on it — the mutant survived my first version of this test for exactly that reason.
    res = _tampered(lambda b: b["anchor"].update(writes_tip="0" * 64))
    assert res["ok"] is False
    assert any("write chain" in p for p in res["problems"])

    only_count = _tampered(lambda b: b["anchor"].update(n_writes=99))
    assert only_count["ok"] is False, "and a count mismatch alone must also fail"


def test_a_bundle_whose_tombstone_chain_disagrees_with_its_anchor_fails():
    """SURVIVOR: the same `or` → `and`, one check down."""
    m = Inspeximus(path=_path(), receipts=True)
    m.remember("a", source={"doc": "dave"})
    m.forget_subject("dave", request_id="R", basis="art17")
    b = build_bundle(m)
    b["anchor"]["n_tombstones"] = 99
    res = verify_bundle(b)
    assert res["ok"] is False
    assert any("tombstone" in p for p in res["problems"])


def test_something_that_is_not_a_bundle_is_rejected():
    """SURVIVOR: `or` → `and` in the kind check made a dict with the right `kind` but no bundle shape — or
    the reverse — slip past the guard clause."""
    assert verify_bundle({"kind": "not-an-inspeximus-bundle"})["ok"] is False
    assert verify_bundle({})["ok"] is False
    assert verify_bundle([])["ok"] is False


def test_a_witness_threshold_of_zero_is_not_satisfied_by_no_witnesses():
    """SURVIVOR: `threshold: int = 1` → `0`. With a zero threshold, k-of-n is vacuously met and the ONLY
    operator-adversarial check in the design passes with nobody having signed anything."""
    m = Inspeximus(path=_path(), receipts=True)
    m.remember("a")
    b = build_bundle(m)
    res = verify_bundle(b, witnesses=["deadbeef" * 8], threshold=1)
    assert res["ok"] is False, "witnesses supplied but no co-signature must not verify"


# ── the echo guard is a DEFAULT, not a spelling ─────────────────────────────────────────────────────
def test_the_mcp_server_ships_the_echo_guard_on():
    """SURVIVOR: mcp_server.py `os.environ.get("INSPEXIMUS_ECHO_GUARD", "1")` → `"0"` — shipping the guard
    OFF — and the suite stayed green, because the only test of it GREPPED both files for the env-var name.
    That test asserted a spelling. This one imports the module and reads the resulting value."""
    pytest.importorskip("mcp")
    import importlib
    os.environ.pop("INSPEXIMUS_ECHO_GUARD", None)
    os.environ["INSPEXIMUS_PATH"] = _path()
    mod = importlib.import_module("inspeximus.mcp_server")
    importlib.reload(mod)
    assert mod._MEM.echo_guard is True, "the MCP surface must ship with the echo guard ON by default"


def test_the_echo_guard_actually_suppresses_a_restatement():
    """And the behaviour behind the default, so the flag cannot become decorative."""
    m = Inspeximus(path=_path())
    m.echo_guard = True
    m.remember("region is tokyo", key="region", object="tokyo")
    m.remember("region is osaka", key="region", object="osaka")
    m.remember("region is tokyo", key="region", object="tokyo")      # the echo
    active = [r["object"] for r in m.items if r.get("key") == "region" and r["status"] == "active"]
    assert active == ["osaka"]


# ── tenant predicates ───────────────────────────────────────────────────────────────────────────────
def _two_tenants():
    s = Inspeximus(path=_path(), receipts=True)
    a, b = s.for_tenant("acme"), s.for_tenant("globex")
    a.remember("acme says the sky is blue", key="sky", object="blue")
    b.remember("globex says the sky is GREEN-SECRET", key="sky", object="GREEN-SECRET")
    return s, a, b


def test_contradictions_does_not_pair_two_tenants_records():
    """SURVIVOR: core.py `contradictions`, tenant scope `==` → `!=`. Inverting it makes the method report on
    exactly the records it must not see — and nothing failed."""
    _, a, _ = _two_tenants()
    assert "GREEN-SECRET" not in str(a.contradictions())


def test_reopened_and_resolution_stay_within_a_tenant():
    """SURVIVOR: `resolve_reopened`, tenant check `!=` → `==` and `and` → `or`."""
    _, a, b = _two_tenants()
    b.observe("globex says the sky is blue", key="sky", object="blue")
    b.observe("globex says the sky is blue", key="sky", object="blue")
    assert not [r for r in (a.reopened() or []) if "GREEN-SECRET" in str(r)]


def test_stale_value_detection_is_tenant_scoped():
    """SURVIVOR: `_stale_by_value`, tenant guard `and` → `or`."""
    _, a, _ = _two_tenants()
    assert "GREEN-SECRET" not in str(a.recall("sky", k=10))


# ── retention and redaction boundaries ──────────────────────────────────────────────────────────────
def test_retention_does_not_expire_a_record_exactly_at_the_cutoff():
    """SURVIVOR: compliance.py retention cutoff `<` → `<=`. An off-by-one on a DELETION boundary erases a
    record the policy says to keep — the kind of mutant that is invisible until it costs someone data."""
    from inspeximus.compliance import retention_sweep
    m = Inspeximus(path=_path(), receipts=True)
    rid = m.remember("kept", source={"doc": "s"})
    rec = next(r for r in m.items if r["id"] == rid)
    import time as _t
    rec["ts"] = _t.time() - 10 * 86400
    at_cutoff = retention_sweep(m, max_age_days=10.0, pii_only=False, apply=False,
                                now_ts=rec["ts"] + 10 * 86400)
    past_cutoff = retention_sweep(m, max_age_days=10.0, pii_only=False, apply=False,
                                  now_ts=rec["ts"] + 11 * 86400)
    assert at_cutoff["eligible"] == 0, "a record exactly AT the retention horizon is not yet past it"
    assert past_cutoff["eligible"] == 1, "and one past it must still be caught"


def test_redaction_spans_that_touch_do_not_swallow_each_other():
    """SURVIVOR: core.py `redact_pii` span-overlap `<` → `<=`. Two adjacent spans are not overlapping ones;
    treating them as such drops a redaction."""
    from inspeximus.core import redact_pii
    out = redact_pii("a@b.com c@d.com")
    assert "a@b.com" not in out and "c@d.com" not in out, out
