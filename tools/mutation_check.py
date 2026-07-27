"""Do these tests have teeth? Break the code on purpose and require them to notice.

A green suite proves nothing about the code; it proves the tests ran. This applies a small edit that a
reader would call a real defect, re-runs the tests, and requires at least one to go red. A mutation that
SURVIVES marks a test that asserts a spelling rather than a behaviour.

WHY THIS IS A FILE AND NOT A SNIPPET
------------------------------------
It existed as an ad-hoc snippet, and the snippet had a bug that inverted its own verdict: it ran pytest
with `-x -rf`. `-x` aborts at the first problem, and `-rf` prints only FAILED to the summary -- never
ERROR. So a mutant killed through a *fixture* (the common shape here, since a broken probe fails at
setup) produced a summary with no FAILED lines, and the harness reported `SURVIVES <<< NO TEETH` for a
mutant its tests had killed four times over.

That is the failure this repository keeps meeting: a check that cannot report the thing it looks for is
indistinguishable from a clean result. A verdict tool is the worst possible place to keep it, because
every downstream conclusion inherits the error silently. Hence: committed, and covered by
`tests/test_mutation_check_harness.py`, which mutates the harness itself.

USAGE
    python tools/mutation_check.py tools/mutations.json
    python tools/mutation_check.py --self-test

Exits non-zero if any mutation survives, so it can gate CI.
"""
from __future__ import annotations

import argparse
import io
import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _pytest(tests: list[str], env: dict) -> subprocess.CompletedProcess:
    # No `-x`: a mutant may break several tests, and stopping early hides which. `-rfE` reports BOTH
    # failures and errors -- an error is a kill, not a crash to be discounted.
    return subprocess.run(
        [sys.executable, "-m", "pytest", *tests, "-q", "--no-header", "--tb=no", "-rfE", "-p", "no:randomly"],
        cwd=ROOT, capture_output=True, text=True, timeout=1800, env=env)


def _killers(stdout: str) -> list[str]:
    """Which tests noticed. Setup errors count: a fixture that refuses to build IS the test failing."""
    out = []
    for line in stdout.splitlines():
        if line.startswith(("FAILED ", "ERROR ")):
            out.append(line.split()[1].split("::")[-1])
    return sorted(set(out))


def _dirty_tracked() -> set:
    """Tracked files git currently reports as modified."""
    r = subprocess.run(["git", "status", "--porcelain", "--untracked-files=no"],
                       cwd=ROOT, capture_output=True, text=True)
    return {ln[3:].strip().strip('"') for ln in r.stdout.splitlines() if ln.strip()}


def _restore(paths) -> list:
    """Undo tracked files THIS run dirtied. Never touches anything that was already modified."""
    restored = []
    for path in sorted(paths):
        r = subprocess.run(["git", "checkout", "--", path], cwd=ROOT, capture_output=True, text=True)
        if r.returncode == 0:
            restored.append(path)
    return restored


def run(mutations: list[dict], verbose: bool = True) -> int:
    env = {**os.environ, "PYTHONPATH": ROOT + os.pathsep + os.environ.get("PYTHONPATH", ""),
           "PYTHONIOENCODING": "utf-8"}
    survived, skipped = [], []
    # A mutant does not only change code -- the tests it runs execute PROBES, and probes write their
    # result files, which are TRACKED. Restoring only the mutated source left
    # `probes/echo_policy_panel_result.json` holding the mutant's output: safe = 0.00 echo-blocked /
    # 1.00 reaffirm-honored, the exact inverse of the number the shipped docstring publishes, plus three
    # "problems" declaring our own claim wrong. Sitting in the working tree, tracked, one `git add -A`
    # from being published as a receipt. Recording what was ALREADY dirty means a developer's own
    # in-progress edits are never reverted by this.
    dirty_before = _dirty_tracked()

    for mut in mutations:
        name, rel, old, new = mut["name"], mut["file"], mut["old"], mut["new"]
        tests = mut["tests"] if isinstance(mut["tests"], list) else [mut["tests"]]
        path = os.path.join(ROOT, rel)
        src = io.open(path, encoding="utf-8").read()

        # A target that is absent, or present more than once, would mutate nothing or the wrong thing --
        # and either way would report a false verdict rather than an error.
        n = src.count(old)
        if n != 1:
            skipped.append(f"{name}: target appears {n}x in {rel}, not uniquely")
            if verbose:
                print(f"  {name[:58]:58s} -> SKIPPED (target appears {n}x)")
            continue

        # Pre-flight: tests that are already red would make every mutant look killed.
        pre = _pytest(tests, env)
        if pre.returncode != 0:
            skipped.append(f"{name}: tests are not green before mutating")
            if verbose:
                print(f"  {name[:58]:58s} -> SKIPPED (not green before mutating)")
            continue

        try:
            io.open(path, "w", encoding="utf-8").write(src.replace(old, new, 1))
            killers = _killers(_pytest(tests, env).stdout)
        finally:
            io.open(path, "w", encoding="utf-8").write(src)

        if killers:
            if verbose:
                print(f"  {name[:58]:58s} -> killed by {', '.join(killers[:3])}"
                      f"{f' (+{len(killers) - 3})' if len(killers) > 3 else ''}")
        else:
            survived.append(name)
            if verbose:
                print(f"  {name[:58]:58s} -> SURVIVES <<< NO TEETH")

    collateral = _dirty_tracked() - dirty_before
    if collateral:
        restored = _restore(collateral)
        if verbose and restored:
            print(f"  restored {len(restored)} tracked file(s) the run dirtied: {', '.join(restored)}")

    if verbose:
        total = len(mutations)
        print(f"\n{total - len(survived) - len(skipped)}/{total} killed, "
              f"{len(survived)} survived, {len(skipped)} skipped")
        for s in skipped:
            print(f"  skipped: {s}")
        for s in survived:
            print(f"  SURVIVED: {s}")
    return 1 if survived else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("spec", nargs="?", default=os.path.join("tools", "mutations.json"),
                    help="JSON list of {name, file, old, new, tests}")
    args = ap.parse_args()

    path = args.spec if os.path.isabs(args.spec) else os.path.join(ROOT, args.spec)
    with open(path, encoding="utf-8") as fh:
        mutations = json.load(fh)
    if not mutations:
        print("the spec is empty: a run over zero mutations is a green result over nothing")
        return 1
    print(f"{len(mutations)} mutations from {os.path.relpath(path, ROOT)}\n")
    return run(mutations)


if __name__ == "__main__":
    raise SystemExit(main())
