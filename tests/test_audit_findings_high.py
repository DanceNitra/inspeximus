"""The HIGH-severity findings from the 2026-07-25 codebase audit.

Same theme as the criticals: each of these reported success while doing the opposite. `remember()` returned an
id for a record it had just evicted; `verify_bundle()` — the side an external auditor runs — returned PASS on
a bundle carrying its own failure, and on a bundle that proved nothing at all.
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from inspeximus import Inspeximus
from inspeximus.audit_bundle import build_bundle, verify_bundle


def _path():
    return os.path.join(tempfile.mkdtemp(), "m.json")


# ── H1: remember() evicting its own write ───────────────────────────────────────────────────────────
def test_remember_never_returns_the_id_of_a_record_it_just_evicted():
    """Measured pre-fix at capacity=3: the id came back, `present in store: False`, recall returned []."""
    m = Inspeximus(path=_path(), capacity=3)
    for i in range(3):
        m.remember(f"high value {i}", value=10.0)

    mid = m.remember("meeting moved to 4pm", value=0.1)
    assert any(r["id"] == mid for r in m.items), "remember() returned an id that is not in the store"
    assert m.recall("meeting moved"), "the record it claimed to store is not recallable"


def test_the_capacity_bound_still_holds_exactly():
    """Admitting the new record must evict one of the others, not silently raise the ceiling."""
    m = Inspeximus(path=_path(), capacity=3)
    for i in range(10):
        m.remember(f"record {i}", value=float(i))
    assert len([r for r in m.items if r.get("status") == "active"]) == 3


def test_a_later_write_can_still_evict_an_earlier_low_value_one():
    """The exemption is only against a record's OWN write. Without this the fix would turn into a leak."""
    m = Inspeximus(path=_path(), capacity=2)
    low = m.remember("trivia", value=0.1)
    m.remember("important A", value=9.0)
    m.remember("important B", value=9.0)
    assert not any(r["id"] == low for r in m.items), "the low-value record should have aged out"


# ── H2: the auditor-facing verifier ─────────────────────────────────────────────────────────────────
def test_a_healthy_bundle_still_verifies():
    """A verifier that cannot pass is as useless as one that cannot fail."""
    m = Inspeximus(path=_path(), receipts=True)
    m.remember("a")
    m.remember("b")
    assert verify_bundle(build_bundle(m))["ok"] is True


def test_verify_bundle_fails_when_the_store_reported_its_own_failure():
    """build_bundle wrote governance.proof.verified=False and the verifier never read it: the chains re-walk
    consistently while the RECORDS no longer match their receipts, and the auditor saw PASS."""
    m = Inspeximus(path=_path(), receipts=True)
    m.remember("a")
    m.items[0]["text"] = "EDITED OUT OF BAND"
    m._save(force=True)
    assert m.verify_writes()[0] is False

    res = verify_bundle(build_bundle(m))
    assert res["ok"] is False
    assert any("write-verification" in p for p in res["problems"])


def test_verify_bundle_does_not_pass_a_bundle_that_proves_nothing():
    """A receipts-disabled store exported a bundle that verified clean with writes=0. 'Nothing to verify' is
    not 'verified'."""
    m = Inspeximus(path=_path(), receipts=False)
    m.remember("x")
    res = verify_bundle(build_bundle(m))
    assert res["ok"] is False
    assert any("NO write or tombstone receipts" in p for p in res["problems"])


def test_an_empty_store_is_reported_as_empty_rather_than_failed():
    """The counterpart: firing on a store with no activity would train people to ignore the finding."""
    m = Inspeximus(path=_path(), receipts=True)
    res = verify_bundle(build_bundle(m))
    assert res["ok"] is True
    assert any("nothing to verify" in c for c in res["checks"])
