"""MemoryAgentBench Conflict Resolution — promoted to a reproducible artifact, WITH the control it lacked.

Third-party ground: the data, the questions and the gold answers are MemoryAgentBench
(Hu et al., arXiv:2507.05257), Conflict-Resolution split; the metric is their `substring_exact_match` with
their `normalize_answer` semantics. Nothing here is a fixture we wrote.

## What was wrong with the existing run, and why this file exists

`bench/run_cr_benchmark.py` has three arms — `base_full`, `inspeximus_single`, `inspeximus_iterative` — and
**both inspeximus arms read the same keyed, superseded store**. So `single -> iterative` isolates *iteration*,
and nothing in that design varies supersession at all. The headline was attributed to a mechanism the
experiment never moved.

This file replaces it with a **2x2 factorial**: {supersession ON, OFF} x {single-shot, iterative}. The new
cell, `iter_off`, is the falsification control. Pre-registered in PREREGISTRATION.md Appendix A as **F1**:

    with retrieval held fixed, supersession-ON must beat supersession-OFF,
    or the CR result is not a supersession result.

Two further defects are fixed here rather than inherited:

* **`reinforce=False` on every recall.** `recall()` defaults to `reinforce=True`, which mutates record
  value on read and makes results ORDER-DEPENDENT — measured elsewhere in this repo at 49-90% of answers
  changing under reordering. A benchmark whose scores depend on question order is not a benchmark. The
  old sweep did not pass it.
* **`bench/README.md` does not reproduce from `bench/results_cr_sweep.json`.** README: 32k 0.44 / 64k 0.50
  / 262k 0.36. JSON: 0.52 / 0.42 / 0.38. Two different runs, not labelled as such. Recorded, not silently
  corrected.

## Two stages, because the GPU is shared

* **Stage 1 (`--stage 1`, zero LLM calls, always runs).** Our derived single-hop probe over their facts:
  at a fixed k, does the retriever put the CURRENT value in the window, and how much of the window goes to
  superseded restatements? Labelled everywhere as OUR probe on THEIR data, not their metric.
* **Stage 2 (`--stage 2`, their metric end-to-end).** Needs a pinned answerer and is gated by the GPU
  pre-flight. Accuracy is contention-INVARIANT (contention costs seconds, not correctness), so a stamped
  run under `--allow-contended-gpu` is admissible for accuracy and inadmissible for latency. That
  distinction is enforced: Stage 2 records no latency claim.
"""
from __future__ import annotations

import argparse
import collections
import json
import pathlib
import re
import string
import sys
import time
from concurrent.futures import ThreadPoolExecutor

HERE = pathlib.Path(__file__).resolve().parent
REPO = HERE.parent.parent
# Measure the code that RUNS: the worktree, not whatever `pip show inspeximus` resolves to.
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "bench"))

import preflight  # noqa: E402
from llm import default_answerer  # noqa: E402

DATASET = ("ai-hyz/MemoryAgentBench", "data/Conflict_Resolution-00000-of-00001.parquet")
K = 15
HOPS = 2
FULLCTX_CHAR_CAP = 100_000        # ~25k tokens; above this the full-context arm is reported N/A

_SYS = ("You answer a question using ONLY the facts provided. Some facts are updated later in the list: "
        "when the same thing is stated more than once, ALWAYS use the MOST RECENT (latest-listed) value. "
        "Reason step by step internally but reply with ONLY the final answer, no explanation.")
_FSYS = ("You are decomposing a multi-hop question into retrieval steps. Given the question and the facts "
         "retrieved so far, name the SINGLE most useful next thing to look up to make progress (an entity "
         "or a relation). Reply with ONLY a short search phrase, no explanation.")


# ---------------------------------------------------------------- their metric
def _norm(t: str) -> str:
    t = (t or "").lower()
    t = "".join(c for c in t if c not in string.punctuation)
    t = re.sub(r"\b(a|an|the)\b", " ", t)
    return " ".join(t.split())


def substring_exact_match(pred: str, golds) -> int:
    p = _norm(pred)
    return int(any(_norm(g) and _norm(g) in p for g in golds))


# ---------------------------------------------------------------- data + stores
def load_rows(rows):
    from huggingface_hub import hf_hub_download
    import pandas as pd
    path = hf_hub_download(DATASET[0], DATASET[1], repo_type="dataset")
    df = pd.read_parquet(path)
    return [(r, df.iloc[r]) for r in rows], path


def build_stores(lines):
    """The two arms of the supersession factor, built from the SAME lines in the SAME order.

    `on`  : every parseable fact is remembered under key=(entity|relation), so a later restatement
            supersedes the earlier one and recall serves only the latest.
    `off` : the identical store with no keys — a keep-all accumulate store on the identical retriever.
            Holding the retriever fixed is what makes the difference attributable to supersession; an
            independently-written baseline would confound the mechanism with its ranking code.
    """
    from inspeximus import Inspeximus
    from memoryagentbench_cr import parse_fact

    on, off = Inspeximus(None), Inspeximus(None)
    keys = collections.OrderedDict()
    for ln in lines:
        p = parse_fact(ln)
        off.remember(ln)
        if p is None:
            on.remember(ln)
            continue
        key, value, _slug = p
        on.remember(ln, key=key, object=value)
        keys.setdefault(key, []).append((ln, value))
    return on, off, keys


def recall_texts(store, query, k=K):
    # reinforce=False: recall() otherwise mutates value on read and makes the whole benchmark
    # order-dependent. Not optional for a measurement.
    return [h["text"] for h in store.recall(query, k=k, mode="lexical", reinforce=False)]


# ---------------------------------------------------------------- Stage 1: mechanism control, zero LLM
K_SWEEP = (1, 3, 5, 15)


def stage1(rows, k_sweep=K_SWEEP, max_keys=250):
    """OUR derived single-hop probe on THEIR facts. For every (entity, relation) restated at least once,
    query the store with the fact's subject+relation (object removed) and ask what lands in the top-k.

    Two properties of this instrument were fixed after the first run ceilinged, both declared in
    PREREGISTRATION.md Appendix B BEFORE they were applied:

    * **Same-key matching, not substring matching.** CR values are shared strings — a city that is the
      stale `born_city` of one entity is the legitimate CURRENT `born_city` of another. Matching a stale
      value as a substring of any retrieved line charged the ON arm for other entities' correct facts and
      read `stale_in_topk` 0.35-0.41 where the mechanism says near-zero. Every retrieved line is now
      parsed back to its own `(entity|relation)` key and only same-key lines are scored.
    * **A rank, swept over k.** `current_in_topk` read 1.000 on both arms at k=15 — a ceiling, not a
      result. `current_rank` (k+1 when absent) cannot ceiling, and the sweep shows where, if anywhere,
      the two arms separate.
    """
    from memoryagentbench_cr import fact_lines, parse_fact
    out = []
    for ridx, row in rows:
        lines = fact_lines(row["context"])
        on, off, keys = build_stores(lines)
        conflicted = [(kk, vv) for kk, vv in keys.items() if len(vv) > 1][:max_keys]
        kmax = max(k_sweep)
        acc = {(a, k): collections.Counter() for a in ("on", "off") for k in k_sweep}
        for key, statements in conflicted:
            latest_line, latest_value = statements[-1]
            stale_values = {v for _l, v in statements[:-1] if v != latest_value}
            if not stale_values:
                continue
            # subject+relation with the object stripped -> the probe every restatement matches equally
            probe = latest_line[: latest_line.rfind(latest_value)].strip() if latest_value in latest_line \
                else latest_line
            for arm, store in (("on", on), ("off", off)):
                hits = recall_texts(store, probe, k=kmax)
                # classify each hit by its OWN parsed key; anything not about this key is irrelevant
                cls = []
                for h in hits:
                    p = parse_fact(h)
                    if p is None or p[0] != key:
                        cls.append(None)
                    elif p[1] == latest_value:
                        cls.append("current")
                    elif p[1] in stale_values:
                        cls.append("stale")
                    else:
                        cls.append(None)
                for k in k_sweep:
                    window = cls[:k]
                    c = acc[(arm, k)]
                    c["n"] += 1
                    c["slots"] += len(window)
                    c["current_in_topk"] += int("current" in window)
                    n_stale = sum(1 for x in window if x == "stale")
                    c["stale_in_topk"] += int(n_stale > 0)
                    c["slots_to_stale"] += n_stale
                    c["rank_sum"] += (window.index("current") + 1) if "current" in window else (k + 1)
        for arm in ("on", "off"):
            for k in k_sweep:
                c = acc[(arm, k)]
                n = c["n"] or 1
                out.append({"row": ridx, "arm": arm, "facts": len(lines),
                            "conflicted_keys": c["n"], "k": k,
                            "current_in_topk": round(c["current_in_topk"] / n, 3),
                            "current_rank": round(c["rank_sum"] / n, 3),
                            "stale_in_topk": round(c["stale_in_topk"] / n, 3),
                            "slots_to_stale": round(c["slots_to_stale"] / max(1, c["slots"]), 3)})
                print(f"  row {ridx} facts={len(lines):6} supersession={arm:3} k={k:2} "
                      f"keys={c['n']:4} current_in_topk={out[-1]['current_in_topk']:.3f} "
                      f"current_rank={out[-1]['current_rank']:.3f} "
                      f"stale_in_topk={out[-1]['stale_in_topk']:.3f} "
                      f"slots_to_stale={out[-1]['slots_to_stale']:.3f}", flush=True)
    return out


# ---------------------------------------------------------------- Stage 2: their metric, 2x2 + full ctx
def stage2(rows, llm, n_questions=25, k=K, workers=3, arms=("full_context", "single_off", "single_on",
                                                            "iter_off", "iter_on")):
    from memoryagentbench_cr import fact_lines
    results = []
    for ridx, row in rows:
        lines = fact_lines(row["context"])
        on, off, _keys = build_stores(lines)
        fits = len("\n".join(lines)) <= FULLCTX_CHAR_CAP
        qs = list(row["questions"])[:n_questions]
        golds = [list(g) if hasattr(g, "__len__") and not isinstance(g, str) else [g]
                 for g in list(row["answers"])[:n_questions]]

        def answer(facts, q):
            return llm.chat(_SYS, "Facts:\n" + "\n".join(facts) + f"\n\nQuestion: {q}\nAnswer:")

        def iterative(store, q):
            facts = set(recall_texts(store, q, k=k))
            for _ in range(HOPS):
                fq = llm.chat(_FSYS, f"Question: {q}\nFacts so far:\n" +
                              "\n".join(sorted(facts)[:40]) + "\n\nNext to look up:", max_tokens=48)
                if fq and not fq.startswith("__ERR__"):
                    facts |= set(recall_texts(store, fq, k=k))
            return answer(sorted(facts), q)

        def one(qg):
            q, gl = qg
            r = {}
            if "full_context" in arms:
                r["full_context"] = substring_exact_match(answer(lines, q), gl) if fits else None
            if "single_off" in arms:
                r["single_off"] = substring_exact_match(answer(recall_texts(off, q, k=k), q), gl)
            if "single_on" in arms:
                r["single_on"] = substring_exact_match(answer(recall_texts(on, q, k=k), q), gl)
            if "iter_off" in arms:
                r["iter_off"] = substring_exact_match(iterative(off, q), gl)
            if "iter_on" in arms:
                r["iter_on"] = substring_exact_match(iterative(on, q), gl)
            return r

        t0 = time.time()
        with ThreadPoolExecutor(max_workers=workers) as ex:
            rs = list(ex.map(one, zip(qs, golds)))
        n = len(rs)
        cell = {"row": ridx, "facts": len(lines), "n": n, "k": k, "hops": HOPS,
                "full_context_fits": fits}
        for arm in arms:
            vals = [r[arm] for r in rs if r.get(arm) is not None]
            cell[arm] = round(sum(vals) / len(vals), 3) if vals else "N/A (context exceeds cap)"
        cell["wall_s"] = round(time.time() - t0, 1)
        results.append(cell)
        print(f"  row {ridx} facts={len(lines):6} " +
              "  ".join(f"{a}={cell[a]}" for a in arms) + f"  ({cell['wall_s']}s)", flush=True)
    return results


# ---------------------------------------------------------------- verdicts
def verdict_f1a(stage1_rows) -> dict:
    """F1a — the zero-LLM half of the falsification control, decided on `current_rank` (Appendix B).

    Lower rank is better, so ON must be STRICTLY BELOW OFF. A cell where both arms sit at the ceiling
    (`current_in_topk == 1.0` on both) is reported as `ceilinged` and carries no evidence either way — the
    same cell is what forced the re-specification in the first place, and hiding it would hide the reason.
    """
    on = [r for r in stage1_rows if r["arm"] == "on"]
    off = [r for r in stage1_rows if r["arm"] == "off"]
    if not on or not off:
        return {"prediction": "F1a", "verdict": "NOT-MEASURED", "reason": "missing an arm"}
    by_k = {}
    for a, b in zip(on, off):
        assert a["row"] == b["row"] and a["k"] == b["k"], "arm rows misaligned"
        by_k.setdefault(a["k"], []).append((a, b))
    per_k = []
    for k in sorted(by_k):
        pairs = by_k[k]
        ron = sum(x["current_rank"] for x, _ in pairs) / len(pairs)
        roff = sum(y["current_rank"] for _, y in pairs) / len(pairs)
        per_k.append({"k": k, "current_rank_on": round(ron, 3), "current_rank_off": round(roff, 3),
                      "delta": round(ron - roff, 3),
                      "current_in_topk_on": round(sum(x["current_in_topk"] for x, _ in pairs) / len(pairs), 3),
                      "current_in_topk_off": round(sum(y["current_in_topk"] for _, y in pairs) / len(pairs), 3),
                      "stale_in_topk_on": round(sum(x["stale_in_topk"] for x, _ in pairs) / len(pairs), 3),
                      "stale_in_topk_off": round(sum(y["stale_in_topk"] for _, y in pairs) / len(pairs), 3),
                      "ceilinged": bool(all(x["current_in_topk"] == 1.0 and y["current_in_topk"] == 1.0
                                            for x, y in pairs))})
    informative = [c for c in per_k if not c["ceilinged"]]
    basis = informative or per_k
    mon = sum(c["current_rank_on"] for c in basis) / len(basis)
    moff = sum(c["current_rank_off"] for c in basis) / len(basis)
    return {"prediction": "F1a (re-specified, PREREGISTRATION Appendix B)",
            "metric": "current_rank, lower is better (OUR derived probe on THEIR facts)",
            "per_k": per_k, "decided_on_k": [c["k"] for c in basis],
            "current_rank_on": round(mon, 3), "current_rank_off": round(moff, 3),
            "delta": round(mon - moff, 3),
            "verdict": "SUPPORTED" if mon < moff else "RED",
            "reading": ("supersession places the current value EARLIER in the retrieval window"
                        if mon < moff else
                        "supersession does NOT improve what reaches the window — the CR result must not "
                        "be published as a supersession result")}


def verdict_f1(stage2_rows) -> dict:
    """F1 — the end-to-end falsification control on their own metric."""
    if not stage2_rows:
        return {"prediction": "F1", "verdict": "NOT-MEASURED", "reason": "stage 2 did not run"}
    def m(arm):
        vals = [r[arm] for r in stage2_rows if isinstance(r.get(arm), (int, float))]
        return round(sum(vals) / len(vals), 3) if vals else None
    it_on, it_off, sg_on, sg_off = m("iter_on"), m("iter_off"), m("single_on"), m("single_off")
    pairs = {"iter_on>iter_off": (it_on, it_off), "single_on>single_off": (sg_on, sg_off)}
    checks = {k: (None if a is None or b is None else a > b) for k, (a, b) in pairs.items()}
    known = [v for v in checks.values() if v is not None]
    verdict = ("NOT-MEASURED" if not known else "SUPPORTED" if all(known) else
               "RED" if not any(known) else "PARTIAL")
    return {"prediction": "F1", "metric": "substring_exact_match (their metric)",
            "iter_on": it_on, "iter_off": it_off, "single_on": sg_on, "single_off": sg_off,
            "checks": checks, "verdict": verdict,
            "reading": ("supersession, with retrieval held fixed, is what moves the metric"
                        if verdict == "SUPPORTED" else
                        "supersession does not carry the result; scope any claim to iterative retrieval"
                        if verdict == "RED" else
                        "one of the two contrasts holds — report both cells, claim neither alone")}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--rows", default="0,1", help="MemoryAgentBench CR row indices (context lengths)")
    ap.add_argument("--stage", default="1", help="1 (zero-LLM control), 2 (their metric), or 1,2")
    ap.add_argument("-n", "--n-questions", type=int, default=25)
    ap.add_argument("--k", type=int, default=K)
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--allow-contended-gpu", action="store_true",
                    help="stage 2 only; stamps gpu_contended=true on every cell")
    ap.add_argument("--out", default=str(HERE / "results" / "cr_control.json"))
    a = ap.parse_args()

    stages = {s.strip() for s in a.stage.split(",")}
    rows_idx = [int(x) for x in a.rows.split(",")]
    rows, dataset_path = load_rows(rows_idx)
    out = {"benchmark": "MemoryAgentBench Conflict Resolution (Hu et al., arXiv:2507.05257)",
           "dataset": DATASET[0] + "/" + DATASET[1], "dataset_cache": dataset_path,
           "metric_stage2": "substring_exact_match (their metric)",
           "metric_stage1": "current_in_topk / stale_in_topk / slots_to_stale "
                            "(OUR derived single-hop probe on THEIR facts, not their metric)",
           "rows": rows_idx, "k": a.k, "hops": HOPS, "recall_reinforce": False,
           "inspeximus_version": _version(), "generated": time.strftime("%Y-%m-%dT%H:%M:%S")}

    if "1" in stages:
        print("STAGE 1 — mechanism control, zero LLM calls")
        out["stage1"] = stage1(rows)
        out["F1a"] = verdict_f1a(out["stage1"])
        print(f"  F1a: {out['F1a']['verdict']}  current_rank on={out['F1a']['current_rank_on']} "
              f"off={out['F1a']['current_rank_off']} delta={out['F1a']['delta']}\n"
              f"       {out['F1a']['reading']}\n")

    if "2" in stages:
        print("STAGE 2 — their metric, 2x2 factorial + full context")
        gpu = preflight.require_gpu(allow_contended=a.allow_contended_gpu)
        llm = default_answerer()
        try:
            probe = llm.require()
        except Exception as e:                                       # noqa: BLE001
            out["stage2"] = []
            out["stage2_not_measured"] = f"answerer unavailable: {e}"
            out["F1"] = verdict_f1([])
            print(f"  NOT-MEASURED: {e}")
        else:
            print(f"  answerer alive: {probe}")
            out["stage2"] = stage2(rows, llm, n_questions=a.n_questions, k=a.k, workers=a.workers)
            out["F1"] = verdict_f1(out["stage2"])
            out["stage2_operating_point"] = llm.operating_point()
            out["stage2_operating_point"]["gpu_contended"] = bool(gpu.get("gpu_contended"))
            out["stage2_operating_point"]["gpu_state"] = {
                k: gpu.get(k) for k in ("vram_free_mb", "vram_total_mb", "blocking_processes",
                                        "preflight_reason")}
            out["stage2_operating_point"]["latency_admissible"] = False
            out["stage2_operating_point"]["latency_note"] = (
                "Accuracy is contention-invariant; latency is not. No latency claim is made from "
                "stage 2 and none may be quoted from it.")
            print(f"  F1: {out['F1']['verdict']}  {out['F1']['checks']}\n")

    p = pathlib.Path(a.out)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(out, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"wrote {p}")
    red = [out.get(k, {}).get("verdict") for k in ("F1a", "F1")]
    return 2 if "RED" in red else 0


def _version() -> str:
    try:
        import inspeximus
        return getattr(inspeximus, "__version__", "?")
    except Exception:                                                # noqa: BLE001
        return "?"


if __name__ == "__main__":
    raise SystemExit(main())
