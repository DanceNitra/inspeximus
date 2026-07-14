# Changelog

All notable changes to mnemo (`agora-mnemo`). Format loosely follows Keep a Changelog; versioning is semver
(MAJOR = stable/breaking, MINOR = features, PATCH = fixes).

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
