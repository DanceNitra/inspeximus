"""3.20, AUDIT-A N-11 (Low-Medium): a client that stops sending cannot hold a server thread for ever.

THE DEFECT. SCRAPI, the witness server and the checkpoint witness each ran a threading HTTP server with no socket
timeout and no limit on threads. 300 connections that sent their headers and one byte of a 1,000-byte body held 300
server threads for as long as the client kept them open (AUDIT-A, dr/ss5/slowloris.py, on dcce3660).

WHAT IS PINNED HERE, on each of the three servers as shipped (their own make_server, default settings):
- 80 stalled connections start at most MAX_CONNECTIONS handler threads, and the ones over the cap are answered 503;
- the server closes a stalled connection after READ_TIMEOUT;
- once the stalled clients are gone, a normal request is answered.

HOW IT IS MEASURED (3.21.0 release; test (3.9) on Linux, run 39287875863, failed with "54 of the 16 connections over
the cap ended at once (0 with 503)"). The first version opened 80 stalled connections and expected 64 of them held
and 16 refused at once, all inside one read timeout. That assumes the server accepts 80 connections in well under
10 s. A forced window (agora_output/strategy/n321/n11_window.py, every accept slowed by 0.2 s) reproduced the failure
on Windows and on Linux: the listen backlog fills, the connects take 15 s, the first slots time out and are given
back before the last connections arrive, so nothing is ever over the cap and no 503 is sent, while 72 sockets end
inside the old window. The cap held throughout. So the two guarantees are tested apart, each at any accept speed:
- the cap, with the read timeout raised so no slot can be freed while the connections arrive: the slots fill, a new
  connection is refused with 503 (one that sends nothing, so no reset can drop it, as in test_3200_n14b), and the
  connections over the cap end at once, every one of them and no other;
- the read timeout, with the shipped READ_TIMEOUT and four stalled connections, well under the cap.
"""
import select
import os
import socket
import sys
import threading
import time

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
pytest.importorskip("cryptography")

STALLED = 80
CAP = 64                  # inspeximus._serve.MAX_CONNECTIONS
READ_TIMEOUT = 10.0       # inspeximus._serve.READ_TIMEOUT


def _scrapi(tmp_path):
    from inspeximus import scrapi
    from test_a_log_a_stranger_can_check_without_us import _service
    return scrapi.make_server(_service(str(tmp_path / "log"), 0), port=0), "/entries"


def _witness(tmp_path):
    from inspeximus import witness_server
    httpd, _ = witness_server.make_server(port=0, state_path=str(tmp_path / "w.json"))
    return httpd, "/cosign"


def _checkpoint(tmp_path):
    from inspeximus import witness_checkpoint as wc
    from test_3200_f4_a_checkpoint_witness_cosigns_one_root_per_size import LPK, ORIGIN, WPK, WSK
    w = wc.CheckpointWitness(str(tmp_path / "cw.json"), {ORIGIN: LPK}, "example.test/w", WSK, WPK)
    return wc.make_server(w, port=0), "/add-checkpoint"


def _stall(port, path):
    s = socket.create_connection(("127.0.0.1", port), timeout=30)
    s.sendall(("POST %s HTTP/1.1\r\nHost: t\r\nContent-Type: application/octet-stream\r\n"
               "Content-Length: 1000\r\n\r\nA" % path).encode("ascii"))
    return s


def _watch(socks, ended, stop):
    """Record, for every socket, when it ended and how: a 503, an EOF or a reset. All of them at once, from the start."""
    for s in socks:
        s.setblocking(False)
    while not stop.is_set() and len(ended) < len(socks):
        live = [s for s in socks if s not in ended]
        r, _, _ = select.select(live, [], [], 0.02)
        for s in r:
            try:
                b = s.recv(64)
                how = "503" if b.startswith(b"HTTP/1.1 503") else ("eof" if b == b"" else "data")
            except OSError:
                how = "reset"
            ended[s] = (time.monotonic(), how)


def _refused(port):
    """True when a new connection is answered 503. It sends nothing, so no unread byte makes the server's close a
    reset that drops the 503 (as in test_3200_n14b)."""
    s = socket.create_connection(("127.0.0.1", port), timeout=10)
    try:
        return s.recv(16).startswith(b"HTTP/1.1 503")
    finally:
        s.close()


SERVERS = pytest.mark.parametrize("make", [_scrapi, _witness, _checkpoint], ids=["scrapi", "witness", "checkpoint"])


def _start(httpd):
    threading.Thread(target=httpd.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
    return httpd.server_address[1]


@SERVERS
def test_stalled_connections_hold_at_most_the_cap_and_the_rest_are_refused(tmp_path, make, monkeypatch):
    # All 80 come from one address, so the per-address cap (N-14, its own test) is lifted to test the total cap. The
    # read timeout is raised so that a slot cannot be given back while the connections arrive, however slowly the
    # server accepts them: the cap is what is measured here, and the timeout has its own test below.
    from inspeximus import _serve as serve_mod
    monkeypatch.setattr(serve_mod, "MAX_PER_ADDRESS", 10 ** 6)
    monkeypatch.setattr(serve_mod, "READ_TIMEOUT", 120.0)
    httpd, path = make(tmp_path)
    slots = getattr(httpd, "_slots", None)
    assert slots is not None, "the server is not the bounded server (%s)" % type(httpd).__name__
    port = _start(httpd)
    base = threading.active_count()
    socks, born, ended, stop = [], [], {}, threading.Event()
    watcher = threading.Thread(target=_watch, args=(socks, ended, stop), daemon=True)
    try:
        for _ in range(STALLED):
            socks.append(_stall(port, path))
            born.append(time.monotonic())
        watcher.start()
        deadline = time.monotonic() + 60
        while (slots._value > 0 or len(ended) < STALLED - CAP) and time.monotonic() < deadline:
            time.sleep(0.05)
        time.sleep(0.5)
        held = threading.active_count() - base - 1          # the watcher is not a server thread
        assert slots._value == 0, "the %d stalled connections never filled the %d slots" % (STALLED, CAP)
        assert held <= CAP + 2, "%d stalled connections hold %d server threads (cap %d)" % (STALLED, held, CAP)
        assert _refused(port), "a new connection was not refused with 503 while every slot was held"
        # Over the cap: answered 503 and closed at accept; when the client's unread body byte turns the close into a
        # reset, the 503 can be lost, so a reset counts as well. Under the cap: held, not closed.
        hows = sorted(how for _t, how in ended.values())
        assert len(ended) == STALLED - CAP, (
            "%d stalled connections ended while every slot was held; the %d over the cap should, and only they (%s)"
            % (len(ended), STALLED - CAP, hows))
        assert all(h in ("503", "eof", "reset") for h in hows), hows
    finally:
        stop.set()
        for s in socks:
            s.close()
    try:
        deadline = time.monotonic() + 30                     # the clients left: every slot comes back
        while slots._value < CAP and time.monotonic() < deadline:
            time.sleep(0.05)
        ok = socket.create_connection(("127.0.0.1", port), timeout=10)
        ok.sendall(b"GET / HTTP/1.1\r\nHost: t\r\nConnection: close\r\n\r\n")
        first = ok.recv(16)
        assert first.startswith(b"HTTP/1.") and b" 503" not in first, (
            "a normal request after the stalled clients left got %r: a slot was not given back" % first)
        ok.close()
    finally:
        httpd.shutdown()
        httpd.server_close()


@SERVERS
def test_the_server_closes_a_stalled_connection_after_the_read_timeout(tmp_path, make):
    """The shipped READ_TIMEOUT, with four stalled connections, well under every cap: each is closed by the server,
    not before the timeout and not long after it."""
    httpd, path = make(tmp_path)
    port = _start(httpd)
    socks, born, ended, stop = [], [], {}, threading.Event()
    watcher = threading.Thread(target=_watch, args=(socks, ended, stop), daemon=True)
    try:
        for _ in range(4):
            socks.append(_stall(port, path))
            born.append(time.monotonic())
        watcher.start()
        watcher.join(READ_TIMEOUT + 10)
        assert len(ended) == 4, "%d of 4 stalled connections still open after %.0f s" % (4 - len(ended), READ_TIMEOUT + 10)
        after = sorted(round(ended[s][0] - b, 2) for s, b in zip(socks, born))
        assert all(READ_TIMEOUT - 1 <= a <= READ_TIMEOUT + 10 for a in after), (
            "stalled connections closed after %s s (read timeout %.0f s)" % (after, READ_TIMEOUT))
    finally:
        stop.set()
        for s in socks:
            s.close()
        httpd.shutdown()
        httpd.server_close()
