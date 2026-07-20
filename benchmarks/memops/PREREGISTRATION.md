# Pre-registration — mnemo on MemOps (internal diagnostic)

**Written BEFORE any measurement.** Timestamp: 2026-07-20, immediately after the environment was
quiesced (brain + dungeon stopped, keepalive disabled, GPU idle, zero cloud contention verified).
Nothing in this file may be edited after the first result lands; corrections go in a dated appendix.

## The question (one sentence)

Does mnemo's **keyed supersession / echo_guard** buy measurable integrity over a **naive verbatim
keep-all** store on someone else's lifecycle-operation data — and if so, on which operations?

Why this and not "do we beat mem0": our own MemoryAgentBench run already found mnemo 85% vs naive
verbatim 87% (within noise) — i.e. our measured advantage there came from **not running an LLM on the
write path**, NOT from supersession. MemOps is the first external dataset with explicit Update/Forget
probes and stale-value / leakage metrics, so it can settle that open question.

## What is NOT being claimed

- **No comparison to the published MemOps table.** Their judge (gpt-4o in the paper, gpt-4.1-mini in the
  shipped code) is unavailable to us and those model families are no longer on OpenAI's pricing page.
  Different judge + different answerer ⇒ our numbers are **not** comparable to theirs. Their Mem0=0.543
  and MemOS=0.785 are context only.
- **No outward claim of any kind** from this run. Internal diagnostic. Anything outward would need the
  full gate plus a judge we can defend.
- Note for the record: MemOps is published by **MemTensor, who also make MemOS**, the best-scoring
  managed-memory system in their own table. Vendor benchmark, not a neutral third party.

## Setup (fixed before running)

- **Answerer**: `deepseek-v4-flash` @ ollama.com, temperature 0.
- **Judge**: `glm-5.2:cloud` via the local Ollama cloud-route, max_tokens ≥ 16000 (it emits empty content
  under a tight cap), temperature 0. **Deliberately a different family from the answerer** to avoid
  self-preference bias.
- **Setting**: long-context (`4-inject_evidence_with_distractors`, ~88k tokens of dialogue per file).
  This is the setting where a memory system actually does work; the adjacent setting hands the model the
  evidence directly and leaves nothing for a store to do.
- **Ingestion granularity**: session/segment-level verbatim chunks (the paper measures session-level RAG
  0.845 vs turn-level 0.618 — turn-level fragmentation is a known confound, so we do not use it).
- **Arms** (identical answerer, identical judge, identical data — only the memory layer differs):
  1. `mnemo` — keyed writes, `echo_guard=True`, supersession active, `resolve_conflicts=True`
  2. `naive` — same chunks, keep-all, no supersession, no guard (**the control that tied us on MAB**)
  3. `no_context` — answerer sees only the question (floor; proves the probes need memory at all)
  4. `mem0` — its own extraction pipeline, LLM pointed at Ollama Cloud (**added 2026-07-20 at the owner's
     request, before any measurement**). Free on our quota; makes the head-to-head internally valid
     instead of borrowing a number from a vendor's own table.
  5. `session_rag` — BM25 over session-level chunks, the paper's strongest non-memory baseline.
- **Pilot**: n ≈ 250 probes, stratified across all five operation types (remember / forget / update /
  reflect / trajectoryops).

## Predictions (directional, with decision rules)

| # | Prediction | SUPPORTED if | REFUTED if |
|---|---|---|---|
| P1 | mnemo ≈ naive on **Remember** accuracy | \|Δ\| ≤ 3 pp | mnemo beats naive by > 3 pp |
| P2 | mnemo beats naive on **Stale Value Rate** (Update) | mnemo's stale rate is lower by ≥ 5 pp | Δ < 5 pp, or naive lower |
| P3 | mnemo beats naive on **Leakage Rate** (Forget) | mnemo's leakage lower by ≥ 5 pp | Δ < 5 pp, or naive lower |
| P4 | mnemo **loses** to naive on `over_forget` | mnemo's over_forget higher by ≥ 5 pp | mnemo ≤ naive |
| P5 | both arms beat `no_context` on accuracy | ≥ 20 pp over the floor | otherwise the probes are answerable without memory and the run is void |

P4 is a prediction **against ourselves** and is the honest one: `forget_subject` deliberately cascades
through the `derived_from` lineage, which is right for compliance but is exactly what MemOps scores as
over-forgetting. If P4 lands we have found a real, previously unnamed tension in our own design.

**The headline result of this run is P2 and P3.** If both are REFUTED, the honest conclusion is that
supersession buys nothing measurable on lifecycle operations either, and we stop building the product
story on it — the same way the MAB result was recorded as an honest null.

## Judge validation gate (runs BEFORE the pilot; the pilot is void without it)

The paper itself reports that "the LLM-based judge exhibits some instability", and we are not using their
judge. So, on ~30 probes each:
- feed the **gold** answer as the response → judge must return `answer_score=1`
- feed a deliberately **stale** value → judge must return `stale_value=1`
- feed a deliberately **leaked** forgotten value → judge must return `leakage=1`

**Gate: ≥ 90% correct on each of the three, else the judge is not fit and the pilot does not run.**
A judge that cannot separate these cannot measure P2/P3, and any number it produces would be noise.

## What we get either way

1. A settled internal answer on whether supersession earns its place.
2. A reusable harness (swap the judge when there is budget or when a leaderboard appears).
3. `over_forget` as a newly-named design tension in `forget_subject`.
4. Their operation taxonomy (trigger / target / scope / state-transition / evidence) as input to mnemo's
   own API design, cited as prior art rather than reinvented.


---

# Appendix A — judge calibration outcome (written 2026-07-20, after Phase 1, before any Phase 2 measurement)

**GATE PASSED**: GOLD 12/12 · STALE 11/12 · LEAK 12/12.

Full disclosure of how it got there, because it took three rounds and two of them were my fault:

1. **Round 1 (GOLD 10/10, STALE 10/10, LEAK 8/10 — gate failed).** The two LEAK misses were a HARNESS bug,
   not a judge bug: I injected the forgotten value into every probe of a forget scenario, including the
   `target_binding` probe that literally asks "which person did I ask you to drop?" — whose GOLD answer
   names that person. The judge was right to score leakage=0. Fixed by a validity filter: a synthetic
   violation case is only built when the value is ABSENT from the gold answer. Also fixed a second round-1
   weakness — all 10 cases came from one scenario file; cases are now spread round-robin across scenarios.
2. **Round 2 (GOLD 12/12, LEAK 12/12, STALE 10/12 — gate failed).** Both STALE misses came from the same
   pathological scenario `A11_update`: a 4-step update chain that REVERSES (A→B→A→C) probed by a
   history-summarisation question. My substring validity filter missed it because the gold answer phrases
   the old value differently. Genuinely the hardest case in the sample.
3. **Round 3 — ONE prompt change, declared in advance as the only one allowed** (otherwise this stops being
   calibration and becomes tuning the instrument until it agrees with me). The stale_value definition now
   states that the current value is the FINAL state of the chain, that asserting an earlier value as
   current is stale even if it was legitimately current before, and that merely recounting history is not.
   Result: STALE 11/12.

**Known blind spot, carried forward:** ['A11_update.json'] still misses — chains containing a reversal, probed by a
history question. STALE numbers from Phase 2 therefore carry ~8% judge error on that case class. This is
recorded here BEFORE the pilot so it cannot be discovered later and explained away.

**What calibration bought:** it caught two systematic defects in my own measuring instrument that would
have silently biased exactly the two metrics the whole study rests on (Leakage, Stale Value). Cost: ~15
minutes and 3 x 36 free judge calls.


---

# Appendix B — the pilot's first run is CONFOUNDED by context budget (written 2026-07-20, after the
# k=20 run completed and BEFORE the corrected run's results existed)

The first pilot run (`pilot_raw_cheap.json`, 24 files, n≈240/arm) finished. Its headline numbers:

| arm | n | accuracy | stale (Update) | leak (Forget) | over_forget |
|---|---|---|---|---|---|
| mnemo | 237 | 0.283 | 0.114 (4/35) | 0.179 (7/39) | 0.625 |
| naive | 238 | 0.269 | 0.081 (3/37) | 0.147 (5/34) | 0.667 |
| session_rag | 240 | 0.442 | 0.114 | 0.333 | 0.278 |
| no_context | 240 | 0.058 | 0.000 | 0.000 | 0.875 |

Read literally: P1 SUPPORTED (Δacc +1.4 pp, bootstrap 95% CI [−6.6, +9.4]), P5 SUPPORTED (~22 pp over the
floor), P2 and P3 REFUTED, P4 REFUTED. **That reading is not admissible**, because of what
`retrieval_coverage.py` (zero LLM calls) then measured on the identical 24 files:

| arm | evidence-turn coverage | avg context chars |
|---|---|---|
| mnemo@20 (the setting the pilot ran) | 0.035 | 1 323 |
| naive@20 | 0.034 | 1 300 |
| mnemo@60 | 0.085 | 4 768 |
| mnemo@150 | 0.142 | 11 916 |
| session_rag | 0.305 | 11 941 |

`TOPK=20` sentence-level hits spend ~1.3k characters; the `session_rag` arm spends ~11.9k. The arms were
therefore never compared at equal context budget — a 9x confound of my own making. Two consequences:

1. **session_rag's accuracy win (0.442 vs 0.283) is not a granularity result.** It is mostly a budget
   result and must not be cited as "BM25 beats mnemo".
2. **P2/P3 cannot be evaluated from this run at all.** Supersession can only correct a stale value that is
   actually retrieved; at 3.5% evidence coverage there is almost nothing in the context for the integrity
   layer to act on. A null here measures the retriever, not the product.

**Correction (declared before the corrected numbers existed):** re-run the `mnemo` and `naive` arms at
`MEMOPS_TOPK=150` (~11.9k chars, matched to `session_rag` to within 0.2%), same 24 files, same answerer,
same judge, same prompts, tag `k150`. P1–P5 are evaluated on THAT run. The k=20 numbers stay on record as
what a too-small retrieval budget produces; they are not the study's result.

**Result that survives either way** (it needs no LLM): at a matched ~11.9k budget, turn-level lexical
retrieval still recovers only 0.142 of the evidence sentences vs 0.305 session-level — turn granularity
costs ~2.1x in evidence recall. That is a real cost of mnemo's keyed-statement ingestion model and it is
independent of the judge.


---

# Appendix C — FINAL RESULT (2026-07-20, all five arms complete)

Corrected run at matched context budget (`MEMOPS_TOPK=150`, ~11.9k characters per arm), same 24
stratified scenarios, same answerer, same judge.

| arm | n | accuracy | stale (Update) | leakage (Forget) | over-forget |
|---|---|---|---|---|---|
| mnemo | 236 | **0.593** | 0.211 (38) | 0.243 (37) | 0.162 (37) |
| naive keep-all | 238 | **0.592** | 0.125 (40) | 0.278 (36) | 0.222 (36) |
| mem0 | 237 | **0.544** | 0.211 (38) | 0.385 (39) | 0.051 (39) |
| session_rag (BM25) | 240 | 0.442 | 0.114 (35) | 0.333 (36) | 0.278 (36) |
| no_context | 240 | 0.058 | 0.000 | 0.000 | 0.875 |

mnemo vs mem0, bootstrap 95% CI on the difference: accuracy [−0.040, +0.138], stale [−0.184, +0.184],
leakage [−0.348, +0.067], over-forget [−0.023, +0.246]. **Every interval contains zero.**

## Verdict against the pre-registered predictions

- **P1 SUPPORTED** — mnemo ≈ naive on Remember/accuracy (Δ = +0.1 pp, CI [−8.8, +8.9]).
- **P2 REFUTED** — no stale-value advantage; mnemo 0.211 vs naive 0.125, CI spans zero.
- **P3 REFUTED** — no leakage advantage; 0.243 vs 0.278, Δ below the 5 pp threshold.
- **P4 REFUTED, in our favour** — over-forget 0.162 vs naive 0.222; the `derived_from` cascade did not
  cost us here. Note `no_context` scores 0.875 on this metric, i.e. it largely measures "declines to
  answer", so it is weak evidence either way.
- **P5 SUPPORTED** — every memory arm clears the 0.058 floor by more than 20 pp.

**The headline (P2 + P3) is a NULL.** This is the third independent null after MemoryAgentBench and the
MAB conflict-resolution test: on lifecycle operations, keyed supersession buys nothing measurable over a
naive keep-all store, and nothing measurable over mem0's LLM-extracted store. A diagnostic on the update
cases confirmed it is not a bug — the current value stays active in the store (0.900 of update probes vs
naive 0.950; the whole gap is one retrieval miss in `A05_update`). The layer is not failing; on this task
it simply has nothing to add, because an answerer given 150 sentences of history resolves corrections by
itself.

## What DID separate, and by how much

**Write cost.** mem0 spends ~600–730 s of LLM extraction per scenario (full run: 4 h 40 min wall clock,
of which 53 s CPU — the rest is waiting on the cloud). mnemo's write path is 0 s and makes no LLM call.
That is an order-of-magnitude separation, not a noise-level one, and it is the one thing this benchmark
measured cleanly in our favour.

**Retrieval-vs-granularity.** At an equal budget, turn-level keyed retrieval beats session-level BM25 on
accuracy (0.593 vs 0.442) despite recovering less than half the evidence sentences (0.142 vs 0.305).

## Disclosures

- 21 extraction-parse failures across mem0's ~950 ingest calls (~2.2%); those memories are missing from
  its store. This handicaps mem0 slightly and is stated here rather than left in a log.
- mem0 was given the STRONGER of the two free models available (glm-5.2, 0 parse errors in the positive
  control, against deepseek-v4-flash's 1) precisely to avoid a strawman.
- Two earlier mem0 scores of 0.000 were OUR defects (a `sess[:6000]` truncation and a `limit=` kwarg that
  mem0 ignores in favour of `top_k`), found by a positive control before any number was recorded.
- Our independent mem0 accuracy (0.544) lands beside the published MemOps figure (0.543). The agreement
  to three decimals is coincidence — different judge, different answerer, different subset — but the
  magnitude corroborates the harness.

## Consequence for the product story

The claim "mnemo gives more accurate or fresher answers" has now failed to replicate three times and must
not be used. What survives is measurable and defensible: **a zero-LLM, deterministic write path**, and
the governance primitives (revert, receipted erasure) that this benchmark never asks about. The latter are
tested separately under `ERASURE_REVERT_SPEC.md`, whose predictions were fixed before it ran and include
E1, which predicts a TIE with the naive baseline.
