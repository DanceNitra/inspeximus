"""The Claude Code plugin must deliver the same memory the CLI installer does.

Measured 2026-09-04. `.claude-plugin/plugin.json` existed since 2.25.0 and registered only an MCP
server, inline, under a key the plugin docs never name. A user who ran `/plugin marketplace add`
got the 73 tools and none of the lifecycle hooks, so nothing was captured automatically and the
cross-session digest never appeared. The hooks lived only in the path written by
`python -m inspeximus.claude_code --install`. claude-mem, 93k stars, is the same product shape
with the hooks wired; the difference a marketplace user saw was that theirs remembered and ours
did not.

The plugin loader reads `hooks/hooks.json` at the plugin root and `.mcp.json` at the plugin root.
These tests pin both, and pin them to the installer, so the two paths cannot drift apart again.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
HOOKS = ROOT / "hooks" / "hooks.json"
MCP = ROOT / ".mcp.json"
PLUGIN = ROOT / ".claude-plugin" / "plugin.json"

EVENTS = ("PreToolUse", "PostToolUse", "UserPromptSubmit", "SessionStart", "SessionEnd")


def _load(p):
    assert p.exists(), "%s is missing; the marketplace install would ship without it" % p.relative_to(ROOT)
    return json.loads(p.read_text(encoding="utf-8"))


def test_the_plugin_wires_every_event_the_installer_wires():
    hooks = _load(HOOKS)["hooks"]
    assert tuple(hooks) == EVENTS, (
        "hooks/hooks.json has %s; the installer writes %s. A marketplace user must get the same "
        "memory as a pip user." % (tuple(hooks), EVENTS))


def test_every_hook_runs_the_module_through_uvx_so_pip_is_not_assumed():
    hooks = _load(HOOKS)["hooks"]
    for ev, entries in hooks.items():
        for e in entries:
            for h in e["hooks"]:
                assert h["type"] == "command", ev
                assert h["command"].startswith("uvx --from inspeximus "), (
                    "%s runs %r. A plugin cannot assume `pip install inspeximus` happened; uvx "
                    "resolves the package itself, the way the MCP entry already does." % (ev, h["command"]))
                assert h["command"].endswith("python -m inspeximus.claude_code"), ev


def test_the_pre_tool_matcher_matches_the_installer_exactly():
    """The installer derives its matcher from `_PRE_TOOLS`; the plugin must carry the same scope,
    or one path pays a process launch on every Read and Grep while the other does not."""
    from inspeximus import claude_code as cc
    hooks = _load(HOOKS)["hooks"]
    expected = "|".join(cc._PRE_TOOLS)
    assert hooks["PreToolUse"][0]["matcher"] == expected


def test_session_end_carries_the_timeout_the_installer_asks_for():
    """SessionEnd shares a 1.5 s budget by default; a hook killed mid-write writes nothing."""
    hooks = _load(HOOKS)["hooks"]
    h = hooks["SessionEnd"][0]["hooks"][0]
    assert h.get("timeout") == 15


def test_the_mcp_server_lives_in_mcp_json_not_inline():
    """The plugin docs list `.mcp.json` at the plugin root as where MCP servers go and never name an
    inline `mcpServers` key in plugin.json. Registering it in the undocumented place and not the
    documented one is how a component quietly fails to load."""
    mcp = _load(MCP)["mcpServers"]
    assert "inspeximus" in mcp
    assert mcp["inspeximus"]["command"] == "uvx"
    assert "inspeximus-mcp" in mcp["inspeximus"]["args"]
    plugin = _load(PLUGIN)
    assert "mcpServers" not in plugin, "mcpServers is back inline in plugin.json"


def test_the_plugin_version_tracks_the_package_version():
    from inspeximus import __version__
    plugin = _load(PLUGIN)
    assert plugin["version"] == __version__, (
        "plugin.json says %s, the package is %s" % (plugin["version"], __version__))
