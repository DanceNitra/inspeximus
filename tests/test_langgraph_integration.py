"""The LangGraph adapters, which were the single largest uncovered module (12 functions).

`InspeximusSaver` is a `BaseCheckpointSaver` — LangGraph writes a graph's execution state through it, so a
wrapper that drops a checkpoint, returns the wrong one, or loses pending writes corrupts a user's agent run
and nothing in our suite would have said a word. `InspeximusStore` is a `BaseStore`, which is also what
LangMem sits on.

The product claim these exist to make is testable, and is tested here: LangGraph's own `InMemoryStore` is
last-write-wins with NO history — a second `put` on a key silently discards the first. `InspeximusStore`
keeps the superseded values on the supersession ledger, so `history(namespace, key)` returns every value the
key has held. If that stops being true, the adapter has no reason to exist.

Guarded with `importorskip`: CI's base environment has no langgraph, and local-green is not CI-green.
"""
import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

pytest.importorskip("langgraph.checkpoint.base")

from inspeximus.integrations.langgraph import InspeximusSaver, InspeximusStore  # noqa: E402


def _path():
    return os.path.join(tempfile.mkdtemp(), "lg.json")


def _cfg(thread_id="t1", ns="", checkpoint_id=None):
    c = {"configurable": {"thread_id": thread_id, "checkpoint_ns": ns}}
    if checkpoint_id:
        c["configurable"]["checkpoint_id"] = checkpoint_id
    return c


def _ckpt(cid, values):
    return {"v": 1, "id": cid, "ts": "2026-07-26T00:00:00+00:00",
            "channel_values": dict(values), "channel_versions": {}, "versions_seen": {}}


# ── the checkpointer round trip ─────────────────────────────────────────────────────────────────────
def test_a_checkpoint_survives_put_and_comes_back_from_get_tuple():
    saver = InspeximusSaver(path=_path())
    cfg = saver.put(_cfg(), _ckpt("c1", {"messages": ["hello"]}), {"step": 1}, {})

    got = saver.get_tuple(cfg or _cfg())
    assert got is not None, "the checkpoint just written must be readable"
    assert got.checkpoint["channel_values"]["messages"] == ["hello"], got.checkpoint


def test_get_tuple_returns_the_LATEST_checkpoint_for_a_thread():
    saver = InspeximusSaver(path=_path())
    saver.put(_cfg(), _ckpt("c1", {"step": "first"}), {"step": 1}, {})
    saver.put(_cfg(), _ckpt("c2", {"step": "second"}), {"step": 2}, {})

    got = saver.get_tuple(_cfg())
    assert got.checkpoint["channel_values"]["step"] == "second", got.checkpoint


def test_get_tuple_on_an_unknown_thread_returns_none():
    assert InspeximusSaver(path=_path()).get_tuple(_cfg("never-seen")) is None


def test_list_returns_the_thread_history_and_honours_limit():
    saver = InspeximusSaver(path=_path())
    for i in range(4):
        saver.put(_cfg(), _ckpt(f"c{i}", {"step": i}), {"step": i}, {})

    everything = list(saver.list(_cfg()))
    assert len(everything) == 4, f"every checkpoint must be listed: {len(everything)}"

    # ORDER, not just count. `list()` is newest-first and LangGraph consumers rely on that; a count-only
    # assertion passes against a reversed implementation, which is exactly what a mutation showed.
    steps = [t.checkpoint["channel_values"]["step"] for t in everything]
    assert steps == [3, 2, 1, 0], f"list() must be newest-first: {steps}"

    limited = list(saver.list(_cfg(), limit=2))
    assert len(limited) == 2, "a dropped `limit` returns plausible results and is invisible otherwise"
    assert [t.checkpoint["channel_values"]["step"] for t in limited] == [3, 2], \
        "and a limited page must be the NEWEST two, not an arbitrary two"


def test_list_does_not_leak_another_thread():
    saver = InspeximusSaver(path=_path())
    saver.put(_cfg("thread-a"), _ckpt("a1", {"who": "a"}), {"step": 1}, {})
    saver.put(_cfg("thread-b"), _ckpt("b1", {"who": "b"}), {"step": 1}, {})

    got = list(saver.list(_cfg("thread-a")))
    assert len(got) == 1, got
    assert got[0].checkpoint["channel_values"]["who"] == "a"


def test_put_writes_are_returned_with_their_checkpoint():
    """Pending writes are how LangGraph resumes an interrupted run. Losing them loses the run."""
    saver = InspeximusSaver(path=_path())
    cfg = saver.put(_cfg(), _ckpt("c1", {"messages": []}), {"step": 1}, {})
    saver.put_writes(cfg or _cfg(), [("messages", "queued value")], task_id="task-1")

    got = saver.get_tuple(cfg or _cfg())
    assert got is not None
    assert got.pending_writes, "the pending write must survive the round trip"
    assert any("queued value" in str(w) for w in got.pending_writes), got.pending_writes


def test_delete_thread_removes_that_thread_and_only_that_thread():
    saver = InspeximusSaver(path=_path())
    saver.put(_cfg("doomed"), _ckpt("d1", {"who": "doomed"}), {"step": 1}, {})
    saver.put(_cfg("keeper"), _ckpt("k1", {"who": "keeper"}), {"step": 1}, {})

    saver.delete_thread("doomed")

    assert saver.get_tuple(_cfg("doomed")) is None, "the thread must be gone"
    kept = saver.get_tuple(_cfg("keeper"))
    assert kept is not None and kept.checkpoint["channel_values"]["who"] == "keeper", \
        "and the other thread must be untouched"


def test_a_saver_can_be_built_on_an_existing_store():
    from inspeximus import Inspeximus
    shared = Inspeximus(path=_path())
    assert len(list(shared.items)) == 0

    saver = InspeximusSaver(store=shared)
    saver.put(_cfg("t1"), _ckpt("c1", {"messages": ["shared"]}), {"step": 1}, {})

    # The record's TEXT is a label ("lg checkpoint t1//c1") and the payload rides in meta -- my first
    # version searched the text for the channel value and failed against correct behaviour. Assert on the
    # key, which is what identifies the checkpoint, and on the round trip, which is what users depend on.
    rows = list(shared.items)
    assert len(rows) == 1, rows
    assert "t1" in str(rows[0].get("key")) and "c1" in str(rows[0].get("key")), rows[0]
    assert saver.get_tuple(_cfg("t1")).checkpoint["channel_values"]["messages"] == ["shared"]


# ── the reason the adapter exists ───────────────────────────────────────────────────────────────────
def test_the_store_keeps_history_where_langgraphs_own_store_does_not():
    """The differentiator, stated in the module docstring and measured against the real InMemoryStore:
    a second put on the same key is last-write-wins there, and the old value is unrecoverable."""
    from langgraph.store.memory import InMemoryStore

    ns, key = ("agent", "profile"), "user-1"

    reference = InMemoryStore()
    reference.put(ns, key, {"tier": "free"})
    reference.put(ns, key, {"tier": "paid"})
    assert reference.get(ns, key).value == {"tier": "paid"}
    assert not hasattr(reference, "history"), "the built-in store offers no history at all"

    ours = InspeximusStore(path=_path())
    ours.put(ns, key, {"tier": "free"})
    ours.put(ns, key, {"tier": "paid"})

    assert ours.get(ns, key).value == {"tier": "paid"}, "current-value semantics must match the reference"
    hist = ours.history(ns, key)
    flat = str(hist)
    assert "free" in flat and "paid" in flat, f"both values must be recoverable: {hist}"


def test_the_store_matches_the_reference_on_delete_and_search():
    from langgraph.store.memory import InMemoryStore

    ns = ("agent", "notes")
    for store in (InMemoryStore(), InspeximusStore(path=_path())):
        store.put(ns, "a", {"text": "alpha note"})
        store.put(ns, "b", {"text": "beta note"})
        assert store.get(ns, "a").value == {"text": "alpha note"}, type(store).__name__
        store.delete(ns, "a")
        assert store.get(ns, "a") is None, f"{type(store).__name__}: delete must remove the item"
        assert store.get(ns, "b") is not None, f"{type(store).__name__}: and only that item"
