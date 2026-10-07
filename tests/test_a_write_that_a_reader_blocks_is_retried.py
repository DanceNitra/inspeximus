"""3.16.6 (AUDIT-B): a state write that another process blocks is retried, and the archive run keeps its mark.

Windows refuses `os.replace` onto a file that another process has open. Measured: with three threads reading the
target in a loop, 293 of 300 replaces failed with PermissionError. The archive run's `done` mark and the hook's
attempt record were then lost, so the next hook saw a run that never finished and waited the full interval before
it started another.

Two changes, tested here: `write_atomic` retries a blocked replace for up to `REPLACE_RETRY_S` on Windows and then
raises as before, and the archive run keeps trying to write its mark until its own deadline instead of giving up at
the first failed write. Taken from AUDIT-B's tests on rc-317, with the re-stamp run, which is 3.17-only, left out.
"""
from __future__ import annotations

import json
import os
import sys
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from inspeximus import _safewrite  # noqa: E402
from inspeximus import claude_code as cc  # noqa: E402


def test_a_replace_that_a_reader_blocks_is_retried_and_then_succeeds(tmp_path, monkeypatch):
    """The first two attempts fail, the third lands, and no temporary file is left."""
    target = str(tmp_path / "state.json")
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
    assert not [f for f in os.listdir(str(tmp_path)) if f.endswith(".tmp")], "a temporary file was left behind"


def test_a_replace_that_stays_blocked_raises_after_the_bound(tmp_path, monkeypatch):
    monkeypatch.setattr(_safewrite, "RETRY_ON_PERMISSION", True)
    monkeypatch.setattr(_safewrite, "REPLACE_RETRY_S", 0.15)
    monkeypatch.setattr(os, "replace", lambda a, b: (_ for _ in ()).throw(PermissionError(13, "open")))
    t = time.monotonic()
    with pytest.raises(PermissionError):
        _safewrite.write_atomic(str(tmp_path / "state.json"), "x")
    assert time.monotonic() - t < 2.0, "the bound was not honoured"
    assert not [f for f in os.listdir(str(tmp_path)) if f.endswith(".tmp")]


def test_control_without_the_retry_flag_a_blocked_replace_raises_at_once(tmp_path, monkeypatch):
    """POSIX does not refuse a replace onto an open file, so the flag is off there and nothing waits."""
    calls = []
    monkeypatch.setattr(_safewrite, "RETRY_ON_PERMISSION", False)
    monkeypatch.setattr(os, "replace",
                        lambda a, b: calls.append(1) or (_ for _ in ()).throw(PermissionError(13, "open")))
    with pytest.raises(PermissionError):
        _safewrite.write_atomic(str(tmp_path / "state.json"), "x")
    assert len(calls) == 1


def test_the_flag_is_on_exactly_on_windows():
    assert _safewrite.RETRY_ON_PERMISSION is (os.name == "nt")


def test_the_archive_run_marks_itself_through_blocked_writes(tmp_path, monkeypatch):
    """The helper gives up at once here, so the RUN must go on trying until its own deadline."""
    path = str(tmp_path / "coding_memory.json")
    state = path + ".archive-auto.json"
    with open(state, "w", encoding="utf-8") as fh:
        json.dump({"last_attempt": time.time() - 100, "pid": 7}, fh)
    real = os.replace
    fails = []

    def flaky(a, b):
        if len(fails) < 4:
            fails.append(1)
            raise PermissionError(13, "a reader has the file open")
        return real(a, b)
    monkeypatch.setattr(_safewrite, "RETRY_ON_PERMISSION", False)
    monkeypatch.setattr(os, "replace", flaky)
    cc._mark_archive_run_done(path, True)
    st = json.load(open(state, encoding="utf-8"))
    assert len(fails) == 4, "CONTROL: the blocked writes did not happen, so this tests nothing"
    assert st.get("done") and st.get("result") == "ok" and st.get("pid") == 7, st


def test_control_no_attempt_record_means_nothing_to_mark(tmp_path):
    """A missing record still ends the run at once: nothing started it, and nothing is created."""
    path = str(tmp_path / "coding_memory.json")
    t = time.monotonic()
    cc._mark_archive_run_done(path, True)
    assert time.monotonic() - t < 1.0 and not os.path.exists(path + ".archive-auto.json")
