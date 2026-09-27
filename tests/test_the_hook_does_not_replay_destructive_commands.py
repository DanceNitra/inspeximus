"""The prompt block does not re-suggest a destructive command it captured (audit A-14).

The Claude Code hook captures every Bash command as `ran: <cmd>` and prints the best matches under
"recent mechanics", beneath a header that says "deterministic, corrections already applied". A kill
by image name, a recursive delete or a discarded git history came back that way to every agent that
asked a nearby question. The record stays in the store as a true account of what ran; what stops is
the replay. Each test proves recall still reaches the record (a control), so only the filter can pass it.
"""
import io
import os
import subprocess
import sys
from contextlib import redirect_stdout

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import inspeximus.claude_code as cc
from inspeximus import _surface

DESTRUCTIVE = [
    "taskkill /F /IM python.exe",
    "Stop-Process -Name python -Force",
    "pkill -9 -f python",
    "git reset --hard origin/main && git clean -fdx",
    "rm -rf ~/projects/research",
]


@pytest.fixture
def project(tmp_path, monkeypatch):
    for k in list(os.environ):
        if k.startswith("INSPEXIMUS_") and k != "INSPEXIMUS_NO_UPDATE_CHECK":
            monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("INSPEXIMUS_NO_NUDGE", "1")
    proj = str(tmp_path / "proj")
    subprocess.run(["git", "init", "-q", proj], check=True)
    return proj


def _run(fn, ev):
    buf = io.StringIO()
    with redirect_stdout(buf):
        fn(ev)
    return buf.getvalue()


def _prompt(proj, text="what is the weather"):
    return _run(cc.recall, {"cwd": proj, "session_id": "s", "hook_event_name": "UserPromptSubmit",
                            "prompt": text})


def _store(proj):
    return _surface.open_store(_surface.coding_store_path(proj))



@pytest.mark.parametrize("command", DESTRUCTIVE)
def test_a_captured_destructive_command_is_not_replayed_into_the_prompt(project, command):
    cc.capture({"cwd": project, "session_id": "s1", "hook_event_name": "PostToolUse", "tool_name": "Bash",
                "tool_input": {"command": command}})
    query = "how do I stop the " + command.split()[-1] + " processes and clean up"
    # The control is the hook's OWN query on the store: the record is kept and recall ranks it, so
    # only the replay filter stands between it and the prompt.
    if not any(command in (h.get("text") or "") for h in _store(project).recall(query, k=16)):
        pytest.fail("control: recall does not reach the captured command, so the replay was not exercised")
    out = _prompt(project, query)
    assert command not in out, f"the hook re-suggested {command!r} under its trusted header"


def test_an_ordinary_command_is_still_replayed(project):
    command = "python -m pytest tests/test_x.py -q"
    cc.capture({"cwd": project, "session_id": "s1", "hook_event_name": "PostToolUse", "tool_name": "Bash",
                "tool_input": {"command": command}})
    assert command in _prompt(project, "how do I run the pytest tests for test_x")


# One row per `_NO_REPLAY` shape, each with a near miss that must still replay. A shape with no row
# here is a shape a mutation can delete unnoticed.
@pytest.mark.parametrize("hit,miss", [
    ("taskkill /F /IM python.exe", "taskkill /PID 4242 /F"),
    ("Stop-Process -Name python -Force", "Stop-Process -Id 4242 -Force"),
    ("Get-Process python | Stop-Process", "Get-Process python | Select-Object Id"),
    ("killall node", "kill 4242"),
    ("rm -fr build/", "rm build/app.log"),
    ("Remove-Item C:/work/out -Recurse -Force", "Remove-Item C:/work/out.txt"),
    ("rmdir /s /q build", "rmdir build"),
    ("git reset --hard HEAD~3", "git reset --soft HEAD~1"),
    ("git clean -f -d", "git clean -n"),
    ("git push origin +main", "git push origin HEAD:main"),
    ("git checkout -- .", "git checkout -b feature"),
    ("git branch -D old-work", "git branch -d merged-work"),
    ("git stash drop", "git stash pop"),
    ("git worktree remove ../wt-other", "git worktree list"),
    ("sqlite3 m.db 'DROP TABLE records'", "sqlite3 m.db 'SELECT drop_ts FROM records'"),
    ("format D: /q", "python -c 'print(format(3))'"),
    ("shutdown /r /t 0", "python shutdown_report.py --dry"),
])
def test_every_no_replay_shape_fires_and_its_near_miss_does_not(hit, miss):
    rec = lambda cmd: {"text": "ran: " + cmd, "tags": ["bash"]}
    assert cc._not_for_replay(rec(hit)), hit
    assert not cc._not_for_replay(rec(miss)), miss


def test_only_a_captured_command_is_judged_not_a_file_that_mentions_one():
    assert not cc._not_for_replay({"text": "deploy.sh :: current state -> rm -rf build", "tags": ["file"]})
