"""`inspeximus install --ide <host>` — register the MCP server in a host's own config file.

The point of this module is not convenience, it is *installability*: a memory layer nobody can wire
up in one command does not get used, however good the store is.

Three rules it holds itself to, because an installer edits files it did not write:

1. **Never clobber.** The host's config is read, the server entry is merged into it, and everything
   else is preserved byte-for-byte where the format allows. A malformed existing file is a hard stop,
   not something to overwrite with a "clean" one.
2. **Idempotent.** Running it twice leaves one server entry, not two, and the second run reports
   "already present, unchanged" rather than pretending it did something.
3. **Never claim success it cannot verify.** Each host carries a `verified` flag saying whether the
   config shape was confirmed against that host's own documentation AND exercised on this machine.
   A host that is only documented, not exercised, prints UNVERIFIED and shows the exact diff instead
   of implying the install works. Saying "installed" for a config that was never loaded by the real
   application is the kind of claim that gets a tool uninstalled.
"""
import json
import os
import pathlib
import platform
import shutil
import sys

SERVER_NAME = "inspeximus"


def _pinned(extra=""):
    """`inspeximus[<extra>]==<this version>`: the spec every uvx launch this installer writes names.

    PINNED TO THE VERSION THAT WROTE IT (3.9.7). An unpinned `--from inspeximus[mcp]` resolves whatever
    the index serves at that moment. Right after 3.9.6 was published the index still served 3.9.5, which
    refuses the INSPEXIMUS_SCOPE=claude-code this installer writes, and the server died with
    StoreScopeError (found by the tester-kit lane). A pin also keeps the server and the hooks on one
    version against one store. To move to a newer release, upgrade inspeximus and re-run the install."""
    from inspeximus import __version__
    return "inspeximus%s==%s" % ("[%s]" % extra if extra else "", __version__)


def default_server_block(store_path=None):
    """The stdio MCP entry, in the shape every JSON-configured host uses."""
    block = {"command": "uvx", "args": ["--from", _pinned("mcp"), "inspeximus-mcp"]}
    if store_path:
        block["env"] = {"INSPEXIMUS_PATH": str(store_path)}
    return block


def _home():
    return pathlib.Path(os.path.expanduser("~"))


def _appdata():
    if platform.system() == "Windows":
        return pathlib.Path(os.environ.get("APPDATA") or (_home() / "AppData" / "Roaming"))
    if platform.system() == "Darwin":
        return _home() / "Library" / "Application Support"
    return pathlib.Path(os.environ.get("XDG_CONFIG_HOME") or (_home() / ".config"))


def _read_json(path):
    """Returns (data, error). A file that exists but does not parse is an error, never an overwrite."""
    if not path.exists():
        return {}, None
    raw = path.read_text(encoding="utf-8")
    if not raw.strip():
        return {}, None
    try:
        return json.loads(raw), None
    except json.JSONDecodeError as e:
        return None, f"{path} exists but is not valid JSON ({e}); refusing to touch it"


def _write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".inspeximus-tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    if path.exists():
        shutil.copy2(path, str(path) + ".bak")
    tmp.replace(path)


def _toml_block(name, block):
    """Render one `[mcp_servers.<name>]` table. Written by hand rather than with a TOML library so
    the core stays dependency-free; only the value shapes we actually emit are supported."""
    # Every value goes through json.dumps, including the command. A Windows path interpolated raw
    # into a TOML basic string is not merely ugly: `C:\Users\...` contains `\U`, which TOML reads as
    # a unicode escape, so the config either fails to parse or silently yields a different path.
    lines = [f"[mcp_servers.{name}]", f'command = {json.dumps(block["command"])}']
    args = ", ".join(json.dumps(a) for a in block.get("args", []))
    lines.append(f"args = [{args}]")
    # Keys the user added to our own table (startup_timeout_sec, enabled, ...) survive a rewrite.
    for k, v in block.items():
        if k in ("command", "args", "env"):
            continue
        if isinstance(v, bool):
            lines.append(f"{k} = {'true' if v else 'false'}")
        elif isinstance(v, (int, float, str)):
            lines.append(f"{k} = {json.dumps(v)}")
        elif isinstance(v, list) and all(isinstance(x, str) for x in v):
            lines.append(f"{k} = [{', '.join(json.dumps(x) for x in v)}]")
    env = block.get("env") or {}
    if env:
        lines.append(f"[mcp_servers.{name}.env]")
        for k, v in env.items():
            lines.append(f'{k} = {json.dumps(str(v))}')
    return "\n".join(lines) + "\n"


def resolve_launcher():
    """Absolute path to `uvx` when we can find one, else the bare name.

    A GUI-launched editor does not necessarily inherit the shell PATH, so a bare "uvx" that works in
    a terminal can fail inside the app with nothing but a "failed to connect". Writing the resolved
    path removes that whole class of support question. Falls back to the bare name rather than
    failing, because a PATH that exists only at launch time is still valid.
    """
    return shutil.which("uvx") or "uvx"


def _mcp_importable():
    import importlib.util
    try:
        return importlib.util.find_spec("mcp") is not None
    except (ImportError, ValueError):
        return False


def resolve_runtime():
    """How the host must launch inspeximus: `("uvx", path)` or `("python", sys.executable)`.

    A BARE "uvx" IS NOT A FALLBACK. Measured on 3.9.5 with no uv on PATH: the installer wrote
    `"command": "uvx"`, reported success, and the server never started, with nothing in the terminal
    to say why. So uvx is used when it resolves, and otherwise the interpreter running this installer.
    The hooks follow the same runtime, so the server and the hooks never run two different inspeximus
    versions against one store. When that interpreter cannot import the `mcp` extra, `plan()` still
    writes the config and says, first, which command makes it start: see `missing_mcp_warning`.
    """
    uvx = shutil.which("uvx")
    if uvx:
        return "uvx", uvx
    return "python", sys.executable


def missing_mcp_warning(kind):
    """The warning for a python runtime that cannot import the `mcp` extra, or "" when there is none.

    A WARNING, NOT A REFUSAL. An earlier 3.9.6 build refused here and wrote nothing. The hooks need
    only the core, so they worked, and the refusal withheld them too; and `pip install inspeximus`
    followed by `inspeximus install --ide claude`, the documented sequence, exited 2. The config
    names an interpreter that exists, and the one missing piece is a pip command, printed first."""
    if kind != "python" or _mcp_importable():
        return ""
    return ('WARNING: uvx is not on PATH and this Python cannot import the MCP extra, so the MCP '
            'server will not start until you run: %s -m pip install "inspeximus[mcp]" (or install '
            'uv: https://docs.astral.sh/uv/getting-started/installation/, and re-run this command). '
            'The hooks need only the core and work now.' % sys.executable)


def _server_launch(kind, exe):
    """(command, args) for the MCP server under a runtime from `resolve_runtime`."""
    if kind == "python":
        return exe, ["-m", "inspeximus.mcp_server"]
    return exe, ["--from", _pinned("mcp"), "inspeximus-mcp"]


def _shell_path(p):
    """A path a hook command line can carry: forward slashes, quoted when it holds a space.

    Claude Code runs a hook command through a shell, and on Windows that can be Git Bash, which
    reads a backslash as an escape. `C:/...` works in bash and in cmd alike."""
    p = str(p).replace("\\", "/") if os.name == "nt" else str(p)
    return '"%s"' % p if " " in p else p


def hook_command(kind, exe):
    """The hook command under a runtime from `resolve_runtime`."""
    if kind == "python":
        return _shell_path(exe) + " -m inspeximus.claude_code"
    return _shell_path(exe) + " --from %s python -m inspeximus.claude_code" % _pinned()


def _claude_settings_path(mcp_config_path):
    """Where Claude Code reads hooks, next to the MCP config the installer writes.

    `~/.claude.json` -> `~/.claude/settings.json`, and `<project>/.mcp.json` ->
    `<project>/.claude/settings.json`. Derived from the MCP path rather than from `_home()`, so a
    caller that redirects the config (the tests do) cannot have its hooks land in a real home."""
    return pathlib.Path(mcp_config_path).parent / ".claude" / "settings.json"


def plan_claude_hooks(settings_path, command):
    """The hooks half of `install --ide claude`: the same five events the plugin ships.

    README promises that the plugin and `inspeximus install --ide claude` both wire "the same hooks".
    On 3.9.5 the installer wrote only `mcpServers`, so an installer user got the tools and no
    SessionStart digest. An event that already has an inspeximus hook is left as it is: the user's
    own command line wins, and a second copy would run every hook twice."""
    import copy
    import difflib
    from inspeximus import claude_code as cc

    res = {"path": settings_path, "error": None}
    data, err = _read_json(settings_path)
    if err:
        res["error"] = err
        return res
    if not isinstance(data, dict):
        res["error"] = f"{settings_path}: top level is {type(data).__name__}, not an object; refusing to touch it"
        return res
    before = json.dumps(data, indent=2) + "\n" if settings_path.exists() else ""
    hooks = data.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        res["error"] = f"{settings_path}: 'hooks' is not an object; refusing to touch it"
        return res
    added = []
    for evt in ("PreToolUse", "PostToolUse", "UserPromptSubmit", "SessionStart", "SessionEnd"):
        present = hooks.get(evt, [])
        if not isinstance(present, list):
            res["error"] = f"{settings_path}: hooks.{evt} is not a list; refusing to touch it"
            return res
        if any(m in json.dumps(present) for m in cc._HOOK_MARKERS):
            continue
        entry = copy.deepcopy(cc._EVENT_HOOK.get(evt, cc._HOOK))
        for h in entry["hooks"]:
            h["command"] = command
        hooks.setdefault(evt, []).append(entry)
        added.append(evt)
    after = json.dumps(data, indent=2) + "\n"
    res.update(data=data, added=added,
               action=("unchanged" if not added else "create" if not settings_path.exists() else "add"),
               diff="".join(difflib.unified_diff(
                   before.splitlines(True), after.splitlines(True),
                   fromfile=str(settings_path) + (" (missing)" if not settings_path.exists() else ""),
                   tofile=str(settings_path))))
    return res


# ── hosts ────────────────────────────────────────────────────────────────────────────────────────
# `verified` means BOTH: the config shape was taken from that host's own documentation, AND it was
# exercised on a real machine. Documentation alone is not verification -- see the module docstring.

def _claude_paths(project):
    return {"user": _home() / ".claude.json",
            "project": pathlib.Path(project or os.getcwd()) / ".mcp.json"}


def _cursor_paths(project):
    return {"user": _home() / ".cursor" / "mcp.json",
            "project": pathlib.Path(project or os.getcwd()) / ".cursor" / "mcp.json"}


def _windsurf_paths(project):
    # Windsurf documents no project-scoped config; do not invent one.
    return {"user": _home() / ".codeium" / "windsurf" / "mcp_config.json"}


def devin_dir():
    """Devin's user config directory: %APPDATA%/devin on Windows, $XDG_CONFIG_HOME/devin or ~/.config/devin
    elsewhere (docs.devin.ai/cli/extensibility/mcp/configuration, read 2026-09-26). On Windows it is taken
    from the home directory, not from APPDATA, so a sandboxed home is never escaped through the environment."""
    if os.name == "nt":
        return _home() / "AppData" / "Roaming" / "devin"
    xdg = os.environ.get("XDG_CONFIG_HOME")
    return (pathlib.Path(xdg) if xdg else _home() / ".config") / "devin"


def _devin_paths(project):
    # WINDSURF IS NOW DEVIN DESKTOP (measured 2026-09-26 against its docs). Both of its agents, Cascade and
    # Devin Local, and Devin CLI read ~/.config/devin/mcp_config.json (%APPDATA%/devin on Windows); the
    # ~/.codeium/windsurf file is read by older Windsurf builds and, in the new editor, only by an opt-in
    # MCP discovery setting. Versions before v3000.3 kept servers in config.json and migrate them on
    # startup; this writes the dedicated file.
    return {"user": devin_dir() / "mcp_config.json",
            "project": pathlib.Path(project or os.getcwd()) / ".devin" / "mcp_config.json"}


def _codex_paths(project):
    # CODEX_HOME defaults to ~/.codex on every platform (no OS branch in codex's own home-dir code).
    home = pathlib.Path(os.environ.get("CODEX_HOME") or (_home() / ".codex"))
    return {"user": home / "config.toml",
            "project": pathlib.Path(project or os.getcwd()) / ".codex" / "config.toml"}


def _cline_paths(project):
    """Cline moved its settings out of the VS Code globalStorage path into a shared client-agnostic
    one. The globalStorage location that most guides still quote is LEGACY -- Cline migrates it on
    startup -- so writing there would land in a file the app is trying to move away from."""
    explicit = os.environ.get("CLINE_MCP_SETTINGS_PATH")
    if explicit:
        return {"user": pathlib.Path(explicit)}
    base = pathlib.Path(os.environ.get("CLINE_DATA_DIR")
                        or (pathlib.Path(os.environ["CLINE_DIR"]) / "data" if os.environ.get("CLINE_DIR")
                            else _home() / ".cline" / "data"))
    return {"user": base / "settings" / "cline_mcp_settings.json"}


def _gemini_paths(project):
    # docs/cli/settings.md and docs/reference/configuration.md in google-gemini/gemini-cli (read 2026-09-26):
    # "User settings: ~/.gemini/settings.json", "Workspace settings: your-project/.gemini/settings.json".
    return {"user": _home() / ".gemini" / "settings.json",
            "project": pathlib.Path(project or os.getcwd()) / ".gemini" / "settings.json"}


def _antigravity_paths(project):
    # https://antigravity.google/docs/mcp (read 2026-09-26): "Global: ~/.gemini/config/mcp_config.json",
    # "Workspace: .agents/mcp_config.json".
    return {"user": _home() / ".gemini" / "config" / "mcp_config.json",
            "project": pathlib.Path(project or os.getcwd()) / ".agents" / "mcp_config.json"}


HOSTS = {
    "claude": {
        "label": "Claude Code",
        "format": "json",
        "root_key": "mcpServers",
        "paths": _claude_paths,
        # Claude Code treats a missing `type` as a configuration error and skips the entry with a
        # warning, so it is written explicitly rather than relying on a stdio default.
        "fields": lambda blk: {"type": "stdio", **blk},
        # VERIFIED: written to a real ~/.claude.json on Windows, then `claude mcp list` reported
        # "Connected" for it. That round trip is what verified means here -- the first attempt wrote a
        # perfectly valid config for a server that could not start, and only launching it caught that.
        "verified": True,
        "docs": "https://code.claude.com/docs/en/mcp.md",
        "note": "Claude Code reads the config at session start: restart the session to pick it up. "
                "A project-scoped .mcp.json additionally needs interactive approval on first use. "
                "The hooks are written too; do not also install the marketplace plugin, or every "
                "hook runs twice.",
    },
    "cursor": {
        "label": "Cursor",
        "format": "json",
        "root_key": "mcpServers",
        "paths": _cursor_paths,
        "fields": lambda blk: dict(blk),          # `type` is optional here; the docs' example omits it
        "verified": False,
        "docs": "https://cursor.com/docs/mcp",
        "note": "Restart Cursor. Verify in Output -> MCP Logs, or the Tools & Integrations settings page.",
    },
    "windsurf": {
        "label": "Windsurf",
        "format": "json",
        "root_key": "mcpServers",
        "paths": _windsurf_paths,
        "fields": lambda blk: dict(blk),
        "verified": False,
        "docs": "https://docs.devin.ai/desktop/cascade/mcp",
        "note": "Global config only -- Windsurf documents no project-scoped MCP file. Restart Windsurf; "
                "verify via the MCPs icon in the Cascade panel. Builds renamed Devin Desktop read the "
                "devin host's file instead; install --all writes both.",
    },
    "devin": {
        "label": "Devin Desktop / CLI",
        "format": "json",
        "root_key": "mcpServers",
        "paths": _devin_paths,
        "fields": lambda blk: {k: v for k, v in blk.items() if k in ("command", "args", "env")},
        "verified": False,
        "docs": "https://docs.devin.ai/cli/extensibility/mcp/configuration",
        "note": "Devin Desktop (formerly Windsurf) and Devin CLI share this file. Restart Devin Desktop; "
                "`devin mcp list` shows the server.",
    },
    "codex": {
        "label": "Codex CLI",
        "format": "toml",
        "root_key": "mcp_servers",
        "paths": _codex_paths,
        # Codex's config schema is deny_unknown_fields: one unrecognised key is a hard parse error,
        # not a warning. Only command/args/env are written.
        "fields": lambda blk: {k: v for k, v in blk.items() if k in ("command", "args", "env")},
        "verified": False,
        "docs": "https://learn.chatgpt.com/docs/extend/mcp",
        "note": "`codex mcp add` owns this file too and is the safer route if you have it. This writer "
                "appends a new table and refuses to rewrite one that already exists.",
    },
    "cline": {
        "label": "Cline",
        "format": "json",
        "root_key": "mcpServers",
        "paths": _cline_paths,
        # `type` must be explicit: for a url entry Cline defaults to sse, and its timeout is SECONDS,
        # not the milliseconds Claude Code uses. autoApprove defaults to [] -- an installer that
        # invented a non-empty one would be granting tool permissions the user never agreed to.
        "fields": lambda blk: {"type": "stdio", **blk, "disabled": False, "autoApprove": [], "timeout": 60},
        "verified": False,
        "docs": "https://docs.cline.bot/mcp/configuring-mcp-servers",
        "note": "Cline watches this file and reloads by itself -- no restart. Note the path is the "
                "shared ~/.cline one, not the legacy VS Code globalStorage path most guides quote.",
    },
    "gemini": {
        "label": "Gemini CLI",
        "format": "json",
        "root_key": "mcpServers",
        "paths": _gemini_paths,
        # docs/tools/mcp-server.md: a stdio entry is command/args/env (+ cwd, timeout, trust). `trust`
        # is left out: true would bypass every tool-call confirmation, which is the user's call.
        "fields": lambda blk: {k: v for k, v in blk.items() if k in ("command", "args", "env")},
        "verified": False,
        "docs": "https://github.com/google-gemini/gemini-cli/blob/main/docs/tools/mcp-server.md",
        "note": "Restart Gemini CLI. It appends this server's instructions to its system instructions, "
                "so the agent is told to recall at the start of each task.",
    },
    "antigravity": {
        "label": "Antigravity",
        "format": "json",
        "root_key": "mcpServers",
        "paths": _antigravity_paths,
        "fields": lambda blk: {k: v for k, v in blk.items() if k in ("command", "args", "env")},
        "verified": False,
        "docs": "https://antigravity.google/docs/mcp",
        "note": "Restart Antigravity, or refresh in Manage MCP Servers. Its docs say nothing about server "
                "instructions, so `install --all` offers a rules file that tells the agent to recall.",
    },
}


def plan(host, scope=None, project=None, store_path=None, name=SERVER_NAME, env=None):
    """Work out exactly what would change, without touching anything.

    Returns a dict carrying the target path, the action, a unified diff and any error. `apply()`
    consumes this; `--dry-run` prints it. Keeping the decision and the write in separate functions is
    what makes the dry run trustworthy: it is the same code path, minus the write.

    `env` (3.14.0, used by `install --all`) is MERGED into the entry's existing env: a key set to None is
    removed, every other key the entry already had is kept. `store_path` replaces the env, as before.
    """
    import difflib

    spec = HOSTS.get(host)
    if spec is None:
        return {"host": host, "error": f"unknown host {host!r}; known: {', '.join(sorted(HOSTS))}"}

    paths = spec["paths"](project)
    scope = scope or ("user" if "user" in paths else sorted(paths)[0])
    if scope not in paths:
        return {"host": host, "label": spec["label"],
                "error": f"{spec['label']} has no {scope!r} scope (available: {', '.join(sorted(paths))})"}
    path = paths[scope]

    kind, exe = resolve_runtime()
    block = spec["fields"](default_server_block(store_path))
    block["command"], block["args"] = _server_launch(kind, exe)

    res = {"host": host, "label": spec["label"], "scope": scope, "path": path,
           "format": spec["format"], "verified": spec["verified"], "note": spec.get("note", ""),
           "docs": spec.get("docs", ""), "name": name, "block": block, "error": None,
           "warning": missing_mcp_warning(kind)}

    if host == "claude":
        res["hooks"] = plan_claude_hooks(_claude_settings_path(path), hook_command(kind, exe))
        if res["hooks"]["error"]:
            res["error"] = res["hooks"]["error"]
            return res
        if store_path and env is None:
            res["note"] += (" --store points the MCP server at %s, while the hooks read "
                            "<git root>/.inspeximus/coding_memory.json, so a decision written "
                            "through MCP will not appear at SessionStart. Omit --store to share "
                            "one store." % store_path)

    if spec["format"] == "json":
        data, err = _read_json(path)
        if err:
            res["error"] = err
            return res
        before = json.dumps(data, indent=2) + "\n" if path.exists() else ""
        # THE TOP LEVEL, checked before the key inside it. The guard below already refuses a
        # root_key whose value is not an object, but it runs AFTER data.setdefault(), which needs
        # `data` to be a dict in the first place. A config that is valid JSON but not an object (a
        # bare list, string or number) therefore crashed plan() with
        # `AttributeError: 'list' object has no attribute 'setdefault'` and a raw traceback --
        # while a MALFORMED file was refused cleanly. plan() is documented to work out what would
        # change "without touching anything" and to report trouble in res["error"]; raising breaks
        # that contract for --dry-run too. Same refusal, one level out.
        if not isinstance(data, dict):
            res["error"] = (f"{path}: top level is {type(data).__name__}, not an object; "
                            f"refusing to touch it")
            return res
        servers = data.setdefault(spec["root_key"], {})
        if not isinstance(servers, dict):
            res["error"] = f"{path}: '{spec['root_key']}' is not an object; refusing to touch it"
            return res
        existing = servers.get(name)
        # NEVER CLOBBER, applied to the ENTRY and not just the file. A second run without --store
        # would otherwise replace the whole entry and silently drop the env the first run wrote, along
        # with any key the user added by hand (timeout, alwaysLoad, autoApprove...). Anything we do not
        # explicitly emit is carried across.
        # ONE STORE FOR THE SERVER AND THE HOOKS. With no --store, the Claude Code entry names the
        # hook's store through INSPEXIMUS_SCOPE=claude-code. An env the user or an earlier run already
        # wrote is kept as it is (NEVER CLOBBER, above).
        if env is not None:
            merged = dict((existing or {}).get("env") or {}) if isinstance(existing, dict) else {}
            for k, v in env.items():
                if v is None:
                    merged.pop(k, None)
                else:
                    merged[k] = v
            block["env"] = merged
        elif host == "claude" and not store_path and not (isinstance(existing, dict) and existing.get("env")):
            block["env"] = {"INSPEXIMUS_SCOPE": "claude-code"}
        if isinstance(existing, dict):
            block = {**existing, **block}
        res["action"] = ("unchanged" if existing == block
                         else "update" if existing is not None
                         else "create" if not path.exists() else "add")
        servers[name] = block
        after = json.dumps(data, indent=2) + "\n"
        res["data"] = data
        res["diff"] = "".join(difflib.unified_diff(
            before.splitlines(True), after.splitlines(True),
            fromfile=str(path) + (" (missing)" if not path.exists() else ""), tofile=str(path)))
    else:                                                   # toml
        before = path.read_text(encoding="utf-8") if path.exists() else ""
        table = f"[mcp_servers.{name}]"
        res["action"] = ("update" if table in before else "create" if not before else "add")
        if env is not None and table in before:
            # REWRITE OUR OWN TABLE ONLY (3.14.0), and only when the result re-parses to the same file
            # with nothing but that table changed. Without a TOML reader (Python < 3.11) it stays a report.
            return _toml_rewrite(res, path, before, name, block, env)
        if env is not None:
            block["env"] = {k: v for k, v in env.items() if v is not None}
        addition = _toml_block(name, block)
        after = before if table in before else (before.rstrip("\n") + "\n\n" + addition if before else addition)
        res["data"] = after
        res["diff"] = "".join(difflib.unified_diff(
            before.splitlines(True), after.splitlines(True),
            fromfile=str(path) + (" (missing)" if not path.exists() else ""), tofile=str(path)))
        if table in before:
            # Rewriting an existing TOML table by hand risks mangling neighbouring content, so this
            # reports rather than edits. Removing the block and re-running is the safe path.
            res["action"] = "present"

    return res


def _toml_rewrite(res, path, before, name, block, env):
    """Replace `[mcp_servers.<name>]` (and its `.env` subtable) in `before`, merging the env."""
    import difflib
    try:
        import tomllib
    except ImportError:
        res["action"] = "present"
        res["diff"] = ""
        res["note"] = (res.get("note", "") + " This Python has no TOML reader, so the existing table was "
                       "left as it is; remove it and re-run, or run on Python 3.11 or later.").strip()
        return res
    try:
        old = tomllib.loads(before)
    except Exception as e:                                   # noqa: BLE001
        res["error"] = f"{path} is not valid TOML ({e}); refusing to touch it"
        return res
    mine = ((old.get("mcp_servers") or {}).get(name)) or {}
    merged_env = dict(mine.get("env") or {})
    for k, v in env.items():
        if v is None:
            merged_env.pop(k, None)
        else:
            merged_env[k] = v
    new_block = {**{k: v for k, v in mine.items() if k != "env"}, "command": block["command"],
                 "args": block["args"], "env": merged_env}
    lines = before.splitlines(True)
    head = f"[mcp_servers.{name}]"
    start = next(i for i, ln in enumerate(lines) if ln.strip() == head)
    end = start + 1
    while end < len(lines):
        s = lines[end].strip()
        if s.startswith("[") and not s.startswith(f"[mcp_servers.{name}."):
            break
        end += 1
    after = "".join(lines[:start]) + _toml_block(name, new_block) + ("\n" if end < len(lines) else "") \
        + "".join(lines[end:])
    try:
        new = tomllib.loads(after)
    except Exception as e:                                   # noqa: BLE001
        res["error"] = f"rewriting {path} would not parse ({e}); left unchanged"
        return res
    others_old = {k: v for k, v in old.items() if k != "mcp_servers"}
    others_new = {k: v for k, v in new.items() if k != "mcp_servers"}
    peers_old = {k: v for k, v in (old.get("mcp_servers") or {}).items() if k != name}
    peers_new = {k: v for k, v in (new.get("mcp_servers") or {}).items() if k != name}
    if others_old != others_new or peers_old != peers_new:
        res["error"] = f"rewriting {path} would change more than [mcp_servers.{name}]; left unchanged"
        return res
    res["action"] = "unchanged" if new["mcp_servers"][name] == mine else "update"
    res["data"] = after
    res["diff"] = "".join(difflib.unified_diff(before.splitlines(True), after.splitlines(True),
                                               fromfile=str(path), tofile=str(path)))
    return res


def apply(p):
    """Write the planned change. Returns (ok, message)."""
    if p.get("error"):
        return False, p["error"]
    hooks = p.get("hooks") or {}
    hooks_change = hooks.get("action") not in (None, "unchanged")
    if p["action"] in ("unchanged", "present") and not hooks_change:
        return True, "already present, unchanged"
    msgs = []
    if p["action"] not in ("unchanged", "present"):
        if p["format"] == "json":
            _write_json(p["path"], p["data"])
        else:
            p["path"].parent.mkdir(parents=True, exist_ok=True)
            if p["path"].exists():
                shutil.copy2(p["path"], str(p["path"]) + ".bak")
            p["path"].write_text(p["data"], encoding="utf-8")
        msgs.append(f"{p['action']} -> {p['path']}")
    if hooks_change:
        _write_json(hooks["path"], hooks["data"])
        msgs.append(f"hooks {hooks['action']} ({', '.join(hooks['added'])}) -> {hooks['path']}")
    return True, "; ".join(msgs)


def render(p, dry_run=False):
    """Human output. An unverified host says so, loudly, instead of implying it works."""
    out = []
    if p.get("error"):
        return f"[{p.get('label', p['host'])}] ERROR: {p['error']}"
    head = f"[{p['label']}] {p['scope']} scope -> {p['path']}"
    out.append(head)
    if p.get("warning"):
        out.append(f"  {p['warning']}")
    if not p["verified"]:
        out.append("  UNVERIFIED: this config shape comes from the host's documentation but has not "
                   "been exercised on this machine.")
        out.append(f"  Check it against {p['docs']} before relying on it.")
    if p["diff"]:
        out.append("".join("  " + ln for ln in p["diff"].splitlines(True)).rstrip())
    else:
        out.append("  (no change)")
    hooks = p.get("hooks")
    if hooks:
        out.append(f"[{p['label']}] hooks -> {hooks['path']}")
        out.append("".join("  " + ln for ln in hooks["diff"].splitlines(True)).rstrip()
                   if hooks.get("diff") else "  (no change: every event already runs inspeximus)")
    if p.get("note"):
        out.append(f"  note: {p['note']}")
    if dry_run:
        out.append("  (dry run - nothing written)")
    return "\n".join(out)
