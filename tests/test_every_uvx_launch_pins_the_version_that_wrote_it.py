"""Every uvx launch names the exact inspeximus version that wrote it.

`uvx --from inspeximus[mcp] inspeximus-mcp` resolves whatever the index serves at that moment. Right
after 3.9.6 was published the index still served 3.9.5 for a while, and 3.9.5 refuses the
INSPEXIMUS_SCOPE=claude-code that 3.9.6 writes: the server died with StoreScopeError (found by the
tester-kit lane on PyPI 3.9.6). An unpinned launch also lets the server and the hooks run two different
versions against one store. Four launches carried the unpinned spec: the installer's MCP entry and its
hook command, and the plugin's .mcp.json and hooks/hooks.json. The plugin files move with the plugin
version, so they are release version carriers like plugin.json.
"""
import json
import os
import sys

from inspeximus import __version__
from inspeximus import install as I

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PIN_MCP = f"inspeximus[mcp]=={__version__}"
PIN_CORE = f"inspeximus=={__version__}"


def test_the_installer_pins_the_server_and_the_hooks_to_its_own_version(tmp_path, monkeypatch):
    monkeypatch.setattr(I, "_home", lambda: tmp_path)
    monkeypatch.setattr(I.shutil, "which", lambda name: "/usr/bin/uvx" if name == "uvx" else None)
    p = I.plan("claude")
    assert p["block"]["args"] == ["--from", PIN_MCP, "inspeximus-mcp"]
    cmd = p["hooks"]["data"]["hooks"]["SessionStart"][0]["hooks"][0]["command"]
    assert f" --from {PIN_CORE} " in cmd, cmd


def test_the_default_server_block_is_pinned():
    assert I.default_server_block()["args"] == ["--from", PIN_MCP, "inspeximus-mcp"]


def test_the_plugin_server_is_pinned_to_the_release():
    cfg = json.load(open(os.path.join(ROOT, ".mcp.json"), encoding="utf-8"))
    assert cfg["mcpServers"]["inspeximus"]["args"] == ["--from", PIN_MCP, "inspeximus-mcp"]


def test_every_plugin_hook_is_pinned_to_the_release():
    hooks = json.load(open(os.path.join(ROOT, "hooks", "hooks.json"), encoding="utf-8"))["hooks"]
    cmds = [h["command"] for groups in hooks.values() for g in groups for h in g["hooks"]]
    assert cmds, "control: the plugin declares hooks"
    unpinned = [c for c in cmds if "uvx" in c and f"--from {PIN_CORE} " not in c]
    assert unpinned == []


def test_the_release_check_reads_both_plugin_launch_files_as_version_carriers():
    sys.path.insert(0, os.path.join(ROOT, "tools"))
    import release_check as rc
    for rel in (".mcp.json", "hooks/hooks.json"):
        assert rel in rc.REQUIRED_CARRIERS
        found, err = rc._read_carrier(rc.ROOT, rel)
        assert err is None and found and {v for _, v in found} == {__version__}, (rel, found, err)
