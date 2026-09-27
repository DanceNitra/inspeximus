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
        self.loads = 0
        self._real_replace = None
        self._real_dump = None
        self._real_load = None

    def __enter__(self):
        self._real_replace, self._real_dump = core.os.replace, core._dump_store
        self._real_load = real_load = core.Inspeximus._load_from_disk

        # A STORE LOAD reads and parses every row of the file. It is the unit of work a hook event
        # pays before it can do anything, and on 2026-09-27 a Read event paid it to write nothing:
        # 5.2 s per event on a 67,165-record store (AUDIT-B B-01).
        def load(inner_self):
            self.loads += 1
            return real_load(inner_self)

        core.Inspeximus._load_from_disk = load

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

        self.calls = dict.fromkeys(COUNTED_CALLS, 0)
        self._real_calls = {}
        for name, (owner, attr) in COUNTED_CALLS.items():
            real = getattr(owner, attr)
            self._real_calls[name] = real

            def counted(*a, _real=real, _name=name, **k):
                self.calls[_name] += 1
                return _real(*a, **k)

            setattr(owner, attr, counted)

        # A regex search by the read guard. `recall` assesses every pooled record in every new process,
        # and seven searches per clean record were 8.1 s of a 12.0 s hook recall (AUDIT-B B-05).
        self.searches = 0
        self._real_shapes = core._INSTRUCTION_SHAPES
        counter = self

        class _Counted:
            def __init__(self, rx):
                self._rx = rx

            def search(self, *a, **k):
                counter.searches += 1
                return self._rx.search(*a, **k)

        core._INSTRUCTION_SHAPES = [(n, _Counted(rx)) for n, rx in self._real_shapes]

        # An order UPDATE executed by the row store. A full-diff save issued one per unmoved row, each a
        # no-op: 67,165 statements per session boundary on a real hook store (AUDIT-B B-09). SQLite's
        # trace callback fires once per row of an executemany, so this is an exact count.
        self.order_updates = 0
        self._real_connect = real_connect = core._rows._connect

        def connect(path):
            con = real_connect(path)
            con.set_trace_callback(lambda s: setattr(counter, "order_updates", counter.order_updates + 1)
                                   if s.lstrip().upper().startswith("UPDATE RECORDS SET ORD") else None)
            return con

        core._rows._connect = connect
        return self

    def __exit__(self, *exc):
        core.os.replace, core._dump_store = self._real_replace, self._real_dump
        core.Inspeximus._load_from_disk = self._real_load
        for name, (owner, attr) in COUNTED_CALLS.items():
            setattr(owner, attr, self._real_calls[name])
        core._INSTRUCTION_SHAPES = self._real_shapes
        core._rows._connect = self._real_connect
        return False

    def as_dict(self):
        return {"replace_store": self.replaces["store"],
                "replace_tombstones": self.replaces["tombstones"],
                "replace_receipts": self.replaces["receipts"],
                "full_serializations": self.dumps,
                "serialized_bytes": self.dump_bytes,
                "store_loads": self.loads,
                "guard_regex_searches": self.searches,
                "order_updates": self.order_updates,
                **self.calls}


#: Calls counted by name: counter -> (owner, attribute). Each one is a unit of work that grew once.
#:   type_inferences  two regex searches over a record's text. Opening a store ran it for every record
#:                    and discarded the result for every record that had a type (AUDIT-B B-04).
#:   current_active_scans  one full scan of the store for one key. The per-key value reports ran one
#:                    per key, O(keys x records): 40.5 s profiled at 10,934 records (AUDIT-B B-06).
COUNTED_CALLS = {
    "type_inferences": (core, "_infer_type"),
    "current_active_scans": (core.Inspeximus, "_current_active"),
}


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


#: The PostToolUse events of `w_hook`, in order. Ten are tools the hook does not capture; three are.
HOOK_EVENTS = ["Read"] * 4 + ["Grep"] * 3 + ["Glob"] * 3 + ["Edit", "Write", "Bash"]


def _clean_env():
    """Remove every INSPEXIMUS_* variable and return them, so a developer's shared store, embedder or
    decision store cannot reach a workload. Restore with `_restore_env`."""
    return {k: os.environ.pop(k) for k in [k for k in os.environ if k.startswith("INSPEXIMUS_")]}


def _restore_env(saved):
    for k in [k for k in os.environ if k.startswith("INSPEXIMUS_")]:
        del os.environ[k]
    os.environ.update(saved)


def w_hook(n):
    """The Claude Code hook's PostToolUse path over a project store of n records.

    The hook is a fresh process per event, so every event that opens the store pays one full load.
    `store_loads` must equal the number of CAPTURED events (3): an ignored tool writes nothing and must
    not read the store either (AUDIT-B B-01).
    """
    import inspeximus.claude_code as cc
    proj = tempfile.mkdtemp()
    os.makedirs(os.path.join(proj, ".git"))
    c = proj.replace("\\", "/")
    env = {"INSPEXIMUS_CODING_STORE": os.path.join(proj, ".inspeximus"), "INSPEXIMUS_NO_NUDGE": "1"}
    saved = _clean_env()
    os.environ.update(env)
    try:
        m = cc._store(proj)
        for i in range(n):
            m.remember(f"ran: make target {i}", key=f"cmd:{i}", mtype="episodic", tags=["bash"])
        m.flush()
    finally:
        _restore_env(saved)
    inputs = {"Read": {"file_path": c + "/x.py"}, "Grep": {"pattern": "foo"}, "Glob": {"pattern": "*.py"},
              "Edit": {"file_path": c + "/a.py", "new_string": "x = 1"},
              "Write": {"file_path": c + "/b.py", "content": "y = 2"}, "Bash": {"command": "ls -la"}}

    def run():
        saved_run = _clean_env()
        os.environ.update(env)
        try:
            for tool in HOOK_EVENTS:
                cc.capture({"hook_event_name": "PostToolUse", "tool_name": tool, "tool_input": inputs[tool],
                            "cwd": c, "session_id": "gate"})
        finally:
            _restore_env(saved_run)
    return run


def w_reports(k):
    """The per-key value reports over k keys, each holding a retired and a current value: one
    supersession_report and five recalls with suppress_stale_values. Each report must look up every
    key's current record in one pass, so `current_active_scans` is 0 (AUDIT-B B-06)."""
    m = Inspeximus(_store_path())
    for i in range(k):
        m.remember(f"the office for team {i} is in Vienna", key=f"office{i}", object="Vienna")
        m.remember(f"the office for team {i} is in Prague", key=f"office{i}", object="Prague")
    m.flush()

    def run():
        m.supersession_report()
        for i in range(5):
            m.recall(f"which office does team {i} use", k=5, suppress_stale_values=True)
    return run


def w_prompt(n):
    """What a UserPromptSubmit hook does: a FRESH handle opens a store of n records and recalls once.

    A fresh process has assessed nothing, so the read guard runs over every record. None of these
    texts holds a word any instruction shape requires, so `guard_regex_searches` is 0 (AUDIT-B B-05);
    it was 7 per record."""
    p = _store_path()
    m = Inspeximus(p)
    for i in range(n):
        m.remember(f"ran: make target {i} in the build directory", key=f"cmd:{i}", mtype="episodic")
    m.flush()

    def run():
        Inspeximus(p).recall("which make target builds the docs", k=6)
    return run


class _CountedId(str):
    """A record id that counts its own `__eq__` calls. A set or dict lookup finds the identical object
    without calling it; a list membership test calls it once per element it passes."""
    compared = 0

    def __eq__(self, other):
        _CountedId.compared += 1
        return str.__eq__(self, other)

    __hash__ = str.__hash__


def w_row_rewrite(n):
    """A row store saved with n new rows, then every row rewritten (`rewrite_all`, the first open after
    an encoding upgrade). `row_id_comparisons` stays near 0: it was n^2 / 2 per save when the full diff
    tested membership in lists (AUDIT-B B-15)."""
    from inspeximus import sqlite_store as ss
    items = [{"id": _CountedId(f"id{i:06d}"), "text": f"record {i}", "ts": 1.0, "mtype": "episodic"}
             for i in range(n)]

    def run():
        p = _store_path()
        with Counters() as c:
            _CountedId.compared = 0
            snap = ss.save(p, items, {})["snapshot"]
            ss.save(p, items, snap, rewrite_all=True)
        run.inner = {**c.as_dict(), "row_id_comparisons": _CountedId.compared}
    return run


def w_boundary(n):
    """A session boundary on a store of n records: open_session, one write, close_session, flush.
    close_session asks for a full reconcile, and the full-diff save issued an order UPDATE for every
    unmoved row. `order_updates` counts the ones executed: 0 when no order moved (AUDIT-B B-09)."""
    p = _store_path()
    m = Inspeximus(p)
    for i in range(n):
        m.remember(f"note {i} about the release", key=f"k{i}", mtype="episodic")
    m.flush()

    def run():
        h = Inspeximus(p)
        h.open_session("gate")
        h.remember("the release moved to Friday", tags=["decision"])
        h.close_session("gate")
        h.flush()
    return run


def w_hook_import(root=None):
    """A PreToolUse event for `ls`, run as the real hook process. `hook_imports_numpy` is 1 when that
    process imported numpy. numpy only accelerates semantic recall, and an eager import was about
    0.17 s of every hook event (AUDIT-B B-10). A stand-in numpy first on PYTHONPATH records its own
    import, so the counter reads the same with or without numpy installed; if the stand-in is not
    importable the workload raises instead of reporting a zero it did not measure."""
    import json as _json
    import subprocess
    d = tempfile.mkdtemp()
    fake = os.path.join(d, "fake")
    os.makedirs(os.path.join(fake, "numpy"))
    with open(os.path.join(fake, "numpy", "__init__.py"), "w", encoding="utf-8") as fh:
        fh.write("import os\nopen(os.environ['NUMPY_MARKER'], 'w').write('imported')\n")
    marker = os.path.join(d, "numpy-was-imported")
    proj = os.path.join(d, "proj")
    os.makedirs(os.path.join(proj, ".git"))
    env = {k: v for k, v in os.environ.items() if not k.startswith("INSPEXIMUS_")}
    env.update(PYTHONPATH=os.pathsep.join([fake, str(root or ROOT)]), NUMPY_MARKER=marker,
               HOME=d, USERPROFILE=d, INSPEXIMUS_NO_UPDATE_CHECK="1")
    subprocess.run([sys.executable, "-c", "import numpy"], env=env, cwd=proj, check=True)
    if not os.path.exists(marker):
        raise RuntimeError("the stand-in numpy is not importable; hook_imports_numpy would measure nothing")
    ev = {"hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": {"command": "ls"},
          "cwd": proj.replace("\\", "/"), "session_id": "gate"}

    def run():
        if os.path.exists(marker):
            os.remove(marker)
        subprocess.run([sys.executable, "-m", "inspeximus.claude_code"], input=_json.dumps(ev).encode(),
                       env=env, cwd=proj, capture_output=True)
        run.inner = {"hook_imports_numpy": int(os.path.exists(marker))}
    return run


WORKLOADS = {
    "write_n1000":        (lambda: w_write(1000),        "1,000 remembers + flush"),
    "recall_n2000_q100":  (lambda: w_recall(2000, 100),  "100 lexical recalls over 2,000 records"),
    "erase_k200_n2000":   (lambda: w_erase(200, 2000),   "erase 200 subject records among 2,000"),
    "session_n500":       (lambda: w_session(500),       "mixed session: 500 writes, 100 recalls, 50 credits, 25 forgets"),
    "hook_n2000":         (lambda: w_hook(2000),         "hook PostToolUse: 10 ignored + 3 captured events, 2,000-record store"),
    "reports_k300":       (lambda: w_reports(300),       "supersession_report + 5 suppressing recalls over 300 keys"),
    "prompt_n2000":       (lambda: w_prompt(2000),       "fresh handle opens a 2,000-record store and recalls once"),
    "row_rewrite_n2000":  (lambda: w_row_rewrite(2000),  "row store: save 2,000 new rows, then rewrite all of them"),
    "hook_import":        (lambda: w_hook_import(),      "the hook process for a PreToolUse `ls`: does it import numpy"),
    "boundary_n2000":     (lambda: w_boundary(2000),     "session boundary (open, write, close, flush) on a 2,000-record store"),
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
