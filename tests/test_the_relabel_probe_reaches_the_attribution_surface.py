"""`verify_attribution` exists to catch a RELABEL, and no probe ever performed one.

The one probe registered against this surface deleted a record. Deletion lands in `missing`, which
`ok` deliberately excludes, because a store that has ever honoured an erasure request has records in
its receipt chain and not in its store, and that is correct rather than suspicious. So the probe
could only ever score SUMMARY_HIDES_DETAIL, and the surface stayed undemonstrated on real data.

The attack the surface was built for is the one it was never shown surviving: rewriting a record's
canonical source after its attribution was committed. `k`, the influence budget, the influence gate,
and slash are all keyed on that source, so a silent relabel voids every one of them at once.

Measured on our own store on 2026-08-29: 619 records, 619 receipts, nothing relabeled, nothing
uncommitted. A store in that state can host the probe, and `verify_attribution` folds `relabeled`
into `ok`, so a working surface reports the corruption and scores NOTICED.
"""
from __future__ import annotations

import os
import tempfile

import pytest

from inspeximus import Inspeximus


def _store_with_receipts(n=3):
    d = tempfile.mkdtemp()
    ix = Inspeximus(path=os.path.join(d, "store.json"), receipts=True)
    for i in range(n):
        ix.remember("fact number %d" % i, key="k%d" % i, object=str(i),
                    source={"doc": "https://example.com/doc-%d" % i})
    ix.flush()
    return ix


def _probe(report, name):
    return next((p for p in report["probes"] if p.get("probe") == name), None)


def test_a_relabel_probe_is_registered_against_verify_attribution():
    ix = _store_with_receipts()
    p = _probe(ix.audit_the_audits(), "source_relabeled_after_receipt")
    assert p is not None, (
        "verify_attribution had no probe performing the attack it exists to catch")
    assert p["surface"] == "verify_attribution"


def test_the_relabel_is_noticed_and_reaches_the_store_tier():
    """The point: this surface becomes demonstrable on real data, not only on a fixture."""
    ix = _store_with_receipts()
    r = ix.audit_the_audits()
    p = _probe(r, "source_relabeled_after_receipt")
    assert p["outcome"] == "NOTICED", (
        "a rewritten canonical source must flip `ok`; got %r" % (p,))
    assert p["tier"] == "your store", (
        "the whole gap was that this surface was never exercised on the caller's own data")
    assert "verify_attribution" in r["surfaces"]["demonstrated_on_your_store"]


# ───────────────────────────────────────────── the controls
def test_the_probe_starts_from_a_clean_verdict():
    """CONTROL. A surface already unhappy before the corruption proves nothing by reacting."""
    ix = _store_with_receipts()
    p = _probe(ix.audit_the_audits(), "source_relabeled_after_receipt")
    assert p.get("clean_before") is True, (
        "the corrupt-case reaction only means something if the clean case passed; got %r" % (p,))
    assert p.get("clean_after") is False


def test_the_surface_itself_still_passes_on_the_untouched_store():
    """CONTROL. If the probe leaked its corruption into the live store, this fails."""
    ix = _store_with_receipts()
    ix.audit_the_audits()
    r = ix.verify_attribution()
    assert r["ok"] is True, (
        "audit_the_audits must corrupt a COPY; the live store came back %r" % (r,))
    assert r["relabeled"] == []


def test_the_deletion_probe_still_reports_the_gap_it_was_built_for():
    """CONTROL. Adding the relabel probe must not retire the finding the old one carries.

    `record_deleted_after_receipt` scores SUMMARY_HIDES_DETAIL on purpose: `ok` stays true while
    `missing` fills. That is a real reporting gap and it keeps its own probe.
    """
    ix = _store_with_receipts()
    p = _probe(ix.audit_the_audits(), "record_deleted_after_receipt")
    assert p is not None, "the deletion probe was removed rather than joined"
    assert p["outcome"] == "SUMMARY_HIDES_DETAIL"
