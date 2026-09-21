"""3.5.1: an edit this handle made to an existing record survives the merge with a changed file.

Crew OS, 2026-09-21: 35 retire() calls in a batch, a fresh handle per call, beside a parallel run;
three keys stayed active though every call returned retired=1. Reproduced here across processes
before the fix (35 retires over 8 processes beside 200 concurrent keyed writes: 6 still active),
and pinned deterministically with two handles: the row-store merge let disk win for every id both
sides held, so a status change this handle made to a record a peer had not touched was dropped
when the peer's append changed the file first. A keyed supersession survived by accident, because
the merge demotes the older of two active values; a retirement has no newer value.
"""
import os

import pytest

from inspeximus import Inspeximus


def _rows(path):
    return Inspeximus(path)._rows_available()


def test_a_retirement_survives_a_peer_append_that_landed_first(tmp_path):
    path = str(tmp_path / "s.json")
    a = Inspeximus(path)
    a.remember("layer", key="crew::L", object="v1")
    a.flush()
    if not _rows(path):
        pytest.skip("row store only; the JSON path refuses the save instead")
    b = Inspeximus(path)                      # a peer, loaded after the layer exists
    a2 = Inspeximus(path)                     # the retiring handle, loaded now
    b.remember("something else", key="crew::other", object="x")
    b.flush()                                 # the file changes under a2
    res = a2.retire("crew::L", reason="ended")
    assert res["retired"] == 1
    a2.flush()                                # merges with disk; the retirement must not lose to disk
    fresh = Inspeximus(path)
    recs = [r for r in fresh.items if r.get("key") == "crew::L"]
    assert [r["status"] for r in recs] == ["superseded"], recs
    assert (recs[0].get("meta") or {}).get("superseded_by_policy") == "retired"
    assert fresh.current("crew::other")["text"] == "something else", "the peer's write is kept too"


def test_a_peer_edit_to_a_record_this_handle_only_read_is_kept(tmp_path):
    """The control: a record this handle did NOT edit still takes the disk copy."""
    path = str(tmp_path / "s.json")
    a = Inspeximus(path)
    a.remember("layer", key="crew::L", object="v1")
    a.flush()
    if not _rows(path):
        pytest.skip("row store only")
    reader = Inspeximus(path)                 # holds crew::L active, never edits it
    peer = Inspeximus(path)
    peer.retire("crew::L", reason="peer ended it")
    peer.flush()
    reader.remember("unrelated", key="crew::U", object="u")
    reader.flush()                            # merges; must not resurrect crew::L from its stale copy
    fresh = Inspeximus(path)
    assert [r["status"] for r in fresh.items if r.get("key") == "crew::L"] == ["superseded"]


def test_a_batch_of_retirements_with_a_fresh_handle_each_beside_a_writer(tmp_path):
    """The Crew OS shape in-process: 35 retires, a handle per call, a writer appending between them."""
    path = str(tmp_path / "s.json")
    seed = Inspeximus(path)
    for i in range(35):
        seed.remember("p %d" % i, key="crew::P%d" % i, object="v1")
    seed.flush()
    if not _rows(path):
        pytest.skip("row store only")
    writer = Inspeximus(path)
    for i in range(35):
        h = Inspeximus(path)                  # loaded before the writer's next append
        writer.remember("w %d" % i, key="crew::W%d" % i, object="v")
        writer.flush()
        assert h.retire("crew::P%d" % i, reason="bulk")["retired"] == 1
        h.flush()
    fresh = Inspeximus(path)
    still = [r["key"] for r in fresh.items if r.get("key", "").startswith("crew::P") and r["status"] == "active"]
    assert still == [], still
    assert sum(1 for r in fresh.items if r.get("key", "").startswith("crew::W") and r["status"] == "active") == 35
