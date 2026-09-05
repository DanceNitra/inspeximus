"""Which curated lists mention our competitors and not us?

WHY. Being findable by search is worth little when the query has ten results. The bigger channel is
the curated list: `awesome-mcp-servers` and its neighbours are how people actually discover an MCP
server, and a mention there is also the inbound link a crawler follows.

WHAT THIS MEASURES. For each list repository, whether it names inspeximus and whether it names each
competitor. A list that carries a competitor and not us is an addressable gap: somebody curates it,
it accepts pull requests, and we qualify on the same grounds they did.

CONTROLS, because a check that cannot see its target reports SAFE:
  * POSITIVE CONTROL ON THE READER. Every list must mention at least ONE of the competitors. A list
    whose README we failed to fetch, or fetched as a stub, would otherwise read as a clean "nobody
    is here" and be counted as no gap. Lists that name nobody are reported separately as UNREADABLE
    rather than silently folded into the result.
  * NEGATIVE CONTROL ON THE MATCHER. A nonsense token must appear in zero lists. If it appears
    anywhere the matcher is matching something other than what it is given.
  * SUBSTRING, NOT WORD. "mem0ai" contains "mem0" and both are the same project, which is what we
    want. But "letta" also appears inside unrelated words, so each name is matched with a boundary
    on the left to keep a coincidence from counting as a mention.

    python probes/which_lists_carry_our_competitors_and_not_us.py
"""
from __future__ import annotations

import base64
import io
import json
import os
import re
import subprocess
import sys
import time

sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace", line_buffering=True)

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "which_lists_carry_our_competitors_and_not_us.result.json")

US = "inspeximus"
COMPETITORS = ("mem0", "zep", "graphiti", "cognee", "letta", "claude-mem", "supermemory",
               "memvid", "basic-memory", "openmemory")
NONSENSE = "zzqqxx-not-a-project-4f9a"

# The queries that find curated lists. Kept broad on purpose: the gap is wherever a curator lists
# this category, not only in the one list we already know about.
QUERIES = (
    "awesome mcp servers", "awesome mcp", "mcp servers list", "awesome ai agents",
    "awesome llm memory", "awesome agent memory", "awesome claude", "mcp directory",
)


def refuse(why):
    print("REFUSED: " + why)
    json.dump({"verdict": "REFUSED", "why": why},
              io.open(OUT, "w", encoding="utf-8", newline="\n"), indent=1)
    raise SystemExit(2)


def gh(path):
    p = subprocess.run(["gh", "api", path], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=90)
    if p.returncode:
        return None
    return json.loads(p.stdout)


def mentions(text, name):
    return re.search(r"(?<![A-Za-z0-9])" + re.escape(name), text) is not None


def main():
    seen, repos = set(), []
    for q in QUERIES:
        d = gh("search/repositories?q=%s&sort=stars&order=desc&per_page=30"
               % q.replace(" ", "+"))
        if d is None:
            refuse("gh api search failed; without it every 'not listed' below is unmeasured")
        for it in d.get("items", []):
            if it["full_name"] not in seen and it["stargazers_count"] >= 100:
                seen.add(it["full_name"])
                repos.append((it["full_name"], it["stargazers_count"]))
        time.sleep(2)
    if len(repos) < 20:
        refuse("only %d candidate lists found; the search is not working and a short list would "
               "understate the gap" % len(repos))

    rows, unreadable = [], []
    for full, stars in sorted(repos, key=lambda r: -r[1]):
        d = gh("repos/%s/readme" % full)
        if not d or not d.get("content"):
            unreadable.append({"repo": full, "stars": stars, "why": "no readme via the API"})
            continue
        try:
            text = base64.b64decode(d["content"]).decode("utf-8", "replace").lower()
        except Exception:
            unreadable.append({"repo": full, "stars": stars, "why": "readme did not decode"})
            continue
        found = [c for c in COMPETITORS if mentions(text, c)]
        # POSITIVE CONTROL: a list naming nobody tells us nothing about our absence from it.
        if not found:
            unreadable.append({"repo": full, "stars": stars,
                               "why": "names no competitor either, so our absence is uninformative"})
            continue
        if mentions(text, NONSENSE):
            refuse("the nonsense token matched in %s, so the matcher is not matching its input" % full)
        rows.append({"repo": full, "stars": stars, "has_us": mentions(text, US),
                     "competitors": found, "n_competitors": len(found),
                     "url": "https://github.com/" + full})
        time.sleep(0.7)

    gaps = [r for r in rows if not r["has_us"]]
    gaps.sort(key=lambda r: (-r["n_competitors"], -r["stars"]))
    res = {
        "verdict": "GAPS_FOUND" if gaps else "WE_ARE_ON_EVERY_READABLE_LIST",
        "lists_examined": len(rows),
        "lists_naming_us": len(rows) - len(gaps),
        "lists_naming_a_competitor_but_not_us": len(gaps),
        "unreadable_or_uninformative": unreadable,
        "gaps": gaps,
        "lists_naming_us_detail": [r for r in rows if r["has_us"]],
    }
    json.dump(res, io.open(OUT, "w", encoding="utf-8", newline="\n"), indent=1, ensure_ascii=False)

    print("  %-52s %7s %5s  %s" % ("list", "stars", "us?", "competitors on it"))
    for r in gaps[:30]:
        print("  %-52s %7d   -    %s" % (r["repo"][:52], r["stars"], ", ".join(r["competitors"])))
    print()
    print("  examined %d readable lists; %d already name us; %d name a competitor and not us"
          % (len(rows), res["lists_naming_us"], len(gaps)))
    if res["lists_naming_us_detail"]:
        print("  already listing us: %s"
              % ", ".join(r["repo"] for r in res["lists_naming_us_detail"]))
    print("  skipped as uninformative (named nobody, or no readable README): %d" % len(unreadable))
    print("  verdict: %s" % res["verdict"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
