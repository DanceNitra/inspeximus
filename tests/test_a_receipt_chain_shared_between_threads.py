"""The receipt and tombstone chains of a handle shared between threads stay one chain (3.19, shared-state inventory).

A chain entry is a read-modify-write: read the tail's hash, hash and sign the new entry over it, append. `remember` and
the `@_decides` operations do it under the store lock, but consolidate, sleep, confirm, retract_lineage, recommit,
enable_receipts and declare_out_of_band_deletion did it with no lock. A `remember` on another thread between the read
and the append chained onto the same tail, and the store verified as tampered although nobody had touched it (measured
with consolidate and sleep beside three writers: "receipt 169: broken chain link", live and after a reopen).

Each test runs one emitter and, at the exact point between its read of the tail and its append, starts a `remember` on
another thread and waits for it up to WAIT seconds. With the chain lock the other thread waits instead, and both entries
land in order. The last test does the same between a flush's copy of the chain and its bookkeeping.
"""
from __future__ import annotations

import os
import sys
import threading

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from inspeximus import core  # noqa: E402
from inspeximus.core import Inspeximus  # noqa: E402

WAIT = 1.5


def _store(tmp_path, name="s.db", **kw):
    m = Inspeximus(str(tmp_path / name), receipts=True, **kw)
    for i in range(6):
        m.remember("note %d about the deploy window and the release slot" % i, source={"doc": "crm/alice"})
    m.flush()
    return m


def _race_at(monkeypatch, hook, caller, other):
    """Patch `Inspeximus.<hook>` so that, the first time `caller` calls it on this thread, `other` runs on a second thread
    while this one waits up to WAIT seconds. Returns the list that holds the second thread once it started."""
    real = Inspeximus.__dict__[hook].__func__
    main, started, done = threading.current_thread(), [], threading.Event()

    def run_other():
        try:
            other()
        finally:
            done.set()

    def hooked(*a):
        if (threading.current_thread() is main and not started
                and sys._getframe(1).f_code.co_name == caller):
            started.append(threading.Thread(target=run_other))
            started[0].start()
            done.wait(WAIT)
        return real(*a)
    monkeypatch.setattr(Inspeximus, hook, staticmethod(hooked))
    return started


def _assert_one_chain(m, path, started):
    assert started, "CONTROL: the other thread ran between the read of the tail and the append"
    started[0].join(30)
    m.flush()
    ok, problems = m.verify_writes()
    assert not [p for p in problems if "chain" in p or "receipt" in p and "DISABLED" not in p], \
        "the chain verifies as tampered on an honest store: %r" % (problems[:3],)
    ok2, problems2 = Inspeximus(path, receipts=True).verify_writes()
    assert not [p for p in problems2 if "chain" in p], "after a reopen: %r" % (problems2[:3],)


def _write_on_another_thread(m):
    return lambda: m.remember("a note written on another thread")


EMITTERS = {
    "consolidate": lambda m: m.consolidate(keep=2),
    "sleep": lambda m: m.sleep(keep=2),
    "confirm": None,                    # needs a provisional record, built below
    "retract_lineage": lambda m: m.retract_lineage("crm/alice"),
}


@pytest.mark.parametrize("emitter", sorted(EMITTERS))
def test_an_emitter_and_a_remember_on_two_threads_keep_one_chain(tmp_path, monkeypatch, emitter):
    m = _store(tmp_path)
    run = EMITTERS[emitter]
    if emitter == "confirm":
        pid = m.remember("a provisional note", provisional=True)
        run = lambda s: s.confirm(pid)                                   # noqa: E731
    before = len(m._receipts)
    started = _race_at(monkeypatch, "_chain_core", "_append_receipt", _write_on_another_thread(m))
    run(m)
    monkeypatch.undo()
    assert len(m._receipts) > before + 1, "CONTROL: %s appended a receipt" % emitter
    _assert_one_chain(m, str(tmp_path / "s.db"), started)


@pytest.mark.parametrize("backfill", [False, True])
def test_recommit_and_enable_receipts_beside_a_remember_keep_one_chain(tmp_path, monkeypatch, backfill):
    p = str(tmp_path / "r.db")
    m0 = Inspeximus(p)
    for i in range(4):
        m0.remember("a note written before receipts %d" % i)
    m0.flush()
    m = Inspeximus(p)
    started = _race_at(monkeypatch, "_chain_core", "_append_receipt", _write_on_another_thread(m))
    if backfill:
        m.enable_receipts()                                             # backfills a receipt per record
    else:
        m.enable_receipts(backfill_genesis=False)
        m.recommit()
    monkeypatch.undo()
    assert len(m._receipts) >= 4, "CONTROL: receipts were emitted"
    _assert_one_chain(m, p, started)


def test_a_tombstone_and_an_erasure_on_two_threads_keep_one_chain(tmp_path, monkeypatch):
    m = _store(tmp_path)
    gone, other = m._items[0]["id"], m._items[1]["id"]
    m._items = [r for r in m._items if r["id"] != gone]                  # deleted by hand, outside the library
    m._save(force=True)
    started = _race_at(monkeypatch, "_tombstone_core", "_seal_tombstone", lambda: m.forget(other))
    m.declare_out_of_band_deletion(gone, actor="ops", reason="deleted by hand")
    monkeypatch.undo()
    assert started, "CONTROL: the erasure ran between the read of the tail and the append"
    started[0].join(30)
    assert len(m._tombstones) == 2, "CONTROL: two tombstones"
    seqs = [t["seq"] for t in m._tombstones]
    links = [t.get("prev") for t in m._tombstones[1:]] == [t["hash"] for t in m._tombstones[:-1]]
    assert seqs == [0, 1] and links, "the tombstone chain forked: %r" % (
        [(t["seq"], t.get("prev", "")[:8], t["hash"][:8]) for t in m._tombstones],)


def test_a_receipt_appended_while_the_chain_is_written_is_written_too(tmp_path, monkeypatch):
    """The flush wrote the receipts it had read, then recorded how many receipts were on disk from the chain as it was by
    then. A receipt another thread appended in between was counted as written and never was, and that thread's own flush
    then had nothing left to write: the reopened store had one receipt fewer than the handle. Forced here: a remember on
    another thread lands while a flush writes the pending receipts."""
    m = _store(tmp_path)
    m._defer_receipt_flush = True                        # two receipts wait in memory for one flush
    m.remember("a pending note")
    m.remember("another pending note")
    m._defer_receipt_flush = False
    main, started, done = threading.current_thread(), [], threading.Event()

    def other():
        try:
            m.remember("a note written while the chain is written")
        finally:
            done.set()

    def hook(real):
        def wrapped(*a, **k):
            out = real(*a, **k)
            if threading.current_thread() is main and not started:
                started.append(threading.Thread(target=other))
                started[0].start()
                done.wait(WAIT)
            return out
        return wrapped
    monkeypatch.setattr(core, "_dump_chain_cached", hook(core._dump_chain_cached))   # a flush that compacts
    monkeypatch.setattr(core, "_receipt_texts", hook(core._receipt_texts))           # a flush that appends
    m._persist_receipts()
    monkeypatch.undo()
    assert started, "CONTROL: the other thread ran while the chain was written"
    started[0].join(30)
    reopened = Inspeximus(str(tmp_path / "s.db"), receipts=True)
    assert len(reopened._receipts) == len(m._receipts), "the reopened store holds %d receipts, this handle %d" % (
        len(reopened._receipts), len(m._receipts))
