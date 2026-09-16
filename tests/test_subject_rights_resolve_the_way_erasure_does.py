"""GDPR Art. 15 access and Art. 16 rectification over agent memory, on the erasure resolver.

The invariant: the records an access request returns are the records an erasure request would
remove. Controls: a second subject is not exported, even when it canonicalizes like the first; a
rectification without an actor or reason is refused; the ledger entry carries the export's manifest
hash and a rewritten export no longer matches it.
"""
import json

import pytest

from inspeximus import Inspeximus, new_receipt_keypair
from inspeximus.actions import ActionLedger
from inspeximus.compliance import compliance_report
from inspeximus.subject_rights import export_subject, rectify, _sha

pytest.importorskip("cryptography")


def _store(tmp_path):
    sk, pk = new_receipt_keypair()
    m = Inspeximus(str(tmp_path / "mem.json"), receipts=True, receipt_key=sk)
    m.remember("alice's phone is +100", key="alice::phone", source={"doc": "crm/alice"})
    m.remember("alice prefers email", source={"doc": "crm/alice"})
    m.remember("bob's phone is +300", key="bob::phone", source={"doc": "crm/bob"})
    return m, ActionLedger(m, actor="agent"), pk


def test_export_returns_the_subjects_records_and_not_the_other_subjects(tmp_path):
    m, led, pk = _store(tmp_path)
    pkg = export_subject(m, "crm/alice")
    texts = sorted(r["text"] for r in pkg["records"])
    assert texts == ["alice prefers email", "alice's phone is +100"]
    assert pkg["counts"] == {"records": 2, "direct": 2, "inherited": 0, "tombstones": 0, "actions": 0}
    assert all("provenance" in r for r in pkg["records"])
    assert [r for r in pkg["records"] if r["key"] == "alice::phone"][0]["history"]
    # the same set erasure would remove
    _, ids, _ = m._resolve_subject("crm/alice")
    assert sorted(r["id"] for r in pkg["records"]) == sorted(ids)


def test_export_lists_the_actions_taken_while_the_subjects_records_were_recalled(tmp_path):
    m, led, pk = _store(tmp_path)
    m.recall("alice phone")
    with led.action("tool:call", inputs={"to": "+100"}) as a:
        a.output("rang")
    # a second handle has an empty recall window: an action taken with nothing recalled
    m2 = Inspeximus(str(tmp_path / "mem.json"), receipts=True, receipt_key=m._receipt_sk)
    ActionLedger(m2, actor="agent").record("tool:call", inputs={"to": "+300"})
    led.reload()
    pkg = export_subject(m, "crm/alice", ledger=led, actor="dpo", request_id="DSAR-17")
    assert [x["seq"] for x in pkg["actions"]] == [0]
    assert pkg["ledger_entry"]["seq"] == 2
    e = led.entries()[2]
    assert e["kind"] == "rights" and e["event"] == "export" and e["manifest_sha256"] == pkg["manifest_sha256"]
    assert e["n_records"] == 2 and e["actor"] == "dpo"
    assert led.verify(expected_pubkey=pk) == (True, [])
    # CONTROL: an export edited after the fact no longer matches the manifest on the chain
    body = {k: v for k, v in pkg.items() if k not in ("manifest_sha256", "ledger_entry")}
    assert _sha(body) == e["manifest_sha256"]
    body["records"][0]["text"] = "alice's phone is +999"
    assert _sha(body) != e["manifest_sha256"]


def test_two_subjects_that_canonicalize_alike_are_kept_apart_as_erasure_keeps_them(tmp_path):
    sk, pk = new_receipt_keypair()
    m = Inspeximus(str(tmp_path / "amb.json"), receipts=True, receipt_key=sk)
    m.remember("alice's plan", source={"doc": "crm.example.com/alice"})
    m.remember("bob's plan", source={"doc": "crm.example.com/bob"})
    # both canonicalize to the same host key; the resolver narrows by path, so alice's export is alice only
    assert Inspeximus._canon_source("crm.example.com/alice") == Inspeximus._canon_source("crm.example.com/bob")
    pkg = export_subject(m, "crm.example.com/alice")
    assert [r["text"] for r in pkg["records"]] == ["alice's plan"]
    _, ids, _ = m._resolve_subject("crm.example.com/alice")
    assert [r["id"] for r in pkg["records"]] == ids
    # and the outcome the erasure path gives is the same set: bob survives an erasure of alice
    m.forget_subject("crm.example.com/alice", request_id="DSAR-1")
    assert [r["text"] for r in m.items if r.get("status") == "active"] == ["bob's plan"]


def test_rectify_supersedes_under_the_key_and_records_who_and_why(tmp_path):
    m, led, pk = _store(tmp_path)
    r = rectify(m, key="alice::phone", text="alice's phone is +200", actor="dpo", reason="DSAR-17",
                subject="crm/alice", ledger=led, request_id="DSAR-17")
    assert r["previous_status"] == "superseded" and r["new_id"] != r["previous_id"]
    assert m.recall("alice phone")[0]["text"] == "alice's phone is +200"
    e = led.entries()[-1]
    assert e["kind"] == "rights" and e["event"] == "rectify" and e["key"] == "alice::phone"
    assert e["actor"] == "dpo" and e["reason"] == "DSAR-17" and e["memory_receipt"] == r["memory_receipt"]
    # the corrected record belongs to the subject: it is in the next export, the old one retired
    pkg = export_subject(m, "crm/alice")
    phone = [x for x in pkg["records"] if x["key"] == "alice::phone"]
    assert {x["status"] for x in phone} == {"active", "superseded"}
    assert led.verify(expected_pubkey=pk) == (True, [])


def test_rectify_without_an_actor_or_a_reason_is_refused_and_writes_nothing(tmp_path):
    m, led, pk = _store(tmp_path)
    n = len(m.items)
    with pytest.raises(ValueError):
        rectify(m, key="alice::phone", text="x", actor="", reason="r")
    with pytest.raises(ValueError):
        rectify(m, key="alice::phone", text="x", actor="a", reason="")
    assert len(m.items) == n and len(led) == 0


def test_the_compliance_report_counts_rights_requests_from_the_ledger(tmp_path):
    m, led, pk = _store(tmp_path)
    r0 = compliance_report(m)
    by = {c["article"]: c for c in r0["controls"]}
    assert by["Art. 15"]["status"] == "available" and by["Art. 16"]["status"] == "available"
    assert len(r0["controls"]) == 22
    export_subject(m, "crm/alice", ledger=led, actor="dpo")
    rectify(m, key="alice::phone", text="alice's phone is +200", actor="dpo", reason="DSAR-17", ledger=led)
    r1 = compliance_report(m)
    by = {c["article"]: c for c in r1["controls"]}
    assert by["Art. 15"]["status"] == "evidence" and by["Art. 15"]["live_count"] == 1
    assert by["Art. 16"]["status"] == "evidence" and by["Art. 16"]["live_count"] == 1
