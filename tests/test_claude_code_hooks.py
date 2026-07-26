"""The Claude Code hook installer, which had 111 uncovered body lines and destroyed user config.

`inspeximus/claude_code.py` is first-party, needs no optional dependency, and `install`/`uninstall` write
into a file we do NOT own: the user's `./.claude/settings.json`. Seven of its functions had zero executed
body lines.

Two defects, both measured before the fix:

* `install` fell back to `cfg = {}` when the file could not be parsed and then WROTE it. A trailing comma
  -- the usual hand-edit mistake -- and the user lost `model`, `permissions`, and their OWN hooks. From a
  function whose docstring says "merging, not clobbering".
* `uninstall` called bare `json.load` with no guard, so it RAISED on the same file: a user whose settings
  had been broken could not even undo the install.

Both now refuse and change nothing, and the write goes through a temp file + `os.replace`, because
`json.dump(open(p, "w"))` truncates the target the instant it opens it.

The hook entry points are fail-open by design ("any error exits 0 with no output, so the hook never blocks
the agent"), which is right for a hook and dangerous for a test: almost anything passes if you only assert
"did not raise". These assert what actually lands in the store.
"""
import io
import json
import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import inspeximus.claude_code as cc

USER_SETTINGS = {
    "model": "opus",
    "permissions": {"allow": ["Bash(git:*)"]},
    "hooks": {"PostToolUse": [{"matcher": "Edit",
                               "hooks": [{"type": "command", "command": "my-linter"}]}]},
}


def _project(settings=None, raw=None):
    """A temp project dir with a .claude/settings.json; `raw` writes bytes verbatim (for malformed cases)."""
    d = tempfile.mkdtemp()
    os.makedirs(os.path.join(d, ".claude"), exist_ok=True)
    p = os.path.join(d, ".claude", "settings.json")
    if raw is not None:
        io.open(p, "w", encoding="utf-8").write(raw)
    elif settings is not None:
        io.open(p, "w", encoding="utf-8").write(json.dumps(settings, indent=2))
    return d, p


def _read(p):
    return io.open(p, encoding="utf-8").read()


# ── never destroy a config we could not read ────────────────────────────────────────────────────────
def test_install_refuses_an_unparseable_settings_file_and_changes_nothing(capsys):
    """THE defect: a trailing comma cost the user their whole settings file."""
    d, p = _project(raw='{\n  "model": "opus",\n  "permissions": {"allow": ["Bash(git:*)"]},\n}')
    before = _read(p)

    assert cc.install(cwd=d) is False
    assert _read(p) == before, "an unreadable config must be left EXACTLY as it was"
    assert "not valid JSON" in capsys.readouterr().out


def test_uninstall_refuses_an_unparseable_settings_file_instead_of_raising(capsys):
    """Undo has to work on the broken file too -- a bare json.load used to raise here."""
    d, p = _project(raw="{ not json at all")
    before = _read(p)

    assert cc.uninstall(cwd=d) is False          # must not raise
    assert _read(p) == before
    assert "not valid JSON" in capsys.readouterr().out


def test_uninstall_on_a_project_with_no_settings_is_a_no_op():
    d = tempfile.mkdtemp()
    assert cc.uninstall(cwd=d) is False
    assert not os.path.exists(os.path.join(d, ".claude", "settings.json"))


# ── merging, as the docstring promises ──────────────────────────────────────────────────────────────
def test_install_keeps_every_unrelated_setting_and_the_users_own_hook(capsys):
    d, p = _project(USER_SETTINGS)
    assert cc.install(cwd=d) is True
    capsys.readouterr()

    raw = _read(p)
    cfg = json.loads(raw)
    assert cfg["model"] == "opus"
    assert cfg["permissions"] == {"allow": ["Bash(git:*)"]}
    assert "my-linter" in raw, "the user's own hook must survive"
    assert any(m in raw for m in cc._HOOK_MARKERS), "and ours must be added"


def test_install_is_idempotent(capsys):
    d, p = _project(USER_SETTINGS)
    cc.install(cwd=d)
    cc.install(cwd=d)
    cc.install(cwd=d)
    capsys.readouterr()

    hooks = json.loads(_read(p))["hooks"]
    ours = [h for h in hooks["PostToolUse"] if any(m in json.dumps(h) for m in cc._HOOK_MARKERS)]
    assert len(ours) == 1, f"three installs must leave ONE of our blocks: {hooks['PostToolUse']}"
    assert len(hooks["PostToolUse"]) == 2, "and the user's linter is still there beside it"


def test_install_creates_the_directory_when_the_project_has_none(capsys):
    d = tempfile.mkdtemp()
    assert cc.install(cwd=d) is True
    capsys.readouterr()
    assert os.path.exists(os.path.join(d, ".claude", "settings.json"))


def test_uninstall_removes_only_our_hooks(capsys):
    d, p = _project(USER_SETTINGS)
    cc.install(cwd=d)
    cc.uninstall(cwd=d)
    capsys.readouterr()

    raw = _read(p)
    cfg = json.loads(raw)
    assert not any(m in raw for m in cc._HOOK_MARKERS), "ours must be gone"
    assert "my-linter" in raw, "the user's hook must NOT be"
    assert cfg["model"] == "opus" and "permissions" in cfg


def test_install_then_uninstall_round_trips_the_users_hooks():
    """The strongest statement: after install+uninstall the user's own hook list is what it was."""
    d, p = _project(USER_SETTINGS)
    before = json.loads(_read(p))["hooks"]["PostToolUse"]
    cc.install(cwd=d)
    cc.uninstall(cwd=d)
    assert json.loads(_read(p))["hooks"]["PostToolUse"] == before


def test_the_write_is_atomic_and_leaves_no_temp_file(capsys):
    d, p = _project(USER_SETTINGS)
    cc.install(cwd=d)
    capsys.readouterr()
    leftovers = [f for f in os.listdir(os.path.dirname(p)) if f.endswith(".inspeximus-tmp")]
    assert not leftovers, leftovers
    json.loads(_read(p))                          # and the result parses


# ── the hook entry points actually store something ──────────────────────────────────────────────────
@pytest.fixture()
def project(monkeypatch):
    d = tempfile.mkdtemp()
    monkeypatch.chdir(d)
    return d


def test_capture_writes_a_tool_event_into_the_project_store(project, capsys):
    cc.capture({"hook_event_name": "PostToolUse", "tool_name": "Write",
                "tool_input": {"file_path": "src/app.py", "content": "def main(): pass"}})
    capsys.readouterr()
    store = os.path.join(project, ".inspeximus", "coding_memory.json")
    assert os.path.exists(store), "the capture hook must persist to ./.inspeximus/coding_memory.json"
    # The store normalises the path to the OS separator (`file:src\app.py` on Windows), so a test
    # written with forward slashes fails there for a reason that has nothing to do with the behaviour.
    assert "app.py" in _read(store)


def test_capture_supersedes_the_earlier_state_of_the_same_file(project, capsys):
    for content in ("def main(): pass", "def main(): return 1"):
        cc.capture({"hook_event_name": "PostToolUse", "tool_name": "Write",
                    "tool_input": {"file_path": "src/app.py", "content": content}})
    capsys.readouterr()

    rows = json.loads(_read(os.path.join(project, ".inspeximus", "coding_memory.json")))
    rows = rows if isinstance(rows, list) else rows.get("items", rows.get("records", []))
    active = [r for r in rows if r.get("status") == "active" and "app.py" in str(r.get("text", ""))]
    assert len(active) == 1, f"the same file must be keyed, not appended twice: {len(active)} active"


def test_capture_ignores_an_event_with_no_file(project, capsys):
    cc.capture({"hook_event_name": "PostToolUse", "tool_name": "Read", "tool_input": {}})
    out = capsys.readouterr().out
    assert out == "" or "src" not in out


def test_recall_prints_something_it_stored(project, capsys):
    cc.capture({"hook_event_name": "PostToolUse", "tool_name": "Write",
                "tool_input": {"file_path": "src/payments.py", "content": "def charge(): ..."}})
    capsys.readouterr()
    cc.recall({"hook_event_name": "UserPromptSubmit", "prompt": "what is in payments.py"})
    assert "payments" in capsys.readouterr().out


def test_session_start_digests_the_known_files(project, capsys):
    cc.capture({"hook_event_name": "PostToolUse", "tool_name": "Write",
                "tool_input": {"file_path": "src/models.py", "content": "class User: ..."}})
    capsys.readouterr()
    cc.session_start({"hook_event_name": "SessionStart"})
    assert "models.py" in capsys.readouterr().out


@pytest.mark.parametrize("ev", [{}, {"hook_event_name": "PostToolUse"}, {"tool_input": None},
                                {"hook_event_name": "PostToolUse", "tool_input": "not-a-dict"}])
def test_the_hooks_are_fail_open_on_malformed_events(project, ev, capsys):
    """Documented: "any error exits 0 with no output, so the hook never blocks the agent". A hook that
    raises would break the user's Claude Code session, not just its own feature."""
    cc.capture(ev)
    cc.recall(ev)
    cc.session_start(ev)
    capsys.readouterr()


def test_a_failed_write_leaves_the_original_settings_intact(monkeypatch):
    """Atomicity, tested the only way it can be: make the write FAIL and check what survives.

    `json.dump(open(p, "w"))` truncates the target the instant it opens it, so a crash, a full disk or a
    permission error mid-write leaves a half-written settings.json. Asserting "no leftover temp file" does
    not discriminate -- the direct-write version passes that too, which is why this test exists.
    """
    d, p = _project(USER_SETTINGS)
    before = _read(p)

    real_dump = json.dump

    def exploding_dump(obj, fh, **kw):
        real_dump(obj, fh, **kw)          # write it, so a truncating implementation is already committed
        raise OSError("no space left on device")

    monkeypatch.setattr(json, "dump", exploding_dump)
    with pytest.raises(OSError):
        cc.install(cwd=d)

    monkeypatch.undo()
    assert _read(p) == before, "a failed write must not touch the file it was going to replace"
    json.loads(_read(p))                  # and it still parses


def test_uninstall_does_not_propagate_a_json_error(capsys):
    """Explicit, because a mutation that removes the guard turns this into an exception rather than a
    failing assertion -- and a runner that only greps for FAILED reports it as surviving."""
    d, p = _project(raw="{{{ definitely not json")
    try:
        result = cc.uninstall(cwd=d)
    except Exception as e:                 # noqa: BLE001 - the point is that nothing escapes
        raise AssertionError(f"uninstall must not raise on a malformed config: {type(e).__name__}: {e}")
    assert result is False
    capsys.readouterr()
