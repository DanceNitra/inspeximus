"""The local full run of the suite, in two steps, and a summary that must add up (3.22).

WHY TWO STEPS. tests/test_probes_cited_by_docs.py runs every cited probe as a subprocess with a wall-clock budget, so
its module puts all of them in one xdist group, on one worker, one at a time. In a single `pytest tests -n 16` that
group is the tail of the run: measured 2026-10-20 on PC1, the suite stood at 99 % for over 10 minutes while one
worker ran probes and the other 15 sat at 0 % CPU, still holding about 15 GB between them until the session ended.
So step 1 runs everything else at -n N, and step 2 runs the cited probes on N workers too, with `--dist load`, which
ignores the group. A probe over its budget there may be the load and not the probe, so the probes that fail in step 2
run once more, serially, and only their second result counts (the owner, 2026-10-20: "rozlozit na viacej workerov").

    python tools/full_suite.py                  # N from free memory, see workers()
    python tools/full_suite.py --workers 8      # N given
    FULL_SUITE_YIELD_TO=a.exe,b.exe python tools/full_suite.py
                                                # 8 workers at most while a process named here runs

THE GUARD. A run that splits the suite can go green by running less of it. Both steps write a JUnit report, and the
run is green only when both steps are green AND their test counts add up to what `pytest --collect-only tests`
collects. Every step passes -rfEs, so a failure, an error and a skip are named in the output, never inferred from
dots. tests/test_full_suite_covers_every_test.py checks that the two steps collect exactly the node ids of the plain
collection.

It is a local tool. CI shards the suite on its own runners (tools/shard_total.py) and does not use it."""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
#: The files step 1 leaves to step 2. A file added here leaves step 1 and runs serially: the test of this tool
#: fails when this tuple changes, so the change is deliberate.
SERIAL = ("tests/test_probes_cited_by_docs.py",)
#: Below this much free memory no step starts; it waits (EM, 2026-10-20).
START_GB = 10.0
COMMON = ["-p", "no:cacheprovider", "-rfEs"]


def free_gb() -> float:
    """Available physical memory in GB."""
    if os.name == "nt":
        import ctypes

        class MS(ctypes.Structure):
            _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                        ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                        ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                        ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                        ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
        ms = MS()
        ms.dwLength = ctypes.sizeof(MS)
        ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(ms))
        return ms.ullAvailPhys / 2 ** 30
    with open("/proc/meminfo", encoding="ascii") as fh:
        for line in fh:
            if line.startswith("MemAvailable:"):
                return int(line.split()[1]) / 2 ** 20
    return 0.0


def _running(names) -> list:
    """Which of the process image names `names` run now (case-insensitive)."""
    if not names:
        return []
    try:
        if os.name == "nt":
            out = subprocess.run(["tasklist", "/fo", "csv", "/nh"], capture_output=True, text=True,
                                 errors="replace").stdout
            images = {ln.split('","')[0].strip('"').lower() for ln in out.splitlines() if ln}
        else:
            out = subprocess.run(["ps", "-eo", "comm="], capture_output=True, text=True).stdout
            images = {os.path.basename(ln.strip()).lower() for ln in out.splitlines()}
    except OSError:
        return []
    return sorted(n for n in names if n.lower() in images)


def workers(free, yielding) -> int:
    """16 workers, or 8 at most while a process the machine must yield to runs (FULL_SUITE_YIELD_TO)."""
    return 8 if yielding else 16


def wait_for_memory() -> float:
    while True:
        g = free_gb()
        if g > START_GB:
            return g
        print("[full_suite] waiting: %.1f GB free, a step starts above %.0f GB" % (g, START_GB), flush=True)
        time.sleep(30)


def steps(n) -> list:
    """The two pytest argument lists, without the JUnit path."""
    ignore = ["--ignore=" + p for p in SERIAL]
    return [["tests"] + ignore + ["-n", str(n)] + COMMON,
            list(SERIAL) + ["-n", str(n), "--dist", "load", "--timeout", "300"] + COMMON]


def retry_args(nodeids) -> list:
    """The serial rerun of the step 2 node ids that failed under load."""
    return list(nodeids) + ["-n", "0", "--timeout", "300"] + COMMON


def collected(args) -> list:
    """The node ids pytest collects for `args` (with -n 0, so collection is in this process)."""
    out = subprocess.run([sys.executable, "-m", "pytest", "--collect-only", "-q", "-n", "0", "-p", "no:cacheprovider"]
                         + args, cwd=ROOT, capture_output=True, text=True, errors="replace").stdout
    return [ln.strip() for ln in out.splitlines() if "::" in ln and not ln.startswith(("=", " "))]


def junit_failed(path) -> list:
    """The node ids of the failed or errored cases in a JUnit report, as pytest names them."""
    out = []
    for case in ET.parse(path).getroot().iter("testcase"):
        if case.find("failure") is not None or case.find("error") is not None:
            mod = case.get("classname", "").replace(".", "/")
            out.append("%s.py::%s" % (mod, case.get("name")))
    return out


def junit_counts(path) -> dict:
    """tests, failures, errors and skipped summed over every testsuite in a JUnit report."""
    root = ET.parse(path).getroot()
    suites = [root] if root.tag == "testsuite" else list(root.iter("testsuite"))
    return {k: sum(int(s.get(k, 0)) for s in suites) for k in ("tests", "failures", "errors", "skipped")}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--workers", type=int, default=0, help="step 1 workers; 0 = from free memory and FULL_SUITE_YIELD_TO")
    args = ap.parse_args(argv)
    total = len(collected(["tests"]))
    yielding = _running([n for n in os.environ.get("FULL_SUITE_YIELD_TO", "").split(",") if n.strip()])
    out_dir = tempfile.mkdtemp(prefix="full-suite-")
    results = []
    for i, step in enumerate(steps(1), 1):
        g = wait_for_memory()
        n = args.workers or workers(g, yielding)
        step = steps(n)[i - 1]
        xml = os.path.join(out_dir, "step%d.xml" % i)
        print("[full_suite] step %d: pytest %s  (%.1f GB free%s)" % (
            i, " ".join(step), g, ", yielding to " + ", ".join(yielding) if yielding else ""), flush=True)
        t0 = time.time()
        rc = subprocess.run([sys.executable, "-m", "pytest"] + step + ["--junitxml", xml], cwd=ROOT).returncode
        c = junit_counts(xml) if os.path.exists(xml) else None
        if i == 2 and c and (c["failures"] or c["errors"]):
            failed = junit_failed(xml)
            rxml = os.path.join(out_dir, "step2_retry.xml")
            print("[full_suite] step 2: %d failed under load; running them once more, serially" % len(failed), flush=True)
            rc = subprocess.run([sys.executable, "-m", "pytest"] + retry_args(failed) + ["--junitxml", rxml],
                                cwd=ROOT).returncode
            r = junit_counts(rxml) if os.path.exists(rxml) else None
            if r is None or r["tests"] != len(failed):
                rc, c = rc or 1, dict(c, failures=c["failures"] + c["errors"], errors=0)   # the retry did not account
            else:
                c = dict(c, failures=r["failures"], errors=r["errors"])
        results.append((i, rc, c, time.time() - t0))
    ran = sum(c["tests"] for _, _, c, _ in results if c)
    for i, rc, c, secs in results:
        if c is None:
            print("[full_suite] step %d: exit %s, NO REPORT" % (i, rc))
        else:
            print("[full_suite] step %d: exit %s in %.0f s: %d tests, %d failed, %d errors, %d skipped"
                  % (i, rc, secs, c["tests"], c["failures"], c["errors"], c["skipped"]))
    green = all(rc == 0 and c and not c["failures"] and not c["errors"] for _, rc, c, _ in results)
    adds_up = ran == total
    print("[full_suite] %s: %d of %d collected tests ran in the two steps%s" % (
        "GREEN" if green and adds_up else "NOT GREEN", ran, total,
        "" if adds_up else " -- the steps do not add up to the collection"))
    return 0 if green and adds_up else 1


if __name__ == "__main__":
    raise SystemExit(main())
