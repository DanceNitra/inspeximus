"""Swapping `active` and `superseded` on disk resurrects a value that was corrected away.

WHY `status_sha256` DOES NOT COVER THIS, by construction. It commits the SERVING CLASS, and folds
`active` and `superseded` into one class deliberately, so consolidation — which moves dozens of
records between them in a single pass — does not churn the receipt chain. That was the right call
and it left this hole: measured 2026-08-15, swapping the two statuses left the store serving the
corrected-away value as current, hiding the correction, with verify_writes, verify_attribution,
verify_bundle and bind_content all reporting clean.

The decisive control is that the SAME edit on a `provisional` record IS caught, which makes this a
coverage hole in that commitment rather than a missing mechanism.

THE FIX AND WHY IT COSTS NOTHING. `retires` is a fact about the WRITE, not about current state —
this write did retire those ids, permanently and unrevisably. So unlike a status it binds for life
and needs no amendment, and it adds zero receipts. It lives in the RECEIPT, not on the record: the
first version stored it as `rec["retires"]` and a test caught that within the run, because it holds
random surrogate ids, so two identically-built stores stopped comparing equal.

HONEST LIMIT, pinned by a test below so nobody reads more into it than it says: this catches
RESURRECTION, not CONCEALMENT. Demoting the current record to hide it still passes, because closing
that needs every retiring path to declare itself and `consolidate()` and capacity eviction retire
records without emitting a receipt at all.
"""
from __future__ import annotations

import json
import os
import tempfile

from inspeximus import Inspeximus


def _corrected():
    d = tempfile.mkdtemp()
    p = os.path.join(d, "s.json")
    ix = Inspeximus(path=p, receipts=True)
    ix.remember("the deploy key rotates every 90 days", key="rot", object="90d")
    ix.remember("the deploy key rotates every 30 days", key="rot", object="30d")
    ix.flush()
    return p, ix


def _edit(p, fn):
    rows = json.load(open(p, encoding="utf-8"))
    fn(rows)
    json.dump(rows, open(p, "w", encoding="utf-8"), ensure_ascii=False)


def test_the_correction_cannot_be_undone_on_disk():
    p, ix = _corrected()
    assert ix.verify_writes() == (True, [])
    _edit(p, lambda rows: [r.update(status=("active" if r["status"] == "superseded" else "superseded"))
                           for r in rows])
    ok, problems = Inspeximus(path=p, receipts=True).verify_writes()
    assert not ok and any("RETIRED this record and it is ACTIVE again" in x for x in problems), problems


def test_the_retirement_is_recorded_in_the_receipt_not_on_the_record():
    """Both halves matter. In the receipt: it is inside the receipt hash, so trimming the list breaks
    the chain link rather than shrinking the check. NOT on the record: it holds random ids, and a
    record shape that varies run to run is not a record shape."""
    _p, ix = _corrected()
    assert [rc["commit"].get("retires") for rc in ix._receipts][-1], "the retirement was not committed"
    assert not any("retires" in r for r in ix.items), "the receipt's fact leaked onto the record"


def test_a_keyless_write_retires_nothing_and_still_gets_a_receipt():
    """The path that broke first: `_retired` was bound only inside the keyed branch, so every
    keyless write raised UnboundLocalError at receipt time -- 250 tests, from one missing default."""
    d = tempfile.mkdtemp()
    ix = Inspeximus(path=os.path.join(d, "s.json"), receipts=True)
    ix.remember("a note with no key at all")
    assert ix.verify_writes() == (True, [])
    assert ix._receipts[-1]["commit"]["retires"] == []


def test_ordinary_supersession_adds_no_receipts():
    """The property the whole design exists to keep. If this regresses, the commitment has moved from
    a fact about the write to a fact about state, and consolidation will flood the chain."""
    d = tempfile.mkdtemp()
    ix = Inspeximus(path=os.path.join(d, "s.json"), receipts=True)
    for i in range(9):
        ix.remember(f"the value is now v{i}", key="churn", object=f"v{i}")
    assert len(ix._receipts) == 9, f"{len(ix._receipts)} receipts for 9 writes"
    assert ix.verify_writes() == (True, [])


def test_the_concealment_half_is_documented_as_open_not_claimed_as_closed():
    """A test that pins a KNOWN GAP, so the guarantee cannot quietly be read as wider than it is.

    If someone closes concealment later, this test fails and they update it deliberately -- which is
    the point. A limit nobody wrote down is a limit that gets forgotten and then over-claimed.
    """
    p, _ix = _corrected()
    _edit(p, lambda rows: [r.update(status="superseded") for r in rows if r["status"] == "active"])
    ix2 = Inspeximus(path=p, receipts=True)
    assert [r for r in ix2.items if r["status"] == "active"] == []
    ok, _problems = ix2.verify_writes()
    assert ok is True, (
        "concealment is now DETECTED -- good. Update this test and the HONEST LIMIT comments in "
        "verify_writes and _supersede_by_key, which currently tell readers it is not.")
