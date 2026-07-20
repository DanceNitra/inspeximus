# Probe spec — erasure completeness & revert exactness on MemOps data

**Written before the probe runs.** Same pre-registration discipline as `PREREGISTRATION.md`: predictions
and decision rules are fixed here first, including the ones that can go against us.

## Why this probe exists

The MemOps pilot settled one thing: on "what is the current value", mnemo's keyed supersession buys
**nothing measurable** over a naive keep-all store (0.593 vs 0.592; P2/P3 REFUTED). That is the second
independent null after MAB. The honest reading is that a strong answerer handed 150 sentences of history
resolves corrections by itself — the write-side integrity layer has no room to help.

But MemOps only ever asks *what is the current value*. It never asks:

- **can this value be REMOVED on request, provably, and does it stay removed?**
- **can a correction be UNDONE to an exact prior state?**

Those are the two axes where a competitive scan of 11 systems found no equivalent primitive. This probe
measures them on the same external data, with **no LLM judge** — every metric is a string or byte check,
so judge instability (the acknowledged weakness of the pilot) cannot touch it.

## Fairness rules (binding — these are what stop this from being a rigged home-field test)

1. **Never score a competitor on a primitive it does not expose; score the BEST PATH it does expose.**
   mem0 has `delete`/`delete_all` and a history DB with `old_memory`. The naive keep-all arm gets the
   competent-engineer path: delete every record whose text contains the value. Both are real erasure
   implementations and are scored as such.
2. **The claim is never "they cannot do it".** It is: how many steps, is it deterministic, does it need
   external knowledge, and what residue is left. ([[competitors-CAN-erase-revert-mnemo-moat-is-determinism]])
3. **Deletion by literal value is the shared baseline.** mnemo does NOT get to use a lineage-aware
   `forget_subject` unless the other arms get their nearest equivalent, because the ingest wrote no
   subject provenance. Any mnemo advantage must come from what the store DOES, not from a richer API call.
4. **Any zero from a competitor is our bug until a positive control says otherwise** — the rule the mem0
   arm already taught us this session (20 memories vs 262).

## Test A — erasure completeness

**Data**: the MemOps `forget` scenarios (the subset used by the pilot). Each names a value the user
explicitly asks to be forgotten, plus retained facts that must survive.

**Procedure per arm**: ingest the full long-context stream → issue the arm's best deletion path for the
forgotten value → measure, with zero LLM calls:

| metric | how | direction |
|---|---|---|
| `retrieval_leakage` | over every scenario probe question, does the retrieved context still contain the value (or a trace alias)? | lower better |
| `paraphrase_residue` | same check against **aliases from the operation trace**, not just the literal string — catches a store that kept an LLM's reworded copy | lower better |
| `raw_residue` | does the value appear in the persisted bytes on disk after deletion (store file / qdrant segments / sqlite)? | lower better |
| `over_forget` | fraction of RETAINED control facts no longer retrievable | lower better |
| `steps` / `llm_calls` | API calls and LLM calls the deletion cost | lower better |
| `deterministic` | repeat the whole ingest+delete on a fresh store; is the resulting state byte-identical? | yes/no |
| `receipt` | is there a verifiable artifact proving the deletion happened? | yes/no, reported as governance, NOT accuracy |

**Predictions**

| # | Prediction | SUPPORTED if | REFUTED if |
|---|---|---|---|
| E1 | mnemo and naive keep-all tie on literal `retrieval_leakage` | both ≤ 0.05 | either > 0.05 |
| E2 | mem0 shows higher `paraphrase_residue` than the verbatim arms | ≥ 15 pp higher | < 15 pp, or lower |
| E3 | mnemo has zero `raw_residue`; at least one other arm does not | mnemo 0 and some arm > 0 | mnemo > 0 |
| E4 | mnemo is deterministic across repeats; the LLM-extraction arm is not | mnemo identical 3/3, mem0 not | mnemo differs across repeats |

E2 is the load-bearing one and it is a real risk to us: if mem0's paraphrases delete just as cleanly,
"verbatim storage makes erasure verifiable" loses its evidence and we say so.

E1 predicts a TIE on purpose. A tie there is the honest expected result and must not be dressed up.

## Test B — revert exactness

**Data**: the `update` scenarios' confirmed operation chains, which give an objective ground truth: after
undoing the last correction, the current value must equal chain step *n−1*. No judge needed.

**Procedure per arm**: ingest → undo the most recent correction by the arm's best path → read back the
current value → compare to the gold predecessor.

| metric | meaning |
|---|---|
| `revert_exact` | restored value == gold predecessor (0/1) |
| `predecessor_recoverable` | is the prior value still IN the store at all, without external knowledge? |
| `needs_external_knowledge` | does the undo require the caller to already know the old value? |
| `steps` / `llm_calls` | cost of the undo |

**Predictions**

| # | Prediction | SUPPORTED if | REFUTED if |
|---|---|---|---|
| R1 | mnemo `revert_exact` ≥ 0.8 | ≥ 0.8 | < 0.8 |
| R2 | naive keep-all needs external knowledge to undo | `needs_external_knowledge` = true | it can undo unaided |
| R3 | mem0's history DB makes its predecessor recoverable too | `predecessor_recoverable` = true for mem0 | false |

**R3 predicts a competitor CAN do it.** If mem0's history makes revert recoverable, the honest claim
shrinks to "ours is one call, deterministic, and does not need the old value" — a cost/determinism claim,
not a capability claim. That is the framing this session's pilot already forced on us.

---

# RESULT + PARKED (2026-07-20)

Ran `mnemo` and `naive` only; 18 scenario-units on 12 workers, 493 s.

| test | arm | result |
|---|---|---|
| A (erasure) | mnemo | leak 0.000 · paraphrase residue 0.000 · over-forget 0.000 · raw residue 0 files · deterministic 4/4 |
| A (erasure) | naive | identical on every metric |
| B (revert) | mnemo | revert_exact 3/9 (3 of 5 real chains) · predecessor recoverable 0/9 · needs external knowledge 3/9 |
| B (revert) | naive | revert_exact 0/9 · needs external knowledge **9/9** |

- **E1 SUPPORTED** (tie on literal leakage, as predicted against ourselves).
- **E3 REFUTED as written** — mnemo's raw residue is zero, but so is naive's, so the "and some arm > 0"
  clause never fired.
- **R1 REFUTED** — 0.6 on real chains against a 0.8 threshold. mnemo reverts in one call and without the
  caller knowing the old value, but only where the deterministic extractor keyed the chain; `recoverable
  0/9` says supersession never fired on the rest.
- **R2 SUPPORTED** — naive cannot undo unaided in 9 of 9.

**E2, E4 and R3 remain UNTESTED**: they compare against an LLM-extracting store, and the `mem0` arm was
not run (~4 h of LLM ingest). Until it is, the only honest statement is about mnemo versus a keyless
version of itself — not versus a competing product.

**PARKED at the owner's decision**, and correctly: this benchmark family has now produced three nulls and
one narrow, partial win, and no result that changes what we ship. Resuming costs one command —
`python erasure_revert_probe.py --arms mem0 --tests A` — and nothing else needs rebuilding.

## What this cannot show

It cannot show that erasure or revert make answers more accurate — the pilot says they do not. It is a
measurement of governance properties: removability, provability, reversibility, and their cost. Any
outward use requires the full standing gate (validate → storm → audit → verify) on top.
