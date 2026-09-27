"""3.14.3: the installer reads back what it wrote, `install --check` reports an entry that no longer
matches, and `--only` limits `--all` to named agents.

Found 2026-09-27 on our own machine: a Claude Code entry that `install --all` wrote read back `==3.14.0`
two hours later, and nothing said so; only a person reading the file noticed. A running Claude Code was
suspected and measured innocent (tools/claude_json_overwrite_repro.py; the live run is
tests/test_a_running_claude_code_keeps_our_entry.py). Whatever the writer, the class is "our entry
changed after we wrote it", so the tests put the old entry back by hand and require the tools to say so.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

from inspeximus import __version__
from inspeximus import install as I
from inspeximus import install_all as A


@pytest.fixture
def home(tmp_path, monkeypatch):
    h = tmp_path / "home"
    h.mkdir()
    for k in ("HOME", "USERPROFILE"):
        monkeypatch.setenv(k, str(h))
    for k in ("CODEX_HOME", "CLINE_DIR", "CLINE_DATA_DIR", "CLINE_MCP_SETTINGS_PATH", "HERMES_HOME",
              "INSPEXIMUS_CODING_STORE", "INSPEXIMUS_PATH", "INSPEXIMUS_SCOPE", "XDG_CONFIG_HOME"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(h / "AppData" / "Local"))
    monkeypatch.setenv("INSPEXIMUS_NO_UPDATE_CHECK", "1")
    monkeypatch.setattr(I, "resolve_runtime", lambda: ("uvx", "uvx"))
    monkeypatch.setattr(A.shutil, "which",
                        lambda cmd: str(h / "fakebin" / cmd) if (h / "fakebin" / cmd).exists() else None)
    (h / "fakebin").mkdir()
    for host in ("claude", "codex", "gemini", "cursor"):
        (h / "fakebin" / host).write_text("", encoding="utf-8")
    for d in (".claude", ".codex", ".gemini", ".cursor"):
        (h / d).mkdir()
    (h / ".gemini" / "settings.json").write_text("{}\n", encoding="utf-8")
    proj = tmp_path / "proj"
    proj.mkdir()
    monkeypatch.chdir(proj)
    return h


def _run(fn, **kw):
    lines = []
    rc = fn(out=lines.append, **kw)
    return rc, "\n".join(lines)


def test_apply_reads_back_and_reports_a_write_that_did_not_hold(home, monkeypatch):
    p = I.plan("claude", store_path=str(home / "s.json"))
    p["hooks"] = None
    ok, msg = I.apply(p)
    assert ok, msg                                                          # control: an honest write holds
    real = I._write_json

    def lands_elsewhere(path, data):                                       # a write replaced at once
        stale = json.loads(json.dumps(data))
        stale["mcpServers"]["inspeximus"]["args"] = ["--from", "inspeximus[mcp]==3.14.0", "inspeximus-mcp"]
        real(path, stale)
    monkeypatch.setattr(I, "_write_json", lands_elsewhere)
    p = I.plan("claude", store_path=str(home / "other.json"))
    p["hooks"] = None
    ok, msg = I.apply(p)
    assert not ok and "reading it back found" in msg and "3.14.0" in msg, msg


def test_check_flags_an_entry_that_was_put_back_after_the_install(home):
    rc, table = _run(A.run, rules="no", only="claude,codex")
    assert rc == 0, table
    rc, table = _run(A.check, only="claude,codex")
    assert rc == 0 and table.count(" ok") >= 2, table                      # control: fresh install checks clean
    cfg = home / ".claude.json"
    data = json.loads(cfg.read_text(encoding="utf-8"))
    data["mcpServers"]["inspeximus"]["args"] = ["--from", "inspeximus[mcp]==3.14.0", "inspeximus-mcp"]
    cfg.write_text(json.dumps(data), encoding="utf-8")                     # what a stale writer does
    rc, table = _run(A.check, only="claude,codex")
    assert rc == 1, table
    claude_row = next(ln for ln in table.splitlines() if ln.startswith("Claude Code"))
    assert f"DIFFERS: pin 3.14.0, this is {__version__}" in claude_row, table
    codex_row = next(ln for ln in table.splitlines() if ln.startswith("Codex"))
    assert codex_row.rstrip().endswith("ok"), table
    assert "install --all --only claude,codex" in table


def test_check_flags_an_entry_that_points_at_another_store(home):
    _run(A.run, rules="no", only="claude,codex")
    cfg = home / ".claude.json"
    data = json.loads(cfg.read_text(encoding="utf-8"))
    data["mcpServers"]["inspeximus"]["env"]["INSPEXIMUS_PATH"] = str(home / "somewhere-else.json")
    cfg.write_text(json.dumps(data), encoding="utf-8")
    rc, table = _run(A.check, only="claude,codex")
    assert rc == 1 and "DIFFERS: store" in table, table


def test_check_writes_nothing(home):
    _run(A.run, rules="no", only="claude")
    before = {p: p.read_bytes() for p in home.rglob("*") if p.is_file()}
    _run(A.check)
    after = {p: p.read_bytes() for p in home.rglob("*") if p.is_file()}
    assert before == after


def test_only_writes_the_named_agents_and_nothing_else(home):
    rc, table = _run(A.run, rules="no", only="claude,codex")
    assert rc == 0, table
    assert (home / ".claude.json").exists() and (home / ".codex" / "config.toml").exists()
    assert json.loads((home / ".gemini" / "settings.json").read_text()) == {}, "Gemini CLI was not named"
    assert not (home / ".cursor" / "mcp.json").exists(), "Cursor was not named"
    assert "Gemini CLI" not in table and "Cursor" not in table
    rc, table = _run(A.run, rules="no")                                     # control: without --only it writes them
    assert (home / ".cursor" / "mcp.json").exists()


def test_only_leaves_hermes_alone_unless_named(home, monkeypatch):
    hh = home / ".hermes"
    py = hh / "hermes-agent" / "venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    py.parent.mkdir(parents=True)
    py.write_text("", encoding="utf-8")
    calls = []
    monkeypatch.setattr(A, "install_into_hermes", lambda p: (calls.append(p) or (True, "ok")))
    monkeypatch.setattr(A, "hermes_loads_provider", lambda p, runner=None: True)
    monkeypatch.setattr(A, "_hermes_python", lambda p, code, runner=None: (False, ""))
    _run(A.run, rules="no", hermes_provider_change="yes", only="claude")
    assert calls == [] and not (hh / "config.yaml").exists()
    _run(A.run, rules="no", hermes_provider_change="yes", only="claude,hermes")
    assert calls == [py]


def test_only_refuses_an_unknown_agent_and_writes_nothing(home):
    rc, table = _run(A.run, rules="no", only="claude,vscode")
    assert rc == 2 and "unknown agent(s) for --only: vscode" in table, table
    assert not (home / ".claude.json").exists()


def test_the_cli_exposes_check_and_only(home, capsys):
    from inspeximus import cli
    assert cli.main(["install", "--all", "--rules", "no", "--only", "claude"]) == 0
    assert cli.main(["install", "--check", "--only", "claude"]) == 0
    out = capsys.readouterr().out
    assert "Claude Code" in out and "shared store" in out
    with pytest.raises(SystemExit):
        cli.main(["install", "--ide", "claude", "--only", "claude"])
