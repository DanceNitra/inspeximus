"""AUDIT-B 3.17.0: the background re-stamp the prompt hook starts follows the rules 3.16.4 set for a run started from the
hook (AUDIT-A F-9, F-13, F-13b, F-14, F-15, F-22), one test for each:

  1. no working directory and no PYTHON* variable on the import path: a repository holding its own `inspeximus/`, and a
     PYTHONPATH naming another, are not imported by the run
  2. CREATE_BREAKAWAY_FROM_JOB on Windows, and the start without it when the job refuses
  3. the attempt is recorded with its pid, the run marks itself `done`, and a run that died is retried after the floor
  4. the switch is read from the environment and the user's config, never from a repository's config
  5. only the store the hook resolved: the decision store, `--store`, and another resolution are not stamped
  6. the key home comes from `_keyhome.key_home()`, which ignores one inside the repository
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
for i in range(4):
    m.remember("a note about the release, number %%d" %% i, key="n%%d" %% i)
m.flush()
""" % FAKE_UCD

DECOY = "import os\nopen(os.environ.get('DECOY_MARK') or 'decoy-imported.txt', 'w').write('imported')\n"


@pytest.fixture
def proj(tmp_path, monkeypatch):
    for k in [k for k in os.environ if k.startswith("INSPEXIMUS_")]:
        monkeypatch.delenv(k)
    monkeypatch.setenv("INSPEXIMUS_KEY_HOME", str(tmp_path / "keyhome"))
    monkeypatch.setenv("INSPEXIMUS_NO_UPDATE_CHECK", "1")
    p = tmp_path / "proj"
    (p / ".git").mkdir(parents=True)
    return str(p)


def _foreign(proj):
    r = subprocess.run([sys.executable, "-c", CHILD, proj], capture_output=True, text=True, encoding="utf-8",
                       env={**os.environ, "PYTHONPATH": ROOT})
    assert r.returncode == 0, r.stderr[-400:]
    assert cc.stamp_guards(proj)["foreign_stamps"] == 4


def _cli(args, cwd):
    """The run as the hook starts it: -E, and this checkout first on the path (the shim `maybe_restamp_in_background` uses)."""
    shim = ("import runpy,sys;sys.path[:]=[p for p in sys.path if p not in (str(),chr(46))];"
            "sys.path.insert(0,%r);runpy._run_module_as_main(sys.argv.pop(1))" % ROOT)
    return subprocess.run([sys.executable, "-E", "-c", shim, "inspeximus.claude_code", *args], cwd=cwd,
                          capture_output=True, text=True, encoding="utf-8")


def _state(proj):
    return json.load(open(os.path.join(proj, ".inspeximus", "coding_memory.json.stamp-auto.json"), encoding="utf-8"))


def _wait_done(proj, secs=120):
    end = time.time() + secs
    while time.time() < end:
        try:
            st = _state(proj)
            if st.get("done"):
                return st
        except (OSError, ValueError):
            pass
        time.sleep(0.25)
    raise AssertionError("the run did not mark itself done: %r" % (_state(proj),))


# 1 ---------------------------------------------------------------------------------------------------------------------

def test_the_run_does_not_import_a_package_from_the_working_directory(proj, tmp_path):
    _foreign(proj)
    mark = str(tmp_path / "mark-cwd.txt")
    os.makedirs(os.path.join(proj, "inspeximus"))
    open(os.path.join(proj, "inspeximus", "__init__.py"), "w").write(DECOY)
    os.environ["DECOY_MARK"] = mark
    try:
        assert cc.maybe_restamp_in_background(proj, foreign=4) == "started"
        st = _wait_done(proj)
    finally:
        del os.environ["DECOY_MARK"]
    assert not os.path.exists(mark), "the run imported the repository's own `inspeximus/`"
    assert st["result"] == "ok" and cc.stamp_guards(proj)["foreign_stamps"] == 0


def test_the_run_ignores_a_pythonpath_a_repository_set(proj, tmp_path, monkeypatch):
    _foreign(proj)
    mark = str(tmp_path / "mark-pp.txt")
    decoy = tmp_path / "decoy"
    os.makedirs(decoy / "inspeximus")
    open(decoy / "inspeximus" / "__init__.py", "w").write(DECOY)
    monkeypatch.setenv("PYTHONPATH", str(decoy))
    monkeypatch.setenv("DECOY_MARK", mark)
    assert cc.maybe_restamp_in_background(proj, foreign=4) == "started"
    st = _wait_done(proj)
    assert not os.path.exists(mark), "the run imported a package from PYTHONPATH"
    assert st["result"] == "ok"


def test_the_launch_uses_dash_e_and_the_shim_and_not_dash_m(proj, monkeypatch):
    _foreign(proj)
    seen = []
    monkeypatch.setattr(subprocess, "Popen", lambda argv, **k: seen.append((argv, k)))
    assert cc.maybe_restamp_in_background(proj, foreign=4) == "started"
    argv, kw = seen[0]
    assert argv[0] == sys.executable and argv[1] == "-E" and argv[2] == "-c" and "-m" not in argv[:3]
    assert "chr(46)" in argv[3] and "_run_module_as_main" in argv[3]
    assert kw["cwd"] == proj and kw["stdin"] == subprocess.DEVNULL


# 2 ---------------------------------------------------------------------------------------------------------------------

@pytest.mark.skipif(os.name != "nt", reason="job objects are a Windows mechanism")
def test_the_run_asks_to_leave_the_hooks_job_and_starts_without_when_refused(proj, monkeypatch):
    _foreign(proj)
    calls = []

    def popen(argv, **k):
        calls.append(k["creationflags"])
        if len(calls) == 1:
            raise OSError("the job does not allow breakaway")
        return type("P", (), {"pid": 1})()

    monkeypatch.setattr(subprocess, "Popen", popen)
    assert cc.maybe_restamp_in_background(proj, foreign=4) == "started"
    assert calls[0] & 0x01000000 and not calls[1] & 0x01000000, calls


# 3 ---------------------------------------------------------------------------------------------------------------------

def test_the_attempt_carries_the_pid_and_the_run_marks_itself_done(proj):
    _foreign(proj)
    assert cc.maybe_restamp_in_background(proj, foreign=4) == "started"
    assert isinstance(_state(proj)["pid"], int)
    st = _wait_done(proj)
    assert st["result"] == "ok" and st["done"] >= st["last_attempt"]


def test_a_run_that_died_is_retried_after_the_floor_and_a_live_one_is_not(proj, monkeypatch):
    _foreign(proj)
    state = os.path.join(proj, ".inspeximus", "coding_memory.json.stamp-auto.json")
    started = []
    monkeypatch.setattr(subprocess, "Popen", lambda argv, **k: started.append(argv) or type("P", (), {"pid": 5})())
    now = time.time()
    json.dump({"last_attempt": now - 30, "pid": 2 ** 22 + 12345}, open(state, "w"))
    assert cc.maybe_restamp_in_background(proj, foreign=4) == "recent", "dead, but not past the floor"
    json.dump({"last_attempt": now - 120, "pid": 2 ** 22 + 12345}, open(state, "w"))
    assert cc.maybe_restamp_in_background(proj, foreign=4) == "started", "dead and past the floor"
    json.dump({"last_attempt": now - 120, "pid": os.getpid()}, open(state, "w"))
    assert cc.maybe_restamp_in_background(proj, foreign=4) == "recent", "alive: leave it"
    json.dump({"last_attempt": now - 120, "pid": 2 ** 22 + 12345, "done": now - 100, "result": "ok"}, open(state, "w"))
    assert cc.maybe_restamp_in_background(proj, foreign=4) == "recent", "finished: the interval holds"


def test_a_run_told_another_store_refuses(proj):
    _foreign(proj)
    r = _cli(["--stamp-guards", "--apply", "--auto", "--expect-store", os.path.join(proj, "elsewhere.json")], proj)
    assert r.returncode == 0 and "refused" in r.stdout, (r.stdout, r.stderr[-300:])


# 4 ---------------------------------------------------------------------------------------------------------------------

def test_the_switch_comes_from_the_environment_and_the_users_config_only(proj, tmp_path, monkeypatch):
    os.makedirs(os.path.join(proj, ".inspeximus"), exist_ok=True)
    json.dump({"stamp": {"auto": False}}, open(os.path.join(proj, ".inspeximus", "config.json"), "w"))
    assert cc.stamp_heal_enabled() is True, "a repository's config does not switch it off, or on"
    cfg = cc.user_config_path()
    os.makedirs(os.path.dirname(cfg), exist_ok=True)
    json.dump({"stamp": {"auto": False}}, open(cfg, "w"))
    assert cc.stamp_heal_enabled() is False, "the user's config does"
    monkeypatch.setenv("INSPEXIMUS_STAMP_AUTO", "1")
    assert cc.stamp_heal_enabled() is True, "and the environment outranks it"
    monkeypatch.setenv("INSPEXIMUS_STAMP_AUTO", "0")
    assert cc.stamp_heal_enabled() is False


# 5 ---------------------------------------------------------------------------------------------------------------------

def test_the_run_stamps_only_the_store_the_hook_resolved(proj, tmp_path):
    _foreign(proj)
    before = open(os.path.join(proj, ".inspeximus", "coding_memory.json"), "rb").read()
    base = ["--stamp-guards", "--apply", "--auto"]
    for extra in (["--expect-store", os.path.join(tmp_path, "other.json")],            # another resolution
                  ["--expect-store", os.path.join(proj, ".inspeximus", "coding_memory.json"), "--store", str(tmp_path / "x.json")],
                  []):                                                                  # no expectation at all
        r = _cli(base + extra, proj)
        assert "refused" in r.stdout, (extra, r.stdout, r.stderr[-300:])
        assert open(os.path.join(proj, ".inspeximus", "coding_memory.json"), "rb").read() == before


def test_the_decision_store_is_never_stamped_from_a_prompt(proj, tmp_path, monkeypatch):
    """INSPEXIMUS_DECISION_STORE comes from the environment a repository's settings can set. The hook reads that store and
    does not start a run on it, whatever stamps it holds."""
    dec = str(tmp_path / "decisions" / "mcp_memory.json")
    os.makedirs(os.path.dirname(dec))
    r = subprocess.run([sys.executable, "-c", CHILD, os.path.dirname(os.path.dirname(dec))], capture_output=True, text=True,
                       encoding="utf-8", env={**os.environ, "PYTHONPATH": ROOT})
    assert r.returncode == 0
    started = []
    monkeypatch.setattr(subprocess, "Popen", lambda argv, **k: started.append(argv) or type("P", (), {"pid": 5})())
    monkeypatch.setenv("INSPEXIMUS_DECISION_STORE", dec)
    ev = {"hook_event_name": "UserPromptSubmit", "prompt": "the release", "cwd": proj, "session_id": "t"}
    cc.recall(ev)
    cc.maybe_restamp_after_recall(proj)
    assert started == [], "the decision store is not stamped from a prompt"


# 6 ---------------------------------------------------------------------------------------------------------------------

def test_a_key_home_inside_the_repository_is_not_used_by_the_run(proj, tmp_path, monkeypatch):
    """The store is stamped under a key kept in the user's key home. A repository that sets INSPEXIMUS_KEY_HOME to a directory
    it ships gets it ignored (F-13, F-13b): the run finds no key there, mints none there, and stamps nothing."""
    _foreign(proj)
    before = open(os.path.join(proj, ".inspeximus", "coding_memory.json"), "rb").read()
    shipped = os.path.join(proj, "keys-from-the-repo")
    monkeypatch.setenv("INSPEXIMUS_KEY_HOME", shipped)
    monkeypatch.setenv("APPDATA", str(tmp_path / "userhome"))       # the per-user directory the rule falls back to
    assert cc.maybe_restamp_in_background(proj, foreign=4) == "started"
    st = _wait_done(proj)
    assert not os.path.exists(shipped), "the run created a key home inside the repository"
    assert open(os.path.join(proj, ".inspeximus", "coding_memory.json"), "rb").read() == before, "a store was stamped"
    assert st["result"] == "failed"
