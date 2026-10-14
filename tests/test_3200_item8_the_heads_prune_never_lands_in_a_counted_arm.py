"""3.20, backlog item 8: the heads prune runs on a wall clock, so the perf gate pins it for every counted region.

THE DEFECT. `Inspeximus._prune_heads` lists the key home's heads directory at most once per 600 s per process. The perf
gate runs all its arms in one process, so whether that one listing fell inside an arm depended on how long the arms
before it took: recommit_n2000 counted dir_listings 1 on a slow runner (CI 38545778386 on release/3.19.1, 600 s after
the erase arms began) and 0 on a fast one (CI 38526943402, 517 s). Measured on an export of 82b882bc: the clock
expired before the arm gave 1 in 2 of 2 runs, a fresh one 0 in 2 of 2.

WHAT IS PINNED HERE. (1) The prune itself, which no test covered: it lists once when the interval has passed and not
again within it, trims the oldest heads down to half the cap, and keeps the newest. (2) The gate's `Counters` makes the
counted region start "just pruned" and puts the clock back afterwards, so recommit counts 0 listings even with the
clock expired before it.
"""
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "perf"))
sys.path.insert(0, ROOT)

import gate  # noqa: E402
from inspeximus import core  # noqa: E402
from inspeximus.core import Inspeximus  # noqa: E402


def _heads(d, n, age_s):
    os.makedirs(d, exist_ok=True)
    now = time.time()
    for i in range(n):
        p = os.path.join(d, "h%04d.json" % i)
        with open(p, "w") as fh:
            fh.write("{}")
        os.utime(p, (now - age_s - (n - i), now - age_s - (n - i)))
    return sorted(os.listdir(d))


def test_the_prune_lists_once_per_interval_and_trims_the_oldest_to_half(tmp_path, monkeypatch):
    d = str(tmp_path / "heads")
    names = _heads(d, 12, age_s=1000)
    fresh = os.path.join(d, "zzzz_written_now.json")
    with open(fresh, "w") as fh:
        fh.write("{}")
    listings = []
    real = core.os.listdir                       # `core.os` IS `os`: this test lists through `real`, so it counts nothing
    monkeypatch.setattr(core.os, "listdir", lambda p: listings.append(p) or real(p))
    monkeypatch.setattr(Inspeximus, "_heads_pruned_at", 0.0)
    Inspeximus._prune_heads(d, max_files=8, interval_s=600.0)
    assert listings == [d], "the prune listed the heads directory once"
    left = sorted(real(d))
    assert len(left) == 8 // 2, left
    assert os.path.basename(fresh) in left, "the newest head was removed"
    assert left[:-1] == names[-(8 // 2 - 1):], "the oldest went first"
    Inspeximus._prune_heads(d, max_files=8, interval_s=600.0)
    assert listings == [d], "a second prune within the interval listed again"


def test_the_prune_under_the_cap_lists_and_removes_nothing(tmp_path, monkeypatch):
    d = str(tmp_path / "heads")
    names = _heads(d, 5, age_s=1000)
    monkeypatch.setattr(Inspeximus, "_heads_pruned_at", 0.0)
    Inspeximus._prune_heads(d, max_files=8, interval_s=600.0)
    assert sorted(os.listdir(d)) == names


def test_recommit_counts_no_listing_even_when_the_prune_clock_has_expired(monkeypatch):
    monkeypatch.setattr(Inspeximus, "_heads_pruned_at", 0.0)      # what a run 600 s after the first prune sees
    with gate._isolated_key_home():
        run = gate.w_recommit(200)
        run()
    assert run.inner["recommitted"] == 200, "CONTROL: the arm did its work"
    assert run.inner["dir_listings"] == 0, "the heads prune landed inside the counted arm"
    assert Inspeximus._heads_pruned_at == 0.0, "the gate did not put the prune clock back"
