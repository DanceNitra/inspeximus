"""The pack asks for the same report several times, and must compute it once while the store is still.

Measured on a 7,696-record store before this existed: 86 of the pack's 95.7 seconds were four calls
to `memory_report()` with identical output. The memo removes the repeats. What these tests guard is
the half that a cache gets wrong: the answer must not change, and a write between two calls must be
seen by the second one.
"""
from __future__ import annotations

import json

import pytest

from inspeximus import Inspeximus
from inspeximus.assessor_pack import _memoized, assessor_pack


@pytest.fixture()
def store(tmp_path):
    m = Inspeximus(str(tmp_path / "mem.json"), receipts=True)
    for i in range(6):
        m.remember("decision %d about the retention window for the audit log" % i, key="k%d" % i)
    m.remember("a correction of decision 0: the retention window is ninety days", key="k0")
    m.flush()
    return m


def _leaves(o, path=""):
    if isinstance(o, dict):
        for k, v in o.items():
            yield from _leaves(v, path + "/" + str(k))
    elif isinstance(o, list):
        for i, v in enumerate(o):
            yield from _leaves(v, path + "[%d]" % i)
    else:
        yield path, json.dumps(o, sort_keys=True, default=str)


def _differing(a, b):
    la, lb = dict(_leaves(a)), dict(_leaves(b))
    return {k for k in set(la) | set(lb) if la.get(k) != lb.get(k) and not k.startswith("/memo")}


def test_the_memo_changes_the_work_and_not_the_answer(tmp_path, store):
    # Some documents stamp the wall clock and hash themselves, so two honest packs of one store
    # differ in those fields. The memo may differ from a plain pack ONLY where two plain packs
    # already differ from each other.
    path = str(tmp_path / "mem.json")
    plain_1 = assessor_pack(Inspeximus(path, receipts=True), now=0.0, memo=False)
    plain_2 = assessor_pack(Inspeximus(path, receipts=True), now=0.0, memo=False)
    memo = assessor_pack(Inspeximus(path, receipts=True), now=0.0)
    clock = _differing(plain_1, plain_2)
    assert _differing(plain_1, memo) <= clock
    assert plain_1["memo"] == {"computed": {}, "reused": {}}

    # CONTROL: the comparison must be able to see a real difference outside the clock fields.
    memo["documents"]["technical_documentation"]["__planted__"] = "differs"
    assert not _differing(plain_1, memo) <= clock


def test_each_repeated_report_is_computed_once_and_reused(store):
    work = assessor_pack(store, now=0.0)["memo"]
    assert work["computed"].get("memory_report") == 1, work
    assert work["reused"].get("memory_report", 0) >= 2, (
        "the pack asks for memory_report more than once; if nothing was reused the memo did nothing")


def test_a_write_between_two_calls_is_seen_by_the_second(store):
    with _memoized(store) as work:
        before = store.supersession_report()
        store.remember("a correction of decision 1: the window is thirty days", key="k1")
        after = store.supersession_report()
    assert work["computed"]["supersession_report"] == 2, work
    assert before != after, "the write changed the store, so a report that did not change is stale"


def test_the_memo_is_gone_when_the_pack_is_done(store):
    with _memoized(store):
        assert "memory_report" in vars(store)
    assert "memory_report" not in vars(store), "the store must not keep a cache after the pack"


def test_editing_a_returned_report_cannot_poison_the_next_reader(store):
    with _memoized(store):
        first = store.pii_report()
        first["poisoned"] = True
        second = store.pii_report()
    assert "poisoned" not in second
