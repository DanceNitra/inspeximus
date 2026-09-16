"""Lifecycle events on the action chain. Controls: an unknown event, a missing actor and a
decommission without a disposition are refused; a rewritten event fails the verifier; Annex IV
point 6 and the deployer report list the entries; the IETF export maps them to the draft's registry."""
import json

import pytest

from inspeximus import Inspeximus, new_receipt_keypair
from inspeximus.actions import ActionLedger, verify_file, LIFECYCLE_EVENTS
from inspeximus.technical_documentation import annex_iv
from inspeximus.deployer import deployer_report
from inspeximus.agent_audit_trail import to_records

pytest.importorskip("cryptography")


def test_lifecycle_events_are_recorded_verified_and_reported(tmp_path):
    sk, pk = new_receipt_keypair()
    m = Inspeximus(str(tmp_path / "mem.json"), receipts=True, receipt_key=sk)
    led = ActionLedger(m, actor="agent")
    led.record("tool:a")
    a = led.lifecycle("substantial_modification", actor="cto", note="new retrieval model", refers_to=0)
    b = led.lifecycle("decommission", actor="ops", disposition="archived")
    assert a["event"] == "substantial_modification" and "111(2)" in a["basis"] and a["refers_to"]["seq"] == 0
    assert b["disposition"] == "archived"
    assert led.verify(expected_pubkey=pk) == (True, [])
    assert led.oversight_report()["lifecycle_events"] == 2 and led.oversight_report()["actions"] == 1
    assert [e["event"] for e in led.lifecycle_events()] == ["substantial_modification", "decommission"]
    for bad in (("reboot", "ops", {}), ("stop", "", {}), ("decommission", "ops", {}), ("stop", "ops", {"disposition": "lost"})):
        with pytest.raises(ValueError):
            led.lifecycle(bad[0], actor=bad[1], **bad[2])
    assert set(LIFECYCLE_EVENTS) >= {"start", "stop", "decommission", "substantial_modification"}
    # the documents read them
    doc = annex_iv(m, ledger=led)
    ev = doc["sections"]["6_lifecycle_changes"]["evidence"]["lifecycle_events"]
    assert [e["event"] for e in ev] == ["substantial_modification", "decommission"]
    rep = deployer_report(m, ledger=led)
    d = rep["sections"]["1_deployer_duties_art_26"]["26_1_use_per_instructions"]["evidence"]
    assert d["substantial_modifications"][0]["actor"] == "cto"
    # the IETF export
    recs = to_records(led.entries(), "urn:agent:x", "1.0.0")
    assert recs[1]["action_type"] == "lifecycle" and recs[1]["action_detail"]["event"] == "configuration_change"
    assert recs[2]["action_detail"]["new_state"] == "decommissioned:memory archived"
    # CONTROL: a rewritten event fails
    data = json.loads(led.path.read_text(encoding="utf-8"))
    data[2]["disposition"] = "erased"
    led.path.write_text(json.dumps(data), encoding="utf-8")
    assert not verify_file(led.path, expected_pubkey=pk)[0]
