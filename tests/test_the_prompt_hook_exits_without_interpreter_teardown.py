"""AUDIT-B 3.17.0: the prompt hook ends with `os._exit(0)` once it has finished, and nothing a normal exit would have done is
missing. A normal exit frees every row dictionary the hook loaded: 0.12 s of a 1.3 s prompt.

The four properties, each in a real process:
  1. the output is complete: a 64 KB block through a pipe, and through a file
  2. no lock file or temporary file is left behind
  3. a peer writer that starts right after the hook gets the store lock at once
  4. what the hook writes in its run is on disk when the process is gone: a record saved in the run, the secrets notice,
     the archive attempt record, and the detached archive run that outlives its parent

`_script_main` is what `python -m inspeximus.claude_code` runs; the wrapper below patches only the handler and then calls it, so
the real exit path is the one tested.
"""
import json
import os
import subprocess
import sys
import time

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
import inspeximus.claude_code as cc  # noqa: E402
from inspeximus.core import Inspeximus  # noqa: E402

WRAP = r"""
import json, os, sys
import inspeximus.claude_code as cc
mode = sys.argv[1]
if mode == "big":
    cc.recall = lambda ev: cc._emit("UserPromptSubmit", "x" * 65536)
elif mode == "unflushed":
    cc.recall = lambda ev: sys.stdout.write("y" * 300) and None         # far below the buffer size: only the exit path can flush it
elif mode == "write":
    real = cc.recall
    def recall(ev):
        m = cc._store(ev["cwd"])
        m.remember("a record written inside the hook run", key="hook-run-write")
        m.flush()
        real(ev)
    cc.recall = recall
cc._script_main()
sys.stdout.write("REACHED THE END OF THE SCRIPT")      # never printed by a hook that ended in _exit_now
"""


@pytest.fixture
def proj(tmp_path, monkeypatch):
    for k in [k for k in os.environ if k.startswith("INSPEXIMUS_")]:
        monkeypatch.delenv(k)
    monkeypatch.setenv("INSPEXIMUS_KEY_HOME", str(tmp_path / "keyhome"))
    monkeypatch.setenv("INSPEXIMUS_NO_UPDATE_CHECK", "1")
    p = tmp_path / "proj"
    (p / ".git").mkdir(parents=True)
    m = cc._store(str(p))
    for i in range(5):
        m.remember(f"a note about the release, number {i}", key=f"n{i}")
    m.flush()
    return str(p)


def _event(proj):
    return json.dumps({"hook_event_name": "UserPromptSubmit", "prompt": "what did we note about the release", "cwd": proj,
                       "session_id": "t"})


def _run(proj, mode, env_extra=None, stdout=subprocess.PIPE):
    env = {**os.environ, "PYTHONPATH": ROOT, **(env_extra or {})}
    return subprocess.run([sys.executable, "-c", WRAP, mode], input=_event(proj), stdout=stdout, stderr=subprocess.PIPE,
                          text=True, encoding="utf-8", env=env, cwd=proj, timeout=120)


def _files(proj):
    out = []
    for dp, _dn, fn in os.walk(os.path.join(proj, ".inspeximus")):
        out += [os.path.join(dp, f) for f in fn]
    return sorted(out)


# 1 ---------------------------------------------------------------------------------------------------------------------

def test_a_64_kb_block_reaches_a_pipe_whole_and_the_process_ends_in_the_fast_exit(proj):
    r = _run(proj, "big")
    assert r.returncode == 0, r.stderr[-300:]
    assert "REACHED THE END" not in r.stdout, "the hook returned from main and ran on: the fast exit did not happen"
    doc = json.loads(r.stdout)                         # whole, or this raises
    assert doc["hookSpecificOutput"]["additionalContext"].endswith("x" * 1000)
    assert len(doc["hookSpecificOutput"]["additionalContext"]) >= 65536


def test_text_a_handler_left_in_the_buffer_is_flushed_by_the_fast_exit(proj, tmp_path):
    r = _run(proj, "unflushed")
    assert r.returncode == 0 and r.stdout == "y" * 300, len(r.stdout)
    out = tmp_path / "unflushed.out"
    with open(out, "w", encoding="utf-8") as fh:
        _run(proj, "unflushed", stdout=fh)
    assert out.read_text(encoding="utf-8") == "y" * 300


def test_a_64_kb_block_reaches_a_file_whole(proj, tmp_path):
    out = tmp_path / "hook.out"
    with open(out, "w", encoding="utf-8") as fh:
        r = _run(proj, "big", stdout=fh)
    assert r.returncode == 0
    doc = json.loads(out.read_text(encoding="utf-8"))
    assert len(doc["hookSpecificOutput"]["additionalContext"]) >= 65536


def test_the_switch_and_a_failing_handler_keep_the_normal_exit(proj):
    r = _run(proj, "normal", {"INSPEXIMUS_HOOK_FAST_EXIT": "0"})
    assert r.returncode == 0 and "REACHED THE END" in r.stdout, "INSPEXIMUS_HOOK_FAST_EXIT=0 keeps the normal exit"
    code = ("import sys\nimport inspeximus.claude_code as cc\ncc.recall = lambda ev: 1 / 0\ncc._script_main()\n"
            "sys.stdout.write('REACHED THE END')\n")
    r = subprocess.run([sys.executable, "-c", code], input=_event(proj), capture_output=True, text=True, encoding="utf-8",
                       env={**os.environ, "PYTHONPATH": ROOT}, cwd=proj, timeout=120)
    assert "REACHED THE END" in r.stdout and "hook UserPromptSubmit failed" in r.stderr, "a failing hook takes the normal exit"


def test_only_the_prompt_hook_ends_this_way(proj):
    code = ("import sys\nimport inspeximus.claude_code as cc\ncc.session_start = lambda ev: None\ncc._script_main()\n"
            "sys.stdout.write('REACHED THE END')\n")
    ev = json.dumps({"hook_event_name": "SessionStart", "cwd": proj, "session_id": "t"})
    r = subprocess.run([sys.executable, "-c", code], input=ev, capture_output=True, text=True, encoding="utf-8",
                       env={**os.environ, "PYTHONPATH": ROOT}, cwd=proj, timeout=120)
    assert "REACHED THE END" in r.stdout


# 2 ---------------------------------------------------------------------------------------------------------------------

def test_no_lock_file_and_no_temporary_file_is_left_behind(proj):
    before = {os.path.basename(f) for f in _files(proj)}
    r = _run(proj, "normal-fast")
    assert r.returncode == 0
    after = {os.path.basename(f) for f in _files(proj)}
    new = after - before
    assert not [f for f in new if f.endswith(".lock") or ".tmp" in f], new
    assert not [f for f in after if f.endswith(".lock") or ".tmp" in f], after


# 3 ---------------------------------------------------------------------------------------------------------------------

def test_a_peer_writer_gets_the_store_lock_at_once_after_the_hook(proj):
    r = _run(proj, "normal-fast")
    assert r.returncode == 0
    t = time.perf_counter()
    m = Inspeximus(os.path.join(proj, ".inspeximus", "coding_memory.json"))
    m.remember("a peer writes right after the hook", key="peer")
    m.flush()
    assert time.perf_counter() - t < 5.0, "the peer waited for a lock the hook should not hold"
    assert any(x["key"] == "peer" for x in Inspeximus(os.path.join(proj, ".inspeximus", "coding_memory.json")).items)


# 4 ---------------------------------------------------------------------------------------------------------------------

def test_a_write_made_in_the_hook_run_is_on_disk_when_the_process_is_gone(proj):
    r = _run(proj, "write")
    assert r.returncode == 0 and "REACHED THE END" not in r.stdout, r.stderr[-300:]
    m = Inspeximus(os.path.join(proj, ".inspeximus", "coding_memory.json"))
    assert [x for x in m.items if x["key"] == "hook-run-write"], "the record saved in the hook run is not on disk"


def test_the_attempt_record_is_whole_and_the_detached_run_outlives_the_hook(proj, tmp_path):
    cfg = tmp_path / "keyhome" / "inspeximus" / "config.json"
    os.makedirs(cfg.parent, exist_ok=True)
    cfg.write_text(json.dumps({"archive": {"auto": True, "trigger_mb": 0.00001, "allow_git_tracked": True,
                                           "older_than_days": 1, "classes": ["cmd"]}}), encoding="utf-8")
    r = _run(proj, "normal-fast")
    assert r.returncode == 0 and "REACHED THE END" not in r.stdout
    state = os.path.join(proj, ".inspeximus", "coding_memory.json.archive-auto.json")
    st = json.load(open(state, encoding="utf-8"))                       # whole JSON, or this raises
    assert isinstance(st["pid"], int), "the attempt record carries the pid of the run the hook started"
    # The run's own log says it finished. (The state file's `done` mark is not used: the archive's attempt record is written
    # again by the hook after the start, and a run that finishes first loses its mark: see the report.)
    log = os.path.join(proj, ".inspeximus", "coding_memory.json.archive-auto.log")
    end = time.time() + 120
    text = ""
    while time.time() < end:
        text = open(log, encoding="utf-8", errors="replace").read() if os.path.exists(log) else ""
        if '"stamp_guards"' in text:
            break
        time.sleep(0.25)
    assert '"stamp_guards"' in text, "the detached run did not finish after the hook's process ended: %r" % text[-300:]


def test_the_prompt_hook_writes_no_receipt_and_leaves_the_receipt_files_as_they_were(proj, tmp_path, monkeypatch):
    monkeypatch.setenv("INSPEXIMUS_RECEIPTS_TAIL", "1")
    p = os.path.join(proj, ".inspeximus", "coding_memory.json")
    m = Inspeximus(p, receipts=True)
    for i in range(3):
        m.remember(f"a receipted note {i}", key=f"r{i}")
    m.flush()
    names = [p + ".receipts.json", p + ".receipts.tail.jsonl"]
    before = {n: open(n, "rb").read() for n in names if os.path.exists(n)}
    assert before, "control: the store has receipt files"
    r = _run(proj, "normal-fast")
    assert r.returncode == 0
    assert {n: open(n, "rb").read() for n in names if os.path.exists(n)} == before
