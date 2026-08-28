# -*- coding: utf-8 -*-
"""The single-writer guard must refuse a second WRITER and stay silent for a second THREAD.

Both halves are here because either one alone can be satisfied by a broken guard. A guard that never
fires passes the concurrency half; a guard that always fires passes the cross-writer half. Only the
pair pins the behaviour.

THE DEFECT THIS EXISTS FOR. `_save` took a lock around the signature check and the write, then
refreshed `self._file_sig` AFTER releasing it. Thread A wrote, released, and before it stamped its
own signature thread B took the lock, compared the file against a signature that predated A's write,
and raised StoreChangedOnDisk about a competing PROCESS that did not exist. LangGraph calls a
checkpointer from its executor, so an ordinary `app.invoke` hit it: 3 to 15 failures in 40 runs,
load-dependent, which is why it read as flaky rather than broken for three weeks.

The earlier fix had already pulled the check and the write into one critical section. The window did
not close, it MOVED -- which is the reason this file tests the property rather than the shape of the
code that provides it.
"""
import os
import threading

import pytest

from inspeximus import Inspeximus
from inspeximus.core import StoreChangedOnDisk


def test_many_threads_on_one_handle_never_raise(tmp_path):
    """One handle, eight threads, a flush after every write: the guard has no peer to complain about."""
    store = Inspeximus(path=str(tmp_path / "a.json"))
    errors = []

    def writer(i):
        try:
            for k in range(15):
                store.remember("v%d-%d" % (i, k), key="k%d-%d" % (i, k))
                store.flush()
        except Exception as e:                                   # noqa: BLE001 - reported, not swallowed
            errors.append("%s: %s" % (type(e).__name__, e))

    threads = [threading.Thread(target=writer, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, "the guard fired on this process's own threads: %s" % errors[:3]
    assert len(store.items) == 120, "writes were lost: %d of 120" % len(store.items)


def test_a_second_handle_on_the_same_file_is_still_refused(tmp_path):
    """THE OTHER HALF. Silencing the peer case must not silence the case the guard exists for.

    Two handles each believe they hold the whole store, so whichever saves last erases the other's
    records. That is the loss this refusal prevents, and it was measured: B's committed, flushed
    record erased by A's next save, with verify_writes() reporting True on both sides because each
    chain was self-consistent.
    """
    path = str(tmp_path / "b.json")
    a = Inspeximus(path=path)
    a.remember("from A", key="a")
    a.flush()

    b = Inspeximus(path=path)          # a separate handle, as another process would have
    b.remember("from B", key="b")
    b.flush()

    with pytest.raises(StoreChangedOnDisk):
        a.remember("A again", key="a2")
        a.flush()


def test_the_file_signature_is_refreshed_before_the_lock_is_released(tmp_path):
    """A control on the MECHANISM, so a future refactor cannot reopen the window unnoticed.

    After a save, the handle's recorded signature must already match the file on disk. If the refresh
    ever moves back outside the critical section this stays true single-threaded, so it is not a
    substitute for the concurrency test above -- it is the cheap, deterministic half that names what
    the expensive one is protecting.
    """
    store = Inspeximus(path=str(tmp_path / "c.json"))
    store.remember("one", key="k")
    store.flush()
    assert store._file_sig == store._stat_sig(), \
        "the handle's signature does not match the file it just wrote"
    assert os.path.exists(str(tmp_path / "c.json"))
