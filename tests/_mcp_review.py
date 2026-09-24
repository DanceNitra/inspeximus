"""Shared harness for the MCP tool correctness review (audits/2026-09-24/mcp-tools-review.md).

Every test that uses it gets a FRESH store in pytest's tmp_path and a freshly reloaded
`inspeximus.mcp_server`, because the server binds a module-global store (`_MEM`) and reads its whole
configuration from the environment at import. Nothing here touches a real store: the environment
variables the server reads are cleared first and restored by monkeypatch afterwards.

`call()` goes through a real MCP client session (in memory), not through the Python function, so the
test sees exactly what a client sees: argument validation, the `isError` flag a raised exception turns
into, and the JSON the result is serialised to. `mod.<tool>(...)` is the plain function, for the places
where the Python object itself is the thing under test.
"""
from __future__ import annotations

import importlib
import json
import logging
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import anyio

# The repository root, so `import inspeximus` resolves to this checkout under a bare `pytest` too.
_ROOT = str(Path(__file__).resolve().parent.parent)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

# FastMCP logs every request at INFO; a test run does not need two lines per tool call.
logging.getLogger("mcp").setLevel(logging.WARNING)

# Every variable mcp_server.py or open_store() reads. Cleared per test so a developer's shell (or a
# previous test) cannot leak a store path, a project scope or a key into the store under test.
SERVER_ENV = (
    "INSPEXIMUS_PATH", "INSPEXIMUS_SCOPE", "INSPEXIMUS_PROJECT", "INSPEXIMUS_RECEIPTS",
    "INSPEXIMUS_ACTIONS", "INSPEXIMUS_ACTOR", "INSPEXIMUS_RECEIPT_PUBKEY", "INSPEXIMUS_WRITER_KEY",
    "INSPEXIMUS_WRITER_KEY_FILE", "INSPEXIMUS_ECHO_GUARD", "INSPEXIMUS_EMBED_URL", "INSPEXIMUS_EMBED_MODEL",
    "INSPEXIMUS_EMBED_KEY", "INSPEXIMUS_OBSERVE_RECALL", "INSPEXIMUS_PII_DETECT", "INSPEXIMUS_PERSIST_VECTORS",
    "INSPEXIMUS_READ_RESOLVER", "INSPEXIMUS_MAX_K", "INSPEXIMUS_SNIPPET_CHARS", "INSPEXIMUS_NO_UPDATE_CHECK",
)


def load_server(monkeypatch, tmp_path, **env: str):
    """Reload the MCP server module against a throwaway store at tmp_path/store.json."""
    for k in SERVER_ENV:
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("INSPEXIMUS_PATH", str(tmp_path / "store.json"))
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    return importlib.reload(importlib.import_module("inspeximus.mcp_server"))


@dataclass
class ToolResult:
    is_error: bool
    data: Any          # structuredContent, with FastMCP's {"result": ...} wrapper removed for non-dict returns
    text: str          # the text content block(s), which is what an error carries


def call(mod, name: str, **arguments) -> ToolResult:
    """Call tool `name` through an in-memory MCP client session, exactly as a client would."""
    from mcp.shared.memory import create_connected_server_and_client_session

    async def _run():
        async with create_connected_server_and_client_session(mod.mcp._mcp_server) as client:
            return await client.call_tool(name, arguments)

    res = anyio.run(_run)
    text = "\n".join(getattr(c, "text", "") for c in (res.content or []))
    data = res.structuredContent
    if isinstance(data, dict) and set(data) == {"result"}:
        data = data["result"]
    if data is None and not res.isError and text:
        try:
            data = json.loads(text)
        except ValueError:
            data = text
    return ToolResult(is_error=bool(res.isError), data=data, text=text)


def tool_description(mod, name: str) -> str:
    return mod.mcp._tool_manager.get_tool(name).description or ""


def items_on_disk(path) -> list[dict]:
    """The records as persisted, read from the file rather than from the server's handle."""
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)
    return raw.get("items", raw) if isinstance(raw, dict) else raw
