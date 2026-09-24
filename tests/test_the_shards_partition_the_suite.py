"""`--shard i/n` must split the suite into n disjoint pieces whose union is the whole suite, balanced on
measured time.

A shard that dropped a test would let CI report green on a suite it never ran, and a test in two
shards would be counted twice against the total. Both are checked on real collections. The balancing
is checked on `shard_plan` directly, with a control that shows the old key (one long group kept whole
in one shard) is measurably worse, and against the committed durations for the shard count CI uses.
"""
from __future__ import annotations

import json
import os
import random
import subprocess
import sys

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tests"))
from conftest import SHARD_DURATIONS, shard_plan  # noqa: E402

ORDINARY = ["tests/test_raise_on_block_turns_a_blocked_write_into_an_error.py",
            "tests/test_the_assessor_pack_computes_each_report_once.py",
            "tests/test_retire_ends_a_key_with_no_replacement.py",
            "tests/test_a_blocked_write_says_so_on_every_surface_and_retire_names_its_status.py"]
GROUPED = "tests/test_probes_cited_by_docs.py"
#: The test time a shard may take. The owner's target is a slowest shard under 8 minutes of wall time,
#: and installing the full environment takes about 2 of them (measured on 320000d: 1 min 44 s).
BUDGET_SECONDS = 6 * 60


def _collect(targets, *extra):
    out = subprocess.run([sys.executable, "-m", "pytest"] + list(targets) +
                         ["--collect-only", "-q", "-n", "0", "-p", "no:cacheprovider"] + list(extra),
                         cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace").stdout
    return {ln.strip() for ln in out.splitlines() if "::" in ln}


def _ci_shard_count():
    with open(os.path.join(ROOT, ".github", "workflows", "ci.yml"), encoding="utf-8") as fh:
        jobs = yaml.safe_load(fh)["jobs"]
    shards = jobs["integrations"]["strategy"]["matrix"]["shard"]
    return len(shards)


def test_four_shards_are_disjoint_and_cover_the_files():
    whole = _collect(ORDINARY)
    parts = [_collect(ORDINARY, "--shard", "%d/4" % i) for i in range(4)]
    assert len(whole) >= 20, "CONTROL: too few tests for the partition to mean anything"
    assert set().union(*parts) == whole
    assert sum(len(p) for p in parts) == len(whole), "a test landed in two shards"
    # Not every shard: shard 0 is charged the serial mutation set up front, so a small subset of cheap
    # tests can all land elsewhere. Spread means more than one shard.
    assert sum(1 for p in parts if p) >= 2, "one shard took every test, so the plan does not spread"


def test_the_long_group_is_split_across_shards_and_still_covered():
    whole = _collect([GROUPED])
    parts = [_collect([GROUPED], "--shard", "%d/4" % i) for i in range(4)]
    assert whole and set().union(*parts) == whole
    assert sum(len(p) for p in parts) == len(whole), "a test landed in two shards"
    assert sum(1 for p in parts if p) >= 2, "the cited-probe group still sits whole in one shard"


def test_a_long_group_is_spread_so_no_shard_runs_it_all_serially():
    tests = [("g::%03d" % k, "g") for k in range(40)] + [("u::%03d" % k, None) for k in range(200)]
    durations = {t: (10.0 if g else 1.0) for t, g in tests}
    _, wall = shard_plan(tests, 4, durations)
    assert max(wall) <= 110.0, wall


def test_CONTROL_keeping_the_group_whole_is_measurably_slower():
    # The old key: the whole group in the last shard, the rest spread over the others.
    tests = [("g::%03d" % k, "g") for k in range(40)] + [("u::%03d" % k, None) for k in range(200)]
    durations = {t: (10.0 if g else 1.0) for t, g in tests}
    _, balanced = shard_plan(tests, 4, durations)
    whole_group_serial = sum(d for t, d in durations.items() if t.startswith("g::"))
    assert whole_group_serial > 3 * max(balanced), (whole_group_serial, balanced)


def test_the_plan_does_not_depend_on_collection_order():
    tests = [("t::%03d" % k, "g" if k % 3 == 0 else None) for k in range(120)]
    durations = {t: float(k % 17 + 1) for k, (t, _) in enumerate(tests)}
    first, _ = shard_plan(tests, 4, durations)
    shuffled = tests[:]
    random.Random(7).shuffle(shuffled)
    second, _ = shard_plan(shuffled, 4, durations)
    assert dict(zip((t for t, _ in tests), first)) == dict(zip((t for t, _ in shuffled), second))


def test_the_committed_durations_fit_every_ci_shard_in_the_budget():
    with open(SHARD_DURATIONS, encoding="utf-8") as fh:
        durations = json.load(fh)
    assert len(durations) > 1000, "CONTROL: the durations file does not describe the suite"
    # Group membership as the conftest sees it: the two files that declare a group.
    def group(node):
        if node.startswith(GROUPED + "::test_an_uncited_probe_still_runs"):
            return "echo_policy_panel"
        if node.startswith(GROUPED + "::"):
            return "cited_probes"
        if node.startswith("tests/test_echo_policy_panel.py::"):
            return "echo_policy_panel"
        return None
    tests = [(t, group(t)) for t in durations if not t.startswith("@")]
    assert any(g == "cited_probes" for _, g in tests), "CONTROL: the long group is not in the file"
    _, wall = shard_plan(tests, _ci_shard_count(), durations)
    assert max(wall) <= BUDGET_SECONDS, ["%.0f s" % w for w in wall]


def test_CONTROL_one_shard_is_not_the_whole_set():
    assert len(_collect(ORDINARY, "--shard", "0/4")) < len(_collect(ORDINARY))
