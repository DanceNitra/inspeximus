"""Silent connections cannot push the prompt hook off the daemon (3.19, item 3).

On 3.18.0 a connection that sent nothing held one of MAX_CONNECTIONS (16) handshake slots for SERVER_READ_TIMEOUT_S
(0.5 s), and the daemon closed any connection beyond them at accept. Sixteen silent clients therefore refused every
hook for as long as they stayed, and three refusals in a row put the hook into BACKOFF_S of today's path.

A new connection that finds every slot taken now evicts the oldest connection that has not said hello. While a slot is
free, a connection keeps SERVER_READ_TIMEOUT_S for its hello, so a slow client is not sent to its normal path. A client
that said hello keeps its slot, so the bound on threads holds and only an owner of the token gets past the handshake.
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




def _silent(d, n):
    """`n` connected-but-silent clients, and the daemon has TAKEN a slot for each before this returns.

    A FIXED SLEEP IS NOT A WAIT FOR THE SERVER. `_connect` returning means the client side connected;
    the daemon takes its slot on a different thread, at `self.conns.acquire`. Two CI runs of the same
    commit failed here on a `time.sleep(0.05)`, on a different Python each time and with a different
    count -- `assert 8 == 0`, then `assert 1 == 0` -- which is a loaded runner, not a defect in the
    daemon. The wait is bounded and its timeout still fails the test, so the control keeps its teeth:
    if the daemon never takes the slots, this raises rather than passing quietly.
    """
    out = [hookd._connect(d.addr, time.monotonic() + 1.0) for _ in range(n)]
    assert all(c is not None for c in out), "CONTROL: every silent connection was accepted"
    want = max(0, hookd.MAX_CONNECTIONS - n)
    deadline = time.monotonic() + 10.0
    while time.monotonic() < deadline:
        if d.conns._value <= want:
            return out
        time.sleep(0.01)
    raise AssertionError(
        "CONTROL: the daemon did not take a slot for each of the %d silent connections within 10 s "
        "(free slots %d, wanted at most %d)" % (n, d.conns._value, want))


def _close(conns):
    for c in conns:
        try:
            c.close()
        except Exception:                                   # noqa: BLE001
            pass


def test_sixteen_silent_connections_do_not_refuse_the_hook(project, daemon):
    """The hook's own bound, REPLY_TIMEOUT_S, three asks in a row: on 3.18.0 each one was refused at accept, and the
    third put the hook into its back-off."""
    proj, sp = project
    hookd.ask(_ev(proj), sp, timeout=2.0)                       # warm
    silent = _silent(daemon, hookd.MAX_CONNECTIONS)
    try:
        got = []
        for _ in range(3):
            hookd.ask(_ev(proj), sp)
            got.append(hookd.LAST["outcome"])
        assert got.count("served") >= 2, "silent connections pushed the hook onto its normal path: %r" % got
        assert "backoff" not in got
    finally:
        _close(silent)


def test_a_renewed_flood_of_silent_connections_does_not_refuse_the_hook(project, daemon):
    """A client that reopens its silent connections as fast as they are dropped keeps MAX_CONNECTIONS of them pending."""
    proj, sp = project
    hookd.ask(_ev(proj), sp, timeout=2.0)
    stop, held, arrived = threading.Event(), [], threading.Event()

    def flood():
        arrived.set()                    # the asks below rely on a running flood (3.19.1, item 13)
        while not stop.is_set():
            if len(held) < hookd.MAX_CONNECTIONS * 2:
                c = hookd._connect(daemon.addr, time.monotonic() + 0.2)
                if c is not None:
                    held.append(c)
            else:
                _close(held[:hookd.MAX_CONNECTIONS])
                del held[:hookd.MAX_CONNECTIONS]
    th = threading.Thread(target=flood, daemon=True)
    th.start()
    try:
        assert arrived.wait(30), "CONTROL: the flood is running"
        time.sleep(0.3)
        got = []
        for _ in range(6):
            hookd.ask(_ev(proj), sp, timeout=1.0)
            got.append(hookd.LAST["outcome"])
        assert got.count("served") >= 5, "a renewed flood refused the hook: %r" % got
    finally:
        stop.set()
        th.join(5)
        _close(held)


def test_the_flood_never_holds_more_than_the_slot_bound(project, daemon):
    """The thread bound of AUDIT-A E-4 holds: at no time more than MAX_CONNECTIONS handshake threads, however many
    silent connections arrive. Counted as threads whose target is the daemon's `_serve_thread`."""
    proj, sp = project
    peak, opened, stop, arrived = [0], [], threading.Event(), threading.Event()

    def count():
        arrived.set()                    # the peak below needs a running sampler (3.19.1, item 13)
        while not stop.is_set():
            n = sum(1 for t in threading.enumerate() if getattr(t, "_target", None) == daemon._serve_thread)
            peak[0] = max(peak[0], n)
            time.sleep(0.001)
    th = threading.Thread(target=count, daemon=True)
    th.start()
    try:
        assert arrived.wait(30), "CONTROL: the sampler is running"
        for _ in range(hookd.MAX_CONNECTIONS * 3):
            c = hookd._connect(daemon.addr, time.monotonic() + 0.3)
            if c is not None:
                opened.append(c)
        time.sleep(0.2)
        assert len(opened) > hookd.MAX_CONNECTIONS, "CONTROL: more silent connections than slots (%d)" % len(opened)
        assert 0 < peak[0] <= hookd.MAX_CONNECTIONS, "%d handshake threads at once" % peak[0]
    finally:
        stop.set()
        th.join(2)
        _close(opened)


def test_an_evicted_connection_gets_nothing(project, daemon):
    """Eviction closes a connection that never said hello, and it receives no byte."""
    proj, sp = project
    first = _silent(daemon, 1)[0]
    rest = _silent(daemon, hookd.MAX_CONNECTIONS)                # the last of these evicts `first`
    try:
        t0 = time.monotonic()
        got = None
        try:
            got = first.recv_bytes() if first.poll(1.0) else "still open"
        except (EOFError, OSError):                     # closed by the daemon with nothing sent: the expected end
            got = None
        assert got is None, "the evicted connection received %r" % (got,)
        assert time.monotonic() - t0 < hookd.SERVER_READ_TIMEOUT_S, "CONTROL: it was evicted, not timed out"
    finally:
        _close([first] + rest)


def test_a_slow_client_is_served_while_slots_are_free(project, daemon, monkeypatch):
    """A hello that comes 0.3 s after the connect, with free slots, is served: a loaded machine must not send a hook to
    its normal path. A first version gave the hello 0.1 s and closed it."""
    proj, sp = project
    hookd.ask(_ev(proj), sp, timeout=2.0)
    real = hookd._send
    first = {"done": False}

    def slow(conn, obj):
        if not first["done"] and isinstance(obj, dict) and "hello" in obj:
            first["done"] = True
            time.sleep(0.3)
        return real(conn, obj)
    monkeypatch.setattr(hookd, "_send", slow)
    got = hookd.ask(_ev(proj), sp, timeout=2.0)
    assert first["done"], "CONTROL: the hello was delayed"
    assert got is not None and hookd.LAST["outcome"] == "served", "a slow hello was closed: %r" % hookd.LAST


def test_eviction_alone_frees_a_slot(project, daemon, monkeypatch):
    """With a long read timeout only the eviction can free a slot: the hook is served beside MAX_CONNECTIONS fresh
    silent connections because the oldest of them is told to go, and goes at its next poll."""
    proj, sp = project
    hookd.ask(_ev(proj), sp, timeout=2.0)
    monkeypatch.setattr(hookd, "SERVER_READ_TIMEOUT_S", 2.0)
    silent = _silent(daemon, hookd.MAX_CONNECTIONS)
    try:
        assert daemon.conns._value == 0, "CONTROL: every slot is held by a silent connection"
        got = hookd.ask(_ev(proj), sp, timeout=1.0)
        assert got is not None and hookd.LAST["outcome"] == "served", "no slot was freed for the hook: %r" % hookd.LAST
    finally:
        _close(silent)


@pytest.mark.skipif(os.name == "nt", reason="the AF_UNIX listen queue")
def test_the_listen_queue_takes_a_burst(project, daemon):
    """multiprocessing's default backlog is 1: a burst of connects without any retry was refused at once, EAGAIN."""
    import socket
    socks, refused = [], 0
    try:
        for _ in range(2 * hookd.MAX_CONNECTIONS):
            s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            s.setblocking(False)
            try:
                s.connect(daemon.addr)
                socks.append(s)
            except BlockingIOError:
                refused += 1
                s.close()
        assert refused == 0, "%d of %d connects refused by a full listen queue" % (refused, 2 * hookd.MAX_CONNECTIONS)
    finally:
        for s in socks:
            s.close()


@pytest.mark.skipif(os.name == "nt", reason="the AF_UNIX listen queue")
def test_a_connect_to_a_full_queue_waits_for_room():
    """The client waits for a full queue until its deadline, as it waits for a busy pipe on Windows."""
    import socket
    import tempfile
    short = tempfile.mkdtemp(prefix="q", dir="/tmp")         # a CI-length tmp_path is past the AF_UNIX limit
    addr = os.path.join(short, "q.sock")
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(addr)
    srv.listen(0)
    fill = []
    try:
        while True:                                      # fill the queue until the kernel refuses
            s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            s.setblocking(False)
            try:
                s.connect(addr)
                fill.append(s)
            except BlockingIOError:
                s.close()
                break
            assert len(fill) < 64, "CONTROL: the queue never filled"
        got = {}
        th = threading.Thread(target=lambda: got.setdefault("c", hookd._connect(addr, time.monotonic() + 1.0)))
        th.start()
        time.sleep(0.1)
        srv.settimeout(1.0)                              # a client that gave up must fail the test, not hang it
        accepted = []
        try:
            for _ in range(len(fill) + 1):               # room, and the waiting client
                accepted.append(srv.accept()[0])
        except OSError:
            pass
        th.join(2)
        assert got.get("c") is not None, "a connect to a full queue gave up instead of waiting for room"
        for a in accepted:
            a.close()
        got["c"].close()
    finally:
        for s in fill:
            s.close()
        srv.close()
        os.unlink(addr)
        os.rmdir(short)
