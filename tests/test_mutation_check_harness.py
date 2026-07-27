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


def test_a_mutation_run_leaves_no_tracked_file_dirty():
    """A mutant does not only change code: the tests it runs execute PROBES, and probes write result
    files that are TRACKED. Restoring only the mutated source left
    `probes/echo_policy_panel_result.json` holding the MUTANT's output — safe = 0.00 echo-blocked /
    1.00 reaffirm-honored, the exact inverse of the number our shipped docstring publishes, plus three
    "problems" declaring our own claim wrong. Sitting in the working tree, tracked, one `git add -A` from
    being published as a receipt. It nearly was.

    Asserted directly rather than through a side effect: the property is "the run leaves the repository as
    it found it"."""
    import subprocess

    def dirty():
        return subprocess.run(["git", "status", "--porcelain", "--untracked-files=no"],
                              cwd=ROOT, capture_output=True, text=True).stdout

    before = dirty()
    mutation_check.run([{
        "name": "flip the echo guard (its probe writes a tracked receipt)",
        "file": "probes/echo_policy_panel.py",
        "old": "m.echo_guard = True", "new": "m.echo_guard = False",
        "tests": ["tests/test_echo_policy_panel.py"],
    }], verbose=False)
    assert dirty() == before, "the mutation run left tracked files modified"


def test_the_receipt_still_holds_the_number_we_publish():
    """The concrete artifact, checked by value. If a mutation run ever leaves this inverted again, this
    fails rather than waiting for someone to notice a published receipt disagreeing with the product."""
    import json

    with open(os.path.join(ROOT, "probes", "echo_policy_panel_result.json"), encoding="utf-8") as fh:
        rows = {r["policy"]: r for r in json.load(fh)["rows"]}
    assert rows["safe"]["echo_blocked"] == 1.0 and rows["safe"]["reaffirm_honored"] == 0.0, rows["safe"]
    assert rows["trusting"]["echo_blocked"] == 0.0 and rows["trusting"]["reaffirm_honored"] == 1.0


def test_restore_touches_only_probe_result_artifacts():
    """It ate a real one-line fix to core.py.

    The harness restores tracked files that became dirty during a run, so a probe's result artifact cannot
    survive as a falsified receipt. But an edit made by a HUMAN while the gate runs in the background is
    dirty during the run too, and `git checkout --` cannot tell the difference. It silently discarded work,
    and the only reason it was noticed was a measurement afterwards that stopped making sense.

    Asserted on `_restore` DIRECTLY. Two earlier versions of this test were worse than useless: the first
    dirtied the file BEFORE calling run(), which puts it in `dirty_before` where it is safe even from the
    unfixed code; the second edited it from a thread mid-run, which made the outcome depend on whether the
    run happened to outlast a sleep. A property this important does not get a racy test."""
    victims = ["inspeximus/core.py", "tests/test_x.py", "README.md",
               "probes/echo_policy_panel_result.json", "probes/agentpoison_influence_gate_result.json"]
    restored = mutation_check._restore(victims)

    assert set(restored) == {"probes/echo_policy_panel_result.json",
                             "probes/agentpoison_influence_gate_result.json"}, restored
    for src in ("inspeximus/core.py", "tests/test_x.py", "README.md"):
        assert src not in restored, f"the harness would revert {src}, which it did not write"


def test_the_artifact_pattern_does_not_match_source_that_merely_lives_in_probes():
    """`probes/locomo_qa.py` is a committed module, not a receipt. The rule is the FILENAME shape, not the
    directory."""
    assert mutation_check._ARTIFACT.match("probes/echo_policy_panel_result.json")
    for other in ("probes/locomo_qa.py", "probes/echo_policy_panel.py", "inspeximus/core.py",
                  "probes/result.json", "memory.json"):
        assert not mutation_check._ARTIFACT.match(other), other
