"""A child Python in a venv can start a process pool under the suite's temporary home (A-25).

On Windows, multiprocessing launches a venv's workers through `sys._base_executable`, not through the
venv's own python.exe, which is a launcher: a worker started through the launcher never receives its
pipe (bpo-35797). With a venv built on the Microsoft Store Python and USERPROFILE redirected to the
suite's temporary home, a child computes `_base_executable` as the launcher itself. Its process pool
then hangs or breaks: `claims_audit.py` exited with BrokenProcessPool in the release venv, and under
xdist its orphaned workers held a test's pipe for the full 900 s. No layout of the temporary home
helps. With any AppData\\Local in it, the Store Python cannot start (FileNotFoundError, or WinError
1920 through a junction). Without one, the venv falls back to the launcher.
"""
import importlib.util
import os
import subprocess
import sys

import pytest

_SHIM = os.path.join(os.path.dirname(os.path.abspath(__file__)), "probe_shadow", "sitecustomize.py")
_spec = importlib.util.spec_from_file_location("_probe_shadow_site", _SHIM)
shim = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(shim)

PROBE = """
import os, sys
from concurrent.futures import ProcessPoolExecutor

def square(i):
    return i * i

if __name__ == "__main__":
    base = sys._base_executable
    ok = (os.path.normcase(base) != os.path.normcase(sys.executable)
          and not os.path.isfile(os.path.join(os.path.dirname(os.path.dirname(base)), "pyvenv.cfg")))
    print("BASE", ok, base)
    sys.stdout.flush()
    if os.name == "nt" and not ok:
        sys.exit(3)                     # workers through a launcher hang; never start a pool in a failing run
    with ProcessPoolExecutor(max_workers=2) as ex:
        print("POOL", sum(ex.map(square, range(6))))
"""


def _installed_profile(fixture):
    """The profile the interpreter is installed under. On Windows that is the parent of LOCALAPPDATA,
    which the redirect leaves alone; the home this run started with may itself be a sandbox."""
    local = os.environ.get("LOCALAPPDATA")
    return os.path.dirname(os.path.dirname(local)) if os.name == "nt" and local else fixture["real"]


def _real_env(real):
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    env.update(USERPROFILE=real, HOME=real)
    return env


def _venv(tmp_path, real):
    """A venv built as the release venv was: from the base interpreter, with the real profile."""
    resolve = getattr(shim, "real_interpreter", lambda exe: None)       # absent before the fix
    base = resolve(sys.executable) or getattr(sys, "_base_executable", None) or sys.executable
    venv = tmp_path / "venv"
    subprocess.run([base, "-m", "venv", "--without-pip", "--system-site-packages", str(venv)],
                   check=True, capture_output=True, timeout=300, env=_real_env(real))
    py = venv / ("Scripts" if os.name == "nt" else "bin") / ("python.exe" if os.name == "nt" else "python")
    probe = tmp_path / "probe.py"
    probe.write_text(PROBE, encoding="utf-8")
    return str(py), str(probe)


def _run(py, probe, env):
    return subprocess.run([py, probe], capture_output=True, text=True, encoding="utf-8", errors="replace",
                          timeout=120, env=env)


def test_a_venv_child_starts_a_pool_under_the_test_home(tmp_path, _no_test_writes_the_real_home):
    py, probe = _venv(tmp_path, _installed_profile(_no_test_writes_the_real_home))
    r = _run(py, probe, dict(os.environ))
    if os.name == "nt":
        assert "BASE True" in r.stdout, ("under the test home a venv child's _base_executable is its own "
                                         f"launcher, so its process pool cannot start: {r.stdout!r} {r.stderr[-300:]!r}")
    assert r.returncode == 0 and "POOL 55" in r.stdout, (r.returncode, r.stdout, r.stderr[-500:])


def _without_the_shim(env):
    """The same environment with tests/probe_shadow off PYTHONPATH, so the child starts as a user's would."""
    here = os.path.normcase(os.path.dirname(_SHIM))
    keep = [p for p in env.get("PYTHONPATH", "").split(os.pathsep)
            if p and os.path.normcase(os.path.abspath(p)) != here]
    out = dict(env)
    if keep:
        out["PYTHONPATH"] = os.pathsep.join(keep)
    else:
        out.pop("PYTHONPATH", None)
    return out


def test_the_repair_is_what_puts_a_venv_childs_base_interpreter_right(tmp_path, _no_test_writes_the_real_home):
    """The repair, not the interpreter, is what makes the test above pass.

    Two registry entries remove the repair (the call in `sitecustomize`, and the assignment inside it).
    They survived the full suite on a second Windows machine (PC2, 2026-09-28), because that machine's
    Python keeps `_base_executable` as the base interpreter under a redirected home, so the venv child was
    healthy with no repair at all: the test above cannot fail there, and a survivor on that machine says
    nothing about the code. This test starts the child WITHOUT the shim first. If that child is already
    healthy, there is nothing to repair on this interpreter and the test skips, which the mutation gate
    reports as NOT EVALUATED, not as a survivor. If it is not, the child WITH the shim must be healthy."""
    if os.name != "nt":
        pytest.skip("only Windows starts a venv's pool workers through _base_executable, so only Windows repairs it")
    py, probe = _venv(tmp_path, _installed_profile(_no_test_writes_the_real_home))
    bare = _run(py, probe, _without_the_shim(dict(os.environ)))
    if "BASE True" in bare.stdout:
        pytest.skip("this interpreter keeps a venv child's base interpreter under a redirected home, "
                    "so there is nothing here for the repair to repair")
    assert "BASE False" in bare.stdout, ("control: the child without the shim neither reported a healthy nor "
                                         f"a collapsed base interpreter: {bare.stdout!r} {bare.stderr[-300:]!r}")
    fixed = _run(py, probe, dict(os.environ))
    assert "BASE True" in fixed.stdout, ("the shim did not put the venv child's base interpreter right: "
                                         f"{fixed.stdout!r} {fixed.stderr[-300:]!r}")


def test_control_the_same_venv_is_healthy_in_the_real_home(tmp_path, _no_test_writes_the_real_home):
    """CONTROL: the venv itself is sound, so a failure above is the temporary home's doing."""
    real = _installed_profile(_no_test_writes_the_real_home)
    py, probe = _venv(tmp_path, real)
    r = _run(py, probe, _real_env(real))
    if os.name == "nt":
        assert "BASE True" in r.stdout, (r.stdout, r.stderr[-500:])
    assert r.returncode == 0 and "POOL 55" in r.stdout, (r.stdout, r.stderr[-500:])
