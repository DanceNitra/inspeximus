"""AUDIT-A's final review of rc-317 (cb2b476c): F-45 and F-46, and the retry that the two new helpers lacked.

F-45  a link at `<store>.embedid` is replaced by a real file; flush() does not raise and the writes go on
F-46  the one-release fallback never lets a repository-planted `<store>.archive-auto.json` decide: a record dated in the future is
      ignored, and a genuine old record is copied into the key home once and then never read again
      `copy_file` and `fresh_file` retry a blocked replace and unlink as `write_atomic` does
"""
import json
import os
import sys
import time

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import inspeximus.claude_code as cc  # noqa: E402
from inspeximus import Inspeximus, _safewrite, core  # noqa: E402

DIM = 8


def _emb(text):
    h = sum(map(ord, text)) % 251
    return [((h * (i + 3)) % 251) / 251.0 for i in range(DIM)]


@pytest.fixture()
def env(tmp_path, monkeypatch):
    for k in [k for k in os.environ if k.startswith("INSPEXIMUS_")]:
        monkeypatch.delenv(k)
    monkeypatch.setenv("INSPEXIMUS_KEY_HOME", str(tmp_path / "kh"))
    monkeypatch.setenv("INSPEXIMUS_NO_UPDATE_CHECK", "1")
    (tmp_path / "kh" / "inspeximus").mkdir(parents=True)
    core._SAID_ONCE.clear()
    return tmp_path


# ── F-45 ────────────────────────────────────────────────────────────────────────────────────────────────────────────────

@pytest.mark.skipif(os.name == "nt", reason="a file link needs a privilege here")
def test_f45_a_link_at_the_embedid_sidecar_is_replaced_and_the_writes_go_on(env, capfd):
    p = str(env / "memory.json")
    m = Inspeximus(p, embed=_emb, embed_id="e1", persist_vectors=True)
    m.remember("first", key="a")
    m.flush()
    victim = env / "victim.txt"
    victim.write_text("USER FILE\n")
    os.remove(p + ".embedid")
    os.symlink(str(victim), p + ".embedid")
    m = Inspeximus(p, embed=_emb, embed_id="e1", persist_vectors=True)
    m.remember("second", key="b")
    m.flush()                                                       # must not raise
    assert victim.read_text() == "USER FILE\n", "the file the link named must be untouched"
    assert not _safewrite.is_link(p + ".embedid") and open(p + ".embedid", encoding="utf-8").read() == "e1"
    assert "was a link" in capfd.readouterr().err
    assert sorted(r["text"] for r in Inspeximus(p, embed=_emb, embed_id="e1", persist_vectors=True).items) == ["first", "second"]


def test_f45_without_a_link_nothing_is_said_and_the_sidecar_is_written(env, capfd):
    p = str(env / "memory.json")
    m = Inspeximus(p, embed=_emb, embed_id="e1", persist_vectors=True)
    m.remember("first", key="a")
    m.flush()
    assert open(p + ".embedid", encoding="utf-8").read() == "e1" and "was a link" not in capfd.readouterr().err


# ── F-46 ────────────────────────────────────────────────────────────────────────────────────────────────────────────────

def _big(env, monkeypatch):
    (env / "kh" / "inspeximus" / "config.json").write_text(json.dumps({"archive": {"auto": True, "trigger_mb": 0.0001}}))
    proj = env / "proj"
    os.makedirs(proj / ".git")
    m = cc._store(str(proj))
    for i in range(30):
        m.remember("ran: command number %d with some output" % i, key="cmd:%d" % i, tags=["bash"], mtype="episodic")
    m.flush()
    import subprocess
    monkeypatch.setattr(subprocess, "Popen", lambda argv, **kw: type("P", (), {"pid": 1})())
    return proj, str(m.path)


def test_f46_a_planted_record_dated_in_the_future_does_not_switch_the_archive_off(env, monkeypatch):
    proj, path = _big(env, monkeypatch)
    with open(path + ".archive-auto.json", "w", encoding="utf-8") as fh:
        json.dump({"last_attempt": time.time() + 10 * 365 * 86400}, fh)
    assert cc.maybe_archive_in_background(str(proj)) == "started"
    assert json.load(open(cc._archive_state_path(path), encoding="utf-8"))["last_attempt"] < time.time() + 120, "the key home has its own record"


@pytest.mark.parametrize("planted", [{"last_attempt": "tomorrow"}, {"last_attempt": True}, {"last_attempt": -5}, [1, 2], {"x": 1}])
def test_f46_a_planted_record_of_the_wrong_shape_is_ignored(env, monkeypatch, planted):
    proj, path = _big(env, monkeypatch)
    with open(path + ".archive-auto.json", "w", encoding="utf-8") as fh:
        json.dump(planted, fh)
    assert cc.maybe_archive_in_background(str(proj)) == "started"


def test_f46_a_genuine_old_record_is_copied_into_the_key_home_once_and_never_read_again(env, monkeypatch):
    proj, path = _big(env, monkeypatch)
    old = path + ".archive-auto.json"
    stamp = time.time() - 5
    with open(old, "w", encoding="utf-8") as fh:
        json.dump({"last_attempt": stamp, "pid": 999999, "done": 1, "result": "ok"}, fh)
    assert cc.maybe_archive_in_background(str(proj)) == "recent", "a run 3.16 started a moment ago still counts"
    moved = json.load(open(cc._archive_state_path(path), encoding="utf-8"))
    assert moved["last_attempt"] == stamp and moved["pid"] == 999999 and "result" not in moved and "done" not in moved, "the time and the pid are carried over (a killed run stays distinguishable), not a result or a done mark"
    os.remove(old)
    assert cc.maybe_archive_in_background(str(proj)) == "recent", "the key home's copy now decides"
    with open(old, "w", encoding="utf-8") as fh:                    # planted AFTER the move: the old place is not read any more
        json.dump({"last_attempt": time.time() + 10 ** 9}, fh)
    assert cc.maybe_archive_in_background(str(proj)) == "recent"
    assert cc._read_archive_state(path)["last_attempt"] == stamp


# ── the retry, for the two helpers ─────────────────────────────────────────────────────────────────────────────────────

def test_copy_file_retries_a_blocked_replace(env, monkeypatch):
    src, dst = env / "src.txt", str(env / "dst.txt")
    src.write_text("data")
    real, calls = os.replace, []

    def flaky(a, b):
        calls.append(1)
        if len(calls) < 3:
            raise PermissionError(13, "open")
        return real(a, b)
    monkeypatch.setattr(_safewrite, "RETRY_ON_PERMISSION", True)
    monkeypatch.setattr(os, "replace", flaky)
    _safewrite.copy_file(str(src), dst)
    assert len(calls) == 3 and open(dst).read() == "data"


def test_fresh_file_retries_a_blocked_unlink(env, monkeypatch):
    target = env / "tmpfile"
    target.write_text("leftover")
    real, calls = os.unlink, []

    def flaky(p, *a, **k):
        calls.append(1)
        if len(calls) < 3:
            raise PermissionError(13, "open")
        return real(p, *a, **k)
    monkeypatch.setattr(_safewrite, "RETRY_ON_PERMISSION", True)
    monkeypatch.setattr(os, "unlink", flaky)
    _safewrite.fresh_file(str(target))
    assert len(calls) == 3 and os.path.getsize(str(target)) == 0


def test_the_retry_is_bounded_for_both_helpers(env, monkeypatch):
    (env / "a").write_text("x")
    monkeypatch.setattr(_safewrite, "RETRY_ON_PERMISSION", True)
    monkeypatch.setattr(_safewrite, "REPLACE_RETRY_S", 0.1)
    monkeypatch.setattr(os, "replace", lambda a, b: (_ for _ in ()).throw(PermissionError(13, "open")))
    seen = []
    t0 = time.monotonic()
    def blocked(p, *a, **k):
        seen.append(1)
        assert time.monotonic() - t0 < 3, "the retry has no bound"      # a missing bound fails here, it does not hang the run
        raise PermissionError(13, "open")
    monkeypatch.setattr(os, "unlink", blocked)
    monkeypatch.setattr(_safewrite.time if hasattr(_safewrite, "time") else __import__("time"), "sleep", lambda s: None)
    with pytest.raises(PermissionError):
        _safewrite.copy_file(str(env / "a"), str(env / "b"))
    with pytest.raises(PermissionError):
        _safewrite.fresh_file(str(env / "a"))
