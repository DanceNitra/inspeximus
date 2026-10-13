"""A verification call whose check could not run says so, instead of reading clean (3.19, G-4).

Each surface below caught any error around one of its checks and went on: `verify_writes` skipped its comparison of the
records with the rows, `verify_bundle` kept `ok=True` with its merkle roots unchecked, `compliance_report` counted an
unreadable partition registry or action ledger as zero, and `audit_the_audits` ran a probe on a copy that was missing
one of the store's files. Each test makes the check raise and requires the result to name the check that did not run.
Each carries a control: without the raise, the same call reads clean or counts as before.
"""
from __future__ import annotations

import os
import shutil
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from inspeximus import core  # noqa: E402
from inspeximus.core import Inspeximus  # noqa: E402


def _store(tmp_path, **kw):
    m = Inspeximus(str(tmp_path / "s.db"), receipts=True, **kw)
    for i in range(4):
        m.remember("note %d about the deploy window" % i, key="k%d" % i)
    m.flush()
    return m


def _boom(*a, **k):
    raise RuntimeError("forced: this check cannot run")


def test_verify_writes_reports_a_comparison_that_did_not_run(tmp_path, monkeypatch):
    m = _store(tmp_path)
    ok, problems = m.verify_writes()
    assert ok and not problems, "CONTROL: a healthy store verifies clean: %r" % (problems,)
    monkeypatch.setattr(core._rows, "load", _boom)
    ok, problems = m.verify_writes()
    assert not ok and any("comparison not run" in p for p in problems), \
        "verify_writes read clean although it never compared the records with the rows: %r" % (problems,)


def test_verify_bundle_reports_merkle_roots_it_could_not_derive(tmp_path, monkeypatch):
    from inspeximus import merkle
    from inspeximus.audit_bundle import build_bundle, verify_bundle
    bundle = build_bundle(_store(tmp_path))
    out = verify_bundle(bundle)
    assert out["ok"] and any("re-derives" in c for c in out["checks"]), \
        "CONTROL: the bundle verifies and its roots are checked: %r" % (out["problems"],)
    monkeypatch.setattr(merkle, "root", _boom)
    out = verify_bundle(bundle)
    assert not out["ok"] and any("check not run" in p for p in out["problems"]), \
        "verify_bundle read ok with its merkle roots unchecked: %r" % (out["problems"],)


def test_compliance_report_does_not_count_an_unreadable_partition_registry_as_none(tmp_path, monkeypatch):
    from inspeximus.compliance import compliance_report
    from inspeximus.partitions import Partitions
    m = _store(tmp_path)
    Partitions(m).open("job-1")
    rep = compliance_report(m)
    row = [c for c in rep["controls"] if c["article"] == "Art. 5(1)(e)"][0]
    assert row["live_count"] == 1 and row["status"] == "evidence", "CONTROL: the open partition is counted: %r" % row
    monkeypatch.setattr(Partitions, "report", _boom)
    rep = compliance_report(m)
    row = [c for c in rep["controls"] if c["article"] == "Art. 5(1)(e)"][0]
    assert row["status"] == "not_checked" and "check not run" in row.get("not_checked", ""), \
        "an unreadable partition registry was reported as %r with %r" % (row["status"], row["live_count"])
    assert rep["summary"]["checks_not_run"], "the summary does not say a check did not run"


def test_compliance_report_does_not_count_an_unopenable_ledger_as_empty(tmp_path, monkeypatch):
    from inspeximus import actions
    from inspeximus.compliance import compliance_report
    m = _store(tmp_path)
    rep = compliance_report(m)
    assert not rep["summary"]["checks_not_run"], "CONTROL: every count was read: %r" % rep["summary"]
    monkeypatch.setattr(actions.ActionLedger, "__init__", _boom)
    rep = compliance_report(m)
    assert rep["action_ledger"].get("ledger_verified") is False and "check not run" in \
        rep["action_ledger"].get("ledger_error", ""), "the ledger read as absent: %r" % (rep["action_ledger"],)
    assert any(c["status"] == "not_checked" for c in rep["controls"]), "no control says its count was not read"


def test_audit_the_audits_says_when_a_copy_is_missing_one_of_the_stores_files(tmp_path, monkeypatch):
    m = _store(tmp_path)
    side = [f for f in os.listdir(tmp_path) if f.startswith("s.db") and f != "s.db"]
    assert side, "CONTROL: the store has a file beside it to copy"
    out = m.audit_the_audits()
    assert not any("incomplete copy" in x for x in out["limits"]), "CONTROL: every copy is complete"
    # A store-mode probe (no `mode`) copies the store itself and is checked above; the others build on a seed copy.
    seeded = {r["probe"] for r in out["probes"] if r.get("mode") is not None and r.get("tier") == "your store"}
    assert seeded, "CONTROL: some probes build on a copy of this store and count as the caller's store"
    real = shutil.copy

    def copy_but_the_sidecars(src, dst, *a, **k):
        if os.path.basename(str(src)) in side:
            raise PermissionError("forced: this file cannot be copied")
        return real(src, dst, *a, **k)
    monkeypatch.setattr(shutil, "copy", copy_but_the_sidecars)
    out = m.audit_the_audits()
    assert any("incomplete copy" in x for x in out["limits"]), \
        "a probe ran on a copy missing %s and nothing said so: %r" % (side, out["limits"][:3])
    # Some argument-mode probes never copy the store, so they stay; every probe built on the seed copy must move.
    still = {r["probe"] for r in out["probes"] if r["probe"] in seeded and r.get("tier") == "your store"}
    assert still < seeded, "probes built on a copy missing %s were counted as the caller's store: %s" % (
        side, sorted(still))
