"""A probe run that measured nothing must not replace the run that did.

WHAT HAPPENED. `probes/what_the_lock_is_actually_holding_back.py` had two exits. The one at the
bottom is guarded: under pytest it prints "the receipt is NOT rewritten" and leaves the file alone,
because the suite runs every uncited probe in parallel on a saturated machine. The VOID exit, 70
lines above it, carried no such guard. It wrote the real receipt and returned 0.

So the suite itself destroyed the measurement. On a two-core runner eight writer processes do not
fit, every trial comes back void, and the probe replaced the receipt with `"arms": {}` and
`verdict: void_no_usable_trial` while reporting success. That file is what sat in git.

Measured on this machine, twenty-four cores, the same probe reports:

    lock held          lost [0, 0, 0, 0] of 96, clean in 4 of 4
    lock degraded      lost [0, 0, 0, 0] of 96, clean in 4 of 4
    degraded, pre-fix  lost [0, 0, 14, 24] of 96, clean in 2 of 4

The last arm is the control: it restores the pre-fix line, and it is the reason the first two mean
anything. All of that was thrown away by a run that could not schedule its own workers.

A guard on one exit is not a guard. This pins the property rather than the line: force every trial
void, run the probe, and require the receipt to be byte-identical afterwards.
"""
from __future__ import annotations

import importlib.util
import json
import os

import pytest

PROBE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     "probes", "what_the_lock_is_actually_holding_back.py")
RECEIPT = os.path.splitext(PROBE)[0] + ".result.json"
VOID = os.path.splitext(PROBE)[0] + ".void.json"


def _module():
    spec = importlib.util.spec_from_file_location("lock_probe_under_test", PROBE)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _all_trials_void(monkeypatch, m):
    """Every trial comes back void, which is what a machine too small to run the probe produces."""
    def void_trial(src, writers, arm):
        if writers == 1:                      # the single-writer control still runs
            return {"landed": m.PER, "attempted": m.PER, "claimed": m.PER,
                    "workers_reporting_success": 1, "any_worker_raised": ""}
        return {"void": True, "landed": 0, "attempted": 0, "claimed": 0,
                "workers_reporting_success": 0, "any_worker_raised": ""}
    monkeypatch.setattr(m, "_trial", void_trial)


def test_a_void_run_leaves_the_receipt_untouched(monkeypatch, tmp_path):
    if not os.path.exists(RECEIPT):
        pytest.skip("no receipt committed, so there is nothing this could overwrite")
    before = open(RECEIPT, "rb").read()
    m = _module()
    _all_trials_void(monkeypatch, m)
    if os.path.exists(VOID):
        os.remove(VOID)
    try:
        m.main()
        assert open(RECEIPT, "rb").read() == before, (
            "a run that measured nothing rewrote the receipt of a run that did. The guard on the "
            "other exit does not cover this one.")
        assert os.path.exists(VOID), "the void outcome must still be recorded, just not as a result"
        v = json.load(open(VOID, encoding="utf-8"))
        assert v["verdict"] == "void_no_usable_trial"
        assert v["arms"] == {}, "a void run has no arms; anything else here would be a measurement"
        assert "why" in v and str(os.cpu_count()) in v["why"]
    finally:
        open(RECEIPT, "wb").write(before)
        if os.path.exists(VOID):
            os.remove(VOID)


def test_the_control_the_old_void_exit_did_overwrite_it(monkeypatch):
    """Without this, the test above would pass just as well against a probe that writes nothing at
    all, or one whose void branch is unreachable. It restores the old behaviour and requires the
    assertion to fail."""
    if not os.path.exists(RECEIPT):
        pytest.skip("no receipt committed")
    before = open(RECEIPT, "rb").read()
    m = _module()
    _all_trials_void(monkeypatch, m)

    real_dumps = json.dumps

    def old_void_exit(out, indent=None, **kw):
        # the pre-fix line: the void branch wrote out to the REAL receipt
        if isinstance(out, dict) and out.get("verdict") == "void_no_usable_trial":
            with open(RECEIPT, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(real_dumps(out, indent=1))
        return real_dumps(out, indent=indent, **kw) if indent is not None else real_dumps(out, **kw)

    monkeypatch.setattr(m.json, "dumps", old_void_exit)
    try:
        m.main()
        assert open(RECEIPT, "rb").read() != before, (
            "the control did not reproduce the overwrite, so the test above measures nothing")
    finally:
        open(RECEIPT, "wb").write(before)
        if os.path.exists(VOID):
            os.remove(VOID)
