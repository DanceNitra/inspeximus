"""`--shard i/n` must split the suite into n disjoint pieces whose union is the whole suite.

A shard that dropped a test would let CI report green on a suite it never ran, and a test in two
shards would be counted twice against the total. Both are checked on real collections. The cited-probe
file is one xdist group on purpose (its per-probe budgets assume one worker), so it must land in ONE
shard whole; the spread is checked on ordinary files.
"""
from __future__ import annotations

import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ORDINARY = ["tests/test_raise_on_block_turns_a_blocked_write_into_an_error.py",
            "tests/test_the_assessor_pack_computes_each_report_once.py",
            "tests/test_retire_ends_a_key_with_no_replacement.py",
            "tests/test_a_blocked_write_says_so_on_every_surface_and_retire_names_its_status.py"]
GROUPED = "tests/test_probes_cited_by_docs.py"


def _collect(targets, *extra):
    out = subprocess.run([sys.executable, "-m", "pytest"] + list(targets) +
                         ["--collect-only", "-q", "-n", "0", "-p", "no:cacheprovider"] + list(extra),
                         cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace").stdout
    return {ln.strip() for ln in out.splitlines() if "::" in ln}


def test_four_shards_are_disjoint_and_cover_the_files():
    whole = _collect(ORDINARY)
    parts = [_collect(ORDINARY, "--shard", "%d/4" % i) for i in range(4)]
    assert len(whole) >= 20, "CONTROL: too few tests for the partition to mean anything"
    assert set().union(*parts) == whole
    assert sum(len(p) for p in parts) == len(whole), "a test landed in two shards"
    assert all(parts[:3]), "an empty shard means the key does not spread the tests"
    assert not parts[3], "the last shard is reserved for the cited_probes group"


def test_a_declared_group_stays_in_one_shard():
    whole = _collect([GROUPED])
    parts = [_collect([GROUPED], "--shard", "%d/4" % i) for i in range(4)]
    assert whole and set().union(*parts) == whole
    cited = {t for t in whole if "test_an_uncited_probe_still_runs" not in t}
    assert cited <= parts[3], "the cited_probes group must sit whole in the last shard"


def test_CONTROL_one_shard_is_not_the_whole_set():
    assert len(_collect(ORDINARY, "--shard", "0/4")) < len(_collect(ORDINARY))
