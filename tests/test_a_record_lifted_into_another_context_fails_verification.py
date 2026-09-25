"""agmi 0.6.0, test T6 (cross-context replay): a genuine signed record moved into another user's context
must not verify.

A write receipt commits to a record's text, key, type, value, status, validity time and sources
(`_write_commit`), and not to the context the record belongs to: its tenant, its owning agent, and the
user, agent and project it was written for (`meta.uid`, `meta.aid`, `meta.project`). So a record
written for alice, signed, and then relabelled on disk as bob's is served in bob's context, and
`verify_writes()` still reports the chain intact: every committed field is unchanged. The record is
genuine; its context is forged.

This file is the reproduction only. It fails on 3.9.6. The fix (bind the context into the signed
commit, with a migration note for receipts written before it) waits for review.
"""
import json

import pytest

from inspeximus import Inspeximus, receipt_key_for

pytest.importorskip("cryptography")


def _signed_store(path):
    return Inspeximus(str(path), receipts=True, receipt_key=receipt_key_for(str(path)))


def _rows(data):
    return data["items"] if isinstance(data, dict) else data


def _write_as_alice(s, field):
    text, key = "pay the invoice to IBAN SK00 1111 2222", "payout_iban"
    if field == "tenant":
        return s.for_tenant("alice").remember(text, key=key)
    if field == "owner_agent":
        return s.as_agent("alice").remember(text, key=key)
    kw = {"uid": "user_id", "aid": "agent_id", "project": "project"}[field]
    return s.remember(text, key=key, **{kw: "alice"})


def _relabel(rec, field):
    if field in ("tenant", "owner_agent"):
        assert rec.get(field) == "alice", f"control: the record carries {field}=alice"
        rec[field] = "bob"
    else:
        assert rec["meta"].get(field) == "alice", f"control: the record carries meta.{field}=alice"
        rec["meta"][field] = "bob"


@pytest.mark.parametrize("field", ["tenant", "owner_agent", "uid", "aid", "project"])
def test_a_signed_record_relabelled_into_another_context_fails_verify_writes(tmp_path, monkeypatch, field):
    monkeypatch.setenv("INSPEXIMUS_STORE_FORMAT", "json")
    monkeypatch.setenv("INSPEXIMUS_KEY_HOME", str(tmp_path / "keys"))
    (tmp_path / "store").mkdir()
    path = tmp_path / "store" / "memory.json"
    s = _signed_store(path)
    rid = _write_as_alice(s, field)
    rid = rid if isinstance(rid, str) else rid["id"]
    ok, problems = s.verify_writes()
    assert ok, f"control: the untouched store verifies: {problems}"

    data = json.loads(path.read_text(encoding="utf-8"))
    hit = [r for r in _rows(data) if r.get("id") == rid]
    assert hit, "control: the record is in the file"
    _relabel(hit[0], field)
    path.write_text(json.dumps(data), encoding="utf-8")

    ok, problems = _signed_store(path).verify_writes()
    assert not ok, f"a record moved from alice's {field} into bob's still verifies"
