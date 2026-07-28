"""An erasure result must say WHAT IT COVERED, measured -- not carry a constant disclaimer.

`forget_subject()` returned `{"erased": 2, "request_id": ..., "tombstones": 2}` and said nothing about the
world outside this store. Measured (research/probes/erasure_manifest_wired_cell.py, re-run 2026-07-28):

    unwired, store-native delete only   -> the app's own vector index still holds the data, 8/8
    wired to a registered target        -> 0/8, manifest complete, chain verifies
    wired but the wiring is BROKEN      -> 8/8 residue, and falsely-complete manifests 0/8

So the mechanism is sound and the default was not: a caller who registered nothing got a confident
`erased: 2` about a surface the library never looked at. The certificate and governance report did carry a
scope sentence, but the SAME sentence appears whether you wired every store or none -- a constant is not a
coverage report.

`coverage` now states the measured position, and `complete` is true only when at least one external target
was registered AND every one verified the data absent. It READS the manifest's own verdict rather than
recomputing it; an earlier version guessed the field names, iterated `targets` (a list of NAMES, not
records) and raised on the first real call.
"""
from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from inspeximus import Inspeximus  # noqa: E402
from inspeximus.deletion_manifest import ErasureTarget  # noqa: E402


class FakeIndex(ErasureTarget):
    """The application's own vector index -- the store inspeximus does NOT manage."""

    def __init__(self, name="app-vector-index", leaky=False):
        self.name, self.leaky, self.rows = name, leaky, {}

    def seed(self, subject, values):
        self.rows[subject] = list(values)

    def erase(self, subject):
        if not self.leaky:
            self.rows.pop(subject, None)
        return {"erased": 0 if self.leaky else 1}

    def still_recoverable(self, subject, values):
        return any(v in (self.rows.get(subject) or []) for v in values)


def _store(**kw):
    d = tempfile.mkdtemp()
    st = Inspeximus(path=os.path.join(d, "s.json"), receipts=True, **kw)
    st.remember("alice's address is 12 Rose Lane", key="a::addr", object="12 Rose Lane",
                source={"doc": "crm/alice"})
    return st


def test_an_unwired_erasure_does_not_read_as_complete():
    """THE default case. Nothing registered, so nothing outside this store was even looked at."""
    st = _store()
    cov = st.forget_subject("crm/alice", request_id="D1", basis="GDPR Art.17")["coverage"]
    assert cov["complete"] is False
    assert cov["unregistered"] is True
    assert cov["external_targets"] == 0
    # the note must name the remedy, not merely disclaim -- advice that cannot be acted on is no advice
    assert "register_erasure_target" in cov["note"]


def test_a_wired_erasure_that_verifies_reads_as_complete():
    st = _store()
    idx = FakeIndex()
    st.register_erasure_target(idx)
    idx.seed("crm/alice", ["12 Rose Lane"])
    cov = st.forget_subject("crm/alice", request_id="D2", basis="GDPR Art.17")["coverage"]
    assert cov["complete"] is True
    assert cov["unregistered"] is False
    assert "unconfirmed" not in cov
    assert idx.still_recoverable("crm/alice", ["12 Rose Lane"]) is False


def test_a_target_that_does_not_actually_erase_cannot_produce_a_clean_result():
    """THE test that makes this a guarantee rather than a label. A broken integration must not certify."""
    st = _store()
    idx = FakeIndex(leaky=True)
    st.register_erasure_target(idx)
    idx.seed("crm/alice", ["12 Rose Lane"])
    cov = st.forget_subject("crm/alice", request_id="D3", basis="GDPR Art.17")["coverage"]
    assert cov["complete"] is False
    assert "app-vector-index" in cov["unconfirmed"]
    assert "NOT complete" in cov["note"]
    # and the data really is still there -- otherwise this test proves nothing about the leak
    assert idx.still_recoverable("crm/alice", ["12 Rose Lane"]) is True


def test_one_leaky_target_among_several_still_fails_the_whole_erasure():
    """Partial coverage is not coverage: a DSAR answer is about the person, not about one store."""
    st = _store()
    good, bad = FakeIndex("search-index"), FakeIndex("analytics-copy", leaky=True)
    st.register_erasure_target(good)
    st.register_erasure_target(bad)
    for t in (good, bad):
        t.seed("crm/alice", ["12 Rose Lane"])
    cov = st.forget_subject("crm/alice", request_id="D4")["coverage"]
    assert cov["complete"] is False
    assert cov["unconfirmed"] == ["analytics-copy"]


def test_the_store_level_numbers_are_unchanged():
    """CONTROL. Adding coverage must not disturb what the call already reported."""
    st = _store()
    res = st.forget_subject("crm/alice", request_id="D5", basis="GDPR Art.17")
    assert res["erased"] == 1 and res["tombstones"] == 1 and res["request_id"] == "D5"
    assert res["coverage"]["store"] is True


def test_coverage_is_present_on_every_erasure_path():
    """A field the caller can rely on only if it is always there -- including when nothing matched."""
    st = _store()
    res = st.forget_subject("crm/nobody-here", request_id="D6")
    assert res["erased"] == 0
    assert "coverage" in res, "an erasure that matched nothing still has to state its coverage"
