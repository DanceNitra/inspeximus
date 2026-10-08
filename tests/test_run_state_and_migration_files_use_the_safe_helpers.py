"""3.17.0: the files a run or a migration makes beside a store go through the safe-write helper, and the run state lives in the key home.

  * the archive run's attempt record and log are in `<key home>/inspeximus/archive-auto/`, nothing is written beside the store, and
    for ONE RELEASE the record 3.16 kept beside the store is still read when the key home has none;
  * the migration's `.pre-rows.bak` and `.rows-tmp` are never made through a link a repository shipped at that name;
  * the receipt tail is never appended through a link.
"""
import errno
import json
import os
import subprocess
import sys
import time

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import inspeximus.claude_code as cc  # noqa: E402
from inspeximus import Inspeximus, _safewrite  # noqa: E402

VICTIM = "A FILE THAT BELONGS TO THE USER\n"


@pytest.fixture()
def env(tmp_path, monkeypatch):
    for k in [k for k in os.environ if k.startswith("INSPEXIMUS_")]:
        monkeypatch.delenv(k)
    monkeypatch.setenv("INSPEXIMUS_KEY_HOME", str(tmp_path / "kh"))
    monkeypatch.setenv("INSPEXIMUS_NO_UPDATE_CHECK", "1")
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "home"))
    (tmp_path / "home").mkdir()
    (tmp_path / "kh" / "inspeximus").mkdir(parents=True)
    return tmp_path


def _plant(name, tmp_path):
    """A link at `name` to something of the user's; returns a function that says whether the target is untouched."""
    name = str(name)
    if os.name == "nt":
        victim = tmp_path / "user_dir"
        victim.mkdir(exist_ok=True)
        (victim / "keep.txt").write_text(VICTIM)
        r = subprocess.run(["cmd", "/c", "mklink", "/J", name, str(victim)], capture_output=True, text=True, encoding="utf-8",
                           errors="replace")
        assert r.returncode == 0, r.stderr
        return lambda: sorted(os.listdir(victim)) == ["keep.txt"] and (victim / "keep.txt").read_text() == VICTIM
    victim = tmp_path / "user_file.json"
    victim.write_text(VICTIM)
    os.symlink(str(victim), name)
    return lambda: victim.read_text() == VICTIM


# ── the run state is in the key home ──────────────────────────────────────────────────────────────────────────────────

def _big_project(env, monkeypatch):
    (env / "kh" / "inspeximus" / "config.json").write_text(json.dumps({"archive": {"auto": True, "trigger_mb": 0.0001}}))
    proj = env / "proj"
    os.makedirs(proj / ".git")
    m = cc._store(str(proj))
    for i in range(40):
        m.remember("ran: command number %d with some output" % i, key="cmd:%d" % i, tags=["bash"], mtype="episodic")
    m.flush()
    monkeypatch.setattr(subprocess, "Popen", lambda argv, **kw: type("P", (), {"pid": 1})())
    return proj, str(m.path)


def test_the_archive_run_keeps_its_state_and_log_in_the_key_home_and_none_beside_the_store(env, monkeypatch):
    proj, path = _big_project(env, monkeypatch)
    before = set(os.listdir(os.path.dirname(path)))
    assert cc.maybe_archive_in_background(str(proj)) == "started"
    assert os.path.exists(cc._archive_state_path(path)) and os.path.exists(cc._archive_state_path(path, ".log"))
    assert os.path.dirname(cc._archive_state_path(path)).startswith(str(env / "kh")), "the state must be in the key home"
    new = set(os.listdir(os.path.dirname(path))) - before
    assert not [f for f in new if "archive-auto" in f], new


def test_for_one_release_the_state_3_16_kept_beside_the_store_is_still_read(env, monkeypatch):
    proj, path = _big_project(env, monkeypatch)
    with open(path + ".archive-auto.json", "w", encoding="utf-8") as fh:
        json.dump({"last_attempt": time.time(), "hot_bytes": 1}, fh)
    assert cc.maybe_archive_in_background(str(proj)) == "recent", "a run 3.16 started a moment ago still counts"
    os.remove(path + ".archive-auto.json")
    assert cc.maybe_archive_in_background(str(proj)) == "recent", "its time was carried into the key home (F-46)"
    assert not os.path.exists(path + ".archive-auto.json"), "nothing is written to the old place any more"


def test_the_key_homes_record_wins_over_the_old_one(env, monkeypatch):
    proj, path = _big_project(env, monkeypatch)
    with open(path + ".archive-auto.json", "w", encoding="utf-8") as fh:
        json.dump({"last_attempt": time.time(), "hot_bytes": 1}, fh)
    with open(cc._archive_state_path(path), "w", encoding="utf-8") as fh:
        json.dump({"last_attempt": time.time() - 100000, "hot_bytes": 1}, fh)
    assert cc._read_archive_state(path)["last_attempt"] < time.time() - 1000
    assert cc.maybe_archive_in_background(str(proj)) == "started"


def test_the_heal_and_the_archive_write_their_state_through_the_safe_helper(env, monkeypatch):
    seen = []
    real = _safewrite.write_atomic
    monkeypatch.setattr(_safewrite, "write_atomic", lambda p, *a, **k: seen.append(os.path.basename(str(p))) or real(p, *a, **k))
    proj, path = _big_project(env, monkeypatch)
    cc.maybe_archive_in_background(str(proj))
    cc._mark_stamp_run(path, True)                    # no attempt record yet: nothing is written, and nothing raises
    cc.maybe_restamp_in_background(str(proj), foreign=3)
    assert any(n.endswith(".json") for n in seen), seen
    src = open(os.path.join(ROOT, "inspeximus", "claude_code.py"), encoding="utf-8").read()
    assert "os.replace(tmp, state)" not in src, "a run state is written by write_atomic, not by a private temp and replace"


# ── the migration's files ─────────────────────────────────────────────────────────────────────────────────────────────

def _json_store(env, monkeypatch, name="s.json"):
    p = str(env / name)
    monkeypatch.setenv("INSPEXIMUS_STORE_FORMAT", "json")
    m = Inspeximus(p)
    m.remember("a note about the release", key="n1")
    m.flush()
    monkeypatch.delenv("INSPEXIMUS_STORE_FORMAT")
    return p


def test_a_link_shipped_at_the_migrations_temp_name_is_not_written_through(env, monkeypatch):
    p = _json_store(env, monkeypatch)
    intact = _plant(p + ".rows-tmp", env)
    m = Inspeximus(p)                                     # the open migrates the legacy store to rows
    assert [r["text"] for r in m.items] == ["a note about the release"]
    assert intact(), "the migration wrote through the link at .rows-tmp"
    from inspeximus import sqlite_store
    assert sqlite_store.looks_like_sqlite(p), "the migration must have run: a link at the temp name does not stop it"


@pytest.mark.skipif(os.name == "nt", reason="a dangling link needs a symlink")
def test_a_dangling_link_at_the_backup_name_is_refused_and_creates_nothing(env, monkeypatch):
    p = _json_store(env, monkeypatch)
    ghost = str(env / "nowhere.bak")
    os.symlink(ghost, p + ".pre-rows.bak")
    m = Inspeximus(p)
    assert [r["text"] for r in m.items] == ["a note about the release"], "the store must still open"
    assert not os.path.exists(ghost), "the backup was copied through a link to a file that did not exist"


def test_the_helpers_themselves(env):
    d = env / "h"
    d.mkdir()
    target = d / "t.txt"
    target.write_text("keep")
    link = str(d / "l")
    if os.name == "nt":
        pytest.skip("a file link needs a privilege")
    os.symlink(str(target), link)
    _safewrite.fresh_file(link)                           # removes the link, creates an empty regular file, leaves the target
    assert not _safewrite.is_link(link) and os.path.getsize(link) == 0 and target.read_text() == "keep"
    with pytest.raises(FileExistsError):
        fd = os.open(link, os.O_WRONLY | os.O_CREAT | os.O_EXCL)           # exclusive: the name is taken
        os.close(fd)
    os.remove(link)
    os.symlink(str(target), link)
    with pytest.raises(_safewrite.LinkRefused):
        _safewrite.copy_file(str(d / "t.txt"), link)
    assert target.read_text() == "keep"
    plain = str(d / "copy")
    _safewrite.copy_file(str(target), plain)
    assert open(plain).read() == "keep"


# ── the receipt tail ──────────────────────────────────────────────────────────────────────────────────────────────────

@pytest.mark.skipif(os.name == "nt", reason="a file link needs a privilege")
def test_the_receipt_tail_is_never_appended_through_a_link(env):
    from inspeximus import receipts_tail
    victim = env / "victim.txt"
    victim.write_text(VICTIM)
    tail = str(env / "store.json.receipts.tail.jsonl")
    os.symlink(str(victim), tail)
    with pytest.raises(OSError) as ei:
        receipts_tail.append(tail, b'{"x":1}\n')
    assert ei.value.errno == errno.ELOOP
    assert victim.read_text() == VICTIM


# ── a run's done mark, and a replace that a reader blocks (3.17.0) ─────────────────────────────────────────────────────

def test_a_replace_that_a_reader_blocks_is_retried_and_then_succeeds(env, monkeypatch):
    """Windows refuses os.replace onto an open file. The first two attempts fail here, the third lands."""
    target = str(env / "state.json")
    real = os.replace
    calls = []

    def flaky(a, b):
        calls.append(1)
        if len(calls) < 3:
            raise PermissionError(13, "the file is open")
        return real(a, b)
    monkeypatch.setattr(_safewrite, "RETRY_ON_PERMISSION", True)
    monkeypatch.setattr(os, "replace", flaky)
    _safewrite.write_atomic(target, '{"ok": true}')
    assert len(calls) == 3 and json.load(open(target, encoding="utf-8")) == {"ok": True}
    assert not [f for f in os.listdir(str(env)) if f.endswith(".tmp")], "no temp file is left behind"


def test_a_replace_that_stays_blocked_raises_after_the_bound(env, monkeypatch):
    monkeypatch.setattr(_safewrite, "RETRY_ON_PERMISSION", True)
    monkeypatch.setattr(_safewrite, "REPLACE_RETRY_S", 0.15)
    monkeypatch.setattr(os, "replace", lambda a, b: (_ for _ in ()).throw(PermissionError(13, "open")))
    with pytest.raises(PermissionError):
        _safewrite.write_atomic(str(env / "state.json"), "x")


def test_without_the_retry_flag_a_blocked_replace_raises_at_once(env, monkeypatch):
    calls = []
    monkeypatch.setattr(_safewrite, "RETRY_ON_PERMISSION", False)
    monkeypatch.setattr(os, "replace", lambda a, b: calls.append(1) or (_ for _ in ()).throw(PermissionError(13, "open")))
    with pytest.raises(PermissionError):
        _safewrite.write_atomic(str(env / "state.json"), "x")
    assert len(calls) == 1


def test_the_restamp_run_waits_for_the_pid_and_writes_its_mark_again(env, monkeypatch):
    """The hook read the record before the run's mark and wrote the pid after it: the mark is gone, and the run writes it again."""
    import threading
    path = str(env / "proj" / ".inspeximus" / "coding_memory.json")
    os.makedirs(os.path.dirname(path))
    state = cc._stamp_state_path(path)
    with open(state, "w", encoding="utf-8") as fh:
        json.dump({"last_attempt": time.time(), "foreign": 3}, fh)
    t = threading.Thread(target=cc._mark_stamp_run, args=(path, True))
    t.start()
    time.sleep(0.5)                                          # the run has marked itself and waits for the pid
    seen = json.load(open(state, encoding="utf-8"))
    assert seen.get("done"), "control: the run marked itself before the hook's write"
    with open(state, "w", encoding="utf-8") as fh:            # the hook's write, from a read before the mark
        json.dump({"last_attempt": seen["last_attempt"], "foreign": 3, "pid": 7}, fh)
    t.join(15)
    st = json.load(open(state, encoding="utf-8"))
    assert not t.is_alive() and st.get("done") and st.get("pid") == 7 and st.get("result") == "ok", st


def test_the_restamp_run_marks_itself_through_a_blocked_replace(env, monkeypatch):
    path = str(env / "proj" / ".inspeximus" / "coding_memory.json")
    os.makedirs(os.path.dirname(path))
    state = cc._stamp_state_path(path)
    with open(state, "w", encoding="utf-8") as fh:
        json.dump({"last_attempt": time.time() - 100, "foreign": 3}, fh)
    real = os.replace
    fails = []

    def flaky(a, b):
        if len(fails) < 4:
            fails.append(1)
            raise PermissionError(13, "a reader has the file open")
        return real(a, b)
    monkeypatch.setattr(_safewrite, "RETRY_ON_PERMISSION", False)       # the helper gives up at once: the RUN must go on trying
    monkeypatch.setattr(os, "replace", flaky)
    cc._mark_stamp_run(path, True)
    assert json.load(open(state, encoding="utf-8")).get("done"), "the run left itself unmarked after four blocked writes"
