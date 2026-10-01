"""release_check's suite legs take their xdist worker count from INSPEXIMUS_RELEASE_WORKERS.

The legs passed an explicit `-n` (up to 8) that no setting could change, so a pre-flight on a shared
machine could not be capped. The variable overrides the default, a value below 2 becomes 2, and a value
that is not an integer is ignored. Only the pool size moves: no assertion changes and no test is skipped.
"""
import os
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import release_check  # noqa: E402


@pytest.fixture(autouse=True)
def _no_env(monkeypatch):
    monkeypatch.delenv("INSPEXIMUS_RELEASE_WORKERS", raising=False)


def _argvs(monkeypatch):
    seen = []

    def fake(argv, *a, **k):
        seen.append(list(argv))
        return subprocess.CompletedProcess(argv, 0, stdout="1 passed in 0.1s\n", stderr="")
    monkeypatch.setattr(release_check.subprocess, "run", fake)
    return seen


def _n_values(seen):
    out = []
    for argv in seen:
        if "-n" in argv:
            out.append(argv[argv.index("-n") + 1])
    return out


def _full_suite(monkeypatch):
    seen = _argvs(monkeypatch)
    release_check.check_tests(release_check.Report(), ROOT)
    return [n for n in _n_values(seen) if n != "0"]      # the mutation-marked leg runs -n 0 on purpose


def _fast(monkeypatch, tmp_path):
    seen = _argvs(monkeypatch)
    monkeypatch.setattr(release_check, "fast_selection", lambda root: (["tests/test_x.py"], "v0", []))
    release_check.check_fast_tests(release_check.Report(), tmp_path)
    return [n for n in _n_values(seen) if n != "0"]


def test_the_value_reaches_the_full_suite_argv(monkeypatch):
    monkeypatch.setenv("INSPEXIMUS_RELEASE_WORKERS", "4")
    assert _full_suite(monkeypatch) == ["4"]


def test_the_value_reaches_both_fast_phase_argvs(monkeypatch, tmp_path):
    monkeypatch.setenv("INSPEXIMUS_RELEASE_WORKERS", "4")
    assert _fast(monkeypatch, tmp_path) == ["4", "4"]


@pytest.mark.parametrize("raw", ["abc", "", "  ", "4.5", "-"])
def test_an_invalid_value_falls_back_to_todays_default(monkeypatch, tmp_path, raw):
    default_full = _full_suite(monkeypatch)
    default_fast = _fast(monkeypatch, tmp_path)
    monkeypatch.setenv("INSPEXIMUS_RELEASE_WORKERS", raw)
    assert _full_suite(monkeypatch) == default_full
    assert _fast(monkeypatch, tmp_path) == default_fast == ["8", "8"]


@pytest.mark.parametrize("raw", ["1", "0", "-3"])
def test_a_value_below_two_becomes_two(monkeypatch, tmp_path, raw):
    monkeypatch.setenv("INSPEXIMUS_RELEASE_WORKERS", raw)
    assert _full_suite(monkeypatch) == ["2"]
    assert _fast(monkeypatch, tmp_path) == ["2", "2"]


def test_without_the_variable_the_defaults_are_unchanged(monkeypatch, tmp_path):
    expected = str(max(2, min(8, (os.cpu_count() or 4) - 1)))
    assert _full_suite(monkeypatch) == [expected]
    assert _fast(monkeypatch, tmp_path) == ["8", "8"]
