# Agent-memory integrity benchmark (open, cross-system, run-it-yourself)

Recall benchmarks (LoCoMo, LongMemEval, MemoryAgentBench) ask *did the store retrieve the right fact*. This
one asks a different question the recall evals skip: **integrity** — which version of a fact wins, whether a
corrected value can be undone on command, whether a restatement resurrects a retired value. It runs the same
adversarial fixture through several memory systems in their **native config** and publishes the matrix
whichever way it falls. If a number here is wrong, the harness is right next to it — run it, or add your system.

This exists because a sharp r/RAG reviewer made the fair point that self-scoring on home fixtures is
unfalsifiable. So: native configs, a shared judge that never sees ground truth, and results published even
where inspeximus does **not** win.

## Methodology (the same for every system)

- **Native config, no tuning in our favor.** mem0 runs on its recommended stack (gpt-4o-mini +
  text-embedding-3-small); Graphiti runs against a live neo4j with its own LLM pipeline; inspeximus runs local.
- **Shared judge.** One OpenAI model reads each system's **full memory state** (`get_all` / all valid facts,
  not just top-k search) and extracts the current value. It never sees the ground truth beyond the two
  candidate tokens, so it can also answer "unclear". Feeding the full state isolates the *integrity* question
  (did the operation change the state) from *retrieval quality* (a different axis we do not test here).
- **Honest reading.** A store that keeps the corrected value when told "go back" is **not wrong** — it simply
  lacks that operation. We report a **capability** difference, never "system X is bad".
- Small n (OpenAI cost). Directional, not a leaderboard. Re-run with a larger `--n` if you want tighter CIs.

## Cell 1 — value-obscuring revert  (`integrity_bench_revert.py`)

Store a value, correct it, then issue an **unmarked** revert that names no value ("go back to what we had",
"roll back the change", "undo it"). Does the current answer return to the OLD value?

    add   "the {entity} is {A}."
    add   "correction: the {entity} is now {B}."
    revert "{unmarked revert, no value}"
    ask   "what is the current {entity}?"   ->   A = revert honored, B = revert ignored

**Symmetric instrument (fairness fix 2026-07-11).** An earlier version scored inspeximus *mechanically* from its own
ledger while mem0/Graphiti went through the LLM judge — an asymmetric instrument a pre-publication red-team
caught. Now **every system is read by the same ground-truth-blind LLM judge on its own native retrieval
surface**. The fix dropped inspeximus's headline from a flattering 1.00 to 0.75.

| system | revert success (n=20) | 95% CI | what happens |
|---|---|---|---|
| **inspeximus** (route/revert) | **0.75** | [0.53, 0.89] | intent router restores the predecessor from the version ledger; 5/20 of inspeximus's own recall surface still reads ambiguous to the neutral judge |
| mem0 2.0.11 (native) | 0.20 | [0.08, 0.42] | no revert operation — the "go back" utterance mostly isn't even stored as a fact, so the corrected value is retained (A=4, B=11, 5 unclear) |
| Graphiti (native, live) | 0.00 | [0.00, 0.16] | no revert operation — keeps the corrected value; bitemporal invalidation fires on named contradictions, not on an unnamed "go back" (A=0, B=11, 9 unclear) |

Reading: value-obscuring revert (undoing a correction from a natural-language command that names no value) is a
capability only inspeximus exposes here. mem0 and Graphiti correctly retain the corrected value; they just have no
channel to undo it on command. Under a fair instrument even the system built for it clears only 0.75, not 1.00 —
and the CIs on inspeximus [0.53, 0.89] and mem0 [0.08, 0.42] do not overlap, so the capability gap survives at n=20.

**Prior art (this is a known-hard property, not a new axis).** Undo-and-consistency-under-update is belief
revision (AGM, 1985), truth-maintenance systems (Doyle, 1979), and bitemporal databases (Snodgrass → SQL:2011).
The 2026 agent-memory benchmark wave — MemConflict (2605.20926), BEAM (2510.27246), TOKI (2606.06240),
STALE (2605.06527), Supersede (2606.27472), plus MemoryAgentBench (2507.05257) and LongMemEval (2410.10813) —
tests *which of two conflicting facts wins*. None tests an **unmarked revert command** or an **adversarial
echo-resurrection**; that narrow, adversarial, command-driven cut is what this harness measures.

The benchmark also improved inspeximus: it surfaced that `route()` missed "roll back" (inspeximus was 0.80) — fixed in
0.7.11.

## Run it / add your system

    # free, local only:
    python probes/integrity_bench_revert.py --systems inspeximus

    # includes paid backends (needs OPENAI_API_KEY in server/.env; Graphiti needs a neo4j at bolt://localhost:7687):
    python probes/integrity_bench_revert.py --systems inspeximus,mem0,graphiti --n 20

Adding a system = one adapter function with the interface `(reset, add(text), revert(text), full memory state
for the judge)`. PRs welcome; we publish whatever it shows.

## Cell 2 — echo resistance  (`integrity_bench_echo.py`)

Store a value, correct it, then **restate the retired value** (an echo — benign repetition or an injected
restatement). Does the current answer stay corrected, or does the stale value come back?

    add   "the {entity} is {A}."
    add   "correction: the {entity} is now {B}."
    echo  "the {entity} is {A}."             # restate the retired value
    ask   "what is the current {entity}?"    ->   B = echo resisted (good), A = resurrected (bad)

**Two honest metrics, and the naive one flatters us — so we don't use it.** Counting "did the system return the
corrected value" would show inspeximus 0.90 / mem0 0.80 / Graphiti 0.55 and imply Graphiti fails echo. It does not.
Measured under the same symmetric instrument as Cell 1 (n=20):

| system | resurrection rate (the attack, lower=better) | 95% CI | clean current-truth rate (answer clarity) |
|---|---|---|---|
| **inspeximus** (echo_guard) | **0.00** | [0.00, 0.16] | 0.90 |
| mem0 2.0.11 (native) | **0.05** | [0.01, 0.24] | 0.80 |
| Graphiti (native, live) | **0.00** | [0.00, 0.16] | 0.55 |
| Hindsight 0.9.2 (native, embedded) | **0.00** | [0.00, 0.16] | 0.95 |

The real finding: **no system systematically resurrects the stale value** — resurrection is at or near zero
across the board (inspeximus 0/20, Graphiti 0/20, mem0 1/20 = 0.05; within noise, not a systematic failure). An
earlier probe of ours over-stated this failure mode; corrected here. Note inspeximus's clean rate is 0.90, not a
suspiciously perfect 1.00 — under the fair instrument even inspeximus's recall surface reads ambiguous to the judge
2/20 of the time. Where the systems actually differ is *answer clarity*: inspeximus and mem0 hand back a single
current value; Graphiti, by bitemporal design, surfaces both the invalidated old edge and the valid new one, so
a naive reader (our judge, 9/20) sees ambiguity — that is a different retrieval contract, **not** a resurrection. If
your consumer resolves validity itself, Graphiti's behaviour is correct; if it just reads the top facts, the
ambiguity can bite.

This cell is the honest counterweight to the revert cell: on the attack that actually matters (resurrection),
inspeximus does **not** win — every system lands at or near zero. Publishing that is the whole point.

## Cell 3 — who resolves the correction, the store or the reader  (`integrity_bench_store_resolves.py`)

Cell 2 ends by noting that Graphiti returns the invalidated old edge alongside the valid new one, and that a
naive reader then sees ambiguity. This cell turns that observation into the measurement, because it decides
something the other two cells cannot see: **cells 1 and 2 read every system through an LLM judge, for fairness,
and a judge cannot tell a store that settled the conflict from a store that returned both and was read well.**

So this one removes the judge entirely and classifies the RAW recall payload:

    resolved_at_store : the payload carries the corrected value and NOT the retired one
    both_returned     : it carries both — the caller has to decide
    only_stale        : it carries the retired value only
    neither           : retrieval missed

No model is involved on our side, so the inspeximus arm is free, deterministic, and reproducible in seconds.

| system | store-resolution rate | 95% CI | resolved / both / stale / neither (n=20) |
|---|---|---|---|
| **inspeximus 2.20.1** (from PyPI) | **1.00** | [0.84, 1.00] | 20 / 0 / 0 / 0 |
| mem0 (native) | 0.05 | [0.01, 0.24] | 1 / 18 / 0 / 1 |
| Hindsight 0.9.2 (native, embedded) | 0.00 | [0.00, 0.16] | 0 / 19 / 0 / 1 |
| Graphiti | — | — | not run — the arm REFUSES when neo4j is unreachable rather than scoring an empty graph |

Hindsight's 0/20 is a replication: two independent runs returned the identical split. The intervals do not
come close to touching.

**This is why Cell 2 was a tie.** Hindsight scores 0.95 clean-current-truth there, better than ours, and it
earns that honestly — but not because its store settled the correction. Its recall returns both values and the
judge picks the right one. Remove the judge and 19 of 20 callers receive the stale value beside the current
one. Our arm reached the same place with zero model calls; theirs ran an embedded Postgres, downloaded an
embedding model, and called an extractor on every write.

**Returning both is not automatically worse, and this cell does not grade it.** A bitemporal store handing
back old and new with validity markers is being honest, and a caller that reads the markers is fine. What the
number establishes is *whose job* disambiguation is — which is a different product promise, not a defect. If
your consumer resolves validity itself, 'both' is the contract you want. If it reads the top facts and acts,
it is the contract that bites.

Run it: `python probes/integrity_bench_store_resolves.py --systems inspeximus` is free and needs
nothing but the package. Adding `,mem0` or `,hindsight` costs their native extractor calls.

## Cell 4 — is the resulting state reproducible?  (`integrity_bench_determinism.py`)

Cells 1-3 ask what a store returns. This asks whether it returns the **same thing twice**. Run the identical
corrections against a fresh store, twice, and compare what recall holds.

| system | reproducible state | cases differing (n=20) | model calls per run | wall clock |
|---|---|---|---|---|
| **inspeximus 2.20.1** | **byte-identical** | **0 / 20** | **0** | **0.002s + 0.001s** |
| Hindsight 0.9.2 (native) | no | 20 / 20 | 60 | 727s + 576s |

18 of Hindsight's 20 differ in wording alone, 2 also in how many facts were extracted. Both passes are full
and well-formed, so this is extraction variance, not an error:

    run 1: Cache region changed to Malmo. | Correction/update to previous cache region information
    run 2: Cache region was Osaka; corrected to malmo; later restated as Osaka ...

Same three sentences in, a different stored state out.

**A confound this cell had, and lost.** The first pass counted 20/20 differing while comparing timestamps
too — Hindsight stamps every extracted fact with a wall clock, so two runs minutes apart differ for a reason
that has nothing to do with a model, and our own payload carries no timestamp at all. Any system that records
time would have "failed". Timestamps are normalised before hashing now, with a control asserting identical
text at different times compares EQUAL. The 20/20 above is what survives that fix. Ordering is normalised too,
so returning the same facts in a different sequence is never scored as non-determinism.

**Non-determinism is the price of extraction, not a defect, and this cell does not grade it.** Extraction buys
Hindsight and mem0 something inspeximus does not have: they absorb a fact from prose with no key, where we
need one. What it costs is the ability to answer *what did the store hold on Tuesday* by re-running Tuesday's
writes. If you never re-derive state, this cell is not about you. If you ship an audit trail, it is the whole
question — a trail you cannot re-derive is a log, not evidence.

Run it: `python probes/integrity_bench_determinism.py --systems inspeximus` needs no key, no server and no
network, and finishes in milliseconds. Adding `,hindsight` costs their native extractor twice over.

## Planned cells (harness shape is the same)

- **graphiti on cell 3** — the one arm that refused; needs a live neo4j.
- **conflict-consolidation** — the MemoryAgentBench-style task where every system is weak (best ~54% single-hop);
  a shared harness to compare on the same fixture.

Every number traces to a probe in this folder. Nothing here is a claim about recall quality — we have not
benchmarked inspeximus's retrieval against mem0/Zep and assume they lead on that axis until we show otherwise.
