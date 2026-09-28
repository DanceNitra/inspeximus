"""One store, one lock, whatever each writer's temp directory is (audit A-10).

`_StoreLock` kept its lock file in `tempfile.gettempdir()`, keyed by a hash of the store path. TEMP is
per user on Windows and private to a sandboxed agent, a snap or flatpak editor or a service, and since
3.14.0 `install --all` points every host at ONE store. Measured 2026-09-27, 8 processes x 12 writes on
one JSON store, each retrying with `reload()` as `StoreChangedOnDisk` prescribes:

    one TEMP (control):   96, 96, 96 of 96 landed
    two TEMPs:            85, 96, 91 of 96 landed, every writer told 96 of 96 succeeded

The lock now lives beside the store and exists only while a write holds it. The old TEMP lock is still
taken second, so a writer pinned to an older version is still excluded.
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
from inspeximus import Inspeximus
from inspeximus.core import StoreLockUnavailable, _StoreLock


def test_one_store_maps_to_one_lock_whatever_the_temp_dir(tmp_path, monkeypatch):
    store = str(tmp_path / "shared" / "memory.json")
    a, b = tmp_path / "tmpA", tmp_path / "tmpB"
    a.mkdir()
    b.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(a))
    lock_a = _StoreLock(store)._path
    monkeypatch.setattr(tempfile, "tempdir", str(b))
    lock_b = _StoreLock(store)._path
    other = _StoreLock(str(tmp_path / "other" / "memory.json"))._path
    if lock_b == other:
        pytest.fail("control: two different stores map to one lock, so the key is not per store")
    assert os.path.normcase(lock_a) == os.path.normcase(lock_b), \
        f"one store, two locks: {lock_a} and {lock_b}"


_HOLDER = textwrap.dedent("""
    import os, sys, tempfile, time
    sys.path.insert(0, {root!r})
    tempfile.tempdir = {temp!r}
    from inspeximus.core import _StoreLock
    if {legacy!r}:      # what an older version takes: the TEMP lock alone
        lock = _StoreLock({store!r}, _legacy=True)
        lock._acquire(*lock._locker)
        release = lock._release
    else:               # what this version's writers take
        lock = _StoreLock({store!r})
        lock.__enter__()
        release = lambda: lock.__exit__(None, None, None)
    open({ready!r}, "w").close()
    time.sleep({hold!r})
    release()
    time.sleep(30)
""")


def _hold(tmp_path, store, temp, legacy=False, hold=30.0):
    """Start a process that holds the lock for `hold` seconds, then releases it and stays alive; return
    it once it holds."""
    ready = str(tmp_path / f"ready-{len(os.listdir(tmp_path))}")
    code = _HOLDER.format(root=ROOT, temp=temp, store=store, ready=ready, legacy=legacy, hold=hold)
    proc = subprocess.Popen([sys.executable, "-c", code])
    t0 = time.time()
    while not os.path.exists(ready):
        if proc.poll() is not None or time.time() - t0 > 30:
            proc.kill()
            pytest.fail("control: the holder process never took the lock")
        time.sleep(0.05)
    return proc


def _free(store):
    """Can a writer in THIS process take the lock right now?

    A try-only acquire never waits and never degrades, on either platform, so the answer is the lock's
    state and not a timing. An earlier version of this helper measured exclusion by the wait-and-degrade
    path, which exists only on Windows: POSIX `flock` waits for the holder and never degrades, so on
    Linux a correct lock read as "not excluded" (3.15.2 CI, 2026-09-28)."""
    lock = _StoreLock(store, try_only=True)
    lock.__enter__()
    try:
        return lock.held
    finally:
        lock.__exit__(None, None, None)


def _excluded(tmp_path, store):
    if not _free(str(tmp_path / "unrelated" / "memory.json")):
        pytest.fail("control: a writer cannot take a lock nobody holds, so a busy answer means nothing")
    return not _free(store)


def test_a_writer_with_another_temp_dir_is_excluded(tmp_path, monkeypatch):
    store = str(tmp_path / "shared" / "memory.json")
    os.makedirs(os.path.dirname(store))
    other_temp = tmp_path / "their-temp"
    other_temp.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path / "our-temp"))
    (tmp_path / "our-temp").mkdir()
    proc = _hold(tmp_path, store, str(other_temp))
    try:
        assert _excluded(tmp_path, store),             "a writer whose TEMP differs took the lock while another process held it"
    finally:
        proc.kill()
        proc.wait()


def test_an_older_writer_on_the_same_temp_is_still_excluded(tmp_path, monkeypatch):
    """A host pinned to an older version takes only the TEMP lock; a new writer must wait for it."""
    store = str(tmp_path / "shared" / "memory.json")
    os.makedirs(os.path.dirname(store))
    temp = tmp_path / "temp"
    temp.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(temp))
    proc = _hold(tmp_path, store, str(temp), legacy=True)
    try:
        assert _excluded(tmp_path, store),             "a new writer ignored the TEMP lock an older version holds"
    finally:
        proc.kill()
        proc.wait()


def _blocking_write(store):
    """Seconds a blocking writer in THIS process takes to hold the lock, and whether it held it."""
    t0 = time.time()
    with _StoreLock(store) as lock:
        return time.time() - t0, lock.held


@pytest.mark.parametrize("legacy", [False, True], ids=["another-temp", "older-writer"])
def test_a_blocking_writer_waits_for_the_holder_and_proceeds_after_release(tmp_path, monkeypatch, legacy):
    """What both platforms share: a blocking writer neither degrades nor takes the lock while another
    process holds it, and takes it once the holder releases. Windows retries `msvcrt.locking` until
    `LOCK_WAIT_S`, POSIX `flock` blocks; the wait here is set far beyond the hold, so either reaching
    the deadline would be a failure, not a pass."""
    import inspeximus.core as core
    monkeypatch.setattr(core, "LOCK_WAIT_S", 60)
    hold = 2.0
    store = str(tmp_path / "shared" / "memory.json")
    os.makedirs(os.path.dirname(store))
    (tmp_path / "our-temp").mkdir()
    (tmp_path / "their-temp").mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path / "our-temp"))
    took, held = _blocking_write(str(tmp_path / "unrelated" / "memory.json"))
    if took >= hold / 2 or not held:
        pytest.fail(f"control: a free lock took {took:.2f} s (held={held}), so a wait means nothing")
    before = sum(_StoreLock.DEGRADED.values())
    proc = _hold(tmp_path, store, str(tmp_path / ("our-temp" if legacy else "their-temp")), legacy, hold)
    try:
        took, held = _blocking_write(store)
    finally:
        proc.kill()
        proc.wait()
    assert sum(_StoreLock.DEGRADED.values()) == before, "the writer degraded instead of waiting"
    assert held, "the writer returned without the lock"
    assert took >= hold / 2, f"the writer took the lock after {took:.2f} s while the holder held it for {hold} s"
    assert took < 30, f"the writer waited {took:.2f} s, long after the holder released at {hold} s"


def test_the_lock_file_does_not_outlive_the_write(tmp_path):
    m = Inspeximus(path=str(tmp_path / "m.json"), receipts=True)
    m.remember("a record", key="k", object="v")
    m.flush()
    left = [n for n in os.listdir(tmp_path) if n.endswith(".lock")]
    assert not left, f"the store's directory kept lock files: {left}"


def test_a_lock_in_a_new_directory_creates_it(tmp_path):
    store = str(tmp_path / "new" / "deeper" / "m.json")
    with _StoreLock(store) as lock:
        assert os.path.exists(lock._path)
    assert os.path.isdir(os.path.dirname(store))


def test_posix_detects_a_lock_file_removed_under_it(tmp_path):
    p = str(tmp_path / "x.lock")
    fh = open(p, "a+b")
    try:
        assert _StoreLock._names(fh, p)
        if os.name == "nt":
            return                          # Windows refuses the unlink below while fh is open
        os.unlink(p)
        assert not _StoreLock._names(fh, p)
        open(p, "a+b").close()
        assert not _StoreLock._names(fh, p), "a new file under the same name is not the locked one"
    finally:
        fh.close()


# ── read-only directory: reads never lock, writes fail loudly ────────────────────────────────────────
def _read_only(d):
    if os.name == "nt":
        subprocess.run(["icacls", str(d), "/deny", "*S-1-1-0:(OI)(CI)(WD,AD,WEA,WA,DC)"], check=True,
                       stdout=subprocess.DEVNULL)
    else:
        if hasattr(os, "geteuid") and os.geteuid() == 0:
            pytest.skip("root ignores directory permissions")
        os.chmod(d, 0o555)


def _writable(d):
    if os.name == "nt":
        subprocess.run(["icacls", str(d), "/remove:d", "*S-1-1-0"], check=False, stdout=subprocess.DEVNULL)
    else:
        os.chmod(d, 0o755)


@pytest.mark.parametrize("fmt", ["json", "rows"])
def test_a_store_in_a_read_only_directory_opens_recalls_and_verifies(tmp_path, monkeypatch, fmt):
    d = tmp_path / "archive"
    d.mkdir()
    path = str(d / "m.json")
    monkeypatch.setenv("INSPEXIMUS_STORE_FORMAT", "json")
    m = Inspeximus(path=path, receipts=True)
    m.remember("the deploy target is staging", key="deploy", object="staging")
    m.flush()
    if fmt == "json":
        # a JSON store written by an older version: opening it would migrate it to rows
        before = set(os.listdir(d))
    else:
        monkeypatch.delenv("INSPEXIMUS_STORE_FORMAT")
        Inspeximus(path=path, receipts=True).flush()        # migrate once, while writable
        before = set(os.listdir(d))
    monkeypatch.delenv("INSPEXIMUS_STORE_FORMAT", raising=False)
    _read_only(d)
    try:
        try:
            open(str(d / "probe"), "w").close()
            pytest.fail("control: the directory is still writable, so nothing was tested")
        except OSError:
            pass
        r = Inspeximus(path=path, receipts=True)
        hits = r.recall("deploy target", k=3)
        assert any("staging" in h["text"] for h in hits), hits
        ok, problems = r.verify_writes()
        assert ok, problems
        assert set(os.listdir(d)) == before, "a read in a read-only directory changed it"
    finally:
        _writable(d)


def test_a_write_in_a_read_only_directory_fails_loudly(tmp_path):
    d = tmp_path / "ro"
    d.mkdir()
    path = str(d / "m.json")
    Inspeximus(path=path).flush()
    _read_only(d)
    try:
        with pytest.raises(StoreLockUnavailable, match="store lock"):
            with _StoreLock(path):
                pass
        assert not [n for n in os.listdir(tempfile.gettempdir())
                    if n.startswith("inspeximus-") and n.endswith(".lock")
                    and os.path.getmtime(os.path.join(tempfile.gettempdir(), n)) > time.time() - 5] \
            or os.name != "nt", "the write fell back to a lock in TEMP"
        m = Inspeximus(path=path)
        m.remember("x", key="k", object="v")
        with pytest.raises(OSError, match="store lock"):
            m.flush()
    finally:
        _writable(d)


# ── the lock never lands in a commit ─────────────────────────────────────────────────────────────────
def test_the_hook_ignores_the_lock_in_a_store_directory_it_creates(tmp_path, monkeypatch):
    import inspeximus.claude_code as cc
    for k in list(os.environ):
        if k.startswith("INSPEXIMUS_") and k != "INSPEXIMUS_NO_UPDATE_CHECK":
            monkeypatch.delenv(k, raising=False)
    proj = str(tmp_path / "proj")
    subprocess.run(["git", "init", "-q", proj], check=True)
    cc.capture({"cwd": proj, "session_id": "s", "hook_event_name": "PostToolUse", "tool_name": "Bash",
                "tool_input": {"command": "python -m pytest -q"}})
    d = os.path.join(proj, ".inspeximus")
    if not os.listdir(d):
        pytest.fail("control: the hook wrote no store, so nothing was created")
    store = cc._store(proj).path
    lock = str(store) + ".lock"
    open(lock, "w").close()
    try:
        r = subprocess.run(["git", "-C", proj, "check-ignore", "-q", os.path.relpath(lock, proj)])
        assert r.returncode == 0, "a lock file in the hook's store directory is not ignored by git"
        r = subprocess.run(["git", "-C", proj, "check-ignore", "-q", os.path.relpath(str(store), proj)])
        assert r.returncode != 0, "the ignore rule also hides the store, which is the user's decision"
    finally:
        os.unlink(lock)


def test_the_hook_leaves_an_existing_store_directory_alone(tmp_path, monkeypatch):
    import inspeximus.claude_code as cc
    for k in list(os.environ):
        if k.startswith("INSPEXIMUS_") and k != "INSPEXIMUS_NO_UPDATE_CHECK":
            monkeypatch.delenv(k, raising=False)
    proj = str(tmp_path / "proj")
    subprocess.run(["git", "init", "-q", proj], check=True)
    os.makedirs(os.path.join(proj, ".inspeximus"))
    cc.capture({"cwd": proj, "session_id": "s", "hook_event_name": "PostToolUse", "tool_name": "Bash",
                "tool_input": {"command": "python -m pytest -q"}})
    assert ".gitignore" not in os.listdir(os.path.join(proj, ".inspeximus"))


_COUNTER_WORKER = textwrap.dedent("""
    import os, sys, tempfile, time
    sys.path.insert(0, {root!r})
    tempfile.tempdir = sys.argv[1]            # every writer its own TEMP: only the store lock protects
    from inspeximus.core import _StoreLock
    for _ in range({n}):
        with _StoreLock({store!r}):
            with open({counter!r}) as fh:
                v = int(fh.read())
            time.sleep(0.0005)
            with open({counter!r}, "w") as fh:
                fh.write(str(v + 1))
""")


@pytest.mark.skipif(os.name == "nt", reason="the unlink-while-held protocol is the POSIX branch; Windows "
                                            "removes the file only when no process has it open")
def test_posix_writers_stay_exclusive_while_the_lock_file_comes_and_goes(tmp_path):
    """Each release removes the lock's name while still holding it. A waiter that wakes on the old file
    must notice and start again, or two writers hold "the" lock at once. A read-modify-write counter
    loses increments the moment that happens."""
    writers, n = 6, 150
    counter = str(tmp_path / "counter")
    with open(counter, "w") as fh:
        fh.write("0")
    store = str(tmp_path / "m.json")
    code = _COUNTER_WORKER.format(root=ROOT, n=n, store=store, counter=counter)
    temps = []
    for i in range(writers):
        t = tmp_path / f"temp{i}"
        t.mkdir()
        temps.append(str(t))
    procs = [subprocess.Popen([sys.executable, "-c", code, t]) for t in temps]
    for p in procs:
        assert p.wait(timeout=300) == 0
    with open(counter) as fh:
        got = int(fh.read())
    assert got == writers * n, f"{writers * n - got} of {writers * n} increments lost: two writers held the lock"
    assert not os.path.exists(_StoreLock(store)._path), "the lock file outlived the last write"
