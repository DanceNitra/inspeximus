"""agmi issue #5 (T6, cross-context replay), 2026-09-27: the two ways across a context that still
verified after 3.11.0 bound the context into the write receipt.

Measured on 3.14.3 before this file existed:

1. A PARTITION is a `partition:<name>` tag, and tags are in no commitment. A record written into
   partition p1 and retagged on disk as p2's was served by p2's recall, and `verify_writes()`
   returned (True, []).
2. A receipt written before 3.11.0 commits no context at all, and `verify_writes()` passed such
   records by default. `context_unbound()` counted only records that still CARRY a context, so
   stripping alice's uid -- which makes the record visible to every user, bob included -- left
   nothing to count, and nothing was said anywhere.

agmi's own T6 cell read "accepted" for a third reason, in its adapter: the victim it picks is the
last row of the whole store, which after seeding the second context is the donor itself, so the
replay rewrote a record with its own bytes. That is agmi's to fix; this file covers ours.
"""
import json

import pytest

from inspeximus import Inspeximus, receipt_key_for
from inspeximus.partitions import Partitions

pytest.importorskip("cryptography")


def _store(tmp_path, monkeypatch):
    monkeypatch.setenv("INSPEXIMUS_STORE_FORMAT", "json")
    monkeypatch.setenv("INSPEXIMUS_KEY_HOME", str(tmp_path / "keys"))
    (tmp_path / "store").mkdir()
    return tmp_path / "store" / "memory.json"


def _signed(path):
    return Inspeximus(str(path), receipts=True, receipt_key=receipt_key_for(str(path)))


def _rows(data):
    return data["items"] if isinstance(data, dict) else data


def _edit(path, rid, fn):
    """Apply `fn` to the stored record `rid` in the file, as an attacker holding the file would."""
    data = json.loads(path.read_text(encoding="utf-8"))
    hit = [r for r in _rows(data) if r.get("id") == rid]
    assert hit, "control: the record is in the file"
    fn(hit[0])
    path.write_text(json.dumps(data), encoding="utf-8")


def _retag(old, new):
    def fn(rec):
        assert old is None or old in rec["tags"], f"control: the record carries {old}"
        rec["tags"] = [t for t in rec["tags"] if t != old] + ([new] if new else [])
    return fn


def _without(*fields):
    """Make `_write_commit` write receipts as an older release did: without `fields`."""
    real = Inspeximus._write_commit

    def old(rec, retires=()):
        c = real(rec, retires)
        for f in fields:
            c.pop(f, None)
        return c
    return real, staticmethod(old)


def _p2_serves(path, text):
    s = _signed(path)
    return any(h["text"] == text for h in Partitions(s).open("p2", kind="process").recall("payout"))


# ---- 1. the partition is bound -------------------------------------------------------------------

def _two_partitions(path):
    s = _signed(path)
    parts = Partitions(s)
    x = parts.open("p1", kind="process").remember("p1 payout goes to IBAN SK11 1111", key="p1_payout")
    parts.open("p2", kind="process").remember("p2 payout note", key="p2_note")
    ok, problems = _signed(path).verify_writes()
    assert ok, f"control: the untouched store verifies: {problems}"
    return x if isinstance(x, str) else x["id"]


def test_a_record_moved_into_another_partition_fails_verify_writes(tmp_path, monkeypatch):
    path = _store(tmp_path, monkeypatch)
    rid = _two_partitions(path)
    _edit(path, rid, _retag("partition:p1", "partition:p2"))
    assert _p2_serves(path, "p1 payout goes to IBAN SK11 1111"), "control: p2 now serves p1's record"
    ok, problems = _signed(path).verify_writes()
    assert not ok and any(rid in p and "WHICH PARTITION" in p for p in problems), problems


def test_a_record_taken_out_of_its_partition_fails_verify_writes(tmp_path, monkeypatch):
    path = _store(tmp_path, monkeypatch)
    rid = _two_partitions(path)
    _edit(path, rid, _retag("partition:p1", None))
    ok, problems = _signed(path).verify_writes()
    assert not ok and any(rid in p and "WHICH PARTITION" in p for p in problems), problems


def test_an_unpartitioned_record_moved_into_a_partition_fails_verify_writes(tmp_path, monkeypatch):
    path = _store(tmp_path, monkeypatch)
    rid = _signed(path).remember("store-wide payout goes to IBAN SK33 3333", key="payout")
    rid = rid if isinstance(rid, str) else rid["id"]
    assert _signed(path).verify_writes()[0], "control: untouched"
    _edit(path, rid, _retag(None, "partition:p2"))
    ok, problems = _signed(path).verify_writes()
    assert not ok and any(rid in p and "WHICH PARTITION" in p for p in problems), problems


def test_other_tags_stay_free_to_change(tmp_path, monkeypatch):
    """Only the partition tag binds. A caller may still add an ordinary tag to a stored record."""
    path = _store(tmp_path, monkeypatch)
    rid = _two_partitions(path)
    _edit(path, rid, _retag(None, "reviewed"))
    ok, problems = _signed(path).verify_writes()
    assert ok, problems


def test_provenance_and_the_audit_bundle_name_a_partition_move(tmp_path, monkeypatch):
    from inspeximus.audit_bundle import bind_content, build_bundle
    path = _store(tmp_path, monkeypatch)
    rid = _two_partitions(path)
    bundle = build_bundle(_signed(path))
    assert bind_content(bundle, list(_signed(path).items))["ok"], "control: the untouched store binds"
    _edit(path, rid, _retag("partition:p1", "partition:p2"))
    moved = _signed(path)
    integ = moved.provenance(id=rid)["integrity"]
    assert integ["content_matches_receipt"] is False
    assert "partition_sha256" in integ.get("content_mismatch_fields", [])
    b = bind_content(bundle, list(moved.items))
    assert not b["ok"] and {"memory_id": rid, "field": "partition_sha256"} in b["mismatched"], b


# ---- 2. receipts that predate a binding are UNSCOPED, and say so ---------------------------------

def test_a_record_written_before_the_partition_binding_is_reported_unscoped(tmp_path, monkeypatch):
    path = _store(tmp_path, monkeypatch)
    real, old = _without("partition_sha256")
    monkeypatch.setattr(Inspeximus, "_write_commit", old)
    s = _signed(path)
    x = Partitions(s).open("p1", kind="process").remember("p1 payout goes to IBAN SK11 1111", key="p1_payout")
    rid = x if isinstance(x, str) else x["id"]
    monkeypatch.setattr(Inspeximus, "_write_commit", staticmethod(real))              # upgrade
    s = _signed(path)
    ok, problems = s.verify_writes()
    assert not ok and any("UNSCOPED" in p and rid in p for p in problems), problems
    assert s.verify_writes(context_strict=False)[0], "the caller can accept the gap, explicitly"
    assert s.context_unbound()["ids"] == [rid]
    s.recommit(ids=[rid])
    assert _signed(path).verify_writes()[0], "recommit binds the current partition"
    _edit(path, rid, _retag("partition:p1", "partition:p2"))
    ok, problems = _signed(path).verify_writes()
    assert not ok and any("WHICH PARTITION" in p for p in problems), problems


def test_a_pre_3_11_record_whose_owner_was_stripped_is_reported(tmp_path, monkeypatch):
    """The move the has-a-context count missed: alice's uid removed, so bob's recall serves it."""
    path = _store(tmp_path, monkeypatch)
    real, old = _without("context_sha256", "partition_sha256")
    monkeypatch.setattr(Inspeximus, "_write_commit", old)
    rid = _signed(path).remember("alice's payout goes to IBAN SK44 4444", key="payout", user_id="alice")
    rid = rid if isinstance(rid, str) else rid["id"]
    monkeypatch.setattr(Inspeximus, "_write_commit", staticmethod(real))              # upgrade
    _edit(path, rid, lambda rec: rec["meta"].pop("uid"))
    s = _signed(path)
    assert any(h["id"] == rid for h in s.recall("payout", user_id="bob")), "control: bob is served it"
    ok, problems = s.verify_writes()
    assert not ok and any("UNSCOPED" in p and rid in p for p in problems), problems
    assert s.context_unbound()["unbound"] == 1


def test_the_unscoped_line_names_the_remedy_and_the_opt_out(tmp_path, monkeypatch):
    path = _store(tmp_path, monkeypatch)
    real, old = _without("context_sha256", "partition_sha256")
    monkeypatch.setattr(Inspeximus, "_write_commit", old)
    _signed(path).remember("the staging database is db-7", key="staging_db")
    monkeypatch.setattr(Inspeximus, "_write_commit", staticmethod(real))
    ok, problems = _signed(path).verify_writes()
    line = [p for p in problems if "UNSCOPED" in p]
    assert not ok and len(line) == 1 and len(line[0].splitlines()) == 1, problems
    assert "recommit(ids=[...])" in line[0] and "context_strict=False" in line[0], line


def test_a_store_written_by_this_version_has_nothing_unscoped(tmp_path, monkeypatch):
    path = _store(tmp_path, monkeypatch)
    s = _signed(path)
    s.remember("alice's payout", key="a", user_id="alice")
    s.remember("a store-wide note", key="n")
    Partitions(s).open("p1", kind="process").remember("p1 note", key="p")
    s = _signed(path)
    assert s.context_unbound() == {"unbound": 0, "ids": [], "warning": None}
    assert s.verify_writes()[0]
