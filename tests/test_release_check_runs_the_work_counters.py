"""release_check runs the work-counter gate, and --skip-tests does not switch it off.

3.15.2's release head passed every leg of tools/release_check.py, was pushed, and CI failed on the job
"work counters must not grow" alone. tools/release.py's pre-flight runs release_check with --skip-tests
and treats a SKIP as passing, so the leg must run under --skip-tests, and only a flag named for what it
costs may skip it.
"""
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import release_check  # noqa: E402


def _tree(tmp_path, body):
    (tmp_path / "perf").mkdir()
    (tmp_path / "perf" / "gate.py").write_text(body, encoding="utf-8")
    return tmp_path


def _leg(tmp_path, body=None, skip=False):
    root = _tree(tmp_path, body) if body is not None else tmp_path
    rep = release_check.Report()
    release_check.check_work_counters(rep, root, skip=skip)
    [(name, status, detail)] = rep.rows
    assert name == "work counters"
    return status, detail


def test_a_counter_that_grew_fails_the_leg_and_is_named(tmp_path):
    status, detail = _leg(tmp_path, "import sys\nprint('PERFORMANCE REGRESSION:')\n"
                                    "print('  erase_k200_n2000.erase_items_reads: 7 -> 9 (grew)')\nsys.exit(1)\n")
    assert status == release_check.FAIL, detail
    assert "erase_items_reads: 7 -> 9" in detail, detail


def test_a_clean_gate_passes_the_leg(tmp_path):
    status, detail = _leg(tmp_path, "import sys\nassert sys.argv[1:] == ['check'], sys.argv\n")
    assert status == release_check.PASS, detail


def test_a_missing_gate_fails_rather_than_being_skipped(tmp_path):
    status, detail = _leg(tmp_path)
    assert status == release_check.FAIL, detail


def test_only_the_named_flag_skips_it_and_the_run_then_cannot_clear_a_release(tmp_path):
    status, detail = _leg(tmp_path, "raise SystemExit(0)\n", skip=True)
    assert status == release_check.SKIP and release_check.SKIP_COUNTERS_FLAG in detail, detail


@pytest.mark.parametrize("argv, skipped", [
    (["--skip-tests"], False),
    ([], False),
    (["--skip-tests", release_check.SKIP_COUNTERS_FLAG], True),
])
def test_skip_tests_alone_still_runs_the_counters(tmp_path, monkeypatch, argv, skipped):
    """The run as release.py's pre-flight makes it, every other leg stubbed so only the wiring is tested."""
    seen = []
    for name in dir(release_check):
        if name.startswith("check_") and name != "check_work_counters":
            monkeypatch.setattr(release_check, name, lambda *a, **k: True)
    monkeypatch.setattr(release_check, "check_work_counters",
                        lambda rep, root=None, skip=False: seen.append(skip))
    (tmp_path / "pyproject.toml").write_text('[project]\nversion = "0.0.0"\n', encoding="utf-8")
    release_check.main(argv + ["--root", str(tmp_path)])
    if not seen:
        pytest.fail("control: run() never reached the work-counter leg, so nothing was tested")
    assert seen == [skipped], seen
