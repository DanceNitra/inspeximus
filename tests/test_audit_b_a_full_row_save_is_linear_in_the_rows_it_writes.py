"""AUDIT-B B-15: the row store's full-diff save must not compare every new or changed id with every other.

`sqlite_store.save` without `dirty` computes `added` and `changed` as LISTS and then asks, for every id
in the store, `k not in added and k not in changed`. A list membership test scans the list, so a save
that adds or rewrites every row is O(rows^2). Two ordinary paths do exactly that: migrating a JSON store
to rows (`migrate_from_json` saves every record as added) and moving a store to a new row encoding
(`rewrite_all`, requested on the first open after an upgrade). Measured 2026-09-27 on this machine:
rewrite_all took 0.04 s at 2,000 rows, 0.11 s at 4,000 and 0.34 s at 8,000, about 3x per doubling,
which puts a 67,165-row store near half a minute on its first open after an upgrade.

The counter is exact and independent of the machine: every id is a `str` subclass that counts its own
`__eq__` calls. A set lookup finds the identical object without calling `__eq__`; a list scan calls it
once per element it passes. The control proves the ids reach the comparison at all.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from inspeximus import sqlite_store as ss  # noqa: E402

N = 400


class CountedId(str):
    compared = 0

    def __eq__(self, other):
        CountedId.compared += 1
        return str.__eq__(self, other)

    __hash__ = str.__hash__


def _items():
    return [{"id": CountedId(f"id{i:05d}"), "text": f"record {i}", "ts": 1.0, "mtype": "episodic"}
            for i in range(N)]


@pytest.mark.xfail(strict=True, raises=AssertionError,
                   reason="B-15: list membership makes a full-diff save quadratic in added or changed rows")
def test_a_save_that_adds_or_rewrites_every_row_is_linear(tmp_path):
    p = str(tmp_path / "s.json")
    items = _items()
    CountedId.compared = 0
    snap = ss.save(p, items, {})["snapshot"]
    added_cmp = CountedId.compared
    CountedId.compared = 0
    res = ss.save(p, items, snap, rewrite_all=True)
    rewrite_cmp = CountedId.compared

    # CONTROL: both saves wrote every row, and the ids went through a comparison that can count.
    if res["changed"] != N or len(snap) != N:
        pytest.fail(f"control: {len(snap)} added and {res['changed']} rewritten, expected {N} each")
    CountedId.compared = 0
    _ = [CountedId("id00001") == x for x in ("id00001", "x")]
    if CountedId.compared != 2:
        pytest.fail("control: the counting id does not count")

    assert added_cmp <= 2 * N, f"saving {N} new rows made {added_cmp} id comparisons"
    assert rewrite_cmp <= 2 * N, f"rewriting {N} rows made {rewrite_cmp} id comparisons"
