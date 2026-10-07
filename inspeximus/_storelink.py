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


def _git_tracks(root, rel) -> "bool | None":
    """True when git tracks `rel` in the work tree at `root`, False when it does not, None when git cannot say."""
    import subprocess                           # imported here: the hook's import time is measured, and git runs rarely
    cmd = ["git", "-c", "core.fsmonitor=false", "-C", root, "ls-files", "--error-unmatch", "--", rel]
    env = dict(os.environ, GIT_OPTIONAL_LOCKS="0", GIT_TERMINAL_PROMPT="0")
    try:
        r = subprocess.run(cmd, capture_output=True, env=env, timeout=10, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError):
        return None
    if r.returncode == 0:
        return True
    return False if r.returncode == 1 else None


def _cache_path() -> str:
    from ._keyhome import key_home
    return os.path.join(key_home(), "inspeximus", CACHE_NAME)


def _cache_key(link, real, root) -> str:
    """A decision holds while the link, its target and the repository's index are what they were."""
    def stamp(p):
        try:
            return str(os.lstat(p).st_mtime_ns)
        except OSError:
            return "-"
    index = os.path.join(root, ".git", "index") if os.path.isdir(os.path.join(root, ".git")) else ""
    parts = [_norm(link), _norm(real), stamp(link), stamp(index) if index else "-"]
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
            tracked = _git_tracks(root, rel)
            if tracked is None:
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


def vet(directory, filename, cwd=None, named_by_env=False):
    """The (directory, file) the caller must open, or `StoreLinkRefused`.

    With no link and no environment override, the two lexical paths come back unchanged after two `lstat` calls. With a link,
    or with an override, both come back as REAL paths."""
    from ._surface import StoreLinkRefused, find_project_root
    d = str(directory)
    f = os.path.join(d, filename)
    links = [p for p in (d, f) if _safewrite.is_link(p)]
    if not links and not named_by_env:
        return d, f
    real_d, real_f = os.path.realpath(d), os.path.realpath(f)
    listed = configured_links()
    if listed and any(_norm(x) in (_norm(real_d), _norm(real_f)) for x in listed):                    # A
        return real_d, real_f
    if _inside_project((real_d, real_f), cwd):                                                         # B
        return real_d, real_f
    if links:
        root = find_project_root(os.path.abspath(cwd or os.getcwd()))
        if root and _untracked(links, root):                                                           # C
            return real_d, real_f
    shown = os.path.abspath(links[0]) if links else os.path.abspath(d)
    target = real_f if links and links[0] == f else real_d
    how = ("INSPEXIMUS_CODING_STORE names %s" % shown) if not links else ("%s is a link to %s" % (shown, target))
    raise StoreLinkRefused(
        "%s, which is outside this project and is not named in your config. No store is used and nothing is written. "
        "If this store is yours, run: inspeximus link %s" % (how, target), path=shown)


def dir_allowed(directory, filename, cwd=None) -> bool:
    """`vet` as a yes or no, for reads of files beside the store such as a project's config."""
    try:
        vet(directory, filename, cwd)
        return True
    except OSError:
        return False
