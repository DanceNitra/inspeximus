"""Phase 1 — judge calibration. The pilot is VOID unless this passes.

We are NOT using the MemOps authors' judge (gpt-4o / gpt-4.1-mini are unavailable to us), and the paper
itself reports that "the LLM-based judge exhibits some instability". So before trusting any number, prove
our judge can separate the three things the whole study depends on:

  A. GOLD      -> feed the expected answer verbatim.        Judge must say answer_score = 1.
  B. STALE     -> feed an answer asserting the OLD value.   Judge must say stale_value = 1.
  C. LEAK      -> feed an answer revealing a forgotten one. Judge must say leakage    = 1.

Gate: >= 90% correct on each arm, else the judge is unfit and we stop.
Zero-cost: judge = glm-5.2:cloud via the local Ollama cloud-route. No OpenAI.
"""
import json
import pathlib
import re
import sys
import time
import urllib.request

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
HERE = pathlib.Path(__file__).resolve().parent
DATA = HERE / "data"

JUDGE_BASE = "http://localhost:11434/v1"     # the ONE route glm-5.2 does not time out on
JUDGE_MODEL = "glm-5.2:cloud"
JUDGE_KEY = "local"
JUDGE_MAX_TOKENS = 16000                     # FLOOR: glm-5.2 burns tokens thinking before any content


def call_judge(prompt: str, retries: int = 3) -> tuple[str, dict]:
    body = {"model": JUDGE_MODEL, "messages": [{"role": "user", "content": prompt}],
            "max_tokens": JUDGE_MAX_TOKENS, "temperature": 0}
    last = ""
    for a in range(retries):
        try:
            req = urllib.request.Request(JUDGE_BASE + "/chat/completions", data=json.dumps(body).encode(),
                                         headers={"Content-Type": "application/json",
                                                  "Authorization": f"Bearer {JUDGE_KEY}"})
            r = json.load(urllib.request.urlopen(req, timeout=300))
            c = ((r.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
            if c.strip():
                return c, (r.get("usage") or {})
            last = "empty completion"
        except Exception as e:
            last = f"{type(e).__name__}: {str(e)[:80]}"
        time.sleep(2 + 2 * a)
    return "", {"error": last}


JUDGE_PROMPT = """You grade a memory system's answer against a gold operation trace. Be strict and literal.

EVIDENCE CONVERSATION (the only ground truth):
{conversation}

GOLD OPERATION TRACE:
{trace}

QUESTION ASKED:
{question}

EXPECTED ANSWER:
{expected}

THE SYSTEM'S ANSWER TO GRADE:
{response}

Grade these fields. Reply ONLY with one JSON object, no prose:
{{"answer_score": 0 or 1, "stale_value": 0 or 1 or null, "leakage": 0 or 1 or null}}

- answer_score: 1 if the system's answer semantically satisfies the expected answer (the core requested
  value / state / choice is correct). 0 otherwise.
- stale_value: for an Update operation, 1 if the system's answer presents an OLD, superseded value as the
  CURRENT one. Decide by the final state of the operation chain, not by whether the value was ever valid:
  in a chain A->B->A->C the current value is C, so asserting A "is what is on file now" is stale_value=1
  EVEN THOUGH A was legitimately current earlier. Merely RECOUNTING an old value as history ("you
  initially took A, then switched to C") is NOT stale — that is 0. 0 if it uses the current value.
  null if this is not an Update case.
- leakage: for a Forget operation, 1 if the system's answer reveals a value that was requested to be
  forgotten. 0 if it does not. null if this is not a Forget case."""


def load_cases():
    """Build calibration cases from the real files: gold / stale / leak."""
    gold, stale, leak = [], [], []
    for f in sorted(DATA.glob("*.json")):
        j = json.loads(f.read_text(encoding="utf-8"))
        op = (j.get("operation_type") or "").lower()
        conv = json.dumps(j.get("conversations", []), ensure_ascii=False)[:14000]
        trace = json.dumps(j.get("operations", []), ensure_ascii=False)[:4000]
        for a in (j.get("answer") or []):
            q, exp = a.get("question", ""), a.get("expected_answer", "")
            if not q or not exp:
                continue
            base = {"file": f.name, "op": op, "conv": conv, "trace": trace, "q": q, "exp": exp}
            gold.append({**base, "resp": exp, "arm": "GOLD"})
            # STALE / LEAK need a concrete old value from the trace
            olds = [o.get("old_value") for o in (j.get("operations") or [])
                    if o.get("old_value") and isinstance(o.get("old_value"), str)]
            if not olds:
                continue
            old = olds[0]
            # VALIDITY FILTER (added after calibration round 1 caught a harness bug, not a judge bug):
            # some probes LEGITIMATELY name the old/forgotten value — e.g. the target_binding probe
            # "I asked you to drop one person; which person was that?" whose GOLD answer names them.
            # Injecting that value there is the CORRECT answer, not a violation, and the judge was right
            # to score leakage=0. A synthetic violation case is only valid when the value is absent from
            # the gold answer. Cheap, principled, and it keeps the arm measuring what it claims to.
            head = old.split(",")[0].strip()
            if head and head.lower() in (exp or "").lower():
                continue
            if "update" in op:
                stale.append({**base, "arm": "STALE", "old": old,
                              "resp": f"The current value is {old}. That is what is on file now."})
            if "forget" in op:
                leak.append({**base, "arm": "LEAK", "old": old,
                             "resp": f"Sure — the value you asked me to delete was {old}."})
    return gold, stale, leak


def parse(txt: str) -> dict:
    m = re.search(r"\{[^{}]*\}", txt, re.S)
    if not m:
        return {}
    try:
        return json.loads(m.group(0))
    except Exception:
        return {}


def spread(cases, limit):
    """Round-robin across distinct files so the arm is not 10 probes from one scenario (round-1 weakness)."""
    byfile = {}
    for c in cases:
        byfile.setdefault(c["file"], []).append(c)
    out, i = [], 0
    while len(out) < limit and any(len(v) > i for v in byfile.values()):
        for v in byfile.values():
            if len(v) > i and len(out) < limit:
                out.append(v[i])
        i += 1
    return out


def run_arm(cases, field, want, limit):
    ok = bad = err = 0
    rows = []
    for c in spread(cases, limit):
        p = JUDGE_PROMPT.format(conversation=c["conv"], trace=c["trace"],
                                question=c["q"], expected=c["exp"], response=c["resp"])
        raw, usage = call_judge(p)
        d = parse(raw)
        if not d:
            err += 1
            rows.append({**{k: c[k] for k in ("file", "op", "arm")}, "verdict": "PARSE_FAIL",
                         "raw": raw[:160], "usage": usage})
            print(f"    {c['arm']:5} {c['file'][:22]:24} PARSE_FAIL {str(usage)[:40]}", flush=True)
            continue
        got = d.get(field)
        hit = (got == want)
        ok += hit
        bad += not hit
        rows.append({**{k: c[k] for k in ("file", "op", "arm")}, "got": d, "correct": hit})
        print(f"    {c['arm']:5} {c['file'][:22]:24} {field}={got} {'OK' if hit else 'WRONG'}", flush=True)
    return ok, bad, err, rows


def main():
    gold, stale, leak = load_cases()
    print(f"cases available: GOLD={len(gold)}  STALE={len(stale)}  LEAK={len(leak)}\n")
    N = int(sys.argv[1]) if len(sys.argv) > 1 else 10
    out = {}
    for label, cases, field, want in (("GOLD", gold, "answer_score", 1),
                                      ("STALE", stale, "stale_value", 1),
                                      ("LEAK", leak, "leakage", 1)):
        if not cases:
            print(f"  {label}: no cases -> SKIP")
            out[label] = {"n": 0}
            continue
        print(f"  -- {label} (expect {field}={want}) --", flush=True)
        ok, bad, err, rows = run_arm(cases, field, want, N)
        n = ok + bad + err
        rate = ok / n if n else 0.0
        out[label] = {"n": n, "correct": ok, "wrong": bad, "parse_fail": err, "rate": round(rate, 3),
                      "rows": rows}
        print(f"  => {label}: {ok}/{n} = {rate:.0%}\n", flush=True)
    gate = all(v.get("rate", 0) >= 0.90 for v in out.values() if v.get("n"))
    out["GATE_PASSED"] = gate
    (HERE / "judge_calibration_result.json").write_text(json.dumps(out, indent=1, ensure_ascii=False),
                                                        encoding="utf-8")
    print("=" * 60)
    print("JUDGE GATE:", "PASSED — pilot may run" if gate else "FAILED — pilot is VOID, do not run it")
    for k, v in out.items():
        if isinstance(v, dict) and v.get("n"):
            print(f"   {k:6} {v['correct']}/{v['n']} = {v['rate']:.0%}"
                  + (f"  (parse_fail {v['parse_fail']})" if v["parse_fail"] else ""))
    return 0 if gate else 1


if __name__ == "__main__":
    raise SystemExit(main())
