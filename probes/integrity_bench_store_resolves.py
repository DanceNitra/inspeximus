"""Does the STORE resolve the correction, or does the judge? Cell 3, and it needs no judge at all.

WHY THIS EXISTS. Cell 2 (echo resistance, n=20, 2026-08-25) came back a TIE: inspeximus 0.00
resurrection / 0.90 clean, Hindsight 0.9.2 0.00 / 0.95, Fisher p = 1.000 on the one-cell gap. So
"we hold corrections and they do not" is not a claim we get to make on that task.

But cell 2 reads every system through an LLM judge, on purpose, for fairness. That instrument
cannot see WHERE the conflict was resolved. A store that returns only the corrected value and a
store that returns both values and lets a smart reader pick both score "clean" -- and in the
wiring run against Hindsight, recall after a correction returned BOTH side by side:

    RecallResult(text='The staging database is db-3.internal.')
    RecallResult(text='The staging database has been corrected to db-7.internal.')

That is one case, on our Ollama credit, not their native config -- a hypothesis, not a finding.
This cell tests it properly, and it is the claim our product actually rests on: the resolution is
deterministic and happens in the store, with no model deciding anything.

THE MEASUREMENT. Same three writes as cell 2 (value, correction, echo of the retired value), then
read the RAW recall payload and ask a question no model is involved in:

    resolved_at_store  : the payload contains B and NOT A       <- the store decided
    both_returned      : the payload contains A and B           <- the reader must decide
    only_stale         : the payload contains A and NOT B       <- the correction is lost
    neither            : payload contains neither               <- retrieval missed

No judge, no LLM on OUR side, so this arm is free and deterministic. Hindsight's side still costs
its native extractor on retain, which is the sanctioned competitor spend.

WHAT IT CANNOT SETTLE. A store returning both values is not automatically worse: a bitemporal
design that returns old+new with validity markers is being honest, and a caller that reads the
markers is fine. What it does mean is that the disambiguation is the CALLER'S job, and that is a
different product promise from ours. This cell reports the split; it does not grade it.

RUN (free, ours only):  python research/probes/integrity_bench_store_resolves.py --systems inspeximus
RUN (paid, competitor): python research/probes/integrity_bench_store_resolves.py --systems inspeximus,hindsight --n 20
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))
from inspeximus import Inspeximus                    # noqa: E402
import integrity_bench_revert as rev                 # noqa: E402

ENTS = rev.ENTS


NL_JOIN = chr(10)


def classify(payload: str, A: str, B: str) -> str:
    a, b = A.lower() in payload.lower(), B.lower() in payload.lower()
    if b and not a:
        return "resolved_at_store"
    if a and b:
        return "both_returned"
    if a and not b:
        return "only_stale"
    return "neither"


def run_inspeximus(cases):
    out = []
    for (e, A, B) in cases:
        m = Inspeximus(path=None)
        m.echo_guard = True
        m.remember(f"the {e} is {A}", key=e, object=A)
        m.remember(f"correction: the {e} is now {B}", key=e, object=B)
        m.remember(f"the {e} is {A}", key=e, object=A)
        hits = m.recall(e, k=6)
        out.append(classify("\n".join(h["text"] for h in hits), A, B))
    return out


def run_hindsight(cases):
    try:
        from hindsight import HindsightClient, HindsightServer
    except ImportError:
        print("    [hindsight not importable -- needs `pip install hindsight-all` in this venv]",
              flush=True)
        return ["error"] * len(cases)
    key = os.environ.get("OPENAI_API_KEY") or rev.env.get("OPENAI_API_KEY", "")
    if not key:
        print("    [hindsight: no OPENAI_API_KEY; refusing rather than scoring an empty store]",
              flush=True)
        return ["error"] * len(cases)
    out, srv = [], None
    try:
        srv = HindsightServer(llm_provider="openai", llm_model="gpt-5-mini", llm_api_key=key)
        srv.__enter__()
        c = HindsightClient(base_url=srv.url)
        for i, (e, A, B) in enumerate(cases):
            try:
                bank = f"store{i}"
                try:
                    c.create_bank(bank_id=bank)
                except Exception:                                    # noqa: BLE001
                    pass
                c.retain(bank_id=bank, content=f"the {e} is {A}")
                c.retain(bank_id=bank, content=f"correction: the {e} is now {B}")
                c.retain(bank_id=bank, content=f"the {e} is {A}")
                r = c.recall(bank_id=bank, query=f"what is the current {e}?")
                items = getattr(r, "results", None) or []
                out.append(classify("\n".join(getattr(x, "text", str(x)) for x in items), A, B))
            except Exception as ex:                                  # noqa: BLE001
                print(f"    [hindsight store {i} error: {str(ex)[:90]}]", flush=True)
                out.append("error")
            if (i + 1) % 5 == 0:
                print(f"    hindsight {i+1}/{len(cases)}", flush=True)
    except Exception as ex:                                          # noqa: BLE001
        print(f"    [hindsight init FAILED: {str(ex)[:140]}]", flush=True)
        out += ["error"] * (len(cases) - len(out))
    finally:
        if srv is not None:
            try:
                srv.__exit__(None, None, None)
            except Exception:                                        # noqa: BLE001
                pass
    return out


def run_mem0(cases):
    """mem0 on its own recommended stack, the same one cell 1 and cell 2 use."""
    try:
        from mem0 import Memory
    except ImportError:
        print('    [mem0 not importable in this venv]', flush=True)
        return ['error'] * len(cases)
    cfg = {'llm': {'provider': 'openai', 'config': {'model': 'gpt-4o-mini', 'temperature': 0.1}},
           'embedder': {'provider': 'openai', 'config': {'model': 'text-embedding-3-small'}}}
    try:
        mem = Memory.from_config(cfg)
    except Exception as ex:
        print(f'    [mem0 init FAILED: {str(ex)[:120]}]', flush=True)
        return ['error'] * len(cases)
    out = []
    for i, (e, A, B) in enumerate(cases):
        try:
            uid = f'store{i}'
            mem.add(f'the {e} is {A}', user_id=uid)
            mem.add(f'correction: the {e} is now {B}', user_id=uid)
            mem.add(f'the {e} is {A}', user_id=uid)
            ga = mem.get_all(filters={'user_id': uid}, top_k=30)
            ms = ga.get('results', ga) if isinstance(ga, dict) else ga
            payload = NL_JOIN.join((x.get('memory') or x.get('text') or str(x)) for x in (ms or []))
            out.append(classify(payload, A, B))
        except Exception as ex:
            print(f'    [mem0 store {i} error: {str(ex)[:90]}]', flush=True)
            out.append('error')
        if (i + 1) % 5 == 0:
            print(f'    mem0 {i+1}/{len(cases)}', flush=True)
    return out


def run_graphiti(cases):
    """Graphiti against a LIVE neo4j. Refuses loudly rather than scoring an unreachable database,
    because an arm that cannot connect returns the same shape as an arm that found nothing."""
    try:
        from graphiti_core import Graphiti
        from graphiti_core.nodes import EpisodeType
    except ImportError:
        print('    [graphiti_core not importable in this venv]', flush=True)
        return ['error'] * len(cases)
    import socket
    sk = socket.socket(); sk.settimeout(3)
    try:
        sk.connect(('127.0.0.1', 7687))
    except Exception:
        print('    [graphiti: neo4j bolt 7687 unreachable -- REFUSING rather than reporting an empty graph as a result]', flush=True)
        return ['error'] * len(cases)
    finally:
        sk.close()
    return ['error'] * len(cases)   # wired in the follow-up once neo4j is up


def score(name, verdicts, n_cases):
    c = {k: sum(1 for v in verdicts if v == k)
         for k in ("resolved_at_store", "both_returned", "only_stale", "neither")}
    err = sum(1 for v in verdicts if v == "error")
    n = n_cases - err
    return {"system": name, "n": n, "errors": err, **c,
            "store_resolution_rate": round(c["resolved_at_store"] / n, 3) if n else 0.0,
            "ci95": list(rev.wilson(c["resolved_at_store"], n)) if n else [0.0, 1.0]}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=20)
    ap.add_argument("--systems", default="inspeximus")
    a = ap.parse_args()
    want = [s.strip() for s in a.systems.split(",") if s.strip()]
    cases = [ENTS[i] for i in range(min(a.n, len(ENTS)))]
    print(f"cell 3 - who resolves the correction · n={len(cases)} · systems={want}")
    print("no judge, no LLM on our side: this reads the RAW recall payload\n")
    out = {}
    if "inspeximus" in want:
        print("inspeximus (local, free)...")
        out["inspeximus"] = score("inspeximus", run_inspeximus(cases), len(cases))
        print(json.dumps(out["inspeximus"]))
    if "hindsight" in want:
        print(chr(10) + "hindsight (native, embedded + gpt-5-mini extractor)...")
        out["hindsight"] = score("hindsight", run_hindsight(cases), len(cases))
        print(json.dumps(out["hindsight"]))

    if "mem0" in want:
        print(chr(10) + "mem0 (native, gpt-4o-mini + text-embedding-3-small)...")
        out["mem0"] = score("mem0", run_mem0(cases), len(cases))
        print(json.dumps(out["mem0"]))
    if "graphiti" in want:
        print(chr(10) + "graphiti (native, live neo4j)...")
        out["graphiti"] = score("graphiti", run_graphiti(cases), len(cases))
        print(json.dumps(out["graphiti"]))

    p = os.path.join(os.path.dirname(__file__), "integrity_bench_store_resolves_result.json")
    # MERGE, never overwrite. Running one arm alone used to clobber the others' numbers, so a cheap
    # re-run of the free arm silently destroyed two paid ones. Each arm carries the UTC minute it was
    # measured, because a merged file otherwise reads as one simultaneous run and is not.
    prev = {}
    _p = os.path.join(os.path.dirname(__file__), "integrity_bench_store_resolves_result.json")
    if os.path.exists(_p):
        try:
            prev = (json.load(open(_p, encoding="utf-8")) or {}).get("results", {}) or {}
        except Exception:                                            # noqa: BLE001
            prev = {}
    import datetime as _dt
    stamp = _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%MZ")
    for k in out:
        out[k]["measured_utc"] = stamp
    merged = {**prev, **out}

    json.dump({"task": "who resolves the correction, the store or the reader",
               "metric": "store_resolution_rate = recall returns the corrected value and NOT the "
                         "retired one; no judge involved",
               "caveat": "returning both is not automatically worse -- a bitemporal store doing it "
                         "with validity markers is being honest. It does mean disambiguation is "
                         "the caller's job, which is a different promise from ours.",
               "results": merged}, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=2)

    print("\n=== WHO RESOLVES IT ===")
    for k, v in merged.items():
        print(f"  {k:11s} store-resolved={v['store_resolution_rate']:.2f}  "
              f"(resolved={v['resolved_at_store']} both={v['both_returned']} "
              f"stale={v['only_stale']} neither={v['neither']}, n={v['n']})")
    print(f"\nwrote {p}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
