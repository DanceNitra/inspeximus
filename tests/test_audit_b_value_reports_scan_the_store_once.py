"""AUDIT-B B-06: the per-key value reports must look up each key's current record without rescanning
the store for every key.

`_short_values_suppression_cannot_see` (inside `supersession_report`, which `memory_report` calls) and
`_retired_values` (inside `recall(suppress_stale_values=True)`) group the records by key and then call
`_current_active(key)` once per key. `_current_active` is a linear scan of every record, so each report
is O(keys x records).

Measured 2026-09-27 on a copy of the MCP store (10,934 records): 6,674 `_current_active` calls and
40.5 s (profiled) inside `supersession_report`, which `memory_report` runs only to read `by_policy`.

The counter is the number of `_current_active` calls a report makes. The control proves the fixture
reaches both code paths: the report names the short-value key, and suppression drops the stale record.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import inspeximus.core as core  # noqa: E402
from inspeximus import Inspeximus  # noqa: E402

K = 40


@pytest.fixture
def store(tmp_path, monkeypatch):
    for k in [k for k in os.environ if k.startswith("INSPEXIMUS_")]:
        monkeypatch.delenv(k)
    m = Inspeximus(str(tmp_path / "s.json"))
    for i in range(K):
        m.remember(f"the office for team {i} is in Vienna", key=f"office{i}", object="Vienna")
        m.remember(f"the office for team {i} is in Prague", key=f"office{i}", object="Prague")
    m.remember("the currency is EUR", key="currency", object="EUR")
    m.remember("the currency is GBP", key="currency", object="GBP")
    m.remember("team 3 still works from the Vienna office", tags=["note"])
    calls = []
    real = core.Inspeximus._current_active

    def counting(self, key):
        calls.append(key)
        return real(self, key)

    monkeypatch.setattr(core.Inspeximus, "_current_active", counting)
    return m, calls


def test_value_reports_do_not_scan_the_store_once_per_key(store):
    m, calls = store
    rep = m.supersession_report()
    report_calls = len(calls)
    del calls[:]
    retired = m._retired_values()
    retired_calls = len(calls)
    del calls[:]
    m.recall("which office does team 3 work from", k=5, suppress_stale_values=True)
    recall_calls = len(calls)
    del calls[:]

    # CONTROL: both reports ran on real data, or a zero below would measure a path that found nothing.
    if rep["values_too_short_to_suppress"]["keys"] != ["currency"]:
        pytest.fail(f"control: short-value keys {rep['values_too_short_to_suppress']['keys']}, "
                    f"expected ['currency']")
    if len(retired) != K or any(r[0][0][0] != "Vienna" for r in retired):
        pytest.fail(f"control: {len(retired)} keys with a retired value, expected {K} holding 'Vienna'")

    assert report_calls == 0, f"supersession_report made {report_calls} full-store scans for {K + 1} keys"
    assert retired_calls == 0, f"_retired_values made {retired_calls} full-store scans for {K + 1} keys"
    assert recall_calls == 0, f"recall(suppress_stale_values=True) made {recall_calls} full-store scans"


class _ScanPerKey(dict):
    """The index answered by the old per-key scan, so a report can be computed both ways."""

    def __init__(self, store):
        super().__init__()
        self._store = store

    def get(self, k, default=None):
        r = core.Inspeximus._current_active(self._store, k)
        return default if r is None else r


def _both_ways(m, fn):
    fast = fn()
    real = core.Inspeximus._current_active_index
    core.Inspeximus._current_active_index = lambda self: _ScanPerKey(self)
    try:
        slow = fn()
    finally:
        core.Inspeximus._current_active_index = real
    return fast, slow


def test_the_index_gives_the_answer_the_scan_gave(tmp_path, monkeypatch):
    """Same answer as one `_current_active` per key, on the shapes where an index could drift from a
    scan: two active records under one key (the FIRST wins), a non-string key, a key whose only record
    is superseded, and a tenant-bound handle that must not see another tenant's current record."""
    for k in [k for k in os.environ if k.startswith("INSPEXIMUS_")]:
        monkeypatch.delenv(k)
    p = str(tmp_path / "t.json")
    m = Inspeximus(p)
    for i in range(6):
        m.remember(f"the city for {i} is Vienna", key=f"city{i}", object="Vienna")
        m.remember(f"the city for {i} is Brno", key=f"city{i}", object="Brno")
    m.remember("the code is EU", key="code", object="EU")
    m.remember("the code is UK", key="code", object="UK")
    m.flush()
    rows = m._items
    # a second ACTIVE record under an existing key, and a record whose key is an int
    extra = dict(rows[-1]); extra["id"] = "dup-active"; extra["object"] = "US"; extra["text"] = "the code is US"
    num = dict(rows[0]); num["id"] = "int-key"; num["key"] = 7; num["status"] = "active"
    m._items = list(rows) + [extra, num]
    for fn in (lambda: m.supersession_report(), lambda: m._retired_values()):
        fast, slow = _both_ways(m, fn)
        assert fast == slow

    # TENANTS: two bound handles write the same keys into one file. Each bound handle must see its own
    # current record; the unbound admin view sees both, and the first in store order wins.
    tp = str(tmp_path / "ten.json")
    for tenant, city in (("a", "Vienna"), ("b", "Brno")):
        h = Inspeximus(tp, tenant=tenant)
        h.remember(f"the office is in {city}", key="office", object=city)
        h.remember(f"the office moved to {city}-West", key="office", object=f"{city}-West")
        h.remember("the tier is EU", key="tier", object="EU")
        h.remember("the tier is UK", key="tier", object="UK")
        h.flush()
    seen = set()
    for tenant in ("a", "b", None):
        h = Inspeximus(tp, tenant=tenant) if tenant else Inspeximus(tp)
        for fn in (lambda: h.supersession_report(), lambda: h._retired_values()):
            fast, slow = _both_ways(h, fn)
            assert fast == slow, tenant
        seen.add(repr(h._retired_values()))
    if len(seen) != 3:
        pytest.fail(f"control: the three views should differ, got {len(seen)} distinct answers")
