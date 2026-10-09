"""When a store is reached through a link (3.16.5, AUDIT-A design 2026-10-07).

A repository can ship a link at `.inspeximus` or at the store file inside it. Followed blindly, the link makes another
project's store text reach the hook output, makes a hostile session write into that store, and creates `coding_memory.json`,
`nudge.json` and `secrets_notice.json` in any directory the link names.

A link, junction included, is followed only when one of three conditions holds:

  A  the user's own config names the target (`<key home>/inspeximus/config.json`, `{"stores": {"links": [...]}}`), which
     `inspeximus link <target>` writes;
  B  the target stays inside the project, or inside the main checkout of a git worktree, and never inside `.git`;
  C  the link is a git work tree's untracked file or directory, which a `git clone` cannot deliver.

Otherwise there is NO STORE: `StoreLinkRefused`, which names the link, the target and the fix. The refusal never falls back to
the default path, because a fallback writes a second store beside the refused link and the user never sees that memory moved.

`INSPEXIMUS_CODING_STORE` names a store a repository can choose, exactly as a shipped link does, because a repository's
Claude Code settings can set it. It gets the same conditions with A and B only: there is no link for C to judge.

A decision is made on the real path. The caller opens the store, its sidecars and its lock by the path returned here, so the
check and the open cannot see different files.

Condition C is not proof against a repository that ships its own `.git`, for example in a zip: a crafted index does not list the
link. A and B do not depend on git. The residual risk of C is recorded in the 3.16.5 report.
"""
import json
import os
import re

from . import _safewrite

CACHE_NAME = "store-links.json"


def _norm(p) -> str:
    return os.path.normcase(os.path.abspath(str(p)))


def _inside(path, root) -> bool:
    """`path` is `root` or below it. Both must already be real paths."""
    path, root = _norm(path), _norm(root)
    return path == root or path.startswith(root.rstrip(os.sep) + os.sep)


def _has_dot_git(path, root) -> bool:
    """A component of `path` below `root` is named `.git`."""
    rel = os.path.relpath(_norm(path), _norm(root))
    return any(part == os.path.normcase(".git") for part in rel.split(os.sep))


def user_config_file() -> str:
    from ._keyhome import key_home
    return os.path.join(key_home(), "inspeximus", "config.json")


def configured_links() -> list:
    """The real paths the user's own config names, `{"stores": {"links": [...]}}`."""
    try:
        with open(user_config_file(), encoding="utf-8") as fh:
            raw = (json.load(fh).get("stores") or {}).get("links") or []
    except (OSError, ValueError, AttributeError):
        return []
    return [os.path.realpath(p) for p in raw if isinstance(p, str) and p.strip()]


def add_configured_link(target) -> str:
    """Record `target` (a store directory or a store file) in the user's config. Returns the config path."""
    real = os.path.realpath(os.path.abspath(str(target)))
    path = user_config_file()
    try:
        with open(path, encoding="utf-8") as fh:
            cfg = json.load(fh)
        if not isinstance(cfg, dict):
            raise ValueError("config is not an object")
    except FileNotFoundError:
        cfg = {}
    stores = cfg.get("stores")
    if not isinstance(stores, dict):
        stores = cfg["stores"] = {}
    links = [p for p in (stores.get("links") or []) if isinstance(p, str)]
    if real not in links:
        links.append(real)
    stores["links"] = links
    os.makedirs(os.path.dirname(path), exist_ok=True)
    _safewrite.write_atomic(path, json.dumps(cfg, indent=2) + "\n")
    return path


def main_checkout(root):
    """The main checkout of the git worktree at `root`, or None.

    The `.git` file of a worktree names a git directory, and that directory's `commondir` names the main `.git`. A crafted
    `.git` file can name any directory, so the claim is accepted only when the main checkout's own record points back: its
    `worktrees/<name>/gitdir` file holds the path of THIS `.git` file, which a repository cannot write into another
    checkout."""
    dotgit = os.path.join(root, ".git")
    try:
        if not os.path.isfile(dotgit):
            return None
        with open(dotgit, encoding="utf-8") as fh:
            line = fh.read().strip()
        if not line.startswith("gitdir:"):
            return None
        gitdir = os.path.realpath(os.path.join(root, line[len("gitdir:"):].strip()))
        with open(os.path.join(gitdir, "commondir"), encoding="utf-8") as fh:
            common = os.path.realpath(os.path.join(gitdir, fh.read().strip()))
        with open(os.path.join(gitdir, "gitdir"), encoding="utf-8") as fh:
            back = os.path.realpath(fh.read().strip())
        if _norm(back) != _norm(os.path.join(os.path.realpath(root), ".git")):
            return None
        if os.path.basename(common) != ".git" or os.path.dirname(gitdir) != os.path.join(common, "worktrees"):
            return None
        return os.path.dirname(common)
    except OSError:
        return None


def _project_roots(cwd) -> list:
    """The real directories condition B accepts: the project (the git work tree, else the launch directory) and, for a
    worktree, its main checkout."""
    from ._surface import find_project_root
    base = os.path.abspath(cwd or os.getcwd())
    root = find_project_root(base) or base
    out = [os.path.realpath(root)]
    main = main_checkout(root)
    if main:
        out.append(os.path.realpath(main))
    return out


def _inside_project(real_paths, cwd) -> bool:
    for root in _project_roots(cwd):
        if all(_inside(p, root) and not _has_dot_git(p, root) for p in real_paths):
            return True
    return False


#: The only variables git sees (AUDIT-A F-30). A project's Claude Code settings can set the environment of the hook, and
#: `GIT_DIR`, `GIT_WORK_TREE`, `GIT_INDEX_FILE` or `GIT_CONFIG_*` make a tracked link look untracked. Everything else, every
#: `GIT_*` included, is dropped; `GIT_OPTIONAL_LOCKS` and `GIT_TERMINAL_PROMPT` are set here.
_GIT_ENV_KEEP = ("PATH", "HOME", "USERPROFILE", "SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "COMSPEC", "PATHEXT", "TEMP", "TMP",
                 "TMPDIR", "LANG", "LC_ALL")
#: Seconds one `git ls-files` may take (AUDIT-A F-32: an index that is a FIFO blocks it, and a prompt resolves the store 3 times).
GIT_TIMEOUT_S = 2
#: (root, relative path) whose git call failed in this process: not asked again, and the link stays refused.
_GIT_FAILED = set()


def _git_env() -> dict:
    from . import _envpolicy
    env = _envpolicy.child_env(keep=lambda k: k.upper() in _GIT_ENV_KEEP)
    env.update(GIT_OPTIONAL_LOCKS="0", GIT_TERMINAL_PROMPT="0")
    return env


def _git_tracks(root, rel) -> "bool | None":
    """True when git tracks `rel` in the work tree at `root`, False when it does not, None when git cannot say."""
    from . import _envpolicy                    # the one place a process starts (3.18, AUDIT-A I-4)
    # No repository config may run a program: no fsmonitor hook, no hooks path, no untracked cache. `--literal-pathspecs`
    # keeps a link named like a glob from matching a tracked sibling.
    cmd = ["git", "--no-optional-locks", "--literal-pathspecs", "-c", "core.fsmonitor=false", "-c", "core.untrackedCache=false",
           "-c", "core.hooksPath=" + os.devnull, "-C", root, "ls-files", "--error-unmatch", "--", rel]
    try:
        r = _envpolicy.start(cmd, capture_output=True, env=_git_env(), timeout=GIT_TIMEOUT_S, stdin=_envpolicy.DEVNULL)
    except (OSError, _envpolicy.SubprocessError):
        return None
    if r.returncode == 0:
        return True
    return False if r.returncode == 1 else None


def _git_dir(root):
    """The git directory of the work tree at `root`: `.git` itself, or the directory a worktree's `.git` FILE names. A worktree's
    index lives there, so a cache key that looked only at `<root>/.git/index` never saw a commit made in a worktree (F-31)."""
    dotgit = os.path.join(root, ".git")
    try:
        if os.path.isdir(dotgit):
            return dotgit
        with open(dotgit, encoding="utf-8") as fh:
            line = fh.read().strip()
        if line.startswith("gitdir:"):
            return os.path.realpath(os.path.join(root, line[len("gitdir:"):].strip()))
    except OSError:
        pass
    return None


def _cache_path() -> str:
    from ._keyhome import key_home
    return os.path.join(key_home(), "inspeximus", CACHE_NAME)


def _cache_key(link, real, root) -> str:
    """A decision holds while the link, its target and the repository's index are what they were."""
    def stamp(p):
        try:
            st = os.lstat(p)
            return "%d:%d" % (st.st_mtime_ns, st.st_size)
        except OSError:
            return "-"
    gitdir = _git_dir(root)
    parts = [_norm(link), _norm(real), stamp(link), stamp(os.path.join(gitdir, "index")) if gitdir else "-"]
    import hashlib
    return hashlib.sha256("|".join(parts).encode("utf-8", "replace")).hexdigest()


def _untracked(links, root) -> bool:
    """Condition C for every link in `links` (lexical paths): a git work tree at `root` that does not track any of them.
    Runs last and is cached per link: git costs 37 ms on Windows (AUDIT-A)."""
    if not os.path.exists(os.path.join(root, ".git")):
        return False
    cache_file = _cache_path()
    try:
        with open(cache_file, encoding="utf-8") as fh:
            cache = json.load(fh)
        cache = cache if isinstance(cache, dict) else {}
    except (OSError, ValueError):
        cache = {}
    dirty = False
    for link in links:
        key = _cache_key(link, os.path.realpath(link), root)
        verdict = cache.get(key)
        if verdict is None:
            rel = os.path.relpath(os.path.abspath(link), os.path.abspath(root)).replace(os.sep, "/")
            if (_norm(root), rel) in _GIT_FAILED:
                return False                      # git failed for this link earlier in this process: not asked again
            tracked = _git_tracks(root, rel)
            if tracked is None:
                _GIT_FAILED.add((_norm(root), rel))
                return False                      # git could not say: no store, and no cache entry
            verdict = cache[key] = {"untracked": not tracked}
            dirty = True
        if not verdict.get("untracked"):
            return False
    if dirty:
        try:
            os.makedirs(os.path.dirname(cache_file), exist_ok=True)
            _safewrite.write_atomic(cache_file, json.dumps(cache) + "\n")
        except OSError:
            pass
    return True


def _components(path) -> list:
    """The components of `path`, split on the separators of THIS system only (AUDIT-A F-39). On POSIX a backslash is a file name
    character, and git can ship a link named `a\b`: splitting on it would turn one component into two and walk past the link."""
    seps = os.sep + (os.altsep or "")
    return re.split("[" + re.escape(seps) + "]+", str(path))


def has_dotdot(path) -> bool:
    return ".." in _components(path)


def _physical_links(raw, root) -> list:
    """The links on the way to `raw`, walked as the kernel walks it (AUDIT-A F-38): `..` goes up from the directory the walk is
    really in, which after a link is the link's target and not the link's parent. `os.path.abspath` collapses `evil/..` lexically
    and never sees `evil`. Only the links that lie inside the project are returned."""
    raw = str(raw)
    if not os.path.isabs(raw):
        raw = os.path.join(os.getcwd(), raw)
    drive, rest = os.path.splitdrive(raw)
    cur = drive + os.sep
    out = []
    for part in _components(rest):
        if not part or part == ".":
            continue
        if part == "..":
            cur = os.path.dirname(os.path.realpath(cur)) or cur
            continue
        cur = os.path.join(cur, part)
        if _safewrite.is_link(cur) and (not root or _inside(cur, root)):
            out.append(cur)
    return out


def link_chain(directory, filename, root) -> list:
    """Every link, a junction included, on the way from the project root down to the store file (AUDIT-A F-35).

    A shipped DIRECTORY link in the middle of a path (`.inspeximus/memory.json`, with `.inspeximus` the link) leads outside as
    surely as a link at the file, and a check of the last component alone never sees it. The walk tests each component below the
    root with `_safewrite.is_link`, which sees a junction (`os.path.islink` does not). A path outside the project has no
    components of the project's to judge: only the directory and the file themselves are tested then."""
    d = str(directory)
    f = os.path.join(d, filename)
    if has_dotdot(f):
        return _physical_links(f, root)
    if root and _inside(f, root):
        out, cur = [], os.path.abspath(root)
        for part in os.path.relpath(os.path.abspath(f), cur).split(os.sep):
            cur = os.path.join(cur, part)
            if _safewrite.is_link(cur):
                out.append(cur)
        return out
    return [p for p in (d, f) if _safewrite.is_link(p)]


def vet(directory, filename, cwd=None, named_by_env=False, root=None):
    """The (directory, file) the caller must open, or `StoreLinkRefused`.

    With no link and no environment override, the two lexical paths come back unchanged after one `lstat` per path component
    below the project root (two for the default store). With a link, or with an override, both come back as REAL paths."""
    from ._surface import StoreLinkRefused, find_project_root
    d = str(directory)
    f = os.path.join(d, filename)
    root = root or find_project_root(os.path.abspath(cwd or os.getcwd())) or os.path.abspath(cwd or os.getcwd())
    links = link_chain(d, filename, root)
    if not links and not named_by_env:
        return d, f
    real_d, real_f = os.path.realpath(d), os.path.realpath(f)
    listed = configured_links()
    if listed and any(_norm(x) in (_norm(real_d), _norm(real_f)) for x in listed):                    # A
        return real_d, real_f
    up = bool(links) and has_dotdot(f)               # a path that goes back UP through a link means two things on two systems (F-38)
    if not up and _inside_project((real_d, real_f), cwd):                                              # B
        return real_d, real_f
    if links and not has_dotdot(f):                 # a path that goes back UP through a link is not a link a user made (F-38)
        git_root = find_project_root(os.path.abspath(cwd or os.getcwd()))
        if git_root and _untracked(links, git_root):                                                   # C
            return real_d, real_f
    shown = os.path.abspath(links[0]) if links else os.path.abspath(d)
    target = os.path.realpath(links[0]) if links else real_d
    raise StoreLinkRefused(_model_text(links), path=shown, user_line=_user_text(links, shown, target))


def clean(text) -> str:
    """`text` with control characters replaced: a path the repository chose is data, and it must not carry a line break."""
    return "".join("?" if (ord(c) < 32 or ord(c) == 127) else c for c in str(text))


def _model_text(links) -> str:
    """The refusal as the MCP server hands it to the model (AUDIT-A F-34): the problem, and what to ask. It carries no target
    path and no command, because a path a repository chose can carry words, and a command the model runs would make the link
    legitimate."""
    what = "the store location is set by an environment variable" if not links else \
        "the store of this project is reached through a link (%s)" % clean(os.path.basename(links[0]) or "link")
    return ("%s, and it leads outside this project to a place your config does not name. No store is used and nothing is written. "
            "Ask the user whether that store is theirs. Only the user can allow it; see docs/store-links.md." % what)


def _user_text(links, shown, target) -> str:
    """The same refusal for the person at the hook's stderr line: the link, where it leads, and the command to allow it."""
    how = ("INSPEXIMUS_CODING_STORE names %s" % clean(shown)) if not links else ("%s is a link to %s" % (clean(shown), clean(target)))
    return ("%s, which is outside this project and is not named in your config. No store is used and nothing is written. "
            "If this store is yours, run in a terminal: inspeximus link %s" % (how, clean(target)))


def dir_allowed(directory, filename, cwd=None) -> bool:
    """`vet` as a yes or no, for reads of files beside the store such as a project's config."""
    try:
        vet(directory, filename, cwd)
        return True
    except OSError:
        return False
