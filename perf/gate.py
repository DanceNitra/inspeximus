#!/usr/bin/env python3
"""Performance REGRESSION GATE for inspeximus. Not a benchmark -- our benchmark is RAMR.

    python perf/gate.py record   # measure and overwrite perf/baseline.json
    python perf/gate.py check    # measure and compare; exit 1 on a regression

WHY THIS IS NOT A WALL-CLOCK GATE, which is the whole design.

The obvious version times a workload and fails if it got slower. Measured on the development machine
before writing this: the SAME code path timed 256.5 ms and 353.7 ms in two runs an hour apart -- 38%
apart -- and `memory_report` at n=8,000 ranged 8.22-11.54 s across five interleaved runs, a 40% spread
inside one arm. A shared CI runner is worse. A wall-clock threshold tight enough to catch a real
regression would fire constantly on noise; one loose enough to be quiet would catch nothing. Both
failure modes end the same way: someone stops reading the job.

So the gate is built on WORK COUNTERS instead -- integers that do not vary between runs or machines:

  * how many times the store file is atomically replaced,
  * how many times each sidecar (tombstones, receipts) is replaced,
  * how many times the WHOLE store is serialized, and how many bytes that produced.

Those are the quantities the real regressions actually moved. The O(k^2) erasure defect fixed in 1.88.1
appeared as 401 tombstone-sidecar writes for 400 erased records where 1 was correct; a timing gate would
have needed a large fixture to see it, while the counter shows it at any size, exactly, with no noise.

Wall-clock is still recorded, because "the counters are flat and it is 10x slower" is worth knowing --
but it is ADVISORY: reported, never the reason for a red build. The band is stated rather than implied.

HOW TO CHANGE A NUMBER. Run `record`, read the diff, and say in the commit message why the new number is
correct. The baseline is pinned to what was MEASURED, with no slack: slack is what lets a regression land
unnoticed, which is how a pinned number stops being a pin.
"""
from __future__ import annotations

import json
import os
import statistics
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
BASELINE = Path(__file__).resolve().parent / "baseline.json"

import inspeximus.core as core                        # noqa: E402
from inspeximus.core import Inspeximus                # noqa: E402

#: `serialized_bytes` is the one counter that is NOT exact, and pretending otherwise would have made this
#: gate a false-alarm machine on day one. MEASURED: two consecutive runs of the same code differ by ~4,500
#: bytes on write_n1000 (0.003%), because record timestamps vary in digit count. It is banded instead of
#: pinned -- wide enough to ignore digit drift, far tighter than any regression that matters (the shape of
#: a real one is a multiple, not a fraction of a percent).
BYTES_BAND = 0.02

#: Wall-clock is advisory. This multiple exists only to catch a catastrophe (an accidental O(n^2) in a
#: path with no counter), not to police normal variation. Measured run-to-run spread on the development
#: machine was 15-40% on these workloads; 4x is comfortably outside that and still catches a 10x.
TIME_ALARM_FACTOR = 4.0

#: Timing repeats. Small on purpose -- the counters are the gate, the clock is a smoke alarm.
REPEATS = 3


# ── instrumentation ────────────────────────────────────────────────────────────────────────────────

class Counters:
    """Wrap the two chokepoints every write and every full serialization passes through.

    `core.os.replace` is the atomic-write primitive for the store AND both sidecars; `core._dump_store`
    is the whole-store serializer. Between them they see every unit of work whose growth has actually
    bitten us. Nothing here samples or estimates: these are exact call counts.
    """

    def __init__(self):
        self.replaces = {"store": 0, "tombstones": 0, "receipts": 0, "other": 0}
        self.dumps = 0
        self.dump_bytes = 0
        self._real_replace = None
        self._real_dump = None

    def __enter__(self):
        self._real_replace, self._real_dump = core.os.replace, core._dump_store

        def replace(src, dst):
            name = str(dst)
            if name.endswith(".tombstones.json"):
                self.replaces["tombstones"] += 1
            elif name.endswith(".receipts.json"):
                self.replaces["receipts"] += 1
            elif name.endswith(".json"):
                self.replaces["store"] += 1
            else:
                self.replaces["other"] += 1
            return self._real_replace(src, dst)

        def dump(items):
            out = self._real_dump(items)
            self.dumps += 1
            self.dump_bytes += len(out)
            return out

        core.os.replace, core._dump_store = replace, dump
        return self

    def __exit__(self, *exc):
        core.os.replace, core._dump_store = self._real_replace, self._real_dump
        return False

    def as_dict(self):
        return {"replace_store": self.replaces["store"],
                "replace_tombstones": self.replaces["tombstones"],
                "replace_receipts": self.replaces["receipts"],
                "full_serializations": self.dumps,
                "serialized_bytes": self.dump_bytes}


# ── locked workloads ───────────────────────────────────────────────────────────────────────────────
# Each returns a callable. Fixtures are deterministic: no randomness, no clock, no network, no embedder,
# so the counters are reproducible on any machine and any Python. If you change a fixture you change the
# baseline -- say so in the commit.

def _store_path():
    return os.path.join(tempfile.mkdtemp(), "s.json")


def w_write(n):
    """n remembers then a flush -- the write path, where per-call full saves would show up."""
    def run():
        m = Inspeximus(_store_path())
        for i in range(n):
            m.remember(f"record {i} alpha beta gamma deploy salary", tags=["a"], source={"doc": f"d{i % 7}"})
        m.flush()
    return run


def w_recall(n, q):
    """q lexical recalls over n records -- the read path. Counters should stay at ZERO here."""
    p = _store_path()
    m = Inspeximus(p)
    for i in range(n):
        m.remember(f"record {i} alpha beta gamma deploy salary prague budget", source={"doc": f"d{i % 50}"})
    m.flush()

    def run():
        for i in range(q):
            m.recall(f"alpha beta gamma {i % 7}", k=5)
    return run


def w_erase(k, n):
    """Erase k records of one subject among n others.

    THE REGRESSION THIS FILE EXISTS FOR. Before 1.88.1 the tombstone sidecar was rewritten once per
    tombstone, so replace_tombstones equalled k. It is 1. If it ever tracks k again, this goes red at
    any fixture size, instantly, with no reference to the clock.
    """
    def run():
        m = Inspeximus(_store_path(), receipts=True)
        for i in range(k):
            m.remember(f"subject record {i}", tags=["pii"], source={"doc": "hr/alice"})
        for j in range(n):
            m.remember(f"other record {j}", tags=["ops"], source={"doc": f"ops/{j % 20}"})
        m.flush()
        # Count AND time only the erasure. The first version timed the whole callable and reported 43.8s
        # for an erasure that takes a fraction of a second -- the fixture build dominated, so the arm was
        # measuring `remember` while claiming to measure `forget_subject`.
        with Counters() as c:
            t0 = time.perf_counter()
            m.forget_subject("hr/alice")
            run.elapsed = time.perf_counter() - t0
        run.inner = c.as_dict()
    return run


def w_session(n):
    """A mixed agent session: writes, reads, credit, then a targeted forget.

    This replaced a `consolidate` arm that could not move. On a fixture of near-identical records
    consolidate flagged all of them as hubs, linked nothing, saved nothing, and finished in 3 ms with
    every counter at zero -- an arm that cannot go red, which is the defect class this whole gate is
    meant to catch. Better to measure the path an agent actually walks.
    """
    def run():
        m = Inspeximus(_store_path(), receipts=True)
        # remember() returns the id as a plain string, not a record dict.
        ids = [m.remember(f"note {i} about deploy key {i % 13} and budget {i % 7}",
                          source={"doc": f"d{i % 9}"}) for i in range(n)]
        for i in range(n // 5):
            m.recall(f"deploy key {i % 13}", k=5)
        for i in range(0, n, 10):
            m.credit(ids[i], True)
        m.forget(ids=ids[: n // 20])
        m.flush()
    return run


WORKLOADS = {
    "write_n1000":        (lambda: w_write(1000),        "1,000 remembers + flush"),
    "recall_n2000_q100":  (lambda: w_recall(2000, 100),  "100 lexical recalls over 2,000 records"),
    "erase_k200_n2000":   (lambda: w_erase(200, 2000),   "erase 200 subject records among 2,000"),
    "session_n500":       (lambda: w_session(500),       "mixed session: 500 writes, 100 recalls, 50 credits, 25 forgets"),
}


# ── measurement ────────────────────────────────────────────────────────────────────────────────────

def measure():
    out = {}
    for name, (build, desc) in WORKLOADS.items():
        run = build()                                   # fixture built OUTSIDE the counted region
        with Counters() as c:
            t0 = time.perf_counter()
            run()
            first = time.perf_counter() - t0
        counters = getattr(run, "inner", None) or c.as_dict()

        times = [getattr(run, "elapsed", first)]
        for _ in range(REPEATS - 1):
            r = build()
            t0 = time.perf_counter()
            r()
            times.append(getattr(r, "elapsed", time.perf_counter() - t0))

        out[name] = {"desc": desc, "counters": counters,
                     "seconds_median": round(statistics.median(times), 4),
                     "seconds_min": round(min(times), 4), "seconds_max": round(max(times), 4)}
    return out


# ── the gate ───────────────────────────────────────────────────────────────────────────────────────

def compare(base, now):
    """Counters are exact and gate the build. Time only alarms past TIME_ALARM_FACTOR."""
    fail, warn = [], []
    for name, b in base.items():
        n = now.get(name)
        if n is None:
            fail.append(f"{name}: workload MISSING from this run -- a gate that lost its workload is not a gate")
            continue
        for key, bv in b["counters"].items():
            nv = n["counters"].get(key)
            if nv is None:
                fail.append(f"{name}.{key}: counter disappeared (instrumentation detached?)")
            elif key == "serialized_bytes":
                if bv and abs(nv - bv) / bv > BYTES_BAND:
                    d = (nv - bv) / bv * 100
                    (fail if nv > bv else warn).append(
                        f"{name}.{key}: {bv:,} -> {nv:,} ({d:+.1f}%, band is +/-{BYTES_BAND*100:.0f}%)")
            elif nv != bv:
                verb = "grew" if nv > bv else "dropped"
                sev = fail if nv > bv else warn
                sev.append(f"{name}.{key}: {bv} -> {nv} ({verb})")
        bt, nt = b["seconds_median"], n["seconds_median"]
        if bt > 0 and nt > bt * TIME_ALARM_FACTOR:
            fail.append(f"{name}: {bt:.3f}s -> {nt:.3f}s, past the {TIME_ALARM_FACTOR}x alarm "
                        f"(advisory band is wide on purpose; this is far outside it)")
    for name in now:
        if name not in base:
            warn.append(f"{name}: new workload, not in the baseline -- run `record`")
    return fail, warn


def main(argv):
    cmd = argv[1] if len(argv) > 1 else "check"
    now = measure()

    if cmd == "record":
        BASELINE.write_text(json.dumps(now, indent=1) + "\n", encoding="utf-8")
        print(f"recorded {len(now)} workloads -> {BASELINE.relative_to(ROOT)}")
        for k, v in now.items():
            print(f"  {k:22} {v['counters']}  median {v['seconds_median']}s")
        return 0

    if not BASELINE.exists():
        print("no baseline; run: python perf/gate.py record", file=sys.stderr)
        return 2
    base = json.loads(BASELINE.read_text(encoding="utf-8"))
    fail, warn = compare(base, now)

    for k, v in now.items():
        b = base.get(k, {})
        print(f"  {k:22} {v['counters']}  median {v['seconds_median']}s "
              f"(baseline {b.get('seconds_median', '?')}s)")
    for w in warn:
        print(f"  NOTE {w}")
    if fail:
        print("\nPERFORMANCE REGRESSION:", file=sys.stderr)
        for f in fail:
            print(f"  {f}", file=sys.stderr)
        print("\nCounters are exact -- a change here is real work being done that was not being done "
              "before. If it is intended, run `python perf/gate.py record` and justify the new number "
              "in the commit message.", file=sys.stderr)
        return 1
    print("\nno regression")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
