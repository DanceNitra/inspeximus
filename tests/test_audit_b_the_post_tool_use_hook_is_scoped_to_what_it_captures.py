"""AUDIT-B B-02: the installed PostToolUse hook must be scoped to the tools `capture` records.

`capture` records Edit, MultiEdit, Write and Bash (`_CAPTURED_TOOLS`). The PreToolUse entry already
carries a matcher derived from its handler's tuple; the PostToolUse entry carried none, in both the
`--install` settings and the plugin's hooks/hooks.json, so Claude Code started a Python process that
imports the package for every Read, Grep, Glob and WebFetch of a session. Measured 2026-09-27: 0.29 to
0.43 s per such event even after the handler stopped opening the store (B-01), against 0.09 to 0.12 s
for bare Python, and 4.5 s per event before B-01 on a 67,165-record store.

The matcher is compared with `_CAPTURED_TOOLS` as a set, so the two cannot drift apart silently: a tool
the handler captures and the matcher omits would never be captured, and one the matcher names and the
handler ignores pays the process launch for nothing. The control is the PreToolUse entry, which already
works this way.
"""
import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import inspeximus.claude_code as cc  # noqa: E402


def _tools(matcher):
    return set((matcher or "").split("|")) if matcher else None


def _ours(entries):
    return [e for e in entries if any("inspeximus.claude_code" in (h.get("command") or "")
                                      for h in e.get("hooks", []))]


@pytest.mark.xfail(strict=True, raises=AssertionError,
                   reason="B-02: the PostToolUse hook has no matcher, so every tool call starts a process")
def test_install_and_the_plugin_manifest_scope_post_tool_use_to_the_captured_tools(tmp_path):
    assert cc.install(cwd=str(tmp_path)) is True
    cfg = json.load(open(tmp_path / ".claude" / "settings.json", encoding="utf-8"))
    plugin = json.load(open(os.path.join(ROOT, "hooks", "hooks.json"), encoding="utf-8"))["hooks"]

    # CONTROL: the PreToolUse entry is scoped to its handler's tuple in both places.
    for where, hooks in (("settings", cfg["hooks"]), ("hooks.json", plugin)):
        pre = _ours(hooks["PreToolUse"])
        if len(pre) != 1 or _tools(pre[0].get("matcher")) != set(cc._PRE_TOOLS):
            pytest.fail(f"control: the {where} PreToolUse entry is not scoped to _PRE_TOOLS: {pre}")

    for where, hooks in (("settings", cfg["hooks"]), ("hooks.json", plugin)):
        post = _ours(hooks["PostToolUse"])
        assert len(post) == 1, f"{where}: {len(post)} inspeximus PostToolUse entries"
        assert _tools(post[0].get("matcher")) == set(cc._CAPTURED_TOOLS), (
            f"{where}: PostToolUse matcher {post[0].get('matcher')!r}, expected the captured tools "
            f"{sorted(cc._CAPTURED_TOOLS)}")
