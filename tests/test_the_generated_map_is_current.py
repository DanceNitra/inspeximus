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

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


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
