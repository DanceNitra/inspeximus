"""A decision made through the MCP server must reach the next session's SessionStart hook.

README: "From the next session on, your agent starts knowing what the last one decided." Measured on a
clean install of 3.9.5 (fresh venv, sandboxed HOME), none of it held:

1. The plugin's `.mcp.json` pointed the server at `.inspeximus/memory.json` while the hook read
   `.inspeximus/coding_memory.json`, and `${CLAUDE_PROJECT_DIR}` is the LAUNCH directory (Claude Code
   2.1.282) while the hook walks up to the git root. Two stores; SessionStart printed nothing.
2. With one store, the session window still dropped the decision: a single stamped hook capture made
   the window "stamped rows only", and the MCP server never knows the host's session id.
3. A session that ended without SessionEnd (a closed terminal) never got a digest at all.
4. `inspeximus install --ide claude` wrote no hooks, wrote a bare "uvx" when uv was missing, and
   `recall("what did we decide about indentation?")` returned [] for topic="indentation".

The two-process tests read the SHIPPED `.mcp.json`, so they fail again if the plugin drifts.
"""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from inspeximus import Inspeximus
from inspeximus import install as I

ROOT = Path(__file__).resolve().parents[1]


# ── the two-process loop, exactly as the plugin wires it ────────────────────────────────────────────
def _env(launch):
    env = dict(os.environ, PYTHONPATH=str(ROOT), CLAUDE_PROJECT_DIR=str(launch),
               INSPEXIMUS_NO_UPDATE_CHECK="1", PYTHONIOENCODING="utf-8")
    for k in ("INSPEXIMUS_PATH", "INSPEXIMUS_CODING_STORE", "INSPEXIMUS_SCOPE", "INSPEXIMUS_NO_INJECT",
              "INSPEXIMUS_SESSION_DIGEST"):
        env.pop(k, None)
    return env


def _hook(launch, ev):
    ev = dict(ev, cwd=str(launch))
    p = subprocess.run([sys.executable, "-m", "inspeximus.claude_code"], input=json.dumps(ev),
                       capture_output=True, text=True, encoding="utf-8", errors="replace",
                       cwd=str(launch), env=_env(launch), timeout=120)
    return p.stdout


def _mcp_remember_decision(launch):
    """Process 1: the MCP server with the env the shipped plugin gives it, driven over stdio."""
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    shipped = json.loads((ROOT / ".mcp.json").read_text(encoding="utf-8"))["mcpServers"]["inspeximus"]
    env = _env(launch)
    env.update({k: v.replace("${CLAUDE_PROJECT_DIR}", str(launch))
                for k, v in (shipped.get("env") or {}).items()})

    async def run():
        params = StdioServerParameters(command=sys.executable, args=["-m", "inspeximus.mcp_server"],
                                       env=env, cwd=str(launch))
        async with stdio_client(params) as (r, w):
            async with ClientSession(r, w) as s:
                await s.initialize()
                await s.call_tool("remember_decision", {
                    "decision": "Use tabs, not spaces, in every Python file.",
                    "because": "the linter config expects tabs", "topic": "indentation"})
    asyncio.run(run())


@pytest.mark.parametrize("session_one", ["no hooks", "hooks with a tool capture", "no SessionEnd"])
def test_an_mcp_decision_is_printed_by_the_next_sessionstart(tmp_path, session_one):
    pytest.importorskip("mcp")
    (tmp_path / ".git").mkdir()
    launch = tmp_path / "sub"                       # claude started in a subdirectory of the repo
    launch.mkdir()
    if session_one != "no hooks":
        _hook(launch, {"hook_event_name": "SessionStart", "session_id": "s1", "source": "startup"})
    _mcp_remember_decision(launch)
    if session_one != "no hooks":
        _hook(launch, {"hook_event_name": "PostToolUse", "session_id": "s1", "tool_name": "Write",
                       "tool_input": {"file_path": "a.py", "content": "x = 1"}, "tool_response": {}})
    if session_one == "hooks with a tool capture":
        _hook(launch, {"hook_event_name": "SessionEnd", "session_id": "s1", "reason": "exit"})
    out = _hook(launch, {"hook_event_name": "SessionStart", "session_id": "s2", "source": "startup"})
    assert "Use tabs, not spaces" in out, (
        "session 2's SessionStart did not carry the decision session 1 made through MCP "
        "(%s); it printed: %r" % (session_one, out[:400]))
    stores = sorted(str(p.relative_to(tmp_path)) for p in tmp_path.rglob("*.json")
                    if p.parent.name == ".inspeximus" and p.name.endswith("memory.json"))
    assert stores == [os.path.join(".inspeximus", "coding_memory.json")], stores


def test_the_shipped_plugin_points_the_server_at_the_hook_store(tmp_path):
    """The cheap half of the test above, with no MCP SDK: resolve the shipped env the way the server does."""
    from inspeximus._surface import resolve_path
    from inspeximus.claude_code import _store_dir
    (tmp_path / ".git").mkdir()
    launch = tmp_path / "a" / "b"
    launch.mkdir(parents=True)
    shipped = json.loads((ROOT / ".mcp.json").read_text(encoding="utf-8"))["mcpServers"]["inspeximus"]
    env = {k: v.replace("${CLAUDE_PROJECT_DIR}", str(launch)) for k, v in (shipped.get("env") or {}).items()}
    server = resolve_path(env=env, cwd=str(launch))
    hook = os.path.join(_store_dir(str(launch)), "coding_memory.json")
    assert os.path.normcase(os.path.abspath(server)) == os.path.normcase(os.path.abspath(hook))


def test_the_claude_code_scope_honours_the_hook_override(tmp_path):
    from inspeximus._surface import resolve_path
    env = {"INSPEXIMUS_SCOPE": "claude-code", "INSPEXIMUS_CODING_STORE": str(tmp_path / "x")}
    assert resolve_path(env=env, cwd=str(tmp_path)) == os.path.join(str(tmp_path / "x"), "coding_memory.json")


def test_a_3_9_5_plugin_store_is_named_until_it_is_merged(tmp_path, monkeypatch):
    """Moving the server alone would leave 3.9.5's MCP decisions where no session reads them."""
    from inspeximus import claude_code as cc
    (tmp_path / ".git").mkdir()
    old = Inspeximus(path=str(tmp_path / ".inspeximus" / "memory.json"))
    old.remember_decision("Deploy on Fridays is banned.", topic="deploy")
    old.flush()
    monkeypatch.chdir(tmp_path)
    for k in ("INSPEXIMUS_PATH", "INSPEXIMUS_CODING_STORE", "INSPEXIMUS_NO_INJECT"):
        monkeypatch.delenv(k, raising=False)
    first = _hook(tmp_path, {"hook_event_name": "SessionStart", "session_id": "s1", "source": "startup"})
    assert "memory.json holds 1 memories" in first and "--merge-store" in first, first
    src = tmp_path / ".inspeximus" / "memory.json"
    before = src.read_bytes()
    assert cc.merge_store(str(src))["applied"] is False                     # dry by default
    r = cc.merge_store(str(src), apply=True)
    assert r["applied"] and r["new"] == 1 and r["added"] == 1
    assert src.read_bytes() == before                                       # the source is never touched
    m = Inspeximus(path=str(tmp_path / ".inspeximus" / "coding_memory.json"))
    assert m.recall("deploy")
    again = _hook(tmp_path, {"hook_event_name": "SessionStart", "session_id": "s2", "source": "startup"})
    assert "--merge-store" not in again, again


# ── the session window ──────────────────────────────────────────────────────────────────────────────
def test_an_unstamped_decision_inside_the_session_is_in_its_digest():
    m = Inspeximus()
    m.remember_decision("before the session opened", topic="early")
    m.open_session("s1")
    m.remember("edited a.py", key="file::a.py", session_id="s1")          # a stamped hook capture
    m.remember_decision("use tabs", topic="indentation")                  # the MCP server: no stamp
    m.remember_decision("from another session", topic="b", session_id="other")
    rep = m.close_session("s1")
    assert rep["mode"] == "sid"
    assert "use tabs" in rep["text"]
    assert "another session" not in rep["text"]
    assert "before the session opened" not in rep["text"]


def test_a_session_that_never_closed_is_closed_when_the_next_one_opens():
    m = Inspeximus()
    m.open_session("s1")
    m.remember_decision("use tabs", topic="indentation")
    m.open_session("s2")                                                   # no close_session("s1")
    assert "use tabs" in m.session_context()["text"]


def test_writes_made_with_no_session_open_reach_the_first_session():
    m = Inspeximus()
    m.remember_decision("use tabs", topic="indentation")
    m.open_session("s1")
    assert "use tabs" in m.session_context()["text"]


def test_an_empty_unclosed_session_writes_no_digest():
    m = Inspeximus()
    m.open_session("s1")
    m.remember("pytest -q", tags=["bash"])
    m.open_session("s2")
    assert not [r for r in m.items if r.get("key") == Inspeximus.SESSION_DIGEST_KEY]


# ── the topic is searchable ─────────────────────────────────────────────────────────────────────────
def test_recall_finds_a_decision_by_its_topic_and_its_context():
    m = Inspeximus()
    mid = m.remember_decision("Use tabs, not spaces.", because="the linter expects tabs",
                              topic="indentation")
    style = m.remember_decision("Black is the formatter.", topic="code-style",
                                context="chosen during the CI migration")
    m.remember("the parking lot opens at nine")
    assert mid in [h["id"] for h in m.recall("what did we decide about indentation?")]
    assert style in [h["id"] for h in m.recall("code style")]
    assert style in [h["id"] for h in m.recall("CI migration")]
    rec = next(r for r in m.items if r["id"] == mid)
    assert rec["text"] == "DECISION: Use tabs, not spaces. — because: the linter expects tabs"


def test_a_plain_keyed_record_does_not_gain_its_key_as_text():
    m = Inspeximus()
    m.remember("the retry budget is five", key="cfg::indentation")
    assert m.recall("indentation") == []


# ── the installer ───────────────────────────────────────────────────────────────────────────────────
@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setattr(I, "_home", lambda: tmp_path)
    return tmp_path


def test_install_claude_writes_the_hooks_the_plugin_ships(home, monkeypatch):
    uvx = str(home / "bin" / "uvx.exe")
    monkeypatch.setattr(I.shutil, "which", lambda name: uvx if name == "uvx" else None)
    ok, msg = I.apply(I.plan("claude"))
    assert ok, msg
    entry = json.loads((home / ".claude.json").read_text(encoding="utf-8"))["mcpServers"]["inspeximus"]
    assert entry["env"] == {"INSPEXIMUS_SCOPE": "claude-code"}
    hooks = json.loads((home / ".claude" / "settings.json").read_text(encoding="utf-8"))["hooks"]
    plugin = json.loads((ROOT / "hooks" / "hooks.json").read_text(encoding="utf-8"))["hooks"]
    assert list(hooks) == list(plugin)
    for evt, entries in hooks.items():
        (h,) = entries[0]["hooks"]
        assert h["command"].endswith("--from inspeximus python -m inspeximus.claude_code"), h
        assert "\\" not in h["command"]
        assert entries[0].get("matcher") == plugin[evt][0].get("matcher")
        assert h.get("timeout") == plugin[evt][0]["hooks"][0].get("timeout")
    second = I.plan("claude")
    assert second["action"] == "unchanged" and second["hooks"]["action"] == "unchanged"
    assert "unchanged" in I.apply(second)[1]


def test_install_keeps_a_users_own_inspeximus_hook(home, monkeypatch):
    monkeypatch.setattr(I.shutil, "which", lambda name: "/usr/bin/uvx" if name == "uvx" else None)
    settings = home / ".claude" / "settings.json"
    settings.parent.mkdir()
    mine = {"hooks": [{"type": "command", "command": "/opt/py/bin/python -m inspeximus.claude_code"}]}
    settings.write_text(json.dumps({"model": "x", "hooks": {"SessionStart": [mine]}}), encoding="utf-8")
    I.apply(I.plan("claude"))
    data = json.loads(settings.read_text(encoding="utf-8"))
    assert data["model"] == "x"
    assert data["hooks"]["SessionStart"] == [mine]
    assert set(data["hooks"]) == {"PreToolUse", "PostToolUse", "UserPromptSubmit", "SessionStart", "SessionEnd"}


def test_install_without_uvx_uses_this_python(home, monkeypatch):
    monkeypatch.setattr(I.shutil, "which", lambda name: None)
    monkeypatch.setattr(I, "_mcp_importable", lambda: True)
    p = I.plan("claude")
    assert p["block"]["command"] == sys.executable
    assert p["block"]["args"] == ["-m", "inspeximus.mcp_server"]
    cmd = p["hooks"]["data"]["hooks"]["SessionStart"][0]["hooks"][0]["command"]
    assert cmd.endswith(" -m inspeximus.claude_code") and "uvx" not in cmd


def test_install_without_uvx_or_the_mcp_extra_refuses_and_writes_nothing(home, monkeypatch):
    monkeypatch.setattr(I.shutil, "which", lambda name: None)
    monkeypatch.setattr(I, "_mcp_importable", lambda: False)
    p = I.plan("claude")
    assert p["error"] and "uv" in p["error"] and 'pip install "inspeximus[mcp]"' in p["error"]
    ok, _ = I.apply(p)
    assert not ok
    assert not (home / ".claude.json").exists() and not (home / ".claude").exists()
