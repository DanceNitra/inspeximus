"""The store lock file is removed when the lock is released, on Windows, and only when nobody holds it.

Measured 2026-09-20: 596,291 `inspeximus-<hex>.lock` files in one user's Temp directory, one per
store path ever opened, none ever removed. The library kept them on purpose (a lock beside the
store would count as residue), but "in the system Temp" and "forever" are different decisions.

WHY WINDOWS ONLY. `open()` sets no FILE_SHARE_DELETE, so `os.unlink` fails while any other
process holds the file and succeeds only when none does; a process that opens the path after a
successful unlink creates a new file that every later opener shares. On POSIX `unlink` succeeds
under an open handle, and a waiter on the old inode would share nothing with a newcomer on the
new one. So the file stays on POSIX, and the first test here is skipped there.
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time

import pytest

from inspeximus import Inspeximus
from inspeximus.core import _StoreLock

WINDOWS = os.name == "nt"


def _mk():
    d = tempfile.mkdtemp()
    return os.path.join(d, "s.json")


@pytest.mark.skipif(not WINDOWS, reason="the removal is safe only under Windows file-sharing rules")
def test_a_write_leaves_no_lock_file_behind():
    p = _mk()
    lock = _StoreLock(p)._path
    ix = Inspeximus(path=p)
    for i in range(3):
        ix.remember(f"fact {i}", key=f"k{i}")
    ix.flush()
    assert not os.path.exists(lock), lock
    # and nothing under this path's temp carries our prefix after the writes
    assert not [n for n in os.listdir(tempfile.gettempdir())
                if n.startswith("inspeximus-") and n.endswith(".lock")
                and n == os.path.basename(lock)]


@pytest.mark.skipif(not WINDOWS, reason="the removal is safe only under Windows file-sharing rules")
def test_a_lock_another_process_holds_is_not_removed():
    """The control for the safety argument: while a second process holds the lock, our release
    must leave the file in place, or two processes could end up locking different files."""
    p = _mk()
    lock = _StoreLock(p)._path
    holder = subprocess.Popen(
        [sys.executable, "-c",
         "import sys,time; sys.path.insert(0,%r); from inspeximus.core import _StoreLock\n"
         "with _StoreLock(%r):\n    print('held', flush=True); time.sleep(4)" % (
             os.path.dirname(os.path.dirname(os.path.abspath(__file__))), p)],
        stdout=subprocess.PIPE, text=True)
    assert holder.stdout.readline().strip() == "held"
    try:
        t0 = time.time()
        with _StoreLock(p):                  # waits for the holder, then holds and releases
            pass
        assert time.time() - t0 >= 2.0, "the lock was not contended: the control measured nothing"
    finally:
        holder.wait(timeout=15)
    # our release ran while the holder still had the file open -> unlink refused -> file present;
    # the holder's own release then removed it
    assert not os.path.exists(lock)


def test_the_next_acquire_reopens_after_a_removal():
    p = _mk()
    ix = Inspeximus(path=p)
    ix.remember("a", key="k")
    ix.flush()
    ix.remember("b", key="k")
    ix.flush()
    assert ix.current("k")["text"] == "b"
    assert not _StoreLock.DEGRADED.get(p), "the lock degraded after the file was removed"
