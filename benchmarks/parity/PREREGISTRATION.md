# Pre-registration — cross-system parity harness

**Written BEFORE any number was collected.** Committed in its own commit, ahead of the harness and the
results, so the git history is the timestamp. Same discipline as `benchmarks/memops/PREREGISTRATION.md`:
axes, metrics, arms, decision rules and **expected directions including the ones that go against us** are
fixed here first. Corrections go in a dated appendix; nothing above the appendix line is edited after the
first result lands.

## The question (one sentence)

On one shared corpus, with one shared harness and one LLM-free reader, **where does inspeximus actually
differ from other agent-memory systems, by how much, and on which axes does it lose?**

## Why this exists

Five separate measurement efforts already live in this repo (`benchmarks/memops/`, `probes/integrity_bench_*`,
`benchmarks/property_benchmark.py`, `probes/forget_verification_bench.py`, `bench/`). Each answers one
question on its own corpus with its own instrument, and two of them use an LLM judge. There is no single
place a reader can see all the axes at one operating point, and no place where the losing rows sit beside
the winning ones. This harness is that place. It **consolidates**; it does not restart.

## What is NOT being claimed

- **No claim that a competitor "cannot" do something.** A prior gate already falsified "only inspeximus can
  revert" — Letta ships checkpoint/revert. Every row describes OUR mechanism, reports THEIR measured
  number, and lets the reader compare.
- **No leaderboard.** n is small by design (a shared RTX 3090 and free-tier quotas). Every rate carries a
  Wilson 95% interval and most of them will be wide.
- **No comparison to any published table.** Different corpus, different models, different reader.
- **Nothing outward** from this run without the full standing gate on top.

## Corpus (fixed before running)

`corpus.py`, deterministic from `seed=20260801`, committed as JSON so every cell is reproducible without
re-running the generator. No external dataset is required and nothing is downloaded.

Each **thread** is an independent scenario in its own namespace and contains:

| element | purpose | axis it serves |
|---|---|---|
| `entity`, `value_a`, `value_b` | a fact and its correction (single unambiguous tokens) | revert, echo |
| `revert_utterance` | an **unmarked** revert naming no value ("go back to what we had") | revert |
| `echo_utterance` | a restatement of the RETIRED value `value_a` | echo |
| `secret` | one erasable value with a distinctive surface form | erasure |
| `retained[]` | facts that must SURVIVE the erasure | over-forget |
| `distractors[]` | background facts, the haystack | retrieval, write cost |
| `probes[]` | paraphrased questions whose gold answer is a distractor value | retrieval |

Probes are deliberately **paraphrased away from the stored wording** (no shared content token between the
question and the stored sentence beyond the entity noun). That is the condition under which a lexical
retriever is expected to lose to an embedding retriever, and it is chosen precisely because it is the
condition that is bad for inspeximus's zero-dependency default.

Subsets: `small` = 12 threads x 8 distractors; `full` = 50 threads x 40 distractors.

## The reader (one instrument, every arm, zero LLM)

Every arm exposes the same three operations — `write(text)`, an axis-specific operation, and
`read(query) -> ordered list of strings` — and is scored by **one** function:

```
top_value(surface, A, B):
    for item in surface (in the arm's own rank order):
        if A in item and B in item: return "unclear"
        if A in item:               return "A"
        if B in item:               return "B"
    return "unclear"
```

Rationale and its known bias, stated up front: this models *a consumer that reads the top facts it is
handed*. A bitemporal store that surfaces the invalidated edge alongside the valid one scores `unclear`,
not `B`. That is a **read-contract difference, not a failure**, it is reported in its own column, and it
must never be folded into another arm's win. The prior LLM-judge instrument in
`probes/INTEGRITY_BENCHMARK.md` made the same observation about Graphiti (9/20 unclear) and its numbers are
carried into the README as context, not merged into this table.

`unclear` is always reported as its own number. No rate in this benchmark is computed with `unclear`
silently in the denominator's favour.

## Arms

| arm | what it is | LLM on the write path |
|---|---|---|
| `inspeximus` | this repo's worktree build, keyed supersession + `echo_guard` + `route()`, default lexical recall | none |
| `inspeximus_embed` | same, `embed=nomic-embed-text` — the fair version of ourselves on the retrieval axis | none (embedding only) |
| `naive` | keep-everything list, same lexical scorer, no supersession, no guard — **the control that has tied us twice** | none |
| `bm25` | keep-everything + Okapi BM25 (`rank_bm25`) — the strongest zero-LLM retrieval baseline | none |
| `mem0` | `mem0ai` 2.0.11, its own extraction pipeline | yes |
| `graphiti` | `graphiti-core` 0.29.2 against a live neo4j, its own entity/edge pipeline | yes |
| `letta` | `letta-client` 1.12.1 against a live Letta server | yes |
| `zep` | `zep-cloud` 3.25.0 | yes |

**Any arm that does not install and pass its positive control is reported as `NOT-MEASURED` with the
reason.** Never estimated, never inferred from a README, never entered as 0.

## Positive control — mandatory, runs before any score is recorded

Per competitor, on the smallest input the system must handle (one thread, 3 writes):

1. the write path returns without raising, and
2. the store afterwards contains a non-zero number of records, and
3. a read for the planted fact returns that fact.

**Gate: all three, or the arm is `NOT-MEASURED`.** A competitor scoring 0.000 is OUR bug until the positive
control says otherwise — the rule that already caught two of our own defects in the mem0 arm
(a `sess[:6000]` truncation and a `limit=` kwarg the API ignores) before either reached a table.

## Axes, metrics and pre-registered expected direction

| # | axis | metric | direction | expectation, fixed now |
|---|---|---|---|---|
| W | write cost | wall seconds per 100 writes; LLM calls per write | lower better | **W1**: inspeximus's LLM calls per write is exactly 0 and its wall time is >= 10x faster than any LLM-extracting arm. Refuted if any LLM arm is within 10x. |
| R | revert | `revert_success` = fraction where an unmarked revert returns value A | higher better | **R1**: inspeximus >= 0.80. **R2**: at least one competitor is > 0.00 (we expect a capability, not a monopoly). Refuted if every competitor is 0.00 — that reading would be too flattering and must be re-checked as a harness bug. |
| E | erasure | `retrieval_leakage`, `raw_residue_files`, `over_forget`, `receipt`, `steps`, `llm_calls`, `deterministic` | lower better (receipt: yes/no) | **E1**: inspeximus and `naive` **TIE** at 0.00 retrieval leakage — a competent hard delete is a competent hard delete. **E2**: inspeximus 0 raw residue. **E3**: at most one system emits a verifiable receipt; that is a governance column, NOT an accuracy column. |
| C | echo | `resurrection_rate` (restated retired value becomes current) | lower better | **C1**: inspeximus 0.00. **C2**: this is expected to be a NEAR-TIE — the prior cross-system run measured 0.00 / 0.05 / 0.00 and found no system systematically resurrects. Refuted if some arm is > 0.25. |
| Q | retrieval | `hit@1`, `hit@5` on paraphrased probes | higher better | **Q1 — WE EXPECT TO LOSE.** inspeximus's default lexical recall is predicted **below** BM25 and below every embedding arm on paraphrased probes. Q1 is SUPPORTED if inspeximus_lexical < bm25. **Q2**: `inspeximus_embed` closes most of the gap, i.e. the deficit is the zero-dependency default, not the ranking model. |

**Q1 is a prediction against ourselves and it is the load-bearing one for credibility.** Our own lab has
measured null-or-negative on every retrieval-mechanism lever it has tried (centering, ABTT, hybrid/RRF,
zero-LLM multi-hop, native ranking vs cosine). If Q1 came out in our favour on a corpus we wrote, the
first thing to suspect would be the corpus.

**R2 is a prediction that a competitor CAN do what we do.** Letta ships checkpointing. If Letta reverts
cleanly, the honest claim shrinks to cost and determinism, and the table says so.

## Operating point — every cell carries it

Recorded in the result JSON per cell and rendered into the table footnotes: corpus id + seed + subset,
`k`, the competing library version, the LLM and embedder used with its endpoint, and wall-clock. A number
without its operating point is not admissible in this table.

**Shared-GPU disclosure, fixed now:** the box runs a single RTX 3090 shared with other long-lived
processes, and this harness is forbidden from stopping any of them. Every LLM-arm latency measured here is
therefore an **upper bound under contention**, is labelled as such in the JSON (`gpu_contended: true`), and
must not be quoted as that system's best-case latency. A full clean run requires the GPU quiesced.

## What we get either way

1. One table, one corpus, one reader — the missing consolidation of five separate efforts.
2. The rows where we lose, published, which is the only thing that makes the rows where we win readable.
3. A reusable adapter interface: adding a system is one class.
4. `NOT-MEASURED` as a first-class outcome, with its reason, instead of a zero that flatters us.


---

# Appendix A — the third-party spine and its falsification control (added 2026-08-01, BEFORE any CR number)

Written after a repo audit and before running anything in `cr_control.py`. Nothing above this line is edited.

## Why the corpus above is not enough

Everything above runs on a fixture **we wrote**. A sceptical reader discounts that, correctly. This repo
already holds a result on third-party ground — `bench/README.md`, MemoryAgentBench
([arXiv:2507.05257](https://arxiv.org/abs/2507.05257)) **Conflict Resolution**, their data, their metric
(`substring_exact_match`), one fixed answerer — and it is not promoted anywhere. That becomes the spine;
the self-authored corpus above becomes the axes layered around it.

## The defect the audit found, recorded before it is fixed

`bench/README.md` publishes 32k **0.44** / 64k **0.50** / 262k **0.36** for the iterative arm.
`bench/results_cr_sweep.json`, the committed artifact in the same directory, holds **0.52 / 0.42 / 0.38**,
and its `base_full` is 0.26 / 0.32 against the README's 0.34 / 0.30. **The published table does not
reproduce from the committed JSON**; they are two different runs and the README does not say so. That is
recorded here as a finding, not quietly corrected.

## The design defect that matters more

The existing sweep has three arms: `base_full`, `inspeximus_single`, `inspeximus_iterative`. Both inspeximus
arms read the **same keyed, superseded store**. So `single -> iterative` isolates *iteration*, and
**nothing in that design isolates supersession at all.** The headline is attributed to a mechanism the
experiment never varied. Replacing it is the point of this appendix.

## The replacement: a 2x2 factorial

Same answerer, same questions, same `k`, same hop count, same scoring — only the two factors move:

| arm | supersession | retrieval |
|---|---|---|
| `single_off` | OFF (keep-all accumulate) | single-shot top-k |
| `single_on` | ON (keyed) | single-shot top-k |
| `iter_off` | **OFF** | iterative, 2 hops |
| `iter_on` | ON | iterative, 2 hops |
| `full_context` | n/a | whole ordered fact stream (`N/A` above the window) |

`iter_off` is the **falsification control**. It did not exist before and it is the only arm that can tell
"supersession works" from "iteration works".

## Pre-registered decision rule — the one that can turn this unit RED

> **F1 (FALSIFICATION CONTROL).** With retrieval held fixed, supersession-ON must beat supersession-OFF.
> **SUPPORTED** if `iter_on > iter_off` AND `single_on > single_off`.
> **RED — the benchmark does not measure the mechanism we claim, and the CR result must not be published
> as a supersession result** if `iter_off >= iter_on`.

Stated plainly so it cannot be walked back: if the control comes out flat, the honest conclusion is that
the 0.44/0.50/0.36 numbers are an *iterative-retrieval* result that supersession is merely present for,
and this unit says so in the PR title.

## Two stages, because the GPU is shared

The end-to-end metric needs an answerer. The box has one RTX 3090 shared with other long-lived processes
that this harness is forbidden to stop, so:

- **Stage 1 — mechanism control, ZERO LLM calls, always runs.** On the same MemoryAgentBench facts, over
  every `(subject, relation)` key that is restated at least once, a derived single-hop probe measures what
  the retriever puts in the window at a fixed `k`:
  - `current_in_topk` — is the CURRENT (latest) value present in the top-k? **Not tautological**: with
    keep-all, the current fact competes against its own stale duplicates for the same `k` slots; whether
    removing them actually promotes it is an empirical question.
  - `stale_in_topk` — is a superseded value present? Partly mechanical-by-construction for the ON arm
    (recall excludes superseded records) and labelled as such; it is reported, not leaned on.
  - `slots_to_stale` — fraction of the `k` window spent on superseded restatements.
  - **F1a**: `current_in_topk(on) > current_in_topk(off)`. RED if `off >= on`.
  This is **our derived probe on their data**, not their metric, and is labelled that way everywhere.
- **Stage 2 — their metric end-to-end**, `substring_exact_match` over the 2x2, answerer pinned in the
  result JSON. **Gated by a hard GPU pre-flight** (below). If the gate refuses, Stage 2 is reported
  `NOT-MEASURED` with the measured VRAM and process list — never estimated, never carried over from the
  old run.

## GPU pre-flight — a hard gate, not a warning

`preflight.py` **refuses to start** any LLM-arm work when free VRAM < 20 GB or any `llama-server.exe` is
resident. It never stops a process; a coordinator owns that decision and other agents run concurrently.
Bypass requires an explicit `--allow-contended-gpu`, and every cell produced under it is stamped
`gpu_contended: true` in the JSON and carries a banner in the rendered table. A latency measured under
contention is an **upper bound**, not that system's number.

## Framing constraint, fixed before the numbers exist

The CR result, whichever way it falls, is scoped to **one task (Conflict Resolution, multi-hop), the
context lengths actually run, one answerer, their metric**. It is **not** evidence of a general accuracy
win: supersession-as-accuracy has four independent nulls against it in this repo (MemoryAgentBench recall,
MemOps P2/P3, the MAB conflict-resolution tie, and the correction-layer QA ceiling), and none of them is
retracted by this. Conflict Resolution is a narrower task than those, which is exactly why a win here can
be real while the general claim stays dead.
