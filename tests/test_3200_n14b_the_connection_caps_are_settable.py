"""3.20, AUDIT-A section 7 (N-14 behind a proxy): both connection caps are settable, and the per-address one can be off.

THE DEFECT. N-14 capped one client address at 16 requests at once. Behind a reverse proxy every request has the
proxy's address, so the cap applied to all users together, and one client behind the proxy could hold all 16 while
everyone else got 503. 3.19.1 had no cap at all. Neither cap could be changed.

WHAT IS PINNED HERE, on each of the three servers through its own make_server:
- with `max_per_address=0`, 20 stalled connections from one address do not refuse a 21st from that address;
- with the default, the 17th from that address is refused with 503;
- `max_connections` sets the total cap;
- SCRAPI and both witness command lines take --max-connections and --max-per-address.
The address is the socket's peer; X-Forwarded-For is not read (a client can write it), which the last case pins.
"""
import os
import socket
import subprocess
import sys
import threading
import time

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
pytest.importorskip("cryptography")

from test_3200_n11_stalled_connections_cannot_hold_every_server_thread import _stall  # noqa: E402


def _scrapi(tmp_path, **caps):
    from inspeximus import scrapi
    from test_a_log_a_stranger_can_check_without_us import _service
    return scrapi.make_server(_service(str(tmp_path / "log"), 0), port=0, **caps), "/entries"


def _witness(tmp_path, **caps):
    from inspeximus import witness_server
    httpd, _ = witness_server.make_server(port=0, state_path=str(tmp_path / "w.json"), **caps)
    return httpd, "/cosign"


def _checkpoint(tmp_path, **caps):
    from inspeximus import witness_checkpoint as wc
    from test_3200_f4_a_checkpoint_witness_cosigns_one_root_per_size import LPK, ORIGIN, WPK, WSK
    w = wc.CheckpointWitness(str(tmp_path / "cw.json"), {ORIGIN: LPK}, "example.test/w", WSK, WPK)
    return wc.make_server(w, port=0, **caps), "/add-checkpoint"


SERVERS = pytest.mark.parametrize("make", [_scrapi, _witness, _checkpoint], ids=["scrapi", "witness", "checkpoint"])


def _get(port, headers=b""):
    """The first bytes of the answer to a GET, or b"" when the server reset the connection before answering."""
    s = socket.create_connection(("127.0.0.1", port), timeout=10)
    try:
        s.sendall(b"GET / HTTP/1.1\r\nHost: t\r\n" + headers + b"Connection: close\r\n\r\n")
        return s.recv(16)
    except ConnectionError:
        return b""
    finally:
        s.close()


def _refused(port):
    """True when a new connection is answered 503. It sends nothing: a refused connection is answered and closed at
    accept, and bytes the server never read would make Windows reset the connection and drop the 503 with it."""
    s = socket.create_connection(("127.0.0.1", port), timeout=10)
    try:
        return s.recv(16).startswith(b"HTTP/1.1 503")
    finally:
        s.close()


def _with_stalled(httpd, path, n, check):
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
    base = threading.active_count()
    socks = []
    try:
        socks = [_stall(port, path) for _ in range(n)]
        # Wait until every stalled connection holds a handler thread: a fixed sleep raced the accept loop.
        deadline = time.monotonic() + 10
        while threading.active_count() - base < n and time.monotonic() < deadline:
            time.sleep(0.05)
        assert threading.active_count() - base >= n, "CONTROL: %d stalled connections are held" % n
        return check(port)
    finally:
        for s in socks:
            s.close()
        httpd.shutdown()
        httpd.server_close()


@SERVERS
def test_with_the_per_address_cap_off_one_address_is_not_refused(tmp_path, make):
    httpd, path = make(tmp_path, max_per_address=0)
    first = _with_stalled(httpd, path, 20, _get)
    assert first.startswith(b"HTTP/1.") and b" 503" not in first, (
        "20 stalled connections from one address refused a 21st with max_per_address=0: %r" % first)


@SERVERS
def test_with_the_default_the_17th_from_one_address_is_refused(tmp_path, make):
    httpd, path = make(tmp_path)
    assert _with_stalled(httpd, path, 16, _refused), "CONTROL: the default per-address cap of 16 refused nothing"


@SERVERS
def test_the_total_cap_is_settable(tmp_path, make):
    httpd, path = make(tmp_path, max_connections=4, max_per_address=0)
    assert _with_stalled(httpd, path, 4, _refused), "max_connections=4 let a 5th request in"


def test_a_forwarded_for_header_does_not_move_a_client_to_another_address(tmp_path):
    httpd, path = _scrapi(tmp_path)
    first = _with_stalled(httpd, path, 16, lambda port: _get(port, b"X-Forwarded-For: 203.0.113.9\r\n"))
    # Refused is a 503, or a reset that dropped it (the header bytes were never read); served is any other answer.
    assert first == b"" or first.startswith(b"HTTP/1.1 503"), "X-Forwarded-For bought a 17th slot: %r" % first


@pytest.mark.parametrize("cmd", [["-m", "inspeximus.scrapi"], ["-m", "inspeximus.witness_server"],
                                 ["-m", "inspeximus.cli", "witness", "serve"]],
                         ids=["scrapi", "witness_server", "inspeximus witness serve"])
def test_the_command_lines_take_both_caps(cmd):
    r = subprocess.run([sys.executable] + cmd + ["--help"], capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=60, cwd=ROOT)
    assert "--max-connections" in r.stdout and "--max-per-address" in r.stdout, r.stdout[-600:]
