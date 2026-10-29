"""The saver's Codex-shaped reply satisfies the three rules Codex checks, on real payload shapes.

Codex 0.154.0 has nowhere to put a digest: measured live on 2026-10-29, a hook that printed
`updatedMCPToolOutput` was handed the real result for a `Bash` call and for an MCP call, and the
model answered with the RAW output both times. `updatedToolOutput` does not appear in the binary.
So this is inert today, and it is written now because the rules it has to satisfy are already known
from the implementation waiting on openai/codex#34895:

  - send the generic `updatedToolOutput`, since `updatedMCPToolOutput` is dropped for a non-MCP tool
  - preserve the JSON KIND of `tool_response`: a string for a shell call, an object for an MCP call
  - stay under 32 KB

Both payload shapes below were copied from a live hook's own event log, not from documentation.
"""
from __future__ import annotations

import json

import pytest

from inspeximus import _envpolicy, _userconfig
from inspeximus import context_saver as cs

BIG = "\n".join("line %d: hydraulic pressure curve sample row" % i for i in range(400)) + "\n"


@pytest.fixture(autouse=True)
def _saver_on(monkeypatch):
    """The function imports these inside its body, so the patch goes on the modules themselves."""
    monkeypatch.setattr(_userconfig, "get", lambda *a: True if a[-1] == "hook" else None)
    monkeypatch.setattr(_envpolicy, "raw", lambda _name: "")


def _shell_event(text=BIG):
    return {"hook_event_name": "PostToolUse", "tool_name": "Bash",
            "tool_input": {"command": "type rows.txt"}, "tool_response": text}


def _mcp_event(text=BIG):
    return {"hook_event_name": "PostToolUse", "tool_name": "mcp__inspeximus__where_am_i",
            "tool_input": {},
            "tool_response": {"content": [{"type": "text", "text": text}]}}


def test_a_shell_result_is_replaced_by_a_string():
    out = cs.codex_post_tool_use_replacement(_shell_event())
    assert out is not None
    rep = out["hookSpecificOutput"]["updatedToolOutput"]
    assert out["hookSpecificOutput"]["hookEventName"] == "PostToolUse"
    assert isinstance(rep, str), "a shell result is a string; an object would be dropped"
    assert "full output: " in rep and len(rep) < len(BIG)


def test_an_mcp_result_is_replaced_by_the_same_object_shape():
    """The kind check is the whole reason this function exists separately."""
    ev = _mcp_event()
    out = cs.codex_post_tool_use_replacement(ev)
    assert out is not None
    rep = out["hookSpecificOutput"]["updatedToolOutput"]
    assert isinstance(rep, dict), "an MCP result is an object; a bare string would be dropped"
    assert list(rep) == list(ev["tool_response"]), "the object's keys must survive"
    blocks = rep["content"]
    assert isinstance(blocks, list) and blocks[0]["type"] == "text"
    assert "full output: " in blocks[0]["text"]
    assert len(blocks[0]["text"]) < len(BIG)


def test_the_generic_field_is_the_one_sent():
    """CONTROL. `updatedMCPToolOutput` is dropped for a non-MCP tool, so it must not be what we send."""
    for ev in (_shell_event(), _mcp_event()):
        out = cs.codex_post_tool_use_replacement(ev)
        assert "updatedToolOutput" in out["hookSpecificOutput"]
        assert "updatedMCPToolOutput" not in out["hookSpecificOutput"]


def test_a_replacement_over_the_ceiling_is_refused(monkeypatch):
    """CONTROL, and it can fail: Codex drops anything over 32 KB, so we must not offer one."""
    monkeypatch.setattr(cs, "CODEX_MAX_REPLACEMENT_BYTES", 10)
    assert cs.codex_post_tool_use_replacement(_shell_event()) is None


@pytest.mark.parametrize("ev", [
    {"tool_name": "Read", "tool_response": BIG},                       # another tool
    {"tool_name": "Bash", "tool_response": "short"},                   # under the passthrough bound
    {"tool_name": "mcp__x__y", "tool_response": {"content": "nope"}},   # content is not a list
    {"tool_name": "mcp__x__y", "tool_response": {"content": []}},       # no text block
    {"tool_name": "Bash", "tool_response": {"stdout": BIG}},            # Claude Code's shape, not Codex's
    "not a dict",
])
def test_a_payload_it_does_not_understand_is_left_alone(ev):
    assert cs.codex_post_tool_use_replacement(ev) is None


def test_the_saver_being_off_leaves_the_result_alone(monkeypatch):
    """CONTROL for the fixture above: with the switch off, nothing is replaced."""
    monkeypatch.setattr(_userconfig, "get", lambda *a: False)
    assert cs.codex_post_tool_use_replacement(_shell_event()) is None


def test_the_replacement_is_json_serialisable():
    """It travels as JSON on a hook's stdout; an object that cannot be dumped is no replacement."""
    out = cs.codex_post_tool_use_replacement(_mcp_event())
    assert json.loads(json.dumps(out))["hookSpecificOutput"]["hookEventName"] == "PostToolUse"
