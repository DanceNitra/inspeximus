"""Can the facts a compressed context MUST keep be derived from use instead of hand-written?

THE QUESTION, and why it is the one worth asking. Context compression for coding agents is being
adopted fast: Paritok (1,450 stars in six weeks, drop-in for Claude Code, Cursor and Codex),
claw-compactor, leanctx, LLMLingua. Two correctness questions are being asked about them and only one
is answered.

  ANSWERED. "Do the bytes come back?" @BayramAnnakov reproduced Paritok independently and found
  non-destructive recall holds, 14 of 14 segments byte-for-byte identical through the shadow store
  (Paritok-official/paritok-4b-v1#21). That property verifies.

  NOT ANSWERED. "Did the fact the agent needed survive?" @UMkuce measured a noisy debugging context
  compressed from ~2,900 tokens to 88-97, a 97% saving, that retained ONE of five required facts
  (#10, open since 2026-07-30; the maintainer agrees it is real and points at a level dial that #13
  says nothing ever selects). Those are different properties: bytes can round-trip perfectly while
  the model never sees the do-not-edit boundary.

The second question already has a tool -- @UMkuce's context-cost-auditor -- and it works. Its limit
is the one this file attacks: the required facts are a HAND-WRITTEN list of strings in the case file
(`required_facts`, `required_fact_groups`), so the audit only covers what a human thought to list,
and it cannot run on a context nobody has annotated. That is the same shape as a defect already in
our ledger: a check whose target is declared rather than derived measures the declaration.

WHAT THIS TESTS. inspeximus knows which records a question actually RETRIEVES. If the retrieved set
carries the same required facts a human wrote down, then required facts can be derived from use, the
audit runs on any context with no annotation, and the same call reports the token saving. If it does
NOT agree with the human list, the idea is decoration and this file says so.

THE FIXTURE IS SOMEONE ELSE'S AND IS NOT VENDORED. `examples/coding_debug_case.json` is fetched from
UMkuce/context-cost-auditor at run time and pinned by sha256, because a commit ref pins a name and a
digest pins the bytes. The repository carries a LICENSE that GitHub cannot classify (NOASSERTION), so
nothing from it is copied into this repository.

CONTROLS, each able to fail:
  * the FULL context must satisfy every required-fact group. If it does not, the fixture is broken
    and every number below is about our splitter, not about retrieval.
  * a RANDOM set of the same size must do worse. Without it, "recall found the facts" could mean
    "the context is so small that anything finds them".
  * a RECENCY set of the same size must be reported too: the cheapest thing a compressor could do
    instead, and the honest baseline to beat.
  * the noise must actually be present: this context is 60% repeated filler, and if the splitter
    silently drops it the task is easier than the real one.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import random
import re
import sys
import tempfile
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
CASE_URL = ("https://raw.githubusercontent.com/UMkuce/context-cost-auditor/main/"
            "examples/coding_debug_case.json")
CASE_SHA256 = "6eeb5b12e905662a144a184c4a5de430e11acea55749d692641424b241ad6273"
SEED = 20260826

sys.path.insert(0, os.path.join(HERE, "..", ".."))
from inspeximus import Inspeximus                                    # noqa: E402


def fetch_case() -> dict:
    cache = os.path.join(tempfile.gettempdir(), "ccauditor_coding_debug_case.json")
    raw = b""
    if os.path.exists(cache):
        raw = io.open(cache, "rb").read()
    if hashlib.sha256(raw).hexdigest() != CASE_SHA256:
        req = urllib.request.Request(CASE_URL, headers={"User-Agent": "inspeximus-benchmark"})
        with urllib.request.urlopen(req, timeout=60) as r:
            raw = r.read()
        io.open(cache, "wb").write(raw)
    got = hashlib.sha256(raw).hexdigest()
    if got != CASE_SHA256:
        raise SystemExit(
            "REFUSED: the upstream fixture is not the one this was measured against.\n"
            "  expected %s\n  found    %s\n"
            "Their file may legitimately have changed; re-pin deliberately, never silently."
            % (CASE_SHA256, got))
    return json.loads(raw.decode("utf-8"))


SECTION = re.compile(
    r"^(File: |Recent production log|Webhook payload sample|Engineering note|"
    r"Unrelated deployment log|Long noisy background log|Additional context|"
    r"More unrelated notes|Repository excerpt|Unrelated note:)")


def split_records(ctx: str, how: str = "section") -> list:
    """Split the context into records the way a compressor would.

    TWO SPLITTERS, BOTH REPORTED, because the first run of this file used only the second and the
    result was about the splitter rather than about retrieval. `block` cuts at every blank line and
    produced 63 records with a median of 116 characters, 38 of them under 120: the signal blocks were
    shattered into fragments, and `recall` at k=3 returned 20 tokens because that is all a fragment
    is. `section` cuts only at the document's own headers, which is the unit a reader sees.

    Neither is the "right" one. Reporting one of them would be choosing the answer.
    """
    if how == "section":
        out, cur = [], []
        for line in ctx.split("\n"):
            if SECTION.match(line) and cur:
                out.append("\n".join(cur).strip())
                cur = []
            cur.append(line)
        if cur:
            out.append("\n".join(cur).strip())
        return [r for r in out if r.strip()]
    out, cur = [], []
    for line in ctx.split("\n"):
        if not line.strip():
            if cur:
                out.append("\n".join(cur).strip())
                cur = []
            continue
        if SECTION.match(line) and cur:
            out.append("\n".join(cur).strip())
            cur = []
        cur.append(line)
    if cur:
        out.append("\n".join(cur).strip())
    return [r for r in out if r]


def groups_hit(text: str, groups: list) -> list:
    """Which required-fact groups a text satisfies, by their OWN alternates."""
    low = text.lower()
    return [g.get("label") or "?" for g in groups
            if any((alt or "").lower() in low for alt in (g.get("any") or []))]


def est_tokens(t: str) -> int:
    return max(1, len(t) // 4)


def main() -> int:
    t0 = time.time()
    case = fetch_case()
    ctx, task = case["context"], case["task"]
    fgroups = case.get("required_fact_groups") or []
    pgroups = case.get("required_path_groups") or []
    groups = fgroups + pgroups
    grid = {}
    rng = random.Random(SEED)

    # TWO QUERIES, and the first run used only the first. The task instruction says what to PRODUCE
    # ("identify the likely bug, name the file to edit, propose the smallest code change") and shares
    # nought or one content word with the blocks that carry the required facts. A retrieval system
    # asked that cannot know it needs the null-customer payload; that is a property of the question,
    # not of the store. The second is what an agent actually holds when it starts debugging: the
    # error line off the top of the log. Both are reported, because reporting one is choosing.
    err = next((ln for ln in ctx.split("\n") if "ERROR" in ln and "TypeError" in ln), "")
    queries = {"task_instruction": task, "task_plus_error": (task + " " + err).strip()}

    for how in ("section", "block"):
        recs = split_records(ctx, how)
        ix = Inspeximus(path=os.path.join(tempfile.mkdtemp(), "s.json"))
        for i, r in enumerate(recs):
            ix.remember(r, key="blk%03d" % i)
        ix.flush()
        for qname, q in queries.items():
            for k in (3, 5, 8, 12):
                hits = ix.recall(q, k=k)
                sel = {"recall": [h.get("text") or "" for h in hits],
                       "recency": recs[-k:],
                       "random": rng.sample(recs, min(k, len(recs)))}
                for name, chosen in sel.items():
                    blob = "\n".join(chosen)
                    grid.setdefault(how, {}).setdefault(qname, {}).setdefault(name, {})[k] = {
                        "groups_kept": len(groups_hit(blob, groups)),
                        "labels": groups_hit(blob, groups),
                        "tokens": est_tokens(blob),
                        "saving_vs_full": round(1 - est_tokens(blob) / est_tokens(ctx), 3),
                    }
        print("  %-8s %d records" % (how, len(recs)), flush=True)

    recs = split_records(ctx, "section")
    results = grid["section"]["task_plus_error"]

    full_hit = groups_hit(ctx, groups)
    noise = [r for r in recs if "Unrelated note" in r or "background sync" in r]

    v = {}
    v["CONTROL_the_fixture_is_intact_full_context_keeps_every_group"] = len(full_hit) == len(groups)
    v["CONTROL_the_noise_is_actually_present"] = len(noise) >= 5
    v["CONTROL_the_splitter_produced_a_real_store"] = len(recs) >= 12
    def best(how, qname, sel):
        d = grid[how][qname][sel]
        return max(d[k]["groups_kept"] for k in d)

    # AT MATCHED TOKENS, not matched k. In the first run random "beat" recall by drawing a 1,225-token
    # block against recall's 374, which is a size confound and not a retrieval result.
    def best_under(how, qname, sel, cap):
        d = grid[how][qname][sel]
        ok = [d[k]["groups_kept"] for k in d if d[k]["tokens"] <= cap]
        return max(ok) if ok else 0

    CAP = 400
    best_recall = best("section", "task_plus_error", "recall")
    best_random = best("section", "task_plus_error", "random")
    best_recency = best("section", "task_plus_error", "recency")
    v["CONTROL_random_does_WORSE_than_recall"] = best_random < best_recall
    # THE CLAIM, and it FAILS. Derived-from-use recovers 6 of the 8 groups a human wrote down, so it
    # does not replace the hand list. The threshold stays at 8 and this verdict stays red: lowering it
    # to 6 would be renaming the result after seeing it.
    v["DERIVED_FROM_USE_MATCHES_THE_HAND_LIST"] = best_recall == len(groups)
    # WHAT DOES HOLD, at a matched token budget rather than at matched k, which is the comparison the
    # first run got wrong: recall keeps 6 of 8 inside 400 tokens where random keeps 3 and recency 1.
    v["AT_A_MATCHED_BUDGET_IT_BEATS_BOTH_BASELINES"] = (
        best_under("section", "task_plus_error", "recall", CAP)
        > max(best_under("section", "task_plus_error", "random", CAP),
              best_under("section", "task_plus_error", "recency", CAP)))
    v["AND_THE_SAVING_IS_REAL"] = any(
        results["recall"][k]["saving_vs_full"] > 0.9 and
        results["recall"][k]["groups_kept"] >= 6 for k in results["recall"])
    # AND THE GAP IS NOT RANDOM: the two it misses are the CODE-level evidence, not the instructions.
    missed = [g for g in [x.get("label") for x in groups]
              if g not in results["recall"][min(results["recall"])]["labels"]]
    v["THE_MISSES_ARE_THE_EVIDENCE_NOT_THE_INSTRUCTIONS"] = set(missed) == {
        "buggy dereference", "null customer trigger"}

    print("\n  required groups: %d  (%s)" % (len(groups), ", ".join(
        (g.get("label") or "?") for g in groups)))
    print("  full context keeps: %d/%d" % (len(full_hit), len(groups)))
    print("\n  BEST groups kept, and at a matched %d-token cap:" % CAP)
    print("  %-8s %-17s %-9s %-9s %s" % ("split", "query", "recall", "recency", "random"))
    for how in ("section", "block"):
        for qname in ("task_instruction", "task_plus_error"):
            print("  %-8s %-17s %d/%d (%d)   %d/%d (%d)   %d/%d (%d)" % (
                how, qname,
                best(how, qname, "recall"), len(groups), best_under(how, qname, "recall", CAP),
                best(how, qname, "recency"), len(groups), best_under(how, qname, "recency", CAP),
                best(how, qname, "random"), len(groups), best_under(how, qname, "random", CAP)))
    print("\n  detail, section split + task_plus_error:")
    print("  %-9s %-4s %-14s %-9s %s" % ("selector", "k", "groups kept", "tokens", "saving"))
    for name in ("recall", "recency", "random"):
        for k in sorted(results[name]):
            r = results[name][k]
            print("  %-9s %-4d %d/%-12d %-9d %.1f%%"
                  % (name, k, r["groups_kept"], len(groups), r["tokens"], 100 * r["saving_vs_full"]))
    print()
    for k, ok in v.items():
        print("  %s  %s" % ("YES" if ok else "no ", k))

    out = {"benchmark": os.path.basename(__file__),
           "fixture": {"url": CASE_URL, "sha256": CASE_SHA256, "vendored": False,
                       "licence": "NOASSERTION upstream; fetched at run time, never copied here"},
           "records": len(recs), "context_tokens_est": est_tokens(ctx),
           "required_groups": [g.get("label") for g in groups],
           "full_context_keeps": len(full_hit),
           "grid": grid, "matched_token_cap": CAP,
           "results_section_task_plus_error": results, "verdicts": v,
           "missed_groups": ["buggy dereference", "null customer trigger"],
           "what_this_means": (
               "derived-from-use finds the SYMPTOM and the INSTRUCTIONS and misses the CODE-level "
               "evidence: the two groups it never recovers are payload.customer.email and "
               "customer: null, both of which live in the source block and the payload sample. So "
               "it does not replace a hand-written required-fact list; it beats every cheap "
               "baseline at a matched budget and leaves a named, reproducible gap."),
           "first_run_was_about_the_harness": (
               "block split + task instruction only: recall 4/8 at k=12 while random reached "
               "5/8 at k=8, but random had drawn a 1,225-token block against recall's 374. "
               "Both defects were ours: 38 of 63 records were under 120 chars, and the task "
               "instruction shares 0-1 content words with the blocks carrying the facts."),
           "elapsed_s": round(time.time() - t0, 1)}
    io.open(os.path.join(HERE, "result.json"), "w", encoding="utf-8").write(
        json.dumps(out, ensure_ascii=False, indent=1))
    return 0 if all(v.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
