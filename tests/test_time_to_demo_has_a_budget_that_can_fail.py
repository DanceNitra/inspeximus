"""tools/time_to_demo.py fails the run over its budget or on a failed demo, and passes otherwise."""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))
from time_to_demo import verdict  # noqa: E402


def test_a_fast_passing_demo_passes():
    ok, problems = verdict(install_s=8.0, demo_s=1.5, demo_ok=True, budget=60)
    assert ok and problems == []


def test_over_the_budget_fails_even_when_the_demo_passes():
    ok, problems = verdict(install_s=58.0, demo_s=3.0, demo_ok=True, budget=60)
    assert not ok and "over the 60 s budget" in problems[0]


def test_a_failed_demo_fails_even_when_it_is_fast():
    ok, problems = verdict(install_s=5.0, demo_s=0.5, demo_ok=False, budget=60)
    assert not ok and "did not pass" in problems[0]


def test_CONTROL_exactly_at_the_budget_is_within_it():
    ok, _ = verdict(install_s=50.0, demo_s=10.0, demo_ok=True, budget=60)
    assert ok
