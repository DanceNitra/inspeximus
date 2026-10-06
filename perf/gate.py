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

import contextlib
import json
import os
import shutil
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

#: Counters of bytes, banded for the same reason: `receipt_bytes_written` is the size of the receipts the arm
#: wrote, and a timestamp's digit count moves it by a fraction of a percent.
BANDED_BYTES = ("serialized_bytes", "receipt_bytes_written")

#: Wall-clock is advisory. This multiple exists only to catch a catastrophe (an accidental O(n^2) in a
#: path with no counter), not to police normal variation. Measured run-to-run spread on the development
#: machine was 15-40% on these workloads; 4x is comfortably outside that and still catches a 10x.
TIME_ALARM_FACTOR = 4.0

#: TWIN ARMS: one workload measured under two conditions, such as `prompt_unstamped_n2000`, which is
#: `prompt_n2000` under a key home that cannot verify the stamps. An arm is the twin of the arm its `desc`
#: starts with. Twins that do the same counted work must take about the same time: a gap past this factor
#: is work no counter sees, and it is a gate error, in `check` and in `record`. Measured at 3.15.4: the
#: two prompt arms had equal work counters and medians 0.099 s and 1.509 s (15x). The gap was an uncached
#: missing-key lookup per record, it was recorded as the baseline, and the hook it measured took 48.6 s a
#: prompt on a 71,772-row store against 5.3 s on 3.15.3 (AUDIT-B, 2026-09-29).
TWIN_TIME_FACTOR = 5.0

#: Counters of setting up a condition rather than of the workload, so twins may differ in them: the
#: unstamped arm lists one more directory (the foreign key home) by construction.
TWIN_SETUP_COUNTERS = frozenset({"dir_listings", "store_loads"})

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
        self.receipt_bytes = 0       #: bytes written to the receipt sidecar pair: whole-file replaces and tail appends
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

        # BYTES WRITTEN TO THE RECEIPT SIDECARS. The array format replaces the whole file on every receipted write
        # (16 MB on our MCP store); the tail format appends one line. `os.replace` counts the first and not the
        # second, so the bytes are counted where both pass: `_durable_replace` and `receipts_tail.append`.
        self._real_durable = core._durable_replace
        from inspeximus import receipts_tail as _rtmod
        self._rtmod = _rtmod
        self._real_tail_append = getattr(_rtmod, "append", None)
        counter0 = self

        def durable(path, payload, encoding="utf-8"):
            if str(path).endswith((".receipts.json", ".receipts.tail.jsonl")):
                counter0.receipt_bytes += len(payload) if isinstance(payload, bytes) else len(payload.encode(encoding))
            return counter0._real_durable(path, payload, encoding)

        core._durable_replace = durable
        if self._real_tail_append is not None:
            def tail_append(tail, data, *a, **k):
                counter0.receipt_bytes += len(data)
                return counter0._real_tail_append(tail, data, *a, **k)

            _rtmod.append = tail_append

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

        # A FULL-DIFF SAVE serialises and compares every row. A session boundary is meant to pay one;
        # a write=False preview inside open_session made it two (AUDIT-B B-20).
        self.full_diff_saves = 0
        self._real_save = real_save = core._rows.save

        def save(path, items, before, dirty=None, rewrite_all=False, **k):
            if dirty is None or rewrite_all:
                counter.full_diff_saves += 1
            return real_save(path, items, before, dirty=dirty, rewrite_all=rewrite_all, **k)

        core._rows.save = save

        # A DIRECTORY LISTING (os.listdir, os.scandir). 3.15.2's merge-backup removal ran once per
        # tombstone and listed the store's directory twice each time: 2k + 3 listings for k erased records.
        # The other counters saw it only on Windows, where the listed names were lowercased; on Linux the
        # gate stayed green (A->B-2). Counted on every platform, so per-record directory work fails here.
        self.listings = 0
        self._real_listdir, self._real_scandir = core.os.listdir, core.os.scandir

        def listdir(*a, **k):
            counter.listings += 1
            return self._real_listdir(*a, **k)

        def scandir(*a, **k):
            counter.listings += 1
            return self._real_scandir(*a, **k)

        core.os.listdir, core.os.scandir = listdir, scandir
        return self

    def __exit__(self, *exc):
        core.os.listdir, core.os.scandir = self._real_listdir, self._real_scandir
        core.os.replace, core._dump_store = self._real_replace, self._real_dump
        core._durable_replace = self._real_durable
        if self._real_tail_append is not None:
            self._rtmod.append = self._real_tail_append
        core.Inspeximus._load_from_disk = self._real_load
        for name, (owner, attr) in COUNTED_CALLS.items():
            setattr(owner, attr, self._real_calls[name])
        core._INSTRUCTION_SHAPES = self._real_shapes
        core._rows._connect = self._real_connect
        core._rows.save = self._real_save
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
                "full_diff_saves": self.full_diff_saves,
                "dir_listings": self.listings,
                **self.calls}


#: Calls counted by name: counter -> (owner, attribute). Each one is a unit of work that grew once.
#:   type_inferences  two regex searches over a record's text. Opening a store ran it for every record
#:                    and discarded the result for every record that had a type (AUDIT-B B-04).
#:   current_active_scans  one full scan of the store for one key. The per-key value reports ran one
#:                    per key, O(keys x records): 40.5 s profiled at 10,934 records (AUDIT-B B-06).
#:   row_serializations  one record serialised to its row text. Opening a store serialised every row
#:                    to build the save baseline, 1.27 s of a 5.51 s open at 67,165 records, including
#:                    opens that never save (AUDIT-B B-08).
#:   read_guard_assessments  one record checked by the read guards while a recall builds its pool.
#:                    memory_report rebuilt the pool for each of its 400 sampled queries: 2,629,200
#:                    assessments on a 10,934-record store, 400 per record (AUDIT-B B-07).
#:   decision_syncs  one check, under the store lock, of whether other writers committed before an
#:                    operation decides from the rows (A-42, A-43, 3.15.5): erasure, credit, retire, revert,
#:                    objections, the irreversible budget and a keyed remember. One per such call; a merge
#:                    follows only when the store moved.
#:   store_hash_reads  one read of the whole store file to hash it. A-37's writer guard (3.15.4) does
#:                    it on every JSON or encrypted save whose stat signature has not moved, because a
#:                    same-tick, same-size peer write leaves (mtime_ns, size) unchanged. One per save on
#:                    a JSON arm is the expected cost; a row store never reads it (its guard is per row).
#:   guard_key_lookups  one lookup of the read-guard key on disk (the key file's path, the location
#:                    checks, an open). A-30 (3.15.4) cached only a key it found, so a store with no key
#:                    in the key home paid one lookup per record on every read: 2,000 in
#:                    prompt_unstamped_n2000, whose counters were otherwise identical to the stamped
#:                    arm's, so only the advisory clock showed it (1.509 s against 0.099 s). One per
#:                    handle is the expected cost (A-45, 3.15.5).
#:   partition_registry_reads  one read of `<store>.partitions.json`. Only a write that carries a
#:                    `partition:` tag reads it, to refuse a closed partition (AUDIT-A F-7, 3.16.3); every
#:                    arm here writes untagged, so the expected count is 0 everywhere.
#:   guard_shape_scans  one record put through the read guard's instruction-shape scan. A record whose
#:                    stored verdict this store's key vouches for skips it, so this is the number of
#:                    rows a read really assessed, where read_guard_assessments also counts the cheap
#:                    skip. After `stamp_read_guards()` a fresh handle's recall scans 0 (AUDIT-B 3.16.3);
#:                    9,596 of 12,176 active rows of our own project store were scanned on every prompt.
#:   tracked_gets    one `get` on a record, which is a Python method (it wraps a nested container on first
#:                    access). state_digest read six fields of every row through it, twice per MCP tool call
#:                    under the action ledger: 155,379 calls and 0.095 s on a 13,359-record store. It reads the
#:                    scalars with `dict.get` now, so a digest costs 0 of these (AUDIT-B 3.16.3).
COUNTED_CALLS = {
    "type_inferences": (core, "_infer_type"),
    "current_active_scans": (core.Inspeximus, "_current_active"),
    "row_serializations": (core._rows, "_doc"),
    "read_guard_assessments": (core.Inspeximus, "_assess_read_guards"),
    "store_hash_reads": (core.Inspeximus, "_disk_hash"),
    "guard_key_lookups": (core, "_guard_key_file"),
    "decision_syncs": (core.Inspeximus, "_sync_before_decision"),
    "partition_registry_reads": (core, "_partition_registry"),
    "guard_shape_scans": (core, "_instruction_shape"),
    "tracked_gets": (core._TrackedDict, "get"),
}


# ── locked workloads ───────────────────────────────────────────────────────────────────────────────
# Each returns a callable. Fixtures are deterministic: no randomness, no clock, no network, no embedder,
# so the counters are reproducible on any machine and any Python. If you change a fixture you change the
# baseline -- say so in the commit.

#: Every store path a workload created since `measure()` last cleared it. The backend witness reads
#: these files after an arm runs (AUDIT-B B-13).
_ARM_STORES: list = []


def _store_path():
    p = os.path.join(tempfile.mkdtemp(), "s.json")
    _ARM_STORES.append(p)
    return p


@contextlib.contextmanager
def _backend(backend):
    """Run an arm on the backend it names. `json` sets INSPEXIMUS_STORE_FORMAT=json, the pin that keeps a
    store in the JSON format; every other arm runs with the pin removed, so a developer's own pin cannot
    turn a row-store arm into a JSON one. The previous value is restored afterwards.

    WHY ARMS NAME A BACKEND (AUDIT-B B-13). The default store became rows after the 2026-08-16 baseline,
    and the write, erase and session arms written for JSON ran on rows from then on. `write_n1000` went
    from 1,000 full serializations to 0, the drop was reported as a NOTE, and the JSON write path was
    measured by nothing. JSON stores are still read (encrypted stores, the pin), so both backends keep
    arms, and `compare` fails when an arm's store files are not in the format it names."""
    prev = os.environ.pop("INSPEXIMUS_STORE_FORMAT", None)
    if backend == "json":
        os.environ["INSPEXIMUS_STORE_FORMAT"] = "json"
    try:
        yield
    finally:
        os.environ.pop("INSPEXIMUS_STORE_FORMAT", None)
        if prev is not None:
            os.environ["INSPEXIMUS_STORE_FORMAT"] = prev


def _observed_backend(paths):
    """The format of the store files an arm left: `json`, `rows`, both joined by `+`, or `none`."""
    kinds = {"rows" if core._rows.looks_like_sqlite(p) else "json" for p in paths if os.path.exists(p)}
    return "+".join(sorted(kinds)) or "none"


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
        with Counters() as c, _ItemsReads() as reads:
            t0 = time.perf_counter()
            m.forget_subject("hr/alice")
            run.elapsed = time.perf_counter() - t0
        run.inner = {**c.as_dict(), "erase_items_reads": reads.n}
        # A SECOND, identical erasure on a fresh copy of the fixture, counted apart because
        # sys.setprofile slows everything it watches: the clock above must not include it.
        m2 = Inspeximus(_store_path(), receipts=True)
        for i in range(k):
            m2.remember(f"subject record {i}", tags=["pii"], source={"doc": "hr/alice"})
        for j in range(n):
            m2.remember(f"other record {j}", tags=["ops"], source={"doc": f"ops/{j % 20}"})
        m2.flush()
        with _LowerCalls() as lowers:
            m2.forget_subject("hr/alice")
        run.inner["erase_lower_calls"] = lowers.n
    return run


class _LowerCalls:
    """Count `str.lower` calls inside a block, through sys.setprofile, which reports every call into a C
    method. The residue scan lowercased every erased value once per surviving record and field:
    2,008,775 calls and 9.58 s of a 12.8 s erasure on a 10,934-record store (AUDIT-B B-18)."""

    def __enter__(self):
        self.n = 0
        counter = self

        def prof(frame, event, arg):
            if event == "c_call" and getattr(arg, "__name__", "") == "lower":
                counter.n += 1

        sys.setprofile(prof)
        return self

    def __exit__(self, *exc):
        sys.setprofile(None)
        return False


class _ItemsReads:
    """Count reads of `Inspeximus.items` inside a block. An erasure that rebuilt a whole-store id map per
    matched record read it once per record: 57.3 s for 1,666 records of a 50,000-record store
    (AUDIT-B B-17). The count is fixed for a fixed fixture, whatever the machine."""

    def __enter__(self):
        self.n = 0
        self._real = real = core.Inspeximus.items
        counter = self

        def fget(inner_self):
            counter.n += 1
            return real.fget(inner_self)

        core.Inspeximus.items = property(fget, real.fset)
        return self

    def __exit__(self, *exc):
        core.Inspeximus.items = self._real
        return False


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
    decision store cannot reach a workload. Restore with `_restore_env`. INSPEXIMUS_KEY_HOME stays set:
    it is where receipted temp stores record their chain heads, and `main()` points it at a temp folder."""
    saved = {k: os.environ.pop(k) for k in [k for k in os.environ if k.startswith("INSPEXIMUS_")]}
    if "INSPEXIMUS_KEY_HOME" in saved:
        os.environ["INSPEXIMUS_KEY_HOME"] = saved["INSPEXIMUS_KEY_HOME"]
    return saved


@contextlib.contextmanager
def _isolated_key_home():
    """Point INSPEXIMUS_KEY_HOME at a temp folder for one gate run, then restore it and delete the folder.

    A receipted store records its chain head under INSPEXIMUS_KEY_HOME, else APPDATA. The erase and session
    workloads use receipted temp stores, so a standalone run left 9 head files per run in the user's real
    `%APPDATA%/inspeximus/heads` (AUDIT-B B-21). Scoped to `main()` rather than set at import, because a
    test that imports this module shares its process with tests that read the real key-home rules."""
    import shutil
    prev = os.environ.get("INSPEXIMUS_KEY_HOME")
    home = tempfile.mkdtemp(prefix="inspeximus-gate-keys-")
    os.environ["INSPEXIMUS_KEY_HOME"] = home
    try:
        yield home
    finally:
        if prev is None:
            os.environ.pop("INSPEXIMUS_KEY_HOME", None)
        else:
            os.environ["INSPEXIMUS_KEY_HOME"] = prev
        shutil.rmtree(home, ignore_errors=True)


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
        _ARM_STORES.append(str(m.path))
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

    THE STAMPED PATH. Since A-30 (3.15.4) remember() stamps a read-guard verdict under the store's key,
    and a fresh handle with that key trusts it, so this arm measures what a user's own hook pays. It
    no longer exercises the guard's regex path at all; `prompt_unstamped_n2000` does."""
    p = _store_path()
    m = Inspeximus(p)
    for i in range(n):
        m.remember(f"ran: make target {i} in the build directory", key=f"cmd:{i}", mtype="episodic")
    m.flush()

    def run():
        Inspeximus(p).recall("which make target builds the docs", k=6)
    return run


def w_prompt_restamped(n):
    """The prompt hook's read of a store whose rows carried no read-guard stamp (written before 3.15.4)
    and were then stamped once by `stamp_read_guards()`: a FRESH handle recalls once and
    `guard_shape_scans` is 0. The stripped stamps and the stamping pass are setup, not measured."""
    p = _store_path()
    m = Inspeximus(p)
    for i in range(n):
        m.remember(f"ran: make target {i} in the build directory", key=f"cmd:{i}", mtype="episodic")
    m.flush()
    m = Inspeximus(p)
    for r in m._items:
        (r.get("meta") or {}).pop("read_guards", None)
        m._touched.add(r["id"])
    m._save(force=True)
    stamped = Inspeximus(p).stamp_read_guards()
    assert stamped["stamped"] == n, stamped

    def run():
        Inspeximus(p).recall("which make target builds the docs", k=6)
    return run


def w_digest(n):
    """`state_digest()` over n records: the digest the action ledger takes before and after every MCP tool
    call. `tracked_gets` is 0: the scalar fields are read without the record's Python-level `get`."""
    p = _store_path()
    m = Inspeximus(p)
    for i in range(n):
        m.remember(f"ran: make target {i} in the build directory", key=f"cmd:{i}", mtype="episodic")
    m.flush()
    h = Inspeximus(p)

    def run():
        with Counters() as c:
            h.state_digest()
        # `digest_rows` is the work done: without a counter above zero an arm cannot go red.
        run.inner = {**c.as_dict(), "digest_rows": len(h._items)}
    return run


def w_prompt_decisions(n):
    """The prompt hook with a DECISION STORE (`INSPEXIMUS_DECISION_STORE`), the form our own machine runs: a
    project store of n captures and a second store of n decisions, both stamped, and one UserPromptSubmit.
    The hook opens the second store and runs decisions_in_force plus a recall over it on every prompt:
    measured 2026-10-04, +0.59 s on a 4 MB store of 619 decisions. `token_builds` is the number of record
    token sets built from text (the in-process cache is empty in a fresh hook process), and
    `store_loads` counts both stores' opens."""
    import contextlib as _cl
    import io
    import inspeximus.claude_code as cc
    proj = tempfile.mkdtemp()
    os.makedirs(os.path.join(proj, ".git"))
    dpath = os.path.join(tempfile.mkdtemp(), "decisions.json")
    _ARM_STORES.append(dpath)
    env = {"INSPEXIMUS_CODING_STORE": os.path.join(proj, ".inspeximus"), "INSPEXIMUS_NO_NUDGE": "1",
           "INSPEXIMUS_DECISION_STORE": dpath}
    saved = _clean_env()
    os.environ.update(env)
    try:
        m = cc._store(proj)
        _ARM_STORES.append(str(m.path))
        for i in range(n):
            m.remember(f"ran: make target {i} in the build directory", key=f"cmd:{i}", mtype="episodic", tags=["bash"])
        m.flush()
        d = Inspeximus(dpath)
        for i in range(n):
            d.remember_decision(f"we decided that component {i} builds with the release target", because="the build is shared",
                                topic=f"component-{i}")
        d.flush()
    finally:
        _restore_env(saved)
    ev = {"hook_event_name": "UserPromptSubmit", "prompt": "which target builds the release component",
          "cwd": proj.replace("\\", "/"), "session_id": "gate"}

    def run():
        saved_run = _clean_env()
        os.environ.update(env)
        real_tokens = core._tokens
        built = {"n": 0}

        def counted(text):
            built["n"] += 1
            return real_tokens(text)
        core._tokens = counted
        try:
            with Counters() as c, _cl.redirect_stdout(io.StringIO()):
                cc.recall(ev)
        finally:
            core._tokens = real_tokens
            _restore_env(saved_run)
        run.inner = {**c.as_dict(), "token_builds": built["n"]}
    return run


def w_remember_receipted(n):
    """Five receipted `remember` calls on a long-lived handle that already holds n receipts: the MCP server's
    shape. Each write rewrites the receipts sidecar, and `receipt_encodes` counts the receipts encoded to
    text for it: 5 (the new ones), where re-encoding the chain each time was about 5 x n. The handle is warmed
    by one write before the measured part, as a server is by its first call."""
    p = _store_path()
    m = Inspeximus(p, receipts=True)
    for i in range(n):
        m.remember(f"ran: make target {i} in the build directory", key=f"cmd:{i}", mtype="episodic")
    m.flush()
    m.remember("warm up the receipt encodings", key="cmd:warm", mtype="episodic")

    def run():
        real = core._encode_receipt
        built = {"n": 0}

        def counted(e):
            built["n"] += 1
            return real(e)
        core._encode_receipt = counted
        try:
            with Counters() as c:
                for i in range(5):
                    m.remember(f"measured write {i}", key=f"cmd:m{i}", mtype="episodic")
        finally:
            core._encode_receipt = real
        run.inner = {**c.as_dict(), "receipt_encodes": built["n"], "receipt_bytes_written": c.receipt_bytes}
    return run


def w_remember_receipted_tail(n):
    """`w_remember_receipted` with the receipt sidecar in the snapshot-plus-tail format
    (`INSPEXIMUS_RECEIPTS_TAIL=1`, 3.17.0 candidate). The five measured writes append five tail lines:
    `replace_receipts` is 0 where the array format replaces the sidecar five times, and `receipt_bytes_written` is
    five receipts where the array's is five whole chains. The tail holds 6 entries at that point, below
    `COMPACT_AT`, so no snapshot is rewritten inside the counted region."""
    prev = os.environ.get("INSPEXIMUS_RECEIPTS_TAIL")
    os.environ["INSPEXIMUS_RECEIPTS_TAIL"] = "1"
    try:
        inner_run = w_remember_receipted(n)
    finally:
        if prev is None:
            os.environ.pop("INSPEXIMUS_RECEIPTS_TAIL", None)
        else:
            os.environ["INSPEXIMUS_RECEIPTS_TAIL"] = prev

    def run():
        prev2 = os.environ.get("INSPEXIMUS_RECEIPTS_TAIL")
        os.environ["INSPEXIMUS_RECEIPTS_TAIL"] = "1"
        try:
            inner_run()
        finally:
            if prev2 is None:
                os.environ.pop("INSPEXIMUS_RECEIPTS_TAIL", None)
            else:
                os.environ["INSPEXIMUS_RECEIPTS_TAIL"] = prev2
        run.inner = inner_run.inner
    return run


def w_prompt_unstamped(n):
    """w_prompt's store and recall, run as a process that CANNOT verify the stamps: a key home that did
    not write them (another user, another machine, a store copied in). The read guard then assesses every
    record, which is the path B-05's word pre-check protects: none of these texts holds a word an
    instruction shape requires, so `guard_regex_searches` stays 0; without the pre-check it was 7 per
    record. Added when A-30 made w_prompt's recall trust the stamps and the gate stopped seeing B-05
    (AUDIT-A, bisected to 812a0eab)."""
    build = w_prompt(n)

    def run():
        prev = os.environ.get("INSPEXIMUS_KEY_HOME")
        foreign = tempfile.mkdtemp(prefix="foreign-key-home-")
        os.environ["INSPEXIMUS_KEY_HOME"] = foreign
        try:
            build()
        finally:
            if prev is None:
                os.environ.pop("INSPEXIMUS_KEY_HOME", None)
            else:
                os.environ["INSPEXIMUS_KEY_HOME"] = prev
            shutil.rmtree(foreign, ignore_errors=True)
    return run


def w_prompt_archived(n):
    """The prompt hook's read after `--archive`: a FRESH handle opens a store of n captures, 80 % of them
    older than 7 days and moved to segments, and recalls once. The hook pays for the rows it loads, so
    `prompt_rows_loaded` is the hot rows only (n / 5), and `archive_segments_read` is 0: a default read
    opens no segment (AUDIT-B B-25). Timestamps are pinned, so the fixture is the same on every run."""
    from inspeximus import archive as _arch
    t0 = 1790000000.0
    p = _store_path()
    real_time = core.time.time
    try:
        m = Inspeximus(p)
        for i in range(n):
            age = 40 if i % 5 else 1
            core.time.time = (lambda age=age, i=i: t0 - age * 86400.0 + i)
            m.remember(f"ran: make target {i} in the build directory", key=f"cmd:{i}", mtype="episodic")
        m.flush()
        core.time.time = lambda: t0
        _arch.apply(Inspeximus(p), 7, now=t0)
    finally:
        core.time.time = real_time

    def run():
        opened = {"n": 0}
        real_read = _arch._segment_records

        def read(*a, **k):
            opened["n"] += 1
            return real_read(*a, **k)

        _arch._segment_records = read
        try:
            with Counters() as c:
                h = Inspeximus(p)
                h.recall("which make target builds the docs", k=6)
        finally:
            _arch._segment_records = real_read
        run.inner = {**c.as_dict(), "prompt_rows_loaded": len(h._items), "archive_segments_read": opened["n"]}
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


def w_recommit(n):
    """recommit() of n active records whose receipts do not bind them. The receipt sidecar is written once
    (`replace_receipts` 1), not once per receipt: it was n, and on our own 13,142-record store the recommit
    took 22.6 minutes (measured 2026-10-01). `recommitted` must stay n, so a run that skipped the work
    cannot pass as a fast one."""
    p = _store_path()
    m0 = Inspeximus(p, receipts=False)
    for i in range(n):
        m0.remember(f"recommit fixture record {i}", source={"doc": f"d{i % 13}"})
    m0.flush()
    m = Inspeximus(p, receipts=True)

    def run():
        with Counters() as c:
            t0 = time.perf_counter()
            res = m.recommit()
            run.elapsed = time.perf_counter() - t0
        run.inner = {**c.as_dict(), "recommitted": len(res["recommitted"])}
    return run


def w_memreport(n):
    """memory_report over n records, which samples 400 of them as queries. The sampled recalls share one
    candidate pool, so `read_guard_assessments` is n, not 400 x n (AUDIT-B B-07)."""
    p = _store_path()
    m = Inspeximus(p)
    for i in range(n):
        m.remember(f"note {i} on the {('deploy', 'budget', 'release', 'office')[i % 4]} plan for team {i % 37}",
                   source={"doc": f"d{i % 11}"})
    m.flush()

    def run():
        m.memory_report()
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


def _sleep_word(prefix, *ns):
    # Letters only: a digit inside a word is a number to `_value_clash`, and every near-duplicate pair
    # would become a numeric update.
    return prefix + "".join(chr(97 + n // 26) + chr(97 + n % 26) for n in ns)


def w_sleep(n):
    """`sleep()` over n records in topics of 50: each topic is one ripe cluster, a skewed shared word makes
    some members near-duplicates (7 of 8 tokens), and each topic carries a numeric update and a negation.

    `sleep_similarity_calls_pairs` counts the pairs the dedup loop scored. 3.15.1 scored every later
    member of a ripe cluster; the prefix filter scores only members that share one of the rarest
    tokens, which every pair able to reach `dup_threshold` does (AUDIT-B B-16).
    `sleep_similarity_calls_cluster` must not move with that change: it is the witness that clustering
    still scores the same candidates. `sleep_regex_calls` counts calls into compiled patterns during a
    second sleep() on an identical store: the contradiction checks read each text's features once, not
    once per matched pair."""
    def build():
        p = _store_path()
        m = Inspeximus(p)
        topics, per = max(1, n // 50), 46
        i = 0
        for t in range(topics):
            base = " ".join(_sleep_word("tpc", t, j) for j in range(6))
            texts = [f"{base} {_sleep_word('skw', t, int((((k * 0.6180339887) % 1.0) ** 2) * 9))} "
                     f"{_sleep_word('own', t, k)}" for k in range(per)]
            texts += [f"{base} retry limit is 5", f"{base} retry limit is 9",
                      f"{base} nightly cache enabled", f"{base} nightly cache not enabled"]
            for text in texts:
                m.remember(text, value=round(1.0 - i / (4 * n), 6))
                i += 1
        m.flush()
        return p

    p, p2 = build(), build()

    def run():
        calls = {"cluster": 0, "pairs": 0}
        phase = ["pairs"]
        real_sim, real_cluster = core.Inspeximus._similarity, core.Inspeximus._cluster_active

        def sim(self, *a, **k):
            calls[phase[0]] += 1
            return real_sim(self, *a, **k)

        def cluster(self, *a, **k):
            phase[0] = "cluster"
            try:
                return real_cluster(self, *a, **k)
            finally:
                phase[0] = "pairs"

        h = Inspeximus(p)
        core.Inspeximus._similarity, core.Inspeximus._cluster_active = sim, cluster
        try:
            with Counters() as c:
                t0 = time.perf_counter()
                h.sleep()
                run.elapsed = time.perf_counter() - t0
        finally:
            core.Inspeximus._similarity, core.Inspeximus._cluster_active = real_sim, real_cluster
        run.inner = {**c.as_dict(), "sleep_similarity_calls_cluster": calls["cluster"],
                     "sleep_similarity_calls_pairs": calls["pairs"]}
        # Counted apart, on the second store, because sys.setprofile slows everything it watches.
        h2 = Inspeximus(p2)
        with _RegexCalls() as rx:
            h2.sleep()
        run.inner["sleep_regex_calls"] = rx.n
    return run


class _RegexCalls:
    """Count calls into a compiled pattern's `search`, `findall` and `sub` inside a block, through
    sys.setprofile. The contradiction checks in `consolidate_clusters` read both texts again for every
    matched pair: 684,362 negation searches for 342,181 pairs on a copy of a 67k store (AUDIT-B B-16)."""

    NAMES = ("search", "findall", "sub")

    def __enter__(self):
        self.n = 0
        counter = self

        def prof(frame, event, arg):
            if event == "c_call" and getattr(arg, "__name__", "") in counter.NAMES                     and isinstance(getattr(arg, "__self__", None), core.re.Pattern):
                counter.n += 1

        sys.setprofile(prof)
        return self

    def __exit__(self, *exc):
        sys.setprofile(None)
        return False


#: The governance modules the package imported eagerly until 3.15.1. The hook calls none of them, and
#: loading them was about 60 ms of every hook event (AUDIT-B B-19).
GOVERNANCE_MODULES = ("actions", "agent_audit_trail", "cose", "deployer", "erasure_residue", "partitions",
                      "scitt", "subject_rights", "technical_documentation", "timestamp", "trusted_list")


def w_hook_import(root=None):
    """A PreToolUse event for `ls`, run as the real hook process. `hook_imports_numpy` is 1 when that
    process imported numpy. numpy only accelerates semantic recall, and an eager import was about
    0.17 s of every hook event (AUDIT-B B-10). A stand-in numpy first on PYTHONPATH records its own
    import, so the counter reads the same with or without numpy installed; if the stand-in is not
    importable the workload raises instead of reporting a zero it did not measure.

    `hook_imports_governance` counts the GOVERNANCE_MODULES the same process had loaded when it exited
    (AUDIT-B B-19). A `sitecustomize` beside the stand-in numpy registers an exit handler that writes
    `sys.modules` to a file, so the hook still runs as the real `-m` process. `-X importtime` was tried
    first and is not a witness: it does not list a module loaded through `importlib.import_module`,
    which is how the package loads these lazily, so an eager copy counted 1 of 11. If the file is
    missing or does not list inspeximus.core, the workload raises instead of reporting a zero it did
    not measure."""
    import json as _json
    import subprocess
    d = tempfile.mkdtemp()
    fake = os.path.join(d, "fake")
    os.makedirs(os.path.join(fake, "numpy"))
    with open(os.path.join(fake, "numpy", "__init__.py"), "w", encoding="utf-8") as fh:
        fh.write("import os\nopen(os.environ['NUMPY_MARKER'], 'w').write('imported')\n")
    marker = os.path.join(d, "numpy-was-imported")
    modules_out = os.path.join(d, "modules-at-exit.json")
    with open(os.path.join(fake, "sitecustomize.py"), "w", encoding="utf-8") as fh:
        fh.write("import atexit, json, os, sys\n"
                 "def _dump():\n"
                 "    with open(os.environ['GATE_MODULES_OUT'], 'w') as f:\n"
                 "        json.dump(sorted(sys.modules), f)\n"
                 "atexit.register(_dump)\n")
    proj = os.path.join(d, "proj")
    os.makedirs(os.path.join(proj, ".git"))
    env = {k: v for k, v in os.environ.items() if not k.startswith("INSPEXIMUS_")}
    env.update(PYTHONPATH=os.pathsep.join([fake, str(root or ROOT)]), NUMPY_MARKER=marker, GATE_MODULES_OUT=modules_out,
               HOME=d, USERPROFILE=d, INSPEXIMUS_NO_UPDATE_CHECK="1")
    subprocess.run([sys.executable, "-c", "import numpy"], env=env, cwd=proj, check=True)
    if not os.path.exists(marker):
        raise RuntimeError("the stand-in numpy is not importable; hook_imports_numpy would measure nothing")
    ev = {"hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": {"command": "ls"},
          "cwd": proj.replace("\\", "/"), "session_id": "gate"}

    def run():
        for f in (marker, modules_out):
            if os.path.exists(f):
                os.remove(f)
        subprocess.run([sys.executable, "-m", "inspeximus.claude_code"], input=_json.dumps(ev).encode(),
                       env=env, cwd=proj, capture_output=True)
        try:
            with open(modules_out, encoding="utf-8") as fh:
                imported = set(_json.load(fh))
        except OSError:
            imported = set()
        if "inspeximus.core" not in imported:
            raise RuntimeError("the hook process reported no inspeximus.core at exit; "
                               "hook_imports_governance would measure nothing")
        run.inner = {"hook_imports_numpy": int(os.path.exists(marker)),
                     "hook_imports_governance": sum(f"inspeximus.{m}" in imported for m in GOVERNANCE_MODULES)}
    return run


def w_hook_install():
    """`--install` into a temp project, then read what it wrote. `post_tool_use_unscoped` is 1 when the
    PostToolUse entry has no matcher, so every tool call of a session starts a hook process;
    `post_tool_use_extra_tools` counts matcher tools `capture` does not record. Both 0 (AUDIT-B B-02)."""
    import inspeximus.claude_code as cc

    def run():
        proj = tempfile.mkdtemp()
        import contextlib
        import io
        with contextlib.redirect_stdout(io.StringIO()):
            cc.install(cwd=proj)
        with open(os.path.join(proj, ".claude", "settings.json"), encoding="utf-8") as fh:
            post = json.load(fh)["hooks"]["PostToolUse"]
        ours = [e for e in post if any("inspeximus.claude_code" in (h.get("command") or "")
                                       for h in e.get("hooks", []))]
        m = (ours[0].get("matcher") if ours else None) or ""
        run.inner = {"post_tool_use_unscoped": int(not m),
                     "post_tool_use_matcher_tools": len(set(m.split("|"))) if m else 0,
                     "post_tool_use_extra_tools": len(set(m.split("|")) - set(cc._CAPTURED_TOOLS)) if m else 0}
    return run


#: name -> (build, description, backend). The backend is `rows`, `json` or `none` (an arm that opens no
#: store), and `compare` fails when an arm's store files are not in that format (AUDIT-B B-13).
WORKLOADS = {
    "write_n1000":        (lambda: w_write(1000),        "1,000 remembers + flush", "rows"),
    "write_json_n1000":   (lambda: w_write(1000),        "1,000 remembers + flush, JSON store", "json"),
    "recall_n2000_q100":  (lambda: w_recall(2000, 100),  "100 lexical recalls over 2,000 records", "rows"),
    "erase_k200_n2000":   (lambda: w_erase(200, 2000),   "erase 200 subject records among 2,000", "rows"),
    "erase_k20_n2000":    (lambda: w_erase(20, 2000),    "erase 20 subject records among 2,000: dir_listings equals k=200's", "rows"),
    "erase_json_k50_n500": (lambda: w_erase(50, 500),    "erase 50 subject records among 500, JSON store", "json"),
    "session_n500":       (lambda: w_session(500),       "mixed session: 500 writes, 100 recalls, 50 credits, 25 forgets", "rows"),
    "session_json_n500":  (lambda: w_session(500),       "mixed session as session_n500, JSON store", "json"),
    "hook_n2000":         (lambda: w_hook(2000),         "hook PostToolUse: 10 ignored + 3 captured events, 2,000-record store", "rows"),
    "reports_k300":       (lambda: w_reports(300),       "supersession_report + 5 suppressing recalls over 300 keys", "rows"),
    "prompt_n2000":       (lambda: w_prompt(2000),       "fresh handle opens a 2,000-record store and recalls once", "rows"),
    "prompt_unstamped_n2000": (lambda: w_prompt_unstamped(2000),
                           "prompt_n2000 under a key home that cannot verify the read-guard stamps", "rows"),
    "prompt_restamped_n2000": (lambda: w_prompt_restamped(2000),
                           "prompt_n2000 after stamp_read_guards() stamped rows that had no verdict", "rows"),
    "digest_n2000":       (lambda: w_digest(2000),       "state_digest over 2,000 records (the action ledger takes it twice per tool call)", "rows"),
    "prompt_decisions_n600": (lambda: w_prompt_decisions(600),
                           "UserPromptSubmit with a 600-decision store (INSPEXIMUS_DECISION_STORE) beside a 600-capture project store", "rows"),
    "remember_receipted_n2000": (lambda: w_remember_receipted(2000),
                           "5 receipted remembers on a handle holding 2,000 receipts (the MCP server shape)", "rows"),
    "remember_receipted_tail_n2000": (lambda: w_remember_receipted_tail(2000),
                           "remember_receipted_n2000 with the receipt sidecar as a snapshot plus an append-only tail", "rows"),
    "row_rewrite_n2000":  (lambda: w_row_rewrite(2000),  "row store: save 2,000 new rows, then rewrite all of them", "rows"),
    "hook_import":        (lambda: w_hook_import(),      "the hook process for a PreToolUse `ls`: does it import numpy", "none"),
    "memreport_n1000":    (lambda: w_memreport(1000),    "memory_report over 1,000 records: 400 sampled recalls", "rows"),
    "recommit_n2000":     (lambda: w_recommit(2000),     "recommit 2,000 unbound records: the receipt sidecar is written once", "rows"),
    "boundary_n2000":     (lambda: w_boundary(2000),     "session boundary (open, write, close, flush) on a 2,000-record store", "rows"),
    "hook_install":       (lambda: w_hook_install(),     "--install into a temp project: is PostToolUse scoped to what capture records", "none"),
    "prompt_archived_n2000": (lambda: w_prompt_archived(2000), "fresh handle recalls once after --archive moved 80 % of 2,000 captures", "rows"),
    "sleep_n2000":        (lambda: w_sleep(2000),        "sleep() over 2,000 records in 40 ripe topic clusters", "rows"),
}


# ── measurement ────────────────────────────────────────────────────────────────────────────────────

def measure():
    out = {}
    for name, (build, desc, backend) in WORKLOADS.items():
        # A heartbeat per arm: a full run takes minutes, and a silent one cannot be told from a wedged one.
        print(f"  measuring {name} ({backend})", file=sys.stderr, flush=True)
        with _backend(backend):
            _ARM_STORES.clear()
            run = build()                               # fixture built OUTSIDE the counted region
            with Counters() as c:
                t0 = time.perf_counter()
                run()
                first = time.perf_counter() - t0
            counters = getattr(run, "inner", None) or c.as_dict()
            observed = _observed_backend(_ARM_STORES)

            times = [getattr(run, "elapsed", first)]
            for _ in range(REPEATS - 1):
                r = build()
                t0 = time.perf_counter()
                r()
                times.append(getattr(r, "elapsed", time.perf_counter() - t0))

        out[name] = {"desc": desc, "backend": {"declared": backend, "observed": observed},
                     "counters": counters,
                     "seconds_median": round(statistics.median(times), 4),
                     "seconds_min": round(min(times), 4), "seconds_max": round(max(times), 4)}
    return out


def _backend_misses(now):
    """Arms whose store files are not in the format the arm names."""
    return [f"{name}: names backend {w['backend']['declared']} but its stores are {w['backend']['observed']}"
            for name, w in now.items()
            if "backend" in w and w["backend"]["observed"] != w["backend"]["declared"]]


def _twins(now):
    """{twin: arm} for every arm whose `desc` starts with another arm's name followed by a space."""
    out = {}
    for name, w in now.items():
        desc = str(w.get("desc", ""))
        for other in now:
            if other != name and desc.startswith(other + " "):
                out[name] = other
    return out


def _twin_misses(now):
    """Twins with the same counted work whose medians are more than TWIN_TIME_FACTOR apart."""
    misses = []
    for twin, arm in sorted(_twins(now).items()):
        a, b = now[arm], now[twin]
        keys = (set(a["counters"]) | set(b["counters"])) - TWIN_SETUP_COUNTERS
        if any(a["counters"].get(k) != b["counters"].get(k) for k in keys):
            continue                                  # different counted work: the counters explain the time
        ta, tb = a["seconds_median"], b["seconds_median"]
        lo, hi = min(ta, tb), max(ta, tb)
        if lo > 0 and hi > lo * TWIN_TIME_FACTOR:
            misses.append(f"{twin} vs {arm}: the same counted work in {tb:.3f}s and {ta:.3f}s "
                          f"({hi / lo:.1f}x, over {TWIN_TIME_FACTOR:g}x): work no counter sees. Count it "
                          f"before recording a baseline.")
    return misses


# ── the gate ───────────────────────────────────────────────────────────────────────────────────────

def compare(base, now):
    """Counters are exact and gate the build. Time only alarms past TIME_ALARM_FACTOR, and twin arms with
    the same counted work must stay within TWIN_TIME_FACTOR of each other."""
    fail, warn = _backend_misses(now) + _twin_misses(now), []
    for name, b in base.items():
        n = now.get(name)
        if n is None:
            fail.append(f"{name}: workload MISSING from this run -- a gate that lost its workload is not a gate")
            continue
        if "backend" in b and b["backend"]["declared"] != n.get("backend", {}).get("declared"):
            fail.append(f"{name}: backend changed from {b['backend']['declared']} to "
                        f"{n.get('backend', {}).get('declared')} without a new baseline")
        for key, bv in b["counters"].items():
            nv = n["counters"].get(key)
            if nv is None:
                fail.append(f"{name}.{key}: counter disappeared (instrumentation detached?)")
            elif key in BANDED_BYTES:
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
    with _isolated_key_home():
        now = measure()

    if cmd == "record":
        misses = _backend_misses(now)
        if misses:
            print("refusing to record: an arm did not run on the backend it names", file=sys.stderr)
            for m in misses:
                print(f"  {m}", file=sys.stderr)
            return 1
        twins = _twin_misses(now)
        if twins:
            print("refusing to record: twin arms differ in time but not in counted work", file=sys.stderr)
            for m in twins:
                print(f"  {m}", file=sys.stderr)
            return 1
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
