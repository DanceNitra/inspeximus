"""The crypto / capability surface, which had ZERO executed body lines before this file.

Measured with coverage at 1.62.0: **115 of 318 public functions (36%) never executed a single body line** —
and the largest of them were the ones an auditor is meant to run. `verify_erasure_certificate` (62 lines),
`submit_revert` (58), `erasure_certificate` (22), `witness` / `verify_witness`, and the whole revert-capability
chain had no test at all.

Writing this file found a defect immediately: every summary field of the erasure certificate was forgeable
while `valid` stayed True. That is the DeletionManifest defect fixed in 1.59.0, one artifact over — and this
one is literally called a certificate.

Note on measuring coverage of a function: count only its BODY lines. The `def` line executes at import, so a
per-function range that includes it makes every function in the package look covered — my first attempt at
this measurement reported 2% uncovered instead of 36%.
"""
import copy
import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import inspeximus.core as core
from inspeximus import Inspeximus


def _path():
    return os.path.join(tempfile.mkdtemp(), "m.json")


def _erased_store():
    m = Inspeximus(path=_path(), receipts=True)
    m.remember("alice ssn 123", source={"doc": "alice"})
    m.remember("bob unrelated", source={"doc": "bob"})
    m.forget_subject("alice", request_id="DSAR-1", basis="gdpr-art17")
    return m


# ── hydration witness ───────────────────────────────────────────────────────────────────────────────
def test_a_witness_verifies_against_the_store_it_was_taken_from():
    m = Inspeximus(path=_path(), receipts=True)
    m.remember("a")
    w = m.witness()
    res = m.verify_witness(w)
    assert res["valid"] is True and res["digest_match"] is True and res["receipts_tip_match"] is True


def test_a_witness_stops_verifying_once_the_store_moves():
    """A witness that still validated after the state changed would be worse than none — it is the whole
    point of taking one."""
    m = Inspeximus(path=_path(), receipts=True)
    m.remember("a")
    w = m.witness()
    m.remember("b")
    res = m.verify_witness(w)
    assert res["valid"] is False
    assert res["digest_match"] is False or res["receipts_tip_match"] is False


def test_a_tampered_witness_digest_is_rejected():
    m = Inspeximus(path=_path(), receipts=True)
    m.remember("a")
    w = dict(m.witness())
    w["digest"] = "0" * 64
    assert m.verify_witness(w)["valid"] is False


# ── erasure certificate ─────────────────────────────────────────────────────────────────────────────
def test_an_honest_erasure_certificate_verifies():
    m = _erased_store()
    cert = m.erasure_certificate(request_id="DSAR-1")
    res = core.verify_erasure_certificate(cert, store_items=m.items)
    assert res["valid"] is True and res["problems"] == []
    assert res["checks"]["store_absent"] is True, "the erased record must be genuinely absent"


@pytest.mark.parametrize("field,value", [
    ("count", 99),
    ("erased_memory_ids", ["never-existed-1", "never-existed-2"]),
    ("request_ids", ["SOMEONE-ELSES-REQUEST"]),
])
def test_a_forged_certificate_summary_does_not_verify(field, value):
    """THE defect this file found. `count`, `erased_memory_ids` and `request_ids` were echoed straight from
    the certificate and never re-derived from the tombstones — which ARE hash-chained and signed. So an
    operator could hand an auditor a certificate claiming to have erased records that never existed, and it
    verified `valid: True` with no problems."""
    m = _erased_store()
    cert = copy.deepcopy(m.erasure_certificate(request_id="DSAR-1"))
    cert[field] = value
    res = core.verify_erasure_certificate(cert, store_items=m.items)
    assert res["valid"] is False, f"a forged {field} verified clean"
    assert res["problems"]


def test_a_certificate_fails_when_an_erased_record_is_still_present():
    """The 'read the raw store' proof — the check soft-delete systems cannot pass."""
    m = _erased_store()
    cert = m.erasure_certificate(request_id="DSAR-1")
    resurrected = m.items + [{"id": cert["erased_memory_ids"][0], "text": "alice ssn 123",
                              "status": "active", "ts": 0.0, "value": 1.0}]
    res = core.verify_erasure_certificate(cert, store_items=resurrected)
    assert res["valid"] is False
    assert any("STILL PRESENT" in p for p in res["problems"])


def test_a_certificate_with_a_broken_tombstone_chain_fails():
    m = _erased_store()
    cert = copy.deepcopy(m.erasure_certificate(request_id="DSAR-1"))
    if cert["tombstones"]:
        cert["tombstones"][0]["hash"] = "0" * 64
    res = core.verify_erasure_certificate(cert, store_items=m.items)
    assert res["valid"] is False


def test_pinning_an_unexpected_pubkey_fails_the_certificate():
    m = _erased_store()
    cert = m.erasure_certificate(request_id="DSAR-1")
    res = core.verify_erasure_certificate(cert, store_items=m.items, expected_pubkey="ab" * 32)
    assert res["valid"] is False


# ── the revert capability chain ─────────────────────────────────────────────────────────────────────
def _two_values():
    m = Inspeximus(path=_path(), receipts=True)
    m.remember("v1", key="k", object="1")
    m.remember("v2", key="k", object="2")
    return m


def test_a_revert_challenge_and_intent_are_bound_to_the_current_state():
    """Both embed the id of the record a revert would undo, so a captured one cannot be replayed after the
    state moves — the property they exist for."""
    m = _two_values()
    challenge = m.revert_challenge("k")
    intent = m.revert_intent("k")
    assert challenge.startswith("revert:k:")
    assert intent.startswith("revert:k@")

    m.remember("v3", key="k", object="3")                 # the state moves
    assert m.revert_challenge("k") != challenge, "a challenge must not survive a state change"


def test_minting_a_capability_without_an_authority_is_refused():
    """Failing OPEN here would hand out a capability nobody can revoke."""
    m = _two_values()
    with pytest.raises(RuntimeError, match="authority"):
        m.revert_capability("k")


def test_submit_revert_rejects_an_intent_for_an_unknown_key():
    m = _two_values()
    res = m.submit_revert("revert:nosuchkey@deadbeef#0000", capability=None)
    assert res.get("ok") is not True


def test_submit_revert_rejects_a_replayed_intent():
    """The nonce is single-use; without that a captured intent reverts the key again later."""
    m = _two_values()
    intent = m.revert_intent("k")
    first = m.submit_revert(intent)
    if first.get("ok"):                                   # legacy-allow path: no authority configured
        assert m.submit_revert(intent).get("ok") is not True, "the same intent must not apply twice"
    else:
        assert first.get("ok") is not True                # authority required: refused, which is also fine


def test_revert_now_on_a_key_with_no_prior_value_is_refused():
    m = Inspeximus(path=_path(), receipts=True)
    m.remember("only value", key="k", object="1")
    res = m.revert_now("k")
    assert res.get("ok") is not True


def test_sign_revert_produces_a_verifiable_signature():
    """`sign_revert` had zero coverage; it is the principal side of the capability handshake."""
    pytest.importorskip("cryptography")
    sk, pk = core.new_ed25519_keypair()
    m = _two_values()
    challenge = m.revert_challenge("k")
    sig = core.sign_revert(sk, challenge)
    assert isinstance(sig, str) and len(sig) >= 64

    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    Ed25519PublicKey.from_public_bytes(bytes.fromhex(pk)).verify(
        bytes.fromhex(sig), challenge.encode("utf-8"))     # raises if it does not verify


def test_a_signature_over_the_wrong_challenge_does_not_verify():
    pytest.importorskip("cryptography")
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    sk, pk = core.new_ed25519_keypair()
    sig = core.sign_revert(sk, "revert:k:aaaaaaaa")
    with pytest.raises(InvalidSignature):
        Ed25519PublicKey.from_public_bytes(bytes.fromhex(pk)).verify(
            bytes.fromhex(sig), b"revert:k:bbbbbbbb")
