"""`coverage` shipped on forget_subject alone. Every path that ERASES has to carry it.

Found by auditing the same day's own work, which is the only reason it was found: the field went in a few
hours after a commit message describing this exact mistake -- a fix at one caller while the siblings keep
the gap, which `_resolve_subject`'s docstring already records 1.53.0 making before either of us.

It matters more than a missing field usually would. A caller can only rely on something that is ALWAYS
there, and here its ABSENCE reads as "nothing to report" rather than "nobody looked" -- on the surface
that answers "did we erase this person".

The boundary is DELETION, not destructiveness: retract_lineage demotes records to superseded and removes
nothing, so it has no external reach to report and correctly has no coverage. Asserted below so the line
is deliberate rather than an oversight nobody noticed.
"""
from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from inspeximus import Inspeximus  # noqa: E402
from inspeximus.compliance import retention_sweep  # noqa: E402
from inspeximus.deletion_manifest import ErasureTarget  # noqa: E402


class Idx(ErasureTarget):
    """The app's own index. `mode='lies'` reports success and claims the data is gone while keeping it."""

    def __init__(self, name="app-index", mode="honest"):
        self.name, self.mode, self.rows = name, mode, {}

    def seed(self, s, vals):
        self.rows[s] = list(vals)

    def erase(self, subject):
        if self.mode == "honest":
            self.rows.pop(subject, None)
        return {"erased": 1}

    def still_recoverable(self, subject, values):
        if self.mode == "lies":
            return False
        return any(v in (self.rows.get(subject) or []) for v in values)


def _store():
    d = tempfile.mkdtemp()
    st = Inspeximus(path=os.path.join(d, "s.json"), receipts=True, pii_detect=True)
    st.remember("alice's email is alice@corp.com", key="a::mail", object="alice@corp.com",
                source={"doc": "crm/alice"})
    return st


def test_forget_subject_reports_coverage():
    assert "coverage" in _store().forget_subject("crm/alice", request_id="r")


def test_forget_by_id_reports_coverage():
    st = _store()
    assert "coverage" in st.forget(ids=[st.items[0]["id"]], request_id="r")


def test_forget_pii_reports_coverage():
    assert "coverage" in _store().forget_pii(subject="crm/alice", request_id="r")


def test_forget_pii_reports_coverage_even_when_nothing_matched():
    """The empty result is still an answer a DSAR reply is built on."""
    assert "coverage" in _store().forget_pii(subject="crm/nobody", request_id="r")


def test_retention_sweep_reports_coverage():
    st = _store()
    res = retention_sweep(st, 0.0, pii_only=False, apply=True, request_id="r")
    assert res["erased"] >= 1, res
    assert "coverage" in res


def test_retract_lineage_has_no_coverage_because_it_deletes_nothing():
    """The boundary, asserted so it stays deliberate. It demotes; there is no external reach to report."""
    st = _store()
    res = st.retract_lineage("crm/alice")
    assert "coverage" not in res
    assert any(r.get("status") == "superseded" for r in st.items), "it should demote, not delete"


def test_coverage_says_who_actually_checked():
    """`complete` means every target SAID the data is gone -- the library cannot look inside a store it
    was handed an interface to. Measured: a target that erases nothing but reports success and returns
    still_recoverable=False gets complete=True while the data sits there. That is the trust boundary, so
    the receipt has to name it rather than let 'verified' imply we looked."""
    st = _store()
    idx = Idx(mode="lies")
    st.register_erasure_target(idx)
    idx.seed("crm/alice", ["alice@corp.com"])
    cov = st.forget_subject("crm/alice", request_id="r")["coverage"]
    assert cov["complete"] is True, "the manifest believes its targets; that is the premise being labelled"
    assert idx.still_recoverable("crm/alice", ["alice@corp.com"]) is False, "the target lied, as designed"
    assert "alice@corp.com" in idx.rows["crm/alice"], "and the data really is still there"
    assert cov.get("attested_by_targets") is True
    assert "still_recoverable" in cov.get("verification", "")


def test_an_honest_target_still_reads_complete():
    """CONTROL. Naming the trust boundary must not turn every real erasure into a hedge."""
    st = _store()
    idx = Idx()
    st.register_erasure_target(idx)
    idx.seed("crm/alice", ["alice@corp.com"])
    cov = st.forget_subject("crm/alice", request_id="r")["coverage"]
    assert cov["complete"] is True and not cov.get("unconfirmed")
    assert idx.rows.get("crm/alice") in (None, [])
