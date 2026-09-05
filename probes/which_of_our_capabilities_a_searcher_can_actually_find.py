"""Which of the things inspeximus does can somebody find it by?

WHY. Measured 2026-09-05, after the owner asked why the project is invisible for its own
capabilities. On Glama's catalogue of 81,811 servers we do not come back for "agent memory" or
"supersede", and on GitHub we are not in the result set at all for "erasure agent" -- a query with
32 matching repositories, against a project shipping four tools named `erasure_*`.

THE MECHANISM, established by control rather than assumed:

  * The repo DESCRIPTION is searched. "windsurf" and "cline" appear only there, and both queries
    return us (positions 89 and 50).
  * TOPICS are searched. "supersede" is a topic and "supersede memory" returns us at 41.
  * The README is NOT searched. "erasure" appears in it thirteen times and "erasure agent" does not
    return us anywhere in its 32 results.

So a capability documented only in the README is invisible to search. That is where eight of ours
lived.

WHAT THIS MEASURES. For every capability the code actually implements, whether the word appears in
each surface a searcher can reach, and then whether GitHub search really returns us for it. The
second half matters because the first is a proxy: a word can be present and still not rank.

CONTROLS, because a check that cannot see its target reports SAFE:
  * POSITIVE CONTROL ON THE SEARCH. Querying our own package name must return us. If it does not,
    the API is rate-limited or broken and every "not found" below is void, so the run refuses.
  * THE CAPABILITY LIST COMES FROM THE CODE, never from a hand-written list of things we like to
    say. It is derived from the MCP tool names, so a capability cannot be claimed here unless
    something implements it.
  * TOPICS ARE CAPPED AT 20 BY GITHUB, so this reports the budget as well as the gap. Adding a
    topic means removing one, and a report that ignored the cap would recommend the impossible.

    python probes/which_of_our_capabilities_a_searcher_can_actually_find.py
"""
from __future__ import annotations

import io
import json
import os
import re
import subprocess
import sys
import time
import urllib.parse
import urllib.request

sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace", line_buffering=True)

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
OUT = os.path.join(HERE, "which_of_our_capabilities_a_searcher_can_actually_find.result.json")
REPO = "DanceNitra/inspeximus"
TOPIC_CAP = 20          # GitHub's own limit, not ours

# The capability vocabulary, derived from the tool names the server actually exposes. The mapping
# from a tool-name fragment to the word a person would search is the only judgement here, and it is
# written down rather than implied.
CAPABILITY_OF = {
    "erasure": "erasure", "forget": "forget", "provenance": "provenance", "audit": "audit",
    "witness": "witness", "attest": "attestation", "retention": "retention",
    "compliance": "compliance", "consolidate": "consolidation", "revert": "revert",
    "conflict": "conflict", "supersession": "supersede", "recall": "recall",
    "grant": "access-control", "anchor": "anchor", "governance": "governance",
}


def refuse(why):
    print("REFUSED: " + why)
    json.dump({"verdict": "REFUSED", "why": why},
              io.open(OUT, "w", encoding="utf-8", newline="\n"), indent=1)
    raise SystemExit(2)


def gh(path):
    out = subprocess.run(["gh", "api", path], capture_output=True, text=True,
                         encoding="utf-8", errors="replace", timeout=90)
    if out.returncode:
        refuse("gh api %s failed: %s" % (path, (out.stderr or "")[:160]))
    return json.loads(out.stdout)


DEPTH = 3               # pages of 100. A miss below means "not in the top 300", never "absent".


def search_position(query, want=REPO, pages=DEPTH):
    """Where the repo lands in GitHub repository search, or None if it is not in the top `pages`*100.

    A None is a BOUNDED absence and is reported as such. GitHub's search API stops serving results
    after 1000, so no query here can prove a repository matches nothing at all.
    """
    q = urllib.parse.quote(query)
    total = 0
    for page in range(1, pages + 1):
        d = gh("search/repositories?q=%s&per_page=100&page=%d" % (q, page))
        total = d.get("total_count", total)
        names = [i["full_name"] for i in d.get("items", [])]
        if want in names:
            return (page - 1) * 100 + names.index(want), total
        if len(names) < 100:
            break
        time.sleep(2)
    return None, total


def main():
    src = io.open(os.path.join(ROOT, "inspeximus", "mcp_server.py"), encoding="utf-8").read()
    tools = re.findall(r"@mcp\.tool\([^)]*\)\s*(?:async\s+)?def\s+(\w+)", src)
    if len(tools) < 20:
        refuse("only %d tools found; the tool-name reader is broken and the capability list it "
               "derives would be a fiction" % len(tools))

    caps = {}
    for frag, word in CAPABILITY_OF.items():
        hits = [t for t in tools if frag in t]
        if hits:
            caps[word] = hits
    if not caps:
        refuse("no capability matched any tool name, so the mapping is stale and every gap below "
               "would be an artifact of it")

    meta = gh("repos/" + REPO)
    topics = [t.lower() for t in meta.get("topics", [])]
    desc = (meta.get("description") or "").lower()
    readme = io.open(os.path.join(ROOT, "README.md"), encoding="utf-8").read().lower()

    # POSITIVE CONTROL: our own name must come back, or every miss below is meaningless.
    pos, _ = search_position("inspeximus")
    if pos is None:
        refuse("GitHub search does not return this repository for its own name, so the instrument "
               "is dead and no 'not found' below can be trusted")

    rows = []
    for word, impl in sorted(caps.items()):
        in_topic = any(word in t for t in topics)
        in_desc = word in desc
        in_readme = readme.count(word)
        p, total = search_position("%s agent memory" % word)
        rows.append({"capability": word, "tools": impl[:4], "in_topics": in_topic,
                     "in_description": in_desc, "in_readme": in_readme,
                     "search_position": p, "search_total": total})
        time.sleep(2)

    findable = [r for r in rows if r["search_position"] is not None]
    readme_only = [r for r in rows
                   if r["in_readme"] and not r["in_topics"] and not r["in_description"]]

    res = {
        "verdict": ("EVERY_CAPABILITY_IS_REACHABLE" if len(findable) == len(rows)
                    else "CAPABILITIES_ARE_INVISIBLE_TO_SEARCH"),
        "repo": REPO,
        "tools": len(tools),
        "capabilities": len(rows),
        "findable_by_search": len(findable),
        "documented_only_in_the_readme": [r["capability"] for r in readme_only],
        "topics_used": len(topics),
        "topic_budget": TOPIC_CAP,
        "topic_slots_free": TOPIC_CAP - len(topics),
        "description_chars": len(meta.get("description") or ""),
        "rows": rows,
    }
    json.dump(res, io.open(OUT, "w", encoding="utf-8", newline="\n"), indent=1, ensure_ascii=False)

    print("  %-14s %-6s %-6s %-8s %s" % ("capability", "topic", "descr", "README", "search rank"))
    for r in rows:
        print("  %-14s %-6s %-6s %-8s %s"
              % (r["capability"], "yes" if r["in_topics"] else "-",
                 "yes" if r["in_description"] else "-", r["in_readme"] or "-",
                 r["search_position"] if r["search_position"] is not None else "not in top %d" % (DEPTH * 100)))
    print()
    print("  reachable by search: %d of %d capabilities" % (len(findable), len(rows)))
    print("  documented only in the README (the one surface search ignores): %s"
          % (", ".join(res["documented_only_in_the_readme"]) or "none"))
    print("  topics %d/%d used, %d free; description %d/350 chars"
          % (len(topics), TOPIC_CAP, res["topic_slots_free"], res["description_chars"]))
    print("  verdict: %s" % res["verdict"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
