"""Phase 0 — judge calibration. Every LongMemEval score this harness produces is VOID unless this passes.

The end-to-end number is an LLM's opinion of another LLM's answer. Before that opinion is allowed to
carry a product claim it has to demonstrate it can separate the three things the whole measurement
rests on, using the real dataset's own questions and gold answers:

  GOLD   feed the gold answer verbatim.                          Judge must say correct = 1.
  WRONG  feed a DIFFERENT question's gold answer.                Judge must say correct = 0.
  HEDGE  feed a refusal ("I don't know").                        Judge must say correct = 0.

GOLD alone proves nothing — a judge that answers 1 to everything passes it. WRONG and HEDGE are the
arms that can fail, and HEDGE is the one that matters most here: the answer prompt instructs the
answerer to say "I don't know" when the retrieved memory is empty, so a judge that scores a refusal
as correct would hand the empty-context CONTROL arm a free score and destroy the band check.

Gate: >= 90% on each arm. Below that the judge is unfit and the pilot does not run.

    python benchmarks/longmemeval/judge_calibration.py [n_per_arm]
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import run as R  # noqa: E402  (the harness itself: same prompts, same client, same models)

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

GATE = 0.90
HEDGES = ["I don't know.",
          "I don't have any information about that in the retrieved memory.",
          "The memory does not contain the answer to this question."]


def build_cases(data: list[dict], n: int) -> dict[str, list[dict]]:
    """Cases come from the real dataset, spread across question types so an arm is not one type.

    WRONG pairs each question with the gold answer of a question of a DIFFERENT type — a same-type
    mismatch ("Business Administration" vs "Computer Science") is a harder discrimination than the
    scoring loop faces, and calibrating on it would report a judge weaker than the one in use.
    """
    pool = R.select_subset(data, min(len(data), max(n * 4, 40)))
    gold, wrong, hedge = [], [], []
    for i, inst in enumerate(pool):
        gold.append({"inst": inst, "resp": inst["answer"], "arm": "GOLD"})
        other = next((o for o in pool[i + 1:] + pool[:i]
                      if o["question_type"] != inst["question_type"]), None)
        if other is not None:
            wrong.append({"inst": inst, "resp": other["answer"], "arm": "WRONG"})
        hedge.append({"inst": inst, "resp": HEDGES[i % len(HEDGES)], "arm": "HEDGE"})
    return {"GOLD": gold[:n], "WRONG": wrong[:n], "HEDGE": hedge[:n]}


def run_arm(cases: list[dict], want: int) -> dict:
    ok = bad = unparsed = 0
    rows = []
    for c in cases:
        verdict, raw = R.judge_one(c["inst"], c["resp"])
        if verdict is None:
            unparsed += 1
            hit = False
        else:
            hit = verdict == want
            ok += hit
            bad += not hit
        rows.append({"qid": c["inst"]["question_id"], "type": c["inst"]["question_type"],
                     "verdict": verdict, "want": want, "correct": hit, "raw": raw[:120]})
        print(f"    {c['arm']:5} {c['inst']['question_id']:14} {c['inst']['question_type']:26} "
              f"correct={verdict} {'OK' if hit else 'WRONG' if verdict is not None else 'UNPARSED'}",
              flush=True)
    n = ok + bad + unparsed
    return {"n": n, "correct": ok, "wrong": bad, "unparsed": unparsed,
            "rate": round(ok / n, 3) if n else 0.0, "rows": rows}


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    allow_contended = "--allow-contended-gpu" in argv
    positional = [x for x in argv if not x.startswith("-")]
    n = int(positional[0]) if positional else 12

    gpu = R.gpu_preflight(allow_contended)
    path = R.resolve_dataset()
    data = R.load_dataset(path)
    pre = R.model_preflight([R.JUDGE])
    if not pre["all_answered"]:
        print("MODEL PRE-FLIGHT FAILED — the judge did not answer a unique prompt correctly.")
        return 4

    cases = build_cases(data, n)
    print(f"\njudge={R.JUDGE}  prompt_sha256={R.PROMPT_HASHES['judge_prompt_sha256'][:16]}…  "
          f"n_per_arm={n}\n", flush=True)

    out: dict = {"judge": R.JUDGE, "gate": GATE, "n_per_arm": n,
                 "generated_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                 "contended": bool(gpu.get("override")), "gpu_preflight": gpu,
                 **R.PROMPT_HASHES}
    for arm, want in (("GOLD", 1), ("WRONG", 0), ("HEDGE", 0)):
        print(f"  -- {arm} (expect correct={want}) --", flush=True)
        out[arm] = run_arm(cases[arm], want)
        print(f"  => {arm}: {out[arm]['correct']}/{out[arm]['n']} = {out[arm]['rate']:.0%}\n",
              flush=True)

    passed = all(out[a]["rate"] >= GATE for a in ("GOLD", "WRONG", "HEDGE"))
    out["GATE_PASSED"] = passed
    out["latency"] = R.LAT.report()
    dest = HERE / "results" / f"judge_calibration_{R.JUDGE.replace(':', '-')}.json"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(out, indent=1, ensure_ascii=False), encoding="utf-8")

    print("=" * 70)
    print("JUDGE GATE:", "PASSED — the pilot may run" if passed
          else "FAILED — any LongMemEval score from this judge is VOID")
    for a in ("GOLD", "WRONG", "HEDGE"):
        print(f"   {a:6} {out[a]['correct']}/{out[a]['n']} = {out[a]['rate']:.0%}"
              + (f"  (unparsed {out[a]['unparsed']})" if out[a]["unparsed"] else ""))
    print(f"saved {dest}")
    return 0 if passed else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except R.DatasetMissing as e:
        print(e.msg, file=sys.stderr)
        raise SystemExit(2) from None
