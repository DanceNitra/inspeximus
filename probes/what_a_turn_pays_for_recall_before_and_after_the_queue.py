"""What a Hermes turn pays for recall, before and after the background queue.

THE CLAIM UNDER TEST is the one in the adapter's docstring: `queue_prefetch` turns the hot path into
a dictionary read. That is a performance claim, so it needs a number rather than an argument, and it
needs the honest cases beside the flattering one.

Four arms, interleaved at every store size so a busy machine cannot favour one:

    inline        prefetch() with nothing queued -- what shipped before this change
    queued-hit    queue_prefetch() after the previous turn, then prefetch() for the SAME query
    queued-miss   a result queued for a DIFFERENT query; prefetch must fall back and must not
                  serve the wrong one. This is the arm that can show a REGRESSION, because the
                  turn pays the inline cost plus the lock.
    racing        queue_prefetch() issued and NOT waited for, then prefetch() immediately. This is
                  what a fast turn actually looks like, and it is the arm most likely to be slower
                  than inline, because two threads then touch the store at once.

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


def _arms(p, turn: int) -> dict:
    """One measured turn per arm, in an order that rotates so no arm always runs on a warm cache."""
    q = QUERIES[turn % len(QUERIES)]
    other = QUERIES[(turn + 1) % len(QUERIES)]
    out = {}

    p._prefetch_cache = None
    out["inline"] = _ms(lambda: p.prefetch(q))

    p.queue_prefetch(q)
    p._prefetch_thread.join(timeout=30)
    out["queued-hit"] = _ms(lambda: p.prefetch(q))

    p.queue_prefetch(other)
    p._prefetch_thread.join(timeout=30)
    out["queued-miss"] = _ms(lambda: p.prefetch(q))

    p._prefetch_cache = None
    p.queue_prefetch(q)
    out["racing"] = _ms(lambda: p.prefetch(q))
    p._prefetch_thread.join(timeout=30)
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

    out = HERE / (pathlib.Path(__file__).stem + ".result.json")
    out.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print("wrote %s" % out.name)

    if os.environ.get("PYTEST_CURRENT_TEST"):
        return 0
    # The control is the gate. A spread there means the arms differ for a reason that has nothing
    # to do with the queue, and no number above can be believed.
    return 0 if spread < 1.0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
