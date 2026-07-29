# The EU AI Act compliance-evidence layer for AI-agent memory

When the EU AI Act's high-risk obligations start to apply — **2 Dec 2027** for standalone Annex III systems and
**2 Aug 2028** for Annex I product-embedded ones, deferred from 2 Aug 2026 by Regulation (EU) 2026/1744 (the
[Digital Omnibus on AI](https://eur-lex.europa.eu/eli/reg/2026/1744/oj/eng), published 24 Jul 2026, in force
27 Jul 2026) — a provider has to *produce* three things about what its AI agent **remembers**: a tamper-evident record of what was logged
(Art. 12 / 19), evidence that the memory is kept accurate and resists tampering (Art. 15), and provable erasure
on request (GDPR Art. 17). Agent-memory libraries ship none of it.

**inspeximus is, to our knowledge, the only open agent-memory library that ships verifiable erasure (with a
receipt) and tamper-evident record-keeping as reusable evidence for the agent-memory slice of the EU AI Act** —
a gap absent from every agent-memory product we scanned. It is a single zero-dependency file plus an MCP server;
the compliance surface is a drop-in overlay, not a rebuild.

## The gap (scanned competitor docs, 2026-07)

| Product | Verifiable erasure (with receipt)? | Tamper-evident record-keeping? | EU AI Act framing? |
|---|---|---|---|
| mem0 | plain `delete()` — no receipt | none found | none found |
| Zep | delete + retention/legal-hold — no *verifiable* erasure | SOC2 + audit logs (not cryptographically tamper-evident) | none found |
| Graphiti | "invalidated, **not deleted**" | provenance/bitemporal (not tamper-evident) | none found |
| Letta, cognee, Memobase, LangMem, Redis, Pinecone | plain `forget()`/`delete()` — no receipt | none found | none found |
| **inspeximus** | **`forget_subject` + signed content-free tombstone + `erasure_certificate`** | **hash-linked receipts + signed anchor, verified offline** | **`inspeximus compliance` article-labelled overlay** |

*(“the only one **we've found**” — a scan of nine libraries, not an exhaustive proof of a universal negative;
Zep does have a genuine SOC2/HIPAA compliance surface, just not verifiable erasure or tamper-evident logs or
AI-Act alignment.)*

## What it gives the memory slice

- **Art. 12 & 19 — record-keeping / logs kept ≥ 6 months.** Every write is a hash-linked, timestamped receipt;
  `anchor()` signs a tree head over the whole history; `inspeximus audit-build` exports a **content-free** bundle
  an auditor re-verifies from genesis **offline** (`audit-verify`) — no live store, no key.

  **Receipts are OPT-IN, and off by default on the MCP server and the CLI.** Enable them with
  `receipts=True` in Python, or `INSPEXIMUS_RECEIPTS=1` for the server and `--receipts` for the CLI; a store
  that already has a `.receipts.json` sidecar keeps them on. The default is off because enabling them
  creates that sidecar next to your store, and a memory server should not grow files in your directory
  unasked — but it means **a fresh store has no chain, and this section's guarantee does not hold until you
  turn it on**. `verify_writes()` says so rather than reporting clean: *"write receipts are DISABLED: N
  record(s) exist with no write chain, so there is nothing to verify — which is not the same as verified."*
  This paragraph exists because the surface-conformance suite caught the omission on its first run; the
  behaviour is pinned in `tests/test_surface_conformance.py` so document and product cannot drift apart.
- **Art. 15 — accuracy, robustness, cybersecurity.** Keyed supersession serves the corrected value and resists
  the stale one resurfacing (`echo_guard`); the influence gate and witness co-signing resist memory-poisoning
  and operator-side tampering (`detect_split_view`).
- **GDPR Art. 17 — right to erasure.** `forget_subject` hard-deletes the subject *plus its derived lineage* and
  emits a signed, content-free tombstone; `erasure_certificate` is the portable proof-of-deletion.

## One command turns a store into a DPO-facing report

```bash
inspeximus --receipts remember "retention policy is 90 days" --key policy::retention --object 90d
inspeximus compliance --out report.html     # article-labelled evidence, LIVE counts from the store
inspeximus audit-build --out bundle.json    # hand the auditor the bundle...
inspeximus audit-verify bundle.json         # ...they verify it offline: exit 0 = PASS
```

`report.html` lists each control (Art. 12/15/19, GDPR 17/30/5(1)(d)) with the obligation, the inspeximus
evidence, and a **live count from your store** — marked `evidence` (exercised), `available` (shipped, not
exercised here), or `needs receipts`.

The same surface is callable **over MCP** — `compliance_report`, `compliance_check`, `retention`,
`audit_bundle`, `verify_audit_bundle` — so an agent (Claude Code, Cursor, any MCP client) can produce and check
its own AI-Act evidence. Start the server with `INSPEXIMUS_RECEIPTS=1` to keep the tamper-evident chain those
tools evidence.

Two of those take the argument that makes the check adversarial, and until 1.86.0 the MCP wrappers dropped
both: pass `compliance_check(prior_anchor=…)` with an anchor you pinned earlier out of band, or
`not_append_only` can never fire; pass `verify_audit_bundle(…, store_path="…")`, or the bundle's content is
never compared and a clean chain over substituted text verifies PASS. A `store_path` that does not exist is
refused rather than answered.

### Keep it enforced — the continuous compliance gate

A one-time report drifts. `inspeximus compliance --check` is a **CI gate** that exits non-zero the moment the
posture regresses — tamper-evident logging turned off, the receipt chain failing integrity, history that is no
longer an append-only extension of a pinned anchor, or PII kept past its retention window:

```bash
inspeximus compliance --check --max-pii-age-days 90 --prior-anchor last_anchor.json   # exit 1 on any violation
```

Drop it into CI or a pre-commit hook (`id: inspeximus-compliance-check`) so the agent-memory record-keeping
duties stay met between audits, not just on audit day.

And when the gate flags PII past its window, **enforce** it — the same erasure, receipted:

```bash
inspeximus retention --max-age-days 90            # DRY-RUN: what would be erased (GDPR Art. 5(1)(e))
inspeximus retention --max-age-days 90 --apply    # erase it, each deletion leaving an auditable tombstone
```

Dry-run by default; `--apply` hard-deletes past-retention records and writes a signed tombstone per record, so
the storage-limitation enforcement is itself part of the audit trail.

## The honest boundary (this is why you can trust it)

This is the **agent-memory slice only** — the records, corrections and erasures in *this* store. It produces
**evidence, not a certification.** The EU AI Act imposes far more than any memory library can satisfy (risk
management, data governance, human oversight, conformity assessment); those are the deployer's job. The
obligations bind the **controller / provider / deployer**, not the library. inspeximus gives the accountable
party the receipts, provenance, and provable erasure they use to *demonstrate* the memory-record duties — and
says so, in every report it prints. The Art. 17 erasure guarantee is only as complete as the encryption and
copy-control of the underlying store.

Full control mapping (NIST 800-53r5 / 218A / 600-1 / 88, OWASP LLM Top 10 / ASI, GDPR, EU AI Act):
[docs/COMPLIANCE.md](COMPLIANCE.md).

---

### Sources (article text verified against primary EU texts)

Article quotations were verified verbatim against the EU Publications Office consolidated texts (the primary OJ
manifestation): **Regulation (EU) 2024/1689** (AI Act) — EUR-Lex ELI `https://eur-lex.europa.eu/eli/reg/2024/1689/oj`
— and **Regulation (EU) 2016/679** (GDPR) — `https://eur-lex.europa.eu/eli/reg/2016/679/oj`.

**Application dates (updated 24 Jul 2026).** AI Act Art. 113 was amended by **Regulation (EU) 2026/1744**
(Digital Omnibus on AI, adopted 8 Jul 2026, OJ 24 Jul 2026, in force 27 Jul 2026 —
[ELI](https://eur-lex.europa.eu/eli/reg/2026/1744/oj/eng)), which **deferred** the high-risk obligations of
Chapter III Sections 1–3 from 2 Aug 2026 to:
- **2 Dec 2027** — standalone high-risk systems (Art. 6(2) / Annex III), where Art. 10/12/13/14/15/19 sit;
- **2 Aug 2028** — high-risk AI embedded in regulated products (Art. 6(1) / Annex I).

Unchanged by the deferral: Chapters I–II and the Art. 5 prohibitions (since 2 Feb 2025); GPAI, governance and
penalty provisions (since 2 Aug 2025, except Art. 101); the Art. 50 transparency duties and the Art. 4 AI
literacy duty.

**What this means for this document, stated plainly:** the record-keeping duties these tools produce evidence
for are **not yet in application** — they bite in Dec 2027. Nothing here should be read as "you are required to
have this today". Article 12 requires *automatic event logging over the system's lifetime*; it does not name
memory, provenance, lineage, attestation or tamper-evidence (its concrete minimum content list in Art. 12(3)
binds only Annex III point 1(a) biometric-identification systems, and the ≥6-month retention duty sits in
Art. 19). These features therefore **exceed** anything the text mandates rather than being required by it —
build with them because you would rather hold the evidence than reconstruct it later, not because a deadline
forces you. We have not tested how any conformity assessor or auditor actually treats them; we make no claim
about that.
