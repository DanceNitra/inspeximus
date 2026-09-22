"""3.6.0: the two rows that carried a "partial" footnote close.

Art. 15: the echo, poisoning and split-view measurements are carried into compliance_report() as
dated evidence rows, each with the sha256 of its receipt. The control: a receipt edited after the
evidence was packaged reads STALE, on the row and on the Art. 15 control, and the coverage probe
for the row goes to 0.

Art. 17: a `qms` ledger entry per procedure (name, version, owner, review date, the Art. 17(1)
aspect), `qms_register()` with the current one per procedure and the overdue ones named, listed in
the deployer report. The control: a review date in the past is reported overdue.
"""
import json
import os
import shutil
import time

import pytest

from inspeximus import Inspeximus
from inspeximus.actions import ActionLedger, QMS_ASPECTS
from inspeximus.compliance import compliance_report, robustness_evidence
from inspeximus.coverage import coverage

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROBES = os.path.join(ROOT, "probes")


def _store(tmp_path):
    m = Inspeximus(str(tmp_path / "s.json"), receipts=True)
    m.remember("the deploy region is eu-central-1", key="region", object="eu-central-1")
    return m


# ------------------------------------------------------------------ Art. 15
def test_the_packaged_evidence_rows_match_the_receipts_in_this_tree():
    ev = robustness_evidence(PROBES)
    assert [r["id"] for r in ev["rows"]] == ["echo", "poison", "split_view"]
    assert all(r["status"] == "verified" for r in ev["rows"]), ev["rows"]
    assert ev["stale"] == []
    for r in ev["rows"]:
        assert os.path.exists(os.path.join(ROOT, r["probe"])), r["probe"]
        assert len(r["receipt_sha256"]) == 64 and r["measured_at"] != "uncommitted"
        assert isinstance(r["value"], (int, float)) and r["metric"] and r["summary"]
    # the numbers are read from the receipts, never typed: check two against their source
    echo = json.load(open(os.path.join(PROBES, "echo_policy_panel_result.json"), encoding="utf-8"))
    assert ev["rows"][0]["value"] == next(x for x in echo["rows"] if x["policy"] == "(default)")["echo_blocked"]
    split = json.load(open(os.path.join(PROBES, "a_split_view_is_detected_and_an_honest_pair_is_not.result.json"),
                           encoding="utf-8"))
    assert split["verdict"] == "DETECTED" and split["forked_pair"]["fork"] is True
    assert split["honest_pair_control"]["fork"] is False, "the control must not fire on an honest pair"


def test_the_report_carries_the_rows_on_the_art_15_control(tmp_path):
    rep = compliance_report(_store(tmp_path), probes_dir=PROBES)
    assert rep["robustness_evidence"]["stale"] == []
    c15 = next(c for c in rep["controls"] if c["article"] == "Art. 15" and c["framework"].startswith("EU AI Act"))
    assert [r["id"] for r in c15["robustness_evidence"]] == ["echo", "poison", "split_view"]
    assert all(r["status"] == "verified" for r in c15["robustness_evidence"])
    assert c15["status"] != "STALE_EVIDENCE"
    gdpr15 = next(c for c in rep["controls"] if c["article"] == "Art. 15" and c["framework"].startswith("GDPR"))
    assert "robustness_evidence" not in gdpr15, "the rows belong to the AI Act article, not the GDPR one"


def test_a_mutated_receipt_reads_stale_on_the_row_the_control_and_the_coverage_probe(tmp_path):
    """THE CONTROL. Copy the probes directory, change one byte of one receipt, and every surface that
    carries the number must say so."""
    copy = tmp_path / "probes"
    copy.mkdir()
    for r in robustness_evidence(PROBES)["rows"]:
        shutil.copy(os.path.join(ROOT, r["receipt"]), copy / os.path.basename(r["receipt"]))
    target = copy / "agentpoison_influence_gate_result.json"
    data = json.load(open(target, encoding="utf-8"))
    data[0]["influence_hijack"] = 0.5
    json.dump(data, open(target, "w", encoding="utf-8"))
    ev = robustness_evidence(str(copy))
    assert ev["stale"] == ["poison"], ev["stale"]
    stale = next(r for r in ev["rows"] if r["id"] == "poison")
    assert stale["status"] == "STALE" and stale["receipt_sha256_now"] != stale["receipt_sha256"]
    assert next(r for r in ev["rows"] if r["id"] == "echo")["status"] == "verified"
    rep = compliance_report(_store(tmp_path), probes_dir=str(copy))
    c15 = next(c for c in rep["controls"] if c["article"] == "Art. 15" and c["framework"].startswith("EU AI Act"))
    assert c15["status"] == "STALE_EVIDENCE"
    assert next(r for r in c15["robustness_evidence"] if r["id"] == "poison")["status"] == "STALE"


def test_an_installed_wheel_with_no_source_tree_reports_the_rows_as_packaged(tmp_path):
    ev = robustness_evidence(str(tmp_path / "nowhere"))
    assert len(ev["rows"]) == 3 and all(r["status"] == "packaged" for r in ev["rows"])
    assert ev["stale"] == []


def test_the_coverage_row_for_art_15_is_evidence_and_has_no_footnote(tmp_path):
    rows = {r["id"]: r for r in coverage(_store(tmp_path))["rows"]}
    assert rows["aia-15"]["state"] == "EVIDENCE" and "partial" not in rows["aia-15"]
    assert "3 robustness evidence row(s)" in rows["aia-15"]["detail"] and "verified" in rows["aia-15"]["detail"]
    fresh = {r["id"]: r for r in coverage(Inspeximus(str(tmp_path / "fresh.json")))["rows"]}
    assert fresh["aia-15"]["state"] == "CAPABILITY", "the rows describe the library; a fresh store holds no evidence"
    assert "robustness_evidence" in rows["aia-15"]["artifact"]


def test_the_coverage_probe_for_art_15_goes_to_zero_on_a_stale_receipt(tmp_path, monkeypatch):
    """The REAL probe, pointed at a copy of the receipts with one byte changed (INSPEXIMUS_PROBES_DIR is
    the operator's knob and the test's handle). The first version of this test stubbed the probe with a
    lambda that returned 0, so the branch under test never ran and the mutation that drops it survived."""
    copy = tmp_path / "probes"
    copy.mkdir()
    for r in robustness_evidence(PROBES)["rows"]:
        shutil.copy(os.path.join(ROOT, r["receipt"]), copy / os.path.basename(r["receipt"]))
    target = copy / "echo_policy_panel_result.json"
    data = json.load(open(target, encoding="utf-8"))
    data["rows"][0]["echo_blocked"] = 0.0
    json.dump(data, open(target, "w", encoding="utf-8"))
    monkeypatch.setenv("INSPEXIMUS_PROBES_DIR", str(copy))
    rows = {r["id"]: r for r in coverage(_store(tmp_path))["rows"]}
    assert rows["aia-15"]["state"] != "EVIDENCE" and "STALE: echo" in rows["aia-15"]["detail"], rows["aia-15"]
    monkeypatch.delenv("INSPEXIMUS_PROBES_DIR")
    rows = {r["id"]: r for r in coverage(_store(tmp_path))["rows"]}
    assert rows["aia-15"]["state"] == "EVIDENCE", "the control: the committed receipts read verified"


# ------------------------------------------------------------------ Art. 17
def test_a_qms_entry_is_recorded_signed_and_the_register_names_the_overdue_one(tmp_path):
    m = _store(tmp_path)
    led = ActionLedger(m, actor="ops")
    now = time.time()
    e = led.record_qms("dpo", "incident reporting", "1.2", "ops lead", now + 86400 * 90, aspect="i",
                       ref="qms/incident-reporting-v1.2.pdf", sha256="a" * 64)
    assert e["kind"] == "qms" and e["procedure"] == "incident reporting" and e["aspect"] == "i"
    led.record_qms("dpo", "change control", "0.9", "cto", now - 1, aspect="a")
    led.record_qms("dpo", "change control", "1.0", "cto", now + 86400 * 30, aspect="a")   # a later entry is current
    reg = led.qms_register(now=now)
    assert reg["procedures"] == 2 and reg["entries"] == 3
    assert reg["overdue"] == []
    current = {r["procedure"]: r for r in reg["rows"]}
    assert current["change control"]["version"] == "1.0" and current["change control"]["overdue"] is False
    assert reg["aspects_covered"] == ["a", "i"] and len(reg["aspects_uncovered"]) == len(QMS_ASPECTS) - 2
    # the control: a review date in the past is overdue, by name
    led.record_qms("dpo", "data management", "2.0", "data lead", now - 3600, aspect="f")
    reg2 = led.qms_register(now=now)
    assert reg2["overdue"] == ["data management"]
    assert next(r for r in reg2["rows"] if r["procedure"] == "data management")["overdue"] is True
    ok, problems = led.verify()
    assert ok is True, problems


def test_a_qms_entry_refuses_a_missing_owner_a_bad_aspect_and_a_bad_sha(tmp_path):
    led = ActionLedger(_store(tmp_path), actor="ops")
    with pytest.raises(ValueError):
        led.record_qms("dpo", "x", "1", "", time.time())
    with pytest.raises(ValueError):
        led.record_qms("dpo", "x", "1", "o", time.time(), aspect="z")
    with pytest.raises(ValueError):
        led.record_qms("dpo", "x", "1", "o", time.time(), sha256="nothex")
    with pytest.raises(ValueError):
        led.record_qms("dpo", "x", "1", "o", "not a time")


def test_the_deployer_report_lists_the_register_and_coverage_closes_the_row(tmp_path):
    from inspeximus.deployer import deployer_report
    m = _store(tmp_path)
    led = ActionLedger(m, actor="ops")
    led.record_qms("dpo", "incident reporting", "1.2", "ops lead", time.time() - 10, aspect="i")
    rep = deployer_report(m, led)
    duty = json.dumps(rep)
    assert "17_quality_management_register" in duty
    sec = json.loads(duty)
    found = None
    def walk(o):
        nonlocal found
        if isinstance(o, dict):
            if "17_quality_management_register" in o:
                found = o["17_quality_management_register"]
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)
    walk(sec)
    assert found and found["evidence"]["procedures"] == 1 and found["evidence"]["overdue"] == ["incident reporting"]
    rows = {r["id"]: r for r in coverage(m)["rows"]}
    assert rows["aia-17"]["state"] == "EVIDENCE" and "partial" not in rows["aia-17"]
    assert "1 overdue" in rows["aia-17"]["detail"]


def test_the_mcp_tools_and_the_ledger_counts_carry_qms(tmp_path, monkeypatch):
    pytest.importorskip("mcp")
    import importlib
    monkeypatch.setenv("INSPEXIMUS_PATH", str(tmp_path / "mcp.json"))
    srv = importlib.reload(importlib.import_module("inspeximus.mcp_server"))
    r = srv.record_qms("dpo", "incident reporting", "1.2", "ops lead", time.time() + 3600, aspect="i")
    assert r["seq"] == 0 and r["procedure"] == "incident reporting"
    assert srv.record_qms("dpo", "x", "1", "o", time.time(), aspect="q")["error"]
    reg = srv.qms_register()
    assert reg["procedures"] == 1 and reg["overdue"] == []
    led = ActionLedger(srv._MEM)
    pm = led.post_market_report(since=time.time() - 3600)
    assert pm["qms_procedures"] == 1 and pm["requirements"]["Art. 17"] == "qms_procedures"


# ------------------------------------------------------------------ the chain accounts for an out-of-band deletion
def test_an_out_of_band_deletion_is_declared_and_the_chain_reads_accounted_for(tmp_path):
    """Measured on the Crew OS store 2026-09-22: two records removed with a raw SQL DELETE read as
    'deleted out-of-band' forever, and forget() on a gone id wrote no tombstone."""
    import sqlite3
    path = str(tmp_path / "s.json")
    m = Inspeximus(path, receipts=True)
    keep = m.remember("kept", key="k1", object="a")
    gone = m.remember("removed outside the api", key="k2", object="b")
    m.flush()
    if not m._rows_available():
        pytest.skip("row store only")
    con = sqlite3.connect(path, isolation_level=None)
    con.execute("DELETE FROM records WHERE id=?", (gone,)); con.close()
    fresh = Inspeximus(path, receipts=True)
    ok, problems = fresh.verify_writes()
    assert ok is False and any(gone in p and "out-of-band" in p for p in problems), problems
    assert fresh.forget(gone)["tombstones"] == 0, "forget() on a gone id writes nothing, which is the gap"
    with pytest.raises(ValueError):
        fresh.declare_out_of_band_deletion(keep, "ops", "still present")
    with pytest.raises(ValueError):
        fresh.declare_out_of_band_deletion("ffffffffff", "ops", "no receipt names it")
    with pytest.raises(ValueError):
        fresh.declare_out_of_band_deletion(gone, "", "no actor")
    res = fresh.declare_out_of_band_deletion(gone, "ops", "raw SQL DELETE on 2026-09-21")
    assert res["declared"] is True
    ok, problems = fresh.verify_writes()
    assert ok is True, problems
    t = next(t for t in fresh._tombstones if t["memory_id"] == gone)
    assert t["auth"]["basis"].startswith("out_of_band:") and t["auth"]["authorized_by"] == "ops"
    assert fresh.declare_out_of_band_deletion(gone, "ops", "again")["declared"] is False
    again = Inspeximus(path, receipts=True)
    assert again.verify_writes()[0] is True, "the tombstone is on disk"
    assert again.current("k1")["text"] == "kept"


def test_the_receipt_sha_is_the_same_under_lf_and_crlf_checkouts(tmp_path):
    """CI checks the receipts out with LF and a Windows checkout with autocrlf holds CRLF. The first
    3.6.0 tag hashed the raw bytes, so three of three rows read STALE on Linux while every local run
    read verified. The hash is over LF-normalised bytes on both sides, and this pins it."""
    lf = tmp_path / "lf"
    crlf = tmp_path / "crlf"
    lf.mkdir()
    crlf.mkdir()
    for r in robustness_evidence(PROBES)["rows"]:
        raw = open(os.path.join(ROOT, r["receipt"]), "rb").read()
        norm = raw.replace(b"\r\n", b"\n")
        (lf / os.path.basename(r["receipt"])).write_bytes(norm)
        (crlf / os.path.basename(r["receipt"])).write_bytes(norm.replace(b"\n", b"\r\n"))
    assert all(x["status"] == "verified" for x in robustness_evidence(str(lf))["rows"])
    assert all(x["status"] == "verified" for x in robustness_evidence(str(crlf))["rows"])
