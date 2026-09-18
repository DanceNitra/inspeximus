"""EU AI Act Art. 9 (risk management) and Art. 72 (post-market monitoring) as ledger evidence.

Art. 9(2) calls the risk management system "a continuous iterative process ... requiring regular
systematic review and updating", so a risk here is a `risk_id` with a history: every review is a new
signed entry, and the register reads the latest state plus the age of the last review. Art. 9(8)
asks for testing "against prior defined metrics and probabilistic thresholds", so a test record needs
its threshold and the register counts open risks that have none. Art. 72(2) asks the provider to
"actively and systematically collect, document and analyse" performance data and evaluate continuous
compliance with Section 2, so the report is built from the ledgers (never typed in), runs the chain
verifier, and with an actor is itself appended as a signed `monitoring` entry.

Controls: a fresh store reads CAPABILITY for both rows and EVIDENCE after one artifact each; a risk
with an unresolvable reference, a source outside Art. 9(2), or a test without a threshold is refused;
the appended monitoring entry carries the hash of the report it summarises and the chain still
verifies with both new kinds in it.
"""
from __future__ import annotations

import os
import time

import pytest

from inspeximus import Inspeximus
from inspeximus.actions import RISK_HARMS, RISK_MEASURES, RISK_SOURCES, ActionLedger, _content_hash
from inspeximus.coverage import CAPABILITY, EVIDENCE, coverage

cryptography = pytest.importorskip("cryptography")


def _fresh(tmp_path):
    m = Inspeximus(str(tmp_path / "mem.json"), receipts=True, receipt_key=os.urandom(32).hex())
    return m, ActionLedger(m, actor="agent")


def _row(rep, oid):
    return next(r for r in rep["rows"] if r["id"] == oid)


def test_a_risk_entry_moves_art_9_from_capability_to_evidence(tmp_path):
    m, led = _fresh(tmp_path)
    assert _row(coverage(m), "aia-9")["state"] == CAPABILITY
    e = led.risk("R1", "recalled text steers the agent", "fundamental_rights", "foreseeable_misuse", "ops",
                 measure="echo_guard on recall", measure_kind="mitigate", residual="low",
                 residual_acceptable=True, evidence=["probes/echo_attack.py"],
                 tests=[{"metric": "resurrection_rate", "threshold": 0.0, "observed": 0.0, "passed": True}])
    assert e["kind"] == "risk" and e["action"] == "risk:foreseeable_misuse" and "sig" in e
    row = _row(coverage(m), "aia-9")
    assert row["state"] == EVIDENCE and row["count"] == 1
    assert led.verify()[0]


def test_the_register_reads_the_latest_state_and_counts_what_an_assessor_asks(tmp_path):
    m, led = _fresh(tmp_path)
    t0 = time.time() - 10 * 86400
    led.risk("R1", "wrong recall", "safety", "intended_use", "ops", likelihood="high", severity="high")
    led.risk("R2", "stale fact resurrects", "fundamental_rights", "post_market", "ops",
             affects_vulnerable_groups=True)
    # a review of R1: measure adopted, residual judged, tested against a prior threshold
    led.risk("R1", "wrong recall", "safety", "intended_use", "ops", likelihood="low", severity="high",
             measure="supersession by key", measure_kind="eliminate", residual="low", residual_acceptable=True,
             evidence=["tests/test_supersession.py"],
             tests=[{"metric": "stale_answer_rate", "threshold": 0.01, "observed": 0.0, "passed": True}])
    reg = led.risk_register(now=time.time())
    r1 = next(r for r in reg["risks"] if r["risk_id"] == "R1")
    assert r1["entries"] == 2 and r1["likelihood"] == "low" and r1["residual_acceptable"] is True
    assert r1["first_seq"] == 0 and r1["last_seq"] == 2 and r1["days_since_review"] < 1
    c = reg["counts"]
    assert c["total"] == 2 and c["open"] == 2
    assert c["by_source"] == {"intended_use": 1, "foreseeable_misuse": 0, "post_market": 1}
    assert c["by_harm"] == {"health": 0, "safety": 1, "fundamental_rights": 1}
    assert c["without_measure"] == 1 and c["residual_not_judged"] == 1 and c["residual_not_acceptable"] == 0
    assert c["without_evidence"] == 1 and c["without_test"] == 1 and c["test_failed"] == 0
    assert c["vulnerable_groups"] == 1
    # a closed risk leaves the open counts
    led.risk("R2", "stale fact resurrects", "fundamental_rights", "post_market", "ops", status="closed")
    c2 = led.risk_register()["counts"]
    assert c2["open"] == 1 and c2["without_measure"] == 0 and c2["total"] == 2
    assert reg["kind"] == "inspeximus.risk_register/1" and "evidence" in reg["scope"]


def test_a_risk_outside_art_9_vocabulary_or_without_its_threshold_is_refused(tmp_path):
    m, led = _fresh(tmp_path)
    with pytest.raises(ValueError, match="source"):
        led.risk("R", "h", "safety", "rumour", "ops")
    with pytest.raises(ValueError, match="harm"):
        led.risk("R", "h", "money", "intended_use", "ops")
    with pytest.raises(ValueError, match="measure_kind"):
        led.risk("R", "h", "safety", "intended_use", "ops", measure_kind="hope")
    with pytest.raises(ValueError, match="residual_acceptable"):
        led.risk("R", "h", "safety", "intended_use", "ops", residual_acceptable=True)
    with pytest.raises(ValueError, match="threshold"):
        led.risk("R", "h", "safety", "intended_use", "ops", tests=[{"metric": "x", "observed": 1}])
    with pytest.raises(ValueError):
        led.risk("R", "h", "safety", "intended_use", "ops", refers_to=[41])
    with pytest.raises(ValueError, match="actor"):
        led.risk("R", "h", "safety", "intended_use", "")
    assert len(led) == 0
    assert RISK_SOURCES == ("intended_use", "foreseeable_misuse", "post_market")
    assert RISK_HARMS == ("health", "safety", "fundamental_rights")
    assert RISK_MEASURES == ("eliminate", "mitigate", "inform")


def test_the_monitoring_report_is_built_from_the_ledgers_and_signed_in(tmp_path):
    m, led = _fresh(tmp_path)
    assert _row(coverage(m), "aia-72")["state"] == CAPABILITY
    since = time.time() - 1
    led.record("tool:search", inputs={"q": "x"}, output=[], actor="agent")
    led.record("tool:send", inputs={"to": "y"}, status="error", error="bounced", actor="agent")
    led.oversight("refuse", actor="dpo", refers_to=1)
    led.incident("bounced mail", "serious", actor="dpo", refers_to=[1])
    led.risk("R1", "wrong recipient", "safety", "post_market", "ops", refers_to=[3])
    led.disclosure(session="s1", shown="You are talking to an AI agent")
    plan = {"name": "PMM plan", "version": "1", "text": "collect refusals and incidents monthly"}

    rep = led.post_market_report(since=since, plan=plan)   # read-only
    assert rep["kind"] == "inspeximus.post_market_report/1"
    assert rep["actions"] == {"total": 2, "error": 1, "by_action": {"tool:search": 1, "tool:send": 1}}
    assert rep["oversight"]["by_event"] == {"refuse": 1} and rep["oversight"]["refusal_or_override_rate"] == 0.5
    assert rep["incidents"]["opened"] == 1 and rep["incidents"]["overdue"] == []
    assert rep["risks"] == {"recorded": 1, "from_post_market": 1, "residual_not_acceptable": 0}
    assert rep["disclosures"] == 1
    assert rep["chain"] == {"verified": True, "problems": 0, "entries": 6}
    assert rep["plan"] == {"name": "PMM plan", "version": "1", "sha256": _content_hash(plan)}
    assert "text" not in str(rep["plan"]) and "ledger_seq" not in rep
    assert "Art. 9" in rep["requirements"] and "Art. 15" in rep["requirements"]
    assert _row(coverage(m), "aia-72")["state"] == CAPABILITY, "a read-only report is not evidence"

    signed = led.post_market_report(since=since, actor="ops", plan=plan, note="monthly")
    entry = led.entries()[signed["ledger_seq"]]
    assert entry["kind"] == "monitoring" and entry["action"] == "monitoring:post_market" and "sig" in entry
    assert entry["counts"] == {"actions": 2, "incidents": 1, "oversight": 1, "risks": 1}
    assert entry["plan"]["sha256"] == _content_hash(plan) and entry["note"] == "monthly"
    unsigned_view = dict(signed)
    unsigned_view.pop("ledger_seq")
    assert entry["summary_sha256"] == _content_hash(unsigned_view)
    row = _row(coverage(m), "aia-72")
    assert row["state"] == EVIDENCE and row["count"] == 1
    assert led.verify()[0]
    rep2 = led.oversight_report()
    assert rep2["risk_entries"] == 1 and rep2["monitoring_reports"] == 1


def test_a_period_that_ends_before_it_starts_is_refused(tmp_path):
    m, led = _fresh(tmp_path)
    with pytest.raises(ValueError, match="until"):
        led.post_market_report(since=100.0, until=50.0)
