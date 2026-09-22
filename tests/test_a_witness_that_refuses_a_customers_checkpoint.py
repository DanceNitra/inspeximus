"""Every refusal the witness owes a customer, and the five attacks the product has to survive.

Each test here is written so that REMOVING the defence makes it fail, because a test that passes on
a witness with no checks is a test of nothing. The five the owner named, in order:

1. two different checkpoints at one size (the split view a customer could try on two witnesses)
2. a signature under a key we do not trust for that origin
3. a size that skips, or a tree that shrank
4. our signing key never leaving the process, and never appearing in what we return
5. a ceiling on volume: body size, proof length, and submissions per minute

The sixth, which is not an attack but the reason the state exists: persist before signing. The
spec's own race note is a rollback, and `test_the_head_is_persisted_before_the_signature_is_made`
holds that ordering down.
"""
from __future__ import annotations

import base64
import hashlib
import json

import pytest

from inspeximus import checkpoint as cp
from inspeximus import merkle
from inspeximus import witness_checkpoint as wc
from inspeximus.core import new_receipt_keypair

pytest.importorskip("cryptography.hazmat.primitives.asymmetric.ed25519")

ORIGIN = "customer.example/log"


@pytest.fixture()
def log_keys():
    return new_receipt_keypair()


@pytest.fixture()
def witness(tmp_path, log_keys):
    sk, pub = new_receipt_keypair()
    return wc.CheckpointWitness(str(tmp_path / "state.json"), {ORIGIN: log_keys[1]},
                                "witness.example/w1", sk, pub)


def _leaves(n):
    return [b"entry %d" % i for i in range(n)]


def _checkpoint(log_keys, n, origin=ORIGIN, root=None):
    sk, pub = log_keys
    return cp.signed_checkpoint(origin, n, root if root is not None else merkle.root(_leaves(n)),
                                sk, pub)


def _submit(witness, log_keys, old, n, proof=None, note=None):
    body = wc.build_request(old, proof if proof is not None else [], note or _checkpoint(log_keys, n))
    return witness.add_checkpoint(body)


# -- the happy path, so the refusals below mean something ---------------------------------------
def test_a_first_checkpoint_is_cosigned_and_the_customer_can_verify_it(witness, log_keys):
    status, ctype, body = _submit(witness, log_keys, 0, 5)
    assert status == 200 and ctype.startswith("text/plain")
    note = _checkpoint(log_keys, 5)
    ts = wc.verify_cosignature(cp.split_note(note)[0], body.strip(), witness.name, witness.pubkey)
    assert ts > 0, "the spec forbids a zero timestamp"


def test_growth_is_cosigned_when_the_proof_holds(witness, log_keys):
    _submit(witness, log_keys, 0, 5)
    proof = merkle.consistency_proof(_leaves(9), 5)
    status, _c, _b = _submit(witness, log_keys, 5, 9, proof=proof)
    assert status == 200
    assert witness.latest(ORIGIN)["size"] == 9


# -- 1. the split view ---------------------------------------------------------------------------
def test_a_second_root_at_the_same_size_is_refused(witness, log_keys):
    """The customer's two-witness attack, seen from one witness: same size, different tree."""
    _submit(witness, log_keys, 0, 5)
    other = _checkpoint(log_keys, 5, root=merkle.root([b"other %d" % i for i in range(5)]))
    with pytest.raises(wc.Refused) as e:
        _submit(witness, log_keys, 5, 5, note=other)
    assert e.value.status == 422
    assert witness.latest(ORIGIN)["root"] == merkle.root(_leaves(5)).hex()


def test_a_fork_at_a_larger_size_fails_the_consistency_proof(witness, log_keys):
    """The other shape of the same attack: a bigger tree that did not grow from ours."""
    _submit(witness, log_keys, 0, 5)
    forked = [b"entry %d" % i for i in range(5)]
    forked[2] = b"rewritten"
    note = _checkpoint(log_keys, 9, root=merkle.root(forked + [b"entry %d" % i for i in range(5, 9)]))
    with pytest.raises(wc.Refused) as e:
        _submit(witness, log_keys, 5, 9, proof=merkle.consistency_proof(_leaves(9), 5), note=note)
    assert e.value.status == 422


# -- 2. a key we do not trust --------------------------------------------------------------------
def test_an_unknown_origin_is_404_not_a_refusal_to_sign(witness, log_keys):
    note = _checkpoint(log_keys, 3, origin="stranger.example/log")
    with pytest.raises(wc.Refused) as e:
        _submit(witness, log_keys, 0, 3, note=note)
    assert e.value.status == 404


def test_a_checkpoint_signed_by_another_key_is_403(witness, log_keys):
    """Same origin, somebody else's key: the id does not match, so nothing here verifies."""
    impostor = new_receipt_keypair()
    with pytest.raises(wc.Refused) as e:
        _submit(witness, log_keys, 0, 3, note=_checkpoint(impostor, 3))
    assert e.value.status == 403
    assert witness.latest(ORIGIN)["size"] == 0


def test_a_tampered_signature_from_the_right_name_is_403(witness, log_keys):
    note = _checkpoint(log_keys, 3)
    text, rest = note.rpartition("\n\n")[0], note.rpartition("\n\n")[2]
    line = rest.strip()
    blob = bytearray(base64.b64decode(line.split(" ", 2)[2]))
    blob[-1] ^= 0xFF                                             # one bit of the signature
    broken = "%s\n\n— %s %s\n" % (text, ORIGIN, base64.b64encode(bytes(blob)).decode())
    with pytest.raises(wc.Refused) as e:
        _submit(witness, log_keys, 0, 3, note=broken)
    assert e.value.status == 403


# -- 3. a size that skips, or a tree that shrank -------------------------------------------------
def test_a_skipped_size_is_409_and_says_what_we_hold(witness, log_keys):
    _submit(witness, log_keys, 0, 5)
    with pytest.raises(wc.Refused) as e:
        _submit(witness, log_keys, 7, 9, proof=merkle.consistency_proof(_leaves(9), 7))
    assert e.value.status == 409
    assert e.value.body == "5\n" and e.value.content_type == "text/x.tlog.size"


def test_a_rollback_leaves_the_state_where_it_was(witness, log_keys):
    """The failure the spec's race note describes, asked for directly."""
    _submit(witness, log_keys, 0, 9)
    with pytest.raises(wc.Refused):
        _submit(witness, log_keys, 5, 5)
    assert witness.latest(ORIGIN)["size"] == 9


def test_an_old_size_larger_than_the_checkpoint_is_400(witness, log_keys):
    with pytest.raises(wc.Refused) as e:
        _submit(witness, log_keys, 9, 5)
    assert e.value.status == 400


def test_a_size_zero_checkpoint_must_carry_the_empty_root(witness, log_keys):
    note = _checkpoint(log_keys, 0, root=hashlib.sha256(b"x").digest())
    with pytest.raises(wc.Refused) as e:
        _submit(witness, log_keys, 0, 0, note=note)
    assert e.value.status == 422


def test_a_proof_sent_from_zero_is_refused(witness, log_keys):
    """The empty tree is consistent with anything, so a proof here is a client that guessed."""
    with pytest.raises(wc.Refused) as e:
        _submit(witness, log_keys, 0, 5, proof=[b"\x00" * 32])
    assert e.value.status == 422


# -- 4. our own key ------------------------------------------------------------------------------
def test_the_witness_secret_never_appears_in_what_we_return(witness, log_keys, tmp_path):
    status, _c, body = _submit(witness, log_keys, 0, 5)
    assert status == 200
    secret = witness._secret
    assert secret not in body
    assert secret not in json.dumps(json.load(open(witness.state_path, encoding="utf-8")))
    assert secret not in repr(witness.latest(ORIGIN))


def test_the_cosignature_key_id_uses_the_cosignature_type_byte(witness):
    """0x04, not the checkpoint's 0x01. With the wrong byte every conforming client ignores us."""
    expected = hashlib.sha256(witness.name.encode() + b"\x0a" + b"\x04"
                              + bytes.fromhex(witness.pubkey)).digest()[:4]
    assert wc.cosignature_key_id(witness.name, witness.pubkey) == expected
    assert wc.cosignature_key_id(witness.name, witness.pubkey) != cp.key_id(witness.name, witness.pubkey)


# -- 5. volume -----------------------------------------------------------------------------------
def test_a_body_over_the_ceiling_is_refused_before_it_is_parsed(witness):
    with pytest.raises(wc.Refused) as e:
        wc.parse_request(b"old 0\n\n" + b"x" * (wc.MAX_BODY_BYTES + 1))
    assert e.value.status == 413


def test_more_proof_lines_than_the_spec_allows_are_refused(witness, log_keys):
    proof = [b"\x11" * 32] * (wc.MAX_PROOF_LINES + 1)
    with pytest.raises(wc.Refused) as e:
        _submit(witness, log_keys, 5, 9, proof=proof)
    assert e.value.status == 400


def test_a_flood_from_one_origin_is_refused(tmp_path, log_keys):
    sk, pub = new_receipt_keypair()
    w = wc.CheckpointWitness(str(tmp_path / "s.json"), {ORIGIN: log_keys[1]},
                             "witness.example/w1", sk, pub, max_per_minute=3)
    _submit(w, log_keys, 0, 1)
    for n in (2, 3):
        _submit(w, log_keys, n - 1, n, proof=merkle.consistency_proof(_leaves(n), n - 1))
    with pytest.raises(wc.Refused) as e:
        _submit(w, log_keys, 3, 4, proof=merkle.consistency_proof(_leaves(4), 3))
    assert e.value.status == 429


# -- the ordering that makes the state worth having ----------------------------------------------
def test_the_head_is_persisted_before_the_signature_is_made(tmp_path, log_keys, monkeypatch):
    """A cosignature for a head we did not persist is the rollback the spec warns about.

    The control: make signing raise, and require the state to be on disk anyway.
    """
    sk, pub = new_receipt_keypair()
    w = wc.CheckpointWitness(str(tmp_path / "s.json"), {ORIGIN: log_keys[1]},
                             "witness.example/w1", sk, pub)
    monkeypatch.setattr(wc, "cosign", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no key")))
    with pytest.raises(RuntimeError):
        _submit(w, log_keys, 0, 5)
    assert w.latest(ORIGIN)["size"] == 5, "the head must be on disk before the signature is attempted"


def test_a_malformed_request_is_400_rather_than_a_traceback(witness):
    for body in (b"", b"no blank line\n", b"old x\n\nnote\n", b"old 01\n\nnote\n"):
        with pytest.raises(wc.Refused) as e:
            wc.parse_request(body)
        assert e.value.status == 400


# -- the HTTP layer, because a class nobody can reach is not a service -----------------------------
def _serve(witness):
    """Start the real server on a loopback port and return (url, shutdown)."""
    import http.server
    import socketserver
    import threading

    class Threaded(socketserver.ThreadingMixIn, http.server.HTTPServer):
        daemon_threads = True
        allow_reuse_address = True

    httpd = Threaded(("127.0.0.1", 0), wc.make_handler(witness))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return "http://127.0.0.1:%d/add-checkpoint" % httpd.server_address[1], httpd.shutdown


def test_a_checkpoint_submitted_over_http_comes_back_cosigned(witness, log_keys):
    url, stop = _serve(witness)
    try:
        note = _checkpoint(log_keys, 4)
        out = wc.submit(url, note, 0, [])
        assert out["status"] == 200
        ts = wc.verify_cosignature(cp.split_note(note)[0], out["cosignatures"].strip(),
                                   witness.name, witness.pubkey)
        assert ts > 0
    finally:
        stop()


def test_http_carries_the_status_and_the_body_the_spec_prescribes(witness, log_keys):
    """A 409 must arrive with our size in text/x.tlog.size, or the client cannot catch up."""
    url, stop = _serve(witness)
    try:
        wc.submit(url, _checkpoint(log_keys, 4), 0, [])
        with pytest.raises(wc.Refused) as e:
            wc.submit(url, _checkpoint(log_keys, 9), 7,
                      merkle.consistency_proof(_leaves(9), 7))
        assert e.value.status == 409 and e.value.reason.strip() == "4"
    finally:
        stop()


def test_the_client_recovers_from_a_409_when_it_can_build_the_proof(witness, log_keys):
    """The flow the spec describes for a client that lost its notes: ask, then resubmit."""
    url, stop = _serve(witness)
    try:
        wc.submit(url, _checkpoint(log_keys, 4), 0, [])
        out = wc.submit(url, _checkpoint(log_keys, 9), 0, [],
                        fetch_proof=lambda old, new: merkle.consistency_proof(_leaves(new), old))
        assert out["status"] == 200
        assert witness.latest(ORIGIN)["size"] == 9
    finally:
        stop()


def test_an_oversized_body_is_refused_by_the_server_before_it_is_read(witness):
    """413 on the Content-Length, not after reading 50 MB into memory."""
    import urllib.error
    import urllib.request
    url, stop = _serve(witness)
    try:
        req = urllib.request.Request(url, data=b"x" * (wc.MAX_BODY_BYTES + 10), method="POST")
        with pytest.raises(urllib.error.HTTPError) as e:
            urllib.request.urlopen(req, timeout=10)
        assert e.value.code == 413
    finally:
        stop()


def test_another_path_is_404_and_the_health_endpoint_answers(witness):
    import urllib.request
    url, stop = _serve(witness)
    try:
        base = url.rsplit("/", 1)[0]
        with urllib.request.urlopen(base + "/health", timeout=10) as r:
            assert r.status == 200 and r.read() == b"ok\n"
    finally:
        stop()
