"""Round eight: what a stateful property run found, and four defects inside my own recent fixes.

Eight rounds had tested FUNCTIONS. This one tested SEQUENCES — 2,700 random operations over a small pool of
keys, subjects and tenants, with eight invariants re-checked after every single one. That found things no unit
test had: `reload()` retiring another tenant's value, and `slash()` raising a tamper alarm on itself in 27 of
45 sequences.

Every defect below is in code I wrote in the last few releases. That is now the expected shape, not a
surprise — which is why the audit round is part of the fix, not optional after it.
"""
import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import inspeximus.core as core
from inspeximus import Inspeximus


def _path():
    return os.path.join(tempfile.mkdtemp(), "m.json")


# ── reload(): the recovery path was breaking two invariants ─────────────────────────────────────────
def test_reload_does_not_retire_another_tenants_value():
    """`reload()`'s last-write-wins keyed on `key` ALONE, so tenant A's current value was retired because
    tenant B happened to use the same key name — cross-tenant data loss in the recovery path, with
    `verify_writes()` reporting True throughout. Found by a 3-operation random sequence."""
    p = _path()
    s = Inspeximus(path=p)
    acme, globex = s.for_tenant("acme"), s.for_tenant("globex")
    acme.remember("acme auth is oauth", key="auth", object="oauth")
    globex.remember("globex auth is saml", key="auth", object="saml")

    s.reload()

    assert [h["text"] for h in acme.recall("auth")] == ["acme auth is oauth"]
    assert [h["text"] for h in globex.recall("auth")] == ["globex auth is saml"]


def test_reload_keeps_deliberate_restatements():
    """`_supersede_by_key` deliberately keeps two same-VALUE rows ("a restatement is not a supersession").
    reload()'s blind LWW retired one, so reload() was not state-preserving where flush()+reopen is."""
    m = Inspeximus(path=_path())
    m.remember("the value is alpha", key="k", object="alpha")
    m.remember("value: alpha", key="k", object="alpha")
    before_active = len([r for r in m.items if r["status"] == "active"])
    digest_before = m.state_digest()

    m.reload()

    assert len([r for r in m.items if r["status"] == "active"]) == before_active
    assert m.state_digest() == digest_before, "reload() must be state-preserving on an up-to-date store"


def test_reload_still_resolves_a_genuine_conflict():
    """The guard must not become a no-op: two DIFFERENT values on one key must still collapse to one."""
    m = Inspeximus(path=_path())
    m.remember("v1", key="k", object="1")
    m.remember("v2", key="k", object="2")
    m._items[0]["status"] = "active"                 # simulate the merge leaving both active
    m.reload()
    active = [r for r in m.items if r.get("key") == "k" and r["status"] == "active"]
    assert len(active) == 1 and active[0].get("object") == "2"


# ── slash()/restore(): the tamper alarm fired on our own lever ──────────────────────────────────────
def _graduated():
    m = Inspeximus(path=_path(), receipts=True)
    rid = m.remember("alpha is the value for k0", key="k0", object="alpha")
    for _ in range(20):
        m.credit([rid], outcome=True)
    assert next(r for r in m.items if r["id"] == rid)["mtype"] == "semantic", "fixture must graduate"
    return m, rid


def test_slash_does_not_report_itself_as_tampering():
    """`slash()` revokes graduation by rewriting `mtype`, which the write receipt commits to — so a
    legitimate in-band operation made `verify_writes()` report "edited after write". It fired in 27 of 45
    random sequences, first at operation 3. For a tamper-evidence product a false positive from its own
    accountability lever poisons the signal. slash now AMENDS the chain instead."""
    m, rid = _graduated()
    assert m.verify_writes()[0] is True
    m.slash([rid], scope="memory")
    ok, problems = m.verify_writes()
    assert ok is True, problems


def test_restore_is_symmetric_with_slash():
    """restore() puts `mtype` back, which is the same committed-field rewrite in the other direction."""
    m, rid = _graduated()
    m.slash([rid], scope="memory")
    m.restore([rid], scope="memory")
    assert m.verify_writes()[0] is True
    m.slash([rid], scope="memory")
    assert m.verify_writes()[0] is True, "a second slash after a restore must also stay clean"


def test_a_genuine_out_of_band_edit_is_still_caught():
    """Amending on legitimate change must not blunt the check it was added beside."""
    m, rid = _graduated()
    m.slash([rid], scope="memory")
    next(r for r in m.items if r["id"] == rid)["text"] = "EDITED OUT OF BAND"
    assert m.verify_writes()[0] is False


def test_the_amendment_guard_does_not_break_the_chain_walk():
    """My first version of this fix used `continue` to skip a superseded receipt — which also skipped the
    `prev = r["hash"]` at the end of the loop, so the chain stopped advancing and EVERY later receipt
    reported "broken chain link". A guard that jumps over the loop's own bookkeeping breaks what it sits
    beside."""
    m, rid = _graduated()
    m.slash([rid], scope="memory")
    m.restore([rid], scope="memory")
    assert len(m._receipts) >= 3
    ok, problems = m.verify_writes()
    assert ok is True and not any("broken chain link" in p for p in problems), problems


# ── exact=True must not re-open the over-erasure it was written to avoid ─────────────────────────────
def test_exact_erasure_spares_a_record_derived_from_the_colliding_subject():
    """My 1.66.0 escape simply cleared `collisions`, which left in every record carrying the shared
    canonical taint — including a summary derived from the OTHER person's record, which was then
    hard-deleted. That is the third-party over-erasure the guard exists to prevent, reintroduced by its own
    escape hatch. `exact` now takes the forward lineage closure of the exact-source records."""
    m = Inspeximus(path=_path(), receipts=True)
    victim = m.remember("alice salary 100", source={"doc": "user-42"})
    other = m.remember("junk", source={"doc": "User_42"})
    victim_summary = m.remember("summary of alice", source={"doc": "summary-svc"}, derived_from=[victim])
    other_summary = m.remember("summary of OTHER", source={"doc": "summary-svc"}, derived_from=[other])

    res = m.forget_subject("user-42", request_id="R1", basis="gdpr-art17", exact=True)
    alive = {r["id"] for r in m.items}

    assert res["erased"] == 2
    assert victim not in alive and victim_summary not in alive
    assert other in alive, "the colliding subject must survive"
    assert other_summary in alive, "and so must what descends from it"


# ── the unscoped certificate ────────────────────────────────────────────────────────────────────────
def _mixed_requests():
    m = Inspeximus(path=_path(), receipts=True)
    m.remember("alice", source={"doc": "alice"})
    other = m.remember("other", source={"doc": "other"})
    m.forget_subject("alice", request_id="R1", basis="gdpr-art17")
    m.forget(ids=[other])                            # housekeeping erasure: no request_id
    return m


def test_an_unscoped_certificate_verifies_when_some_erasure_had_no_request_id():
    """`request_ids` drops None, so the verifier could not tell "unscoped" from "scoped to exactly these" —
    and an honest UNSCOPED certificate failed its own chain in any store where one erasure ran without a
    request id. The producer now emits an explicit `scoped_to` marker."""
    m = _mixed_requests()
    res = core.verify_erasure_certificate(m.erasure_certificate(), store_items=m.items)
    assert res["valid"] is True, res["problems"]
    assert res["count"] == 2


def test_a_scoped_certificate_still_covers_only_its_request():
    m = _mixed_requests()
    res = core.verify_erasure_certificate(m.erasure_certificate(request_id="R1"), store_items=m.items)
    assert res["valid"] is True and res["count"] == 1


def test_a_pre_marker_certificate_still_verifies():
    """Backward tolerance: a certificate minted before `scoped_to` existed must not start failing."""
    import copy
    m = _mixed_requests()
    old = copy.deepcopy(m.erasure_certificate())
    old.pop("scoped_to")
    assert core.verify_erasure_certificate(old, store_items=m.items)["valid"] is True


@pytest.mark.parametrize("field,value", [("count", 99), ("erased_memory_ids", ["never"])])
def test_forgery_is_still_rejected_in_both_scopes(field, value):
    import copy
    m = _mixed_requests()
    for cert in (m.erasure_certificate(), m.erasure_certificate(request_id="R1")):
        c = copy.deepcopy(cert)
        c[field] = value
        assert core.verify_erasure_certificate(c, store_items=m.items)["valid"] is False
