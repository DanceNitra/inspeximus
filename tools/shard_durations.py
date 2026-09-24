"""Merge the per-shard timing files of one CI run into tests/shard_durations.json.

Each CI shard runs pytest with SHARD_TIMES set and uploads {node id: seconds} (tests/conftest.py). The
shards are balanced on the merged file, so it has to describe the suite as it runs in CI, not on this
machine: a Windows laptop and a 4-vCPU Linux runner spend their time in different places.

    python tools/shard_durations.py "times/*.json" [--reports "reports/*.json" --shards N]

With --reports and --shards, it also prints the estimated wall time of each shard under that count,
computed by the same function the conftest uses. The groups come from the shard reports, where xdist
writes each grouped test as "<node id>@<group>", so nothing here restates which tests are grouped.
"""
from __future__ import annotations

import glob
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "tests", "shard_durations.json")


def _paths(pattern):
    return sorted(glob.glob(pattern))


def merge(paths):
    """Suite timings keyed by node id. A file named mutation-*.json holds the serial mutation set,
    which shard 0 runs after its tests; it is stored as one total under the conftest's MUTATION_KEY."""
    merged, mutation = {}, 0.0
    for p in paths:
        with open(p, encoding="utf-8") as fh:
            data = json.load(fh)
        if os.path.basename(p).startswith("mutation-"):
            mutation += sum(float(v) for v in data.values())
            continue
        for node, secs in data.items():
            merged[node.split("@")[0]] = round(float(secs), 3)
    if mutation:
        merged["@mutation_set"] = round(mutation, 3)
    return dict(sorted(merged.items()))


def groups_from_reports(paths):
    groups = {}
    for p in paths:
        with open(p, encoding="utf-8") as fh:
            for node in json.load(fh)["all"]:
                if "@" in node:
                    plain, group = node.split("@", 1)
                    groups[plain] = group
    return groups


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    opts = {}
    for flag in ("--reports", "--shards"):
        if flag in argv:
            i = argv.index(flag)
            opts[flag] = argv[i + 1]
            del argv[i:i + 2]
    paths = [p for a in argv for p in _paths(a)]
    if not paths:
        print("no timing files matched", argv)
        return 1
    merged = merge(paths)
    with open(OUT, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(merged, fh, indent=0)
        fh.write("\n")
    print("wrote %d tests, %.0f s in total, to %s"
          % (sum(1 for t in merged if not t.startswith("@")), sum(merged.values()), OUT))
    if "--reports" in opts and "--shards" in opts:
        sys.path.insert(0, os.path.join(ROOT, "tests"))
        from conftest import shard_plan  # noqa: E402
        groups = groups_from_reports(_paths(opts["--reports"]))
        n = int(opts["--shards"])
        tests = [(t, groups.get(t)) for t in merged if not t.startswith("@")]
        _, wall = shard_plan(tests, n, merged)
        for j, w in enumerate(wall):
            print("shard %d/%d: estimated %.1f min of test time" % (j, n, w / 60))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
