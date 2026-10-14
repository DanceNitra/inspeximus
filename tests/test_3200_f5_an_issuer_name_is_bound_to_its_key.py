"""3.20, F5 (Claude Security scan, MEDIUM for multi-issuer deployments): an issuer name can be bound to its key.

THE DEFECT. SCRAPI's `--issuer-pubkey` accepted any listed key for any issuer name, so a statement signed by issuer
A's key that claimed to be issuer B was registered and logged as `issuer:B` (measured on v3.19.1, AUDIT-A's
f3f5_repro.py). The log recorded what the statement CLAIMED and nothing about which key signed it.

THE FIX (AUDIT-A's design, EM 2026-10-14; no break in 3.20). `TransparencyService(issuer_keys={name: pubkey})` and
SCRAPI's `--issuer NAME=PUBKEY` bind a name to its key: a statement claiming a bound name is verified against that key
only, so neither another bound key nor a legacy `--issuer-pubkey` key can claim it. `--issuer-pubkey` keeps its
3.19 behaviour, with a warning when two or more keys make the claim unauthenticated. Every new entry records
`signed_by`, the fingerprint of the key that verified, so even a legacy deployment's log says who really signed.
"""
import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

pytest.importorskip("cryptography")
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey  # noqa: E402

from inspeximus import cose, scitt  # noqa: E402
from inspeximus.scrapi import parse_issuers, policy_from_args  # noqa: E402
from inspeximus.transparency import (RegistrationPolicy, RegistrationRefused, TransparencyService,  # noqa: E402
                                     key_fingerprint)


def _key():
    k = Ed25519PrivateKey.generate()
    return k, k.public_key().public_bytes_raw().hex()


A, A_PUB = _key()
B, B_PUB = _key()
SVC, SVC_PUB = _key()


def _legacy_verifier(*pubs):
    """SCRAPI's --issuer-pubkey verifier: any listed key, for any name, and `which` names the key."""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    def which(msg, sig):
        for h in pubs:
            try:
                Ed25519PublicKey.from_public_bytes(bytes.fromhex(h)).verify(sig, msg)
                return h
            except Exception:                                       # noqa: BLE001
                continue
        return None

    def verify(msg, sig):
        return which(msg, sig) is not None
    verify.which = which
    return verify


def _svc(tmp_path, verifier, issuer_keys=None, name="reg.log"):
    return TransparencyService(str(tmp_path / name), RegistrationPolicy("p", accepted_issuers=None, notes=""),
                               SVC.sign, verifier, service_pubkey=SVC_PUB, issuer_keys=issuer_keys)


def _statement(key, issuer, payload=b"x" * 32):
    return scitt.signed_statement(payload, issuer, "subject:s", key.sign)


def _last(svc):
    return svc._entries[-1]


def test_a_legacy_deployment_still_registers_the_claim_but_records_who_signed(tmp_path):
    """3.19 behaviour kept for --issuer-pubkey, and the log now says which key verified."""
    svc = _svc(tmp_path, _legacy_verifier(A_PUB, B_PUB))
    svc.register(_statement(A, "issuer:B"))                          # AUDIT-A's F5: A's key claiming B
    e = _last(svc)
    assert e["issuer"] == "issuer:B" and e["signed_by"] == key_fingerprint(A_PUB), e


def test_a_bound_name_is_verified_against_its_own_key_only(tmp_path):
    svc = _svc(tmp_path, _legacy_verifier(A_PUB), issuer_keys={"issuer:B": B_PUB})
    with pytest.raises(RegistrationRefused, match="signature does not verify"):
        svc.register(_statement(A, "issuer:B"))                      # a legacy key claiming a bound name
    n = len(svc._entries)
    svc.register(_statement(B, "issuer:B"))
    assert len(svc._entries) == n + 1 and _last(svc)["signed_by"] == key_fingerprint(B_PUB)
    svc.register(_statement(A, "issuer:other"))                      # an unbound name still goes the legacy way
    assert _last(svc)["signed_by"] == key_fingerprint(A_PUB)


def test_one_bound_key_cannot_claim_another_bound_name(tmp_path):
    svc = _svc(tmp_path, lambda m, s: False, issuer_keys={"issuer:A": A_PUB, "issuer:B": B_PUB})
    with pytest.raises(RegistrationRefused):
        svc.register(_statement(A, "issuer:B"))
    svc.register(_statement(A, "issuer:A"))
    assert _last(svc)["issuer"] == "issuer:A"


def test_a_verifier_that_cannot_name_its_key_records_signed_by_none(tmp_path):
    svc = _svc(tmp_path, lambda m, s: True)
    svc.register(_statement(A, "issuer:any"))
    assert "signed_by" in _last(svc) and _last(svc)["signed_by"] is None


def test_a_319_log_with_new_entries_still_proves_every_entry(tmp_path):
    """Entries written before 3.20 carry no signed_by. A log that holds both must still prove every leaf."""
    svc = _svc(tmp_path, _legacy_verifier(A_PUB))
    for i in range(2):
        svc.register(_statement(A, "issuer:a", b"%d" % i * 32))
    p = tmp_path / "reg.log"
    old = [json.loads(ln) for ln in p.read_text().splitlines() if ln.strip()]
    for e in old:
        e.pop("signed_by", None)                                     # what a 3.19 service wrote
    p.write_text("".join(json.dumps(e) + "\n" for e in old))
    svc2 = _svc(tmp_path, _legacy_verifier(A_PUB))
    svc2.register(_statement(A, "issuer:a", b"new" * 11))
    assert "signed_by" not in svc2._entries[1] and svc2._entries[-1]["signed_by"]

    def verify(msg, sig):
        try:
            SVC.public_key().verify(sig, msg)
            return True
        except Exception:                                            # noqa: BLE001
            return False
    for i in range(svc2.size()):
        got = cose.verify_receipt(svc2.receipt_for(i), verify, leaf_data=svc2.entry_leaf(i))
        assert got["signature_ok"] and got["inclusion_ok"], (i, got["problems"])


class _Args:
    def __init__(self, **kw):
        self.policy_name, self.policy_notes_file, self.accept_any_issuer, self.issuer_pubkey = "p", None, False, []
        self.__dict__.update(kw)


def test_bound_names_are_the_policy_and_a_mixed_setup_says_it_is_not_authenticated():
    keys = parse_issuers(["issuer:a=" + A_PUB, "issuer:b=" + B_PUB])
    assert keys == {"issuer:a": A_PUB, "issuer:b": B_PUB}
    pure = policy_from_args(_Args(), issuer_keys=keys, legacy_open=False)
    assert pure.accepted_issuers == ["issuer:a", "issuer:b"]
    mixed = policy_from_args(_Args(issuer_pubkey=[A_PUB]), issuer_keys=keys, legacy_open=True)
    assert mixed.accepted_issuers == [] and "NOT authenticated" in mixed.notes
    with pytest.raises(SystemExit):
        parse_issuers(["no-separator"])
