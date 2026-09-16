"""What a Hermes turn pays for recall, before and after the background queue.

THE CLAIM UNDER TEST is the one in the adapter's docstring: `queue_prefetch` turns the hot path into
a dictionary read. That is a performance claim, so it needs a number rather than an argument, and it
needs the honest cases beside the flattering one.

Five arms, interleaved at every store size so a busy machine cannot favour one:

    shipped         prefetch() as the library ships it: recall inline, no speculation.
    spec-hit        the speculating variant, asked the SAME query it was queued with.
    spec-miss       queued for a different query and already finished, so it falls back.
    spec-racing     queued for the same query and still running when the turn asks.
    spec-host-flow  WHAT HERMES ACTUALLY DOES. run_agent.py:897 queues the text of the turn that
                    just ENDED; turn_context.py:762 then prefetches the NEW user message at the
                    start of the next turn. The two queries differ, so the cache cannot hit, and
                    the useless background recall is still running while the turn does its own.
                    Serving the queued result anyway is not an option: it would inject the previous
                    question's memories.

The speculating variant lives in this file rather than in the library, because 2.27.3 shipped it
and 2.27.4 took it out. The arm that decided it is the last one.

A control runs at the end: the same measurement with a store of ONE record, where every arm must be
indistinguishable. If the control shows a spread, the harness is measuring itself.

Run: python -X utf8 probes/what_a_turn_pays_for_recall_before_and_after_the_queue.py
"""
from __future__ import annotations

import json
import os
import pathlib
import statistics
import sys
import tempfile
import threading
import time
import types
from abc import ABC, abstractmethod

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

SIZES = (100, 1_000, 10_000)
TURNS = 40
QUERIES = ("the staging database", "the release branch", "the deploy window", "the review policy")


def _install_host_stub():
    """The adapter builds its class against the host's ABC, so the probe supplies a stand-in."""

    class MemoryProvider(ABC):
        @property
        @abstractmethod
        def name(self): ...
        @abstractmethod
        def is_available(self): ...
        @abstractmethod
        def initialize(self, session_id, **kwargs): ...
        @abstractmethod
        def get_tool_schemas(self): ...

    pkg, mod = types.ModuleType("agent"), types.ModuleType("agent.memory_provider")
    mod.MemoryProvider = MemoryProvider
    pkg.memory_provider = mod
    sys.modules["agent"], sys.modules["agent.memory_provider"] = pkg, mod


def _provider(tmp: str, n: int):
    from inspeximus.integrations.hermes_agent import register
    p = register()
    p.initialize("probe", hermes_home=tmp)
    for i in range(n):
        topic = QUERIES[i % len(QUERIES)]
        p._store.remember("%s note %d is that value %d applies" % (topic, i, i),
                          key="k%d" % i, mtype="fact", source={"doc": "hermes-agent::probe"})
    p._store.flush()
    return p


def _ms(fn) -> float:
    t0 = time.perf_counter()
    fn()
    return (time.perf_counter() - t0) * 1000.0


def _speculate(p):
    """Attach the speculating `queue_prefetch` that 2.27.3 shipped and 2.27.4 removed.

    The probe carries it rather than the library, because the library no longer has it and the
    number that justified taking it out has to stay re-runnable. This is the exact shape that
    shipped: one background thread per queued query, and a cache keyed on the query text.
    """
    p._spec_lock = threading.Lock()
    p._spec_thread = None
    p._spec_cache = None
    p._spec_query = ""

    def queue(query):
        with p._spec_lock:
            if p._spec_thread is not None and p._spec_thread.is_alive():
                return
            p._spec_query = query

            def _work(q=query):
                lines = p._recall_lines(q)
                with p._spec_lock:
                    if p._spec_query == q:
                        p._spec_cache = (q, lines)

            p._spec_thread = threading.Thread(target=_work, daemon=True)
            p._spec_thread.start()

    def fetch(query):
        lines, waitable = None, None
        with p._spec_lock:
            cached = p._spec_cache
            if cached is not None and cached[0] == query:
                lines = cached[1]
            p._spec_cache = None
            if lines is None and p._spec_query == query:
                waitable = p._spec_thread
        # 2.27.3 shipped this wait as well, so it is here: without it, a turn that repeats its
        # question starts a second scan beside the running one. The wait cannot help the
        # host-flow arm, where the queries differ by construction.
        if lines is None and waitable is not None and waitable.is_alive():
            waitable.join(timeout=2.0)
            with p._spec_lock:
                cached = p._spec_cache
                if cached is not None and cached[0] == query:
                    lines = cached[1]
                p._spec_cache = None
        if lines is None:
            lines = p._recall_lines(query)
        return lines

    return queue, fetch


def _arms(p, turn: int) -> dict:
    """One measured turn per arm, in an order that rotates so no arm always runs on a warm cache."""
    q = QUERIES[turn % len(QUERIES)]
    other = QUERIES[(turn + 1) % len(QUERIES)]
    queue, fetch = _speculate(p)
    out = {}

    out["shipped"] = _ms(lambda: p.prefetch(q))

    queue(q)
    p._spec_thread.join(timeout=30)
    out["spec-hit"] = _ms(lambda: fetch(q))

    queue(other)
    p._spec_thread.join(timeout=30)
    out["spec-miss"] = _ms(lambda: fetch(q))

    p._spec_cache = None
    queue(q)
    out["spec-racing"] = _ms(lambda: fetch(q))
    p._spec_thread.join(timeout=30)

    # The real one. Queue the previous turn's question, do not wait, then serve THIS turn's.
    p._spec_cache = None
    queue(other)
    out["spec-host-flow"] = _ms(lambda: fetch(q))
    p._spec_thread.join(timeout=30)
    return out


def _summarize(rows: dict) -> dict:
    def stat(v):
        v = sorted(v)
        return {"median_ms": round(statistics.median(v), 3),
                "p90_ms": round(v[int(len(v) * 0.9) - 1], 3),
                "n": len(v)}
    return {arm: stat(v) for arm, v in rows.items()}


def measure(n: int, turns: int) -> dict:
    tmp = tempfile.mkdtemp(prefix="hermes_prefetch_")
    t0 = time.time()
    print("  n=%-6d building the store" % n, flush=True)
    p = _provider(tmp, n)
    build_s = round(time.time() - t0, 1)
    print("  n=%-6d built in %.1f s" % (n, build_s), flush=True)
    rows: dict = {}
    t1 = time.time()
    for turn in range(turns):
        if turn % 10 == 0:
            print("  n=%-6d turn %d/%d" % (n, turn, turns), flush=True)
        for arm, ms in _arms(p, turn).items():
            rows.setdefault(arm, []).append(ms)
    p.shutdown()
    out = _summarize(rows)
    # Reported because the split is the surprise: the arms below take seconds, and almost all of
    # this probe's wall clock is spent WRITING the store one record at a time. Each write persists
    # the whole file, so the build grows faster than the record count.
    out["_build_s"] = build_s
    out["_measure_s"] = round(time.time() - t1, 1)
    return out


def main() -> int:
    _install_host_stub()
    started = time.time()
    result = {"when_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
              "turns_per_arm": TURNS, "sizes": {}}
    for n in SIZES:
        result["sizes"][str(n)] = measure(n, TURNS)
    print("  control: one record, every arm must agree", flush=True)
    result["control_one_record"] = measure(1, TURNS)

    print()
    print("%-8s %-12s %10s %10s" % ("records", "arm", "median ms", "p90 ms"))
    for n, arms in list(result["sizes"].items()) + [("1 (control)", result["control_one_record"])]:
        for arm, st in sorted(arms.items()):
            if arm.startswith("_"):
                continue
            print("%-8s %-12s %10.3f %10.3f" % (n, arm, st["median_ms"], st["p90_ms"]))
        print("%-8s %-12s   build %.1f s, measure %.1f s"
              % ("", "", arms["_build_s"], arms["_measure_s"]))

    ctl = {k: v for k, v in result["control_one_record"].items() if not k.startswith("_")}
    spread = max(s["median_ms"] for s in ctl.values()) - min(s["median_ms"] for s in ctl.values())
    result["control_spread_ms"] = round(spread, 3)
    result["elapsed_s"] = round(time.time() - started, 1)
    print("\ncontrol spread: %.3f ms" % spread)

    # the receipt is written through the shared helper, which refuses inside the suite: this probe
    # wrote its own file and the suite re-measured it under load on every run (0.245 ms idle became
    # 0.581 ms with twelve workers beside it, 2026-09-15)
    sys.path.insert(0, str(HERE))
    from _receipt import write_receipt, suppressed
    write_receipt(__file__, result)
    if suppressed():
        return 0
    # The control is the gate. A spread there means the arms differ for a reason that has nothing
    # to do with the queue, and no number above can be believed.
    return 0 if spread < 1.0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
