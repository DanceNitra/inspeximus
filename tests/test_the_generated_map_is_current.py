"""The core map is generated, so a stale one is never a decision: it is always "regenerate".

WHY THIS TEST EXISTS. `docs/CORE_MAP.md` is produced by `tools/gen_core_map.py` and records where
things live in `inspeximus/core.py`, so ANY added or moved method makes it stale. The check for that
lived only in the audit workflow, which means the feedback loop was: push, wait, then a red email.
That happened twice in one session, both times for a one-line regeneration.

A generated artifact whose only guard runs after the push is a guard aimed at the wrong moment. This
moves it into the suite that runs before one.
"""
from __future__ import annotations

import os
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

#: BOTH TESTS TOUCH ONE TRACKED FILE, so they must not run at the same time.
#:
#: The control below edits docs/CORE_MAP.md and restores it. Under pytest-xdist the other test runs on
#: a different worker and can read the file mid-edit, which fails it for a reason that has nothing to
#: do with the code it guards. Measured: the pair went red locally under -n 3 while `--check` on its
#: own said the map was current.
#:
#: A shared group serialises them onto one worker. The alternative, giving the generator a --root so
#: the control could work on a copy, is the better shape and a larger change than this file's subject.
pytestmark = pytest.mark.xdist_group("core_map_file")


def test_docs_core_map_matches_core_py():
    r = subprocess.run([sys.executable, os.path.join("tools", "gen_core_map.py"), "--check"],
                       cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace")
    assert r.returncode == 0, (
        "docs/CORE_MAP.md is stale. Run `python tools/gen_core_map.py`.\n" + (r.stdout or "") + (r.stderr or ""))


def test_the_checker_can_actually_fail():
    """CONTROL. Without it, a green result above is equally consistent with a checker that exits 0 on
    everything, which is exactly what a generated-file guard tends to decay into."""
    path = os.path.join(ROOT, "docs", "CORE_MAP.md")
    original = open(path, encoding="utf-8").read()
    try:
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(original + "\nan edit the generator would never make\n")
        r = subprocess.run([sys.executable, os.path.join("tools", "gen_core_map.py"), "--check"],
                           cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace")
        assert r.returncode != 0, "the checker passed a file it did not generate"
    finally:
        with open(path, "w", encoding="utf-8", newline="") as fh:
            fh.write(original)
    # and the file is back exactly as it was, or this test would leave the repo dirty
    assert open(path, encoding="utf-8").read() == original
