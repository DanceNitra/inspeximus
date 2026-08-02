# Pre-registration — LongMemEval_S end-to-end QA for inspeximus

Written and committed **before** the scored run. Everything below — the operating point, the arms,
the exclusions, the judge gate and the five predictions — was fixed in advance so that a
disappointing number cannot be turned into a flattering one by moving `k`, changing the subset or
swapping the judge afterwards.

A 3-question `smoke` run was executed first, for the sole purpose of finding harness bugs. It is not
part of any reported result and its numbers are not quoted anywhere.

---

## 1. Why

This repository has never had an end-to-end QA score. What it had was a retrieval-recall pair on
LoCoMo (`@25 = 0.783 / 0.648`) that the CHANGELOG itself marks as *"reported, not independently
reproducible from this repo"*. Retrieval recall answers "did the right turn come back". Buyers,
comparison tables and every competitor README quote "did the assistant answer the question". Those
are different measurements and only the second one enters the conversation we need to be in.

## 2. Dataset

**LongMemEval_S** (Wu et al., *LongMemEval: Benchmarking Chat Assistants on Long-Term Interactive
Memory*, ICLR 2025, arXiv:2410.10813), from
`huggingface.co/datasets/xiaowu0162/longmemeval`, file `longmemeval_s`, CC-BY-NC 4.0.

* 500 instances; each carries a question, a gold answer, and a haystack of ~500 chat turns across
  ~54 sessions (**measured on this file**: median 498 turns / 489k characters per instance).
* sha256 `08d8dad4be43ee2049a22ff5674eb86725d0ce5ff434cde2627e5e8e7e117894`, pinned in `run.py` and
  re-verified and recorded on every run.
* Not redistributed here. `run.py --download` fetches it; a missing dataset is a **hard, loud
  failure** (exit 2) with the fetch command, never a silent fallback to another dataset.

## 3. Subset — fixed before the run, no RNG

`small` = **20 questions**, and that is the number the README quotes.

* Abstention instances (`question_id` ending `_abs`, 30 of 500) are **excluded**. Their gold answer
  is *"You did not mention this information…"*, which needs an abstention rubric, not a semantic
  match; scoring them under the factual rubric would measure the judge. 470 remain.
* Stratified proportionally across the six `question_type` values by largest remainder, giving
  temporal-reasoning 6, multi-session 5, knowledge-update 3, single-session-user 3,
  single-session-assistant 2, single-session-preference 1.
* Within a type, instances are ordered by **sha256 of the `question_id`**. Not lexicographically:
  89 of the 133 temporal-reasoning questions carry a `gpt4_` prefix, and a plain sort would have put
  every one of them behind the hex-named ones, so the 6-question temporal stratum would have
  contained exactly zero GPT-4-generated temporal questions. That is a content-correlated bias
  hiding inside an "obvious" tie-break.
* No seed, no shuffle, no re-roll. The membership is a pure function of the dataset file, printed
  into the result JSON as `question_ids`, and anyone can re-derive it.

## 4. Operating point — pinned

| knob | value | why this value |
|---|---|---|
| `k` | 25 | the operating point this repository already published on LoCoMo; not chosen here |
| recall mode | `hybrid` (lexical + semantic RRF) | explicit rather than left to the `auto` threshold |
| `reinforce` | `False` | **not cosmetic.** `recall()` reinforces what it returns, so with the default every query is answered by a store the previous queries modified and the score depends on question order. Measured independently on LoCoMo in this repo the same night: recall@25 `0.783 / 0.648` at the default, `0.8262 / 0.6986` at `reinforce=False`. Pinned off here and recorded in the result JSON. |
| context budget | 24 000 characters, **identical for every arm** | a 9x budget gap once flipped the ranking of a benchmark in this repository |
| embedder | `nomic-embed-text` (local Ollama, 768d) | the repo's standard local embedder |
| answerer | `llama3.1:8b` | fixed across all arms |
| judge | `qwen2.5:7b` | deliberately a **different model family** from the answerer |
| `num_ctx` | 8192, explicit | Ollama's default (2048) truncates silently, which would measure the truncation |
| temperature / seed | 0.0 / 0 | everywhere |
| write path | `remember()` + embedder | **zero LLM**: no extraction, no summarisation, no rewriting |

## 5. Arms — five, one judge, one budget

| arm | what it is | role |
|---|---|---|
| `oracle` | the gold evidence session(s) only | **CEILING** — perfect retrieval; the setting the authors ship as `longmemeval_oracle` |
| `inspeximus` | `recall(question, k=25, mode="hybrid")` over every haystack turn | the system under test |
| `recency` | the newest turns, filled to the same budget | **FLOOR** — no memory system, just keep the tail |
| `shuffled` | inspeximus's own retrieval, pointed at a **different question's store** | control: same code path, same budget, wrong haystack |
| `empty` | no context | control: what the answerer knows without memory |
| `full_context` | the entire haystack | expected **NOT COMPUTABLE** at ~489k characters; reported with the measured size rather than omitted |

### 5a. Recorded deviation from the assigned plan — the ceiling arm

The unit was assigned three arms: **full-context ceiling**, naive-recency floor, inspeximus.
`full_context` is **not computable** on LongMemEval_S on this hardware, and that is a measured fact,
not a preference: a haystack is ~489 000 characters (~122k tokens), while the answerer at
`num_ctx=8192` holds ~24 000. Serving it would need a 128k-token window whose KV cache does not fit
on a shared 24 GiB card. The plan is therefore changed, deliberately and on the record:

* `full_context` **stays an arm** and is executed, so the run reports NOT COMPUTABLE together with
  the measured character count instead of quietly dropping it.
* `oracle` — the gold evidence session(s) only — becomes the **ceiling for the band check**. It is
  the dataset authors' own construct (they ship `longmemeval_oracle`), it is what perfect retrieval
  into the same budget would deliver, and unlike full-context it is a ceiling the system under test
  could in principle reach.

The band check is unchanged in intent and still fails the run: `recency < inspeximus < oracle`.

## 6. The judge gate — the run is VOID without it

`judge_calibration.py`, on the real dataset's own questions:

* **GOLD** — gold answer fed verbatim → must score 1.
* **WRONG** — a *different type's* gold answer fed instead → must score 0.
* **HEDGE** — a refusal ("I don't know") fed instead → must score 0.

Gate: **≥ 90 % on each arm**, 12 cases per arm. GOLD alone proves nothing (a judge that always says
1 passes it). HEDGE is the load-bearing arm: the answer prompt tells the answerer to reply
"I don't know" when memory is empty, so a lenient judge would hand the `empty` control a free score
and destroy the band check below.

Escalation ladder, fixed in advance: if `qwen2.5:7b` fails, try `gemma2:9b`, then `qwen3:30b-a3b`.
Whichever passes first is the judge. No further search.

## 7. Predictions

* **P1** `recency < inspeximus < oracle`, strictly. This is the **band check**, it is asserted in
  code, and a violation **fails the run** (exit 5). Outside the band the harness is measuring
  something other than memory quality and the number is worthless in either direction.
* **P2** `shuffled ≤ recency` and `empty ≤ recency`. A control arm that beats the floor means the
  judge or the answerer is being rewarded for something the memory did not supply.
* **P3** `empty` scores near zero. The questions are about a synthetic user's personal history; a
  model answering them without memory is a defect in the measurement.
* **P4** `full_context` is NOT COMPUTABLE at this operating point (~489k characters against an
  ~24k-character window). This is a statement about the hardware, not about any system.
* **P5** `knowledge-update` and `temporal-reasoning` score below the subset mean. Both need the
  *current* value or a date arithmetic over retrieved turns, and single-turn retrieval supplies
  neither for free.

No prediction is made about the absolute value of `inspeximus`, and none is needed. **A low score is
a valid, publishable result.** The one thing that would make this run worthless is tuning `k`, the
subset, the budget or the judge after seeing it.

## 7a. Outcome — recorded after the scored run, predictions left as written

Run `results/longmemeval_s_small_20260802-002956.json`, 20 questions, contended GPU.

| prediction | outcome |
|---|---|
| **P1** `recency < inspeximus < oracle` | **HELD** — 0.05 < 0.45 < 0.50 |
| **P2** both controls at or below the floor | **`empty` held (0.00); `shuffled` REFUTED** — 0.10 against a 0.05 floor. Two questions of twenty against one. The band check fails the run on it (exit 5) and the number stands as reported. |
| **P3** `empty` near zero | **HELD** — 0.00 exactly |
| **P4** `full_context` not computable | **HELD** — 516 048 characters against an ~24 000-character window |
| **P5** knowledge-update and temporal-reasoning below the subset mean | **SPLIT** — knowledge-update 0.33 (below 0.45), temporal-reasoning 0.50 (above). The temporal *ceiling* is 0.17, so on that slice the answerer is the binding constraint, not retrieval. |

Nothing above was adjusted after seeing the numbers. `shuffled` beating `recency` by one question at
n=20 is a power problem, and the answer to a power problem is `--subset medium` / `--subset full`,
not a looser gate.

## 8. Declared limits

* The judge and answerer are 7–8 B local models. Published LongMemEval numbers (Zep, mem0, and the
  paper's own baselines) use GPT-4-class answerers and judges. **The absolute number here is not
  comparable to theirs.** What is comparable is the *within-run* spread between the arms, which is
  the only thing this harness claims.
* 20 questions is a small subset. The 95 % binomial interval at n=20 is roughly ±0.21 — wide enough
  that only large gaps between arms mean anything. `--subset medium` (50) and `--subset full` (470)
  exist and change nothing except the sample.
* One RTX 3090 is shared with a long-running research process. The harness has a **hard GPU
  pre-flight** (≥ 20 GiB free, no foreign model runner) that refuses to start (exit 3). A run forced
  past it with `--allow-contended-gpu` is stamped `contended: true` in the result JSON and is not
  quotable as a clean measurement.
* Single-session-preference questions are graded against a *rubric*, not a fact, with a separate
  judge prompt; both prompts are hashed into the result JSON.
