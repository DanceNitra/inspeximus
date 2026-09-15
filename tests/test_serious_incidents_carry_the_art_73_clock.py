"""Serious incidents (EU AI Act Art. 73) on the action chain, with the reporting clock.

Controls: a severity outside the Act's cases is refused; evidence that does not exist is refused;
evidence rewritten on disk fails verification; a clock computed from awareness, not from recording;
an incident past its deadline with no reported_ts is listed as overdue and one reported in time is not.
"""
import json
import time

import pytest

from inspeximus import Inspeximus, new_receipt_keypair
from inspeximus.actions import ActionLedger, verify_file, INCIDENT_DEADLINES_DAYS
from inspeximus.compliance import compliance_report

pytest.importorskip("cryptography")

DAY = 86400.0


def _led(tmp_path):
    sk, pk = new_receipt_keypair()
    m = Inspeximus(str(tmp_path / "mem.json"), receipts=True, receipt_key=sk)
    m.remember("the transfer limit is 50", key="limit")
    led = ActionLedger(m, actor="agent")
    m.recall("transfer limit")
    with pytest.raises(RuntimeError):
        with led.action("tool:transfer", inputs={"amt": 900}):
            raise RuntimeError("limit exceeded")
    led.oversight("review", "ops-lead", reason="limit hit", refers_to=0)
    return m, led, pk


def test_an_incident_links_its_evidence_and_carries_the_statutory_clock(tmp_path):
    m, led, pk = _led(tmp_path)
    aware = 1_800_000_000.0
    e = led.incident("transfer above limit reached a customer", "serious", "dpo",
                     description="agent moved 900 with a 50 limit", refers_to=[0, 1], aware_ts=aware)
    assert e["kind"] == "incident" and e["severity"] == "serious"
    assert [r["seq"] for r in e["evidence"]] == [0, 1]
    assert e["report_deadline_ts"] == aware + 15 * DAY and e["report_deadline_days"] == 15
    assert led.verify(expected_pubkey=pk) == (True, [])
    rep = led.incident_report(e["seq"], now=aware + 3 * DAY)
    assert rep["clock"]["days_left"] == 12.0 and rep["clock"]["overdue"] is False
    assert [x["seq"] for x in rep["evidence"]] == [0, 1]
    assert rep["evidence"][0]["oversight"][0]["event"] == "review"
    assert rep["evidence"][0]["memory_state"]["recalled"]


def test_the_deadline_follows_the_severity(tmp_path):
    m, led, pk = _led(tmp_path)
    aware = 1_800_000_000.0
    for sev, days in INCIDENT_DEADLINES_DAYS.items():
        e = led.incident(f"case {sev}", sev, "dpo", aware_ts=aware)
        assert e["report_deadline_ts"] == (aware + days * DAY if days else None)
    with pytest.raises(ValueError):
        led.incident("x", "catastrophic", "dpo")
    with pytest.raises(ValueError):
        led.incident("x", "serious", "")
    with pytest.raises(ValueError):
        led.incident("x", "serious", "dpo", refers_to=[99])


def test_an_incident_past_its_deadline_without_a_report_is_overdue_and_a_reported_one_is_not(tmp_path):
    m, led, pk = _led(tmp_path)
    aware = time.time() - 30 * DAY          # in the past, so the wall-clock report can see the overdue one
    late = led.incident("late", "widespread", "dpo", aware_ts=aware)
    ok = led.incident("reported", "widespread", "dpo", aware_ts=aware, reported_to="MSA", reported_ts=aware + DAY)
    rep_late = led.incident_report(late["seq"], now=aware + 5 * DAY)
    rep_ok = led.incident_report(ok["seq"], now=aware + 5 * DAY)
    assert rep_late["clock"]["overdue"] is True and rep_ok["clock"]["overdue"] is False
    assert rep_ok["clock"]["reported"] is True
    # the ledger-wide report uses the wall clock; both are in the past, so only the unreported one is overdue
    assert led.oversight_report()["incidents_overdue"] == [late["seq"]]


def test_evidence_rewritten_on_disk_fails_verification(tmp_path):
    m, led, pk = _led(tmp_path)
    led.record("tool:other")
    e = led.incident("x", "serious", "dpo", refers_to=[0])
    assert verify_file(led.path)[0]
    data = json.loads(led.path.read_text(encoding="utf-8"))
    data[e["seq"]]["evidence"][0]["seq"] = 2
    led.path.write_text(json.dumps(data), encoding="utf-8")
    ok, problems = verify_file(led.path)
    assert not ok and any("incident evidence does not resolve" in p for p in problems)


def test_a_later_entry_that_refers_to_the_incident_shows_as_an_update(tmp_path):
    m, led, pk = _led(tmp_path)
    e = led.incident("x", "serious", "dpo", refers_to=[0])
    led.oversight("stop", "ops-lead", reason="contain the incident", refers_to=e["seq"])
    rep = led.incident_report(e["seq"])
    assert [u["event"] for u in rep["updates"]] == ["stop"]
    with pytest.raises(ValueError):
        led.incident_report(0)


def test_the_compliance_report_counts_incidents_through_the_verifier(tmp_path):
    m, led, pk = _led(tmp_path)
    r0 = compliance_report(m)
    by = {c["article"]: c for c in r0["controls"]}
    assert by["Art. 73"]["status"] == "available" and len(r0["controls"]) == 21
    led.incident("x", "serious", "dpo", refers_to=[0])
    r1 = compliance_report(m)
    by = {c["article"]: c for c in r1["controls"]}
    assert by["Art. 73"]["status"] == "evidence" and by["Art. 73"]["live_count"] == 1
    assert r1["action_ledger"]["incidents_overdue"] == []
