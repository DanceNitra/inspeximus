"""How many tests does this environment actually run, and how many does it only appear to?

A module-level `pytest.importorskip` makes an entire file vanish as a SINGLE skip line. `test_examples_run.py`
holds 29 tests; without its dependency the suite says "1 skipped" and moves on. Summed across 21 modules that
is 240 tests, of which roughly a hundred had never once run in CI -- while the run reported
"993 passed, 43 skipped" and looked healthy. Nobody decided to exclude them; it was the default, and the
default was invisible.

So this counts tests by PARSING, not by collecting -- an environment cannot hide a test from `ast`. It
prints what runs here, what does not, and why, and names the pip install that would close each gap.

RUN:  python tools/skip_census.py            # census for this environment
      python tools/skip_census.py --json     # machine-readable
"""
from __future__ import annotations

import ast
import importlib.util
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TESTS = os.path.join(ROOT, "tests")


def _test_count(tree) -> int:
    """Every `def test_*` at module level or inside a Test class. Parametrisation multiplies this at
    runtime, so the real number is HIGHER -- this is the floor, which is the honest direction to err."""
    n = 0
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test_"):
            n += 1
    return n


def _module_level_guards(src: str, tree) -> list[str]:
    """Dependencies whose absence removes the WHOLE file. Only top-level statements count: an
    `importorskip` inside a function skips one test, which is visible and fine."""
    deps: list[str] = []
    for node in tree.body:
        seg = ast.get_source_segment(src, node) or ""
        if "importorskip" in seg:
            deps += re.findall(r"importorskip\(\s*[\"']([\w.]+)", seg)
        elif isinstance(node, ast.Try) and ("skip" in seg or "SkipTest" in seg):
            deps += re.findall(r"^\s*(?:import|from)\s+([\w.]+)", seg, re.M)
    return sorted({d.split(".")[0] for d in deps})


def census() -> dict:
    rows, missing_here = [], set()
    for name in sorted(os.listdir(TESTS)):
        if not (name.startswith("test_") and name.endswith(".py")):
            continue
        path = os.path.join(TESTS, name)
        with open(path, encoding="utf-8") as fh:
            src = fh.read()
        try:
            tree = ast.parse(src)
        except SyntaxError:
            continue
        deps = _module_level_guards(src, tree)
        absent = [d for d in deps if importlib.util.find_spec(d) is None]
        rows.append({"module": name, "tests": _test_count(tree), "guards": deps, "absent_here": absent,
                     "runs_here": not absent})
        missing_here.update(absent)

    runs = sum(r["tests"] for r in rows if r["runs_here"])
    hidden = sum(r["tests"] for r in rows if not r["runs_here"])
    return {"total_tests": runs + hidden, "runs_here": runs, "hidden_here": hidden,
            "missing_packages": sorted(missing_here),
            "hidden_modules": [r for r in rows if not r["runs_here"]],
            "guarded_modules": [r for r in rows if r["guards"]]}


def main() -> int:
    c = census()
    if "--json" in sys.argv[1:]:
        print(json.dumps(c, indent=1))
        return 0

    print(f"tests defined (floor, before parametrisation): {c['total_tests']}")
    print(f"  run in THIS environment:                     {c['runs_here']}")
    print(f"  invisible here (whole module skipped):       {c['hidden_here']}")
    if c["hidden_modules"]:
        print("\nnot running here:")
        for r in sorted(c["hidden_modules"], key=lambda r: -r["tests"]):
            print(f"  {r['tests']:4d}  {r['module']:44s} needs {', '.join(r['absent_here'])}")
        print(f"\nclose the gap with:  pip install {' '.join(c['missing_packages'])}")
    else:
        print("\nevery guarded module's dependency is present: nothing is hidden here.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
