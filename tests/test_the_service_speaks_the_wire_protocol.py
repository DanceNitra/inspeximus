"""SCRAPI is the difference between a library somebody must learn and a service they can point at.

WHY IT MATTERS. `transparency.py` is conformant to RFC 9943 and reachable only by writing Python
against it. draft-ietf-scitt-scrapi-11 defines the HTTP surface a SCITT client already speaks, so
this is what makes the service usable by a tool nobody here wrote.

WHAT THESE TESTS PIN, and each is a place where "close enough" would make a conformance claim false
while every hand-written client kept working:

  * The media types are the draft's, not the ones an HTTP developer expects. Statements and Receipts
    are `application/cose`; the key set is `application/cbor`; errors are
    `application/concise-problem-details+cbor` and NOT `application/problem+json`.
  * 201 with a Location on registration, 404 for an entry that never existed, and 400 for a refusal.
    A refusal is not a server error and must not invite a retry.
  * A Receipt fetched over HTTP verifies against the log, which is the only thing that makes the
    endpoint worth calling.
  * The server does not start without an issuer key unless the operator says out loud that it vets
    nobody.
"""
from __future__ import annotations

import hashlib
import os
import sys
import tempfile
import threading
import urllib.error
import urllib.request

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from inspeximus import cose, new_receipt_keypair, signed_statement
from inspeximus.scrapi import CBOR, COSE, PROBLEM, main, make_server
from inspeximus.transparency import RegistrationPolicy, TransparencyService

ISSUER = "did:web:agora.example"


def _keypair():
    from cryptography.hazmat.primitives.asymmetric.ed25519 import (Ed25519PrivateKey as SK,
                                                                   Ed25519PublicKey as PK)
    sk_hex, pk_hex = new_receipt_keypair()
    sk, pk = SK.from_private_bytes(bytes.fromhex(sk_hex)), PK.from_public_bytes(bytes.fromhex(pk_hex))

    def verify(msg, sig):
        try:
            pk.verify(sig, msg)
            return True
        except Exception:
            return False
    return sk.sign, verify, pk_hex


@pytest.fixture()
def wired():
    isign, iverify, _ip = _keypair()
    ssign, sverify, spub = _keypair()
    ts = TransparencyService(os.path.join(tempfile.mkdtemp(), "log.jsonl"),
                             RegistrationPolicy("scrapi-test", accepted_issuers=[ISSUER]),
                             ssign, iverify, service_pubkey=spub)
    # PORT 0 lets the OS pick a free one. A counter cannot: pytest-xdist runs several workers at
    # once, each with its own copy of this module and its own counter, so they hand out the same
    # numbers and the second binder gets EADDRINUSE. CI caught it on every runner; locally with -n 0
    # it passed, which is the shape of a defect that only appears where you are not looking.
    srv = make_server(ts, port=0)
    port = srv.server_address[1]
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        yield ts, isign, sverify, "http://127.0.0.1:%d" % port
    finally:
        srv.shutdown()
        srv.server_close()


def _statement(isign, text=b"a fact", subject="memory:abc", issuer=ISSUER):
    return signed_statement(hashlib.sha256(text).digest(), issuer, subject, isign)


def _post(base, body, ctype=COSE):
    req = urllib.request.Request(base + "/entries", data=body,
                                 headers={"Content-Type": ctype}, method="POST")
    return urllib.request.urlopen(req, timeout=10)


def test_registering_over_http_returns_201_a_location_and_a_usable_receipt(wired):
    ts, isign, sverify, base = wired
    with _post(base, _statement(isign)) as r:
        assert r.status == 201
        assert r.headers["Content-Type"] == COSE
        assert r.headers["Location"] == "/entries/1"
        receipt = r.read()
    out = cose.verify_receipt(receipt, sverify, leaf_data=ts.entry_leaf(1), expected_root=ts.root())
    assert out["ok"], out["problems"]


def test_an_entry_can_be_fetched_and_still_verifies(wired):
    ts, isign, sverify, base = wired
    _post(base, _statement(isign)).read()
    with urllib.request.urlopen(base + "/entries/1", timeout=10) as r:
        assert r.status == 200 and r.headers["Content-Type"] == COSE
        receipt = r.read()
    out = cose.verify_receipt(receipt, sverify, leaf_data=ts.entry_leaf(1), expected_root=ts.root())
    assert out["ok"], out["problems"]


def test_the_key_set_is_cbor_and_carries_the_policy_and_the_scope(wired):
    _ts, _isign, _sv, base = wired
    with urllib.request.urlopen(base + "/.well-known/scitt-keys", timeout=10) as r:
        assert r.status == 200 and r.headers["Content-Type"] == CBOR
        ks = cose.decode(r.read())
    assert ks["keys"][0]["alg"] == "EdDSA" and ks["keys"][0]["kid"]
    assert ks["policy"]["name"] == "scrapi-test"
    assert "NON-EQUIVOCATION is not" in ks["scope"], (
        "a reader must be told that a root from the audited party proves self-consistency only")


def test_an_unknown_entry_is_404_and_an_unknown_kid_is_404(wired):
    _ts, _isign, _sv, base = wired
    for path in ("/entries/99", "/entries/not-a-number", "/.well-known/scitt-keys/deadbeef"):
        with pytest.raises(urllib.error.HTTPError) as e:
            urllib.request.urlopen(base + path, timeout=10)
        assert e.value.code == 404, path
        assert e.value.headers["Content-Type"] == PROBLEM


def test_a_refusal_is_400_with_the_policy_reason_and_not_a_server_error(wired):
    """A 5xx invites a retry against a decision that will never change."""
    _ts, isign, _sv, base = wired
    with pytest.raises(urllib.error.HTTPError) as e:
        _post(base, _statement(isign, issuer="did:web:stranger"))
    assert e.value.code == 400
    assert e.value.headers["Content-Type"] == PROBLEM
    body = cose.decode(e.value.read())
    assert body[-1] == "registration refused"
    assert "trust anchor" in body[-2]


def test_the_wrong_content_type_is_refused(wired):
    _ts, isign, _sv, base = wired
    with pytest.raises(urllib.error.HTTPError) as e:
        _post(base, _statement(isign), ctype="application/json")
    assert e.value.code == 400
    assert cose.decode(e.value.read())[-1] == "wrong content type"


def test_an_empty_body_is_refused(wired):
    _ts, _isign, _sv, base = wired
    with pytest.raises(urllib.error.HTTPError) as e:
        _post(base, b"")
    assert e.value.code == 400


def test_errors_are_cbor_problem_details_and_never_problem_json(wired):
    """The draft says concise-problem-details+cbor. Serving problem+json is the kind of "close
    enough" that leaves a conformance claim false while every hand-written client keeps working."""
    _ts, _isign, _sv, base = wired
    with pytest.raises(urllib.error.HTTPError) as e:
        urllib.request.urlopen(base + "/nope", timeout=10)
    assert e.value.headers["Content-Type"] == PROBLEM
    assert "json" not in e.value.headers["Content-Type"]
    decoded = cose.decode(e.value.read())
    assert isinstance(decoded, dict) and -1 in decoded


def test_the_server_refuses_to_start_without_an_issuer_key(capsys):
    """A service that cannot authenticate an issuer is recording bytes, not statements. Starting
    anyway would fill a log with entries whose signatures were never checked."""
    log = os.path.join(tempfile.mkdtemp(), "l.jsonl")
    assert main(["--log", log, "--port", "0"]) == 2
    assert "refusing to start" in capsys.readouterr().err
    assert not os.path.exists(log), "a refused start must not create a log"
