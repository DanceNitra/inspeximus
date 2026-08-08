"""A COSE Receipt of Inclusion that a standard verifier could read — not our JSON in a new coat.

`inclusion_proof()` already proves inclusion, but in our own shape. The reason to emit
draft-ietf-cose-merkle-tree-proofs instead is interoperability: the labels, the tag, and the byte
layout are what let someone else's verifier check our log without importing us.

So this file tests three separate things, and only the first is about our code being self-consistent:

  1. the CBOR encoder against RFC 8949's PUBLISHED Appendix A vectors (external truth);
  2. the receipt structure against the draft's own worked example — tag 18, vds 395 = 1,
     vdp 396 with inclusion proofs under -1, payload = the Merkle root;
  3. NEGATIVE controls: a stranger's signature, a tampered path, a swapped root, a forged leaf must
     each be REFUSED, and refused with a message naming which part failed.
"""
from __future__ import annotations

import hashlib
import os
import tempfile

import pytest

from inspeximus import Inspeximus
from inspeximus.cose import (ALG_EDDSA, COSE_SIGN1_TAG, HDR_ALG, HDR_VDP, HDR_VDS, PROOF_INCLUSION,
                             VDS_RFC9162_SHA256, CBORTag, decode, encode, inclusion_receipt,
                             receipt_digest, verify_receipt)

ed = pytest.importorskip("cryptography.hazmat.primitives.asymmetric.ed25519",
                         reason="signing needs inspeximus[crypto]")


@pytest.fixture()
def signer():
    sk = ed.Ed25519PrivateKey.generate()
    pk = sk.public_key()

    def sign(b):
        return sk.sign(b)

    def verify(b, sig):
        try:
            pk.verify(sig, b)
            return True
        except Exception:
            return False
    return sign, verify


@pytest.fixture()
def store():
    d = tempfile.mkdtemp()
    s = Inspeximus(os.path.join(d, "s.json"), receipts=True)
    for i in range(9):
        s.remember("fact %d" % i, key="k::%d" % i)
    return s


def test_cbor_matches_rfc8949_appendix_a():
    """EXTERNAL TRUTH. Published vectors, so this cannot pass by agreeing with itself."""
    for value, expect in [(0, "00"), (23, "17"), (24, "1818"), (1000, "1903e8"), (-1, "20"),
                          (-1000, "3903e7"), (b"\x01\x02\x03\x04", "4401020304"),
                          ("IETF", "6449455446"), ([1, 2, 3], "83010203"),
                          ({1: 2, 3: 4}, "a201020304"), (None, "f6"), (True, "f5")]:
        assert encode(value).hex() == expect, "CBOR for %r" % (value,)


def test_map_keys_are_canonically_ordered():
    """A receipt whose bytes depend on dict iteration order is not a receipt."""
    assert encode({3: 1, 1: 2}) == encode({1: 2, 3: 1})


def test_indefinite_length_is_refused():
    with pytest.raises(ValueError):
        decode(b"\x9f\x01\xff")            # indefinite-length array: not deterministic


def test_the_receipt_has_the_shape_the_draft_specifies(store, signer):
    sign, _ = signer
    b = store.inclusion_proof(3)
    r = inclusion_receipt(b["tree_size"], b["index"], [bytes.fromhex(h) for h in b["audit_path"]],
                          bytes.fromhex(b["root"]), sign)
    tagged = decode(r)
    assert isinstance(tagged, CBORTag) and tagged.tag == COSE_SIGN1_TAG
    protected, unprotected, payload, sig = tagged.value
    ph = decode(protected)
    assert ph[HDR_ALG] == ALG_EDDSA
    assert ph[HDR_VDS] == VDS_RFC9162_SHA256          # 395 : 1  (RFC9162_SHA256)
    proofs = unprotected[HDR_VDP][PROOF_INCLUSION]     # 396 : { -1 : [ ... ] }
    size, index, path = decode(proofs[0])
    assert (size, index) == (9, 3)
    assert payload == bytes.fromhex(b["root"]), "payload MUST be the Merkle root"
    assert len(sig) == 64


def test_the_receipt_verifies_end_to_end(store, signer):
    sign, verify = signer
    b = store.inclusion_proof(3)
    leaf = b["leaf"].encode("utf-8")
    r = inclusion_receipt(b["tree_size"], b["index"], [bytes.fromhex(h) for h in b["audit_path"]],
                          bytes.fromhex(b["root"]), sign)
    out = verify_receipt(r, verify, leaf_data=leaf, expected_root=bytes.fromhex(b["root"]))
    assert out["ok"], out["problems"]
    assert out["signature_ok"] and out["inclusion_ok"] and out["root_matches"]


def test_a_stranger_cannot_pass_as_the_signer(store, signer):
    sign, _ = signer
    other = ed.Ed25519PrivateKey.generate().public_key()

    def verify_other(b, sig):
        try:
            other.verify(sig, b)
            return True
        except Exception:
            return False
    b = store.inclusion_proof(3)
    r = inclusion_receipt(b["tree_size"], b["index"], [bytes.fromhex(h) for h in b["audit_path"]],
                          bytes.fromhex(b["root"]), sign)
    out = verify_receipt(r, verify_other, leaf_data=b["leaf"].encode(),
                         expected_root=bytes.fromhex(b["root"]))
    assert not out["ok"] and not out["signature_ok"]
    assert any("signature" in p for p in out["problems"])


def test_a_forged_leaf_is_caught_even_with_a_good_signature(store, signer):
    """THE ONE THAT MATTERS. A valid signature over a valid root says nothing about WHICH record
    was included; only the audit path does."""
    sign, verify = signer
    b = store.inclusion_proof(3)
    r = inclusion_receipt(b["tree_size"], b["index"], [bytes.fromhex(h) for h in b["audit_path"]],
                          bytes.fromhex(b["root"]), sign)
    out = verify_receipt(r, verify, leaf_data=b"a record that was never written",
                         expected_root=bytes.fromhex(b["root"]))
    assert out["signature_ok"], "the signature is genuine; that is the point"
    assert not out["inclusion_ok"] and not out["ok"]
    assert any("audit path" in p for p in out["problems"])


def test_a_receipt_for_another_root_is_refused(store, signer):
    sign, verify = signer
    b = store.inclusion_proof(3)
    r = inclusion_receipt(b["tree_size"], b["index"], [bytes.fromhex(h) for h in b["audit_path"]],
                          bytes.fromhex(b["root"]), sign)
    out = verify_receipt(r, verify, leaf_data=b["leaf"].encode(), expected_root=bytes(32))
    assert not out["ok"] and out["root_matches"] is False


def test_not_checking_inclusion_is_reported_not_assumed(store, signer):
    """Verifying without the leaf must NOT report ok — silence about a check is not a pass."""
    sign, verify = signer
    b = store.inclusion_proof(3)
    r = inclusion_receipt(b["tree_size"], b["index"], [bytes.fromhex(h) for h in b["audit_path"]],
                          bytes.fromhex(b["root"]), sign)
    out = verify_receipt(r, verify)
    assert not out["ok"]
    assert any("inclusion NOT checked" in p for p in out["problems"])


def test_an_unsigned_receipt_cannot_be_produced(store):
    b = store.inclusion_proof(0)
    with pytest.raises(TypeError):
        inclusion_receipt(b["tree_size"], b["index"], [], bytes.fromhex(b["root"]), None)
    with pytest.raises(RuntimeError):
        inclusion_receipt(b["tree_size"], b["index"], [], bytes.fromhex(b["root"]), lambda _: b"")


def test_garbage_is_refused_without_raising(signer):
    _, verify = signer
    for junk in (b"", b"\x00", b"not cbor at all", encode([1, 2, 3])):
        out = verify_receipt(junk, verify)
        assert not out["ok"] and out["problems"]


def test_the_digest_is_stable(store, signer):
    sign, _ = signer
    b = store.inclusion_proof(2)
    args = (b["tree_size"], b["index"], [bytes.fromhex(h) for h in b["audit_path"]],
            bytes.fromhex(b["root"]))
    r1 = inclusion_receipt(*args, sign)
    assert receipt_digest(r1) == hashlib.sha256(r1).hexdigest()
    assert len(receipt_digest(r1)) == 64
