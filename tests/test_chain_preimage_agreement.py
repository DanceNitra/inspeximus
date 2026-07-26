"""One hash preimage, four consumers, and what happened when a change reached only two of them.

1.68.0 put `amends` into a write receipt's hash. It was added in `_emit_write_receipt` and `verify_writes`,
and NOT in `_chain_core` -- which is what `anchor()` and the offline `audit_bundle` verifier use. Nor in
`_content_free_writes`, which decides what actually travels in the bundle.

Measured against the INSTALLED 1.68.0 and 1.71.0 packages, after a single slash():

    verify_writes()      -> True          (the store says it is fine)
    verify_bundle()      -> False         "write chain breaks at index 1"

The bundle is the artefact an auditor verifies offline with no store and no key, so the offline audit path
was broken for every store where standing had ever been revoked -- including under the version that wrote
the bundle.

A CORRECTION belongs here too. The first version of this file also claimed `anchor()` was committing to a
truncated chain, "n_writes = 1 with TWO receipts present". That was my own misreading: the fixture behind
it used a record that never graduated, so `slash()` changed no committed field, no amendment was emitted,
and the store really did hold one receipt. Re-measured with a fixture that graduates, the anchor reports
n_writes=2 of 2 on 1.68.0, 1.71.0 and 1.72.0 alike -- it was never affected. The anchor assertion below is
kept as a genuine invariant, but it is NOT a regression test: it passes before and after.

The fix is not four fixes: `_chain_core` is now THE definition and the emitter and verifier both call it.
These tests assert the surfaces AGREE, because that is the property that was violated -- each one on its
own was self-consistent.
"""
import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import inspeximus.core as core
from inspeximus import Inspeximus
from inspeximus.audit_bundle import build_bundle, verify_bundle


def _graduated(receipts=True):
    """A record that has EARNED semantic standing -- without graduation `slash()` changes no committed
    field, emits no amendment, and the whole class under test is never exercised."""
    m = Inspeximus(path=os.path.join(tempfile.mkdtemp(), "m.json"), receipts=receipts)
    rid = m.remember("alpha is the value for k0", key="k0", object="alpha")
    for _ in range(20):
        m.credit([rid], outcome=True)
    rec = next(r for r in m.items if r["id"] == rid)
    assert rec["mtype"] == "semantic", "fixture must graduate, or no amendment is ever emitted"
    return m, rid


@pytest.mark.parametrize("ops", [("slash",), ("slash", "restore"), ("slash", "restore", "slash")])
def test_every_surface_agrees_after_an_amendment(ops):
    m, rid = _graduated()
    for op in ops:
        getattr(m, op)([rid], scope="memory")
    m.flush()

    assert any(r.get("amends") for r in m._receipts), "the fixture must have produced an amendment"

    ok, problems = m.verify_writes()
    assert ok is True, problems

    anchor = m.anchor()
    assert anchor["n_writes"] == len(m._receipts), \
        f"the anchor must commit to the WHOLE chain, not a prefix it can still re-derive: {anchor}"

    res = verify_bundle(build_bundle(m))
    assert res["ok"] is True, res.get("problems")


def test_the_bundle_carries_the_amends_field():
    """The preimage can be right everywhere and still fail if the field never leaves the store: the export
    used a fixed key list and dropped it."""
    m, rid = _graduated()
    m.slash([rid], scope="memory")
    m.flush()

    chain = build_bundle(m)["write_chain"]
    assert any(r.get("amends") for r in chain), \
        f"an offline verifier cannot re-derive a hash from fields it was never given: {chain}"


def test_the_preimage_has_exactly_one_definition():
    """Structural. If a fifth consumer starts recomputing the preimage inline, this is the test that says
    so -- the defect was four definitions, not a wrong one."""
    m, rid = _graduated()
    m.slash([rid], scope="memory")

    amendment = next(r for r in m._receipts if r.get("amends"))
    assert core._sha256_hex(core._canon(Inspeximus._chain_core(amendment, "write"))) == amendment["hash"], \
        "the stored hash must equal what the single shared definition produces"

    plain = next(r for r in m._receipts if not r.get("amends"))
    assert "amends" not in Inspeximus._chain_core(plain, "write"), \
        "and a receipt with no amendment must not carry an empty one into the preimage"


def test_an_amendment_still_cannot_be_forged_into_an_existing_receipt():
    """The reason `amends` is in the preimage at all. Unifying the definition must not have loosened it."""
    m, rid = _graduated()
    m.slash([rid], scope="memory")
    next(r for r in m.items if r["id"] == rid)["text"] = "FORGED"
    assert m.verify_writes()[0] is False

    m._receipts[0]["amends"] = ["immutable_sha256"]        # try to authorise the edit after the fact
    ok, problems = m.verify_writes()
    assert ok is False
    assert any("hash mismatch" in p for p in problems), problems

    assert verify_bundle(build_bundle(m))["ok"] is False, "and the offline verifier must refuse it too"


def test_a_store_with_no_amendments_is_unaffected():
    """Backward compatibility in the direction that matters: nothing changes for a chain that never
    amended, so existing anchors and bundles keep verifying."""
    m, _rid = _graduated()
    m.remember("a second plain write")
    m.flush()

    assert not any(r.get("amends") for r in m._receipts)
    assert m.verify_writes()[0] is True
    assert m.anchor()["n_writes"] == len(m._receipts)
    assert verify_bundle(build_bundle(m))["ok"] is True
