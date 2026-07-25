"""Round two: what the RE-audit found after the 1.54.0 fixes.

The lesson of this file is not any single defect. It is that the first round fixed each defect *at the
instance it was reported* and the class survived in every case — the tenant guard's own allow-list contained
destructive store-wide methods, the collision guard covered the erasure paths but not the standing/budget
levers, and the persistence fix covered the store file but not the sidecars that hold the evidence.
"""
import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from inspeximus import AmbiguousSubject, Inspeximus
from inspeximus.audit_bundle import build_bundle, verify_bundle


def _path():
    return os.path.join(tempfile.mkdtemp(), "m.json")


def _two_tenants():
    s = Inspeximus(path=_path(), receipts=True)
    a, b = s.for_tenant("acme"), s.for_tenant("globex")
    a.remember("acme row")
    b.remember("globex SECRET row", key="b/k", object="v1")
    return s, a, b


def test_revert_cannot_restore_another_tenants_value():
    """`revert` WAS rebound on the tenant view, and still leaked: it scanned the shared list for the key, so
    A.revert(B_key) returned B's plaintext and wrote a copy of it into A."""
    s, a, b = _two_tenants()
    b.remember("globex v2", key="b/k", object="v2")

    res = a.revert("b/k")
    assert not res.get("ok"), f"a tenant reverted another tenant's key: {res}"
    assert "SECRET" not in str(res)


def test_the_store_level_allowlist_holds_no_destructive_method():
    """I put apply_retention, shred, grade and erasure_certificate in the passthrough list. They iterate the
    whole store, so from a tenant view they reach every tenant's records."""
    from inspeximus.core import _TenantView
    destructive = {"shred", "apply_retention", "sleep", "forget", "forget_pii", "forget_subject",
                   "slash", "retract_lineage", "rederive", "revert", "consolidate"}
    assert not (destructive & _TenantView._STORE_LEVEL), (
        f"destructive methods must be tenant-bound, not passed through: "
        f"{sorted(destructive & _TenantView._STORE_LEVEL)}")


def test_slash_refuses_to_forfeit_a_colliding_subjects_standing():
    """Caught on crm.example.com/alice, slash forfeited crm.example.com/bob too — measured `slashed: 2` with
    Bob's standing inverted. Same lossy-key-as-selector defect as erasure, one lever over."""
    m = Inspeximus(path=_path(), receipts=True)
    alice = m.remember("alice fact", source={"doc": "crm.example.com/alice"})
    m.remember("bob fact", source={"doc": "crm.example.com/bob"})

    with pytest.raises(AmbiguousSubject):
        m.slash([alice])
    assert m.slash([alice], allow_ambiguous=True)["slashed"] == 2      # deliberate is still possible


def test_slash_still_expands_across_a_genuine_sybil_source():
    """The guard must not break what source-scope slashing is FOR."""
    m = Inspeximus(path=_path(), receipts=True)
    first = m.remember("one", source={"doc": "runbook"})
    m.remember("two", source={"doc": "runbook"})
    assert m.slash([first])["slashed"] == 2


@pytest.mark.parametrize("sidecar,act", [
    ("receipts", lambda m: m.remember("b")),
    ("tombstones", lambda m: m.forget_subject("dave", request_id="R", basis="b")),
])
def test_a_sidecar_that_never_reached_disk_is_reported(sidecar, act):
    """The receipt and tombstone chains ARE the evidence. Losing them silently was worse than losing a
    record: 4 receipts in memory, verify_writes() -> True, zero on reload; and a tombstoned erasure whose
    certificate said verified while a reload showed erasures_total: 0."""
    p = _path()
    m = Inspeximus(path=p, receipts=True)
    m.remember("a", source={"doc": "dave"})

    side = p + f".{sidecar}.json"
    if os.path.exists(side):
        os.remove(side)
    os.makedirs(side)                                   # a directory cannot be overwritten by a file write
    act(m)

    ok, problems = m.verify_writes()
    assert ok is False
    assert any(f"{sidecar} chain was NOT persisted" in x for x in problems)
    with pytest.raises(OSError):
        m.flush()


def test_a_successful_store_save_does_not_erase_a_sidecar_failure():
    """The first version of this fix put both in one slot, so the next successful _save() wiped the record
    that the tombstone chain had never been written."""
    p = _path()
    m = Inspeximus(path=p, receipts=True)
    m.remember("a", source={"doc": "dave"})
    side = p + ".tombstones.json"
    if os.path.exists(side):
        os.remove(side)
    os.makedirs(side)
    m.forget_subject("dave", request_id="R", basis="b")
    m.remember("a later write that saves fine")

    assert m.verify_writes()[0] is False


def test_a_partially_receipted_store_is_not_reported_as_verified():
    """The empty-chain check only fired when NOTHING was receipted. Five records written with receipts off,
    reopened with them on, one more written: 6 records, 1 receipt, bundle verified clean."""
    p = _path()
    m = Inspeximus(path=p, receipts=False)
    for i in range(5):
        m.remember(f"unreceipted {i}")
    m2 = Inspeximus(path=p, receipts=True)
    m2.remember("receipted")

    res = verify_bundle(build_bundle(m2))
    assert res["ok"] is False
    assert any("covered by a write receipt" in x for x in res["problems"])


def test_governance_report_does_not_certify_a_store_with_no_receipts():
    """The auditor-facing compliance surface passed vacuously on the same store its sibling refused."""
    m = Inspeximus(path=_path(), receipts=False)
    for i in range(5):
        m.remember(f"x{i}")
    assert (m.governance_report().get("proof") or {}).get("verified") is False

    healthy = Inspeximus(path=_path(), receipts=True)
    healthy.remember("ok")
    assert (healthy.governance_report().get("proof") or {}).get("verified") is True


def test_receipts_enabled_but_an_empty_chain_is_also_not_a_pass():
    """The other half of the same hole, and the one a mutation caught this test missing: receipts ON, records
    present, chain empty (a store opened with receipts after the fact, or a sidecar that never loaded)."""
    p = _path()
    m = Inspeximus(path=p, receipts=False)
    for i in range(3):
        m.remember(f"x{i}")
    reopened = Inspeximus(path=p, receipts=True)
    reopened._receipts = []                                   # chain absent, records present
    assert (reopened.governance_report().get("proof") or {}).get("verified") is False


def test_the_ambiguity_guard_has_an_escape_hatch_on_the_mcp_surface():
    """A guard with no override turns a legitimate GDPR erasure into an unreachable one. The library had
    allow_ambiguous; the product surface did not."""
    import ast
    src = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "inspeximus", "mcp_server.py"), encoding="utf-8").read()
    fn = next(n for n in ast.walk(ast.parse(src))
              if isinstance(n, ast.FunctionDef) and n.name == "forget_subject")
    assert "allow_ambiguous" in [a.arg for a in fn.args.args]
    assert "AmbiguousSubject" in (ast.get_docstring(fn) or ""), \
        "the tool must tell the agent what to do when the guard fires"
