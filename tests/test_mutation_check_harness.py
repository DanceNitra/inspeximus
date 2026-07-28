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
    """Mutating the wrong one of five matches would score a verdict about code nobody meant to test —
    and a target that is absent must not be counted as evaluated either. See the next test for the
    exit code, which is the half of this that was wrong."""
    rc = mutation_check.run([{"name": "absent", "file": "inspeximus/core.py",
                              "old": "this string is not in the file", "new": "x",
                              "tests": ["tests/test_mutation_check_harness.py::test_a_failure_counts_as_a_kill"]}],
                            verbose=False)
    assert rc != 0, "a mutation that was never applied must not be reported as a clean run"


def test_a_skip_is_not_a_pass():
    """This returned 0 for a run in which a mutation was never evaluated.

    Measured today on the committed spec: `74/75 killed, 0 survived, 1 skipped`, exit code 0. CI reads the
    exit code, so a mutation whose target had drifted — or whose tests were already red, which is exactly
    what happened — was announced on one line and then counted as though it had been checked. The tool
    whose entire purpose is to catch "a check that cannot report what it looks for reads like a clean
    result" was doing it.
    """
    absent = {"name": "absent", "file": "inspeximus/core.py",
              "old": "this string is not in the file", "new": "x",
              "tests": ["tests/test_mutation_check_harness.py::test_a_failure_counts_as_a_kill"]}
    killed = {"name": "default policy drifts", "file": "inspeximus/core.py",
              "old": 'policy: str = "safe"', "new": 'policy: str = "trusting"',
              "tests": ["tests/test_echo_policy_panel.py::test_the_default_policy_is_the_safe_one"]}

    assert mutation_check.run([killed], verbose=False) == 0, "a killed mutant is still a pass"
    assert mutation_check.run([killed, absent], verbose=False) != 0, \
        "one unevaluated mutation among killed ones must still fail the gate"


def test_the_cli_exits_non_zero_when_a_mutation_was_skipped():
    """Through the process boundary, because the exit code is the only thing CI reads."""
    import json as _json
    import tempfile
    p = os.path.join(tempfile.mkdtemp(), "spec.json")
    with open(p, "w", encoding="utf-8") as fh:
        _json.dump([{"name": "absent", "file": "inspeximus/core.py",
                     "old": "this string is not in the file", "new": "x",
                     "tests": ["tests/test_mutation_check_harness.py::test_a_failure_counts_as_a_kill"]}], fh)
    r = subprocess.run([sys.executable, os.path.join("tools", "mutation_check.py"), p],
                       cwd=ROOT, capture_output=True, text=True,
                       env={**os.environ, "PYTHONIOENCODING": "utf-8"})
    assert r.returncode != 0, r.stdout + r.stderr
    assert "skipped" in r.stdout, r.stdout


def test_the_committed_spec_is_not_empty_and_names_real_targets():
    """An empty or stale spec makes the CI gate pass over nothing -- the same silence in a different place."""
    with open(os.path.join(ROOT, "tools", "mutations.json"), encoding="utf-8") as fh:
        spec = json.load(fh)
    assert spec, "tools/mutations.json is empty: the gate would pass over zero mutations"
    for mut in spec:
        assert set(mut) >= {"name", "file", "old", "new", "tests"}, mut
        path = os.path.join(ROOT, mut["file"])
        assert os.path.exists(path), f"{mut['name']} targets a file that is gone: {mut['file']}"
        # Resolve the target THE WAY THE GATE DOES, or this check answers a different question than the
        # tool it guards. The gate reads byte-exactly and re-renders the spec fragment into the file's own
        # line endings (`_match_endings`); this test read in text mode, which silently normalises, so on a
        # CRLF checkout a multi-line target could pass here and be SKIPPED by the real run -- a guard
        # against silent skips, itself blind to the commonest cause of one.
        src = mutation_check._read_exact(path)
        assert src.count(mutation_check._match_endings(mut["old"], src)) == 1, \
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


def test_a_mutants_receipt_does_not_survive_into_the_next_mutation(capsys):
    """The falsified receipt used to live in the tree for the REST of the run.

    Source was restored per mutation; the probe RECEIPTS the mutant's tests wrote were collected once,
    after the whole loop. So the echo-guard mutant wrote an inverted
    `probes/echo_policy_panel_result.json` (safe = 0.00 echo-blocked, where we publish 1.00) and every
    later mutation's PRE-FLIGHT read it. `test_the_receipt_still_holds_the_number_we_publish` then went
    red, and whichever mutation ran under it was SKIPPED — 76/77 with the 77th never evaluated, twice,
    and the second time only because a skip had been made to fail the gate.

    So: run the receipt-writing mutant FIRST, then a mutation whose tests include the receipt check. If
    the artifact is not restored between them, the second is skipped as "not green before mutating".
    """
    flip = {"name": "flip the echo guard (its probe writes a tracked receipt)",
            "file": "probes/echo_policy_panel.py",
            "old": "m.echo_guard = True", "new": "m.echo_guard = False",
            "tests": ["tests/test_echo_policy_panel.py"]}
    after = {"name": "anything judged while the receipt must be intact",
             "file": "inspeximus/core.py",
             "old": 'policy: str = "safe"', "new": 'policy: str = "trusting"',
             "tests": ["tests/test_mutation_check_harness.py::test_the_receipt_still_holds_the_number_we_publish",
                       "tests/test_echo_policy_panel.py::test_the_default_policy_is_the_safe_one"]}

    mutation_check.run([flip, after], verbose=True)
    out = capsys.readouterr().out
    assert "SKIPPED (not green before mutating)" not in out, out


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


def test_the_artifact_pattern_covers_every_receipt_and_no_source():
    """It used to require the `_result.json` suffix — a convention a fifth of the receipts do not follow.

    `probes/governance_sufficiency_bytes.json` is written by `governance_sufficiency_probe.py` and did not
    match, so every run left it dirty and 45 lines of it were committed as churn. Dirt that survives a run
    is worse than untidy: the next run records it in `dirty_before`, protects it as if a human had written
    it, and then reads a mutant's receipt as fact — which is how a mutation ended up SKIPPED.

    So the rule is the DIRECTORY plus the extension, and it is asserted against the tree rather than a
    remembered list: every committed `probes/*.json` must be restorable, and no probe SOURCE may be.
    """
    import glob
    receipts = [os.path.basename(p) for p in glob.glob(os.path.join(ROOT, "probes", "*.json"))]
    assert receipts, "no probe receipts found — this test would pass over nothing"
    for name in receipts:
        assert mutation_check._ARTIFACT.match(f"probes/{name}"), f"receipt not restorable: {name}"

    for other in ("probes/locomo_qa.py", "probes/echo_policy_panel.py", "inspeximus/core.py",
                  "tools/mutations.json", "memory.json", "probes/sub/dir/x.json"):
        assert not mutation_check._ARTIFACT.match(other), other
