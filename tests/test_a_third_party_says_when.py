"""A timestamp is the one claim in this package we cannot manufacture ourselves.

WHY. Everything else proves ORDER: this entry is in the log, this log extends that one. None of it
proves WHEN, because every clock in the system belongs to the operator being audited. RFC 3161 buys
the missing half from a third party, and under eIDAS Article 41 a qualified timestamp from an EU
Trusted List provider carries a rebuttable presumption of the time it shows.

THE FIXTURE IS A REAL TOKEN. `fixtures/digicert_granted.tsr` was issued by DigiCert's public TSA on
2026-08-31 over sha256(b"a transparency log head"), using the request this module builds. That is the
strongest available evidence that our DER is correct: a production authority parsed it and answered.
A mock we wrote would only prove our encoder agrees with our decoder.

WHAT THESE TESTS PIN:

  * The request carries our digest, starts as a DER SEQUENCE, and refuses anything that is not a
    SHA-256, because a timestamp over a digest nobody can recompute proves nothing about the log.
  * A rejection is never stored as if it were a token. `read_status` distinguishes granted from
    refused, and `stamp` raises rather than returning a dict a caller might file as evidence.
  * `verify_with_openssl` reports UNVERIFIED, not FAILED, when it cannot check. "I could not check
    this" and "this is bad" are different answers and a caller acting on the wrong one is the reason
    the distinction is tested.

Nothing here reaches the network. The live TSA call is exercised by `--tsa-url`, opt in.
"""
from __future__ import annotations

import hashlib
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from inspeximus.timestamp import (PKI_STATUS, TimestampError, read_status, request_bytes,
                                  verify_with_openssl)

FIXTURE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "digicert_granted.tsr")
STAMPED = hashlib.sha256(b"a transparency log head").digest()


def _token():
    with open(FIXTURE, "rb") as fh:
        return fh.read()


def test_the_request_is_der_and_carries_the_digest():
    d = hashlib.sha256(b"anything").digest()
    req = request_bytes(d, nonce=0x1122334455667788)
    assert req[0] == 0x30, "a TimeStampReq is a DER SEQUENCE"
    assert d in req, "the imprint must be the digest we asked about"
    assert b"\x06\x09\x60\x86\x48\x01\x65\x03\x04\x02\x01" in req, "the sha256 OID"


def test_a_nonce_is_present_and_differs_between_calls():
    """Without a nonce, a TSA or anything between you and it can return a token minted earlier for
    the same digest, and a replay is indistinguishable from a fresh answer."""
    d = hashlib.sha256(b"x").digest()
    a, b = request_bytes(d), request_bytes(d)
    assert a != b, "two requests for one digest must not be byte-identical"


def test_only_a_sha256_is_accepted():
    for bad in (b"", b"short", b"x" * 31, b"x" * 33, "not bytes"):
        with pytest.raises(ValueError):
            request_bytes(bad)


def test_a_real_authority_granted_our_request():
    """The fixture is the answer a production TSA gave to bytes this module produced."""
    st = read_status(_token())
    assert st["status"] == 0 and st["status_text"] == "granted"
    assert st["has_token"] is True
    assert st["problems"] == []


def test_the_token_covers_the_digest_we_asked_about():
    """The binding that makes the token worth keeping. A timestamp over somebody else's hash is a
    timestamp about somebody else."""
    assert STAMPED in _token()
    assert STAMPED.hex() == "30c322b8b8afd8948a16a7c23068f9912f1c4f0f50e766771ab0701f6e6c8317"


def test_a_rejection_is_recognised_and_not_mistaken_for_proof():
    """CONTROL for the granted case above: without a refused token to compare against, "granted"
    would pass equally on a reader that says granted to everything."""
    # PKIStatus 2 = rejection, hand-built to the shape RFC 3161 section 2.4.2 defines
    rejected = bytes([0x30, 0x05, 0x30, 0x03, 0x02, 0x01, 0x02])
    st = read_status(rejected)
    assert st["status"] == 2 and st["status_text"] == "rejection"
    assert st["has_token"] is False
    assert any("did not grant" in p for p in st["problems"])


def test_every_status_code_the_rfc_defines_has_a_name():
    assert set(PKI_STATUS) == {0, 1, 2, 3, 4, 5}
    assert PKI_STATUS[1] == "grantedWithMods"


def test_garbage_is_reported_rather_than_parsed():
    for junk in (b"", b"not der at all", b"\x02\x01\x00"):
        st = read_status(junk)
        assert st["has_token"] is False and st["problems"]


def test_openssl_reports_unverified_rather_than_failed_without_a_trust_anchor():
    """"I could not check this" and "this is bad" are different answers, and a caller that cannot
    tell them apart acts on the wrong one."""
    out = verify_with_openssl(_token(), STAMPED, ca_file=None)
    assert out["verified"] is None, "no CA file means unverified, never False"
    assert out["ran"] is False
    assert any("trust anchor" in p for p in out["problems"])


def test_a_missing_openssl_is_reported_and_not_silently_passed():
    out = verify_with_openssl(_token(), STAMPED, ca_file=__file__,
                              openssl=os.path.join(os.path.dirname(__file__), "no-such-openssl"))
    assert out["verified"] is None, "an unrunnable verifier must not produce a verdict"
    assert any("could not run" in p for p in out["problems"])


def test_a_missing_ca_file_is_unverified_and_not_a_failed_token():
    """openssl exits non-zero for a missing CA file exactly as it does for a forged token. Reporting
    the first as verified=False would tell a reader the timestamp is bad when the truth is that we
    never looked."""
    out = verify_with_openssl(_token(), STAMPED, ca_file="/no/such/ca.pem")
    assert out["verified"] is None
    assert out["ran"] is False
    assert any("does not exist" in p for p in out["problems"])
