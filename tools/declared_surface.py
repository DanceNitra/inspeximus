"""The topics and description are a product decision, so they live in the repository, not only on GitHub.

WHY THIS EXISTS. On 2026-09-05 six topics were replaced after measuring which ones a six-star
repository can actually appear on. On 2026-09-07 the live set had `python` back, at 865,124
repositories, and `witness` gone, and three capabilities had fallen out of the top 300 of the search
that finds us. Nobody noticed for two days, and nothing could have: the intended set existed only in
a commit message and in whoever remembered it.

`discovery_floor.py` measures the OUTCOME and fires after the loss. This declares the INPUT, so a
change to the storefront is a diff someone approves rather than an edit that happens to a live page.

    python tools/declared_surface.py            # compare the live repository against the declaration
    python tools/declared_surface.py --adopt    # write the live state into the declaration
    python tools/declared_surface.py --push     # make the live repository match the declaration

WHY EACH TOPIC IS HERE is recorded beside it, because the reason is the part that gets lost. A topic
carrying hundreds of thousands of repositories is a slot spent on a page we cannot appear on, and
that is the judgement this file exists to keep.
"""
from __future__ import annotations

import argparse
import io
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
DECL = os.path.join(HERE, "declared_surface.json")
REPO = "DanceNitra/inspeximus"


def gh(args: list) -> dict:
    out = subprocess.run(["gh"] + args, capture_output=True, text=True)
    if out.returncode != 0:
        print("REFUSED: gh failed: %s" % (out.stderr or "")[:200])
        raise SystemExit(2)
    return json.loads(out.stdout or "{}")


def live() -> dict:
    d = gh(["api", "repos/" + REPO, "--jq", "{description: .description, topics: .topics}"])
    return {"description": d.get("description") or "", "topics": sorted(d.get("topics") or [])}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--adopt", action="store_true", help="write the live state into the declaration")
    ap.add_argument("--push", action="store_true", help="make the live repository match the file")
    a = ap.parse_args(argv)

    now = live()
    if a.adopt:
        decl = json.loads(io.open(DECL, encoding="utf-8").read()) if os.path.exists(DECL) else {}
        decl["description"] = now["description"]
        decl["topics"] = now["topics"]
        io.open(DECL, "w", encoding="utf-8", newline="\n").write(
            json.dumps(decl, indent=1, ensure_ascii=False) + "\n")
        print("adopted the live state: %d topics, %d-char description"
              % (len(now["topics"]), len(now["description"])))
        return 0

    if not os.path.exists(DECL):
        print("REFUSED: %s does not exist. Run --adopt once to record the current storefront."
              % os.path.basename(DECL))
        return 2
    decl = json.loads(io.open(DECL, encoding="utf-8").read())
    want = {"description": decl["description"], "topics": sorted(decl["topics"])}

    if a.push:
        args = ["api", "-X", "PUT", "repos/%s/topics" % REPO]
        for t in want["topics"]:
            args += ["-f", "names[]=" + t]
        gh(args)
        gh(["api", "-X", "PATCH", "repos/" + REPO, "-f", "description=" + want["description"]])
        print("pushed: %d topics and the declared description" % len(want["topics"]))
        return 0

    gone = sorted(set(want["topics"]) - set(now["topics"]))
    extra = sorted(set(now["topics"]) - set(want["topics"]))
    desc_ok = now["description"] == want["description"]
    print("  topics declared : %d" % len(want["topics"]))
    print("  topics live     : %d" % len(now["topics"]))
    if gone:
        print("  MISSING from the live repository: %s" % ", ".join(gone))
    if extra:
        print("  NOT DECLARED, live anyway      : %s" % ", ".join(extra))
    if not desc_ok:
        print("  DESCRIPTION differs:")
        print("    declared: %s" % want["description"])
        print("    live    : %s" % now["description"])

    if gone or extra or not desc_ok:
        print("\n  The storefront does not match what this repository declares. Either the change was"
              "\n  meant, and `--adopt` records it with a reason, or it was not, and `--push` undoes"
              "\n  it. A silent edit is the case this check exists for.")
        return 1
    print("\n  OK: the live storefront matches the declaration.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
