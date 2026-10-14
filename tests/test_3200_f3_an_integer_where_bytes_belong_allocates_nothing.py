"""3.20, F3 (Claude Security scan, MEDIUM): a CBOR integer where a byte string belongs is refused, and allocates nothing.

THE DEFECT. A COSE_Sign1 is CBOR its sender chooses, and the verifiers took each slot's type on trust. `bytes(x)` of an
integer allocates x zero bytes, so a 90-byte POST to SCRAPI's /entries with the integer 2^27 in the payload slot grew
the server by 399 MB before the signature check refused it (measured on v3.19.1, AUDIT-A's f3f5_repro.py), and 2^32
asks for 4 GiB. The same call sat on every slot a sender controls: the protected header, the payload and the signature
of a statement (`scitt.verify_signed_statement`), the payload, signature and audit-path elements of a receipt
(`cose.verify_receipt`), the receipts a transparent statement carries (`scitt.receipts_of`), and `cose.decode` itself,
which began with `bytes(b)`. SCRAPI also read whatever Content-Length a client declared.

WHAT IS PINNED HERE. Each slot, given 2^27, is refused with a problem naming it, and the call's peak allocation stays
under 8 MB (tracemalloc, which counts the bytes object `bytes(n)` makes). SCRAPI refuses a declared body over its cap
with 413 before it reads one byte of it, and the 90-byte POST gets its 400 without the allocation.
"""
import os
import socket
import sys
import threading
import tracemalloc
import urllib.error
import urllib.request

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from inspeximus import cose, scitt  # noqa: E402
from inspeximus.cose import CBORTag, CoseDecodeError  # noqa: E402

BIG = 2 ** 27                                   # 134 MB if anything turns it into bytes
PEAK_BOUND = 8 * 1024 * 1024
PROT = cose.encode({1: -8, 15: {1: "issuer:a", 2: "subject:s"}})


def _peak(fn):
    tracemalloc.start()
    try:
        out = fn()
    finally:
        _cur, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
    return out, peak


def _statement(protected=PROT, payload=b"x" * 32, sig=b"\x00" * 64):
    return cose.encode(CBORTag(18, [protected, {}, payload, sig]))


def test_decode_refuses_an_integer_without_allocating_it():
    def call():
        with pytest.raises(CoseDecodeError):
            cose.decode(BIG)
    _out, peak = _peak(call)
    assert peak < PEAK_BOUND, "decode(%d) allocated %d bytes" % (BIG, peak)


@pytest.mark.parametrize("slot", ["protected", "payload", "signature"])
def test_a_statement_with_an_integer_in_a_byte_slot_is_refused_without_the_allocation(slot):
    kw = {"protected": {"protected": BIG}, "payload": {"payload": BIG}, "signature": {"sig": BIG}}[slot]
    st = _statement(**kw)
    assert len(st) < 200, "CONTROL: the request is tiny"
    out, peak = _peak(lambda: scitt.verify_signed_statement(st, lambda m, s: True))
    assert out["ok"] is False and out["signature_ok"] is False
    assert any("byte string" in p for p in out["problems"]), out["problems"]
    assert peak < PEAK_BOUND, "%s slot: %d bytes allocated for a %d-byte statement" % (slot, peak, len(st))


def _receipt(payload=b"\x00" * 32, sig=b"\x00" * 64, path=(b"\x11" * 32,)):
    protected = cose.encode({1: -8, cose.HDR_VDS: 1})
    proof = cose.encode([2, 0, list(path)])
    return cose.encode(CBORTag(18, [protected, {cose.HDR_VDP: {cose.PROOF_INCLUSION: [proof]}}, payload, sig]))


@pytest.mark.parametrize("slot", ["payload", "signature", "path"])
def test_a_receipt_with_an_integer_in_a_byte_slot_is_refused_without_the_allocation(slot):
    kw = {"payload": {"payload": BIG}, "signature": {"sig": BIG}, "path": {"path": (BIG,)}}[slot]
    rc = _receipt(**kw)
    out, peak = _peak(lambda: cose.verify_receipt(rc, lambda m, s: True, leaf_data=b"leaf"))
    assert out["ok"] is False
    assert any("byte string" in p for p in out["problems"]), out["problems"]
    assert peak < PEAK_BOUND, "%s slot: %d bytes allocated" % (slot, peak)


def test_a_carried_receipt_that_is_an_integer_is_refused_without_the_allocation():
    st = cose.encode(CBORTag(18, [PROT, {scitt.HDR_RECEIPTS: [BIG]}, b"x" * 32, b"\x00" * 64]))

    def call():
        with pytest.raises(CoseDecodeError):
            scitt.receipts_of(st)
    _out, peak = _peak(call)
    assert peak < PEAK_BOUND, "receipts_of allocated %d bytes" % peak


def test_the_byte_slots_still_accept_byte_strings():
    """The control: a real statement and a real receipt still verify, so the refusals above are about the type."""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    k = Ed25519PrivateKey.generate()
    pub = k.public_key()

    def verify(msg, sig):
        try:
            pub.verify(sig, msg)
            return True
        except Exception:                                # noqa: BLE001
            return False
    st = scitt.signed_statement(b"x" * 32, "issuer:a", "subject:s", k.sign)
    assert scitt.verify_signed_statement(st, verify)["ok"] is True
    from inspeximus import merkle
    leaves = [b"a", b"b"]
    root = merkle._root_hashed([merkle.leaf_hash(x) for x in leaves])
    rc = cose.inclusion_receipt(2, 0, merkle.inclusion_proof(leaves, 0), root, k.sign)
    got = cose.verify_receipt(rc, verify, leaf_data=b"a")
    assert got["signature_ok"] and got["inclusion_ok"], got["problems"]


@pytest.fixture
def scrapi_server(tmp_path):
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from inspeximus import scrapi
    from inspeximus.transparency import RegistrationPolicy, TransparencyService
    svc_sk = Ed25519PrivateKey.generate()
    policy = RegistrationPolicy("scrapi-default", accepted_issuers=None, notes="")
    svc = TransparencyService(str(tmp_path / "reg.log"), policy, svc_sk.sign, lambda m, s: True,
                              service_pubkey=svc_sk.public_key().public_bytes_raw().hex())
    httpd = scrapi.make_server(svc, "127.0.0.1", 0)
    th = threading.Thread(target=httpd.serve_forever, daemon=True)
    th.start()
    yield httpd
    httpd.shutdown()
    th.join(10)


def test_scrapi_refuses_the_integer_payload_post_without_the_allocation(scrapi_server):
    body = _statement(payload=BIG)
    url = "http://127.0.0.1:%d/entries" % scrapi_server.server_address[1]
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/cose"})

    def call():
        try:
            urllib.request.urlopen(req, timeout=60)
            return 201
        except urllib.error.HTTPError as e:
            return e.code
    code, peak = _peak(call)
    assert code == 400, code
    assert peak < PEAK_BOUND, "SCRAPI allocated %d bytes for a %d-byte POST" % (peak, len(body))


def test_scrapi_refuses_a_declared_body_over_its_cap_before_reading_it(scrapi_server):
    from inspeximus import scrapi
    port = scrapi_server.server_address[1]
    declared = scrapi.MAX_BODY_BYTES + 1
    with socket.create_connection(("127.0.0.1", port), timeout=10) as s:
        s.sendall(("POST /entries HTTP/1.1\r\nHost: x\r\nContent-Type: application/cose\r\n"
                   "Content-Length: %d\r\n\r\n" % declared).encode() + b"\x00" * 16)
        s.settimeout(10)
        head = s.recv(256)                      # answered at once, though the declared body never comes
    assert head.startswith(b"HTTP/1.") and b" 413 " in head.split(b"\r\n", 1)[0], head[:80]

@pytest.mark.parametrize("arg", ["leaf_data", "expected_root"])
def test_verify_receipt_refuses_an_integer_leaf_or_root_without_the_allocation(arg):
    """The caller's leaf and root are often read from another decoded structure (a log entry, a bundle)."""
    rc = _receipt()
    kw = {"leaf_data": b"leaf", arg: BIG}
    out, peak = _peak(lambda: cose.verify_receipt(rc, lambda m, s: True, **kw))
    assert out["ok"] is False and any("byte string" in p for p in out["problems"]), out["problems"]
    assert peak < PEAK_BOUND, "%s: %d bytes allocated" % (arg, peak)


@pytest.mark.parametrize("where", ["leaf", "expected_root", "carried_receipt"])
def test_a_transparent_statement_refuses_an_integer_without_the_allocation(where):
    receipts = [BIG] if where == "carried_receipt" else [_receipt()]
    st = cose.encode(CBORTag(18, [PROT, {scitt.HDR_RECEIPTS: receipts}, b"x" * 32, b"\x00" * 64]))
    leaf = BIG if where == "leaf" else b"leaf"
    root = BIG if where == "expected_root" else b"\x00" * 32
    out, peak = _peak(lambda: scitt.verify_transparent_statement(st, lambda m, s: True, lambda m, s: True, leaf, root))
    assert out["ok"] is False
    assert any("byte string" in p for p in out["problems"]), out["problems"]
    assert peak < PEAK_BOUND, "%s: %d bytes allocated" % (where, peak)


def test_must_be_bytes_enforces_its_limit():
    assert cose.must_be_bytes(b"abc", "x", limit=3) == b"abc"
    with pytest.raises(CoseDecodeError, match="over the 3-byte bound"):
        cose.must_be_bytes(b"abcd", "x", limit=3)
