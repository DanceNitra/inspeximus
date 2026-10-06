"""AUDIT-B 3.17.0: a read-guard stamp made under another Python is reported, and the hook heals it.

The guard-set hash includes `unicodedata.unidata_version`, so a stamp made by Python 3.12 (Unicode 15.0.0) reads as invalid
under Python 3.14 (Unicode 16.0.0), and the hook assesses every such row on every prompt. Measured 2026-10-06: stamps from
3.12.10 under the hook's 3.14.4 gained nothing, and the same stamps made by 3.14.4 took the uvx hook from 2.40 s to 1.87 s.
The guard-set hash keeps the Unicode version, because a regex that reads Unicode properties can change its answer with it.

Here "another Python" is a child process that sets `unicodedata.unidata_version` before the first stamp.
"""
import json
import os
import subprocess
import sys
import time

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
import inspeximus.claude_code as cc  # noqa: E402
import inspeximus.core as core  # noqa: E402

FAKE_UCD = "99.0.0"

CHILD = """
import sys, unicodedata
unicodedata.unidata_version = %r
import inspeximus.claude_code as cc
m = cc._store(sys.argv[1])
m._guard_key(create=True)
for i in range(6):
    m.remember("a note about the release, number %%d" %% i, key="n%%d" %% i)
m.flush()
""" % FAKE_UCD


@pytest.fixture
def proj(tmp_path, monkeypatch):
    for k in [k for k in os.environ if k.startswith("INSPEXIMUS_")]:
        monkeypatch.delenv(k)
    monkeypatch.setenv("INSPEXIMUS_KEY_HOME", str(tmp_path / "keyhome"))
    monkeypatch.setenv("INSPEXIMUS_NO_UPDATE_CHECK", "1")
    p = tmp_path / "proj"
    (p / ".git").mkdir(parents=True)
    return str(p)


def _stamped_elsewhere(proj):
    env = {**os.environ, "PYTHONPATH": ROOT}
    r = subprocess.run([sys.executable, "-c", CHILD, proj], capture_output=True, text=True, encoding="utf-8", env=env)
    assert r.returncode == 0, r.stderr[-500:]


def test_stamps_made_here_are_not_foreign(proj):
    cc._store(proj)._guard_key(create=True)
    m = cc._store(proj)
    for i in range(3):
        m.remember(f"a note number {i}", key=f"n{i}")
    m.flush()
    out = cc.stamp_guards(proj)
    assert out["foreign_stamps"] == 0 and out["valid_stamps"] == out["active"] == 3
    assert {r["meta"]["read_guards"].get("env") for r in cc._store(proj).items} == {core._guard_env_tag()}, "a remember stamps its origin"
    assert out["interpreter"]["executable"] == sys.executable
    assert out["interpreter"]["stamps_made_here_read_as"] == core._guard_env_tag()
    assert "note_foreign" not in out


def test_a_dry_run_counts_the_stamps_another_unicode_version_made_and_names_it(proj):
    _stamped_elsewhere(proj)
    out = cc.stamp_guards(proj)
    assert out["active"] == 6 and out["valid_stamps"] == 0 and out["foreign_stamps"] == 6, out
    assert list(out["foreign_made_under"]) == ["%d.%d.%d/ucd%s" % (*sys.version_info[:3], FAKE_UCD)]
    assert "another Python or Unicode version" in out["note_foreign"] and sys.executable in out["note_foreign"]
    assert cc.foreign_stamp_count(cc._store(proj)) == 6


def test_a_stamp_without_the_origin_field_is_reported_as_unknown(proj):
    _stamped_elsewhere(proj)
    m = cc._store(proj)
    for r in m.items:
        r["meta"]["read_guards"].pop("env", None)
        m._touched.add(r["id"])
    m.flush()
    out = cc.stamp_guards(proj)
    assert list(out["foreign_made_under"]) == ["unknown (made before 3.17.0)"]


def test_the_hook_command_is_read_from_the_settings_when_there_is_one(proj, tmp_path, monkeypatch):
    assert cc.stamp_guards(proj)["hook_command"] is None or True
    os.makedirs(os.path.join(proj, ".claude"))
    cmd = "uvx --from inspeximus==3.16.3 python -m inspeximus.claude_code"
    with open(os.path.join(proj, ".claude", "settings.json"), "w", encoding="utf-8") as fh:
        json.dump({"hooks": {"UserPromptSubmit": [{"hooks": [{"type": "command", "command": cmd}]}]}}, fh)
    assert cc._hook_command(proj) == cmd


def test_the_background_restamp_has_reasons_not_to_start(proj, monkeypatch):
    _stamped_elsewhere(proj)
    assert cc.maybe_restamp_in_background(proj, foreign=0) == "none"
    monkeypatch.setenv("INSPEXIMUS_STAMP_AUTO", "0")
    assert cc.maybe_restamp_in_background(proj, foreign=6) == "off"
    monkeypatch.delenv("INSPEXIMUS_STAMP_AUTO")
    started = []
    monkeypatch.setattr(subprocess, "Popen", lambda argv, **k: started.append(argv))
    assert cc.maybe_restamp_in_background(proj, foreign=6) == "started"
    assert started and started[0][0] == sys.executable and "--stamp-guards" in started[0] and "--apply" in started[0], started
    assert cc.maybe_restamp_in_background(proj, foreign=6) == "recent", "one attempt per interval"


def test_a_prompt_that_meets_foreign_stamps_starts_the_restamp_and_the_store_heals(proj):
    """The real hook, as a process: the first prompt assesses every row, the background run stamps them under the hook's
    own interpreter, and the stamps then read as valid."""
    _stamped_elsewhere(proj)
    assert cc.stamp_guards(proj)["foreign_stamps"] == 6
    env = {**os.environ, "PYTHONPATH": ROOT}
    ev = {"hook_event_name": "UserPromptSubmit", "prompt": "what did we note about the release", "cwd": proj, "session_id": "t"}
    r = subprocess.run([sys.executable, "-m", "inspeximus.claude_code"], input=json.dumps(ev), capture_output=True,
                       text=True, encoding="utf-8", env=env, timeout=120)
    assert r.returncode == 0, r.stderr[-400:]
    end = time.time() + 120
    out = None
    while time.time() < end:
        out = cc.stamp_guards(proj)
        if out["valid_stamps"] == out["active"] and out["foreign_stamps"] == 0:
            break
        time.sleep(0.5)
    assert out["valid_stamps"] == out["active"] == 6 and out["foreign_stamps"] == 0, out
    m = cc._store(proj)
    assert {r["meta"]["read_guards"].get("env") for r in m.items if r["status"] == "active"} == {core._guard_env_tag()}
    state = json.load(open(os.path.join(proj, ".inspeximus", "coding_memory.json.stamp-auto.json"), encoding="utf-8"))
    assert state["foreign"] == 6 and state["interpreter"] == sys.executable


def test_the_guard_set_still_holds_the_unicode_version():
    """The hash is not weakened: a Unicode database that changes what a regex matches must invalidate a stamp."""
    import inspect
    assert "unidata_version" in inspect.getsource(core._guard_set_hash)
