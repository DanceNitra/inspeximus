"""A-38: the legacy TEMP lock is keyed exactly as 3.15.1 keyed it, whatever the path's spelling.

Since A-10 (3.15.2) the store lock lives beside the store, and the old TEMP lock is still taken, second,
so that a writer pinned to 3.15.1 or older is excluded. That exclusion only works if both compute the SAME
file name. Nothing pinned it: a mutant that dropped `os.path.normcase` from the legacy key survived every
test, because the lock beside the store folds case on its own and every exclusion test went through it.

The expected names below are LITERALS, computed from 3.15.1's own formula
(`hashlib.sha256(os.path.normcase(os.path.abspath(path)).encode("utf-8", "replace")).hexdigest()[:16]`,
inspeximus/core.py at tag v3.15.1), not from the code under test. A self-consistency check would pass a
change that moves both spellings to a new key together, and an old writer would then hold a different lock.
"""
import os
import subprocess
import sys
import tempfile
import textwrap
import time

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from inspeximus.core import _StoreLock  # noqa: E402

GOLDEN = {                                   # from v3.15.1's formula, computed once and pinned
    "nt": {r"C:\Store\Memory.json": "778a600babffa608", r"c:\store\memory.json": "778a600babffa608"},
    "posix": {"/store/Memory.json": "9c0cbe3c09b04b37"},
}


def _legacy_name(path):
    return os.path.basename(_StoreLock(path, _legacy=True)._path)


@pytest.mark.parametrize("path,key", sorted(GOLDEN["nt" if os.name == "nt" else "posix"].items()))
def test_the_legacy_lock_name_is_the_one_3_15_1_computes(path, key):
    assert _legacy_name(path) == f"inspeximus-{key}.lock"


def test_the_legacy_lock_lives_in_temp_like_3_15_1(tmp_path, monkeypatch):
    import tempfile as _t
    monkeypatch.setattr(_t, "tempdir", str(tmp_path))
    lock = _StoreLock(str(tmp_path / "s.json"), _legacy=True)._path
    assert os.path.dirname(lock) == str(tmp_path)


@pytest.mark.skipif(os.name != "nt", reason="two spellings of one path name one file only on a "
                                            "case-insensitive filesystem")
def test_a_3_15_1_writer_under_another_spelling_is_excluded(tmp_path, monkeypatch):
    """A process that takes the TEMP lock exactly as 3.15.1 did, for the store spelled in upper case,
    excludes a current writer that names the store in lower case."""
    store_upper = str(tmp_path / "Shared" / "Memory.json")
    os.makedirs(os.path.dirname(store_upper))
    store_lower = store_upper.lower()
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    ready = str(tmp_path / "ready")
    code = textwrap.dedent(f"""
        import hashlib, os, time, msvcrt
        h = hashlib.sha256(os.path.normcase(os.path.abspath({store_upper!r})).encode("utf-8", "replace")).hexdigest()[:16]
        fh = open(os.path.join({str(tmp_path)!r}, f"inspeximus-{{h}}.lock"), "a+b")
        fh.seek(0)
        msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
        open({ready!r}, "w").close()
        time.sleep(30)
    """)
    proc = subprocess.Popen([sys.executable, "-c", code])
    try:
        t0 = time.time()
        while not os.path.exists(ready):
            if proc.poll() is not None or time.time() - t0 > 30:
                pytest.fail("control: the 3.15.1-style holder never took its lock")
            time.sleep(0.05)
        free = _StoreLock(str(tmp_path / "other" / "m.json"), try_only=True)
        free.__enter__()
        try:
            if not free.held:
                pytest.fail("control: an unrelated store's lock is busy, so a busy answer means nothing")
        finally:
            free.__exit__(None, None, None)
        lock = _StoreLock(store_lower, try_only=True)
        lock.__enter__()
        try:
            assert not lock.held, "a current writer took the lock while a 3.15.1 writer held it under another spelling"
        finally:
            lock.__exit__(None, None, None)
    finally:
        proc.kill()
        proc.wait()
