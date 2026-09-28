"""3.15.3: the UPGRADE path of `install --all`, the one every existing user takes.

Found on 2026-09-28, when our own machine moved to 3.15.1 and a friend-flow re-test ran on a second one:
F1  `--only` created ~/.inspeximus/shared.json and so moved every project's hook store: a pin move changed
    the store topology.
F2  the setup decision was written unsigned into a store whose MCP writes are signed.
F3  an existing installer-written hook line counted as "the user's own", so an upgrade left the hooks on the
    old pin and the old PostToolUse matcher.
F5  a bare `inspeximus` resolved to an older copy in another program's venv, and nothing said which ran.
F6  a 9B model printed an ARMED block the installer never wrote; the installer now keeps its own copy.
"""
from __future__ import annotations

import json
import os
import re

import pytest

from inspeximus import __version__
from inspeximus import claude_code as cc
from inspeximus import install as I
from inspeximus import install_all as A

UVX = "C:/tools/uv/uvx.exe" if os.name == "nt" else "/opt/uv/bin/uvx"


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
              "INSPEXIMUS_WRITER_KEY_FILE", "INSPEXIMUS_WRITER_KEY"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("INSPEXIMUS_NO_UPDATE_CHECK", "1")
    monkeypatch.setattr(I, "resolve_runtime", lambda: ("uvx", UVX))
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


def _run(**kw):
    lines = []
    rc = A.run(out=lines.append, **kw)
    return rc, lines


def _shared(h):
    return h / ".inspeximus" / "shared.json"


# ── F5: which installer runs, and never an older one over newer pins ─────────────────────────────────
def test_the_first_line_names_the_interpreter_and_the_version(home):
    rc, lines = _run(rules="no", only="claude,codex")
    assert lines[0].startswith("running ") and __version__ in lines[0] and os.sep in lines[0], lines[0]
    lines = []
    A.check(out=lines.append, only="claude")
    assert lines[0].startswith("running "), lines[0]


@pytest.mark.parametrize("where", ["an agent pin", "shared.json"])
def test_an_older_installer_refuses_and_writes_nothing(home, where):
    if where == "an agent pin":
        (home / ".claude.json").write_text(json.dumps({"mcpServers": {"inspeximus": {
            "command": "uvx", "args": ["--from", "inspeximus[mcp]==99.0.0", "inspeximus-mcp"]}}}), encoding="utf-8")
    else:
        _shared(home).parent.mkdir()
        _shared(home).write_text(json.dumps({"store": str(home / "s.json"), "version": "99.0.0"}), encoding="utf-8")
    before = {p: p.read_bytes() for p in home.rglob("*") if p.is_file()}
    rc, lines = _run(rules="no", only="claude,codex")
    assert rc == 2 and any("older than what is already wired" in ln and "99.0.0" in ln for ln in lines), lines
    assert {p: p.read_bytes() for p in home.rglob("*") if p.is_file()} == before
    rc, lines = _run(rules="no", only="claude,codex", allow_older=True)      # on purpose, it goes ahead
    assert rc == 0, lines


def test_the_same_version_is_not_older(home):
    (home / ".claude.json").write_text(json.dumps({"mcpServers": {"inspeximus": {
        "command": "uvx", "args": ["--from", "inspeximus[mcp]==%s" % __version__, "inspeximus-mcp"]}}}),
        encoding="utf-8")
    rc, lines = _run(rules="no", only="claude,codex")
    assert rc == 0, lines                                             # control for the refusal above


@pytest.mark.skipif(os.name != "nt", reason="a Git Bash path read by Windows is a Windows spelling")
@pytest.mark.parametrize("what", ["--store", "interpreter"])
def test_a_git_bash_path_read_by_windows_is_refused(home, monkeypatch, what):
    drive, rest = os.path.splitdrive(str(home))
    misread = drive + "\\" + drive[0].lower() + rest                  # C:\c\Users\... for C:\Users\...
    if what == "--store":
        rc, lines = _run(rules="no", only="claude", store="/" + drive[0].lower() + (rest + "/s.json").replace("\\", "/"))
    else:
        monkeypatch.setattr(I, "resolve_runtime", lambda: ("python", misread + "\\.inspeximus\\venv\\Scripts\\python.exe"))
        rc, lines = _run(rules="no", only="claude")
    assert rc == 2 and "Git Bash path" in "\n".join(lines) and str(home) in "\n".join(lines), lines
    assert not (home / ".claude.json").exists()


# ── F1: a pin move never switches the store topology ─────────────────────────────────────────────────
def test_only_without_a_shared_store_records_none(home):
    rc, lines = _run(rules="no", only="claude,codex", store=str(home / "chain.json"))
    assert rc == 0, lines
    assert not _shared(home).exists()
    assert not (home / "chain.json").exists(), "no setup decision is written either"
    notice = [i for i, ln in enumerate(lines) if "--only does not record a shared store" in ln]
    table = [i for i, ln in enumerate(lines) if ln.startswith("host ")]
    assert notice and table and notice[0] < table[0], lines          # said before the writes, not after
    assert lines[-1] == "seal: none (--only without a shared store)", lines[-5:]


def test_all_records_it_and_says_so_first(home):
    rc, lines = _run(rules="no", store=str(home / "chain.json"))
    assert rc == 0 and _shared(home).exists(), lines
    assert any("records the shared store" in ln for ln in lines[:3]), lines[:3]


def test_only_with_the_flag_or_an_existing_record_keeps_or_makes_one(home):
    rc, lines = _run(rules="no", only="claude", store=str(home / "chain.json"), shared_store=True)
    assert rc == 0 and _shared(home).exists(), lines
    rc, lines = _run(rules="no", only="codex")                        # an existing record is kept and updated
    assert rc == 0 and json.loads(_shared(home).read_text(encoding="utf-8"))["agents"] == ["Claude Code", "Codex CLI"]


# ── F2: the seal is signed where the store is signed ──────────────────────────────────────────────────
def _setup(store):
    from inspeximus._surface import open_store
    return [it for it in open_store(str(store)).items if it.get("key") == A.SETUP_KEY]


def _signed_store(path):
    pytest.importorskip("cryptography")
    from cryptography.hazmat.primitives import serialization as s
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from inspeximus import Inspeximus
    key = Ed25519PrivateKey.generate().private_bytes(s.Encoding.Raw, s.PrivateFormat.Raw, s.NoEncryption()).hex()
    m = Inspeximus(path=str(path), writer_key=key)
    m.remember("a signed fact")
    m.flush()
    return key


def test_an_unsigned_store_gets_the_setup_decision_as_before(home):
    rc, lines = _run(rules="no", store=str(home / "chain.json"))
    rec = _setup(home / "chain.json")
    assert rc == 0 and len(rec) == 1 and not rec[0].get("attested_key"), lines
    assert re.fullmatch(r"seal: [0-9a-f]+ [0-9a-f]{12}", lines[-1]), lines[-1]


def test_a_signed_store_with_a_key_gets_it_signed(home, tmp_path, monkeypatch):
    store = home / "chain.json"
    key = _signed_store(store)
    kf = tmp_path / "writer.key"
    kf.write_text(key, encoding="utf-8")
    monkeypatch.setenv("INSPEXIMUS_WRITER_KEY_FILE", str(kf))
    rc, lines = _run(rules="no", store=str(store))
    rec = _setup(store)
    assert rc == 0 and len(rec) == 1 and rec[0].get("attested_key"), lines


def test_a_signed_store_without_a_key_gets_no_unsigned_record(home):
    store = home / "chain.json"
    _signed_store(store)
    rc, lines = _run(rules="no", store=str(store))
    assert rc == 0 and _setup(store) == [], lines
    assert lines[-1] == "seal: none (signed store, no writer key)", lines[-5:]
    assert any("was not written" in ln and "writer key" in ln for ln in lines), lines


# ── F3: the installer updates its own earlier hook lines, and only those ─────────────────────────────
def _settings(home, commands, post_matcher=None):
    hooks = {}
    for evt, cmd in commands.items():
        g = {"hooks": [{"type": "command", "command": cmd}]}
        if evt == "PreToolUse":
            g["matcher"] = "Bash|Write|Edit|MultiEdit|NotebookEdit"
        if evt == "PostToolUse" and post_matcher:
            g["matcher"] = post_matcher
        hooks[evt] = [g]
    (home / ".claude" / "settings.json").write_text(json.dumps({"theme": "dark", "hooks": hooks}), encoding="utf-8")


EVENTS = ("PreToolUse", "PostToolUse", "UserPromptSubmit", "SessionStart", "SessionEnd")


def test_installer_written_hooks_move_to_this_version(home):
    old = UVX + " --from inspeximus==3.14.3 python -m inspeximus.claude_code"
    _settings(home, {e: old for e in EVENTS})
    rc, lines = _run(rules="no", only="claude")
    assert rc == 0, lines
    s = json.loads((home / ".claude" / "settings.json").read_text(encoding="utf-8"))
    want = I.hook_command("uvx", UVX)
    assert {h["command"] for e in EVENTS for g in s["hooks"][e] for h in g["hooks"]} == {want}
    assert s["hooks"]["PostToolUse"][0]["matcher"] == cc._EVENT_HOOK["PostToolUse"]["matcher"]
    assert s["theme"] == "dark"
    assert any("hooks this installer wrote before are updated" in ln for ln in lines), lines


def test_a_hand_written_hook_line_is_kept_and_named(home):
    old = UVX + " --from inspeximus==3.14.3 python -m inspeximus.claude_code"
    cmds = {e: old for e in EVENTS}
    cmds["UserPromptSubmit"] = "python -m inspeximus.claude_code"          # a person wrote this one
    _settings(home, cmds)
    rc, lines = _run(rules="no", only="claude")
    s = json.loads((home / ".claude" / "settings.json").read_text(encoding="utf-8"))
    assert s["hooks"]["UserPromptSubmit"][0]["hooks"][0]["command"] == "python -m inspeximus.claude_code"
    assert s["hooks"]["SessionStart"][0]["hooks"][0]["command"] == I.hook_command("uvx", UVX)
    assert any("kept a hook line written by hand" in ln and "UserPromptSubmit" in ln for ln in lines), lines
    assert sum(1 for e in EVENTS for g in s["hooks"][e] for h in g["hooks"]
               if "inspeximus.claude_code" in h["command"]) == 5, "no event runs twice"


def test_a_venv_interpreter_line_is_the_installers_and_a_system_one_is_the_users(home):
    """PC2's install wrote the interpreter form from ~/.inspeximus/venv; a person's /opt/py line stays theirs."""
    venv = str(home / ".inspeximus" / "venv" / "Scripts" / "python.exe").replace("\\", "/")
    theirs = ("C:/opt/py/python.exe" if os.name == "nt" else "/opt/py/bin/python") + " -m inspeximus.claude_code"
    cmds = {e: venv + " -m inspeximus.claude_code" for e in EVENTS}
    cmds["SessionEnd"] = theirs
    _settings(home, cmds)
    rc, lines = _run(rules="no", only="claude")
    s = json.loads((home / ".claude" / "settings.json").read_text(encoding="utf-8"))
    assert s["hooks"]["SessionStart"][0]["hooks"][0]["command"] == I.hook_command("uvx", UVX), lines
    assert s["hooks"]["SessionEnd"][0]["hooks"][0]["command"] == theirs              # control: kept
    assert any("kept a hook line written by hand" in ln and "SessionEnd" in ln for ln in lines), lines


def test_the_install_creates_the_folder_of_the_store_it_names(home):
    """The MCP server refuses a store in a missing folder, so the install that names one must create it."""
    from inspeximus._surface import store_location_problem
    store = home / "memories" / "team.json"
    assert store_location_problem(str(store)), "control: before the install the path is refused"
    rc, lines = _run(rules="no", only="claude", store=str(store))
    assert rc == 0 and store.parent.is_dir(), lines
    assert store_location_problem(str(store)) is None
    rc, lines = _run(rules="no", only="claude", store=str(home / "dry" / "s.json"), dry_run=True)
    assert not (home / "dry").exists(), "a dry run creates nothing"


def test_an_update_that_did_not_hold_is_reported(home, monkeypatch):
    old = UVX + " --from inspeximus==3.14.3 python -m inspeximus.claude_code"
    _settings(home, {e: old for e in EVENTS})
    p = I.plan("claude", store_path=str(home / "s.json"))
    assert p["hooks"]["updated"], p["hooks"]
    real, settings = I._write_json, home / ".claude" / "settings.json"
    unchanged = json.loads(settings.read_text(encoding="utf-8"))

    def keeps_the_old_hooks(path, data):                    # the settings write lands without our update
        real(path, unchanged if str(path) == str(settings) else data)
    monkeypatch.setattr(I, "_write_json", keeps_the_old_hooks)
    ok, msg = I.apply(p)
    assert not ok and "hooks not updated" in msg, msg


# ── F6: the installer's own copy of the block ─────────────────────────────────────────────────────────
def test_the_block_is_saved_with_the_time(home):
    rc, lines = _run(rules="no", store=str(home / "chain.json"))
    f = home / ".inspeximus" / "ARMED.txt"
    text = f.read_text(encoding="utf-8").splitlines()
    assert re.match(r"written \d{4}-\d\d-\d\d \d\d:\d\d:\d\d .* by running ", text[0]), text[0]
    block = lines[lines.index("") + 1:]
    assert text[1:] == block, (text, block)
    assert any(str(f) in ln for ln in lines), "the run says where the copy is"


def test_a_dry_run_and_a_check_write_no_copy(home):
    _run(rules="no", dry_run=True)
    A.check(out=lambda s: None, only="claude")
    assert not (home / ".inspeximus" / "ARMED.txt").exists()
