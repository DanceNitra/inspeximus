"""The lock probe must count clean trials against the trials that ran, not against the constant.

WHAT THIS PINS. CI on 6eee2de failed with "the LOCKED arm lost records a writer was TOLD had been
written" and then printed the losses as [0, 0, 0]. Nothing had been lost. `TRIALS` is 4, one trial
went void on a small runner, the arm dropped it, and the assertion compared a count of three clean
trials against the constant four. A void trial read as a lossy one, and the message said the
opposite of its own evidence.

The void handling arrived in 81b042f and the three places that still counted against the constant
did not move with it, which is a fix landing at the instance while the class survives one line over.

BOTH DIRECTIONS ARE TESTED, because only fixing the first would let the assertion be deleted:

  a void trial must not fail the probe      one trial returns void, the rest are clean, exit 0
  a real loss must still fail it            one trial loses records, exit non-zero

The second case is what stops "count against usable trials" from becoming "never fail". Without it
a probe that always exits 0 would pass this file.

The concurrency is not run here. `_trial` is replaced, so this exercises the counting rather than
the store, and it costs no processes. The probe's own run is what measures the store.
"""
import importlib.util
import os
import sys

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROBE = os.path.join(REPO, "probes", "what_the_lock_is_actually_holding_back.py")


def _load():
    """A fresh module object each time, so one test's monkeypatch cannot reach another."""
    spec = importlib.util.spec_from_file_location("lock_probe_under_test", PROBE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _fake_trial(mod, script):
    """Replace the real trial with a scripted sequence: one entry per call, cycling per arm.

    `script` maps an arm name to the list of results its trials return. The single-writer control
    is asked for first and always lands cleanly, since a void control voids the whole run by design
    and would hide what these tests are pinning.
    """
    calls = {"control": 0}
    per_arm = {k: 0 for k in script}

    def trial(src, writers, arm):
        if writers == 1:
            calls["control"] += 1
            return {"landed": 12, "claimed": 12, "attempted": 12,
                    "workers_reporting_success": 1, "any_worker_raised": None}
        i = per_arm[arm]
        per_arm[arm] += 1
        return script[arm][i]

    mod._trial = trial
    return calls


def _clean(writers):
    n = writers * 12
    return {"landed": n, "claimed": n, "attempted": n,
            "workers_reporting_success": writers, "any_worker_raised": None}


def _void():
    return {"void": True, "landed": 0, "claimed": 0, "attempted": 0,
            "workers_reporting_success": 0, "any_worker_raised": None}


def _lossy(writers, lost):
    n = writers * 12
    return {"landed": n - lost, "claimed": n, "attempted": n,
            "workers_reporting_success": writers, "any_worker_raised": None}


def _script(mod, locked):
    """The three arms the probe runs, with only the locked arm varied."""
    w = mod.WRITERS
    other = [_clean(w) for _ in range(mod.TRIALS)]
    pre = [_lossy(w, 9) for _ in range(mod.TRIALS)]
    return {"locked": locked, "unlocked": other, "prefix": pre}


def test_a_void_trial_is_excluded_rather_than_counted_as_a_loss(tmp_path, monkeypatch):
    mod = _load()
    monkeypatch.chdir(tmp_path)
    locked = [_clean(mod.WRITERS) for _ in range(mod.TRIALS - 1)] + [_void()]
    _fake_trial(mod, _script(mod, locked))
    assert mod.main() == 0, (
        "one void trial among otherwise clean ones must not read as a lost record. This is the "
        "exact CI failure being pinned.")


def test_a_real_loss_in_the_locked_arm_still_fails(tmp_path, monkeypatch):
    mod = _load()
    monkeypatch.chdir(tmp_path)
    locked = [_clean(mod.WRITERS) for _ in range(mod.TRIALS - 1)] + [_lossy(mod.WRITERS, 4)]
    _fake_trial(mod, _script(mod, locked))
    with pytest.raises(AssertionError, match="LOCKED arm lost records"):
        mod.main()


def test_an_arm_left_with_too_few_usable_trials_is_void_not_passing(tmp_path, monkeypatch):
    """Counting against usable trials must not let an arm shrink to one lucky trial."""
    mod = _load()
    monkeypatch.chdir(tmp_path)
    locked = [_clean(mod.WRITERS)] + [_void() for _ in range(mod.TRIALS - 1)]
    _fake_trial(mod, _script(mod, locked))
    assert mod.main() == 0
    assert mod.MIN_USABLE >= 2, "one trial is not a measurement"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
