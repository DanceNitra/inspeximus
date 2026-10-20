"""A mutant run that proves nothing is NOT EVALUATED, never SURVIVED and never KILLED (3.21.1 stress run 1).

In the first CI-like load pass of 3.21.1, `the replay filter loses r"\\bgit\\s+checkout..."` came back SURVIVED.
Its test is a pure function call that must fail with that mutant, and it was killed on every idle run. The
gate called a mutant SURVIVED whenever the run printed no FAILED or ERROR line, without reading the exit code
or the count, so a run that crashed or printed nothing read like a run in which every test passed. The same
blindness worked the other way: any ERROR line, a collection error included, read as a kill.

Each case here hands `mutation_check.run` a fake pytest run, so the verdict depends only on what the run
printed and how it exited. The target is a file in tmp_path."""
import os
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))
import mutation_check  # noqa: E402

PRE = subprocess.CompletedProcess([], 0, "2 passed in 0.10s\n", "")


def _gate(tmp_path, monkeypatch, capsys, post):
    """Run the gate on one entry: the pre-flight passes 2 tests, then the mutant run returns `post`."""
    target = tmp_path / "target.py"
    target.write_text("VALUE = 1\n", encoding="utf-8")
    listed = str(tmp_path / "test_listed.py")
    runs = iter([PRE, post])

    def fake(tests, env, tb="no"):
        r = next(runs)
        if isinstance(r, Exception):
            raise r
        return r

    monkeypatch.setattr(mutation_check, "_pytest", fake)
    monkeypatch.setenv("MUTATION_FULL_SUITE", "off")
    rc = mutation_check.run([{"name": "one entry", "file": str(target), "old": "VALUE = 1", "new": "VALUE = 2",
                              "tests": [listed]}], verbose=True)
    assert target.read_text(encoding="utf-8") == "VALUE = 1\n", "the gate must restore the target"
    return rc, capsys.readouterr().out


def _run(rc, out):
    return subprocess.CompletedProcess([], rc, out, "")


def _not_evaluated(rc, out):
    assert rc != 0, out
    assert "NOT EVALUATED" in out, out
    assert "SURVIVES" not in out and "killed by" not in out, out


def test_an_internal_error_with_no_output_is_not_evaluated(tmp_path, monkeypatch, capsys):
    _not_evaluated(*_gate(tmp_path, monkeypatch, capsys, _run(3, "")))


def test_an_internal_error_after_a_full_pass_line_is_not_evaluated(tmp_path, monkeypatch, capsys):
    # The count matches the pre-flight; only the exit code says the run did not finish cleanly.
    _not_evaluated(*_gate(tmp_path, monkeypatch, capsys, _run(3, "2 passed in 0.10s\nINTERNALERROR> boom\n")))


def test_an_internal_error_beside_a_failed_line_is_not_a_kill(tmp_path, monkeypatch, capsys):
    out = "FAILED test_listed.py::test_catches - assert 2 == 1\nINTERNALERROR> boom\n"
    _not_evaluated(*_gate(tmp_path, monkeypatch, capsys, _run(3, out)))


@pytest.mark.parametrize("rc", [1, 2])
def test_a_collection_error_is_not_a_kill(tmp_path, monkeypatch, capsys, rc):
    out = "ERROR test_listed.py - ImportError: cannot import name 'x'\nInterrupted: 1 error during collection\n"
    _not_evaluated(*_gate(tmp_path, monkeypatch, capsys, _run(rc, out)))


def test_a_failure_of_a_test_outside_the_list_is_not_a_kill(tmp_path, monkeypatch, capsys):
    out = "FAILED test_other.py::test_something - assert False\n1 failed, 2 passed in 0.10s\n"
    _not_evaluated(*_gate(tmp_path, monkeypatch, capsys, _run(1, out)))


def test_a_passing_run_with_fewer_tests_than_the_pre_flight_is_not_a_survivor(tmp_path, monkeypatch, capsys):
    _not_evaluated(*_gate(tmp_path, monkeypatch, capsys, _run(0, "1 passed in 0.10s\n")))


def test_a_timeout_is_not_evaluated(tmp_path, monkeypatch, capsys):
    calls = []

    def slow(*a, **k):
        calls.append(1)
        raise subprocess.TimeoutExpired(a[0], 1800, output=b"1 passed", stderr=None)

    monkeypatch.setattr(mutation_check.subprocess, "run", slow)
    r = mutation_check._pytest(["t.py"], {})
    monkeypatch.undo()
    assert calls and r.returncode is None, r
    assert mutation_check._verdict(r, ["t.py"], 1)[0] == "not evaluated"


def test_the_report_names_the_exit_code_the_counts_and_the_tails(tmp_path, monkeypatch, capsys):
    post = subprocess.CompletedProcess([], 3, "INTERNALERROR> boom\n", "Fatal Python error: out of memory\n")
    rc, out = _gate(tmp_path, monkeypatch, capsys, post)
    for part in ("exit 3", "0 passed, 2 before mutating", "INTERNALERROR> boom", "out of memory"):
        assert part in out, (part, out)


def test_control_a_listed_failure_is_still_a_kill(tmp_path, monkeypatch, capsys):
    """CONTROL: the stricter rule must not turn a real kill into a skip."""
    out = "FAILED test_listed.py::test_catches - assert 2 == 1\n1 failed, 1 passed in 0.10s\n"
    rc, text = _gate(tmp_path, monkeypatch, capsys, _run(1, out))
    assert rc == 0 and "killed by test_catches" in text, text


def test_control_a_listed_setup_error_is_still_a_kill(tmp_path, monkeypatch, capsys):
    """CONTROL: a fixture that refuses to build is the test failing (test_mutation_check_harness.py)."""
    out = "ERROR test_listed.py::test_catches - RuntimeError: fixture\n1 passed, 1 error in 0.10s\n"
    rc, text = _gate(tmp_path, monkeypatch, capsys, _run(1, out))
    assert rc == 0 and "killed by test_catches" in text, text


def test_control_a_clean_full_pass_is_still_a_survivor(tmp_path, monkeypatch, capsys):
    """CONTROL: a mutant every listed test saw and missed is still called one."""
    rc, text = _gate(tmp_path, monkeypatch, capsys, _run(0, "2 passed in 0.10s\n"))
    assert rc != 0 and "SURVIVES" in text and "NOT EVALUATED" not in text, text


@pytest.mark.parametrize("nodeid,listed,want", [
    ("tests/test_a.py::test_x", ["C:/repo/tests/test_a.py"], True),
    ("tests/test_a.py::test_x[p]", ["tests/test_a.py::test_x"], True),
    ("tests/test_a.py::test_y", ["tests/test_a.py::test_x"], False),
    ("tests/test_b.py::test_x", ["tests/test_a.py"], False),
    ("tests/test_a.py::test_x", ["tests"], True),
])
def test_a_reported_node_is_matched_against_the_listed_tests(nodeid, listed, want):
    assert mutation_check._is_listed(nodeid, listed) is want
