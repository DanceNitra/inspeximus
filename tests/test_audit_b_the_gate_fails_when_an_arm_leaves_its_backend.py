"""AUDIT-B B-13: the perf gate must go red when an arm stops exercising the store backend it names.

The default store became rows after `perf/baseline.json` was last recorded (2026-08-16). The write, erase
and session arms were written against the JSON backend, and from then on they ran on rows: `write_n1000`
went from 1,000 full serializations to 0, and the gate reported the drop as a NOTE and stayed green. The
JSON write path was no longer measured by anything, and nothing said so.

Session 1's decision (2026-09-27): keep arms pinned to the JSON backend while JSON stores are read
(encrypted stores, `INSPEXIMUS_STORE_FORMAT=json`), add row-store arms, and make the gate fail when an arm
stops exercising the backend it names.

The mechanism test runs a JSON-named arm with the pin removed, which is what the default flip did, and
requires the gate to fail. Its control runs the same arm pinned and requires the gate to pass, so a red
result means the witness saw the backend change, not that the witness rejects everything.
"""
import contextlib
import copy
import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "perf"))
sys.path.insert(0, ROOT)
import gate  # noqa: E402

BACKENDS = {"json", "rows", "none"}

pytestmark = pytest.mark.xfail(strict=True, raises=AssertionError,
                                reason="B-13: the gate's arms name no backend, so leaving one stays green")


def _names_backends():
    return hasattr(gate, "_backend") and all(len(spec) == 3 for spec in gate.WORKLOADS.values())


def _tiny(monkeypatch, backend="json"):
    for k in [k for k in os.environ if k.startswith("INSPEXIMUS_") and k != "INSPEXIMUS_KEY_HOME"]:
        monkeypatch.delenv(k)
    monkeypatch.setattr(gate, "WORKLOADS", {"write_tiny": (lambda: gate.w_write(20), "20 writes", backend)})
    monkeypatch.setattr(gate, "REPEATS", 1)


def test_an_arm_that_leaves_its_backend_turns_the_gate_red(monkeypatch):
    assert _names_backends(), "the gate's workloads name no backend, so no arm can be held to one"
    _tiny(monkeypatch)

    # CONTROL: pinned, the JSON arm writes a JSON store and the gate passes against itself.
    good = gate.measure()
    assert good["write_tiny"]["backend"] == {"declared": "json", "observed": "json"}, good["write_tiny"]
    fail, _ = gate.compare(good, copy.deepcopy(good))
    assert fail == [], fail

    # The default flip: the pin disappears and the same arm runs on rows.
    monkeypatch.setattr(gate, "_backend", lambda backend: contextlib.nullcontext())
    bad = gate.measure()
    assert bad["write_tiny"]["backend"]["observed"] == "rows", bad["write_tiny"]
    fail, _ = gate.compare(good, bad)
    assert any("write_tiny" in f and "backend" in f for f in fail), (
        f"a JSON arm ran on rows and the gate stayed green: {fail}")


def test_record_refuses_an_arm_that_left_its_backend(monkeypatch, tmp_path, capsys):
    assert _names_backends(), "the gate's workloads name no backend, so record cannot check one"
    _tiny(monkeypatch)
    monkeypatch.setattr(gate, "BASELINE", tmp_path / "baseline.json")
    monkeypatch.setattr(gate, "_backend", lambda backend: contextlib.nullcontext())
    assert gate.main(["gate", "record"]) != 0, "record wrote a baseline for an arm that left its backend"
    assert not (tmp_path / "baseline.json").exists(), "record wrote the baseline and then reported failure"


def test_every_arm_names_its_backend_and_the_baseline_saw_it():
    assert _names_backends(), "the gate's workloads name no backend"
    bad = {n: spec[2] for n, spec in gate.WORKLOADS.items() if spec[2] not in BACKENDS}
    assert bad == {}, f"arms with an unknown backend: {bad}"
    with open(os.path.join(ROOT, "perf", "baseline.json"), encoding="utf-8") as fh:
        base = json.load(fh)
    for name, (_build, _desc, backend) in gate.WORKLOADS.items():
        assert base[name]["backend"] == {"declared": backend, "observed": backend}, (name, base[name].get("backend"))
    # Both backends keep a write, an erase and a session arm, so neither path goes unmeasured again.
    for kind in ("write", "erase", "session"):
        names = {gate.WORKLOADS[n][2] for n in gate.WORKLOADS if n.startswith(kind + "_")}
        assert {"json", "rows"} <= names, f"{kind}: arms cover only {names}"
    # A JSON write arm that serialises nothing is measuring nothing.
    json_writes = [n for n in gate.WORKLOADS if n.startswith("write_") and gate.WORKLOADS[n][2] == "json"]
    assert all(base[n]["counters"]["full_serializations"] > 0 for n in json_writes), json_writes
