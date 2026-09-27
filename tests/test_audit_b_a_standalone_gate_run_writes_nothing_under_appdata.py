"""AUDIT-B B-21: a standalone `python perf/gate.py` run must not write into the user's APPDATA.

The gate's erase and session workloads use receipted temp stores, and a receipted store records its chain
head under the key home: `INSPEXIMUS_KEY_HOME`, else APPDATA. The gate set neither, so every standalone run
left head files in the real `%APPDATA%\\inspeximus\\heads`: 9 per run, measured 2026-09-27 with APPDATA
pointed at a scratch folder (AUDIT-A filed it; the same class as A-17, a test tool writing a real home).

The test runs the gate's own `main()` with one small receipted workload and APPDATA on a temp folder.
The control runs the same workload outside `main()` and requires heads to appear under APPDATA, so a
zero inside `main()` means the gate kept them elsewhere, not that the workload wrote none.
"""
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "perf"))
sys.path.insert(0, ROOT)
import gate  # noqa: E402


def _heads(appdata):
    d = os.path.join(appdata, "inspeximus", "heads")
    return sorted(os.listdir(d)) if os.path.isdir(d) else []


def test_the_gate_keeps_its_chain_heads_out_of_appdata(tmp_path, monkeypatch, capsys):
    for k in [k for k in os.environ if k.startswith("INSPEXIMUS_")]:
        monkeypatch.delenv(k)
    appdata = tmp_path / "appdata"
    appdata.mkdir()
    monkeypatch.setenv("APPDATA", str(appdata))

    # CONTROL: the workload writes heads under APPDATA when nothing redirects them.
    gate.w_erase(3, 10)()
    if not _heads(str(appdata)):
        pytest.fail("control: the erase workload wrote no chain head, so a zero below would prove nothing")
    for f in _heads(str(appdata)):
        os.remove(os.path.join(str(appdata), "inspeximus", "heads", f))

    monkeypatch.setattr(gate, "WORKLOADS", {"erase_tiny": (lambda: gate.w_erase(3, 10), "tiny erase", "rows")})
    monkeypatch.setattr(gate, "REPEATS", 1)
    monkeypatch.setattr(gate, "BASELINE", tmp_path / "no-baseline.json")
    # `check` measures every workload first, then finds no baseline and returns 2.
    if gate.main(["gate", "check"]) != 2:
        pytest.fail("control: the gate did not measure and then report the missing baseline")
    assert "INSPEXIMUS_KEY_HOME" not in os.environ, "the gate left its key home set after main()"
    assert _heads(str(appdata)) == [], f"a standalone gate run wrote {len(_heads(str(appdata)))} head file(s) under APPDATA"
