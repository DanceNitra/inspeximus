<div align="center">

# Mnemosyne · `mnemo`

**The self-correcting memory layer for AI agents.**

*When a fact changes, mnemo serves the correction — and won't let the stale value creep back in. Extracted from an autonomous research OS that has run it daily over ~6,000 notes.*

`pip install agora-mnemo` · [PyPI](https://pypi.org/project/agora-mnemo/) · [Hugging Face](https://huggingface.co/Danchi17/mnemo) · [DOI 10.5281/zenodo.21128549](https://doi.org/10.5281/zenodo.21128549) · [Homepage](https://dancenitra.github.io/mnemo/) · MIT · v1.9.9

</div>

---

`mnemo` is the recall + consolidation core of [Agora](https://github.com/DanceNitra/agora) — an
autonomous research system — distilled into **a single file with no required dependencies**. It does
the four things agent memory actually needs, the way that held up running in production for weeks.

Most "agent memory" libraries are demos. This one is extracted from a system that has used it daily
to curate a 6,000-note knowledge base, and whose consolidation behaviour we have **measured**, not
assumed (see *Provenance* below).

## Quickstart (2 minutes)

```bash
pip install agora-mnemo          # zero required dependencies
```

```python
from mnemo import Mnemo

m = Mnemo("memory.json")                      # persists to JSON; drop the path for pure in-memory

m.remember("The API rate limit is 1000 req/min", key="api::rate_limit")
m.remember("User prefers dark mode",            key="ui::theme")

m.recall("what is the rate limit")            # -> ["The API rate limit is 1000 req/min"]

# Correction is first-class: writing the same key supersedes the old value — no config, no LLM call.
m.remember("The API rate limit is 5000 req/min", key="api::rate_limit")
m.recall("rate limit")                        # -> ["The API rate limit is 5000 req/min"]  (only the current value)

m.history("api::rate_limit")                  # -> full audit trail: [1000 -> 5000], oldest to newest
```

That is the whole loop most agents need: **remember, recall, correct, and audit** — in one zero-dependency
file. Add `embed=your_model` for semantic recall; everything below is depth (governance, poison-resistance,
bitemporal, multi-tenancy) you can reach for when you need it.

Runnable examples live in [`examples/`](examples/): [basics](examples/01_basics.py) ·
[correction & erasure](examples/02_correction_and_erasure.py) · [semantic recall](examples/03_semantic_recall.py).

**Jump to:** [Correction (measured)](#correction-is-a-first-class-operation-measured-across-systems) ·
[Governance & erasure](#governance-erasure--audit) · [Install](#install) ·
[MCP server](#use-it-as-an-mcp-server-any-claude--cursor--agent-client) ·
[Framework integrations](#framework-integrations) ·
[The four operations](#the-four-operations) · [Five rules](#five-rules-it-wont-break-each-one-cost-us-to-learn) ·
[Provenance & receipts](#provenance--why-these-rules-with-receipts) · [Threat model](#threat-model--layered-defense-adversarial-memory-integrity)

## Correction is a first-class operation (measured across systems)

Any memory layer can store a fact and retrieve it. The harder, less-benchmarked property is **integrity**:
when a fact is corrected, can the store *undo* the correction on command, and does restating a retired value
*resurrect* it? mnemo treats correction as a first-class channel — `revert(key)`, `revert_now` /
`revert_intent`, `retract_lineage`, `echo_guard`, and the `route()` intent tagger — and we measured it against
mem0 and Graphiti in their **native configs** with a shared, **ground-truth-blind** judge (harness +
methodology: [`probes/INTEGRITY_BENCHMARK.md`](probes/INTEGRITY_BENCHMARK.md)):

| value-obscuring revert · undo a correction from an unmarked "go back" (n=20) | success | 95% CI |
|---|---|---|
| **mnemo** (route/revert) | **0.75** | [0.53, 0.89] |
| mem0 2.0.11 (native, gpt-4o-mini) | 0.20 | [0.08, 0.42] |
| Graphiti (native, live neo4j) | 0.00 | [0.00, 0.16] |

Only mnemo exposes a channel to undo a correction on command; mnemo's and mem0's CIs do not overlap, so the
capability gap survives at n=20. We lead with the cell we *don't* win: **echo-resurrection is a tie** — all
three defend against a restated stale value. This is a narrow, adversarial, command-driven cut, not a general
"mnemo is better" claim; run it yourself or add your system.

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
from an injected one is an **authority** call, not a content call: `Mnemo(support_authorities=[...])` requires
grounds to be **Ed25519-signed by an allowlisted key** (self-minted grounds then count zero, and a
`{pubkey: class}` mapping counts distinct provenance *classes*, so two keys sharing one upstream source count
once). Honest limit, credited: this is exogenous-trust-root / anti-Sybil (Douceur 2002; DKIM / W3C VC — a
signature attests *source*, not *truth*); it makes the steward's independence judgement *enforceable*, it does
not certify independence. Runnable: [`examples/05_review_trigger.py`](examples/05_review_trigger.py).

## Governance, erasure & audit

mnemo ships tamper-evident governance primitives — built by auditing mnemo against a governance-evidence
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
  every memory store's native delete (8/8 in our measured cell, mnemo included). The fix:
  `register_erasure_target(target)` your app-side stores (vector index, caches, logs — the two-method
  `ErasureTarget` protocol), and `forget_subject()` cascades the erasure through every one and returns a
  hash-chained **manifest** — honest by construction: `complete` only if *every* store (mnemo self-checked
  first) verified the value no longer recoverable, leaking stores NAMED. Measured: unwired 8/8 leak → wired
  0/8 with verifying chains; a broken wiring cannot produce a clean receipt (0/8 falsely complete).
  `DeletionManifest` (`mnemo.deletion_manifest`) remains usable standalone.
- **Identity-confidence gate on supersession (1.9.0)** — a keyed correction supersedes on `(entity, field)`,
  which is only right if the identity is right. When identity is resolved fuzzily, `remember(..., identity_confidence=c)`
  gates the write: `c` below `fork_below` (0.7) forks a **candidate** instead of overwriting the authoritative
  value, and `candidates()` / `promote_candidate()` / `discard_candidate()` are the steward path. Measured: under
  noisy identity resolution an ungated auto-commit corrupts the ledger 13.5% of the time; the gate cuts it to 1.0%
  (93%) at the cost of a review queue. *Not a new idea, credited: record linkage's clerical-review zone (Fellegi &
  Sunter 1969) and MDM match-merge stewardship, ported to an agent-memory write path where nobody gates it.*
- **`ErasureAuditor`** (`mnemo.erasure_auditor`) — after your app runs its deletion, adversarially re-attempts
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
curl -O https://raw.githubusercontent.com/DanceNitra/mnemo/main/mnemo/mnemo.py
```

## Use

```python
from mnemo import Mnemo

m = Mnemo("memory.json")                       # persists to JSON; or Mnemo("memory.json", embed=my_model)

m.remember("Pre-trend tests catch only ~31% of fatal DiD bias.", tags=["causal"], value=3, mtype="semantic")
m.recall("difference in differences", k=5)     # relevance × value, decayed by the memory's per-type half-life
m.consolidate(keep=200)                        # the "dream" pass: hubs, dedup, STATE-TOGGLE, keep-budget
m.consolidate_clusters(threshold=15)           # cluster-TRIGGERED: consolidate only a topic that's grown dense
m.contradictions()                             # flag incompatible memories for REVIEW (never deletes)
m.value_by_cohort()                            # value reported per tag/time-block, not per memory
```

Bring any text→vector function as `embed=` for semantic recall; with none, `mnemo` falls back to a
forgiving lexical match so it **runs anywhere, today**. Once the store grows past the threshold, recall
**fuses lexical (BM25) + semantic with Reciprocal Rank Fusion**. On high-lexical-overlap agent memory
(e.g. LoCoMo) the fused hybrid *measurably* beats either channel alone (recall@20 **+0.06** over the best
single channel, 9/10 conversations, conversation-level bootstrap CI excludes 0; receipt:
[`probes/locomo_retrieval_map.py`](probes/locomo_retrieval_map.py)); where the embedder already dominates
(paraphrase-heavy corpora, see benchmarks) fusion adds little. `mode='auto'` fuses; `mode='lexical'` /
`'semantic'` force a single channel.

### Poison-resistant recall: `recall(..., influence_only=True)` (0.4.0)

Retrieval-time / embedding-geometry defenses do **not** stop memory poisoning in general. We red-teamed
`mnemo` with a real AgentPoison-style single-instance attack (Chen et al., NeurIPS 2024; PoisonedRAG, Zou
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
provenances), it does not make poisoning impossible. Receipts: [`probes/agentpoison_influence_gate.py`](probes/agentpoison_influence_gate.py),
[`probes/agentpoison_influence_gate_validation.py`](probes/agentpoison_influence_gate_validation.py).

### Know before you gate: `influence_gate_report()` (0.4.3)

The influence gate is not free, and its cost is **density-dependent** — so check it before you rely on it.
`influence_gate_report()` returns the gate's **live cost on your store** (`would_block_frac` = the fraction of
active memories it would filter, plus the corroboration breakdown and an `advice` string). Why it matters, and
both measured on [`probes/oracle_separation_density.py`](probes/oracle_separation_density.py) (controlled corpus,
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
per-action cap only lets him amortize. Receipts: [`probes/triad_attacker_split.py`](probes/triad_attacker_split.py),
[`probes/reversibility_gate_frontier.py`](probes/reversibility_gate_frontier.py).

Because detection is imperfect — a self-graded / MINJA-style oracle can be *tricked* into flagging a legitimate
source, so `slash()` can be **weaponised** to knock out a rival's memory — the forfeiture is reversible:
`restore(ids, scope='source')` recovers the **exact** pre-slash standing (saved in `meta['pre_slash']`), or a
clean slate if none was recorded. The penalty is heavy, so the appeal is cheap — otherwise `slash()` itself
becomes the attack surface.

**Provenance that rides through transformation: `remember(..., derived_from=[ids])` (0.4.6).** All of the above —
`slash`, a per-source influence budget, any source-level accountability — is silently un-countable the moment a
memory is *transformed*: an app-side **summary** of five source-memories is a fresh record with no source, so
`slash(source)` can't reach it and a cumulative cap can't attribute its slices. `mnemo`'s own consolidation never
loses provenance (it links, never merges text), but LLM summarization/rewrite does. `remember(text,
derived_from=[parent_ids])` closes that hole: the new record **inherits the union of its parents' canonical
sources** as a `taint` (transitively — a summary-of-a-summary still carries the origin), and `slash(scope='source')`
matches on *own source OR inherited taint*, so forfeiting a source also burns every derived summary it fed. The
honest boundary: the app has to *declare* the derivation at the transformation step — `mnemo` can carry the taint
through, but it can't recover provenance an opaque summary threw away. This is the substrate everything else is
deterrence math on top of. Receipt: [`probes/triad_attacker_split.py`](probes/triad_attacker_split.py).

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
[`probes/triad_attacker_split.py`](probes/triad_attacker_split.py),
[`probes/reversibility_gate_frontier.py`](probes/reversibility_gate_frontier.py).

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
its irreversibility). Measured: [`probes/lifetime_budget_bound.py`](probes/lifetime_budget_bound.py) shows the
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
Measured: [`probes/attribution_floor.py`](probes/attribution_floor.py) — a source relabel and a taint-strip are
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
signs the claims it authored (`sig = mnemo.attest(text, source_sk, source_doc)`; write with
`remember(..., attestation=(source_pubkey, sig))`), the signature is verified over the *same claim + canonical
source* at write time (a forged or replayed attestation is **rejected**, not silently dropped), and the record
carries `attested_key`. Independence is then measured by distinct **Ed25519 public keys an attacker cannot
forge** — N sybil variants of one origin collapse to one witness unless the attacker holds N distinct keys (a
costly identity; Douceur 2002). This is the **exogenous trust root** the attribution problem bottoms out on:
"can I trust the label" becomes "can I trust the root", i.e. the identity axis. Measured:
[`probes/attribution_verified_key.py`](probes/attribution_verified_key.py) — a two-string spoof that passes the
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
correlated-auditor guard. Receipt: [`probes/evidence_grade_ratchet.py`](probes/evidence_grade_ratchet.py) shows (1)
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
`wikipedia.org` to one, but on **time**. Measured ([`probes/temporal_gate_demo.py`](probes/temporal_gate_demo.py)):
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
token-Jaccard — above the threshold. Measured ([`probes/coherence_gate_demo.py`](probes/coherence_gate_demo.py)):
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
supplies the text and never holds the switch. Measured ([`probes/autostamp_lineage.py`](probes/autostamp_lineage.py)):
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
[`mnemo/probes/retraction_propagation.py`](https://github.com/DanceNitra/agora/blob/main/mnemo/probes/retraction_propagation.py).
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
`recall()` on LoCoMo (receipt: [`probes/locomo_soft_prefer_filter.py`](probes/locomo_soft_prefer_filter.py)):
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
case, unchanged. MEASURED (receipt: [`probes/locomo_composed_soft_filters.py`](probes/locomo_composed_soft_filters.py),
self-check 0/1568 vs the shipped path): on LoCoMo questions carrying two independent cues (n=183), the
product composition scores **recall@20 0.865 vs 0.755 for the best single cue (+0.110, bootstrap CI excludes
0)**, while a summed boost *capped at one dimension's trust* crowds out (−0.053 — the cap flattens the joint
evidence, the classic "combine outside the saturating form" failure, BM25F/Robertson et al. CIKM 2004). So:
compose as a **product**, and if you cap, cap the product — the same choice production search settled on
(Elasticsearch defaults `score_mode=multiply`). Honest scope: one benchmark, one embedder, near-orthogonal
cues. Reversible: a single dict / `None` behaves exactly as before.

**Compose only cues you trust** (receipt: [`probes/locomo_correlated_cue_composition.py`](probes/locomo_correlated_cue_composition.py)).
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
vector index. Receipt: `mnemo/probes/continuous_chunk_recall_probe.py`.

### Make the not-asserting visible: `recall(with_warrant=True)` + `spend_irreversible(provenance_lo=...)` (0.6.6)
A silent low score for "no independent channel" decays into *"unverified but present"* — a downstream consumer
reads quiet as a soft yes and you are back to consensus-over-poison with extra steps. So the abstention is made
a first-class, branchable STATE: `recall(with_warrant=True)` tags each hit `earned` / `corroborated` /
`unwarranted`, and the consumer rule is *never let `unwarranted` drive a consequential decision*. Complementing
it, `spend_irreversible(ids, amount, budget, provenance_lo=0.15)` caps a source with **no corroborated
contributing record** at the small `provenance_lo` instead of the full budget — a low-provenance memory
recalled into an irreversible action binds that action's budget **against itself**, scoping the hard floor to
the consequential slice rather than the whole store. Both opt-in (`with_warrant=False` / `provenance_lo=None` =
legacy). Receipt: `mnemo/probes/legible_warrant_scoped_budget_probe.py`.

### Require earned outcome for the irreversible tail: `spend_irreversible(require_earned=True)` (0.6.7)
By default `spend_irreversible(provenance_lo=...)` grants the full irreversible budget to any *corroborated*
source — and in the default (non-strict) config corroboration accepts ≥2 distinct **source strings**, which the
attacker sets, so a forged-source sybil poison can earn the full budget for an irreversible action.
`require_earned=True` narrows the full-budget grant to sources with an **earned outcome** (`good>0` and
`good>=bad`, set by `credit()` on real downstream success) — the one signal a sybil cannot mint (a forged or
attested ≥2-witness sybil clears corroboration but not this). Cost: any not-yet-earned legitimate source is
throttled to `provenance_lo` too, so it is opt-in for high-stakes deployments; default `False` is a
byte-identical legacy path. Receipt: `mnemo/probes/spend_irreversible_require_earned_probe.py`.

### Near-tie recency reorder for corrected facts: `recall(tie_recent=eps)` (0.6.8)
When a fact is later **corrected in free text**, SRO supersession never triggers and the stale value can
outrank the fresh one: measured on MemBench (ACL 2025 Findings) knowledge-update questions, the **stale value
wins rank-1 in 32.7%** of cases — identically for raw cosine and mnemo's semantic recall (receipt:
`mnemo/probes/membench_recall_probe_v2.py`). `tie_recent=eps` re-orders candidates whose relevance is within
`eps` of the strongest candidate **newest-first** (by `valid_from`, falling back to `ts`); everything below the
band keeps its score order. Measured sweep (222 questions incl. 3 non-update control splits, receipt:
`mnemo/probes/membench_recency_tiebreak_probe.py`): `tie_recent=0.05` on centered cosine cuts stale-beats-fresh
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
(`mnemo/probes/echo_attack_probe_v2.py`, retrieval-level stale-answer-rate, 43 corrected-fact cases; echoes
paraphrased cross-family with deepseek/kimi/glm): recency, a mem0-v1-faithful ADD/UPDATE/DELETE policy, and a
**bi-temporal Graphiti-faithful** policy all go **0.21 → 1.00** under both verbatim *and* paraphrased echo; a
verbatim-hash policy (MemStrata-style) holds against verbatim (0.21) but is **destroyed by paraphrase (1.00)**.
mnemo's own keyed supersession is vulnerable too (end-to-end `echo_guard_e2e_probe.py`: **1.00** under both).

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
un-supersede on its own). Opt-in; `echo_guard=False` (default) = byte-identical legacy keyed supersession.

### Close the retrieval loop: `propagate_outcome()` (0.6.10)
The un-self-gradable earned-outcome signal (`credit()`) is what the influence gate and `echo_guard` ride on
— but on a live store we measured retrieval→earned **conversion at only ~28%** (16–62% across 8 agents;
`mnemo/probes/retrieval_exposure_coverage_probe.py`). That gap is an **attribution** problem, not a ceiling:
the app hand-credits only some acted-on recalls, so most retrieved-and-used memory never earns its signal.
`propagate_outcome(outcome)` auto-credits the **decision-driving** subset of the last recall when the action
is scored, so coverage rises toward the app's scored-action rate without hand-threading ids into `credit()`.
Measured (`mnemo/probes/outcome_propagation_probe.py`): conversion lifts from manual-attribution-limited to
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
(`mnemo/probes/revert_by_reference_probe.py`, resistance **0.00 → 1.00**). Discrimination gap 1.0 vs a
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
mnemo's only twist is that it rides the same `derived_from` taint as `forget_subject`, so it needs no separate
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
mnemo already has the parts — `forget_subject(subject, request_id=...)` hard-deletes the subject **plus its
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
mnemo store only — not your vector store, prompt logs, or backups — and the tombstone proves the *act* of
deletion, never the content (a hash of PII is still PII). The signature is load-bearing only against a party
who does **not** hold `receipt_key`; anchor the chain head externally for operator-adversarial audit. It is a
tamper-evident **integrity primitive, not a compliance certification**. Prior art: crypto-shredding, Cassandra
/ event-sourcing tombstones, Certificate Transparency.

### Point-in-time / bi-temporal reads: `as_of()` + `history()` (0.6.14)
Every keyed write already carries a `[valid_from, invalidated_at)` interval, so the timeline is
reconstructable with **no graph DB**. `as_of(key, when)` returns the value that was current at event-time
`when`; `history(key)` returns the full validity timeline (every value the key has held, each interval, its
status, and — since 0.6.18 — the policy that retired it). Closes the one real point-in-time edge a
bi-temporal graph store had, on the existing intervals. Honest limit: an out-of-order back-fill resolves by
event-time (`valid_from`), not ingest order.

### Run bounded in production: `Mnemo(capacity=N)` two-tier eviction (0.6.15)
Append-only is unbounded; production memory isn't. `Mnemo(capacity=N)` hard-evicts the lowest-value **active**
records past `N` via the verified value-protected + recency-aged rule (`protect_frac` of the cap is
recency-immune so a rare-but-critical memory survives a flood; the rest fill by decay-weighted value so a
stale high-value memory can't crowd out a fresh one). Superseded history isn't counted or evicted (it's cheap
and preserves `as_of`). Default `None` = unbounded legacy, byte-identical. (`mnemo/probes/` Lab 29992a.)

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
contribute **zero** trusted witnesses (`mnemo/probes/seed_anchored_trust_probe.py`, 4/4). Default empty set =
byte-identical legacy. Honest limit: it relocates the residual to "earn *one* seed endorsement" and assumes
sound seeds + attribution — the earned-outcome path (`credit()`) stays the orthogonal unforgeable channel.

### Which resolver retired each fact: `superseded_by_policy` + `supersession_report()` (0.6.18)
A store's history says *what* was retired but not *why*. Every supersession path now stamps
`meta['superseded_by_policy']` (`keyed_lww` / `keyed_lww_backfill` / `keyed_reaffirm` / `echo_guard` /
`objectless_guard` / `state_toggle` / `toggle_corroborated` / `toggle_persistence` / `keep_budget`);
`history()` exposes it per row and `supersession_report()` aggregates counts per policy — the write-time
judge log most memory systems omit (cf. TOKI, arXiv:2606.06240). Additive metadata only; no resolution
decision changes (`mnemo/probes/supersession_policy_stamp_probe.py`, 10/10).

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
**Honest scope:** this erases + proves-the-act **within this mnemo store only** (not your vector store, prompt
logs, or backups); it is an integrity primitive, **not** a compliance certification, and the signature is
load-bearing only against a party who does not hold `receipt_key`. Prior art: crypto-shredding; Cassandra /
event-sourcing tombstones; GDPR Art. 30 erasure logs; Crosby-Wallach / Certificate-Transparency
tamper-evident logs. Receipt: `mnemo/probes/forget_subject_tombstone_probe.py` (8/8).

## Framework integrations

mnemo drops into the major agent frameworks as their **native memory type**, so an agent gets value-ranked
recall plus **correction-integrity** (a corrected fact does not resurrect on a later read) without changing its
code. Each adapter lives under `mnemo.integrations.*` and is an opt-in extra — `import mnemo` stays
zero-dependency, and the framework is imported lazily only when you use its adapter.

| framework | mnemo class | install |
|---|---|---|
| [OpenAI Agents SDK](#drop-in-memory-for-the-openai-agents-sdk-mnemosession-0620) | `MnemoSession` | `pip install "agora-mnemo[openai-agents]"` |
| [AutoGen](#current-truth-memory-for-autogen-mnemomemory-070) | `MnemoMemory` | `pip install "agora-mnemo[autogen]"` |
| [LangGraph / LangMem](#langgraph-store-with-queryable-history-mnemostore-071) | `MnemoStore` | `pip install "agora-mnemo[langgraph]"` |
| [LlamaIndex](#current-truth-long-term-memory-for-llamaindex-mnemomemoryblock-073) | `MnemoMemoryBlock` | `pip install "agora-mnemo[llamaindex]"` |
| [Google ADK](#persistent-memory-for-google-adk-mnemomemoryservice-074) | `MnemoMemoryService` | `pip install "agora-mnemo[google-adk]"` |
| [Pydantic AI](#memory-as-tools-for-pydantic-ai-mnemo_toolset-078) | `mnemo_toolset` | `pip install "agora-mnemo[pydantic-ai]"` |

Details for each below.

### Drop-in memory for the OpenAI Agents SDK: `MnemoSession` (0.6.20+)
`mnemo.integrations.openai_agents.MnemoSession` is a persistent [`Session`](https://openai.github.io/openai-agents-python/sessions/)
backend — the same slot `SQLiteSession`/`RedisSession` fill — so agent conversations survive restarts:

```python
from agents import Agent, Runner
from mnemo.integrations.openai_agents import MnemoSession
session = MnemoSession("user-42", path="sessions.json")   # one store can hold many sessions
Runner.run_sync(agent, "hi", session=session)
```

It faithfully implements the protocol (`get_items`/`add_items`/`pop_item`/`clear_session`, verbatim items,
`limit`=latest-N, multi-session isolation) and needs **no dependency** — the SDK is matched structurally,
never imported. **Honest scope:** a `Session` is a verbatim turn log, so mnemo's supersession/echo_guard
(which key on *facts*) don't auto-clean replayed messages — for poison-resistant fact memory use mnemo's core
`remember(key=…)`/`recall()` alongside. What it adds *for free* over a plain SQLite session: **right-to-erasure**
of a user's turns with a signed, content-free deletion tombstone (`session.forget_subject()`), and
**tamper-evident** history (`store.verify_writes()` with receipts enabled). Receipt:
`mnemo/probes/mnemo_session_adapter_probe.py` (11/11). Adapters live under `mnemo.integrations` (opt-in extras).

### Current-truth memory for AutoGen: `MnemoMemory` (0.7.0+)
`mnemo.integrations.autogen.MnemoMemory` implements AutoGen's [`Memory`](https://microsoft.github.io/autogen/stable/user-guide/agentchat-user-guide/memory.html)
protocol (`add`/`query`/`update_context`/`clear`/`close`) — and here mnemo's value is not incidental. Unlike a
verbatim `Session`, AutoGen `Memory` retrieves facts and injects them before each turn, so **`recall()` hiding
superseded values means the agent is grounded on current-truth, not on a stale value a later correction
already retired**:

```python
from autogen_agentchat.agents import AssistantAgent
from mnemo.integrations.autogen import MnemoMemory
mem = MnemoMemory(path="mem.json")
agent = AssistantAgent("assistant", model_client=..., memory=[mem])
```

Pass a stable `key` (+ `object`) in a memory's `metadata` to drive deterministic supersession — a later
`key="user::timezone", object="PST"` retires an earlier `UTC`, and `update_context` then injects only `PST`.
Verified end-to-end against the real `autogen-core` (`mnemo/probes/mnemo_autogen_adapter_probe.py`, 7/7,
including "superseded value is not injected"). Zero-dependency core: AutoGen is imported lazily inside the
adapter, never by `import mnemo`.

### LangGraph store with queryable history: `MnemoStore` (0.7.1+)
`mnemo.integrations.langgraph.MnemoStore` is a LangGraph [`BaseStore`](https://langchain-ai.github.io/langgraph/reference/store/)
(faithful `put`/`get`/`search`/`delete`/`list_namespaces` + `batch`/`abatch`) — and since LangMem sits on any
BaseStore, one adapter reaches both. Same last-write-wins semantics as the built-in `InMemoryStore`, plus the
thing it throws away: **history**. A second `put` on a key overwrites the first in `InMemoryStore` and the old
value is gone; `MnemoStore` keeps it on mnemo's supersession ledger, so `store.history(namespace, key)` returns
every value the key has held — plus point-in-time reads, tamper-evident receipts, and `forget_subject` erasure.

```python
from mnemo.integrations.langgraph import MnemoStore
store = MnemoStore(path="lg.json")
store.put(("user","42"), "timezone", {"tz": "UTC"}); store.put(("user","42"), "timezone", {"tz": "PST"})
store.get(("user","42"), "timezone").value    # {"tz": "PST"}   (like InMemoryStore)
store.history(("user","42"), "timezone")       # [{"tz":"UTC"}, {"tz":"PST"}]   (mnemo-only)
```

Verified end-to-end against real `langgraph` (`mnemo/probes/mnemo_langgraph_adapter_probe.py`, 9/9, incl. the
"InMemoryStore has no history" contrast). Subclasses BaseStore, so importing this module imports LangGraph
(opt-in extra); `import mnemo` stays zero-dependency.

### Flag conflicts before you trust the write: `check_conflict()` (0.7.2+)
Practitioners keep landing on the same move: stop trusting the write path, check each new fact against what's
already stored, and flag conflicts *before* they commit. `check_conflict(text, key=…, object=…)` does that,
read-only and with no LLM: it returns the active memories the new fact would contradict — a value change on a
managed `key`, or a numeric/negation clash with a similar memory — so you can gate, review, or reject the write
before calling `remember()`.

```python
m.remember("the retry limit is 5 attempts")
m.check_conflict("the retry limit is 12 attempts")   # -> [{'kind': 'clash', ...}]  (numeric update)
m.check_conflict("the retry limit is 5 attempts")    # -> []  a duplicate is NOT a conflict
```

The signal is a value/negation clash, **not** cosine similarity — which is the whole point: a corrected value
is often *more* embedding-similar to the original than a rephrase (AUROC ~0.59 at telling them apart), so a
"too similar, must be a dup" gate silently swallows the contradiction. Pass `incompatible(a, b) -> bool` (e.g.
an LLM judge) to also catch a purely semantic contradiction with no numeric/negation marker. The mechanism is
textbook (a DB CHECK-constraint validate-on-write; TMS contradiction-on-assert, Doyle 1979) — here it's a
native, zero-dependency primitive. Also exposed as the `check_conflict` MCP tool. Receipt:
`mnemo/probes/check_conflict_probe.py` (8/8).

### Current-truth long-term memory for LlamaIndex: `MnemoMemoryBlock` (0.7.3+)
`mnemo.integrations.llamaindex.MnemoMemoryBlock` is a LlamaIndex long-term [`BaseMemoryBlock`](https://developers.llamaindex.ai/python/framework/module_guides/deploying/agents/memory/)
(async `_aget`/`_aput`), so it sits alongside the built-in Static/FactExtraction/Vector blocks on a `Memory`:

```python
from llama_index.core.memory import Memory
from mnemo.integrations.llamaindex import MnemoMemoryBlock
memory = Memory.from_defaults(session_id="s1", token_limit=40000,
                              memory_blocks=[MnemoMemoryBlock(name="mnemo", path="mem.json", k=5)])
```

Same differentiator as the AutoGen block: `_aget` retrieves through mnemo's `recall()`, which hides superseded
values, so once a fact is corrected (via a keyed write) the block never injects the stale value back into the
prompt. Verified end-to-end against real `llama-index-core`
(`mnemo/probes/mnemo_llamaindex_adapter_probe.py`, 4/4, incl. "corrected value not re-injected"). Subclasses
BaseMemoryBlock so importing it imports LlamaIndex (opt-in extra); `import mnemo` stays zero-dependency.

### Persistent memory for Google ADK: `MnemoMemoryService` (0.7.4+)
`mnemo.integrations.google_adk.MnemoMemoryService` is a drop-in Google ADK [`BaseMemoryService`](https://google.github.io/adk-docs/sessions/memory/)
(`add_session_to_memory` / `search_memory`), backed by a mnemo store so memory persists and retrieval is
value-ranked lexical+semantic instead of the built-in word-overlap:

```python
from google.adk.runners import Runner
from mnemo.integrations.google_adk import MnemoMemoryService
runner = Runner(agent=agent, app_name="app", session_service=...,
                memory_service=MnemoMemoryService(path="mem.json"))
```

Two honest extras over `InMemoryMemoryService`: `search_memory` goes through supersession-filtered `recall()`
(a corrected keyed fact is not returned), and `forget_subject_for(app_name, user_id, request_id=…)` gives
per-user right-to-erasure with a signed deletion tombstone. Verified end-to-end against real `google-adk`
2.4.0 (`mnemo/probes/mnemo_adk_adapter_probe.py`, 4/4, incl. per-user isolation, current-truth, and
accounted-for erasure). Opt-in extra; `import mnemo` stays zero-dependency.

### Memory-as-tools for Pydantic AI: `mnemo_toolset` (0.7.8+)
Pydantic AI ships no built-in persistent memory by design; the pattern (Hindsight's `hindsight-pydantic-ai`,
etc.) is to expose memory as agent tools. `mnemo.integrations.pydantic_ai.mnemo_toolset` returns a
[`FunctionToolset`](https://ai.pydantic.dev/toolsets/) the agent can call — `remember`, `recall`,
`check_conflict`, `forget`:

```python
from pydantic_ai import Agent
from mnemo.integrations.pydantic_ai import mnemo_toolset
agent = Agent("openai:gpt-4o-mini", toolsets=[mnemo_toolset(path="mem.json")])
```

The differentiators the built-in "give the model a scratchpad" pattern lacks: `recall` is
supersession-filtered (a corrected value stops surfacing, so the agent reads current-truth), and
`check_conflict` lets the agent test a fact for a contradiction with what is already stored BEFORE it commits
it. Pass `extractor=` so the tools auto-key free text (so both supersession and conflict-detection fire
without the model supplying a key). Verified end-to-end against real `pydantic-ai` 2.8.0 with `TestModel` (no
API key): the agent invokes all four tools, and current-truth / conflict / erasure all hold
(`mnemo/probes/mnemo_pydantic_ai_adapter_probe.py`). Importing this module imports Pydantic AI (opt-in
extra); `import mnemo` stays zero-dependency.

### Make the governance layer key itself over free text: the `extractor` hook (0.7.5+)
mnemo's supersession, `echo_guard`, `check_conflict`, and `forget_subject` all key on the `(key, object)` of a
fact. That's great when you write structured facts, but a conversation `Session` or a chat turn is free text
with no key, so supersession never fires on it. Plug an `extractor` once and every `remember()` derives the
key for you, so the whole governance layer composes over free text with no per-call keying:

```python
import re
m.extractor = lambda t: (m := re.match(r"(.+?) is (\w+)", t)) and (f"fact::{m[1].strip()}", m[2])
m.remember("server timezone is UTC")
m.remember("server timezone is PST")   # same derived key -> supersedes UTC, no manual key=
m.recall("server timezone")            # -> PST only
```

Your extractor can be a regex or an LLM you call and cache; it returns `(key, object)` or `None`. Explicit
`key=`/`object=` always win, and a broken extractor fails open (the write still lands as a plain append).
Honest limit: supersession is only as sound as your extractor, so a mis-derived key mis-supersedes (the same
risk as a wrong manual `key=`) — keep it deterministic and reviewable. This is a before-save hook (DB trigger
/ ORM before_save; textbook) packaged so the integrity primitives compose without threading keys everywhere.
Receipt: `mnemo/probes/extractor_hook_probe.py` (7/7).

The free-text framework adapters (OpenAI Agents `Session`, AutoGen `Memory`, LlamaIndex `BaseMemoryBlock`,
Google ADK `MemoryService`, Pydantic AI `mnemo_toolset`) accept `extractor=` and wire it into their store, so
plugging it once makes their current-truth recall fire automatically over conversation turns:

```python
mem = MnemoMemory(path="mem.json", extractor=my_extractor)   # AutoGen; same for the others
```

Verified against real `autogen-core` (`mnemo/probes/extractor_adapter_wireup_probe.py`): without the extractor
a corrected fact still leaks; with it, only the current value is recalled.

### Data minimization: `apply_retention(max_age_days)` (0.7.7+)
The age-bound companion to `capacity=` (size bound) and `forget_subject` (subject erasure), for the GDPR
storage-limitation principle: don't keep data longer than you need it. `apply_retention(days)` hard-deletes old
memories, but never the current value of a key and never a graduated `semantic`/`procedural` fact, those are
the live state, not stale accumulation. By default it drops old *superseded* values (minimizing retained PII,
which trades off `as_of()` history for those intervals, your call via `drop_superseded`) and old un-keyed
*episodic* turns. Run it directly, or on idle via `sleep(retention_days=90)`.

```python
m.apply_retention(max_age_days=90)     # or: m.sleep(retention_days=90)
```

Textbook (DB TTL / log retention), packaged as a native zero-dependency retention primitive. Receipt:
`mnemo/probes/retention_probe.py` (7/7, incl. "current keyed value and semantic facts are never expired").

### One-call write router with revert resolution: `route()` (0.7.9+)
"Go back to what we had before" names no value, so a value-keyed store has nothing to match and cosine has
nothing to grab — it is an unresolved pointer, not a similarity failure. `route(text)` ships the two-job split
for exactly this: a deterministic, ledger-aware intent tagger (assert / correct / revert / echo) in front, and
a fuzzy-version resolver behind it ("back / the way it was" → the predecessor via `revert()`; "the original /
what we started with" → the first version; a named old value → that version) — so a revert executes on the
version graph through the sanctioned reaffirm channel, and similarity never runs on a revert:

```python
m.route("the cache region is osaka", key="cache region", object="osaka")
m.route("correction: the cache region is now malmo", key="cache region", object="malmo")
m.route("go back to what we had for the cache region")   # no value named -> restores osaka from the ledger
```

Measured (`mnemo/probes/route_probe.py`, 148 rows): every *marked* class — corrections, value-obscuring
reverts, named reverts, original-restores, innocent temporal chatter — routes at 1.00 end-to-end under every
policy, with zero LLM (LLM taggers measured on the same rows add nothing: 1.00 on marked classes too). The
honest limit is measured rather than hidden: an UNMARKED restatement of a superseded value is ambiguous by
construction (a stale echo and a deliberate reaffirm can be byte-identical; LLMs land at ~coin-flip 0.35–0.55),
so `policy=` picks the failure mode — `safe` (default) never restores on an unmarked restatement
(echo-blocked 1.00 / legit-reaffirm-honored 0.00), `context` separates honest twins via the preceding turn
(1.00/1.00) but is forgeable (a forged change-aware context walks through it), `trusting` always restores
(0.00/1.00). The unforgeable separator is provenance — the explicit `revert()` channel or a revert marker —
not smarter classification. Also an MCP tool (`route`).

### Authorized revert channel: stop content from undoing a correction (0.7.10+)
A value-obscuring "go back to what we had" and a stale echo are byte-identical, so — as a sharp r/RAG thread
put it — the tie-break is an *authentication* problem, not an NLP one: it cannot come from the text, only from
an authority whose origin an attacker who can write text cannot author. Opt in and `route()`/`revert()` require
an out-of-band **capability** before they will restore a superseded value:

```python
from mnemo import Mnemo, new_receipt_keypair, sign_revert

# symmetric (zero extra deps): the harness holds a secret; the content path can't mint the capability
m = Mnemo(path="mem.json", revert_authority="a-harness-side-secret")
m.route("go back to what we had for the region", policy="trusting")   # -> action="authorization_required"
m.revert("region", capability=m.revert_capability("region"))          # principal path executes

# asymmetric (closes the residual: even a compromised on-box harness can't mint):
sk, pk = new_receipt_keypair()                 # private key stays OFF the box, store holds only pk
m = Mnemo(path="mem.json", revert_pubkey=pk)
cap = sign_revert(sk, m.revert_challenge("region"))   # only the off-box private key can produce this
m.revert("region", capability=cap)
```

With an authority set, a text-derived revert never executes — `route()` returns `authorization_required` and
the principal confirms out of band; `remember(reaffirm=True)` is gated the same way, so the raw primitive can't
bypass it. The capability binds to the key and the current record (`revert_challenge`), so a captured one can't
be replayed after the value moves or retargeted to another key. Textbook capability security (Dennis & Van Horn
1966) / confused-deputy fix (Hardy 1988), packaged onto the memory store's revert path. Honest boundary: this
closes the content→restore path (and, in asymmetric mode, the on-box-harness→restore path); it does not stop a
stolen private key or authenticate a human. Adversarial receipt: `mnemo/probes/authorized_revert_probe.py`
(11/11: content blocked, harness-can't-mint, replay/retarget/forgery refused, principal path works).

## Use it as an MCP server (any Claude / Cursor / agent client)

`mnemo` ships an [MCP](https://modelcontextprotocol.io) stdio server so any MCP-compatible agent can
use it as long-term memory — `remember` (with a per-type decay prior), value-ranked `recall`,
`consolidate`, `consolidate_clusters`, `contradictions`, `value_by_cohort`, `forget` (verified erasure).
Correction is first-class over MCP too: `revert` / `route` undo a correction on an unmarked "go back", and the
read-path review layer `observe` / `reopened` / `resolve_reopened` (1.9.2–1.9.5) reopens a settled record for
steward review on a *corroborated* contradiction (a lone restatement stays an echo, never an auto-change).
The MCP `remember` exposes `key` (deterministic supersession) plus `object` / `reaffirm`, and the server
runs with **`echo_guard` ON by default** (0.6.11) so a corrected fact stays corrected even if the old value
is re-stated later — the failure mode a plain keyed/add-based store shows on RAMR's ECHO-RESISTANCE
(keyed-without-guard 0.00, a real add-based system 0.57, guard 1.00). Set `MNEMO_ECHO_GUARD=0` to disable.
Install and run the server straight from PyPI (the `[mcp]` extra pulls the MCP SDK; the core library stays
dependency-free):

```bash
pip install "agora-mnemo[mcp]"     # the library + the MCP server SDK
mnemo-mcp                          # speaks MCP over stdio
```

Register it with any MCP client — Claude Code (`.mcp.json`), Claude Desktop
(`claude_desktop_config.json`), Cursor, Windsurf, Codex, Gemini. Zero-setup with `uvx` (installs on first run):

```json
{
  "mcpServers": {
    "mnemo": {
      "command": "uvx",
      "args": ["--from", "agora-mnemo[mcp]", "mnemo-mcp"],
      "env": { "MNEMO_PATH": "./mnemo_memory.json" }
    }
  }
}
```

Or, after `pip install "agora-mnemo[mcp]"`, with the console script directly:

```json
{
  "mcpServers": {
    "mnemo": {
      "command": "mnemo-mcp",
      "env": { "MNEMO_PATH": "./mnemo_memory.json" }
    }
  }
}
```

For **semantic** recall, point it at any OpenAI-compatible embeddings endpoint via
`MNEMO_EMBED_URL` / `MNEMO_EMBED_MODEL` / `MNEMO_EMBED_KEY`; with none set it uses the lexical
fallback. The agent then calls `recall(query)` before reasoning and `remember(fact)` as it learns —
its memory is value-ranked and append-only, not a recency buffer.

## The four operations

| op | what it does |
|---|---|
| `remember(text, tags, value, mtype, key)` | **append-only** raw capture, absolute UTC time, never edited; `mtype` ∈ {episodic, semantic, procedural} sets the **decay prior** (events fade fast, durable facts slow, rules barely). Optional `key` = a **deterministic (subject, relation) supersession key**: a new value retires every active record with the same key — *no similarity threshold, no LLM* — so recall never serves the stale value (bi-temporal: a back-filled earlier value can't overwrite the current one) |
| `recall(query, k, where=…)` | **value-ranked** retrieval: relevance × value, **decayed by the memory's per-type half-life** (access resets the clock), so important durable memories beat both merely-similar and stale ones. Optional `where` = a **metadata pre-filter** (the cheap *filter-before-you-rank* lever): field → scalar / list / operator (`$gte $lte $gt $lt $in $nin $ne $contains`), matched top-level then `meta`, ALL fields AND-ed — e.g. a hard time-range `where={"valid_from":{"$gte":t0,"$lte":t1}}` or a closed-set entity `where={"speaker":{"$in":[…]}}`. Measured to beat retriever choice on LoCoMo (`probes/locomo_metadata_prefilter.py`); it's a HARD filter, so on lossy/predicted extraction keep it loose (a wrong filter hard-deletes the answer). Reinforcement is **relevance-weighted** (a bullseye hit reinforces value more than one that squeaked into top-k, so a weak-but-frequent false positive can't go immortal); a repeatedly-recalled episodic memory **graduates** to semantic **only when corroborated** — by an earned outcome, or by **≥2 distinct *canonical* sources** (entity-resolved before counting, so sybil variants of one origin — `Wikipedia` / `wikipedia.org` / a full URL — collapse to one and can't mint durability); and a memory whose source was later contradicted is **provenance-demoted** + flagged `stale_derived` |
| `consolidate(keep)` | the **dream pass**: flag universal-matcher *hubs*, link near-duplicates, apply the **state-toggle guard** (a polarity clash supersedes, doesn't merge), supersede the low-value surplus — only *adds* a derived layer |
| `consolidate_clusters(threshold)` | **cluster-triggered** consolidation: consolidate a semantic cluster only once it's grown past `threshold` — sparse topics keep their raw episodes, dense ones don't grow unbounded |
| `contradictions()` | flag mutually-incompatible **related** memories (similarity-gated) for human review |
| `forget(ids, where)` | the one op that **truly deletes** (the rest is append-only): hard-removes the matched records *and* scrubs their ids from every survivor's links + toggle pointers + the vec/token caches, so a forgotten memory can't resurface via recall, a consolidation link, or the dream pass. For erasure / right-to-be-forgotten, poison removal, or a hard correction — measured 15/15 on a verified-forgetting severe-test |

## Five rules it won't break (each one cost us to learn)

1. **Raw capture is immutable.** Consolidation adds links and markers; it never overwrites the
   source. This is what stops the slow accuracy drift of LLM-rewritten memory.
2. **Absolute timestamps at write time.** Relative/derived times rot the moment they're consolidated.
3. **Value-ranked, type-aware decay.** Retention is `value × a per-type half-life`, not recency or
   access-frequency alone. A *uniform* access-reset clock keeps merely-*popular* memories while a
   load-bearing-but-cold fact — queried once a month, prevents a destructive action — starves; we
   measured exactly that failure. The fix is that the half-life is set by **kind**, not by read
   count: episodic events fade in days, semantic facts in months, procedural rules barely at all. A
   cold-but-critical fact survives by being **typed** semantic/procedural (long half-life × its high
   value), not by frequent reads; access only resets the clock *within* a type's window.
4. **Value is reported at the cohort level** (tag / time-block), never per-memory.
5. **Contradictions are flagged, never auto-resolved.** Silent rewrites destroy trust in the whole
   memory.

## Provenance — why these rules, with receipts

<details>
<summary>Why these rules — the measured receipts behind mnemo's design. Click to expand.</summary>

`mnemo`'s design isn't taste; it's what Agora's lab *measured*:

- **Semantic recall beats keyword recall, and the gap widens with scale** — as the store grows to
  the ~6,000-note full corpus, lexical `recall@5` decays from **0.94** (small store) to **0.25**,
  while semantic **holds at ~0.65** — ≈**2.6×** at full scale (Agora Lab `b4c260`); on paraphrase
  queries semantic `recall@5` is **0.86 vs 0.20** lexical (`3501f1`). The embedder is the real lever
  at scale; the lexical overlap match is the zero-dependency *floor* that still runs anywhere on a
  small store. (Honest footnote: pruning
  universal-matcher *hub* notes lifts **lexical** recall ~20% only when a store is link-spammed, and
  does **not** move semantic recall — it's a lexical/hybrid optimisation, not a headline.)
- **Value-ranked consolidation** — under a keep-budget, ranking *what to keep* by value beats
  FIFO/random, and the advantage **scales super-linearly as the budget shrinks** (≈1.8× at half
  budget → ≈4× at one-eighth), surviving heavy estimation noise.
- **Retention must blend value with recency, not decay on access alone** — we simulated a
  half-life-with-access-reset policy (a *popularity* signal) against a value-aware blend under a
  shrinking budget, with value made deliberately anti-correlated with access-frequency for a
  load-bearing-but-cold subset. At a 30% keep-budget the access-decay policy retained only **2.8%**
  of the high-value/low-frequency memories and **20%** of total value, vs **100%** and **64%** for
  the blend — about **3× more value kept** (the gap persists, ≈2.2× retained value, even at a 7%
  budget). Pure access-frequency decay starves the rarely-queried-but-critical memories; forgetting
  must consume an explicit value channel *separate from* access recency. (Agora Lab `19d802`.)
- **Supersession needs a deterministic key, not embedding similarity** — replicating an external
  result (MemStrata / Yadav, arXiv 2606.26511) on our own local `nomic` stack: a cosine-similarity
  classifier separating a *contradicted* fact from a *rephrased duplicate* scores **AUROC ~0.61**
  (near chance) — a contradiction is often *more* embedding-similar to the original than a true
  rephrase is. A similarity-based store therefore serves the **stale value ~42% of the time**; the
  deterministic `(subject, relation, object)` supersession key (`remember(..., key=...)`) drives that
  to **0%** (Agora Lab `exp_supersession_replication`, severe-test 8/8). This is *why* supersession is
  a key, not a threshold.
- **No single recall mechanism survives all operating points — only the layered store does** —
  head-to-head on a synthetic *evolving + contaminated* stream (stable / superseded / poisoned facts,
  local `nomic`): a naive **cosine top-1** store scores **42%** (fine on stable, but blind to
  supersession — **0/8** on updated facts — and fooled by repeated lies); a **recency** store **67%**
  (fixes supersession but serves the *freshest lie* — **0/8** on poison); `mnemo` — deterministic
  supersession key **+** corroboration gate **+** value-ranking — is **100%**, robust across all three.
  Each single mechanism wins one regime and loses another (the *memory operating-point trap*), which is
  why the durable layer needs all three together (probe `mnemo/probes/operating_point_memory.py`).
- **Cohort-level value** — per-memory outcome attribution is **statistically underpowered at n-of-1**
  (the best proxy reached only ~0.36 power at realistic sample sizes); the cohort is where the
  signal lives. Hence rule 4.
- **Contradiction detection** runs in production over the 6,000-note vault; the lesson that it must
  *flag, not auto-edit* (rule 5) is why silent rewrites are forbidden.

(Methods + numbers live in the Agora track record: <https://dancenitra.github.io/agora/>.)

</details>

## Threat model & layered defense (adversarial memory integrity)

<details>
<summary>The full adversarial threat model + layered defenses. Click to expand.</summary>

An untrusted-ingestion memory store cannot decide whether a written claim is *true*. mnemo doesn't try to;
it makes the attacker **pay**, and the honest map of what each layer buys — worked to bedrock across a public
practitioner thread with adversarial review — is below. Every claim here has a runnable receipt in
[`mnemo/probes/`](probes/); this is textbook mechanism with a receipt, **not** a new theory.

**A defense the attacker can also write is a suggestion, not a defense.** Content-declared provenance is
theater: `Source: X` and `corroborated by N` are strings a writer controls, so default (distinct source
*strings*) corroboration falls to a sybil that mints two labels (~0.9 attack-success across 10 models —
[`memory_defense_layer_probe`](probes/memory_defense_layer_probe.py)). Only channels the writer does **not**
control hold — distinct *verified keys* (`strict_corroboration`, Ed25519 `attest`) **whose issuance is itself
costly/rate-limited** (a free-to-mint key is just another string a sybil spends), an *earned* Beta(good,bad)
outcome credit a session can't self-grant, and *system write-history*.
That is Biba integrity (1977) / Cheng-Friedman (2005): no symmetric reputation is sybilproof; the escape is
an exogenous, un-writable anchor.

**The layers, and the exact residual each leaves:**
- **Provenance — did the call happen?** Bind standing to a *runtime* signature over the real `(tool, result)`,
  not the session's log ([`execution_receipt_gate`](probes/execution_receipt_gate_probe.py)). Closes fabricated
  logs **iff the executor is a trust domain separate from the session**; in-process tools → the key is
  session-readable → theater.
- **Veracity — is a real call's output true?** No write-time oracle (a novel fact has no priors; attestation =
  authorship, not truth). Standing is *earned at use time* from an observed outcome
  ([`veracity_outcome_grounding`](probes/veracity_outcome_grounding_probe.py)) — only helps where harm is observable.
- **The irreversible tail.** A provenance-scaled `spend_irreversible` budget makes a low-provenance memory bind an
  honest action's irreversible spend against itself ([`soft_influence_taint`](probes/soft_influence_taint_probe.py));
  a reversibility checkpoint bounds a one-shot defector. Bounds, doesn't close (Sybil relocates it per identity).
- **The sub-threshold coordinated campaign.** A temporal change-detector is evaded for free at bias `h→0`, but
  `B≈N·h` forces a growing correlated low-provenance population a **cross-sectional** detector catches
  ([`two_detector_trap`](probes/two_detector_trap_probe.py)); and randomized retrieval raises a *targeted*
  attacker's coverage from O(1) to ~Θ(fN) with no anchor
  ([`moving_target_retrieval`](probes/moving_target_retrieval_probe.py)).

**The bedrock.** Checked from ~8 directions (detection, reputation, elicitation-without-ground-truth à la Bayesian
Truth Serum, robust aggregation, moving-target), every layer we examined reduces to one of two exogenous anchors —
a bounded corruption *count* (Byzantine; sybils break it) or a shared attacker-independent *prior* (peer-prediction;
a coalition coordinating its reports breaks it). *(A synthesis over those cases, not a proof.)* You cannot separate
a large coordinated coalition from genuine consensus from internal signals alone (Cheng-Friedman +
Lamport-Shostak-Pease 1982; and no internal truth-oracle, by analogy to Tarski's undefinability). What that
leaves is not "give up" but a shape: **localize the one exogenous check at the rare high-consequence irreversible
step** (a human, a separately-provenanced feed — a channel the poison can't reach), and **don't let evidence-free
consensus drive an irreversible action** (weight it ~0; on an observable target, require an independent evidentiary
provenance, which is super-linear to forge, not N reputations). The residual is the integrity of that one minimal
anchor — a standard, bounded problem, not the intractable verify-all-memory one.

Prior art credited throughout: Biba 1977 · Douceur 2002 · Cheng-Friedman 2005 · Friedman-Resnick 2001 ·
Lamport-Shostak-Pease 1982 · Lorden 1971 / Moustakides 1986 (CUSUM delay floor) ·
Tarski (undefinability of truth, used by analogy) · Doyle 1979 (truth-maintenance) · Garcia-Molina & Salem 1987 (Sagas) ·
Prelec 2004 (Bayesian Truth Serum) · Blanchard 2017 / Yin 2018 (Byzantine-robust aggregation) ·
PoisonedRAG (Zou 2024) · MINJA (Dong, arXiv:2503.03704) · AgentPoison (Chen, arXiv:2407.12784) ·
the shilling / Sybil-detection line (Mobasher-Burke 2007, Mehta-Nejdl 2009, SybilRank/Cao 2012, Viswanath 2010).

</details>

## The `second_brain` thinking layer

<details>
<summary><strong>Optional add-on</strong> — a separate MCP server that reasons over a folder of Markdown notes. Click to expand.</summary>

`mnemo_mcp` gives an agent **memory**. `second_brain_mcp` gives it a **second brain to think over** —
point it at any folder of Markdown notes (an Obsidian vault, a Zettelkasten, a `docs/` tree) and an
MCP client (Claude Desktop, Claude Code, Cursor, your own agent) gets the substrate to *reason
against* those notes: pull what's relevant, find where the network is blind, surface non-obvious
bridges, isolate the claims worth checking, and generate ideas by named methods.

**The split that keeps it honest.** The server returns **retrieval + structure**; the calling LLM does
the **reasoning**. The tool is the memory and the map; the agent is the mind. There is no LLM call
inside this server — it scores, links, and slices your notes, then hands the material back. So the
claims below are about what an *agent* did with the tools, not about the tool "thinking" on its own.
No autonomous oracle.

**Runs today, zero config.** It indexes your notes into an in-process `mnemo` store at startup; with
no embedder it uses the lexical-overlap fallback. An embedder (`MNEMO_EMBED_URL/MODEL/KEY`) is optional
and matters **at scale**: on a ~6,000-note vault, lexical recall@5 decays from 0.94 (small store) to
**0.25** at full corpus while semantic **holds ~0.65** — ≈2.6× (Agora Lab `b4c260`); on paraphrase
queries semantic recall@5 is **0.86 vs 0.20** lexical (`3501f1`).

```
NOTES_DIR=/path/to/your/vault python second_brain_mcp.py      # run after a flat download of both files
```

### See it run (no setup)

![second_brain demo — your notes, thinking](../examples/demo.gif)

`python examples/demo.py` runs every tool against a tiny bundled sample vault — no MCP client, no
key, no embedder. (Regenerate the GIF with `python examples/_make_gif.py` (Pillow) or
[`examples/demo.tape`](../examples/demo.tape) + [`vhs`](https://github.com/charmbracelet/vhs).)
The same session in text:

```text
▸ relevant_notes("how does feedback speed up learning", k=3)
  → Deliberate Practice (Learning)   relevance 0.60
  → Expected Value     (Decisions)   relevance 0.20

▸ find_gaps()              → isolated: ["Sourdough Starter"]   (the one note with no [[links]])

▸ bridge_candidates("Deliberate Practice")
  → Habit Loops (Habits, DISTANT domain)   — both turn on "feedback latency", and nothing links them

▸ extract_claims("Deliberate Practice")
  → "Feedback latency is the hidden variable: the longer the gap between an action
     and its feedback, the slower the learning."   (line 3 — go ground or challenge it)

▸ idea_methods()           → 10 recipes (Hidden-Connection Bridge, Missing-Reciprocity, …)
```

That `bridge_candidates` hit is the point: a connection across two folders that *you never linked* —
the agent now writes the mapping (or rejects it). The tool found the material; the agent does the thinking.

Register it with an MCP client (point `args` at the file's absolute path so `mnemo.py`, which sits
beside it, is found):

```json
{
  "mcpServers": {
    "second_brain": {
      "command": "python",
      "args": ["/abs/path/to/second_brain_mcp.py"],
      "env": {
        "NOTES_DIR": "/abs/path/to/your/vault",
        "SECOND_BRAIN_INDEX": "/abs/path/to/second_brain_index.json"
      }
    }
  }
}
```

| tool | returns |
|---|---|
| `index_status` | notes indexed, folder spread, resolved `NOTES_DIR` (call first; `0` ⇒ fix `NOTES_DIR`) |
| `relevant_notes` | the `k` most relevant notes by relevance × accrued value (value accrues with use; a cold index is effectively relevance-ranked), with excerpts |
| `coverage_gap` | the **negative space** of a question: top notes + a measured completeness score + the explicit sub-terms with **no** supporting note — a WYSIATI guard so the agent sees what's *missing* and doesn't answer a tidy-but-incomplete context with false confidence |
| `find_gaps` | isolated/under-linked notes + thin folders — where the network is blind (noisy on a tiny vault; earns its keep at scale) |
| `bridge_candidates` | distant notes (different folder, no link) that are semantically close = candidate connections; the agent writes or rejects the mapping |
| `extract_claims` | claim-like sentences from a note so the agent can ground or challenge them |
| `idea_methods` | a toolkit of named idea-generation recipes, so generation is principled, not a vibe |

Dogfood result, stated honestly: pointed at the maintainer's own ~6,000-note vault, an agent using
these tools caught a number in his *own* forecasting note inflated ~7× ("60-78%" vs the real ~6-11%),
surfaced two silently-contradicting notes, and proposed ideas via `idea_methods` — two of which were
then severe-tested **in Agora's separate research lab** (not inside this server) and held. The LLM did
the reasoning; the corrections still warrant a source-check before public citation.

### Trust & safety
- **Read-only over your notes.** The server reads `NOTES_DIR` recursively; it does no `eval`, no shell,
  no subprocess, and writes only its own index file. Symlinks/junctions that point *outside*
  `NOTES_DIR` are deliberately **not** followed (so a planted link in a shared/cloned vault can't leak
  files from elsewhere on disk).
- **The embedder is a trust boundary.** If you set `MNEMO_EMBED_URL`, the **full text of every note**
  is POSTed there. It's validated at startup — `https` anywhere, plain `http` only to loopback (local
  Ollama, etc.), and cloud-metadata/link-local targets are refused. Point it only at an endpoint you trust.
- **Notes over ~2 MB are skipped** (configurable via `SECOND_BRAIN_MAX_BYTES`) so a single huge file
  can't exhaust memory.

</details>

## Status

`v0.2` — the core, honest and runnable, **now with two MCP servers** (`mnemo_mcp` for memory,
`second_brain_mcp` for the thinking layer over your notes) **and a deterministic supersession key**
(`remember(..., key=...)`) that closes the embedding *supersession blind spot*. Roadmap: pluggable
vector stores, a hosted tier. Open-core; the core stays free.

MIT-licensed · part of [Agora](https://github.com/DanceNitra/agora).

## Self-maintaining (maintain.py)
The #1 second-brain frustration is **maintenance**, not capture. `maintain.py` runs the chore people
stop doing — over a folder of Markdown notes it finds **dead `[[wikilinks]]`, orphan notes, stale
notes, near-duplicate clusters**, and a **vault health score** (`self_legibility` = % of notes in the
link graph's giant component — knowledge debt is a *percolation* collapse, so it warns *before* the
cliff). Crucially it turns findings into **actions**: for each orphan it **suggests which existing
note to link it to** (re-connecting it to the graph), and flags **archive candidates** (old +
isolated). It resolves links by filename *or* frontmatter alias, and dates notes by frontmatter
(not git-reset mtime) — both learned from dogfooding it on a real ~7,700-note vault (it rescued ~300
falsely-flagged orphans). Advisory + safe: it returns a plan and an action list; it never edits,
moves, or deletes a note. And it can **apply** the fix when you ask: `apply_suggestions` appends a
marked `## Related (auto-suggested)` block of `[[links]]` to each orphan — additive only, idempotent
(re-running replaces its own block), **dry-run by default**. `python maintain.py` runs a verified
round-trip on a synthetic vault (diagnose → suggest → apply); `maintenance_report` and `apply_links`
in `second_brain_mcp.py` expose it to any MCP agent.

<!-- MCP registry ownership proof -->
mcp-name: io.github.DanceNitra/mnemo
