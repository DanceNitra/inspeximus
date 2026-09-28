"""3.14.4: `install --all` and `install --check` end with a fixed ARMED block that carries a seal.

Found 2026-09-27 on a second machine: Hermes Agent on a 9B local model followed the install page,
installed correctly, and answered "Yes, done" in four bullets. The page told it to show the installer's
table; it summarized the table away, and the owner saw no confirmation at all. A weak model copies a
short fixed block where it rewrites a table, so the installer now ends with one, and the page says to
copy it exactly.

The seal is the setup decision the installer stores: its id and the `immutable_sha256` a write receipt
commits to. `install --check` recomputes it from the store, so a seal that no longer matches is reported.
"""
from __future__ import annotations

import contextlib
import io
import json
import re
import sys
import types
from abc import ABC, abstractmethod
from dataclasses import dataclass

import pytest

from inspeximus import __version__
from inspeximus import install as I
from inspeximus import install_all as A

BLOCK_HEADS = ("inspeximus ", "store: ", "wired: ", "restart: ", "seal: ")


@pytest.fixture
def home(tmp_path, monkeypatch):
    h = tmp_path / "home"
    h.mkdir()
    for k in ("HOME", "USERPROFILE"):
        monkeypatch.setenv(k, str(h))
    monkeypatch.setenv("APPDATA", str(h / "AppData" / "Roaming"))
    monkeypatch.setenv("LOCALAPPDATA", str(h / "AppData" / "Local"))
    for k in ("CODEX_HOME", "CLINE_DIR", "CLINE_DATA_DIR", "CLINE_MCP_SETTINGS_PATH", "HERMES_HOME",
              "INSPEXIMUS_CODING_STORE", "INSPEXIMUS_PATH", "INSPEXIMUS_SCOPE", "XDG_CONFIG_HOME",
              "INSPEXIMUS_NO_INJECT", "INSPEXIMUS_AGENT_ID"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("INSPEXIMUS_NO_UPDATE_CHECK", "1")
    monkeypatch.setattr(I, "resolve_runtime", lambda: ("uvx", "uvx"))
    monkeypatch.setattr(A.shutil, "which",
                        lambda cmd: str(h / "fakebin" / cmd) if (h / "fakebin" / cmd).exists() else None)
    (h / "fakebin").mkdir()
    for host in ("claude", "codex"):
        (h / "fakebin" / host).write_text("", encoding="utf-8")
    for d in (".claude", ".codex"):
        (h / d).mkdir()
    proj = tmp_path / "proj"
    proj.mkdir()
    monkeypatch.chdir(proj)
    return h


def _run(fn, **kw):
    lines = []
    rc = fn(out=lines.append, **kw)
    return rc, lines


def block_at_end(lines):
    """The block when it is the last output, else None. Strict: exactly these line heads, in this order,
    at the very end, plus an optional `attention:` line, all ASCII."""
    tail = list(lines)
    extra = tail[-1:] if tail and tail[-1].startswith("attention: ") else []
    body = tail[-(5 + len(extra)):-len(extra)] if extra else tail[-5:]
    if len(body) != 5 or any(not ln.startswith(h) for ln, h in zip(body, BLOCK_HEADS)):
        return None
    if not all(ln.isascii() for ln in body + extra):
        return None
    return body + extra


def test_install_all_ends_with_the_block(home):
    rc, lines = _run(A.run, rules="no")
    assert rc == 0, lines
    block = block_at_end(lines)
    assert block, lines
    assert block[0] == f"inspeximus {__version__} ARMED: one memory for 2 agents", block
    assert block[2] == "wired: Claude Code, Codex CLI" and block[3] == "restart: Claude Code, Codex CLI", block
    assert re.fullmatch(r"seal: [0-9a-f]+ [0-9a-f]{12}", block[4]), block
    assert block_at_end(lines[:-1]) is None, "control: the checker must fail when the block is cut"


def test_the_seal_is_the_setup_record_and_check_recomputes_it(home):
    rc, lines = _run(A.run, rules="no")
    assert rc == 0, lines
    shared = json.loads((home / ".inspeximus" / "shared.json").read_text(encoding="utf-8"))
    records, seal = A.read_store(shared["store"])
    assert (shared["seal"]["id"], shared["seal"]["sha256"]) == seal, (shared, seal)
    assert block_at_end(lines)[4] == f"seal: {seal[0]} {seal[1][:12]}"
    assert shared["agents"] == ["Claude Code", "Codex CLI"], shared

    rc, lines = _run(A.check, only="claude,codex")
    block = block_at_end(lines)
    assert rc == 0 and block, lines
    assert block[0] == f"inspeximus {__version__} ARMED: one memory for 2 agents", block
    assert block[3] == "restart: none" and block[4].endswith("(matches the install)"), block

    shared["seal"]["sha256"] = "0" * 64                        # the recorded seal no longer matches the store
    (home / ".inspeximus" / "shared.json").write_text(json.dumps(shared), encoding="utf-8")
    rc, lines = _run(A.check, only="claude,codex")
    block = block_at_end(lines)
    assert rc == 1 and block and "DIFFERS from the install" in block[4], lines
    assert block[-1].startswith("attention: ") and "seal" in block[-1], block


def test_check_prints_the_block_last_and_writes_nothing(home):
    _run(A.run, rules="no")
    watched = [home / ".claude.json", home / ".claude" / "settings.json", home / ".codex" / "config.toml",
               home / ".inspeximus" / "shared.json"]
    store = json.loads(watched[-1].read_text(encoding="utf-8"))["store"]
    watched.append(__import__("pathlib").Path(store))
    before = {p: p.read_bytes() for p in watched}
    rc, lines = _run(A.check, only="claude,codex")
    assert rc == 0 and block_at_end(lines), lines
    assert {p: p.read_bytes() for p in watched} == before


def test_nothing_found_says_not_armed(home):
    for host in ("claude", "codex"):
        (home / "fakebin" / host).unlink()
    rc, lines = _run(A.run, rules="no", only="claude,codex")
    block = block_at_end(lines)
    assert block and block[0] == f"inspeximus {__version__} NOT ARMED: no agent is wired", lines
    assert block[4] == "seal: none" and not (home / ".inspeximus" / "shared.json").exists()


@pytest.mark.parametrize("bad", ["%HOME%/.inspeximus/s.json", "$HOME/.inspeximus/s.json", "${HOME}/s.json"])
def test_a_store_path_with_a_literal_variable_is_refused(home, bad):
    rc, lines = _run(A.run, rules="no", only="claude,codex", store=bad)
    assert lines[0].startswith("running "), lines                  # F5: even a refusal names the installer
    assert rc == 2 and "literally" in lines[1], lines
    assert not (home / ".claude.json").exists()
    rc, lines = _run(A.run, rules="no", only="claude,codex", store=str(home / "ok.json"))
    assert rc == 0, lines                                     # control: a written-out path installs


def test_an_interpreter_in_a_literal_percent_home_folder_is_refused(home, monkeypatch):
    """PC2, 2026-09-27: in Git Bash, `python -m venv "%HOME%/.inspeximus/venv"` made a folder named %HOME%."""
    monkeypatch.setattr(I, "resolve_runtime",
                        lambda: ("python", str(home / "proj" / "%HOME%" / ".inspeximus" / "venv" / "python")))
    rc, lines = _run(A.run, rules="no", only="claude,codex")
    assert lines[0].startswith("running "), lines                  # F5: even a refusal names the installer
    assert rc == 2 and "%HOME%" in lines[1] and "virtual environment" in lines[1], lines
    assert not (home / ".claude.json").exists()


def test_only_one_agent_after_a_full_install_keeps_the_others_named(home):
    _run(A.run, rules="no")
    rc, lines = _run(A.run, rules="no", only="claude")
    assert rc == 0, lines
    shared = json.loads((home / ".inspeximus" / "shared.json").read_text(encoding="utf-8"))
    assert shared["agents"] == ["Claude Code", "Codex CLI"], shared
    from inspeximus._surface import open_store
    setup = [it for it in open_store(shared["store"]).items
             if it.get("key") == A.SETUP_KEY and it.get("status") != "superseded"]
    assert "Claude Code, Codex CLI" in setup[-1]["text"], setup[-1]["text"]


# ── the one-time "memory active" line ─────────────────────────────────────────────────────────────────
def _session_start(proj, transcript):
    from inspeximus import claude_code as cc
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        cc.session_start({"hook_event_name": "SessionStart", "session_id": "s1", "cwd": str(proj),
                          "source": "startup", "transcript_path": transcript})
    text = buf.getvalue().strip()
    return json.loads(text) if text else {}


def test_claude_code_shows_the_line_once_per_install(home, tmp_path):
    _run(A.run, rules="no")
    first = _session_start(tmp_path / "proj", str(home / ".claude" / "projects" / "p" / "s1.jsonl"))
    msg = first.get("systemMessage", "")
    assert re.fullmatch(r"inspeximus memory active: \d+ records?, shared with Codex CLI", msg), first
    again = _session_start(tmp_path / "proj", str(home / ".claude" / "projects" / "p" / "s2.jsonl"))
    assert "systemMessage" not in again, again
    seal = json.loads((home / ".inspeximus" / "shared.json").read_text(encoding="utf-8"))["seal"]
    _run(A.run, rules="no")                                    # an identical re-run keeps the seal
    assert json.loads((home / ".inspeximus" / "shared.json").read_text(encoding="utf-8"))["seal"] == seal
    assert "systemMessage" not in _session_start(tmp_path / "proj", str(home / ".claude" / "p" / "s3.jsonl"))
    (home / "fakebin" / "gemini").write_text("", encoding="utf-8")
    _run(A.run, rules="no")                                    # a new agent is a new install seal
    assert json.loads((home / ".inspeximus" / "shared.json").read_text(encoding="utf-8"))["seal"] != seal
    shown = _session_start(tmp_path / "proj", str(home / ".claude" / "p" / "s4.jsonl")).get("systemMessage", "")
    assert shown.endswith("shared with Codex CLI, Gemini CLI"), shown


def test_the_line_names_only_the_shared_store(home, tmp_path):
    """An agent whose store is not the shared one would announce a store it does not read."""
    _run(A.run, rules="no")
    from inspeximus._surface import announcement
    store = json.loads((home / ".inspeximus" / "shared.json").read_text(encoding="utf-8"))["store"]
    assert announcement("claude-code", "Claude Code", store, 3) == \
        "inspeximus memory active: 3 records, shared with Codex CLI"                  # control
    assert announcement("claude-code", "Claude Code", str(tmp_path / "elsewhere.json"), 3) is None


def test_codex_never_gets_the_field(home, tmp_path):
    _run(A.run, rules="no")
    out = _session_start(tmp_path / "proj", str(home / ".codex" / "sessions" / "s1.jsonl"))
    assert "systemMessage" not in out, out


@pytest.fixture()
def hermes_stub(monkeypatch):
    @dataclass(frozen=True)
    class RecallStatus:
        provider_label: str
        count: int
        glyph: str = "*"

    class MemoryProvider(ABC):
        @property
        @abstractmethod
        def name(self) -> str: ...

        @abstractmethod
        def is_available(self) -> bool: ...

        @abstractmethod
        def initialize(self, session_id: str, **kwargs) -> None: ...

        @abstractmethod
        def get_tool_schemas(self): ...

    pkg = types.ModuleType("agent")
    mod = types.ModuleType("agent.memory_provider")
    mod.MemoryProvider = MemoryProvider
    mod.RecallStatus = RecallStatus
    pkg.memory_provider = mod
    monkeypatch.setitem(sys.modules, "agent", pkg)
    monkeypatch.setitem(sys.modules, "agent.memory_provider", mod)
    return mod


def test_hermes_shows_the_line_once_in_its_memory_line(home, hermes_stub, tmp_path):
    _run(A.run, rules="no")
    store = json.loads((home / ".inspeximus" / "shared.json").read_text(encoding="utf-8"))["store"]
    hh = tmp_path / "hermes-home"
    (hh / "inspeximus").mkdir(parents=True)
    (hh / "inspeximus" / "config.json").write_text(json.dumps({"path": store}), encoding="utf-8")
    from inspeximus.integrations import hermes_agent
    p = hermes_agent.register()
    p.initialize("s1", hermes_home=str(hh))
    assert p.prefetch("which agents share one memory")                 # the setup decision is recalled
    first = p.recall_status()
    assert re.fullmatch(r"inspeximus memory active: \d+ records?, shared with Claude Code, Codex CLI",
                        first.provider_label), first
    p.prefetch("which agents share one memory")
    assert p.recall_status().provider_label == "inspeximus"
