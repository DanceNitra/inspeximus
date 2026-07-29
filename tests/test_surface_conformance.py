"""Every guarantee the README and docs/AI_ACT.md advertise, exercised through the SURFACE a user has.

WHY THIS FILE EXISTS. Two consecutive releases shipped erasure defects that 1600 library tests could not
see, because the tests call `Inspeximus(...)` and the product is used through an MCP server and a CLI.
Measured on 1.86.0: a store written through its own MCP surface answered `would_erase = 0` to every
phrasing of the subject, while the identical write through the library answered 1. Both defects were found
by downloading the published wheel and trying it by hand, which is not a strategy.

So the rule here, and it is the whole point: **nothing in this file imports Inspeximus to do the work.**
Every guarantee is set up, exercised and read back through `inspeximus.mcp_server` or `python -m
inspeximus.cli`. The library is used only to inspect the resulting store, never to create it.

A guarantee that a surface cannot express is not a failure to be hidden -- it is recorded as
`unreachable`, because "the library can do it" is not the claim the documentation makes to a user.
"""
from __future__ import annotations

import importlib
import json
import os
import subprocess
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# ── the two surfaces ─────────────────────────────────────────────────────────────────────────────
def cli(store, *args, expect_ok=True):
    env = {**os.environ, "PYTHONPATH": ROOT + os.pathsep + os.environ.get("PYTHONPATH", ""),
           "PYTHONIOENCODING": "utf-8"}
    r = subprocess.run([sys.executable, "-m", "inspeximus.cli", "--path", str(store), *args],
                       cwd=ROOT, capture_output=True, text=True, env=env, timeout=300)
    if expect_ok:
        assert r.returncode == 0, f"CLI {args} failed: {r.stderr[-500:]}"
    return r


@pytest.fixture()
def mcp(monkeypatch, tmp_path):
    """A fresh MCP server bound to a throwaway store."""
    pytest.importorskip("mcp")
    monkeypatch.setenv("INSPEXIMUS_PATH", str(tmp_path / "mcp.json"))
    import inspeximus.mcp_server as m
    return importlib.reload(m)


def store_of(path):
    """Read the resulting store. Inspection only -- never used to CREATE what is under test."""
    from inspeximus import Inspeximus
    return Inspeximus(path=str(path), receipts=True)


# ── GDPR Art.17: erasure reaches the subject and its derived lineage ────────────────────────────
def test_art17_erasure_through_MCP(mcp):
    """The guarantee: "forget_subject hard-deletes the subject PLUS its derived lineage".

    This is the one that shipped broken twice. Set up entirely through the MCP tools."""
    parent = mcp.remember("alice home address is 5 Elm St", key="alice::addr",
                          object="5 Elm St", source="hr/alice")
    mcp.remember("summary of alice's file", source="summary-svc", derived_from=[parent["id"]])
    res = mcp.forget_subject("hr/alice", request_id="dsar-1", basis="art17")
    assert res["erased"] == 2, f"the derived record was not reached: {res}"


def test_art17_erasure_through_CLI(tmp_path):
    s = tmp_path / "s.json"
    parent = json.loads(cli(s, "--json", "remember", "alice home address is 5 Elm St",
                            "--source", "hr/alice").stdout)["id"]
    cli(s, "--json", "remember", "summary of alice", "--source", "summary-svc",
        "--derived-from", parent)
    out = cli(s, "forget-subject", "hr/alice", "--dry-run").stdout
    assert "would erase 2 record(s)" in out and "1 reached through lineage" in out


def test_art17_a_correction_goes_with_the_subject_through_MCP(mcp):
    """The 1.88.0 fix, through the surface: a correction routed by an utterance must not survive the
    subject's erasure holding their CURRENT value."""
    mcp.remember("alice addr is 5 Elm St", key="alice::addr", object="5 Elm St", source="hr/alice")
    mcp.route("actually alice moved to 9 Oak Ave", key="alice::addr", object="9 Oak Ave")
    res = mcp.forget_subject("hr/alice", request_id="dsar-2", basis="art17")
    st = store_of(os.environ["INSPEXIMUS_PATH"])
    alive = " ".join((r.get("text") or "") for r in st.items if r.get("status") != "erased")
    assert res["erased"] == 2 and "9 Oak" not in alive


def test_art17_a_ghost_subject_erases_nothing_through_MCP(mcp):
    """The 1.87.0 fix, through the surface. CONTROL for the tests above: erasure must be narrow."""
    mcp.remember("alice payroll", key="p", object="x", source="hr/alice")
    assert mcp.forget_subject("hr/nobody-here", request_id="g", basis="art17")["erased"] == 0


# ── GDPR Art.17: the erasure carries a receipt ───────────────────────────────────────────────────
def test_erasure_certificate_through_MCP(mcp):
    """The guarantee: "signed content-free tombstone + erasure_certificate"."""
    mcp.remember("alice detail", key="d", object="x", source="hr/alice")
    mcp.forget_subject("hr/alice", request_id="dsar-3", basis="art17")
    cert = mcp.erasure_certificate()
    assert cert.get("count", 0) >= 1
    assert cert.get("self_check", {}).get("verified") is True
    assert "alice detail" not in json.dumps(cert), "the certificate must stay CONTENT-FREE"


# ── Art.12/19: every write is a hash-linked receipt, verifiable ──────────────────────────────────
def test_art12_receipts_are_OFF_by_default_at_the_surface(mcp):
    """FOUND BY THIS SUITE ON ITS FIRST RUN, and it is a documentation defect rather than a code one.

    docs/AI_ACT.md states the Art.12/19 guarantee as "Every write is a hash-linked, timestamped receipt"
    and bolds tamper-evident record-keeping as the differentiator against mem0, Zep and Graphiti. Through
    the MCP surface it is OPT-IN and off:

        verify_writes() -> ok False, "write receipts are DISABLED: 1 record(s) exist with no write
                           chain, so there is nothing to verify -- which is not the same as verified"

    The default itself is defensible -- enabling receipts creates a `.receipts.json` sidecar next to the
    user's store, and a memory server should not grow files in someone's directory unasked. What was not
    defensible was a compliance document asserting the guarantee with no mention of the precondition. The
    document now states it; this test pins the behaviour so the two cannot drift apart again.

    verify_writes() reporting False here is the RIGHT answer and the reason this is not a silent hole: it
    distinguishes "nothing to verify" from "verified", which is the distinction the whole audit is about."""
    mcp.remember("revenue is 100M", key="rev", object="100M", source="finance")
    ok = mcp.verify_writes()
    assert ok["ok"] is False
    assert "DISABLED" in " ".join(ok["problems"])
    assert "not the same as verified" in " ".join(ok["problems"])


def test_art12_write_receipts_verify_once_ENABLED(monkeypatch, tmp_path):
    """The guarantee itself, with the precondition the document now names."""
    pytest.importorskip("mcp")
    monkeypatch.setenv("INSPEXIMUS_PATH", str(tmp_path / "r.json"))
    monkeypatch.setenv("INSPEXIMUS_RECEIPTS", "1")
    m = importlib.reload(sys.modules.get("inspeximus.mcp_server")
                         or importlib.import_module("inspeximus.mcp_server"))
    m.remember("revenue is 100M", key="rev", object="100M", source="finance")
    ok = m.verify_writes()
    assert ok["ok"] is True, ok


def test_art12_a_tampered_record_is_caught_through_MCP(monkeypatch, tmp_path):
    """CONTROL: a receipt chain that cannot report tampering is decoration. Run with receipts ON,
    because with them off `verify_writes` returns False for a different reason and the test would pass
    without ever exercising the chain."""
    pytest.importorskip("mcp")
    monkeypatch.setenv("INSPEXIMUS_PATH", str(tmp_path / "t.json"))
    monkeypatch.setenv("INSPEXIMUS_RECEIPTS", "1")
    m = importlib.reload(sys.modules.get("inspeximus.mcp_server")
                         or importlib.import_module("inspeximus.mcp_server"))
    rid = m.remember("revenue is 100M", key="rev", object="100M", source="finance")["id"]
    assert m.verify_writes()["ok"] is True, "the chain must be clean before it is tampered with"
    st = store_of(os.environ["INSPEXIMUS_PATH"])
    rec = next(r for r in st.items if r["id"] == rid)
    rec["text"] = "revenue is 900M"
    st._save(force=True)
    m2 = importlib.reload(sys.modules["inspeximus.mcp_server"])
    assert m2.verify_writes()["ok"] is False, "an out-of-band edit was not detected"


# ── Art.15: keyed supersession serves the corrected value ────────────────────────────────────────
def test_art15_correction_sticks_through_MCP(mcp):
    """The README's first line: correct a fact once and it stays corrected."""
    mcp.remember("the deploy region is frankfurt", key="cfg::region", object="frankfurt")
    mcp.remember("the deploy region is ohio", key="cfg::region", object="ohio")
    hits = mcp.recall("deploy region", k=5)
    text = " ".join(h.get("text", "") for h in hits)
    assert "ohio" in text and "frankfurt" not in text


def test_art15_an_echo_cannot_resurrect_the_old_value_through_MCP(mcp):
    """The echo guard, through the surface -- default ON since 1.87.0."""
    mcp.remember("the deploy region is frankfurt", key="cfg::region", object="frankfurt")
    mcp.remember("the deploy region is ohio", key="cfg::region", object="ohio")
    mcp.remember("the deploy region is frankfurt", key="cfg::region", object="frankfurt")
    hits = mcp.recall("deploy region", k=5)
    assert "frankfurt" not in " ".join(h.get("text", "") for h in hits)


def test_art15_correction_sticks_through_CLI(tmp_path):
    s = tmp_path / "s.json"
    cli(s, "--json", "remember", "the deploy region is frankfurt", "--key", "cfg::region",
        "--object", "frankfurt")
    cli(s, "--json", "remember", "the deploy region is ohio", "--key", "cfg::region",
        "--object", "ohio")
    out = cli(s, "--json", "recall", "deploy region").stdout
    assert "ohio" in out and "frankfurt" not in out


# ── the compliance overlay ───────────────────────────────────────────────────────────────────────
def test_compliance_report_through_CLI(tmp_path):
    """The guarantee: "`inspeximus compliance` article-labelled overlay"."""
    s = tmp_path / "s.json"
    cli(s, "--json", "remember", "alice detail", "--source", "hr/alice")
    out = cli(s, "--json", "compliance").stdout
    assert out.strip(), "the compliance command produced nothing"
    blob = out.lower()
    assert "art" in blob and ("17" in blob or "12" in blob), out[:300]


def test_retention_dry_run_through_CLI(tmp_path):
    """The guarantee: "DRY-RUN: what would be erased (GDPR Art. 5(1)(e))"."""
    s = tmp_path / "s.json"
    cli(s, "--json", "remember", "alice detail", "--source", "hr/alice")
    r = cli(s, "retention", "--max-age-days", "90")
    st = store_of(s)
    assert any(x.get("status") != "erased" for x in st.items), \
        "a dry run must not erase anything"
    assert r.returncode == 0
