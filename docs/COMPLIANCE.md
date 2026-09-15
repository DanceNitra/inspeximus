# inspeximus — Compliance Control Mapping

## Purpose

This document maps the actual, shipped primitives of **inspeximus** — a zero-dependency Python agent-memory library whose differentiator is *deterministic memory integrity* — to recognized security and compliance control frameworks. It is written for an enterprise security reviewer, DPO, or GRC lead who needs to see, control-by-control, where inspeximus can supply technical evidence toward a control objective and where it cannot.

inspeximus is a **memory-layer library**: it stores, corrects, supersedes, attributes, and cryptographically proves the integrity of an agent's memory records over time, on a deterministic (no-LLM) path. Its integrity model is built on hash-linked write receipts, an RFC 6962-style signed append-only log (`anchor()` / `verify_consistency()`), Ed25519 attestation and capability-gated reversal, external witness co-signing with split-view detection, and portable erasure certificates.

## Disclaimer — mapping is not certification

- **A control mapping is not a compliance certification, attestation, or audit.** Nothing in this document certifies inspeximus, or any system that embeds it, against any framework. Certification is issued by accredited assessors against a defined system boundary, not by a library vendor.
- inspeximus is **one component**. A control is satisfied by the *whole system* — people, process, deployment configuration, key management, hosting, and other tooling — not by a library in isolation. inspeximus can *supply evidence for* or *support* a control; the enterprise remains the accountable party.
- Throughout, we use **"supports"** and **"provides evidence for"** deliberately. We do **not** claim inspeximus "guarantees compliance," "certifies," or "makes you GDPR/AI-Act compliant."
- Every control ID and title below was checked against its primary source. Where a mapping depends on an interpretation, or where we could not confirm an exact identifier, it is marked **(unverified)**. Framework documents are revised; verify IDs against the current published version for your assessment.
- Cryptographic guarantees are **conditional on correct key management and deployment** (e.g. witness keys held by an independent party; anchors published to an append-only medium the operator cannot silently rewrite). A misconfigured deployment weakens or voids the evidentiary value. See *Gaps / not covered*.

---

## 1. NIST SP 800-53 Rev. 5 — Security and Privacy Controls

Mapping to the control catalog in NIST SP 800-53r5. inspeximus is most relevant to the **AU (Audit and Accountability)**, **SI (System and Information Integrity)**, **SC (System and Communications Protection)**, **MP (Media Protection)**, and **SR (Supply Chain Risk Management)** families.

| Control ID | Control title | inspeximus primitive(s) | How it supports the control |
|---|---|---|---|
| **AU-2** | Event Logging | Tamper-evident write receipts; `history()`; supersession ledger | Every write, tombstone, and reversal is recorded as a defined, auditable event on the receipt chain — a deterministic log of memory-state changes. |
| **AU-3** | Content of Audit Records | Write receipts; attestation; `as_of()` / `history()` | Records carry what changed, the keyed value, timestamps (valid + transaction time via bitemporality), and — with attestation — a signed authorship identity, satisfying the "who/what/when" content requirement for memory events. |
| **AU-9** | Protection of Audit Information | Hash-linked append-only receipt chain; `anchor()` / `verify_consistency()` | The receipt chain is tamper-evident: any edit or deletion of a past record breaks the hash link. The signed tree head plus consistency proof detects rollback/rewrite of the log itself. |
| **AU-9(3)** | Protection of Audit Information \| Cryptographic Protection | Ed25519-signed tree head; hash-linked receipts | Integrity of audit records is enforced cryptographically (hash chain + signed anchor), not by access control alone. |
| **AU-10** | Non-repudiation | Attestation (source Ed25519-signs authorship); capability tokens + signed revert intent; `verify_attribution()` | A signed write binds a memory record to its author; a signed `revert` intent binds a correction to an authorized actor — producing non-repudiable evidence of origin for writes and reversals. |
| **AU-11** | Audit Record Retention | Append-only receipt chain; `history()` | Records are retained on the append-only chain; nothing is silently overwritten (supersession retires a *value* while retaining the prior record in history). |
| **AU-12** | Audit Record Generation | Write receipts generated inline on every write/tombstone | Audit records are generated automatically at the point of each memory operation, without a separate logging path. |
| **SI-7** | Software, Firmware, and Information Integrity | Hash-linked receipts; `state_digest`; `witness()` / `verify_witness()`; `verify_writes()` | Provides integrity verification for stored *information* (memory records): a deterministic state fingerprint plus per-write tamper-evidence detect unauthorized modification. |
| **SI-7(1)** | Integrity Checks | `verify_consistency()`; `verify_witness()`; `state_digest` | Supports integrity checks at defined transitions / on demand — the consistency proof and hydration receipt confirm state has not been altered since a known anchor. |
| **SI-7(6)** | Cryptographic Protection | Ed25519 signatures; SHA-256 hash chain | Integrity verification uses cryptographic mechanisms rather than checksums alone. |
| **SI-7(7)** | Integration of Detection and Response | `detect_split_view`; `verify_consistency()` failure signals | Detection of a rewritten/rolled-back log or a fork/split-view is surfaced as an explicit verifiable failure that a response process can act on. |
| **SI-4** | System Monitoring | `witness_cosign` / `verify_cosigned_anchor`; `detect_split_view` (v1.34.0); influence gate reports | External witness co-signing (k-of-n) and split-view detection let an independent party monitor for an operator who serves divergent histories to different observers. |
| **SI-10** | Information Input Validation | `check_conflict` (write-time contradiction gate); poison-defense / influence gate | Contradictory or anomalous writes are gated at ingestion — validating memory input before it becomes trusted state (relevant to poisoned/indirect input). |
| **SC-12 / SC-13** | Cryptographic Key Establishment and Management / Cryptographic Protection | Ed25519 keys for attestation, anchors, witness co-signing; capability tokens | Cryptographic operations underpinning attribution and integrity depend on documented key material; controls apply to how the embedding system manages those keys. |
| **SC-28** | Protection of Information at Rest | `shred()` (crypto-shred over an encrypted store) | For an encrypted store, key destruction renders at-rest ciphertext unrecoverable — supporting protection/sanitization of information at rest. |
| **MP-6** | Media Sanitization | `shred()` — NIST SP 800-88 "Purge" via key destruction; `erasure_certificate` | Cryptographic erase is a recognized Purge technique; the erasure certificate provides verifiable evidence the sanitization action occurred (see §4). |
| **SR-4** | Provenance | Attestation; `history()`; hash-linked receipts | Maintains provenance for each memory record — signed authorship and a complete, tamper-evident lineage of how a value reached its current state. |
| **SR-11** | Component Authenticity *(applied to data records — analogy, unverified)* | Attestation; `verify_attribution()` | Signed authorship lets a consumer verify a record originated from the claimed source rather than an impersonator. Note: SR-11 targets supply-chain *components*; applying it to memory records is an analogy, hence marked unverified. |
| **AC-4** | Information Flow Enforcement | Tenant isolation; influence gate | Per-tenant isolation constrains memory flow across trust boundaries; the influence gate limits how much a single source can steer recall. |

---

## 2. NIST SP 800-218A — Secure Software Development Practices for Generative AI (SSDF Community Profile)

SP 800-218A augments the SSDF (SP 800-218 v1.1) with AI-specific tasks, organized under the base SSDF practice groups **PO / PS / PW / RV**. inspeximus is a runtime memory component, so it supports the *data-integrity and provenance* practices most directly; it does not cover model training or the broader SDLC.

| Practice / Task | Title (SSDF base, as augmented by 800-218A for AI) | inspeximus primitive(s) | How it supports the practice |
|---|---|---|---|
| **PS.1** | Protect All Forms of Code from Unauthorized Access and Tampering *(here: the data/memory the AI relies on)* | Hash-linked receipts; `anchor()` / `verify_consistency()` | Detects unauthorized tampering with the persisted memory that conditions model behavior — a data analogue of tamper protection. |
| **PS.2** | Provide a Mechanism for Verifying Software Release Integrity *(analogue: verify memory-state integrity)* | `state_digest`; `witness()` / `verify_witness()`; `verify_consistency()` | Supplies a verifiable integrity mechanism for the memory state a running AI system depends on. |
| **PS.3.2** | Collect, Safeguard, Maintain, and Share Provenance Data | Attestation; `history()`; `as_of()` | Maintains signed, queryable provenance for each memory record — the data-provenance emphasis 800-218A adds to the SSDF. |
| **PW.1** | Design Software to Meet Security Requirements and Mitigate Risks *(AI: mitigate data-poisoning/integrity risk)* | `check_conflict`; poison-defense / influence gate; `verify_claim` | Integrity and anti-poisoning gates are designed-in at the memory layer, addressing an AI-specific data-integrity risk 800-218A calls out. |
| **RV.1** | Identify and Confirm Vulnerabilities *(AI: detect integrity/poisoning incidents in operation)* | `detect_split_view`; `verify_writes()`; influence-gate reports | Provides detection signals (fork/split-view, failed write verification) that let operators confirm a memory-integrity compromise. |

**Scope caveat:** 800-218A is a *producer/acquirer* profile spanning the full AI SDLC. inspeximus touches only the runtime data-integrity and provenance slice. The AI-specific sub-task numbering is **(unverified here)**; base SSDF task IDs are used as anchors.

---

## 3. NIST AI 600-1 — AI RMF Generative AI Profile

AI 600-1 enumerates GAI risk categories and suggested actions mapped to **GOVERN / MAP / MEASURE / MANAGE**. inspeximus is relevant primarily to **Information Integrity**, **Data Privacy**, **Information Security**, and **Confabulation** (as it pertains to grounded, attributable memory).

| GAI risk / area | inspeximus primitive(s) | How it supports risk management |
|---|---|---|
| **Information Integrity** | Supersession; hash-linked receipts; `anchor()` / `verify_consistency()`; `detect_split_view` | Ensures the memory feeding a generative system reflects corrected, attributable, tamper-evident state — and detects an operator who rewrites that state. |
| **Data Provenance** | Attestation; `verify_attribution()`; `history()` / `as_of()` | Each memory record carries signed authorship and a full validity timeline, supporting the profile's provenance-logging recommendations. |
| **Confabulation** | `verify_claim` (read-time grounding); `check_self_narration`; `check_conflict` | Read-time grounding and self-narration checks constrain recall to substantiated memory. Mitigation at the memory layer only — not a model-level hallucination fix. |
| **Data Privacy** | `forget_subject`; `forget_pii`; `detect_pii` / `redact_pii`; `erasure_certificate`; `shred()` | Supports data-minimization and subject-level erasure, with a verifiable erasure receipt. |
| **Information Security** | Tenant isolation; capability-gated `revert`; influence / poison gate; witness co-signing | Isolation, gated correction, and anti-poisoning defenses harden the persistent memory store. |

**Mapping note:** AI 600-1 expresses suggested *actions* rather than single-line control IDs; the table maps to the profile's **risk categories** rather than individual action numbers, which are **(unverified at the action-ID level)**.

---

## 4. NIST SP 800-88 Rev. 1 — Guidelines for Media Sanitization

SP 800-88 defines **Clear / Purge / Destroy**, and lists **Cryptographic Erase (CE)** as a **Purge** technique for encrypted storage.

| Concept | inspeximus primitive(s) | How it supports the guideline |
|---|---|---|
| **Purge — Cryptographic Erase** | `shred()` — destroys the encryption key for an encrypted store | Destroying the key renders the ciphertext unrecoverable, implementing the CE Purge technique for that logical store. |
| **Verification of sanitization** | `erasure_certificate` / `erasure_report` | Produces a portable, independently verifiable record that the sanitization action occurred. |

**Scope limits:** Crypto-shred only sanitizes data actually encrypted with the destroyed key — plaintext copies, unencrypted backups, swap, snapshots, or replicas outside inspeximus's control are **not** purged. CE assurance depends on encryption strength and the absence of key escrow/backups.

---

## 5. OWASP — LLM Top 10 (2025) and Agentic Security (ASI)

### 5a. OWASP Top 10 for LLM Applications (2025)

| Risk ID | Risk title | inspeximus primitive(s) | How it supports mitigation |
|---|---|---|---|
| **LLM04** | Data and Model Poisoning | `check_conflict`; poison-defense / influence gate; attestation; witness co-signing | Gates contradictory/anomalous writes, caps single-source influence, and requires signed authorship. |
| **LLM02** | Sensitive Information Disclosure | `detect_pii` / `redact_pii`; `forget_pii` / `forget_subject`; tenant isolation | PII detection/redaction and subject-level erasure reduce sensitive-data exposure; isolation prevents cross-tenant leakage. |
| **LLM08** | Vector and Embedding Weaknesses | Deterministic (no-LLM) integrity path; `check_conflict`; influence gate | Integrity decisions do not themselves depend on a manipulable embedding/model. |
| **LLM09** | Misinformation | Supersession; `verify_claim`; `history()` | Corrected facts retire stale values so recall does not resurface superseded misinformation. |
| **LLM06** | Excessive Agency | Capability tokens + Ed25519-signed revert intent | Destructive memory operations are gated behind capability tokens and signed intent. |
| **LLM01** | Prompt Injection *(persistence vector only)* | `check_conflict`; influence gate; `check_self_narration` | Limits an injected instruction's ability to *persist* into trusted memory — not a prompt-level classifier. |

### 5b. OWASP Agentic Security — Top 10 for Agentic Applications (ASI)

| Risk ID | Risk title | inspeximus primitive(s) | How it supports mitigation |
|---|---|---|---|
| **ASI06** | Memory & Context Poisoning | `check_conflict`; poison-defense / influence gate; attestation + `verify_attribution()`; hash-linked receipts; `anchor()` / `verify_consistency()`; `witness_cosign` / `detect_split_view`; `check_self_narration` | The core alignment. Addresses all three ASI06 vectors: **direct injection** (write-time gate + signed authorship), **indirect injection** (influence gate on untrusted tool/web writes), and **gradual erosion / "sleeper"** (tamper-evident receipt chain + consistency/split-view detection over history). Reversal is a ledgered, attributable, capability-gated event. |

**Honesty note on ASI06:** inspeximus is **not** a prompt-injection content classifier. Pair it with an input/output classifier for full ASI06 coverage.

---

## 6. GDPR and EU AI Act

inspeximus targets the **erasure** and **record-keeping / traceability** obligations. It provides technical *evidence*; legal compliance is determined by the controller's overall processing — not by a library.

### 6a. GDPR (Regulation (EU) 2016/679)

| Article | Title | inspeximus primitive(s) | How it supports the obligation |
|---|---|---|---|
| **Art. 17** | Right to erasure | `forget_subject`; `forget_pii`; `erasure_certificate` / `erasure_report`; `shred()` | Executes subject/PII-level erasure and produces a portable, verifiable erasure receipt. |
| **Art. 5(1)(d)** | Accuracy | Supersession / keyed last-write-wins; `history()` | A corrected fact retires the stale value so recall returns current truth. |
| **Art. 5(2)** | Accountability | Hash-linked receipts; attestation; `anchor()` / `verify_consistency()` | Tamper-evident, attributable records let the controller *demonstrate* how memory data was written, corrected, and erased. |
| **Art. 30** | Records of processing activities | `history()`; write receipts; supersession ledger | Supplies a technical record of processing events at the memory-record level. |
| **Art. 25** | Data protection by design and by default | `detect_pii` / `redact_pii`; per-type decay; tenant isolation; default-deny influence gate | Data-minimization and isolation primitives support a privacy-by-design posture. |
| **Art. 5(1)(e)** | Storage limitation | Per-type decay; `forget_*` | Per-type decay and targeted forget support retention limits. |
| **Art. 15** | Right of access | `export_subject` | Every record whose source resolves to the subject, with provenance and history, on the same resolver erasure uses; the export is a signed ledger entry with a manifest hash. |
| **Art. 16** | Right to rectification | `rectify` | A correction with a receipt naming who asked and why, on the same chain as the actions. |
| **Art. 22** | Automated individual decision-making | `ActionLedger.oversight` | Human review, override or refusal tied to the action that produced the decision, with the actor. |
| **Art. 35(7)** | Data protection impact assessment | `dpia_appendix` | The (a) inventory and (d) measures filled from the store and ledger; the (b) and (c) judgements marked as the controller's. |

### 6b. EU AI Act (Regulation (EU) 2024/1689) — high-risk provisions

| Article | Title | inspeximus primitive(s) | How it supports the obligation |
|---|---|---|---|
| **Art. 12** | Record-keeping (automatic logging over lifetime) | Hash-linked write receipts; `history()`; `as_of()`; supersession ledger; `anchor()`; **portable audit bundle** (`audit-build` / `audit-verify`) | Automatic, tamper-evident, timestamped logging of memory events supports traceability; the bundle is a content-free snapshot an auditor re-verifies from genesis **offline**. |
| **Art. 19** | Automatically generated logs (kept ≥ 6 months) | Append-only receipt + tombstone chains; `anchor()`; portable audit bundle | Append-only receipts with a signed tree head support keeping the logs available with integrity preserved for the required retention period (Art. 19: appropriate period, at least six months). |
| **Art. 10** | Data and data governance | Attestation; `check_conflict`; `detect_pii` / `redact_pii`; per-type decay | Provenance, contradiction gating, and PII handling contribute to data-governance evidence at the memory-record level. |
| **Art. 15** | Accuracy, robustness and cybersecurity | Supersession; `echo_guard`; `verify_claim`; poison-defense / influence gate; hash-linked receipts; witness co-signing + `detect_split_view` | Correction, resistance to a stale value resurfacing, poison-defense, and operator-adversarial anti-tampering contribute to accuracy, robustness and cybersecurity (Art. 15 resilience to unauthorised alteration / data poisoning). |
| **Art. 12 (actions)** | Traceability of the system's functioning | `ActionLedger` (2.29.0+) | Every action is a signed, hash-chained entry carrying the memory state digest and the recalled ids; bound to the memory chain by position. |
| **Art. 14** | Human oversight | `ActionLedger.oversight`; `oversight_report` | Approve, refuse, override, stop and review events with an actor, tied to the action; error actions with no oversight after them are listed. |
| **Art. 19 / Art. 26(6)** | Logs kept at least six months | `attest_retention`; `archive` (2.31.0+) | A signed retention statement made from the ledger; rotation under a signed checkpoint the verifier follows, nothing deleted. |
| **Art. 50** | Transparency towards persons | `ActionLedger.disclosure` | One signed entry per session recording the disclosure shown. |
| **Art. 73** | Serious incidents | `ActionLedger.incident`; `incident_report`; `incident_reported` | The record with the clock from awareness (15, 2 or 10 days by severity), linked evidence, and the later entry that closes it. |
| **Art. 11 / Annex IV** | Technical documentation | `annex_iv`; `instructions_for_use` (Art. 13(3)(f)) | The skeleton with the evidence sections filled and 24 provider fields marked as operator input. |
| **Art. 26** | Deployer duties | `deployer_report` (2.30.0+) | The evidence under each paragraph (oversight recorded, incidents, log age against the floor, disclosures, the offline bundle) with the DPIA and FRIA appendices. |
| **Art. 27** | Fundamental rights impact assessment | `fria_appendix` | Observed period and frequency, oversight recorded, incidents and rights requests from the ledger; cross-references the DPIA per Art. 27(4) as amended. |
| **Art. 49 / Annex VIII** | Registration in the EU database | `registration_export` (2.31.0+) | The Annex VIII fields for section A, B or C with the traceability reference, the information-used description, the instructions for use and the FRIA and DPIA summaries filled from evidence. |

**Runnable overlay.** `inspeximus compliance` emits an article-labelled EVIDENCE report (HTML or JSON) with **live counts from the store** for 21 of the rows above (the document generators report `available`: they are generators, not counters); `inspeximus audit-build` exports the portable bundle an auditor verifies with `inspeximus audit-verify` (offline, no store, no key). These make the mapping demonstrable per store, not merely asserted.

**Enforcement-date note (accuracy matters — updated 24 Jul 2026).** The AI Act applies in **staggered phases** (Art. 113), and those phases were **changed** by **Regulation (EU) 2026/1744** (Digital Omnibus on AI; adopted 8 Jul 2026, OJ 24 Jul 2026, in force 27 Jul 2026 — EUR-Lex ELI `reg/2026/1744/oj`). Chapters I and II (general provisions + prohibited practices) applied 2 Feb 2025; the GPAI-model, governance and penalty provisions (Chapter III Section 4, Chapters V, VII, XII and Art. 78) from 2 Aug 2025, **with the exception of Art. 101**. The high-risk obligations of **Chapter III Sections 1–3** — where Art. 10/12/13/14/15/19 sit — were **deferred from 2 Aug 2026** to **2 Dec 2027** for standalone Annex III systems (Art. 6(2)) and **2 Aug 2028** for Annex I product-embedded systems (Art. 6(1)). So the memory-relevant record-keeping / accuracy / data-governance duties bite **2 Dec 2027 for Annex III high-risk systems** — not August 2026. The Art. 50 transparency duties and the Art. 4 AI-literacy duty were not deferred. Sources: Reg (EU) 2024/1689 Art. 113 as amended (EUR-Lex ELI `reg/2024/1689/oj`), by the amending Reg (EU) 2026/1744 (ELI `reg/2026/1744/oj`).

**Legal caveat:** GDPR and the EU AI Act impose obligations on **controllers / providers / deployers**, not on libraries. inspeximus produces the receipts, provenance, and tamper-evident logs an accountable party uses as evidence, but cannot by itself make a system compliant, and does not determine lawful basis or DPIA outcomes. The **Art. 17 erasure guarantee is only as complete as the encryption and copy-control of the underlying store** (see §4).

---

## Gaps / not covered — what inspeximus does NOT do

Being explicit about scope is part of the integrity claim.

- **Not a certification, and not certified.** No framework accreditation is claimed or implied.
- **Not a prompt-injection or content classifier.** inspeximus limits an injection's *persistence* into memory; it does not detect malicious natural-language content at the prompt. Pair with an input/output guardrail.
- **Not a model-level hallucination fix.** `verify_claim` / `check_self_narration` constrain *recall* to grounded memory; they do not stop a model confabulating from its own weights or non-inspeximus context.
- **Crypto-shred sanitizes only what it encrypted.** Plaintext copies, external backups, snapshots, replicas, swap, or logs outside inspeximus's key are **not** purged.
- **Cryptographic guarantees are deployment-conditional.** `anchor()` / witness integrity assumes anchors are published to a medium the operator cannot silently rewrite and that **witness keys are held by an independent party**. A single-operator deployment with self-held witness keys weakens split-view detection to near-zero. Key management (SC-12/SC-13) is on the deployer.
- **No transport/network security, IAM, or access control of its own** beyond capability tokens for revert.
- **No SDLC / training-time coverage.** It does not address model training, evaluation, dependency scanning, or the broader lifecycle beyond the runtime memory slice.
- **No availability / DoS / unbounded-consumption controls** (OWASP LLM10 is not addressed).
- **Not legal advice.** DPIA, lawful basis, records-of-processing completeness, and regulatory classification remain the controller's responsibility.

---

*Control IDs and titles were checked against primary sources (NIST CSRC, OWASP, and the consolidated EU AI Act / GDPR texts). Items marked **(unverified)** could not be confirmed at the exact identifier level and should be checked against the current published framework version before use in a formal assessment. This mapping reflects inspeximus's shipped primitives as of the referenced versions (including `witness_cosign` / `detect_split_view` in v1.34.0) and should be re-reviewed when either the library or a framework is revised.*
