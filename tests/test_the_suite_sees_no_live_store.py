"""The suite sees none of the owner's live stores (3.19, EM).

Measured 2026-10-12 on PC1: the session environment carried INSPEXIMUS_DECISION_STORE, naming the live crew store, and
the suite did not strip it. Every prompt-daemon test then held that store, read-only, and two daemon tests flaked on
the live writes beside them. conftest.py now strips every INSPEXIMUS_* variable before collection (`_strip_inspeximus_env`).

These tests check what the library resolves inside the run: every store and home it would use lies in the run's
temporary roots, and no INSPEXIMUS_* variable from the caller is visible. The control runs this file in a child with
the strip switched off and a decision store outside the sandbox, and requires it to fail.
"""
from __future__ import annotations

import os
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import conftest  # noqa: E402

#: The variables the run itself sets: the update check off (conftest), and the per-run key home (a session fixture).
SET_BY_THE_RUN = {"INSPEXIMUS_NO_UPDATE_CHECK", "INSPEXIMUS_KEY_HOME"}


def _roots(config, tmp_path_factory):
    roots = [str(tmp_path_factory.getbasetemp())]
    for name in ("_test_home", "_run_tmp"):
        v = getattr(config, name, None)
        if v:
            roots.append(str(v))
    return [os.path.normcase(os.path.realpath(r)) for r in roots]


def _inside(path, roots):
    p = os.path.normcase(os.path.realpath(path))
    return any(p == r or p.startswith(r + os.sep) for r in roots)


def _resolved(project):
    """Every store and home the library resolves from the environment, for a project in the sandbox."""
    from inspeximus import _envpolicy, _surface
    from inspeximus._keyhome import key_home
    default = _surface.resolve_path(cwd=project)
    out = {"key home": key_home(), "default store": os.path.join(project, default),   # a relative default is cwd's
           "coding store": _surface.coding_store_path(project)}
    ds = _envpolicy.decision_store()
    if ds:
        out["decision store"] = ds
    shared = _surface.shared_store_path()
    if shared:
        out["shared store"] = shared
    for k in ("INSPEXIMUS_PATH", "INSPEXIMUS_CODING_STORE"):
        if os.environ.get(k):
            out[k] = os.environ[k]
    return out


def test_no_inspeximus_variable_from_the_caller_was_visible_at_collection(request):
    seen = set(request.config._inspeximus_env_at_collection)
    allowed = SET_BY_THE_RUN | set(conftest.INHERITED_INSPEXIMUS)
    assert seen <= allowed, "INSPEXIMUS_* from the caller visible at collection: %s" % sorted(seen - allowed)


def test_every_store_and_home_the_suite_resolves_is_inside_the_run(request, tmp_path_factory, tmp_path):
    project = tmp_path / "proj"
    project.mkdir()
    subprocess.run(["git", "init", "-q", str(project)], check=True)
    roots = _roots(request.config, tmp_path_factory)
    outside = {k: v for k, v in _resolved(str(project)).items() if not _inside(v, roots)}
    assert not outside, "the suite resolves paths outside its temporary roots %s: %s" % (roots, outside)


def _owner_file(root, token, pid, start):
    import json
    with open(os.path.join(root, conftest.RUN_OWNER_FILE), "w", encoding="utf-8") as fh:
        json.dump({"token": token, "pid": pid, "start": start}, fh)


def test_a_nested_run_needs_a_live_owner_with_its_token(tmp_path, monkeypatch):
    """`_nested_in_a_live_run` is true only for a root whose owner file carries the inherited token and names a live
    process with the start time it recorded. A stale variable, a dead owner, or a hand-made value is a top-level run."""
    from inspeximus import hookd
    root = str(tmp_path)
    live = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        start = hookd._proc_start(live.pid)
        assert start is not None, "CONTROL: this platform reads a start time"
        monkeypatch.setenv(conftest.RUN_TMP_ENV, root)
        monkeypatch.setenv(conftest.RUN_TOKEN_ENV, "t0ken")
        _owner_file(root, "t0ken", live.pid, start)
        assert conftest._nested_in_a_live_run(), "CONTROL: a live owner with the token is a nested run"
        _owner_file(root, "other", live.pid, start)
        assert not conftest._nested_in_a_live_run(), "a token that does not match was trusted"
        _owner_file(root, "t0ken", live.pid, start + 1)
        assert not conftest._nested_in_a_live_run(), "a reused pid (another start time) was trusted"
        _owner_file(root, "t0ken", os.getpid(), hookd._proc_start(os.getpid()))
        assert not conftest._nested_in_a_live_run(), "this process was taken for its own parent run"
        os.unlink(os.path.join(root, conftest.RUN_OWNER_FILE))
        assert not conftest._nested_in_a_live_run(), "a root with no owner file (a finished run) was trusted"
    finally:
        live.kill()
        live.wait()
    _owner_file(root, "t0ken", live.pid, start)
    assert not conftest._nested_in_a_live_run(), "an owner that has exited was trusted"


def test_a_stale_run_variable_does_not_exempt_a_run(tmp_path):
    """A top-level run whose shell carries a stale RUN_TMP_ENV and token, and a decision store outside the sandbox: the
    strip must still run, so the two guards pass in the child. Exempted, they fail (the mutant shows it)."""
    stale = tmp_path / "stale-run-root"
    stale.mkdir()
    _owner_file(str(stale), "stale", 999999, 1)               # a finished run's file: its pid is gone
    outside_dir = tmp_path / "outside-the-run"
    outside_dir.mkdir()
    env = dict(os.environ)
    env[conftest.RUN_TMP_ENV] = str(stale)
    env[conftest.RUN_TOKEN_ENV] = "stale"
    env["INSPEXIMUS_DECISION_STORE"] = str(outside_dir / "crew.json")
    env.pop(conftest.NO_STRIP_FOR_THE_CONTROL, None)
    r = subprocess.run([sys.executable, "-m", "pytest", "-n", "0", "-p", "no:cacheprovider", "-q", "--tb=line",
                        "--basetemp", str(tmp_path / "bt-stale"), os.path.join("tests", "test_the_suite_sees_no_live_store.py"),
                        "-k", "visible_at_collection or resolves_is_inside"],
                       cwd=ROOT, env=env, capture_output=True, text=True, timeout=300)
    assert r.returncode == 0, "a stale RUN_TMP_ENV exempted a top-level run from the strip:\n" + r.stdout[-3000:]
    assert "2 passed" in r.stdout, "CONTROL: both guards ran in the child:\n" + r.stdout[-1500:]


def test_the_strip_leaves_nothing_but_the_inherited_ones():
    class Cfg:
        pass
    saved = dict(os.environ)
    try:
        os.environ["INSPEXIMUS_DECISION_STORE"] = "/somewhere/else.json"
        os.environ["INSPEXIMUS_PYTHON"] = sys.executable
        os.environ.pop(conftest.NO_STRIP_FOR_THE_CONTROL, None)
        os.environ.pop(conftest.RUN_TMP_ENV, None)                 # a top-level run
        os.environ.pop(conftest.RUN_TOKEN_ENV, None)
        cfg = Cfg()
        conftest._strip_inspeximus_env(cfg)
        left = {k for k in os.environ if k.startswith("INSPEXIMUS_")}
        assert left == {"INSPEXIMUS_PYTHON"}, left
        assert "INSPEXIMUS_DECISION_STORE" in cfg._inspeximus_env_stripped
    finally:
        os.environ.clear()
        os.environ.update(saved)


def test_the_guard_fails_on_a_leaked_decision_store(tmp_path):
    """CONTROL: the strip switched off and a decision store outside the sandbox: the two guards above must fail. The
    same child with the strip on passes, so the failure is the leak and not the child's setup."""
    outside_dir = tmp_path / "outside-the-run"
    outside_dir.mkdir()
    outside = str(outside_dir / "crew.json")

    def child(strip):
        env = dict(os.environ)
        env.pop(conftest.RUN_TMP_ENV, None)              # a top-level run, as a caller starts it, not a nested one
        env.pop(conftest.RUN_TOKEN_ENV, None)
        env["INSPEXIMUS_DECISION_STORE"] = outside
        if strip:
            env.pop(conftest.NO_STRIP_FOR_THE_CONTROL, None)
        else:
            env[conftest.NO_STRIP_FOR_THE_CONTROL] = "1"
        r = subprocess.run([sys.executable, "-m", "pytest", "-n", "0", "-p", "no:cacheprovider", "-q", "--tb=line",
                            "--basetemp", str(tmp_path / ("bt-strip" if strip else "bt-leak")),
                            os.path.join("tests", "test_the_suite_sees_no_live_store.py"),
                            "-k", "not test_the_guard_fails_on_a_leaked_decision_store"],      # not itself: no recursion
                           cwd=ROOT, env=env, capture_output=True, text=True, timeout=300)
        return r.returncode, r.stdout[-4000:]
    rc_leak, out_leak = child(strip=False)
    assert rc_leak != 0, "the guard passed with a decision store outside the sandbox:\n" + out_leak
    for name in ("test_no_inspeximus_variable_from_the_caller_was_visible_at_collection",
                 "test_every_store_and_home_the_suite_resolves_is_inside_the_run"):
        assert "FAILED tests/test_the_suite_sees_no_live_store.py::" + name in out_leak, (name, out_leak)
    rc_ok, out_ok = child(strip=True)
    assert rc_ok == 0, "CONTROL: the same child with the strip on fails:\n" + out_ok
