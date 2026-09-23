"""The shards of one CI run must add up to the whole suite, each test exactly once.

Each shard runs `pytest --shard i/n` with SHARD_REPORT set, and tests/conftest.py writes what that
shard collected and what it kept. A green shard says its tests passed; only this says that the green
shards between them ran everything. It fails when a shard is missing, when two shards collected
different suites (different dependencies, a different commit), when a test is in no shard, or when a
test is in two.

    python tools/shard_total.py shard-reports/*.json [--expect N]
"""
from __future__ import annotations

import glob
import json
import sys


def check(paths, expect_shards=None):
    reports = [json.load(open(p, encoding="utf-8")) for p in paths]
    problems = []
    if not reports:
        return ["no shard reports"], 0
    n = int(reports[0]["shard"].split("/")[1])
    seen = sorted(int(r["shard"].split("/")[0]) for r in reports)
    if seen != list(range(expect_shards or n)):
        problems.append("shards present %s, expected 0..%d" % (seen, (expect_shards or n) - 1))
    whole = set(reports[0]["all"])
    for r in reports[1:]:
        if set(r["all"]) != whole:
            problems.append("shard %s collected a different suite (%d vs %d tests)"
                            % (r["shard"], len(r["all"]), len(whole)))
    kept = [t for r in reports for t in r["mine"]]
    missing = whole - set(kept)
    doubled = len(kept) - len(set(kept))
    if missing:
        problems.append("%d test(s) in no shard, first: %s" % (len(missing), sorted(missing)[0]))
    if doubled:
        problems.append("%d test(s) in two shards" % doubled)
    return problems, len(whole)


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    expect = None
    if "--expect" in argv:
        i = argv.index("--expect")
        expect = int(argv[i + 1])
        del argv[i:i + 2]
    paths = [p for a in argv for p in glob.glob(a)]
    problems, total = check(paths, expect)
    if problems:
        for p in problems:
            print("FAIL:", p)
        return 1
    print("OK: %d shard(s) ran all %d collected tests, each exactly once" % (len(paths), total))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
