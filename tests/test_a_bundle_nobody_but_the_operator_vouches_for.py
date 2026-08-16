"""An audit bundle where every check is the operator verifying the operator.

Everything else in a bundle is self-certification. A receipt chain signed with `receipt_key` catches
an editor who lacks the key; it cannot catch the party who HOLDS it, and that party is whoever runs
the store. Measured here: an operator with the key rebuilt the store around a forged policy,
re-signed the whole chain, and

    verify_writes            -> True
    a fresh bundle over it   -> ok True, a page of OK lines, VERDICT PASS

An independent witness attesting the log's HEIGHT AND TIP at a point in time is the one thing in the
artifact that an operator cannot forge alone. All three witnesses refused the rewritten head.

THE PARTS WERE ALL HERE AND NOTHING JOINED THEM. `collect_cosignatures()` returned a list,
`verify_cosigned_anchor()` consumed one, and check (5) of verify_bundle read
`anchor["cosignatures"]` -- but nothing in the library ever WROTE that key. Measured 2026-08-16:
absent from every anchor this library produced. So the only operator-adversarial check was reachable
only by a caller who built the anchor, called the collector, hand-stuffed the result back in, and
then built the bundle around it. `build_bundle(witnesses=[...])` is that flow, once.

And an unwitnessed bundle now SAYS it is self-certified. It used to pass in silence, which is the
same defect as the signature coverage one file over: absence of a finding is not a finding of
absence.
"""
from __future__ import annotations

import os
import tempfile

import pytest

from inspeximus import Inspeximus
from inspeximus.audit_bundle import build_bundle, verify_bundle
from inspeximus.core import new_ed25519_keypair
from inspeximus.witness_pool import Witness

from conftest import fork_of
SK, _PUB = new_ed25519_keypair()


@pytest.fixture
def pool():
    d = tempfile.mkdtemp()
    ws = [Witness(state_path=os.path.join(d, f"w{i}.json")) for i in range(3)]
    return d, ws, [w.public for w in ws]


def _store(d, name="s.json", policy="two approvers", **kw):
    os.makedirs(os.path.join(d, os.path.dirname(name) or "."), exist_ok=True)
    ix = Inspeximus(path=os.path.join(d, name), receipts=True, receipt_key=SK, **kw)
    ix.remember(f"deployment needs {policy}", key="pol", object=policy.split()[0])
    ix.remember("the deploy key rotates every 90 days", key="rot", object="90d")
    ix.flush()
    return ix


# ─────────────────────────────────────────────── the flow exists at all
def test_build_bundle_attaches_what_the_witnesses_return(pool):
    """The gap that made this an expert option: nothing ever wrote `anchor["cosignatures"]`."""
    d, ws, pks = pool
    b = build_bundle(_store(d), witnesses=ws, store_id="store-1")
    assert len(b["anchor"].get("cosignatures") or []) == 3
    out = verify_bundle(b, witnesses=pks, threshold=2)
    assert out["ok"] and any("operator-adversarial" in c for c in out["checks"]), out


def test_an_unwitnessed_bundle_says_it_is_self_certified(pool):
    d, _ws, _pks = pool
    out = verify_bundle(build_bundle(_store(d)))
    assert out["ok"], "an unwitnessed bundle is legitimate and must still pass by default"
    assert any("SELF-CERTIFIED" in x for x in out["limits"]), out["limits"]


def test_require_witnessed_refuses_a_self_certified_bundle(pool):
    d, _ws, _pks = pool
    assert not verify_bundle(build_bundle(_store(d)), require_witnessed=True)["ok"]


# ─────────────────────────────────────────────── the payoff
def test_an_operator_holding_the_key_cannot_get_a_rewritten_history_witnessed(pool):
    """THE test this whole feature exists for.

    The operator rebuilds the store around a forged policy and re-signs the chain with their own key.
    Every self-certified surface accepts it. The witnesses do not: same height, different tip.
    """
    d, ws, pks = pool
    honest = _store(d, "s.json", "two approvers")
    build_bundle(honest, witnesses=ws, store_id="store-1")          # witnessed at time T

    # A REAL FORK: same genesis receipt, divergent after it. This used to build a second store
    # from scratch, which after the derived-id fix is a DIFFERENT store rather than a rewrite of
    # this one -- so the witnesses had nothing to refuse and the test passed on an attack it was no
    # longer performing.
    # SAME HEIGHT, different tip. One record too many and the fork is TALLER than the victim, which
    # the witness reads as ordinary growth and co-signs -- correctly, since a taller chain is what
    # honest writing looks like. Rewrite-then-grow is caught by the bundle's chain-prefix check and
    # by the attestation, not here; this test is about the equal-height fork.
    #
    # The genesis record survives, because it must: identity IS the genesis receipt hash, so a
    # rewrite that reaches back that far produces a different store rather than a forked one.
    forged = fork_of(honest, os.path.join(d, "rw"),
                     [("deployment needs ONE approver", "pol", "one")], receipt_key=SK)
    assert forged.verify_writes()[0] is True, "the rewrite must verify internally, or this proves nothing"
    assert verify_bundle(build_bundle(forged))["ok"] is True, "same"

    b = build_bundle(forged, witnesses=ws, store_id="store-1")
    assert (b["anchor"].get("cosignatures") or []) == []
    assert len(b["anchor"].get("witness_refusals") or []) == 3
    out = verify_bundle(b, witnesses=pks, threshold=2)
    assert not out["ok"] and any("REFUSED" in x for x in out["problems"]), out["problems"]


def test_control_an_honest_later_export_is_still_co_signed(pool):
    """The must-not-brick control. A witness pool that refuses everything after the first export
    would make the feature unusable, and the refusal above would mean nothing."""
    d, ws, pks = pool
    ix = _store(d)
    build_bundle(ix, witnesses=ws, store_id="store-1")
    ix.remember("a third honest record", key="c", object="3")
    ix.flush()
    b = build_bundle(ix, witnesses=ws, store_id="store-1")
    assert len(b["anchor"].get("cosignatures") or []) == 3
    assert verify_bundle(b, witnesses=pks, threshold=2)["ok"]


def test_a_refusal_is_surfaced_even_when_the_auditor_passes_no_witnesses(pool):
    """An honest witness refuses only a fork or a rollback, so a refusal recorded at export is the
    loudest signal in the artifact -- louder than any check inside it, because it came from outside.
    It must not depend on the auditor remembering to pass an allowlist."""
    d, ws, _pks = pool
    honest = _store(d, "s.json", "two approvers")
    build_bundle(honest, witnesses=ws, store_id="store-1")
    b = build_bundle(fork_of(honest, os.path.join(d, "rw"),
                             [("deployment needs ONE approver", "pol", "one")], receipt_key=SK),
                     witnesses=ws, store_id="store-1")
    out = verify_bundle(b)                                   # no witnesses= at all
    assert not out["ok"] and any("REFUSED" in x for x in out["problems"]), out["problems"]


def test_a_partial_pool_still_meets_a_lower_threshold(pool):
    """k-of-n is the point: one unreachable witness must not block an export. A pool where every
    witness has to answer is a single point of failure wearing three hats."""
    d, ws, pks = pool

    def dead(_sid, _anchor):
        raise RuntimeError("witness unreachable")

    b = build_bundle(_store(d), witnesses=[ws[0], dead, ws[2]], store_id="store-1")
    assert len(b["anchor"].get("cosignatures") or []) == 2
    assert len(b["anchor"].get("witness_refusals") or []) == 1
    # The refusal is still reported -- an unreachable witness and a refusing one look alike from
    # here, and treating either as routine is how the alarm gets trained away.
    assert not verify_bundle(b, witnesses=pks, threshold=2)["ok"]
