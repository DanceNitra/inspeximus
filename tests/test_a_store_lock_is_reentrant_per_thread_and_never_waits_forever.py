"""The store lock is re-entrant per thread, and every wait for it ends (3.15.6).

Measured 2026-09-28: a broad suite on the lock-then-decide helper (A-42/A-43) sat at 98% for fifteen hours. The
in-process half of `_StoreLock` was a plain threading.Lock, taken without a timeout: an operation holding the
lock that reached code opening a SECOND handle on the same store, in the same thread, and writing through it,
waited on itself for ever. Now the lock is re-entrant per thread and path (a depth count and the owning thread),
another thread still waits, every wait has one deadline on every OS, and the deadline raises StoreLockTimeout
instead of writing unprotected.

Each call that could hang runs in a helper thread joined with a timeout, so a regression FAILS here instead of
hanging the run.
"""
import os
import subprocess
import sys
import textwrap
import threading
import time

import pytest

import inspeximus.core as core
from inspeximus import Inspeximus
from inspeximus.core import StoreLockTimeout, _StoreLock


@pytest.fixture(autouse=True)
def _no_env(monkeypatch):
    for k in [k for k in os.environ if k.startswith("INSPEXIMUS_") and k != "INSPEXIMUS_KEY_HOME"]:
        monkeypatch.delenv(k)


def _bounded(fn, seconds=60):
    """Run fn in a thread; return its result or raise; fail if it does not finish."""
    out = {}

    def run():
        try:
            out["v"] = fn()
        except BaseException as e:                  # noqa: BLE001
            out["e"] = e
    t = threading.Thread(target=run, daemon=True)
    t.start()
    t.join(seconds)
    if t.is_alive():
        pytest.fail(f"did not finish in {seconds}s: a lock wait with no end")
    if "e" in out:
        raise out["e"]
    return out.get("v")


def _store(tmp_path):
    p = str(tmp_path / "store.json")
    m = Inspeximus(p)
    rid = m.remember("a useful fact", key="fact", object="x")
    m.flush()
    return p, rid


def test_a_second_handle_in_the_same_thread_goes_through(tmp_path):
    p, rid = _store(tmp_path)

    def nested():
        outer = Inspeximus(p)
        with outer._deciding():
            inner = Inspeximus(p)
            inner.credit(rid, 1.0)                  # decides and saves under the outer hold
            inner.remember("colour v1", key="colour", object="v1")
            inner.flush()
        return True
    assert _bounded(nested)
    assert next(r for r in Inspeximus(p).items if r["id"] == rid).get("good") == 1.0


def test_the_depth_returns_to_zero_and_the_path_is_free(tmp_path):
    p, rid = _store(tmp_path)
    key = os.path.normcase(os.path.realpath(os.path.abspath(p))) + ".lock"

    def nested():
        with _StoreLock(p):
            assert _StoreLock._OWNER[key][1] == 1
            with _StoreLock(p):
                assert _StoreLock._OWNER[key][1] == 2
            assert _StoreLock._OWNER[key][1] == 1
        return key not in _StoreLock._OWNER
    assert _bounded(nested) is True
    assert _bounded(lambda: Inspeximus(p).credit(rid, 1.0))


def test_another_thread_waits_and_then_gets_a_named_error(tmp_path, monkeypatch):
    p, _ = _store(tmp_path)
    monkeypatch.setattr(core, "LOCK_WAIT_S", 0.5)
    held, release = threading.Event(), threading.Event()

    def holder():
        with _StoreLock(p):
            held.set()
            release.wait(30)
    t = threading.Thread(target=holder, daemon=True)
    t.start()
    assert held.wait(10), "control: the holder took the lock"
    try:
        t0 = time.time()
        with pytest.raises(StoreLockTimeout):
            _bounded(lambda: _StoreLock(p).__enter__(), seconds=30)
        assert time.time() - t0 < 20
    finally:
        release.set()
        t.join(10)
    assert _bounded(lambda: Inspeximus(p).remember("after the holder", key="k", object="1"))


HOLD = textwrap.dedent("""
    import sys, time
    sys.path.insert(0, sys.argv[1])
    from inspeximus.core import _StoreLock
    with _StoreLock(sys.argv[2]):
        print("held", flush=True)
        time.sleep(float(sys.argv[3]))
""")


def test_a_lock_another_process_holds_gives_the_error_not_a_hang(tmp_path, monkeypatch):
    p, _ = _store(tmp_path)
    root = os.path.dirname(os.path.dirname(os.path.abspath(core.__file__)))
    proc = subprocess.Popen([sys.executable, "-c", HOLD, root, p, "30"], stdout=subprocess.PIPE, text=True)
    try:
        assert proc.stdout.readline().strip() == "held", "control: the other process holds the lock"
        monkeypatch.setattr(core, "LOCK_WAIT_S", 1.0)
        t0 = time.time()
        with pytest.raises(StoreLockTimeout):
            _bounded(lambda: _StoreLock(p).__enter__(), seconds=40)
        assert time.time() - t0 < 30
    finally:
        proc.kill()
        proc.wait(10)


def test_a_subscriber_that_writes_through_another_handle_does_not_deadlock(tmp_path):
    """The shape the suite hung on: an operation holding the lock whose save fires a subscriber, and the
    subscriber writes to the same store through a handle of its own. Subscribers now run after the hold is
    released, and the lock would let the same thread through anyway."""
    p, rid = _store(tmp_path)
    seen = []

    def go():
        m = Inspeximus(p)
        other = Inspeximus(p)

        def on_event(ev):
            seen.append(ev)
            other.remember("written from a subscriber", key=f"echo{len(seen)}", object=str(len(seen)))
            other.flush()
        m.subscribe("*", on_event)
        m.credit(rid, 1.0)
        m.remember("colour v1", key="colour", object="v1")
        m.flush()
        return True
    assert _bounded(go)


def test_a_decision_whose_merge_migrates_the_store_does_not_deadlock(tmp_path, monkeypatch):
    """THE PATH THE SUITE HUNG ON, from its stack dump (test_a37_..._unlocked_write_right_after_the_replace):
    _deciding holds the store lock -> _sync_before_decision -> _merge_with_disk -> _load_from_disk ->
    _migrate_json_store, which takes _StoreLock on the same path again, in the same thread. A JSON store is
    written while the format is pinned to json; the pin is lifted, a peer changes the file, and a keyed
    write's decision merges, which migrates the file to rows inside the hold."""
    p = str(tmp_path / "store.json")
    monkeypatch.setenv("INSPEXIMUS_STORE_FORMAT", "json")
    m = Inspeximus(p)
    m.remember("the first value", key="colour", object="v1")
    m.flush()
    peer = Inspeximus(p)
    peer.remember("a peer's record", key="other", object="o")
    peer.flush()
    monkeypatch.delenv("INSPEXIMUS_STORE_FORMAT")
    assert _bounded(lambda: m.remember("the second value", key="colour", object="v2") or True)
    m.flush()
    from inspeximus import sqlite_store as ss
    assert ss.looks_like_sqlite(p), "control: the merge did migrate the store to rows"
    active = [r.get("object") for r in Inspeximus(p).items if r.get("key") == "colour" and r.get("status") == "active"]
    assert active == ["v2"]
