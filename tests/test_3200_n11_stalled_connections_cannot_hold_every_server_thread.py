"""3.20, AUDIT-A N-11 (Low-Medium): a client that stops sending cannot hold a server thread for ever.

THE DEFECT. SCRAPI, the witness server and the checkpoint witness each ran a threading HTTP server with no socket
timeout and no limit on threads. 300 connections that sent their headers and one byte of a 1,000-byte body held 300
server threads for as long as the client kept them open (AUDIT-A, dr/ss5/slowloris.py, on dcce3660).

WHAT IS PINNED HERE, on each of the three servers as shipped (their own make_server, default settings):
- 80 stalled connections start at most MAX_CONNECTIONS handler threads, and the ones over the cap are answered 503;
- the server closes a stalled connection after READ_TIMEOUT;
- once the stalled clients are gone, a normal request is answered.
"""
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


def _closed_or_busy(s, deadline):
    """b'' when the server closed the connection, the first bytes when it answered, None at the deadline."""
    s.settimeout(max(0.1, deadline - time.monotonic()))
    try:
        return s.recv(64)
    except socket.timeout:
        return None
    except OSError:
        return b""


@pytest.mark.parametrize("make", [_scrapi, _witness, _checkpoint], ids=["scrapi", "witness", "checkpoint"])
def test_stalled_connections_hold_at_most_the_cap_and_are_closed(tmp_path, make, monkeypatch):
    # All 80 come from one address, so the per-address cap (N-14, its own test) is lifted to test the total cap.
    from inspeximus import _serve as serve_mod
    monkeypatch.setattr(serve_mod, "MAX_PER_ADDRESS", 10 ** 6)
    httpd, path = make(tmp_path)
    port = httpd.server_address[1]
    t = threading.Thread(target=httpd.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
    t.start()
    base = threading.active_count()
    socks = []
    try:
        socks = [_stall(port, path) for _ in range(STALLED)]
        deadline = time.monotonic() + 5
        while threading.active_count() - base < min(STALLED, CAP) and time.monotonic() < deadline:
            time.sleep(0.05)
        time.sleep(0.5)
        held = threading.active_count() - base
        assert held <= CAP + 2, "%d stalled connections hold %d server threads (cap %d)" % (STALLED, held, CAP)

        # Over the cap: answered 503 and closed at once. On Windows the close can reset the connection before the
        # 503 is read (the client's body byte is unread), so an early close counts too, and at least one 503 must be
        # seen.
        early = [_closed_or_busy(s, time.monotonic() + 0.05) for s in socks]
        ended = sum(a is not None for a in early)
        busy = sum(bool(a) and a.startswith(b"HTTP/1.1 503") for a in early)
        assert ended >= STALLED - CAP and busy >= 1, (
            "%d of the %d connections over the cap ended at once (%d with 503)" % (ended, STALLED - CAP, busy))

        deadline = time.monotonic() + READ_TIMEOUT + 10
        rest = [_closed_or_busy(s, deadline) if a is None else a for s, a in zip(socks, early)]
        still_open = sum(a is None for a in rest)
        assert still_open == 0, "%d stalled connections still open after %.0f s" % (still_open, READ_TIMEOUT + 10)

        ok = socket.create_connection(("127.0.0.1", port), timeout=10)
        ok.sendall(b"GET / HTTP/1.1\r\nHost: t\r\nConnection: close\r\n\r\n")
        first = ok.recv(16)
        assert first.startswith(b"HTTP/1.") and b" 503" not in first, (
            "a normal request after the stalled clients left got %r: a slot was not given back" % first)
        ok.close()
    finally:
        for s in socks:
            s.close()
        httpd.shutdown()
        httpd.server_close()
