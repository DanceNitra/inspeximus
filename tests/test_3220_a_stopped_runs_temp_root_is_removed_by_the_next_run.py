"""A run stopped by its pid leaves its temporary root behind; the next run removes it (3.22, item 27).

Measured 2026-10-20 on the machine that runs this suite: the user's Temp held `inspeximus-run-*` roots of runs stopped
mid-way, because `_restore_temp_root` runs only when a run ends. `_stale_run_roots` is the sweep the next run does first.
"""
import json
import os
import subprocess
import sys
import time

import pytest

import conftest
from inspeximus import hookd


def _root(tmp_path, name, owner=None):
    r = tmp_path / name
    r.mkdir()
    if owner is not None:
        (r / conftest.RUN_OWNER_FILE).write_text(json.dumps(owner), encoding="utf-8")
    return str(r)


def _dead_pid():
    p = subprocess.Popen([sys.executable, "-c", "pass"])
    p.wait()
    return p.pid


def _me():
    return {"token": "t", "pid": os.getpid(), "start": hookd._proc_start(os.getpid())}


def test_a_root_whose_run_is_dead_is_stale_and_a_live_runs_root_is_kept(tmp_path):
    dead = _root(tmp_path, "inspeximus-run-dead", {"token": "t", "pid": _dead_pid(), "start": 1.0})
    live = _root(tmp_path, "inspeximus-run-live", _me())
    assert conftest._stale_run_roots(str(tmp_path)) == [dead]
    assert os.path.isdir(live)


def test_a_reused_pid_with_another_start_time_is_stale(tmp_path):
    me = _me()
    if me["start"] is None:
        pytest.skip("this platform reports no start time, so a reused pid cannot be told apart (AUDIT-A F-6)")
    reused = _root(tmp_path, "inspeximus-run-reused", dict(me, start=(me["start"] or 0) - 100))
    assert conftest._stale_run_roots(str(tmp_path)) == [reused]


def test_a_root_without_an_owner_file_is_kept_until_the_grace_period_ends(tmp_path):
    young = _root(tmp_path, "inspeximus-run-young")
    old = _root(tmp_path, "inspeximus-run-old")
    t = time.time() - conftest.UNOWNED_ROOT_GRACE_S - 60
    os.utime(old, (t, t))
    assert conftest._stale_run_roots(str(tmp_path)) == [old]
    assert os.path.isdir(young)


def test_only_run_roots_are_considered(tmp_path):
    other = _root(tmp_path, "something-else", {"token": "t", "pid": _dead_pid(), "start": 1.0})
    (tmp_path / "inspeximus-run-file").write_text("not a directory", encoding="utf-8")
    assert conftest._stale_run_roots(str(tmp_path)) == []
    assert os.path.isdir(other)


def test_a_run_sweeps_before_it_creates_its_own_root(tmp_path, monkeypatch):
    # The wiring, not only the helper: _own_temp_root removes a dead run's root from the temp directory it uses.
    dead = _root(tmp_path, "inspeximus-run-dead", {"token": "t", "pid": _dead_pid(), "start": 1.0})
    import tempfile

    class Config:
        pass
    cfg = Config()
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    for k in ("TEMP", "TMP", "TMPDIR", conftest.RUN_TMP_ENV, conftest.RUN_TOKEN_ENV):
        monkeypatch.setenv(k, os.environ.get(k, ""))
    conftest._own_temp_root(cfg)
    try:
        assert not os.path.exists(dead) and os.path.isdir(cfg._run_tmp)
    finally:
        conftest._restore_temp_root(cfg)


def test_a_live_run_is_kept_where_no_start_time_can_be_read(tmp_path, monkeypatch):
    """AUDIT-A F-6: on a Mac the start time is None, and the sweep read that as a dead run and removed a live root."""
    recorded_none = _root(tmp_path, "inspeximus-run-mac", dict(_me(), start=None))
    unreadable_now = _root(tmp_path, "inspeximus-run-later", dict(_me(), start=12345.0))
    dead = _root(tmp_path, "inspeximus-run-dead", {"token": "t", "pid": _dead_pid(), "start": None})
    monkeypatch.setattr(hookd, "_proc_start", lambda pid: None)
    # CONTROL: a dead pid is stale whatever its start time, so the rule did not turn into "keep everything".
    assert conftest._stale_run_roots(str(tmp_path)) == [dead]
    assert os.path.isdir(recorded_none) and os.path.isdir(unreadable_now)
