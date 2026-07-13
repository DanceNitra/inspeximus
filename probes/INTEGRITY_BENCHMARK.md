# Agent-memory integrity benchmark (open, cross-system, run-it-yourself)

Recall benchmarks (LoCoMo, LongMemEval, MemoryAgentBench) ask *did the store retrieve the right fact*. This
one asks a different question the recall evals skip: **integrity** — which version of a fact wins, whether a
corrected value can be undone on command, whether a restatement resurrects a retired value. It runs the same
adversarial fixture through several memory systems in their **native config** and publishes the matrix
whichever way it falls. If a number here is wrong, the harness is right next to it — run it, or add your system.

This exists because a sharp r/RAG reviewer made the fair point that self-scoring on home fixtures is
unfalsifiable. So: native configs, a shared judge that never sees ground truth, and results published even
where mnemo does **not** win.

## Methodology (the same for every system)

- **Native config, no tuning in our favor.** mem0 runs on its recommended stack (gpt-4o-mini +
  text-embedding-3-small); Graphiti runs against a live neo4j with its own LLM pipeline; mnemo runs local.
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

**Symmetric instrument (fairness fix 2026-07-11).** An earlier version scored mnemo *mechanically* from its own
ledger while mem0/Graphiti went through the LLM judge — an asymmetric instrument a pre-publication red-team
caught. Now **every system is read by the same ground-truth-blind LLM judge on its own native retrieval
surface**. The fix dropped mnemo's headline from a flattering 1.00 to 0.75.

| system | revert success (n=20) | 95% CI | what happens |
|---|---|---|---|
| **mnemo** (route/revert) | **0.75** | [0.53, 0.89] | intent router restores the predecessor from the version ledger; 5/20 of mnemo's own recall surface still reads ambiguous to the neutral judge |
| mem0 2.0.11 (native) | 0.20 | [0.08, 0.42] | no revert operation — the "go back" utterance mostly isn't even stored as a fact, so the corrected value is retained (A=4, B=11, 5 unclear) |
| Graphiti (native, live) | 0.00 | [0.00, 0.16] | no revert operation — keeps the corrected value; bitemporal invalidation fires on named contradictions, not on an unnamed "go back" (A=0, B=11, 9 unclear) |

Reading: value-obscuring revert (undoing a correction from a natural-language command that names no value) is a
capability only mnemo exposes here. mem0 and Graphiti correctly retain the corrected value; they just have no
channel to undo it on command. Under a fair instrument even the system built for it clears only 0.75, not 1.00 —
and the CIs on mnemo [0.53, 0.89] and mem0 [0.08, 0.42] do not overlap, so the capability gap survives at n=20.

**Prior art (this is a known-hard property, not a new axis).** Undo-and-consistency-under-update is belief
revision (AGM, 1985), truth-maintenance systems (Doyle, 1979), and bitemporal databases (Snodgrass → SQL:2011).
The 2026 agent-memory benchmark wave — MemConflict (2605.20926), BEAM (2510.27246), TOKI (2606.06240),
STALE (2605.06527), Supersede (2606.27472), plus MemoryAgentBench (2507.05257) and LongMemEval (2410.10813) —
tests *which of two conflicting facts wins*. None tests an **unmarked revert command** or an **adversarial
echo-resurrection**; that narrow, adversarial, command-driven cut is what this harness measures.

The benchmark also improved mnemo: it surfaced that `route()` missed "roll back" (mnemo was 0.80) — fixed in
0.7.11.

## Run it / add your system

    # free, local only:
    python mnemo/probes/integrity_bench_revert.py --systems mnemo

    # includes paid backends (needs OPENAI_API_KEY in server/.env; Graphiti needs a neo4j at bolt://localhost:7687):
    python mnemo/probes/integrity_bench_revert.py --systems mnemo,mem0,graphiti --n 20

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
corrected value" would show mnemo 0.90 / mem0 0.80 / Graphiti 0.55 and imply Graphiti fails echo. It does not.
Measured under the same symmetric instrument as Cell 1 (n=20):

| system | resurrection rate (the attack, lower=better) | 95% CI | clean current-truth rate (answer clarity) |
|---|---|---|---|
| **mnemo** (echo_guard) | **0.00** | [0.00, 0.16] | 0.90 |
| mem0 2.0.11 (native) | **0.05** | [0.01, 0.24] | 0.80 |
| Graphiti (native, live) | **0.00** | [0.00, 0.16] | 0.55 |

The real finding: **no system systematically resurrects the stale value** — resurrection is at or near zero
across the board (mnemo 0/20, Graphiti 0/20, mem0 1/20 = 0.05; within noise, not a systematic failure). An
earlier probe of ours over-stated this failure mode; corrected here. Note mnemo's clean rate is 0.90, not a
suspiciously perfect 1.00 — under the fair instrument even mnemo's recall surface reads ambiguous to the judge
2/20 of the time. Where the systems actually differ is *answer clarity*: mnemo and mem0 hand back a single
current value; Graphiti, by bitemporal design, surfaces both the invalidated old edge and the valid new one, so
a naive reader (our judge, 9/20) sees ambiguity — that is a different retrieval contract, **not** a resurrection. If
your consumer resolves validity itself, Graphiti's behaviour is correct; if it just reads the top facts, the
ambiguity can bite.

This cell is the honest counterweight to the revert cell: on the attack that actually matters (resurrection),
mnemo does **not** win — every system lands at or near zero. Publishing that is the whole point.

## Planned cells (harness shape is the same)

- **conflict-consolidation** — the MemoryAgentBench-style task where every system is weak (best ~54% single-hop);
  a shared harness to compare on the same fixture.

Every number traces to a probe in this folder. Nothing here is a claim about recall quality — we have not
benchmarked mnemo's retrieval against mem0/Zep and assume they lead on that axis until we show otherwise.
