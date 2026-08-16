"""Three attestation gaps, all reachable by an editor who reseals the bundle's own advisory hash.

`bundle_hash` is self-computed and documented in-band as advisory, so every attack here begins by
editing the artifact and recomputing it. That is not a finding; it is the threat model. What each
test measures is whether anything UNDERNEATH the seal notices.

  * The Merkle ROOTS were in the anchor and bound by nothing. `_STH_FIELDS` is
    ("n_writes", "writes_tip", "n_tombstones", "tombstones_tip") — tips and counts, not roots — and
    `verify_bundle` never re-derived them. Zero `writes_root`, reseal, verdict PASS. The root is what
    an inclusion proof verifies against, the SCITT-style receipt an auditor checks WITHOUT the log,
    so a substituted root lets a proof over a forged tree verify clean.

  * SIGNATURES could be stripped. Delete every `sig` and `pubkey`, reseal: ok=True, no problem, and
    nothing anywhere saying the artifact had ever been signed. The chain stays internally consistent
    — that is exactly why "no signature" has to be reported as a FACT rather than as an absence of
    findings.

  * TOMBSTONES honoured `receipt_key` (the in-process key) and ignored `receipt_signer` (an external
    KMS/HSM) entirely. So the deployment that took the write-authority boundary seriously — where the
    store cannot mint a signature at all — got UNSIGNED erasure proofs, and `governance_report` said
    `all_signed: False` with nothing explaining why.
"""
from __future__ import annotations

import copy
import os
import tempfile

import pytest

from inspeximus import Inspeximus
from inspeximus.audit_bundle import _bundle_hash, build_bundle, verify_bundle
from inspeximus.core import _Ed25519SK, new_ed25519_keypair

SK, PUB = new_ed25519_keypair()


def _signed_store(**kw):
    d = tempfile.mkdtemp()
    ix = Inspeximus(path=os.path.join(d, "s.json"), receipts=True, **kw)
    for i in range(3):
        ix.remember(f"record {i}", key=f"k{i}", object=f"v{i}")
    ix.flush()
    return ix


def _reseal(b):
    b.pop("bundle_hash", None)
    b["bundle_hash"] = _bundle_hash(b)
    return b


# ─────────────────────────────────────────────────────────── merkle roots
def test_control_an_honest_bundle_verifies():
    """First: every test below asserts a FAILURE, and a verifier that fails on everything catches
    nothing."""
    assert verify_bundle(build_bundle(_signed_store(receipt_key=SK), expected_pubkey=PUB))["ok"]


@pytest.mark.parametrize("field", ["writes_root", "tombstones_root", "root_hash"])
def test_a_substituted_merkle_root_is_re_derived_and_caught(field):
    b = build_bundle(_signed_store(receipt_key=SK), expected_pubkey=PUB)
    if field not in (b.get("anchor") or {}):
        pytest.skip(f"anchor carries no {field}")
    t = copy.deepcopy(b)
    t["anchor"][field] = "0" * 64
    out = verify_bundle(_reseal(t))
    assert not out["ok"] and any(field in x for x in out["problems"]), out["problems"]


def test_the_roots_are_checked_against_the_CHAIN_not_against_each_other():
    """The distinction that matters. Recomputing `root_hash` from the anchor's own root fields is
    self-consistency; re-deriving the roots from the chains the bundle carries is verification. A
    consistent set of substituted roots must still fail."""
    ix = _signed_store(receipt_key=SK)
    b = build_bundle(ix, expected_pubkey=PUB)
    t = copy.deepcopy(b)
    from inspeximus.core import _canon, _sha256_hex
    t["anchor"]["writes_root"] = "0" * 64
    t["anchor"]["root_hash"] = _sha256_hex(_canon({k: t["anchor"].get(k) for k in
                                                   ("n_writes", "writes_root", "n_tombstones",
                                                    "tombstones_root", "merkle")}))
    out = verify_bundle(_reseal(t))
    assert not out["ok"] and any("writes_root" in x and "chain" in x for x in out["problems"]), \
        out["problems"]


# ─────────────────────────────────────────────────────────── signature downgrade
def test_a_stripped_bundle_is_reported_as_unsigned_rather_than_as_verified():
    b = build_bundle(_signed_store(receipt_key=SK), expected_pubkey=PUB)
    t = copy.deepcopy(b)
    for r in (t.get("write_chain") or []):
        r.pop("sig", None)
        r.pop("pubkey", None)
    out = verify_bundle(_reseal(t))
    assert any("UNSIGNED" in x for x in out["limits"] + out["problems"]), \
        "the downgrade is silent, which is the whole defect"


def test_require_signed_refuses_the_downgrade():
    b = build_bundle(_signed_store(receipt_key=SK), expected_pubkey=PUB)
    t = copy.deepcopy(b)
    for r in (t.get("write_chain") or []):
        r.pop("sig", None)
        r.pop("pubkey", None)
    assert not verify_bundle(_reseal(t), require_signed=True)["ok"]


def test_a_genuinely_unsigned_store_still_passes_by_default():
    """THE control on the honest side. An unsigned bundle from an unsigned store is legitimate, and
    the check cannot prove a bundle WAS signed -- an attacker strips the pubkey too. What it buys is
    that the auditor is TOLD which one they are holding."""
    d = tempfile.mkdtemp()
    ix = Inspeximus(path=os.path.join(d, "s.json"), receipts=True)
    ix.remember("an unsigned store", key="k")
    ix.flush()
    out = verify_bundle(build_bundle(ix))
    assert out["ok"] and any("UNSIGNED" in x for x in out["limits"])


def test_a_partially_signed_chain_is_a_problem_not_a_note():
    """Between the two honest cases sits one that is neither: a chain signed in places is not signed,
    and reporting it as an ordinary unsigned store would hide a targeted strip."""
    ix = _signed_store(receipt_key=SK)
    b = build_bundle(ix, expected_pubkey=PUB)
    t = copy.deepcopy(b)
    t["write_chain"][0].pop("sig", None)
    out = verify_bundle(_reseal(t))
    assert not out["ok"] and any("PARTIALLY SIGNED" in x for x in out["problems"]), out["problems"]


# ─────────────────────────────────────────────────────────── the tombstone signing path
def _erasing_store(**kw):
    d = tempfile.mkdtemp()
    ix = Inspeximus(path=os.path.join(d, "s.json"), receipts=True, **kw)
    ix.remember("alice record", key="a", source={"doc": "alice"})
    ix.forget_subject("alice", request_id="DSAR-1", basis="art17", authorized_by="dpo")
    return ix


def test_an_external_signer_signs_the_tombstones_too():
    calls = []

    def signer(h):
        calls.append(h)
        return _Ed25519SK.from_private_bytes(bytes.fromhex(SK)).sign(bytes.fromhex(h)).hex()

    ix = _erasing_store(receipt_signer=signer, receipt_pubkey=PUB)
    assert ix._tombstones and all("sig" in t for t in ix._tombstones), \
        "the external signer was ignored on the erasure proof"
    assert len(calls) >= 2, "the signer was asked for the receipt but not the tombstone"
    assert ix.governance_report()["proof"]["all_signed"] is True


def test_control_the_in_process_key_path_still_signs_them():
    ix = _erasing_store(receipt_key=SK)
    assert all("sig" in t for t in ix._tombstones)


def test_a_failing_signer_refuses_the_tombstone_rather_than_writing_it_unsigned():
    """Fails CLOSED, like the write path. An unsigned tombstone written when a signer was configured
    would later read as "no signature required" -- the failure-open this boundary exists to prevent."""
    def working(h):
        return _Ed25519SK.from_private_bytes(bytes.fromhex(SK)).sign(bytes.fromhex(h)).hex()

    def broken(h):
        raise RuntimeError("KMS unreachable")

    # The signer must be healthy for the WRITE and break before the ERASURE, or the write path raises
    # first and this test passes on the wrong assertion -- which it did on the first attempt.
    d = tempfile.mkdtemp()
    ix = Inspeximus(path=os.path.join(d, "s.json"), receipts=True,
                    receipt_signer=working, receipt_pubkey=PUB)
    ix.remember("alice record", key="a", source={"doc": "alice"})
    ix._receipt_signer = broken
    with pytest.raises(RuntimeError, match="refusing to append an unsigned tombstone"):
        ix.forget_subject("alice", request_id="DSAR-1", basis="art17", authorized_by="dpo")
