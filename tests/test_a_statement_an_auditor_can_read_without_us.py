"""A Signed Statement is the first artifact we emit whose MEANING comes from an RFC rather than from us.

WHY IT EXISTS. `cose.py` proves a leaf is in a log. That is a strong fact and an incomplete one: it
says nothing about who put the leaf there or what it is about, so a third party holding a receipt still
has to take our word for the rest. RFC 9943 section 6 closes it. A Signed Statement is a COSE_Sign1
whose protected header carries CWT Claims (RFC 9597, label 15) naming an Issuer (claim 1) and a
Subject (claim 2). Section 7 then defines a Transparent Statement: the same statement with its
Receipt in the UNPROTECTED header at label 394.

The unprotected placement is the load-bearing design detail, and these tests pin it. It lets a Receipt
be attached after the Issuer signed, by a different party, without invalidating the Issuer's
signature. Two signers, two claims, one artifact: the Issuer says what is true, the log says when it
was recorded, and neither has to trust the other.

WHAT THESE TESTS PIN:

  * The protected header really carries label 15 with claims 1 and 2. Asserted by decoding the CBOR,
    not by reading back what our own helper returned.
  * Every refusal is a refusal: no issuer, no subject, no signer, no payload.
  * expected_issuer and expected_subject fail on a mismatch AND pass on a match, because a check only
    tested in one direction has not been shown to discriminate.
  * Attaching a Receipt leaves the Issuer's signature verifiable.
  * Editing the payload after signing breaks it.
"""
from __future__ import annotations

import hashlib
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from inspeximus import cose, new_receipt_keypair
from inspeximus.merkle import inclusion_proof, root
from inspeximus.scitt import (CWT_ISSUER, CWT_SUBJECT, HDR_CWT_CLAIMS, HDR_RECEIPTS,
                              receipts_of, signed_statement, statement_digest,
                              transparent_statement, verify_signed_statement)

ISSUER = "did:web:agora.example"
SUBJECT = "memory:staging-db"


@pytest.fixture()
def signer():
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
    return (lambda b: sk.sign(b)), verify


@pytest.fixture()
def digest():
    return hashlib.sha256(b"the staging database is db-7.internal").digest()


def _protected_claims(statement: bytes) -> dict:
    """Read the claims out of the CBOR, so the assertion is about the wire format and not our helper."""
    protected = cose.decode(statement).value[0]
    return cose.decode(protected).get(HDR_CWT_CLAIMS)


def test_the_statement_carries_the_claims_the_rfc_requires(signer, digest):
    sign, _verify = signer
    st = signed_statement(digest, ISSUER, SUBJECT, sign)
    tagged = cose.decode(st)
    assert tagged.tag == cose.COSE_SIGN1_TAG
    claims = _protected_claims(st)
    assert isinstance(claims, dict), "RFC 9943 requires CWT Claims at label 15 in the PROTECTED header"
    assert claims[CWT_ISSUER] == ISSUER
    assert claims[CWT_SUBJECT] == SUBJECT
    assert len(tagged.value[3]) == 64, "Ed25519 signature"


def test_it_verifies_and_reports_who_said_what(signer, digest):
    sign, verify = signer
    out = verify_signed_statement(signed_statement(digest, ISSUER, SUBJECT, sign), verify)
    assert out["ok"] and out["signature_ok"]
    assert (out["issuer"], out["subject"]) == (ISSUER, SUBJECT)
    assert out["payload"] == digest


def test_an_edited_payload_breaks_the_signature(signer, digest):
    """The point of signing. Whoever holds the statement must not be able to change what it says."""
    sign, verify = signer
    st = signed_statement(digest, ISSUER, SUBJECT, sign)
    tagged = cose.decode(st)
    protected, unprotected, _payload, sig = tagged.value
    forged = cose.encode(cose.CBORTag(cose.COSE_SIGN1_TAG,
                                      [protected, unprotected, b"\x00" * 32, sig]))
    out = verify_signed_statement(forged, verify)
    assert not out["ok"] and not out["signature_ok"]


def test_a_different_signer_is_caught(signer, digest):
    sign, _verify = signer
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey as PK
    _sk2, pk2_hex = new_receipt_keypair()
    pk2 = PK.from_public_bytes(bytes.fromhex(pk2_hex))

    def other_verify(msg, sig):
        try:
            pk2.verify(sig, msg)
            return True
        except Exception:
            return False
    out = verify_signed_statement(signed_statement(digest, ISSUER, SUBJECT, sign), other_verify)
    assert not out["signature_ok"]


def test_the_expectations_discriminate_in_both_directions(signer, digest):
    """A check only exercised on the failing side has not been shown to pass anything either."""
    sign, verify = signer
    st = signed_statement(digest, ISSUER, SUBJECT, sign)
    assert verify_signed_statement(st, verify, expected_issuer=ISSUER)["ok"]
    assert not verify_signed_statement(st, verify, expected_issuer="did:web:someone.else")["ok"]
    assert verify_signed_statement(st, verify, expected_subject=SUBJECT)["ok"]
    assert not verify_signed_statement(st, verify, expected_subject="memory:other")["ok"]


def test_a_cose_sign1_without_cwt_claims_is_not_a_signed_statement(signer, digest):
    """CONTROL for the claims check: a well-formed COSE_Sign1 that omits label 15 must be refused,
    or the verifier is only checking that the bytes parse."""
    sign, verify = signer
    protected = cose.encode({cose.HDR_ALG: cose.ALG_EDDSA})          # no CWT Claims
    sig = sign(cose._sig_structure(protected, digest))
    bare = cose.encode(cose.CBORTag(cose.COSE_SIGN1_TAG, [protected, {}, digest, sig]))
    out = verify_signed_statement(bare, verify)
    assert not out["ok"]
    assert any("CWT Claims" in p for p in out["problems"])


def test_an_empty_issuer_or_subject_is_refused_rather_than_defaulted(signer, digest):
    """A statement whose issuer is a placeholder is worse than an unsigned one: it looks attributable."""
    sign, _verify = signer
    for bad in ("", "   ", None):
        with pytest.raises((ValueError, TypeError)):
            signed_statement(digest, bad, SUBJECT, sign)
        with pytest.raises((ValueError, TypeError)):
            signed_statement(digest, ISSUER, bad, sign)


def test_there_is_no_unsigned_path(digest):
    with pytest.raises(TypeError):
        signed_statement(digest, ISSUER, SUBJECT, None)
    with pytest.raises(RuntimeError):
        signed_statement(digest, ISSUER, SUBJECT, lambda _b: b"")


def test_extra_claims_cannot_overwrite_the_two_that_are_required(signer, digest):
    sign, verify = signer
    with pytest.raises(ValueError):
        signed_statement(digest, ISSUER, SUBJECT, sign, extra_claims={CWT_ISSUER: "someone else"})
    # CONTROL: an unrelated claim is accepted and survives the round trip.
    st = signed_statement(digest, ISSUER, SUBJECT, sign, extra_claims={6: 1735689600})
    assert _protected_claims(st)[6] == 1735689600
    assert verify_signed_statement(st, verify)["ok"]


# ---------------------------------------------------------------------------------------------------
# Transparent Statements: the Receipt rides in the UNPROTECTED header, so a second party can attach it.
# ---------------------------------------------------------------------------------------------------

def _receipt(sign, digest):
    leaves = [b"a", digest, b"c"]
    return cose.inclusion_receipt(len(leaves), 1, inclusion_proof(leaves, 1), root(leaves), sign), \
        root(leaves), leaves


def test_attaching_a_receipt_leaves_the_issuers_signature_intact(signer, digest):
    """THE reason RFC 9943 puts Receipts in the unprotected header, and the property that makes the
    artifact worth having: the Issuer signs what is true, the log signs when it was recorded, and
    neither has to hold the other's key."""
    sign, verify = signer
    st = signed_statement(digest, ISSUER, SUBJECT, sign)
    rec, r, _leaves = _receipt(sign, digest)
    ts = transparent_statement(st, [rec])

    assert len(receipts_of(ts)) == 1
    assert cose.decode(ts).value[1][HDR_RECEIPTS], "receipts belong at unprotected label 394"
    out = verify_signed_statement(ts, verify)
    assert out["ok"], out["problems"]
    assert out["issuer"] == ISSUER
    # And the receipt itself still checks out against the root.
    rv = cose.verify_receipt(receipts_of(ts)[0], verify, leaf_data=digest, expected_root=r)
    assert rv["ok"] and rv["root_matches"] is True


def test_a_bare_signed_statement_carries_no_receipts(signer, digest):
    """CONTROL for the count above: without this, `len(...) == 1` is consistent with a reader that
    always finds one."""
    sign, _verify = signer
    assert receipts_of(signed_statement(digest, ISSUER, SUBJECT, sign)) == []


def test_receipts_accumulate_rather_than_replace(signer, digest):
    """More than one log may register the same statement, and losing the first is losing evidence."""
    sign, _verify = signer
    st = signed_statement(digest, ISSUER, SUBJECT, sign)
    rec, _r, _l = _receipt(sign, digest)
    assert len(receipts_of(transparent_statement(transparent_statement(st, [rec]), [rec]))) == 2


def test_making_something_that_is_not_a_statement_transparent_is_refused(signer, digest):
    sign, _verify = signer
    rec, _r, _l = _receipt(sign, digest)
    with pytest.raises(ValueError):
        transparent_statement(cose.encode({"not": "a cose_sign1"}), [rec])
    with pytest.raises(ValueError):
        transparent_statement(signed_statement(digest, ISSUER, SUBJECT, sign), [])


def test_the_digest_is_stable_and_changes_with_the_bytes(signer, digest):
    sign, _verify = signer
    a = signed_statement(digest, ISSUER, SUBJECT, sign)
    assert statement_digest(a) == statement_digest(a)
    assert statement_digest(a) != statement_digest(
        signed_statement(digest, ISSUER, "memory:something-else", sign))


def test_the_statement_carries_a_digest_and_not_the_memory(signer):
    """A statement travels: to an auditor, to a regulator, possibly into a public log. Putting the
    record's text in it would leak the content the store exists to govern, in the one artifact most
    likely to be copied. This pins the convention the docstring states."""
    sign, _verify = signer
    secret = "the staging database is db-7.internal"
    st = signed_statement(hashlib.sha256(secret.encode()).digest(), ISSUER, SUBJECT, sign)
    assert secret.encode() not in st
    assert b"db-7" not in st


# ---------------------------------------------------------------------------------------------------
# The store surface: both halves built from one inclusion bundle, so they cannot be about two records.
# ---------------------------------------------------------------------------------------------------

def _store(n=3):
    import tempfile
    from inspeximus import Inspeximus, new_receipt_keypair
    sk, pk = new_receipt_keypair()
    path = os.path.join(tempfile.mkdtemp(), "m.json")
    m = Inspeximus(path=path, receipts=True, receipt_key=sk)
    for i in range(n):
        m.remember("fact number %d" % i, key="k%d" % i)
    m.flush()
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey as PK
    pub = PK.from_public_bytes(bytes.fromhex(pk))

    def verify(msg, sig):
        try:
            pub.verify(sig, msg)
            return True
        except Exception:
            return False
    return m, verify


def test_the_store_emits_a_pair_that_verifies_end_to_end():
    from inspeximus import verify_transparent_statement
    m, verify = _store()
    d = m.transparent_statement(0, issuer=ISSUER)
    out = verify_transparent_statement(bytes.fromhex(d["statement"]), verify, verify,
                                       d["leaf"].encode(), bytes.fromhex(d["root"]),
                                       expected_issuer=ISSUER)
    assert out["ok"], out["problems"]
    assert out["bound"] is True


def test_a_statement_and_a_receipt_about_different_records_do_not_pass():
    """THE check the pair exists for. Both artifacts verify on their own; nothing inside either one
    connects them, so an assembled pair would otherwise prove something about nothing."""
    from inspeximus import verify_transparent_statement
    m, verify = _store()
    a, b = m.transparent_statement(0, issuer=ISSUER), m.transparent_statement(1, issuer=ISSUER)
    out = verify_transparent_statement(bytes.fromhex(a["statement"]), verify, verify,
                                       b["leaf"].encode(), bytes.fromhex(b["root"]))
    assert out["bound"] is False and not out["ok"]
    assert any("different records" in p for p in out["problems"])


def test_the_subject_names_the_record_and_not_the_key_or_the_position():
    """Two writes under one key are two records. A subject of "staging-db" would not say which value
    it vouches for, and "write:0" means something else once the log grows."""
    m, _verify = _store(n=0)
    m.remember("v1", key="staging-db")
    m.remember("v2", key="staging-db")
    m.flush()
    a = m.transparent_statement(0, issuer=ISSUER)["subject"]
    b = m.transparent_statement(1, issuer=ISSUER)["subject"]
    assert a != b, "one key, two records, two subjects"
    assert a not in ("staging-db", "write:0") and b != "write:1"


def test_an_issuer_is_required_and_a_signer_must_exist():
    import tempfile
    from inspeximus import Inspeximus
    m, _verify = _store()
    with pytest.raises(ValueError):
        m.transparent_statement(0, issuer="")
    unsigned = Inspeximus(path=os.path.join(tempfile.mkdtemp(), "u.json"))
    unsigned.remember("x")
    unsigned.flush()
    with pytest.raises(RuntimeError):
        unsigned.transparent_statement(0, issuer=ISSUER)
