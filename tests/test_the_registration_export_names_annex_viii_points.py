"""Annex VIII registration export (Art. 49). Controls: every field carries its Annex VIII point; the
operator's values are used and the rest marked, never invented; section B has 7 points because the
omnibus deleted 7 and 9; section C's summaries are drawn from the deployer report and carry the
ledger's incidents; an unknown section is refused; the CLI subcommand is real.
"""
import json
import sys

import pytest

from inspeximus import Inspeximus, new_receipt_keypair
from inspeximus.actions import ActionLedger
from inspeximus.technical_documentation import registration_export, REGISTRATION_FIELDS, OPERATOR_INPUT

pytest.importorskip("cryptography")


def _store(tmp_path):
    sk, pk = new_receipt_keypair()
    m = Inspeximus(str(tmp_path / "mem.json"), receipts=True, receipt_key=sk)
    m.remember("alice phone is +100", key="alice::phone", source={"doc": "crm/alice"}, pii=["phone"])
    led = ActionLedger(m, actor="agent")
    led.record("tool:x")
    led.incident("t", "serious", "dpo", refers_to=[0])
    return m, led


def test_every_field_carries_its_point_and_operator_values_are_used(tmp_path):
    m, led = _store(tmp_path)
    doc = registration_export(m, led, {"trade_name": "Support agent", "status": "in service"}, section="A")
    f = doc["fields"]
    assert f["trade_name"]["value"] == "Support agent" and f["trade_name"]["annex_viii"] == "A.4"
    assert f["status"]["value"] == "in service"
    assert f["provider_name_address_contact"]["value"] == OPERATOR_INPUT
    assert set(doc["operator_fields_missing"]) == set(REGISTRATION_FIELDS["A"]) - {"trade_name", "status"}
    assert doc["operator_fields_total"] == 13
    assert f["trade_name"]["traceability_reference"]["anchor_sth"]
    assert f["information_used_and_operating_logic"]["evidence"]["personal_data_types_tagged"] == ["phone"]
    assert "how_to_verify" in f["electronic_instructions_for_use"]["evidence"]
    assert "+100" not in json.dumps(doc)


def test_section_b_has_seven_points_and_section_c_draws_on_the_deployer_report(tmp_path):
    m, led = _store(tmp_path)
    b = registration_export(m, led, {}, section="b")
    assert b["section"] == "B" and b["operator_fields_total"] == 7 and "deleted" in b["basis"]
    assert "B.7" not in json.dumps(b) and "B.9" not in json.dumps(b)
    c = registration_export(m, led, {"deployer_name_address_contact": "Town of X"}, section="C")
    assert c["operator_fields_total"] == 5
    assert c["fields"]["fria_summary"]["evidence"]["incidents"]["incidents"] == 1
    assert "observed_period_and_frequency" not in c["fields"]["fria_summary"]["evidence"]   # not a finding
    text = json.dumps(c)
    assert "dpo" not in text and "store_path" not in text and str(tmp_path) not in text  # nothing that names a person or a machine
    assert c["fields"]["dpia_summary"]["evidence"]["inventory"]["personal_data_tagged"]["records_with_pii"] == 1
    assert c["fields"]["deployer_name_address_contact"]["value"] == "Town of X"
    with pytest.raises(ValueError):
        registration_export(m, led, {}, section="D")


def test_the_cli_writes_the_export(tmp_path, monkeypatch, capsys):
    m, led = _store(tmp_path)
    out = tmp_path / "reg.json"
    from inspeximus import cli
    monkeypatch.setattr(sys, "argv", ["inspeximus", "--path", str(m.path), "registration-export",
                                      "--section", "C", "--out", str(out)])
    try:
        cli.main()
    except SystemExit as e:
        assert e.code in (0, None)
    doc = json.loads(out.read_text(encoding="utf-8"))
    assert doc["kind"] == "inspeximus.registration_export/1" and doc["section"] == "C"
    assert "section C" in capsys.readouterr().out
