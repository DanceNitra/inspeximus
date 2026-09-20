"""`memory_events`: a content-free row per committed change, in the same transaction as the change.

WHY A TABLE AND NOT A BROKER. Twenty-two agent processes share one store file. `export_changeset`
is a one-shot package, so the only way a process learned that another had changed a key was to
re-read the store. A table the row writer appends to inside its own transaction gives every
process a cursor (`seq`) it can tail, with no daemon and no dependency, and the one property a
broker cannot give: an event exists exactly when its row does.

THE FAILURE THIS PREVENTS IS A PHANTOM. An event written before the row commits announces a change
that may roll back; one written after can be lost between the two. Both are the same defect, and
the test for it is the same: make the row write fail and require that no event was written.
"""
from __future__ import annotations

import json
import os
import sqlite3
import tempfile

import pytest

from inspeximus import Inspeximus
from inspeximus import sqlite_store


def _mk(**kw):
    d = tempfile.mkdtemp()
    p = os.path.join(d, "s.json")
    return p, Inspeximus(path=p, **kw)


def _events(p):
    con = sqlite3.connect(p)
    try:
        return con.execute("SELECT seq, type, memory_id, payload FROM memory_events ORDER BY seq").fetchall()
    finally:
        con.close()


def test_a_write_produces_one_added_event_and_a_supersession_one_changed():
    p, ix = _mk()
    a = ix.remember("the deadline is Friday", key="deadline")
    ix.flush()
    evs = ix.poll_events()
    assert [e["type"] for e in evs] == ["record.added"]
    assert evs[0]["memory_id"] == a and evs[0]["payload"]["key"] == "deadline"
    assert "text" not in json.dumps(evs)                      # content-free, by construction
    b = ix.remember("the deadline is Monday", key="deadline")
    ix.flush()
    later = ix.poll_events(since_seq=evs[-1]["seq"])
    kinds = sorted((e["type"], e["memory_id"]) for e in later)
    assert ("record.added", b) in kinds and ("record.changed", a) in kinds
    assert next(e for e in later if e["memory_id"] == a)["payload"]["status"] == "superseded"


def test_another_process_sees_the_event_without_a_reload():
    p, ix = _mk()
    other = Inspeximus(path=p)
    tip = other.events_tip()
    ix.remember("x", key="k")
    ix.flush()
    got = other.poll_events(since_seq=tip)
    assert len(got) == 1 and got[0]["type"] == "record.added"


class _RowInsertFails(sqlite3.Connection):
    """A connection whose INSERT into `records` raises, after the transaction is open."""
    def executemany(self, sql, rows):
        if sql.lstrip().upper().startswith("INSERT INTO RECORDS"):
            raise sqlite3.OperationalError("injected: the row write failed")
        return super().executemany(sql, rows)


def test_a_row_write_that_fails_writes_no_event(monkeypatch):
    p, ix = _mk()
    ix.remember("first", key="a")
    ix.flush()
    n0 = len(_events(p))
    # Fail the row INSERT from INSIDE the open transaction, not by editing the file (an on-disk
    # edit trips the changed-on-disk guard first, which is a different refusal).
    real = sqlite3.connect
    monkeypatch.setattr(sqlite_store.sqlite3, "connect",
                        lambda *a, **k: real(*a, factory=_RowInsertFails, **k))
    ix.remember("second", key="b")                   # the hot path records the failure ...
    with pytest.raises(OSError):
        ix.flush()                                   # ... and flush() is where it is raised
    monkeypatch.undo()
    assert len(_events(p)) == n0, "an event landed for a row that did not"
    assert len(sqlite_store.load(p)) == 1


def test_a_removal_is_an_event_too():
    p, ix = _mk()
    a = ix.remember("to be erased", key="gone", source={"doc": "u1"})
    ix.flush()
    tip = ix.events_tip()
    ix.forget(ids=[a])
    ix.flush()
    evs = ix.poll_events(since_seq=tip)
    assert any(e["type"] == "record.removed" and e["memory_id"] == a for e in evs)


def test_publish_event_returns_a_committed_seq_and_refuses_the_reserved_prefix():
    p, ix = _mk()
    seq = ix.publish_event("plan.updated", {"task": "t-1"}, agent_id="lead")
    assert seq >= 1
    ev = ix.poll_events(since_seq=seq - 1)[0]
    assert ev["type"] == "plan.updated" and ev["agent"] == "lead" and ev["payload"] == {"task": "t-1"}
    with pytest.raises(ValueError):
        ix.publish_event("record.added", {})


def test_events_off_writes_no_table_rows_and_publish_refuses():
    p, ix = _mk(events=False)
    ix.remember("x", key="k")
    ix.flush()
    assert _events(p) == []
    with pytest.raises(RuntimeError):
        ix.publish_event("anything")


def test_a_store_created_before_the_table_gets_it_in_place():
    p, ix = _mk()
    ix.remember("old", key="k")
    ix.flush()
    con = sqlite3.connect(p)
    con.execute("DROP TABLE memory_events")
    con.commit(); con.close()
    ix2 = Inspeximus(path=p)
    ix2.remember("new", key="k2")
    ix2.flush()
    assert [e["type"] for e in ix2.poll_events()] == ["record.added"]


def test_a_tenant_bound_view_sees_only_its_tenants_events():
    p, ix = _mk()
    a = ix.for_tenant("acme")
    b = ix.for_tenant("globex")
    a.remember("acme fact", key="k")
    b.remember("globex fact", key="k")
    ix.flush()
    assert {e["tenant"] for e in a.poll_events()} == {"acme"}
    assert {e["tenant"] for e in b.poll_events()} == {"globex"}
    assert {e["tenant"] for e in ix.poll_events()} == {"acme", "globex"}


def test_an_agent_bound_view_sees_a_record_event_only_with_a_grant():
    p, ix = _mk()
    alice = ix.as_agent("alice")
    bob = ix.as_agent("bob")
    rid = alice.remember("alice's roadmap", key="roadmap")
    ix.flush()
    assert all(e["memory_id"] != rid for e in bob.poll_events())      # no grant: not even the event
    assert any(e["memory_id"] == rid for e in alice.poll_events())
    ix.grant("bob", key="roadmap", by="alice")
    ix.flush()
    assert any(e["memory_id"] == rid for e in bob.poll_events())


def test_an_application_event_reaches_the_agent_it_names():
    p, ix = _mk()
    ix.publish_event("task.assigned", {"to": "bob", "task": "t-9"}, agent_id="lead")
    ix.publish_event("task.assigned", {"to": "carol"}, agent_id="lead")
    ix.publish_event("tick", {}, agent_id="system")
    seen = [e["type"] + ":" + str(e["payload"].get("to")) for e in ix.as_agent("bob").poll_events()]
    assert seen == ["task.assigned:bob", "tick:None"]


def test_subscribers_are_called_after_the_commit_and_across_processes():
    p, ix = _mk()
    got = []
    sid = ix.subscribe("record.added", got.append)
    ix.remember("x", key="k")
    ix.flush()
    assert len(got) == 1 and got[0]["type"] == "record.added"
    other = Inspeximus(path=p)
    other.remember("from elsewhere", key="k2")
    other.flush()
    assert ix.dispatch_events() == 1 and got[-1]["payload"]["key"] == "k2"
    assert ix.unsubscribe(sid) is True and ix.unsubscribe(sid) is False
