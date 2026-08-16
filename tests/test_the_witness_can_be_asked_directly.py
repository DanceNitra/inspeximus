"""The evidence must not live with the party being audited.

2.10.3 recorded witness refusals in the bundle. Measured 2026-08-16, that helps an HONEST operator
prove diligence and does not bind a dishonest one: delete `witness_refusals`, reseal the (advisory,
self-computed) bundle hash, and an auditor who does not already hold the witness allowlist sees an
ordinary SELF-CERTIFIED bundle with three refusals invisible. No further check INSIDE the artifact
can fix that, because the artifact is built by the operator.

The witness knew the whole time and had no way to say so: `last_head()` is an unsigned local dict
read, and a refusal was an exception the operator caught. `attest()` is the surface a third party
asks, signed, answering three things the operator cannot answer honestly about themselves:

  * WHAT head did I last co-sign, and at what height  -> a stale or forked bundle is visible
  * WHEN did I last see this store                    -> SILENCE stops being invisible, so an
                                                         operator who rewrote and then stopped
                                                         submitting is no longer indistinguishable
                                                         from an idle one
  * WHAT DID I REFUSE                                 -> durable, and not deletable by the operator

HONEST SCOPE, and it is a deployment fact this cannot assert: if the operator never submitted the
store to any witness, there is nothing to ask. `seen: False` makes that an answer rather than an
absence, but the auditor still has to know WHICH witnesses should have seen it.
"""
from __future__ import annotations

import copy
import os
import tempfile

import pytest

from inspeximus import Inspeximus
from inspeximus.audit_bundle import _bundle_hash, build_bundle, verify_bundle
from inspeximus.core import new_ed25519_keypair
from inspeximus.witness_pool import Witness, verify_attestation

SK, _PUB = new_ed25519_keypair()
SID = "prod"


@pytest.fixture
def scene():
    """An honest store witnessed at T, then rebuilt by an operator who holds the receipt key."""
    d = tempfile.mkdtemp()
    ws = [Witness(state_path=os.path.join(d, f"w{i}.json")) for i in range(3)]

    def store(sub, policy):
        os.makedirs(os.path.join(d, sub), exist_ok=True)
        ix = Inspeximus(path=os.path.join(d, sub, "s.json"), receipts=True, receipt_key=SK)
        ix.remember(f"deployment needs {policy}", key="pol", object=policy.split()[0])
        ix.remember("the deploy key rotates every 90 days", key="rot", object="90d")
        ix.flush()
        return ix

    honest = store("a", "two approvers")
    build_bundle(honest, witnesses=ws, store_id=SID)
    forged = store("b", "ONE approver")
    return d, ws, honest, forged


def _stripped(forged, ws):
    b = build_bundle(forged, witnesses=ws, store_id=SID)
    assert b["anchor"].get("witness_refusals"), "the fixture did not produce a refusal to hide"
    t = copy.deepcopy(b)
    t["anchor"].pop("witness_refusals", None)
    t.pop("bundle_hash", None)
    t["bundle_hash"] = _bundle_hash(t)
    return t


# ─────────────────────────────────────────────── the hole, and that it is still there
def test_the_operator_can_still_hide_a_refusal_inside_their_own_artifact(scene):
    """Pinned deliberately. This is NOT fixed and cannot be: the bundle is built by the audited
    party. If it ever starts failing, someone has added a check inside the artifact and believes it
    binds a dishonest operator -- it does not, and this test is the reminder."""
    _d, ws, _honest, forged = scene
    assert verify_bundle(_stripped(forged, ws))["ok"] is True


# ─────────────────────────────────────────────── asking the witness instead
def test_asking_the_witness_surfaces_the_refusal_the_operator_deleted(scene):
    _d, ws, _honest, forged = scene
    t = _stripped(forged, ws)
    out = verify_bundle(t, attestations=[w.attest(SID) for w in ws])
    assert not out["ok"] and any("REFUSED" in x for x in out["problems"]), out["problems"]


def test_an_attestation_is_signed_and_pinnable(scene):
    _d, ws, _honest, _forged = scene
    a = ws[0].attest(SID)
    assert verify_attestation(a, witness_pubkey=ws[0].public)["signed"] is True
    assert any("different witness" in x
               for x in verify_attestation(a, witness_pubkey=ws[1].public)["problems"])


def test_an_edited_attestation_does_not_verify(scene):
    """Whoever carries the statement must not be able to edit it -- including the operator, who is
    the likeliest courier."""
    _d, ws, _honest, forged = scene
    build_bundle(forged, witnesses=ws, store_id=SID)          # make the witness refuse, so there is
    a = ws[0].attest(SID)                                     # something to delete
    assert a["refusals"], "nothing to strip: this test would pass on an unchanged statement"
    a["refusals"] = []
    out = verify_attestation(a, witness_pubkey=ws[0].public)
    assert not out["ok"] and any("does not match its own fields" in x for x in out["problems"])


def test_the_refusal_survives_a_witness_restart(scene):
    """A refusal held only in memory is evidence until the process restarts, which an operator can
    arrange."""
    _d, ws, _honest, forged = scene
    build_bundle(forged, witnesses=ws, store_id=SID)
    reborn = Witness(ws[0]._secret, state_path=ws[0]._state_path)
    assert reborn.refusals(SID), "the refusal did not survive"


def test_silence_is_visible(scene):
    """An operator who rewrote history and then simply stopped submitting heads is otherwise
    indistinguishable from an idle store -- an attack whose entire cost is doing nothing."""
    _d, ws, _honest, _forged = scene
    assert (ws[0].attest(SID)["last_head"] or {}).get("seen_ts")


# ─────────────────────────────────────────────── the controls
def test_control_a_clean_pool_on_an_honest_bundle_reports_nothing():
    """A pool that has never seen a fork must pass. The fixture above deliberately reuses witnesses
    that already refused for this store -- and they keep reporting it, which is correct: once a
    witness has seen a fork, that is a permanent fact about the store, not about the bundle in your
    hand. My first attempt at this control reused them and read the correct alarm as a failure."""
    d = tempfile.mkdtemp()
    ws = [Witness(state_path=os.path.join(d, f"c{i}.json")) for i in range(3)]
    ix = Inspeximus(path=os.path.join(d, "s.json"), receipts=True, receipt_key=SK)
    ix.remember("deployment needs two approvers", key="pol", object="two")
    ix.flush()
    b = build_bundle(ix, witnesses=ws, store_id=SID)
    out = verify_bundle(b, witnesses=[w.public for w in ws], threshold=2,
                        attestations=[w.attest(SID) for w in ws])
    assert out["ok"], out["problems"]


def test_control_ordinary_growth_past_the_witness_is_a_note_not_a_failure():
    """The operator may legitimately have written more since the witness last looked. That must not
    fail -- but the auditor is told which head the third party actually stands behind, because a
    witnessed-but-stale bundle is what a rewrite looks like from here."""
    d = tempfile.mkdtemp()
    ws = [Witness(state_path=os.path.join(d, "c.json"))]
    ix = Inspeximus(path=os.path.join(d, "s.json"), receipts=True, receipt_key=SK)
    ix.remember("first", key="a", object="1")
    ix.flush()
    build_bundle(ix, witnesses=ws, store_id=SID)
    ix.remember("a later honest write", key="b", object="2")
    ix.flush()
    out = verify_bundle(build_bundle(ix), attestations=[ws[0].attest(SID)])
    assert out["ok"], out["problems"]
    assert any("last saw head" in x for x in out["limits"])


def test_a_witness_that_never_saw_the_store_says_so():
    """`seen: False` rather than an empty pass. Asking the wrong witness must not look like a clean
    bill of health -- which is the shape of every vacuous check in this repo's history."""
    d = tempfile.mkdtemp()
    ix = Inspeximus(path=os.path.join(d, "s.json"), receipts=True)
    ix.remember("x", key="k", object="v")
    ix.flush()
    stranger = Witness(state_path=os.path.join(d, "stranger.json"))
    out = verify_bundle(build_bundle(ix), attestations=[stranger.attest(SID)])
    assert not out["ok"] and any("never seen this store" in x for x in out["problems"])


def test_an_older_state_file_still_starts_the_witness():
    """The state file gained a shape (`heads`/`refusals`). Refusing to start on a valid older file
    would take a witness OFFLINE on upgrade -- and a witness that will not start co-signs nothing,
    which is worse than the gap it was protecting."""
    import json
    d = tempfile.mkdtemp()
    sp = os.path.join(d, "w.json")
    json.dump({SID: {"n_writes": 1, "writes_tip": "aa" * 32}}, open(sp, "w", encoding="utf-8"))
    w = Witness(state_path=sp)
    assert (w.last_head(SID) or {}).get("n_writes") == 1
    assert w.refusals() == []
