"""The rule every 3.21.1 timing verdict follows (tests/_timing.py), tested without a clock.

CI runs 39372338135 and 39377281135 failed linear rewrites on single readings of 1 to 90 ms. A verdict now needs the
minimum of 3 readings, a bound of FLOOR or more, and the same verdict again in a fresh child process."""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _timing  # noqa: E402

#: A child that "measures" each case by returning the value the parent passed for it in ARG.
ECHO = "import json\nprint(json.dumps({name: ARG[name] for name in sorted(ONLY or ARG)}))\n"


def test_a_case_over_the_bound_once_is_measured_again_and_cleared():
    took = {"stalled": 0.31, "fast": 0.01}
    assert _timing.over_bound(ECHO, took, 0.1, arg={"stalled": 0.02, "fast": 0.01}) == {}


def test_control_a_case_over_the_bound_twice_fails():
    took = {"slow": 0.31, "fast": 0.01}
    assert _timing.over_bound(ECHO, took, 0.1, arg={"slow": 0.29, "fast": 0.01}) == {"slow": [0.31, 0.29]}


def test_the_fresh_child_measures_only_the_flagged_cases():
    # `fast` is not in ARG: a child asked for it would fail with a KeyError.
    assert _timing.over_bound(ECHO, {"slow": 0.5, "fast": 0.0}, 0.1, arg={"slow": 0.0}) == {}


def test_a_bound_under_the_floor_is_refused():
    with pytest.raises(AssertionError):
        _timing.over_bound(ECHO, {"a": 0.0}, _timing.FLOOR / 2)


BEST = "import json\nimport time\nn = [0]\ndef call(x):\n    n[0] += 1\n    time.sleep(x[n[0] - 1])\nprint(json.dumps({'best': best(call, ARG['sleeps'], ARG['bound']), 'calls': n[0]}))\n"


def test_best_is_the_minimum_of_three():
    # Three calls of 0.12, 0.06 and 0.09 s with a 0.05 s bound: none is under it, so all three run.
    r = _timing.child(BEST, arg={"sleeps": [0.12, 0.06, 0.09], "bound": 0.05})
    assert r["calls"] == 3 and 0.06 <= r["best"] < 0.09, r


def test_best_stops_at_one_call_under_the_bound():
    r = _timing.child(BEST, arg={"sleeps": [0.0, 0.5, 0.5], "bound": 0.05})
    assert r["calls"] == 1, r
