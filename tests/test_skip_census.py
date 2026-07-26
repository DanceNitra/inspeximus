"""What a green suite silently did not run.

A module-level `pytest.importorskip` removes an entire file as ONE skip line. `test_examples_run.py` holds
29 tests; without its dependency the run says "1 skipped". Across 21 modules that is 240 tests, and in the
CI base image 155 test functions in 16 modules had never executed -- while the job reported
"993 passed, 43 skipped" and read as healthy. Nobody chose that exclusion; it was the default, and the
default was invisible, which is the same defect shape as a check that cannot fail.

Two things now hold it open: a CI job that installs the optional dependencies so those tests actually run,
and this file, which PINS how much may hide. Growth is allowed only by editing the number here, in a diff
someone reads.
"""
import importlib.util
import os
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))
import skip_census  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

#: Measured 2026-07-26 against a simulated CI base image (pytest + cryptography only).
#: It is INTERPRETER-DEPENDENT, and pinning one number for the whole matrix failed on 3.9 within minutes:
#: `tomllib` entered the standard library in 3.11, so on 3.9 `test_install.py` (13 tests) is hidden too.
#: That is a real, explainable stdlib fact rather than a silent exclusion, so the pin follows it instead of
#: being raised to the maximum -- a pin set to the worst leg stops constraining the others.
MAX_HIDDEN_IN_BASE_ENV = 160 if sys.version_info >= (3, 11) else 175


def _base_env_census():
    """What the base CI image would see. Simulated by making the optional packages unfindable, so the
    number does not depend on what happens to be installed on the machine running this test -- the exact
    mistake that put a false failure in CI twice today."""
    # `tomllib` is stdlib from 3.11 only, so whether it is "in the base image" is a property of the
    # interpreter. Deriving it rather than hard-coding it keeps this honest on every matrix leg.
    base = {"pytest", "cryptography", "inspeximus", "json", "os", "sys"}
    if sys.version_info >= (3, 11):
        base.add("tomllib")
    real = importlib.util.find_spec
    try:
        importlib.util.find_spec = lambda n, *a, **k: (real(n, *a, **k)
                                                       if n.split(".")[0] in base else None)
        return skip_census.census()
    finally:
        importlib.util.find_spec = real


def test_the_amount_hidden_from_the_base_job_is_pinned():
    c = _base_env_census()
    assert c["hidden_here"] <= MAX_HIDDEN_IN_BASE_ENV, (
        f"{c['hidden_here']} test functions are invisible to the base CI job, above the pinned "
        f"{MAX_HIDDEN_IN_BASE_ENV}. A whole module skipped shows up as one line, so this grows without "
        f"anyone noticing. Modules: "
        + ", ".join(f"{r['module']}({r['tests']})" for r in c["hidden_modules"]))


def test_the_three_nine_leg_hides_exactly_one_more_module_and_we_know_which():
    """The matrix legs must differ for a REASON we can name. If 3.9 ever starts hiding something else, the
    difference stops being explainable and this fails rather than being absorbed by a bigger pin."""
    c = _base_env_census()
    hidden = {r["module"] for r in c["hidden_modules"]}
    if sys.version_info >= (3, 11):
        assert "test_install.py" not in hidden, "tomllib is stdlib here; nothing should hide it"
    else:
        assert "test_install.py" in hidden, "on 3.9 tomllib is absent, so this module cannot collect"


def test_the_census_is_not_measuring_nothing():
    """If the parse or the guard-detection broke, every assertion here would pass over an empty set --
    a green result computed from a collection that was never populated."""
    c = _base_env_census()
    assert c["total_tests"] > 500, c["total_tests"]
    assert c["hidden_here"] > 0, ("the base image cannot see everything; if this ever legitimately "
                                  "becomes zero, delete the pin rather than leaving a check that "
                                  "cannot fail")
    # Named because they are the largest, and because naming them is what makes a rise in the pin above
    # readable. `test_examples_run.py` is deliberately NOT here: my first scan called it module-guarded and
    # this assertion caught that -- its importorskip sits inside a single test, so it does run in CI.
    hidden = {r["module"] for r in c["hidden_modules"]}
    assert {"test_mcp_behaviour.py", "test_google_adk.py", "test_langgraph_integration.py"} <= hidden, \
        sorted(hidden)


def test_the_census_counts_by_parsing_not_by_importing():
    """An environment must not be able to hide a test from the census. This is the property that makes the
    number comparable between this box and CI at all."""
    c = skip_census.census()
    assert c["total_tests"] >= _base_env_census()["total_tests"], \
        "the total is a property of the repository and must not move with the environment"


def test_ci_installs_the_optional_dependencies_somewhere():
    """The pin above limits the damage; this is what actually removes it. If the integrations job is ever
    deleted, the 155 quietly stop running again and every other test here still passes."""
    with open(os.path.join(ROOT, ".github", "workflows", "ci.yml"), encoding="utf-8") as fh:
        ci = fh.read()
    assert "integrations:" in ci, "the job that runs the guarded modules is gone"
    for extra in ("mcp", "langgraph", "crewai", "google-adk"):
        assert extra in ci, f"the integrations job no longer installs {extra}"
    assert "haystack" in ci, "haystack is not in pyproject's extras, so it must be installed explicitly"


def test_the_census_tool_runs_and_exits_clean():
    r = subprocess.run([sys.executable, os.path.join("tools", "skip_census.py"), "--json"],
                       cwd=ROOT, capture_output=True, text=True,
                       env={**os.environ, "PYTHONIOENCODING": "utf-8"})
    assert r.returncode == 0, r.stderr[-800:]
    import json
    assert json.loads(r.stdout)["total_tests"] > 500
