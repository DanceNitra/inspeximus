"""The suite runs in a temporary home, and the run-end guard sees a write to the real one.

MEASURED 2026-09-27: every suite run installed this tree's version into the owner's real
~/.claude.json and ~/.claude/settings.json, through test_docs_examples_are_runnable.py running the
documented `inspeximus install --ide claude` with the real home inherited. The conftest fixture
`_no_test_writes_the_real_home` gives every test and every child process a temporary home; the
conftest's run-end guard compares the real home's host configurations before and after the run
(see tests/_home_guard.py for why it compares inspeximus entries rather than size and mtime).
"""
import json
import os
import pathlib
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _home_guard

#: What a module that copies the environment at import time sees. Four test modules do exactly
#: this (`ENV = {**os.environ, ...}`), and import happens during collection, before any fixture:
#: the first version of the redirect was a session fixture, and those modules' children wrote the
#: real home. This copy must already be redirected.
_ENV_AT_IMPORT = dict(os.environ)


def _same(a, b):
    return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))


def test_a_test_sees_the_temporary_home_and_not_the_real_one(_no_test_writes_the_real_home):
    h = _no_test_writes_the_real_home
    if _same(h["home"], h["real"]):
        pytest.fail("control: the temporary home IS the real home, so nothing here is measured")
    assert _same(os.path.expanduser("~"), h["home"])
    assert _same(str(pathlib.Path.home()), h["home"])
    assert os.environ["INSPEXIMUS_NO_UPDATE_CHECK"] == "1"


def test_an_environment_copied_at_import_time_is_already_redirected(_no_test_writes_the_real_home):
    h = _no_test_writes_the_real_home
    assert _same(_ENV_AT_IMPORT.get("USERPROFILE", ""), h["home"]),         "a module that copied os.environ at import time got the real USERPROFILE"
    assert _same(_ENV_AT_IMPORT.get("APPDATA", ""), os.path.join(h["home"], "AppData", "Roaming"))


def test_a_child_process_inherits_the_temporary_home(_no_test_writes_the_real_home):
    h = _no_test_writes_the_real_home
    env = {k: v for k, v in os.environ.items() if not k.startswith("INSPEXIMUS_")}   # as the docs test does
    r = subprocess.run([sys.executable, "-c", "import os; print(os.path.expanduser('~'))"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace", env=env,
                       timeout=60)
    assert r.returncode == 0, r.stderr
    assert _same(r.stdout.strip(), h["home"]), f"a child resolved ~ to {r.stdout.strip()!r}"


def test_the_documented_installer_writes_the_temporary_home_only(_no_test_writes_the_real_home, tmp_path):
    """The command that leaked, run the way the docs test runs it.

    THE CHECK COMES BEFORE THE SIDE EFFECT. This test runs a real installer, so if the fixture is
    ever broken it would write the real home itself. That happened on 2026-09-27: a mutation check
    of this file removed USERPROFILE from the fixture, and this test pinned a pre-release version
    into the owner's ~/.claude.json. So it refuses to run the installer unless the redirect is
    demonstrably in effect, in this process and in the child's environment."""
    h = _no_test_writes_the_real_home
    env = {k: v for k, v in os.environ.items() if not k.startswith("INSPEXIMUS_")}
    env.update(PYTHONPATH=ROOT, PYTHONIOENCODING="utf-8")
    probe = subprocess.run([sys.executable, "-c", "import os; print(os.path.expanduser('~'))"],
                           capture_output=True, text=True, encoding="utf-8", errors="replace",
                           env=env, timeout=60).stdout.strip()
    for where, seen in (("this process", os.path.expanduser("~")), ("the child", probe)):
        if not _same(seen, h["home"]) or _same(seen, h["real"]):
            pytest.fail(f"refusing to run the installer: {where} resolves ~ to {seen!r}, not the "
                        f"temporary home, so the installer would write a real one")
    real_before = _home_guard.snapshot(h["real"])
    r = subprocess.run([sys.executable, "-m", "inspeximus.cli", "install", "--ide", "claude"],
                       cwd=str(tmp_path), capture_output=True, text=True, encoding="utf-8",
                       errors="replace", env=env, timeout=300)
    assert r.returncode == 0, (r.stdout + r.stderr)[-500:]
    if not os.path.exists(os.path.join(h["home"], ".claude.json")):
        pytest.fail("control: the installer wrote no ~/.claude.json anywhere visible, so it proves nothing")
    assert _home_guard.diff(real_before, _home_guard.snapshot(h["real"])) == [], \
        "the documented installer changed the real home"


def _fake_real_home(tmp_path, pin="3.14.1"):
    home = tmp_path / "realish"
    (home / ".claude").mkdir(parents=True)
    (home / ".inspeximus").mkdir()
    cfg = {"numStartups": 7, "projects": {"C:/p": {"history": [{"display": "tell me about inspeximus"}],
                                                   "mcpServers": {}}},
           "mcpServers": {"inspeximus": {"command": "uvx",
                                         "args": ["--from", f"inspeximus[mcp]=={pin}", "inspeximus-mcp"]}}}
    (home / ".claude.json").write_text(json.dumps(cfg), encoding="utf-8")
    (home / ".claude" / "settings.json").write_text(json.dumps({"hooks": {"PostToolUse": [
        {"hooks": [{"type": "command", "command": "python -m inspeximus.claude_code"}]}]}}),
        encoding="utf-8")
    (home / ".inspeximus" / "mcp_memory.json").write_text("x", encoding="utf-8")
    return home


def test_the_guard_ignores_what_a_live_session_changes(tmp_path):
    home = _fake_real_home(tmp_path)
    before = _home_guard.snapshot(str(home))
    cfg = json.loads((home / ".claude.json").read_text(encoding="utf-8"))
    cfg["numStartups"] = 8                                             # session state
    cfg["projects"]["C:/p"]["history"].append({"display": "inspeximus again"})
    (home / ".claude.json").write_text(json.dumps(cfg), encoding="utf-8")
    (home / ".inspeximus" / "mcp_memory.json").write_text("xy", encoding="utf-8")   # a live write
    (home / ".inspeximus" / "mcp_memory.json-journal").write_text("j", encoding="utf-8")
    assert _home_guard.diff(before, _home_guard.snapshot(str(home))) == []


@pytest.mark.parametrize("change", ["pin", "hook", "new_file", "new_host_config"])
def test_the_guard_names_what_a_test_would_change(tmp_path, change):
    home = _fake_real_home(tmp_path)
    before = _home_guard.snapshot(str(home))
    if change == "pin":
        cfg = json.loads((home / ".claude.json").read_text(encoding="utf-8"))
        cfg["mcpServers"]["inspeximus"]["args"][1] = "inspeximus[mcp]==3.14.2"
        (home / ".claude.json").write_text(json.dumps(cfg), encoding="utf-8")
    elif change == "hook":
        (home / ".claude" / "settings.json").write_text(json.dumps({"hooks": {}}), encoding="utf-8")
    elif change == "new_file":
        (home / ".inspeximus" / ".update_check.json").write_text("{}", encoding="utf-8")
    else:
        (home / ".codex").mkdir()
        (home / ".codex" / "config.toml").write_text('[mcp_servers.inspeximus]\ncommand = "uvx"\n',
                                                       encoding="utf-8")
    assert _home_guard.diff(before, _home_guard.snapshot(str(home))), f"the guard missed a {change} change"


def test_a_leak_into_the_real_home(_no_test_writes_the_real_home):
    """The inner half of the next test: skipped unless that test starts it, and then it writes where
    the run-end guard must see it. Never writes when run as part of the normal suite."""
    if os.environ.get("HOME_GUARD_SELFTEST") != "1":
        pytest.skip("only runs inside test_the_run_end_guard_fails_a_run_that_wrote_the_real_home")
    target = os.path.join(_no_test_writes_the_real_home["real"], ".inspeximus")
    os.makedirs(target, exist_ok=True)
    with open(os.path.join(target, "written-by-a-test.json"), "w", encoding="utf-8") as fh:
        fh.write("{}")


def test_the_run_end_guard_fails_a_run_that_wrote_the_real_home(tmp_path):
    fake_real = tmp_path / "real"
    (fake_real / "AppData" / "Roaming").mkdir(parents=True)
    env = dict(os.environ, USERPROFILE=str(fake_real), HOME=str(fake_real),
               APPDATA=str(fake_real / "AppData" / "Roaming"), HOME_GUARD_SELFTEST="1")
    here = os.path.relpath(__file__, ROOT)
    r = subprocess.run([sys.executable, "-m", "pytest", here + "::test_a_leak_into_the_real_home",
                        "-n", "0", "-q", "-p", "no:cacheprovider"], cwd=ROOT, env=env,
                       capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300)
    if not (fake_real / ".inspeximus" / "written-by-a-test.json").exists():
        pytest.fail(f"control: the inner test did not write, so the guard had nothing to see: {r.stdout[-400:]}")
    assert r.returncode != 0, "a run that wrote the real home exited 0"
    assert "THIS RUN CHANGED THE REAL HOME" in r.stdout and "written-by-a-test.json" in r.stdout, r.stdout[-600:]
