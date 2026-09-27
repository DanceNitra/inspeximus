"""Fixed in 3.14.2: the hook stored secrets verbatim and injected them back into the model.

Before 3.14.2 `capture()` stored the first 200 characters of every Bash command and an excerpt of every
Edit/Write body with no redaction. A command such as `export OPENAI_API_KEY=sk-...` or a write to
`.env` lands in the store, and two injection paths then print it into the model's context:
UserPromptSubmit recall ("recent mechanics") and SessionStart ("current known files"). Since 3.14.0
`install --all` shares that store with every other host, so one captured key reaches all of them.

Reproduced 2026-09-27 on 3.14.0 and 3.14.1 with fake keys in a scratch HOME; each test here failed
there and passes with the 3.14.2 fix (`_remember_masked`, `inspeximus/_secrets.py`). The class is
"secret-shaped text reaches the store", so the assertions read the raw bytes of every file the hook
wrote, not only the record text: a fix that redacts the text but keeps the key in `object`, `meta`
or a sidecar would still fail here.

Each test first proves the hook DID write (a control), so a hook that silently stored nothing
cannot pass as "stored no secret".
"""
import io
import json
import os
import subprocess
import sys
from contextlib import redirect_stdout

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import inspeximus.claude_code as cc

OPENAI = "sk-proj-FAKEa1b2c3d4e5f6g7h8i9j0k1l2m3n4o5p6q7r8s9t0"
GITHUB = "ghp_FAKE0123456789abcdefghijklmnopqrstuv"
AWS = "AKIAFAKE0123456789AB"



@pytest.fixture
def project(tmp_path, monkeypatch):
    for k in list(os.environ):
        if k.startswith("INSPEXIMUS_"):
            monkeypatch.delenv(k, raising=False)
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("INSPEXIMUS_NO_UPDATE_CHECK", "1")
    monkeypatch.setenv("INSPEXIMUS_NO_NUDGE", "1")
    proj = tmp_path / "proj"
    proj.mkdir()
    subprocess.run(["git", "init", "-q", str(proj)], check=True)
    return str(proj)


def _store_bytes(proj):
    out = b""
    for dp, _, fs in os.walk(os.path.join(proj, ".inspeximus")):
        for f in fs:
            with open(os.path.join(dp, f), "rb") as fh:
                out += fh.read()
    return out


def _ev(proj, **kw):
    ev = {"cwd": proj, "session_id": "s1",
          "transcript_path": os.path.join(proj, ".claude", "projects", "x", "s1.jsonl")}
    ev.update(kw)
    return ev


def _capture_control(proj):
    """The hook must demonstrably write in this sandbox, or 'no secret stored' means nothing."""
    cc.capture(_ev(proj, hook_event_name="PostToolUse", tool_name="Bash",
                   tool_input={"command": "echo control-marker-7f3a"}))
    if b"control-marker-7f3a" not in _store_bytes(proj):
        pytest.fail("control: the hook stored nothing, so this sandbox cannot test redaction")


def test_a_key_exported_in_a_bash_command_never_reaches_the_store(project):
    _capture_control(project)
    cc.capture(_ev(project, hook_event_name="PostToolUse", tool_name="Bash",
                   tool_input={"command": f"export OPENAI_API_KEY={OPENAI} && python run.py"}))
    raw = _store_bytes(project)
    assert OPENAI.encode() not in raw, "the OpenAI key from a Bash command is on disk in the store"


def test_a_token_written_to_dot_env_never_reaches_the_store(project):
    _capture_control(project)
    cc.capture(_ev(project, hook_event_name="PostToolUse", tool_name="Write",
                   tool_input={"file_path": os.path.join(project, ".env"),
                               "content": f"GITHUB_TOKEN={GITHUB}\nAWS_ACCESS_KEY_ID={AWS}\n"}))
    raw = _store_bytes(project)
    assert GITHUB.encode() not in raw, "the GitHub token written to .env is on disk in the store"
    assert AWS.encode() not in raw, "the AWS key id written to .env is on disk in the store"


def test_a_captured_key_is_never_injected_into_the_prompt(project):
    _capture_control(project)
    cc.capture(_ev(project, hook_event_name="PostToolUse", tool_name="Bash",
                   tool_input={"command": f"export OPENAI_API_KEY={OPENAI} && python run.py"}))
    buf = io.StringIO()
    with redirect_stdout(buf):
        cc.recall(_ev(project, hook_event_name="UserPromptSubmit",
                      prompt="how do I set the OPENAI_API_KEY for run.py"))
    out = buf.getvalue()
    if "[inspeximus]" not in out:
        pytest.fail("control: recall injected nothing, so the injection path was not exercised")
    assert OPENAI not in out, "the recall block printed the captured key into the model's context"


def test_a_captured_token_is_never_injected_at_session_start(project):
    cc.capture(_ev(project, hook_event_name="PostToolUse", tool_name="Write",
                   tool_input={"file_path": os.path.join(project, ".env"),
                               "content": f"GITHUB_TOKEN={GITHUB}\n"}))
    buf = io.StringIO()
    with redirect_stdout(buf):
        cc.session_start(_ev(project, hook_event_name="SessionStart", source="startup",
                             session_id="s2"))
    out = buf.getvalue()
    if ".env" not in out:
        pytest.fail("control: SessionStart listed no known file, so the injection path was not exercised")
    assert GITHUB not in out, "SessionStart printed the .env token into the model's context"
