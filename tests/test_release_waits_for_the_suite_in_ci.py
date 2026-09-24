"""The release check no longer runs the whole suite locally, so it must not clear a release until CI has.

`ci_verdict` decides on the runs `gh` lists for HEAD. The `tests` workflow runs the full suite; if it
is missing or still running, the answer is SKIP (the release is not cleared), never PASS. A red run
anywhere is FAIL.
"""
from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import release_check as rc  # noqa: E402

HEAD = "0123456789abcdef"


def run(name, status="completed", conclusion="success"):
    return {"name": name, "status": status, "conclusion": conclusion, "url": "https://x/" + name}


def test_a_finished_green_suite_clears():
    status, _ = rc.ci_verdict([run("tests"), run("audit")], HEAD, required=("tests",))
    assert status == rc.PASS


def test_a_suite_still_running_does_not_clear():
    runs = [run("tests", status="in_progress", conclusion=""), run("audit")]
    status, detail = rc.ci_verdict(runs, HEAD, required=("tests",))
    assert status == rc.SKIP and "not finished" in detail


def test_a_missing_suite_run_does_not_clear():
    status, detail = rc.ci_verdict([run("audit")], HEAD, required=("tests",))
    assert status == rc.SKIP and "UNVERIFIED" in detail


def test_a_red_run_fails():
    status, _ = rc.ci_verdict([run("tests", conclusion="failure"), run("audit")], HEAD,
                              required=("tests",))
    assert status == rc.FAIL


def test_CONTROL_without_the_requirement_a_running_suite_would_have_passed():
    # The old check read only COMPLETED runs, so a suite still in progress was invisible to it.
    runs = [run("tests", status="in_progress", conclusion=""), run("audit")]
    status, _ = rc.ci_verdict(runs, HEAD)
    assert status == rc.PASS


def test_the_default_run_requires_the_suite_workflow():
    import inspect
    src = inspect.getsource(rc.run)
    assert "required=() if (skip_tests or full_local) else (SUITE_WORKFLOW,)" in src
    assert rc.SUITE_WORKFLOW == "tests"
