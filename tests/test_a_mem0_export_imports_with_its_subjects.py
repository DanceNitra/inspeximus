"""A mem0 export imports one record per memory, with the user as the subject and mem0's timestamp as
the event time, once, with a receipt each; and a re-import after an erasure does not resurrect the
person.

The export shape is the dict `Memory.get_all()` returns in mem0 2.0.11 (`{"results": [...]}`, items
with `id`, `memory`, `hash` = md5 of the text, `created_at`, `updated_at` = `created_at` at creation,
promoted `user_id`/`agent_id`/... and `metadata`), read from `Memory._get_all_from_vector_store` and
`_create_memory` rather than assumed. mem0 itself is not imported here; the fixture is that shape
written by hand, with the real md5 of each text.

Controls, each one a break the 2026-09-17 red team found on the first version: a second import
writes nothing; a re-import after `forget_subject` writes nothing (the sidecar remembers the erased
ids, the tombstone cannot); an expired memory is skipped by mem0's own rule (date before today, so a
memory expiring today is still imported) unless asked for; two users' `{"key": "phone"}` do not
retire each other; an item without an id is still idempotent; a duplicate id inside one export is
reported; a memory without `user_id` is imported but counted as having no subject.
"""
from __future__ import annotations

import hashlib
import os

from inspeximus import Inspeximus
from inspeximus.migrate import _event_time, _expired_for_mem0, identity_of, import_mem0, sidecar_path


def _md5(s):
    return hashlib.md5(s.encode("utf-8")).hexdigest()


ITEMS = [
    {"id": "m-1", "memory": "Alice prefers email over phone calls", "hash": _md5("Alice prefers email over phone calls"),
     "created_at": "2026-09-01T10:15:00Z", "updated_at": "2026-09-01T10:15:00Z", "user_id": "alice"},
    {"id": "m-2", "memory": "Alice's phone number is +100", "hash": _md5("Alice's phone number is +100"),
     "created_at": "2026-09-02T08:00:00Z", "updated_at": "2026-09-02T08:00:00Z", "user_id": "alice",
     "metadata": {"key": "phone", "channel": "crm"}},
    {"id": "m-3", "memory": "Bob's phone number is +300", "hash": _md5("Bob's phone number is +300"),
     "created_at": "2026-09-03T12:30:00Z", "updated_at": "2026-09-03T12:30:00Z", "user_id": "bob",
     "agent_id": "support-bot", "metadata": {"key": "phone"}},
    {"id": "m-4", "memory": "Promo code SUMMER26 is valid", "hash": _md5("Promo code SUMMER26 is valid"),
     "created_at": "2026-06-01T00:00:00Z", "updated_at": "2026-06-01T00:00:00Z", "user_id": "bob",
     "expiration_date": "2026-09-01"},
    {"id": "m-5", "memory": "The office is in Nitra", "hash": _md5("The office is in Nitra"),
     "created_at": "2026-09-04T00:00:00Z", "updated_at": "2026-09-04T00:00:00Z"},
]
NOW = _event_time("2026-09-17T12:00:00Z")


def _store(tmp_path):
    return Inspeximus(str(tmp_path / "mem.json"), receipts=True, receipt_key=os.urandom(32).hex())


def test_one_record_per_memory_with_the_user_as_subject_and_created_at_as_event_time(tmp_path):
    m = _store(tmp_path)
    res = import_mem0(m, ITEMS, now=NOW)
    assert len(res["written"]) == 4 and [s["why"] for s in res["skipped"]] == ["expired in mem0"]
    assert res["without_subject"] == 1
    by_text = {r["text"]: r for r in m.items}
    alice = by_text["Alice prefers email over phone calls"]
    assert alice["source"] == {"doc": "mem0/user/alice"}
    assert alice["valid_from"] == _event_time("2026-09-01T10:15:00Z")
    assert alice["meta"]["mem0_id"] == "m-1" and alice["meta"]["mem0_hash"] == _md5(alice["text"])
    assert by_text["Alice's phone number is +100"]["key"] == "alice::phone"      # namespaced by user
    assert by_text["Bob's phone number is +300"]["key"] == "bob::phone"
    assert by_text["Alice's phone number is +100"]["meta"]["mem0_metadata"] == {"key": "phone", "channel": "crm"}
    assert "agent_id:support-bot" in by_text["Bob's phone number is +300"]["tags"]
    assert by_text["The office is in Nitra"].get("source") is None
    assert m.verify_writes()[0] is True                       # one receipt each, chain intact


def test_two_users_with_the_same_metadata_key_do_not_retire_each_other(tmp_path):
    m = _store(tmp_path)
    import_mem0(m, ITEMS, now=NOW)
    active = {r["text"] for r in m.items if r.get("status") == "active"}
    assert {"Alice's phone number is +100", "Bob's phone number is +300"} <= active


def test_a_second_import_writes_nothing(tmp_path):
    m = _store(tmp_path)
    import_mem0(m, ITEMS, now=NOW)
    n = len(list(m.items))
    res = import_mem0(m, ITEMS, now=NOW)
    assert res["written"] == [] and len(list(m.items)) == n
    assert sorted({s["why"] for s in res["skipped"]}) == ["already imported", "expired in mem0"]


def test_a_reimport_after_an_erasure_does_not_resurrect_the_person(tmp_path):
    m = _store(tmp_path)
    import_mem0(m, ITEMS, now=NOW)
    m.forget_subject("mem0/user/alice", request_id="DSAR-3")
    assert not any("Alice" in r["text"] for r in m.items)
    res = import_mem0(m, ITEMS, now=NOW)
    assert res["written"] == []
    assert sum(1 for s in res["skipped"] if s["why"] == "imported earlier and since erased") == 2
    assert not any("Alice" in r["text"] for r in m.items)
    assert os.path.exists(sidecar_path(m))
    # the sidecar holds hashes of ids, never text or ids in the clear
    raw = open(sidecar_path(m), encoding="utf-8").read()
    assert "Alice" not in raw and "m-1" not in raw


def test_an_expired_memory_follows_mem0s_own_rule(tmp_path):
    assert _expired_for_mem0("2026-09-01", NOW) is True
    assert _expired_for_mem0("2026-09-17", NOW) is False        # expiring today is still served today
    assert _expired_for_mem0("2026-09-18", NOW) is False
    assert _expired_for_mem0(None, NOW) is False
    m = _store(tmp_path)
    res = import_mem0(m, ITEMS, include_expired=True, now=NOW)
    assert len(res["written"]) == 5 and res["skipped"] == []
    promo = next(r for r in m.items if r["text"].startswith("Promo"))
    assert promo["meta"]["mem0_expiration_date"] == "2026-09-01"


def test_subject_erasure_reaches_every_imported_record_of_that_user(tmp_path):
    m = _store(tmp_path)
    import_mem0(m, ITEMS, now=NOW)
    preview = m.forget_subject("mem0/user/alice", request_id="DSAR-3", dry_run=True)
    assert preview["would_erase"] == 2
    m.forget_subject("mem0/user/alice", request_id="DSAR-3")
    texts = {r["text"] for r in m.items}
    assert "Alice's phone number is +100" not in texts and "Bob's phone number is +300" in texts


def test_the_imported_key_supersedes_like_a_native_one(tmp_path):
    m = _store(tmp_path)
    import_mem0(m, ITEMS, now=NOW)
    m.remember("Alice's phone number is +200", key="alice::phone", source={"doc": "mem0/user/alice"})
    active = [r for r in m.items if r.get("key") == "alice::phone" and r.get("status") == "active"]
    assert [r["text"] for r in active] == ["Alice's phone number is +200"]


def test_items_without_an_id_are_still_idempotent_and_duplicates_are_reported(tmp_path):
    m = _store(tmp_path)
    no_id = [{"memory": "x is 1", "created_at": "2026-09-01T00:00:00Z", "user_id": "u"},
             {"memory": "y is 2", "created_at": "2026-09-01T00:00:00Z", "user_id": "u"}]
    assert import_mem0(m, no_id, now=NOW)["written"] and len(list(m.items)) == 2
    assert import_mem0(m, no_id, now=NOW)["written"] == [] and len(list(m.items)) == 2
    dup = [{"id": "d", "memory": "first", "user_id": "u"}, {"id": "d", "memory": "second", "user_id": "u"}]
    res = import_mem0(m, dup, now=NOW)
    assert len(res["written"]) == 1 and res["skipped"] == [{"id": "d", "why": "duplicate id in export"}]
    res = import_mem0(m, [{"id": "n", "memory": 42, "user_id": "u"}], now=NOW)
    assert res["written"] == [] and res["skipped"][0]["why"] == "no memory text"
    assert identity_of({"memory": "t", "user_id": "u", "created_at": "c"}).startswith("sha256:")


def test_the_timestamp_parser_accepts_what_mem0_writes():
    assert _event_time("2026-09-01T10:15:00Z") == _event_time("2026-09-01T10:15:00+00:00")
    assert _event_time(1700000000) == 1700000000.0
    assert _event_time(None) is None and _event_time("not a date") is None
