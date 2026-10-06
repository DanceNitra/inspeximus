"""AUDIT-A round 2 on v3.16.3, item 2: the detached `--maintain` run and a job object that ends with the hook.
Measured on Windows with a job object (KILL_ON_JOB_CLOSE, no breakaway) around a hook that started the run on a
20,000-row copy: the run did not survive the job closing. An early kill left the store untouched. A late kill left
segments and the log written and the hot rows still in the store: log valid, no temp, nothing lost, and the next
`apply` finished the move (0 rows hot, 20,000 in segments, no duplicate). The half state is safe. Two things are not:
1. the run is started without CREATE_BREAKAWAY_FROM_JOB, so a job that allows breakaway cannot be left;
2. the attempt is recorded BEFORE the start and never marked done, so a killed run blocks the next try for
   `min_interval_s` (3,600 s by default), and if every hook's process tree is ended the same way, the archive never
   completes. Real Claude Code 2.1.291 in `-p` mode runs hooks in a job that allows breakaway and has no
   KILL_ON_JOB_CLOSE (measured); other hosts were not measured.
Fails on v3.16.3."""
import json
import os
import subprocess
import sys
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from inspeximus import claude_code as cc  # noqa: E402


def _setup(tmp_path, monkeypatch):
    for k in [k for k in os.environ if k.startswith("INSPEXIMUS_")]:
        monkeypatch.delenv(k)
    monkeypatch.setenv("INSPEXIMUS_KEY_HOME", str(tmp_path / "keyhome"))
    (tmp_path / "keyhome" / "inspeximus").mkdir(parents=True)
    (tmp_path / "keyhome" / "inspeximus" / "config.json").write_text(json.dumps(
        {"archive": {"auto": True, "trigger_mb": 0.0001}}))
    store_dir = tmp_path / "store"
    store_dir.mkdir()
    monkeypatch.setenv("INSPEXIMUS_CODING_STORE", str(store_dir))
    (store_dir / "coding_memory.json").write_bytes(b"x" * 4096)
    spawned = []
    monkeypatch.setattr(subprocess, "Popen", lambda argv, **kw: spawned.append((argv, kw)) or type("P", (), {"pid": 1})())
    return str(store_dir / "coding_memory.json"), spawned


@pytest.mark.skipif(os.name != "nt", reason="job objects are a Windows mechanism")
def test_the_detached_run_asks_to_leave_the_hooks_job(tmp_path, monkeypatch):
    _, spawned = _setup(tmp_path, monkeypatch)
    assert cc.maybe_archive_in_background(str(tmp_path / "repo")) == "started"
    flags = spawned[0][1].get("creationflags", 0)
    assert flags & 0x01000000, "CREATE_BREAKAWAY_FROM_JOB is not set, so a job that ends with the hook ends the run"


def test_a_run_that_was_killed_is_tried_again_after_the_floor_not_after_an_hour(tmp_path, monkeypatch):
    store, spawned = _setup(tmp_path, monkeypatch)
    dead_pid = 2 ** 22 + 12345                                      # no such process
    with open(store + ".archive-auto.json", "w", encoding="utf-8") as fh:
        json.dump({"last_attempt": time.time() - 120, "pid": dead_pid}, fh)
    assert cc.maybe_archive_in_background(str(tmp_path / "repo")) == "started", \
        "a run whose process is gone, started 2 minutes ago, still blocks the next try for the full interval"
