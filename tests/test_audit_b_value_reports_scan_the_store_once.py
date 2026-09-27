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


@pytest.mark.xfail(strict=True, raises=AssertionError,
                   reason="B-06: one full scan of the store per key in the value reports")
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
