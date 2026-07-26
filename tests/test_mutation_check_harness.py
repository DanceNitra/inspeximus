"""The teeth-checker's own teeth.

`tools/mutation_check.py` decides whether every other test in this repository asserts a behaviour or a
spelling. It began as an ad-hoc snippet that ran pytest with `-x -rf`, and those two flags inverted its
verdict: `-x` stopped at the first problem and `-rf` printed only FAILED to the summary, never ERROR. A
mutant killed through a FIXTURE therefore produced a summary with no FAILED lines, and the harness
announced `SURVIVES <<< NO TEETH` about a mutant its tests had killed four times.

Nothing downstream could have caught that. A verdict tool that cannot see one class of kill reads exactly
like a clean result, and every conclusion drawn from it inherits the error in silence. So the harness gets
the treatment it hands out.
"""
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))
import mutation_check  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_a_setup_error_counts_as_a_kill():
    """THE regression guard. This summary is what pytest prints when a mutant breaks a module-scoped
    fixture: no test ever runs, so nothing is FAILED, and the old harness read that as survival."""
    summary = ("ERROR tests/test_echo_policy_panel.py::test_safe_blocks_every_echo\n"
               "ERROR tests/test_echo_policy_panel.py::test_trusting_is_the_exact_mirror - As...\n")
    assert mutation_check._killers(summary) == ["test_safe_blocks_every_echo",
                                                "test_trusting_is_the_exact_mirror"]


def test_a_failure_counts_as_a_kill():
    assert mutation_check._killers("FAILED tests/t.py::test_x\n") == ["test_x"]


def test_a_clean_run_is_not_read_as_a_kill():
    """The other direction: if this ever returned something for a green run, every mutant would look dead
    and the harness would certify teeth that are not there."""
    assert mutation_check._killers("12 passed in 0.4s\n") == []


def test_prose_mentioning_failures_is_not_mistaken_for_a_kill():
    """Tracebacks and docstrings say the word. Only pytest's own summary lines start with it."""
    assert mutation_check._killers("  the test FAILED because ...\nE   assert ERROR\n") == []


def test_the_harness_reports_a_kill_that_happens_only_at_setup():
    """End-to-end, through real pytest: this mutation dies ONLY as a setup ERROR, because the one test
    named here depends on the probe running clean. Under the old flags it read as SURVIVES."""
    rc = mutation_check.run([{
        "name": "default policy drifts (kills via fixture only)",
        "file": "inspeximus/core.py",
        "old": 'policy: str = "safe"',
        "new": 'policy: str = "trusting"',
        "tests": ["tests/test_echo_policy_panel.py::test_the_default_policy_is_the_safe_one"],
    }], verbose=False)
    assert rc == 0, "a mutant killed at fixture setup was reported as surviving"


def test_the_harness_restores_the_file_it_mutated():
    """It edits the shipped source in place. A crash mid-run that left `trusting` on disk would be a
    defect shipped by the tool meant to prevent them."""
    before = open(os.path.join(ROOT, "inspeximus", "core.py"), encoding="utf-8").read()
    mutation_check.run([{"name": "restore check", "file": "inspeximus/core.py",
                         "old": 'policy: str = "safe"', "new": 'policy: str = "trusting"',
                         "tests": ["tests/test_mutation_check_harness.py::test_a_failure_counts_as_a_kill"]}],
                       verbose=False)
    assert open(os.path.join(ROOT, "inspeximus", "core.py"), encoding="utf-8").read() == before


def test_an_ambiguous_or_absent_target_is_skipped_not_scored():
    """Mutating the wrong one of five matches would score a verdict about code nobody meant to test."""
    assert mutation_check.run([{"name": "absent", "file": "inspeximus/core.py",
                                "old": "this string is not in the file", "new": "x",
                                "tests": ["tests/test_mutation_check_harness.py::test_a_failure_counts_as_a_kill"]}],
                              verbose=False) == 0


def test_the_committed_spec_is_not_empty_and_names_real_targets():
    """An empty or stale spec makes the CI gate pass over nothing -- the same silence in a different place."""
    with open(os.path.join(ROOT, "tools", "mutations.json"), encoding="utf-8") as fh:
        spec = json.load(fh)
    assert spec, "tools/mutations.json is empty: the gate would pass over zero mutations"
    for mut in spec:
        assert set(mut) >= {"name", "file", "old", "new", "tests"}, mut
        path = os.path.join(ROOT, mut["file"])
        assert os.path.exists(path), f"{mut['name']} targets a file that is gone: {mut['file']}"
        assert open(path, encoding="utf-8").read().count(mut["old"]) == 1, \
            f"{mut['name']}: target is absent or ambiguous, so it would be silently skipped"


def test_the_cli_refuses_an_empty_spec():
    import tempfile
    p = os.path.join(tempfile.mkdtemp(), "empty.json")
    with open(p, "w", encoding="utf-8") as fh:
        fh.write("[]")
    r = subprocess.run([sys.executable, os.path.join("tools", "mutation_check.py"), p],
                       cwd=ROOT, capture_output=True, text=True,
                       env={**os.environ, "PYTHONIOENCODING": "utf-8"})
    assert r.returncode != 0, "a run over zero mutations must not exit green"
