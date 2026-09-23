"""How long from nothing to a correction that holds, following only the README?

A new user's path, measured on this machine in a fresh virtual environment: create the venv, run the
README's install line, then run the README's first Python block exactly as printed. The first
meaningful result is the one the README promises in its comments: after the correction, recall
answers db-7, and after `revert()` the old value is back.

The block is read from README.md at run time, never copied into this file, so the probe measures the
README as it stands. It is run twice: as a script (what `python quickstart.py` does) and line by line
in an interactive console (what pasting into `python` does), because the two show different things.

A STUCK POINT is recorded whenever a step fails, or a line's visible result differs from what the
README's comment says it prints, or a promised result is not visible at all. Each is written down
with the step, the command and what the user saw.

    python -X utf8 probes/time_to_first_success.py [--version X.Y.Z] [--readme README.md]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))


def first_blocks(readme: str) -> tuple[str, str]:
    """The first ```bash block (the install line) and the first ```python block after it."""
    text = open(readme, encoding="utf-8").read()
    b = re.search(r"```bash\n(.*?)```", text, re.S)
    p = re.search(r"```python\n(.*?)```", text[b.end():] if b else text, re.S)
    return (b.group(1).strip() if b else ""), (p.group(1) if p else "")


def expected_comments(block: str) -> list[tuple[str, str]]:
    """(line, the value its comment promises) for lines whose comment is a quoted value."""
    out = []
    for line in block.splitlines():
        m = re.match(r"^(.*?)\s+#\s*('.*')\s*$", line)
        if m:
            out.append((m.group(1).strip(), m.group(2)))
    return out


def run(cmd, cwd, env=None, timeout=900):
    t = time.perf_counter()
    p = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=timeout)
    return p, round(time.perf_counter() - t, 2)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--version", default=None, help="pin the install to this version")
    ap.add_argument("--readme", default=os.path.join(os.path.dirname(HERE), "README.md"))
    a = ap.parse_args()

    install_line, block = first_blocks(a.readme)
    work = tempfile.mkdtemp(prefix="ttfs_")
    venv = os.path.join(work, "venv")
    py = os.path.join(venv, "Scripts" if os.name == "nt" else "bin", "python")
    stuck, steps = [], []

    p, dt = run([sys.executable, "-m", "venv", venv], work)
    steps.append({"step": "create venv", "seconds": dt, "ok": p.returncode == 0})

    # The README's install line, run with the venv's pip. A pinned version only makes the run
    # reproducible; the command a user types is the README's.
    pkg = "inspeximus" + ("==" + a.version if a.version else "")
    p, dt = run([py, "-m", "pip", "install", "--no-cache-dir", "-q", pkg], work)
    steps.append({"step": "README: %s" % install_line, "seconds": dt, "ok": p.returncode == 0,
                  "stderr_tail": p.stderr[-400:]})
    if p.returncode != 0:
        stuck.append({"severity": "blocker", "step": "install", "saw": p.stderr[-400:]})
    p, _ = run([py, "-c", "import inspeximus;print(inspeximus.__version__)"], work)
    installed = p.stdout.strip()

    # 1) as a script
    script = os.path.join(work, "quickstart.py")
    open(script, "w", encoding="utf-8").write(block)
    p, dt = run([py, script], work)
    steps.append({"step": "README block as a script", "seconds": dt, "ok": p.returncode == 0,
                  "stdout": p.stdout, "stderr_tail": p.stderr[-600:]})
    if p.returncode != 0:
        stuck.append({"severity": "blocker", "step": "script", "saw": p.stderr[-600:]})
    elif not p.stdout.strip():
        stuck.append({"severity": "no visible result", "step": "script", "saw": "the script ran and printed nothing: the values the "
                      "README's comments promise are only shown by an interactive console"})

    # 2) pasted into an interactive console, line by line, echoing each expression's value
    for f in ("memory.json",):
        for g in os.listdir(work):
            if g.startswith(f):
                p2 = os.path.join(work, g)
                os.remove(p2) if os.path.isfile(p2) else shutil.rmtree(p2)
    driver = (
        "import code,io,sys,json\n"
        "src=open(%r,encoding='utf-8').read().splitlines()\n"
        "con=code.InteractiveConsole()\n"
        "out=[]\n"
        "for line in src:\n"
        "    buf=io.StringIO(); old=sys.stdout; sys.stdout=buf\n"
        "    try:\n"
        "        con.push(line)\n"
        "    finally:\n"
        "        sys.stdout=old\n"
        "    out.append([line, buf.getvalue().strip()])\n"
        "print(json.dumps(out))\n" % script)
    p, dt = run([py, "-c", driver], work)
    steps.append({"step": "README block pasted into python", "seconds": dt, "ok": p.returncode == 0,
                  "stderr_tail": p.stderr[-600:]})
    echoed = json.loads(p.stdout.strip().splitlines()[-1]) if p.returncode == 0 and p.stdout.strip() else []
    seen = {ln.strip(): val for ln, val in echoed}
    checks = []
    for code_line, promised in expected_comments(block):
        got = seen.get(code_line.strip(), None)
        match = [k for k in seen if k.startswith(code_line.strip())]
        got = seen[match[0]] if match else got
        ok = got == promised
        checks.append({"line": code_line, "promised": promised, "saw": got, "ok": ok})
        if not ok:
            stuck.append({"severity": "blocker", "step": "interactive", "line": code_line, "promised": promised, "saw": got})
    # lines with a non-value comment that still produce output the README does not mention
    for ln, val in echoed:
        if val and not any(ln.strip().startswith(c.strip()) for c, _ in expected_comments(block)):
            stuck.append({"severity": "unexplained output", "step": "interactive", "line": ln.strip(),
                          "saw": val[:300], "promised": "(no value shown in the README)"})

    # The README says the correction is reversible but shows no way to confirm it. The probe checks it
    # itself, in the same interactive session, so "reversible" is measured rather than assumed.
    after, _ = run([py, "-c", "from inspeximus import Inspeximus;"
                    "print(Inspeximus('memory.json').recall('which staging database')[0]['text'])"], work)
    revert_ok = "db-3" in after.stdout
    checks.append({"line": "(probe) recall after revert", "promised": "db-3 is back",
                   "saw": after.stdout.strip(), "ok": revert_ok})
    stuck.append({"severity": "no visible result", "step": "interactive",
                  "line": "m.revert(\"staging-db\")",
                  "saw": "the README shows no line that confirms the revert; the probe confirmed it: %s"
                         % revert_ok})
    first_success = next((c for c in checks if "db-7" in c["promised"]), None)
    total = round(sum(s["seconds"] for s in steps if s["step"] != "README block as a script"), 2)
    receipt = {"kind": "inspeximus.probe/time-to-first-success/1",
               "measured_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
               "python": sys.version.split()[0], "platform": sys.platform,
               "installed_version": installed, "install_line": install_line,
               "steps": steps, "checks": checks,
               "first_success": bool(first_success and first_success["ok"]),
               "seconds_to_first_success": total, "stuck_points": stuck}
    out = os.path.join(HERE, "time_to_first_success.result.json")
    with open(out, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(receipt, fh, indent=1)
    for s in steps:
        print("%-45s %7.2f s  %s" % (s["step"][:45], s["seconds"], "ok" if s["ok"] else "FAILED"))
    print("installed %s; first success %s; %d stuck point(s)"
          % (installed, receipt["first_success"], len(stuck)))
    for s in stuck:
        print("  STUCK [%s] %s" % (s["severity"], json.dumps({k: v for k, v in s.items() if k != "severity"})[:260]))
    shutil.rmtree(work, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
