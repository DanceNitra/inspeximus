"""The installer must install the guard it has, not four of the five hooks.

WHY THIS EXISTS. The PreToolUse handler was written, tested and committed on 2026-08-11 with the
outreach pattern (`gh issue comment`) explicitly in it -- and it never fired once. `install()`
iterated ("PostToolUse", "UserPromptSubmit", "SessionStart", "SessionEnd"): PreToolUse was not in the
tuple, so every settings.json it had ever written carried four hooks and no guard. Three outreach
posts went out without the pre-publish gate in a single day while a guard for exactly that sat in the
source, uninvoked, and the reason given for each was that I had forgotten -- when the truth was that
nothing could have reminded me.

A mechanism is not shipped when it is written. It is shipped when something invokes it. This file is
the something.

AND THEN IT WAS INVOKED, AND STILL REACHED NOBODY. 2026-08-12, live on the host: the guard fired on a
real `gh issue comment` call -- the host's own transcript records hookEvent=PreToolUse, exitCode=0,
stdout = the warning -- and across the same session it fired 3 times and its text entered the model's
context 0 times. Claude Code injects hook stdout for UserPromptSubmit and SessionStart; for PreToolUse
at exit 0 it only logs it. So "it fires" was the wrong question, and these tests now assert the
channel, not the firing: a warning must be emitted as hookSpecificOutput.additionalContext (measured
to arrive) and a block as exit 2 + stderr (measured to arrive AND to stop the call, which
permissionDecision:"ask" does not do here -- this deployment runs permissionMode=bypassPermissions).
"""
import json
import os
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

ENV = {**os.environ, "PYTHONPATH": ROOT, "PYTHONIOENCODING": "utf-8"}


def _install(tmp_path):
    subprocess.run([sys.executable, "-m", "inspeximus.claude_code", "--install"],
                   cwd=str(tmp_path), capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=180,
                   env=ENV, check=True)
    with open(os.path.join(str(tmp_path), ".claude", "settings.json"), encoding="utf-8") as fh:
        return json.load(fh)


def _fire(command):
    ev = {"hook_event_name": "PreToolUse", "tool_name": "Bash",
          "tool_input": {"command": command}}
    return subprocess.run([sys.executable, "-m", "inspeximus.claude_code"], input=json.dumps(ev),
                          capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=180, env=ENV)


def _delivered(r):
    """The text the MODEL actually receives -- which is not the same as the text that was printed.

    A warning arrives only inside hookSpecificOutput.additionalContext; a block arrives only on
    stderr with exit 2. Anything else the handler emits is written to a log and read by nobody, so
    this helper deliberately CANNOT see it: a test that reads raw stdout would have passed on every
    day the guard was silent in practice.
    """
    if r.returncode == 2:
        return r.stderr
    if not r.stdout.strip():
        return ""
    payload = json.loads(r.stdout)  # not valid JSON -> the channel is wrong -> this raises, correctly
    return payload["hookSpecificOutput"]["additionalContext"]


def test_install_writes_the_pretooluse_guard(tmp_path):
    hooks = _install(tmp_path).get("hooks", {})
    assert "PreToolUse" in hooks, (
        "install() wrote %r and no PreToolUse. The guard is in the source and nothing invokes it -- "
        "which is how three outreach posts went out ungated in one day." % sorted(hooks))


def test_the_pretooluse_entry_carries_a_matcher(tmp_path):
    """A matcher SCOPES the guard. It is not what makes it fire.

    This docstring used to say that without a matcher the hook registers and never fires. That was
    never measured, and it is false: a matcher-less PreToolUse entry in a project settings.json fired
    on Write twice in the session of 2026-08-12, which is how the claim was caught. The real reason to
    carry one is cost and noise -- matcher-less, the handler runs on EVERY tool call at ~0.77 s each
    (measured, silent path included, 3x3 runs), because it loads the store before discovering it has
    nothing to say. A false reason inside a passing test is how a wrong belief gets pinned.
    """
    from inspeximus.claude_code import _PRE_TOOLS
    entry = _install(tmp_path)["hooks"]["PreToolUse"][0]
    assert entry.get("matcher"), "PreToolUse without a matcher runs on every tool call: %r" % entry
    assert set(entry["matcher"].split("|")) == set(_PRE_TOOLS), (
        "the matcher and the handler's own tool list have drifted, which is silent in BOTH "
        "directions: a tool the handler inspects but the matcher omits is never consulted, and one "
        "the matcher admits but the handler ignores pays a process launch to return nothing. "
        "matcher=%r handler=%r" % (entry["matcher"], _PRE_TOOLS))


def test_install_does_not_clobber_settings_it_did_not_write(tmp_path):
    """It edits a file we do not own. The user's model and theme must survive."""
    d = os.path.join(str(tmp_path), ".claude")
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "settings.json"), "w", encoding="utf-8") as fh:
        json.dump({"model": "opus[1m]", "theme": "dark"}, fh)
    cfg = _install(tmp_path)
    assert cfg["model"] == "opus[1m]" and cfg["theme"] == "dark"
    assert "PreToolUse" in cfg["hooks"]


@pytest.mark.parametrize("command,needle", [
    ("gh issue comment 1 --repo a/b --body-file x.md", "SOMEONE ELSE'S REPOSITORY"),
    ("git add -A", "deleted real work"),
])
def test_the_guard_reaches_the_model_on_what_it_guards(command, needle):
    """Not `is it printed` -- `does it arrive`. _delivered() reads only the two channels measured to
    reach the model, so a regression to bare stdout fails here instead of passing quietly."""
    assert needle in _delivered(_fire(command)), "not delivered for %r" % command


def test_a_warning_is_emitted_on_the_channel_that_arrives():
    """The regression test for the actual defect of 2026-08-12: correct text, wrong channel."""
    r = _fire("git add -A")
    assert r.returncode == 0, "a warning must not block: %r" % r.stderr
    payload = json.loads(r.stdout)          # bare text here is the defect, and this raises on it
    hso = payload["hookSpecificOutput"]
    assert hso["hookEventName"] == "PreToolUse"
    assert hso["additionalContext"].startswith("[inspeximus] before this action:")


def test_outreach_is_BLOCKED_not_merely_announced():
    """Outreach is the one class where the cost is external and irreversible, and where the owner's
    rule is absolute. A reminder was not enough: three posts went out ungated on 2026-08-11."""
    r = _fire("gh issue comment 1462 --repo deepseek-ai/DeepSeek-V3 --body-file draft.md")
    assert r.returncode == 2, "outreach must stop the call, got exit %d" % r.returncode
    assert "SOMEONE ELSE'S REPOSITORY" in r.stderr
    assert r.stdout.strip() == "", "at exit 2 the host reads stderr; stdout is thrown away"


def test_the_block_can_be_overridden_once_the_owner_has_approved():
    """A gate with no way through is a gate that gets removed. The override is weak on purpose -- it
    buys an explicit, auditable act in place of an omission, not a security boundary."""
    r = _fire("AGORA_OUTREACH_APPROVED=1 gh issue comment 1462 --repo a/b --body-file draft.md")
    assert r.returncode == 0, "an approved post must go through, got exit 2"
    text = _delivered(r)
    assert "Blocked" not in text, (
        "the override path replayed the block text: it announced 'Blocked -- re-issue with the "
        "override' about a call that was not blocked and already carried it. Measured live "
        "2026-08-12. A guard that misdescribes what it just did is one you stop reading.")
    assert "AGORA_OUTREACH_APPROVED" in text and "OWNER approved" in text


@pytest.mark.parametrize("command", [
    "gh issue comment 1462 --repo a/b --body-file draft.md",           # bare
    "cat draft.md | gh issue comment 1462 --repo a/b --body-file -",   # after a pipe
    "cd /repo && gh pr comment 7 --body hi",                           # after &&
    "GH_TOKEN=x gh issue comment 1 --body hi",                         # behind an env assignment
])
def test_a_post_in_COMMAND_POSITION_blocks_however_it_is_reached(command):
    assert _fire(command).returncode == 2, "outreach slipped through: %r" % command


@pytest.mark.parametrize("command", [
    "gh api repos/o/r/issues/comments/123",                            # GET: reading a comment back
    "gh api repos/o/r/issues/1/comments --jq '.[].body'",              # GET: listing them
    # A read chained to an unrelated command that happens to carry a write-looking flag. The first
    # version scanned the whole line and refused this, because `git commit -F` has a -F.
    "gh api repos/o/r/issues/comments/123 --jq .body && git commit -F -",
    "gh api repos/o/r/issues/comments/9 ; cp -f a b",
])
def test_CONTROL_reading_a_comment_is_not_posting_one(command):
    """Measured minutes after the guard's first real use: it blocked us READING BACK the comment we
    had just posted, to verify it. `gh api ... comments` is a GET unless a write flag says otherwise,
    and a guard that blocks reads teaches you to route around it."""
    r = _fire(command)
    assert r.returncode == 0, "a read must not block: %r" % command
    assert "does not appear to" in _delivered(r)


@pytest.mark.parametrize("command", [
    "gh api repos/o/r/issues/1/comments -X POST -f body=hi",           # explicit method
    "gh api repos/o/r/issues/1/comments -f body=@draft.md",            # -f implies POST
])
def test_a_WRITE_through_gh_api_still_blocks(command):
    assert _fire(command).returncode == 2, "gh api write slipped through: %r" % command


@pytest.mark.parametrize("command", [
    """python -c "print('gh issue comment is the pattern')" """,       # quoted in a diagnostic
    "grep -rn 'gh issue comment' inspeximus/",                         # searching for it
])
def test_CONTROL_merely_MENTIONING_a_post_warns_and_does_not_block(command):
    """The false positive that showed up within a minute of shipping the block: a read-only probe was
    stopped because the string appeared inside a Python literal. A guard that blocks reading about
    itself is one that gets removed, so a mention warns -- it still arrives, it just does not wall."""
    r = _fire(command)
    assert r.returncode == 0, "a mention must not block: %r" % command
    assert "does not appear to" in _delivered(r), "a mention must still be delivered: %r" % command


def test_CONTROL_an_ordinary_command_stays_silent():
    """The negative control. A guard that fires on everything is one you learn to ignore, and then it
    protects nothing at the moment it matters. Both channels must be empty, not just the one we look
    at: a stray byte on stderr at exit 0 is a warning the host would show and nobody wrote."""
    r = _fire("ls -la")
    assert (r.returncode, r.stdout.strip(), r.stderr.strip()) == (0, "", "")
