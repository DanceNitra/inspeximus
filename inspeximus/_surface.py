"""One way to open a store from a SURFACE — the CLI, the MCP server, the editor hook, the adapters.

HISTORY, kept because it is the argument for why this module exists. `echo_guard` used to default to OFF
in the library and ON at each surface: a direct API user "got exactly what they constructed", while the
CLI and `inspeximus-mcp` — documented as sharing one store — both turned it on from `INSPEXIMUS_ECHO_GUARD`.

The nine framework adapters never got that memo. Each built `Inspeximus(path=...)` directly, inheriting
the library default, and the consequence was not cosmetic — measured on one store file:

    1. CLI corrects the payout wallet 0xAAA -> 0xBBB      store serves 0xBBB
    2. an adapter restates the OLD value                  store serves 0xAAA   <- the correction is undone
    3. CLI corrects it again                              store serves 0xAAA   <- and now it is STUCK

Step 3 is the part that makes it serious. Once the retired value is active again, the honest re-correction
looks like an echo of the value the guard just retired, so the guard refuses it. The store cannot be put
right through the same surface that broke it, and "correct a fact once and it stays corrected" — the first
line of the README — is false through the ordinary integration path.

The receipts rule has the same shape: a store that already has a `.receipts.json` sidecar keeps receipts on,
so a surface write cannot silently punch a hole in the evidence chain. That rule also lived in cli._store
alone, while `mcp_server` read the env var only and `claude_code` passed nothing.

Both rules live here now, and every surface calls this. A default that has to be re-declared at each entry
point is a default that will be missed at one of them; it already was, at ten.

SINCE 1.87.0 the guard defaults to ON in the library too, so the split this module was built to paper over
is gone at its source. The lesson outlived it: keeping the two in agreement by CONVENTION lasted exactly
as long as nobody added a tenth entry point. `echo_guard_default()` now delegates to the library's own
resolver rather than re-deriving it, and this module's remaining job is the receipts rule and the path
rule — the same class of default, still declared once.
"""
from __future__ import annotations

import os


def echo_guard_default() -> bool:
    """ON unless `INSPEXIMUS_ECHO_GUARD=0`. Delegates: since 1.87.0 the LIBRARY resolves the posture the
    same way, so there is one rule rather than two that can drift apart -- and they had: this function
    honoured the env var while `Inspeximus()` hardcoded True and took no argument, so the documented
    off-switch worked through a surface and was silently ignored by a direct API caller."""
    from .core import _resolve_echo_guard
    return _resolve_echo_guard()


class StoreScopeError(ValueError):
    """`INSPEXIMUS_SCOPE` asked for a store location that could not be resolved."""


def find_project_root(cwd=None):
    """The nearest enclosing git repository root, or None. Zero dependencies: walks up looking for `.git`.

    `.git` is accepted as a DIRECTORY or a FILE — a git worktree and a submodule both carry a `.git` file
    holding a `gitdir:` pointer, and treating only the directory as a repo would silently fail to find the
    root in exactly those checkouts (this repo's own development happens in worktrees).
    """
    p = os.path.abspath(cwd or os.getcwd())
    while True:
        if os.path.exists(os.path.join(p, ".git")):
            return p
        parent = os.path.dirname(p)
        if parent == p:                       # filesystem root reached
            return None
        p = parent


#: The Claude Code hook's store filename. The MCP server reaches the same file through
#: `INSPEXIMUS_SCOPE=claude-code`, so there is one project store and not two.
CODING_STORE_FILENAME = "coding_memory.json"


def coding_store_dir(cwd=None, env=None) -> str:
    """The directory holding a project's Claude Code store: `<git root or cwd>/.inspeximus`.

    `$INSPEXIMUS_CODING_STORE` (a directory) overrides it. This is the ONE resolver for that store.
    The hook and the MCP server each used to decide it themselves, and measured on 3.9.5 they
    disagreed twice: the plugin pointed the server at `.inspeximus/memory.json` while the hook read
    `.inspeximus/coding_memory.json`, and the server's `${CLAUDE_PROJECT_DIR}` is the LAUNCH
    directory (Claude Code 2.1.282 sets it to wherever `claude` started) while the hook walks up to
    the git root. A decision written through MCP never reached the next session's SessionStart.
    """
    env = os.environ if env is None else env
    override = (env.get("INSPEXIMUS_CODING_STORE") or "").strip()
    if override:
        return override
    base = cwd or os.getcwd()
    return os.path.join(find_project_root(base) or base, ".inspeximus")


def coding_store_path(cwd=None, env=None) -> str:
    """The Claude Code store file. See `coding_store_dir`."""
    return os.path.join(coding_store_dir(cwd, env), CODING_STORE_FILENAME)


def resolve_path(path=None, *, env=None, cwd=None) -> str:
    """`--path`, else `$INSPEXIMUS_PATH`, else `$INSPEXIMUS_SCOPE`, else the documented default filename.

    THE BUG THIS CLOSES. The default is the RELATIVE filename `inspeximus_memory.json`, and an MCP stdio
    server does not choose its own working directory — the host does. So the same agent, with the same
    config, talked to a DIFFERENT store depending on where its client happened to start, and nothing said
    so: writes succeeded, recalls came back empty, and the store that held the memories was one directory
    away. Silent, and worse than having no scoping at all.

    `INSPEXIMUS_SCOPE` (opt-in) fixes it by anchoring the store to something stable:
        user     — today's behaviour, stated explicitly (the cwd-relative default filename).
        project  — `<git-root>/.inspeximus/memory.json`, an ABSOLUTE path. Identical from every directory
                   inside the repo; different between repos.
        claude-code — the Claude Code hook's store, `<git-root or cwd>/.inspeximus/coding_memory.json`.
                   The plugin and `inspeximus install --ide claude` set it, so a decision written through
                   MCP is the one the next session's SessionStart hook reads.
    UNSET is `user`, so nothing changes for anyone who does not ask.

    A `project` scope with no enclosing git repository RAISES rather than falling back to the cwd-relative
    default — the fallback would reintroduce the very cwd-dependence the scope was set to remove, and would
    do it silently, which is how a store ends up "empty" for reasons nobody can see.

    PRECEDENCE: an explicit `--path` or `$INSPEXIMUS_PATH` beats the scope, because naming a file is the
    more specific instruction. That combination is reported by the MCP `where_am_i` tool (`path_source`)
    rather than left to be inferred — a scope silently outranked is the same class of defect as a scope
    silently resolved.
    """
    env = os.environ if env is None else env
    if path:
        return path
    from_env = env.get("INSPEXIMUS_PATH")
    if from_env:
        return from_env
    scope = (env.get("INSPEXIMUS_SCOPE") or "").strip().lower()
    if scope in ("", "user"):
        return "inspeximus_memory.json"
    if scope == "project":
        root = find_project_root(cwd)
        if root is None:
            raise StoreScopeError(
                f"INSPEXIMUS_SCOPE=project needs an enclosing git repository, and none was found above "
                f"{os.path.abspath(cwd or os.getcwd())!r}. Refusing to fall back to the working-directory "
                f"default, because that is the cwd-dependent behaviour this scope exists to remove. "
                f"Either run inside a repository, or set INSPEXIMUS_PATH to an absolute file.")
        return os.path.join(root, ".inspeximus", "memory.json")
    if scope == "claude-code":
        # The Claude Code hook's store, resolved by the hook's own rule. No repository is not an error
        # here, because the hook falls back to the working directory and this must land beside it.
        return coding_store_path(cwd, env)
    raise StoreScopeError(f"INSPEXIMUS_SCOPE={scope!r} is not a known scope; "
                          f"use 'user', 'project' or 'claude-code'")


def open_store(path=None, *, receipts: bool = False, persist_vectors: bool = False, embed=None,
               resolve: bool = True, **kwargs):
    """Open a store the way a SURFACE should: shared echo-guard posture, receipts kept if already on.

    `resolve=False` keeps an explicit `path=None` (an in-memory store) instead of falling back to the
    default filename — the adapters that mean "no file" need that.
    """
    from inspeximus import Inspeximus

    p = resolve_path(path) if resolve else path
    # A store that ALREADY has a receipt chain keeps it. Detected from the sidecar rather than a flag,
    # because a user who enabled receipts in Python should not have to re-declare them at every call.
    if not receipts and p and os.path.exists(str(p) + ".receipts.json"):
        receipts = True

    store = Inspeximus(path=p, embed=embed, persist_vectors=persist_vectors, receipts=receipts, **kwargs)
    store.echo_guard = echo_guard_default()
    return store
