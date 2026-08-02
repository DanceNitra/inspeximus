"""Phase 1 — judge calibration. The LOCOMO run is VOID unless this passes.

Same shape as `benchmarks/memops/judge_calibration.py`, adapted to the LOCOMO QA metric: we are not
using the LOCOMO / mem0 authors' judge (gpt-4o is not available to us), so before trusting any number
we prove our judge can separate the three things the whole score depends on:

  A. GOLD    -> feed the gold answer verbatim.                Judge must say YES.
  B. WRONG   -> feed ANOTHER question's gold answer.          Judge must say NO.
  C. REFUSAL -> feed "I don't know / not in the memory".      Judge must say NO.

Gate: >= 90% correct on each arm, else the judge is unfit and the run does not happen.

The WRONG arm is the one that matters most here. The floor controls in run.py only mean something if a
judge that is handed an unrelated answer says NO — a lenient judge would raise the floor and the whole
band with it, and the harness would report a healthy-looking number for a broken measurement.

Cases are built from the real dataset (never hand-written), round-robin across conversations so the arm
is not twelve probes from one scenario. Zero cost beyond the local models named in config.json.

RUN:  python benchmarks/locomo/judge_calibration.py [--cases 12]
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import harness as H  # noqa: E402

REFUSALS = ["I don't know.",
            "The memory does not contain that information.",
            "There is nothing in the conversation about that.",
            "I could not find an answer in the provided memory."]


def build_cases(data, cfg, limit):
    """GOLD / WRONG / REFUSAL cases, round-robin across conversations, deterministic under cfg.seed."""
    rnd = random.Random(cfg["seed"])
    per_conv = []
    for sample in data:
        qs = H.answerable_questions(sample, cfg)
        if len(qs) >= 2:
            per_conv.append(qs)
    gold, wrong, refusal = [], [], []
    depth = 0
    while (min(len(gold), len(wrong), len(refusal)) < limit
           and any(len(qs) > depth for qs in per_conv)):
        for qs in per_conv:
            if len(qs) <= depth:
                continue
            q = qs[depth]
            g = str(q.get("answer", "")).strip()
            if not g:
                continue
            base = {"question": q["question"], "gold": g, "category": q.get("category")}
            gold.append({**base, "arm": "GOLD", "pred": g, "want": True})
            # WRONG: another question's gold answer from the SAME conversation. Skipped when the two
            # answers coincide -- an identical string is not a wrong answer, and scoring it as one
            # would measure the fixture, not the judge.
            other = [o for o in qs
                     if str(o.get("answer", "")).strip()
                     and str(o.get("answer", "")).strip().lower() != g.lower()]
            if other:
                o = rnd.choice(other)
                wrong.append({**base, "arm": "WRONG", "pred": str(o["answer"]).strip(), "want": False})
            refusal.append({**base, "arm": "REFUSAL", "pred": rnd.choice(REFUSALS), "want": False})
        depth += 1
    return gold[:limit], wrong[:limit], refusal[:limit]


def run_arm(llm, cfg, cases, label):
    ok = bad = parse_fail = 0
    rows = []
    for c in cases:
        verdict, raw = H.judge_answer(llm, cfg, c["question"], c["gold"], c["pred"])
        if verdict is None:
            parse_fail += 1
            hit = False
        else:
            hit = (verdict == c["want"])
        ok += hit
        bad += not hit
        rows.append({"arm": label, "category": c["category"], "question": c["question"][:90],
                     "gold": c["gold"][:60], "pred": c["pred"][:60], "want": c["want"],
                     "verdict": verdict, "raw": raw[:40], "correct": bool(hit)})
        print(f"    {label:8} want={str(c['want']):5} got={str(verdict):5} "
              f"{'OK' if hit else 'WRONG'}  {c['question'][:56]}", flush=True)
    n = ok + bad
    return {"n": n, "correct": ok, "wrong": bad, "parse_fail": parse_fail,
            "rate": round(ok / n, 3) if n else 0.0, "rows": rows}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--config", default=None)
    ap.add_argument("--data", default=None, help="path to locomo10.json")
    ap.add_argument("--cases", type=int, default=None, help="cases per arm (default from config)")
    ap.add_argument("--allow-dataset-drift", action="store_true")
    ap.add_argument("--out", default=None)
    a = ap.parse_args(argv)

    cfg = H.load_config(a.config)
    limit = a.cases or cfg["judge_gate"]["cases_per_arm"]
    try:
        path = H.resolve_dataset(cfg, a.data)
        ds = H.check_dataset_sha(cfg, path, a.allow_dataset_drift)
    except H.DatasetMissing as e:
        print("SKIP — " + str(e))
        return 3

    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)

    llm = H.LLM(cfg)
    probe = llm.probe(cfg["qa"]["judge_model"])
    print(f"judge liveness: {probe}\n", flush=True)
    if not probe["alive"]:
        print("JUDGE GATE: FAILED — the judge model did not answer at all.")
        return 1

    gold, wrong, refusal = build_cases(data, cfg, limit)
    print(f"cases: GOLD={len(gold)}  WRONG={len(wrong)}  REFUSAL={len(refusal)}\n", flush=True)

    out = {"judge_model": cfg["qa"]["judge_model"], "min_rate": cfg["judge_gate"]["min_rate"],
           "dataset": ds, "liveness_probe": probe, "prompts": H.prompt_fingerprints(), "arms": {}}
    for label, cases in (("GOLD", gold), ("WRONG", wrong), ("REFUSAL", refusal)):
        if not cases:
            out["arms"][label] = {"n": 0, "rate": 0.0, "why": "no cases could be built"}
            print(f"  -- {label}: NO CASES -> the arm has measured nothing")
            continue
        print(f"  -- {label} --", flush=True)
        out["arms"][label] = run_arm(llm, cfg, cases, label)
        print(f"  => {label}: {out['arms'][label]['correct']}/{out['arms'][label]['n']} = "
              f"{out['arms'][label]['rate']:.0%}\n", flush=True)

    # An arm with no cases FAILS the gate. A gate that skips the arm it cannot build reports SAFE for a
    # judge it never tested.
    gate = bool(out["arms"]) and all(v.get("n") and v.get("rate", 0) >= out["min_rate"]
                                     for v in out["arms"].values())
    out["gate_passed"] = gate
    out["llm"] = llm.stats()

    dest = a.out or os.path.join(H.RESULTS_DIR, "judge_calibration.json")
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    with open(dest, "w", encoding="utf-8") as fh:
        json.dump(out, fh, indent=1, ensure_ascii=False)

    print("=" * 66)
    print("JUDGE GATE:", "PASSED — the run may proceed" if gate else "FAILED — the run is VOID")
    for k, v in out["arms"].items():
        print(f"   {k:8} {v.get('correct', 0)}/{v.get('n', 0)} = {v.get('rate', 0):.0%}"
              + (f"  (parse_fail {v['parse_fail']})" if v.get("parse_fail") else ""))
    print(f"   saved {dest}")
    return 0 if gate else 1


if __name__ == "__main__":
    raise SystemExit(main())
