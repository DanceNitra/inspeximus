"""Round three, on 1.56.0 — including two regressions the structural fix itself introduced.

The pattern from rounds one and two held again, and this time I was the source of it: the tenant-isolation
work rebound a `get` that does not exist, and cached the scoped view as a mutable list that could be poisoned.
A fix is new code and carries the same defect rate as any other; the round that audits it is not optional.
"""
import copy
import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from inspeximus import AmbiguousSubject, Inspeximus
from inspeximus.compliance import compliance_check


def _path():
    return os.path.join(tempfile.mkdtemp(), "m.json")


# ── regressions introduced by the 1.56.0 tenant work ────────────────────────────────────────────────
def test_the_tenant_view_exposes_no_method_that_does_not_exist():
    """`get` was rebound onto the view, but `Inspeximus.get` has never existed — so every tenant-scoped
    `get()` raised AttributeError. Shipped inside the commit that was meant to make tenancy safe."""
    s = Inspeximus(path=_path())
    view = s.for_tenant("acme")
    from inspeximus.core import _TenantView
    slots = set(getattr(_TenantView, "__slots__", ()))
    missing = [n for n in _TenantView.__dict__
               if not n.startswith("__") and n not in slots
               and n not in ("items", "_items", "for_tenant", "_STORE_LEVEL")
               and not hasattr(Inspeximus, n)]
    assert not missing, f"the view rebinds methods that do not exist on Inspeximus: {missing}"
    assert view is not None


def test_a_write_into_the_scoped_view_cannot_plant_a_phantom_record():
    """The cache returned the list OBJECT, so `view.items.append(rec)` did not merely fail to persist — it
    planted a record every later reader saw, including fresh handles, that recall ranked FIRST, that was never
    on disk, and that vanished on the next write."""
    s = Inspeximus(path=_path())
    t = s.for_tenant("acme")
    t.remember("real acme fact about widgets")

    phantom = copy.deepcopy(s._items[0])
    phantom.update({"id": "phantom", "text": "phantom fact about widgets"})
    with pytest.raises(AttributeError):
        t.items.append(phantom)

    assert [r["id"] for r in s.for_tenant("acme").items] == [s._items[0]["id"]]


def test_an_unparseable_store_refuses_to_open_instead_of_overwriting_it():
    """A truncated file loaded as [] and the very next save wrote that empty list over it: 5 records in,
    0 loaded, 1 on disk afterwards. The encrypted branch had always raised here; the plaintext one destroyed
    the store instead. Receipts would have caught it — but they are off by default."""
    p = _path()
    m = Inspeximus(path=p)
    for i in range(5):
        m.remember(f"record {i}")
    raw = open(p, encoding="utf-8").read()
    open(p, "w", encoding="utf-8").write(raw[:len(raw) // 2])

    with pytest.raises(ValueError, match="Refusing to open"):
        Inspeximus(path=p)
    assert open(p, encoding="utf-8").read() == raw[:len(raw) // 2],         "the unreadable file must be left exactly as found, not overwritten"


# ── the collision class, on the levers round two missed ─────────────────────────────────────────────
ALICE, BOB = "crm.example.com/alice", "crm.example.com/bob"


def _colliding():
    m = Inspeximus(path=_path(), receipts=True)
    a = m.remember("alice fact", source={"doc": ALICE})
    b = m.remember("bob fact", source={"doc": BOB})
    return m, a, b


@pytest.mark.parametrize("name,call", [
    ("monitor", lambda m, a: m.monitor([a], outcome=False)),
    ("spend_irreversible", lambda m, a: m.spend_irreversible([a], 1.0)),
    ("restore", lambda m, a: (m.slash([a], allow_ambiguous=True), m.restore([a]))[1]),
])
def test_the_remaining_standing_levers_refuse_a_collision(name, call):
    """Measured before: 20 bad outcomes on Alice left Bob one call from an alarm he never earned; Alice's
    spend exhausted Bob's lifetime budget; and restoring Alice cleared a slash Bob had earned on his own
    catch — the worst of the three, because it RE-ADMITS a source that was correctly forfeited."""
    m, a, _ = _colliding()
    with pytest.raises(AmbiguousSubject):
        call(m, a)


def test_those_levers_still_work_across_a_genuine_sybil_source():
    m = Inspeximus(path=_path(), receipts=True)
    first = m.remember("one", source={"doc": "runbook"})
    m.remember("two", source={"doc": "runbook"})
    assert "cusum" in m.monitor([first], outcome=False)
    assert m.spend_irreversible([first], 0.1)["allowed"] is True


# ── verifiers ───────────────────────────────────────────────────────────────────────────────────────
def test_compliance_check_sees_partial_receipt_coverage():
    """verify_bundle got this check in 1.54.0; its sibling gate never did. Five records written with receipts
    off, reopened with them on, one more written: ok=True, violations []."""
    p = _path()
    m = Inspeximus(path=p, receipts=False)
    for i in range(5):
        m.remember(f"x{i}")
    m2 = Inspeximus(path=p, receipts=True)
    m2.remember("receipted")

    res = compliance_check(m2)
    assert res["ok"] is False
    assert "receipts_partial" in [v["code"] for v in res["violations"]]


def test_compliance_check_still_passes_a_fully_receipted_store():
    m = Inspeximus(path=_path(), receipts=True)
    m.remember("a")
    m.remember("b")
    assert compliance_check(m)["ok"] is True


def test_the_forgeable_bundle_checks_are_labelled_as_advisory():
    """`bundle_hash` is an unkeyed SHA-256, so an exporter can set `n_records` or `proof.verified` and
    recompute it in three lines — both demonstrated. The checks stay (the accidental case is the common one),
    but nothing may present them as proof."""
    from inspeximus.audit_bundle import verify_bundle
    doc = verify_bundle.__doc__ or ""
    assert "ADVISORY" in doc and "forged" in doc.lower()


# ── the remaining silent writes ─────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("sidecar,act", [
    ("cusum", lambda m, rid: m.monitor([rid], outcome=False)),
    ("irrev", lambda m, rid: m.spend_irreversible([rid], 0.5)),
])
def test_the_last_silent_sidecar_writes_are_reported(sidecar, act):
    """Both promised cross-session state in their docstrings — the CUSUM detector and the lifetime
    irreversible budget. Both lost it silently, so a restart reset the cap the budget exists to enforce."""
    p = _path()
    m = Inspeximus(path=p, receipts=True)
    rid = m.remember("x", source={"doc": "src"})
    side = p + f".{sidecar}.json"
    if os.path.exists(side):
        os.remove(side)
    os.makedirs(side)

    try:
        act(m, rid)
    except Exception:
        pass
    ok, problems = m.verify_writes()
    assert ok is False
    assert any(sidecar in x for x in problems)
