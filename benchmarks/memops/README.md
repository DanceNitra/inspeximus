# MemOps: what happened when mnemo's correction layer was measured against a keep-everything store

Short version: **it bought nothing measurable.** This directory is the harness, the pre-registration
written before the run, and the raw results, published because the result went against us and a null
that only exists on the author's laptop is not a result.

## The numbers

Matched context budget (~11.9k characters per arm), 24 stratified scenarios, ~237 probes per arm,
identical answerer and judge across arms.

| arm | accuracy | stale value (Update) | leakage (Forget) |
|---|---|---|---|
| mnemo (keyed supersession, echo_guard, read-time resolver) | 0.593 | 0.211 | 0.243 |
| naive keep-everything store | 0.592 | 0.125 | 0.278 |
| mem0 | 0.544 | 0.211 | 0.385 |
| session-level BM25 | 0.442 | 0.114 | 0.333 |
| no context (floor) | 0.058 | — | — |

Bootstrap 95% CIs on every mnemo-vs-mem0 difference contain zero. Against the naive store the
accuracy difference is +0.1 pp. Pre-registered predictions P2 and P3 — that supersession would lower
the stale-value and leakage rates — are **refuted**; P1 (parity on plain recall) and P5 (every arm
clears the no-context floor) hold. Full verdict in `PREREGISTRATION.md`, Appendix C.

What *did* separate, by an order of magnitude, is the write path: the LLM-extraction pipeline spent
**519–917 s per scenario** (median 606 s, n=24) against zero model calls for the deterministic one.
That is a cost difference, not a quality one, and it is the only claim this run supports.

## Two confounds that had to be removed first, both ours

1. **A 9x context-budget gap.** The first run gave the memory arms ~1.3k characters (`k=20` sentence
   hits) and BM25 ~11.9k (whole sessions). Accuracy went 0.28 → 0.59 once matched, and the ranking
   flipped. `retrieval_coverage.py` measures the budget and the evidence coverage per arm with no LLM
   calls; it read 3.5% coverage in the broken configuration.
2. **mem0 scored 0.000 twice, from our defects** — `sess[:6000]` truncation cutting off the injected
   evidence, and passing `limit=` to an API whose parameter is `top_k=`. `mem0_positive_control.py`
   is the check that caught it: the smallest input the system must handle, run before any number is
   recorded. Fixed, it stores 262 memories where 20 had been measured.

## Reproducing

The dataset is not redistributed here. Get it from [MemTensor/MemOps](https://github.com/MemTensor/MemOps)
(MIT) and place the long-context scenarios in `data_lc/` and the evidence conversations in `data/`.

```bash
pip install agora-mnemo mem0ai rank-bm25
export MEMOPS_API_KEY=...          # any OpenAI-compatible endpoint for the answerer
python judge_calibration.py        # the gate: >=90% on gold/stale/leak or the pilot does not run
MEMOPS_TOPK=150 python pilot.py 24 mnemo,naive,no_context,session_rag k150
MEMOPS_TOPK=150 python pilot.py 24 mem0 mem0full
python retrieval_coverage.py 20,60,150
```

`results/` holds the raw per-probe judgements from the runs the table above is computed from.

## Honest scope

- The judge is an LLM (`glm-5.2`, deliberately a different family from the answerer). It passed a
  pre-registered calibration gate — gold 12/12, stale 11/12, leak 12/12 — with one recorded blind
  spot (`A11_update`, a chain that reverses) written down *before* the pilot ran.
- mem0 was given the stronger of the two models available to us, on purpose. 21 of its ~950 extraction
  calls failed to parse (~2.2%); those memories are missing from its store and that handicaps it.
- MemOps is published by MemTensor, who also make a competing memory system. Their own table is
  context, not a baseline we compare against — different judge, different answerer, different subset.
- A diagnostic on the update cases confirmed the null is not a bug in the integrity layer: the current
  value stays active in the store (0.900 of update probes vs 0.950 for naive; the entire gap is one
  retrieval miss). Given 150 sentences of history, the answerer resolves the correction itself and
  there is nothing left for a write-side layer to win.
