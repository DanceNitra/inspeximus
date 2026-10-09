"""tools/mutation_check_parallel.py: each worker has a home of its own, and a run that is mostly red before mutating
fails as a broken run.

Measured 2026-10-09 on feat-318: two workers inherited one sandboxed APPDATA, so they shared one user config. One
worker's tests wrote `hook.daemon: true` there, the other worker's daemon tests then went red before any mutation, and
60 of 155 entries were reported as skips in a summary that read like a result.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import mutation_check_parallel as mcp  # noqa: E402

WRITE = ("import json, os; from inspeximus import _userconfig; p = _userconfig.path(); "
         "os.makedirs(os.path.dirname(p), exist_ok=True); json.dump({'hook': {'daemon': True}}, open(p, 'w'))")
READ = "from inspeximus import _userconfig; print(_userconfig.get('hook', 'daemon'))"


def _py(code, env):
    env = dict(env, PYTHONPATH=ROOT)
    r = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stderr[-400:]
    return r.stdout.strip()


def test_one_workers_user_config_is_not_another_workers(tmp_path):
    base = str(tmp_path)
    a = mcp.worker_env(str(tmp_path / "home0"), str(tmp_path / "w0"), base)
    b = mcp.worker_env(str(tmp_path / "home1"), str(tmp_path / "w1"), base)
    _py(WRITE, a)
    assert _py(READ, a) == "True", "CONTROL: the worker that wrote the config reads it"
    assert _py(READ, b) == "None", "a worker read another worker's user config"
    for k in ("APPDATA", "LOCALAPPDATA", "XDG_CONFIG_HOME", "INSPEXIMUS_KEY_HOME", "TMP", "TEMP", "HOME"):
        assert a[k] != b[k] and a[k].startswith(str(tmp_path / "home0")), k
    if "windowsapps" not in os.path.normcase(sys.executable):
        assert a["USERPROFILE"] != b["USERPROFILE"], "USERPROFILE is per worker where the interpreter allows it"


def test_control_the_old_environment_shared_the_config(tmp_path):
    """The environment the tool built before this fix: its own HOME and USERPROFILE, everything else inherited. With one
    APPDATA for both, the second worker reads the first one's config, which is the defect the test above rules out."""
    shared = {k: v for k, v in os.environ.items() if not k.upper().startswith("INSPEXIMUS_")}
    shared.update(APPDATA=str(tmp_path / "shared" / "Roaming"), XDG_CONFIG_HOME=str(tmp_path / "shared" / ".config"))
    old_a = dict(shared, HOME=str(tmp_path / "h0"), USERPROFILE=str(tmp_path / "h0"))
    old_b = dict(shared, HOME=str(tmp_path / "h1"), USERPROFILE=str(tmp_path / "h1"))
    _py(WRITE, old_a)
    assert _py(READ, old_b) == "True", "CONTROL: the old environment no longer shares the config, so this proves nothing"


def test_the_callers_inspeximus_settings_do_not_reach_a_worker(tmp_path):
    env = mcp.worker_env(str(tmp_path / "h"), str(tmp_path / "w"), str(tmp_path),
                         environ={"PATH": os.environ.get("PATH", ""), "INSPEXIMUS_PATH": "/x", "INSPEXIMUS_KEY_HOME": "/y"})
    assert "INSPEXIMUS_PATH" not in env and env["INSPEXIMUS_KEY_HOME"] != "/y"


def _main_with(monkeypatch, tmp_path, red, total=20):
    spec = tmp_path / "spec.json"
    entries = [{"name": "m%d" % i, "file": "inspeximus/core.py", "old": "x", "new": "y", "tests": []}
               for i in range(total)]
    spec.write_text(json.dumps(entries), encoding="utf-8")

    def fake_worker(idx, mutations, base, keep):
        names = [m["name"] for m in mutations]
        bad = [n for n in names if int(n[1:]) < red]
        return {"idx": idx, "rc": 1 if bad else 0, "secs": 0.0, "log": None,
                "totals": (len(names) - len(bad), len(names), 0, len(bad)), "survived": [],
                "skipped": ["%s: tests are not green before mutating" % n for n in bad],
                "preflight_red": ["%s: tests are not green before mutating" % n for n in bad],
                "expected": names, "killed_clipped": [], "stderr_tail": "", "stdout": ""}
    monkeypatch.setattr(mcp, "_run_worker", fake_worker)
    monkeypatch.setattr(mcp, "_dirty_tracked", lambda: [])
    monkeypatch.setattr(sys, "argv", ["mutation_check_parallel.py", str(spec), "--workers", "2"])
    return mcp.main()


def test_a_run_mostly_red_before_mutating_is_a_broken_run(monkeypatch, tmp_path, capsys):
    assert _main_with(monkeypatch, tmp_path, red=5) == 3
    assert "BROKEN RUN: 5 of 20" in capsys.readouterr().out


def test_a_few_red_entries_are_still_skips(monkeypatch, tmp_path, capsys):
    """CONTROL: at or under the share the run reports skips as before, so the threshold is what decides."""
    assert _main_with(monkeypatch, tmp_path, red=2) == 1
    assert "BROKEN RUN" not in capsys.readouterr().out


def test_a_test_in_a_worker_can_start_sys_executable(tmp_path):
    """The suite's own tests start `sys.executable`. On a Microsoft Store Python that needs the caller's USERPROFILE,
    which worker_env keeps there; a run where they cannot start is red before mutating on every such test."""
    env = mcp.worker_env(str(tmp_path / "h"), ROOT, str(tmp_path))
    inner = ("import subprocess, sys; r = subprocess.run([sys.executable, '-c', 'print(8)'], capture_output=True, "
             "text=True); print(r.stdout.strip())")
    assert _py(inner, env) == "8"


def test_a_worker_can_start_its_own_pytest(tmp_path):
    """A worker starts pytest through the interpreter its parent gave it (MUTATION_PYTHON). With the worker's own
    USERPROFILE, a Microsoft Store Python resolves sys.executable inside the sandbox, where nothing can be started."""
    env = mcp.worker_env(str(tmp_path / "h"), ROOT, str(tmp_path))
    inner = ("import os, subprocess, sys; r = subprocess.run([os.environ.get('MUTATION_PYTHON') or sys.executable, "
             "'-c', 'print(7)'], capture_output=True, text=True); print(r.stdout.strip())")
    assert _py(inner, env) == "7"
