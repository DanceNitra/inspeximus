"""`inspeximus install --all`: every agent found on the machine, one shared store, nothing else broken.

Measured on 3.13.0 (one `--ide` per host, clean sandbox): Claude Code resolved to
`<git root>/.inspeximus/coding_memory.json` and Cursor, Windsurf, Codex and Cline to
`inspeximus_memory.json` in whatever directory each host launched them from. These tests pin the fix at the
config level; tools/one_memory_check.py starts every host's entry for real (CI job one-memory).
"""
from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys

import pytest

from inspeximus import install as I
from inspeximus import install_all as A

ALL = ("claude", "cursor", "windsurf", "codex", "cline", "gemini", "antigravity", "devin")


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
    monkeypatch.setattr(I, "resolve_runtime", lambda: ("python", sys.executable))
    monkeypatch.setattr(A.shutil, "which", lambda cmd: None)          # nothing found by PATH, only by folder
    proj = tmp_path / "proj"
    (proj / "src").mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(proj)], check=True)
    monkeypatch.chdir(proj)
    return h


def _all_hosts(h):
    for d in (".claude", ".cursor", ".codeium/windsurf", ".codex", ".cline", ".gemini/config"):
        (h / d).mkdir(parents=True, exist_ok=True)
    (h / ".gemini" / "settings.json").write_text("{}\n", encoding="utf-8")
    I.devin_dir().mkdir(parents=True, exist_ok=True)


def _entry(host):
    spec = I.HOSTS[host]
    path = spec["paths"](None)["user"]
    if spec["format"] == "toml":
        tomllib = pytest.importorskip("tomllib")
        return (tomllib.loads(path.read_text(encoding="utf-8")).get("mcp_servers") or {}).get("inspeximus")
    return (json.loads(path.read_text(encoding="utf-8"))[spec["root_key"]] or {}).get("inspeximus")


def _run(**kw):
    lines = []
    rc = A.run(out=lines.append, **kw)
    return rc, "\n".join(lines)


def test_every_found_agent_points_at_one_store(home):
    _all_hosts(home)
    rc, table = _run(rules="no")
    assert rc == 0, table
    store = str(home / ".inspeximus" / "coding_memory.json")
    for h in ALL:
        e = _entry(h)
        assert e and (e.get("env") or {}).get("INSPEXIMUS_PATH") == store, (h, e)
        assert "INSPEXIMUS_SCOPE" not in (e.get("env") or {}), h
    # one launch spec for every host: the same version serves every agent
    assert len({json.dumps([_entry(h)["command"]] + _entry(h)["args"]) for h in ALL}) == 1
    assert json.loads((home / ".inspeximus" / "shared.json").read_text())["store"] == store
    for h in ALL:
        assert I.HOSTS[h]["label"] in table
    assert "host" in table and "store path" in table


def test_a_host_that_is_not_installed_is_left_alone(home):
    (home / ".cursor").mkdir()
    rc, table = _run(rules="no")
    assert rc == 0
    assert (home / ".cursor" / "mcp.json").exists()
    assert not (home / ".gemini").exists() and not (home / ".codex" / "config.toml").exists()
    rows = {ln.split("  ")[0]: ln.split() for ln in table.splitlines()}
    assert "no" in rows["Gemini CLI"] and "yes" in rows["Cursor"], table


def test_a_second_run_changes_nothing(home):
    _all_hosts(home)
    _run(rules="yes")
    files = {p: p.read_bytes() for p in home.rglob("*") if p.is_file() and ".bak" not in p.name
             and "coding_memory" not in p.name and ".update_check" not in p.name}
    rc, table = _run(rules="yes")
    assert rc == 0, table
    after = {p: p.read_bytes() for p in files}
    assert after == files, [str(p) for p in files if after[p] != files[p]]


def test_an_existing_config_keeps_everything_else(home):
    _all_hosts(home)
    cur = home / ".cursor" / "mcp.json"
    cur.write_text(json.dumps({"mcpServers": {
        "github": {"command": "gh-mcp", "args": ["--x"]},
        "inspeximus": {"command": "old", "args": [], "timeout": 99,
                       "env": {"INSPEXIMUS_RECEIPTS": "1"}}}, "theme": "dark"}), encoding="utf-8")
    codex = home / ".codex" / "config.toml"
    codex.write_text('model = "o4"\n\n[mcp_servers.inspeximus]\ncommand = "old"\nargs = []\n'
                     'startup_timeout_sec = 30\n\n[mcp_servers.other]\ncommand = "x"\nargs = ["y"]\n',
                     encoding="utf-8")
    rc, table = _run(rules="no")
    assert rc == 0, table
    data = json.loads(cur.read_text())
    assert data["theme"] == "dark" and data["mcpServers"]["github"] == {"command": "gh-mcp", "args": ["--x"]}
    mine = data["mcpServers"]["inspeximus"]
    assert mine["timeout"] == 99 and mine["env"]["INSPEXIMUS_RECEIPTS"] == "1"
    assert mine["env"]["INSPEXIMUS_PATH"].endswith("coding_memory.json")
    tomllib = pytest.importorskip("tomllib")
    t = tomllib.loads(codex.read_text())
    assert t["model"] == "o4" and t["mcp_servers"]["other"] == {"command": "x", "args": ["y"]}
    assert t["mcp_servers"]["inspeximus"]["startup_timeout_sec"] == 30
    assert t["mcp_servers"]["inspeximus"]["env"]["INSPEXIMUS_PATH"].endswith("coding_memory.json")
    assert (home / ".cursor" / "mcp.json.bak").exists()


def test_two_different_stores_already_named_is_a_question_not_a_guess(home):
    _all_hosts(home)
    for host, path in (("cursor", "/a/one.json"), ("gemini", "/b/two.json")):
        p = I.HOSTS[host]["paths"](None)["user"]
        p.write_text(json.dumps({"mcpServers": {"inspeximus": {"command": "x", "args": [],
                                                               "env": {"INSPEXIMUS_PATH": path}}}}))
    before = {p: p.read_bytes() for p in home.rglob("*") if p.is_file()}
    rc, table = _run(rules="no")
    assert rc == 2 and "--store" in table
    assert {p: p.read_bytes() for p in home.rglob("*") if p.is_file()} == before, "nothing written"


def test_one_store_already_named_becomes_the_shared_one(home, tmp_path):
    _all_hosts(home)
    mine = tmp_path / "mine" / "mcp_memory_chain.json"
    p = I.HOSTS["claude"]["paths"](None)["user"]
    p.write_text(json.dumps({"mcpServers": {"inspeximus": {"type": "stdio", "command": "x", "args": [],
                                                           "env": {"INSPEXIMUS_PATH": str(mine)}}}}))
    rc, table = _run(rules="no")
    assert rc == 0, table
    for h in ALL:
        assert _entry(h)["env"]["INSPEXIMUS_PATH"] == str(mine), h
    from inspeximus._surface import coding_store_dir, coding_store_path
    assert coding_store_path(os.getcwd()) == str(mine), "the Claude Code hooks read the same store"
    assert coding_store_dir(os.getcwd()) == str(mine.parent), "and keep their files beside it"


def test_rules_are_asked_for_and_written_once(home):
    _all_hosts(home)
    gr = home / ".codeium" / "windsurf" / "memories" / "global_rules.md"
    gr.parent.mkdir(parents=True)
    gr.write_text("Always write tests.\n", encoding="utf-8")
    _run(rules="no")
    assert gr.read_text() == "Always write tests.\n", "--rules no writes nothing"
    assert not (home / ".gemini" / "config" / "rules").exists()
    _run(rules="yes")
    _run(rules="yes")
    text = gr.read_text()
    assert text.startswith("Always write tests.\n") and text.count(A.RULE_MARK) == 1
    ag = (home / ".gemini" / "config" / "rules" / "inspeximus.md").read_text()
    assert ag.startswith("---\ntrigger: always_on\n---") and A.RULE_MARK in ag
    assert A.RULE_MARK in (pathlib.Path(os.getcwd()) / ".cursor" / "rules" / "inspeximus.mdc").read_text()
    assert A.RULE_MARK in (home / "Documents" / "Cline" / "Rules" / "inspeximus.md").read_text()
    assert (I.devin_dir() / "AGENTS.md").read_text().count(A.RULE_MARK) == 1


def test_windsurf_renamed_devin_desktop_is_wired_where_its_docs_say(home):
    """Devin Desktop (formerly Windsurf) and Devin CLI read ~/.config/devin/mcp_config.json, %APPDATA%/devin
    on Windows; the ~/.codeium/windsurf file is read only by older builds and an opt-in discovery setting."""
    _all_hosts(home)
    rc, table = _run(rules="no")
    assert rc == 0, table
    want = home / "AppData" / "Roaming" / "devin" if os.name == "nt" else home / ".config" / "devin"
    assert I.devin_dir() == want
    entry = json.loads((want / "mcp_config.json").read_text())["mcpServers"]["inspeximus"]
    assert entry["env"]["INSPEXIMUS_PATH"] == str(home / ".inspeximus" / "coding_memory.json")
    assert "Devin Desktop / CLI" in table


def test_without_a_terminal_ask_means_no(home, monkeypatch):
    _all_hosts(home)
    monkeypatch.setattr(sys, "stdin", None)
    rc, table = _run(rules="ask")
    assert rc == 0 and not (home / ".gemini" / "config" / "rules").exists()
    assert "no rule written" in table


def test_the_install_records_one_decision_every_agent_can_recall(home):
    _all_hosts(home)
    _run(rules="no")
    from inspeximus import Inspeximus
    m = Inspeximus(str(home / ".inspeximus" / "coding_memory.json"))
    hits = m.recall("inspeximus-setup shares one memory", k=3)
    assert any("every AI agent on this machine shares one inspeximus memory" in h["text"] for h in hits)
    _run(rules="no")
    active = [r for r in m.__class__(m.path).items
              if r.get("key") == "decision::inspeximus-setup" and r.get("status") == "active"]
    assert len(active) == 1, "a second run supersedes the first decision, not a second copy"


def test_the_current_projects_old_store_is_imported(home):
    _all_hosts(home)
    from inspeximus import Inspeximus
    old = pathlib.Path(os.getcwd()) / ".inspeximus" / "coding_memory.json"
    old.parent.mkdir(parents=True)
    o = Inspeximus(str(old))
    o.remember("the staging database is db-7", key="staging-db")
    o.flush()
    rc, table = _run(rules="no")
    assert rc == 0 and "imported 1 record" in table
    m = Inspeximus(str(home / ".inspeximus" / "coding_memory.json"))
    assert any("db-7" in r["text"] for r in m.items)
    assert old.exists(), "the old store is left in place"


def test_hermes_provider_line_edits():
    assert A.hermes_provider("model: x\nmemory:\n  provider: mem0\n") == "mem0"
    assert A.hermes_provider("memory:\n  enabled: true\n") is None
    assert A.hermes_set_provider("model: x\nmemory:\n  provider: mem0\nother: 1\n") == \
        "model: x\nmemory:\n  provider: inspeximus\nother: 1\n"
    assert A.hermes_set_provider("memory:\n    enabled: true\n") == "memory:\n    provider: inspeximus\n    enabled: true\n"
    assert A.hermes_set_provider("model: x\n") == "model: x\nmemory:\n  provider: inspeximus\n"
    assert A.hermes_set_provider("") == "memory:\n  provider: inspeximus\n"


@pytest.mark.parametrize("current, answer, expected", [
    (None, "no", "inspeximus"), ("mem0", "no", "mem0"), ("mem0", "yes", "inspeximus")])
def test_hermes_is_wired_and_another_provider_is_asked_about(home, monkeypatch, current, answer, expected):
    hh = home / ".hermes"
    py = hh / "hermes-agent" / "venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    py.parent.mkdir(parents=True)
    py.write_text("", encoding="utf-8")
    if current:
        (hh / "config.yaml").write_text(f"model: m\nmemory:\n  provider: {current}\n", encoding="utf-8")
    calls = []
    monkeypatch.setattr(A, "install_into_hermes", lambda p: (calls.append(p) or (True, "ok")))
    (home / ".cursor").mkdir()
    rc, table = _run(rules="no", hermes_provider_change=answer)
    assert rc == 0, table
    assert calls == [py], "installed into Hermes' own venv"
    assert A.hermes_provider((hh / "config.yaml").read_text()) == expected
    cfg = hh / "inspeximus" / "config.json"
    if expected == "inspeximus":
        assert json.loads(cfg.read_text())["path"].endswith("coding_memory.json")
    else:
        assert not cfg.exists()


def test_without_the_flag_another_hermes_provider_is_kept_and_nothing_is_asked(home, monkeypatch, capsys):
    """An agent runs the installer; a prompt it cannot see would hang it. No flag means no change."""
    hh = home / ".hermes"
    py = hh / "hermes-agent" / "venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    py.parent.mkdir(parents=True)
    py.write_text("", encoding="utf-8")
    before = "model: m\nmemory:\n  provider: mem0\n"
    (hh / "config.yaml").write_text(before, encoding="utf-8")
    monkeypatch.setattr(A, "install_into_hermes", lambda p: (True, "ok"))
    (home / ".cursor").mkdir()

    class _NoStdin:
        def __getattr__(self, name):
            raise AssertionError(f"the installer touched stdin ({name})")
    monkeypatch.setattr(sys, "stdin", _NoStdin())
    monkeypatch.setattr("builtins.input", lambda *a: (_ for _ in ()).throw(AssertionError("input() called")))
    from inspeximus import cli
    rc = cli.main(["install", "--all", "--rules", "no"])
    out = capsys.readouterr().out
    assert rc == 0, out
    assert (hh / "config.yaml").read_text(encoding="utf-8") == before, "the provider is unchanged"
    assert not (hh / "config.yaml.bak").exists() and not (hh / "inspeximus" / "config.json").exists()
    assert "kept provider mem0" in out and "--hermes-provider yes" in out
    # control: the same run with the flag does switch it, so the fixture reaches the Hermes branch
    rc = cli.main(["install", "--all", "--rules", "no", "--hermes-provider", "yes"])
    assert rc == 0 and A.hermes_provider((hh / "config.yaml").read_text(encoding="utf-8")) == "inspeximus"


def test_the_hermes_provider_reads_the_configured_path(tmp_path):
    from inspeximus.integrations import hermes_agent as H
    cls = H._make_class(object)
    (tmp_path / "inspeximus").mkdir()
    (tmp_path / "inspeximus" / "config.json").write_text(json.dumps({"path": str(tmp_path / "shared.json")}))
    assert cls._configured_path(str(tmp_path)) == str(tmp_path / "shared.json")
    assert cls._configured_path(str(tmp_path / "nowhere")) is None


def test_gemini_and_antigravity_entries_have_only_documented_fields(home):
    _all_hosts(home)
    _run(rules="no")
    for h in ("gemini", "antigravity"):
        assert set(_entry(h)) == {"command", "args", "env"}, h
    assert I.HOSTS["gemini"]["paths"](None)["user"] == home / ".gemini" / "settings.json"
    assert I.HOSTS["antigravity"]["paths"](None)["user"] == home / ".gemini" / "config" / "mcp_config.json"


def test_the_update_notice_is_answered_from_the_cache_inside_the_window(tmp_path, monkeypatch):
    import time
    from inspeximus import _update
    monkeypatch.delenv("INSPEXIMUS_NO_UPDATE_CHECK", raising=False)
    (tmp_path / ".update_check.json").write_text(json.dumps({"checked_at": time.time(), "latest": "99.0.0"}))
    assert _update.check_for_update("3.14.0", cache_dir=str(tmp_path)) is None, "the network check stays throttled"
    note = _update.cached_notice("3.14.0", cache_dir=str(tmp_path))
    assert note and "99.0.0" in note and "install --all" in note, "every process sees it, not only the first"
    assert _update.cached_notice("99.0.0", cache_dir=str(tmp_path)) is None, "control: current is quiet"


def test_the_hermes_provider_opens_the_configured_store(tmp_path):
    """initialize() reads the path `install --all` (or the dashboard) wrote; before 3.14.0 it ignored it."""
    from inspeximus.integrations import hermes_agent as H
    cls = H._make_class(object)
    shared = tmp_path / "shared" / "coding_memory.json"
    (tmp_path / "hh" / "inspeximus").mkdir(parents=True)
    (tmp_path / "hh" / "inspeximus" / "config.json").write_text(json.dumps({"path": str(shared)}))
    p = cls()
    p.initialize("s1", hermes_home=str(tmp_path / "hh"))
    assert os.path.abspath(str(p._store.path)) == os.path.abspath(str(shared))
    q = cls()
    q.initialize("s2", hermes_home=str(tmp_path / "other"))
    assert "other" in str(q._store.path), "control: no config, the profile's own store"


def test_the_handshake_tells_every_client_to_recall_and_carries_the_notice():
    pytest.importorskip("mcp")
    from inspeximus import mcp_server as S
    assert "call `recall`" in S._instructions(notice="") and "remember_decision" in S._instructions(notice="")
    with_notice = S._instructions(notice="[inspeximus] A new version is available: 99.0.0 (you have 3.14.0).")
    assert "99.0.0" in with_notice and with_notice.startswith(S._RECALL_INSTRUCTIONS)
