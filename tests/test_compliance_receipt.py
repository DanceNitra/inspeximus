"""1.5.0: signed proof-of-erasure compliance receipt (GDPR Art.17 / EU AI Act record-keeping artifact)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from mnemo import new_source_keypair
from mnemo.erasure_auditor import (
    ErasureAuditor, TextStoreProbe, verify_compliance_receipt, ed25519_signer, ed25519_verify,
)


def _auditor(leaky):
    a = ErasureAuditor()
    texts = ["alice's balance was 12.69"] if leaky else ["nothing sensitive here"]
    return a.register(TextStoreProbe("logs", texts))


def test_receipt_records_verdict():
    r = _auditor(leaky=False).compliance_receipt("alice", ["12.69"], request_id="dsar-7", basis="GDPR Art.17")
    assert r["erasure_verified"] is True and r["basis"] == "GDPR Art.17"
    assert r["request_id"] == "dsar-7" and r["receipt_version"] == 1
    assert "generated_unix" in r and r["stores_audited"] == ["logs"]


def test_receipt_flags_a_leak():
    r = _auditor(leaky=True).compliance_receipt("alice", ["12.69"])
    assert r["erasure_verified"] is False and "logs" in r["leaking_stores"]


def test_signed_receipt_verifies():
    sk, pk = new_source_keypair()
    r = _auditor(leaky=False).compliance_receipt("alice", ["12.69"], sign=ed25519_signer(sk), pubkey=pk,
                                                 now=1_700_000_000.0)
    assert r["signature"] and r["pubkey"] == pk
    ok, reason = verify_compliance_receipt(r, ed25519_verify)
    assert ok is True and reason == "ok"
    ok2, _ = verify_compliance_receipt(r, ed25519_verify, expected_pubkey=pk)
    assert ok2 is True


def test_tampered_receipt_fails():
    sk, pk = new_source_keypair()
    r = _auditor(leaky=False).compliance_receipt("alice", ["12.69"], sign=ed25519_signer(sk), pubkey=pk,
                                                 now=1_700_000_000.0)
    r["erasure_verified"] = True  # already True; flip a real field to simulate tampering
    r["subject"] = "bob"          # alter the signed content
    ok, reason = verify_compliance_receipt(r, ed25519_verify)
    assert ok is False and "does not verify" in reason


def test_swapped_key_rejected_when_pinned():
    sk, pk = new_source_keypair()
    sk2, pk2 = new_source_keypair()
    r = _auditor(leaky=False).compliance_receipt("alice", ["12.69"], sign=ed25519_signer(sk), pubkey=pk)
    # attacker re-signs the same body with their own key and swaps both sig+pubkey
    from mnemo.erasure_auditor import _receipt_message
    r["signature"] = ed25519_signer(sk2)(_receipt_message(r)); r["pubkey"] = pk2
    ok_unpinned, _ = verify_compliance_receipt(r, ed25519_verify)          # self-consistent -> passes
    ok_pinned, reason = verify_compliance_receipt(r, ed25519_verify, expected_pubkey=pk)  # pinned -> rejected
    assert ok_unpinned is True and ok_pinned is False and "expected_pubkey" in reason


def test_unsigned_receipt_reports_unsigned():
    r = _auditor(leaky=False).compliance_receipt("alice", ["12.69"])
    ok, reason = verify_compliance_receipt(r, ed25519_verify)
    assert ok is False and reason == "unsigned receipt"
