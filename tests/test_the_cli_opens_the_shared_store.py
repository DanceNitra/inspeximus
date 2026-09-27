"""3.14.3: after `install --all`, the plain CLI and the MCP server open the shared store, not an empty one.

Found 2026-09-27 in the Hermes step 3 run: `inspeximus stats` reported `inspeximus_memory.json: 0 total`,
a new empty file in the working directory, while every agent's memory sat in the store that
~/.inspeximus/shared.json names. The Claude Code hooks read that record; `_surface.resolve_path()`, which
every CLI command and the MCP server go through, did not.

Order, tested here: --path, then INSPEXIMUS_PATH, then an explicit INSPEXIMUS_SCOPE, then the shared store
record, then the old default file. Every run is sandboxed: HOME, USERPROFILE and APPDATA point at a temp
directory, because on 2026-09-27 a test that set only HOME wrote the real ~/.claude.json on Windows.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture
def sandbox(tmp_path):
    home = tmp_path / "home"
    (home / ".inspeximus").mkdir(parents=True)
    work = tmp_path / "somewhere"
    work.mkdir()
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith("INSPEXIMUS_")}
    # No LOCALAPPDATA inside the temporary home: with the Microsoft Store Python that breaks
    # sys.executable two process levels down (measured by AUDIT-A, 2026-09-27). `~` comes from
    # USERPROFILE on Windows and HOME elsewhere, and both are set.
    env.update(HOME=str(home), USERPROFILE=str(home), APPDATA=str(home / "AppData" / "Roaming"),
               PYTHONPATH=REPO, PYTHONIOENCODING="utf-8", INSPEXIMUS_NO_UPDATE_CHECK="1")
    shared = home / ".inspeximus" / "coding_memory.json"
    (home / ".inspeximus" / "shared.json").write_text(json.dumps({"store": str(shared)}), encoding="utf-8")
    return home, work, env, shared


def cli(env, cwd, *args):
    r = subprocess.run([sys.executable, "-m", "inspeximus.cli", *args], cwd=cwd, env=env, capture_output=True,
                       text=True, encoding="utf-8", errors="replace", timeout=120)
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def test_the_cli_reads_and_writes_the_shared_store(sandbox):
    home, work, env, shared = sandbox
    rc, out = cli(env, work, "remember", "the release freeze starts on Friday")
    assert rc == 0, out
    assert shared.exists(), "the write went to the store shared.json names"
    assert not (work / "inspeximus_memory.json").exists(), "no empty store in the working directory"
    rc, out = cli(env, work, "recall", "release freeze")
    assert rc == 0 and "Friday" in out, out
    rc, out = cli(env, work, "stats")
    assert rc == 0 and "coding_memory.json" in out and "1 total" in out, out


def test_an_explicit_path_or_inspeximus_path_still_wins(sandbox):
    home, work, env, shared = sandbox
    cli(env, work, "remember", "shared fact alpha")
    other = work / "mine.json"
    rc, out = cli(env, work, "--path", str(other), "recall", "shared fact alpha")
    assert rc == 0 and "alpha" not in out.split("\n", 1)[-1], out                  # --path wins
    rc, out = cli(dict(env, INSPEXIMUS_PATH=str(other)), work, "stats")
    assert rc == 0 and "mine.json" in out, out                                       # INSPEXIMUS_PATH wins
    rc, out = cli(dict(env, INSPEXIMUS_SCOPE="user"), work, "stats")
    assert rc == 0 and "inspeximus_memory.json" in out, out                         # an explicit scope wins


def test_without_a_record_the_old_default_is_unchanged(sandbox):
    home, work, env, shared = sandbox
    (home / ".inspeximus" / "shared.json").unlink()
    rc, out = cli(env, work, "stats")
    assert rc == 0 and "inspeximus_memory.json" in out, out


def test_the_mcp_server_resolves_the_same_way(sandbox, monkeypatch):
    home, work, env, shared = sandbox
    for k in ("HOME", "USERPROFILE", "APPDATA"):
        monkeypatch.setenv(k, env[k])
    for k in [k for k in os.environ if k.upper().startswith("INSPEXIMUS_")]:
        monkeypatch.delenv(k)
    from inspeximus._surface import resolve_path, resolved_path_source
    assert resolve_path(env={}) == str(shared)
    assert "shared.json" in resolved_path_source(env={})
    assert resolve_path(env={"INSPEXIMUS_PATH": "x.json"}) == "x.json"
