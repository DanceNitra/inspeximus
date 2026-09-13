"""Does the Hermes Agent installed on this machine discover, load and drive our provider?

WHY THIS EXISTS. The provider's test suite runs against a STAND-IN for Hermes' base class, because
Hermes is not a PyPI distribution and must never become a test dependency. A stand-in checks our
methods against our copy of the contract; it cannot check what the host's loader actually does with
a pip entry point. Read from the host's own plugins/memory/__init__.py on 2026-09-13 (0.21.1):

  - it tries `loaded()` first and accepts a returned MemoryProvider, which is the path the first
    version of `register()` relied on alone, while the documented contract is
    `register(ctx)` -> `ctx.register_memory_provider(p)`;
  - a bare-module entry point has no directory, so the host cannot read `config_schema.py` or
    `cli.py` from disk beside it, and the provider shows an empty description in the list;
  - the dashboard still builds a panel through the legacy `get_config_schema()` surface.

None of that is visible to the stand-in. This probe runs the host's real loader, in the host's own
venv, against whatever inspeximus that venv holds.

Refuses (exit 3) when no Hermes install is found, because a probe with no host reports nothing and
"nothing" reads as "fine". Prints the host commit so a breakage can be dated.

Run: python -X utf8 probes/does_the_installed_hermes_actually_load_our_provider.py
"""
from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parent

CANDIDATES = [
    pathlib.Path(os.path.expandvars(r"%LOCALAPPDATA%\hermes\hermes-agent")),
    pathlib.Path.home() / ".local" / "share" / "hermes" / "hermes-agent",
    pathlib.Path.home() / "hermes-agent",
]

#: What runs INSIDE the host's venv. Kept as a string so this file has no import of Hermes.
DRIVER = r'''
import json, tempfile, sys
out = {"steps": []}
def step(name, ok, detail=""):
    out["steps"].append({"name": name, "ok": bool(ok), "detail": str(detail)[:300]})
try:
    import inspeximus
    step("inspeximus importable in the host venv", True, inspeximus.__version__)
    out["inspeximus_version"] = inspeximus.__version__
except Exception as e:
    step("inspeximus importable in the host venv", False, repr(e)); print(json.dumps(out)); sys.exit(0)
from plugins.memory import (discover_memory_providers, load_memory_provider,
                            list_memory_provider_names, find_provider_dir)
from agent.memory_provider import MemoryProvider
names = list_memory_provider_names()
step("listed by name", "inspeximus" in names, names)
disc = {n: (ok, d) for n, d, ok in discover_memory_providers()}
step("discovered as available", disc.get("inspeximus", (False, ""))[0], disc.get("inspeximus"))
out["description_in_list"] = disc.get("inspeximus", (None, ""))[1]
out["provider_dir"] = str(find_provider_dir("inspeximus"))
p = load_memory_provider("inspeximus", register_skills=False)
step("loaded by the host loader", p is not None, type(p).__name__ if p else None)
step("isinstance of the HOST's MemoryProvider", isinstance(p, MemoryProvider))
p.initialize("probe", hermes_home=tempfile.mkdtemp())
tools = [t["name"] for t in p.get_tool_schemas()]
step("five tools", len(tools) == 5, tools)
p.handle_tool_call("inspeximus_remember", {"text": "the release branch is release-1", "key": "repo::branch"})
p.handle_tool_call("inspeximus_correct", {"key": "repo::branch", "text": "the release branch is release-2"})
pf = p.prefetch("release branch")
step("prefetch excludes the retired value", "release-2" in pf and "release-1" not in pf, pf)
rs = p.recall_status()
step("recall_status is the host's RecallStatus", rs is not None and rs.count == 1, rs)
pc = p.on_pre_compress([{"role": "user", "content": "deploy from the release branch"}])
step("on_pre_compress returns the current value", "release-2" in pc and "release-1" not in pc)
try:
    from hermes_cli.web_routers.memory_providers import _load_memory_provider, _memory_provider_payload
    pl = _memory_provider_payload("inspeximus", _load_memory_provider("inspeximus"))
    step("dashboard panel builds (legacy surface)", bool(pl["fields"]), [f["key"] for f in pl["fields"]])
except Exception as e:
    step("dashboard panel builds (legacy surface)", False, repr(e))
print(json.dumps(out))
'''


def main() -> int:
    root = next((c for c in CANDIDATES if (c / "plugins" / "memory").is_dir()), None)
    if root is None:
        print("REFUSED: no Hermes installation found; a probe with no host reports nothing.")
        for c in CANDIDATES:
            print("  looked in " + str(c))
        return 3
    py = root / "venv" / ("Scripts" if os.name == "nt" else "bin") / ("python.exe" if os.name == "nt" else "python")
    if not py.is_file():
        print("REFUSED: Hermes found at %s but no venv python at %s" % (root, py))
        return 3

    commit = subprocess.run(["git", "-C", str(root), "log", "-1", "--format=%h %ci"],
                            capture_output=True, text=True).stdout.strip()
    print("host: %s  (%s)" % (root, commit or "commit unknown"))

    env = {**os.environ, "PYTHONPATH": str(root), "PYTHONIOENCODING": "utf-8"}
    r = subprocess.run([str(py), "-X", "utf8", "-"], input=DRIVER, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", env=env, cwd=str(root), timeout=300)
    line = [l for l in r.stdout.splitlines() if l.startswith("{")]
    if not line:
        print("the driver produced no result; stderr tail:\n" + r.stderr[-1500:])
        return 2
    out = json.loads(line[-1])
    out["host"] = str(root)
    out["host_commit"] = commit
    failed = [s for s in out["steps"] if not s["ok"]]
    for s in out["steps"]:
        print("  %s  %s  %s" % ("ok " if s["ok"] else "FAIL", s["name"], s["detail"]))
    print("  provider dir: %s   description in list: %r" % (out.get("provider_dir"), out.get("description_in_list")))
    (HERE / (pathlib.Path(__file__).stem + ".result.json")).write_text(
        json.dumps(out, indent=2), encoding="utf-8")
    print("\n%d of %d steps passed" % (len(out["steps"]) - len(failed), len(out["steps"])))
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
