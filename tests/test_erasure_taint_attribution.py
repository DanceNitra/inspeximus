"""Inherited `taint` may select for deletion only when it can attribute precisely.

The ghost-subject fix shipped this morning was INCOMPLETE, and its own docstring denied the hole it left:
it claimed taint was read "so the ghost cannot sneak back in through it", while matching taint against the
coarse host-only key the fix existed to stop using. Found by auditing the same day's work. Measured:

    forget_subject("crm/nobody-here")  erased a summary DERIVED from crm/alice   (ghost, via taint)
    forget_subject("crm/alice")        erased a summary DERIVED from crm/BOB     (third party, no refusal)

The second is the worse one: the summary's raw source is the writer service, so `_erasure_collisions`
never sees a conflict and nothing refuses. `also_carrying` names the writer service, not Bob.

WHY NOT JUST DROP THE TAINT PATH. Removing it fixed both and broke a real case: erase a parent BY ID, and
the records derived from it are reachable only by taint, so a later forget_subject left dangling lineage
and erasure_audit correctly reported residue. Incomplete erasure is not an improvement on over-erasure.

THE RULE. `taint` stores `_canon_source` keys, so it can attribute precisely only when that key still
spells the whole subject. 'user-42' survives canonicalisation intact; 'crm/alice' becomes 'crm', which is
equally 'crm/bob' and 'crm/nobody-here'. Taint therefore selects only for subjects with no path component
for canonicalisation to discard. Everything else travels along declared derived_from edges from a root
whose RAW source matches -- provenance the store actually recorded.
"""
from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from inspeximus import Inspeximus  # noqa: E402


def _store():
    return Inspeximus(path=os.path.join(tempfile.mkdtemp(), "s.json"), receipts=True)


def _alive(st):
    return sorted((r.get("text") or "") for r in st.items if r.get("status") == "active")


def test_a_ghost_subject_cannot_reach_a_derived_record():
    """THE hole the first fix left. The subject was never written to this store."""
    st = _store()
    a = st.remember("alice home address is 5 Elm St", key="a", object="5 Elm St",
                    source={"doc": "crm/alice"})
    st.remember("summary of alice file", key="s", object="sum", source={"doc": "summary-svc"},
                derived_from=[a])
    res = st.forget_subject("crm/nobody-here", request_id="ghost")
    assert res["erased"] == 0, res
    assert len(_alive(st)) == 2


def test_a_dsar_does_not_erase_a_record_derived_from_a_different_subject():
    """Bob's summary carries the coarse 'crm' taint; its RAW source is the writer service, so the
    collision guard is structurally blind to it. Attribution has to refuse, not the guard."""
    st = _store()
    st.remember("alice salary 100", key="a", object="100", source={"doc": "crm/alice"})
    b = st.remember("bob salary 200", key="b", object="200", source={"doc": "crm/bob"})
    st.remember("summary of bob file", key="bs", object="sum", source={"doc": "summary-svc"},
                derived_from=[b])
    res = st.forget_subject("crm/alice", request_id="dsar")
    assert res["erased"] == 1, res
    left = _alive(st)
    assert any("bob salary" in t for t in left), left
    assert any("summary of bob" in t for t in left), f"a third party's derived record went: {left}"


def test_the_lineage_cascade_still_erases_the_subjects_own_derived_records():
    """CONTROL. Narrowing must not turn over-erasure into INCOMPLETE erasure."""
    st = _store()
    a = st.remember("alice home", key="a", object="x", source={"doc": "crm/alice"})
    st.remember("summary of alice", key="s", object="y", source={"doc": "summary-svc"},
                derived_from=[a])
    assert st.forget_subject("crm/alice")["erased"] == 2


def test_taint_still_reaches_derived_records_when_the_root_is_already_gone():
    """The case dropping the taint path broke: the parent is erased by id first, so the derived record
    is reachable ONLY by taint. The subject has no path, so the coarse key attributes it precisely."""
    st = _store()
    parent = st.remember("alice bought a red bicycle", source={"doc": "user-42"})
    st.remember("summary: customer prefers red", derived=True, derived_from=[parent],
                source={"doc": "digest"})
    st.forget(ids=[parent], request_id="R1", basis="art17")
    assert st.forget_subject("user-42", request_id="R2")["erased"] == 1, "dangling lineage was left behind"


def test_a_subject_absent_from_the_store_does_not_match_by_punctuation():
    """`crm/alice-1` and `crm/alice1` were one identity because canonicalisation DELETED punctuation
    instead of collapsing it, and the collision guard cannot fire without an exact raw match."""
    st = _store()
    st.remember("payroll row", key="p", object="x", source={"doc": "crm/alice1"})
    assert st.forget_subject("crm/alice-1", request_id="r")["erased"] == 0
    assert len(_alive(st)) == 1


def test_the_tolerance_the_guard_exists_for_is_unchanged():
    """CONTROL. Collapsing punctuation must not break the variant matching it is there to allow."""
    assert Inspeximus._canon_subject("User_42") == Inspeximus._canon_subject("user-42")
    assert Inspeximus._canon_subject("crm/alice") != Inspeximus._canon_subject("crm/bob")
    assert Inspeximus._canon_subject("crm/alice-1") != Inspeximus._canon_subject("crm/alice1")
