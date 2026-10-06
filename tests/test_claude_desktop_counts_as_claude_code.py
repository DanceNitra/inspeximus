"""3.14.4: `install --all` wires Claude Code when only Claude Desktop is installed.

Found 2026-09-27 on a second machine with Claude Desktop and no `claude` on PATH: Hermes Agent ran the
install page and wired itself, and a new Claude Desktop Code-tab session had no inspeximus hooks or MCP
tools. Detection looked only for `claude` on PATH and two CLI folders. Claude Desktop keeps a bundled
Claude Code per version, and its Code tab reads ~/.claude.json and ~/.claude/settings.json like the CLI.

Each location is tested with a control that removes the executable: a folder alone must not count.
"""
from __future__ import annotations

import json
import os
import sys

import pytest

from inspeximus import install as I
from inspeximus import install_all as A


@pytest.fixture
def home(tmp_path, monkeypatch):
    """A home with NOTHING on PATH: no claude, no uv, no uvx."""
    h = tmp_path / "home"
    h.mkdir()
    for k in ("HOME", "USERPROFILE"):
        monkeypatch.setenv(k, str(h))
    monkeypatch.setenv("APPDATA", str(h / "AppData" / "Roaming"))
    monkeypatch.setenv("LOCALAPPDATA", str(h / "AppData" / "Local"))
    for k in ("CODEX_HOME", "CLINE_DIR", "CLINE_DATA_DIR", "CLINE_MCP_SETTINGS_PATH", "HERMES_HOME",
              "INSPEXIMUS_CODING_STORE", "INSPEXIMUS_PATH", "INSPEXIMUS_SCOPE", "XDG_CONFIG_HOME"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("INSPEXIMUS_NO_UPDATE_CHECK", "1")
    monkeypatch.setattr(A.shutil, "which", lambda cmd: None)
    proj = tmp_path / "proj"
    proj.mkdir()
    monkeypatch.chdir(proj)
    return h


def _desktop_exe(h, where):
    """The bundled Claude Code executable Claude Desktop keeps, at each known location."""
    if where == "classic":                      # the classic Windows installer: %APPDATA%\Claude\claude-code
        return I._appdata() / "Claude" / "claude-code" / "2.1.280" / "claude.exe"
    if where == "msix":                         # the Microsoft Store package keeps it in its LocalCache
        return (h / "AppData" / "Local" / "Packages" / "Claude_pzs8sxrjxfjjc" / "LocalCache" / "Roaming"
                / "Claude" / "claude-code" / "2.1.280" / "claude.exe")
    if where == "macos":                        # ~/Library/Application Support on macOS (_appdata there)
        return I._appdata() / "Claude" / "claude-code" / "2.1.280" / "claude"
    raise ValueError(where)


def _run(**kw):
    lines = []
    rc = A.run(out=lines.append, **kw)
    return rc, "\n".join(lines)


@pytest.mark.parametrize("where", ["classic", "msix", "macos"])
def test_claude_desktops_own_claude_code_is_found_and_wired(home, where):
    exe = _desktop_exe(home, where)
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"")
    found, why = A.detect("claude")
    assert found and why == str(exe), (found, why)
    rc, out = _run(rules="no", only="claude")
    assert rc == 0, out
    entry = json.loads((home / ".claude.json").read_text(encoding="utf-8"))["mcpServers"]["inspeximus"]
    assert entry["env"]["INSPEXIMUS_PATH"]
    hooks = json.loads((home / ".claude" / "settings.json").read_text(encoding="utf-8"))["hooks"]
    assert "UserPromptSubmit" in hooks and "SessionStart" in hooks, hooks


@pytest.mark.parametrize("where", ["classic", "msix", "macos"])
def test_the_folder_without_its_executable_does_not_count(home, where):
    exe = _desktop_exe(home, where)
    exe.parent.mkdir(parents=True)                           # the version folder, no executable in it
    (I._appdata() / "Claude").mkdir(parents=True, exist_ok=True)
    (I._appdata() / "Claude" / "claude_desktop_config.json").write_text("{}", encoding="utf-8")
    assert A.detect("claude") == (False, "")
    rc, out = _run(rules="no", only="claude")
    assert not (home / ".claude.json").exists(), out
    exe.write_bytes(b"")                                     # control: the executable is what counts
    assert A.detect("claude")[0]


def test_the_commands_need_no_claude_and_no_uv_on_path(home):
    """PC2 had neither: Hermes built ~/.inspeximus/venv from its own bundled Python. With no uvx the MCP
    entry and every hook name the absolute interpreter that ran the installer, and nothing names a bare
    command that a PATH lookup would have to find."""
    exe = _desktop_exe(home, "msix")
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"")
    assert I.resolve_runtime() == ("python", sys.executable)
    rc, out = _run(rules="no", only="claude")
    assert rc == 0, out
    entry = json.loads((home / ".claude.json").read_text(encoding="utf-8"))["mcpServers"]["inspeximus"]
    assert os.path.isabs(entry["command"]) and os.path.samefile(entry["command"], sys.executable), entry
    assert entry["args"][:3] == ["-I", "-m", "inspeximus.mcp_server"], entry     # -I: AUDIT-A F-15
    hooks = json.loads((home / ".claude" / "settings.json").read_text(encoding="utf-8"))["hooks"]
    commands = [h["command"] for evt in hooks.values() for m in evt for h in m.get("hooks", [])
                if "inspeximus" in h.get("command", "")]
    assert commands, hooks
    for c in commands:
        assert c.startswith(I._shell_path(sys.executable)), c
        assert not c.split()[0].strip('"').lower() in ("uv", "uvx", "claude", "python", "python3"), c


def test_inside_the_msix_container_the_files_written_are_the_real_ones(home, tmp_path, monkeypatch):
    """An installer run from a Desktop Code-tab session runs inside the MSIX container, where %APPDATA%
    is virtualized. ~/.claude.json and ~/.claude/settings.json are not under AppData, so they are the
    real files the Code tab reads, and the installer must write exactly those, nothing under %APPDATA%."""
    virtual = tmp_path / "container-view" / "Roaming"
    monkeypatch.setenv("APPDATA", str(virtual))
    exe = I._appdata() / "Claude" / "claude-code" / "2.1.280" / "claude.exe"   # the container's view
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"")
    assert A.detect("claude")[0]
    before = {p for p in virtual.rglob("*")}
    rc, out = _run(rules="no", only="claude")
    assert rc == 0, out
    p = I.plan("claude", store_path=str(home / "s.json"))
    assert p["path"] == home / ".claude.json" and p["hooks"]["path"] == home / ".claude" / "settings.json", p
    assert (home / ".claude.json").exists() and (home / ".claude" / "settings.json").exists()
    assert {p for p in virtual.rglob("*")} == before, "the installer wrote into the virtualized %APPDATA%"
