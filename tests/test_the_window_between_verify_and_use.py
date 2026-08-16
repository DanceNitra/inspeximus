"""The third invalidation window: the store is untouched and the world has moved.

Provenance has a temporal chain, and a mutation in each gap is a different failure needing a
different remedy. The framing is @safal207's on anthropics/claude-code#34556, generalised from two
bugs found independently — his in OmniMemory (hash at session-end), ours in inspeximus (hash at
write time):

    OBSERVE -> CAPTURE   the fingerprint is of bytes nobody read   UNBOUND_CAPTURE   (2.10.6)
    CAPTURE -> VERIFY    the source changed since capture          DRIFTED           (long before)
    VERIFY  -> USE       the source changed after it checked out   STALE_AT_USE      (here)

MEASURED BEFORE BUILDING, in probes/the_third_window_verify_to_use.py: `check_sources` returned
FRESH, the file changed, `recall` served the old text, and `verify_witness` answered
digest_match=True — correctly, because the STORE did not change. The window was never invisible; a
second `check_sources` sees it. It was UNBOUND: nothing carried the verification forward to the
moment the memory was acted on, so the check that would have caught it is the one nobody re-ran.

The four rules below are not decoration. Every one of them is a shape this repository has shipped
at least once: coverage read as a guarantee, an empty check read as clean, silence read as pass,
and two independent questions collapsed into one score.
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile

import pytest

from inspeximus import Inspeximus


def _scene(text=b"deployment needs two approvers", bind=True):
    d = tempfile.mkdtemp()
    src = os.path.join(d, "policy.txt")
    open(src, "wb").write(text)
    ix = Inspeximus(path=os.path.join(d, "s.json"), receipts=True)
    kw = {"doc": src}
    if bind:
        kw["observed_sha256"] = hashlib.sha256(text).hexdigest()
    ix.remember("deployment needs two approvers", key="pol", object="two", source=kw)
    ix.flush()
    return d, src, ix


# ─────────────────────────────────────────────────────── the window, and that it closes
def test_a_source_that_moves_after_verification_is_caught_at_use():
    """THE POINT. Verify passes, the world changes, the memory is used — and now something says so."""
    _d, src, ix = _scene()
    w = ix.witness(ix.recall("approvers"), bind_sources=True)
    assert ix.verify_witness(w)["sources_match"] is True, "the fixture starts dirty"

    open(src, "wb").write(b"deployment needs ONE approver")
    out = ix.verify_witness(w)
    assert out["stale_at_use"] is True and out["sources_match"] is False
    assert out["sources_moved"] and not out["valid"]


def test_the_store_answer_and_the_world_answer_stay_separate():
    """A single score hides WHERE the guarantee was lost, and the remedies differ: a moved source
    wants revalidation, a changed digest wants re-derivation. This is the defect the whole line of
    work came from, so it gets its own test rather than a sentence in a docstring."""
    _d, src, ix = _scene()
    w = ix.witness(ix.recall("approvers"), bind_sources=True)
    open(src, "wb").write(b"deployment needs ONE approver")
    out = ix.verify_witness(w)
    assert out["digest_match"] is True, "the store did not change and must not be blamed"
    assert out["sources_match"] is False


def test_the_other_direction_too_a_changed_store_with_a_steady_source():
    """The mirror. If this ever collapsed into one flag, this is the case that would go silent."""
    _d, _src, ix = _scene()
    w = ix.witness(ix.recall("approvers"), bind_sources=True)
    ix.remember("an unrelated later write", key="z", object="9")
    ix.flush()
    out = ix.verify_witness(w)
    assert out["digest_match"] is False and out["sources_match"] is True


# ─────────────────────────────────────────────────────── the four honesty rules
def test_a_witness_that_bound_nothing_does_not_report_a_clean_world():
    """RULE 1, and the one this repo gets wrong most often. The caller ASKED for source binding; if
    nothing could be bound, the answer is "nothing was checked", never "everything is fine"."""
    d = tempfile.mkdtemp()
    ix = Inspeximus(path=os.path.join(d, "s.json"), receipts=True)
    # NAMES a document and carries no fingerprint -- the unmet request. A record with NO anchor is a
    # different empty (nothing was ever checkable) and is covered in
    # tests/test_not_bindable_is_not_a_backlog.py; that distinction arrived one release later, and
    # this test used to conflate them.
    ix.remember("names a doc, never fingerprinted", key="bare", object="x", source={"doc": "PROJ-1"})
    ix.flush()
    w = ix.witness(ix.recall("doc"), bind_sources=True)
    assert w["sources_bound"] == "0/1"
    out = ix.verify_witness(w)
    assert out["sources_match"] is False and out["valid"] is False
    assert any("bound NONE" in x for x in out["limits"]), out.get("limits")


def test_a_write_time_hash_is_bound_but_not_confused_with_an_observed_one():
    """THE DESIGN CHANGED HERE, and the test above is why.

    A record given `source={"doc": path}` and no `observed_sha256` still gets a fingerprint -- of
    the file as it stood when remember() ran. That is a perfectly good baseline for THIS window: "has
    the source moved since I checked" needs only a hash that was true at check time. It is not
    evidence the memory was ever right about the bytes it read.

    My first version of this feature reported both kinds as one `sources_bound` number, which is the
    exact conflation the whole OBSERVE/CAPTURE/VERIFY/USE line of work exists to undo -- and the
    test above caught it by asserting 0/1 and getting 1/1. Both are pinned now, and the KIND travels
    with the pin."""
    _d, src, ix = _scene(bind=False)
    w = ix.witness(ix.recall("approvers"), bind_sources=True)
    assert w["sources_bound"] == "1/1" and w["sources_observation_bound"] == "0/1"

    out = ix.verify_witness(w)
    assert out["sources_match"] is True, "a write-time hash still answers the VERIFY->USE question"
    assert any("WRITE-TIME hash" in x for x in out["limits"]), out.get("limits")

    open(src, "wb").write(b"deployment needs ONE approver")
    assert ix.verify_witness(w)["stale_at_use"] is True

    # ...and the observation-bound fixture says so, which is the control that keeps the field honest
    _d2, _s2, ix2 = _scene(bind=True)
    w2 = ix2.witness(ix2.recall("approvers"), bind_sources=True)
    assert w2["sources_observation_bound"] == "1/1"
    assert not any("WRITE-TIME hash" in x for x in ix2.verify_witness(w2).get("limits", []))


def test_an_unbindable_record_is_named_not_skipped():
    """RULE 2. Dropping it would make a half-covered answer read exactly like a fully covered one."""
    d, src, ix = _scene()
    ix.remember("names a doc, never fingerprinted", key="bare", object="x", source={"doc": "PROJ-1"})
    ix.flush()
    w = ix.witness(bind_sources=True)
    assert w["sources_bound"] == "1/2", "the half-covered answer must not read as fully covered"
    assert w.get("sources_unbound"), "the unbindable record vanished from the witness"
    assert any("backfillable kind" in x for x in ix.verify_witness(w)["limits"])


def test_a_source_that_cannot_be_re_read_is_neither_fresh_nor_moved():
    """RULE 3. Silence is not agreement. An orphaned source answers the question with nothing, and
    nothing must not read as pass -- the shape behind the erasure certificate that reported valid
    while its absence proof pointed at a typo."""
    _d, src, ix = _scene()
    w = ix.witness(ix.recall("approvers"), bind_sources=True)
    os.unlink(src)
    out = ix.verify_witness(w)
    assert out["sources_orphaned"] and not out["sources_moved"]
    assert out["sources_match"] is False and out["stale_at_use"] is False
    assert any("could not be re-read" in x for x in out["limits"])


def test_the_coverage_is_reported_as_a_fraction_not_a_boolean():
    """RULE 4. `declared_observation_binding_coverage` carries the word declared for this reason:
    coverage is not a guarantee, and the number has to let a reader tell them apart."""
    _d, _src, ix = _scene()
    w = ix.witness(ix.recall("approvers"), bind_sources=True)
    assert w["sources_bound"] == "1/1"


def test_an_id_the_store_does_not_hold_is_reported():
    """An erasure between recall and witness leaves the answer resting on something no witness can
    speak for. Silently dropping it would shrink the denominator and flatter the coverage."""
    _d, _src, ix = _scene()
    w = ix.witness(["not-a-real-id"], bind_sources=True)
    assert w.get("sources_unknown") == ["not-a-real-id"] and w["sources_bound"] == "0/1"
    assert any("not in this store" in x for x in ix.verify_witness(w)["limits"])


def _two_tenants():
    d = tempfile.mkdtemp()
    root = Inspeximus(path=os.path.join(d, "s.json"), receipts=True)
    paths = {}
    for t in ("acme", "globex"):
        p = os.path.join(d, f"{t}-secret-policy.txt")
        b = f"{t} needs two approvers".encode()
        open(p, "wb").write(b)
        paths[t] = p
        root.for_tenant(t).remember(f"{t} policy", key="pol", object="two",
                                    source={"doc": p,
                                            "observed_sha256": hashlib.sha256(b).hexdigest()})
    root.flush()
    return d, root, paths


def test_a_tenants_hydration_witness_attested_the_whole_store():
    """SHIPPED THAT WAY, and found while adding bind_sources rather than by the sweep.

    `state_digest` was rebound on `_TenantView` and `witness`, which wraps it, was NOT -- so the
    whole method ran PARENT-bound. Measured before the fix: on a store where acme owns one record,
    `acme.witness()` reported records=2, active=2 and the ROOT digest. The sweep did not catch it
    because the sweep covers PRIVATE helpers, and this is a public method: the half that was
    supposed to fail closed."""
    _d, root, _paths = _two_tenants()
    acme = root.for_tenant("acme")
    assert root.witness()["records"] == 2
    assert acme.witness()["records"] == 1, "the tenant's receipt is attesting other tenants' rows"


def test_a_tenants_witness_binds_only_that_tenants_sources():
    """The artifact is meant to be handed onward, so a leak here travels. `_bind_sources` was
    rebound the day it was written -- by the structural sweep failing the build, not by me
    remembering -- and it still leaked until `witness` itself was rebound above it."""
    _d, root, paths = _two_tenants()
    w = root.for_tenant("acme").witness(bind_sources=True)
    assert [v["doc"] for v in w["sources"].values()] == [paths["acme"]]
    assert paths["globex"] not in json.dumps(w), "another tenant's path rode along in the artifact"


def test_another_tenants_write_does_not_invalidate_this_tenants_witness():
    """The receipt chain is per-STORE and the witness is per-tenant, so a neighbour's write moves
    this tenant's tip while their own records are untouched. Failing there would mark every
    tenant's receipt invalid on a busy store, and an alarm that fires on other people's activity is
    one that gets switched off before it ever catches anything. Reported, not fatal."""
    _d, root, _paths = _two_tenants()
    acme = root.for_tenant("acme")
    w = acme.witness(bind_sources=True)
    root.for_tenant("globex").remember("an unrelated globex note", key="n", object="1")
    root.flush()

    out = acme.verify_witness(w)
    assert out["valid"] is True and out["digest_match"] is True
    assert out["receipts_tip_match"] is False, "the shared tip really did move; do not hide it"
    assert any("ANOTHER tenant" in x for x in out["limits"]), out.get("limits")


def test_control_the_tenants_own_write_still_invalidates_it():
    """The must-still-fail control for the concession above."""
    _d, root, _paths = _two_tenants()
    acme = root.for_tenant("acme")
    w = acme.witness(bind_sources=True)
    acme.remember("acme changes its mind", key="pol", object="one")
    root.flush()
    assert acme.verify_witness(w)["valid"] is False


def test_control_an_unbound_store_keeps_the_strict_chain_meaning():
    """On an admin store a moved tip IS the history changing, which is what the anchor exists to
    catch. The concession is scoped to tenant views and must not leak out of them."""
    _d, root, _paths = _two_tenants()
    w = root.witness()
    root.remember("an admin write", key="a", object="1")
    root.flush()
    assert root.verify_witness(w)["valid"] is False


# ─────────────────────────────────────────────────────── the controls
def test_control_an_untouched_source_verifies_clean():
    """The must-not-cry-wolf control. If a steady source ever reported stale, the field would be
    noise within a week and every test above would be measuring a constant."""
    _d, _src, ix = _scene()
    w = ix.witness(ix.recall("approvers"), bind_sources=True)
    out = ix.verify_witness(w)
    assert out["valid"] and out["sources_match"] and not out["stale_at_use"]
    assert "limits" not in out, out.get("limits")


def test_control_a_witness_without_bind_sources_is_unchanged():
    """The must-not-break control. The old witness is a shipped contract; adding a window to it must
    not change what it means for every caller who did not ask."""
    _d, src, ix = _scene()
    w = ix.witness()
    assert "sources" not in w and "sources_bound" not in w
    open(src, "wb").write(b"deployment needs ONE approver")
    out = ix.verify_witness(w)
    assert out["valid"] is True and "sources_match" not in out, \
        "a caller who never asked about sources had their verdict changed under them"


def test_a_resolver_covers_a_source_that_is_not_a_file():
    """Parity with check_sources: the sources worth binding are often URLs, and a file-only
    implementation would quietly report every one of them as orphaned."""
    d = tempfile.mkdtemp()
    body = b"deployment needs two approvers"
    ix = Inspeximus(path=os.path.join(d, "s.json"), receipts=True)
    ix.remember("policy", key="pol", object="two",
                source={"doc": "https://example.invalid/policy",
                        "observed_sha256": hashlib.sha256(body).hexdigest()})
    ix.flush()
    w = ix.witness(ix.recall("policy"), bind_sources=True)
    assert w["sources_bound"] == "1/1"
    assert ix.verify_witness(w, resolver=lambda _doc: body)["sources_match"] is True
    moved = ix.verify_witness(w, resolver=lambda _doc: b"deployment needs ONE approver")
    assert moved["stale_at_use"] is True
