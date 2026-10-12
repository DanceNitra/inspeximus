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


def test_the_strip_leaves_nothing_but_the_inherited_ones():
    class Cfg:
        pass
    saved = dict(os.environ)
    try:
        os.environ["INSPEXIMUS_DECISION_STORE"] = "/somewhere/else.json"
        os.environ["INSPEXIMUS_PYTHON"] = sys.executable
        os.environ.pop(conftest.NO_STRIP_FOR_THE_CONTROL, None)
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
