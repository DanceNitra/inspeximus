# inspeximus as the complete evidence product for AI agents under the EU AI Act and GDPR

Written 2026-09-15. This plan turns inspeximus from "the memory slice" into the whole evidence
product an operator of AI agents needs to demonstrate compliance. Stand-alone: no dependency on
any other vendor's audit tool. Every module lands inside this repository and this package.

Scope of the law is stated from the Official Journal texts (Regulation (EU) 2024/1689 as amended by
Regulation (EU) 2026/1744, and Regulation (EU) 2016/679). Verify each article quote against the OJ
before it goes into a public page. Nothing here is legal advice; the product produces evidence, the
accountable party produces compliance.

## 0. The whole Act, step by step (owner's directive, 2026-09-17)

The owner, 2026-09-17: our path is THE WHOLE ACT, not "the agent-memory slice". This section is the
procedure we follow to get there, in the order we follow it, and it is the only place the procedure
lives. Section 2's status table below it is now GENERATED from `inspeximus.coverage` by
`tools/gen_coverage_table.py`, and a test fails when the two disagree, because the hand-written
table had gone stale for four duties within two days of being written.

**The rule of the procedure.** One duty at a time. For each: (1) read the article in the
consolidated text, not a summary; (2) name the artifact an operator would hand an assessor;
(3) build the call that produces it, with a receipt, and a probe in `inspeximus.coverage` that
counts it; (4) tests that move the row from CAPABILITY to EVIDENCE on a fixture and a mutation
that kills it; (5) the row's `gap` text is deleted, or the test
`test_every_not_covered_row_names_a_function_that_does_not_exist_yet` refuses the release;
(6) the release goes through `tools/release_check.py`; (7) the public claim changes only after the
matrix does. "The whole Act" is reached when `inspeximus coverage` prints zero NOT COVERED rows on
a fresh store; until then every surface says how many remain, from the matrix, not from memory.

**The order.** Small releases, each one gated, each one leaving a NOT COVERED row fewer:

1. **2.42.0, shipped 2026-09-18: the matrix itself.** `inspeximus coverage` (CLI and MCP), four
   states per duty, 36 in-scope duties, 2 not applicable with the reason, the plan table generated
   from it. 23 covered, 13 not covered.
2. **2.43.0, shipped 2026-09-18: Art. 9 and Art. 72.** `ActionLedger.risk()` (source, harm, measure
   and its kind, residual and its judgement, tests against a prior threshold, vulnerable groups,
   references that resolve) with `risk_register()`; `post_market_report()` from the ledgers
   (actions, oversight, incidents, rights, risks, retention, lifecycle, disclosures, the chain
   verdict) against a plan carried by name, version and hash, signed into the ledger as a
   `monitoring` entry. MCP: `record_risk`, `risk_register`, `post_market_report`. 25 covered, 11
   not covered.
3. **2.44.0, shipped 2026-09-18: Art. 20, 21, 86 and GDPR 33/34.** `corrective_action()` with
   the parties informed and `corrective_action_report(seq)` naming those not informed;
   `authority_request()` with what was provided by reference, never content (21(3));
   `decision_explanation(seq)` in one document from the chain, logged as `rights:explanation`
   when produced; `breach()` with the 72-hour clock, `breach_notified()` refusing a late
   notification without its reasons, the 34(3) exemptions, `breach_report(seq)`. MCP:
   `record_corrective_action`, `corrective_action_report`, `record_authority_request`,
   `decision_explanation`, `record_breach`, `breach_notified`, `breach_report`. 28 covered, 8 not
   covered.
4. **2.45.0: Art. 18, 43/47/48, 4, 5, 25.** `attest_documentation_retention` over the Annex IV
   document and the declaration; `record_declaration` with the Annex V fields linked to Annex IV;
   `record_literacy`, `record_attestation` (prohibited practices), `record_responsibilities`.
5. **2.46.0: GDPR 13/14, 20, 21, 28.** `record_notice`; the subject export labelled as the Art. 20
   response with a format version; `record_objection` that withholds the subject's records from
   recall; `record_processing_role`.
6. **Art. 15 and Art. 21 partials.** The poisoning, echo and split-view measurements carried into
   `compliance_report()` as receipts; the authority-request record above closes Art. 21.
7. **Then the surfaces.** ai-act.html rewritten as "evidence for every obligation of an agent
   operator", with the matrix on the page; README, bio and PyPI description without the "slice"
   wording; each through the standing gate.

Every step is also the evidence for the next: the risk register (Art. 9) feeds Annex IV section 3,
the monitoring report (Art. 72) feeds the corrective-action record (Art. 20), and the declaration
(Art. 47) is what the documentation retention (Art. 18) attests over.

## 1. What changed and why the plan exists

- The Digital Omnibus on AI (OJ 24 July 2026, in force 27 July 2026) moved the high-risk duties of
  Chapter III Sections 1 to 3 to **2 December 2027** (Annex III) and **2 August 2028** (Annex I).
  Article 50 transparency, the Article 5 prohibitions and the GPAI chapter kept their dates.
  GDPR applies in full today.
- The "sign every agent action" slot is occupied: Asqav (529 GitHub stars on 2026-09-15, hosted
  keys, Elastic License), AIR Blackbox (23 stars, scanner plus trust layers), and several small
  hash-chain loggers. LangChain closed two requests for a compliance callback as not planned.
- Nobody attests **what the agent knew when it acted** or **proves erasure from the agent's memory
  and recall path**. A data-governance vendor (Atlan) and a privacy law firm (Astraea Counsel)
  both describe that gap in 2026. A thesis (ReDit, UT Arlington, spring 2026) found 0 of 770
  open-source agents compliant with Article 12 and 256 with no logging.
- Decision (owner, 2026-09-15): build the whole product ourselves, on inspeximus, including the
  action-record half. Concepts are public patterns; code is ours (agent-receipts, June 2026, MIT).

## 2. What the law asks of an operator of AI agents

The table names each duty, who carries it, and what artifact a tool can produce. "Have" means
shipped in inspeximus 2.28.1. "Build" is this plan.

<!-- coverage:begin (generated by tools/gen_coverage_table.py; do not edit by hand) -->

28 of 36 in-scope duties covered by the library, 8 not covered, 2 not applicable. `inspeximus coverage` prints the same matrix for a real store, with EVIDENCE counts.

| law | article | duty | who | state | artifact or gap |
|---|---|---|---|---|---|
| AI Act | Art. 4 | AI literacy of staff | provider, deployer | NOT COVERED | a `record_literacy` ledger event (who, what, when) and a count in the deployer report |
| AI Act | Art. 5 | prohibited practices are not used | provider, deployer | NOT COVERED | a signed `record_attestation` event per prohibited-practice class, dated, in the ledger |
| AI Act | Art. 50 | disclosure that the user interacts with an AI system; marking of generated content | provider, deployer | CAPABILITY | record_disclosure() |
| AI Act | Art. 9 | risk management system over the lifecycle | provider | CAPABILITY | ActionLedger.risk() entries, risk_register(); 9(2)(a) to (d), 9(5), 9(9) fields |
| AI Act | Art. 10 | data governance: provenance, relevance, bias examination of the data the system works on | provider | CAPABILITY | provenance(), pii_report(), retention(); per-record source and lineage |
| AI Act | Art. 11, Annex IV | technical documentation | provider | CAPABILITY | annex_iv() |
| AI Act | Art. 12 | automatic recording of events over the lifetime | provider | CAPABILITY | ActionLedger.record(); actions verify; export-trail |
| AI Act | Art. 13 | instructions for use, including how to read the logs | provider | CAPABILITY | instructions_for_use() |
| AI Act | Art. 14 | human oversight: intervene, override, stop | provider, deployer | CAPABILITY | record_oversight(): approve, refuse, override, stop, review |
| AI Act | Art. 15 | accuracy, robustness, cybersecurity, resilience to poisoning | provider | CAPABILITY | receipt chain, verify_writes(), influence gate, echo guard, audit_the_audits(); partial: the poisoning and split-view measurements live in probes/ and are not yet carried into compliance_report() |
| AI Act | Art. 17 | quality management system | provider | CAPABILITY | compliance_report(), audit bundle; partial: the library stores and proves the QMS records; the QMS itself is the provider's |
| AI Act | Art. 18 | keep the documentation ten years | provider | NOT COVERED | an `attest_documentation_retention` statement over annex_iv and the declaration, like attest_retention over logs |
| AI Act | Art. 19 | keep the logs at least six months | provider | CAPABILITY | attest_retention(), archive under a signed checkpoint |
| AI Act | Art. 20 | corrective actions: withdraw, disable, recall; inform the chain | provider | CAPABILITY | corrective_action() with the parties informed; corrective_action_report(seq) |
| AI Act | Art. 21 | cooperation with authorities: hand over documentation and logs | provider | CAPABILITY | authority_request() naming the request, its scope and what was provided (by reference); the audit bundle, export-trail and compliance_report() are what is handed over |
| AI Act | Art. 25 | responsibilities along the value chain | provider, distributor, deployer | NOT COVERED | a signed `record_responsibilities` event naming the parties and the split |
| AI Act | Art. 43, 47, 48 | conformity assessment, EU declaration of conformity, CE marking | provider | NOT COVERED | a `record_declaration` event with the Annex V fields, linked to annex_iv; the assessment itself is the provider's |
| AI Act | Art. 49, Annex VIII | registration in the EU database | provider, some deployers | CAPABILITY | registration_export() sections A, B, C |
| AI Act | Art. 72 | post-market monitoring plan and reports | provider | CAPABILITY | post_market_report() signed into the ledger, carrying the plan by name, version and hash; partial: the plan itself is the operator's document; the Commission's template (Art. 72(3)) is not published yet |
| AI Act | Art. 73 | serious incident reporting within the deadlines | provider | CAPABILITY | record_incident(), incident_report() with the reporting clock |
| AI Act | Art. 26 | deployer duties: use per instructions, assign oversight, monitor, keep logs, inform workers | deployer | CAPABILITY | deployer_report() |
| AI Act | Art. 27 | fundamental rights impact assessment | deployer (public bodies, named private uses) | CAPABILITY | fria_appendix() |
| AI Act | Art. 86 | explanation of an individual decision to the affected person | deployer | CAPABILITY | decision_explanation(seq, actor=...): the action, what it knew, the oversight on it, in one document, logged as rights:explanation |
| AI Act | Art. 53 to 55 | general-purpose AI model provider duties | GPAI model provider | NOT APPLICABLE | an operator of an agent built on a model is not the model's provider; the model provider's documentation is an input to Annex IV, not this store's output |
| AI Act | Art. 60, 61 | real-world testing outside sandboxes, informed consent | provider testing pre-market | NOT APPLICABLE | a testing regime, not an operating one; the consent records it needs are the disclosure and oversight events above once a test runs |
| GDPR | Art. 5(2) | accountability: demonstrate compliance | controller | CAPABILITY | receipt chain, anchor, transparency log, offline verifiers |
| GDPR | Art. 13, 14 | information given to the data subject at collection | controller | NOT COVERED | a `record_notice` receipt per subject: what was told, when, on which channel; the disclosure receipt is the shape |
| GDPR | Art. 15 | right of access | controller | CAPABILITY | export_subject() |
| GDPR | Art. 16 | rectification | controller | CAPABILITY | rectify() |
| GDPR | Art. 17 | erasure | controller | CAPABILITY | forget_subject(), erasure_certificate(), erasure-verify |
| GDPR | Art. 20 | portability in a machine-readable format | controller | CAPABILITY | export_subject() returns JSON with provenance; partial: the export exists; it is not labelled as an Art. 20 response and carries no format version |
| GDPR | Art. 21 | objection: stop the processing | controller | NOT COVERED | a `record_objection` event that withholds the subject's records from recall, with a receipt |
| GDPR | Art. 22 | automated decisions: human review on request | controller | CAPABILITY | record_oversight(review) on the action |
| GDPR | Art. 25 | data protection by design and by default | controller | CAPABILITY | memory partitions with expiry, retention sweep, PII tagging |
| GDPR | Art. 28 | processor obligations and records | controller, processor | NOT COVERED | a `record_processing_role` per store (controller or processor, on whose instruction) |
| GDPR | Art. 30 | records of processing activities | controller | CAPABILITY | compliance_report() records section |
| GDPR | Art. 33, 34 | breach notification within 72 hours, and to the subject | controller | CAPABILITY | breach() with the 72-hour clock and the 33(3) fields; breach_notified() to the authority, the subjects or the public; breach_report(seq) |
| GDPR | Art. 35 | data protection impact assessment | controller | CAPABILITY | dpia_appendix() |

<!-- coverage:end -->

## 3. The product, in modules

One package, zero core dependencies, one file per module, one CLI, one MCP server. Every module
writes into signed, hash-chained ledgers that the free verifier checks offline. A paid tier
assembles, it never gates a primitive.

1. **Memory ledger** (have). Writes, corrections, erasures, revert, provenance, `as_of`,
   `why_recalled`, `state_digest`.
2. **Action ledger** (build first). `ActionLedger` records each tool call, model call, or
   external side effect as a signed event. Each event carries `memory_state`: the store's
   `state_digest`, the last receipt hash, and the ids recall returned since the previous action.
   This is the field no action logger has: the agent's knowledge at the moment it acted, bound
   into the same chain as the action. Verify offline. Hooks: LangChain callback, CrewAI, OpenAI
   Agents, MCP server dispatch, a plain decorator.
3. **Oversight ledger** (build). `approve`, `refuse`, `override`, `stop`, `review` events with an
   actor (person or role), a reason, and the action they refer to. Serves Art. 14 and GDPR Art. 22.
4. **Subject rights** (build). `export_subject` (Art. 15) across memory, actions and derived
   facts; `rectify` receipt (Art. 16); `forget_subject` and the erasure certificate (Art. 17, have).
5. **Incident ledger** (build). Record, link evidence, generate the Art. 73 report skeleton with a
   fifteen-day clock.
6. **Disclosure receipt** (build, small). One signed event per session stating the Art. 50
   disclosure shown to the user.
7. **Retention** (built 2026-09-16). The action ledger rotates under a signed checkpoint and attests its retention.
8. **Controls map and evidence report** (21 controls since 2.31.0, every row of section 2 that has a primitive;
   section 2). `inspeximus compliance` prints live status per control, `--check` gates CI.
9. **Annex IV technical documentation generator** (build). Free: the skeleton with evidence-filled
   sections. Pro: the branded dossier across stores and agents.
10. **Verifier, anchor, witness, timestamp** (have; 2.32.0 adds RFC 3161 tail timestamps inside the action ledger). `audit-build`, `audit-verify`, witness
    co-signing, RFC 3161 timestamps, SCITT and COSE encodings.
11. **Framework hooks** (partly have). Memory adapters exist for eight frameworks; add the action
    and oversight hooks to the same modules.

## 4. Order of work

Each step ships as a release with tests, a runnable probe, and a receipt. Nothing is announced
before it ships.

1. **2.29.0: action ledger, oversight events, disclosure receipts** (built 2026-09-15). Module, CLI,
   MCP dispatch hook and tools, LangChain callback, decorator; oversight and disclosure on the same
   chain; the compliance overlay grew from 7 to 11 controls. Probe: an agent that acts on a fact, the
   fact is corrected, the agent acts again; the two receipts carry different `memory_state` digests
   and the verifier rejects a rewritten action, a rewritten memory, a forged signature and a
   rewritten oversight reference.
2. **Subject rights** (built 2026-09-15, in 2.29.0): `export_subject` (Art. 15), `rectify` (Art. 16);
   the overlay reads both, 13 controls.
   Formerly planned as 2.30.0. `export_subject`, rectification receipt, report rows for Art. 15,
   Art. 16.
3. **Incident ledger and Art. 73 report** (built 2026-09-15, in 2.29.0): `record_incident`, `incident_report`,
   the clock per severity, the overlay row. Formerly planned as 2.31.0.
4. **Deployer report, DPIA and FRIA appendices, Annex IV skeleton** (built 2026-09-15, 2.29.0 and
   2.30.0). The controls map (21 rows), ledger rotation and retention attestation and the Art. 49 export
   followed in 2.31.0 (2026-09-16).
5. **Pro:** dossier across stores, hosted witness, DSR workflow. Only after an inbound signal.
   (inspeximus-pro 0.3.1, local, 2026-09-16: the pack carries the ledgers with archives, the three
   documents per store and the IETF trail; not distributed yet.)
6. **2.32.0 to 2.35.0 (night of 2026-09-15/16), from the market scan and the ISO/IEC 42001 and CNIL
   readings:** RFC 3161 timestamps chained into the ledger; the five agmi at-rest attacks measured
   (5 of 5 detected with receipts on); `model`, `principal`, `session` on every action and `agent`,
   `principal` on disclosures; the content-free `timeline()` of one workflow; the IETF
   draft-sharif-agent-audit-trail-04 export and verifier; lifecycle events including
   `substantial_modification` (Art. 3(23), the Art. 111(2) trigger) and `decommission` with the
   memory's disposition; `scope_covers` and `scope_excludes` on the erasure certificate.

7. **2.36.0 to 2.37.0 (2026-09-16):** memory partitions per agent and per process (CNIL);
   dogfooding the action ledger on our own MCP server found and fixed two defects in an hour (a recall
   made inside an action was attributed to the next entry; the MCP ledger was unsigned), 2.36.1;
   `ActionLedger.matches()` checks a retained transcript against an entry's salted digests and the
   LangChain callback digests the whole context with roles and tool calls, 2.37.0. The agmi
   measurement now has a second row: with the receipts sidecar in the attacker's hands (write access
   to the store's directory), 4 of 5, the tail truncation accepted without an anchor kept elsewhere;
   the README says so. An agmi adapter PR with three rows (default read path 5 accepted; receipts and
   sidecar 4 reported; receipts and file only 5 reported) is prepared and gated on the owner; a
   follow-up for the maintainer, measured: their OpenFang reference row (the fixed model) scores
   truncate accepted once the attacker rewrites the in-store tip, the same shape. The planned article
   "five at-rest attacks on three stores" was killed before drafting as already said by agmi's own
   README, with the mechanism textbook (Crosby and Wallach 2009, RFC 9162).

8. **2.37.1 (2026-09-16):** draft-sharif-agent-audit-trail-04 read again in full after a rescan
   flagged it: the chain hash covers the complete previous record with only a detached `batch`
   removed; our verifier stripped `signature` and would have called a conformant signed file broken
   at every link. Fixed with a mutation control; timestamps and nonces checked as the draft asks; the
   ledger's Ed25519 signature travels under `action_detail.inspeximus` because the draft registers
   only ES256 and ML-DSA-65. The competitor rescan of the same day found no cell change on
   ai-act.html (18 products checked, LangChain #35357 and #35691 still closed).

9. **2.38.0 (2026-09-16):** the owner refused the accepted cell ("we cannot lose anywhere"), and
   the answer was a head of the receipt chain kept in the config home, outside the store's
   directory, written after every receipt and never lowered by a write: the agmi directory
   attacker now reads 5 of 5, the attacker who also holds the config home 4 of 5. The red team
   found the first version's bypass (wait for the agent's next write) before release; the full
   suite found the rechain flow and the prune cost. PR tech4biz-yasha/agmi#1 opened the same
   evening with three rows.

### Open after 2.39.1

- ~~The write receipts keep an unsalted content hash of every record, erased ones included~~: closed in 2.40.0 by a per-record nonce folded into the receipt's content hashes (the nonce lives in the record, so live verification is unchanged and an erasure removes the guessable preimage; pre-2.40.0 receipts keep the old hash). Original note:
  Found by the 2026-09-17 red team on the erasure page's own run: `mem.json.receipts.json` carries
  `immutable_sha256 = sha256(canon({text, key}))` per write, so "Alice phone is +100" was recovered
  from the receipt of an erased record with a thousand guesses. The tombstone is content-free; the
  receipt is not. `actions.py` salts its input and output digests for exactly this reason. Options:
  a per-store secret salt beside the receipts (then a third party without the salt verifies the
  chain but not the content, the trade the action ledger already makes), or an HMAC keyed by the
  writer key. Either changes the receipt preimage, so it lands as a minor release with a format
  version, and until then both pages and the certificate's scope prose say the receipts file is
  personal data.
- **2.39.1 closed three verifier holes the same red team found** (a trimmed certificate, an
  unbound store, a ledger that did not re-hash the records) and one ledger read-side defect (a
  missing salt answered false instead of refusing). The witnessed-anchor input exists now; the
  pages still show an unwitnessed run with the NOTE line, because a demo cannot witness itself.

### Open after 2.38.0

- ~~A rectified record can lose a recall tie to an unrelated older record~~: closed in 2.39.0 at the
  tokenizer, not the tie. "alice's" stemmed to "alice'", so the corrected record matched one query
  token and sat level with two unrelated records; a possessive now folds to its noun and the corrected
  record matches both tokens. The decay-decided tie at exactly equal relevance is the designed weight
  and is left as it is. Original note: found 2026-09-16 while
  the chain-head prune made writes slow: `recall("alice phone")` scores "alice prefers email",
  "alice's phone is +200" (the rectified value) and "bob's phone is +300" all at 0.847, and with
  1.2 s between writes the rectified record comes second. Reproduced with heads off, so it is a
  ranking tie the recency rule does not settle, not the head. The test passes only because writes
  land within the same second. Fix the tie-break and make the test insensitive to write latency.

- ~~ai-act.html does not name the agent-audit-trail export or `matches()`~~: a FAQ entry and one
  paragraph, gated (two red-team lenses, verify 11 of 11, humanizer), live 2026-09-16 (061d918). The
  FAQ sentence "it does not see the prompt" was corrected at the same time; 2.37.0 made it untrue on
  the callback path.

- **prEN 18229-1 event classes and the ISO/IEC 24970 information model.** Both drafts are behind
  paywalls (CEN enquiry closed 5 Aug 2026; ISO FDIS ballot from 28 Aug 2026); tag entries with their
  classes only once the text can be read, and never claim conformity to a draft.
- ~~Per-agent and per-process memory partitions with expiry~~ (CNIL note, 20 Jul 2026): built
  2026-09-16, 2.36.0 (`inspeximus.partitions`: open, sweep, close with disposition, report; 22nd
  control).
- **The AI Office's FRIA questionnaire** (Art. 27(5) as amended) and the Art. 73 reporting template:
  map the appendix and `incident_report` onto them when published.
- **Distribution of inspeximus-pro:** the owner's call (Polar CTA and pricing are drafted in that repo).

## 5. Making it findable

The page `ai-act.html` leads with GDPR erasure (in force) and the AI Act date (2 December 2027),
names the audit-trail tools honestly, and answers the questions people search in a FAQ with
FAQPage markup. Google knew one URL of this site on 2026-09-15; indexing was requested for six.
Each release adds one page or section that answers one searched question with a runnable example,
and one inbound link from a host Google crawls daily (PyPI, GitHub, the LangChain providers
list). No claim goes on a page before the module ships and the standing gate has run.

## 6. What this does not do

It does not certify. It does not run a risk-management process or a quality-management system;
it stores and proves their records. It does not replace a conformity assessment. It does not
delete data outside the stores registered with it; it names them.
