"""Run tools/mutation_check.py across N isolated worktrees at once.

WHY THIS EXISTS. The serial gate runs pytest TWICE per mutation (a pre-flight green check, then the
mutant), so 169 mutations are 338 pytest invocations on one core. Measured 2026-08-10 on a 12-core /
24-thread machine: ~4.6 mutations/min, ~37 minutes wall-clock, with the CPU at 14%. The work is
embarrassingly parallel and was not being parallelised.

WHAT IT DOES NOT DO: it does not re-implement the gate. The serial logic is delicate -- byte-exact
reads, CRLF matching, per-mutation artifact restore, "a skip is not a pass" -- and every one of those
rules is there because it was once wrong. So this SHARDS the audited tool rather than replacing it:
each worker runs the real `mutation_check.py` over its own slice, in its own git worktree, with its
own HOME. The verdict semantics are inherited, not rewritten.

ISOLATION, and why each piece is needed:
  * a git worktree per worker -- mutations edit files IN PLACE and restore them; two workers in one
    tree would mutate each other's code and report nonsense.
  * a private HOME/USERPROFILE per worker -- some tests resolve `~` (e.g. `~/.inspeximus_tilde_probe.json`).
    Shared, two workers race on one file and produce a flaky red that looks like a killed mutant.
  * round-robin splitting, not contiguous chunks -- test subsets differ in cost by an order of
    magnitude, and contiguous slices leave workers idle at the end.

THE GUARD THAT MATTERS. A sharded run can go green by not running things: a worker that dies takes its
slice with it, and the surviving workers still report "0 survived". So this RECONCILES -- every
mutation named in the spec must be accounted for in exactly one worker's output, and a mismatch is a
hard failure with the missing names printed. A parallel gate without that reconciliation is the
repository's oldest bug wearing a new hat.

USAGE
    python tools/mutation_check_parallel.py                        # tools/mutations.json, auto workers
    python tools/mutation_check_parallel.py --workers 8
    python tools/mutation_check_parallel.py --keep                 # leave worktrees for inspection

Exits non-zero if any mutation survives, is skipped, or goes unaccounted for.
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SUMMARY_RE = re.compile(r"^(\d+)/(\d+) killed, (\d+) survived, (\d+) skipped\s*$")

# Git serialises itself with `.git/index.lock` and FAILS rather than waits. Eight threads calling
# `worktree add` at once therefore lose several worktrees to "Unable to create index.lock: File
# exists" -- and a worker with no worktree contributes no verdicts, which is exactly the silent
# under-run the reconciliation guard exists to catch. Cheaper to not create it: one lock, held only
# for the repo-level git calls. The pytest runs themselves stay fully parallel.
_GIT_LOCK = threading.Lock()


def _git(*args, cwd=ROOT):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)


def _dirty_tracked() -> list[str]:
    r = _git("status", "--porcelain", "--untracked-files=no")
    return [ln[3:].strip() for ln in r.stdout.splitlines() if ln.strip()]


def _parse(stdout: str) -> dict:
    """Read a worker's verdicts. Parses the FULL-NAME summary lines, not the truncated progress lines.

    The progress line clips the name to 58 chars; the tail summary does not. Parsing the clipped form
    would silently merge two mutations whose names share a prefix -- which is how a reconciliation
    check ends up confirming a count it derived from the same mistake.
    """
    killed_names, survived, skipped, totals = [], [], [], None
    for line in stdout.splitlines():
        s = line.strip()
        m = SUMMARY_RE.match(s)
        if m:
            totals = tuple(int(g) for g in m.groups())
            continue
        if s.startswith("SURVIVED: "):
            survived.append(s[len("SURVIVED: "):])
        elif s.startswith("skipped: "):
            # KEEP THE REASON. This split the line on ":" and took the name alone, which threw away the
            # only part that decides what to do next: "target appears 0x" is a drifted spec, while
            # "tests are not green before mutating" is a RED SUITE hiding unevaluated mutations. On the
            # first real run the two skips were the second kind -- a failing test on the release commit
            # -- and the aggregator had discarded the sentence that said so.
            skipped.append(s[len("skipped: "):])
        elif " -> killed by " in line:
            killed_names.append(line.split(" -> killed by ")[0].strip())
    return {"totals": totals, "survived": survived, "skipped": skipped, "killed_clipped": killed_names}


def _run_worker(idx: int, mutations: list[dict], base: str, keep: bool) -> dict:
    wt = os.path.join(base, f"w{idx}")
    home = os.path.join(base, f"home{idx}")
    os.makedirs(home, exist_ok=True)
    with _GIT_LOCK:
        r = _git("worktree", "add", "--detach", "--force", wt, "HEAD")
    if r.returncode != 0:
        return {"idx": idx, "error": f"worktree add failed: {r.stderr.strip()[:300]}",
                "expected": [m["name"] for m in mutations]}

    spec = os.path.join(wt, "tools", "_mut_shard.json")
    with open(spec, "w", encoding="utf-8") as fh:
        json.dump(mutations, fh)

    env = {**os.environ,
           "HOME": home, "USERPROFILE": home,
           "PYTHONPATH": wt + os.pathsep + os.environ.get("PYTHONPATH", ""),
           "PYTHONIOENCODING": "utf-8", "PYTHONUNBUFFERED": "1"}
    t0 = time.time()
    p = subprocess.run([sys.executable, "-X", "utf8", os.path.join("tools", "mutation_check.py"),
                        os.path.join("tools", "_mut_shard.json")],
                       cwd=wt, capture_output=True, text=True, env=env, timeout=7200)
    out = _parse(p.stdout)
    out.update({"idx": idx, "rc": p.returncode, "secs": round(time.time() - t0, 1),
                "expected": [m["name"] for m in mutations],
                "stderr_tail": p.stderr.strip()[-400:], "stdout": p.stdout})
    if not keep:
        with _GIT_LOCK:
            _git("worktree", "remove", "--force", wt)
        shutil.rmtree(home, ignore_errors=True)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("spec", nargs="?", default=os.path.join("tools", "mutations.json"))
    ap.add_argument("--workers", type=int, default=0, help="0 = auto (cores - 4, capped at 10)")
    ap.add_argument("--keep", action="store_true", help="keep worktrees after the run")
    ap.add_argument("--allow-dirty", action="store_true",
                    help="proceed even though workers will test HEAD, not the dirty working tree")
    args = ap.parse_args()

    path = args.spec if os.path.isabs(args.spec) else os.path.join(ROOT, args.spec)
    with open(path, encoding="utf-8") as fh:
        mutations = json.load(fh)
    if not mutations:
        print("the spec is empty: a run over zero mutations is a green result over nothing")
        return 1

    # WORKERS TEST **HEAD**, because a worktree is a checkout of a commit. If the working tree differs,
    # this gate measures code that is not the code in front of you -- the exact mistake CLAUDE.md logs
    # as "measure the code that RUNS". Refuse rather than quietly test something else.
    dirty = _dirty_tracked()
    if dirty:
        print(f"!! {len(dirty)} tracked file(s) differ from HEAD; workers would test HEAD, not these:")
        for d in dirty[:20]:
            print(f"     {d}")
        # NAME THE COMMON CAUSE instead of leaving the next person to work it out. release_check
        # rewrites probe receipts in THIS tree while it runs. The two gates are safe to execute at the
        # same time -- workers each get their own worktree -- but this check reads the MAIN tree, so a
        # concurrent release run makes it refuse over files that are transient rather than wrong.
        # Measured 2026-08-10: exactly that, and --allow-dirty would have been the wrong answer,
        # because the receipts were mid-rewrite and HEAD was one commit behind the tree anyway.
        if any(d.replace("\\", "/").startswith("probes/") and d.endswith(".json") for d in dirty):
            print("   NOTE: probe receipts are dirty. If tools/release_check.py is running right now,")
            print("         that is the cause -- wait for it to finish and re-run. Do NOT pass")
            print("         --allow-dirty: a receipt caught mid-rewrite is not a transient difference.")
        if not args.allow_dirty:
            print("   commit them, or pass --allow-dirty if you know the difference is transient.")
            return 2
        print("   --allow-dirty given: proceeding against HEAD anyway.\n")

    n = args.workers or max(2, min(10, (os.cpu_count() or 4) - 4))
    n = min(n, len(mutations))
    shards = [mutations[i::n] for i in range(n)]                  # round-robin: balances uneven costs
    base = os.path.join(ROOT, ".mutwt")
    os.makedirs(base, exist_ok=True)

    head = _git("rev-parse", "--short", "HEAD").stdout.strip()
    print(f"{len(mutations)} mutations from {os.path.relpath(path, ROOT)} across {n} worker(s) at {head}")
    print(f"  shard sizes: {[len(s) for s in shards]}\n")

    t0 = time.time()
    results = []
    with cf.ThreadPoolExecutor(max_workers=n) as pool:
        futs = {pool.submit(_run_worker, i, shards[i], base, args.keep): i for i in range(n)}
        for fut in cf.as_completed(futs):
            i = futs[fut]
            try:
                r = fut.result()
            except Exception as exc:                              # a crashed worker must not vanish
                r = {"idx": i, "error": f"{type(exc).__name__}: {exc}",
                     "expected": [m["name"] for m in shards[i]]}
            results.append(r)
            if "error" in r:
                print(f"  worker {i}: ERROR -- {r['error']}")
            else:
                t = r["totals"] or (0, 0, 0, 0)
                print(f"  worker {i}: {t[0]}/{t[1]} killed, {t[2]} survived, {t[3]} skipped "
                      f"({r['secs']}s, rc={r['rc']})")
    wall = time.time() - t0

    survived = sorted({s for r in results for s in r.get("survived", [])})
    skipped = sorted({s for r in results for s in r.get("skipped", [])})
    # RECONCILE. Every mutation in the spec must have been evaluated by exactly one worker. A worker
    # that died reports nothing, and "0 survived" over a slice that never ran is a green result over
    # nothing -- the failure this whole file exists to make impossible.
    accounted = 0
    lost = []
    for r in results:
        if "error" in r or r.get("totals") is None:
            lost.extend(r.get("expected", []))
            continue
        k, tot, sv, sk = r["totals"]
        if tot != len(r["expected"]) or k + sv + sk != tot:
            lost.extend(r["expected"])
            continue
        accounted += tot
    print(f"\nwall clock {wall:.1f}s ({wall/60:.1f} min)")
    print(f"{accounted - len(survived) - len(skipped)}/{len(mutations)} killed, "
          f"{len(survived)} survived, {len(skipped)} skipped, {len(lost)} UNACCOUNTED")
    for s in skipped:
        print(f"  skipped: {s}")
    for s in survived:
        print(f"  SURVIVED: {s}")
    for s in sorted(set(lost))[:40]:
        print(f"  UNACCOUNTED: {s}")
    if accounted != len(mutations) or lost:
        print("\n!! the shards do not add up -- a worker's slice was not evaluated. NOT a pass.")
        for r in results:
            if "error" in r or r.get("totals") is None:
                print(f"   worker {r['idx']} stderr: {r.get('stderr_tail', '')[:300]}")
        return 1
    return 1 if (survived or skipped) else 0


if __name__ == "__main__":
    raise SystemExit(main())
