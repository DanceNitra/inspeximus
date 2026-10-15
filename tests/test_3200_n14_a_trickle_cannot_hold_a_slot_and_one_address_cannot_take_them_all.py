"""3.20, AUDIT-A N-14 (Low): a trickling client is cut at a deadline, and one address cannot take every slot.

THE DEFECT, a cost of the N-11 fix. N-11 capped a server at 64 requests at once and timed out each READ after 10 s. A
client that sends one byte every few seconds never waits 10 s for a read, so 64 such connections held every slot for
as long as they kept sending, and an honest client got 503 meanwhile (AUDIT-A, dr/ss5/serve_cap.py, on d0e5b2ed).
3.19.1, with no cap, still answered a normal request with 300 stalled connections open.

WHAT IS PINNED HERE, on each of the three servers as shipped:
- a request is closed REQUEST_DEADLINE seconds after it was accepted, however it keeps sending (the test sets the
  deadline to 3 s and trickles a byte every 0.5 s);
- one client address holds at most MAX_PER_ADDRESS requests at once; its next connection is answered 503, and a
  client from another address is still served, and the address gets its slots back when its clients leave.
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

from test_3200_n11_stalled_connections_cannot_hold_every_server_thread import (  # noqa: E402
    _checkpoint, _scrapi, _stall, _witness)

SERVERS = pytest.mark.parametrize("make", [_scrapi, _witness, _checkpoint], ids=["scrapi", "witness", "checkpoint"])


def _serve(httpd):
    t = threading.Thread(target=httpd.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
    t.start()
    return httpd.server_address[1]


def _stop(httpd, socks):
    for s in socks:
        s.close()
    httpd.shutdown()
    httpd.server_close()


@SERVERS
def test_a_trickling_request_is_closed_at_the_deadline(tmp_path, make, monkeypatch):
    from inspeximus import _serve as serve_mod
    monkeypatch.setattr(serve_mod, "REQUEST_DEADLINE", 3.0, raising=False)
    httpd, path = make(tmp_path)
    port = _serve(httpd)
    s = _stall(port, path)
    t0 = time.monotonic()
    closed_at = None
    try:
        s.settimeout(0.5)
        while time.monotonic() - t0 < 12:
            try:
                s.sendall(b"A")                       # one byte every 0.5 s: no read ever waits 10 s
                if s.recv(1) == b"":
                    closed_at = time.monotonic() - t0
                    break
            except socket.timeout:
                continue
            except OSError:
                closed_at = time.monotonic() - t0
                break
        assert closed_at is not None, "a trickling request held its slot for 12 s (deadline 3 s)"
        assert closed_at < 3.0 + 2.5, "a trickling request was closed after %.1f s (deadline 3 s)" % closed_at
    finally:
        _stop(httpd, [s])


@SERVERS
def test_one_address_cannot_take_every_slot(tmp_path, make):
    from inspeximus import _serve as serve_mod
    per = getattr(serve_mod, "MAX_PER_ADDRESS", 16)    # 16 where the constant does not exist yet
    httpd, path = make(tmp_path)
    port = _serve(httpd)
    base = threading.active_count()
    socks = []
    try:
        socks = [_stall(port, path) for _ in range(per + 14)]
        deadline = time.monotonic() + 5
        while threading.active_count() - base < per and time.monotonic() < deadline:
            time.sleep(0.05)
        time.sleep(0.5)
        held = threading.active_count() - base
        assert held <= per + 2, "one address holds %d server threads (per-address cap %d)" % (held, per)

        other = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        other.settimeout(10)
        other.bind(("127.0.0.2", 0))
        other.connect(("127.0.0.1", port))
        other.sendall(b"GET / HTTP/1.1\r\nHost: t\r\nConnection: close\r\n\r\n")
        first = other.recv(16)
        other.close()
        assert first.startswith(b"HTTP/1.") and b" 503" not in first, (
            "a client from another address got %r while one address held its slots" % first)

        for s in socks:                                  # the stalled clients leave: their slots come back
            s.close()
        socks = []
        deadline = time.monotonic() + 10
        while threading.active_count() - base > 2 and time.monotonic() < deadline:
            time.sleep(0.05)
        again = socket.create_connection(("127.0.0.1", port), timeout=10)
        again.sendall(b"GET / HTTP/1.1\r\nHost: t\r\nConnection: close\r\n\r\n")
        first = again.recv(16)
        again.close()
        assert first.startswith(b"HTTP/1.") and b" 503" not in first, (
            "the address got %r after its clients left: a per-address slot was not given back" % first)
    finally:
        _stop(httpd, socks)
