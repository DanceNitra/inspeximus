"""Every script CLAIMS.md cites as a reproduction must exist and must survive being imported.

The generalisation of #1. That issue was one entrypoint dying at import time on a hardcoded path,
under a claims status promising the command runs once you supply its dependencies. Nothing checked
that promise for any of the commands, so the only reason it surfaced was an outside reader trying it.

This is the cheapest check that would have caught it on the day it landed: for each script named in
a `python ...` command in docs/CLAIMS.md, execute its MODULE LEVEL from a directory that is not the
repo root, with no OPENAI_API_KEY, and require it to complete. `runpy` with a run_name other than
`__main__` runs the imports and the module body without running `main()`, so no benchmark executes
and no paid call is made -- while exactly the class of defect #1 reported is still exercised.

Measured 2026-08-22 after the fix: 0 of 15 present scripts fail, 88s wall for the whole set. It is
parametrised so xdist spreads it rather than serialising.

A NOTE ON MEASURING THIS UNDER LOAD, because the first run of this probe reported 14 of 15 FAILING
and every one of those was a timeout at 25s. The probe had been launched next to a full `-n auto`
pytest run, which owns every core on this machine; re-run on a quiet box the same scripts return 0
in 5.4s each. A uniform failure across unrelated scripts is a statement about the harness. The
timeout here is generous for that reason, and the failure message says so.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CLAIMS = os.path.join(REPO, "docs", "CLAIMS.md")

#: Cited scripts that deliberately live in another repository. Listed explicitly rather than
#: skipped by a pattern, so adding one is a decision someone makes on purpose.
ELSEWHERE = {"ramr_echo_resistance_backends.py"}


def _cited_scripts():
    if not os.path.exists(CLAIMS):
        return {}
    txt = open(CLAIMS, encoding="utf-8").read()
    out = {}
    for m in re.finditer(r"`(python[^`]+)`", txt):
        cmd = m.group(1).strip()
        for tok in cmd.split()[1:]:
            if tok.endswith(".py"):
                out.setdefault(tok, set()).add(cmd)
                break
    return out


CITED = _cited_scripts()


def test_claims_file_is_readable_and_cites_commands():
    """The control for every parametrised case below: if CLAIMS.md moved or stopped carrying
    commands, this file would silently have nothing to check and would report green."""
    assert CITED, "no `python ...` reproduction commands found in docs/CLAIMS.md"
    assert len(CITED) >= 10, f"only {len(CITED)} cited scripts; the parser has probably drifted"


@pytest.mark.parametrize("script", sorted(CITED))
def test_cited_script_exists(script):
    if script in ELSEWHERE:
        pytest.skip(f"{script} lives in another repository by design")
    assert os.path.exists(os.path.join(REPO, script)), (
        f"docs/CLAIMS.md cites {script} as a reproduction and it is not in this repository; "
        f"cited by: {sorted(CITED[script])[0]}")


@pytest.mark.parametrize("script", sorted(s for s in CITED if s not in ELSEWHERE))
def test_cited_script_survives_import_from_a_foreign_directory(script, tmp_path):
    path = os.path.join(REPO, script)
    if not os.path.exists(path):
        pytest.skip("covered by test_cited_script_exists")
    env = dict(os.environ)
    env.pop("OPENAI_API_KEY", None)
    env["PYTHONIOENCODING"] = "utf-8"
    code = "import runpy;runpy.run_path(%r, run_name='__not_main__')" % path
    try:
        p = subprocess.run([sys.executable, "-c", code], cwd=str(tmp_path), env=env,
                           capture_output=True, text=True, timeout=300)
    except subprocess.TimeoutExpired:
        pytest.fail(f"{script} did not finish its module level in 300s from a foreign cwd. "
                    f"If the machine is otherwise busy this is the harness, not the script -- "
                    f"re-run it alone before believing it.")
    assert p.returncode == 0, (
        f"{script} fails at import time from a directory that is not the repo root.\n"
        f"It is cited in docs/CLAIMS.md as: {sorted(CITED[script])[0]}\n"
        f"{(p.stderr or '')[-900:]}")
