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


def _old_receipts(monkeypatch):
    """Write as a pre-3.11.0 store would: receipts without `context_sha256`."""
    real = Inspeximus._write_commit

    def old(rec, retires=()):
        c = real(rec, retires)
        c.pop("context_sha256", None)
        return c
    monkeypatch.setattr(Inspeximus, "_write_commit", staticmethod(old))
    return real


def _store(tmp_path, monkeypatch):
    monkeypatch.setenv("INSPEXIMUS_STORE_FORMAT", "json")
    monkeypatch.setenv("INSPEXIMUS_KEY_HOME", str(tmp_path / "keys"))
    (tmp_path / "store").mkdir()
    return tmp_path / "store" / "memory.json"


def _upgraded_store(tmp_path, monkeypatch, n=2):
    """A store written with pre-3.11.0 receipts (no context binding), reopened on this version."""
    path = _store(tmp_path, monkeypatch)
    real = _old_receipts(monkeypatch)
    s = _signed_store(path)
    ids = []
    for i in range(n):
        rid = s.remember(f"invoice {i} goes to IBAN SK00 {i}", key=f"payout_{i}", user_id="alice")
        ids.append(rid if isinstance(rid, str) else rid["id"])
    monkeypatch.setattr(Inspeximus, "_write_commit", staticmethod(real))     # upgrade
    return path, ids


def test_a_an_upgraded_store_passes_by_default_and_reports_its_unbound_records(tmp_path, monkeypatch):
    path, ids = _upgraded_store(tmp_path, monkeypatch)
    s = _signed_store(path)
    ok, problems = s.verify_writes()
    assert ok, problems
    cu = s.context_unbound()
    assert cu["unbound"] == 2 and cu["ids"] == sorted(ids)
    assert "recommit(ids=[...])" in cu["warning"] and len(cu["warning"].splitlines()) == 1
    proof = s.governance_report()["proof"]
    assert proof["verified"] and proof["context_unbound"] == 2 and proof["warnings"] == [cu["warning"]]
    s.recommit(ids=ids)
    assert s.context_unbound() == {"unbound": 0, "ids": [], "warning": None}


def test_b_the_same_store_fails_under_context_strict(tmp_path, monkeypatch):
    path, ids = _upgraded_store(tmp_path, monkeypatch)
    s = _signed_store(path)
    ok, problems = s.verify_writes(context_strict=True)
    assert not ok and any("recommit(ids=[...])" in p and ids[0] in p for p in problems), problems
    s.recommit(ids=ids)
    assert s.verify_writes(context_strict=True)[0]


@pytest.mark.parametrize("strict", [False, True], ids=["default", "context_strict"])
def test_c_a_moved_record_with_a_bound_receipt_fails_in_both_modes(tmp_path, monkeypatch, strict):
    path = _store(tmp_path, monkeypatch)
    rid = _signed_store(path).remember("pay the invoice", key="payout", user_id="alice")
    rid = rid if isinstance(rid, str) else rid["id"]
    assert _signed_store(path).verify_writes(context_strict=strict)[0], "control: untouched"
    data = json.loads(path.read_text(encoding="utf-8"))
    [r for r in _rows(data) if r["id"] == rid][0]["meta"]["uid"] = "bob"
    path.write_text(json.dumps(data), encoding="utf-8")
    ok, problems = _signed_store(path).verify_writes(context_strict=strict)
    assert not ok and any("WHOSE it is" in p for p in problems), problems


def test_a_recommitted_receipt_catches_a_later_move(tmp_path, monkeypatch):
    path, ids = _upgraded_store(tmp_path, monkeypatch, n=1)
    _signed_store(path).recommit(ids=ids)
    data = json.loads(path.read_text(encoding="utf-8"))
    [r for r in _rows(data) if r["id"] == ids[0]][0]["meta"]["uid"] = "bob"
    path.write_text(json.dumps(data), encoding="utf-8")
    assert not _signed_store(path).verify_writes()[0]


def test_a_store_without_any_context_upgrades_clean(tmp_path, monkeypatch):
    path = _store(tmp_path, monkeypatch)
    real = _old_receipts(monkeypatch)
    _signed_store(path).remember("the staging database is db-7", key="staging_db")
    monkeypatch.setattr(Inspeximus, "_write_commit", staticmethod(real))
    ok, problems = _signed_store(path).verify_writes()
    assert ok, problems


def test_provenance_names_a_context_change(tmp_path, monkeypatch):
    path = _store(tmp_path, monkeypatch)
    rid = _signed_store(path).remember("pay the invoice", key="payout", project="alice")
    rid = rid if isinstance(rid, str) else rid["id"]
    data = json.loads(path.read_text(encoding="utf-8"))
    [r for r in _rows(data) if r["id"] == rid][0]["meta"]["project"] = "bob"
    path.write_text(json.dumps(data), encoding="utf-8")
    integ = _signed_store(path).provenance(id=rid)["integrity"]
    assert integ["content_matches_receipt"] is False
    assert "context_sha256" in integ.get("content_mismatch_fields", [])
