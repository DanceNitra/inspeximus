"""Our published 0.75 is the middle of a band, and the band comes from the judge, not the store.

WHY THIS EXISTS. The benchmark on the site reads every store through one shared LLM judge at
temperature 0.0. Two runs an hour apart both returned exactly 0.75, which was reported as "reproduces
to the digit". A third, same command, same model, same temperature, returned 0.70. The claim of exact
reproduction was two agreeing samples, not determinism, and it had already been written onto the
public page as "re-measured and unchanged".

WHAT THIS SEPARATES. Two candidate sources of that movement, and only one of them is ours:

  ARM 1  the STORE -- does inspeximus return the same retrieved contexts on every run?
  ARM 2  the JUDGE -- given byte-identical contexts, does the model return the same verdicts?

Measured 2026-08-22, n=20 fixture, gpt-4o-mini at temperature 0.0:

    store   5 runs -> 1 distinct context set                      DETERMINISTIC
    judge   8 runs -> 0.70, 0.75 x6, 0.80                         spread 0.10

So the product is not the source of the variance and the instrument is. For scale: replaying the same
contexts through the OTHER judges that accept temperature 0.0 moved the figure by 0.05, one case in
twenty. Re-running the SAME judge moves it by twice that. Which model judges you matters less than
which run you happened to publish.

AND B IS ZERO IN EVERY RUN AND EVERY COLUMN. No judge, on any model, ever answered that the
superseded value was the current one. The band is entirely the model choosing between the right
answer and "unclear", so it measures the judge's willingness to commit on an ambiguous context, not
whether the revert worked. A deterministic string rule scores the same contexts 1.00.

CONTROLS:
  * the store arm must come out DETERMINISTIC, or the judge arm is confounded and says nothing;
  * the contexts handed to the judge are captured ONCE and reused byte-for-byte, asserted here rather
    than assumed;
  * B is reported per run, because a spread that ever contained B would be a different and much worse
    finding than a spread that never does.

Run:  python probes/the_judge_is_not_deterministic_at_temperature_zero.py [--runs 8] [--n 20]
Needs OPENAI_API_KEY. 20 short judge calls per run.
"""
from __future__ import annotations
import argparse
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, ".."))

# A reader who clicks the link in the post downloads THIS file and nothing else, and used to
# get a bare ModuleNotFoundError here -- before ever reaching the OPENAI_API_KEY check further
# down, so the "set OPENAI_API_KEY" message this file already prints was unreachable for them.
if not os.path.exists(os.path.join(HERE, "integrity_bench_revert.py")):
    sys.stderr.write(
        "\n"
        "This probe needs integrity_bench_revert.py beside it, and it is not here.\n"
        "  expected: " + os.path.join(HERE, "integrity_bench_revert.py") + "\n"
        "  get it:   curl -O https://raw.githubusercontent.com/DanceNitra/inspeximus/main/probes/integrity_bench_revert.py\n"
        "Then run this file again from the same directory. It also needs OPENAI_API_KEY.\n\n")
    raise SystemExit(2)
import integrity_bench_revert as rev  # noqa: E402
from inspeximus import Inspeximus     # noqa: E402


def build_cases(n):
    return [(e, A, B, rev.REVERTS[i % len(rev.REVERTS)].format(e=e))
            for i, (e, A, B) in enumerate(rev.ENTS[:n])]


def contexts(cases):
    out = []
    for (e, A, B, revcmd) in cases:
        m = Inspeximus(path=None)
        m.echo_guard = True
        m.remember("the %s is %s" % (e, A), key=e, object=A)
        m.remember("correction: the %s is now %s" % (e, B), key=e, object=B)
        m.route(revcmd, policy="safe")
        out.append("\n".join(h["text"] for h in m.recall(e, k=6)))
    return out


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=8)
    ap.add_argument("--n", type=int, default=20)
    a = ap.parse_args()
    if not os.environ.get("OPENAI_API_KEY"):
        print("set OPENAI_API_KEY", file=sys.stderr)
        return 2

    cases = build_cases(a.n)
    ok = True

    # ---- ARM 1 / CONTROL: the store must be deterministic or arm 2 means nothing ----------------
    sigs = [tuple(contexts(cases)) for _ in range(5)]
    store_det = len(set(sigs)) == 1
    print("STORE: 5 runs produced %d distinct context set(s)  ->  %s"
          % (len(set(sigs)), "DETERMINISTIC" if store_det else "NOT deterministic"), flush=True)
    if not store_det:
        print("  the judge arm below would be confounded; stopping", flush=True)
        return 1

    ctx = list(sigs[0])
    assert all(tuple(contexts(cases)) == sigs[0] for _ in range(1)), "contexts drifted mid-run"

    # ---- ARM 2: identical contexts, identical model, identical temperature ----------------------
    rates, rows = [], []
    for r in range(a.runs):
        v = [rev.judge_current(e, c or "(no memories)", A, B)
             for (e, A, B, _), c in zip(cases, ctx)]
        rate = sum(1 for x in v if x == "A") / len(v)
        rates.append(rate)
        rows.append({"run": r + 1, "rate": rate, "A": v.count("A"),
                     "other": v.count("other"), "B": v.count("B")})
        print("  run %d: %.2f  (A=%d other=%d B=%d)"
              % (r + 1, rate, v.count("A"), v.count("other"), v.count("B")), flush=True)

    spread = max(rates) - min(rates)
    any_b = any(x["B"] for x in rows)
    print("\nJUDGE, %s @ T=0.0, byte-identical contexts, %d runs:" % ("gpt-4o-mini", a.runs))
    print("  distinct values: %s" % sorted(set(rates)))
    print("  min %.2f  max %.2f  spread %.2f" % (min(rates), max(rates), spread))
    print("  B (judge said the superseded value is current) in any run: %s" % any_b)

    moved = spread > 0
    print("\n  CONTROL the spread is real, not a constant: %s" % ("PASS" if moved else "FAIL"))
    print("  CONTROL B never fires, so the band is abstention: %s"
          % ("PASS" if not any_b else "FAIL -- a run claimed the stale value; that is a worse finding"))
    ok = ok and store_det and (not any_b)

    print("\n" + "=" * 92)
    print("The store is deterministic and the judge is not. Read the published 0.75 as the middle of")
    print("a %.2f-wide band. Swapping the judge model moved this figure by 0.05, one case in twenty;" % spread)
    print("re-running the same model moves it by %.2f. Which model judges matters less than which" % spread)
    print("run got published.")
    print("=" * 92)

    out = os.path.join(HERE, "the_judge_is_not_deterministic_at_temperature_zero.result.json")
    json.dump({"judge_model": "gpt-4o-mini", "judge_temperature": 0.0, "n": a.n, "runs": a.runs,
               "store_deterministic": store_det, "rates": rates, "spread": spread,
               "per_run": rows, "any_B": any_b,
               "measured_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())},
              open(out, "w", encoding="utf-8"), indent=1)
    print("receipt -> " + out)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
