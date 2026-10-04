"""Two identifiers that AUDIT-A measured `forget_subject()` keeping on 2026-09-30, and what became of each.

1. A tenant or agent id in the event journal. Through 3.16.1, `for_tenant("jane-tenant-77")` was still in
   the store file twice after the erasure, and the docs told users to pick a pseudonymous id. Since 3.16.2
   the erasure replaces both columns of the record's journal rows with a salted pseudonym
   (tests/test_an_erasure_pseudonymises_the_agent_and_tenant_in_the_journal.py), so this test now pins
   the absence, and the limit has left docs/ERASURE.md and the README.
2. `<store>.objections.json` keeps the objecting subject's identifier, because that entry is what keeps
   suppressing new records about the subject. That limit stands and is still documented.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from inspeximus import Inspeximus  # noqa: E402

EMAIL = b"jane@example.test"


def test_a_tenant_id_that_names_a_person_leaves_the_store_file(tmp_path):
    path = tmp_path / "memory.json"
    tenant = Inspeximus(str(path)).for_tenant("jane-tenant-77")
    tenant.remember("Jane prefers invoices to jane@example.test", key="invoice-email",
                    source={"doc": "jane.example"})
    before = path.read_bytes()
    assert before.count(EMAIL) >= 1, "control: the record's text must be in the file before the erasure"
    assert tenant.forget_subject("jane.example")["erased"] == 1
    after = path.read_bytes()
    assert after.count(EMAIL) == 0
    assert before.count(b"jane-tenant-77") >= 2, "control: the journal holds the tenant id before the erasure"
    assert after.count(b"jane-tenant-77") == 0, after.count(b"jane-tenant-77")


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
