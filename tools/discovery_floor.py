"""Fail when fewer capabilities are reachable by search than the last time we looked.

A ratchet, not a target. `probes/which_of_our_capabilities_a_searcher_can_actually_find.py` measures
which of the things inspeximus implements a person can actually find it by; this refuses to let that
number fall silently.

WHY A FLOOR AND NOT A FIXED NUMBER. Search rankings move on their own, so an equality check would go
red on somebody else publishing a repository. A floor only fires when WE lose ground, which is the
event worth a build failure. It also names the capability that went missing, because "4 instead of
5" does not tell you what to fix.

RAISE THE FLOOR when a run beats it. That is a deliberate commit, so the ratchet cannot creep
upward on one lucky measurement and then fail every week after.

    python tools/discovery_floor.py
"""
from __future__ import annotations

import io
import json
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
RESULT = os.path.join(ROOT, "probes",
                      "which_of_our_capabilities_a_searcher_can_actually_find.result.json")
FLOOR = os.path.join(HERE, "discovery_floor.json")


def main():
    if not os.path.exists(RESULT):
        print("REFUSED: %s does not exist, so the probe did not run and there is nothing to "
              "compare. A missing measurement is not a pass." % os.path.relpath(RESULT, ROOT))
        return 2
    res = json.load(io.open(RESULT, encoding="utf-8"))
    if res.get("verdict") == "REFUSED":
        print("REFUSED: the probe itself refused (%s). No floor can be checked against a "
              "measurement that never happened." % res.get("why", "no reason recorded"))
        return 2

    floor = json.load(io.open(FLOOR, encoding="utf-8"))
    reachable = {r["capability"] for r in res["rows"] if r.get("search_position") is not None}
    want = set(floor["reachable"])

    print("reachable now : %d  (%s)" % (len(reachable), ", ".join(sorted(reachable)) or "none"))
    print("floor         : %d  (%s)" % (len(want), ", ".join(sorted(want))))

    lost = sorted(want - reachable)
    if lost:
        print()
        print("REGRESSION: %s no longer returns this repository in the top %d."
              % (", ".join(lost), 300))
        print("Either the topic or description term that carried it was removed, or somebody else "
              "now outranks us. Check the repo topics and server.json before assuming the latter.")
        return 1

    gained = sorted(reachable - want)
    if gained:
        print()
        print("GAINED: %s. Raise the floor in tools/discovery_floor.json to keep the ratchet honest."
              % ", ".join(gained))
    print()
    print("OK: nothing that was reachable has been lost.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
