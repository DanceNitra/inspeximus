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
import re
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
    "devin": ("rules", "AGENTS.md in the Devin config directory, loaded at the start of every session "
                       "(docs.devin.ai/cli/extensibility/rules)"),
}


# ── detection ────────────────────────────────────────────────────────────────────────────────────────
def _codex_home():
    return pathlib.Path(os.environ.get("CODEX_HOME") or (_i._home() / ".codex"))


def _install_markers(host):
    """(commands, globs) that mean the APP is installed: a command on PATH, or an install location.

    A CONFIG FOLDER IS NOT AN INSTALL (3.14.1). 3.14.0 counted `~/.gemini/config` as Antigravity and
    `~/.codeium/windsurf` as Windsurf, so a Gemini CLI user got an Antigravity entry and an uninstalled
    Windsurf got a config written for it. Measured on the owner's machine before the dogfood run. Each
    glob is an install location: the per-user Windows install folder, the macOS app bundle, the
    Microsoft Store package folder, or the VS Code extension folder for Cline."""
    h = _i._home()
    la = h / "AppData" / "Local"
    apps = [pathlib.Path("/Applications"), h / "Applications"]

    def app(name):
        return [str(la / "Programs" / name)] + [str(a / (name + ".app")) for a in apps]
    ext = [str(h / d / "extensions" / "saoudrizwan.claude-dev-*")
           for d in (".vscode", ".vscode-insiders", ".cursor", ".windsurf")]
    return {
        "claude": (["claude"], [str(h / ".claude" / "local" / "claude*"), str(h / ".local" / "bin" / "claude*")]),
        "gemini": (["gemini"], []),
        "codex": (["codex"], [str(la / "Packages" / "OpenAI.Codex_*"), str(la / "Programs" / "OpenAI" / "Codex")]
                  + [str(a / "Codex.app") for a in apps]),
        "antigravity": (["antigravity"], app("Antigravity")),
        "cursor": (["cursor"], app("cursor") + app("Cursor")),
        "devin": (["devin"], app("Devin")),
        "windsurf": (["windsurf"], app("Windsurf")),
        "cline": (["cline"], ext),
    }[host]


def detect(host):
    """(found, why): the host's command on PATH, or an install location that exists."""
    import glob
    cmds, globs = _install_markers(host)
    for cmd in cmds:
        if shutil.which(cmd):
            return True, f"`{cmd}` on PATH"
    for g in globs:
        hit = glob.glob(g)
        if hit:
            return True, hit[0]
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
    if host == "devin":
        return _i.devin_dir() / "AGENTS.md", RULE_LINE + "\n", "append"
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
        py = _pm_python(home)
        if py is None:
            for venv in (home / "hermes-agent" / "venv", home / "venv"):
                cand = venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
                if cand.exists():
                    py = cand
                    break
        if py is not None:
            out.append((home, py))
    return out


def _pm_python(home):
    """The interpreter of a current Hermes install, which its package manager (`pm`) records in
    <home>/installs/<key>/facts.json under packages.venv.environment.

    MEASURED IN CI 2026-09-27: the official installer on Linux and macOS finished with exit 0 and no
    <home>/hermes-agent/venv at all, so 3.14.1 as first written reported no Hermes. `pm` builds each
    environment under <home>/installs/<key>/environments/ and deletes the in-tree venv once one is
    committed (hermes-agent pm/environments.py). The in-tree venv is still read for older installs."""
    import glob
    for facts in sorted(glob.glob(str(home / "installs" / "*" / "facts.json"))):
        try:
            data = json.loads(pathlib.Path(facts).read_text(encoding="utf-8-sig"))
            env = ((data.get("packages") or {}).get("venv") or {}).get("environment")
        except (OSError, ValueError, AttributeError):
            continue
        if isinstance(env, str) and env:
            py = pathlib.Path(env) / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
            if py.exists():
                return py
    return None


def hermes_root(py):
    """The Hermes checkout for an interpreter: <home>/hermes-agent above it, or None. Hermes' own modules
    (`plugins.memory`) are imported from there, as Hermes itself does."""
    for parent in pathlib.Path(py).parents:
        cand = parent / "hermes-agent"
        if (cand / "plugins" / "memory").is_dir():
            return cand
        if parent.name == "hermes-agent" and (parent / "plugins" / "memory").is_dir():
            return parent
    return None


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


#: Where a user gets the Hermes build whose loader discovers provider packages.
HERMES_INSTALL_DOCS = "https://hermes-agent.nousresearch.com/docs/getting-started/installation"


def hermes_installs():
    """(home, python or None, kind) for every Hermes install found.

    The official installer's layout comes first (see hermes_candidates). A `hermes` command on PATH
    that lives outside those homes is a pip install, and its interpreter is the Python beside it.
    Measured 2026-09-27: the hermes-agent package on PyPI is 0.19.0, and its provider loader has
    no entry-point discovery, so it never lists inspeximus; the official install (0.21.3 on the owner's
    machine) does. The kind is reported, and hermes_loads_provider() decides, not the version number."""
    out = [(home, py, "official installer") for home, py in hermes_candidates()]
    exe = shutil.which("hermes")
    if exe:
        d = pathlib.Path(exe).resolve().parent
        inside = any(os.path.normcase(str(d)).startswith(os.path.normcase(str(home))) for home, _, _ in out)
        if not inside:
            py = d / ("python.exe" if os.name == "nt" else "python")
            home = pathlib.Path(os.environ.get("HERMES_HOME") or (_i._home() / ".hermes"))
            out.append((home, py if py.exists() else None, "pip install"))
    return out


def _hermes_python(py, code, runner=subprocess.run):
    root = hermes_root(py)
    env = dict(os.environ)
    if root:
        env["PYTHONPATH"] = os.pathsep.join(p for p in (str(root), env.get("PYTHONPATH")) if p)
    try:
        r = runner([str(py), "-c", code], capture_output=True, text=True, encoding="utf-8",
                   errors="replace", timeout=180, cwd=str(root) if root else None, env=env)
    except (OSError, subprocess.SubprocessError) as e:
        return False, repr(e)[:200]
    return r.returncode == 0, ((r.stdout or "").strip() or (r.stderr or "")[-200:])


def hermes_version(py, runner=subprocess.run):
    ok, out = _hermes_python(py, "import importlib.metadata as m; print(m.version('hermes-agent'))", runner)
    return out.splitlines()[-1] if ok and out else "unknown version"


def hermes_loads_provider(py, runner=subprocess.run):
    """Ask Hermes' OWN loader, in its own venv, whether it lists the inspeximus provider. A behaviour
    check rather than a version check: a build either discovers the package or it does not."""
    ok, out = _hermes_python(py, "from plugins.memory import list_memory_provider_names as f; "
                                 "print('inspeximus' in f())", runner)
    return ok and out.splitlines()[-1:] == ["True"]


def _uv_for(py):
    """uv on PATH, else the uv the official Hermes installer ships in <hermes home>/bin or /tools. A venv
    that uv built has no pip, so without uv there is no way to install into it."""
    import glob
    found = shutil.which("uv")
    if found:
        return found
    exe = "uv.exe" if os.name == "nt" else "uv"
    # the Hermes home is some parent of the interpreter: <home>/hermes-agent/venv/<bin>/python on older
    # installs, <home>/installs/<key>/environments/<generation>/<bin>/python with `pm`
    for home in list(pathlib.Path(py).resolve().parents)[:7]:
        for pattern in (home / "bin" / exe, home / "tools" / exe, home / "tools" / "*" / exe,
                        home / "tools" / "*" / "*" / exe):
            hit = sorted(glob.glob(str(pattern)))
            if hit:
                return hit[0]
    return None


def install_into_hermes(py, runner=subprocess.run):
    """Install this version of inspeximus into Hermes' own venv. Hermes ships uv, and its interpreter can
    refuse `pip install` (PEP 668), so uv is tried first."""
    spec = f"inspeximus=={_version()}"
    uv = _uv_for(py)
    cmds = ([[uv, "pip", "install", "--python", str(py), spec]] if uv else []) + \
        [[str(py), "-m", "pip", "install", "-q", spec]]
    last = None
    for cmd in cmds:
        last = runner(cmd, capture_output=True, text=True)
        if last.returncode == 0:
            return True, " ".join(cmd[:2] + ["...", spec])
    return False, ((last.stderr or last.stdout or "")[-300:] if last else "no installer found")


def uninstall_from_hermes(py, runner=subprocess.run):
    """Undo install_into_hermes() when the build cannot load the provider, so "cannot load" leaves Hermes
    as it was."""
    uv = _uv_for(py)
    for cmd in ([[uv, "pip", "uninstall", "--python", str(py), "inspeximus"]] if uv else []) + \
            [[str(py), "-m", "pip", "uninstall", "-y", "-q", "inspeximus"]]:
        if runner(cmd, capture_output=True, text=True).returncode == 0:
            return True
    return False


# ── the first-run proof and the migration ────────────────────────────────────────────────────────────
def record_first_run(store, wired):
    from ._surface import open_store
    m = open_store(str(store))
    labels = ", ".join("Hermes Agent" if h == "hermes" else _i.HOSTS[h]["label"] for h in wired) or "none"
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


#: Table order (3.14.1): the agents our first testers use first. Hermes Agent's rows come before these.
HOST_ORDER = ("claude", "gemini", "codex", "antigravity", "cursor", "devin", "windsurf", "cline")


def _hermes(path, change, dry_run, notes, wired):
    """Hermes Agent's rows. ONLY "yes" touches Hermes (3.14.1): 3.14.0 installed into Hermes' venv and set
    memory.provider on "no" whenever no provider was set, so "no" did not mean no."""
    rows = []
    for home, py, kind in hermes_installs():
        label = f"Hermes Agent ({home})"
        cfg = home / "config.yaml"
        text = cfg.read_text(encoding="utf-8") if cfg.exists() else ""
        current = hermes_provider(text)
        if change != "yes":
            rows.append((label, "yes", "skipped (no)", "-", "provider"))
            notes.append("Hermes Agent: skipped (--hermes-provider no), nothing in Hermes was changed"
                         + (f"; kept provider {current}" if current else "")
                         + ". To use the shared memory in Hermes, run install --all again with --hermes-provider yes")
            continue
        if py is None:
            rows.append((label, "yes", f"cannot load provider ({kind})", "-", "provider"))
            notes.append(f"Hermes Agent ({kind}): its Python was not found, so nothing in it was changed. "
                         f"Install Hermes with its official installer ({HERMES_INSTALL_DOCS}), then run "
                         f"install --all again")
            continue
        if dry_run:
            rows.append((label, "yes", "would install", str(path), "provider"))
            continue
        had_it = _hermes_python(py, "import inspeximus")[0]
        ok, msg = install_into_hermes(py)
        if not ok:
            rows.append((label, "yes", "ERROR", "-", msg))
            continue
        if not hermes_loads_provider(py):
            if not had_it:
                uninstall_from_hermes(py)
            ver = hermes_version(py)
            rows.append((label, "yes", f"cannot load provider (hermes-agent {ver}, {kind})", "-", "provider"))
            notes.append(f"Hermes Agent: hermes-agent {ver} ({kind}) does not load memory-provider packages, so "
                         f"nothing in it was changed. Install Hermes with its official installer "
                         f"({HERMES_INSTALL_DOCS}), then run install --all again")
            continue
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
    return rows


def select(only=None):
    """(hosts, include Hermes, error) for `--only a,b,c`; every agent when `only` is empty.

    3.14.3: the dogfood run on 2026-09-27 needed exactly Claude Code and Codex, and `--all` would also
    have written Cline, Windsurf and Antigravity, so the per-agent step had to be called by hand."""
    hosts = [h for h in HOST_ORDER if h in _i.HOSTS] + [h for h in _i.HOSTS if h not in HOST_ORDER]
    if not only:
        return hosts, True, None
    names = [n.strip().lower() for n in (only if isinstance(only, (list, tuple)) else str(only).split(",")) if n.strip()]
    valid = set(hosts) | {"hermes"}
    unknown = [n for n in names if n not in valid]
    if unknown:
        return None, False, (f"unknown agent(s) for --only: {', '.join(unknown)}; "
                             f"known: hermes, {', '.join(hosts)}")
    return [h for h in hosts if h in names], "hermes" in names, None


def run(store=None, dry_run=False, rules="ask", hermes_provider_change="no", project=None, out=print, only=None):
    hosts, with_hermes, err = select(only)
    if err:
        out("ERROR: " + err)
        return 2
    found = {h: detect(h) for h in hosts}
    targets = [h for h in hosts if found[h][0]]
    path, err = choose_store(targets, store)
    if err:
        out("ERROR: " + err)
        return 2
    rows, wired, notes = [], [], []
    # NEVER A PROMPT HERE. An agent runs this installer, and a question it cannot see hangs the install in
    # a pseudo-terminal. The agent asks the user first and passes the answer.
    if with_hermes:
        rows += _hermes(path, hermes_provider_change, dry_run, notes, wired)
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

    # rules, only where the docs say nothing about server instructions; asked, never silent
    for h in wired:
        if RECALL_MECHANISM.get(h, ("provider",))[0] != "rules":         # Hermes recalls through its provider
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

    migrated = source = None
    rid = None
    if not dry_run and wired:
        path.parent.mkdir(parents=True, exist_ok=True)
        write_shared_record(path)
        migrated, source = import_project_store(path, project)
        rid = record_first_run(path, [h for h in wired if h in _i.HOSTS or h == "hermes"])

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


# ── install --check: read-only, what each agent's config says now ──────────────────────────────────────
def _pin(entry):
    for a in (entry or {}).get("args") or []:
        m = re.search(r"inspeximus(?:\[[^\]]*\])?==([0-9][0-9A-Za-z.+-]*)", str(a))
        if m:
            return m.group(1)
    return None


def _same_file(a, b):
    try:
        return os.path.normcase(os.path.abspath(os.path.expanduser(str(a)))) == \
            os.path.normcase(os.path.abspath(os.path.expanduser(str(b))))
    except (TypeError, ValueError):
        return False


def entry_status(entry, store):
    """'ok', or the reasons an entry no longer matches this version and this store."""
    if not entry:
        return "not wired"
    reasons = []
    pin = _pin(entry)
    cmd = str(entry.get("command") or "")
    if pin and pin != _version():
        reasons.append(f"pin {pin}, this is {_version()}")
    elif not pin and not (cmd and (shutil.which(cmd) or os.path.exists(cmd))):
        reasons.append(f"command not found: {cmd or '(none)'}")
    path = ((entry.get("env") or {}).get("INSPEXIMUS_PATH"))
    if not path:
        reasons.append("no INSPEXIMUS_PATH")
    elif store and not _same_file(path, store):
        reasons.append(f"store {path}, shared store {store}")
    return "ok" if not reasons else "DIFFERS: " + "; ".join(reasons)


def check(store=None, only=None, out=print):
    """Read every agent's config and report whether its inspeximus entry still matches this version and
    the shared store. Writes nothing. Returns 1 when an entry differs (or an agent named in --only is
    not wired), else 0.

    Written for 2026-09-27: a Claude Code entry `install --all` had written read back `==3.14.0` two
    hours later, and only a person reading the file noticed."""
    hosts, with_hermes, err = select(only)
    if err:
        out("ERROR: " + err)
        return 2
    from ._surface import shared_store_path
    entries = {h: _i.read_entry(h) for h in hosts}
    if store:
        want = str(pathlib.Path(store).expanduser().resolve())
    else:
        named = sorted({(e or {}).get("env", {}).get("INSPEXIMUS_PATH") for _, e, _ in entries.values()
                        if e and (e.get("env") or {}).get("INSPEXIMUS_PATH")})
        want = shared_store_path() or (named[0] if len(named) == 1 else None)
    rows, bad = [], 0
    for h in hosts:
        path, entry, rerr = entries[h]
        found = detect(h)[0]
        status = f"ERROR: {rerr}" if rerr else entry_status(entry, want)
        if not found and not entry:
            status = "-"
        flagged = status.startswith(("DIFFERS", "ERROR")) or (only and status == "not wired")
        bad += bool(flagged)
        store_now = ((entry or {}).get("env") or {}).get("INSPEXIMUS_PATH") or "-"
        rows.append((_i.HOSTS[h]["label"], "yes" if found else "no", _pin(entry) or ("-" if not entry else "python"),
                     store_now, status))
    if with_hermes:
        for home, py, kind in hermes_installs():
            cfg = home / "config.yaml"
            provider = hermes_provider(cfg.read_text(encoding="utf-8")) if cfg.exists() else None
            pc = home / "inspeximus" / "config.json"
            try:
                hpath = json.loads(pc.read_text(encoding="utf-8")).get("path") if pc.exists() else None
            except (OSError, ValueError):
                hpath = None
            if provider != "inspeximus":
                status = "not wired" if not provider else f"not wired (provider {provider})"
            else:
                reasons = []
                if want and hpath and not _same_file(hpath, want):
                    reasons.append(f"store {hpath}, shared store {want}")
                if not hpath:
                    reasons.append("no store path in inspeximus/config.json")
                if py is None or not hermes_loads_provider(py):
                    reasons.append("Hermes' loader does not list inspeximus (run install --all again)")
                status = "ok" if not reasons else "DIFFERS: " + "; ".join(reasons)
            flagged = status.startswith("DIFFERS") or (only and status.startswith("not wired"))
            bad += bool(flagged)
            rows.insert(0, (f"Hermes Agent ({home})", "yes", kind, hpath or "-", status))
    head = ("host", "found", "pin", "store path", "status")
    w = [max(len(str(r[i])) for r in rows + [head]) for i in range(5)]
    for r in [head] + rows:
        out("  ".join(str(c).ljust(w[i]) for i, c in enumerate(r)))
    out(f"shared store: {want or 'none recorded'}; this is inspeximus {_version()}")
    if bad:
        out(f"{bad} agent(s) need attention. To rewrite them: inspeximus install --all"
            + (f" --only {only if isinstance(only, str) else ','.join(only)}" if only else ""))
    return 1 if bad else 0
