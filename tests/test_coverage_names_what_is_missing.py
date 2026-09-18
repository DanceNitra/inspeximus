"""`coverage()` answers, for every obligation of an AI-agent operator, whether this store holds
evidence, whether the library could produce it, or whether nothing produces it.

Built 2026-09-17 after the owner found the product still calling itself "the agent-memory slice"
while the plan said "the whole evidence product": nobody could say in one place which duties were
covered, and the plan's own table was stale for four of them. The matrix is code, so it cannot be.

Controls. Every probe moves from CAPABILITY to EVIDENCE when the artifact is written (each duty is
exercised on a fixture, and a fresh store reads CAPABILITY for the same row). Every NOT COVERED row
names, in backticks, a function that does not exist in the package; the day one of them ships this
test fails until the row is rewritten, so a gap cannot close silently. NOT APPLICABLE rows carry a
reason. The rendered table lists every row once.
"""
from __future__ import annotations

import os
import re

import pytest

from inspeximus import Inspeximus
from inspeximus.coverage import (CAPABILITY, EVIDENCE, NOT_APPLICABLE, NOT_COVERED, OBLIGATIONS, coverage,
                                 render_markdown, render_text)

cryptography = pytest.importorskip("cryptography")


def _fresh(tmp_path):
    return Inspeximus(str(tmp_path / "mem.json"), receipts=True, receipt_key=os.urandom(32).hex())


def _state(rep, oid):
    return next(r for r in rep["rows"] if r["id"] == oid)


def test_a_fresh_store_holds_no_evidence_but_every_built_duty_is_a_capability(tmp_path):
    rep = coverage(_fresh(tmp_path))
    assert rep["counts"][EVIDENCE] == 0
    for r in rep["rows"]:
        assert r["state"] in (CAPABILITY, NOT_COVERED, NOT_APPLICABLE), r
    assert rep["covered"] == rep["counts"][CAPABILITY]
    assert rep["in_scope"] == len([o for o in OBLIGATIONS if not o.get("why")])


def test_each_artifact_moves_its_row_to_evidence(tmp_path):
    from inspeximus.actions import ActionLedger
    m = _fresh(tmp_path)
    led = ActionLedger(m, actor="agent")
    before = coverage(m)
    assert _state(before, "aia-12")["state"] == CAPABILITY
    assert _state(before, "aia-14")["state"] == CAPABILITY
    assert _state(before, "gdpr-17")["state"] == CAPABILITY

    rid = m.remember("Alice phone is +100", key="alice::phone", source={"doc": "crm/alice"})
    led.record("tool:sms", inputs={"to": "+100"}, output={"sent": True}, actor="agent")
    led.oversight("approve", actor="dpo", refers_to=0)
    led.disclosure(session="s1", shown="You are talking to an AI agent")
    led.incident(title="wrong number", severity="serious", actor="dpo")
    led.attest_retention(policy_days=180, actor="dpo")
    m.forget_subject("crm/alice", request_id="DSAR-1")

    after = coverage(m)
    for oid in ("aia-12", "aia-14", "aia-50", "aia-73", "aia-19", "gdpr-17", "gdpr-5", "aia-15"):
        assert _state(after, oid)["state"] == EVIDENCE, (oid, _state(after, oid))
    assert _state(after, "aia-12")["count"] == 1
    assert _state(after, "gdpr-17")["count"] == 1
    assert after["counts"][EVIDENCE] > before["counts"][EVIDENCE]


def test_every_not_covered_row_names_a_function_that_does_not_exist_yet():
    """The gap text is a promise; when the function ships, this fails until the row is rewritten."""
    import inspeximus.actions as actions
    try:
        import inspeximus.mcp_server as mcp        # CI's plain job has no MCP SDK; the ledger check still runs
    except ImportError:
        mcp = None
    for ob in OBLIGATIONS:
        if ob.get("probe") is None and not ob.get("why"):
            names = re.findall(r"`([a-z_]+)`", ob["gap"])
            assert names, f"{ob['id']}: the gap must name the function that would close it, in backticks"
            for n in names:
                assert not hasattr(actions.ActionLedger, n) and not (mcp is not None and hasattr(mcp, n)), \
                    f"{ob['id']}: `{n}` exists now; rewrite the row with a probe"


def test_not_applicable_rows_carry_a_reason_and_are_outside_the_score(tmp_path):
    rep = coverage(_fresh(tmp_path))
    na = [r for r in rep["rows"] if r["state"] == NOT_APPLICABLE]
    assert na and all(len(r["detail"]) > 40 for r in na)
    assert rep["in_scope"] + len(na) == len(rep["rows"])


def test_the_renderings_list_every_row_once(tmp_path):
    rep = coverage(_fresh(tmp_path))
    md = render_markdown(rep)
    txt = render_text(rep)
    for r in rep["rows"]:
        assert md.count(f"| {r['law']} | {r['article']} |") == 1
        assert r["duty"] in txt
    assert "not a certification" in txt.lower() or "not a certification" in rep["scope"].lower()


def test_a_probe_that_raises_is_capability_not_evidence(tmp_path, monkeypatch):
    from inspeximus import coverage as cov
    row = next(o for o in cov.OBLIGATIONS if o["id"] == "aia-12")
    monkeypatch.setitem(row, "probe", lambda store: (_ for _ in ()).throw(RuntimeError("boom")))
    rep = coverage(_fresh(tmp_path))
    r = _state(rep, "aia-12")
    assert r["state"] == CAPABILITY and "boom" in r["detail"]
