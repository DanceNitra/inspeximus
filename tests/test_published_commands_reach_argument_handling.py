"""A command published as reproducible must at least START from a clean clone.

WHY THIS EXISTS. @mioimotoai-lgtm cloned the repo, ran the command `docs/CLAIMS.md` gives as the
reproduction for rows 89, 90, 91, 99 and 100, and got `FileNotFoundError: 'server/.env'` -- a path
that has never been in this repository -- before argparse ever ran. The report sat open for 38 days
and every word of it still reproduced on 2.20.0.

Row 42 makes it six, and nobody knew: `integrity_bench_echo.py` binds its judge from the revert cell
at import time, so it died the same way. That one was found by the mutation control at the bottom of
this file, not by reading, and not by the reporter.

The status those six claims carry is `REPRODUCIBLE-WITH-DEPS`, defined in claims_audit.py as
"committed command, but needs a service/dataset we cannot ship". That promise is about DEPENDENCIES.
It was not true here in a way no dependency could fix: supplying an OpenAI key did not help, because
the failure was a hardcoded relative path read at import time.

So this test does not check the benchmark's numbers, which need a paid judge. It checks the part the
label actually promises and nobody was testing: that the entrypoint imports, parses its arguments,
and either runs or refuses with an actionable message -- from a directory that is not the repo root,
with no dotenv anywhere, and no key in the environment.

THE CONTROL is the mutant below: the test re-runs the same entrypoint with the old unconditional
loader restored and asserts it FAILS. Without that, a test asserting "no traceback" would pass just
as well against an entrypoint that had been quietly deleted.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENTRY = os.path.join("probes", "integrity_bench_revert.py")


def _clean_tree():
    """A working copy holding only the package and the probes, checked out somewhere else."""
    d = tempfile.mkdtemp(prefix="insp_pubcmd_")
    for part in ("inspeximus", "probes"):
        shutil.copytree(os.path.join(REPO, part), os.path.join(d, part),
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    assert not os.path.exists(os.path.join(d, "server", ".env"))
    return d


def _run(cwd, *args, key=None):
    env = dict(os.environ)
    env.pop("OPENAI_API_KEY", None)
    if key is not None:
        env["OPENAI_API_KEY"] = key
    env["PYTHONIOENCODING"] = "utf-8"
    return subprocess.run([sys.executable, ENTRY, *args], cwd=cwd, env=env,
                          capture_output=True, text=True, timeout=600)


@pytest.fixture(scope="module")
def tree():
    d = _clean_tree()
    yield d
    shutil.rmtree(d, ignore_errors=True)


def test_published_command_does_not_die_on_a_missing_dotenv(tree):
    """The exact command from docs/CLAIMS.md, with no key: refuse, do not crash."""
    p = _run(tree, "--systems", "inspeximus", "--n", "20")
    both = p.stdout + p.stderr
    assert "FileNotFoundError" not in both, both[-800:]
    assert "server/.env" not in p.stderr or "fallback" in p.stderr, p.stderr[-800:]
    assert p.returncode == 2, f"want an actionable refusal (2), got {p.returncode}\n{both[-800:]}"
    assert "OPENAI_API_KEY" in p.stderr and "--judge local" in p.stderr, p.stderr[-800:]


def test_a_free_local_run_exists_and_completes(tree):
    """The second half of #1: `--systems inspeximus` alone was never free, because it goes through
    the same paid judge as its competitors. `--judge local` is the free path, and it must run."""
    p = _run(tree, "--systems", "inspeximus", "--judge", "local", "--n", "20")
    assert p.returncode == 0, (p.stdout + p.stderr)[-1200:]
    assert "revert_success_rate" in p.stdout, p.stdout[-600:]
    assert "NOT comparable" in p.stdout, p.stdout[-600:]


def test_the_local_run_labels_its_instrument_in_the_artifact(tree):
    """A score whose judge is not recorded beside it invites the comparison the judge warns against,
    and a free run must not overwrite the artifact the site's figures cite."""
    import json
    _run(tree, "--systems", "inspeximus", "--judge", "local", "--n", "20")
    local = os.path.join(tree, "probes", "integrity_bench_revert_result_localjudge.json")
    assert os.path.exists(local), "the local run must write its own file"
    d = json.load(open(local, encoding="utf-8"))
    assert d["judge"] == "local"
    assert d["comparable_with_published"] is False


def test_an_existing_process_key_is_never_overwritten(tree):
    """The third defect in #1: the loader replaced a real process key with whatever the dotenv held,
    including the empty string. With a key present the run must get past the refusal."""
    p = _run(tree, "--systems", "inspeximus", "--n", "1", key="sk-test-not-a-real-key")
    assert p.returncode != 2, "a key was set; the run must not refuse for lack of one"
    assert "no key is set" not in p.stderr


def test_control_the_old_loader_would_still_fail(tree):
    """THE CONTROL. Restore the unconditional read and assert the suite catches it -- otherwise
    every assertion above would pass equally well against a deleted entrypoint."""
    path = os.path.join(tree, ENTRY)
    good = open(path, encoding="utf-8").read()
    marker = "OPENAI_KEY = _load_key()"
    assert marker in good, "the fixed loader is not in the file this test is guarding"
    mutant = good.replace(
        marker,
        'env = {}\n'
        'for line in open("server/.env", encoding="utf-8", errors="replace"):\n'
        '    pass\n'
        'OPENAI_KEY = ""',
        1)
    try:
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(mutant)
        p = _run(tree, "--systems", "inspeximus", "--n", "20")
        both = p.stdout + p.stderr
        assert "FileNotFoundError" in both, "the control did not reproduce the reported defect"
        assert p.returncode != 2, "the mutant crashed rather than refusing, which is the point"
    finally:
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(good)
    p = _run(tree, "--systems", "inspeximus", "--n", "20")
    assert p.returncode == 2, "the fixed entrypoint was not restored after the control"


def test_the_echo_cell_inherited_the_same_defect_and_the_same_fix(tree):
    """It was never named in #1 and it was broken by it. `integrity_bench_echo.py` binds
    `judge_current` from the revert cell at import time, so the unconditional dotenv read killed
    this entrypoint too. docs/CLAIMS.md row 42 cites it. It surfaced only because a mutation control
    put the old loader back and TWO tests went red instead of one."""
    entry = os.path.join("probes", "integrity_bench_echo.py")
    env = dict(os.environ)
    env.pop("OPENAI_API_KEY", None)
    env["PYTHONIOENCODING"] = "utf-8"
    refuse = subprocess.run([sys.executable, entry, "--systems", "inspeximus"], cwd=tree, env=env,
                            capture_output=True, text=True, timeout=600)
    assert "FileNotFoundError" not in refuse.stdout + refuse.stderr
    assert refuse.returncode == 2, (refuse.stdout + refuse.stderr)[-800:]
    free = subprocess.run([sys.executable, entry, "--systems", "inspeximus",
                           "--judge", "local", "--n", "20"], cwd=tree, env=env,
                          capture_output=True, text=True, timeout=600)
    assert free.returncode == 0, (free.stdout + free.stderr)[-1200:]
    assert "resurrection" in free.stdout, free.stdout[-600:]
