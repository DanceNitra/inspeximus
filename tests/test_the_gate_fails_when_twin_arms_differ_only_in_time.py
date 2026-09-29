"""Twin arms with the same counted work must take about the same time (3.15.5).

At 3.15.4 `prompt_unstamped_n2000` (a key home that cannot verify the read-guard stamps) and
`prompt_n2000` had equal work counters and medians 1.509 s and 0.099 s. The 15x was an uncached
missing-key lookup per record that no counter saw; wall-clock is advisory, so the gate recorded it as the
baseline. On a 71,772-row store the hook it measured took 48.6 s a prompt against 5.3 s on 3.15.3.
"""
import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "perf"))
sys.path.insert(0, ROOT)
import gate  # noqa: E402

C = {"store_loads": 1, "dir_listings": 1, "read_guard_assessments": 2000, "guard_regex_searches": 0}


def _arm(desc, t, **over):
    return {"desc": desc, "counters": {**C, **over}, "seconds_median": t}


def _pair(t_base, t_twin, **twin_over):
    return {"prompt_n2000": _arm("fresh handle opens a 2,000-record store and recalls once", t_base),
            "prompt_unstamped_n2000": _arm("prompt_n2000 under a key home that cannot verify the stamps",
                                           t_twin, **twin_over)}


def test_a_twin_is_the_arm_its_desc_starts_with():
    assert gate._twins(_pair(0.1, 0.1)) == {"prompt_unstamped_n2000": "prompt_n2000"}
    assert gate._twins({"a_long": _arm("x", 1), "a": _arm("a_longer other thing", 1)}) == {}, (
        "a name followed by a space, not a prefix of a longer word")


def test_it_fails_on_the_3_15_4_pair_same_counted_work_15x_apart():
    now = _pair(0.0992, 1.5093, dir_listings=2)          # the recorded 3.15.4 numbers
    fail, _ = gate.compare(now, now)
    assert any("prompt_unstamped_n2000 vs prompt_n2000" in f and "15.2x" in f for f in fail), fail


def test_it_passes_twins_within_the_factor():
    now = _pair(0.10, 0.45, dir_listings=2)
    assert gate._twin_misses(now) == []


def test_different_counted_work_explains_the_time():
    now = _pair(0.10, 1.50, read_guard_assessments=30000)
    assert gate._twin_misses(now) == []


def test_setup_counters_do_not_make_twins_different():
    now = _pair(0.10, 1.50, dir_listings=2, store_loads=2)
    assert gate._twin_misses(now), "a setup-only difference hid the gap"


def test_record_refuses_a_baseline_with_the_gap(tmp_path, monkeypatch):
    base = tmp_path / "baseline.json"
    monkeypatch.setattr(gate, "BASELINE", base)
    monkeypatch.setattr(gate, "ROOT", tmp_path)
    monkeypatch.setattr(gate, "measure", lambda: _pair(0.0992, 1.5093, dir_listings=2))
    assert gate.main(["gate.py", "record"]) == 1
    assert not base.exists(), "the gap was recorded as the baseline"
    monkeypatch.setattr(gate, "measure", lambda: _pair(0.10, 0.12, dir_listings=2))
    assert gate.main(["gate.py", "record"]) == 0
    assert json.loads(base.read_text(encoding="utf-8"))["prompt_unstamped_n2000"]["seconds_median"] == 0.12


def test_control_the_gate_declares_the_prompt_twins():
    """The pair this rule exists for is still a pair in the gate's own arms: its desc still names its twin."""
    src = open(os.path.join(ROOT, "perf", "gate.py"), encoding="utf-8").read()
    assert '"prompt_unstamped_n2000"' in src and "prompt_n2000 under a key home" in src
