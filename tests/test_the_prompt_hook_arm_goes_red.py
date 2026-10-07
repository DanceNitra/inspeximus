"""The perf gate's prompt-hook arm (`prompt_hook_n1200`) holds the work counters the 3.17 hook gains rest on, and goes red
when each one moves (AUDIT-B, 2026-10-07). Counters only: the seconds are advisory and are not compared here.

  foreign_stamps / guard_shape_scans   0 while the interpreter that stamped is the interpreter that reads
  token_builds                         one per active row of both stores, once per prompt
  rows_parsed                          every row of both stores parsed once per prompt
  fast_exit_prompt / fast_exit_other   1 for UserPromptSubmit, 0 for every other event
"""
import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "perf"))
sys.path.insert(0, ROOT)
import gate  # noqa: E402

ARM = "prompt_hook_n1200"
WORK = ("foreign_stamps", "guard_shape_scans", "token_builds", "rows_parsed", "fast_exit_prompt", "fast_exit_other",
        "read_guard_assessments", "store_loads", "tracked_gets")


def _counters():
    with gate._isolated_key_home():
        run = gate.w_prompt_hook(1200)
        run()
    return run.inner


def test_the_arm_matches_the_baseline_and_holds_the_expected_values():
    """The control: without it every red below is satisfied by an arm that never matches."""
    with open(os.path.join(ROOT, "perf", "baseline.json"), encoding="utf-8") as fh:
        base = json.load(fh)[ARM]["counters"]
    now = _counters()
    assert {k: now[k] for k in WORK} == {k: base[k] for k in WORK}, (now, base)
    assert (now["foreign_stamps"], now["guard_shape_scans"], now["fast_exit_prompt"], now["fast_exit_other"]) == (0, 0, 1, 0)
    assert now["rows_parsed"] == 1400, now          # 1,200 captures and 200 decisions, each parsed once
    assert ARM in gate.WORKLOADS


def test_the_arm_is_in_the_gate_and_in_the_baseline():
    with open(os.path.join(ROOT, "perf", "baseline.json"), encoding="utf-8") as fh:
        assert ARM in json.load(fh)
