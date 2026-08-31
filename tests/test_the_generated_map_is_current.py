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

def test_docs_core_map_matches_core_py():
    r = subprocess.run([sys.executable, os.path.join("tools", "gen_core_map.py"), "--check"],
                       cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace")
    assert r.returncode == 0, (
        "docs/CORE_MAP.md is stale. Run `python tools/gen_core_map.py`.\n" + (r.stdout or "") + (r.stderr or ""))


def test_the_checker_can_actually_fail():
    """CONTROL, run on a COPY. Without it, a green result above is equally consistent with a checker
    that exits 0 on everything, which is what a generated-file guard tends to decay into.

    The first version edited the tracked docs/CORE_MAP.md and restored it. That raced any other test
    reading the file under pytest-xdist, and left the repository dirty if it crashed: a guard that
    has to damage the repository to prove it works is one nobody runs twice. --src and --out exist
    so it does not have to.
    """
    import shutil
    import tempfile
    d = tempfile.mkdtemp()
    src = os.path.join(d, "core_copy.py")
    out = os.path.join(d, "map_copy.md")
    shutil.copy(os.path.join(ROOT, "inspeximus", "core.py"), src)

    def check():
        return subprocess.run([sys.executable, os.path.join("tools", "gen_core_map.py"),
                               "--src", src, "--out", out, "--check"],
                              cwd=ROOT, capture_output=True, text=True,
                              encoding="utf-8", errors="replace")

    subprocess.run([sys.executable, os.path.join("tools", "gen_core_map.py"),
                    "--src", src, "--out", out], cwd=ROOT, check=True, capture_output=True)
    assert check().returncode == 0, "a freshly generated map must satisfy its own checker"

    with open(out, "a", encoding="utf-8") as fh:
        fh.write(chr(10) + "an edit the generator would never make" + chr(10))
    r = check()
    assert r.returncode != 0, "the checker passed a file it did not generate"
    assert "an edit the generator would never make" in r.stdout, (
        "the diff must NAME what differs; 'stale' alone costs a round trip per guess")

    # The repository's own map was never touched, which is the point of doing this on a copy.
    status = subprocess.run(["git", "status", "--short", "docs/CORE_MAP.md"], cwd=ROOT,
                            capture_output=True, text=True)
    assert status.stdout.strip() == "", "the control modified the tracked map"
