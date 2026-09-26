"""Run exactly the commands docs/install/index.md gives, in a sandbox, then check one memory works.

    python tools/run_install_page.py <workdir> [--find-links DIR] [--constraint SPEC] [--keep-uv] [--expect-fail] [--answers yes|no]

The block after `<!-- ci: posix -->` runs under bash on Linux and macOS, the block after
`<!-- ci: windows -->` under PowerShell on Windows, with HOME pointing at a sandbox that holds every
host's config directory (tools/one_memory_check.py builds it). The page is not paraphrased: if a command on
it stops working, this fails. `--find-links` lets pip see a wheel built from this checkout before it is on
PyPI; `--answers` is what the user said to the page's two questions (default yes), and with no the
runner requires that no rules file was written; `--constraint` pins inspeximus for the control run (3.9.5 has no `install --all`, so it must fail).
Then the five criteria of tools/one_memory_check.py run against the configs the page produced.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import one_memory_check as om  # noqa: E402

PAGE = os.path.join(os.path.dirname(HERE), "docs", "install", "index.md")


#: The page's placeholders for the user's two answers. The agent replaces them, and so does this runner.
PLACEHOLDERS = ("RULES_ANSWER", "HERMES_ANSWER")


def page_block(kind, answer):
    text = open(PAGE, encoding="utf-8").read()
    m = re.search(r"<!-- ci: %s -->\s*```(?:bash|powershell)\n(.*?)```" % kind, text, re.S)
    if not m:
        raise SystemExit(f"docs/install/index.md has no block marked `<!-- ci: {kind} -->`")
    block = m.group(1)
    missing = [p for p in PLACEHOLDERS if p not in block]
    if missing:                                              # the answer must come from the user, not the page
        raise SystemExit(f"the {kind} block no longer carries {missing}: the user's answer would be ignored")
    for p in PLACEHOLDERS:
        block = block.replace(p, answer)
    return block


def main():
    work = os.path.abspath(sys.argv[1])
    args = sys.argv[2:]
    expect_fail = "--expect-fail" in args
    keep_uv = "--keep-uv" in args
    home, proj, env = om.sandbox(work, keep_uv=keep_uv)
    if "--find-links" in args:
        env["PIP_FIND_LINKS"] = os.path.abspath(args[args.index("--find-links") + 1])
        if keep_uv:                                          # uvx resolves the pinned version there too
            env["UV_FIND_LINKS"] = env["PIP_FIND_LINKS"]
    if "--constraint" in args:
        c = os.path.join(work, "constraints.txt")
        open(c, "w").write(args[args.index("--constraint") + 1] + "\n")
        env["PIP_CONSTRAINT"] = c
    answer = args[args.index("--answers") + 1] if "--answers" in args else "yes"
    win = os.name == "nt"
    block = page_block("windows" if win else "posix", answer)
    if win:
        cmd = ["pwsh", "-NoProfile", "-NonInteractive", "-Command", "$ErrorActionPreference='Stop'\n" + block]
    else:
        cmd = ["bash", "-e", "-c", block]
    r = subprocess.run(cmd, cwd=proj, env=env, capture_output=True, text=True, encoding="utf-8", errors="replace")
    print("page commands exit", r.returncode)
    print((r.stdout or "")[-3000:], (r.stderr or "")[-2000:])
    bindir = os.path.join(home, ".inspeximus", "venv", "Scripts" if win else "bin")
    json.dump(dict(env, _BINDIR=bindir), open(os.path.join(work, "env.json"), "w", encoding="utf-8"))
    launch_cwd = {h: os.path.join(proj, "src") for h in om.HOSTS}
    launch_cwd.update(cursor=home, windsurf=home, cline=home, antigravity=home, devin=home)
    if r.returncode != 0:
        res = {"page_commands": "failed with exit %d" % r.returncode}
    else:
        try:
            res = om.criteria(home, proj, env, launch_cwd)
        except Exception as ex:                              # noqa: BLE001
            res = {"crash": repr(ex)[:500]}
    if answer == "yes":
        verdict = r.returncode == 0 and all(res.get(c, {}).get("PASS") for c in ("C1", "C2", "C3", "C4", "C5"))
    else:
        # CONSENT: "no" must write no rules file anywhere, and the hosts that read server instructions
        # must still be told to recall. C1 fails here by design, because it requires the rules files.
        c1 = res.get("C1") or {}
        rules = c1.get("rules_file_written") or {}
        told = c1.get("instructions_tell_recall") or {}
        res["consent"] = {"PASS": bool(rules) and not any(rules.values()) and bool(told) and all(told.values()),
                          "rules_file_written": rules}
        verdict = r.returncode == 0 and res["consent"]["PASS"] and \
            all(res.get(c, {}).get("PASS") for c in ("C2", "C3", "C4", "C5"))
    res["PASS"] = verdict
    json.dump(res, open(os.path.join(work, "one_memory.json"), "w", encoding="utf-8"), indent=1)
    print(json.dumps(res, indent=1)[:6000])
    if expect_fail and verdict:
        print("CONTROL PASSED: the old release followed the page and gave every agent one memory")
    sys.exit(0 if verdict != expect_fail else 1)


if __name__ == "__main__":
    main()
