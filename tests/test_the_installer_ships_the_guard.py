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
                   cwd=str(tmp_path), capture_output=True, text=True, timeout=180,
                   env=ENV, check=True)
    with open(os.path.join(str(tmp_path), ".claude", "settings.json"), encoding="utf-8") as fh:
        return json.load(fh)


def _fire(command):
    ev = {"hook_event_name": "PreToolUse", "tool_name": "Bash",
          "tool_input": {"command": command}}
    r = subprocess.run([sys.executable, "-m", "inspeximus.claude_code"], input=json.dumps(ev),
                       capture_output=True, text=True, timeout=180, env=ENV)
    return r.stdout


def test_install_writes_the_pretooluse_guard(tmp_path):
    hooks = _install(tmp_path).get("hooks", {})
    assert "PreToolUse" in hooks, (
        "install() wrote %r and no PreToolUse. The guard is in the source and nothing invokes it -- "
        "which is how three outreach posts went out ungated in one day." % sorted(hooks))


def test_the_pretooluse_entry_carries_a_matcher(tmp_path):
    """Without a matcher the hook is registered and never fires: installed and silent, which reads as
    protection and is not. That failure would be invisible to the test above."""
    entry = _install(tmp_path)["hooks"]["PreToolUse"][0]
    assert entry.get("matcher"), "PreToolUse without a matcher does not fire: %r" % entry
    assert "Bash" in entry["matcher"]


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
def test_the_guard_fires_on_what_it_guards(command, needle):
    assert needle in _fire(command), "no warning for %r" % command


def test_CONTROL_an_ordinary_command_stays_silent():
    """The negative control. A guard that fires on everything is one you learn to ignore, and then it
    protects nothing at the moment it matters."""
    assert _fire("ls -la").strip() == ""
