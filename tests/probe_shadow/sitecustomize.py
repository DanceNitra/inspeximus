"""While the suite runs, a probe's write to a file git tracks lands in a shadow copy, not in the repo.

tests/conftest.py puts this directory on PYTHONPATH for every Python the suite starts, and sets
INSPEXIMUS_PROBE_ROOT (the repository), INSPEXIMUS_PROBE_SHADOW (a directory under pytest's basetemp),
and INSPEXIMUS_PROBE_TRACKED (a file listing `git ls-files`). Python imports `sitecustomize` at
startup, so this runs before the probe's first line.

WHY IT LIVES HERE AND NOT IN THE PROBES. A probe's committed result file is the measurement someone
cites, and a suite run on a loaded machine must not replace it. probes/_receipt.py suppresses the
write for probes that call it, and three rounds of converting callers still left writers outside it:
measured 2026-09-27, one full local run rewrote two tracked receipts through a bare open(), and CI
failed tests/test_examples_run.py because a third probe rewrote agora_output/lab/data/*.json while
that test was reading `git status`. 127 write calls exist across the probes. The one place every
one of them passes through is the interpreter, so the rule is enforced there.

WHAT IT DOES. Only in a process whose main script is a file in <root>/probes/, and only for paths
git tracks: opening for writing (w, a, x or +), Path.write_text/write_bytes, and os.replace/os.rename
onto such a path go to <shadow>/<relative path>. A later read of that path in the same process reads
the shadow copy, so a probe that writes and then reads its own file still works. Appending or
updating in place starts from the committed bytes. Nothing else changes: an untracked file, a temp
file, or a process that is not a probe behaves exactly as without this module.
"""
import os
import sys


def _install():
    root = os.environ.get("INSPEXIMUS_PROBE_ROOT")
    shadow = os.environ.get("INSPEXIMUS_PROBE_SHADOW")
    listing = os.environ.get("INSPEXIMUS_PROBE_TRACKED")
    if not (root and shadow and listing and sys.argv and sys.argv[0]):
        return
    root = os.path.abspath(root)
    main = os.path.abspath(sys.argv[0])
    if os.path.normcase(os.path.dirname(main)) != os.path.normcase(os.path.join(root, "probes")):
        return
    try:
        with open(listing, encoding="utf-8") as fh:
            tracked = {os.path.normcase(os.path.join(root, ln.strip())) for ln in fh if ln.strip()}
    except OSError:
        return

    import builtins
    import io
    import shutil

    real_open = builtins.open
    real_replace, real_rename = os.replace, os.rename

    def shadow_of(path):
        try:
            p = os.path.abspath(os.fspath(path))
        except TypeError:                                    # an int file descriptor, not a path
            return None
        if isinstance(p, bytes):
            p = os.fsdecode(p)
        if os.path.normcase(p) not in tracked:
            return None
        return p, os.path.join(shadow, os.path.relpath(p, root))

    def guarded_open(file, mode="r", *args, **kwargs):
        m = shadow_of(file) if isinstance(file, (str, bytes, os.PathLike)) else None
        if m is not None:
            real, copy = m
            writing = any(c in mode for c in "wax+")
            if writing:
                os.makedirs(os.path.dirname(copy), exist_ok=True)
                if ("a" in mode or "+" in mode) and not os.path.exists(copy) and os.path.exists(real):
                    shutil.copyfile(real, copy)
            if writing or os.path.exists(copy):
                file = copy
                # PYTHON 3.9 AND EARLIER: Path.open passes `opener=self._opener`, which opens the
                # ORIGINAL path whatever name reaches open(), so Path.write_text escaped the shadow.
                # Measured: CI's 3.9 leg and a local 3.8 wrote the tracked file; 3.11 and 3.12 did not.
                # Dropping an opener bound to a path object leaves the default, which opens `file`.
                if isinstance(getattr(kwargs.get("opener"), "__self__", None), os.PathLike):
                    kwargs.pop("opener")
        return real_open(file, mode, *args, **kwargs)

    def guarded_move(real_fn):
        def move(src, dst, *args, **kwargs):
            m = shadow_of(dst)
            if m is not None:
                os.makedirs(os.path.dirname(m[1]), exist_ok=True)
                dst = m[1]
            return real_fn(src, dst, *args, **kwargs)
        return move

    builtins.open = guarded_open
    io.open = guarded_open
    os.replace = guarded_move(real_replace)
    os.rename = guarded_move(real_rename)


def _venv_base():
    """A venv child under the suite's temporary home can still start a process pool (A-25, 2026-09-28).

    This module is the suite's one startup hook, so the second job it has lives here too. On Windows,
    multiprocessing launches a venv's workers through sys._base_executable, because a worker started
    through the venv's own python.exe (a launcher) never receives its pipe (bpo-35797). In a venv built
    on the Microsoft Store Python, a process started with USERPROFILE redirected to the suite's home
    computes _base_executable as that launcher: claims_audit.py's pool broke or hung in the release
    venv, and under xdist its orphaned workers held a test's pipe for 900 s. No layout of the home
    avoids it. With an AppData\\Local in it the Store Python does not start (FileNotFoundError, or
    WinError 1920 through a junction); without one the venv falls back to the launcher. So this puts
    back the base interpreter the venv names in its own pyvenv.cfg, and only when the value has
    collapsed to the launcher. Anywhere else it changes nothing.

    A venv can be built from another venv's python.exe, so the named interpreter is followed until it
    is not a venv launcher itself: measured, a test venv made inside the release venv named the
    release venv's launcher, and its pool hung the same way."""
    exe = getattr(sys, "_base_executable", "")
    if os.name != "nt" or sys.prefix == sys.base_prefix or \
            os.path.normcase(exe) != os.path.normcase(sys.executable):
        return
    base = real_interpreter(sys.executable)
    if base and os.path.isfile(base):
        sys._base_executable = base


def real_interpreter(exe, hops=4):
    """The interpreter behind a venv launcher: each venv's pyvenv.cfg, followed until the path is not
    <venv>\\Scripts\\python.exe. None if the chain does not end within `hops` or cannot be read."""
    for _ in range(hops):
        cfg_path = os.path.join(os.path.dirname(os.path.dirname(exe)), "pyvenv.cfg")
        if not os.path.isfile(cfg_path):
            return exe
        try:
            with open(cfg_path, encoding="utf-8") as fh:
                cfg = {k.strip(): v.strip() for k, v in
                       (ln.split("=", 1) for ln in fh.read().splitlines() if "=" in ln)}
        except OSError:
            return None
        nxt = cfg.get("executable") or os.path.join(cfg.get("home", ""), "python.exe")
        if not nxt or os.path.normcase(nxt) == os.path.normcase(exe):
            return None
        exe = nxt
    return None


_venv_base()
_install()
