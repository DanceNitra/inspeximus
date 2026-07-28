"""What a green suite silently did not run.

A module-level `pytest.importorskip` removes an entire file as ONE skip line. `test_examples_run.py` holds
29 tests; without its dependency the run says "1 skipped". Across 21 modules that is 240 tests, and in the
CI base image 155 test functions in 16 modules had never executed -- while the job reported
"993 passed, 43 skipped" and read as healthy. Nobody chose that exclusion; it was the default, and the
default was invisible, which is the same defect shape as a check that cannot fail.

The count was first reported as 155 across 16 modules. That was wrong -- the census over-counted any file
whose HELPER mentioned importorskip -- and the honest figure is 102 across 11. The gap itself is real and
unchanged: the base job runs ~1001 tests where the integrations job runs 1144.

Two things now hold it open: a CI job that installs the optional dependencies so those tests actually run,
and this file, which PINS how much may hide. Growth is allowed only by editing the number here, in a diff
someone reads.
"""
import ast
import importlib.util
import os
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))
import skip_census  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

#: Measured 2026-07-27 against a simulated CI base image (pytest + cryptography only): 102 test functions
#: across 11 modules, the same on every matrix leg.
#:
#: CORRECTION. This first read 155, and 175 on 3.9, and BOTH were inflated by a bug in the census itself:
#: it read the source text of each top-level node, and a `def` is a top-level node whose text includes its
#: body -- so any file with a `pytest.importorskip` inside a helper was counted as entirely invisible.
#: `test_install.py` was the interpreter-dependent case, and its `tomllib` guard is inside a single test
#: function, so it skips ONE test on 3.9 and always collected. There is no per-version difference, and the
#: split pin that chased one is gone. The gap it measures is real -- the base job runs ~1001 tests and the
#: integrations job 1144 -- only the per-module attribution was wrong.
#: RAISED 110 -> 122 on 2026-07-28, and the slack removed. Three audit files added this cycle need an
#: optional dependency and are therefore invisible to the base job, though the `tests` CI job (full extras)
#: does run them and now gates publish:
#:     test_mcp_verification_gaps.py (7)        needs mcp   -- verify_audit_bundle / compliance_check
#:     test_release_cannot_publish_untested.py (5) needs mcp
#:     test_mcp_key_binding.py (8)              needs mcp + cryptography -- the signing-key pin
#: RAISED 122 -> 130 on 2026-07-28: test_mcp_erasure_attribution.py (8), needs mcp -- the erasure surface
#: could not reach exact=True or attribute a tombstone. Same job runs it.
#:
#: 102 measured + 12 + 8 + 8 = 130. The pin now sits EXACTLY on the measured number rather than carrying 8 of
#: headroom, because the headroom is what let the first 12 land unnoticed: CI went red at bf4f8a5 and two
#: further commits were pushed on top of it before a full-suite run caught up. A pin with slack absorbs
#: exactly the growth it exists to make someone read.
#: RAISED 130 -> 146 on 2026-07-28, for the two provenance files that closed the erasure-reachability gap:
#:     test_mcp_provenance_reach.py (10)          needs mcp
#:     test_write_paths_provenance_census.py (6)  needs mcp
#: Both shipped WITHOUT the importorskip guard and turned CI red: `mcp_server` raises ImportError by
#: design, so on a base install the modules ERROR at setup rather than skipping, and an erroring fixture
#: is a failure. The guard is the fix; this pin is the consequence, and it stays on the measured number.
#: LOWERED 146 -> 144 on 2026-07-29. Two CLI arms moved out of the mcp-guarded file into
#: test_cli_provenance.py, which needs nothing optional -- the guard added to fix CI had been skipping
#: them too, narrowing coverage of one thing as a side effect of fixing another.
#: The move also exposed a defect in the DETECTOR: it text-searched each top-level node for
#: "importorskip", and a module DOCSTRING is a top-level node, so a file whose prose merely MENTIONED
#: `pytest.importorskip("mcp")` was counted as entirely hidden -- inflating this pin by 6 with tests that
#: were never hidden. That is worse than under-counting: a pin raised to cover phantoms then absorbs the
#: real growth it exists to surface. It now matches a CALL, not the word.
MAX_HIDDEN_IN_BASE_ENV = 144


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


def test_every_matrix_leg_hides_the_same_thing():
    """The legs must not differ, and if they ever do the reason has to be nameable rather than absorbed by
    a bigger pin. An earlier version of this test asserted a difference that did not exist: the census's
    own over-counting invented one, and the pin was split per interpreter to accommodate it."""
    hidden = {r["module"] for r in _base_env_census()["hidden_modules"]}
    assert "test_install.py" not in hidden,         "its tomllib guard is inside a single test, so it skips one test and the module still collects"
    assert "test_examples_run.py" not in hidden, "same shape: the guard is inside one test"


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


def test_a_guard_inside_a_function_is_not_a_module_level_guard():
    """The census read the SOURCE TEXT of each top-level node, and a `def` is a top-level node whose text
    includes its body -- so a helper containing `pytest.importorskip` made the whole file count as hidden.
    It over-counted by 9 the first time a new test file used that ordinary pattern, and the pin above is
    what caught it: an instrument crying wolf about itself.

    A guard inside a function skips ONE test and shows up as one skip line. That is visible, which is the
    entire distinction this tool exists to draw."""
    import ast

    src = ('import pytest\n'
           'def helper():\n'
           '    pytest.importorskip("mcp")\n'
           'def test_x():\n'
           '    helper()\n')
    assert skip_census._module_level_guards(src, ast.parse(src)) == []

    module_level = 'import pytest\npytest.importorskip("mcp")\ndef test_x():\n    pass\n'
    assert skip_census._module_level_guards(module_level, ast.parse(module_level)) == ["mcp"]


def test_a_guard_is_a_call_not_the_word():
    """The detector text-searched each top-level node, and a module DOCSTRING is a top-level node -- so a
    file whose prose merely MENTIONED `pytest.importorskip("mcp")` was read as entirely hidden. It
    inflated the pin by 6 with tests that were never hidden, which is worse than under-counting: a pin
    raised to cover phantoms absorbs the real growth it exists to make someone read."""
    doc = '"""No guard here. We deliberately removed pytest.importorskip(\'mcp\')."""\nimport os\n'
    assert skip_census._module_level_guards(doc, ast.parse(doc)) == []
    comment = '# pytest.importorskip("mcp") was removed on purpose\nimport os\n'
    assert skip_census._module_level_guards(comment, ast.parse(comment)) == []


def test_every_real_guard_shape_is_still_detected():
    """CONTROL. A detector that stopped matching would make every guarded file look visible, which is the
    same error pointing the other way and would drive the pin to zero."""
    for src, want in (
        ('import pytest\npytest.importorskip("mcp")\n', ["mcp"]),
        ('import pytest\nm = pytest.importorskip("yaml")\n', ["yaml"]),          # assigned
        ('from pytest import importorskip\nimportorskip("crewai")\n', ["crewai"]),  # bare name
        ('import pytest\npytest.importorskip("mcp")\npytest.importorskip("cryptography")\n',
         ["cryptography", "mcp"]),
    ):
        assert skip_census._module_level_guards(src, ast.parse(src)) == want, src


def test_the_try_except_form_still_counts():
    """It gates on this node not BEING an importorskip, not on it containing no calls -- conditioning on
    'no calls' killed this branch outright, because such a block ends in `pytest.skip(...)`, a call."""
    src = ('try:\n    import haystack\nexcept ImportError:\n'
           '    import pytest; pytest.skip("no haystack", allow_module_level=True)\n')
    assert "haystack" in skip_census._module_level_guards(src, ast.parse(src))
