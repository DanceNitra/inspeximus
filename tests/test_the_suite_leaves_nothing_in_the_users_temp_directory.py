"""The suite's temporary files land under pytest's basetemp, which pytest prunes, not in the user's Temp.

Measured 2026-09-20 before the conftest fixture existed: 1,027,150 entries in the user's Temp were
ours (596,291 store lock files, 422,798 `tmp*` directories, 8,060 example runner directories), and
the Windows cleanup of that directory took an hour. Each check here fails when the redirect is
removed, and the last one is the control that shows the redirect is the only thing between a test
and the system directory.
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile

from inspeximus import Inspeximus
from inspeximus.core import _StoreLock


def _under(path, base) -> bool:
    try:
        return os.path.commonpath([os.path.realpath(path), os.path.realpath(base)]) == os.path.realpath(base)
    except ValueError:
        return False


def test_mkdtemp_and_the_store_lock_land_under_basetemp(tmp_path_factory):
    base = str(tmp_path_factory.getbasetemp())
    assert _under(tempfile.gettempdir(), base)
    assert _under(tempfile.mkdtemp(), base)
    p = os.path.join(tempfile.mkdtemp(), "s.json")
    Inspeximus(path=p).remember("x", key="k")
    assert _under(_StoreLock(p)._path, base), "the store lock file escaped to the system Temp"


def test_a_child_interpreter_inherits_the_redirect(tmp_path_factory):
    base = str(tmp_path_factory.getbasetemp())
    code = "import tempfile; print(tempfile.gettempdir())"
    child = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                           encoding="utf-8", errors="replace").stdout.strip()
    assert _under(child, base), child


def test_CONTROL_without_the_environment_a_child_uses_the_system_temp(tmp_path_factory):
    """The control: strip TMP/TEMP/TMPDIR and a child reports the real system directory. If this
    ever lands under basetemp too, the redirect is not what the other tests are measuring."""
    base = str(tmp_path_factory.getbasetemp())
    env = {k: v for k, v in os.environ.items() if k not in ("TMP", "TEMP", "TMPDIR")}
    code = "import tempfile; print(tempfile.gettempdir())"
    child = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", env=env).stdout.strip()
    assert not _under(child, base), child
