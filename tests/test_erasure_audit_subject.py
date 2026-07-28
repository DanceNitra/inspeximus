"""The erasure audit has to answer about the STORE, not echo the subject it was handed.

`erasure_audit(subject)` selected records by the coarse `_canon_source` key -- the same lossy-key-as-
selector defect fixed in `forget_subject` earlier the same day, one lever over, on the surface an operator
reads to decide whether a DSAR is discharged. On a two-person store, measured, all three arms returned the
SAME record:

    erasure_audit('hr/carol')        residue_found, id cb0a2a7f38   <- carol was correctly erased
    erasure_audit('hr/dave')         residue_found, id cb0a2a7f38
    erasure_audit('hr/nobody-here')  residue_found, id cb0a2a7f38   <- never written to this store

'hr/carol', 'hr/dave' and 'hr/nobody-here' all collapse to 'hr'. Both directions are wrong and neither is
the lesser one: a subject that was never written is told a stranger's record is attributable to it (and the
`detail` string names the ghost, so the report reads as though the store knew it), while a correctly
completed erasure is reported as having left residue. A compliance surface that cries failure on a success
gets ignored, which is how the real failure gets through.

The fix reuses `_narrow_to_subject` rather than restating its rule: travel declared derived_from edges from
a root whose RAW source matches, and admit inherited taint only where canonicalisation loses nothing about
the subject. Two copies of that rule would drift -- which is exactly how this surface came to disagree with
`forget_subject()`.

Also here: `dry_run` now carries `coverage`. The real run gained it earlier the same day and the preview
did not, so the one surface built for deciding WHETHER to erase was the one that did not state what the
erasure covers.
"""
from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from inspeximus import Inspeximus  # noqa: E402


def _two_person_store():
    st = Inspeximus(path=os.path.join(tempfile.mkdtemp(), "s.json"), receipts=True)
    st.remember("carol salary 50", key="p::carol", object="50", source={"doc": "hr/carol"})
    st.remember("dave salary 60", key="p::dave", object="60", source={"doc": "hr/dave"})
    return st


def _attributable(rep):
    return [f["id"] for f in rep["residue"] if f["kind"] == "subject_still_attributable"]


def test_a_ghost_subject_is_not_told_a_strangers_record_is_its_own():
    """THE defect, and the worse half: the `detail` string named the ghost, so the report read as
    though the store had a record for a subject it had never seen."""
    st = _two_person_store()
    assert _attributable(st.erasure_audit("hr/nobody-here")) == []


def test_a_completed_erasure_is_not_reported_as_residue():
    """The other direction. Carol's DSAR succeeded; dave's record shares the host, so the audit said
    it had failed."""
    st = _two_person_store()
    st.forget_subject("hr/carol", request_id="r1", basis="art17")
    assert _attributable(st.erasure_audit("hr/carol")) == []


def test_a_subject_that_really_is_still_there_is_still_reported():
    """CONTROL. If narrowing simply returned nothing, both tests above would pass while the audit had
    stopped auditing -- a guard that can never fire reports safe."""
    st = _two_person_store()
    st.forget_subject("hr/carol", request_id="r1", basis="art17")
    found = _attributable(st.erasure_audit("hr/dave"))
    assert len(found) == 1
    assert (st.erasure_audit("hr/dave")["residue"][0]["detail"]).startswith("still attributable to 'hr/dave'")


def test_the_audit_agrees_with_what_the_erasure_actually_did():
    """The two surfaces resolve the same subject the same way, because they share one implementation."""
    st = _two_person_store()
    res = st.forget_subject("hr/carol", request_id="r1", basis="art17")
    assert res["erased"] == 1, "the erasure itself must be narrow too"
    assert _attributable(st.erasure_audit("hr/carol")) == []
    assert len(_attributable(st.erasure_audit("hr/dave"))) == 1


def test_the_preview_states_what_the_erasure_would_cover():
    st = _two_person_store()
    dry = st.forget_subject("hr/carol", request_id="dry", dry_run=True)
    cov = dry["coverage"]
    assert cov["preview"] is True, "a rehearsal must be labelled as one"
    assert cov["unregistered"] is True and cov["external_targets"] == 0
    assert cov["complete"] is False, "nothing was contacted, so nothing can be complete"
    assert "untouched and unaccounted for" in cov["note"]


def test_the_preview_still_touches_nothing():
    """CONTROL. The worst possible outcome of adding a field to the preview would be a preview that
    stopped being one."""
    st = _two_person_store()
    before = [r["id"] for r in st.items]
    st.forget_subject("hr/carol", request_id="dry", dry_run=True)
    assert [r["id"] for r in st.items] == before
    assert st.forget_subject("hr/carol", request_id="r1", basis="art17")["erased"] == 1
