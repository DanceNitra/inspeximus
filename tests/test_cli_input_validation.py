"""The CLI must not report a BAD REQUEST or a MISTYPED PATH as an empty result.

Both defects below were found by driving the installed console script and reading exit codes, not by
reading the source. They share one shape: the command answered "(nothing in memory for that query)"
and exited 0, so a caller could not distinguish "your request was invalid" or "your path is wrong"
from "you genuinely have no memories". For a script or a CI job, which sees only the exit code, that
is the difference between a caught error and a silent wrong answer.

The CLI already gets this right on the write side: a --path that cannot be persisted to exits 3 with
"NOT PERSISTED" (see _flush_or_fail). These tests hold the read side to the same standard.

Driven as a SUBPROCESS on purpose: exit codes and stream separation cannot be asserted in-process.
"""
import os
import subprocess
import sys

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def run_cli(*args, cwd=None):
    """Invoke the CLI the way a user's shell does, via the module entry point."""
    proc = subprocess.run([sys.executable, "-m", "inspeximus.cli", *args],
                          cwd=cwd or REPO, capture_output=True, text=True, timeout=180,
                          encoding="utf-8", errors="replace")
    return proc.returncode, proc.stdout or "", proc.stderr or ""


@pytest.fixture()
def store(tmp_path):
    p = tmp_path / "s.json"
    code, out, err = run_cli("--path", str(p), "remember", "billing runbook drains the queue")
    assert code == 0, f"setup write failed: {out}{err}"
    return p


@pytest.mark.parametrize("k", ["0", "-1", "-5"])
def test_non_positive_k_is_rejected_not_answered_with_nothing(store, k):
    """`-k 0` / `-k -5` used to print '(nothing in memory for that query)' and exit 0.

    argparse's type=int already rejected `-k abc`, so the TYPE was validated and the RANGE was not —
    the quiet half of the same check.
    """
    code, out, err = run_cli("--path", str(store), "recall", "billing", "-k", k)
    assert code != 0, (f"-k {k} must be rejected, got exit 0 with output {out!r}. An invalid request "
                       f"reported as an empty result is indistinguishable from an empty store.")
    assert "nothing in memory" not in out.lower(), "a rejected request must not print an empty-result line"


@pytest.mark.parametrize("k", ["1", "6"])
def test_valid_k_still_works(store, k):
    """The fix must not touch valid usage."""
    code, out, _ = run_cli("--path", str(store), "recall", "billing", "-k", k)
    assert code == 0
    assert "billing runbook" in out


def test_non_integer_k_is_still_rejected(store):
    """Pre-existing behaviour that must survive the new validator."""
    code, _, _ = run_cli("--path", str(store), "recall", "billing", "-k", "abc")
    assert code != 0


def test_a_missing_store_directory_is_reported_on_stderr(tmp_path):
    """A --path whose DIRECTORY does not exist is a typo, and reads were silent about it.

    The warning goes to stderr so machine-readable stdout stays parseable.
    """
    bogus = tmp_path / "no-such-dir" / "s.json"
    code, out, err = run_cli("--path", str(bogus), "recall", "anything")
    assert "no such directory" in err.lower(), (
        f"a mistyped store path must be visible; got stderr={err!r}. Otherwise an empty result is "
        f"indistinguishable from a wrong path.")
    assert "no such directory" not in out.lower(), "the diagnostic belongs on stderr, not stdout"


def test_a_brand_new_store_in_an_existing_directory_warns_about_nothing(tmp_path):
    """The legitimate case must stay quiet: a missing FILE in a real directory is a new empty store.

    This is the control for the test above — without it, that test would also pass if the warning
    fired for every fresh store, which would train users to ignore it.
    """
    fresh = tmp_path / "fresh.json"
    code, out, err = run_cli("--path", str(fresh), "recall", "anything")
    assert code == 0, f"a new store must read cleanly, got {code}: {err}"
    assert "no such directory" not in err.lower(), f"must not warn on a legitimate new store: {err!r}"


def test_cli_diagnostics_are_ascii(tmp_path):
    """This CLI runs on a Windows console that is not UTF-8 (cp1250 on the dev box).

    A non-ASCII character in a diagnostic rendered as a replacement char here and can raise
    UnicodeEncodeError on a stricter console — the diagnostic crashing the run it is diagnosing. The
    first version of the warning used an em dash and did exactly that.
    """
    bogus = tmp_path / "no-such-dir" / "s.json"
    _, _, err = run_cli("--path", str(bogus), "recall", "anything")
    offending = [c for c in err if ord(c) > 127]
    assert not offending, f"CLI diagnostics must be ASCII; found {offending[:5]!r} in {err!r}"
