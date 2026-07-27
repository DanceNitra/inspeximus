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
import re
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


#: Only these may be restored. A probe writes its receipt; nothing else about a run is expected to touch
#: the working tree.
#:
#: The rule used to be the FILENAME shape -- `<something>_result.json` -- and that convention is not one
#: every probe follows. `probes/governance_sufficiency_bytes.json` is written by
#: `governance_sufficiency_probe.py`, did not match, and was therefore left dirty by every run; 45 lines of
#: it (random record ids) were committed as churn in ebabfa8. Worse, dirt that survives a run is dirt the
#: NEXT run records in `dirty_before` and so protects forever, and a receipt a mutant wrote then reads as
#: a developer's own edit. That is how a mutation came to be SKIPPED: `test_the_receipt_still_holds_the
#: _number_we_publish` read an inverted echo_policy receipt, the pre-flight was red, and the run reported
#: 74/75 with the 75th never evaluated.
#:
#: So the rule is now: a `.json` under `probes/` IS a receipt (checked -- all 19 committed ones are written
#: by a probe in that directory), and everything else, including the probe SOURCE that lives beside it, is
#: not. A convention that a fifth of the receipts do not follow is not a rule, it is a coin flip.
_ARTIFACT = re.compile(r"^probes/[\w.-]+\.json$")


def _restore(paths) -> list:
    """Undo the RESULT ARTIFACTS this run dirtied -- and nothing else.

    The first version restored every tracked file that became dirty during the run. That is wrong in a way
    that cost real work: a developer editing source WHILE the gate runs in the background looks identical
    to collateral, and `git checkout --` silently threw the edit away. It happened here, to a one-line fix
    in core.py, and the only reason it was caught was a measurement that stopped making sense.

    A tool whose job is to leave the repository as it found it must not be able to delete what it did not
    write. Restricted to probe result files, which is the only collateral a mutation run actually produces.
    """
    restored = []
    for path in sorted(paths):
        if not _ARTIFACT.match(path.replace("\\", "/")):
            continue
        r = subprocess.run(["git", "checkout", "--", path], cwd=ROOT, capture_output=True, text=True)
        if r.returncode == 0:
            restored.append(path)
    return restored


def run(mutations: list[dict], verbose: bool = True) -> int:
    env = {**os.environ, "PYTHONPATH": ROOT + os.pathsep + os.environ.get("PYTHONPATH", ""),
           "PYTHONIOENCODING": "utf-8"}
    survived, skipped, restored_all = [], [], set()
    # A mutant does not only change code -- the tests it runs execute PROBES, and probes write their
    # result files, which are TRACKED. Restoring only the mutated source left
    # `probes/echo_policy_panel_result.json` holding the mutant's output: safe = 0.00 echo-blocked /
    # 1.00 reaffirm-honored, the exact inverse of the number the shipped docstring publishes, plus three
    # "problems" declaring our own claim wrong. Sitting in the working tree, tracked, one `git add -A`
    # from being published as a receipt. Recording what was ALREADY dirty means a developer's own
    # in-progress edits are never reverted by this.
    dirty_before = _dirty_tracked()
    # Say what we INHERITED. A receipt left dirty by an earlier run is protected by `dirty_before` (it
    # looks exactly like a developer's edit), so it is read by every test in this run and never restored.
    # It cannot be reverted safely from here -- but it must not be silent.
    inherited = sorted(p for p in dirty_before if _ARTIFACT.match(p.replace("\\", "/")))
    if inherited and verbose:
        print(f"  NOTE: {len(inherited)} probe receipt(s) were ALREADY modified before this run and will "
              f"be read as-is: {', '.join(inherited)}\n")

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
            # RESTORE THE MUTANT'S ARTIFACTS NOW, NOT AT THE END OF THE RUN. The source was always put
            # back per mutation; its RECEIPTS were not -- collateral was collected once, after the whole
            # loop. So a mutant that flips the echo guard writes an INVERTED
            # probes/echo_policy_panel_result.json (safe = 0.00 echo-blocked where we publish 1.00) and
            # that falsified receipt then sits in the tree for every remaining mutation. The pre-flight
            # of a later one reads it, `test_the_receipt_still_holds_the_number_we_publish` goes red,
            # and the mutation is SKIPPED -- measured: 76/77 with the 77th never evaluated, twice.
            # The run's own collateral was changing what its later checks saw. Same window, per mutation.
            for _p in _restore(_dirty_tracked() - dirty_before):
                restored_all.add(_p)

        if killers:
            if verbose:
                print(f"  {name[:58]:58s} -> killed by {', '.join(killers[:3])}"
                      f"{f' (+{len(killers) - 3})' if len(killers) > 3 else ''}")
        else:
            survived.append(name)
            if verbose:
                print(f"  {name[:58]:58s} -> SURVIVES <<< NO TEETH")

    left = _dirty_tracked() - dirty_before
    for _p in _restore(left):                                 # anything a skip path left behind
        restored_all.add(_p)
    if verbose and restored_all:
        print(f"  restored {len(restored_all)} tracked file(s) the run dirtied: "
              f"{', '.join(sorted(restored_all))}")
    # NAME WHAT WE REFUSED TO RESTORE. The allowlist is deliberately narrow -- this tool must never be
    # able to delete work it did not write -- but silence about the remainder is how a mutant reached a
    # committed file today: a test ran the release pinner against the REAL repo, a mutant made that
    # pinner write a wrong field, and `.claude-plugin/marketplace.json` was left corrupted with nothing
    # in the output to say so. Restoring it automatically would be the worse bug; saying nothing was
    # the one we had. The test was fixed to run against a copy; this makes the next one visible.
    unrestored = sorted(p for p in left if p not in restored_all)
    if unrestored:
        print(f"  !! {len(unrestored)} tracked file(s) were dirtied by this run and are OUTSIDE the "
              f"restore allowlist -- check them by hand: {', '.join(unrestored)}")

    if verbose:
        total = len(mutations)
        print(f"\n{total - len(survived) - len(skipped)}/{total} killed, "
              f"{len(survived)} survived, {len(skipped)} skipped")
        for s in skipped:
            print(f"  skipped: {s}")
        for s in survived:
            print(f"  SURVIVED: {s}")
    # A SKIP IS NOT A PASS. This returned 0 whenever nothing survived, so a mutation whose target had
    # drifted, or whose tests were already red, was reported on one line and then counted as if it had been
    # evaluated -- and the process exit code, which is what CI reads, said everything was fine. Measured
    # today: `74/75 killed, 0 survived, 1 skipped` exited 0, and the 75th mutation was never run. That is
    # the shape this repository keeps finding -- a check that cannot report the thing it looks for is
    # indistinguishable from a clean result -- sitting in the tool whose whole job is to catch it.
    return 1 if (survived or skipped) else 0


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
