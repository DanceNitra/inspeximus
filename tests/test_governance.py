"""Governance + tamper-evidence: CT-style anchor, authenticated-principal erasure, decision basis."""
import sys, os, copy
import pytest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from inspeximus import (Inspeximus, new_receipt_keypair, new_source_keypair, sign_erasure, erasure_challenge)
from inspeximus.core import _sha256_hex, _canon, _GENESIS

from _store_io import edit_store


def test_anchor_verifies_append_only_extension():
    m = Inspeximus(receipts=True)
    for i in range(3):
        m.remember(f"f{i}")
    a = m.anchor()
    m.remember("f3")
    ok, problems = m.verify_consistency(a)
    assert ok and not problems


def test_anchor_catches_operator_rewrite():
    m = Inspeximus(receipts=True)
    for i in range(4):
        m.remember(f"f{i}")
    a = m.anchor()
    # operator with the key rewrites history AND re-chains it so verify_writes still passes
    forged = copy.deepcopy(m)
    forged.items[1]["text"] = "ATTACKER"
    by_id = {it["id"]: it for it in forged.items}
    prev = _GENESIS
    for r in forged._receipts:
        rec = by_id.get(r["memory_id"])
        if rec is not None:
            r["commit"] = forged._write_commit(rec)
        r["prev"] = prev
        r["hash"] = _sha256_hex(_canon({k: r.get(k) for k in ("seq", "ts", "memory_id", "commit", "prev")}))
        prev = r["hash"]
    ok_wr, _ = forged.verify_writes()
    ok_c, problems = forged.verify_consistency(a)
    assert ok_wr is True            # verify_writes cannot catch a re-chained rewrite
    assert ok_c is False and problems   # the external anchor does


def test_anchor_catches_rollback():
    m = Inspeximus(receipts=True)
    for i in range(5):
        m.remember(f"f{i}")
    a = m.anchor()
    m._receipts = m._receipts[:2]
    ok, problems = m.verify_consistency(a)
    assert not ok and problems


def test_erasure_binds_authenticated_principal_and_basis():
    sk, pk = new_receipt_keypair()
    m = Inspeximus(receipts=True, receipt_key=sk, receipt_pubkey=pk)
    m.remember("bob data", source={"doc": "bob"})
    psk, ppk = new_source_keypair()
    authz = sign_erasure(psk, "bob", "req-1")
    m.forget_subject("bob", request_id="req-1", basis="GDPR Art.17", authorized_by=ppk, authorization=authz)
    t = m._tombstones[-1]
    assert t["auth"]["basis"] == "GDPR Art.17"
    assert t["auth"]["authorized_by"] == ppk
    # the authorization actually verifies against the principal + request
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    Ed25519PublicKey.from_public_bytes(bytes.fromhex(ppk)).verify(
        bytes.fromhex(t["auth"]["authorization"]), erasure_challenge("bob", "req-1").encode())


def test_governance_report_carries_anchor():
    sk, pk = new_receipt_keypair()
    m = Inspeximus(receipts=True, receipt_key=sk, receipt_pubkey=pk)
    m.remember("x", source={"doc": "s"}); m.forget_subject("s", request_id="r")
    rep = m.governance_report(expected_pubkey=pk)
    assert rep["proof"]["verified"]
    assert rep["proof"]["anchor"]["sth_hash"] and "writes_tip" in rep["proof"]["anchor"]


def test_receipt_pubkey_is_derived_when_only_the_private_key_is_given(tmp_path):
    """Every other test in this file passes BOTH halves, which is why this went unnoticed.

    Passing `receipt_key` alone used to sign each receipt with `"pubkey": None`, so verify_writes()
    could not check the signature and reported "invalid signature" on records the store had just
    written itself. A false tampering alarm is worse than no alarm: it trains the reader to ignore it.
    """
    sk, pk = new_receipt_keypair()
    m = Inspeximus(path=str(tmp_path / "a.json"), receipts=True, receipt_key=sk)
    assert m.receipt_pubkey == pk
    m.remember("the sky is blue")
    assert m.verify_writes() == (True, [])


def test_a_malformed_receipt_key_is_rejected_at_construction(tmp_path):
    """It used to surface as a ValueError from bytes.fromhex deep inside remember()."""
    with pytest.raises(ValueError, match="Ed25519 private key"):
        Inspeximus(path=str(tmp_path / "b.json"), receipts=True, receipt_key="not-a-key")


def test_tamper_detection_still_fires_after_the_derivation_fix(tmp_path):
    """The control for the fix: deriving the key must not turn verification into a rubber stamp."""
    sk, _ = new_receipt_keypair()
    p = tmp_path / "c.json"
    m = Inspeximus(path=str(p), receipts=True, receipt_key=sk)
    m.remember("the invoice is 100 EUR")
    m._save(force=True)
    def _bump(rows):
        for r in rows:
            r["text"] = r["text"].replace("100 EUR", "900 EUR")
    edit_store(p, _bump)
    ok, problems = Inspeximus(path=str(p), receipts=True, receipt_key=sk).verify_writes()
    assert ok is False and problems
