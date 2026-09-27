"""3.14.3: the installer rewrites a user's file in the line endings that file already uses.

Measured 2026-09-27 on Windows: every write of ~/.claude.json by the installer turned its 2,604 LF line
endings into CRLF (about 2.6 KB of churn), and Claude Code's next write turned them back. `write_text`
translates "\\n" to the platform's line ending; the installer now writes the bytes itself.

Both directions are tested on every OS. Before the fix, "LF stays LF" failed on Windows and "CRLF stays
CRLF" failed everywhere else, so between the CI legs neither regression can pass unseen.
"""
from __future__ import annotations

import json

import pytest

from inspeximus import install as I
from inspeximus import install_all as A


@pytest.fixture
def home(tmp_path, monkeypatch):
    h = tmp_path / "home"
    h.mkdir()
    for k in ("HOME", "USERPROFILE"):
        monkeypatch.setenv(k, str(h))
    for k in ("CODEX_HOME", "INSPEXIMUS_PATH", "INSPEXIMUS_SCOPE", "XDG_CONFIG_HOME"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("INSPEXIMUS_NO_UPDATE_CHECK", "1")
    monkeypatch.setattr(I, "resolve_runtime", lambda: ("uvx", "uvx"))
    return h


def _endings(path):
    b = path.read_bytes()
    crlf = b.count(b"\r\n")
    return crlf, b.count(b"\n") - crlf


@pytest.mark.parametrize("nl", ["\n", "\r\n"])
def test_a_json_config_keeps_its_line_endings(home, nl):
    cfg = home / ".claude.json"
    text = json.dumps({"numStartups": 3, "projects": {"C:/p": {"x": 1}},
                       "mcpServers": {"other": {"command": "o"}}}, indent=2) + "\n"
    cfg.write_bytes(text.replace("\n", nl).encode("utf-8"))
    p = I.plan("claude", store_path=str(home / "s.json"))
    p["hooks"] = None
    ok, msg = I.apply(p)
    assert ok, msg
    crlf, lf = _endings(cfg)
    assert json.loads(cfg.read_text(encoding="utf-8"))["mcpServers"]["inspeximus"]
    if nl == "\n":
        assert crlf == 0 and lf > 5, (crlf, lf)
    else:
        assert lf == 0 and crlf > 5, (crlf, lf)


@pytest.mark.parametrize("nl", ["\n", "\r\n"])
def test_the_codex_toml_keeps_its_line_endings(home, nl):
    cfg = home / ".codex" / "config.toml"
    cfg.parent.mkdir()
    cfg.write_bytes('model = "o3"\n\n[mcp_servers.other]\ncommand = "o"\n'.replace("\n", nl).encode("utf-8"))
    ok, msg = I.apply(I.plan("codex", store_path=str(home / "s.json")))
    assert ok, msg
    crlf, lf = _endings(cfg)
    assert "[mcp_servers.inspeximus]" in cfg.read_text(encoding="utf-8")
    assert (crlf == 0 and lf > 3) if nl == "\n" else (lf == 0 and crlf > 3), (crlf, lf)


@pytest.mark.parametrize("nl", ["\n", "\r\n"])
def test_a_rules_file_the_installer_appends_to_keeps_its_line_endings(home, nl):
    rules = home / ".codeium" / "windsurf" / "memories" / "global_rules.md"
    rules.parent.mkdir(parents=True)
    rules.write_bytes("Always write tests.\nPrefer small commits.\n".replace("\n", nl).encode("utf-8"))
    A.apply_rules({"host": "windsurf", "path": rules, "action": "append", "content": A.RULE_LINE + "\n"})
    crlf, lf = _endings(rules)
    assert A.RULE_MARK in rules.read_text(encoding="utf-8")
    assert (crlf == 0 and lf >= 3) if nl == "\n" else (lf == 0 and crlf >= 3), (crlf, lf)


def test_a_new_file_is_written_with_lf(home):
    cfg = home / ".cursor" / "mcp.json"
    ok, msg = I.apply(I.plan("cursor", store_path=str(home / "s.json")))
    assert ok, msg
    crlf, lf = _endings(cfg)
    assert crlf == 0 and lf > 0


def test_the_newline_detector_reads_the_first_line_ending():
    assert I._newline_of.__doc__
    import pathlib
    import tempfile
    d = pathlib.Path(tempfile.mkdtemp())
    for name, body, want in (("lf", b"a\nb\r\n", "\n"), ("crlf", b"a\r\nb\n", "\r\n"), ("one", b"a", "\n")):
        (d / name).write_bytes(body)
        assert I._newline_of(d / name) == want, name
    assert I._newline_of(d / "missing") == "\n"
