"""Every hook run puts exactly one JSON object on stdout, or nothing, for every event and message (A-28).

Codex parses hook stdout with serde_json::from_str, which rejects trailing characters. Output that
starts with `{` and does not parse marks the hook Failed and injects nothing, so a recall block and
four lines of text after it lost the recall block (openai/codex, hooks/src/events/user_prompt_submit.rs
and engine/output_parser.rs, read 2026-09-28). test_a_hook_writes_one_json_object_and_nothing_else.py
covered the envelope, but never on the prompt that carried the one-time star ask, which is where the
text was printed. So this runs every event through `main()`, the dispatch the host calls, in each state
that adds a message, for both hosts, and parses stdout the way Codex does: json.loads rejects trailing
characters just as from_str does.
"""
from __future__ import annotations

import io
import json
import os
import sys
import tempfile

import pytest

from inspeximus import claude_code as cc

HOSTS = {"claude-code": "/home/u/.claude/projects/p/s.jsonl", "codex": "/home/u/.codex/sessions/s.jsonl"}


def _strict(out: str):
    """None for silence, else the one object; fails on anything a strict parser would refuse."""
    if not out.strip():
        return None
    try:
        obj = json.loads(out)
    except json.JSONDecodeError as e:
        raise AssertionError(f"stdout is not one JSON object ({e}); a strict parser drops it all:\n{out}") from None
    assert isinstance(obj, dict), f"stdout must be an object, got {type(obj).__name__}"
    return obj


@pytest.fixture()
def project(monkeypatch):
    d = tempfile.mkdtemp()
    monkeypatch.chdir(d)
    monkeypatch.delenv("INSPEXIMUS_NO_NUDGE", raising=False)
    monkeypatch.delenv("INSPEXIMUS_AGENT_ID", raising=False)
    return d


def _run(monkeypatch, capsys, ev):
    """One hook process's worth of work, through main(), with this event on stdin."""
    capsys.readouterr()
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(ev)))
    monkeypatch.setattr(sys, "argv", ["inspeximus.claude_code"])
    cc.main()
    return capsys.readouterr().out


def _seed(project, monkeypatch, capsys, host):
    _run(monkeypatch, capsys, {"hook_event_name": "PostToolUse", "tool_name": "Write", "cwd": project,
                               "transcript_path": HOSTS[host], "session_id": "s1",
                               "tool_input": {"file_path": "src/payments.py", "content": "def charge(): ..."}})


def _ask_due(project):
    with open(cc._nudge_path(project), "w", encoding="utf-8") as fh:
        json.dump({"writes": cc._NUDGE_AFTER, "shown": False}, fh)


def _events(project, host):
    tp = HOSTS[host]
    return {
        "SessionStart": {"hook_event_name": "SessionStart", "source": "startup", "cwd": project,
                         "transcript_path": tp, "session_id": "s2"},
        "UserPromptSubmit": {"hook_event_name": "UserPromptSubmit", "prompt": "what is in payments.py",
                             "cwd": project, "transcript_path": tp, "session_id": "s2"},
        "PreToolUse": {"hook_event_name": "PreToolUse", "tool_name": "Bash", "cwd": project,
                       "transcript_path": tp, "session_id": "s2", "tool_input": {"command": "ls"}},
        "PostToolUse": {"hook_event_name": "PostToolUse", "tool_name": "Edit", "cwd": project,
                        "transcript_path": tp, "session_id": "s2",
                        "tool_input": {"file_path": "src/payments.py", "old_string": "a", "new_string": "b"}},
        "SessionEnd": {"hook_event_name": "SessionEnd", "reason": "exit", "cwd": project,
                       "transcript_path": tp, "session_id": "s2"},
    }


@pytest.mark.parametrize("host", sorted(HOSTS))
@pytest.mark.parametrize("event", ["SessionStart", "UserPromptSubmit", "PreToolUse", "PostToolUse", "SessionEnd"])
@pytest.mark.parametrize("state", ["fresh", "with memory", "star ask due", "memory active due"])
def test_every_event_in_every_state_prints_one_object_or_nothing(project, monkeypatch, capsys, host, event, state):
    if state != "fresh":
        _seed(project, monkeypatch, capsys, host)
    if state == "star ask due":
        _ask_due(project)
    if state == "memory active due":
        import inspeximus._surface as surface
        monkeypatch.setattr(surface, "announcement", lambda *a, **k: "[inspeximus] memory active for this agent.")
        monkeypatch.setattr(surface, "mark_announced", lambda *a, **k: None)
    _strict(_run(monkeypatch, capsys, _events(project, host)[event]))


@pytest.mark.parametrize("host", sorted(HOSTS))
@pytest.mark.parametrize("recalled", [True, False], ids=["with recall", "nothing to recall"])
def test_the_star_ask_reaches_the_user_and_leaves_the_recall_block_intact(project, monkeypatch, capsys, host,
                                                                         recalled):
    _seed(project, monkeypatch, capsys, host)
    _ask_due(project)
    ev = _events(project, host)["UserPromptSubmit"]
    if not recalled:
        ev = dict(ev, prompt="zzqx unrelated words")
    obj = _strict(_run(monkeypatch, capsys, ev))
    assert obj is not None and "A small ask" in obj.get("systemMessage", ""), obj
    context = (obj.get("hookSpecificOutput") or {}).get("additionalContext", "")
    assert "A small ask" not in context, "the ask is for the user, not the model's context"
    if recalled:
        assert "payments.py" in context, obj
    assert json.load(open(cc._nudge_path(project), encoding="utf-8"))["shown"] is True


def test_control_the_strict_parser_refuses_the_shape_that_lost_the_recall_block():
    """CONTROL: the parse above can fail, on exactly the output 3.15.1 printed."""
    old = json.dumps({"hookSpecificOutput": {"hookEventName": "UserPromptSubmit", "additionalContext": "x"}})
    with pytest.raises(AssertionError, match="not one JSON object"):
        _strict(old + "\n\n[inspeximus] A small ask: ...\n")
    with pytest.raises(AssertionError, match="not one JSON object"):
        _strict(old + "\n" + old)
