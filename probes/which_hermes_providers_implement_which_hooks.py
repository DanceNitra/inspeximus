"""Which of Hermes Agent's bundled memory providers implement which optional hooks.

WHY THIS EXISTS. The adapter's docstring made a comparative claim about the bundled providers, and
the first version of it was FALSE: it said none of the eight implements `on_pre_compress` or
`on_memory_write`, while six implement the second and one implements the first. The claim was typed
from an argument rather than read from the code. This probe reads the code.

It reads a REAL Hermes installation, not a copy: the plugin directory that the running host loads.
An absent installation is a refusal, not a pass, because a scan with no target reports a comfortable
zero for every hook.

Run: python -X utf8 probes/which_hermes_providers_implement_which_hooks.py
"""
from __future__ import annotations

import json
import os
import pathlib
import re
import sys

HERE = pathlib.Path(__file__).resolve().parent

#: Where the desktop installer puts the host on Windows, and the two POSIX locations.
CANDIDATES = [
    pathlib.Path(os.path.expandvars(r"%LOCALAPPDATA%\hermes\hermes-agent")),
    pathlib.Path.home() / ".local" / "share" / "hermes" / "hermes-agent",
    pathlib.Path.home() / "hermes-agent",
]

#: Every optional hook the base class offers, so the table cannot be curated to flatter us.
HOOKS = [
    "queue_prefetch", "recall_status", "sync_turn", "on_turn_start", "identity_signature",
    "on_session_end", "on_session_switch", "on_pre_compress", "on_delegation",
    "get_config_schema", "save_config", "on_memory_write", "backup_paths",
]


def _root() -> pathlib.Path | None:
    for c in CANDIDATES:
        if (c / "plugins" / "memory").is_dir() and (c / "agent" / "memory_provider.py").is_file():
            return c
    return None


def _implements(pkg: pathlib.Path, hook: str) -> bool:
    pat = re.compile(r"^\s*(async\s+)?def\s+%s\s*\(" % re.escape(hook), re.M)
    return any(pat.search(f.read_text(encoding="utf-8", errors="replace"))
               for f in pkg.rglob("*.py"))


def main() -> int:
    root = _root()
    if root is None:
        print("REFUSED: no Hermes installation found. Looked in:")
        for c in CANDIDATES:
            print("  " + str(c))
        print("A scan with no target reports zero for every hook, which is why this is a refusal.")
        return 3

    base = (root / "agent" / "memory_provider.py").read_text(encoding="utf-8", errors="replace")
    declared = [h for h in HOOKS if re.search(r"^\s*def\s+%s\s*\(" % re.escape(h), base, re.M)]
    missing = sorted(set(HOOKS) - set(declared))
    if missing:
        print("WARNING: the host no longer declares %s; the table below is stale." % missing)

    pkgs = sorted(d for d in (root / "plugins" / "memory").iterdir()
                  if d.is_dir() and (d / "__init__.py").is_file())
    table = {d.name: {h: _implements(d, h) for h in declared} for d in pkgs}

    width = max(len(n) for n in table) + 2
    print("Hermes providers bundled at %s" % root)
    print()
    print("%-*s %s" % (width, "provider", "  ".join(h[:14] for h in declared)))
    for name, row in table.items():
        print("%-*s %s" % (width, name,
                           "  ".join(("yes" if row[h] else " . ").ljust(min(len(h), 14))
                                     for h in declared)))
    print()
    totals = {h: sum(1 for r in table.values() if r[h]) for h in declared}
    for h in declared:
        print("  %-22s %d of %d" % (h, totals[h], len(table)))

    out = HERE / (pathlib.Path(__file__).stem + ".result.json")
    out.write_text(json.dumps(
        {"root": str(root), "providers": sorted(table), "declared_hooks": declared,
         "implemented_by": totals, "table": table}, indent=2), encoding="utf-8")
    print("\nwrote %s" % out.name)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
