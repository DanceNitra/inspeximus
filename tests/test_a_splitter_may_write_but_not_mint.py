"""A writer that cannot vouch for its output writes PROVISIONAL, and nothing retrieves it until confirmed.

WHERE THIS CAME FROM. yun520-1, on openclaw/openclaw#7707, on our measurement that a
period-and-capital sentence splitter is 30% wrong on hard text and turns "J. R. R. Tolkien" into
three fabricated claims:

    the splitter now gets to mint first-class records, which means the splitter is a trust boundary,
    and it is a worse one than the chunking it replaced (chunk boundaries are syntactic, sentence
    boundaries are semantic) ... The invariant both options share: the splitter must never be the
    sole authority that mints a record. Wherever the split happens, verification has to be able to
    say no to a sentence it can't confirm — including "J." as a standalone fact.

So a writer that cannot vouch for what it produced passes `provisional=True`. The record is stored,
keyed, lineage-stamped and auditable — and unretrievable until something confirms it.

THE POSITION OF THE GATE IS THE GUARANTEE, and it took two tries. Placed above the catch-all but
below the bitemporal branch, `recall(as_of=...)` returned provisional records: that branch returns
True before any status check runs. It is first in `_eligible` now. Hence the flag matrix below
rather than one happy-path assertion — the leak was on the fourth combination, not the first.
"""
from __future__ import annotations

import os
import tempfile
import time

import pytest

from inspeximus import Inspeximus

FRAGMENT = "R. R. Tolkien was born in Bloemfontein"
WHOLE = "J. R. R. Tolkien was born in Bloemfontein in 1892"


def _store() -> Inspeximus:
    return Inspeximus(path=os.path.join(tempfile.mkdtemp(), "s.json"))


def _id(x) -> str:
    return x if isinstance(x, str) else x["id"]


def _texts(hits) -> list[str]:
    return [h.get("text") or "" for h in (hits or [])]


# ------------------------------------------------------------- unretrievable on EVERY path, not one
@pytest.mark.parametrize("kw", [
    {},
    {"include_superseded": True},
    {"include_hubs": True},
    {"as_of": time.time() + 10},
    {"as_of": time.time() + 10, "include_superseded": True},
    {"as_of": time.time() + 10, "include_hubs": True, "include_superseded": True},
], ids=["default", "superseded", "hubs", "as_of", "as_of+sup", "as_of+hubs+sup"])
def test_a_provisional_record_is_never_retrievable(kw):
    ix = _store()
    ix.remember(FRAGMENT, mtype="semantic", provisional=True)
    assert not any("Bloemfontein" in t for t in _texts(ix.recall("Tolkien born", **kw))), \
        f"a provisional record surfaced under {kw}"


def test_as_of_cannot_resurrect_it():
    """The combination that actually leaked. A provisional record was never valid at ANY time —
    'was this true at T' has no meaning for something nobody confirmed."""
    ix = _store()
    ix.remember(FRAGMENT, mtype="semantic", provisional=True)
    for t in (time.time() - 3600, time.time(), time.time() + 3600):
        assert not any("Bloemfontein" in x for x in _texts(ix.recall("Tolkien born", as_of=t)))


# ------------------------------------------------------------------------ the queue and its verdicts
def test_it_is_stored_queued_and_carries_its_lineage():
    """Unretrievable is not the same as discarded: the point is that a verifier can see it and rule."""
    ix = _store()
    ix.remember(WHOLE, mtype="semantic")
    ix.recall("Tolkien")
    fid = _id(ix.remember(FRAGMENT, mtype="semantic", provisional=True, derived=True))
    q = ix.provisional()
    assert [x["id"] for x in q] == [fid]
    assert q[0]["text"] == FRAGMENT
    assert q[0]["derived_from"], "the fragment does not point back at the chunk it came from"


def test_confirming_makes_it_ordinary_and_records_who_vouched():
    ix = _store()
    fid = _id(ix.remember(FRAGMENT, mtype="semantic", provisional=True))
    r = ix.confirm(fid, by="corroborated against the parent chunk")
    assert r["confirmed"] is True and r["by"] == "corroborated against the parent chunk"
    assert any("Bloemfontein" in t for t in _texts(ix.recall("Tolkien born")))
    assert ix.provisional() == []


def test_confirmation_without_a_named_voucher_says_so():
    """'Confirmed' with no author reads as verification while asserting nothing — same failure as an
    optional reason nobody fills."""
    ix = _store()
    fid = _id(ix.remember(FRAGMENT, mtype="semantic", provisional=True))
    ix.confirm(fid)
    rec = [x for x in ix.items if x["id"] == fid][0]
    assert rec["confirmed_by"] == "unstated"


def test_discarding_keeps_it_out_for_good():
    """The verifier saying NO — which is the half that makes the queue worth having."""
    ix = _store()
    fid = _id(ix.remember(FRAGMENT, mtype="semantic", provisional=True))
    assert ix.discard_provisional(fid, basis="the splitter invented it; not in the source")["discarded"]
    assert ix.provisional() == []
    for kw in ({}, {"include_superseded": True}, {"as_of": time.time() + 10}):
        assert not any("Bloemfontein" in t for t in _texts(ix.recall("Tolkien born", **kw)))


def test_confirming_something_that_is_not_provisional_is_refused():
    ix = _store()
    ordinary = _id(ix.remember(WHOLE, mtype="semantic"))
    assert ix.confirm(ordinary)["confirmed"] is False
    assert ix.discard_provisional("nope")["discarded"] is False


# -------------------------------------------------------------- it must not take over a live key
def test_a_provisional_record_does_not_supersede_the_authoritative_value():
    """The failure that would make this worse than no feature: an unconfirmed fragment quietly
    replacing a verified fact on the same key."""
    ix = _store()
    ix.remember("Tolkien was born in 1892", key="tolkien.born", mtype="semantic")
    ix.remember("Tolkien was born in 1802", key="tolkien.born", mtype="semantic", provisional=True)
    hits = _texts(ix.recall("when was Tolkien born"))
    assert any("1892" in t for t in hits), f"the authoritative value vanished: {hits}"
    assert not any("1802" in t for t in hits), f"the unconfirmed value took the key: {hits}"


# ----------------------------------------------------------------------------- the positive control
def test_an_ordinary_write_is_untouched():
    """A gate that swallowed everything would pass every assertion above."""
    ix = _store()
    ix.remember(WHOLE, mtype="semantic")
    assert any("Bloemfontein" in t for t in _texts(ix.recall("where was Tolkien born")))
    assert ix.provisional() == []
    rec = [x for x in ix.items if WHOLE in (x.get("text") or "")][0]
    assert rec["status"] == "active" and "confirmed_by" not in rec
