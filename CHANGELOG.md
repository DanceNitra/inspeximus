# Changelog

All notable changes to mnemo (`agora-mnemo`). Format loosely follows Keep a Changelog; versioning is semver
(MAJOR = stable/breaking, MINOR = features, PATCH = fixes).

## 1.13.0

**Auditor-grade erasure certificate — independently verifiable, no trust in the operator.** `m.erasure_certificate(request_id=...)`
packages the signed deletion tombstones (full hash-chain), the request-scoped erased ids, the receipt public
key, and a CT-style anchor into ONE portable, content-free JSON document. A third party runs the new module
function `verify_erasure_certificate(cert, store_path=...)` — WITHOUT the private key and WITHOUT trusting the
operator — and gets a machine-checkable verdict: the tombstone chain re-derives, every Ed25519 signature
verifies (pinnable to an expected pubkey), the anchor commits to the chain tip, AND every erased id is genuinely
ABSENT from mnemo's store records (the value is deleted, not soft-deleted or kept in a history table by design
as most libraries do). Tampering a tombstone, faking an "erased" id that is still present, or pinning the wrong
key all flip the verdict to INVALID. This is the erasure primitive built for a right-to-erasure demand (GDPR
Art.17) with an Art.30-style auditable record — a governance capability most agent-memory libraries do not
expose. Honest scope stays in-band: it proves erasure from THIS store's records (the ACT, not the content;
witness the anchor externally for an operator-adversarial audit) — it is NOT secure at-rest erasure against
raw-disk/backup forensics (a plaintext store of any library leaves bytes in free space/backups → use an
encrypted store + `shred()`, NIST SP 800-88 crypto-erasure) and NOT the app's own vector store/logs (register
`ErasureTarget`s for cross-store cascade). Receipts:
`mnemo/probes/erasure_certificate_probe.py` (9/9) + `mnemo/probes/erasure_raw_store_probe.py` (12/12).

## 1.12.4

**`mnemo` shell CLI.** A new console command to script the memory layer from the terminal — no Python and no
MCP server needed: `mnemo remember "..." --key k`, `mnemo recall "..."` (current-truth, superseded values
hidden), `mnemo revert <key>`, `mnemo forget --key/--id/--contains`, `mnemo list`, `mnemo stats`. Shares the
store with `mnemo-mcp` (`--path` / `$MNEMO_PATH` / `./mnemo_memory.json`); `--json` for scripting; lexical by
default, semantic when `$MNEMO_EMBED_URL` is set. Zero dependencies. Receipt: `mnemo/probes/mnemo_cli_probe.py`
(6/6).

## 1.12.3

**Optional reranker hook: `recall(rerank=callable, rerank_pool=N)`.** A retrieve-then-rerank extension point:
`rerank(query, records) -> list[float]` (one relevance score per record, higher=better) reorders the top
candidates before truncation to `k`. Model-agnostic (mnemo imports no model) and moat-safe: no model runs
unless the caller supplies one, the WRITE path is untouched, default `None` = zero behavior change, and it
fails open (a broken or wrong-length reranker keeps the pre-rerank order). Honest scope: the lift is only as
good as the reranker — a model-READER reranker is the measured multi-hop lever (LoCoMo ~0.30->~0.48), whereas a
generic query-relevance cross-encoder does NOT help multi-hop (measured: it hurts, because 2nd-hop evidence
isn't directly query-relevant). Receipt: `mnemo/probes/mnemo_rerank_hook_probe.py` (5/5).

## 1.12.2

**Opt-out "a newer version is available" check.** When mnemo runs (Claude Code `SessionStart`, or the MCP
server starting), it checks PyPI at most once per 24h and prints a single ASCII line if the installed version
is behind — the standard pip/npm/gh courtesy, so users who installed weeks ago learn about new integrity
features instead of silently staying on an old release. Fail-open (offline = silent), never blocks, and the
MCP server routes it to stderr so the stdio JSON-RPC channel is untouched. Silence with `MNEMO_NO_UPDATE_CHECK=1`.

## 1.12.1

**Claude Code plugin: a one-time, opt-out star nudge.** After mnemo has actually been useful — 25 captured
writes in a project — the plugin prints a single, warm request to star the repo on the next prompt, then never
again. ASCII-only (safe on non-UTF-8 consoles), never blocks, and silenced anytime with `MNEMO_NO_NUDGE=1`.
Tied to a moment of demonstrated value, not to install time (which wheels can't run anyway).

## 1.12.0

Additive only, no breaking changes.

**CrewAI integration.** `mnemo.integrations.crewai` ships `MnemoStorage`, a drop-in CrewAI `Storage`
(`save`/`search`/`reset`) you hand to `ExternalMemory` (or any custom-storage slot). `search()` retrieves
through mnemo's supersession-filtered `recall()`, so a corrected fact never returns into the crew's context.
Duck-typed — CrewAI is matched structurally and never imported, so the zero-dependency core is untouched.
Opt-in extra: `pip install "agora-mnemo[crewai]"`. Receipt: `mnemo/probes/mnemo_crewai_adapter_probe.py` (6/6).

**Claude Code plugin: optional semantic recall.** The auto-capture plugin (`mnemo.claude_code`) now supports
SEMANTIC recall against any OpenAI-compatible `/embeddings` endpoint (e.g. local Ollama), configured by env
(`MNEMO_EMBED_URL` / `MNEMO_EMBED_MODEL`) or a per-project `.mnemo/config.json`. Default stays deterministic
LEXICAL (runs anywhere, no service). Writes remain verbatim, keyed and no-LLM; the embedder only builds a
retrieval index and fails open (a down endpoint degrades to lexical, never drops a capture).

**New `Mnemo(persist_vectors=True)` option.** By default embedding vectors are a RAM-only cache stripped on
save (keeps the file small and dodges the frozen-world GIL stall on large stores). `persist_vectors=True`
keeps them on disk — intended for a SMALL, frequently-reloaded store (the Claude Code plugin sets it when an
embedder is configured) so semantic recall survives a reload without re-embedding every item on each start.
Leave it off for large brain-scale stores.

**Docs.** The LangChain adapter (shipped in 1.11.0) now has a full entry in the framework-integrations table
and its own README section.

## 1.11.0

Three additive features, no breaking changes.

**Ready-made write-path extractors.** `regex_extractor` (deterministic, no LLM — keeps the zero-LLM-on-write
core) and `make_llm_extractor(call_fn)` (opt-in; puts an LLM on the write path in exchange for auto-capture of
unstructured text). Set `m.extractor = regex_extractor` and supersession/echo_guard/revert engage over free text
without the caller passing an explicit `key`. Both fail-open (a returned `None` falls back to a plain append).

**LangChain integration.** `mnemo.integrations.langchain` ships `MnemoRetriever` (a `BaseRetriever` whose
results are supersession-filtered — a corrected fact is never retrieved back into the prompt) and
`MnemoChatMessageHistory`. Opt-in extra: `pip install "agora-mnemo[langchain]"`.

**Tuned recall recipe + a measured LOCOMO number.** `mnemo/examples/recall_recipe_locomo.py` shows the built-in
levers (an embedder → lexical+semantic hybrid RRF; a soft speaker/entity prefilter via `recall(prefer=...)`) that
put mnemo in the top tier on retrieval. Measured on the full LOCOMO benchmark (n=1536), LLM-free and reproducible:
retrieval-recall@25 = 0.783 (any evidence turn) / 0.648 (all). Run `mnemo/probes/retrieval_recall_locomo.py`.

## 1.10.0

Claude Code integration: deterministic, no-LLM auto-capture of coding-agent memory. `python -m
mnemo.claude_code --install` writes lifecycle hooks (`PostToolUse` / `UserPromptSubmit` / `SessionStart`) into
`.claude/settings.json`. `PostToolUse` captures Edit/Write/MultiEdit/Bash events into a deterministic keyed
store (`file:<path>`), so a corrected fact supersedes the stale one and `echo_guard` blocks its resurrection;
`UserPromptSubmit` injects the current-state memory; `SessionStart` digests the project's known files. No LLM on
the write path (unlike the LLM-summarizing coding memories, which drop facts, leak on erasure, and are
non-reproducible). Fail-open hooks, local JSON store at `.mnemo/coding_memory.json`, `--uninstall` to remove.

## 1.9.0

Identity-confidence gate on supersession, with a candidate reconciliation queue. Prompted by a sharp reader
(marintkael): a keyed store supersedes on `(entity, field)`, but that is only correct if the identity the new
value attaches to is right. When identity is resolved fuzzily (an extractor / embedding match, not a caller
asserted key), a wrong match silently promotes into the authoritative record: a confident-but-WRONG ledger,
harder to catch than a set. Nobody in agent memory gates this: mem0, Zep/Graphiti and Letta all auto-commit an
ungated update.

**Not a new idea** (credited, not claimed): this is the record-linkage clerical-review zone (Fellegi & Sunter,
"A Theory for Record Linkage", JASA 1969: match / non-match / *possible match → review*) and MDM match-merge
stewardship (auto-merge above a threshold, route the intermediate band to a steward queue). The contribution is
the port into an agent-memory write path plus the measured prevention vs an ungated baseline.

- **`remember(..., identity_confidence=c)`** — `c` in [0,1] from your entity-resolution step. `c >= fork_below`
  (default 0.7, `Mnemo.fork_below`) supersedes as before; **below it the write forks a CANDIDATE**
  (`status='candidate'`) that does NOT supersede and is excluded from authoritative resolution. Passing no
  `identity_confidence` = caller asserts identity = supersede, byte-identical legacy.
- **`candidates(key=None)`** — the reconciliation queue: each pending fork with its proposed key, value,
  confidence, and the current authoritative value it would replace.
- **`promote_candidate(id, capability=)`** — steward accepts: candidate becomes authoritative and supersedes the
  prior value. Takes the same capability as `revert()` when a revert authority is set (promoting a fuzzy match
  into authority is exactly the write to protect).
- **`discard_candidate(id, basis=)`** — steward rejects; authority never touched.
- Measured (probes/identity_gate_supersession_probe.py, deterministic, E=40, p_miss=0.2, 5 seeds): under noisy
  identity resolution an ungated auto-commit corrupts the authoritative ledger 13.5% of the time; the gate cuts
  that to **1.0% (a 93% reduction)** at the cost of a steward review queue (~65 candidates/run). Residual = mis
  resolutions that scored above the threshold (the gate is only as good as the confidence signal). Tenant-scoped;
  10 new tests; suite 99/99.

## 1.8.0

Cross-store erasure becomes a first-class operation. Motivated by a measured gap (audit report, July 2026):
a copy the application embedded into its OWN vector index survives every memory store's native delete (8/8 in
our cell, mnemo included) — the store alone cannot fix that, because it cannot see infrastructure it was never
told about. 1.8.0 wires the fan-out into the erasure path:

- **`register_erasure_target(target)`** — register app-side stores (the app's vector index, embedding/response
  caches, retrieval logs) implementing the two-method `ErasureTarget` protocol (`erase(subject)`,
  `still_recoverable(subject, values)`). Targets are live client adapters, so they are RAM-only: re-register on
  process start.
- **`forget_subject(...)` cascades**: with targets registered it erases the store (as before), then every
  registered target, re-checks residual recoverability per target, and returns a hash-chained **`manifest`**
  in its result — honest by construction: `complete` is True only if EVERY store (mnemo itself included, as the
  first self-checked target) verified the value no longer recoverable, and leaking stores are NAMED in
  `residual_targets`. Check values are captured automatically from the erased records (or pass `values=[...]`).
- Measured (deterministic cell, n=8): unwired external index leaks 8/8 after a store-native delete; wired it
  erases 0/8 with 8/8 `complete` manifests whose chains verify; a deliberately broken wiring produces ZERO
  falsely-complete receipts and names the leak 8/8. The receipt cannot lie about the fan-out it was given.
- **Honest scope (unchanged philosophy):** the manifest covers only REGISTERED targets — unknown copies stay
  unknown; it attests recoverability at check time, not physical destruction, and does not cover backups or
  embedding inversion of retained vectors. New tests: `tests/test_erasure_manifest_integration.py` (6).

## 1.7.0

Encryption-at-rest + crypto-shredding — the confidentiality leg of the governance layer (integrity +
provenance + erasure + **confidentiality**). Standard primitives only; we do not roll our own crypto.

- **`Mnemo(path=..., encrypt_key=...)`** (raw 32-byte key from `new_encryption_key()`) or **`encrypt_passphrase=...`**
  (scrypt-stretched) encrypts the store at rest with **AES-256-GCM** (AEAD: confidentiality + tamper-detection),
  a fresh random 96-bit nonce per save, file layout `MAGIC(5)+salt(16)+nonce(12)+ciphertext` with the header
  authenticated as AAD. Opt-in, default OFF → byte-identical plaintext-JSON legacy. mnemo never persists the
  key. A wrong key / tampered file **fails loud** (never a silent empty store). Needs the `cryptography` package.
- **`shred()`** — crypto-shredding: destroy the in-memory key so the on-disk ciphertext (and every at-rest
  backup of it) becomes permanently unrecoverable (NIST SP 800-88 key-destruction "Purge"), clearing plaintext
  from RAM. Supports a GDPR Art.17 erasure workflow.
- **Honest scope (documented, not overclaimed):** protects the store AT REST (a read file / stolen disk /
  backup); does NOT protect a compromised running process (key + plaintext in RAM), the key holder, or against
  malware — it is not end-to-end and not runtime protection. Prior art credited: SQLCipher, NIST SP 800-88,
  age/Fernet. Receipt: `probes/encryption_at_rest_probe.py`; 10 tests in `tests/test_encryption.py`.

## 1.6.0

Hard tenant isolation + a PII floor — logical multi-tenancy enforced by the store, fail-closed.

- **Tenant isolation, bound to the store (not a per-call arg).** `Mnemo(tenant="acme")` binds a store to one
  tenant; `store.for_tenant(id)` hands out logically-isolated views over ONE shared physical store (shared
  items/file/caches, no duplication). Every write is tenant-stamped; recall, keyed supersession, the echo
  guard, erasure, **and the consolidation/dedup/contradictions/conflict paths** are hard-filtered to the
  acting tenant — so no forgotten parameter can leak or mutate another tenant's data. An unbound store is the
  admin view (sees all). Honest scope: logical in-process isolation, not a security boundary between hostile
  tenants. Measured receipt: `probes/tenant_isolation_probe.py` (cross-tenant read leak 0/20 with name+value
  detection, cross-tenant supersession 0, consolidation cross-links 0 — control shows 10 when unscoped —
  poisoning 0, over-erasure 0).
- **PII layer (a floor, not a DLP).** `detect_pii` / `redact_pii` module functions (regex; SSN/credit-card
  matched before the broad phone pattern); `remember(pii=...)` tagging or store-wide `pii_detect=True`;
  `recall(redact_pii=True)` masks PII in the RETURNED text only (the stored record is untouched);
  `forget_pii()` sweeps + tombstones PII rows for data minimization; `pii_report()` audits exposure. Regex
  catches structured formats and essentially no names — use a real DLP for detection; this is a
  zero-dependency default for reducing raw PII flow into prompts.
- README: 2-minute Quickstart up top; runnable `examples/` directory (basics, correction & erasure,
  bring-your-own-embedder semantic recall).

## 1.5.0

Provable forget + bitemporal audit — the governance/temporal pillar.

- **`ErasureAuditor.compliance_receipt(subject, values, sign=, pubkey=, request_id=, basis=)`** — runs the audit
  and packages it as a shareable, optionally-SIGNED proof-of-erasure receipt (the artifact a DPO hands a
  regulator under GDPR Art. 17 / EU AI Act record-keeping): which stores were checked, the per-store verdict,
  the request/basis, a timestamp, tamper-evident under your key. `verify_compliance_receipt(receipt, verify,
  expected_pubkey=)` re-checks it; `ed25519_signer(sk)` / `ed25519_verify` are BYO-key helpers (or plug an
  HSM/KMS). Crypto is a lazy import — the auditor framework stays dependency-free until you sign.
- **Bitemporal query** — `as_of(key, when, as_recorded=)` gains a second clock: pass a transaction-time
  `as_recorded` to reconstruct "what did we BELIEVE, at that recording time, was true at valid-time `when`",
  using only records written by then, so a correction recorded LATER can't leak into the earlier belief.
  **`believed_at(key, as_recorded)`** returns the value the agent would have acted on if frozen at that time —
  replay/audit without contamination. `as_recorded=None` is byte-identical to the prior valid-time `as_of`.
- **`probes/forget_verification_bench.py`** — an open benchmark for a capability no recall leaderboard scores:
  after a right-to-erasure deletion, does the value provably stop being recoverable across the 6-store fan-out
  (primary log, vector index, cache, Qdrant/pgvector/S3 soft-delete residue)? Scores soft-delete (the common
  "delete the row" bug: ~0.17 — five stores still leak) vs hard-delete (1.00, verified) and emits a signed receipt.

## 1.4.0

Soft-delete residual probes for the `ErasureAuditor` — from an r/RAG thread: a store reports a delete as DONE
(HTTP 200) while the data physically survives until a background compaction/vacuum/GC that may never trigger, so
"the API returned 200" and "it's gone" are two different things. Each probe calls only the client you pass —
mnemo keeps ZERO external dependencies (no qdrant/psycopg/boto3 import).

- **`QdrantSoftDeleteProbe`** — deleted points sit in the bitmask until a segment crosses the optimizer's
  `deleted_threshold` (default 0.2, 1000-vector min); flags residue with compaction pending.
- **`PgVectorSoftDeleteProbe`** — MVCC dead tuples stay on disk (and the HNSW graph unrepaired) until VACUUM;
  reads `n_dead_tup` from `pg_stat_user_tables`.
- **`S3VersioningProbe`** — a "delete" on a versioned bucket is just a delete marker; the prior version is one
  `list_object_versions` call away.
- **`SoftDeleteProbe`** — generic escape hatch for the long tail (uncompacted Chroma segment, observability
  spans carrying full chunk text, CDC/Kafka topics, embedding-provider request logs): supply a `residual()` check.

## 1.3.0

Clean memory — a write-admission gate and an inspector, aimed at agent memory's #1 real-world failure:
indiscriminate writes (audited stores measured ~98% junk, one fact cloned 800+ times). All read-only or
opt-in; no change to existing `remember()`/`recall()` behaviour.

- **`admit(text, ..., dup_threshold=0.92, quality=True)`** — decide whether a candidate is worth storing BEFORE
  it bloats the store. Rejects empty / too-short / non-content (refusals, "no sources ..."), and skips a
  near-identical active memory (returns its id instead of appending a copy). A value UPDATE (same text, new
  number) is admitted so consolidation can supersede the stale value. Returns
  `{admitted, id, reason, duplicate_of, similarity}`. (Reliably kills exact/near-exact re-extraction bloat;
  paraphrase-level dedup is tunable via a lower `dup_threshold`, trading precision.)
- **`why_recalled(query, id=None)`** — inspector: the per-candidate score breakdown `recall()` ranks by
  (semantic cosine, lexical overlap, decayed effective value, corroboration good/bad, stale-derived flag, and
  the live rank), so "why did this surface / why not" stops being an archaeology dig.
- **`memory_report()`** — inspector overview: active/superseded, counts by type, consolidated, decayed, and a
  near-duplicate redundancy estimate — the surface that proves a store did NOT accumulate 800 copies of a fact.

## 1.2.0

Universal-executor gate — OPT-IN; default (`tool=None`) is byte-identical to 1.1.0 (verified by tests).

- **`is_universal_executor(tool, signature=None)`** — detect verb-polymorphic universal executors
  (shell/terminal, eval/exec, arbitrary SQL, generic HTTP, run-arbitrary-command) whose reversibility is NOT
  decidable from the tool signature.
- **`spend_irreversible(..., tool=, contained=)`** — when an irreversible action routes through a universal
  executor, a per-tool reversibility label is unsound and the executor's external harm-reach is bounded only by
  containment, so an *uncontained* universal executor is denied outright (the caller must sandbox it,
  `contained=True`, or route the effect through a specific signature-decidable tool). `contained=True` falls
  through to the normal per-source budget check.
  Motivation is measured (mnemo lab, ToolEmu 330 tools, 2 labelers): tool reversibility is ~93% decidable from
  the signature (Cohen's κ=0.82); the ~7% undecidable residual is exactly the universal-executor class, whose
  realized harm-reach is environment-conditional (isolated executor ~0% external, networked ~0.66). Honest
  bound: the detector is a heuristic and `contained` is a caller assertion mnemo cannot verify — it forces the
  declaration, it does not enforce the sandbox. Credits the reversibility×scope grid of arXiv:2607.07474.

## 1.1.0

Security hardening from the first internal security pass (see SECURITY.md). Both additions are OPT-IN; the
default behaviour is byte-identical to 1.0.0 (verified by tests).

- **`Mnemo(max_text=N)`** — availability guard: `remember()` truncates a single record's text to N chars and
  stamps `meta["truncated_from"]`, so one runaway/malicious write can't exhaust memory. Default `None` =
  unbounded (legacy).
- **`verify_writes(warn_unpinned=True)`** — surfaces the self-referential-pubkey footgun: when signatures are
  present but no `expected_pubkey` is pinned, it reports a problem (a store-rewriter can swap sig+key and still
  pass). Default `False` = legacy. `governance_report()` now also states `proof.signature_authenticity`
  ("pinned to expected_pubkey" vs "self-referential — pin expected_pubkey or witness anchor() externally").

## 1.0.0

First stable release. The library matured over the 0.4–0.7 line into a real, shipped product; 1.0.0 marks a
**stable public API** (`mnemo.__all__`), a **runnable test suite** (`tests/`, CI on every push), a documented
changelog, and the governance/erasure tooling consolidated. No functional change from 0.7.22 — this release is
about production-readiness and API stability, not new features.

- **Public API frozen** in `mnemo.__all__`: `Mnemo`, `new_receipt_keypair`, `new_source_keypair`, `sign_revert`,
  `sign_erasure`, `erasure_challenge`, `attest`. Governance/erasure tools live in submodules
  `mnemo.deletion_manifest` and `mnemo.erasure_auditor`.
- **Tests + CI** added (`tests/test_core.py`, `test_governance.py`, `test_erasure.py`) — core recall/supersession
  /revert/echo-guard/forget, tamper-evidence, the CT-anchor, authenticated-principal erasure, the deletion
  manifest, and the erasure auditor, all cloud-free and deterministic.

## 0.7.x (highlights)

- **0.7.22** — governance & cross-store erasure tools: `anchor()`/`verify_consistency()` (CT-style external
  anchor, RFC 6962 — catches a key-holder history rewrite), authenticated-principal + decision-basis erasure
  tombstones, `DeletionManifest` (honest-by-construction cross-store record), `ErasureAuditor` (adversarial
  "content still reconstructible?" audit across the fan-out).
- **0.7.20** — dedicated repo, branding page, docs.
- **0.7.0–0.7.19** — first-class correction/erasure channel (revert, `retract_lineage`, `echo_guard`,
  `route()` intent tagger, `classify_reversion`, `forget_subject` + tombstones, `verify_writes` hash-chained
  receipts), six framework adapters (OpenAI Agents, AutoGen, LangGraph, LlamaIndex, Google ADK, Pydantic AI),
  and the cross-system integrity benchmark.
- **0.4–0.6** — value-ranked recall, per-type decay, consolidation, lexical+semantic auto-mode (RRF),
  corroboration-gated influence, MCP server.
