# LongMemEval_S — an end-to-end QA score for inspeximus

This directory answers the question every buyer opens with and this repository could not previously
answer: **given a long chat history, does the assistant get the question right?**

What existed before was a *retrieval* number on LoCoMo (`recall@25 = 0.783 / 0.648`) which the
CHANGELOG itself marks as *"reported, not independently reproducible from this repo"*. Retrieval
recall says the right turn came back. It does not say the question was answered. Those are different
measurements and only the second one is comparable to what competitors publish.

**Results, the design that produced them, and the pre-registration are in
[`PREREGISTRATION.md`](PREREGISTRATION.md), written before the scored run.**

---

## The number

**LongMemEval_S, 20 questions, end-to-end QA accuracy, LLM-judged.** Full record:
[`results/longmemeval_s_small_20260802-002956.json`](results/longmemeval_s_small_20260802-002956.json).

| arm | accuracy | mean context | what it is |
|---|---|---|---|
| `oracle` | **0.50** | 18 441 c | ceiling — the gold evidence session(s) only |
| **`inspeximus`** | **0.45** | 21 383 c | `recall(q, k=25, mode="hybrid", reinforce=False)` over 500 haystack turns |
| `recency` | **0.05** | 23 096 c | floor — the newest turns, same budget, no memory system |
| `shuffled` | 0.10 | 22 014 c | control — same retrieval, *another question's* store |
| `empty` | 0.00 | 0 c | control — no context |
| `full_context` | **N/A** | — | NOT COMPUTABLE: the haystack is 516 048 characters |

Operating point: LongMemEval_S sha256 `08d8dad4…`, 500 instances, 20-question stratified subset ·
k=25 · `mode="hybrid"` · `reinforce=False` · 24 000-character budget on every arm ·
embedder `nomic-embed-text` · answerer `llama3.1:8b` · judge `qwen2.5:7b`
(judge prompt sha256 `9f86b20a…`) · `num_ctx=8192` · temperature 0 · seed 0 ·
inspeximus 1.89.0 · zero LLM on the write path.

**Judge gate: PASSED** — GOLD 12/12, WRONG 11/12, HEDGE 12/12
([`results/judge_calibration_qwen2.5-7b.json`](results/judge_calibration_qwen2.5-7b.json)).

**Band check: FAILED, on the control clause, and the run exits 5 because of it.** The core band holds
— `recency 0.05 < inspeximus 0.45 < oracle 0.50` — and inspeximus recovers 90 % of what perfect
retrieval delivers into the same budget while scoring 9x the no-memory floor. What failed is the
stricter clause this harness adds: `shuffled` (0.10) came in above `recency` (0.05). That is 2 of 20
against 1 of 20 — one question — and at n=20 it is inside the noise, not a finding. It is reported
rather than smoothed over because the alternative is a gate that only ever passes. The fix is more
questions, not a looser rule: `--subset medium` (50) and `--subset full` (470) exist and change
nothing but the sample.

**This run was CONTENDED** and is stamped `"contended": true`. The GPU pre-flight refused (11 396 MiB
free, `llama-server.exe` resident) and was overridden with `--allow-contended-gpu` because the card
could not be quiesced. Contention does not bias the accuracy — every arm shares one answerer, one
judge and one budget — but the latencies (judge p95 34 s against a 9 s median) are contention, not
model speed, and a clean number needs the card to itself.

**Where it wins and where it does not** (n is 1–6 per cell; directional only):
`inspeximus` matches the `oracle` ceiling exactly on single-session-assistant (1.00) and
single-session-preference (1.00), reaches 0.67 on single-session-user against a 1.00 ceiling, and
lands at 0.50 on temporal-reasoning where the ceiling itself is 0.17 — the answerer, not retrieval,
is what fails there. It scores **0.00 on multi-session** against a 0.20 ceiling: a single top-k pass
returns turns about the question, and a question spanning several sessions needs turns about each
part of it.

**Not comparable to published LongMemEval numbers.** Zep, mem0 and the paper's own baselines use
GPT-4-class answerers and judges; these are 7–8 B local models. What this measures is the spread
*between arms* under one judge, one answerer and one budget.

## Running it

```bash
python benchmarks/longmemeval/run.py --download        # once: fetches the 278 MB dataset
python benchmarks/longmemeval/judge_calibration.py     # the gate: >=90% on GOLD / WRONG / HEDGE
python benchmarks/longmemeval/run.py --subset small    # the pinned 20-question run
```

Requirements: a running [Ollama](https://ollama.com) with `llama3.1:8b`, `qwen2.5:7b` and
`nomic-embed-text` pulled. **No Python dependencies beyond the standard library and `inspeximus`
itself** — the harness uses `urllib`, and nothing here becomes an install requirement of the package.

Point it at a different endpoint with `OLLAMA_HOST_URL`, and at an existing copy of the dataset with
`LONGMEMEVAL_DATA=/path/to/longmemeval_s.json`.

Exit codes: `0` ok · `2` dataset missing or not LongMemEval · `3` GPU pre-flight refused · `4` model
pre-flight failed · `5` band check failed.

## What makes this harness trustworthy (or tells you it is not)

**The dataset gate.** LongMemEval is CC-BY-NC 4.0 and is not redistributed here. If it is absent the
harness exits 2 with the fetch command, and if it is handed a *different* dataset it refuses that
too. It never silently scores on a substitute — LoCoMo is sitting on the machine this was built on,
and a harness that quietly used it would have produced a plausible number for the wrong benchmark.

**The GPU gate.** This box shares one RTX 3090 with a long-running research process. A score taken
while another model owns the card measures the contention. `run.py` refuses to start (exit 3) unless
≥ 20 GiB of VRAM is free and no foreign model runner is resident, and it refuses just as hard when
it *cannot read* the card — unknown does not read as free. `--allow-contended-gpu` forces a run and
stamps `contended: true` on every number it produces.

**The model gate.** Each model is probed with a nonce that has never been sent before and must echo
it back. A string coming back is not evidence a model ran, and a 0.0 s reply is a cache hit rather
than a call.

**The judge gate.** `judge_calibration.py` feeds the judge the gold answer (must score 1), a
different question's gold answer (must score 0) and a refusal (must score 0), ≥ 90 % on each arm.
The refusal arm carries the weight: the answerer is instructed to say "I don't know" when memory is
empty, so a lenient judge would hand the empty-context control a free score.

**The band check.** `recency < inspeximus < oracle`, strictly, plus both controls at or below the
floor. It **fails the run** with exit 5. This is the harness's own falsification test — outside the
band it is measuring something other than memory quality, and a number from it is worthless in
either direction. `tests/test_longmemeval_harness.py` exercises it in both directions, including the
case where a control beats the floor.

**One context budget for every arm.** 24 000 characters, matched by construction and recorded per
arm. A 9x budget gap between arms once flipped the ranking of a different benchmark in this
repository; it is not left to chance here.

**Zero LLM on the write path.** Ingestion is `remember()` plus a local embedding model — no
extraction, no summarisation, no rewriting. A test points `chat` at a landmine and ingests a haystack
to prove it. The answerer and the judge are read/eval-side only.

## Honest limits

* The answerer and judge are 7–8 B local models. Published LongMemEval numbers use GPT-4-class
  models. **The absolute value here is not comparable to theirs.** What this harness claims is the
  *within-run* spread between arms measured under one judge, one answerer and one budget.
* 20 questions is a small subset; the 95 % binomial interval at n=20 is roughly ±0.21. Only large
  gaps between arms mean anything. `--subset medium` (50) and `--subset full` (470) change nothing
  but the sample.
* Abstention instances (30 of 500) are excluded — their gold answer needs an abstention rubric, not
  a semantic match. Declared in the pre-registration, before the run.
* `full_context` is executed and reported NOT COMPUTABLE with its measured size. That is a fact
  about this hardware, not about any memory system.

## Prior art reused

`probes/locomo_qa.py` (batched embedding, build-the-store-once, the date-prefixed turn format),
`benchmarks/memops/judge_calibration.py` (the pre-registered judge gate and its arm structure),
`benchmarks/memops/PREREGISTRATION.md` (predictions fixed before the run), and
`bench/run_cr_benchmark.py` (declaring a full-context arm NOT COMPUTABLE rather than dropping it).

## Citation

LongMemEval: Wu, Di, Hongwei Wang, Wenhao Yu, Yuwei Zhang, Kai-Wei Chang, Dong Yu.
*LongMemEval: Benchmarking Chat Assistants on Long-Term Interactive Memory.* ICLR 2025.
[arXiv:2410.10813](https://arxiv.org/abs/2410.10813) · CC-BY-NC 4.0 ·
[huggingface.co/datasets/xiaowu0162/longmemeval](https://huggingface.co/datasets/xiaowu0162/longmemeval)
