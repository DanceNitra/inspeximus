"""The privileged-write window must not be shared between threads.

The reserved keyspace stops a caller handing itself a trust tier the library is supposed to grant. The
mechanism is a call-path one: a privileged helper bumps a counter inside a try/finally and calls the
ordinary write path, so reserved keys are honoured only while that counter is up. The argument for it
is that a new internal marker inherits privilege by construction and nothing reachable from a caller
can open the door.

That argument is about WHICH CODE opens the door. It says nothing about who else is standing in the
doorway while it is open. Measured 2026-08-11 with the counter as a plain instance attribute: on an
in-memory store shared by two threads, a forged reserved key survived on 400 of 400 caller writes made
during another thread's privileged window. File-backed stores never reached it, but only because the
single-writer guard refuses a second writer first -- protection by accident, not by design, and it
disappears the moment a store is in memory.

The regression test is written against the FAILING SHAPE rather than the fixed one: it holds a
privileged window open in one thread and asserts a caller write in another is still stripped.
"""
import threading
import time

from inspeximus import Inspeximus

FORGED = {"graduated_from_episodic": True}


def _survived(store, mid):
    rec = [r for r in store.items if r["id"] == mid]
    return bool(rec) and "graduated_from_episodic" in (rec[0].get("meta") or {})


def test_a_caller_write_is_stripped_inside_another_threads_privileged_window():
    ix = Inspeximus(path=None)

    # CONTROL FIRST: with no window open anywhere the key must already be stripped, or a clean result
    # below would be measuring a broken reservation instead of a closed race.
    assert not _survived(ix, ix.remember("caller", mtype="semantic", meta=dict(FORGED))), \
        "the reserved key is not stripped even single-threaded -- this test cannot see a race"

    stop, errors, opened = threading.Event(), [], threading.Event()

    def hold_window():
        while not stop.is_set():
            try:
                ix._stamp("internal marker", meta={"session_seq": 1})
                opened.set()
            except Exception as e:
                errors.append(repr(e))
                return

    t = threading.Thread(target=hold_window, daemon=True)
    t.start()
    assert opened.wait(timeout=5), "the privileged writer never ran; the window was never open"

    leaked = 0
    for _ in range(200):
        if _survived(ix, ix.remember("caller", mtype="semantic", meta=dict(FORGED))):
            leaked += 1
    stop.set()
    t.join(timeout=5)

    assert not errors, (
        "the privileged writer raised (%r), so its window closed early and a clean result here would "
        "understate the exposure" % errors[:1])
    assert leaked == 0, (
        "%d of 200 caller writes kept a forged reserved key while another thread held the privileged "
        "window open -- the window is shared, not per-thread" % leaked)


def test_the_window_closes_on_its_own_thread_after_an_exception():
    """A privileged write that raises must not leave the door open behind it. try/finally is what makes
    the counter safe within a thread, and it is the half that still matters after the thread-local fix."""
    ix = Inspeximus(path=None)
    try:
        ix._stamp(None)                      # invalid text: the write path raises inside the window
    except Exception:
        pass
    assert not _survived(ix, ix.remember("caller", mtype="semantic", meta=dict(FORGED))), \
        "a raised privileged write left the reserved-key window open on this thread"
