"""Read the same untouched store twice, seconds apart. How much moves, and does it cost anything?

WHY THIS EXISTS. Reading the same untouched store twice, seconds apart, changes a large minority of
the top-1 answers -- nothing written in between, no field, value or insertion position altered. A
README paragraph states that with numbers, and a sentence citing a measurement nobody can re-run is
not evidence. It reports three things:

  MOVEMENT -- how many top-1 answers differ between two reads separated by a gap. Expect a lot.
  CONSEQUENCE -- whether that movement costs retrieval accuracy. Expect approximately nothing, with
  a direction that flips.
  GAP CONTROL -- the same measurement at a ZERO gap. Without it, "the ranking depends on when you
  ask" is only an observation about two reads; zero movement at a zero gap is what pins the cause on
  elapsed time.

WHAT THIS DOES *NOT* ESTABLISH: the mechanism. The only clock-dependent input to ranking is the
per-type decay term, which makes it the obvious candidate -- but a synthetic store built with the
same age spread and the same tie structure does not reproduce the movement at all (tried at 120-600
records, ages spread over 2-10 s, both exact-tie and near-tie text). So the explanation is
incomplete, and this file deliberately claims the phenomenon and its bounds rather than the cause.

Run-to-run determinism at a FIXED instant is a different property, it holds, and it is asserted
separately (arm (a) of `reinforce_accuracy_ablation.py`, 0.0000 on every corpus).

THE CONTROL THAT MATTERS, and it corrected the first version of this measurement. Read in natural
conversation order the accuracy change was -0.0063 five times out of five, identical to four decimal
places, which reads as a small systematic degradation. It is not. Records are inserted in
conversation order and LOCOMO gold turns skew late, so gold records are NEWER and the recency channel
flatters them while the store is fresh. Randomising the insert order dissolves it: the change runs
+0.0094 to -0.0219 and is negative in 2 of 5. The suspicious part was the REPRODUCIBILITY -- a
result identical to four decimals across five runs is measuring the fixture, not the world.

Run: python probes/recall_over_a_time_gap.py
"""

import argparse
import json
import os
import random
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import reinforce_accuracy_ablation as A  # noqa: E402  (the corpus loader + scorer live there)

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "recall_over_a_time_gap.result.json")


def _sweep(store, idx, sample):
    """One pass over the questions. reinforce=False: a NON-mutating read, so the store is untouched."""
    hit1 = hitk = 0
    tops, scores = [], []
    for text, gold in sample:
        gs = set(gold) if isinstance(gold, (list, tuple)) else {gold}
        hits = store.recall(text, k=A.K, reinforce=False) or []
        a, b, top = A._score(hits, idx, gs)
        hit1 += a
        hitk += b
        tops.append(top)
        scores.append([h.get("score") for h in hits[:2]])
    return hit1, hitk, tops, scores


def _one_corpus(records, questions, gap, order_seed):
    order = None
    if order_seed is not None:
        order = list(range(len(records)))
        random.Random(order_seed).shuffle(order)
    store, idx = A.build_store(records, order=order)
    sample = questions if len(questions) <= 80 else random.Random(7).sample(questions, 80)
    a1, ak, tops_a, scores_a = _sweep(store, idx, sample)
    time.sleep(gap)
    b1, bk, tops_b, scores_b = _sweep(store, idx, sample)
    moved = [i for i, (x, y) in enumerate(zip(tops_a, tops_b)) if x != y]
    # Of the answers that moved, how many moved between records the API reports as EQUALLY scored?
    # `score` is rounded to 3 decimals on the way out, so "tied" here means tied at the resolution a
    # caller can actually see -- the band inside which a swap is not a ranking change to anyone.
    within_tie = 0
    for i in moved:
        pair = scores_a[i]
        if len(pair) >= 2 and pair[0] is not None and pair[1] is not None and pair[0] == pair[1]:
            within_tie += 1
    n = len(sample)
    return {"n_questions": n, "hit1_before": a1 / n, "hit1_after": b1 / n,
            "hitk_before": ak / n, "hitk_after": bk / n,
            "moved": len(moved), "moved_within_displayed_tie": within_tie}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--gap", type=float, default=2.0, help="seconds between the two reads")
    ap.add_argument("--convs", type=int, default=4)
    ap.add_argument("--shuffles", type=int, default=5)
    ap.add_argument("--locomo", default=None)
    a = ap.parse_args(argv)

    path = A._locomo_candidates(a.locomo)
    if not path:
        print("locomo10.json not found; pass --locomo PATH")
        return 2
    convs = A.load_locomo(path, max_convs=a.convs)

    def run(order_seed):
        rows = [_one_corpus(r, q, a.gap, order_seed) for _lbl, r, q in convs]
        n = sum(x["n_questions"] for x in rows)
        agg = {k: sum(x[k] * x["n_questions"] for x in rows) / n
               for k in ("hit1_before", "hit1_after", "hitk_before", "hitk_after")}
        agg["n"] = n
        agg["moved"] = sum(x["moved"] for x in rows)
        agg["moved_within_displayed_tie"] = sum(x["moved_within_displayed_tie"] for x in rows)
        agg["delta_hit1"] = agg["hit1_after"] - agg["hit1_before"]
        agg["delta_hitk"] = agg["hitk_after"] - agg["hitk_before"]
        return agg

    print(f"  MOVEMENT AND ITS CONSEQUENCE across a {a.gap:.1f}s gap on an untouched store "
          f"({len(convs)} LOCOMO conversations)\n")
    print(f"  {'insert order':<24}{'hit@1 t0':>10}{'hit@1 t1':>10}{'delta@1':>10}{'delta@k':>10}"
          f"{'moved':>12}{'of those, tied':>16}")
    natural = run(None)
    print(f"  {'natural (conversation)':<24}{natural['hit1_before']:>10.4f}{natural['hit1_after']:>10.4f}"
          f"{natural['delta_hit1']:>+10.4f}{natural['delta_hitk']:>+10.4f}"
          f"{natural['moved']:>8}/{natural['n']}{natural['moved_within_displayed_tie']:>16}")
    shuffled = []
    for i in range(a.shuffles):
        seed = 11 * (i + 1)
        r = run(seed)
        r["order_seed"] = seed
        shuffled.append(r)
        print(f"  {'shuffled seed %-3d' % seed:<24}{r['hit1_before']:>10.4f}{r['hit1_after']:>10.4f}"
              f"{r['delta_hit1']:>+10.4f}{r['delta_hitk']:>+10.4f}"
              f"{r['moved']:>8}/{r['n']}{r['moved_within_displayed_tie']:>16}")

    # THE CONTROL THAT NAMES THE CAUSE. Without it "the ranking depends on when you ask" is just an
    # observation about two reads; with it, zero movement at a zero gap says the gap is what does it.
    lbl0, recs0, qs0 = convs[0]
    gap_sweep = []
    for g in (0.0, 0.0, a.gap, 5 * a.gap):
        row = _one_corpus(recs0, qs0, g, None)
        gap_sweep.append({"gap_seconds": g, "moved": row["moved"], "n": row["n_questions"]})
    print(f"\n  GAP CONTROL on {lbl0} ({len(recs0)} records): "
          + ", ".join(f"{r['gap_seconds']:.1f}s -> {r['moved']}/{r['n']}" for r in gap_sweep))
    if gap_sweep[0]["moved"] == 0 and gap_sweep[2]["moved"] > 0:
        print("  Zero movement at a zero gap, movement at a gap: elapsed time is the cause, not the read.")
    else:
        print("  WARNING: the zero-gap control did NOT come out clean; the attribution to elapsed time "
              "is unsupported on this run and the README sentence should not be trusted until it does.")

    d1 = [r["delta_hit1"] for r in shuffled]
    neg = sum(1 for x in d1 if x < 0)
    print(f"\n  CONSEQUENCE: over randomised insert orders the hit@1 change runs {min(d1):+.4f} to "
          f"{max(d1):+.4f},\n  negative in {neg} of {len(d1)}. The direction depends on which of two "
          f"tied records is newer,\n  which is what makes them ties. The movement is real; a "
          f"systematic accuracy cost is not.")
    print(f"\n  THE CONTROL: in natural conversation order alone this reads {natural['delta_hit1']:+.4f} "
          f"and reproduces to four\n  decimals every run -- gold turns skew late, so gold records are "
          f"newer and the fresh store flatters\n  them. A result that stable across runs was measuring "
          f"the fixture.")

    payload = {"probe": "recall_over_a_time_gap", "gap_seconds": a.gap,
               "locomo_path": path, "n_conversations": len(convs),
               "natural_order": natural, "shuffled_orders": shuffled,
               "shuffled_delta_hit1_min": min(d1), "shuffled_delta_hit1_max": max(d1),
               "shuffled_delta_hit1_negative_of": [neg, len(d1)],
               "gap_control": gap_sweep,
               "note": "movement is large and real; its accuracy consequence has no consistent sign "
                       "once insert order is randomised. Run-to-run determinism at a fixed instant is "
                       "a different property and holds (reinforce_accuracy_ablation arm a = 0.0000)."}
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=1)
    print(f"\nwrote {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
