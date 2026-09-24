"""Time from a clean environment to a passing `inspeximus demo`, with a budget that fails the run.

A new user's first minute: `pip install "inspeximus[crypto]"`, then `inspeximus demo`. The demo signs
its stores, so it needs the `crypto` extra; without it, it says so and exits 2. This creates a fresh
virtual environment, installs the package with that extra (this checkout by default, or a release from
PyPI), runs the demo with `--json`, and reports the seconds of each step. It exits 1 when the demo does
not pass all three checks, or when install plus demo take longer than the budget (60 s by default).
Creating the venv is timed and reported, but not charged to the budget: it is the machine's cost, not
ours.

    python tools/time_to_demo.py [--source .|pypi] [--version X.Y.Z] [--budget 60] [--out result.json]
    python tools/time_to_demo.py --summary result.json     (a Markdown table of an earlier result)
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def verdict(install_s: float, demo_s: float, demo_ok: bool, budget: float) -> tuple[bool, list[str]]:
    """Pass only if the demo passed and install plus demo fit the budget. Pure, so it is testable."""
    problems = []
    if not demo_ok:
        problems.append("the demo did not pass all three checks")
    total = install_s + demo_s
    if total > budget:
        problems.append("install plus demo took %.1f s, over the %.0f s budget" % (total, budget))
    return (not problems), problems


def summary(path: str) -> str:
    """A Markdown table of a result file, for a CI job summary."""
    try:
        with open(path, encoding="utf-8") as fh:
            d = json.load(fh)
    except (OSError, ValueError):
        return "time to first success: no result file (%s); the run failed before it wrote one\n" % path
    lines = ["| step | seconds |", "|---|---|"]
    lines += ["| %s | %s |" % (k, v) for k, v in d["seconds"].items()]
    lines.append("")
    lines.append("budget %.0f s: %s%s" % (d["budget_s"], "PASS" if d["ok"] else "FAIL",
                                           ("; " + "; ".join(d["problems"])) if d["problems"] else ""))
    return "\n".join(lines) + "\n"


def _timed(cmd, cwd):
    t = time.perf_counter()
    p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    return p, round(time.perf_counter() - t, 2)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--source", choices=[".", "pypi"], default=".",
                    help="install this checkout (default) or a release from PyPI")
    ap.add_argument("--version", default=None, help="with --source pypi, the release to install")
    ap.add_argument("--budget", type=float, default=60.0, help="seconds for install plus demo")
    ap.add_argument("--out", default=None, help="write the result as JSON here")
    ap.add_argument("--summary", default=None, metavar="RESULT_JSON",
                    help="print a Markdown table of an earlier result and exit")
    a = ap.parse_args(argv)
    if a.summary:
        print(summary(a.summary), end="")
        return 0

    work = tempfile.mkdtemp(prefix="time-to-demo-")
    try:
        venv = os.path.join(work, "venv")
        bindir = os.path.join(venv, "Scripts" if os.name == "nt" else "bin")
        py = os.path.join(bindir, "python")
        p, venv_s = _timed([sys.executable, "-m", "venv", venv], work)
        if p.returncode != 0:
            print("could not create a venv: " + p.stderr[-400:], file=sys.stderr)
            return 2
        target = (ROOT + "[crypto]" if a.source == "." else
                  ("inspeximus[crypto]==" + a.version if a.version else "inspeximus[crypto]"))
        p, install_s = _timed([py, "-m", "pip", "install", "--no-cache-dir", "-q", target], work)
        if p.returncode != 0:
            print("pip install failed: " + p.stderr[-800:], file=sys.stderr)
            return 1
        exe = os.path.join(bindir, "inspeximus")
        p, demo_s = _timed([exe, "--json", "demo"], work)
        try:
            demo = json.loads(p.stdout)
        except ValueError:
            demo = {"ok": False, "raw": p.stdout[-800:] + p.stderr[-800:]}
        ok, problems = verdict(install_s, demo_s, p.returncode == 0 and bool(demo.get("ok")), a.budget)
        result = {"ok": ok, "problems": problems, "budget_s": a.budget,
                  "source": a.source if a.source == "." else target,
                  "seconds": {"create_venv": venv_s, "pip_install": install_s, "demo": demo_s,
                              "install_plus_demo": round(install_s + demo_s, 2)},
                  "demo_steps": [{"step": s.get("step"), "ok": s.get("ok")} for s in demo.get("steps", [])],
                  "python": sys.version.split()[0], "platform": sys.platform}
        print(json.dumps(result, indent=2))
        if a.out:
            with open(a.out, "w", encoding="utf-8") as fh:
                json.dump(result, fh, indent=2)
        return 0 if ok else 1
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
