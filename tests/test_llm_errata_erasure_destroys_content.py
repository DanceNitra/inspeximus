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
