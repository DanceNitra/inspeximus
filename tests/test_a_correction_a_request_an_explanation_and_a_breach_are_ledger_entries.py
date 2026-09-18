"""EU AI Act Art. 20 (corrective actions), Art. 21 (cooperation with authorities), Art. 86 (explanation
of an individual decision) and GDPR Art. 33 and 34 (breach notification) as ledger evidence.

Art. 20(1) names what a provider does with a non-conforming system and whom it informs, and 20(2)
adds the authority when the system presents a risk; the record carries the action, the parties told
with their dates, and the report lists the Art. 20 parties NOT told rather than judging them. Art.
21(3) puts what an authority receives under confidentiality, so the request record carries references
and hashes, never content. Art. 86(1) gives the person a right to "the role of the AI system in the
decision-making procedure", which is what the chain recorded at the action: its memory state, what
recall returned, the oversight on it. GDPR Art. 33(1) is a 72-hour clock from awareness with reasons
required for a late notification; Art. 34 is the subject communication or the 34(3) reason it was not
made.

Controls: a fresh store reads CAPABILITY for all four rows and EVIDENCE after one artifact each; a
party outside Art. 20, a scope outside Art. 21, an explanation of a non-action, a late notification
without reasons, and an exemption aimed at the authority are each refused; the read-only explanation
moves no row; the chain verifies with all four kinds in it.
"""
from __future__ import annotations

import os
import time

import pytest

from inspeximus import Inspeximus
from inspeximus.actions import (BREACH_DEADLINE_HOURS, CORRECTIVE_ACTIONS, INFORMED_PARTIES, ActionLedger,
                                _content_hash)
from inspeximus.coverage import CAPABILITY, EVIDENCE, coverage

cryptography = pytest.importorskip("cryptography")


def _fresh(tmp_path):
    m = Inspeximus(str(tmp_path / "mem.json"), receipts=True, receipt_key=os.urandom(32).hex())
    return m, ActionLedger(m, actor="agent")


def _row(rep, oid):
    return next(r for r in rep["rows"] if r["id"] == oid)


def test_a_corrective_action_records_who_was_told_and_the_report_lists_who_was_not(tmp_path):
    m, led = _fresh(tmp_path)
    assert _row(coverage(m), "aia-20")["state"] == CAPABILITY
    led.record("tool:send", inputs={"to": "+100"}, status="error", error="stale address", actor="agent")
    inc = led.incident("sent to a stale address", "serious", actor="dpo", refers_to=[0])
    e = led.corrective_action("disable", "ops", "the send tool acts on superseded addresses",
                              refers_to=[inc["seq"]], causes="recall returned the retired value",
                              informed=[{"party": "deployer", "how": "email"}, {"party": "distributor"}],
                              presents_risk=True)
    assert e["kind"] == "corrective" and e["action"] == "corrective:disable" and "sig" in e
    rep = led.corrective_action_report(e["seq"])
    assert rep["action"] == "disable" and rep["causes"] == "recall returned the retired value"
    assert [p["party"] for p in rep["informed"]] == ["deployer", "distributor"]
    assert rep["not_informed"] == ["authorised_representative", "importer", "market_surveillance_authority", "notified_body"]
    assert rep["authority_informed"] is False, "presents_risk without the authority told is visible, not hidden"
    assert rep["evidence"][0]["seq"] == inc["seq"] and rep["evidence"][0]["kind"] == "incident"
    row = _row(coverage(m), "aia-20")
    assert row["state"] == EVIDENCE and row["count"] == 1
    # a later entry that refers to the action shows up in its report
    led.oversight("review", actor="dpo", refers_to=e["seq"])
    assert led.corrective_action_report(e["seq"])["later_entries"][0]["kind"] == "oversight"
    with pytest.raises(ValueError, match="kind"):
        led.corrective_action("apologise", "ops", "x")
    with pytest.raises(ValueError, match="party"):
        led.corrective_action("recall", "ops", "x", informed=[{"party": "the press"}])
    with pytest.raises(ValueError):
        led.corrective_action("recall", "ops", "x", refers_to=[99])
    with pytest.raises(ValueError, match="not a corrective"):
        led.corrective_action_report(0)
    assert CORRECTIVE_ACTIONS == ("conformity", "withdraw", "disable", "recall")
    assert len(INFORMED_PARTIES) == 6 and "market_surveillance_authority" in INFORMED_PARTIES
    assert led.verify()[0]


def test_an_authority_request_carries_references_never_content(tmp_path):
    m, led = _fresh(tmp_path)
    assert _row(coverage(m), "aia-21")["state"] == CAPABILITY
    e = led.authority_request("market surveillance authority SK", "MSA-2026-17", "ops", "both",
                              received_ts=1000.0, provided=[{"item": "audit_bundle.json", "sha256": "ab" * 32},
                                                            {"item": "annex_iv.md"}],
                              provided_ts=2000.0, language="sk")
    assert e["kind"] == "authority" and e["action"] == "authority:both" and "sig" in e
    reqs = led.authority_requests()
    assert len(reqs) == 1 and reqs[0]["reference"] == "MSA-2026-17" and reqs[0]["provided_ts"] == 2000.0
    assert reqs[0]["provided"] == [{"item": "audit_bundle.json", "sha256": "ab" * 32}, {"item": "annex_iv.md", "sha256": None}]
    assert "content" not in str(e)
    row = _row(coverage(m), "aia-21")
    assert row["state"] == EVIDENCE and row["count"] == 1
    with pytest.raises(ValueError, match="scope"):
        led.authority_request("a", "b", "ops", "everything")
    with pytest.raises(ValueError, match="item"):
        led.authority_request("a", "b", "ops", "logs", provided=[{"sha256": "x"}])
    with pytest.raises(ValueError, match="reference"):
        led.authority_request("a", "", "ops", "logs")


def test_an_explanation_is_the_chain_around_one_action_and_is_logged_when_produced(tmp_path):
    m, led = _fresh(tmp_path)
    assert _row(coverage(m), "aia-86")["state"] == CAPABILITY
    rid = m.remember("Alice's limit is 500", key="alice::limit")
    m.recall("Alice limit", k=1)
    a = led.record("decide:credit", inputs={"applicant": "ref-7"}, output={"approved": False}, actor="agent",
                   session="s7", model="m-2026-09", principal="officer-3")
    led.disclosure(session="s7", shown="You are talking to an AI agent")
    led.oversight("override", actor="officer-3", reason="manual review", refers_to=a["seq"])
    led.incident("disputed decision", "other", actor="dpo", refers_to=[a["seq"]])

    doc = led.decision_explanation(a["seq"])                       # read-only
    assert doc["kind"] == "inspeximus.decision_explanation/1"
    assert doc["decision"]["model"] == "m-2026-09" and doc["decision"]["principal"] == "officer-3"
    assert doc["decision"]["signed"] is True and doc["decision"]["session"] == "s7"
    assert doc["role_of_the_system"]["memory_state"]["records"] == 1
    assert [o["event"] for o in doc["oversight"]] == ["override"]
    assert len(doc["disclosures_in_session"]) == 1
    assert [r["kind"] for r in doc["referring_entries"]] == ["incident"]
    assert "ledger_entry" not in doc
    assert _row(coverage(m), "aia-86")["state"] == CAPABILITY, "reading is not producing"

    logged = led.decision_explanation(a["seq"], actor="dpo", subject="ref-7", request_id="EXP-1")
    entry = led.entries()[logged["ledger_entry"]["seq"]]
    assert entry["kind"] == "rights" and entry["action"] == "rights:explanation" and entry["event"] == "explanation"
    assert entry["subject"] == "ref-7" and entry["request_id"] == "EXP-1"
    unlogged = dict(logged)
    unlogged.pop("ledger_entry")
    assert entry["manifest_sha256"] == _content_hash(unlogged)
    row = _row(coverage(m), "aia-86")
    assert row["state"] == EVIDENCE and row["count"] == 1
    with pytest.raises(ValueError, match="not an action"):
        led.decision_explanation(entry["seq"])
    assert led.verify()[0]


def test_a_breach_has_a_72_hour_clock_and_a_late_notification_needs_its_reasons(tmp_path):
    m, led = _fresh(tmp_path)
    assert _row(coverage(m), "gdpr-33")["state"] == CAPABILITY
    aware = time.time() - 100 * 3600
    b = led.breach("export to the wrong subject", "dpo", "a subject export was sent to another subject",
                   aware_ts=aware, subjects_approx=1, records_approx=12, categories=["contact data"],
                   consequences="the second subject can read the first subject's records",
                   measures="export recalled; the recipient confirmed deletion", high_risk=True, contact="dpo@example")
    assert b["kind"] == "breach" and b["action"] == "breach:opened" and "sig" in b
    assert b["notify_deadline_ts"] == pytest.approx(aware + 72 * 3600), "Art. 33(1): 72 hours, written as the number"
    assert BREACH_DEADLINE_HOURS == 72
    row = _row(coverage(m), "gdpr-33")
    assert row["state"] == EVIDENCE and row["count"] == 1
    fifty = led.breach("fifty hours old", "dpo", "inside the window", aware_ts=time.time() - 50 * 3600)
    assert led.breach_report(fifty["seq"])["authority"]["overdue"] is False

    rep = led.breach_report(b["seq"])
    assert rep["authority"]["notified_ts"] is None and rep["authority"]["overdue"] is True
    assert rep["subjects"] == {"event": None, "high_risk": True, "required": True}
    assert rep["article_33_3"]["subjects_approx"] == 1 and rep["article_33_3"]["contact"] == "dpo@example"
    assert rep["documentation_33_5"]["remedial_action"].startswith("export recalled")

    with pytest.raises(ValueError, match="reasons_for_delay"):
        led.breach_notified(b["seq"], "dpo", "supervisory_authority")
    n = led.breach_notified(b["seq"], "dpo", "supervisory_authority", reasons_for_delay="forensic image took four days")
    assert n["event"] == "notified" and n["late"] is True and n["action"] == "breach:notified"
    with pytest.raises(ValueError, match="data_subjects only"):
        led.breach_notified(b["seq"], "dpo", "supervisory_authority", exemption="mitigated")
    x = led.breach_notified(b["seq"], "dpo", "data_subjects", exemption="mitigated")
    assert x["event"] == "subjects_exempt"
    rep = led.breach_report(b["seq"])
    assert rep["authority"]["late"] is True and rep["authority"]["reasons_for_delay"].startswith("forensic")
    assert rep["subjects"]["event"] == "subjects_exempt" and rep["subjects"]["exemption"] == "mitigated"
    assert rep["documentation_33_5"]["updates"] == 2
    assert led.oversight_report()["breaches"] == 2 and led.oversight_report()["breaches_overdue"] == []
    pm = led.post_market_report(since=0)
    assert pm["breaches"] == {"opened": 2, "notified_late": 1} and "GDPR Art. 33" in pm["requirements"]

    # a breach notified inside the window carries no late flag and needs no reasons
    b2 = led.breach("second", "dpo", "a log line held a name")
    n2 = led.breach_notified(b2["seq"], "dpo", "supervisory_authority")
    assert n2["late"] is False
    with pytest.raises(ValueError, match="to must be"):
        led.breach_notified(b2["seq"], "dpo", "the press")
    with pytest.raises(ValueError, match="not a breach"):
        led.breach_notified(n2["seq"], "dpo", "public")
    with pytest.raises(ValueError, match="nature"):
        led.breach("x", "dpo", "")
    assert led.verify()[0]


def test_an_overdue_breach_is_named_in_the_oversight_report(tmp_path):
    m, led = _fresh(tmp_path)
    b = led.breach("late one", "dpo", "unnoticed for a week", aware_ts=time.time() - 8 * 86400)
    led.breach("fresh one", "dpo", "noticed now")
    assert led.oversight_report()["breaches_overdue"] == [b["seq"]]
