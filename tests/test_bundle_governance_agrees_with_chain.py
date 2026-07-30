"""The governance summary must agree with the tombstone chain in the same bundle.

An audit bundle is what gets handed to a regulator, and `governance` is the part read first:
"how many erasures, for which requests". The tombstone chain underneath it was already protected —
dropping a tombstone is caught — but the summary was not cross-checked against it. So a bundle could
carry `erasures_total: 0, by_request: {}` while its own tombstone_chain held two tombstones, and
verify_bundle returned ok=True with ZERO problems. Demonstrated before the fix.

SCOPE, which the module is already explicit about and this does not change: an exporter determined to
lie edits both halves and re-seals the unkeyed bundle_hash in three lines. Only the witness
co-signature check is operator-adversarial. This is an INTERNAL-CONSISTENCY check, the same kind the
anchor counts and n_records already get — its absence meant two halves of one fact could disagree in
silence, which is the misconfigured-export case rather than the malicious one.
"""
import copy
import os
import tempfile

import pytest

from inspeximus.audit_bundle import build_bundle, verify_bundle
from inspeximus.core import Inspeximus, _canon, _sha256_hex


def _reseal(b):
    """What an exporter editing the bundle would do — otherwise the outer hash catches it and the
    deeper checks are never exercised."""
    b["bundle_hash"] = _sha256_hex(_canon({k: v for k, v in b.items() if k != "bundle_hash"}))
    return b


@pytest.fixture()
def bundle(tmp_path):
    m = Inspeximus(str(tmp_path / "s.json"), receipts=True)
    for who in ("alice", "bob", "carol"):
        m.remember(f"{who} record", tags=["pii"], source={"doc": f"hr/{who}"})
    m.forget_subject("hr/alice")
    m.forget_subject("hr/bob")
    m.flush()
    b = build_bundle(m)
    assert len(b["tombstone_chain"]) == 2, "fixture must actually erase twice"
    return b


def test_control_an_untouched_bundle_still_verifies(bundle):
    """Without this the checks below could be a verifier that rejects everything."""
    r = verify_bundle(bundle)
    assert r["ok"] is True, r.get("problems")


def test_a_bundle_cannot_claim_it_never_erased_anything(bundle):
    """The headline case: understate the erasures and the evidence still sits in the same file."""
    b = _reseal(dict(copy.deepcopy(bundle), **{}))
    b["governance"]["erasures_total"] = 0
    b["governance"]["by_request"] = {}
    _reseal(b)

    r = verify_bundle(b)
    assert r["ok"] is False, (
        "a bundle claiming zero erasures while carrying two tombstones verified clean — the summary an "
        "auditor reads first was allowed to contradict the evidence beneath it")
    assert any("erasures_total" in p and "tombstone chain" in p for p in r["problems"]), r["problems"]


def test_the_per_request_breakdown_must_add_up_to_its_own_total(bundle):
    """Overstating one request while the total stays put is the same lie in the other direction."""
    b = copy.deepcopy(bundle)
    b["governance"]["by_request"] = {"r1": {"erased": 1, "memory_ids": ["x"]}}
    _reseal(b)

    r = verify_bundle(b)
    assert r["ok"] is False
    assert any("by_request" in p for p in r["problems"]), r["problems"]


def test_the_check_does_not_fire_on_a_store_that_erased_nothing(bundle, tmp_path):
    """A clean store must stay clean: 0 erasures and 0 tombstones agree, so nothing is flagged.

    Without this the new check could be satisfied by always complaining, and every bundle from a store
    that has simply never erased anything would carry a spurious problem.
    """
    m = Inspeximus(str(tmp_path / "clean.json"), receipts=True)
    m.remember("nothing was ever erased here", tags=["ops"])
    m.flush()
    b = build_bundle(m)
    assert b["tombstone_chain"] == []
    r = verify_bundle(b)
    assert not any("erasures_total" in p for p in r.get("problems") or []), r.get("problems")
