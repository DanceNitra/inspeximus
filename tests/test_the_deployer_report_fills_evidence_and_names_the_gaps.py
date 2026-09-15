"""The deployer report (Art. 26) with the DPIA (GDPR Art. 35(7)) and FRIA (Art. 27(1)) appendices.

Controls: a field the deployer did not supply is marked, never invented; the six-month log floor is
'not yet testable' on a young log and 'floor_observed' only once the oldest entry is old enough; the
observed period comes from the ledger and an empty ledger says so; a rewritten ledger shows as not
verified in the DPIA measures; the FRIA cross-references the DPIA sections it shares evidence with;
the CLI subcommand is real and writes the file it names.
"""
import json
import time

import pytest

from inspeximus import Inspeximus, new_receipt_keypair
from inspeximus.actions import ActionLedger
from inspeximus.deployer import (deployer_report, dpia_appendix, fria_appendix, render_markdown,
                                 DEPLOYER_FIELDS, DPIA_FIELDS, FRIA_FIELDS, SIX_MONTHS_DAYS)
from inspeximus.technical_documentation import OPERATOR_INPUT

pytest.importorskip("cryptography")


def _store(tmp_path):
    sk, pk = new_receipt_keypair()
    m = Inspeximus(str(tmp_path / "mem.json"), receipts=True, receipt_key=sk)
    m.remember("the limit is 50", key="limit", source={"doc": "crm/a"})
    m.remember("alice phone is +100", key="alice::phone", source={"doc": "crm/alice"})
    led = ActionLedger(m, actor="agent")
    with led.action("tool:x") as a:
        a.output(1)
    with pytest.raises(RuntimeError):
        with led.action("tool:y"):
            raise RuntimeError("boom")
    led.oversight("review", "dpo", refers_to=0)
    led.disclosure("s1", "You are chatting with an AI assistant.", channel="web")
    led.incident("transfer above limit", "serious", "dpo", refers_to=[0])
    m.forget_subject("crm/alice", request_id="DSAR-1")
    return m, led, pk


def test_operator_fields_are_marked_not_invented_and_supplied_ones_are_used(tmp_path):
    m, led, pk = _store(tmp_path)
    doc = deployer_report(m, ledger=led, operator={"deployer": "Acme GmbH", "purposes": "support triage",
                                                   "fria_required": "no: private entity, no public service"})
    d = doc["sections"]["1_deployer_duties_art_26"]
    assert d["26_1_use_per_instructions"]["operator"]["deployer"] == "Acme GmbH"
    assert d["26_1_use_per_instructions"]["operator"]["system_name"] == OPERATOR_INPUT
    dpia = doc["sections"]["2_appendix_dpia_gdpr_art_35_7"]
    assert dpia["35_7_a_description_of_processing"]["operator"]["purposes"] == "support triage"
    fria = doc["sections"]["3_appendix_fria_art_27_1"]
    assert fria["applicability"]["operator"].startswith("no:")
    expected_missing = (set(DEPLOYER_FIELDS) | set(DPIA_FIELDS) | set(FRIA_FIELDS)) - {"deployer", "purposes", "fria_required"}
    assert set(doc["operator_fields_missing"]) == expected_missing
    assert doc["operator_fields_total"] == len(DEPLOYER_FIELDS) + len(DPIA_FIELDS) + len(FRIA_FIELDS)
    assert json.dumps(doc, default=str).count(OPERATOR_INPUT) >= 20


def test_the_six_month_floor_is_not_claimed_on_a_young_log(tmp_path):
    m, led, pk = _store(tmp_path)
    first = led.entries()[0]["ts"]
    young = deployer_report(m, ledger=led, now=first + 10 * 86400)
    ev = young["sections"]["1_deployer_duties_art_26"]["26_6_log_retention"]["evidence"]
    assert ev["status"] == "not_yet_testable" and ev["six_month_floor_days"] == SIX_MONTHS_DAYS
    assert 9.9 < ev["oldest_entry_age_days"]["action_ledger"] < 10.1
    # CONTROL: the same ledger read once it is old enough
    old = deployer_report(m, ledger=led, now=first + (SIX_MONTHS_DAYS + 1) * 86400)
    assert old["sections"]["1_deployer_duties_art_26"]["26_6_log_retention"]["evidence"]["status"] == "floor_observed"
    # CONTROL: no receipts and no ledger is 'no_log', never a status about a floor
    plain = Inspeximus(str(tmp_path / "plain.json"))
    plain.remember("x", key="k")
    none = deployer_report(plain)["sections"]["1_deployer_duties_art_26"]["26_6_log_retention"]["evidence"]
    assert none["status"] == "no_log"


def test_the_observed_period_comes_from_the_ledger(tmp_path):
    m, led, pk = _store(tmp_path)
    fria = fria_appendix(m, ledger=led)
    obs = fria["27_1_b_period_and_frequency"]["evidence_observed"]
    assert obs["observed"] is True and obs["actions"] == 2 and obs["errors"] == 1
    assert obs["by_actor"] == {"agent": 2}
    assert fria["27_1_b_period_and_frequency"]["operator_intended"] == OPERATOR_INPUT
    # CONTROL: a ledger with no actions reports no period rather than a zero rate
    m2 = Inspeximus(str(tmp_path / "m2.json"), receipts=True)
    led2 = ActionLedger(m2)
    led2.oversight("review", "dpo")
    assert fria_appendix(m2, ledger=led2)["27_1_b_period_and_frequency"]["evidence_observed"]["observed"] is False
    assert fria_appendix(m2)["27_1_b_period_and_frequency"]["evidence_observed"]["observed"] is False


def test_oversight_incidents_and_rights_flow_into_the_duties_and_the_fria(tmp_path):
    m, led, pk = _store(tmp_path)
    doc = deployer_report(m, ledger=led, expected_pubkey=pk)
    d = doc["sections"]["1_deployer_duties_art_26"]
    ov = d["26_2_human_oversight_assigned"]["evidence"]
    assert ov["by_event"] == {"review": 1} and ov["by_actor"] == {"dpo": 1}
    assert ov["error_actions_without_oversight"] == [1]          # tool:y failed and nobody reviewed it
    inc = d["26_5_monitoring_and_incidents"]["evidence"]
    assert inc["incidents"] == 1 and inc["overdue"] == [] and inc["rows"][0]["severity"] == "serious"
    assert d["26_11_persons_informed"]["evidence"]["disclosures_recorded"]["sessions_disclosed"] == {"s1": ["interaction"]}
    fria = doc["sections"]["3_appendix_fria_art_27_1"]
    assert fria["27_1_e_human_oversight_implementation"]["evidence_recorded"]["oversight_events"] == 1
    assert fria["27_1_f_measures_on_materialisation"]["evidence"]["incidents"]["incidents"] == 1
    # the clock: read 16 days after awareness the incident is overdue
    aware = [e for e in led.entries() if e.get("kind") == "incident"][0]["aware_ts"]
    late = deployer_report(m, ledger=led, now=aware + 16 * 86400)
    assert late["sections"]["1_deployer_duties_art_26"]["26_5_monitoring_and_incidents"]["evidence"]["overdue"]


def test_the_fria_cross_references_the_dpia_and_the_dpia_carries_the_erasures(tmp_path):
    m, led, pk = _store(tmp_path)
    doc = deployer_report(m, ledger=led)
    fria = doc["sections"]["3_appendix_fria_art_27_1"]
    for sec, target in (("27_1_c_affected_persons_and_groups", "35_7_a_description_of_processing"),
                        ("27_1_d_specific_risks_of_harm", "35_7_c_risks_to_rights_and_freedoms"),
                        ("27_1_f_measures_on_materialisation", "35_7_d_measures")):
        ref = fria[sec]["dpia_cross_reference"]
        assert ref.startswith("appendix_dpia." + target) and "27(4)" in ref
        assert target in doc["sections"]["2_appendix_dpia_gdpr_art_35_7"]      # the target exists
    dpia = doc["sections"]["2_appendix_dpia_gdpr_art_35_7"]
    inv = dpia["35_7_a_description_of_processing"]["evidence_inventory_of_this_store"]
    assert inv["erasures"] == {"tombstoned_total": 1, "by_request": ["DSAR-1"]}
    assert "+100" not in json.dumps(doc)                                       # no record text, no erased content
    assert dpia["35_7_d_measures"]["evidence"]["integrity"]["memory_chain_verified"] is True


def test_a_rewritten_ledger_shows_as_not_verified_in_the_dpia_measures(tmp_path):
    m, led, pk = _store(tmp_path)
    data = json.loads(led.path.read_text(encoding="utf-8"))
    data[0]["action"] = "tool:z"
    led.path.write_text(json.dumps(data), encoding="utf-8")
    led.reload()
    integ = dpia_appendix(m, ledger=led)["35_7_d_measures"]["evidence"]["integrity"]
    assert integ["action_ledger_verified"] is False and integ["action_ledger_problems"]


def test_the_content_hash_moves_with_the_store_and_the_cli_writes_the_file(tmp_path, monkeypatch, capsys):
    m, led, pk = _store(tmp_path)
    a = deployer_report(m, ledger=led, now=1.0)["content_sha256"]
    m.remember("the limit is 60", key="limit")
    assert deployer_report(m, ledger=led, now=1.0)["content_sha256"] != a
    md = render_markdown(deployer_report(m, ledger=led))
    assert md.startswith("# Deployer report (Art. 26)") and "## 2. Appendix dpia gdpr art 35 7" in md
    from inspeximus import cli
    src = open(cli.__file__, encoding="utf-8").read()
    assert '"deployer-report"' in src
    out = tmp_path / "deployer.md"
    op = tmp_path / "op.json"
    op.write_text(json.dumps({"deployer": "Acme GmbH"}), encoding="utf-8")
    monkeypatch.setenv("INSPEXIMUS_PATH", str(m.path))
    import sys
    monkeypatch.setattr(sys, "argv", ["inspeximus", "--path", str(m.path), "deployer-report",
                                      "--out", str(out), "--operator", str(op)])
    try:
        cli.main()
    except SystemExit as e:
        assert e.code in (0, None)
    text = out.read_text(encoding="utf-8")
    assert "Acme GmbH" in text and OPERATOR_INPUT in text
    assert "deployer report" in capsys.readouterr().out
