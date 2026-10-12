"""An erased record leaves the prompt daemon's memory without waiting for a request (3.19, item 2).

On 3.18.0 the daemon compared a held store's signature only when a request arrived, so a record erased by another
process stayed in the daemon's memory until the next prompt, or until the daemon exited after IDLE_EXIT_S (600 s)
without one. Measured on 3.18.0: the erased record was still held 3 s after the erasure, with no request in between.

The watchdog now compares each held store's signature every HELD_CHECK_S and drops a handle whose store moved. The
next request reopens it, as a request after a write did before, so the reopen count per prompt does not change.
"""
from __future__ import annotations

import os
import subprocess
import sys
import threading
import time

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from inspeximus import hookd  # noqa: E402
from inspeximus._surface import coding_store_path  # noqa: E402
from inspeximus.core import Inspeximus  # noqa: E402

PROMPT = "when is the deploy window"
#: The longest the tests wait for the watchdog, well above HELD_CHECK_S so a slow runner does not fail them.
WAIT_S = 3.0


@pytest.fixture
def project(tmp_path):
    proj = tmp_path / "proj"
    proj.mkdir()
    subprocess.run(["git", "init", "-q", str(proj)], check=True)
    sp = coding_store_path(str(proj))
    os.makedirs(os.path.dirname(sp), exist_ok=True)
    m = Inspeximus(sp)
    for i in range(20):
        m.remember("we decided the deploy window is Tuesday %d" % i, key="decision::deploy%d" % i, tags=["decision"])
    m.flush()
    return str(proj), sp


@pytest.fixture
def daemon(project):
    proj, sp = project
    d = hookd.Daemon(sp, idle_exit_s=60)
    th = threading.Thread(target=d.serve, kwargs={"warm_cwd": proj}, daemon=True)
    th.start()
    for _ in range(100):
        if hookd._read_token(d.kh, d.tag):
            break
        time.sleep(0.05)
    yield d
    d.stop = True
    d._wake()
    th.join(10)


def _ev(proj):
    return {"hook_event_name": "UserPromptSubmit", "prompt": PROMPT, "cwd": proj, "session_id": "s"}


def _held_ids(d):
    """Every record id the daemon still holds anywhere: the held handles' records and id-keyed caches, the recall
    index's pools, and the handles the foreign-stamp count keeps."""
    handles = [m for _sig, m in list(d.held.values())] + [m for m, _n in list(d.counts.values())]
    out = set()
    for m in handles:
        out |= {r.get("id") for r in list(m._items)}
        for name in ("_tok_cache", "_sig_cache", "_tc_cache", "_vnorm"):
            out |= set(getattr(m, name, None) or ())
        for e in list((getattr(m, "_recall_ix", None) or {}).values()):
            out |= {r.get("id") for r in list(e.get("pool") or []) + list(e.get("snap") or [])}
    return out


def _wait_until(pred, limit=WAIT_S):
    t0 = time.monotonic()
    while not pred() and time.monotonic() - t0 < limit:
        time.sleep(0.02)
    return time.monotonic() - t0


def _served_once(proj, sp, rid, d):
    assert "Thursday" in hookd.ask(_ev(proj), sp, timeout=2.0), "CONTROL: the record is served"
    assert hookd.LAST["outcome"] == "served"
    assert rid in _held_ids(d), "CONTROL: the daemon holds the record before the erasure"


@pytest.mark.parametrize("how", ["forget", "forget_subject"])
def test_an_erasure_leaves_the_daemon_without_a_request(project, daemon, how):
    proj, sp = project
    p = Inspeximus(sp)
    rid = p.remember("we decided the deploy window moved to Thursday", key="decision::deploy0", tags=["decision"],
                     source={"doc": "customer-7"})
    p.flush()
    _served_once(proj, sp, rid, daemon)
    n_asks = daemon.last_request
    before = [m for _s, m in list(daemon.held.values()) if any(r.get("id") == rid for r in list(m._items))]
    assert before, "CONTROL: a held handle holds the record"
    q = Inspeximus(sp)
    if how == "forget":
        q.forget(rid)
    else:
        q.forget_subject("customer-7")
    q.flush()
    took = _wait_until(lambda: rid not in _held_ids(daemon))
    assert daemon.last_request == n_asks, "CONTROL: no request reached the daemon while it waited"
    assert rid not in _held_ids(daemon), "the erased record is still in the daemon's memory %.1f s later" % took
    assert all(not m._items for m in before), "the dropped handle still holds its records until the collector runs"
    got = hookd.ask(_ev(proj), sp, timeout=2.0)
    assert hookd.LAST["outcome"] == "served" and "Thursday" not in got, "the next answer served the erased record"


def test_an_unchanged_store_keeps_its_handle(project, daemon):
    """The watchdog drops a handle only when its store moved: an idle daemon keeps its warm handle."""
    proj, sp = project
    hookd.ask(_ev(proj), sp, timeout=2.0)
    held = {k: v[1] for k, v in daemon.held.items()}
    assert held, "CONTROL: the daemon holds a handle"
    time.sleep(4 * getattr(hookd, "HELD_CHECK_S", 0.25) + 0.2)
    assert {k: v[1] for k, v in daemon.held.items()} == held, "the watchdog dropped the handle of an unchanged store"
    n = daemon.reopens
    hookd.ask(_ev(proj), sp, timeout=2.0)
    assert daemon.reopens == n, "an unchanged store was reopened"


def test_a_write_costs_one_reopen_at_the_next_request_as_before(project, daemon):
    """A dropped handle is reopened by the next request, once, as a request after a write reopened it on 3.18.0."""
    proj, sp = project
    hookd.ask(_ev(proj), sp, timeout=2.0)
    n = daemon.reopens
    p = Inspeximus(sp)
    for i in range(3):
        p.remember("an unrelated note %d" % i)
        p.flush()
    _wait_until(lambda: not daemon.held)
    assert daemon.reopens == n, "the watchdog reopened the store itself: work no prompt asked for"
    hookd.ask(_ev(proj), sp, timeout=2.0)
    assert hookd.LAST["outcome"] == "served"
    assert daemon.reopens == n + 1, "a request after the writes reopened the store %d times" % (daemon.reopens - n)


def test_a_failing_check_does_not_stop_the_daemon(project, daemon, monkeypatch):
    proj, sp = project
    hookd.ask(_ev(proj), sp, timeout=2.0)

    def broken(_path):
        raise OSError("simulated")
    monkeypatch.setattr(hookd, "store_signature", broken)
    time.sleep(4 * getattr(hookd, "HELD_CHECK_S", 0.25) + 0.2)
    assert not daemon.stop, "an error in the watchdog's check stopped the daemon"
    monkeypatch.undo()
    p = Inspeximus(sp)
    rid = p.remember("we decided the deploy window moved to Thursday", key="decision::deploy0", tags=["decision"])
    p.flush()
    _served_once(proj, sp, rid, daemon)
    q = Inspeximus(sp)
    q.forget(rid)
    q.flush()
    _wait_until(lambda: rid not in _held_ids(daemon))
    assert rid not in _held_ids(daemon), "the watchdog stopped checking after one failed check"


def test_a_handle_current_again_under_the_lock_is_kept(project, daemon, monkeypatch):
    """The scan runs outside the answer lock, so an answer can reopen the store between the scan and the drop. The drop
    compares again under the lock and keeps a handle that is current. Forced here: the scan sees a moved signature, the
    comparison under the lock sees the real one."""
    proj, sp = project
    hookd.ask(_ev(proj), sp, timeout=2.0)
    daemon.stop = True                                  # the watchdog's own ticks would race this direct call
    time.sleep(2 * getattr(hookd, "HELD_CHECK_S", 0.25))
    held = {k: v[1] for k, v in daemon.held.items()}
    real = hookd.store_signature
    calls = {"n": 0}

    def first_call_moved(path, *a, **k):
        calls["n"] += 1
        return "moved" if calls["n"] <= len(held) else real(path, *a, **k)
    monkeypatch.setattr(hookd, "store_signature", first_call_moved)
    monkeypatch.setattr(hookd, "store_mark", lambda path: ("moved",), raising=False)   # past the cheap mark
    assert daemon.drop_moved() == 0, "a handle that was current under the lock was dropped"
    assert calls["n"] > len(held), "CONTROL: the drop compared again under the lock"
    assert {k: v[1] for k, v in daemon.held.items()} == held
