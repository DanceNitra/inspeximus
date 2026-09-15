"""Annex IV technical documentation (Art. 11) and instructions for use (Art. 13) from the evidence.

Controls: a field the provider did not supply is marked, never invented; a rewritten action ledger
shows as not verified in section 2(g); the content hash changes when the store changes; the verifier
commands the instructions name are real CLI subcommands.
"""
import json

import pytest

from inspeximus import Inspeximus, new_receipt_keypair
from inspeximus.actions import ActionLedger
from inspeximus.technical_documentation import annex_iv, instructions_for_use, render_markdown, OPERATOR_FIELDS, OPERATOR_INPUT

pytest.importorskip("cryptography")


def _store(tmp_path):
    sk, pk = new_receipt_keypair()
    m = Inspeximus(str(tmp_path / "mem.json"), receipts=True, receipt_key=sk)
    m.remember("the limit is 50", key="limit", source={"doc": "crm/a"})
    led = ActionLedger(m, actor="agent")
    m.recall("limit")
    with led.action("tool:x") as a:
        a.output(1)
    led.oversight("review", "dpo", refers_to=0)
    return m, led, pk


def test_operator_fields_are_marked_not_invented_and_supplied_ones_are_used(tmp_path):
    m, led, pk = _store(tmp_path)
    doc = annex_iv(m, ledger=led, operator={"system_name": "Support agent", "provider": "Acme GmbH"})
    s1 = doc["sections"]["1_general_description"]["a_intended_purpose_provider_version"]
    assert s1["system_name"] == "Support agent" and s1["provider"] == "Acme GmbH"
    assert s1["intended_purpose"] == OPERATOR_INPUT
    assert set(doc["operator_fields_missing"]) == set(OPERATOR_FIELDS) - {"system_name", "provider"}
    assert doc["operator_fields_total"] == len(OPERATOR_FIELDS)
    text = json.dumps(doc, default=str)
    assert "Acme GmbH" in text and text.count(OPERATOR_INPUT) >= 20


def test_the_evidence_sections_come_from_the_store_and_ledger(tmp_path):
    m, led, pk = _store(tmp_path)
    doc = annex_iv(m, ledger=led, expected_pubkey=pk)
    s2 = doc["sections"]["2_elements_and_development"]
    assert s2["e_human_oversight_measures"]["oversight_events_recorded"]["by_event"] == {"review": 1}
    assert s2["g_validation_and_testing"]["action_ledger_verified"] is True
    assert s2["g_validation_and_testing"]["memory_chain_verified"] is True
    assert s2["d_data_requirements"]["memory_records"]["total"] == 1
    assert s2["h_cybersecurity_measures"]["evidence"]["anchor"]["n_writes"] == 1
    assert len(doc["sections"]["3_monitoring_functioning_control"]["controls_report"]["controls"]) == 21


def test_a_rewritten_ledger_shows_as_not_verified_in_the_document(tmp_path):
    m, led, pk = _store(tmp_path)
    data = json.loads(led.path.read_text(encoding="utf-8"))
    data[0]["action"] = "tool:z"
    led.path.write_text(json.dumps(data), encoding="utf-8")
    led.reload()
    doc = annex_iv(m, ledger=led)
    g = doc["sections"]["2_elements_and_development"]["g_validation_and_testing"]
    assert g["action_ledger_verified"] is False and g["action_ledger_problems"]


def test_the_content_hash_moves_with_the_store(tmp_path):
    m, led, pk = _store(tmp_path)
    a = annex_iv(m, ledger=led)["content_sha256"]
    m.remember("the limit is 60", key="limit")
    b = annex_iv(m, ledger=led)["content_sha256"]
    assert a != b


def test_instructions_for_use_name_real_commands_and_the_real_files(tmp_path):
    m, led, pk = _store(tmp_path)
    ins = instructions_for_use(m, led)
    assert ins["files"]["memory_store"] == "mem.json" and str(tmp_path) not in json.dumps(ins)   # names, never paths
    assert ins["files"]["action_ledger"] == "mem.json.actions.json"
    assert ins["files"]["receipts_enabled"] is True
    from inspeximus import cli
    src = open(cli.__file__, encoding="utf-8").read()
    for cmd in ("audit-build", "audit-verify", "actions", "compliance"):
        assert any(cmd in line for line in ins["how_to_verify"])
        assert f'"{cmd}"' in src, f"the instructions name `{cmd}` but the CLI does not define it"
    md = render_markdown(annex_iv(m, ledger=led))
    assert "## 1. General description" in md and "### (h) Instructions for use logs" in md
    assert "## 9. Post market monitoring plan" in md
    assert OPERATOR_INPUT in md
