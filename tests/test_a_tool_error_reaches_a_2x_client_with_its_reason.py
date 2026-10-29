"""A tool's own error reaches an MCP client with its reason, on both SDK majors.

mcp 2.x sends a client the text of a `ToolError` only; any other exception arrives as the bare
`Error executing tool <name>`. Our tools raise RuntimeError and ValueError on purpose with the reason and the fix
in the text, and 1.x passed that text through. `_FreshFastMCP.call_tool`, defined on 2.x only, restores it on the
REQUEST path, which is what these tests drive: a call through an in-memory client, never the tool function.

On 1.x the override does not exist and the SDK sends the text itself, so the same assertion checks that the two
majors send the same message. The mutation that deletes the 2.x re-raise is killed by the first test on 2.x
(measured 2026-10-29); on 1.x that mutant cannot exist, because the method is not defined there.
"""
import os
import sys
import types

import pytest

pytest.importorskip("mcp")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _mcp_review  # noqa: E402

REASON = "zebra-quartz refused: the store directory is missing"


def _probe_server(mod):
    """A separate server of the same class, so the probe tool never joins the real server's 135."""
    srv = mod._FreshFastMCP("reason-probe")

    @srv.tool()
    def refuse(n: int) -> str:
        raise RuntimeError("%s (%d)" % (REASON, n))

    @srv.tool()
    def answer(n: int) -> str:
        return "ok %d" % n

    return srv, refuse, answer


def _call(mod, srv, tool, **arguments):
    return _mcp_review.call(types.SimpleNamespace(mcp=srv, _lowlevel=mod._lowlevel), tool, **arguments)


def test_a_client_sees_the_reason_a_tool_raised(monkeypatch, tmp_path):
    mod = _mcp_review.load_server(monkeypatch, tmp_path)
    srv, _, _ = _probe_server(mod)
    res = _call(mod, srv, "refuse", n=7)
    assert res.is_error, res
    assert res.text == "Error executing tool refuse: %s (7)" % REASON, (
        "the client got %r: the reason the tool raised did not reach it" % res.text)


def test_control_a_tool_that_returns_is_not_an_error(monkeypatch, tmp_path):
    # Without this, a probe server that fails every call would satisfy the test above.
    mod = _mcp_review.load_server(monkeypatch, tmp_path)
    srv, _, _ = _probe_server(mod)
    res = _call(mod, srv, "answer", n=7)
    assert not res.is_error and "ok 7" in res.text, res


def test_a_direct_caller_still_gets_the_exception_the_tool_raised(monkeypatch, tmp_path):
    # The conversion is on the request path only. A library caller or a test that calls the tool function
    # gets the RuntimeError itself, which is what two tests elsewhere in this suite rely on.
    mod = _mcp_review.load_server(monkeypatch, tmp_path)
    _, refuse, _ = _probe_server(mod)
    with pytest.raises(RuntimeError, match="zebra-quartz refused"):
        refuse(7)
