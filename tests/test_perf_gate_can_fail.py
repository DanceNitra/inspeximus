"""The performance gate must go red when work grows — proven, not assumed.

A gate nobody has watched fail is a gate nobody knows works. This repo learned that twice in one day: the
audit job's falsification control had been aimed at a string that no longer existed and could not fire for
a day, and the `check_code` build gate reported a clean verdict on a store it could not read. So before
`perf/gate.py` is allowed to keep anyone honest, it has to be shown failing.

Four properties, in the order they matter:

1. it PASSES on the current tree (else every red below is a verifier that rejects everything);
2. it FAILS when the O(k^2) tombstone write is reintroduced — the actual regression it was built for;
3. it does NOT fail on the byte drift that happens between two identical runs (no false alarms);
4. it FAILS when a workload disappears, because losing an arm silently is how a gate stops gating.
"""
import copy
import json
import os
import sys
import tempfile

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "perf"))
sys.path.insert(0, ROOT)

# A plain import on purpose: perf/gate.py ships in this repo and needs nothing optional, so an
# importorskip here would be a guard that can never fire -- and tools/skip_census.py counts any module
# carrying one as hidden from the base CI job, inflating its pin with tests that are not hidden at all.
import gate  # noqa: E402
import inspeximus.core as core  # noqa: E402


@pytest.fixture()
def baseline():
    with open(os.path.join(ROOT, "perf", "baseline.json"), encoding="utf-8") as f:
        return json.load(f)


def _erase_counters():
    """Run only the erase workload and return its counters — the arm the regression lives in."""
    run = gate.w_erase(60, 300)      # returns the callable; calling it runs the workload
    run()
    return run.inner


def test_control_the_gate_passes_on_an_unchanged_run(baseline):
    """Without this, every assertion below is satisfied by a checker that always fails."""
    now = copy.deepcopy(baseline)
    fail, _ = gate.compare(baseline, now)
    assert fail == [], fail


def test_it_fails_when_the_quadratic_tombstone_write_comes_back(baseline):
    """THE ONE IT EXISTS FOR. Before 1.88.1 the sidecar was rewritten once per tombstone.

    Simulated by flushing inside `_emit_tombstone` again, exactly as the old code did, and measured
    through the same counters the gate reads — not asserted from a table.
    """
    good = _erase_counters()
    assert good["replace_tombstones"] == 1, ("fixture error: the batched write is already broken", good)

    real = core.Inspeximus._emit_tombstone

    def per_tombstone(self, *a, **k):
        k.pop("defer", None)
        t = real(self, *a, defer=True, **k)
        self._flush_tombstones()          # the pre-1.88.1 behaviour
        return t

    core.Inspeximus._emit_tombstone = per_tombstone
    try:
        bad = _erase_counters()
    finally:
        core.Inspeximus._emit_tombstone = real

    # k + 1: sixty per-tombstone flushes from the reintroduced defect, plus the one batch flush that the
    # current forget() still performs at the end. The number that matters is that it now TRACKS k, where
    # the fixed code is a constant 1 no matter how many records are erased.
    assert bad["replace_tombstones"] == 61, (
        "reintroducing the per-tombstone write did not move the counter, so the counter is not measuring "
        f"what the gate claims: {bad}")

    base = {"erase": {"counters": good, "seconds_median": 0.1}}
    now = {"erase": {"counters": bad, "seconds_median": 0.1}}
    fail, _ = gate.compare(base, now)
    assert any("replace_tombstones" in f for f in fail), (
        f"the counter moved 1 -> 61 and the gate stayed green: {fail}")


def test_it_does_not_fire_on_the_byte_drift_between_two_identical_runs():
    """No false alarms. Timestamps vary in digit count, so serialized_bytes moves ~0.003% run to run.

    A gate that reddens on that gets muted within a week, and a muted gate is worse than none.
    """
    base = {"w": {"counters": {"replace_store": 1, "serialized_bytes": 147_642_011}, "seconds_median": 1.0}}
    for delta in (4_479, -4_479, 1_476_420):          # observed drift, its mirror, and a full 1%
        now = copy.deepcopy(base)
        now["w"]["counters"]["serialized_bytes"] = 147_642_011 + delta
        fail, _ = gate.compare(base, now)
        assert fail == [], (f"the gate reddened on a {delta / 147_642_011 * 100:+.3f}% byte change, "
                            f"inside its stated +/-{gate.BYTES_BAND * 100:.0f}% band: {fail}")


def test_it_does_fire_when_serialized_volume_grows_past_the_band():
    """The band must not be so wide that it absorbs what it exists to surface."""
    base = {"w": {"counters": {"replace_store": 1, "serialized_bytes": 147_642_011}, "seconds_median": 1.0}}
    now = copy.deepcopy(base)
    now["w"]["counters"]["serialized_bytes"] = int(147_642_011 * 1.10)      # +10%
    fail, _ = gate.compare(base, now)
    assert any("serialized_bytes" in f for f in fail), fail


def test_it_fails_when_a_workload_disappears(baseline):
    """A gate that quietly runs three of its four arms reports green for work it never did."""
    now = copy.deepcopy(baseline)
    dropped = sorted(now)[0]
    del now[dropped]
    fail, _ = gate.compare(baseline, now)
    assert any(dropped in f and "MISSING" in f for f in fail), fail


def test_every_recorded_arm_actually_does_something(baseline):
    """An arm whose counters are all zero AND finishes instantly cannot go red.

    The first version of this gate had one: a `consolidate` workload that flagged everything as a hub,
    saved nothing, and finished in 3 ms with every counter at zero. It was replaced. This keeps the next
    one from being added without anyone noticing.
    """
    dead = [name for name, w in baseline.items()
            if not any(w["counters"].values()) and w["seconds_median"] < 0.05]
    assert dead == [], f"workload(s) with no measurable work at all: {dead}"
