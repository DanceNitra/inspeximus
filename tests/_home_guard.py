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
#:
#: THE WHOLE KEY HOME, IN BOTH VIEWS (AUDIT-A, 2026-10-08). This listed only `heads`, so a key written into the real
#: `keys` folder passed unnoticed: the float16 probe wrote 6 key and head pairs there on 10-07 and 10-08. A Microsoft
#: Store Python also redirects every write under %APPDATA% into its package folder (`LocalCache\Roaming`), which it
#: reads merged with the real one and which Git Bash and every other program cannot see; that view is listed too. A
#: name with `*` is a glob, relative to the home.
KEY_HOME_VIEWS = (os.path.join("AppData", "Roaming", "inspeximus"),
                  os.path.join("AppData", "Local", "Packages", "PythonSoftwareFoundation.Python.*", "LocalCache",
                               "Roaming", "inspeximus"))
FILE_SETS = (".inspeximus",) + KEY_HOME_VIEWS
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
    import glob
    rels = []
    for rel in FILE_SETS:
        if "*" in rel:
            rels += [os.path.relpath(p, home) for p in sorted(glob.glob(os.path.join(home, rel)))]
        else:
            rels.append(rel)
    for rel in rels:
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


def _is_key_home(rel: str) -> bool:
    """`rel` is one of the views of the key home, the real folder or a Store Python's package copy."""
    return os.path.normcase(rel).endswith(os.path.normcase(os.path.join("Roaming", "inspeximus")))


def _is_head(rel: str, name: str) -> bool:
    return _is_key_home(rel) and name.split(os.sep)[0] == "heads"


def _writer(home, rel: str, name: str) -> str:
    """For a new chain head: the store it records, which names the writer. A head's file name is a hash
    of that path, so the name alone says nothing. Measured 2026-09-28: a run that exited 1 on this guard
    had 15 new heads; reading their `path` by hand showed 13 temp stores of claims_audit.py and
    governance_audit.py (a concurrent release_check), one pytest temp store and one live MCP store."""
    if home is None or not _is_head(rel, name):
        return ""
    try:
        with open(os.path.join(home, rel, name), encoding="utf-8") as fh:
            return " (store " + str(json.load(fh).get("path")) + ")"
    except (OSError, ValueError, AttributeError):
        return " (store unreadable)"


def _head_store(home, rel: str, name: str):
    try:
        with open(os.path.join(home, rel, name), encoding="utf-8") as fh:
            return str(json.load(fh).get("path") or "") or None
    except (OSError, ValueError, AttributeError):
        return None


def _inside(path: str, roots: list) -> bool:
    p = os.path.normcase(os.path.realpath(path))
    return any(p == r or p.startswith(r.rstrip(os.sep) + os.sep) for r in roots)


def _tag(name: str) -> str:
    """The store tag in a key-home file name: `heads/<tag>.json`, `keys/<tag>.guards.key`, `stamp-auto/<tag>.json`."""
    return os.path.basename(name).split(".")[0]


def _record_store(home, rel: str, name: str):
    """The store a JSON record in the key home names (`path` or `store`), or None."""
    if not name.endswith(".json"):
        return None
    try:
        with open(os.path.join(home, rel, name), encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return None
    if isinstance(data, dict):
        for k in ("path", "store", "store_path"):
            if isinstance(data.get(k), str) and data[k]:
                return data[k]
    return None


def _modified(home, rel: str, name: str) -> str:
    import time
    try:
        return time.strftime("%H:%M:%S", time.localtime(os.path.getmtime(os.path.join(home, rel, name))))
    except OSError:
        return "unknown"


def run_tags(roots) -> set:
    """The store tag (`sha256(abspath)[:16]`, what a key, a salt, a head and a run-state record are named by) of every file
    and folder under `roots`, so a key-home file for a store this run created is found without a head to read it from
    (G-1a). Walks each root as given and as its real path."""
    import hashlib
    tags, seen = set(), set()
    for root in roots:
        for base in {str(root), os.path.realpath(str(root))}:
            if base in seen or not os.path.isdir(base):
                continue
            seen.add(base)
            for dp, dirs, files in os.walk(base):
                for n in dirs + files:
                    tags.add(hashlib.sha256(os.path.abspath(os.path.join(dp, n)).encode("utf-8", "replace")).hexdigest()[:16])
            tags.add(hashlib.sha256(os.path.abspath(base).encode("utf-8", "replace")).hexdigest()[:16])
    return tags


def classify(before: dict, after: dict, home: str, temp_roots=None) -> tuple:
    """(failing lines, information lines). ATTRIBUTION BY STORE PATH (3.17.0, EM's decision of 2026-10-09).

    A new file in the real key home is this run's, and fails the run, only when it names a store under `temp_roots`:
    a new head, or a record that names its store, by the path it records; a key, a salt or a stamp-auto or hookd record
    by the head with the same tag. Every other new file gets one information line with its name and time and does not
    fail: a live hook, a live session's lazy heal or a daemon writes such files while a suite runs (measured
    2026-10-08: a key appeared two minutes into a run that had written none). A file that is REMOVED still fails. The
    conftest passes this run's own temporary root (3.16.3), which TEMP, TMP and TMPDIR point at for every test and child.

    Not seen, by design (G-2): a file the run overwrote with the same name, and a file the run created and deleted again
    inside the run. Both leave the set of names as it was. Not handled yet (G-1c, for 3.18): a file REMOVED by a live daemon
    at its idle exit still fails the run; the daemon is not in 3.17.0."""
    import tempfile
    raw_roots = list(temp_roots or [tempfile.gettempdir()])
    roots = [os.path.normcase(os.path.realpath(r)) for r in raw_roots]
    fail, info = [], []
    tags = None
    views = [r for r in set(before) | set(after) if _is_key_home(r)]
    for rel in sorted(views):
        b, a = before.get(rel), after.get(rel)
        if b == a:
            continue
        added, removed = sorted(set(a or []) - set(b or [])), sorted(set(b or []) - set(a or []))
        by_tag = {}
        for n in (a or []):
            if _is_head(rel, n):
                st = _head_store(home, rel, n)
                if st:
                    by_tag[_tag(n)] = st

        def store_of(n, _rel=rel, _by=by_tag):
            if _is_head(_rel, n):
                return _by.get(_tag(n))
            return _record_store(home, _rel, n) or _by.get(_tag(n))

        stores = {n: store_of(n) for n in added}
        if tags is None and added:
            tags = run_tags(raw_roots)
        # THIS RUN'S: a store under its temp root (G-1); a tag that is the hash of a path under it, with or without a head
        # (G-1a: a harness key of a temp store has no head); a head that is unreadable or names no store (G-1b: a foreign
        # head is written atomically, so a half-written one is not a live writer's).
        mine = [n for n in added if (stores[n] and _inside(stores[n], roots)) or (tags and _tag(n) in tags)
                or (_is_head(rel, n) and not stores[n])]
        other = [n for n in added if n not in mine]
        if mine or removed:
            fail.append(f"{rel}: +{len(mine)} -{len(removed)} (new files in the real key home for this run's temp stores), "
                        f"first +{mine[:3]} -{removed[:3]}")
            fail += [f"    new {n} ({'store ' + stores[n] if stores[n] else 'no readable store; tag ' + _tag(n)})"
                     for n in mine[:20]]
        if other:
            info.append(f"{rel}: {len(other)} new file(s) not attributed to this run (no store under this run's temp root), "
                        f"written by another process during the run:")
            info += [f"    new {n} ({'store ' + stores[n] if stores[n] else 'no store recorded'}), modified "
                     f"{_modified(home, rel, n)}" for n in other[:20]]
    fail = diff({k: v for k, v in before.items() if k not in views},
                {k: v for k, v in after.items() if k not in views}, home) + fail
    return fail, info


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
