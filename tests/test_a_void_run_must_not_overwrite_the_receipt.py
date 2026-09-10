"""A probe run that measured nothing must not replace the run that did.

WHAT HAPPENED. `probes/what_the_lock_is_actually_holding_back.py` had two exits. The one at the
bottom is guarded: under pytest it leaves the receipt alone, because the suite runs every uncited
probe in parallel on a saturated machine. The VOID exit, 70 lines above it, carried no such guard.
It wrote the real receipt and returned 0.

So the suite destroyed its own evidence. On a two-core runner eight writer processes do not fit,
every trial comes back void, and the probe replaced the receipt with `"arms": {}` and
`verdict: void_no_usable_trial` while reporting success. That file is what sat in git. Re-run on
twenty-four cores it reports [0, 0, 0, 0] lost on the shipped path and [0, 0, 14, 24] of 96 on the
arm that restores the pre-fix line.

THIS TEST WORKS ON A COPY, and the first version did not. It edited the real receipt and put it
back, which passed alone and failed in the full suite: another test executes the same probe, and
two workers were writing one file. A test that asserts "do not share this file" must not share it
either. The probe derives its receipt path from `__file__`, so a copy in tmp_path takes its paths
with it.
"""
from __future__ import annotations

import importlib.util
import json
import os
import shutil

import pytest

PROBE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     "probes", "what_the_lock_is_actually_holding_back.py")


def _probe_copy(tmp_path, receipt_text='{"this": "is the real measurement"}'):
    """The probe, and a receipt beside it, entirely inside tmp_path."""
    dest = tmp_path / "what_the_lock_is_actually_holding_back.py"
    shutil.copyfile(PROBE, dest)
    receipt = tmp_path / "what_the_lock_is_actually_holding_back.result.json"
    receipt.write_bytes(receipt_text.encode("utf-8"))
    spec = importlib.util.spec_from_file_location("lock_probe_under_test_%s" % tmp_path.name,
                                                  str(dest))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m, receipt, tmp_path / "what_the_lock_is_actually_holding_back.void.json"


def _all_trials_void(monkeypatch, m):
    """Every trial void, which is what a machine too small to run the probe produces."""
    def void_trial(src, writers, arm):
        if writers == 1:                      # the single-writer control still runs
            return {"landed": m.PER, "attempted": m.PER, "claimed": m.PER,
                    "workers_reporting_success": 1, "any_worker_raised": ""}
        return {"void": True, "landed": 0, "attempted": 0, "claimed": 0,
                "workers_reporting_success": 0, "any_worker_raised": ""}
    monkeypatch.setattr(m, "_trial", void_trial)


def test_a_void_run_leaves_the_receipt_untouched(monkeypatch, tmp_path):
    m, receipt, void = _probe_copy(tmp_path)
    before = receipt.read_bytes()
    _all_trials_void(monkeypatch, m)

    m.main()

    assert receipt.read_bytes() == before, (
        "a run that measured nothing rewrote the receipt of a run that did. The guard on the other "
        "exit does not cover this one.")
    assert void.exists(), "the void outcome must still be recorded, just not as a result"
    v = json.loads(void.read_text(encoding="utf-8"))
    assert v["verdict"] == "void_no_usable_trial"
    assert v["arms"] == {}, "a void run has no arms; anything else here would be a measurement"
    assert "why" in v and str(os.cpu_count()) in v["why"]


def test_the_control_the_old_void_exit_did_overwrite_it(monkeypatch, tmp_path):
    """Without this, the test above would pass just as well against a probe that writes nothing at
    all, or one whose void branch is unreachable. It restores the old behaviour on the COPY and
    requires the assertion to fail against it."""
    m, receipt, _void = _probe_copy(tmp_path)
    before = receipt.read_bytes()
    _all_trials_void(monkeypatch, m)

    real_dumps = json.dumps

    def old_void_exit(out, indent=None, **kw):
        # the pre-fix line: the void branch wrote `out` to the REAL receipt
        if isinstance(out, dict) and out.get("verdict") == "void_no_usable_trial":
            receipt.write_text(real_dumps(out, indent=1), encoding="utf-8")
        return real_dumps(out, indent=indent, **kw) if indent is not None else real_dumps(out, **kw)

    monkeypatch.setattr(m.json, "dumps", old_void_exit)
    m.main()
    assert receipt.read_bytes() != before, (
        "the control did not reproduce the overwrite, so the test above measures nothing")


def test_the_real_receipt_is_a_measurement_and_not_a_void_run():
    """The committed file itself. It was `{"arms": {}}` in git; this is what stops that returning."""
    real = os.path.splitext(PROBE)[0] + ".result.json"
    if not os.path.exists(real):
        pytest.skip("no receipt committed")
    d = json.loads(open(real, encoding="utf-8").read())
    assert d.get("verdict") != "void_no_usable_trial", "the committed receipt measured nothing"
    assert d.get("arms"), "a receipt with no arms is not a measurement"
    assert "degraded, pre-fix" in d["arms"], (
        "the control arm is what makes the other two mean anything; without it a clean run cannot "
        "be told from a run too quiet to interleave the writers")
