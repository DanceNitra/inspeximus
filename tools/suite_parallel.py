"""Run the test suite across N isolated worktrees at once, and refuse a run that does not add up.

WHY. `release_check` runs `pytest tests/ -q` in one process: measured today, 2,716 tests in 1,213 s
(20 min) on a 12-core machine. Every release pays it, and it is the long pole by an order of
magnitude.

WHY NOT pytest-xdist. Tried first, because it is the obvious answer. Measured on this suite with
`-n 6 --dist loadfile`: four errors, one failure, and a run that would not finish -- this suite has
module-scoped fixtures, tests that resolve `~`, and probes that write TRACKED result files into the
tree, so in-process sharding puts several workers on one filesystem and one home directory. Rather
than green it by force, this shards the way the mutation gate already does: one worktree per worker,
one private HOME per worker, whole FILES to a worker so module-scoped fixtures stay intact.

THE GUARD. A sharded suite goes green by not running things. So the shard totals are reconciled
against a `--collect-only` count, and a mismatch fails the run and prints the arithmetic. Without
that, a worker that dies quietly subtracts its tests from the denominator and the remaining ones
still report "all passed". It fired on the first real run: 2,255 accounted of 2,717.

The baseline is collected INSIDE a worktree at HEAD, not in the working tree, because those are two
different test sets. Measured while building this: the first version collected in the working tree
and the dirty-check used `--untracked-files=no`, so an UNTRACKED new file looked like a clean tree
while changing the denominator -- this file itself added one case to
`test_no_pep604_annotation_without_postponed_evaluation`, and the guard would have blamed a worker
for the difference.

WHAT IT IS NOT. A screen, not the authority. A private HOME is what makes the shards independent,
and it also breaks tests that need the real one: on this machine the crewai integrations raise
`pywintypes.com_error` under a synthetic HOME and pass serially. Treat a failure here as a candidate
to reproduce in the real tree, never as a verdict. `release_check` remains the gate.

USAGE
    python tools/suite_parallel.py                 # auto workers
    python tools/suite_parallel.py --workers 8
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import os
import re
import shutil
import subprocess
import sys
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_GIT_LOCK = threading.Lock()
COUNT_RE = re.compile(r"(\d+) (passed|failed|skipped|xfailed|xpassed|error(?:s)?)")


def _git(*args, cwd=ROOT):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)


def _counts(out: str) -> dict:
    """Totals from pytest's summary line. Reads the LAST line that carries them, because a suite
    prints intermediate summaries too and the first match is not the run's."""
    tally: dict = {}
    for line in out.strip().splitlines()[::-1]:
        found = COUNT_RE.findall(line)
        if found and ("passed" in line or "failed" in line or "error" in line):
            for n, kind in found:
                tally[kind.rstrip("s") if kind.startswith("error") else kind] = int(n)
            break
    return tally


def _worker(idx: int, files: list[str], base: str, keep: bool) -> dict:
    wt, home = os.path.join(base, f"s{idx}"), os.path.join(base, f"home{idx}")
    os.makedirs(home, exist_ok=True)
    with _GIT_LOCK:
        r = _git("worktree", "add", "--detach", "--force", wt, "HEAD")
    if r.returncode != 0:
        return {"idx": idx, "error": r.stderr.strip()[:300], "files": files}
    env = {**os.environ, "HOME": home, "USERPROFILE": home,
           "PYTHONPATH": wt + os.pathsep + os.environ.get("PYTHONPATH", ""),
           "PYTHONIOENCODING": "utf-8", "PYTHONUNBUFFERED": "1"}
    t0 = time.time()
    p = subprocess.run([sys.executable, "-X", "utf8", "-m", "pytest", *files,
                        # --continue-on-collection-errors, because ONE unimportable file killed a whole
                        # shard. Measured: worker 7 exited after 17.6s with 0 passed and 1 error, and
                        # its 432 tests were never run -- the reconciliation guard caught it as
                        # UNACCOUNTED, which is the guard working, but the right answer is to run the
                        # other 431 rather than to report the loss well. The error is still surfaced;
                        # it just no longer takes the shard down with it.
                        "-q", "--no-header", "--tb=short", "-rfE", "-p", "no:randomly",
                        "--continue-on-collection-errors"],
                       cwd=wt, capture_output=True, text=True, errors="replace", timeout=3600, env=env)
    out = p.stdout or ""
    fails = [ln for ln in out.splitlines() if ln.startswith(("FAILED ", "ERROR "))]
    res = {"idx": idx, "rc": p.returncode, "secs": round(time.time() - t0, 1),
           "counts": _counts(out), "fails": fails, "files": files,
           "tb": "\n".join(out.splitlines()[-60:]) if p.returncode != 0 else ""}
    if not keep:
        with _GIT_LOCK:
            _git("worktree", "remove", "--force", wt)
        shutil.rmtree(home, ignore_errors=True)
    return res


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--workers", type=int, default=0)
    ap.add_argument("--keep", action="store_true")
    args = ap.parse_args()

    # WHAT ACTUALLY CHANGES THE DENOMINATOR. `--untracked-files=no` calls a tree clean while a new
    # file sits in it, and a new SOURCE file can change the collected test set -- this tool did
    # exactly that, adding a case to test_no_pep604_annotation_without_postponed_evaluation. So
    # untracked files are not ignored. But refusing on ALL of them was too blunt to survive contact
    # with a real working tree: a first run was blocked by benchmark .json output and a NOTES.md,
    # neither of which can add a test, and a guard people route around with a flag is worse than none.
    # Modified tracked files and untracked *.py refuse; anything else is named and the run proceeds.
    dirty = [ln for ln in _git("status", "--porcelain").stdout.splitlines() if ln.strip()]
    blocking, benign = [], []
    for d in dirty:
        path = d[3:].strip().strip('"')
        (blocking if (not d.startswith("??") or path.endswith(".py")) else benign).append(d.strip())
    if benign:
        print("note: %d untracked non-source path(s) ignored (they cannot change the test set): %s"
              % (len(benign), ", ".join(b[3:] for b in benign[:6])))
    if blocking:
        print("!! the working tree differs from HEAD in ways that CAN change what is collected;")
        print("   workers test HEAD, not these:")
        for d in blocking[:20]:
            print("   ", d)
        return 2

    files = sorted(f"tests/{f}" for f in os.listdir(os.path.join(ROOT, "tests"))
                   if f.startswith("test_") and f.endswith(".py"))
    n = args.workers or max(2, min(10, (os.cpu_count() or 4) - 4))
    n = min(n, len(files))
    shards = [files[i::n] for i in range(n)]

    base_dir = os.path.join(ROOT, ".suitewt")
    os.makedirs(base_dir, exist_ok=True)
    print("collecting the baseline inside a worktree at HEAD (the denominator this run reconciles against)...")
    bwt = os.path.join(base_dir, "baseline")
    with _GIT_LOCK:
        br = _git("worktree", "add", "--detach", "--force", bwt, "HEAD")
    if br.returncode != 0:
        print(f"could not create the baseline worktree: {br.stderr.strip()[:200]}")
        return 2
    try:
        c = subprocess.run([sys.executable, "-X", "utf8", "-m", "pytest", "--collect-only", "-q",
                            "-p", "no:randomly"], cwd=bwt, capture_output=True, text=True, errors="replace")
        m = re.search(r"(\d+) tests collected", c.stdout or "")
        expected = int(m.group(1)) if m else 0
    finally:
        with _GIT_LOCK:
            _git("worktree", "remove", "--force", bwt)
    if not expected:
        print("could not read a collect-only baseline; refusing to report a total I cannot check")
        return 2
    print(f"{len(files)} test files, {expected} tests, {n} workers\n")

    t0 = time.time()
    results = []
    with cf.ThreadPoolExecutor(max_workers=n) as pool:
        futs = {pool.submit(_worker, i, shards[i], base_dir, args.keep): i
                for i in range(n)}
        for fut in cf.as_completed(futs):
            r = fut.result()
            results.append(r)
            if "error" in r:
                print(f"  worker {r['idx']}: ERROR {r['error']}")
            else:
                c_ = r["counts"]
                print(f"  worker {r['idx']}: {c_.get('passed', 0)} passed, {c_.get('failed', 0)} failed, "
                      f"{c_.get('error', 0)} error, {c_.get('skipped', 0)} skipped, "
                      f"{c_.get('xfailed', 0)} xfailed ({r['secs']}s)")
    wall = time.time() - t0

    tot = {}
    for r in results:
        for k, v in (r.get("counts") or {}).items():
            tot[k] = tot.get(k, 0) + v
    ran = sum(tot.get(k, 0) for k in ("passed", "failed", "skipped", "xfailed", "xpassed", "error"))
    fails = [f for r in results for f in r.get("fails", [])]

    print(f"\nwall clock {wall:.1f}s ({wall/60:.1f} min)")
    print(f"{tot.get('passed', 0)} passed, {tot.get('failed', 0)} failed, {tot.get('error', 0)} error, "
          f"{tot.get('skipped', 0)} skipped, {tot.get('xfailed', 0)} xfailed  "
          f"[{ran} accounted / {expected} collected]")
    for f in fails:
        print(f"  {f}")
    if ran != expected:
        print(f"\n!! {expected - ran} test(s) were never accounted for -- a shard did not run. NOT a pass.")
        for r in results:
            if "error" in r or not r.get("counts"):
                print(f"   worker {r['idx']} produced no totals; files: {', '.join(r['files'][:6])} ...")
        return 1
    if tot.get("failed") or tot.get("error"):
        for r in results:
            if r.get("tb"):
                print(f"\n----- worker {r['idx']} tail -----\n{r['tb']}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
