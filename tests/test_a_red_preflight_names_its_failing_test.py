"""A red pre-flight names the test that was red, and keeps the output that says why.

PC2's full mutation audit of 515a7439 reported two red pre-flights (audit_bundle.py #7, cli.py #8) that
did not reproduce in three re-runs. The gate had printed "tests are not green before mutating" and
discarded the pytest output, and tools/mutation_check_parallel.py never wrote its workers' output, so
there was nothing left to diagnose them with. These tests force a red pre-flight and require the failing
test id in the reported reason and the full output in a file, through the serial gate and the parallel one.
"""
import json
import os
import re
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import mutation_check  # noqa: E402

#: Runs the in-place mutation harness, so it runs serially with the other harness tests.
pytestmark = pytest.mark.mutation

#: A real, unique target. The pre-flight is red, so the mutant is never applied.
TARGET = {"file": "inspeximus/core.py", "old": 'policy: str = "safe"', "new": 'policy: str = "trusting"'}


def _own_root(tmp_path):
    """A pytest.ini makes the fixture directory its own rootdir. Without it pytest collected from the
    temp directory above and met another run's temporary home vanishing mid-scan: a red pre-flight that
    had nothing to do with the test, found by the very output this file makes the gate keep."""
    (tmp_path / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")


def _red_test(tmp_path):
    _own_root(tmp_path)
    t = tmp_path / "test_red_on_purpose.py"
    t.write_text("def test_red_on_purpose():\n    assert 'the reason is kept' == 'it was lost'\n",
                 encoding="utf-8")
    return str(t)


def test_a_red_preflight_reports_the_failing_test_and_keeps_its_output(tmp_path, monkeypatch, capsys):
    logs = tmp_path / "preflight"
    monkeypatch.setenv("MUTATION_PREFLIGHT_DIR", str(logs))
    rc = mutation_check.run([{**TARGET, "name": "forced red pre-flight", "tests": [_red_test(tmp_path)]}])
    out = capsys.readouterr().out
    assert rc == 1, "a red pre-flight must fail the gate"
    reason = [ln for ln in out.splitlines() if ln.strip().startswith("skipped: forced red pre-flight")]
    assert len(reason) == 1, out
    assert "test_red_on_purpose.py::test_red_on_purpose" in reason[0], reason[0]
    assert "exit 1" in reason[0] and "1 failed" in reason[0], reason[0]
    path = re.search(r"full output: (.+?\.log)\)", reason[0]).group(1)
    assert os.path.dirname(path) == str(logs)
    assert "the reason is kept" in open(path, encoding="utf-8").read()


def test_a_green_preflight_writes_no_output_file(tmp_path, monkeypatch):
    """The control: the file is written because the pre-flight was red, not on every run."""
    logs = tmp_path / "preflight"
    monkeypatch.setenv("MUTATION_PREFLIGHT_DIR", str(logs))
    _own_root(tmp_path)
    green = tmp_path / "test_green.py"
    green.write_text("def test_green():\n    assert True\n", encoding="utf-8")
    mutation_check.run([{**TARGET, "name": "green pre-flight", "tests": [str(green)]}], verbose=False)
    assert not logs.exists()


def test_the_parallel_runner_reports_the_failing_test_and_keeps_worker_output(tmp_path):
    """Through tools/mutation_check_parallel.py, whose worker worktree is removed after the run: the id
    must reach the aggregated report, and the output must be in a file outside that worktree."""
    spec = tmp_path / "spec.json"
    spec.write_text(json.dumps([{**TARGET, "name": "forced red pre-flight (parallel)",
                                 "tests": [_red_test(tmp_path)]}]), encoding="utf-8")
    env = {k: v for k, v in os.environ.items() if k != "MUTATION_PREFLIGHT_DIR"}
    p = subprocess.run([sys.executable, os.path.join("tools", "mutation_check_parallel.py"), str(spec),
                        "--workers", "1", "--allow-dirty"], cwd=ROOT, capture_output=True, text=True,
                       env=env, timeout=900)
    assert p.returncode == 1, p.stdout[-2000:] + p.stderr[-2000:]
    reason = [ln for ln in p.stdout.splitlines()
              if ln.strip().startswith("skipped: forced red pre-flight (parallel)")]
    assert len(reason) == 1, p.stdout[-2000:]
    assert "test_red_on_purpose.py::test_red_on_purpose" in reason[0], reason[0]
    path = re.search(r"full output: (.+?\.log)\)", reason[0]).group(1)
    assert os.path.exists(path), "the full output was lost with the worker's worktree"
    assert "the reason is kept" in open(path, encoding="utf-8").read()
    worker_log = os.path.join(ROOT, ".mutwt", "worker0.log")
    assert "forced red pre-flight (parallel)" in open(worker_log, encoding="utf-8").read()
