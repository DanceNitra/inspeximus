"""Is our retrieval gap a capability gap, or did we run our own arm without the lever the other had?

THE OBSERVATION THAT PROMPTED THIS. In the erasure run, the positive control -- "is the value
retrievable BEFORE deletion" -- passed for the DCI-Lite arm on 58 of 69 scenarios and for inspeximus
on 45. On finding things, the LLM-driven harness beat us.

That comparison is not apples to apples, and saying so is not an excuse: DCI-Lite composes its grep
patterns with a model at query time, by construction. Our arm ran bare lexical recall on the raw
probe question. The two arms did not differ only in storage design; one got a model on the read path
and the other did not.

Our constraint is NO MODEL ON THE WRITE PATH -- that is what makes the store byte-reproducible and
erasable. It says nothing about the read path, where the calling agent already has a model, and
`recall_iterative` ships exactly this: the agent writes the follow-up queries. So the fair question
is whether the gap survives when both arms get the same affordance.

WHAT THIS MEASURES. For every scenario where our control FAILED, ask the same local model that drove
the DCI-Lite arm to write search terms from the probe question -- the identical prompt DCI-Lite gets
-- and run inspeximus recall on those terms. Then report how many of the failures recover.

CONTROLS, because this is a measurement we would like to win:
  * the BASELINE arm is re-run here, not read from the earlier file, so both numbers come from the
    same code path on the same day;
  * a scenario only counts as recovered if the value appears where the bare query did NOT find it;
  * and the model gets the SAME prompt and the SAME token floor as the DCI-Lite arm, so any gain is
    the lever and not a better prompt written for our own side.

Run: python retrieval_parity_probe.py [--limit N]
"""
import argparse
import json
import os
import pathlib
import sys
import time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
os.environ.setdefault("MEMOPS_TOPK", "150")
HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import pilot                                                    # noqa: E402
from dci_lite_erasure import (ask, build_inspeximus, deletion_key,  # noqa: E402
                              insp_context, strip_think)
from erasure_revert_probe import norm                            # noqa: E402

MODEL = "qwen3:30b-a3b"


def terms_from_question(question):
    """The DCI-Lite pattern prompt, verbatim, so the comparison is the lever and not the wording."""
    out = strip_think(ask(MODEL,
        "You search a plain-text memory with grep. Write up to 3 case-insensitive regular "
        "expressions that would find the records needed to answer the question.\n"
        f"QUESTION: {question}\n"
        "Reply with ONLY the patterns, one per line, no numbering, no explanation."))
    pats = [p.strip().strip("`") for p in out.splitlines() if p.strip() and len(p.strip()) < 200]
    return pats[:3]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--out", default="retrieval_parity_result.json")
    a = ap.parse_args()

    have = {p.name for p in (HERE / "data_lc").glob("*.json")}
    scen = []
    for f in sorted((HERE / "data").glob("*.json")):
        if f.name not in have:
            continue
        ev = json.loads(f.read_text(encoding="utf-8"))
        ops = [o for o in (ev.get("operations") or []) if o.get("validity") == "confirmed"]
        fg = [o for o in ops if o.get("type") == "forget" and o.get("old_value")]
        if not fg:
            continue
        lc = json.loads((HERE / "data_lc" / f.name).read_text(encoding="utf-8"))
        units = [s for _i, _r, s in pilot.units_of(lc)]
        ret = [o["new_value"] for o in ops if o.get("type") == "remember" and o.get("new_value")]
        key = deletion_key(fg[0]["old_value"], ret, units)
        if key:
            scen.append((f.name, lc, key))
    if a.limit:
        scen = scen[:a.limit]
    print(f"  {len(scen)} scenarios with a usable deletion key\n")

    rows = []
    base_hits = expanded_hits = 0
    for name, lc, key in scen:
        t0 = time.time()
        probes = [x["question"] for x in (lc.get("answer") or []) if x.get("question")][:8]
        if not probes:
            continue
        import tempfile
        import shutil
        d = tempfile.mkdtemp(prefix="parity_")
        try:
            m = build_inspeximus(lc, os.path.join(d, "s.jsonl"))
            base = any(key in insp_context(m, q) for q in probes)
            exp = base
            if not base:                                   # only spend the model where we failed
                for q in probes:
                    for t in terms_from_question(q):
                        if key in insp_context(m, t):
                            exp = True
                            break
                    if exp:
                        break
        finally:
            shutil.rmtree(d, ignore_errors=True)
        base_hits += base
        expanded_hits += exp
        rows.append({"file": name, "key": key, "bare_query": base, "model_written_terms": exp,
                     "recovered": bool(exp and not base), "seconds": round(time.time() - t0, 1)})
        mark = "recovered" if (exp and not base) else ("hit" if base else "still missed")
        print(f"  {name:26} {mark:<13} {rows[-1]['seconds']:>6.1f}s", flush=True)
        (HERE / a.out).write_text(json.dumps(rows, indent=1), encoding="utf-8")

    n = len(rows) or 1
    print(f"\n  ==== retrieval parity, n={len(rows)} ====")
    print(f"    bare lexical query          : {base_hits}/{len(rows)} ({100*base_hits/n:.0f}%)")
    print(f"    + model-written search terms: {expanded_hits}/{len(rows)} ({100*expanded_hits/n:.0f}%)")
    print(f"    recovered by the lever      : {expanded_hits - base_hits}")
    print("    DCI-Lite's own rate on the same scenarios was 58/69 (84%).")
    print(f"\n  wrote {a.out}")


if __name__ == "__main__":
    main()
