"""Identity-confidence gate on supersession + candidate reconciliation (1.9.0). A fuzzy-identity keyed write
must NOT clobber the authoritative value; it forks a candidate the steward promotes or discards. Ports the
Fellegi-Sunter (1969) clerical-review zone / MDM match-merge stewardship into agent memory."""
import tempfile
import os

import pytest

from mnemo import Mnemo


@pytest.fixture()
def store(tmp_path):
    m = Mnemo(path=str(tmp_path / "m.json"))
    m.remember("billing region is Frankfurt", key="billing::region", object="Frankfurt")
    return m


def _active(m, key):
    return [r["object"] for r in m.items if r.get("key") == key and r.get("status") == "active"]


def test_no_confidence_is_legacy_supersede(store):
    store.remember("region is Ohio", key="billing::region", object="Ohio")
    assert _active(store, "billing::region") == ["Ohio"]
    assert store.candidates() == []


def test_high_confidence_supersedes(store):
    store.remember("region is Ohio", key="billing::region", object="Ohio", identity_confidence=0.95)
    assert _active(store, "billing::region") == ["Ohio"]
    assert store.candidates() == []


def test_low_confidence_forks_candidate_not_clobber(store):
    store.remember("region is Ohio", key="billing::region", object="Ohio", identity_confidence=0.95)
    rid = store.remember("region is Berlin?", key="billing::region", object="Berlin", identity_confidence=0.3)
    # authority untouched
    assert _active(store, "billing::region") == ["Ohio"]
    cands = store.candidates("billing::region")
    assert len(cands) == 1
    assert cands[0]["object"] == "Berlin" and cands[0]["id"] == rid
    assert cands[0]["current"]["object"] == "Ohio"       # shows what it WOULD replace
    # a candidate is not returned as an active/keyed record
    rec = next(r for r in store.items if r["id"] == rid)
    assert rec["status"] == "candidate" and "key" not in rec and rec["candidate_key"] == "billing::region"


def test_promote_candidate_becomes_authoritative(store):
    store.remember("region is Ohio", key="billing::region", object="Ohio", identity_confidence=0.95)
    rid = store.remember("region is Berlin", key="billing::region", object="Berlin", identity_confidence=0.3)
    out = store.promote_candidate(rid)
    assert out["promoted"] == rid and out["key"] == "billing::region"
    assert _active(store, "billing::region") == ["Berlin"]
    assert store.candidates() == []


def test_discard_candidate_leaves_authority(store):
    store.remember("region is Ohio", key="billing::region", object="Ohio", identity_confidence=0.95)
    rid = store.remember("region is Tokyo", key="billing::region", object="Tokyo", identity_confidence=0.2)
    store.discard_candidate(rid, basis="wrong entity")
    assert _active(store, "billing::region") == ["Ohio"]
    assert store.candidates() == []
    rec = next(r for r in store.items if r["id"] == rid)
    assert rec["status"] == "superseded" and rec["meta"]["superseded_by_policy"] == "candidate_discarded"


def test_fork_below_threshold_configurable(store):
    store.fork_below = 0.5
    store.remember("region is Ohio", key="billing::region", object="Ohio")
    # 0.6 is now ABOVE the (lowered) threshold -> supersedes
    store.remember("region is Reno", key="billing::region", object="Reno", identity_confidence=0.6)
    assert _active(store, "billing::region") == ["Reno"]
    assert store.candidates() == []


def test_candidate_excluded_from_recall_current_value(store):
    store.remember("region is Ohio", key="billing::region", object="Ohio", identity_confidence=0.95)
    store.remember("region is Berlin", key="billing::region", object="Berlin", identity_confidence=0.3)
    hits = store.recall("what is the billing region", k=6, mode="lexical")
    blob = " ".join(h.get("text", "") for h in hits)
    # the authoritative answer path must not surface the un-reconciled candidate as current
    active = [r for r in store.items if r.get("key") == "billing::region" and r.get("status") == "active"]
    assert len(active) == 1 and active[0]["object"] == "Ohio"


def test_promote_requires_capability_under_authority(tmp_path):
    from mnemo import new_source_keypair
    sk, pk = new_source_keypair()
    m = Mnemo(path=str(tmp_path / "a.json"), revert_pubkey=pk)
    m.remember("region is Ohio", key="r::x", object="Ohio")
    rid = m.remember("region is Berlin", key="r::x", object="Berlin", identity_confidence=0.3)
    with pytest.raises(PermissionError):
        m.promote_candidate(rid)                          # no capability -> refused when authority is set


def test_tenant_isolation_on_candidates(tmp_path):
    m = Mnemo(path=str(tmp_path / "t.json"))
    a = m.for_tenant("acme")
    b = m.for_tenant("beta")
    a.remember("x is 1", key="k::x", object="1", identity_confidence=0.95)
    a.remember("x is 2?", key="k::x", object="2", identity_confidence=0.3)
    assert len(a.candidates()) == 1
    assert b.candidates() == []                           # beta sees none of acme's candidates
