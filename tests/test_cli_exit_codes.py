"""A refused operation must not exit 0.

`inspeximus revert <key>` never inspected `res["ok"]`. A refusal printed

    reverted region: now -> {'ok': False, 'reason': 'no superseded predecessor for key'}

and exited **0**, so `inspeximus revert region && echo rolled-back` printed rolled-back after nothing had
rolled back. `--json` exited 0 too, so even a script parsing the payload had to know to distrust the exit
code. Every other refusal path in this CLI exits 1 or 2.

It is also the operation we put on the front page as the differentiator, and no test had ever invoked the
subcommand — the whole class was reachable only by running it by hand, which is what an audit did.
"""
import json
import os
import subprocess
import sys
import tempfile

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _cli(*args, path=None):
    return subprocess.run([sys.executable, "-m", "inspeximus.cli", "--path", path, *args],
                          cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=180,
                          env={**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONPATH": ROOT})


@pytest.fixture
def store(tmp_path):
    p = str(tmp_path / "m.json")
    r = _cli("remember", "the region is eu-west", "--key", "region", path=p)
    assert r.returncode == 0, r.stderr
    return p


def test_a_refused_revert_exits_nonzero(store):
    """THE defect: there is nothing to revert to, and the command said it had reverted."""
    r = _cli("revert", "region", path=store)
    assert r.returncode == 1, f"exit={r.returncode}\n{r.stdout}{r.stderr}"
    assert "refused" in (r.stdout + r.stderr).lower()
    assert "no superseded predecessor" in (r.stdout + r.stderr)


def test_a_refused_revert_does_not_claim_it_reverted(store):
    """The message mattered as much as the code: it began with the word 'reverted'."""
    r = _cli("revert", "region", path=store)
    assert "reverted region" not in r.stdout


def test_a_refused_revert_exits_nonzero_in_json_mode_too(store):
    """A script parsing --json still reads the exit code first."""
    r = _cli("--json", "revert", "region", path=store)
    assert r.returncode == 1, r.stdout
    assert json.loads(r.stdout)["ok"] is False


def test_a_successful_revert_still_exits_zero(store):
    """The other direction. A command that always failed would be no better."""
    assert _cli("remember", "the region is us-east", "--key", "region", path=store).returncode == 0
    r = _cli("revert", "region", path=store)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "reverted region" in r.stdout


def test_the_shell_idiom_now_tells_the_truth(store):
    """`revert && echo done` is how this gets used in a runbook, and it was reporting success on a
    refusal. Asserted through the shell rather than the exit code alone, because that is the failure the
    user actually meets."""
    r = subprocess.run(
        f'{sys.executable} -m inspeximus.cli --path "{store}" revert region && echo ROLLED-BACK',
        shell=True, cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=180,
        env={**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONPATH": ROOT})
    assert "ROLLED-BACK" not in r.stdout, "the shell believed a refused revert had succeeded"


def test_reverting_an_unknown_key_is_also_a_refusal(store):
    r = _cli("revert", "no-such-key", path=store)
    assert r.returncode == 1, r.stdout + r.stderr
