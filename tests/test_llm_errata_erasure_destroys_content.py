"""An erasure through the LLM Errata adapter must DESTROY content, not demote it.

Shipped broken through 2.9.0: `retire()` marked the record `superseded` and kept its text in both
branches, so an `erase` erratum returned `aggregate: verified` while the erased proposition sat in
the store, and on disk, verbatim. A success-shaped non-erasure in the product we sell on memory
integrity.

The reference contract keys this on `superseded_at`: supplied only for a supersession, where history
is worth keeping; its ABSENCE means correction or erasure, where the content must go. IDEA.md is
explicit that a receipt must not preserve the secret it claims to erase.

Found by our own candidate conformance case only after it was strengthened to search the PERSISTED
state rather than present-tense recall, which is the only view where concealment and erasure differ.
"""
import json
import os
import tempfile

import pytest

from inspeximus import Inspeximus
from inspeximus.integrations.llm_errata import InspeximusErrataAdapter


def _store():
    path = os.path.join(tempfile.mkdtemp(), "m.json")
    mem = Inspeximus(path=path, receipts=True)
    mem.remember("is vegetarian", source={"doc": "fact:diet"}, key="diet")
    mem.remember("prefers quiet restaurants", source={"doc": "fact:rest"}, key="rest")
    return mem, path


def _target(mem):
    return next(r["id"] for r in mem.items if r.get("key") == "diet")


def test_erasure_removes_the_content_from_the_persisted_state():
    mem, path = _store()
    InspeximusErrataAdapter(mem).retire(_target(mem))
    assert "is vegetarian" not in open(path, encoding="utf-8").read()
    assert "prefers quiet restaurants" in open(path, encoding="utf-8").read()


def test_supersession_keeps_the_history_it_is_for():
    """The CONTROL. Without it, `retire` could destroy everything and still pass the test above,
    which would break supersession -- the operation whose entire purpose is retaining what was true."""
    mem, path = _store()
    InspeximusErrataAdapter(mem).retire(_target(mem), superseded_at="2026-08-01T00:00:00Z")
    raw = open(path, encoding="utf-8").read()
    assert "is vegetarian" in raw, "a supersession must keep the proposition that was true"
    assert json.loads(raw) or True
    demoted = [r for r in mem.items if r.get("text") == "is vegetarian"]
    assert demoted and demoted[0]["status"] == "superseded"


def test_the_erased_value_is_not_recallable_afterwards():
    mem, _ = _store()
    InspeximusErrataAdapter(mem).retire(_target(mem))
    assert not [r for r in mem.items if r.get("text") == "is vegetarian"]


def _with_derivative():
    path = os.path.join(tempfile.mkdtemp(), "m.json")
    mem = Inspeximus(path=path, receipts=True)
    a = mem.remember("is vegetarian", source={"doc": "fact:diet"}, key="diet")
    b = mem.remember("prefers quiet restaurants", source={"doc": "fact:rest"}, key="rest")
    mem.remember("is vegetarian; prefers quiet restaurants", derived=True, derived_from=[a, b])
    return mem, path


def test_a_surviving_derived_copy_blocks_a_verified_claim():
    """forget() removes the record it is given, not the proposition wherever a summariser copied it.

    Its completeness note rests on "consolidation never copies raw text into other records", which
    does not hold for remember(derived=True). So the erased value can survive verbatim in a
    derivative, and the record that would have noticed is the one just destroyed. Coverage must
    therefore say `partial`, not `verified`. This is the conservative half of the fix: widening the
    deletion is a separate data-loss decision.
    """
    mem, path = _with_derivative()
    adapter = InspeximusErrataAdapter(mem)
    adapter.retire(_target(mem))
    assert adapter._erasure_residue, "a surviving copy must be recorded, not ignored"
    assert "is vegetarian" in open(path, encoding="utf-8").read()
    # _coverage() degrades to a bare string when the spec package is absent, and it must:
    # importing inspeximus may never require llm-errata to be installed.
    cov = adapter.coverage("fact:diet")
    assert getattr(cov, "value", cov) != "verified"


def test_a_forget_that_removes_nothing_still_demotes_the_record():
    """The regression this fix introduced, now a test.

    forget() reports `forgotten: 0` without raising when the id is outside the caller's tenant rows.
    The first destructive branch discarded that result AND no longer set a status, so a no-op
    erasure left the record ACTIVE -- worse than the demotion it replaced. A fix that can silently
    do nothing is the defect it was meant to correct, one layer down.
    """
    mem, _ = _store()
    adapter = InspeximusErrataAdapter(mem)
    adapter.store.forget = lambda **kw: {"forgotten": 0}
    target = _target(mem)
    adapter.retire(target)
    rec = next(r for r in mem.items if r["id"] == target)
    assert rec["status"] == "superseded", "a failed erasure must not leave the record active"
    assert adapter._erasure_residue, "a failed erasure must be recorded"
    # _coverage() degrades to a bare string when the spec package is absent, and it must:
    # importing inspeximus may never require llm-errata to be installed.
    cov = adapter.coverage("fact:diet")
    assert getattr(cov, "value", cov) != "verified"


def test_an_erasure_destroys_the_mixed_descendant_and_keeps_the_collateral():
    """The debt this file was opened with, now paid.

    An erasure carries no replacement, so `rebuild` used to write nothing and leave the mixed
    descendant demoted with its text intact: a summariser's record reading "is vegetarian; prefers
    quiet restaurants" survived an erase of the first half verbatim, and coverage could only honestly
    report `partial`. It now destroys the descendant AFTER the surviving inputs have been re-asserted,
    so the collateral it takes with it is collateral that already exists elsewhere.
    """
    # The spec package is an OPTIONAL dependency and must stay one: importing inspeximus may never
    # require llm-errata. Skip rather than fail where it is absent.
    pytest.importorskip("prototype")
    from prototype.errata import Erratum, Operation
    from prototype.scenario import build_importer
    from prototype.signing import Ed25519Signer

    mem, path = _with_derivative()
    owner = Ed25519Signer(b"\x01" * 32, key_id="key-1")
    importer = build_importer(owner)
    importer.adapters = [InspeximusErrataAdapter(mem)]
    importer.roots = ("fact:diet",)
    receipt = importer.repair(owner.sign_erratum(Erratum(
        erratum_id="e", sequence=1, target_root="fact:diet", operation=Operation.ERASE,
        valid_from="2026-08-01T00:00:00Z",
        postconditions={"negative": "vegetarian", "preserve": "quiet restaurants"})))

    raw = open(path, encoding="utf-8").read()
    assert "is vegetarian" not in raw, "the descendant kept a verbatim copy of the erased fact"
    assert "prefers quiet restaurants" in raw, "the collateral was destroyed with it"
    assert receipt.aggregate.value == "verified"


def test_residue_is_rechecked_rather_than_trusted():
    """A flag recorded in one phase must not outlive the condition another phase removed.

    `retire` records a surviving copy at the moment it destroys the root; `rebuild` then destroys that
    copy in the same repair. Trusting the recorded list reported `partial` for a store whose erasure
    had completed. Coverage re-reads the store instead.
    """
    mem, _ = _store()
    adapter = InspeximusErrataAdapter(mem)
    adapter._erasure_residue.append(
        {"proposition": "a proposition no record contains", "record": "x"})
    cov = adapter.coverage("fact:diet")
    assert getattr(cov, "value", cov) == "verified", "a stale residue entry blocked a clean verdict"
