"""Publishing must be downstream of the test suite — asserted on the workflow, not on intent.

1.86.0 went to PyPI while `tests` was RED on the same commit. Not because anyone disabled a check:
`release.yml` had a job literally named "audits must pass first", and it meant it — but it ran
`claims_audit.py` and `governance_audit.py`, while the pytest suite lived in a SEPARATE workflow
(`ci.yml`) that release.yml did not depend on. Two workflows on one push, neither waiting for the
other. The wheel happened to be correct that day (the red was stale version strings in two registry
manifests), which is luck, not a guarantee.

It cannot be repaired afterwards either: RELEASING.md forbids retagging a published version to turn a
red run green. So the only place this can be fixed is before the publish, as a dependency.

This walks the `needs` graph from the publishing job to make sure the suite is genuinely upstream —
a job inserted later that quietly drops the edge is caught here rather than on PyPI.
"""
from __future__ import annotations

import os

import pytest

yaml = pytest.importorskip("yaml")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WF = os.path.join(ROOT, ".github", "workflows", "release.yml")


def _jobs() -> dict:
    with open(WF, encoding="utf-8") as fh:
        return (yaml.safe_load(fh) or {}).get("jobs") or {}


def _needs(job: dict) -> list:
    n = job.get("needs") or []
    return [n] if isinstance(n, str) else list(n)


def _upstream(jobs: dict, start: str) -> set:
    """Every job that must succeed before `start` runs."""
    seen, stack = set(), list(_needs(jobs.get(start) or {}))
    while stack:
        j = stack.pop()
        if j in seen or j not in jobs:
            continue
        seen.add(j)
        stack.extend(_needs(jobs[j]))
    return seen


def test_the_publishing_job_exists_and_is_named_what_we_think():
    jobs = _jobs()
    assert "publish" in jobs, f"no `publish` job in release.yml; jobs are {sorted(jobs)}"


def test_publish_cannot_run_before_the_test_suite():
    jobs = _jobs()
    up = _upstream(jobs, "publish")
    assert "tests" in up, (
        "`publish` does not depend on the test suite — a red suite would publish to PyPI again. "
        f"upstream of publish: {sorted(up) or 'nothing'}")


def test_publish_still_depends_on_the_audits():
    """The guarantee that was already there must not be traded away for the new one."""
    up = _upstream(_jobs(), "publish")
    assert "audit" in up, f"`publish` lost its audit dependency; upstream: {sorted(up)}"


def test_the_test_job_actually_runs_pytest():
    """A `needs:` edge to a job that runs nothing is a dependency on a formality."""
    job = _jobs().get("tests") or {}
    steps = " ".join(str(s.get("run", "")) for s in (job.get("steps") or []))
    assert "pytest" in steps, f"the `tests` job never invokes pytest: {steps[:200]!r}"


def test_the_guard_can_fail(tmp_path):
    """The control: a workflow whose publish skips the suite must be rejected by this same walk."""
    bad = {"jobs": {"tests": {}, "audit": {}, "build": {"needs": ["audit"]},
                    "publish": {"needs": ["build"]}}}
    p = tmp_path / "release.yml"
    with open(p, "w", encoding="utf-8") as fh:
        yaml.safe_dump(bad, fh)
    with open(p, encoding="utf-8") as fh:
        jobs = (yaml.safe_load(fh) or {}).get("jobs") or {}
    assert "tests" not in _upstream(jobs, "publish"), \
        "the walk cannot detect a missing dependency, so the assertions above prove nothing"
