"""The critical defects found by the 2026-07-25 codebase audit, pinned so they cannot come back.

All three were found by auditing for a defect CLASS we had just fixed elsewhere — and all three had the same
shape as the thing that prompted the audit: an instrument reported safe while the guarantee was broken.
`recall()` honoured tenant isolation while `history()` handed over the other tenant's plaintext;
`verify_writes()` returned True while four of five records had never reached disk; and the collision guard
shipped in 1.53.0 covered one of five subject-scoped destructive paths.
"""
import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from inspeximus import AmbiguousSubject, Inspeximus
from inspeximus.core import _TenantView


def _path():
    return os.path.join(tempfile.mkdtemp(), "m.json")


# ── tenant isolation ────────────────────────────────────────────────────────────────────────────────
def _two_tenants():
    s = Inspeximus(path=_path())
    a, b = s.for_tenant("acme"), s.for_tenant("globex")
    a.remember("acme note", key="acme thing", object="x")
    b.remember("GLOBEX_SECRET the globex api key is sk-globex-999",
               key="globex api key", object="sk-globex-999")
    return s, a, b


@pytest.mark.parametrize("call", [
    lambda a: [x.get("text", "") for x in a.history("globex api key")],
    lambda a: [str(a.provenance("globex api key").get("current"))],
    lambda a: [str(a.as_of("globex api key", 9e9))],
    lambda a: [str(x) for x in (a.why_recalled("globex api key", k=10) or [])],
])
def test_no_read_path_returns_another_tenants_text(call):
    """recall() was scoped; these four read the shared list directly and returned the secret verbatim."""
    _, a, _ = _two_tenants()
    assert "sk-globex-999" not in " ".join(call(a))


def test_a_tenant_cannot_delete_another_tenants_record():
    """`beta.forget([acme_id])` returned {'forgotten': 1} and the row was gone."""
    s, a, b = _two_tenants()
    acme_id = next(r["id"] for r in s.items if r.get("tenant") == "acme")
    res = b.forget([acme_id])
    assert res["forgotten"] == 0
    assert any(r["id"] == acme_id for r in s.items), "another tenant's record was hard-deleted"


def test_a_tenant_cannot_write_to_another_tenants_record():
    s, a, b = _two_tenants()
    acme_id = next(r["id"] for r in s.items if r.get("tenant") == "acme")
    assert b.credit([acme_id], outcome=True)["updated"] == []


def test_aggregate_reports_do_not_count_another_tenant():
    s, a, b = _two_tenants()
    assert a.memory_report()["total"] == 1
    assert a.state_digest() != b.state_digest(), "identical digests reveal the other tenant's state"


def test_an_unclassified_method_RAISES_rather_than_running_as_admin():
    """THE structural fix. The view forwarded everything it did not rebind, so 54 of 79 public methods ran
    against the shared store as tenant=None. A method added tomorrow must fail loudly, not leak quietly."""
    s = Inspeximus(path=_path())
    view = s.for_tenant("acme")

    def brand_new_method(self):                       # simulate tomorrow's addition
        return [r["text"] for r in self.items]
    Inspeximus.brand_new_method = brand_new_method
    try:
        with pytest.raises(AttributeError, match="not classified for tenant views"):
            view.brand_new_method()
    finally:
        del Inspeximus.brand_new_method


def test_every_public_method_is_classified():
    """Fails the moment someone adds a public method without deciding whether it is tenant-scoped."""
    public = {n for n in dir(Inspeximus)
              if not n.startswith("_") and callable(getattr(Inspeximus, n))}
    unclassified = sorted(public - set(_TenantView.__dict__) - _TenantView._STORE_LEVEL)
    assert not unclassified, f"classify these on _TenantView: {unclassified}"


# ── persistence ─────────────────────────────────────────────────────────────────────────────────────
def test_a_failed_save_is_not_reported_as_a_successful_one():
    """Measured pre-fix: 5 records in memory, 1 on disk, verify_writes() -> True."""
    p = _path()
    m = Inspeximus(path=p, receipts=True)
    m.remember("fact 0")

    class Unserialisable:
        pass
    with pytest.raises(ValueError, match="JSON-serialisable"):
        m.remember("poison", meta={"o": Unserialisable()})   # rejected at the write that carries it

    for i in range(1, 4):
        m.remember(f"good fact {i}")
    m.flush()
    assert len(Inspeximus(path=p).items) == 4, "the poisoned write must not cost the good ones"


def test_verify_writes_reports_a_store_that_never_reached_disk():
    p = os.path.join(tempfile.mkdtemp(), "sub", "m.json")     # parent dir does not exist -> save fails
    m = Inspeximus(path=p, receipts=True)
    m.remember("fact 0")
    ok, problems = m.verify_writes()
    assert ok is False
    assert any("not persisted" in x for x in problems)
    with pytest.raises(OSError):
        m.flush()


# ── the collision guard, on every destructive path ──────────────────────────────────────────────────
ALICE, BOB = "crm.example.com/alice", "crm.example.com/bob"


def _colliding_store():
    m = Inspeximus(path=_path(), receipts=True, pii_detect=True)
    m.remember("alice a@corp.com: the db host is old.host",
               key="db::host", object="old.host", source={"doc": ALICE})
    m.remember("bob b@corp.com notes: the db host is old.host", source={"doc": BOB})
    return m


@pytest.mark.parametrize("call", [
    lambda m: m.forget_subject(ALICE, request_id="r", basis="b"),
    lambda m: m.forget_pii(subject=ALICE, request_id="r"),
    lambda m: m.retract_lineage(ALICE),
    lambda m: m.rederive(ALICE),
])
def test_every_subject_scoped_destructive_path_refuses_a_collision(call):
    """1.53.0 guarded forget_subject only. Its four siblings kept the defect: forget_pii hard-deleted Bob,
    retract_lineage demoted him, and rederive REWROTE his text and re-emitted it."""
    m = _colliding_store()
    with pytest.raises(AmbiguousSubject):
        call(m)
    assert len(m.items) == 2, "a refused call must not have changed anything"


@pytest.mark.parametrize("call", [
    lambda m: m.forget_subject("User 42", request_id="r", basis="b")["erased"],
    lambda m: m.forget_pii(subject="User 42", request_id="r")["erased"],
    lambda m: m.retract_lineage("User 42")["demoted"],
])
def test_the_guard_does_not_break_intended_canonical_resolution(call):
    m = Inspeximus(path=_path(), receipts=True, pii_detect=True)
    m.remember("a note a@corp.com", source={"doc": "user-42"})
    assert call(m) == 1


def test_subject_resolution_lives_in_one_place():
    """The guard regressed because five call sites each inlined `cand = {subject, _canon_source(subject)}`.
    Fixing one fixed one. New subject-scoped paths must go through the shared resolver."""
    import re
    src = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "inspeximus", "core.py"), encoding="utf-8").read()
    inlined = len(re.findall(r"cand = \{subject, Inspeximus\._canon_source\(subject\)\}", src))
    assert inlined <= 3, (
        f"{inlined} inlined subject resolutions — use self._resolve_subject(), which carries the "
        f"collision guard. Allowed: _resolve_subject itself, forget_subject, and the read-only "
        f"erasure_audit.")
