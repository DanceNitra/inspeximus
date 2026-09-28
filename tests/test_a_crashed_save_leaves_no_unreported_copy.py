"""A save that dies before its replace leaves no copy an erasure cannot see (audit A-08).

`_durable_replace` writes the whole store to `<store>.<random>.tmp`, fsyncs it, then replaces the store.
A process killed between the two (a hook at its timeout, a closed terminal; on Windows the replace
retries for seconds while a reader holds the file) left that file with every record in plaintext, and a
later `forget_subject` reported `residue_in_store.ok = True` while `erasure_certificate` verified.

A writer holds the store lock for as long as its temp file exists, so opening the store removes any
match it finds while it holds the lock itself. A copy that cannot be removed stops both reports from
verifying.
"""
import glob
import json
import os
import shutil
import subprocess
import sys
import textwrap
import time

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from inspeximus import Inspeximus
from inspeximus.core import _StoreLock

ERASED = "12 Elm Street"

_CRASH = textwrap.dedent("""
    import os, sys
    sys.path.insert(0, {root!r})
    from inspeximus import Inspeximus
    path = sys.argv[1]
    real = os.replace
    def boom(src, dst):
        if os.path.abspath(str(dst)) == os.path.abspath(path):
            os._exit(9)
        return real(src, dst)
    os.replace = boom
    Inspeximus(path, receipts=True).remember("an unrelated later note", key="n::1", object="1")
""")

_HOLD = textwrap.dedent("""
    import sys, time
    sys.path.insert(0, {root!r})
    from inspeximus.core import _StoreLock
    _StoreLock(sys.argv[1]).__enter__()
    open(sys.argv[2], "w").close()
    time.sleep(30)
""")


@pytest.fixture
def store(tmp_path, monkeypatch):
    for k in list(os.environ):
        if k.startswith("INSPEXIMUS_") and k != "INSPEXIMUS_NO_UPDATE_CHECK":
            monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("INSPEXIMUS_STORE_FORMAT", "json")
    path = str(tmp_path / "s.json")
    m = Inspeximus(path, receipts=True)
    m.remember(f"Alice Novak lives at {ERASED}", key="alice::address", object=ERASED,
               source={"doc": "crm/alice"})
    m.flush()
    return path


def _holders(d):
    return sorted(os.path.basename(p) for p in glob.glob(os.path.join(d, "*"))
                  if os.path.isfile(p) and ERASED.encode() in open(p, "rb").read())


def _fake_temp(path, name=None):
    p = name or (path + ".ab12cd34.tmp")
    shutil.copy(path, p)
    return p


def test_an_erasure_after_a_crashed_save_leaves_no_copy(store, tmp_path):
    r = subprocess.run([sys.executable, "-c", _CRASH.format(root=ROOT), store])
    if r.returncode != 9 or not glob.glob(store + ".*.tmp"):
        pytest.fail("control: the crash left no temp copy, so there is nothing to find")
    m = Inspeximus(store, receipts=True)
    out = m.forget_subject("crm/alice")
    m.flush()
    if not out.get("ids"):
        pytest.fail("control: forget_subject erased nothing")
    assert _holders(str(tmp_path)) == [], "the erased text is still on disk beside the store"
    assert out["residue_in_store"]["ok"] is True
    assert m.erasure_certificate()["self_check"]["verified"] is True


def test_a_copy_that_cannot_be_removed_stops_both_reports(store, tmp_path, monkeypatch):
    tmp = _fake_temp(store)
    real_unlink = os.unlink

    def refuse(p, *a, **k):
        if str(p).endswith(".tmp"):
            raise PermissionError(13, "held by a scanner", str(p))
        return real_unlink(p, *a, **k)

    monkeypatch.setattr(os, "unlink", refuse)
    m = Inspeximus(store, receipts=True)
    if not os.path.exists(tmp):
        pytest.fail("control: the copy was removed, so the unremovable case was not exercised")
    out = m.forget_subject("crm/alice")
    rs = out["residue_in_store"]
    assert rs["ok"] is False and any(f.get("kind") == "INTERRUPTED_SAVE" for f in rs["findings"]), rs
    cert = m.erasure_certificate()
    assert cert["self_check"]["verified"] is False
    assert any(os.path.basename(tmp) in p for p in cert["self_check"]["problems"]), cert["self_check"]


def test_a_live_writers_temp_is_left_alone(store, tmp_path):
    tmp = _fake_temp(store)
    ready = str(tmp_path / "ready")
    proc = subprocess.Popen([sys.executable, "-c", _HOLD.format(root=ROOT), store, ready])
    try:
        t0 = time.time()
        while not os.path.exists(ready):
            if proc.poll() is not None or time.time() - t0 > 30:
                pytest.fail("control: the holder never took the lock")
            time.sleep(0.05)
        t0 = time.time()
        Inspeximus(store, receipts=True)
        assert time.time() - t0 < 5, "opening waited on a busy lock"
        assert os.path.exists(tmp), "opening removed a temp file while a writer held the lock"
    finally:
        proc.kill()
        proc.wait()
    Inspeximus(store, receipts=True)
    assert not os.path.exists(tmp), "the stale copy survived an open once the lock was free"


def test_only_the_stores_own_temps_are_removed(store, tmp_path):
    keep = [store + ".receipts.json.ab12cd34.tmp", str(tmp_path / "notes.tmp"), store + ".bak",
            store + ".abc.tmp"]
    for p in keep:
        open(p, "w").close()
    gone = [_fake_temp(store), _fake_temp(store, store + ".rows-tmp")]
    Inspeximus(store, receipts=True)
    assert [p for p in gone if os.path.exists(p)] == []
    assert [p for p in keep if not os.path.exists(p)] == [], "a file that is not a save temp was removed"


def test_try_only_never_waits_and_says_whether_it_holds(store, tmp_path):
    with _StoreLock(store, try_only=True) as lock:
        assert lock.held
    ready = str(tmp_path / "ready")
    proc = subprocess.Popen([sys.executable, "-c", _HOLD.format(root=ROOT), store, ready])
    try:
        t0 = time.time()
        while not os.path.exists(ready):
            if proc.poll() is not None or time.time() - t0 > 30:
                pytest.fail("control: the holder never took the lock")
            time.sleep(0.05)
        t0 = time.time()
        before = dict(_StoreLock.DEGRADED)
        with _StoreLock(store, try_only=True) as lock:
            assert not lock.held
        assert time.time() - t0 < 2
        assert dict(_StoreLock.DEGRADED) == before, "a skipped try counted as an unprotected write"
    finally:
        proc.kill()
        proc.wait()


_HOLD_LEGACY = textwrap.dedent("""
    import sys, time
    sys.path.insert(0, {root!r})
    from inspeximus.core import _StoreLock
    lock = _StoreLock(sys.argv[1], _legacy=True)      # what an older version holds while it saves
    lock._acquire(*lock._locker)
    open(sys.argv[2], "w").close()
    time.sleep(30)
""")


def test_an_older_writers_temp_is_left_alone(store, tmp_path):
    """A version before 3.15.2 takes only the TEMP lock while its temp file exists."""
    tmp = _fake_temp(store)
    ready = str(tmp_path / "ready")
    proc = subprocess.Popen([sys.executable, "-c", _HOLD_LEGACY.format(root=ROOT), store, ready])
    try:
        t0 = time.time()
        while not os.path.exists(ready):
            if proc.poll() is not None or time.time() - t0 > 30:
                pytest.fail("control: the holder never took the lock")
            time.sleep(0.05)
        Inspeximus(store, receipts=True)
        assert os.path.exists(tmp), "opening removed a temp file while an older writer held its lock"
    finally:
        proc.kill()
        proc.wait()
