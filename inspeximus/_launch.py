"""How inspeximus starts one of its own modules for a project: the working directory is never on the import path,
and the user site stays on it.

THE TWO REQUIREMENTS (3.16.4, AUDIT-A F-15 and F-22). Claude Code, Codex and the other hosts run a hook with the
project as the working directory, and `python -m` puts that directory first on sys.path, so a repository holding
`inspeximus/` ran its own code as the hook (F-15). The first fix used `python -I`, which also drops the user
site, so an inspeximus installed with `pip install --user` (the default on Windows Store Python) failed with "No
module named 'inspeximus'" in every hook and in the MCP server (F-22).

  * -E on every form: a host applies a project's settings `env` to hooks, so PYTHONPATH, PYTHONHOME and
    PYTHONUSERBASE can come from the repository (AUDIT-A, measured with Claude Code 2.1.291). -E ignores every
    PYTHON* variable, as -I did, without -I's -s: the user site at its default location stays on the path.
  * Python 3.11 and later: `python -E -P -m <module>`. -P leaves the working directory out.
  * Python 3.9 and 3.10, or a version this code cannot know (a static file, uvx's choice of interpreter): `python
    -E -c SHIM <module>`. The shim removes '' and '.' from sys.path, then runs the module the way `-m` does
    (`runpy._run_module_as_main`, the function `-m` itself calls). It holds no quote character, so it survives
    bash, cmd and a single-quoted `sh -c` unchanged.
"""
import subprocess
import sys

#: The package index every uvx command names (3.16.4, AUDIT-A F-23). A host applies a project's settings `env`
#: to hooks, so UV_INDEX_URL can come from the repository and choose where the hook's code is fetched from; the
#: flag beats the variable (measured with uv 0.11.9: a dead UV_INDEX_URL and this flag, and the hook ran).
DEFAULT_INDEX = "https://pypi.org/simple"
UVX_INDEX_ARGS = ["--default-index", DEFAULT_INDEX]

SHIM = ("import runpy,sys;sys.path[:]=[p for p in sys.path if p not in (str(),chr(46))];"
        "runpy._run_module_as_main(sys.argv.pop(1))")

_VERSIONS = {}


def which(name):
    """`shutil.which`, never answering from the working directory (3.16.4, EM, measured).

    On Windows, Python 3.9 to 3.11 search the working directory before PATH: with `uvx.bat` in the directory,
    `shutil.which("uvx")` returned `.\\uvx.BAT` (3.10.20 and 3.11.15; 3.12 does not). An install run inside a
    repository would then write the repository's uvx, python or hermes into every agent's config, where it stays.
    A hit in the working directory is set aside and PATH is searched without it."""
    import os
    import shutil
    cand = shutil.which(name)
    if not cand:
        return None
    try:
        cwd = os.path.normcase(os.path.realpath(os.getcwd()))
    except OSError:
        return cand
    here = os.path.normcase(os.path.realpath(os.path.dirname(os.path.abspath(cand))))
    if here != cwd:
        return cand
    exts = [""]
    if os.name == "nt":
        exts += [e for e in os.environ.get("PATHEXT", ".COM;.EXE;.BAT;.CMD").split(os.pathsep) if e]
    for d in os.environ.get("PATH", "").split(os.pathsep):
        if not d or d == os.curdir:
            continue
        try:
            if os.path.normcase(os.path.realpath(d)) == cwd:
                continue
        except OSError:
            continue
        for e in exts:
            p = os.path.join(d, name + e)
            if os.path.isfile(p) and os.access(p, os.X_OK):
                return p
    return None


def module_args(module, version=None) -> list:
    """The arguments after the interpreter that run `module` with no working directory on the import path."""
    v = tuple(version or ())[:2]
    if v >= (3, 11):
        return ["-E", "-P", "-m", module]
    return ["-E", "-c", SHIM, module]


def module_command(module, version=None) -> str:
    """`module_args` as one command-line fragment, the shim in double quotes."""
    return " ".join('"%s"' % a if " " in a else a for a in module_args(module, version))


def user_command(module) -> str:
    """The command to print for a person, in the form this interpreter runs."""
    return "python " + module_command(module, sys.version_info)


def interpreter_version(exe):
    """(major, minor) of the interpreter at `exe`, asked once per process; None when it cannot be asked."""
    if exe in _VERSIONS:
        return _VERSIONS[exe]
    v = None
    try:
        if exe and str(exe) == sys.executable:
            v = tuple(sys.version_info[:2])
        else:
            out = subprocess.run([str(exe), "-c", "import sys;print(sys.version_info[0],sys.version_info[1])"],
                                 capture_output=True, text=True, timeout=30).stdout.split()
            v = (int(out[0]), int(out[1])) if len(out) == 2 else None
    except (OSError, ValueError, subprocess.SubprocessError):
        v = None
    _VERSIONS[exe] = v
    return v
