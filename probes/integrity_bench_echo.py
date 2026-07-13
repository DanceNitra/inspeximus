"""integrity_bench_echo.py — cross-system INTEGRITY benchmark, cell 2: echo resistance.

Same methodology as integrity_bench_revert.py (native config, shared judge on full memory state, published
whichever way it falls). Task: store a value, correct it, then RESTATE the old value (an echo — benign
repetition or an injected restatement). Does the current answer stay corrected, or does the stale value come
back?

    add   "the {entity} is {A}."
    add   "correction: the {entity} is now {B}."
    echo  "the {entity} is {A}."            # restate the retired value
    ask   "what is the current {entity}?"   ->   B = echo resisted (good), A = resurrected (bad)

metric = echo-resistance rate (fraction where the current answer is still B). mnemo is measured LOCALLY (its
echo_guard, no OpenAI). mem0 / graphiti are gated behind --systems because they cost OpenAI calls; the honest
expected shape from prior work: mnemo defends, Graphiti also defends (~0% resurrection in its real pipeline),
mem0 ~0.53. We do NOT sweep this cell — that is the point.

RUN (free):  python mnemo/probes/integrity_bench_echo.py --systems mnemo
RUN (paid):  python mnemo/probes/integrity_bench_echo.py --systems mnemo,mem0,graphiti --n 20
"""
import os, sys, json, argparse

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))
from mnemo import Mnemo
# reuse the shared judge + entity fixture + adapters wiring from the revert cell
import integrity_bench_revert as rev
ENTS, openai_chat, judge_current = rev.ENTS, rev.openai_chat, rev.judge_current


def run_mnemo_echo(cases):
    out = []
    for (e, A, B) in cases:
        m = Mnemo(path=None); m.echo_guard = True
        m.remember(f"the {e} is {A}", key=e, object=A)
        m.remember(f"correction: the {e} is now {B}", key=e, object=B)
        m.remember(f"the {e} is {A}", key=e, object=A)          # the echo of the retired value
        hits = m.recall(e, k=6)
        ctx = "\n".join(h["text"] for h in hits)
        # SYMMETRIC INSTRUMENT (fairness fix 2026-07-11): read mnemo through the SAME LLM judge on its native
        # recall surface, exactly as mem0/graphiti — not mechanically from its own ledger (the asymmetric
        # instrument the pre-publication audit caught). judge returns A=resurrected / B=resisted / other.
        out.append(judge_current(e, ctx or "(no memories)", A, B))
    return out


def run_mem0_echo(cases):
    from mem0 import Memory
    cfg = {"llm": {"provider": "openai", "config": {"model": "gpt-4o-mini", "temperature": 0.1}},
           "embedder": {"provider": "openai", "config": {"model": "text-embedding-3-small"}}}
    try:
        mem = Memory.from_config(cfg)
    except Exception as ex:
        print(f"    [mem0 init FAILED: {str(ex)[:120]}]", flush=True); return ["error"] * len(cases)
    out = []
    for i, (e, A, B) in enumerate(cases):
        try:
            uid = f"echo{i}"
            mem.add(f"the {e} is {A}", user_id=uid)
            mem.add(f"correction: the {e} is now {B}", user_id=uid)
            mem.add(f"the {e} is {A}", user_id=uid)
            ga = mem.get_all(filters={"user_id": uid}, top_k=30)
            mems = ga.get("results", ga) if isinstance(ga, dict) else ga
            ctx = "\n".join((x.get("memory") or x.get("text") or str(x)) for x in (mems or []))
            v = judge_current(e, ctx or "(none)", A, B)         # judge returns A / B / other
            out.append(v)
        except Exception as ex:
            print(f"    [mem0 echo {i} error: {str(ex)[:90]}]", flush=True); out.append("error")
        if (i + 1) % 5 == 0:
            print(f"    mem0 {i+1}/{len(cases)}", flush=True)
    return out


def run_graphiti_echo(cases):
    import asyncio, datetime
    from graphiti_core import Graphiti
    from graphiti_core.nodes import EpisodeType

    async def _run():
        g = Graphiti("bolt://localhost:7687", "neo4j", "testpassword123"); out = []
        try:
            await g.build_indices_and_constraints()
            for i, (e, A, B) in enumerate(cases):
                try:
                    gid = f"echocase_{i}_{datetime.datetime.now(datetime.timezone.utc).strftime('%H%M%S%f')}"
                    t0 = datetime.datetime(2026, 7, 11, 10, 0, 0, tzinfo=datetime.timezone.utc)
                    for j, msg in enumerate([f"the {e} is {A}", f"correction: the {e} is now {B}", f"the {e} is {A}"]):
                        await g.add_episode(name=f"m{j}", episode_body=msg, source_description="chat",
                                            reference_time=t0 + datetime.timedelta(minutes=j),
                                            source=EpisodeType.message, group_id=gid)
                    res = await g.search(f"what is the current {e}?", group_ids=[gid], num_results=10)
                    ctx = "\n".join(getattr(x, "fact", str(x)) for x in res)
                    out.append(judge_current(e, ctx or "(no facts)", A, B))
                except Exception as ex:
                    print(f"    [graphiti echo {i} error: {str(ex)[:90]}]", flush=True); out.append("error")
                if (i + 1) % 5 == 0:
                    print(f"    graphiti {i+1}/{len(cases)}", flush=True)
        finally:
            await g.close()
        return out
    return asyncio.run(_run())


def score(name, verdicts, n_cases):
    B = sum(1 for v in verdicts if v == "B")      # clean current-truth (returns the corrected value)
    A = sum(1 for v in verdicts if v == "A")      # RESURRECTED the stale value (the actual attack success)
    o = sum(1 for v in verdicts if v == "other")  # ambiguous — both facts visible, judge can't pick (not a fail)
    err = sum(1 for v in verdicts if v == "error")
    n = n_cases - err
    # TWO honest metrics: resurrection_rate is the attack (lower=better); clean_current_truth is answer clarity.
    # "other" is NOT resurrection — a bitemporal store that returns old+new invalidated/valid edges reads
    # ambiguous to the judge but never asserts the stale value as current.
    return {"system": name, "n": n, "resurrected_A": A, "clean_current_B": B, "ambiguous_other": o, "errors": err,
            "resurrection_rate": round(A / n, 3) if n else 0.0,
            "resurrection_ci95": list(rev.wilson(A, n)),
            "clean_current_truth_rate": round(B / n, 3) if n else 0.0,
            "clean_ci95": list(rev.wilson(B, n))}


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--n", type=int, default=20)
    ap.add_argument("--systems", default="mnemo")
    a = ap.parse_args(); want = [s.strip() for s in a.systems.split(",") if s.strip()]
    cases = [ENTS[i] for i in range(min(a.n, len(ENTS)))]
    print(f"cross-system integrity benchmark — echo resistance · n={len(cases)} · systems={want}\n")
    out = {}
    if "mnemo" in want:
        print("mnemo (local, echo_guard)...")
        out["mnemo"] = score("mnemo", run_mnemo_echo(cases), len(cases)); print(json.dumps(out["mnemo"]))
    if "mem0" in want:
        print("\nmem0 (native, OpenAI)..."); out["mem0"] = score("mem0", run_mem0_echo(cases), len(cases)); print(json.dumps(out["mem0"]))
    if "graphiti" in want:
        print("\ngraphiti (native, neo4j + OpenAI)..."); out["graphiti"] = score("graphiti", run_graphiti_echo(cases), len(cases)); print(json.dumps(out["graphiti"]))
    json.dump({"task": "echo resistance", "metric": "echo_resistance (current answer stays corrected B)",
               "results": out}, open(os.path.join(os.path.dirname(__file__), "integrity_bench_echo_result.json"), "w"), indent=2)
    print("\n=== ECHO MATRIX (honest: resurrection = the attack; clean-current-truth = answer clarity) ===")
    for k, v in out.items():
        print(f"  {k:9s} resurrection={v['resurrection_rate']:.2f}  clean-current-truth={v['clean_current_truth_rate']:.2f}"
              f"  (resurrected={v['resurrected_A']} clean={v['clean_current_B']} ambiguous={v['ambiguous_other']}, n={v['n']})")


if __name__ == "__main__":
    main()
