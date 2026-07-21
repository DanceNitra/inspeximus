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


def default_server_block(store_path=None):
    """The stdio MCP entry, in the shape every JSON-configured host uses."""
    block = {"command": "uvx", "args": ["--from", "inspeximus[mcp]", "inspeximus-mcp"]}
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


HOSTS = {
    "claude": {
        "label": "Claude Code",
        "format": "json",
        "root_key": "mcpServers",
        "paths": _claude_paths,
        # Claude Code treats a missing `type` as a configuration error and skips the entry with a
        # warning, so it is written explicitly rather than relying on a stdio default.
        "fields": lambda blk: {"type": "stdio", **blk},
        "verified": False,          # flipped to True below once exercised on this machine
        "docs": "https://code.claude.com/docs/en/mcp.md",
        "note": "Claude Code reads the config at session start: restart the session to pick it up. "
                "A project-scoped .mcp.json additionally needs interactive approval on first use.",
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
                "verify via the MCPs icon in the Cascade panel.",
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
}


def plan(host, scope=None, project=None, store_path=None, name=SERVER_NAME):
    """Work out exactly what would change, without touching anything.

    Returns a dict carrying the target path, the action, a unified diff and any error. `apply()`
    consumes this; `--dry-run` prints it. Keeping the decision and the write in separate functions is
    what makes the dry run trustworthy: it is the same code path, minus the write.
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

    block = spec["fields"](default_server_block(store_path))
    block["command"] = resolve_launcher()

    res = {"host": host, "label": spec["label"], "scope": scope, "path": path,
           "format": spec["format"], "verified": spec["verified"], "note": spec.get("note", ""),
           "docs": spec.get("docs", ""), "name": name, "block": block, "error": None}

    if spec["format"] == "json":
        data, err = _read_json(path)
        if err:
            res["error"] = err
            return res
        before = json.dumps(data, indent=2) + "\n" if path.exists() else ""
        servers = data.setdefault(spec["root_key"], {})
        if not isinstance(servers, dict):
            res["error"] = f"{path}: '{spec['root_key']}' is not an object; refusing to touch it"
            return res
        existing = servers.get(name)
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


def apply(p):
    """Write the planned change. Returns (ok, message)."""
    if p.get("error"):
        return False, p["error"]
    if p["action"] in ("unchanged", "present"):
        return True, "already present, unchanged"
    if p["format"] == "json":
        _write_json(p["path"], p["data"])
    else:
        p["path"].parent.mkdir(parents=True, exist_ok=True)
        if p["path"].exists():
            shutil.copy2(p["path"], str(p["path"]) + ".bak")
        p["path"].write_text(p["data"], encoding="utf-8")
    return True, f"{p['action']} -> {p['path']}"


def render(p, dry_run=False):
    """Human output. An unverified host says so, loudly, instead of implying it works."""
    out = []
    if p.get("error"):
        return f"[{p.get('label', p['host'])}] ERROR: {p['error']}"
    head = f"[{p['label']}] {p['scope']} scope -> {p['path']}"
    out.append(head)
    if not p["verified"]:
        out.append("  UNVERIFIED: this config shape comes from the host's documentation but has not "
                   "been exercised on this machine.")
        out.append(f"  Check it against {p['docs']} before relying on it.")
    if p["diff"]:
        out.append("".join("  " + ln for ln in p["diff"].splitlines(True)).rstrip())
    else:
        out.append("  (no change)")
    if p.get("note"):
        out.append(f"  note: {p['note']}")
    if dry_run:
        out.append("  (dry run - nothing written)")
    return "\n".join(out)
