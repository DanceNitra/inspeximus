"""The two audit scripts are our published evidence, and nothing tested them.

`claims_audit.py` verifies the README's claims against a wheel; `governance_audit.py` verifies the erasure
story. Both run in CI and `claims_audit` gates a release. Neither had a single test, and both were partly
incapable of failing:

  * `c_zero_deps` read installed `*.dist-info/METADATA`. In a SOURCE checkout that glob matches nothing, so
    `requires` was `[]` and the check reported "mandatory requirements=none" and passed -- on every matrix
    leg and at the pre-publish gate, of which exactly one installs a wheel. Adding a hard dependency to
    pyproject would not have changed the verdict.
  * A missing capability, or any exception inside a check, produced `[SKIP]`, "0 FAILED" and exit 0. A
    README-asserted feature that had been deleted would have kept the gate green.
  * `governance_audit` could not run AT ALL: it downloaded `agora-inspeximus`, a package that does not
    exist. The project is `inspeximus`. Its default path had been broken silently.
  * `_secrets()` intersected each scenario against a hard-coded six-token tuple, so a scenario using any
    other token swept for NOTHING -- and `not any(s in blob for s in [])` is True, so the four erasure
    checks passed over an empty list.
"""
import os
import re
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


def _run(script, *args, env=None):
    return subprocess.run([sys.executable, script, "--local", *args], cwd=ROOT, capture_output=True,
                          text=True, encoding="utf-8", errors="replace", timeout=900,
                          env={**os.environ, "PYTHONIOENCODING": "utf-8", **(env or {})})


def test_the_claims_audit_runs_and_passes():
    r = _run("claims_audit.py")
    assert r.returncode == 0, r.stdout[-2000:]
    assert "FAILED" in r.stdout


def test_the_governance_audit_runs_and_passes():
    """It could not run at all: `pip download agora-inspeximus`, a package that does not exist."""
    r = _run("governance_audit.py")
    assert r.returncode == 0, r.stdout[-2000:]
    assert "CLAIM HOLDS" in r.stdout


def test_the_governance_audit_fails_when_the_claim_is_falsified():
    """Its built-in falsifier is the only evidence the checks can fail at all. If GOV_FALSIFY ever stops
    breaking them, every PASS above becomes meaningless."""
    r = _run("governance_audit.py", env={"GOV_FALSIFY": "1"})
    # The message names the value it saw: an assertion that prints only stdout made this look like a
    # content problem when the exit code was the subject.
    assert r.returncode == 1, (f"exit={r.returncode}; stderr={r.stderr[-600:]}; "
                               f"stdout={r.stdout[-1200:]}")
    assert "CLAIM BROKEN" in r.stdout
    # No literal em-dash: the subprocess's stdout is decoded with the platform locale, and on this box a
    # cp1250 console turns "—" into mojibake, so a regex containing one silently matches nothing. A test
    # that cannot match is a test that cannot fail for the right reason.
    n = re.search(r"CLAIM BROKEN\D+(\d+) failing", r.stdout)
    assert n and int(n.group(1)) > 10, r.stdout[-800:]


def test_the_audit_downloads_the_package_that_actually_exists():
    """A stale distribution name makes the non-local path unrunnable, which is how it stayed broken."""
    for script in ("claims_audit.py", "governance_audit.py"):
        with open(os.path.join(ROOT, script), encoding="utf-8") as fh:
            src = fh.read()
        assert "agora-inspeximus" not in src, f"{script} downloads a package that does not exist"


def test_zero_deps_reads_a_source_of_truth_and_can_fail():
    """The check that could not fail. It now reads pyproject when there is no installed METADATA, so a
    declared dependency flips it -- proven by declaring one."""
    import claims_audit

    ok, evidence = claims_audit.c_zero_deps()
    assert ok is True and "read from" in evidence, evidence

    pyproject = os.path.join(ROOT, "pyproject.toml")
    with open(pyproject, encoding="utf-8") as fh:
        original = fh.read()
    try:
        with open(pyproject, "w", encoding="utf-8") as fh:
            fh.write(original.replace("requires-python",
                                      'dependencies = ["requests"]\nrequires-python', 1))
        import importlib
        importlib.reload(claims_audit)
        ok2, ev2 = claims_audit.c_zero_deps()
        assert ok2 is False, f"a declared dependency must break the zero-dependency claim: {ev2}"
        assert "requests" in ev2
    finally:
        with open(pyproject, "w", encoding="utf-8") as fh:
            fh.write(original)
        import importlib
        importlib.reload(claims_audit)


def test_a_scenario_that_declares_no_secrets_is_an_error_not_a_pass():
    """`not any(s in blob for s in [])` is True: an empty sweep passed four erasure checks."""
    import governance_audit as G

    for sc in G.SCENARIOS:
        assert G._secrets(sc), sc["name"]

    with pytest.raises(AssertionError, match="declares no `secrets`"):
        G._secrets({**G.SCENARIOS[0], "secrets": []})


def test_a_declared_secret_that_is_not_in_the_scenario_is_an_error():
    """Erasing a string the fixture never contained proves nothing, and would look identical to success."""
    import governance_audit as G

    with pytest.raises(AssertionError, match="do not appear"):
        G._secrets({**G.SCENARIOS[0], "secrets": ["Nobody At All"]})


def test_every_scenario_declares_its_own_secrets():
    """A new scenario must not inherit somebody else's tokens -- that coupling is what made the sweep
    silently empty in the first place."""
    import governance_audit as G

    for sc in G.SCENARIOS:
        assert sc.get("secrets"), f"{sc['name']} declares no secrets"
        corpus = " ".join(sc["owned"] + [sc["derived"]]).lower()
        for tok in sc["secrets"]:
            assert tok.lower() in corpus, f"{sc['name']}: {tok!r} is not in its own text"


def test_a_skipped_claim_fails_the_gate_unless_a_historical_version_is_under_audit():
    """A missing capability and a raised exception both produced [SKIP], "0 FAILED" and exit 0, so
    deleting a feature the README asserts would have kept the pre-publish gate green. The rule lived
    inline in main() where nothing could reach it -- a mutation removing it survived the whole suite."""
    from claims_audit import counts_as_failure

    assert counts_as_failure(False, auditing_history=False) is True
    assert counts_as_failure(False, auditing_history=True) is True, "a real failure fails on any artifact"

    assert counts_as_failure(None, auditing_history=False) is True, \
        "against the working tree, 'this build does not have it' means the claim has no implementation"
    assert counts_as_failure(None, auditing_history=True) is False, \
        "against a historical release it is a fact about the artifact, not about the claim"

    assert counts_as_failure(True, auditing_history=False) is False
    assert counts_as_failure(True, auditing_history=True) is False
