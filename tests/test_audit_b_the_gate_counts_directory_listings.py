"""AUDIT-B, A->B-2: the perf gate counts directory listings, so per-record directory work fails on every OS.

3.15.2's merge-backup removal ran once per tombstone, and each run listed the store's directory twice:
erasing k records listed it 2k + 3 times. The gate's counters (items reads, str.lower calls) saw that only
on Windows, where the listed names were lowercased; CI's Linux gate stayed green. `dir_listings` counts
os.listdir and os.scandir inside the counted region. The erase arm runs at two sizes and must list the
same number of times at both: a count that grows with k is per-record work.
"""
import importlib.util
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture(scope="module")
def gate():
    sys.path.insert(0, ROOT)
    spec = importlib.util.spec_from_file_location("perf_gate_for_listings", os.path.join(ROOT, "perf", "gate.py"))
    g = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(g)
    return g


def _listings(gate, k, n):
    with gate._isolated_key_home():
        run = gate.w_erase(k, n)
        run()
    return run.inner["dir_listings"]


def test_an_erasure_lists_the_directory_the_same_number_of_times_for_2_and_40_records(gate):
    small, large = _listings(gate, 2, 60), _listings(gate, 40, 60)
    assert small > 0, "the control: the counter sees the listings an erasure does make"
    assert large == small, (f"erasing 40 records listed the store's directory {large} times, 2 records "
                            f"{small}: directory work per erased record")


def test_the_gate_carries_the_counter_on_both_erase_sizes(gate):
    assert {"erase_k200_n2000", "erase_k20_n2000"} <= set(gate.WORKLOADS)
    with gate.Counters() as c:
        os.listdir(ROOT)
        list(os.scandir(ROOT))
    assert c.as_dict()["dir_listings"] == 2
