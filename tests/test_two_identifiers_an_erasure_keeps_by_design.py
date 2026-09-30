"""Two identifiers that `forget_subject()` keeps, and that docs/ERASURE.md ("Full scope") and the README
state as limits. Measured by AUDIT-A on 2026-09-30.

1. A tenant or agent id stays in the event journal: `for_tenant("jane-tenant-77")` is still in the store
   file twice after the erasure. The docs tell users to pick a pseudonymous id.
2. `<store>.objections.json` keeps the objecting subject's identifier, because that entry is what keeps
   suppressing new records about the subject.

These tests pin the documented behaviour. When a later release redacts either identifier, they fail, and
the two limits in the docs have to go with them.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from inspeximus import Inspeximus  # noqa: E402

EMAIL = b"jane@example.test"


def test_a_tenant_id_that_names_a_person_stays_in_the_store_file(tmp_path):
    path = tmp_path / "memory.json"
    tenant = Inspeximus(str(path)).for_tenant("jane-tenant-77")
    tenant.remember("Jane prefers invoices to jane@example.test", key="invoice-email",
                    source={"doc": "jane.example"})
    before = path.read_bytes()
    assert before.count(EMAIL) >= 1, "control: the record's text must be in the file before the erasure"
    assert tenant.forget_subject("jane.example")["erased"] == 1
    after = path.read_bytes()
    assert after.count(EMAIL) == 0
    assert after.count(b"jane-tenant-77") == 2, after.count(b"jane-tenant-77")


def test_an_objection_keeps_the_subject_id_after_the_erasure(tmp_path):
    path = tmp_path / "memory.json"
    m = Inspeximus(str(path))
    m.remember("Jane prefers invoices to jane@example.test", key="invoice-email",
               source={"doc": "jane.example"})
    m.object_processing("jane.example", actor="dpo", ground="direct_marketing")
    m.forget_subject("jane.example")
    assert path.read_bytes().count(b"jane.example") == 0
    objections = tmp_path / "memory.json.objections.json"
    assert objections.exists(), sorted(os.listdir(tmp_path))
    assert objections.read_bytes().count(b"jane.example") == 2
