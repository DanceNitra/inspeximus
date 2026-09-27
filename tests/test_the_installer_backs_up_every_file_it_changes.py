"""3.14.4: every file `install --all` changes gets a `.bak` of its previous bytes, and `inspeximus --version`
and `python -m inspeximus` work.

Found 2026-09-27 on a second machine after Hermes Agent ran the install page. The page promises "keeps a
.bak copy of every file it changes"; the backup was a flag each write passed, and the write of Hermes'
`inspeximus/config.json` did not pass it. The same check found `inspeximus --version` exiting 2 and
`python -m inspeximus` failing, the first two commands an agent tried.
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

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture
def home(tmp_path, monkeypatch):
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
    monkeypatch.setattr(I, "resolve_runtime", lambda: ("uvx", "uvx"))
    monkeypatch.setattr(A.shutil, "which",
                        lambda cmd: str(h / "fakebin" / cmd) if (h / "fakebin" / cmd).exists() else None)
    monkeypatch.setattr(A, "hermes_loads_provider", lambda py, runner=None: True)
    monkeypatch.setattr(A, "_hermes_python", lambda py, code, runner=None: (False, ""))
    monkeypatch.setattr(A, "hermes_version", lambda py, runner=None: "0.21.3")
    monkeypatch.setattr(A, "install_into_hermes", lambda py: (True, "ok"))
    (h / "fakebin").mkdir()
    for host in ("claude", "codex", "cursor"):
        (h / "fakebin" / host).write_text("", encoding="utf-8")
    proj = tmp_path / "proj"
    proj.mkdir()
    monkeypatch.chdir(proj)
    return h


@pytest.mark.parametrize("older_bak", [False, True], ids=["no-bak-yet", "another-tools-bak"])
def test_every_existing_file_the_install_changes_keeps_a_bak(home, older_bak):
    """Every target already exists with other content, as on a machine that had a previous setup. With
    `another-tools-bak`, each also has a `.bak` another tool made (on PC2, `hermes config set` 14 minutes
    earlier): that backup must survive, and the pre-install bytes must still be kept somewhere named."""
    hh = home / ".hermes"
    py = hh / "hermes-agent" / "venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    py.parent.mkdir(parents=True)
    py.write_text("", encoding="utf-8")
    before = {
        home / ".claude.json": json.dumps({"numStartups": 3}, indent=2) + "\n",
        home / ".claude" / "settings.json": json.dumps({"theme": "dark"}, indent=2) + "\n",
        home / ".codex" / "config.toml": 'model = "o3"\n',
        home / ".cursor" / "mcp.json": json.dumps({"mcpServers": {}}, indent=2) + "\n",
        hh / "config.yaml": "model:\n  default: qwen3.5-9b\nmemory:\n  provider: mem0\n",
        hh / "inspeximus" / "config.json": json.dumps({"path": str(home / "old-store.json")}) + "\n",
    }
    for p, text in before.items():
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(text.encode("utf-8"))
        if older_bak:
            (p.parent / (p.name + ".bak")).write_bytes(b"another tool's backup of " + p.name.encode())
    rc, lines = _run(rules="no", hermes_provider_change="yes", only="hermes,claude,codex,cursor")
    assert rc == 0, lines
    changed = [p for p, text in before.items() if p.read_bytes() != text.encode("utf-8")]
    assert set(changed) == set(before), [str(p) for p in set(before) - set(changed)]   # control: all changed
    printed = "\n".join(lines)
    lost, clobbered = [], []
    for p, text in before.items():
        baks = [b for b in p.parent.glob(p.name + ".bak*") if b.read_bytes() == text.encode("utf-8")]
        if not baks or not any(f"kept a copy of {p} as {b}" in printed for b in baks):
            lost.append(str(p))
        if older_bak and (p.parent / (p.name + ".bak")).read_bytes() != b"another tool's backup of " + p.name.encode():
            clobbered.append(str(p))
    assert not lost, (lost, printed)
    assert not clobbered, clobbered


def test_a_write_that_changes_nothing_leaves_the_file_and_its_backup_alone(home, tmp_path):
    p = tmp_path / "cfg.json"
    I.write_text_keeping_newlines(p, '{"a": 1}\n')
    I.write_text_keeping_newlines(p, '{"a": 2}\n')
    bak = p.parent / (p.name + ".bak")
    assert bak.read_text(encoding="utf-8") == '{"a": 1}\n'
    stamp = (p.stat().st_mtime_ns, bak.read_bytes(), sorted(x.name for x in tmp_path.iterdir()))
    assert I.write_text_keeping_newlines(p, '{"a": 2}\n') is None       # identical: nothing is rewritten
    assert (p.stat().st_mtime_ns, bak.read_bytes(), sorted(x.name for x in tmp_path.iterdir())) == stamp


def _run(**kw):
    lines = []
    rc = A.run(out=lines.append, **kw)
    return rc, lines


def test_the_setup_decision_says_decision_once(home):
    rc, lines = _run(rules="no", only="claude,codex")
    assert rc == 0, lines
    store = json.loads((home / ".inspeximus" / "shared.json").read_text(encoding="utf-8"))["store"]
    from inspeximus._surface import open_store
    rec = [it for it in open_store(store).items if it.get("key") == A.SETUP_KEY][-1]
    assert rec["text"].count("DECISION:") == 1 and rec["text"].startswith("DECISION: every AI agent"), rec["text"]


@pytest.mark.parametrize("argv", [["-m", "inspeximus", "--version"], ["-m", "inspeximus.cli", "--version"]])
def test_version_answers_through_both_entry_points(argv, tmp_path):
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith("INSPEXIMUS_")}
    env.update(PYTHONPATH=REPO, INSPEXIMUS_NO_UPDATE_CHECK="1", PYTHONIOENCODING="utf-8")
    r = subprocess.run([sys.executable, *argv], cwd=tmp_path, env=env, capture_output=True, text=True, timeout=120)
    assert r.returncode == 0 and r.stdout.strip() == f"inspeximus {__version__}", (r.returncode, r.stdout, r.stderr)


def test_python_dash_m_inspeximus_runs_a_command(tmp_path):
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith("INSPEXIMUS_")}
    env.update(PYTHONPATH=REPO, INSPEXIMUS_NO_UPDATE_CHECK="1", PYTHONIOENCODING="utf-8")
    r = subprocess.run([sys.executable, "-m", "inspeximus", "--path", str(tmp_path / "s.json"), "stats"],
                       cwd=tmp_path, env=env, capture_output=True, text=True, timeout=120)
    assert r.returncode == 0 and "0 total" in r.stdout, (r.returncode, r.stdout, r.stderr)
