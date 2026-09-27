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
            if any(c in mode for c in "wax+"):
                os.makedirs(os.path.dirname(copy), exist_ok=True)
                if ("a" in mode or "+" in mode) and not os.path.exists(copy) and os.path.exists(real):
                    shutil.copyfile(real, copy)
                file = copy
            elif os.path.exists(copy):
                file = copy
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


_install()
