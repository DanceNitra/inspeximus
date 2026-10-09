"""The performance gate must go red when work grows — proven, not assumed.

A gate nobody has watched fail is a gate nobody knows works. This repo learned that twice in one day: the
audit job's falsification control had been aimed at a string that no longer existed and could not fire for
a day, and the `check_code` build gate reported a clean verdict on a store it could not read. So before
`perf/gate.py` is allowed to keep anyone honest, it has to be shown failing.

Four properties, in the order they matter:

1. it PASSES on the current tree (else every red below is a verifier that rejects everything);
2. it FAILS when the O(k^2) tombstone write is reintroduced — the actual regression it was built for;
3. it does NOT fail on the byte drift that happens between two identical runs (no false alarms);
4. it FAILS when a workload disappears, because losing an arm silently is how a gate stops gating.
"""
import copy
import json
import os
import sys
import tempfile

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "perf"))
sys.path.insert(0, ROOT)

# A plain import on purpose: perf/gate.py ships in this repo and needs nothing optional, so an
# importorskip here would be a guard that can never fire -- and tools/skip_census.py counts any module
# carrying one as hidden from the base CI job, inflating its pin with tests that are not hidden at all.
import gate  # noqa: E402
import inspeximus.core as core  # noqa: E402


@pytest.fixture()
def baseline():
    with open(os.path.join(ROOT, "perf", "baseline.json"), encoding="utf-8") as f:
        return json.load(f)


def _erase_counters():
    """Run only the erase workload and return its counters — the arm the regression lives in."""
    run = gate.w_erase(60, 300)      # returns the callable; calling it runs the workload
    run()
    return run.inner


def test_control_the_gate_passes_on_an_unchanged_run(baseline):
    """Without this, every assertion below is satisfied by a checker that always fails."""
    now = copy.deepcopy(baseline)
    fail, _ = gate.compare(baseline, now)
    assert fail == [], fail


def test_it_fails_when_the_quadratic_tombstone_write_comes_back(baseline):
    """THE ONE IT EXISTS FOR. Before 1.88.1 the sidecar was rewritten once per tombstone.

    Simulated by flushing inside `_emit_tombstone` again, exactly as the old code did, and measured
    through the same counters the gate reads — not asserted from a table.
    """
    good = _erase_counters()
    assert good["replace_tombstones"] == 1, ("fixture error: the batched write is already broken", good)

    real = core.Inspeximus._emit_tombstone

    def per_tombstone(self, *a, **k):
        k.pop("defer", None)
        t = real(self, *a, defer=True, **k)
        self._flush_tombstones()          # the pre-1.88.1 behaviour
        return t

    core.Inspeximus._emit_tombstone = per_tombstone
    try:
        bad = _erase_counters()
    finally:
        core.Inspeximus._emit_tombstone = real

    # k + 1: sixty per-tombstone flushes from the reintroduced defect, plus the one batch flush that the
    # current forget() still performs at the end. The number that matters is that it now TRACKS k, where
    # the fixed code is a constant 1 no matter how many records are erased.
    assert bad["replace_tombstones"] == 61, (
        "reintroducing the per-tombstone write did not move the counter, so the counter is not measuring "
        f"what the gate claims: {bad}")

    base = {"erase": {"counters": good, "seconds_median": 0.1}}
    now = {"erase": {"counters": bad, "seconds_median": 0.1}}
    fail, _ = gate.compare(base, now)
    assert any("replace_tombstones" in f for f in fail), (
        f"the counter moved 1 -> 61 and the gate stayed green: {fail}")


def test_it_does_not_fire_on_the_byte_drift_between_two_identical_runs():
    """No false alarms. Timestamps vary in digit count, so serialized_bytes moves ~0.003% run to run.

    A gate that reddens on that gets muted within a week, and a muted gate is worse than none.
    """
    base = {"w": {"counters": {"replace_store": 1, "serialized_bytes": 147_642_011}, "seconds_median": 1.0}}
    for delta in (4_479, -4_479, 1_476_420):          # observed drift, its mirror, and a full 1%
        now = copy.deepcopy(base)
        now["w"]["counters"]["serialized_bytes"] = 147_642_011 + delta
        fail, _ = gate.compare(base, now)
        assert fail == [], (f"the gate reddened on a {delta / 147_642_011 * 100:+.3f}% byte change, "
                            f"inside its stated +/-{gate.BYTES_BAND * 100:.0f}% band: {fail}")


def test_it_does_fire_when_serialized_volume_grows_past_the_band():
    """The band must not be so wide that it absorbs what it exists to surface."""
    base = {"w": {"counters": {"replace_store": 1, "serialized_bytes": 147_642_011}, "seconds_median": 1.0}}
    now = copy.deepcopy(base)
    now["w"]["counters"]["serialized_bytes"] = int(147_642_011 * 1.10)      # +10%
    fail, _ = gate.compare(base, now)
    assert any("serialized_bytes" in f for f in fail), fail


def test_it_fails_when_a_workload_disappears(baseline):
    """A gate that quietly runs three of its four arms reports green for work it never did."""
    now = copy.deepcopy(baseline)
    dropped = sorted(now)[0]
    del now[dropped]
    fail, _ = gate.compare(baseline, now)
    assert any(dropped in f and "MISSING" in f for f in fail), fail


def test_every_recorded_arm_actually_does_something(baseline):
    """An arm whose counters are all zero AND finishes instantly cannot go red.

    The first version of this gate had one: a `consolidate` workload that flagged everything as a hub,
    saved nothing, and finished in 3 ms with every counter at zero. It was replaced. This keeps the next
    one from being added without anyone noticing.
    """
    dead = [name for name, w in baseline.items()
            if not any(w["counters"].values()) and w["seconds_median"] < 0.05]
    assert dead == [], f"workload(s) with no measurable work at all: {dead}"


def test_it_fails_when_the_hook_opens_the_store_for_a_tool_it_ignores():
    """AUDIT-B B-01. A PostToolUse event for Read, Grep or Glob captures nothing, and before the fix it
    still loaded the whole store: 5.2 s per event on a 67,165-record hook store.

    Reintroduced by adding the ignored tools to `_CAPTURED_TOOLS`, which puts the open back in front of
    them and changes nothing else, and measured through the counter the gate reads.
    """
    import inspeximus.claude_code as cc
    run = gate.w_hook(200)
    with gate.Counters() as c:
        run()
    good = c.as_dict()
    captured = sum(1 for t in gate.HOOK_EVENTS if t in cc._CAPTURED_TOOLS)
    assert good["store_loads"] == captured == 3, ("fixture error: the fixed hook does not load once per "
                                                  "captured event", good)

    real = cc._CAPTURED_TOOLS
    cc._CAPTURED_TOOLS = real + ("Read", "Grep", "Glob")
    try:
        run = gate.w_hook(200)
        with gate.Counters() as c:
            run()
        bad = c.as_dict()
    finally:
        cc._CAPTURED_TOOLS = real
    assert bad["store_loads"] == len(gate.HOOK_EVENTS) == 13, (
        f"reopening the store for ignored tools did not move the counter: {bad}")

    base = {"hook": {"counters": good, "seconds_median": 0.1}}
    now = {"hook": {"counters": bad, "seconds_median": 0.1}}
    fail, _ = gate.compare(base, now)
    assert any("store_loads" in f for f in fail), f"store_loads moved 3 -> 13 and the gate stayed green: {fail}"


def test_it_fails_when_opening_a_store_infers_types_it_discards():
    """AUDIT-B B-04. `setdefault("mtype", _infer_type(text))` ran two regex searches over every record at
    every open and discarded the result for every record that had a type: 1.78 s of a 5.51 s open at
    67,165 records.

    Reintroduced by evaluating the inference before the real normaliser runs, which is what the eager
    default did, and measured through the counter the gate reads (the hook workload opens its store 3
    times, and every record in it carries a type).
    """
    run = gate.w_hook(200)
    with gate.Counters() as c:
        run()
    good = c.as_dict()
    assert good["store_loads"] == 3 and good["type_inferences"] == 0, (
        "fixture error: the hook workload should open 3 times and infer nothing", good)

    real = core.Inspeximus._normalise_loaded

    def eager(r):
        core._infer_type(r.get("text") or "")
        return real(r)

    core.Inspeximus._normalise_loaded = staticmethod(eager)
    try:
        run = gate.w_hook(200)
        with gate.Counters() as c:
            run()
        bad = c.as_dict()
    finally:
        core.Inspeximus._normalise_loaded = staticmethod(real)
    assert bad["type_inferences"] >= 3 * 200, f"the eager inference did not move the counter: {bad}"

    base = {"hook": {"counters": good, "seconds_median": 0.1}}
    now = {"hook": {"counters": bad, "seconds_median": 0.1}}
    fail, _ = gate.compare(base, now)
    assert any("type_inferences" in f for f in fail), f"type_inferences grew and the gate stayed green: {fail}"


def test_it_fails_when_the_value_reports_scan_the_store_once_per_key():
    """AUDIT-B B-06. `_short_values_suppression_cannot_see` and `_retired_values` called
    `_current_active` once per key, a full scan each: 6,674 scans and 40.5 s (profiled) inside
    supersession_report on a 10,934-record store.

    Reintroduced by answering the one-pass index with one `_current_active` call per lookup, which is
    what the loops did, and measured through the counter the gate reads.
    """
    class ScanPerKey(dict):
        def __init__(self, store):
            super().__init__()
            self.store = store

        def get(self, k, default=None):
            r = core.Inspeximus._current_active(self.store, k)
            return default if r is None else r

    run = gate.w_reports(30)
    with gate.Counters() as c:
        run()
    good = c.as_dict()
    assert good["current_active_scans"] == 0, ("fixture error: the reports already scan per key", good)

    real = core.Inspeximus._current_active_index
    core.Inspeximus._current_active_index = lambda self: ScanPerKey(self)
    try:
        run = gate.w_reports(30)
        with gate.Counters() as c:
            run()
        bad = c.as_dict()
    finally:
        core.Inspeximus._current_active_index = real
    assert bad["current_active_scans"] >= 6 * 30, f"per-key scanning did not move the counter: {bad}"

    base = {"reports": {"counters": good, "seconds_median": 0.1}}
    now = {"reports": {"counters": bad, "seconds_median": 0.1}}
    fail, _ = gate.compare(base, now)
    assert any("current_active_scans" in f for f in fail), f"the scans grew and the gate stayed green: {fail}"


def test_it_fails_when_the_read_guard_searches_text_that_cannot_match(tmp_path, monkeypatch):
    """AUDIT-B B-05. The read guard ran seven regex searches over every clean record in every new
    process: 8.1 s of a 12.0 s hook recall at 67,165 records.

    Reintroduced by emptying the word pre-check, which is exactly the old behaviour (every pattern
    searched), and measured through the counter the gate reads.

    UNDER A KEY HOME THAT CANNOT VERIFY THE STAMPS. Since A-30 a record carries a read-guard verdict
    stamped under the store's key, and a fresh handle with that key trusts it, so the recall searched
    nothing whatever the pre-check did and this test could not fail. The recall below runs as a fresh
    process meets a store it cannot vouch for, which is the path the pre-check protects.
    """
    def unstamped(run):
        def go():
            with monkeypatch.context() as mp:
                mp.setenv("INSPEXIMUS_KEY_HOME", str(tmp_path / f"foreign-{len(os.listdir(tmp_path))}"))
                run()
        return go

    run = unstamped(gate.w_prompt(200))
    with gate.Counters() as c:
        run()
    good = c.as_dict()
    assert good["store_loads"] == 1 and good["guard_regex_searches"] == 0, (
        "fixture error: a fresh recall over clean text should load once and search nothing", good)

    real = core._SHAPE_REQUIRES
    core._SHAPE_REQUIRES = {}
    try:
        run = unstamped(gate.w_prompt(200))
        with gate.Counters() as c:
            run()
        bad = c.as_dict()
    finally:
        core._SHAPE_REQUIRES = real
    assert bad["guard_regex_searches"] >= 7 * 200, f"searching every text did not move the counter: {bad}"

    base = {"prompt": {"counters": good, "seconds_median": 0.1}}
    now = {"prompt": {"counters": bad, "seconds_median": 0.1}}
    fail, _ = gate.compare(base, now)
    assert any("guard_regex_searches" in f for f in fail), f"the searches grew and the gate stayed green: {fail}"


def test_it_fails_when_a_full_row_save_goes_quadratic(monkeypatch):
    """AUDIT-B B-15. The full-diff save tested every id for membership in the `added` and `changed`
    LISTS, so a save that adds or rewrites every row was O(rows^2): 0.34 s at 8,000 rows and about 3x
    per doubling. Reintroduced through the real save path: `set` inside sqlite_store is replaced by a
    list that answers `in` by scanning, which is what the membership test did before the fix."""
    from inspeximus import sqlite_store as ss

    class ListSet(list):
        def __or__(self, other):
            return ListSet(list(self) + list(other))

    run = gate.w_row_rewrite(300)
    run()
    good = run.inner
    assert good["row_id_comparisons"] <= 2 * 300, ("fixture error: the save already scans lists", good)

    monkeypatch.setattr(ss, "set", ListSet, raising=False)
    run = gate.w_row_rewrite(300)
    run()
    bad = run.inner
    monkeypatch.undo()
    assert bad["row_id_comparisons"] >= 300 * 299 // 2, f"list membership did not move the counter: {bad}"

    base = {"rows": {"counters": good, "seconds_median": 0.1}}
    now = {"rows": {"counters": bad, "seconds_median": 0.1}}
    fail, _ = gate.compare(base, now)
    assert any("row_id_comparisons" in f for f in fail), f"the comparisons grew and the gate stayed green: {fail}"


def test_it_fails_when_the_hook_imports_numpy_again(tmp_path):
    """AUDIT-B B-10. core.py imported numpy at module load, so every hook event paid about 0.17 s for an
    optional accelerator it never used. Reintroduced in a copy of the package whose core imports numpy
    eagerly again, run as the real hook process, and measured through the counter the gate reads."""
    import shutil
    run = gate.w_hook_import()
    run()
    good = run.inner
    assert good["hook_imports_numpy"] == 0, ("fixture error: the hook already imports numpy", good)

    pkg = tmp_path / "pkg"
    shutil.copytree(os.path.join(ROOT, "inspeximus"), pkg / "inspeximus",
                    ignore=shutil.ignore_patterns("__pycache__"))
    core_py = pkg / "inspeximus" / "core.py"
    src = core_py.read_text(encoding="utf-8")
    assert src.count("_np = _NP_UNLOADED\n") == 1, "fixture error: the lazy numpy line moved"
    core_py.write_text(src.replace("_np = _NP_UNLOADED\n", "import numpy as _np\n"), encoding="utf-8")
    run = gate.w_hook_import(root=pkg)
    run()
    bad = run.inner
    assert bad["hook_imports_numpy"] == 1, f"the eager import did not move the counter: {bad}"

    fail, _ = gate.compare({"h": {"counters": good, "seconds_median": 0.1}},
                           {"h": {"counters": bad, "seconds_median": 0.1}})
    assert any("hook_imports_numpy" in f for f in fail), f"numpy came back and the gate stayed green: {fail}"


def test_it_fails_when_a_session_boundary_updates_every_unmoved_row():
    """AUDIT-B B-09. A full-diff save issued an order UPDATE for every unmoved row: 67,165 no-op
    statements per session boundary on a real hook store. Reintroduced through the real save path by
    hiding the stored order from the filter (its SELECT returns no rows), so every unmoved row is updated
    again, exactly as before the fix, and measured through the counter the gate reads."""
    run = gate.w_boundary(300)
    with gate.Counters() as c:
        run()
    good = c.as_dict()
    assert good["order_updates"] == 0, ("fixture error: the boundary already updates rows", good)

    class HidesOrder:
        def __init__(self, con):
            object.__setattr__(self, "_con", con)

        def execute(self, sql, *a):
            if sql.strip().upper().startswith("SELECT ID, ORD FROM RECORDS"):
                return self._con.execute("SELECT id, ord FROM records WHERE 0")
            return self._con.execute(sql, *a)

        def __getattr__(self, name):
            return getattr(self._con, name)

    real = core._rows._connect
    core._rows._connect = lambda path: HidesOrder(real(path))
    try:
        run = gate.w_boundary(300)
        with gate.Counters() as c:
            run()
        bad = c.as_dict()
    finally:
        core._rows._connect = real
    assert bad["order_updates"] >= 300, f"updating every unmoved row did not move the counter: {bad}"

    fail, _ = gate.compare({"b": {"counters": good, "seconds_median": 0.1}},
                           {"b": {"counters": bad, "seconds_median": 0.1}})
    assert any("order_updates" in f for f in fail), f"the updates grew and the gate stayed green: {fail}"


def test_it_fails_when_an_erasure_rebuilds_the_id_map_per_record():
    """AUDIT-B B-17. `_erasure_collisions` built a dict of the whole store once per matched record:
    57.3 s for 1,666 records of a 50,000-record store. Reintroduced by doing that per-record read again
    in front of the real function, and measured through the counter the gate reads."""
    run = gate.w_erase(60, 300)
    run()
    good = run.inner
    assert good["erase_items_reads"] < 60, ("fixture error: the erasure already reads per record", good)

    real = core.Inspeximus._erasure_collisions

    def per_record(self, subject, cand, subj_ids):
        for rid in subj_ids:
            {r["id"]: r for r in self.items}.get(rid)          # the pre-fix read, once per matched id
        return real(self, subject, cand, subj_ids)

    core.Inspeximus._erasure_collisions = per_record
    try:
        run = gate.w_erase(60, 300)
        run()
        bad = run.inner
    finally:
        core.Inspeximus._erasure_collisions = real
    assert bad["erase_items_reads"] >= good["erase_items_reads"] + 60, f"the counter did not move: {bad}"

    fail, _ = gate.compare({"e": {"counters": good, "seconds_median": 0.1}},
                           {"e": {"counters": bad, "seconds_median": 0.1}})
    assert any("erase_items_reads" in f for f in fail), f"the reads grew and the gate stayed green: {fail}"


def test_it_fails_when_the_residue_scan_lowercases_per_record():
    """AUDIT-B B-18. scan_records lowercased every erased value once per surviving record and field:
    2,008,775 calls and 9.58 s of a 12.8 s erasure on a 10,934-record store. Reintroduced by replaying
    the pre-fix inner loop in front of the real scan, and measured through the counter the gate reads."""
    from inspeximus import erasure_residue as er
    run = gate.w_erase(60, 300)
    run()
    good = run.inner
    assert good["erase_lower_calls"] < 60 * 300, ("fixture error: the scan already lowercases per record", good)

    real = er.scan_records

    def per_record(records, values, max_pairs=2_000_000):
        vals = [v for v in {str(v).strip() for v in (values or [])} if len(v) >= 4]
        for r in list(records or []):
            for field in ("text", "object"):
                blob = r.get(field)
                if isinstance(blob, str) and blob:
                    low = blob.lower()
                    for v in vals:
                        _ = v.lower() in low                  # the pre-fix inner loop
        return real(records, values, max_pairs)

    er.scan_records = per_record
    try:
        run = gate.w_erase(60, 300)
        run()
        bad = run.inner
    finally:
        er.scan_records = real
    assert bad["erase_lower_calls"] >= good["erase_lower_calls"] + 60 * 300, f"the counter did not move: {bad}"

    fail, _ = gate.compare({"e": {"counters": good, "seconds_median": 0.1}},
                           {"e": {"counters": bad, "seconds_median": 0.1}})
    assert any("erase_lower_calls" in f for f in fail), f"the calls grew and the gate stayed green: {fail}"


def test_it_fails_when_opening_a_store_serialises_every_row_again():
    """AUDIT-B B-08. The save baseline was built at open by serialising every record: 1.27 s of a 5.51 s
    open at 67,165 records, paid by opens that never save. Reintroduced by building it with `snapshot`
    again, which is exactly what the open did, and measured through the counter the gate reads."""
    run = gate.w_prompt(200)
    with gate.Counters() as c:
        run()
    good = c.as_dict()
    assert good["store_loads"] == 1 and good["row_serializations"] == 0, (
        "fixture error: a fresh open already serialises rows", good)

    real = core._rows.snapshot_from_docs
    core._rows.snapshot_from_docs = lambda items, docs, reuse, keep_vec=True: core._rows.snapshot(items, keep_vec)
    try:
        run = gate.w_prompt(200)
        with gate.Counters() as c:
            run()
        bad = c.as_dict()
    finally:
        core._rows.snapshot_from_docs = real
    assert bad["row_serializations"] >= 200, f"serialising every row did not move the counter: {bad}"

    fail, _ = gate.compare({"p": {"counters": good, "seconds_median": 0.1}},
                           {"p": {"counters": bad, "seconds_median": 0.1}})
    assert any("row_serializations" in f for f in fail), f"the serialisations grew and the gate stayed green: {fail}"


def test_it_fails_when_the_post_tool_use_hook_loses_its_matcher():
    """AUDIT-B B-02. Without a matcher the PostToolUse hook starts a process for every tool call, and
    `capture` returns at once for all but four tools. Reintroduced by removing the PostToolUse entry
    from _EVENT_HOOK, which is exactly the pre-fix installer, and measured through the gate's counter."""
    import inspeximus.claude_code as cc
    run = gate.w_hook_install()
    run()
    good = run.inner
    assert good == {"post_tool_use_unscoped": 0, "post_tool_use_matcher_tools": 4,
                    "post_tool_use_extra_tools": 0}, good

    real = dict(cc._EVENT_HOOK)
    cc._EVENT_HOOK.pop("PostToolUse")
    try:
        run = gate.w_hook_install()
        run()
        bad = run.inner
    finally:
        cc._EVENT_HOOK.clear()
        cc._EVENT_HOOK.update(real)
    assert bad["post_tool_use_unscoped"] == 1, bad
    fail, _ = gate.compare({"h": {"counters": good, "seconds_median": 0.1}},
                           {"h": {"counters": bad, "seconds_median": 0.1}})
    assert any("post_tool_use_unscoped" in f for f in fail), fail


def test_it_fails_when_a_session_boundary_reconciles_the_store_twice():
    """AUDIT-B B-20. close_session requested a full reconcile even as a write=False preview, and
    open_session runs that preview, so every boundary serialised and compared the whole store twice.
    Reintroduced by setting the flag after every preview, which is what the pre-fix code did, and
    measured through the counter the gate reads."""
    run = gate.w_boundary(300)
    with gate.Counters() as c:
        run()
    good = c.as_dict()
    assert good["full_diff_saves"] == 1, ("fixture error: the boundary does not pay exactly one", good)

    real = core.Inspeximus.close_session

    def flagging(self, *a, write=True, **k):
        out = real(self, *a, write=write, **k)
        if not write:
            self._full_reconcile = True
        return out

    core.Inspeximus.close_session = flagging
    try:
        run = gate.w_boundary(300)
        with gate.Counters() as c:
            run()
        bad = c.as_dict()
    finally:
        core.Inspeximus.close_session = real
    assert bad["full_diff_saves"] == 2, f"the preview flag did not move the counter: {bad}"
    fail, _ = gate.compare({"b": {"counters": good, "seconds_median": 0.1}},
                           {"b": {"counters": bad, "seconds_median": 0.1}})
    assert any("full_diff_saves" in f for f in fail), fail


def test_it_fails_when_memory_report_rebuilds_the_pool_per_query(monkeypatch):
    """AUDIT-B B-07. memory_report's sampled recalls rebuilt the candidate pool, assessing every record
    once per query. Reintroduced by making the shared pool a no-op, which is what the pre-fix code did,
    and measured through the counter the gate reads."""
    import contextlib
    run = gate.w_memreport(150)
    with gate.Counters() as c:
        run()
    good = c.as_dict()
    assert good["read_guard_assessments"] == 150, ("fixture error: the report did not assess each record once", good)

    monkeypatch.setattr(core, "_shared_recall_pool", lambda store: contextlib.nullcontext())
    # 3.18: the recall index also keeps one pool across the report's queries, so with the shared pool gone and the index
    # on, each record is assessed once per query until the query that builds the entry (_RECALL_IX_BUILD_AFTER for a
    # handle no process holds, as the report's is), and the rest reuse it. The pre-fix code had neither;
    # INSPEXIMUS_RECALL_INDEX=0 turns the index off, and then the shared pool is what holds the count.
    run = gate.w_memreport(150)
    with gate.Counters() as c:
        run()
    assert c.as_dict()["read_guard_assessments"] == core._RECALL_IX_BUILD_AFTER * 150,         "the recall index no longer keeps the pool across queries"
    monkeypatch.setattr(core, "_RECALL_INDEX_ON", False)
    run = gate.w_memreport(150)
    with gate.Counters() as c:
        run()
    bad = c.as_dict()
    assert bad["read_guard_assessments"] == 150 * 150, f"a pool per query did not move the counter: {bad}"
    fail, _ = gate.compare({"r": {"counters": good, "seconds_median": 0.1}},
                           {"r": {"counters": bad, "seconds_median": 0.1}})
    assert any("read_guard_assessments" in f for f in fail), fail


def test_it_fails_when_the_package_imports_its_governance_modules_eagerly_again(tmp_path):
    """AUDIT-B B-19. The package __init__ imported eleven governance modules, so every hook event loaded
    them although the hook calls none. Reintroduced in a copy of the package whose __init__ imports them
    eagerly again, run as the real hook process, and measured through the counter the gate reads."""
    import shutil
    run = gate.w_hook_import()
    run()
    good = run.inner
    assert good["hook_imports_governance"] == 0, ("fixture error: the hook already imports them", good)

    pkg = tmp_path / "pkg"
    shutil.copytree(os.path.join(ROOT, "inspeximus"), pkg / "inspeximus",
                    ignore=shutil.ignore_patterns("__pycache__"))
    init = pkg / "inspeximus" / "__init__.py"
    init.write_text(init.read_text(encoding="utf-8") + "\nfrom . import "
                    + ", ".join(gate.GOVERNANCE_MODULES) + "\n", encoding="utf-8")
    run = gate.w_hook_import(root=pkg)
    run()
    bad = run.inner
    assert bad["hook_imports_governance"] == len(gate.GOVERNANCE_MODULES), f"the eager imports did not move the counter: {bad}"

    fail, _ = gate.compare({"h": {"counters": good, "seconds_median": 0.1}},
                           {"h": {"counters": bad, "seconds_median": 0.1}})
    assert any("hook_imports_governance" in f for f in fail), f"the eager imports came back and the gate stayed green: {fail}"


def test_the_unstamped_arm_sees_the_b05_regression_the_stamped_arm_cannot():
    """Since A-30 the stamped arm (prompt_n2000) trusts the read-guard stamps, so it runs no regex search
    whether or not B-05's word pre-check exists. The unstamped arm recalls under a key home that cannot
    verify them. Reintroduce B-05 by emptying the pre-check: the unstamped arm's searches jump from 0,
    and the stamped arm's stay at 0, which is why the gate carries both."""
    def searches(build):
        run = build(200)
        with gate.Counters() as c:
            run()
        return c.as_dict()["guard_regex_searches"]
    assert searches(gate.w_prompt_unstamped) == 0, "control: with the pre-check, no search"
    real = core._SHAPE_REQUIRES
    core._SHAPE_REQUIRES = {}
    try:
        unstamped, stamped = searches(gate.w_prompt_unstamped), searches(gate.w_prompt)
    finally:
        core._SHAPE_REQUIRES = real
    assert unstamped >= 200 * 7, f"the unstamped arm did not see B-05: {unstamped} searches"
    assert stamped == 0, f"control: the stamped arm is blind to B-05 by design ({stamped})"


def _key_lookups(build):
    run = build(200)
    with gate.Counters() as c:
        run()
    return c.as_dict()["guard_key_lookups"]


def test_the_unstamped_arm_looks_the_read_guard_key_up_once():
    """A-45: under a key home with no key, a fresh handle looks the key up once, not once per record."""
    assert _key_lookups(gate.w_prompt_unstamped) <= 1


def test_it_fails_when_a_missing_key_is_looked_up_per_record_again(baseline, monkeypatch):
    """Restore 3.15.4's lookup, which cached only a found key: the unstamped arm's lookups grow from one
    to one per record, and `compare` names the counter."""
    good = _key_lookups(gate.w_prompt_unstamped)
    monkeypatch.setattr(core.Inspeximus, "_guard_key",
                        lambda self, create=False: self.__dict__.get("_guard_key_bytes")
                        or self._load_guard_key(create))
    bad = _key_lookups(gate.w_prompt_unstamped)
    assert bad >= 200, f"the regression did not reproduce: {bad} lookups"
    arm = copy.deepcopy(baseline["prompt_unstamped_n2000"])
    base = {"prompt_unstamped_n2000": {**arm, "counters": {**arm["counters"], "guard_key_lookups": good}}}
    now = {"prompt_unstamped_n2000": {**arm, "counters": {**arm["counters"], "guard_key_lookups": bad}}}
    fail, _ = gate.compare(base, now)
    assert any("guard_key_lookups" in f for f in fail), fail


def test_a_json_save_reads_the_store_once_and_a_row_save_never():
    """A-37's writer guard hashes the store file on a JSON save whose stat signature has not moved. The
    counter pins that cost at one read per save (none for the first save, which has no file yet), and at
    zero for the row store, whose guard is per row."""
    def reads(backend):
        with gate._backend(backend):
            run = gate.w_write(50)
            with gate.Counters() as c:
                run()
        return c.as_dict()["store_hash_reads"]
    assert reads("json") == 49
    assert reads("rows") == 0


def test_it_fails_when_a_json_save_reads_the_store_twice(baseline):
    """The counter is gated: a second read per save shows as growth against the recorded arm."""
    base = copy.deepcopy(baseline)
    with gate._backend("json"):
        run = gate.w_write(50)
        with gate.Counters() as c:
            run()
        good = c.as_dict()
    with gate._backend("json"):
        run = gate.w_write(50)
        with gate.Counters() as c:
            counted = core.Inspeximus._disk_hash          # the counter's wrapper: inject INSIDE it
            core.Inspeximus._disk_hash = lambda self: (counted(self), counted(self))[1]
            run()                                         # Counters.__exit__ puts the real method back
        bad = c.as_dict()
    assert bad["store_hash_reads"] == 2 * good["store_hash_reads"]
    base["write_json_n1000"]["counters"] = {**base["write_json_n1000"]["counters"], **good}
    now = {"write_json_n1000": {**base["write_json_n1000"], "counters": {**base["write_json_n1000"]["counters"], **bad}}}
    fail, _ = gate.compare({"write_json_n1000": base["write_json_n1000"]}, now)
    assert any("store_hash_reads" in f for f in fail), fail
