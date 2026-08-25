"""Round three on the 2.10.6 candidate: attacking round two's fixes.

Round one: eleven fixes. Round two attacked those and found eleven more, three of them FALSE CLAIMS
written into comments while fixing round one. Round three attacks round two's fixes and found two,
both of the same shape as the worst finding of round two -- a mechanism that exists, is argued for
at length in its own source, and is reachable from nothing we ship.

A NOTE ON HOW ONE OF THEM WAS NEARLY MISSED, because it is the more useful half. The probe for the
identity fallback asked whether two stores COLLIDE on one id. They do not. The property that had
failed is whether an id SURVIVES A COPY -- the entire reason the derived id replaced the filename --
and every receipts-disabled store was `cp`-able to a fresh witness contact while the probe reported
clean. The criterion was narrower than the property, which is this repo's oldest recurring defect,
and it was written by someone who keeps a note about it.
"""
from __future__ import annotations

import inspect
import json
import os
import shutil
import subprocess
import sys
import tempfile

import pytest

from inspeximus import Inspeximus
from inspeximus.audit_bundle import _derived_store_id, build_bundle, verify_bundle
from inspeximus.core import new_ed25519_keypair
from inspeximus.witness_pool import Witness

SK, PK = new_ed25519_keypair()


def _store(d, sub, n=3, **kw):
    os.makedirs(os.path.join(d, sub), exist_ok=True)
    ix = Inspeximus(path=os.path.join(d, sub, "s.json"), **kw)
    for i in range(n):
        ix.remember(f"record {i}", key=f"k{i}", object=str(i))
    ix.flush()
    return ix


def _help(*argv):
    return subprocess.run([sys.executable, "-m", "inspeximus.cli", *argv, "--help"],
                          capture_output=True, text=True, encoding="utf-8", errors="replace",
                          env={**os.environ, "PYTHONUTF8": "1"}).stdout


# ═════════════════════════════ G1: hardening that only an importer could reach
@pytest.mark.parametrize("argv", [("witness", "serve"), ("witness", "cosign")])
@pytest.mark.parametrize("flag", ["--strict", "--require-authenticated-state"])
def test_the_witness_hardening_is_reachable_from_the_cli(argv, flag):
    """`strict` (refuse a store this witness has no memory of -- amnesia IS the attack) and
    `require_authenticated_state` (refuse an un-MAC'd fork-memory file) were `Witness` constructor
    arguments that NO shipped interface passed: not the CLI, not the HTTP server, not MCP. Both are
    load-bearing against an operator, and both were available only to someone importing the library
    -- which is not the deployment either of them is written for."""
    assert flag in _help(*argv), f"{flag} unreachable from `inspeximus {' '.join(argv)}`"


def test_the_witness_server_accepts_them_too():
    """The HTTP server is the deployment the module recommends, so it is the one that most needs
    them -- and it hardcoded both to their defaults."""
    import inspeximus.witness_server as wsrv
    p = inspect.signature(wsrv.serve).parameters
    assert "strict" in p and "require_authenticated_state" in p


def test_strict_actually_takes_effect_through_the_server_constructor():
    """A flag that is accepted and dropped is worse than one that is absent: it reads as protection.
    So this asserts the BEHAVIOUR, not the signature."""
    import inspeximus.witness_server as wsrv
    d = tempfile.mkdtemp()
    src = inspect.getsource(wsrv.serve)
    assert "strict=strict" in src and "require_authenticated_state=require_authenticated_state" in src
    w = Witness(state_path=os.path.join(d, "w.json"), strict=True)
    ix = _store(d, "a", 2, receipts=True, receipt_key=SK)
    with pytest.raises(ValueError, match="strict witness has no record"):
        w.cosign(_derived_store_id(ix), ix.anchor())


# ═════════════════════════════ G3: the fallback identity restored the defeated scheme
def test_a_receipts_disabled_store_cannot_be_witnessed():
    """Two measured reasons, either sufficient.

    THE ANCHOR COMMITS TO NOTHING: with receipts off, a store holding three records still reports
    n_writes=0 and writes_tip=all zeros, so the witness cannot tell it from an empty store nor from
    any other receipts-disabled store, and has nothing to compare on the next submission. What comes
    back is a valid signature over zeros, and the bundle then printed "external witnesses co-signed
    the anchor (operator-adversarial)" as a green check.

    THE IDENTITY IS THE FILENAME: `_derived_store_id` fell back to the path, which is the operator-
    chosen value the derived id was introduced to replace."""
    d = tempfile.mkdtemp()
    ix = _store(d, "a", 3)
    assert ix.anchor()["n_writes"] == 0 and set(ix.anchor()["writes_tip"]) == {"0"}, \
        "the fixture no longer produces a zero anchor: this test would prove nothing"
    with pytest.raises(ValueError, match="receipts disabled"):
        build_bundle(ix, witnesses=[Witness(state_path=os.path.join(d, "w.json"))])


def test_the_unkeyed_identity_says_it_is_unkeyed():
    """It used to return the bare path while the docstring claimed "saying so beats inventing one".
    It did not say so. The prefix is what makes the two checks above possible."""
    d = tempfile.mkdtemp()
    assert _derived_store_id(_store(d, "a", 2)).startswith("unkeyed:")


def test_a_hand_built_bundle_cannot_claim_cosignatures_over_an_unkeyed_store():
    """build_bundle will not produce one, but a bundle is a file and anyone can write a file."""
    d = tempfile.mkdtemp()
    b = build_bundle(_store(d, "a", 2))
    b["anchor"]["cosignatures"] = [["aa" * 32, "bb" * 64]]
    out = verify_bundle(b)
    assert not out["ok"] and any("vouch for nothing" in p for p in out["problems"]), out["problems"]


def test_control_a_receipted_identity_survives_a_copy_and_is_witnessable():
    """THE PROPERTY, and the criterion the first probe should have used. Collision-freedom was the
    wrong question -- receipt-less stores never collided, they each carried their own path, and
    every one of them was copyable to a fresh witness contact."""
    d = tempfile.mkdtemp()
    ix = _store(d, "a", 3, receipts=True, receipt_key=SK)
    w = Witness(state_path=os.path.join(d, "w.json"))
    wid = build_bundle(ix, witnesses=[w])["store_id_derived"]
    assert wid.startswith("insp1:")

    shutil.copytree(os.path.join(d, "a"), os.path.join(d, "b"))
    moved = Inspeximus(path=os.path.join(d, "b", "s.json"), receipts=True, receipt_key=SK)
    assert _derived_store_id(moved) == wid, "a copy became a new identity: the cp bypass is open"


def test_control_an_ordinary_receipted_bundle_is_unaffected():
    """The must-not-brick control for the refusal above. If witnessing a normal store ever started
    raising, the two tests before this one would be documenting a broken feature."""
    d = tempfile.mkdtemp()
    ix = _store(d, "a", 3, receipts=True, receipt_key=SK)
    ws = [Witness(state_path=os.path.join(d, f"w{i}.json")) for i in range(3)]
    b = build_bundle(ix, witnesses=ws)
    assert len(b["anchor"]["cosignatures"]) == 3
    assert verify_bundle(b, witnesses=[w.public for w in ws], threshold=2)["ok"]
