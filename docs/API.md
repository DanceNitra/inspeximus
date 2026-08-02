<!-- moved out of README.md to keep the landing page readable; content unchanged -->

## Use it from the shell: the `inspeximus` CLI (1.12.4)

`pip install inspeximus` also gives you a `inspeximus` command — script the memory layer from the terminal, bash,
or cron, with no Python and no MCP server:

```bash
inspeximus remember "the deploy channel is BLUE-9" --key deploy-channel
inspeximus remember "the deploy channel is RED-2"  --key deploy-channel   # supersedes BLUE-9
inspeximus recall  "what is the deploy channel?"                          # -> RED-2 (current-truth)
inspeximus revert  deploy-channel                                         # roll back to BLUE-9
inspeximus list -n 10           # recent active memories
inspeximus forget --key deploy-channel        # or --id <id> / --contains <substr>
inspeximus stats               # store summary   ·   add --json to any command for scripting
```

It shares one store with the MCP server (`--path`, else `$INSPEXIMUS_PATH`, else `./inspeximus_memory.json`). Recall is
lexical by default; set `$INSPEXIMUS_EMBED_URL` (+ `$INSPEXIMUS_EMBED_MODEL`) to any OpenAI-compatible `/embeddings`
endpoint (e.g. local Ollama) for semantic recall. Zero dependencies.

## Multi-hop recall, reachable from MCP and the shell: `recall_iterative` / `recall_followup` (1.90.0)

One-shot top-k systematically misses the SECOND hop. When the answer needs a fact that is reachable only
*through* another one ("which city does the person who signed off on the marketing review work from"), the
record holding it is similar to the **bridge entity**, not to your question — so no amount of ranking brings
it back. Every static retrieval trick we measured against this failed or regressed: dense-neighbour
expansion, lexical bridges, PRF/Rocchio, multi-query RRF, and cross-encoder reranking (which *hurts* here by
construction — hop-2 evidence is not directly relevant to the question, so a relevance reranker demotes it).

The one thing that works is putting a **reading model** in the loop. `Inspeximus.recall_iterative()` has done
that since 0.x via an `ask_followup` callable — and a callable is unreachable from an MCP client or a shell,
which is where users and agents actually stand. So the same lever is now a **two-phase, client-driven loop**:

```bash
# phase 1 — round-1 hits plus the instruction to hand your model, and a continuation token
inspeximus recall-iterative "which city does the person who signed off on the marketing review work from"

# phase 2 — your model read round-1, named the bridge, and hands its queries back
inspeximus recall-iterative "...work from" --followup "Priya Raman"        # both phases, one call
```

Over MCP it is the pair `recall_iterative(query, k, max_followups)` → `recall_followup(query, followups,
prior_ids)`. **No LLM enters inspeximus.** The MCP client already *is* a model, so the reasoning stays the
caller's and the retrieval, dedup and merge stay ours: deterministic, zero-LLM, single file. `prior_ids` is
the entire continuation state and travels with the caller — there is no server-side session to grow, expire,
or serve to the wrong client, and phase 2 returns only the records you do not already hold.

**Bound (asserted, `tests/test_recall_iterative_surface.py`).** Phase 1: exactly **1** retrieval, at most `k`
records. Phase 2: at most `min(len(followups), max_followups)` retrievals with `max_followups` hard-capped at
**8**, and at most `k × max_followups` new records — worst case 8 retrievals / 400 records. Cost is a
function of `(k, max_followups)` and **not** of store size: measured at **n=330 and n=4,080 records**
(the probe's `--filler` is 250/4,000 on top of the fixture's own 80), the payload is byte-identical --
2,410 bytes for phase 1 and 2,964 for phase 2 at k=6, max_followups=3, at both sizes. Surplus follow-ups are dropped and *reported*
in `followups_dropped`, never silently truncated.

**What is measured, and at which operating point.** Two numbers circulate for this lever and they are two
different fixtures, not a disagreement. Both are multi-hop **full-evidence recall@50** on LoCoMo with a
**model** reader (which sees the question and round-1 only, never the gold) and local nomic-embed vectors, at
an equal retrieval budget of B=50:

| fixture | flat top-50 | iterative | ratio |
|---|---|---|---|
| **n=276, all 10 conversations (the full benchmark)** | 0.145 | **0.297** | **2.05×** |
| n=70, the 3 hardest conversations (a subset) | 0.057 | 0.186 | 3.3× |

The 3.3× is the harder subset and the flattering ratio; **2.05× on the full benchmark is the citable one.**
Neither is reproduced by this repository's test suite — both need the LoCoMo corpus (not shipped) plus a
model reader. What this repo *does* reproduce on every run is
`probes/recall_iterative_surface_multihop.py`, which measures the surface itself with a **mechanical,
gold-blind reader** (proper nouns in round-1 that are absent from the question) at lexical recall, k=6:

- **shipped synthetic bridge fixture: 20/20** questions where a single `recall(k=6)` misses the bridge record
  and the two-call sequence retrieves it. Control: **0** of the 20 were reachable by the single call (if any
  were, the fixture would be too easy to detect the lever and the probe fails). Single-hop control: no
  regression, and the caller's final record set is a superset of plain recall's.
- **LoCoMo, 409 multi-hop questions (≥2 gold evidence turns, category ≠ 5): 19/394 = 0.048.** This is a
  *weak* result and it is the expected one: a mechanical reader is exactly the zero-LLM bridging our lab
  already measured as null on this corpus. It is the honest floor for the surface, and it is the argument for
  the design — the gain lives in the reader, which is why the loop is handed to the caller's model rather
  than faked inside the library.

## Claude Code: deterministic auto-capture memory (1.10.0)

One command turns inspeximus into persistent memory for Claude Code, the same auto-capture the popular coding-memory
plugins do, but with **no LLM on the write path**, so a corrected fact supersedes the stale one and cannot come
back:

```bash
pip install inspeximus
python -m inspeximus.claude_code --install     # writes the hooks into ./.claude/settings.json
```

That is it. `PostToolUse` captures your edits and commands into a deterministic, keyed store; `UserPromptSubmit`
injects the current-state memory before Claude answers; `SessionStart` shows what the project already knows. The
store is a local JSON file at `.inspeximus/coding_memory.json` you can read, grep, or delete.

Why it differs from the LLM-summarizing coding memories: you change an API signature, rename a symbol, move a
file, and inspeximus keeps only the current state (keyed by file). Next session Claude recalls the new signature,
never the old one, and a stale line reappearing in a diff or paste cannot resurrect it (`echo_guard`). Same
convenience, but corrections stick, capture is reproducible, and a secret can be provably erased. Remove with
`python -m inspeximus.claude_code --uninstall`.

## Correction is a first-class operation (measured across systems)

Any memory layer can store a fact and retrieve it. The harder, less-benchmarked property is **integrity**:
when a fact is corrected, can the store *undo* the correction on command, and does restating a retired value
*resurrect* it? inspeximus treats correction as a first-class channel — `revert(key)`, `revert_now` /
`revert_intent`, `retract_lineage`, `echo_guard`, and the `route()` intent tagger — and we measured it against
mem0 and Graphiti in their **native configs** with a shared, **ground-truth-blind** judge (harness +
methodology: [`probes/INTEGRITY_BENCHMARK.md`](../probes/INTEGRITY_BENCHMARK.md)):

| value-obscuring revert · undo a correction from an unmarked "go back" (n=20) | success | 95% CI |
|---|---|---|
| **inspeximus** (route/revert) | **0.75** | [0.53, 0.89] |
| mem0 2.0.11 (native, gpt-4o-mini) | 0.20 | [0.08, 0.42] |
| Graphiti (native, live neo4j) | 0.00 | [0.00, 0.16] |

Only inspeximus exposes a channel to undo a correction on command; inspeximus's and mem0's CIs do not overlap, so the
capability gap survives at n=20. We lead with the cell we *don't* win: **echo-resurrection is a tie** — all
three defend against a restated stale value. This is a narrow, adversarial, command-driven cut, not a general
"inspeximus is better" claim; run it yourself or add your system.

**Run the receipts yourself** — all harnesses are public, point-at-your-own-store, and live in one place:
[`ramr`](https://github.com/DanceNitra/ramr) (RAMR — Retrieval-Augmented Memory Reliability, Zenodo-DOI'd).
It carries the contamination-resistant reliability probes (chain-fragility, fact-retention, echo-resistance
— every number traceable to a persisted source file, limitations stated first) **and**, under
[`ramr/integrity/`](https://github.com/DanceNitra/ramr/tree/main/integrity), the cross-system revert + echo
cells above (shared ground-truth-blind judge) plus a run-your-own
[erasure self-check](https://github.com/DanceNitra/ramr/blob/main/integrity/erasure_selfcheck.py).

### After the write: a read-path review trigger (1.9.2–1.9.7)

Supersession and revert handle correction at *write* time. But a store can also be *confidently wrong* — a
value settles, and later a contradicting observation arrives that write-time gating already accepted or
rejected. You do not want to silently trust every contradiction (an attacker or a stray transcript line can
mint them) nor silently ignore it (a real correction never gets seen). `observe()` is the mirror of a
write-time hold-for-review: it **reopens a settled record for steward review** on a *corroborated*
contradiction, and never on a lone one.

```python
m.remember("the region is Frankfurt", key="svc/region", object="Frankfurt")
m.remember("correction: it's now Ohio", key="svc/region", object="Ohio")

m.observe("someone says Berlin", key="svc/region", object="Berlin", support=["slack-8842"])  # 1 ground -> held
m.observe("Berlin again",        key="svc/region", object="Berlin", support=["slack-8842"])  # same ground -> echo
m.observe("Berlin, per audit",   key="svc/region", object="Berlin", support=["audit-Q3"])    # 2nd ground -> REOPEN

m.reopened()                       # the review queue; recall() still returns Ohio meanwhile
m.recall("region", k=1)[0]         # ... but the hit now carries under_review=True + review_reason + review_prior
m.resolve_reopened(id, "keep_current")   # steward: false alarm  (or "reaffirm_prior" to restore via revert)
```

The review signal reaches the **agent**, not just the steward: while a record is reopened, `recall()` hits for
it carry `under_review: True`, `review_reason`, and `review_prior` (the value the contradiction points back
to), so a consumer can branch — defer, ask, or hedge — instead of acting on a contested value with full
confidence. The fields disappear once the steward resolves; a record that was never reopened has none.

Corroboration counts **distinct novel grounds** in `support`, so replaying one ground is an echo, not a vote.
`observe()` only *flags* — it never supersedes; the steward decides. Distinguishing a legitimate contradiction
from an injected one is an **authority** call, not a content call: `Inspeximus(support_authorities=[...])` requires
grounds to be **Ed25519-signed by an allowlisted key** (self-minted grounds then count zero, and a
`{pubkey: class}` mapping counts distinct provenance *classes*, so two keys sharing one upstream source count
once). Honest limit, credited: this is exogenous-trust-root / anti-Sybil (Douceur 2002; DKIM / W3C VC — a
signature attests *source*, not *truth*); it makes the steward's independence judgement *enforceable*, it does
not certify independence. Runnable: [`examples/05_review_trigger.py`](../examples/05_review_trigger.py).

## Governance, erasure & audit

inspeximus ships tamper-evident governance primitives — built by auditing inspeximus against a governance-evidence
rubric, finding gaps, and closing them in the open. These are engineering, not novelty: each applies a
well-known primitive, credited below.

- **`anchor()` + `verify_consistency()`** — a Certificate-Transparency-style external anchor (a signed tree
  head). The write/tombstone receipts are hash-chained, but an operator who *holds the receipt key* can rewrite
  and re-chain the whole history so it still verifies internally. `anchor()` emits a compact commitment you
  publish/witness out of band; `verify_consistency(prior_anchor)` then catches a key-holder rewrite or rollback.
  *Prior art: RFC 6962 (Laurie-Langley-Kasper 2013); Crosby-Wallach 2009; Schneier-Kelsey 1999.*
- **`forget_subject(subject, basis=, authorized_by=, authorization=)`** — right-to-erasure across derived
  lineage, with an erasure tombstone that binds the act to an **authenticated principal** (Ed25519 signature
  over the request, via `sign_erasure()`) and records the **decision basis** — both inside the tamper-evident
  hash. An auditor verifies *who* authorized the deletion and *on what basis*, not a free-text id.
- **Cross-store erasure, first-class (1.8.0)** — a copy the app embedded into its *own* vector index survives
  every memory store's native delete (8/8 in our measured cell, inspeximus included). The fix:
  `register_erasure_target(target)` your app-side stores (vector index, caches, logs — the two-method
  `ErasureTarget` protocol), and `forget_subject()` cascades the erasure through every one and returns a
  hash-chained **manifest** — honest by construction: `complete` only if *every* store (inspeximus self-checked
  first) verified the value no longer recoverable, leaking stores NAMED. Measured: unwired 8/8 leak → wired
  0/8 with verifying chains; a broken wiring cannot produce a clean receipt (0/8 falsely complete).
  `DeletionManifest` (`inspeximus.deletion_manifest`) remains usable standalone.
- **Identity-confidence gate on supersession (1.9.0)** — a keyed correction supersedes on `(entity, field)`,
  which is only right if the identity is right. When identity is resolved fuzzily, `remember(..., identity_confidence=c)`
  gates the write: `c` below `fork_below` (0.7) forks a **candidate** instead of overwriting the authoritative
  value, and `candidates()` / `promote_candidate()` / `discard_candidate()` are the steward path. Measured: under
  noisy identity resolution an ungated auto-commit corrupts the ledger 13.5% of the time; the gate cuts it to 1.0%
  (93%) at the cost of a review queue. *Not a new idea, credited: record linkage's clerical-review zone (Fellegi &
  Sunter 1969) and MDM match-merge stewardship, ported to an agent-memory write path where nobody gates it.*
- **`ErasureAuditor`** (`inspeximus.erasure_auditor`) — after your app runs its deletion, adversarially re-attempts
  **recovery** of the subject's values from each store (verbatim scan for text/caches; **NN-inversion** for a
  vector index whose embeddings may survive). Answers "is the content still *reconstructible*?" — the check DSAR
  tooling skips — not just "was the row deleted?". *A retained embedding reconstructs the content: Morris et al.,
  "Text Embeddings Reveal (Almost) As Much As Text", EMNLP 2023; Ghost Vectors, arXiv 2606.18497.*

Honest scope: these attest and audit the erasure ACT and residual recoverability across REGISTERED stores; they
do not prove physical destruction, do not cover unregistered stores or backups, and the vector-recovery check is
a lower bound on embedding inversion. When a store leaks, the fix is hard-delete + reindex or crypto-shredding
(destroy the key, not the row — EDPB 05/2019; NIST SP 800-88).

## Install

```bash
# single file, zero dependencies
curl -O https://raw.githubusercontent.com/DanceNitra/inspeximus/main/inspeximus/inspeximus.py
```

## Use

```python
from inspeximus import Inspeximus

m = Inspeximus("memory.json")                       # persists to JSON; or Inspeximus("memory.json", embed=my_model)

m.remember("Pre-trend tests catch only ~31% of fatal DiD bias.", tags=["causal"], value=3, mtype="semantic")
m.recall("difference in differences", k=5)     # relevance × value, decayed by the memory's per-type half-life
m.consolidate(keep=200)                        # the "dream" pass: hubs, dedup, STATE-TOGGLE, keep-budget
m.consolidate_clusters(threshold=15)           # cluster-TRIGGERED: consolidate only a topic that's grown dense
m.contradictions()                             # flag incompatible memories for REVIEW (never deletes)
m.value_by_cohort()                            # value reported per tag/time-block, not per memory
```

Bring any text→vector function as `embed=` for semantic recall; with none, `inspeximus` falls back to a
forgiving lexical match so it **runs anywhere, today**. Once the store grows past the threshold, recall
**fuses lexical (BM25) + semantic with Reciprocal Rank Fusion**. On high-lexical-overlap agent memory
(e.g. LoCoMo) the fused hybrid *measurably* beats either channel alone (recall@20 **+0.06** over the best
single channel, 9/10 conversations, conversation-level bootstrap CI excludes 0; receipt:
[`probes/locomo_retrieval_map.py`](../probes/locomo_retrieval_map.py)); where the embedder already dominates
(paraphrase-heavy corpora, see benchmarks) fusion adds little. `mode='auto'` fuses; `mode='lexical'` /
`'semantic'` force a single channel.

### Poison-resistant recall: `recall(..., influence_only=True)` (0.4.0)

Retrieval-time / embedding-geometry defenses do **not** stop memory poisoning in general. We red-teamed
`inspeximus` with a real AgentPoison-style single-instance attack (Chen et al., NeurIPS 2024; PoisonedRAG, Zou
et al., USENIX Security 2025): a **plain-English trigger sentence** in one poisoned memory hijacks raw
top-1 retrieval **88–100%**, it is **scale-invariant** (60→10 000 memories), it **evades a perplexity
filter** (natural triggers have natural perplexity), and coherence/outlier retrieval defenses **don't
generalize across encoders**. The layer that *does* generalize is **influence-gating by corroboration**:
`recall(..., influence_only=True)` returns only memories that earned the same bar as episodic→semantic
graduation (a credited good outcome, or ≥2 distinct-source links). Retrieve freely for context; gate what
drives an *action*. Measured: single-instance poison rank-1 hijack → **0%** on MiniLM/BGE/Contriever and
at every scale, because an injected poison never earns corroboration while real memories earn it through
use — and it generalizes precisely because it lives in **provenance metadata, not embedding geometry**.
Honest cost (a calibration tradeoff): a rare-but-true memory that hasn't earned corroboration is filtered
too (recall 1.00 corroborated vs 0.08 uncorroborated), so this is for **adversarial / untrusted-ingestion**
use. It raises attacker cost (defeating it needs ≥3 coordinated records with ≥2 forged independent
provenances), it does not make poisoning impossible. Receipts: [`probes/agentpoison_influence_gate.py`](../probes/agentpoison_influence_gate.py),
[`probes/agentpoison_influence_gate_validation.py`](../probes/agentpoison_influence_gate_validation.py).

### Know before you gate: `influence_gate_report()` (0.4.3)

The influence gate is not free, and its cost is **density-dependent** — so check it before you rely on it.
`influence_gate_report()` returns the gate's **live cost on your store** (`would_block_frac` = the fraction of
active memories it would filter, plus the corroboration breakdown and an `advice` string). Why it matters, and
both measured on [`probes/oracle_separation_density.py`](../probes/oracle_separation_density.py) (controlled corpus,
real embeddings): **(1) density = affordability** — the fraction of *legitimate* high-stakes recalls the gate
blocks falls from **~51% when each memory is used ~once (sparse)** to **~6% when each is used ~8× (dense)**,
because a legit memory only earns standing through repeated successful use; in a thin store the gate can't tell a
poison from a newcomer and filters most legit recalls (the classic *cheap-pseudonyms / whitewashing* tax, Friedman
& Resnick 2001). **(2) The gate rides entirely on an un-self-gradable oracle** — a MINJA-style self-graded outcome
(arXiv:2503.03704) collapses it at *every* density, even inverting it (blocking legit more than poison), so never
let recalled content drive its own `credit()`; issue outcomes from the application, on real resolved work.

### Retroactive standing forfeiture: `slash(ids)` (0.4.4)

`credit()` is append-only, so a **patient "sleeper"** that banks good outcomes across many benign memories under
one source survives a single bad one (good=50, bad=1 stays trusted) — the residual attack against
outcome-standing is a slow, in-domain accumulator, not a one-shot. `slash(ids, scope='source')` is the
accountability lever: when a memory is **caught** driving a bad outcome, it forfeits the **entire accrued
standing of that source** — every active memory sharing its canonical source goes net-negative and loses any
episodic→semantic graduation, so the source immediately fails the influence gate. The accrued reputation *is* the
bond; one catch turns the attacker's patience into its largest exposed stake. Unlike `forget()` it deletes
nothing (records stay recallable for context and audit via `meta['slashed']`); unlike `credit(bad)` it can't be
out-banked. This makes cost-of-corruption scale with accrued-standing × detectability (the classic
expected-penalty result — penalty must beat gain / P(caught)), the lever that bites a time-rich attacker a
per-action cap only lets him amortize. Receipts: [`probes/triad_attacker_split.py`](../probes/triad_attacker_split.py),
[`probes/reversibility_gate_frontier.py`](../probes/reversibility_gate_frontier.py).

Because detection is imperfect — a self-graded / MINJA-style oracle can be *tricked* into flagging a legitimate
source, so `slash()` can be **weaponised** to knock out a rival's memory — the forfeiture is reversible:
`restore(ids, scope='source')` recovers the **exact** pre-slash standing (saved in `meta['pre_slash']`), or a
clean slate if none was recorded. The penalty is heavy, so the appeal is cheap — otherwise `slash()` itself
becomes the attack surface.

**Provenance that rides through transformation: `remember(..., derived_from=[ids])` (0.4.6).** All of the above —
`slash`, a per-source influence budget, any source-level accountability — is silently un-countable the moment a
memory is *transformed*: an app-side **summary** of five source-memories is a fresh record with no source, so
`slash(source)` can't reach it and a cumulative cap can't attribute its slices. `inspeximus`'s own consolidation never
loses provenance (it links, never merges text), but LLM summarization/rewrite does. `remember(text,
derived_from=[parent_ids])` closes that hole: the new record **inherits the union of its parents' canonical
sources** as a `taint` (transitively — a summary-of-a-summary still carries the origin), and `slash(scope='source')`
matches on *own source OR inherited taint*, so forfeiting a source also burns every derived summary it fed. The
honest boundary: the app has to *declare* the derivation at the transformation step — `inspeximus` can carry the taint
through, but it can't recover provenance an opaque summary threw away. This is the substrate everything else is
deterrence math on top of. Receipt: [`probes/triad_attacker_split.py`](../probes/triad_attacker_split.py).

**The cumulative trigger the slash needs — as a case-raiser, not an auto-executioner: `monitor()` (0.4.7,
hardened 0.4.8).** Retroactive `slash()` *cannot* fire per-slice against a slow salami attacker: per-slice
`P(detected) ≈ 0`, and the deterrence bond scales with `1/P(detected)`, so the penalty blows up on exactly the
attack you're worried about. So the trigger has to be **cumulative**. `monitor(ids, outcome)` is a drop-in for
`credit()` that runs a one-sided **CUSUM-type** detector on each attributed source's bad-rate above a benign
reference `k`; on breach of `h` it **raises a case** for that source, with attribution carried through the
`derived_from` taint so slices later summarized still accumulate against their origin. `h` sets the false-alarm
rate (`ARL ~ exp(h)`) and the detection delay `~ h/(rate−k)` — the Lorden floor no gate shrinks. State persists
to a side file so a drip can't reset the detector across sessions.

Three honest limits (from a full adversarial review — this does **not** "solve" poisoning):
(1) it's **CUSUM-*type*** (Gaussian-mean-shift `x−k`), not the exactly-optimal Bernoulli log-likelihood form;
(2) **`k` is a tolerated-rate *price*, not a wall** — an attacker holding its bad-rate at/below `k` drifts the
statistic to zero and is *provably* undetectable, so this catches the careless poisoner while a patient one nets
a bounded `k × exposure` residual (the latency floor moved to `k`, not closed); lowering `k` just raises false
alarms on honest sources;
(3) **don't auto-fire the irreversible penalty** — `auto_slash` **defaults OFF**. Seventy years of automated
penalties (SPC → fraud → content moderation, e.g. Knight Capital, no-fly lists) converged on automatic
*detection* + a human-reviewable *reversible* penalty, because a drifting base-rate guarantees false alarms, a
single false positive nukes a whole tainted downstream tree (guilt-by-linkage), and if outcomes are
attacker-influenceable (MINJA) the auto-trigger becomes a *framing weapon* (feed bad outcomes attributed to a
rival → auto-slash the rival; cf. RepTrap / bad-mouthing). Recommended: on a case, cap/freeze the source's
forward influence (reversible) and queue a **human** review; confirm the `slash()` by hand; keep `restore()` one
call away. `auto_slash=True` is an explicit opt-in for a high-integrity, un-self-gradable oracle only. Receipts:
[`probes/triad_attacker_split.py`](../probes/triad_attacker_split.py),
[`probes/reversibility_gate_frontier.py`](../probes/reversibility_gate_frontier.py).

**Bound the one residual the rate-detector can't: `spend_irreversible()` (0.4.9).** `monitor`'s `k` is a
tolerated *rate*, so the **strictly-below-`k`** attacker gives the CUSUM negative drift → no detection power above
the baseline false-alarm rate, netting `k × exposure` — but exposure is **per-window**, so over an unbounded
horizon its *total* irreversible blast grows without bound. Capping a rate never bounds an integral.
`spend_irreversible(ids, amount, budget)` caps the integral directly: a per-source **lifetime budget on
irreversible influence**, charged through the `derived_from` taint (the tightest contributing source binds — an
action tainted by an exhausted source is denied). Meter it **only on the irreversible tail**: reversible influence
is never charged, so genuinely-good sources keep *unbounded* reversible influence and only their irreversible tail
is finite. It is **not** refunded by `restore()` — an irreversible action can't be un-done, so its integral is
permanent (raise a reviewed source's ceiling by hand, not by a cheap auto-refund). The honest read: you don't
remove the residual, you **price it (`k`), bound it (this lifetime budget), and put a reversible human gate
(`slash`/`restore`) on the irreversible tail** — but this is **not a closed decomposition**, because all three
lean on a fourth, unbounded axis: **sound attribution** (correctly assigning an action to its source and labeling
its irreversibility). Measured: [`probes/lifetime_budget_bound.py`](../probes/lifetime_budget_bound.py) shows the
same undetectable below-`k` attacker's total blast go from unbounded (50→800 over the horizon) to **capped at
`budget` regardless of horizon**, while a good source keeps 10 000 free reversible uses.

Honest limits (this **relocates** the residual, it does not close it): (1) the budget is **per-source, so it
doesn't escape Sybil** — a whitewashing attacker who mints fresh cheap identities gets a fresh `budget` each, so
system-wide blast is `budget × (#identities)`; the identity count is bounded by a *different* axis (a
standing-independent capability ceiling / an identity cost; Douceur 2002, Friedman–Resnick 2001), so this bounds a
*known* source, not an adversary who can mint new ones. (2) **Refund-free forfeiture is a griefing vector** — get
bad irreversible actions attributed or taint-linked to a rival's source and you permanently exhaust its budget
(RepTrap on the budget axis), the same weapon the `auto_slash`-off default avoids, so tune `budget` and the
reversible/irreversible split conservatively. (3) It assumes the app can label an action's **irreversibility and
blast `amount`** at spend time; `amount` is caller-supplied and one action of size=`budget` exhausts a source in
one shot (so the guarantee is "≤ `budget` per source", `k` doesn't enter it), and if the classifier is
attacker-influenceable the meter leaks. Prior art (textbook; the shipped plumbing on an agent-memory core is
what's new): a total-budget-on-cumulative-cost is the differential-privacy **privacy budget** (a total `ε` caps
cumulative leakage across queries under composition; Dwork & Roth 2014), an SRE **error budget**, a **VaR / loss
limit**, and Sagas' **compensable-vs-non-compensable** transaction split (Garcia-Molina & Salem 1987) — "cap the
integral, not the rate."

**Harden the floor the other three stand on: `verify_attribution()` (0.5.0).** `k`, the influence budget, the
influence gate and `slash` are all keyed on a memory's canonical **source id**. So attribution is not a fourth
axis — it is the **floor** the other three stand on, and the only one that isn't self-certifying: a single
post-hoc **relabel** (rewrite a record's source, or strip a summary's inherited `derived_from` taint to launder a
poisoned origin) doesn't *degrade* the other three, it **voids all of them at once, silently**, with no inner
layer to appeal to. So bind attribution into the tamper-evident write-receipt chain (enable `receipts=True` /
`receipt_key=…`): the receipt now commits to each write's canonical sources, and `verify_attribution()` reports any
active memory whose *current* sources no longer match what was committed. **A relabel becomes loud, not silent.**
Measured: [`probes/attribution_floor.py`](../probes/attribution_floor.py) — a source relabel and a taint-strip are
both **detected**; a legitimate `slash` does **not** false-alarm; editing a past receipt breaks the hash chain.

**Two honest limits — read this as tamper-EVIDENT, not tamper-PROOF.** (1) **Tamper-evidence ≠ correctness.** A
source that was *wrong at write time* — an attacker who controls the labeling channel (MINJA-style) and asserts a
benign source — is committed faithfully and `verify_attribution()` **cannot** tell it was wrong. That is the
genuinely-open **oracle problem**, untouched. (2) **The chain is only tamper-evident if it is signed (offline key)
or externally anchored.** Unsigned — the default — an attacker who can silently relabel `rec["source"]` can equally
recompute the whole sidecar receipt chain with the new sources and pass the check, so bare `verify_attribution()`
only catches a relabel by an actor who can edit the store but **not** the `.receipts` sidecar (e.g. an out-of-band
DB edit). For the "loud" property to hold against a store-capable attacker you must pass `receipt_key=…` (Ed25519)
with the key out of reach, or anchor the chain head externally. The crypto is textbook — hash-chains (Haber &
Stornetta 1991), tamper-evident logs (Schneier & Kelsey 1998), the same design as our
[agent-receipts](https://github.com/DanceNitra/agora) work; the only new bit is the systems observation that a
source-keyed defense stack has one silent single-point-of-failure (relabel), and committing attribution converts
that failure from silent to loud. The correctness slice stays the small, sharp, unshipped problem.

**Make corroboration count a distinct *verified key*, not a distinct *string*: `strict_corroboration` +
`attestation` (0.5.2).** The corroboration gate (episodic→semantic graduation and `recall(influence_only=True)`)
requires "≥2 distinct sources". By default a source is a canonical **string** — entity-resolution collapses honest
sybil variants (`Wikipedia`/`wikipedia.org`/a URL → one), but an attacker who owns the labeling channel can still
supply two *unrelated* source strings it controls and manufacture "independent" corroboration. Set
`m.strict_corroboration = True` and a corroborating link counts only if it carries a **verified key**: a source
signs the claims it authored (`sig = inspeximus.attest(text, source_sk, source_doc)`; write with
`remember(..., attestation=(source_pubkey, sig))`), the signature is verified over the *same claim + canonical
source* at write time (a forged or replayed attestation is **rejected**, not silently dropped), and the record
carries `attested_key`. Independence is then measured by distinct **Ed25519 public keys an attacker cannot
forge** — N sybil variants of one origin collapse to one witness unless the attacker holds N distinct keys (a
costly identity; Douceur 2002). This is the **exogenous trust root** the attribution problem bottoms out on:
"can I trust the label" becomes "can I trust the root", i.e. the identity axis. Measured:
[`probes/attribution_verified_key.py`](../probes/attribution_verified_key.py) — a two-string spoof that passes the
default gate is **rejected** under strict; two distinct signed witnesses pass; the same key used twice collapses
to one; forged and claim-replayed attestations are refused at write time. **Honest limit:** this buys unforgeable
**independence**, not **correctness** — an attested source can still sign a *false* claim (a wrong-at-write-time /
MINJA attack survives a signature); a signature proves **authorship** (so a caught liar is a non-repudiable,
revocable key), not truth. Textbook root-of-trust (PKI/TCB; costly-identity sybil defense, Douceur 2002); the new
bit is binding the independence rail of a memory's corroboration gate to that root. Opt-in, default OFF → identical
legacy behavior.

### Evidence-grade ratchet: `grade()` + `ratify()` (0.6.0)

**A claim's status is something it *earns*, not a label the writer self-assigns.** Two axes ride on the existing
substrate and can only move UP on an event from a party *other than the writer*: a **confidence grade**
(`claimed → corroborated → verified → settled`) and a separate **novelty** flag (`novel` only when an external
prior-art search comes back **empty**). `remember()` cannot set either; `grade(id)` is a pure function of
ratifications + corroboration + `credit()` outcomes, so there is nothing to spoof. `ratify(id, kind, by_key, lens=)`
records an external event (`independent_witness` / `reproduction` / `prior_art_empty` / `audit`); a ratifier whose
`by_key` is the claim's own author is **rejected**, and a duplicate `(by_key, kind, lens)` does not stack, so a
correlated or repeat auditor adds nothing. The top grade requires a reproduction **plus two distinct lenses** — the
correlated-auditor guard. Receipt: [`probes/evidence_grade_ratchet.py`](../probes/evidence_grade_ratchet.py) shows (1)
the ratchet holds (a generator upgrading its own claim does nothing), (2) forge-cost — one identity is stuck at
`claimed`, every rung up needs another distinct key (Douceur; pair with `attestation` to make those keys
unforgeable), and (3) a replay of our own 32 adversarially-audited posts through the ratchet reproduces the audit's
headline **for free**: **0/32 reach `novel`** (none had an empty prior-art search) and the 11 substantive-wrong ones
stay at `claimed` while the 21 reproduced ones reach `verified`. Over-labeling isn't caught after the fact — it
becomes structurally un-assertable. **Honest limit:** this bounds *who* may upgrade a label to *distinct
identities*, not truth — a wrong claim with real reproductions still climbs; and `by_key` is spoofable unless paired
with `attestation` (then each identity is Douceur-costly). Evidence-grade / staged-promotion is textbook
(argumentation & KR justification levels, staged review); the new bit is a runnable memory primitive that makes the
grade externally-ratcheted by construction. Opt-in; default behavior unchanged.

### Independent in time, not just in source: the `temporal_gate` (0.6.5)

A corroborating link proves independence of *source*, never of *timing* — genuinely independent sources rarely
write within seconds of each other, but a coordinated forgery writes its witnesses in a burst. `temporal_gate`
(opt-in, `m.temporal_gate = 60.0` seconds; default `None` → **zero behavior change**, suggested by **hannune** on
r/RAG) collapses corroborating links that **co-arrive** (timestamps within the window of each other) to **one
anchor** before the `≥2-distinct-source` count — exactly as source canonicalisation collapses `Wikipedia` /
`wikipedia.org` to one, but on **time**. Measured ([`probes/temporal_gate_demo.py`](../probes/temporal_gate_demo.py)):
a genuine recovery whose witnesses are spread out in time is untouched, a **co-arrival burst is blocked**, and — the
**honest limit** — a **patient** attacker who spaces the forged writes beyond the window still passes (a timing
signal can't catch patience; the sleeper again). It's a soft, **decorrelated** layer — timing is orthogonal to both
source-count and content-coherence — so its value is exactly the decorrelation the attacker leaves you; it composes
with `coherence_gate`. Textbook coordinated-burst / Sybil-timing detection, shipped as one honest gate, not a wall.

### On-topic corroboration: the `coherence_gate` (0.6.4)

A corroborating link proves *independence of source*, never that the witness is *about the claim* — so a forged
2-source poison whose "witnesses" are off-topic filler still clears the `≥2-distinct-source` bar. `coherence_gate`
(opt-in, `m.coherence_gate = 0.18`; default `None` → **zero behavior change**) makes a link count toward that bar
only if its witness is **coherent with the claim** — embedder cosine if you passed an `embed` fn, else lexical
token-Jaccard — above the threshold. Measured ([`probes/coherence_gate_demo.py`](../probes/coherence_gate_demo.py)):
a genuine on-topic recovery is untouched (no false-withhold), a **lazy off-topic forgery is blocked**, and — the
**honest limit** — a *sophisticated* forgery with **on-topic** witnesses still passes. So this **raises the forger's
bar** from "2 distinct source strings" to "2 distinct source strings + on-topic witness text"; it does **not** close
the residual. This is textbook **adaptive-attack / common-mode** territory (Carlini & Wagner 2017; Knight & Leveson
1986; PoisonedRAG) — a defense-in-depth layer, not a wall. Ship it as one more gate whose value is exactly the
decorrelation the attacker leaves you, not a claimed defense.

### Provenance that survives the LLM rewrite: auto-stamped lineage (0.6.3)

The retraction in 0.6.2 rides `derived_from` taint — but an app-side summarize/consolidate step (an untrusted LLM
rewrite) usually **drops** that link, orphaning the summary so a retraction can't reach it. 0.6.3 closes that at the
**transformation boundary**, and — because we ran the claim through a full multi-lens review + citation check first
— it does so **honestly**. A source-string default-deny (demote any write with no source) is textbook **Biba (1977)**:
it authenticates *origin, not truth*, a caller can forge a source, and it doesn't touch poison that carries valid
provenance (MINJA, [arXiv:2503.03704](https://arxiv.org/abs/2503.03704), NeurIPS 2025) or attacks retrieval geometry
(AgentPoison, NeurIPS 2024). The form that actually **measures** is **store-carried lineage**: `recall()` records what
it surfaced, and `remember(..., derived=True)` with no explicit parent **auto-stamps `derived_from` from that recall**,
so a summary written right after a recall inherits its ancestors' taint **by the store** — the untrusted LLM only
supplies the text and never holds the switch. Measured ([`probes/autostamp_lineage.py`](../probes/autostamp_lineage.py)):
the laundered summary inherits the root's taint, is not an orphan, and **falls with a `slash()` on the root** (reversible);
a derived write with **no** preceding recall stays an **orphan** (fail-closed). This lines up with **MemLineage**
([arXiv:2605.14421](https://arxiv.org/abs/2605.14421): signature-only 6/6 attacks → 0/6 once ancestor lineage propagates).
Also ships `remember(derived=True)` (declare a transformation output) and a store-level `strict_provenance` flag
(standing requires a shown source **or** resolvable parents). **Honest scope:** this is a Biba-style integrity /
taint-tracking *application* (not novel) that closes the **laundered-summary** path; it does **not** stop
provenance-carrying poison — that needs content moderation + trust-decay retrieval. All opt-in; `derived=False` default
→ zero behavior change. Credit: jacksonxly (transformation-boundary framing) + marintkael.

### A landed retraction wins on every path: `slash()` → 0 load-bearing (0.6.2)

Corroboration can only raise *confidence*, never confer *truth* — so an authenticated-but-false claim **will**
be admitted. The property that actually holds is temporal: *nothing false stays load-bearing past the moment a
correctness signal lands* (bounded blast radius + reversible propagation). `slash(ids, scope='source')` lands
that signal — when a bad outcome is finally attributed to a source, it forfeits standing and, via `derived_from`
taint, propagates through summaries/consolidations to the whole transitive derived subtree; `restore()` is exact,
so a mistaken or weaponized retraction is undoable. **0.6.2 closes the last hole:** a caught record that
independently cleared the ≥2-distinct-source bar used to *survive* (slash books accountability but doesn't strip
corroboration links), so `_is_corroborated()` — the recall influence gate **and** the graduation bar — now
returns `False` for any slash'd record on **every** path (credit, graduation, and distinct-link corroboration
alike). Measured: one slash revokes 5/5 provenance-reached descendants (incl. a depth-2 rollup and the
link-corroborated one), restore recovers 5/5; the only survivor is a lineage-stripped orphan (preserve
`derived_from` through summarization — a usage requirement, not a store bug). Runnable receipt:
[`inspeximus/probes/retraction_propagation.py`](https://github.com/DanceNitra/agora/blob/main/inspeximus/probes/retraction_propagation.py).
Credit: jacksonxly (the invariant) + marintkael (authenticated-but-false). Reversible; default behavior unchanged.

### Convergence-backed status: `convergence_report()` + `recall(with_status=)` (0.6.1)

**Corroboration measures independence of *origin*, never *correctness*** — so genuinely independent sources can
converge on a *false* claim ("authenticated-but-false") and nothing in the record content catches it. This upgrade
(prompted by a sharp r/RAG exchange) makes the memory layer carry that honestly instead of promoting convergence to
"true". `convergence_report(id)` returns a **`convergence-backed`** status (sources agree, *not* adjudicated true)
vs **`adjudicated`** (an out-of-band check ratified it via `ratify(kind='reproduction'/'audit')` from a *different*
identity — a different failure mode); it flags **`low_source_diversity`** (≥2 corroborating links resolving to ≤1
distinct origin — uniform agreement from few origins should *raise* suspicion, since errors correlate when sources
share a substrate); and it reports a **`lineage_grade`** capping a derived memory at its weakest parent (trust taint
propagates, not just source taint). `recall(with_status=True)` carries the status at the point of use. The mechanism
is textbook — redundancy recovers a wrong consensus only to the degree failure modes are independent (Knight &
Leveson 1986; Condorcet/Ladha 1992; Campbell & Fiske 1959); the new bit is a runnable memory primitive that names
it. Opt-in; default behavior unchanged.

### Soft metadata filter: `recall(prefer=..., prefer_trust=...)` (0.4.1)

A hard metadata filter (`where={"speaker": x}`) deletes non-matching memories — great when the filter is
right, but when your extractor guesses the wrong value it **hard-deletes the answer**. The soft version
only *boosts* matching memories, weighted by how much you trust the cue this call, and leaves everything
else rankable: `recall(q, prefer={"speaker": x}, prefer_trust=t)`, `t∈[0,1]` (0 = no filter, 1 = strong
preference). Pass a **low** `prefer_trust` when the match is weak/ambiguous so the filter backs off toward
plain recall. The point is to weight by the **a-priori reliability of the extraction** (e.g. alias-match
strength: exact-name hit → ~1.0, no-name/ambiguous guess → ~0.0), *not* by the extractor model's own
self-reported confidence (which is corrupted exactly when it's wrong). MEASURED end-to-end through
`recall()` on LoCoMo (receipt: [`probes/locomo_soft_prefer_filter.py`](../probes/locomo_soft_prefer_filter.py)):
with an extractor that is reliable on exact-name questions (5% wrong) but guesses on ambiguous ones (67%
wrong), alias-strength-weighted `prefer` scores **recall@20 0.718 (+0.144 over no filter, best of all,
10/10 conversations)** and — on the subset where the extractor picked the wrong speaker — recovers to
**0.315 vs the hard filter's 0.110** (which craters by deleting the right answer). Soft `prefer` gives the
filter's upside without the hard filter's downside. Reversible: `prefer=None` = legacy recall.

### Compose several soft cues: multi-dimension `prefer` (0.4.2)

Pass `prefer` as a **list** of `(cond, trust)` tuples (or `{"cond":…, "trust":…}` dicts) to weight more
than one cue at once — e.g. a resolved time window *and* a named speaker:
`recall(q, prefer=[({"year": 2023}, 0.9), ({"speaker": x}, 0.7)])`. Matching cues **compose as a product**
of neutral-at-1.0 factors, so a memory matching both is boosted more than one matching a single cue, and a
non-matching cue is inert. Cap the total with `prefer_max_boost` (a ceiling on the product, like
Elasticsearch `function_score`'s `max_boost`). A single `dict` + scalar `prefer_trust` is the one-dimension
case, unchanged. MEASURED (receipt: [`probes/locomo_composed_soft_filters.py`](../probes/locomo_composed_soft_filters.py),
self-check 0/1568 vs the shipped path): on LoCoMo questions carrying two independent cues (n=183), the
product composition scores **recall@20 0.865 vs 0.755 for the best single cue (+0.110, bootstrap CI excludes
0)**, while a summed boost *capped at one dimension's trust* crowds out (−0.053 — the cap flattens the joint
evidence, the classic "combine outside the saturating form" failure, BM25F/Robertson et al. CIKM 2004). So:
compose as a **product**, and if you cap, cap the product — the same choice production search settled on
(Elasticsearch defaults `score_mode=multiply`). Honest scope: one benchmark, one embedder, near-orthogonal
cues. Reversible: a single dict / `None` behaves exactly as before.

**Compose only cues you trust** (receipt: [`probes/locomo_correlated_cue_composition.py`](../probes/locomo_correlated_cue_composition.py)).
A product inherits the product-of-experts *veto* (Hinton 2002): a near-zero factor vetoes, so a target that
misses *either* cue collapses far below an additive sum or the trusted cue alone — measured, on the subset
where the second cue is wrong-for-the-query, product recall@20 **0.10 vs sum 0.52 vs one-cue 0.70**. So an
unreliable second cue hurts a *product* more than a *sum* (and can do worse than not composing at all). The
fix is the per-cue `trust` you already pass: down-weighting an untrusted cue restores the product toward the
sum. Interestingly this is **not** a correlation effect — the gap is largest when the cues are *orthogonal*
and *shrinks* as they correlate (a redundant copy just can't miss when the real cue hits). Rule of thumb:
compose a second cue **only when it is independently reliable for the query**, and weight it by that reliability.

### Continuous state cue: `recall(near=...)` (0.6.6)
`prefer` matches CATEGORICAL meta (`theme == "identity"`). For a **continuous** state vector — a TAT-style
5-D chunk, or any embedding-like feature stored in meta — you want nearest-neighbour in the numeric subspace,
not exact match. `recall(query, k, near={"target": {"theme": 0.29, "role": 0.33, ...}, "trust": 0.7, "half": 0.2})`
boosts each record by `1 + trust*(coverage)*exp(-distance/half)` over the target's numeric dims (per-dim-
normalised, coverage-weighted, NaN/bool-guarded). Soft (never hard-deletes; missing dims → neutral), composes
with text sim and `prefer`, `near=None` = byte-identical legacy. MEASURED on a real TAT 5-D state trace:
regime-relevance precision@5 **0.984 (near) vs 0.758 (plain text)**. It re-ranks the recall pool — not a
vector index. Receipt: `inspeximus/probes/continuous_chunk_recall_probe.py`.

### Make the not-asserting visible: `recall(with_warrant=True)` + `spend_irreversible(provenance_lo=...)` (0.6.6)
A silent low score for "no independent channel" decays into *"unverified but present"* — a downstream consumer
reads quiet as a soft yes and you are back to consensus-over-poison with extra steps. So the abstention is made
a first-class, branchable STATE: `recall(with_warrant=True)` tags each hit `earned` / `corroborated` /
`unwarranted`, and the consumer rule is *never let `unwarranted` drive a consequential decision*. Complementing
it, `spend_irreversible(ids, amount, budget, provenance_lo=0.15)` caps a source with **no corroborated
contributing record** at the small `provenance_lo` instead of the full budget — a low-provenance memory
recalled into an irreversible action binds that action's budget **against itself**, scoping the hard floor to
the consequential slice rather than the whole store. Both opt-in (`with_warrant=False` / `provenance_lo=None` =
legacy). Receipt: `inspeximus/probes/legible_warrant_scoped_budget_probe.py`.

### Require earned outcome for the irreversible tail: `spend_irreversible(require_earned=True)` (0.6.7)
By default `spend_irreversible(provenance_lo=...)` grants the full irreversible budget to any *corroborated*
source — and in the default (non-strict) config corroboration accepts ≥2 distinct **source strings**, which the
attacker sets, so a forged-source sybil poison can earn the full budget for an irreversible action.
`require_earned=True` narrows the full-budget grant to sources with an **earned outcome** (`good>0` and
`good>=bad`, set by `credit()` on real downstream success) — the one signal a sybil cannot mint (a forged or
attested ≥2-witness sybil clears corroboration but not this). Cost: any not-yet-earned legitimate source is
throttled to `provenance_lo` too, so it is opt-in for high-stakes deployments; default `False` is a
byte-identical legacy path. Receipt: `inspeximus/probes/spend_irreversible_require_earned_probe.py`.

### Near-tie recency reorder for corrected facts: `recall(tie_recent=eps)` (0.6.8)
When a fact is later **corrected in free text**, SRO supersession never triggers and the stale value can
outrank the fresh one: measured on MemBench (ACL 2025 Findings) knowledge-update questions, the **stale value
wins rank-1 in 32.7%** of cases — identically for raw cosine and inspeximus's semantic recall (receipt:
`inspeximus/probes/membench_recall_probe_v2.py`). `tie_recent=eps` re-orders candidates whose relevance is within
`eps` of the strongest candidate **newest-first** (by `valid_from`, falling back to `ts`); everything below the
band keeps its score order. Measured sweep (222 questions incl. 3 non-update control splits, receipt:
`inspeximus/probes/membench_recency_tiebreak_probe.py`): `tie_recent=0.05` on centered cosine cuts stale-beats-fresh
**0.327 → 0.109 (3×) at ~zero hit@1/5 cost on the control splits**; a *linear* position bonus was measured
useless (no movement before it damages controls) — the band reorder is the shape that works. Honest scope: the
benchmark's corrections always come after the original mention (by construction; the control-split cost is the
fairness check), and an adversarial **echo of the stale value re-stated after the correction would be
promoted** — don't use on hostile ingestion without provenance gating (combine with `influence_only`).
Opt-in; default `None` = byte-identical legacy recall.

### Echo-attack guard for corrected facts: `m.echo_guard = True` + `remember(object=...)` (0.6.9)
A fact is corrected (old value → superseded); later the OLD value is **re-stated** — a benign restatement or
an attacker re-injection. On a plain recency / bi-temporal / last-writer-wins store the restatement carries a
newer timestamp and **resurrects the stale value**. Measured on a MemBench echo fixture
(`inspeximus/probes/echo_attack_probe_v2.py`, retrieval-level stale-answer-rate, 43 corrected-fact cases; echoes
paraphrased cross-family with deepseek/kimi/glm): recency, a mem0-v1-faithful ADD/UPDATE/DELETE policy, and a
**bi-temporal Graphiti-faithful** policy all go **0.21 → 1.00** under both verbatim *and* paraphrased echo; a
verbatim-hash policy (MemStrata-style) holds against verbatim (0.21) but is **destroyed by paraphrase (1.00)**.
inspeximus's own keyed supersession is vulnerable too (end-to-end `echo_guard_e2e_probe.py`: **1.00** under both).

Set `echo_guard=True` and pass the asserted value as `remember(text, key=..., object=...)`: a keyed write
whose `object` matches a value **already superseded** for that key is a restatement-of-superseded — retired
on arrival, current value preserved. End-to-end this holds the stale rate at its no-echo baseline (~0.28)
under **both** verbatim and paraphrased echo (attack Δ ≈ 0, vs +0.65 without the guard).

**Load-bearing limit (measured, not assumed):** paraphrase-resistance comes ONLY from `object` being
value-preserving. Embedding near-duplicate **cannot** separate a same-value paraphrase (cos ≈ 0.95) from a
different-value correction (≈ 0.84) — they overlap (~42% false-block at 0.9) — so the guard is object/text
based, never similarity based. An echo that **obscures** the value (coreferent "her old hobby") is not
caught, and without `object` the guard falls back to normalized text (verbatim-only, MemStrata-equivalent).
A genuine reversal back to a superseded value needs `remember(..., reaffirm=True)` (the guard can't
un-supersede on its own), and the call reports what happened: `store.last_write["blocked"]` is `True` with
a `note` naming that remedy, so a retired write is never mistaken for one that landed.

**Default ON since 1.87.0.** `Inspeximus(echo_guard=False)` — or `INSPEXIMUS_ECHO_GUARD=0` in the
environment, which the constructor now honours as well as the surfaces do — restores byte-identical legacy
keyed supersession. An explicit argument always wins over the env var.

### Close the retrieval loop: `propagate_outcome()` (0.6.10)
The un-self-gradable earned-outcome signal (`credit()`) is what the influence gate and `echo_guard` ride on
— but on a live store we measured retrieval→earned **conversion at only ~28%** (16–62% across 8 agents;
`inspeximus/probes/retrieval_exposure_coverage_probe.py`). That gap is an **attribution** problem, not a ceiling:
the app hand-credits only some acted-on recalls, so most retrieved-and-used memory never earns its signal.
`propagate_outcome(outcome)` auto-credits the **decision-driving** subset of the last recall when the action
is scored, so coverage rises toward the app's scored-action rate without hand-threading ids into `credit()`.
Measured (`inspeximus/probes/outcome_propagation_probe.py`): conversion lifts from manual-attribution-limited to
the scored-action rate, and a **non-driver poison in the recall set earns 0%** under the default
`driving_only` mode (vs **50%** if you credit the whole set with `driving_only=False`) — so closing the loop
does **not** open a recall-set-attribution poison surface. Load-bearing limit: `driving_only=True, ids=None`
has a cold-start (a not-yet-corroborated fresh memory earns nothing) — pass the explicit driver id(s) for
first-use credit; the explicit path's poison-safety equals that of the recall that picked the driver (use
`recall(..., influence_only=True)` for high-stakes). Opt-in; nothing changes until you call it.

### Un-supersede a corrected fact: `revert(key)` + object-less clobber guard (0.6.12 / 0.6.13)
`revert(key)` restores the value that was current *before* the last keyed supersession — resolved
deterministically from the supersession ledger and re-asserted append-only (`reaffirm=True`), never by
editing a row. It's the control-plane un-do for a keyed value, exposed as an MCP tool. Alongside it, an
**object-less clobber guard**: on a key managed with explicit `object=` values (a value ledger), a keyed
write carrying *no* object can no longer displace a real value — a hole our own pilot found, where a
value-free reversion utterance ("go back to the old one") superseded the real value with junk text
(`inspeximus/probes/revert_by_reference_probe.py`, resistance **0.00 → 1.00**). Discrimination gap 1.0 vs a
content-only store. Changing a ledgered value now requires an explicit object, `reaffirm=True`, or `revert()`.

### Lineage-aware correction: `retract_lineage(subject)` (0.7.16)
When a fact has been corrected *after* it seeded derived write-backs (an agent stored "we use MongoDB", then
wrote "the MongoDB connection string is in config"), a value-only correction leaves those derived records
active — the knowledge-editing **ripple effect** (Cohen et al. RippleEdits, TACL 2024). `retract_lineage`
demotes the subject *and* everything that inherited it through `derived_from` taint to `superseded` — gone
from default recall, but **retained** (recallable with `include_superseded`, flagged `needs_rederivation`) so
you can re-derive against the corrected root instead of hard-deleting the payload (as `forget_subject` would).
This is classic retract-and-retain from Truth-Maintenance (Doyle 1979) and bitemporal invalidation, recently
ported to LLM-agent memory ([TOKI](https://arxiv.org/abs/2606.06240), [MemLineage](https://arxiv.org/abs/2605.14421));
inspeximus's only twist is that it rides the same `derived_from` taint as `forget_subject`, so it needs no separate
graph. It can only cascade on links that were actually recorded.

### Regenerate the demoted payload: `rederive(subject)` (0.7.17)
`retract_lineage` parks the derived facts; `rederive` brings them back. After you write the correction, it
takes every record stamped `needs_rederivation`, rewrites its text against the corrected root (default:
deterministic verbatim value substitution — a paraphrased fact that does not contain the old value verbatim
is SKIPPED and reported, never guessed; pass `rewrite=` for an LLM-backed rewriter), and re-remembers it with
`derived_from` -> the corrected root, so a future correction can cascade again. Measured
(`recovery_halflife_pilot.py`, k=3): residual harm 0.00 with the derived payload back ACTIVE asserting the
corrected value (3/3), vs naive correction (poisoned payload stays active, harm 0.98), hard delete (payload
lost) or demote alone (payload parked). corrupt -> launder -> correct -> `retract_lineage` -> `rederive` is
the complete correction lifecycle.

### Erasure-with-proof, in one call: `governance_report()` (0.7.18)
A right-to-erasure request (GDPR Art.17) is one place agent memory gets legally sharp: you must delete a
subject's data *and* keep an auditable record of the act (Art.30), without the deletion looking like tampering.
inspeximus already has the parts — `forget_subject(subject, request_id=...)` hard-deletes the subject **plus its
`derived_from` lineage** (a summary built from that subject's data goes too) and writes a hash-chained,
optionally Ed25519-signed deletion tombstone; `verify_writes()` then proves both the write-receipt chain and
the tombstone chain are intact, so a real erasure reads as *accounted-for* while a silent out-of-band delete
still trips the verifier. `governance_report(expected_pubkey=...)` stitches these into one auditor-facing
surface: erasures total, a per-`request_id` breakdown, and the tamper-evidence verdict.

```python
m.forget_subject("user-42", request_id="dsr-2026-07-12-0001")
m.governance_report(expected_pubkey=pk)
# -> {erasures_total, by_request:{"dsr-...":{erased, memory_ids}}, proof:{verified:True, all_signed:True, ...}, scope}
```

**Honest scope (stated in-band, because overclaiming here is the failure mode):** erasure is within *this*
inspeximus store only — not your vector store, prompt logs, or backups — and the tombstone proves the *act* of
deletion, never the content (a hash of PII is still PII). The signature is load-bearing only against a party
who does **not** hold `receipt_key`; anchor the chain head externally for operator-adversarial audit. It is a
tamper-evident **integrity primitive, not a compliance certification**. Prior art: crypto-shredding, Cassandra
/ event-sourcing tombstones, Certificate Transparency.

### The auditor's copy: a portable, independently-verifiable erasure certificate (1.13.0)
`governance_report()` is the operator's view; `erasure_certificate()` is the **auditor's** — a portable,
content-free document your DPO hands to a third party who then checks it **without your private key and without
trusting you**:

```python
cert = m.erasure_certificate(request_id="dsr-2026-07-12-0001")   # operator issues it

from inspeximus import verify_erasure_certificate                     # auditor verifies, standalone
verify_erasure_certificate(cert, store_path="mem.json", expected_pubkey=pk)
# -> {"valid": True, "checks": {chain_intact, signatures_valid, anchor_matches_tip, store_absent}, ...}
```

The verifier re-derives the tombstone hash-chain, checks every Ed25519 signature (pinnable to `expected_pubkey`),
confirms the anchor commits to the chain tip, and — reading **inspeximus's store records** — confirms every erased id
is genuinely absent (the value is gone from the store, not merely soft-deleted or kept in a history table).
Tampering a tombstone, faking an "erased" id that is still present, or pinning the wrong key each flips the
verdict to `valid: False`. Honest scope (`governance_report()`'s): this proves erasure from THIS inspeximus store's
records — NOT secure at-rest erasure against raw-disk/backup forensics (a plaintext store of any library, inspeximus
included, leaves bytes in free space/backups), and NOT the app's own vector store/logs. For secure at-rest
erasure use an encrypted store + `shred()` (NIST SP 800-88 crypto-erasure: destroy the key, ciphertext and
every backup die); for cross-store erasure register `ErasureTarget`s so `forget_subject` cascades. Receipts:
`inspeximus/probes/erasure_certificate_probe.py`, `erasure_raw_store_probe.py`.

### Hydration witness + index coherence: "this answer reflects store state as of revision X" (1.21.0)
A governed store can still serve a stale answer if the **derived index** (embeddings, caches) lags the store —
git guarantees the files, nothing guarantees the index agrees with them. Two deterministic, zero-LLM checks:

```python
w = m.witness()            # {digest, records, active, iso, receipts_tip?} — attach to any answer
m.verify_witness(w)        # later: {valid, digest_match, ...} — False = the answer predates a change
m.index_coherence()        # {coherent, missing_vecs, recipe_match, ...} — does the vec index match the store?
```

`state_digest()` covers exactly what retrieval can serve (id, status, ts, key, tenant, content hash), so any
write, supersession, revert, erasure, or out-of-band edit changes it; with `receipts=True` the witness is also
anchored to the tamper-evident write chain. Honest scope: the witness pins **this store and its view of its
index inputs** — it cannot attest external caches or copies it never saw.
(Receipt: `probes/hydration_witness_probe.py`, 12/12.)

### Org-wide erasure receipt: one signed manifest across every store you REGISTER
A right-to-erasure demand isn't satisfied by one library scrubbing its own file — the subject's data is also in
your vector index, your retrieval logs, your caches, your backups. `DeletionManifest` (`inspeximus.deletion_manifest`)
cascades the erasure across **every store you register** and emits ONE signed, tamper-evident manifest. Honest
scope: the manifest is an auditable trail over the stores it was shown — it names the registered stores that
complied (and the non-compliant ones), but it cannot attest a copy nobody registered (an unknown cache, a backup,
a teammate's already-hydrated context):

```python
from inspeximus.deletion_manifest import DeletionManifest
man = (DeletionManifest(sign_sk_hex=sk, pubkey_hex=pub)
       .register(InspeximusTarget(m)).register(vector_index).register(retrieval_log).register(backup))
cert = man.execute("alice", values=[the_pii], request_id="dsr-2026-...")
# -> {complete: bool, residual_targets: [...], entries:[{target, erased, verified_absent, sig}], chain_tip}
man.verify(cert)   # -> (ok, problems)  — re-checkable by an auditor
```

The property a within-one-library scrub can't give you: **`complete` is True only if EVERY registered store
verified the value no longer recoverable, and any store that didn't comply is NAMED in `residual_targets`** — the
receipt refuses to falsely certify. Each `ErasureTarget` implements two methods (`erase` + `still_recoverable`),
so a broken wiring produces an INCOMPLETE receipt, never a clean lie. Honest scope (in-band): it covers only the
registered targets (not unregistered stores), and "complete" is verified-non-recoverable at check time, not proof
of physical destruction — and it does not defend against reconstructing the subject from RETAINED embeddings
(embedding inversion, Morris et al., EMNLP 2023) unless the embeddings are a registered target too. Receipt:
`inspeximus/probes/org_wide_erasure_probe.py` (10/10, incl. a non-compliant backup correctly named + a tamper caught).

### Point-in-time / bi-temporal reads: `as_of()` + `history()` (0.6.14)
Every keyed write already carries a `[valid_from, invalidated_at)` interval, so the timeline is
reconstructable with **no graph DB**. `as_of(key, when)` returns the value that was current at event-time
`when`; `history(key)` returns the full validity timeline (every value the key has held, each interval, its
status, and — since 0.6.18 — the policy that retired it). Closes the one real point-in-time edge a
bi-temporal graph store had, on the existing intervals. Honest limit: an out-of-order back-fill resolves by
event-time (`valid_from`), not ingest order.

### Run bounded in production: `Inspeximus(capacity=N)` two-tier eviction (0.6.15)
Append-only is unbounded; production memory isn't. `Inspeximus(capacity=N)` hard-evicts the lowest-value **active**
records past `N` via the verified value-protected + recency-aged rule (`protect_frac` of the cap is
recency-immune so a rare-but-critical memory survives a flood; the rest fill by decay-weighted value so a
stale high-value memory can't crowd out a fresh one). Superseded history isn't counted or evicted (it's cheap
and preserves `as_of`). Default `None` = unbounded legacy, byte-identical. (`inspeximus/probes/` Lab 29992a.)

### Defer the expensive reorg to idle: `sleep()` (0.6.16)
Consolidation (cluster merge, keep-budget, capacity) is O(n); doing it on the write path taxes every
`remember()`. `sleep()` defers it to an idle call the host schedules (a "sleep-time compute" pass) — the
write path stays fast, `sleep()` is a no-op when there's nothing ripe, idempotent, and recall-safe. Exposed
as the `sleep` MCP tool. Pure library primitive: no agent loop, no graph DB, no host required.

### Sybil-resistant corroboration: seed-anchored flow trust `trust_seeds` (0.6.17)
Corroboration by "≥2 distinct sources" (or, with `strict_corroboration`, ≥2 distinct Ed25519 keys) is
**symmetric** — and distinct keys are free to mint, so a determined Sybil clears the bar (Douceur 2002;
Cheng–Friedman 2005 prove only *asymmetric*, flow-based trust is Sybilproof). `trust_seeds` adds that anchor:
a corroborating witness counts only if its source is in the trust closure grown from app-seeded roots via
vouch edges (TrustRank/Advogato; Gyöngyi et al. 2004), up to `trust_hops`. Un-vouched self-minted sources
contribute **zero** trusted witnesses (`inspeximus/probes/seed_anchored_trust_probe.py`, 4/4). Default empty set =
byte-identical legacy. Honest limit: it relocates the residual to "earn *one* seed endorsement" and assumes
sound seeds + attribution — the earned-outcome path (`credit()`) stays the orthogonal unforgeable channel.

### Which resolver retired each fact: `superseded_by_policy` + `supersession_report()` (0.6.18)
A store's history says *what* was retired but not *why*. Every supersession path now stamps
`meta['superseded_by_policy']` (`keyed_lww` / `keyed_lww_backfill` / `keyed_reaffirm` / `echo_guard` /
`objectless_guard` / `state_toggle` / `toggle_corroborated` / `toggle_persistence` / `keep_budget`);
`history()` exposes it per row and `supersession_report()` aggregates counts per policy — the write-time
judge log most memory systems omit (cf. TOKI, arXiv:2606.06240). Additive metadata only; no resolution
decision changes (`inspeximus/probes/supersession_policy_stamp_probe.py`, 10/10).

### Right-to-erasure that keeps the audit trail honest: `forget_subject()` + deletion tombstones (0.6.19+)
`forget()` genuinely removes content — but a hard delete makes `verify_writes()` report the now-missing
record as "deleted out-of-band", so a legitimate erasure is indistinguishable from tampering.
`forget_subject(subject, request_id=…)` erases every memory attributable to a data subject **across
provenance lineage** (its own canonical source *and* any record that inherited it through `derived_from`
taint — so a summary built from the subject's data is erased too, which a naive text-match delete misses),
then appends a signed, hash-chained **deletion tombstone** per record. The tombstone commits to the record's
random surrogate id + a timestamp + your opaque `request_id` and **nothing content-derived** (a hash of PII
is still PII), so `verify_writes()` now reports the erasure as *accounted-for* (chain intact, provably
erased) while a record missing *without* a tombstone still flags as tampering — and a forged tombstone is
caught by the same check. `erasure_report()` is the content-free proof-of-deletion trail.
**Honest scope:** this erases + proves-the-act **within this inspeximus store only** (not your vector store, prompt
logs, or backups); it is an integrity primitive, **not** a compliance certification, and the signature is
load-bearing only against a party who does not hold `receipt_key`. Prior art: crypto-shredding; Cassandra /
event-sourcing tombstones; GDPR Art. 30 erasure logs; Crosby-Wallach / Certificate-Transparency
tamper-evident logs. Receipt: `inspeximus/probes/forget_subject_tombstone_probe.py` (8/8).


### One answer to "where did this fact come from?": `provenance()` (1.47.0)
The parts were all there — `source` + `derived_from` taint, `attested_key`, `grade()`, `history()`,
`verify_attribution()`, `anchor()` — but answering the single most-asked question of a memory layer meant
calling six of them and knowing which. `provenance(key=…)` (or `id=…`) assembles them for ONE fact:
`origin` (declared source, the taint inherited **transitively** through summarization, attestation, the
acting user/agent/session, the orphan flag, and any ancestor that has since been erased), `trust` (the
evidence grade — never writer-settable), `timeline` (`history()`, incl. the policy that retired each value),
and `integrity` (whether the record still matches the content **and attribution** its write receipt
committed to, so a later relabel is loud; plus the current `anchor()` to pin the answer against). Exposed as
`inspeximus provenance <key>` (`--json`) and the `provenance` MCP tool; the CLI forces receipts on, since a
report *about* the chain must load it. Read-only, no new state, no new claim layer.
**Honest scope**, returned in a `limits` field so a renderer cannot silently drop it: tamper-**evident**, not
**correct** (a source that was wrong at write time is committed faithfully — the oracle problem, untouched),
and unsigned it only catches an editor who cannot also rewrite the `.receipts` sidecar. Receipt:
`tests/test_provenance.py` (9/9, incl. a relabel-detection case).

**Two further limits worth stating precisely.** (1) The database-provenance literature separates *where*
(source location), *why* (witness set) and *how* (semiring derivation) provenance, and proves they are not
interchangeable — where-provenance is not expressible in the semiring model (Cheney, Chiticariu & Tan, *FnT
Databases* 1(4), 2009, §5.4; how-provenance is Green, Karvounarakis & Tannen, PODS 2007). What
`provenance()` returns is **where-provenance plus a lineage edge set**, not a derivation semiring: it tells
you which sources a value is attributable to and which ancestors a retraction would reach, not how the value
was computed from them. (2) Propagating taint through summarization inherits the taint-analysis dilemma
(Schwartz, Avgerinos & Brumley, IEEE S&P 2010): propagate everything and eventually all memory is tainted;
propagate nothing and you miss real flows. inspeximus propagates along **explicit `derived_from` edges only**,
which is deliberately on the under-tainting side — it will miss a derivation the caller never declared.

For context on where this sits: reading the memory-write paths of mem0, Zep/Graphiti, Cognee, Letta and LangMem
at `main` on 24 Jul 2026, we found no hash chain, signature or anchoring over memory writes, and no transitive
lineage taint through summarization (Cognee's `source_content_hash` is the nearest thing and is a content
identifier, not a chain). That is five libraries, not the field — smaller projects do ship hash-chained memory
audit logs, and we read the write paths rather than every file. Graphiti carries the richest lineage —
`EntityEdge.episodes` plus validity intervals — and its own docs are explicit that `remove_episode` does not
regenerate node summaries, which is the same summarization boundary described above.

**Prior art, credited.** Committing the actor/attribution into a tamper-evident provenance chain so a
retroactive relabel is detectable is Hasan, Sion & Winslett, *The Case of the Fake Picasso: Preventing History
Forgery with Secure Provenance* (USENIX FAST 2009; journal version ACM TOS 5(4), 2009); serving provenance facets from a single call is standard in provenance-aware
databases (Perm, ProvSQL, ProQL); signed Merkle-logged lineage for LLM agent memory is MemLineage
([arXiv:2605.14421](https://arxiv.org/abs/2605.14421)), already credited in `remember()`'s lineage
auto-stamping. The mechanism is not new; the contribution is packaging it into one zero-dependency library
with the limits returned alongside the answer.

### After a deletion, check what the lineage says survived: `erasure_audit()` (1.48.0)
`forget_subject()` erases the records attributable to a subject in this store and tombstones the act.
`erasure_audit(subject=, values=)` answers the next question — what survived? The hard case is never the
record; it is the summary built from it, which no longer resembles the subject's data.

Returns `{verdict, residue, advisory, coverage, checked, limits}`. **`coverage` is the load-bearing field.**
Every structural check walks DECLARED `derived_from` edges, so a store that declares none has nothing to walk
and would otherwise report "nothing found" while having inspected nothing — a false assurance on a deletion.
When nothing is declared the verdict is `unaudited`, never a pass; `coverage` reports
`{records, with_declared_lineage, undeclared_derived, declared_ratio}` so a caller can see how much the
answer is worth.

`residue` (drives the verdict) holds findings tied to a **deliberate** erasure — one whose tombstone carries a
request id or a real basis, not the generic default: `subject_still_attributable`, `taint_without_origin` (a
derivative outlived the origin it inherited), `dangling_lineage`, `tombstone_gap`. `advisory` holds the same
shapes where the missing record was removed with **no** erasure request: capacity eviction and the
consolidation keep-budget both hard-delete for size reasons and would otherwise masquerade as erasure residue
in any bounded store — reported with a `cause`, never counted. `value_possibly_recoverable` (only with
`values=`) is an explicit heuristic in `advisory` that never moves the verdict, and matches with **longer-token
exclusion**: plain `` lets `UTC` fire inside `UTC-8`, reporting a different, longer value as recovered.

CLI `inspeximus erasure-audit --subject X [--value V]` (prints coverage first; exit 1 only on `residue_found`,
so it works as a regression gate) and the `erasure_audit` MCP tool. Deterministic, read-only, no LLM.

**What it is not.** Evidence about what the store has RECORDED, not proof that no copy of the material
remains, and it does not discharge an erasure obligation. Limits shipped in the response: taint propagates
along declared edges only, so an undeclared summary is invisible to every structural check (asserted by
`test_an_undeclared_derivative_is_NOT_found_structurally` — we ship the hole as a test, not a footnote); it
covers this store only, never your vector index, prompt logs, model weights or backups; and because it reads
metadata the writer supplied, a party that stops declaring lineage always looks clean. The declared-edges
choice is the under-tainting side of the overtainting/undertainting trade-off argued for dynamic taint
analysis by Schwartz, Avgerinos & Brumley (IEEE S&P 2010), which is program analysis rather than lineage, so
we borrow the trade-off, not a result.

**Prior art.** This is DELF-style deletion-correctness auditing (Cohn-Gordon et al., *DELF: Safeguarding
deletion correctness in Online Social Networks*, USENIX Security 2020 — deletion annotations over a typed
object graph, statically rejecting unannotated object/edge **types**) applied to an agent-memory store; the orphan/dangling half is
classical referential-integrity checking. Stronger formal treatments of the same problem exist (Garg,
Goldwasser & Vasudevan, *Formalizing Data Deletion in the Context of the Right to be Forgotten*,
EUROCRYPT 2020; Chakraborty et al., *Meaningful Data
Erasure in the Presence of Dependencies*, PVLDB 18(10) 2025). What is ours is a shipped implementation in an
agent-memory library. Receipt: `tests/test_erasure_audit.py` (10/10, including a mutation-killing negative
control and the eviction-is-not-residue case).

**Fix shipped alongside (1.48.0):** the CLI opened stores with receipts OFF, so a shell `inspeximus remember`
against a receipted store silently did **not** extend the receipt chain — the CLI punched a hole in the very
evidence it exists to produce, and the next `verify_writes()` saw an unreceipted record. `_store()` now
detects an existing `<path>.receipts.json` sidecar and keeps receipts on. Regression test:
`test_cli_write_extends_an_existing_receipt_chain`.

### Lineage without asking the writer: `infer_lineage` (1.49.0)
`remember(derived=True)` stamps the last recall as a write's parents — the store carrying the edge so the
untrusted model never holds the switch. Correct shape, wrong trigger: the flag is writer-set, and measured on
our own 8-agent, 43-day, **27,290-record** deployment its coverage was **0.00%**, alongside `key`, `object`,
`source`, `taint` and `attested_key` — while the fields the store computes for itself (`links`, `superseded`)
ran at 88–90%. The single write call in that deployment passes four arguments, none of them a declaration.
**Anything the writer must declare reads zero.**

`Inspeximus(infer_lineage=0.2)` moves the decision into the store: on a write with no explicit parents, if the
new text shares enough *more* with what was just recalled than with a same-store baseline it was not built
from, that recall is stamped as the parents. No flag, no embedding, no LLM.

The null adjustment is the design, not a refinement. On 27,342 real agent writes a raw overlap threshold is
degenerate — median overlap against the true predecessor is **1.000**, and even 0.8 stamps **77%** of writes,
because agents reuse a small vocabulary. Against a random same-store window the overlap is still 0.540, so
most of a raw score is vocabulary rather than lineage. Null-adjusted, the firing rate is **~22%** and stable
across thresholds 0.10–0.50.

**MEASURED AND WITHDRAWN (1.50.0; numbers corrected 2026-07-27).** Against constructed ground truth
(`probes/infer_lineage_precision.py`) this does not work — and the way it fails is not the way this
paragraph used to describe. Re-run against the probe as it stands, at its best threshold (0.05), with
same-domain negatives written immediately after the same recall:

| parent wording retained in the derivative | TP | FP | precision | recall |
|---|---|---|---|---|
| 90% | 8 | **0** | 1.000 | 0.133 |
| 70% | 5 | **0** | 1.000 | 0.083 |
| 50% | 1 | **0** | 1.000 | 0.017 |
| 30% | 0 | 0 | — | 0.000 |

It stamps **no wrong parents at all**; it simply almost never finds the right one, and stops finding
anything once the derivative stops quoting its parent. Recall falls from 0.133 to 0.000 across a 90%→30%
drop in retained wording, and above threshold 0.05 it fires on nothing.

This paragraph previously read "precision 0.06-0.23 … 43 wrong parents for every 13 right ones", which is
the OPPOSITE failure mode and appears nowhere in the probe's output; it was corrected when an audit
compared the published number against the artifact it cites. The withdrawal stands either way — a signal
that only fires on near-verbatim reuse is not lineage — but for the recall reason, not the precision one.

So the ~22% firing rate above is not 22% of true derivations. Default stays `0.0` = OFF; the code is kept as a substrate, the claim is not. It over-taints deliberately: a false parent is visible in `provenance()`, a
missing one is silent. Default `0.0` = OFF (byte-identical legacy), and an explicit `derived_from` always
wins. Receipt: `tests/test_infer_lineage.py` (8/8).
