"""A long-lived handle sees a peer process's write on its READ path, without a write of its own.

WHY. "One memory, every agent, at once" was met on the write path: a save that finds the file moved
merges and lands. The read path was not. An MCP server or a framework adapter is one handle that
lives for hours, and it answered `recall()` from the records it loaded at open. Measured 2026-09-14
on 2.27.8, both formats: a peer's record was invisible to `recall()` and to `items` until this handle
happened to write, and on the JSON format that write was refused, so it stayed invisible after it.

`refresh()` is one stat when the file has not moved and the documented merge when it has. The MCP
server calls it at the tool boundary, so every one of its 73 tools sees its peers by construction.
The peer is a separate OS process, so the interpreter lock cannot hide the case.
"""
from __future__ import annotations

import importlib
import os
import subprocess
import sys
import tempfile

import pytest

from inspeximus import Inspeximus

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _peer_writes(path, text, fmt):
    code = (f"import os, sys; os.environ['INSPEXIMUS_STORE_FORMAT'] = {fmt!r}; sys.path.insert(0, {REPO!r}); "
            f"from inspeximus import Inspeximus; Inspeximus(path={path!r}).remember({text!r}, mtype='fact')")
    subprocess.run([sys.executable, "-c", code], check=True, timeout=60)


@pytest.mark.parametrize("fmt", ["rows", "json"])
def test_refresh_brings_in_a_peers_record_on_both_formats(tmp_path, monkeypatch, fmt):
    monkeypatch.setenv("INSPEXIMUS_STORE_FORMAT", fmt)
    p = str(tmp_path / "s.json")
    a = Inspeximus(path=p)
    a.remember("the deploy window is 02:00 UTC", mtype="fact")
    _peer_writes(p, "the staging database is db-9", fmt)
    assert not any("db-9" in r["text"] for r in a.items), "the fixture did not reproduce staleness; nothing measures"
    out = a.refresh()
    assert out["changed"] is True
    assert any("db-9" in r["text"] for r in a.recall("staging database", k=5))
    assert a.refresh() == {"changed": False}, "a second refresh on an unmoved file must be a stat, not a reload"
    a.remember("written after the refresh", mtype="fact")
    assert len(Inspeximus(path=p).items) == 3, "the refresh must not cost the peer's record or this handle's"


def test_refresh_keeps_this_handles_unsaved_record(tmp_path, monkeypatch):
    """The merge is a union: what this handle holds and could not yet save survives the refresh."""
    monkeypatch.setenv("INSPEXIMUS_STORE_FORMAT", "json")
    p = str(tmp_path / "s.json")
    a = Inspeximus(path=p)
    a.remember("the deploy window is 02:00 UTC", mtype="fact")
    _peer_writes(p, "the staging database is db-9", "json")
    from inspeximus.core import StoreChangedOnDisk
    with pytest.raises(StoreChangedOnDisk):
        a.remember("held in memory, refused on disk", mtype="fact")
    out = a.refresh()
    assert out["changed"] is True and out["readded"] == 1, out
    texts = {r["text"] for r in a.items}
    assert {"the deploy window is 02:00 UTC", "the staging database is db-9",
            "held in memory, refused on disk"} <= texts
    a.remember("and now it lands", mtype="fact")
    assert len(Inspeximus(path=p).items) == 4


def test_control_refresh_on_an_unmoved_file_does_not_reload(tmp_path, monkeypatch):
    monkeypatch.setenv("INSPEXIMUS_STORE_FORMAT", "rows")
    a = Inspeximus(path=str(tmp_path / "s.json"))
    a.remember("only record", mtype="fact")
    loads = {"n": 0}
    real = Inspeximus._load_from_disk

    def counted(self):
        loads["n"] += 1
        return real(self)

    monkeypatch.setattr(Inspeximus, "_load_from_disk", counted)
    for _ in range(50):
        assert a.refresh() == {"changed": False}
    assert loads["n"] == 0, "refresh reloaded a file that had not moved"


def test_the_mcp_recall_tool_sees_a_peers_write_without_a_restart(tmp_path, monkeypatch):
    monkeypatch.setenv("INSPEXIMUS_STORE_FORMAT", "rows")
    p = str(tmp_path / "s.json")
    monkeypatch.setenv("INSPEXIMUS_PATH", p)
    pytest.importorskip("mcp")
    import inspeximus.mcp_server as mcp_server
    m = importlib.reload(mcp_server)
    m._MEM.remember("the deploy window is 02:00 UTC", mtype="fact")
    _peer_writes(p, "the staging database is db-9", "rows")
    hits = m.recall("staging database")
    assert any("db-9" in (h.get("text") or "") for h in hits), hits


def test_every_adapter_read_is_preceded_by_a_refresh():
    """The adoption guard. A framework adapter is a long-lived handle too, and a helper with zero
    callers is the shape a rule takes when it is remembered rather than wired. Every `.recall(` in
    an integration module must have a `.refresh()` on the line before it, except in docstrings."""
    import glob
    import re
    missing = []
    for path in sorted(glob.glob(os.path.join(REPO, "inspeximus", "integrations", "*.py"))):
        lines = open(path, encoding="utf-8").read().splitlines()
        for i, ln in enumerate(lines):
            if re.search(r"\.recall\(", ln) and not ln.lstrip().startswith(("#", "*", "current-truth")) \
                    and "InspeximusRetriever" not in ln:
                prev = lines[i - 1] if i else ""
                if ".refresh()" not in prev:
                    missing.append(f"{os.path.basename(path)}:{i + 1}")
    assert not missing, f"adapter reads without a refresh() before them: {missing}"
