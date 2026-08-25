"""Cell 4: run the identical corrections twice. Is the resulting state byte-identical?

WHY THIS CELL AND NOT ANOTHER. Cell 3 measured who resolves a correction (us 20/20, Hindsight
0/20, mem0 1/20). The natural next question was whether we could beat Hindsight on Cell 2's
clean-current-truth, where they score 0.95 to our 0.90. We cannot, and the reason is worth
recording: those two "ambiguous" cases are the JUDGE, not us. Measured 2026-08-25 -- our recall
returned a single unambiguous line in 20/20 cases, and the judge still answered "other" on

    correction: the default currency is now guarani
    correction: the scheduler is now weighted

Rephrasing does not rescue it. `the default currency is guarani`, a maximally plain assertion,
still scores "other". So the judge stumbles on particular VALUE TOKENS, not on our phrasing, and
no product change moves that number. Cell 2's clean-current-truth carries roughly 10% instrument
noise for every system in it, ours and theirs alike, which is what its overlapping CIs already say.

THE AXIS WHERE ARCHITECTURE DECIDES. An LLM on the write path cannot promise that the same writes
produce the same stored state. Ours has no model on that path at all. This measures the difference
rather than asserting it:

    run the identical fixture TWICE against a fresh store each time
    hash the recall payloads
    identical hash  ->  the state is reproducible; an audit of it can be re-derived
    different hash  ->  the state depends on something the caller does not control

WHAT THIS IS NOT, and it matters because the cell is easy to read as a gotcha. Non-determinism is
not a defect; it is the price of extraction, and extraction buys Hindsight and mem0 something we
do not have -- they can absorb a fact from prose we would need a key for. The cost of that trade
is that "what did the store hold on Tuesday" stops being answerable by re-running Tuesday's
writes. If you never need to re-derive state, this cell is not about you.

It also records wall clock and model calls, because those are the same trade seen from the side a
user feels.

RUN (free, ours):   python research/probes/integrity_bench_determinism.py --systems inspeximus
RUN (paid, theirs): python research/probes/integrity_bench_determinism.py --systems inspeximus,hindsight --n 20
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))
from inspeximus import Inspeximus                    # noqa: E402
import integrity_bench_revert as rev                 # noqa: E402

ENTS = rev.ENTS
NL = chr(10)


import re

# TIMESTAMPS ARE NOT NON-DETERMINISM, and the first run of this cell counted them as if they were.
# Hindsight stamps each extracted fact with a wall-clock time, so two passes minutes apart differ
# for a reason that has nothing to do with the model -- and our own arm puts no timestamp in its
# payload at all, so the comparison was not like for like. Any system that records time would have
# 'failed' this cell. Stripped before hashing; what remains is what the store actually decided.
_TS = re.compile(r'\d{4}-\d{2}-\d{2}(?:T[\d:.+\-]+)?')


def strip_time(payload: str) -> str:
    return _TS.sub("<TS>", payload)


def digest(payloads) -> str:
    return hashlib.sha256(chr(31).join(strip_time(p) for p in payloads).encode("utf-8")).hexdigest()


def inspeximus_pass(cases):
    out = []
    for (e, A, B) in cases:
        m = Inspeximus(path=None)
        m.echo_guard = True
        m.remember(f"the {e} is {A}", key=e, object=A)
        m.remember(f"correction: the {e} is now {B}", key=e, object=B)
        m.remember(f"the {e} is {A}", key=e, object=A)
        out.append(NL.join(h["text"] for h in m.recall(e, k=6)))
    return out, 0        # payloads, model calls


def hindsight_pass(cases, tag):
    from hindsight import HindsightClient, HindsightServer
    key = os.environ.get("OPENAI_API_KEY") or rev.env.get("OPENAI_API_KEY", "")
    if not key:
        raise SystemExit("REFUSED: no OPENAI_API_KEY; an unconfigured store would look "
                         "deterministic for the wrong reason")
    out, calls, srv = [], 0, None
    try:
        srv = HindsightServer(llm_provider="openai", llm_model="gpt-5-mini", llm_api_key=key)
        srv.__enter__()
        c = HindsightClient(base_url=srv.url)
        for i, (e, A, B) in enumerate(cases):
            bank = f"det{tag}{i}"
            try:
                c.create_bank(bank_id=bank)
            except Exception:                                        # noqa: BLE001
                pass
            for content in (f"the {e} is {A}",
                            f"correction: the {e} is now {B}",
                            f"the {e} is {A}"):
                c.retain(bank_id=bank, content=content)
                calls += 1        # one extraction per retain, by their design
            r = c.recall(bank_id=bank, query=f"what is the current {e}?")
            items = getattr(r, "results", None) or []
            # SORTED, so ordering alone never counts as non-determinism. The question is whether
            # the store held the same FACTS, not whether it returned them in the same sequence.
            out.append(NL.join(sorted(getattr(x, "text", str(x)) for x in items)))
            if (i + 1) % 5 == 0:
                print(f"    hindsight pass {tag} {i+1}/{len(cases)}", flush=True)
    finally:
        if srv is not None:
            try:
                srv.__exit__(None, None, None)
            except Exception:                                        # noqa: BLE001
                pass
    return out, calls


def measure(name, runner, cases):
    t0 = time.time()
    p1, c1 = runner(cases, "a") if name == "hindsight" else runner(cases)
    t1 = time.time()
    p2, c2 = runner(cases, "b") if name == "hindsight" else runner(cases)
    t2 = time.time()
    d1, d2 = digest(p1), digest(p2)
    differing = [i for i, (a, b) in enumerate(zip(p1, p2))
                 if strip_time(a) != strip_time(b)]
    return {"system": name, "n": len(cases),
            "run1_sha256": d1[:32], "run2_sha256": d2[:32],
            "byte_identical": d1 == d2,
            "cases_that_differ": len(differing),
            "differing_indexes": differing[:8],
            "model_calls_per_run": c1,
            "seconds_run1": round(t1 - t0, 3), "seconds_run2": round(t2 - t1, 3),
            "payloads_run1": p1, "payloads_run2": p2,
            "example_diff": ({"case": ENTS[differing[0]][0],
                              "run1": p1[differing[0]][:300],
                              "run2": p2[differing[0]][:300]} if differing else None)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=20)
    ap.add_argument("--systems", default="inspeximus")
    a = ap.parse_args()
    want = [s.strip() for s in a.systems.split(",") if s.strip()]
    cases = [ENTS[i] for i in range(min(a.n, len(ENTS)))]
    print(f"cell 4 - is the resulting state reproducible? n={len(cases)} systems={want}")
    print("two passes over an identical fixture, fresh store each time, payloads hashed\n")

    out = {}
    if "inspeximus" in want:
        print("inspeximus (local, no model on the write path)...")
        out["inspeximus"] = measure("inspeximus", inspeximus_pass, cases)
        print(json.dumps({k: v for k, v in out["inspeximus"].items() if k not in ("example_diff","payloads_run1","payloads_run2")}))
    if "hindsight" in want:
        print(NL + "hindsight (native, gpt-5-mini extraction on every retain)...")
        try:
            out["hindsight"] = measure("hindsight", hindsight_pass, cases)
        except Exception as ex:                                      # noqa: BLE001
            print(f"    [hindsight FAILED: {type(ex).__name__}: {str(ex)[:160]}]", flush=True)
            out["hindsight"] = {"system": "hindsight", "error": f"{type(ex).__name__}: {ex}"[:300]}
        print(json.dumps({k: v for k, v in out["hindsight"].items() if k not in ("example_diff","payloads_run1","payloads_run2")}))

    p = os.path.join(os.path.dirname(__file__), "integrity_bench_determinism_result.json")
    prev = {}
    if os.path.exists(p):
        try:
            prev = (json.load(open(p, encoding="utf-8")) or {}).get("results", {}) or {}
        except Exception:                                            # noqa: BLE001
            prev = {}
    import datetime as _dt
    stamp = _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%MZ")
    for k in out:
        out[k]["measured_utc"] = stamp
    merged = {**prev, **out}
    json.dump({"task": "is the resulting state reproducible across identical runs",
               "metric": "byte_identical recall payloads across two passes on a fresh store",
               "caveat": "non-determinism is the price of LLM extraction, not a defect. Extraction "
                         "buys absorbing a fact from prose without a key. What it costs is the "
                         "ability to re-derive a past state by re-running its writes.",
               "results": merged}, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=2)

    print("\n=== REPRODUCIBLE STATE ===")
    for k, v in merged.items():
        if "error" in v:
            print(f"  {k:11s} ERROR {v['error'][:60]}")
            continue
        print(f"  {k:11s} byte-identical={v['byte_identical']}  differing={v['cases_that_differ']}"
              f"/{v['n']}  model-calls/run={v['model_calls_per_run']}  "
              f"{v['seconds_run1']}s + {v['seconds_run2']}s")
    print(f"\nwrote {p}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
