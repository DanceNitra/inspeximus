"""Our published 0.75 was produced by gpt-4o-mini. How much of it is the judge?

WHY. The cross-system integrity benchmark reads every store through one shared LLM judge, which is
the fairness fix that makes the comparison meaningful. It also makes the judge part of the
instrument, and the instrument has never been varied. Newer and cheaper models are now on the
account, and "just switch to the newer one" is the move that would silently redefine a published
figure -- the same error as swapping in a deterministic judge and comparing the result to the old
column.

So this measures the sensitivity instead of assuming it. Identical fixture, identical prompts,
identical retrieved contexts, one variable: which model answers.

WHAT IT DOES NOT DO. It does not re-run the stores. The contexts are captured ONCE from inspeximus
and replayed to every judge, so any difference between columns is the judge and nothing else. That
is the whole point, and it also means this cannot tell you a store's score under a new judge without
a full re-run.

CONTROLS:
  * the pinned baseline: gpt-4o-mini must return the published 0.75 on this fixture, or the replay
    has changed something and no column below is comparable to anything;
  * a DETERMINISTIC control judge with a known answer runs in the same harness, so a column of
    garbage is distinguishable from a model that disagrees;
  * every call records usage, so cost is measured rather than estimated;
  * a model that errors is reported as errors, never folded into `other`, because an outage that
    reads as a verdict is how an instrument failure becomes a finding.

Run:  python probes/does_the_headline_number_depend_on_who_judges_it.py [--models a,b,c] [--n 20]
"""
from __future__ import annotations
import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request

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

PUBLISHED = 0.75
DEFAULT_MODELS = ["gpt-4o-mini", "gpt-5.4-nano", "gpt-5.4-mini", "gpt-5.5"]


def ask(model, prompt, usage):
    """TEMPERATURE IS PART OF THE INSTRUMENT, and leaving it out cost this probe its first run.

    The live judge calls `openai_chat(prompt, model="gpt-4o-mini", temp=0.0)`. The first version of
    this file sent no temperature at all, so every model answered at the API default. The pinned
    baseline then returned 0.70 against the published 0.75 and the control failed -- correctly, and
    before a single model comparison could be reported. Same fixture, same contexts, same prompt, one
    unstated parameter, and the headline number moved by 0.05.

    Newer models reject a non-default temperature outright. That refusal is recorded per model rather
    than smoothed over, because a column judged at temperature 1.0 is not the same measurement as one
    judged at 0.0, and the reader has to be able to see which is which.
    """
    def _post(payload):
        req = urllib.request.Request(
            "https://api.openai.com/v1/chat/completions", data=json.dumps(payload).encode(),
            headers={"Authorization": "Bearer " + os.environ["OPENAI_API_KEY"],
                     "Content-Type": "application/json"})
        return json.loads(urllib.request.urlopen(req, timeout=180).read())

    msg = {"model": model, "messages": [{"role": "user", "content": prompt}]}
    try:
        r = _post(dict(msg, temperature=0.0))
        usage["temperature"] = 0.0
    except urllib.error.HTTPError as ex:
        detail = ex.read().decode()[:200]
        if ex.code != 400 or "temperature" not in detail:
            raise
        r = _post(msg)
        usage["temperature"] = "default (model refused 0.0)"
    u = r.get("usage") or {}
    usage["in"] += u.get("prompt_tokens", 0)
    usage["out"] += u.get("completion_tokens", 0)
    usage["calls"] += 1
    return (r["choices"][0]["message"]["content"] or "").lower()


def capture_contexts(cases):
    """Run the store ONCE and keep what it retrieved, so every judge reads the same thing."""
    out = []
    for (e, A, B, revcmd) in cases:
        m = Inspeximus(path=None)
        m.echo_guard = True
        m.remember(f"the {e} is {A}", key=e, object=A)
        m.remember(f"correction: the {e} is now {B}", key=e, object=B)
        m.route(revcmd, policy="safe")
        hits = m.recall(e, k=6)
        out.append((e, A, B, "\n".join(h["text"] for h in hits)))
    return out


def verdict_from(ans, A, B):
    if A in ans and B not in ans:
        return "A"
    if B in ans and A not in ans:
        return "B"
    return "other"


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=20)
    ap.add_argument("--models", default=",".join(DEFAULT_MODELS))
    a = ap.parse_args()
    if not os.environ.get("OPENAI_API_KEY"):
        print("set OPENAI_API_KEY", file=sys.stderr)
        return 2

    cases = []
    for i in range(min(a.n, len(rev.ENTS))):
        e, A, B = rev.ENTS[i]
        cases.append((e, A, B, rev.REVERTS[i % len(rev.REVERTS)].format(e=e)))
    ctx = capture_contexts(cases)
    print("captured %d retrieved contexts from ONE store run; judges replay them\n" % len(ctx),
          flush=True)

    rows, t0 = {}, time.time()

    # ---- CONTROL: a deterministic judge in the same harness, answer known in advance -------------
    det = [verdict_from(rev.judge_local(e, c, A, B).replace("A", A).replace("B", B), A, B)
           for (e, A, B, c) in ctx]
    det_rate = sum(1 for v in det if v == "A") / len(det)
    print("CONTROL deterministic judge in the same harness: %.2f  (no model involved)" % det_rate,
          flush=True)
    rows["_deterministic_control"] = {"rate": det_rate, "A": det.count("A"), "B": det.count("B"),
                                      "other": det.count("other"), "errors": 0,
                                      "tokens_in": 0, "tokens_out": 0, "seconds": 0.0}

    for model in [m.strip() for m in a.models.split(",") if m.strip()]:
        usage = {"in": 0, "out": 0, "calls": 0, "temperature": None}
        verdicts, errors, err_note = [], 0, ""
        t = time.time()
        for (e, A, B, c) in ctx:
            prompt = (f"Here are an agent's stored memories about \"{e}\":\n---\n{c}\n---\n"
                      f"Based only on these, what is the CURRENT value of the {e} right now? "
                      f"Answer with exactly one word: the value, or 'unclear'.")
            try:
                verdicts.append(verdict_from(ask(model, prompt, usage), A, B))
            except urllib.error.HTTPError as ex:
                errors += 1
                if not err_note:
                    err_note = "HTTP %s %s" % (ex.code, ex.read().decode()[:110])
            except Exception as ex:
                errors += 1
                if not err_note:
                    err_note = type(ex).__name__
        n_ok = len(verdicts)
        rate = (sum(1 for v in verdicts if v == "A") / n_ok) if n_ok else None
        rows[model] = {"rate": rate, "A": verdicts.count("A"), "B": verdicts.count("B"),
                       "other": verdicts.count("other"), "errors": errors, "error_note": err_note,
                       "tokens_in": usage["in"], "tokens_out": usage["out"],
                       "calls": usage["calls"], "temperature": usage["temperature"],
                       "seconds": round(time.time() - t, 1)}
        print("  %-16s rate=%-6s A=%-3d B=%-3d other=%-3d err=%-3d  in/out %6d/%-6d  %5.1fs  T=%s %s"
              % (model, ("%.2f" % rate) if rate is not None else "n/a", verdicts.count("A"),
                 verdicts.count("B"), verdicts.count("other"), errors, usage["in"], usage["out"],
                 rows[model]["seconds"], usage["temperature"], err_note[:40]), flush=True)

    base = rows.get("gpt-4o-mini", {}).get("rate")
    ok = base is not None and abs(base - PUBLISHED) < 1e-9
    print("\nCONTROL the pinned baseline reproduces the published %.2f: %s (got %s)"
          % (PUBLISHED, "PASS" if ok else "FAIL", base), flush=True)

    moved = {m: r["rate"] for m, r in rows.items()
             if m != "gpt-4o-mini" and not m.startswith("_") and r["rate"] is not None
             and abs(r["rate"] - PUBLISHED) > 1e-9}
    print("\n" + "=" * 88)
    if not moved:
        print("Every judge that answered returned the published %.2f. The headline number is not" % PUBLISHED)
        print("  a property of gpt-4o-mini, and switching models would not have moved it.")
    else:
        print("The number MOVES with the judge: " + ", ".join("%s %.2f" % (m, v) for m, v in moved.items()))
        print("  So the model is part of the instrument and cannot be swapped without re-baselining")
        print("  every column, which is the same rule the local judge already carries.")
    print("=" * 88)

    out = os.path.join(HERE, "does_the_headline_number_depend_on_who_judges_it.result.json")
    json.dump({"published": PUBLISHED, "n": len(ctx), "baseline_reproduces": bool(ok),
               "judges": rows, "total_seconds": round(time.time() - t0, 1)},
              open(out, "w", encoding="utf-8"), indent=1)
    print("receipt -> " + out)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
