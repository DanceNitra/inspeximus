"""Round four, on round three's fixes -- and the first finding was one I had created an hour earlier.

Round three exposed `strict` and `require_authenticated_state` on the CLI and the witness server,
because both were reachable from nothing we ship. The `--strict` help text told operators to run
`inspeximus witness bootstrap`. **That command did not exist**, and nothing anywhere reached
`Witness.bootstrap` -- so the flag added to fix a class of "unreachable mechanism" defects was itself
an unreachable mechanism, and worse than the gap: a strict witness refuses every store it has no
memory of, so `--strict` as shipped would have bricked the witness.

Underneath it, a pre-existing one that made a command alone insufficient: `bootstrap()` wrote to an
in-memory set and returned. A CLI witness is a fresh process per call, so the declaration was gone
before the next invocation, and a server lost every bootstrap on restart. `strict=True` had never
been usable outside one long-lived Python process.

Everything here goes through a real subprocess or a real socket. A signature check passes on a flag
that is accepted and dropped, and that is worse than an absent flag because it reads as protection.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request

import pytest

from inspeximus import Inspeximus
from inspeximus.audit_bundle import _derived_store_id
from inspeximus.witness_pool import Witness

ENV = {**os.environ, "PYTHONUTF8": "1"}


def _cli(*argv):
    return subprocess.run([sys.executable, "-m", "inspeximus.cli", *argv],
                          capture_output=True, text=True, encoding="utf-8", errors="replace", env=ENV)


@pytest.fixture
def scene():
    """A keyed witness and a receipted store, on disk, addressable from another process."""
    d = tempfile.mkdtemp()
    key = os.path.join(d, "w.key")
    _cli("witness", "keygen", "--out", key)
    ix = Inspeximus(path=os.path.join(d, "s.json"), receipts=True)
    for i in range(3):
        ix.remember(f"r{i}", key=f"k{i}", object=str(i))
    ix.flush()
    ap = os.path.join(d, "anchor.json")
    json.dump(ix.anchor(), open(ap, "w", encoding="utf-8"))
    return d, key, key + ".state.json", ix, _derived_store_id(ix), ap


def _cosign(sid, key, state, ap, *extra):
    return _cli("witness", "cosign", ap, "--store-id", sid, "--key", key, "--state", state, *extra)


# ═══════════════════════════════════ H1: the remedy the help text names has to exist and work
def test_the_bootstrap_command_exists(scene):
    """`--strict`'s help named it before it existed. Naming a remedy you have not built is how a
    flag ships that cannot be used -- and this repo has a standing rule about testing the remedy an
    error message names, written after the same mistake."""
    assert "--store-id" in _cli("witness", "bootstrap", "--help").stdout


def test_strict_refuses_a_store_it_was_never_told_about(scene):
    _d, key, state, _ix, sid, ap = scene
    r = _cosign(sid, key, state, ap, "--strict")
    assert r.returncode != 0 and "no record" in (r.stdout + r.stderr), r.stdout + r.stderr


def test_the_declaration_survives_into_a_separate_process(scene):
    """THE POINT, and what a signature check could not have told us. `bootstrap()` added to an
    in-memory set, so each CLI call -- a fresh process -- started having forgotten."""
    _d, key, state, _ix, sid, ap = scene
    assert _cli("witness", "bootstrap", "--store-id", sid, "--key", key,
                "--state", state).returncode == 0
    r = _cosign(sid, key, state, ap, "--strict")
    assert r.returncode == 0, r.stdout + r.stderr


def test_deleting_the_memory_does_not_launder_a_first_contact(scene):
    """The attack `strict` exists for: amnesia. If this ever passes by co-signing, the flag is
    decoration."""
    _d, key, state, _ix, sid, ap = scene
    _cli("witness", "bootstrap", "--store-id", sid, "--key", key, "--state", state)
    assert _cosign(sid, key, state, ap, "--strict").returncode == 0
    os.unlink(state)
    assert _cosign(sid, key, state, ap, "--strict").returncode != 0


def test_control_the_default_is_unchanged(scene):
    """A witness that refuses every new store on upgrade is a worse outcome than the attack it
    prevents, so `strict` is opt-in and the default must stay usable."""
    _d, key, state, _ix, sid, ap = scene
    assert _cosign(sid, key, state, ap).returncode == 0


def test_control_a_rollback_is_still_refused(scene):
    """The guard the whole feature exists for, checked after all of the above."""
    d, key, state, _ix, sid, ap = scene
    _cosign(sid, key, state, ap)
    a = json.load(open(ap, encoding="utf-8"))
    a["n_writes"] = 1
    roll = os.path.join(d, "roll.json")
    json.dump(a, open(roll, "w", encoding="utf-8"))
    assert _cosign(sid, key, state, roll).returncode != 0


# ═══════════════════════════════════ H2: the same declaration, over the wire
def _serve(d, strict=True, token=None, port=0):
    """PORT 0, and no sleep. A fixed number collides the moment pytest-xdist runs two of these at
    once, and sleeping 1.2 seconds afterwards is a guess about a race rather than a fix for it.
    make_server() returns with the socket already bound, so the port is known and there is nothing
    to wait for."""
    sp = os.path.join(d, "w.json")
    w = Witness(state_path=sp)
    from inspeximus.witness_server import make_server
    srv, _w = make_server(port=port, state_path=sp, secret_hex=w._secret,
                          strict=strict, bootstrap_token=token)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return sp, w, srv.server_address[1]


def _post(port, path, body, token=None):
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}",
                                 data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json",
                                          **({"X-Bootstrap-Token": token} if token else {})})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.load(r)
    except urllib.error.HTTPError as e:
        return e.code, json.load(e)


def test_the_strict_server_can_be_bootstrapped_only_with_the_token():
    """An unauthenticated bootstrap would defeat `--strict` outright: anyone could declare any store
    id, and the witness would then co-sign the rollback it had been made to forget. It is the one
    write endpoint that is authenticated, because it is the only one that can WEAKEN the witness --
    /cosign can only record facts."""
    d = tempfile.mkdtemp()
    sp, w, port = _serve(d, token="s3cret")
    ix = Inspeximus(path=os.path.join(d, "s.json"), receipts=True)
    for i in range(3):
        ix.remember(f"r{i}", key=f"k{i}", object=str(i))
    ix.flush()
    sid = _derived_store_id(ix)

    assert _post(port, "/cosign", {"store_id": sid, "anchor": ix.anchor()})[0] == 409
    assert _post(port, "/bootstrap", {"store_id": sid})[0] == 403
    assert _post(port, "/bootstrap", {"store_id": sid}, token="wrong")[0] == 403
    assert _post(port, "/bootstrap", {"store_id": sid}, token="s3cret")[0] == 200
    assert _post(port, "/cosign", {"store_id": sid, "anchor": ix.anchor()})[0] == 200
    assert sid in Witness(w._secret, state_path=sp)._bootstrapped, "a restart forgot the bootstrap"


def test_an_early_refusal_reaches_the_client_instead_of_resetting_the_connection():
    """BaseHTTPRequestHandler does not consume the request body, and a response sent with unread
    bytes in flight makes the client see a CONNECTION RESET rather than the response. Measured on
    this route: `ConnectionResetError(10054)`, so the 403 explaining how to enable /bootstrap -- the
    entire value of that branch -- was never delivered. A helpful error nobody receives is a hang
    with extra steps."""
    d = tempfile.mkdtemp()
    _sp, _w, port = _serve(d, token=None)
    code, body = _post(port, "/bootstrap", {"store_id": "insp1:whatever"})
    assert code == 403 and "bootstrap-token" in json.dumps(body), body


def test_a_malformed_body_is_a_400_and_not_a_500():
    """FOUND IN PASS FIVE, on the route pass four added -- kept here because it is about this route.

    Both handlers parsed inside a broad `except Exception -> 500 {type}: {message}`, so `not json`
    from an unauthenticated caller produced a server error carrying an exception class and its text.
    On the one endpoint a stranger can reach, that is a free error-shaped oracle and an ops alarm
    they can pull at will -- and it is simply the wrong code, since the fault is the caller's."""
    d = tempfile.mkdtemp()
    _sp, _w, port = _serve(d, token="tok")

    def raw(path, body, token=None):
        req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=body,
                                     headers={**({"X-Bootstrap-Token": token} if token else {})})
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status
        except urllib.error.HTTPError as e:
            return e.code

    assert raw("/bootstrap", b"not json", token="tok") == 400
    assert raw("/cosign", b"{") == 400
    assert raw("/cosign", b'"a string, not an object"') == 400
    # the must-not-brick control: the server still answers a well-formed call afterwards
    assert _post(port, "/bootstrap", {"store_id": "insp1:z"}, token="tok")[0] == 200


def test_bootstrap_on_a_non_strict_witness_says_it_is_a_no_op():
    """Accepting a call that changes nothing is how an operator concludes they are protected."""
    d = tempfile.mkdtemp()
    _sp, _w, port = _serve(d, strict=False, token="s3cret")
    code, body = _post(port, "/bootstrap", {"store_id": "insp1:x"}, token="s3cret")
    assert code == 400 and "no-op" in json.dumps(body), body
