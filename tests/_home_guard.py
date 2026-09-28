"""What a test run must never change in the real home, read before and after the run.

MEASURED 2026-09-27: `test_docs_examples_are_runnable.py::test_every_documented_inspeximus_command_block_runs`
runs every documented `inspeximus ...` command, and README.md documents `inspeximus install --ide
claude`. The subprocess inherited the real HOME and USERPROFILE, so every suite run installed this
tree's version into the owner's real ~/.claude.json (`inspeximus[mcp]==3.14.2`, a version not yet on
PyPI) and ~/.claude/settings.json. Every new Claude Code session on that machine then launched an MCP
server that could not start. The conftest now gives every test a temporary home; this is the check
that the temporary home held.

WHAT IT COMPARES, AND WHY NOT SIZE AND MTIME. The real ~/.claude.json is rewritten constantly by the
Claude Code sessions running beside the suite, and ~/.inspeximus is written by their hooks and MCP
servers. A guard on size or mtime fails on nearly every run for reasons that are not the suite's. So
it compares what a test can change and a live session does not: the inspeximus entries of each host
configuration, and the set of file names inspeximus keeps under the home.
"""
from __future__ import annotations

import json
import os

#: Host configurations `inspeximus install` and `install --all` write, relative to the home.
HOST_CONFIGS = (
    ".claude.json", os.path.join(".claude", "settings.json"), os.path.join(".codex", "config.toml"),
    os.path.join(".gemini", "settings.json"), os.path.join(".cursor", "mcp.json"),
    os.path.join(".codeium", "windsurf", "mcp_config.json"), os.path.join(".hermes", "config.yaml"),
    os.path.join("AppData", "Roaming", "Claude", "claude_desktop_config.json"),
)
#: Directories whose file NAMES are compared: a new file there during a run is the suite's.
FILE_SETS = (".inspeximus", os.path.join("AppData", "Roaming", "inspeximus", "heads"))
#: Transient names a live writer creates and removes by itself.
_TRANSIENT = ("-journal", "-wal", "-shm", ".lock", ".tmp")
#: In ~/.claude.json only these subtrees are configuration. The rest is session state, and a prompt
#: history that mentions inspeximus changes whenever the owner types the word.
_CLAUDE_JSON_KEYS = ("mcpServers",)


def _parts(obj) -> list:
    """Every inspeximus-bearing subtree or string, as canonical JSON, WITHOUT positions: a live session
    that adds an unrelated hook before ours shifts list indices and must not read as a change."""
    out = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            if "inspeximus" in str(k).lower():
                out.append(json.dumps({str(k): v}, sort_keys=True))
            else:
                out += _parts(v)
    elif isinstance(obj, list):
        for v in obj:
            out += _parts(v)
    elif isinstance(obj, str) and "inspeximus" in obj.lower():
        out.append(json.dumps(obj))
    return out


def _config_parts(path: str, rel: str):
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            text = fh.read()
    except OSError:
        return None
    if not rel.endswith(".json"):
        return sorted(ln.strip() for ln in text.splitlines() if "inspeximus" in ln.lower())
    try:
        data = json.loads(text)
    except ValueError:
        return ["<unparseable>"]
    if rel == ".claude.json" and isinstance(data, dict):
        scoped = [data.get(k) for k in _CLAUDE_JSON_KEYS]
        for proj in (data.get("projects") or {}).values():
            if isinstance(proj, dict):
                scoped += [proj.get(k) for k in _CLAUDE_JSON_KEYS]
        data = scoped
    return sorted(_parts(data))


def snapshot(home: str) -> dict:
    """{relative path: inspeximus entries (host configs) or file names (FILE_SETS), None if absent}."""
    snap = {}
    for rel in HOST_CONFIGS:
        snap[rel] = _config_parts(os.path.join(home, rel), rel)
    for rel in FILE_SETS:
        root = os.path.join(home, rel)
        if not os.path.isdir(root):
            snap[rel] = None
            continue
        names = []
        for dp, _dirs, files in os.walk(root):
            for f in files:
                if not f.endswith(_TRANSIENT):
                    names.append(os.path.relpath(os.path.join(dp, f), root))
        snap[rel] = sorted(names)
    return snap


def _writer(home, rel: str, name: str) -> str:
    """For a new chain head: the store it records, which names the writer. A head's file name is a hash
    of that path, so the name alone says nothing. Measured 2026-09-28: a run that exited 1 on this guard
    had 15 new heads; reading their `path` by hand showed 13 temp stores of claims_audit.py and
    governance_audit.py (a concurrent release_check), one pytest temp store and one live MCP store."""
    if home is None or not rel.endswith("heads"):
        return ""
    try:
        with open(os.path.join(home, rel, name), encoding="utf-8") as fh:
            return " (store " + str(json.load(fh).get("path")) + ")"
    except (OSError, ValueError, AttributeError):
        return " (store unreadable)"


def diff(before: dict, after: dict, home: str | None = None) -> list:
    """Human-readable lines, one per path whose guarded content changed; empty when nothing did. With
    `home`, each new chain head also names the store that wrote it."""
    out = []
    for rel in sorted(set(before) | set(after)):
        b, a = before.get(rel), after.get(rel)
        if b == a:
            continue
        # An absent file or directory compares as empty, so a directory the run CREATED still names
        # what it put there: "absent -> present" alone does not say which test to look for.
        added, removed = sorted(set(a or []) - set(b or [])), sorted(set(b or []) - set(a or []))
        state = "" if (b is None) == (a is None) else f" ({'absent' if b is None else 'present'} -> " \
                                                       f"{'absent' if a is None else 'present'})"
        out.append(f"{rel}{state}: +{len(added)} -{len(removed)}, first +{added[:3]} -{removed[:3]}")
        out += [f"    new {n}{_writer(home, rel, n)}" for n in added[:20] if _writer(home, rel, n)]
    return out
