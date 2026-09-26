"""`inspeximus install --all`: ONE memory for every AI agent on this machine.

Measured on 3.13.0 in a clean sandbox, one `--ide` per host: the Claude Code entry resolved to
`<git root>/.inspeximus/coding_memory.json` and the Cursor, Windsurf, Codex and Cline entries to
`inspeximus_memory.json` in whatever directory each host launched them from. Five agents, five or more
stores, and a decision made in one was invisible to the others.

WHY A USER-LEVEL STORE. A project-level store is resolved from the directory a host launches its MCP server
in, and the host chooses that directory: Claude Code passes CLAUDE_PROJECT_DIR, Codex and Gemini CLI start
where the user typed the command, Cursor, Windsurf and Cline document nothing, and Windsurf and Cline have
no project-level MCP config at all. An absolute user-level path is the one location every host reaches the
same way, so a decision made in Claude Code is the one Codex and Gemini recall in the same project. Every
entry names the path explicitly (INSPEXIMUS_PATH), and `~/.inspeximus/shared.json` records it for the
Claude Code hooks, which cannot carry an environment variable.

WHAT IT DOES, per detected host: registers the MCP server pointing at the shared store (merging into an
existing entry, never replacing the user's other settings), pins every host to this version, offers a
one-line rule where the host's docs say nothing about MCP server instructions (asked, never silent), wires
Hermes Agent when its venv is found (asking before changing another memory provider), imports the current
project's old Claude Code store into the shared one, and records one decision proving the store is live.
"""
from __future__ import annotations

import json
import os
import pathlib
import shutil
import subprocess
import sys
import time

from . import install as _i

#: The one sentence a rules file carries. `inspeximus:recall` marks it, so a second run finds it.
RULE_LINE = ("At the start of each task, call the inspeximus MCP tool `recall` with the task's topic, and "
             "record decisions with `remember_decision` (with a `topic`); it is the memory the user shares "
             "across their AI agents. <!-- inspeximus:recall -->")
RULE_MARK = "inspeximus:recall"

#: How each host is told to recall at the start of a task, read from its official docs on 2026-09-26.
#: "instructions": the host passes the MCP server's `instructions` to the model; "hooks": Claude Code's
#: SessionStart hook recalls; "rules": the docs say nothing about server instructions, so a rules file.
RECALL_MECHANISM = {
    "claude": ("hooks", "the SessionStart hook injects recall into every session"),
    "gemini": ("instructions", "appended to the system instructions (gemini-cli docs/tools/mcp-server.md)"),
    "codex": ("instructions", "shown as the server's tool-namespace description "
                              "(openai/codex codex-rs/codex-mcp/src/rmcp_client.rs)"),
    "antigravity": ("rules", "~/.gemini/config/rules/inspeximus.md, trigger always_on "
                             "(antigravity.google/docs/rules)"),
    "cursor": ("rules", "project rule .cursor/rules/inspeximus.mdc, alwaysApply; Cursor keeps global "
                        "rules in its settings UI only (cursor.com/docs/context/rules)"),
    "windsurf": ("rules", "~/.codeium/windsurf/memories/global_rules.md, always on "
                          "(docs.devin.ai/desktop/cascade/memories)"),
    "cline": ("rules", "~/Documents/Cline/Rules/inspeximus.md (docs.cline.bot/features/cline-rules)"),
}


# ── detection ────────────────────────────────────────────────────────────────────────────────────────
def _codex_home():
    return pathlib.Path(os.environ.get("CODEX_HOME") or (_i._home() / ".codex"))


def _detect_markers(host):
    """Paths whose existence means the host is installed for this user, and a command on PATH."""
    h = _i._home()
    return {
        "claude": ([h / ".claude.json", h / ".claude"], "claude"),
        "cursor": ([h / ".cursor"], "cursor"),
        "windsurf": ([h / ".codeium" / "windsurf"], "windsurf"),
        "codex": ([_codex_home()], "codex"),
        "cline": ([h / ".cline"], "cline"),
        "gemini": ([h / ".gemini" / "settings.json"], "gemini"),
        "antigravity": ([h / ".gemini" / "config", h / ".gemini" / "antigravity"], "antigravity"),
    }[host]


def detect(host):
    """(found, why): a config location the host creates, or its command on PATH."""
    paths, cmd = _detect_markers(host)
    for p in paths:
        if p.exists():
            return True, str(p)
    if cmd and shutil.which(cmd):
        return True, f"`{cmd}` on PATH"
    return False, ""


# ── the shared store ─────────────────────────────────────────────────────────────────────────────────
def default_store():
    return _i._home() / ".inspeximus" / "coding_memory.json"


def _existing_store(host):
    """The INSPEXIMUS_PATH an existing entry for this host names, or None."""
    spec = _i.HOSTS[host]
    path = spec["paths"](None).get("user")
    if not path or not path.exists():
        return None
    try:
        if spec["format"] == "json":
            entry = (json.loads(path.read_text(encoding="utf-8") or "{}").get(spec["root_key"]) or {}) \
                .get(_i.SERVER_NAME)
        else:
            import tomllib
            entry = (tomllib.loads(path.read_text(encoding="utf-8")).get("mcp_servers") or {}).get(_i.SERVER_NAME)
    except Exception:                                        # noqa: BLE001 -- unreadable: plan() reports it
        return None
    env = (entry or {}).get("env") or {}
    return env.get("INSPEXIMUS_PATH")


def choose_store(hosts, store=None):
    """(store path, error). An explicit --store wins; otherwise the shared store already recorded; otherwise
    the one path every existing entry agrees on; otherwise the default. Two DIFFERENT paths already written
    are a question for the user, not a guess: picking one would hide the other store's memory."""
    if store:
        return pathlib.Path(store).expanduser().resolve(), None
    from ._surface import shared_store_path
    recorded = shared_store_path()
    if recorded:
        return pathlib.Path(recorded), None
    named = {h: _existing_store(h) for h in hosts}
    distinct = sorted({p for p in named.values() if p})
    if len(distinct) > 1:
        return None, ("your agents already point at different stores: "
                      + "; ".join(f"{_i.HOSTS[h]['label']} -> {p}" for h, p in named.items() if p)
                      + ". Pick one with --store <path>; the others are left as they are.")
    if distinct:
        return pathlib.Path(distinct[0]).expanduser(), None
    return default_store(), None


def write_shared_record(store):
    from ._surface import shared_config_path
    p = pathlib.Path(shared_config_path())
    data = {"store": str(store), "written_by": "inspeximus install --all", "version": _version()}
    old = None
    try:
        old = json.loads(p.read_text(encoding="utf-8"))
    except Exception:                                        # noqa: BLE001
        pass
    if isinstance(old, dict) and old.get("store") == str(store):
        return "unchanged"
    _i._write_json(p, data)
    return "written"


def _version():
    from . import __version__
    return __version__


# ── rules files, where the host ignores MCP instructions ─────────────────────────────────────────────
def rules_target(host, project=None):
    """(path, content to append or create, how) for the host's rules file, or (None, None, manual step)."""
    h = _i._home()
    if host == "antigravity":
        return (h / ".gemini" / "config" / "rules" / "inspeximus.md",
                "---\ntrigger: always_on\n---\n\n" + RULE_LINE + "\n", "create")
    if host == "windsurf":
        return h / ".codeium" / "windsurf" / "memories" / "global_rules.md", RULE_LINE + "\n", "append"
    if host == "cline":
        docs = h / "Documents"
        return docs / "Cline" / "Rules" / "inspeximus.md", RULE_LINE + "\n", "create"
    if host == "cursor":
        from ._surface import find_project_root
        root = find_project_root(project or os.getcwd())
        if not root:
            return None, None, ("Cursor keeps global rules in Customize -> Rules only; paste this line there: "
                                + RULE_LINE)
        return (pathlib.Path(root) / ".cursor" / "rules" / "inspeximus.mdc",
                "---\ndescription: shared memory\nalwaysApply: true\n---\n\n" + RULE_LINE + "\n", "create")
    return None, None, None


def plan_rules(host, project=None):
    path, content, how = rules_target(host, project)
    if path is None:
        return {"host": host, "path": None, "action": "manual" if how else "none", "manual": how}
    if path.exists() and RULE_MARK in path.read_text(encoding="utf-8", errors="replace"):
        return {"host": host, "path": path, "action": "present"}
    return {"host": host, "path": path, "action": how, "content": content}


def apply_rules(r):
    p = r["path"]
    p.parent.mkdir(parents=True, exist_ok=True)
    if r["action"] == "append" and p.exists():
        shutil.copy2(p, str(p) + ".bak")
        text = p.read_text(encoding="utf-8")
        p.write_text(text + ("" if text.endswith("\n") or not text else "\n") + "\n" + r["content"],
                     encoding="utf-8")
    elif r["action"] in ("create", "append"):
        if p.exists():
            shutil.copy2(p, str(p) + ".bak")
        p.write_text(r["content"], encoding="utf-8")


# ── Hermes Agent ─────────────────────────────────────────────────────────────────────────────────────
def hermes_candidates():
    """(hermes_home, venv python) pairs that exist. HERMES_HOME first, then the installer's defaults:
    ~/.hermes (Linux, macOS) and %LOCALAPPDATA%\\hermes (the Windows desktop install)."""
    homes = []
    if os.environ.get("HERMES_HOME"):
        homes.append(pathlib.Path(os.environ["HERMES_HOME"]))
    homes.append(_i._home() / ".hermes")
    if os.environ.get("LOCALAPPDATA"):
        homes.append(pathlib.Path(os.environ["LOCALAPPDATA"]) / "hermes")
    out = []
    for home in dict.fromkeys(homes):
        for venv in (home / "hermes-agent" / "venv", home / "venv"):
            py = venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
            if py.exists():
                out.append((home, py))
                break
    return out


def hermes_provider(config_text):
    """The current `memory.provider` in a Hermes config.yaml, read line by line (no YAML dependency)."""
    in_memory = False
    for line in config_text.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if not line[0].isspace():
            in_memory = line.split("#")[0].strip() == "memory:"
            continue
        if in_memory and line.strip().startswith("provider:"):
            return line.split(":", 1)[1].split("#")[0].strip().strip("'\"") or None
    return None


def hermes_set_provider(config_text):
    """The config text with `memory.provider: inspeximus`, changing only that one line or adding it."""
    lines = config_text.splitlines(True)
    for i, line in enumerate(lines):
        if line.rstrip("\r\n") == "memory:" or line.split("#")[0].strip() == "memory:" and not line[0].isspace():
            j = i + 1
            indent = "  "
            while j < len(lines) and (not lines[j].strip() or lines[j][0].isspace()):
                if lines[j].strip().startswith("provider:"):
                    lead = lines[j][:len(lines[j]) - len(lines[j].lstrip())]
                    lines[j] = f"{lead}provider: inspeximus\n"
                    return "".join(lines)
                if lines[j].strip():
                    indent = lines[j][:len(lines[j]) - len(lines[j].lstrip())]
                j += 1
            lines.insert(i + 1, f"{indent}provider: inspeximus\n")
            return "".join(lines)
    sep = "" if not config_text or config_text.endswith("\n") else "\n"
    return config_text + sep + "memory:\n  provider: inspeximus\n"


def install_into_hermes(py, runner=subprocess.run):
    """Install this version of inspeximus into Hermes' own venv. Hermes ships uv, and its interpreter can
    refuse `pip install` (PEP 668), so uv is tried first."""
    spec = f"inspeximus=={_version()}"
    uv = shutil.which("uv")
    cmds = ([[uv, "pip", "install", "--python", str(py), spec]] if uv else []) + \
        [[str(py), "-m", "pip", "install", "-q", spec]]
    last = None
    for cmd in cmds:
        last = runner(cmd, capture_output=True, text=True)
        if last.returncode == 0:
            return True, " ".join(cmd[:2] + ["...", spec])
    return False, ((last.stderr or last.stdout or "")[-300:] if last else "no installer found")


# ── the first-run proof and the migration ────────────────────────────────────────────────────────────
def record_first_run(store, wired):
    from ._surface import open_store
    m = open_store(str(store))
    labels = ", ".join(_i.HOSTS[h]["label"] for h in wired) or "none"
    text = (f"DECISION: every AI agent on this machine shares one inspeximus memory: {labels}. "
            f"Installed with inspeximus {_version()} on {time.strftime('%Y-%m-%d')}; store {store}.")
    rid = m.remember_decision(text, because="inspeximus install --all wired these agents to one store",
                              topic="inspeximus-setup")
    m.flush()
    return rid if isinstance(rid, str) else (rid or {}).get("id")


def import_project_store(store, project=None):
    """Import the current project's old Claude Code store into the shared one. Returns (count, source)."""
    from ._surface import find_project_root, open_store
    base = project or os.getcwd()
    old = pathlib.Path(find_project_root(base) or base) / ".inspeximus" / "coding_memory.json"
    if not old.exists() or old.resolve() == pathlib.Path(store).resolve():
        return 0, None
    src = open_store(str(old))
    if not src.items:
        return 0, str(old)
    dst = open_store(str(store))
    res = dst.import_changeset(src.export_changeset())
    dst.flush()
    return int((res or {}).get("added", 0)), str(old)


# ── the run ──────────────────────────────────────────────────────────────────────────────────────────
def _ask(question, answer):
    """`answer` is "yes", "no" or "ask". Asking needs a terminal; without one the answer is no."""
    if answer in ("yes", "no"):
        return answer == "yes"
    if not sys.stdin or not sys.stdin.isatty():
        return False
    try:
        return input(question + " [y/N] ").strip().lower() in ("y", "yes")
    except EOFError:
        return False


def run(store=None, dry_run=False, rules="ask", hermes_provider_change="ask", project=None, out=print):
    hosts = list(_i.HOSTS)
    found = {h: detect(h) for h in hosts}
    targets = [h for h in hosts if found[h][0]]
    path, err = choose_store(targets, store)
    if err:
        out("ERROR: " + err)
        return 2
    rows, wired = [], []
    env = {"INSPEXIMUS_PATH": str(path), "INSPEXIMUS_SCOPE": None}
    for h in hosts:
        if not found[h][0]:
            rows.append((_i.HOSTS[h]["label"], "no", "-", "-", ""))
            continue
        p = _i.plan(h, env=env)
        if p.get("error"):
            rows.append((_i.HOSTS[h]["label"], "yes", "ERROR", "-", p["error"]))
            continue
        if dry_run:
            state = p["action"]
        else:
            ok, msg = _i.apply(p)
            state = p["action"] if ok else "ERROR: " + msg
        if not str(state).startswith("ERROR"):
            wired.append(h)
        rows.append((_i.HOSTS[h]["label"], "yes", state, str(path), RECALL_MECHANISM[h][0]))

    notes = []
    # rules, only where the docs say nothing about server instructions; asked, never silent
    for h in wired:
        if RECALL_MECHANISM[h][0] != "rules":
            continue
        r = plan_rules(h, project)
        if r["action"] == "manual":
            notes.append(f"{_i.HOSTS[h]['label']}: {r['manual']}")
        elif r["action"] in ("create", "append"):
            q = (f"{_i.HOSTS[h]['label']} does not document MCP server instructions. Add one line telling "
                 f"its agent to recall at the start of each task to {r['path']}?")
            if not dry_run and _ask(q, rules):
                apply_rules(r)
                notes.append(f"{_i.HOSTS[h]['label']}: rule written to {r['path']}")
            else:
                notes.append(f"{_i.HOSTS[h]['label']}: no rule written. To add it, put this line in "
                             f"{r['path']}: {RULE_LINE}")

    # Hermes Agent
    for home, py in hermes_candidates():
        label = f"Hermes Agent ({home})"
        if dry_run:
            rows.append((label, "yes", "would install", str(path), "provider"))
            continue
        ok, msg = install_into_hermes(py)
        if not ok:
            rows.append((label, "yes", "ERROR", "-", msg))
            continue
        cfg = home / "config.yaml"
        text = cfg.read_text(encoding="utf-8") if cfg.exists() else ""
        current = hermes_provider(text)
        if current in (None, "inspeximus") or _ask(
                f"Hermes uses the memory provider {current!r}. Switch it to inspeximus?", hermes_provider_change):
            if current != "inspeximus":
                if cfg.exists():
                    shutil.copy2(cfg, str(cfg) + ".bak")
                cfg.parent.mkdir(parents=True, exist_ok=True)
                cfg.write_text(hermes_set_provider(text), encoding="utf-8")
            pc = home / "inspeximus" / "config.json"
            pc.parent.mkdir(parents=True, exist_ok=True)
            pc.write_text(json.dumps({"path": str(path)}, indent=2) + "\n", encoding="utf-8")
            rows.append((label, "yes", "provider inspeximus", str(path), "provider"))
            wired.append("hermes")
        else:
            rows.append((label, "yes", f"kept provider {current}", "-", "provider"))

    migrated = source = None
    rid = None
    if not dry_run and wired:
        path.parent.mkdir(parents=True, exist_ok=True)
        write_shared_record(path)
        migrated, source = import_project_store(path, project)
        rid = record_first_run(path, [h for h in wired if h in _i.HOSTS])

    widths = [max(len(str(r[i])) for r in rows + [("host", "found", "wired", "store path", "recall")])
              for i in range(5)]
    head = ("host", "found", "wired", "store path", "recall")
    out("  ".join(str(c).ljust(widths[i]) for i, c in enumerate(head)))
    for r in rows:
        out("  ".join(str(c).ljust(widths[i]) for i, c in enumerate(r)))
    for n in notes:
        out("note: " + n)
    if source:
        out(f"imported {migrated} record(s) from {source} into the shared store")
    if rid:
        out(f"first-run decision recorded in {path} (id {rid}); every agent's recall finds it.")
    if dry_run:
        out("(dry run - nothing written)")
    else:
        out("Restart each app listed as wired, so it starts the memory server.")
    return 0 if all(not str(r[2]).startswith("ERROR") for r in rows) else 1
