# LOCOMO, end to end and reproducible

For two years this repository cited a LOCOMO retrieval pair — **recall@25 = 0.783 (any supporting turn)
/ 0.648 (all supporting turns)**, n=1536 — that nobody could re-run, including us. The 1.54.0 CHANGELOG
said so out loud: *"reported, not independently reproducible from this repo."* A number you cannot
re-run is a claim, not a measurement, and it sat in our own release notes as one.

This directory is the harness that closes it. One command, a pinned operating point, a committed
result, and a test that fails when a re-run drifts.

**The published pair reproduces.** At its own operating point — the one the original probe actually ran,
`reinforce=True` — this harness measures **0.7839 / 0.6484** against the published **0.783 / 0.648**:
+0.0009 and +0.0004, on the identical denominator of 1536 questions. At the operating point this
harness *pins* (`reinforce=False`, which is deterministic) it measures **0.8262 / 0.6986**. The
published figure was not optimistic; it was, if anything, 0.04 short of what the same recipe scores when
recall stops mutating its own store mid-benchmark.

---

## The numbers

### Retrieval recall — full set, all 10 conversations, n=1536, LLM-free

| arm | `reinforce` | recall_any@25 | recall_all@25 | vs published |
|---|---|---|---|---|
| **published_config** — the probe's own operating point | `True` (recall's default) | **0.7839** | **0.6484** | **+0.0009 / +0.0004 — matches** |
| **pinned** — the harness operating point | `False` | **0.8262** | **0.6986** | +0.0432 / +0.0506 |
| *published reference (2024, `probes/retrieval_recall_locomo.py`)* | `True` | *0.783* | *0.648* | — |

`results/full_retrieval.json` · reproduce with `python benchmarks/locomo/run.py --subset full
--retrieval-only`. No model is called: with the embedding cache warm this arm makes zero GPU requests
and is bit-for-bit deterministic.

Why the two arms differ at all is the interesting part. `recall()` defaults to `reinforce=True`, which
updates each returned record's value and last-access time — so the *n*-th query is answered by a store
the previous *n−1* queries modified, and the result depends on the order the questions were asked in.
That is correct behaviour for a memory that learns from use, and wrong for a benchmark that wants the
same answer twice. Pinning it off costs nothing and gains 4-5 points.

### End-to-end QA - conversation 0, 20 questions, one judge across all six arms

`results/small.json`, per-item judgements in `results/small_rows.json`, judge gate in
`results/judge_calibration.json`. Answerer `llama3.1:8b`, judge `qwen2.5:7b`, **quiesced card**
(`gpu.contended: false`, 0 foreign runners, window stable 14312 -> 4782 MiB), 242 model calls,
p50 2.53 s, **zero errors, zero unparseable verdicts, zero sub-50 ms replies**.

| arm | what it is | role | accuracy |
|---|---|---|---|
| `ceiling_verbatim` | the gold answer written into the store as a record, then retrieved normally | control, must be >=0.80 | **0.90** (18/20) |
| `fullcontext` | the whole conversation in the prompt, no retrieval | band ceiling | **0.20** (4/20) |
| `inspeximus` | the pinned recipe: hybrid RRF + soft speaker `prefer=` | the subject | **0.10** (2/20) |
| `floor_shuffled` | every question answered from *another* question's retrieved context | control, must be <=0.35 | **0.10** (2/20) |
| `naive_recency` | the last *k* turns, query-blind - what many agent frameworks ship as "memory" | band floor | **0.05** (1/20) |
| `floor_empty` | no context at all | control, must be <=0.20 | **0.05** (1/20) |

Band: `0.05 < 0.10 < 0.20`, strictly. All three controls pass. The verbatim record was retrieved for 95%
of questions and answered correctly for 90%, so the chain demonstrably works end to end.

**Read these numbers with two caveats, and both of them lower the headline.**

*The slice is the hard one, by accident.* `small` takes the first 20 answerable questions of conversation
0 in dataset order, and that slice happens to hold **8 multi-hop, 10 temporal, 2 open-domain and zero
single-hop** questions - while single-hop is 841 of the benchmark's 1536 (55%) and is by some margin the
easiest category. The subset was not re-picked after this was noticed. Treat 0.10 as a lower bound
measured on a hard slice, not as an estimate of a benchmark-wide QA score; `--subset medium` is there for
a wider run.

*The answerer is the binding constraint, not the memory.* On this same conversation retrieval recall@25 is
**0.80** - the supporting turn is in the context four times out of five - and end-to-end QA is 0.10. The
gap is not retrieval: `floor_shuffled` ties `inspeximus` here, and `fullcontext`, handed the entire
conversation and doing no retrieval at all, reaches only 0.20 - which bounds what *any* retriever could
add. A local 8B answerer turning retrieved evidence into a judged-correct answer is where this pipeline
loses its accuracy. That is exactly why the retrieval number and the QA number had to be measured at one
operating point rather than quoted from two, and it is why this directory does not headline the QA score.

The absolute QA number is judge-dependent and is **not** comparable to mem0's 66.9% or Zep's 71.2%, which
were scored by gpt-4o under their own harnesses. What is comparable is the *ordering* of the arms, because
one judge grades all six from the same prompt.

---

## Run it

```bash
# 1. Get the dataset (we do not redistribute it — it is not ours to ship)
#    https://github.com/snap-research/locomo -> locomo10.json
#    sha256 79fa87e90f04081343b8c8debecb80a9a6842b76a7aa537dc9fdf651ea698ff4
mv locomo10.json benchmarks/locomo/data/          # or: export INSPEXIMUS_LOCOMO_PATH=/path/to/locomo10.json

# 2. Local models (Ollama). Nothing else is needed: no pip install, no API key.
ollama pull nomic-embed-text llama3.1:8b qwen2.5:7b

# 3. The command
python benchmarks/locomo/run.py --subset small
```

That re-measures and then **compares against `results/small.json`**, printing every field with its
delta and its tolerance, and exiting non-zero if anything drifted. Useful variants:

```bash
python benchmarks/locomo/run.py --subset full --retrieval-only   # the 0.783/0.648 check, no GPU
python benchmarks/locomo/run.py --subset small --update-baseline # re-cut the committed result
python benchmarks/locomo/judge_calibration.py                    # the judge gate on its own
python -m pytest tests/test_locomo_benchmark.py -q               # the guard rails
```

Exit codes are distinct on purpose, so *"it skipped"* can never be read as *"it passed"*:

| code | meaning |
|---|---|
| 0 | ran, controls passed, within tolerance of the committed result |
| 1 | a control failed, the band failed, or a number drifted |
| 2 | the judge did not clear its calibration gate — the run is void |
| 3 | the dataset is absent, or its sha256 is not the pinned one — **skipped, with a reason** |
| 4 | the GPU pre-flight refused to start (contended) |

Zero benchmark dependencies leak into the library. `harness.py` imports the standard library and
`inspeximus`, nothing else, and nothing here is an install requirement.

---

## Controls — the part that is allowed to say the harness is broken

A benchmark that can only report a score is a benchmark that cannot tell a good store from a good
answerer. Three controls run beside every QA measurement, and if any of them fails, `run.py` prints
**HARNESS BROKEN** and exits non-zero instead of publishing a number.

- **`floor_empty` — no context.** Chance on open-ended QA is ~0. If this scores well, the answerer is
  answering from world knowledge and *nothing* in the run measures memory.
- **`floor_shuffled` — someone else's context.** Each question is answered from another question's
  retrieved context (a seeded derangement, no fixed points). Same length, same style, same
  conversation, so it isolates *relevance* rather than *the presence of text*. It is a deliberately
  conservative floor: a deranged context can still contain the answer by luck, which is why its bound
  is looser than `floor_empty`'s.
- **`ceiling_verbatim` — the answer is in the store.** The gold answer is written in as a real record
  and retrieved normally, exercising write → recall → answer → judge. If this scores badly, a low
  `inspeximus` score would be the harness, not the store. The record is *keyed*, so each question's
  control record supersedes the previous one and only the live one is recallable — a test asserts none
  of them survive the run.

And a fourth check on top, on the three comparison arms: `inspeximus` must land **strictly between**
`naive_recency` and `fullcontext`. A violation is reported, never tuned away. Retrieval beating a
stuffed context is a real result; so is retrieval failing to beat a last-*k* buffer.

Every one of these gates is itself tested with an input it cannot pass — a floor that scores 0.90, a
ceiling that scores 0.20, a band that inverts, an arm that never ran, a comparison whose field vanished.
A gate that has never been shown failing has measured nothing.

### The judge is gated before it is trusted

`judge_calibration.py` reuses the design of `benchmarks/memops/judge_calibration.py`: three arms,
≥90% on each, or the run is void. Adapted to this metric, they are **GOLD** (the gold answer verbatim →
must say YES), **WRONG** (another question's gold answer → must say NO) and **REFUSAL** ("I don't know"
→ must say NO). WRONG is the load-bearing one: the floor controls only mean anything if a judge handed
an unrelated answer says no. Measured with `qwen2.5:7b`: GOLD 11/12, WRONG 12/12, REFUSAL 12/12.

The judge is deliberately a different model family from the answerer (`qwen2.5:7b` grading
`llama3.1:8b`), so no answer is graded by its own author.

---

## The pinned operating point

Everything that moves the number lives in `config.json`, and `run.py` stamps the whole config into
every result file — so a number can never be read without the conditions it was measured under.

| | |
|---|---|
| dataset | `locomo10.json`, sha256 `79fa87e9…698ff4`, 10 conversations / 5882 turns |
| questions | categories 1-4 (5 is LOCOMO's adversarial class), evidence required |
| retrieval | k=25, `mode="hybrid"`, `prefer={"speaker": …}`, **`reinforce=False`** |
| embedder | `nomic-embed-text` (local), disk-cached |
| answerer | `llama3.1:8b` @ `localhost:11434/v1`, temperature 0, 80 tokens |
| judge | `qwen2.5:7b`, temperature 0, 8 tokens |
| context budget | 6000 chars for every retrieval arm; `fullcontext` gets the conversation |
| seed | 20260801 (the derangement) |

`mode="hybrid"` and the soft speaker `prefer=` are not guesses; both were measured, and the probes that
measured them are named under *Provenance* below.

### Two denominators, because the choice is worth 0.005

The published pair counted **1536** questions. Five of them carry evidence ids that match no turn in
their own conversation — no retriever can ever hit them, and the original probe scored them as misses.
This harness reports that denominator as the headline (it is the one the published number used) and
`*_resolvable` (n=1531) beside it. Quietly dropping five unwinnable questions and calling the result the
same metric would have been flattering by exactly the margin that makes a reproduction look like an
improvement.

### Tolerance, and where it comes from

Measured, by running `--subset small` three times at the same operating point and diffing every published
field. The retrieval half came back **identical every time**; the judged half moved by at most one
question on any arm.

| field | run 1 | run 2 | run 3 (committed) | spread | tolerance |
|---|---|---|---|---|---|
| `retrieval.pinned.recall_any` | 0.8000 | 0.8000 | 0.8000 | **0.0000** | +/-0.02 |
| `retrieval.pinned.recall_all` | 0.6667 | 0.6667 | 0.6667 | **0.0000** | +/-0.02 |
| `retrieval.published_config.recall_any` | 0.7200 | 0.7200 | 0.7200 | **0.0000** | +/-0.02 |
| `retrieval.published_config.recall_all` | 0.5933 | 0.5933 | 0.5933 | **0.0000** | +/-0.02 |
| `qa.naive_recency` | 0.05 | 0.05 | 0.05 | 0.00 | +/-0.10 |
| `qa.inspeximus` | 0.15 | 0.20 | 0.10 | 0.10 | +/-0.10 |
| `qa.fullcontext` | 0.20 | 0.25 | 0.20 | 0.05 | +/-0.10 |
| `qa.floor_empty` | 0.05 | 0.05 | 0.05 | 0.00 | +/-0.15 |
| `qa.floor_shuffled` | 0.10 | 0.10 | 0.10 | 0.00 | +/-0.15 |
| `qa.ceiling_verbatim` | 0.90 | - | 0.90 | 0.00 | +/-0.15 |

Runs 1 and 2 were measured while the card was shared; **run 3 is the committed one and is the only
quiesced one**, which is why it is the baseline rather than an average. Contended numbers are discarded
or re-measured, never averaged with clean ones - the two are different experiments.

So the bands are not guesses:

- **retrieval +/-0.02.** With the embedding cache warm and `reinforce=False` this half is deterministic and
  re-ran to 0.0000 three times, on a busy card and on an idle one. It makes zero model calls, so GPU
  contention cannot touch it. If it ever moves by 0.02, something real changed.
- **QA +/-0.10.** On 20 questions one flipped judgement is 0.05. `inspeximus` spanned 0.10-0.20 across the
  three runs, which is the full width of the band - honest evidence that 20 judged questions is a coarse
  instrument, and the reason the QA score is reported but not headlined.
- **control +/-0.15.** Controls are pass/fail against their own bound; every control reproduced exactly.

`tests/test_locomo_benchmark.py` asserts these against the committed result and - more importantly -
asserts that the comparison **can fail**, by feeding it a drifted number, a vanished field and an empty
comparison, and requiring it to say so in each case.

---

## Provenance — which probe produced which published number

The one-off probes in `probes/` are not deleted; this harness *imports* the load-bearing ones, so the
store and the retrieval here are literally the code path the published number came from.

| probe | what it produced |
|---|---|
| `probes/retrieval_recall_locomo.py` | **the published 0.783 / 0.648 @k=25, n=1536.** Its metric definition (recall_any / recall_all over LOCOMO's gold evidence turns) is reimplemented here field for field. |
| `probes/locomo_qa.py` | the QA scaffold this harness imports: `conv_turns` (which prepends each session's date — without it LOCOMO's temporal category is unanswerable), `nomic_embed`, `build_inspeximus_store`, `recall_context`, and the inspeximus / fullcontext / naive arm triple. **No QA number was ever published from it.** |
| `probes/locomo_retrieval_map.py` | hybrid RRF vs a single vector index, 0.609 vs 0.552 recall@20 — why `mode="hybrid"` is pinned. |
| `probes/locomo_metadata_prefilter.py` | the speaker pre-filter: a hard filter wins overall but **zeroes** the subset where the gold turn belongs to the other speaker; the soft filter keeps the gain and the fallback. Why `prefer=` is pinned over `where=`. |
| `probes/locomo_soft_prefer_filter.py` | validation of the shipped soft `prefer=` through `recall()` itself. |
| `probes/locomo_composed_soft_filters.py`, `probes/locomo_correlated_cue_composition.py` | composition of soft cues (capped sum vs product). Not part of this operating point; listed so the family is accounted for. |

### Why it was not reproducible, precisely

Three things, all verified before this harness was written:

1. **The probes could not find their own data.** `probes/retrieval_recall_locomo.py` and
   `probes/locomo_qa.py` resolve the dataset as `<HERE>/../../agora_output/lab/data/locomo10.json`.
   They were written under `research/probes/` in a different repository; copied into `probes/`, that
   path points *outside* this repo at a file that does not exist. Both probes fail on load.
2. **Nothing was pinned.** `reinforce` was left at its default, and its default makes recall
   order-dependent.
3. **No result was committed**, so a re-run had nothing to disagree with.

`run.py` fixes all three, and the resolution order — `--data`, then `$INSPEXIMUS_LOCOMO_PATH`, then
`$LOCOMO_PATH` (what the old probes use), then `benchmarks/locomo/data/` — means the harness finds the
file wherever it already is, and says exactly where it looked when it cannot.

---

## Honest scope

- **One judge, locally hosted.** The QA absolute is judge-dependent. It is not comparable to a
  gpt-4o-judged number from another paper, and this directory does not claim it is.
- **`--subset small` is 20 questions from one conversation.** Enough to catch drift and to exercise
  every control on a GPU shared with other processes; not enough to rank memory systems. The
  retrieval half runs the full 1536.
- **The committed QA result was measured on a quiesced GPU** — `gpu.contended: false`, zero foreign
  runners, 20,117 MiB effective free. The pre-flight is a hard gate: it refuses to start when another
  job's model runner is on the card or when there is not enough VRAM, and `--allow-shared-gpu`
  overrides it only by stamping `gpu.contended: true` on the result, so a contended number can never
  be read as a quiesced one. The harness never kills anything to get its window — that belongs to
  whoever owns the machine.
- **Retrieval recall is an upper bound on QA**, not a proxy for it. A retrieved gold turn that the
  answerer misreads still counts as recall and not as an answer. Reporting both at the same operating
  point is the point of this directory.
- **A 0.0 s model reply is a cache hit, not a call.** Liveness is probed with a unique nonce and the
  *answer* is checked, and `latency.suspected_cache_hits` counts anything that returned faster than
  50 ms.
