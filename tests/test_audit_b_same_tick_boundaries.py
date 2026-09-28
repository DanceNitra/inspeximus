"""Timestamps that tie: A-35 and A-36 (PC2), with the clock fixed so they fail on every OS.

PC2 saw both as flakes: a coarse clock put two events in one tick some of the time. A fixed `time.time`
puts them there every time, which turns a 2-in-6 flake into a deterministic defect, Linux included.

A-36: post_market_report(until=None) became `time.time()` under a strict `since <= ts < until`, so an
entry written in the tick of the call was dropped from every section. A-35: certificate_drift ordered two
certificates with a strict `>` on issue time, so a same-tick pair was left in argument order.
"""
import os
import time

import pytest

from inspeximus import Inspeximus
from inspeximus.actions import PROHIBITED_PRACTICES, ActionLedger
from inspeximus.erasure_residue import certificate_drift, residue_certificate
from inspeximus.subject_rights import record_objection, resolve_objection

cryptography = pytest.importorskip("cryptography")

T = 1_790_000_000.0


@pytest.fixture(autouse=True)
def _no_env(monkeypatch):
    for k in [k for k in os.environ if k.startswith("INSPEXIMUS_")]:
        monkeypatch.delenv(k)


def _one_of_each(tmp_path):
    """One entry in each section PC2 found short: oversight, breaches, attestations, objections, QMS."""
    m = Inspeximus(str(tmp_path / "mem.json"), receipts=True, receipt_key=os.urandom(32).hex())
    led = ActionLedger(m, actor="agent")
    led.oversight("refuse", actor="dpo")
    led.breach("a log line held a name", "dpo", "confidentiality")
    led.record_attestation("ops", sorted(PROHIBITED_PRACTICES)[0], "not_used")
    m.remember("Alice prefers the blue dashboard", key="alice-pref", source={"doc": "crm/alice"})
    record_objection(m, "crm/alice", "dpo", "own_situation", ledger=led)
    resolve_objection(m, "crm/alice", "dpo", "upheld", ledger=led)
    led.record_qms("dpo", "incident reporting", "1.2", "ops lead", T + 3600, aspect="i")
    return m, led


def _sections(pm):
    return {"oversight": pm["oversight"]["total"], "breaches": pm["breaches"]["opened"],
            "attestations": pm["attestations"]["entries"], "objections": pm["objections"],
            "qms": pm["qms_procedures"]}


EXPECTED = {"oversight": 1, "breaches": 1, "attestations": 1,
            "objections": {"recorded": 1, "resolved": 1}, "qms": 1}


def test_a_report_up_to_now_counts_what_was_written_in_the_same_tick(tmp_path, monkeypatch):
    monkeypatch.setattr(time, "time", lambda: T)
    m, led = _one_of_each(tmp_path)
    assert {e["ts"] for e in led.entries()} == {T}, "control: every entry sits in the tick of the call"
    pm = led.post_market_report(since=T - 3600)
    assert _sections(pm) == EXPECTED
    assert pm["period"]["open_ended"] is True


def test_an_explicit_until_stays_half_open(tmp_path, monkeypatch):
    """Consecutive periods must cover each entry exactly once, so an entry AT `until` belongs to the next
    period. Only the open end changed."""
    monkeypatch.setattr(time, "time", lambda: T)
    m, led = _one_of_each(tmp_path)
    before = led.post_market_report(since=T - 3600, until=T)
    after = led.post_market_report(since=T, until=T + 1)
    assert _sections(before) == {"oversight": 0, "breaches": 0, "attestations": 0,
                                 "objections": {"recorded": 0, "resolved": 0}, "qms": 0}
    assert _sections(after) == EXPECTED
    assert before["period"]["open_ended"] is False


def test_the_mcp_tool_counts_the_same_tick_too(tmp_path, monkeypatch):
    pytest.importorskip("mcp")
    import importlib
    monkeypatch.setattr(time, "time", lambda: T)
    monkeypatch.setenv("INSPEXIMUS_PATH", str(tmp_path / "mcp.json"))
    srv = importlib.reload(importlib.import_module("inspeximus.mcp_server"))
    assert srv.record_qms("dpo", "incident reporting", "1.2", "ops lead", T + 3600, aspect="i")["seq"] == 0
    pm = srv.post_market_report(since=T - 3600)
    assert pm["qms_procedures"] == 1, pm.get("qms_procedures")


# ── A-35 ──────────────────────────────────────────────────────────────────────────────────────────────

SECRET = "alice.secret@example.org"


def _cert(d):
    return residue_certificate(str(d), [SECRET], root_label="store")


def _pair(tmp_path, monkeypatch, t_before, t_after):
    d = tmp_path / "store"
    d.mkdir()
    (d / "notes.txt").write_text("nothing here\n", encoding="utf-8")
    monkeypatch.setattr(time, "time", lambda: t_before)
    before = _cert(d)
    (d / "leak.log").write_text(SECRET, encoding="utf-8")
    monkeypatch.setattr(time, "time", lambda: t_after)
    after = _cert(d)
    return before, after


def test_same_tick_certificates_give_one_answer_and_say_the_order_is_undetermined(tmp_path, monkeypatch):
    before, after = _pair(tmp_path, monkeypatch, T, T)
    assert before["issued_ts"] == after["issued_ts"] == T, "control: both issued in one tick"
    a, b = certificate_drift(before, after), certificate_drift(after, before)
    assert a == b, "the answer depended on argument order"
    assert a["comparable"] is False
    assert any("same issue time" in p for p in a["problems"])


def test_certificates_issued_apart_are_ordered_by_time_either_way_round(tmp_path, monkeypatch):
    """The control: with distinct issue times the direction is known, and it does not depend on position."""
    before, after = _pair(tmp_path, monkeypatch, T, T + 60)
    a, b = certificate_drift(after, before), certificate_drift(before, after)
    assert a == b and a["comparable"] is True
    assert a["clean_to_dirty"] is True and a["added"] == ["leak.log"]
