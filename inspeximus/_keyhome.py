"""The key home: where the receipt keys, the read-guard keys, the chain heads, the event salts and the user's own
config live. One resolver, so the six places that read it cannot disagree about which directory it is.

`INSPEXIMUS_KEY_HOME`, else APPDATA, else XDG_CONFIG_HOME, else ~/.config.

A KEY HOME INSIDE A REPOSITORY IS REFUSED (3.16.4, AUDIT-A F-13). Claude Code applies a project's settings `env`
to hooks and MCP servers, so a cloned repository can set INSPEXIMUS_KEY_HOME to a directory it ships. A key home
it controls let it forge a read-guard stamp, so an instruction-shaped record was served unquarantined, and it
made the repository's file the "user's own config" that the archive and embed rules trust. A value of
INSPEXIMUS_KEY_HOME that resolves inside a git work tree, or inside the project of the store it is asked about,
is ignored with one stderr line, and the per-user directory is used instead.
"""
import os
import sys

_NOTICE = []
_CACHE = {}


def default_home() -> str:
    """The per-user config directory, ignoring INSPEXIMUS_KEY_HOME."""
    from . import _envpolicy
    return (_envpolicy.other("APPDATA") or _envpolicy.other("XDG_CONFIG_HOME")
            or os.path.join(os.path.expanduser("~"), ".config"))


def _norm(p) -> str:
    return os.path.normcase(os.path.realpath(str(p)))


def _inside(child, parent) -> bool:
    try:
        return os.path.commonpath([child, parent]) == parent
    except ValueError:                                   # different drives
        return False


def _git_work_tree(path):
    """The nearest directory at or above `path` that holds `.git` (a work tree or a worktree file), or None."""
    d = path
    while True:
        if os.path.exists(os.path.join(d, ".git")):
            return d
        parent = os.path.dirname(d)
        if parent == d:
            return None
        d = parent


def _project_of(store_path):
    """The project a store belongs to: the git work tree above it; else, for a store in a `.inspeximus`
    directory (the hook's `<project>/.inspeximus/<store>`), that directory's parent; else None. A plain store
    file in a plain directory has no project here: a key home beside it is the accident the location guards
    (`core._guard_key_location`) already refuse with an error, and that contract stays theirs."""
    d = _norm(os.path.dirname(os.path.abspath(str(store_path))) or ".")
    tree = _git_work_tree(d)
    if tree:
        return tree
    return os.path.dirname(d) if os.path.basename(d) == ".inspeximus" else None


_MARKERS = (".git", ".claude", ".inspeximus")


def _cwd_project():
    """The project the process runs in: the nearest directory at or above the working directory that holds .git,
    .claude or .inspeximus, searched below the user's home only (the user's own ~/.claude is not a project); else
    the working directory itself, unless that is the home directory or above it, where a terminal starts.

    A hook runs with the repository as its working directory, and a repository's `.claude/settings.json` is what
    can set INSPEXIMUS_KEY_HOME (AUDIT-A F-13b: a zip download with no .git, its store named by
    INSPEXIMUS_CODING_STORE outside .inspeximus).

    Kept per working directory and home, as given, like `_CACHE`: every reader of the key home asks, and the
    walk normalizes each ancestor (measured: 10 more lower() calls per erasure in perf/gate.py without it)."""
    try:
        raw = (os.getcwd(), os.path.expanduser("~"))
    except OSError:
        return None
    if raw not in _CWD_PROJECT:
        _CWD_PROJECT[raw] = _walk_cwd_project(*raw)
    return _CWD_PROJECT[raw]


_CWD_PROJECT = {}


def _walk_cwd_project(cwd, home):
    cwd = _norm(cwd)
    home = _norm(home)
    d = cwd
    while _inside(d, home) and d != home:
        if any(os.path.exists(os.path.join(d, m)) for m in _MARKERS):
            return d
        parent = os.path.dirname(d)
        if parent == d:
            break
        d = parent
    if cwd == home or _inside(home, cwd):
        return None
    return cwd


def refusal(env_home, store_path=None):
    """Why `env_home` cannot be the key home, or None when it can."""
    k = _norm(env_home)
    proj = _cwd_project()
    key = (k, _norm(store_path) if store_path else None, proj)
    if key in _CACHE:
        return _CACHE[key]
    why = None
    tree = _git_work_tree(k)
    if tree:
        why = "it is inside the git work tree %s" % tree
    elif store_path and _project_of(store_path) and _inside(k, _project_of(store_path)):
        why = "it is inside the project of the store %s" % store_path
    elif proj and _inside(k, proj):
        why = "it is inside the project this process runs in, %s" % proj
    _CACHE[key] = why
    return why


def key_home(store_path=None) -> str:
    """The key home to use for `store_path` (or for no particular store)."""
    from . import _envpolicy
    env = (_envpolicy.raw("INSPEXIMUS_KEY_HOME") or "").strip()
    if not env:
        return default_home()
    why = refusal(env, store_path)
    if why is None:
        return env
    if not _NOTICE:
        _NOTICE.append(env)
        try:
            sys.stderr.write("[inspeximus] INSPEXIMUS_KEY_HOME=%s is ignored: %s, and a repository must not choose "
                             "where your keys and your config live. Using %s.%s"
                             % (env, why, default_home(), chr(10)))
        except Exception:                                # noqa: BLE001
            pass
    return default_home()
