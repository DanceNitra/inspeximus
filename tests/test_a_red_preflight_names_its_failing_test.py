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
import mutation_check_parallel  # noqa: E402

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
                       encoding="utf-8", errors="replace", env=env, timeout=900)
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


def test_every_pytest_run_inside_a_worker_is_serial(tmp_path, monkeypatch):
    """Session 1's rule: parallelism lives only in mutation_check_parallel's --workers. The pre-flight, the
    mutant run and a survivor's full-suite run all carry `-n 0`, which overrides pytest.ini's `-n auto`. An
    unmarked test file is used, because a `mutation`-marked one got `-n 0` before this rule existed."""
    monkeypatch.setenv("MUTATION_FULL_SUITE", "full-suite-stand-in")
    target = tmp_path / "target.py"
    target.write_text("VALUE = 1\n", encoding="utf-8")
    calls = []
    real = mutation_check.subprocess.run

    def run(cmd, *a, **k):
        if "pytest" in cmd:
            calls.append(list(cmd))
            return subprocess.CompletedProcess(cmd, 0, stdout="1 passed in 0.01s\n", stderr="")
        return real(cmd, *a, **k)
    monkeypatch.setattr(mutation_check.subprocess, "run", run)
    tests = ["tests/test_every_mutation_entry_runs_its_tests.py"]
    assert not mutation_check._marked_mutation(tests), "control: the listed test file is not marked"
    mutation_check.run([{"name": "serial worker", "file": str(target), "old": "VALUE = 1",
                         "new": "VALUE = 2", "tests": tests}], verbose=False)
    assert len(calls) == 3, "control: a pre-flight, a mutant run and the survivor's full-suite run"
    assert "full-suite-stand-in" in calls[2]
    for cmd in calls:
        i = cmd.index("-n")
        assert cmd[i + 1] == "0", cmd
    assert target.read_text(encoding="utf-8") == "VALUE = 1\n"


# ── a survivor says whether the registry or the suite is short ───────────────────────────────────────

def _fixture_suite(tmp_path, catcher: bool):
    """A target module, a listed test that never looks at it, and optionally an UNLISTED test that does."""
    suite = tmp_path / "suite"
    suite.mkdir()
    (suite / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
    target = tmp_path / "target.py"
    target.write_text("VALUE = 1\n", encoding="utf-8")
    (suite / "test_listed.py").write_text("def test_listed_looks_elsewhere():\n    assert True\n",
                                          encoding="utf-8")
    if catcher:
        (suite / "test_unlisted.py").write_text(
            "def test_unlisted_reads_the_target():\n"
            f"    assert open(r'{target}', encoding='utf-8').read() == 'VALUE = 1\\n'\n", encoding="utf-8")
    return suite, target


@pytest.mark.parametrize("catcher", [True, False], ids=["spec_gap", "full_suite"])
def test_a_survivor_is_classified_against_the_full_suite(tmp_path, monkeypatch, capsys, catcher):
    """Session 1, after PC2's survivor #428: SURVIVED_SPEC_GAP names the unlisted test that kills the
    mutant; SURVIVED says it survives the full suite. Both fail the gate."""
    suite, target = _fixture_suite(tmp_path, catcher)
    monkeypatch.setenv("MUTATION_FULL_SUITE", str(suite))
    rc = mutation_check.run([{"name": "registry check", "file": str(target), "old": "VALUE = 1",
                              "new": "VALUE = 2", "tests": [str(suite / "test_listed.py")]}])
    out = capsys.readouterr().out
    assert rc == 1, "a survivor of either kind fails the gate"
    line = [ln.strip() for ln in out.splitlines() if ln.strip().startswith("SURVIVED")]
    assert len(line) == 1, out
    if catcher:
        assert line[0].startswith("SURVIVED_SPEC_GAP: registry check -- killed outside its listed tests by:"), out
        assert "test_unlisted.py::test_unlisted_reads_the_target" in line[0]
    else:
        assert line[0] == "SURVIVED: registry check -- survives the full suite"
    assert target.read_text(encoding="utf-8") == "VALUE = 1\n", "the mutant was left in place"


def test_a_test_that_is_red_without_the_mutant_is_not_counted_as_its_killer(tmp_path, monkeypatch, capsys):
    """The full-suite run's reds are re-run on the unmutated code: an already-red test is not a kill."""
    suite, target = _fixture_suite(tmp_path, catcher=False)
    (suite / "test_already_red.py").write_text("def test_already_red():\n    assert False\n", encoding="utf-8")
    monkeypatch.setenv("MUTATION_FULL_SUITE", str(suite))
    mutation_check.run([{"name": "red elsewhere", "file": str(target), "old": "VALUE = 1",
                         "new": "VALUE = 2", "tests": [str(suite / "test_listed.py")]}])
    out = capsys.readouterr().out
    assert "SURVIVED: red elsewhere -- survives the full suite" in out, out
    assert "SPEC_GAP" not in out


def test_off_reports_the_survivor_as_not_classified_and_the_full_run_cannot_recurse(tmp_path, monkeypatch,
                                                                                  capsys):
    """MUTATION_FULL_SUITE=off: no full-suite run, and the report says the survivor was not classified
    rather than claiming it survives the suite. The full-suite run itself carries off, so a suite test
    that drives this tool with a survivor does not start a run of its own."""
    suite, target = _fixture_suite(tmp_path, catcher=True)
    monkeypatch.setenv("MUTATION_FULL_SUITE", "off")
    mutation_check.run([{"name": "unclassified", "file": str(target), "old": "VALUE = 1",
                         "new": "VALUE = 2", "tests": [str(suite / "test_listed.py")]}])
    assert "SURVIVED: unclassified -- not classified: MUTATION_FULL_SUITE is off" in capsys.readouterr().out
    envs = []
    real = mutation_check._pytest

    def spy(tests, env, tb="no"):
        envs.append(env.get("MUTATION_FULL_SUITE"))
        return real(tests, env, tb)
    monkeypatch.setattr(mutation_check, "_pytest", spy)
    monkeypatch.setenv("MUTATION_FULL_SUITE", str(suite))
    mutation_check.run([{"name": "classified", "file": str(target), "old": "VALUE = 1",
                         "new": "VALUE = 2", "tests": [str(suite / "test_listed.py")]}], verbose=False)
    assert envs[2:] and all(e == "off" for e in envs[2:]), envs


def test_the_parallel_runner_keeps_the_survivor_label():
    parsed = mutation_check_parallel._parse(
        "1/3 killed, 2 survived, 0 skipped\n"
        "  SURVIVED_SPEC_GAP: a -- killed outside its listed tests by: tests/t.py::x\n"
        "  SURVIVED: b -- survives the full suite\n")
    assert parsed["survived"] == ["SURVIVED_SPEC_GAP: a -- killed outside its listed tests by: tests/t.py::x",
                                  "SURVIVED: b -- survives the full suite"]


def test_the_test_session_turns_the_full_suite_run_off():
    """tests/conftest.py sets it, so a test that drives the gate with a survivor does not cost a full run."""
    assert os.environ.get("MUTATION_FULL_SUITE") == "off"
