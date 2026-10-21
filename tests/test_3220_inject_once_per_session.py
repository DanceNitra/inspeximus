"""The prompt hook gives a session each memory line once (3.22, inspeximus/_seen.py).

Measured before the change (AUDIT-B, 3,666 transcripts): 92.7 % of the 49.7 MB the UserPromptSubmit hook injected
repeated a line the same session had already been given. These tests pin the four rules: a repeat is dropped, a
compaction resets, a changed line is injected again, and two sessions never share a set. The off switch and the
fail-open path are pinned too.
"""
from __future__ import annotations

import json

import pytest

from inspeximus import Inspeximus, _seen
from inspeximus import claude_code as cc


@pytest.fixture
def project(tmp_path, monkeypatch):
    home = tmp_path / "keyhome"
    home.mkdir()
    monkeypatch.setenv("INSPEXIMUS_KEY_HOME", str(home))
    for k in ("INSPEXIMUS_NO_INJECT", "INSPEXIMUS_HOOK_DEDUPE", "INSPEXIMUS_PATH", "INSPEXIMUS_CODING_STORE",
              "INSPEXIMUS_DECISION_STORE", "INSPEXIMUS_SCOPE"):
        monkeypatch.delenv(k, raising=False)
    proj = tmp_path / "proj"
    (proj / ".inspeximus").mkdir(parents=True)
    m = Inspeximus(str(proj / ".inspeximus" / "coding_memory.json"))
    m.remember_decision("Release only through tools/release.py", because="the gate stops before PyPI",
                        topic="release")
    m.flush()
    return proj


def _prompt(proj, sid, capsys, prompt="how do we release?"):
    """One UserPromptSubmit through the hook's own entry point, as Claude Code runs it."""
    import io
    import sys
    ev = {"hook_event_name": "UserPromptSubmit", "cwd": str(proj), "session_id": sid, "prompt": prompt}
    old = sys.stdin, sys.argv
    sys.stdin, sys.argv = io.StringIO(json.dumps(ev)), ["inspeximus.claude_code"]
    try:
        cc.main()
    finally:
        sys.stdin, sys.argv = old
    out = capsys.readouterr().out.strip()
    return json.loads(out)["hookSpecificOutput"]["additionalContext"] if out else ""


def test_recall_itself_is_unchanged_so_the_daemon_serves_todays_bytes(project, capsys):
    # The filter sits where the block leaves the hook; recall() prints the full block every time.
    for _ in range(2):
        cc.recall({"cwd": str(project), "session_id": "s1", "prompt": "how do we release?"})
        assert "tools/release.py" in capsys.readouterr().out


def test_an_envelope_with_nothing_seen_before_is_returned_byte_for_byte(tmp_path, monkeypatch):
    monkeypatch.setenv("INSPEXIMUS_KEY_HOME", str(tmp_path))
    monkeypatch.delenv("INSPEXIMUS_HOOK_DEDUPE", raising=False)
    # Formatted unlike json.dumps' default, so a rebuild would show: the bytes that came in are the bytes that go out.
    env = json.dumps({"hookSpecificOutput": {"hookEventName": "UserPromptSubmit", "additionalContext":
                      cc._MEMORY_HEADER + "\ndecisions/rules (what we concluded, and why):\n  * x"}}, indent=1) + "\n"
    assert cc._once_envelope(env, "s") == env
    assert cc._once_envelope(env, "s") == ""                # the second time nothing is left to give


def test_a_notice_beside_the_block_is_kept_when_the_block_empties(tmp_path, monkeypatch):
    monkeypatch.setenv("INSPEXIMUS_KEY_HOME", str(tmp_path))
    monkeypatch.delenv("INSPEXIMUS_HOOK_DEDUPE", raising=False)
    ctx = cc._MEMORY_HEADER + "\ncurated knowledge (from memory):\n  = k\n[inspeximus] 2 stored record(s) look like"
    env = json.dumps({"hookSpecificOutput": {"hookEventName": "UserPromptSubmit", "additionalContext": ctx},
                      "systemMessage": "ask"}) + "\n"
    cc._once_envelope(env, "s")
    obj = json.loads(cc._once_envelope(env, "s"))
    assert obj["systemMessage"] == "ask"
    assert obj["hookSpecificOutput"]["additionalContext"] == "[inspeximus] 2 stored record(s) look like"


def test_a_line_is_injected_once_per_session(project, capsys):
    first = _prompt(project, "s1", capsys)
    assert "tools/release.py" in first
    assert "tools/release.py" not in _prompt(project, "s1", capsys)


def test_two_sessions_do_not_share_a_set(project, capsys):
    assert "tools/release.py" in _prompt(project, "s1", capsys)
    assert "tools/release.py" in _prompt(project, "s2", capsys)


def test_a_compaction_injects_again(project, capsys):
    assert "tools/release.py" in _prompt(project, "s1", capsys)
    cc.session_start({"cwd": str(project), "session_id": "s1", "source": "compact"})
    capsys.readouterr()
    assert "tools/release.py" in _prompt(project, "s1", capsys)


def test_a_superseded_decision_is_injected_again_with_its_new_value(project, capsys):
    assert "tools/release.py" in _prompt(project, "s1", capsys)
    m = Inspeximus(str(project / ".inspeximus" / "coding_memory.json"))
    m.remember_decision("Release only through tools/release2.py", because="the new gate", topic="release")
    m.flush()
    assert "tools/release2.py" in _prompt(project, "s1", capsys)


def test_the_off_switch_injects_every_time(project, capsys, monkeypatch):
    monkeypatch.setenv("INSPEXIMUS_HOOK_DEDUPE", "0")
    assert "tools/release.py" in _prompt(project, "s1", capsys)
    assert "tools/release.py" in _prompt(project, "s1", capsys)


def test_without_a_session_id_nothing_is_withheld(project, capsys):
    assert "tools/release.py" in _prompt(project, "", capsys)
    assert "tools/release.py" in _prompt(project, "", capsys)


def test_an_unreadable_state_file_fails_open(tmp_path, monkeypatch):
    monkeypatch.setenv("INSPEXIMUS_KEY_HOME", str(tmp_path))
    monkeypatch.delenv("INSPEXIMUS_HOOK_DEDUPE", raising=False)
    assert _seen.once("s", ["a"]) == ["a"]
    with open(_seen._path("s"), "w", encoding="utf-8") as fh:
        fh.write("{not json")
    assert _seen.once("s", ["a"]) == ["a"]


def test_a_state_directory_that_cannot_be_written_fails_open(tmp_path, monkeypatch):
    # The directory the sets live in is a FILE, so the write raises: every line must still be returned, every time.
    monkeypatch.setenv("INSPEXIMUS_KEY_HOME", str(tmp_path))
    monkeypatch.delenv("INSPEXIMUS_HOOK_DEDUPE", raising=False)
    (tmp_path / "inspeximus").mkdir()
    (tmp_path / "inspeximus" / "seen").write_text("not a directory", encoding="utf-8")
    assert _seen.once("s", ["a", "b"]) == ["a", "b"]
    assert _seen.once("s", ["a", "b"]) == ["a", "b"]


def test_whitespace_does_not_make_a_line_new_and_a_repeat_inside_one_call_is_kept_once(tmp_path, monkeypatch):
    monkeypatch.setenv("INSPEXIMUS_KEY_HOME", str(tmp_path))
    monkeypatch.delenv("INSPEXIMUS_HOOK_DEDUPE", raising=False)
    assert _seen.once("s", ["a  b", "a b", "c"]) == ["a  b", "c"]
    assert _seen.once("s", ["a\tb", "d"]) == ["d"]


def test_the_state_lives_under_the_key_home_and_the_session_id_cannot_name_a_path(tmp_path, monkeypatch):
    monkeypatch.setenv("INSPEXIMUS_KEY_HOME", str(tmp_path))
    p = _seen._path("../../etc/passwd")
    assert p.startswith(str(tmp_path)) and ".." not in p[len(str(tmp_path)):]


def test_an_old_session_set_expires(tmp_path, monkeypatch):
    import os
    monkeypatch.setenv("INSPEXIMUS_KEY_HOME", str(tmp_path))
    monkeypatch.delenv("INSPEXIMUS_HOOK_DEDUPE", raising=False)
    _seen.once("old", ["x"])
    old = _seen._path("old")
    t = os.path.getmtime(old) - _seen.EXPIRE_S - 60
    os.utime(old, (t, t))
    _seen.once("new", ["y"])
    assert not os.path.exists(old) and os.path.exists(_seen._path("new"))

def test_a_recall_that_fails_partway_still_shows_what_it_printed(project, capsys, monkeypatch):
    # Before 3.22 the hook printed straight to stdout, so a recall that raised left its partial output there.
    def partial(ev):
        print("[inspeximus] partial block")
        raise RuntimeError("recall broke")
    monkeypatch.setattr(cc, "recall", partial)
    import io
    import sys
    ev = {"hook_event_name": "UserPromptSubmit", "cwd": str(project), "session_id": "s1", "prompt": "x"}
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(ev)))
    monkeypatch.setattr(sys, "argv", ["inspeximus.claude_code"])
    try:
        cc.main()
    except (RuntimeError, SystemExit):
        pass
    assert "[inspeximus] partial block" in capsys.readouterr().out

def test_only_a_sessions_first_write_lists_the_directory(tmp_path, monkeypatch):
    # perf/gate.py's prompt_hook_n1200 counts directory listings: expiry runs once per session, not on every prompt.
    import os
    monkeypatch.setenv("INSPEXIMUS_KEY_HOME", str(tmp_path))
    monkeypatch.delenv("INSPEXIMUS_HOOK_DEDUPE", raising=False)
    calls = []
    real = os.listdir
    monkeypatch.setattr(_seen.os, "listdir", lambda d: calls.append(d) or real(d))
    _seen.once("s", ["a"])
    assert len(calls) == 1
    _seen.once("s", ["b"])
    _seen.once("s", ["c"])
    assert len(calls) == 1


def test_a_decision_reverted_to_its_earlier_value_is_injected_again(project, capsys):
    # AUDIT-A 3.22 F-2: the set was keyed on text alone. A shown, then B, then a revert back to A: the session's
    # last view was B, the retired value, and A was withheld as "seen".
    assert "tools/release.py" in _prompt(project, "s1", capsys)
    m = Inspeximus(str(project / ".inspeximus" / "coding_memory.json"))
    m.remember_decision("Release only through tools/release2.py", because="the new gate", topic="release")
    m.flush()
    assert "tools/release2.py" in _prompt(project, "s1", capsys)
    m = Inspeximus(str(project / ".inspeximus" / "coding_memory.json"))
    assert m.revert("decision::release").get("ok") is not False
    m.flush()
    back = _prompt(project, "s1", capsys)
    assert "tools/release.py" in back and "release2" not in back, back
    assert "tools/release.py" not in _prompt(project, "s1", capsys)          # control: unchanged, withheld again


def test_a_keyed_line_is_withheld_only_while_it_is_the_last_shown_for_its_key(tmp_path, monkeypatch):
    monkeypatch.setenv("INSPEXIMUS_KEY_HOME", str(tmp_path))
    monkeypatch.delenv("INSPEXIMUS_HOOK_DEDUPE", raising=False)
    a, b, s = "  * DECISION: A", "  * DECISION: B", {"  * DECISION: A": "decision::x", "  * DECISION: B": "decision::x"}
    assert _seen.once("s", [a], s) == [a]
    assert _seen.once("s", [a], s) == []
    assert _seen.once("s", [b], s) == [b]
    assert _seen.once("s", [a], s) == [a]
    assert _seen.once("s", [a], s) == []
    assert _seen.once("t", [a]) == [a] and _seen.once("t", [b]) == [b] and _seen.once("t", [a]) == []  # unkeyed


def _pre_tool(proj, ev_extra, capsys):
    ev = {"hook_event_name": "PreToolUse", "cwd": str(proj), "session_id": "s1", "tool_name": "Write",
          "tool_input": {"file_path": str(proj / "new_release_notes.py")}, **ev_extra}
    cc.pre_tool_use(ev)
    out = capsys.readouterr().out.strip()
    return json.loads(out)["hookSpecificOutput"]["additionalContext"] if out else ""


def test_a_subagent_gets_the_decisions_its_parent_was_shown(project, capsys):
    # AUDIT-A 3.22 F-3: a subagent's PreToolUse carries the parent's session_id and starts with a fresh context.
    first = _pre_tool(project, {}, capsys)
    assert "decided before" in first and "tools/release.py" in first, first
    assert "decided before" not in _pre_tool(project, {}, capsys)              # control: the parent's repeat
    sub = _pre_tool(project, {"agent_id": "agent-7"}, capsys)
    assert "decided before" in sub and "tools/release.py" in sub, sub
    assert "decided before" not in _pre_tool(project, {"agent_id": "agent-7"}, capsys)
    assert "decided before" in _pre_tool(project, {"agent_id": "agent-8"}, capsys)
