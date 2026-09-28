"""3.15.3 (AUDIT-A A-11): a store path in a directory that does not exist is refused, and no read creates one.

A mistyped INSPEXIMUS_PATH, or a Git Bash `/c/...` path that Windows reads as `C:\\c\\...`, opened a new,
empty store: an MCP `recall` answered [] with `isError: false`, and that read CREATED the directory. Each
surface now refuses in its own way, and each test below carries a control on a correct path:

  core     opening and reading create nothing; the first write creates the directory
  MCP      the server still starts, and every tool call answers isError with the path and the fix
  CLI      a non-zero exit with the same message
  hooks    never block a prompt: exit 0, nothing on stdout
  check    warns about a Git Bash copy of the home (C:\\c\\Users\\<you>)
A store file that does not exist yet in a directory that does is "no store yet", said in the MCP result.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _env(**extra):
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith("INSPEXIMUS_")}
    env.update(PYTHONPATH=REPO, INSPEXIMUS_NO_UPDATE_CHECK="1", PYTHONIOENCODING="utf-8", **extra)
    return env


# ── core ───────────────────────────────────────────────────────────────────────────────────────────────
def test_the_directory_is_made_by_the_first_write_not_by_opening(tmp_path):
    from inspeximus import Inspeximus
    store = tmp_path / "new" / "deep" / "memory.json"
    m = Inspeximus(path=str(store))
    m.recall("anything", k=3)
    m.flush()
    assert not store.parent.exists(), "opening, reading or an empty flush created the directory"
    m.remember("the first fact")
    m.flush()
    assert store.exists(), "control: the first write creates the directory and the file"


@pytest.mark.parametrize("receipts", [False, True])
@pytest.mark.parametrize("fmt", ["rows", "json"])
def test_opening_and_reading_an_unwritten_store_writes_nothing(tmp_path, monkeypatch, fmt, receipts):
    """The invariant the removed empty-flush guard stood for, pinned on both formats, receipts on and off:
    opening, reading and flushing a store that has no file create neither its folder nor any file."""
    from inspeximus import Inspeximus
    from inspeximus import sqlite_store as _rows
    if fmt == "json":
        monkeypatch.setenv("INSPEXIMUS_STORE_FORMAT", "json")
    else:
        monkeypatch.delenv("INSPEXIMUS_STORE_FORMAT", raising=False)
    missing = tmp_path / "missing" / "memory.json"
    present = tmp_path / "present" / "memory.json"
    present.parent.mkdir()
    for store in (missing, present):
        m = Inspeximus(path=str(store), receipts=receipts)
        m.recall("anything", k=3)
        m.memory_report()
        m.verify_writes()
        m.state_digest()
        m.flush()
        del m
    assert not missing.parent.exists(), "a read created the store's folder"
    assert list(present.parent.iterdir()) == [], "a read wrote into an existing folder"
    m = Inspeximus(path=str(present), receipts=receipts)                   # control: a write lands, in this format
    m.remember("the first fact")
    m.flush()
    assert present.exists() and _rows.looks_like_sqlite(present) == (fmt == "rows")
    assert (present.parent / "memory.json.receipts.json").exists() == receipts


def test_the_refusal_suggests_a_near_match(tmp_path):
    from inspeximus._surface import store_location_problem
    (tmp_path / "project").mkdir()
    msg = store_location_problem(str(tmp_path / "projetc" / "memory.json"))
    assert msg and "no such directory" in msg and str(tmp_path / "project" / "memory.json") in msg, msg
    far = store_location_problem(str(tmp_path / "zzzzzz" / "memory.json"))
    assert far and "Did you mean" not in far, far                                      # control: no match, no guess
    assert store_location_problem(str(tmp_path / "project" / "memory.json")) is None  # control: an existing folder
    (tmp_path / "notes.txt").write_text("a file", encoding="utf-8")
    under_a_file = store_location_problem(str(tmp_path / "notes.txt" / "memory.json"))
    assert under_a_file and "is a file, not a directory" in under_a_file and "NOT PERSISTED" in under_a_file


# ── MCP ────────────────────────────────────────────────────────────────────────────────────────────────
class _Mcp:
    def __init__(self, env, cwd):
        self.p = subprocess.Popen([sys.executable, "-m", "inspeximus.mcp_server"], stdin=subprocess.PIPE,
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env, cwd=str(cwd),
                                  text=True, encoding="utf-8", errors="replace")
        self.n = 0
        assert self.call("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                        "clientInfo": {"name": "test", "version": "0"}}), "the server did not start"
        self.p.stdin.write(json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n")
        self.p.stdin.flush()

    def call(self, method, params):
        self.n += 1
        self.p.stdin.write(json.dumps({"jsonrpc": "2.0", "id": self.n, "method": method, "params": params}) + "\n")
        self.p.stdin.flush()
        deadline = time.time() + 120
        while time.time() < deadline:
            line = self.p.stdout.readline()
            if not line:
                return None
            try:
                m = json.loads(line)
            except ValueError:
                continue
            if m.get("id") == self.n:
                return m
        return None

    def tool(self, name, **args):
        r = self.call("tools/call", {"name": name, "arguments": args})
        res = (r or {}).get("result") or {}
        return bool(res.get("isError")), " ".join(c.get("text", "") for c in res.get("content") or [])

    def close(self):
        try:
            self.p.stdin.close()
            self.p.wait(timeout=20)
        except Exception:                                   # noqa: BLE001
            self.p.kill()


@pytest.fixture
def mcp_ok():
    pytest.importorskip("mcp")


def test_the_mcp_server_starts_and_every_call_names_the_missing_directory(mcp_ok, tmp_path):
    bad = tmp_path / "no_such_dir" / "memory.json"
    s = _Mcp(_env(INSPEXIMUS_PATH=str(bad)), tmp_path)
    try:
        for name, args in (("recall", {"query": "anything"}), ("remember", {"text": "a fact"})):
            err, text = s.tool(name, **args)
            assert err and "no such directory" in text and "no_such_dir" in text, (name, text)
    finally:
        s.close()
    assert not bad.parent.exists(), "the refused path was created anyway"


def test_a_store_not_written_yet_says_no_store_yet_and_then_answers(mcp_ok, tmp_path):
    store = tmp_path / "memory.json"
    s = _Mcp(_env(INSPEXIMUS_PATH=str(store)), tmp_path)
    try:
        err, text = s.tool("recall", query="anything")
        assert not err and "no_store_yet" in text, text
        err, text = s.tool("remember", text="the release freeze starts on Friday")
        assert not err, text
        err, text = s.tool("recall", query="release freeze")
        assert not err and "Friday" in text and "no_store_yet" not in text, text      # control
    finally:
        s.close()


# ── CLI ────────────────────────────────────────────────────────────────────────────────────────────────
def test_the_cli_exits_non_zero_and_creates_nothing(tmp_path):
    bad = tmp_path / "no_such_dir" / "s.json"
    r = subprocess.run([sys.executable, "-m", "inspeximus.cli", "--path", str(bad), "recall", "x"], env=_env(),
                       cwd=str(tmp_path), capture_output=True, text=True, encoding="utf-8", errors="replace")
    assert r.returncode == 2 and "no such directory" in r.stderr, (r.returncode, r.stderr)
    assert not bad.parent.exists()
    ok = subprocess.run([sys.executable, "-m", "inspeximus.cli", "--path", str(tmp_path / "s.json"), "recall", "x"],
                        env=_env(), cwd=str(tmp_path), capture_output=True, text=True, encoding="utf-8")
    assert ok.returncode == 0, ok.stderr                                              # control


# ── hooks ──────────────────────────────────────────────────────────────────────────────────────────────
def _hook(env, cwd, event):
    ev = {"hook_event_name": event, "prompt": "what did we decide", "cwd": str(cwd), "session_id": "s1"}
    return subprocess.run([sys.executable, "-m", "inspeximus.claude_code"], input=json.dumps(ev), env=env,
                          cwd=str(cwd), capture_output=True, text=True, encoding="utf-8", errors="replace",
                          timeout=120)


def test_a_hook_on_a_refused_store_never_blocks_the_prompt(tmp_path):
    proj = tmp_path / "proj"
    proj.mkdir()
    r = _hook(_env(INSPEXIMUS_CODING_STORE=str(tmp_path / "no_such_dir")), proj, "UserPromptSubmit")
    assert r.returncode == 0 and r.stdout.strip() == "", (r.returncode, r.stdout, r.stderr)
    assert "no such directory" in r.stderr, r.stderr
    ok = _hook(_env(), proj, "UserPromptSubmit")                                      # control: the project store
    assert ok.returncode == 0 and "no such directory" not in ok.stderr, ok.stderr


# ── install --check ────────────────────────────────────────────────────────────────────────────────────
@pytest.mark.skipif(os.name != "nt", reason="a Git Bash copy of the home is a Windows spelling")
def test_check_warns_about_a_git_bash_copy_of_the_home(tmp_path, monkeypatch):
    from inspeximus import install_all as A
    home = tmp_path / "home"
    home.mkdir()
    for k in ("HOME", "USERPROFILE"):
        monkeypatch.setenv(k, str(home))
    drive, rest = os.path.splitdrive(str(home))
    copy = drive + "\\" + drive[0].lower() + rest
    top = copy
    while not os.path.exists(os.path.dirname(top)):
        top = os.path.dirname(top)
    lines = []
    A.check(out=lines.append, only="claude")
    assert not any("copy of your home" in ln for ln in lines), lines                  # control: no copy, no warning
    os.makedirs(os.path.join(copy, ".inspeximus", "venv"))
    try:
        lines = []
        A.check(out=lines.append, only="claude")
        assert any("copy of your home" in ln and copy in ln and str(home) in ln for ln in lines), lines
    finally:
        shutil.rmtree(top, ignore_errors=True)
