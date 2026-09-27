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


class StoreLocationError(FileNotFoundError):
    """A store path whose directory does not exist, outside the locations inspeximus creates itself."""


def relpath_or_abs(path, start=None):
    """`path` relative to `start` (default: the working directory), or absolute when that is impossible.

    On Windows os.path.relpath raises ValueError when the two are on different drives, and a store on D:
    with the working directory on C: is an ordinary setup (Windows CI trial, 2026-09-28: a checkout on D:
    and the temp folder on C: crashed make_examples.py)."""
    try:
        return os.path.relpath(path, start) if start is not None else os.path.relpath(path)
    except ValueError:
        return os.path.abspath(path)


def git_bash_misread(path):
    """The intended path when `path` is a Git Bash `/c/...` path that Windows read as `C:\\c\\...`, or None.

    Found on 2026-09-28 on a friend's machine: in a Git Bash that does not convert paths for native
    programs, `python -m venv "$HOME/.inspeximus/venv"` got `/c/Users/<you>/...` and created
    `C:\\c\\Users\\<you>\\.inspeximus\\venv`, silently and with exit 0; on our own machine the same class had
    left eleven files from other sessions under `C:\\c\\Users\\...`. Recognised by a single-letter second
    component whose drive holds the rest: `C:\\c\\Users\\x` when `C:\\Users` exists."""
    if os.name != "nt" or not path:
        return None
    import re
    p = os.path.abspath(os.path.expanduser(str(path)))
    m = re.match(r"^([A-Za-z]):\\([A-Za-z])\\([^\\]+)(\\.*)?$", p)
    if not m:
        return None
    real = f"{m.group(2).upper()}:\\{m.group(3)}"
    return real + (m.group(4) or "") if os.path.isdir(real) else None


def store_location_problem(path):
    """Why a store at `path` must not be opened, or None.

    A STORE IN A DIRECTORY THAT DOES NOT EXIST IS REFUSED (3.15.3, AUDIT-A A-11). Opening one used to create
    the directory and serve an empty store: a typo in INSPEXIMUS_PATH, or a Git Bash path, gave the agent an
    empty memory with `isError: false`, and a READ changed the filesystem. The one exception is a directory
    inspeximus owns, `.inspeximus` under a folder that exists (`~/.inspeximus`, `<project>/.inspeximus`):
    the first write creates it, and until then the store simply has no records yet."""
    if not path:
        return None
    p = os.path.abspath(os.path.expanduser(str(path)))
    real = git_bash_misread(p)
    if real:
        return (f"store path {path}: this is the Git Bash path /{real[0].lower()}{real[2:].replace(os.sep, '/')} "
                f"read by Windows as a folder under {p[:3]}, which is a different, empty place. The intended "
                f"path is {real}. Pass it in that form.")
    parent = os.path.dirname(p)
    if os.path.isdir(parent):
        return None
    if os.path.basename(parent) == ".inspeximus" and os.path.isdir(os.path.dirname(parent)):
        return None
    blocker = parent
    while not os.path.exists(blocker) and os.path.dirname(blocker) != blocker:
        blocker = os.path.dirname(blocker)
    if os.path.exists(blocker) and not os.path.isdir(blocker):
        # A FILE WHERE A FOLDER SHOULD BE. Before 3.15.3 a write here failed with NOT PERSISTED and a read
        # served an empty store; now both are refused, and the message keeps the marker scripts look for.
        return (f"NOT PERSISTED: store path {path}: {blocker} is a file, not a directory, so no store can be "
                f"read or written there. Correct the path.")
    top = parent
    while not os.path.isdir(os.path.dirname(top)) and os.path.dirname(top) != top:
        top = os.path.dirname(top)
    hint = ""
    try:
        import difflib
        base = os.path.dirname(top)
        near = difflib.get_close_matches(os.path.basename(top), [d for d in os.listdir(base)
                                                                  if os.path.isdir(os.path.join(base, d))],
                                         n=1, cutoff=0.8)
        if near:
            hint = f" Did you mean {os.path.join(base, near[0]) + p[len(top):]}?"
    except OSError:
        pass
    return (f"store path {path}: no such directory {parent}; inspeximus does not create it, because a "
            f"mistyped path would otherwise be a new, empty memory.{hint} Create the directory first, or correct "
            f"the path.")


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

#: Written by `inspeximus install --all`: {"store": "<absolute store file>"}. Its presence is what makes
#: every agent on this machine share one store; without it nothing changes for anyone.
SHARED_CONFIG_FILENAME = "shared.json"


def shared_config_path() -> str:
    """`~/.inspeximus/shared.json`, the user-level record of the shared store."""
    return os.path.join(os.path.expanduser("~"), ".inspeximus", SHARED_CONFIG_FILENAME)


def shared_store_path():
    """The shared store file `inspeximus install --all` recorded, or None.

    ONE MEMORY FOR EVERY AGENT (3.14.0). Measured on 3.13.0 in a clean sandbox: the Claude Code entry
    resolved to `<git root>/.inspeximus/coding_memory.json` and every other host's entry to
    `inspeximus_memory.json` in whatever directory the host launched it from, so five agents kept five
    or more stores. A user-level store is the one location every host can reach whatever its launch
    directory, and this file is how the Claude Code hooks, which cannot carry an environment variable,
    find it too."""
    try:
        import json
        with open(shared_config_path(), encoding="utf-8") as fh:
            f = json.load(fh).get("store")
        return f if isinstance(f, str) and f.strip() and os.path.isabs(f) else None
    except Exception:
        return None


def shared_record() -> dict:
    """Everything `inspeximus install --all` recorded in shared.json, or {}: the store, the agents it
    wired, and since 3.14.4 the SEAL, the id and `immutable_sha256` of the setup decision it wrote."""
    try:
        import json
        with open(shared_config_path(), encoding="utf-8") as fh:
            d = json.load(fh)
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


#: Which agents have shown the one-time "memory active" line for which install seal.
ANNOUNCED_FILENAME = "announced.json"


def _announced_path() -> str:
    return os.path.join(os.path.expanduser("~"), ".inspeximus", ANNOUNCED_FILENAME)


def announcement(agent: str, label: str, store_path, records: int):
    """The one-time line an agent shows its user in the first session after `install --all` (3.14.4),
    or None: no install seal, this agent already showed it for this seal, or the agent's store is not the
    shared one (the line would then name a store the agent does not read).

    Found on 2026-09-27: the owner saw no sign that the install worked until he asked an agent. The line
    is shown once per install seal, so a re-install shows it again and an ordinary session does not."""
    rec = shared_record()
    seal = (rec.get("seal") or {}).get("id")
    store = rec.get("store")
    if not seal or not store or not store_path:
        return None
    try:
        if os.path.normcase(os.path.abspath(str(store_path))) != os.path.normcase(os.path.abspath(store)):
            return None
        import json
        with open(_announced_path(), encoding="utf-8") as fh:
            if (json.load(fh) or {}).get(agent) == seal:
                return None
    except Exception:                       # no marker yet, or an unreadable one: show the line
        pass
    others = [a for a in rec.get("agents") or [] if isinstance(a, str) and a != label]
    return ("inspeximus memory active: %d record%s" % (int(records), "" if int(records) == 1 else "s")
            + (", shared with " + ", ".join(others) if others else ""))


def mark_announced(agent: str) -> None:
    """Record that `agent` showed the line for the current install seal. Never raises."""
    try:
        import json
        seal = (shared_record().get("seal") or {}).get("id")
        if not seal:
            return
        p = _announced_path()
        try:
            with open(p, encoding="utf-8") as fh:
                d = json.load(fh)
            d = d if isinstance(d, dict) else {}
        except Exception:
            d = {}
        d[agent] = seal
        os.makedirs(os.path.dirname(p), exist_ok=True)
        tmp = p + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(d, fh)
        os.replace(tmp, p)
    except Exception:
        pass


def coding_store_dir(cwd=None, env=None) -> str:
    """The directory holding a project's Claude Code store: `<git root or cwd>/.inspeximus`.

    `$INSPEXIMUS_CODING_STORE` (a directory) overrides it, and after that the shared store recorded by
    `inspeximus install --all` (see `shared_store_path`). This is the ONE resolver for that store.
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
    shared = shared_store_path()                 # `inspeximus install --all` (3.14.0)
    if shared:
        return os.path.dirname(shared)
    base = cwd or os.getcwd()
    return os.path.join(find_project_root(base) or base, ".inspeximus")


def coding_store_path(cwd=None, env=None) -> str:
    """The Claude Code store file. See `coding_store_dir`. The shared store keeps its own file name, so a
    store a user had already named (for example `mcp_memory_chain.json`) can be the shared one."""
    env_ = os.environ if env is None else env
    if not (env_.get("INSPEXIMUS_CODING_STORE") or "").strip():
        shared = shared_store_path()
        if shared:
            return shared
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
    if scope == "":
        # THE SHARED STORE, WHEN `install --all` RECORDED ONE (3.14.3). The hooks already read the record
        # (coding_store_path); this function, which every CLI command and the MCP server go through, did
        # not, so on 2026-09-27 `inspeximus stats` after install --all opened an empty file in the working
        # directory while the memory sat in the shared store. An explicit scope, including `user`, keeps
        # its old meaning.
        return shared_store_path() or "inspeximus_memory.json"
    if scope == "user":
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


def resolved_path_source(path=None, env=None) -> str:
    """WHICH rule `resolve_path` applied, in words. Reported by the MCP `where_am_i` tool, so a store
    chosen by the shared record is never mistaken for one chosen by the working directory."""
    env = os.environ if env is None else env
    scope = (env.get("INSPEXIMUS_SCOPE") or "").strip().lower()
    if path:
        return "--path"
    if env.get("INSPEXIMUS_PATH"):
        if scope in ("project", "claude-code"):
            return f"INSPEXIMUS_PATH (explicit path OUTRANKS INSPEXIMUS_SCOPE={scope})"
        return "INSPEXIMUS_PATH"
    if scope == "project":
        return "INSPEXIMUS_SCOPE=project (git root)"
    if scope == "claude-code":
        return "INSPEXIMUS_SCOPE=claude-code (the Claude Code hook's store)"
    if scope == "" and shared_store_path():
        return f"the shared store recorded by install --all ({shared_config_path()})"
    return "default filename, relative to this server's working directory"


def open_store(path=None, *, receipts: bool = False, persist_vectors: bool = False, embed=None,
               resolve: bool = True, **kwargs):
    """Open a store the way a SURFACE should: shared echo-guard posture, receipts kept if already on.

    `resolve=False` keeps an explicit `path=None` (an in-memory store) instead of falling back to the
    default filename — the adapters that mean "no file" need that.
    """
    from inspeximus import Inspeximus

    p = resolve_path(path) if resolve else path
    # SURFACE PATHS ONLY: --path, INSPEXIMUS_PATH, the scopes and the hook store, where a typo is the user's
    # and silence costs them their memory. An adapter's explicit path (resolve=False) is the program's own
    # choice; for it the directory is still made by the first write, never by opening.
    problem = store_location_problem(p) if resolve else None
    if problem:
        raise StoreLocationError(problem)
    # A store that ALREADY has a receipt chain keeps it. Detected from the sidecar rather than a flag,
    # because a user who enabled receipts in Python should not have to re-declare them at every call.
    if not receipts and p and os.path.exists(str(p) + ".receipts.json"):
        receipts = True

    store = Inspeximus(path=p, embed=embed, persist_vectors=persist_vectors, receipts=receipts, **kwargs)
    store.echo_guard = echo_guard_default()
    return store


def recommit_named(store, ids=None, all_records: bool = False, project=None) -> dict:
    """`Inspeximus.recommit()` for a SURFACE: the records are named, or the caller says all of them.

    The library method sweeps every active record when `ids` is None. A recommit binds each record's
    state AS IT IS NOW, so a record edited out of band verifies clean afterwards. A sweep that happens
    because an argument was left out would do that to the whole store. So the MCP tool and the CLI pass
    `ids` or `all_records=True`, and neither or both is refused with nothing written.

    `project` is the surface's own scope. With one, only the records that scope reads (its own and the
    unscoped ones) are recommitted, and a named id outside it is reported rather than written.

    Also refused, with nothing written, when this handle cannot sign the way the chain is signed. An
    unsigned receipt on a signed chain, or a signed one on an unsigned chain, leaves the chain signed in
    places, and verify_writes() reports that. The chain is append-only, so those receipts could not be
    taken back.

    Returns {recommitted, skipped, problems}. A named id that matched no active record is named in
    `problems` rather than dropped."""
    named = list(dict.fromkeys(str(i).strip() for i in (ids or ()) if str(i).strip()))
    nothing = {"recommitted": [], "skipped": []}
    if named and all_records:
        return {**nothing, "problems": ["pass ids or all, not both; nothing was recommitted"]}
    if not named and not all_records:
        return {**nothing, "problems": [
            "name the records to recommit: the ids the UNSCOPED line of verify_writes() or "
            "context_unbound() lists, each checked against a copy you trust, or all for every active "
            "record; nothing was recommitted"]}
    if not getattr(store, "receipts_enabled", False):
        return store.recommit(ids=[])          # the library's own "receipts are disabled" answer
    from inspeximus.core import _HAVE_ED
    chain = list(getattr(store, "_receipts", None) or ()) + list(getattr(store, "_tombstones", None) or ())
    n_signed = sum(1 for r in chain if r.get("sig"))
    signs = (getattr(store, "_receipt_signer", None) is not None
             or bool(getattr(store, "_receipt_sk", None) and _HAVE_ED))
    if n_signed and not signs:
        return {**nothing, "problems": [
            f"this store's chain is signed ({n_signed} of {len(chain)} entries) and this handle holds no "
            f"receipt key, so every recommit receipt would be UNSIGNED and verify_writes() would report a "
            f"chain signed in places; open the store with its key (INSPEXIMUS_RECEIPT_KEY_FILE, or "
            f"--receipt-key-file in the CLI). Nothing was recommitted."]}
    if chain and not n_signed and signs:
        return {**nothing, "problems": [
            f"this store's chain is unsigned ({len(chain)} entries) and this handle signs, so every "
            f"recommit receipt would be SIGNED and verify_writes() would report a chain signed in places; "
            f"open the store without a receipt key. Nothing was recommitted."]}
    target = None if all_records else named
    if project:
        seen = {r["id"] for r in store.items
                if (r.get("meta") or {}).get("project") in (None, str(project))}
        target = sorted(seen) if all_records else [i for i in named if i in seen]
    res = store.recommit(ids=target)
    covered = set(res["recommitted"]) | set(res["skipped"])
    missing = [i for i in named if i not in covered]
    if missing:
        where = f" in project {project!r}" if project else ""
        res["problems"].append(
            f"{len(missing)} named id(s) are not an active record{where} of this store, so nothing was "
            f"written for them: {', '.join(missing[:5])}"
            + (f", +{len(missing) - 5} more" if len(missing) > 5 else ""))
    return res
