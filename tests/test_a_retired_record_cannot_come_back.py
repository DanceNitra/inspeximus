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

BOTH DIRECTIONS ARE CLOSED NOW, and the second one cost something. `retires` catches un-retiring an
old record and is free. CONCEALMENT -- hiding the current record instead -- needed every retirement
path to declare itself, which for the five that have no "newer" record to hang it on means one
receipt each (`_declare_retired`). Plus `born_status`, so a record retired ON ARRIVAL by the echo,
objectless or back-fill guards is not mistaken for a hidden one.

The test that pinned the concealment gap as OPEN is the one that told me it had closed: it asserted
`ok is True` and carried a message telling whoever closed it to come here. Both false-alarm controls
below are load-bearing -- the first version of the skip condition tested "born recallable" instead of
"born live", and since `superseded` IS in `_RECALLABLE`, every honest echo was reported as
concealment.
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


def test_the_concealment_half_is_closed_too():
    """The mirror image: hide the CURRENT record instead of un-retiring an old one.

    This test previously PINNED THE GAP AS OPEN -- it asserted `ok is True` and carried an error
    message telling whoever closed it to come here and update it. That is what happened, in the same
    session: `_declare_retired` gives every legitimate retirement path a receipt, and `born_status`
    tells a record retired ON ARRIVAL from one that was live and got hidden. So the assertion is
    inverted now, deliberately and in writing, rather than a limit quietly outgrowing its comment.
    """
    p, _ix = _corrected()
    _edit(p, lambda rows: [r.update(status="superseded") for r in rows if r["status"] == "active"])
    ix2 = Inspeximus(path=p, receipts=True)
    assert [r for r in ix2.items if r["status"] == "active"] == []
    ok, problems = ix2.verify_writes()
    assert not ok and any("hidden" in x for x in problems), problems


def test_a_record_retired_on_arrival_is_not_reported_as_hidden():
    """The false-alarm control that makes the check usable. The echo guard, the objectless guard and
    the back-fill rule all retire the INCOMING record, so a store doing ordinary work is full of
    records that are `superseded` and were never live. `born_status` is what separates them; without
    it this check would fire on every honest store that ever rejected an echo."""
    d = tempfile.mkdtemp()
    ix = Inspeximus(path=os.path.join(d, "s.json"), receipts=True)
    ix.remember("the rate is 5%", key="rate", object="5")
    ix.remember("the rate is 7%", key="rate", object="7")
    ix.remember("the rate is 5%", key="rate", object="5")      # an echo: retired on arrival
    assert any(r["status"] == "superseded" for r in ix.items)
    assert ix.verify_writes() == (True, [])


def test_ordinary_housekeeping_does_not_trip_it():
    """The other false-alarm control: a keep-budget demotion is legitimate and now declares itself.
    If `_declare_retired` were dropped from that path, this fires."""
    d = tempfile.mkdtemp()
    ix = Inspeximus(path=os.path.join(d, "s.json"), receipts=True)
    for i in range(12):
        ix.remember(f"an unrelated observation number {i} worth keeping around")
    ix.consolidate(keep=4)
    assert ix.verify_writes() == (True, [])
