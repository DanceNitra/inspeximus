# Parity harness — one corpus, one reader, several memory systems

Five separate measurement efforts already lived in this repo (`benchmarks/memops/`,
`probes/integrity_bench_*`, `probes/forget_verification_bench.py`, `benchmarks/property_benchmark.py`,
`bench/`). Each answered one question on its own corpus with its own instrument, and no single place put
the losing rows next to the winning ones. This directory is that place. It **consolidates** those efforts;
it does not restart them.

**Every number lives in [`RESULTS.md`](RESULTS.md), which is generated from the committed result JSON.**
This README deliberately restates none of them. That is not fastidiousness: the benchmark this harness
replaces published a table in `bench/README.md` (32k 0.44 / 64k 0.50 / 262k 0.36) that **does not
reproduce from the committed `bench/results_cr_sweep.json`** beside it (0.52 / 0.42 / 0.38) — two
different runs, unlabelled. A hand-copied number drifts from its artifact; a generated one cannot.

Read [`PREREGISTRATION.md`](PREREGISTRATION.md) first. The axes, the metrics, the arms and the expected
directions — including **Q1**, which predicts we lose on retrieval, and **F1**, which can turn the
headline result RED — were fixed before any number existed.

## What it measures

| | axis | why it is here |
|---|---|---|
| **spine** | MemoryAgentBench **Conflict Resolution** (third-party data, their metric) | neutral ground; a sceptical reader discounts a fixture we wrote, and correctly |
| W | write cost / latency | deterministic writes make no model call; LLM-extracting stores do one per write |
| R | revert to a prior value | an *unmarked* revert that names no value |
| C | echo / repetition resistance | a retired value restated |
| E | verifiable erasure / residue | leakage, residue in the persisted bytes, over-forgetting, receipt |
| Q | retrieval quality | included **because we expect to lose it** |

## Run it

```bash
python benchmarks/parity/corpus.py                   # regenerate + verify the fixture digest
python benchmarks/parity/run.py --subset small       # the axes (zero-LLM arms, ~5 s)
python benchmarks/parity/cr_control.py --stage 1     # the spine's mechanism control, zero LLM calls
python benchmarks/parity/cr_control.py --stage 1,2   # + their metric end-to-end (needs a quiet GPU)
python benchmarks/parity/render.py                   # -> RESULTS.md
```

The corpus is **generated, not stored**: the generator, the seed and a SHA-256 per subset live in
`corpus_manifest.json`, and `load()` refuses to run if the generator has drifted from the pinned digest.
Regenerate and compare the digest to prove you hold the same fixture the published numbers came from —
that is the property that matters, and it costs ten lines of diff instead of eighteen thousand.

## The rules this harness enforces in code

**A competitor scoring 0.000 is OUR bug until proven otherwise.** Every arm must pass a positive control
— write three facts, read one back — before any number from it is recorded. An arm that fails is reported
`NOT-MEASURED` with its reason, never as a zero. This rule has already caught two of our own defects in a
previous mem0 arm (a `sess[:6000]` truncation, and `limit=` passed to an API whose parameter is `top_k=`),
both of which had produced a flattering zero.

**Never "competitors can't X".** A prior gate here falsified "only inspeximus can revert" — Letta ships
checkpointing. Every row describes OUR mechanism, reports THEIR measured number, and lets the reader
compare. An arm that has no revert channel is scored on what it *does* expose: the utterance is stored as
another fact, which is what that system genuinely does.

**One reader, every arm.** A single LLM-free verdict function reads each arm's own retrieval surface. A
store that returns the retired value alongside the current one scores `unclear` — a read-contract
difference, not a failure — and `unclear` is always its own column, never folded into someone's win.

**An attack is only scored where it has something to attack.** The revert and echo axes first check that
the correction *took effect in that arm's read surface*. Without this, the `naive` keep-all arm scored
**revert_success 1.00 with no revert channel at all**, purely because its ranker preferred the original,
cleanly-worded sentence over the wordier "correction: ..." line — so the read surface had been serving
the old value the whole time and the "revert" changed nothing. That is now reported as
`NOT-MEASURED — correction never took effect`, which is the honest cell.

**Rows where we lose are published.** `render.py` emits a "Rows where inspeximus does NOT come first"
section that it computes itself, so a table of only our wins cannot be produced from this script by
accident.

## Honest scope

- Small n. Every rate carries a Wilson 95% interval and most of them are wide. Directional, not a
  leaderboard.
- The axes corpus is **self-authored**. That is why the third-party spine exists, and why the retrieval
  axis is built to be hostile to our own zero-dependency default (probes share no content word with the
  fact they target).
- The CR result is scoped to one task, the context lengths actually run, one answerer and their metric.
  It is **not** evidence of a general accuracy win: supersession-as-accuracy has four independent nulls
  against it in this repo, and nothing here retracts them.
- The box has one RTX 3090 shared with other long-lived processes that this harness may not stop.
  `preflight.py` **refuses to start** LLM work when free VRAM is under 20 GB or a model server is
  resident. Accuracy is contention-invariant; latency is not, so any run under
  `--allow-contended-gpu` is stamped and no latency claim is made from it.

## Prior art in this repo, reused rather than re-derived

- `benchmarks/memops/` — MemOps pilot, judge calibration, the mem0 positive control, the erasure/revert
  spec. The positive-control discipline here is that file's rule, generalised to every arm.
- `probes/integrity_bench_revert.py` / `_echo.py` — the cross-system integrity cells, with an **LLM
  judge**. This harness uses an LLM-free reader instead, so the two are complementary, not duplicates:
  where they disagree, the judge-based numbers and their operating point stay in `probes/`.
- `bench/memoryagentbench_cr.py` — the fact-template parser and keyed consolidation, imported directly.
