"""draft-sharif-agent-audit-trail-04 export. Controls: every mandatory field, registry values only,
the prev_hash chain per RFC 8785 holds; an edited, removed or reordered line fails; salted digests
are exported under their own name and the draft's plain-hash fields are left out; the canonical JSON
refuses a float; an oversight entry carries human_override; nothing content-bearing is exported."""
import json

import pytest

from inspeximus import Inspeximus, new_receipt_keypair
from inspeximus.actions import ActionLedger
from inspeximus.agent_audit_trail import export_jsonl, verify_jsonl, to_records, canonical_json, DRAFT

pytest.importorskip("cryptography")


def _ledger(tmp_path):
    sk, pk = new_receipt_keypair()
    m = Inspeximus(str(tmp_path / "mem.json"), receipts=True, receipt_key=sk)
    m.remember("the limit is 50", key="limit")
    led = ActionLedger(m, actor="agent")
    m.recall("limit")
    with led.action("tool:transfer", inputs={"phone": "SECRET-PHONE-0100", "amount": "SECRET-AMOUNT"}, model="gpt-5", session="s1") as a:
        a.output({"ok": True})
    with pytest.raises(RuntimeError):
        with led.action("api:bank"):
            raise RuntimeError("upstream down")
    led.oversight("refuse", "ops-lead", refers_to=0, reason="above limit")
    led.disclosure("s1", "You are chatting with an AI assistant.")
    led.incident("transfer above limit", "serious", "dpo", refers_to=[0])
    return led


def test_the_export_verifies_and_carries_the_mapping(tmp_path):
    led = _ledger(tmp_path)
    out = tmp_path / "trail.jsonl"
    res = export_jsonl(led, out, agent_id="urn:agent:support.acme.example", agent_version="1.4.0")
    assert res["records"] == 5 and res["draft"] == DRAFT
    ok, problems = verify_jsonl(out)
    assert ok, problems
    recs = [json.loads(l) for l in out.read_text(encoding="utf-8").splitlines()]
    assert [r["action_type"] for r in recs] == ["tool_call", "error", "decision", "lifecycle", "escalation"]
    assert [r["outcome"] for r in recs] == ["success", "failure", "denied", "success", "escalated"]
    assert recs[0]["trust_level"] == "L1" and recs[0]["model_id"] == "gpt-5" and recs[0]["parent_record_id"] is None
    assert recs[2]["human_override"]["operator_id"] == "ops-lead" and recs[2]["human_override"]["original_action"]["refers_to_seq"] == 0
    assert recs[0]["action_detail"]["inspeximus"]["memory_digest"] and recs[0]["action_detail"]["inspeximus"]["recalled_ids"]
    assert "input_hash" not in recs[0] and recs[0]["action_detail"]["inspeximus"]["inputs_sha256_salted"]
    assert recs[0]["action_detail"]["parameters_hash"] == led.entries()[0]["inputs_sha256"]
    text = out.read_text(encoding="utf-8")
    assert "SECRET-PHONE" not in text and "SECRET-AMOUNT" not in text and "You are chatting" not in text
    assert len({r["session_id"] for r in recs}) == 1


def test_an_edited_removed_or_reordered_line_fails(tmp_path):
    led = _ledger(tmp_path)
    out = tmp_path / "trail.jsonl"
    export_jsonl(led, out, agent_id="urn:agent:x", agent_version="1.0.0")
    lines = out.read_text(encoding="utf-8").splitlines()
    edited = json.loads(lines[1]); edited["outcome"] = "success"
    (tmp_path / "e.jsonl").write_text("\n".join([lines[0], json.dumps(edited)] + lines[2:]) + "\n", encoding="utf-8")
    ok, problems = verify_jsonl(tmp_path / "e.jsonl")
    assert not ok and any("prev_hash" in p for p in problems)
    (tmp_path / "r.jsonl").write_text("\n".join([lines[0]] + lines[2:]) + "\n", encoding="utf-8")
    assert not verify_jsonl(tmp_path / "r.jsonl")[0]
    (tmp_path / "o.jsonl").write_text("\n".join([lines[0], lines[2], lines[1]] + lines[3:]) + "\n", encoding="utf-8")
    assert not verify_jsonl(tmp_path / "o.jsonl")[0]
    # CONTROL: the untouched file still verifies, and an unsigned ledger exports as L0
    assert verify_jsonl(out)[0]
    m = Inspeximus(str(tmp_path / "plain.json"), receipts=True)
    led2 = ActionLedger(m)
    led2.record("tool:a")
    assert to_records(led2.entries(), "urn:agent:y", "0.1.0")[0]["trust_level"] == "L0"
    with pytest.raises(ValueError):
        to_records(led2.entries(), "", "0.1.0")


def test_canonical_json_sorts_by_utf16_and_refuses_a_float():
    assert canonical_json({"b": 1, "a": [2, {"z": None, "y": "é"}]}) == b'{"a":[2,{"y":"\xc3\xa9","z":null}],"b":1}'
    assert canonical_json({"x": 2.0}) == b'{"x":2}'
    with pytest.raises(ValueError):
        canonical_json({"x": 0.1})
