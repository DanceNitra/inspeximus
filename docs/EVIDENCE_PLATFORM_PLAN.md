# inspeximus as the complete evidence product for AI agents under the EU AI Act and GDPR

Written 2026-09-15. This plan turns inspeximus from "the memory slice" into the whole evidence
product an operator of AI agents needs to demonstrate compliance. Stand-alone: no dependency on
any other vendor's audit tool. Every module lands inside this repository and this package.

Scope of the law is stated from the Official Journal texts (Regulation (EU) 2024/1689 as amended by
Regulation (EU) 2026/1744, and Regulation (EU) 2016/679). Verify each article quote against the OJ
before it goes into a public page. Nothing here is legal advice; the product produces evidence, the
accountable party produces compliance.

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

| duty | who | artifact a tool can produce | status |
|---|---|---|---|
| AI Act Art. 12 record-keeping: automatic event logs over the lifetime; for Annex III 1(a) systems also usage period, reference database, input data matched, natural persons involved in verification | provider | signed, chained event ledger: memory writes, corrections, erasures, **actions (tool and model calls)**, **oversight events** | memory: have. actions, oversight: build |
| Art. 19 and Art. 26(6): keep logs at least six months | provider, deployer | retention policy enforced and attested, export bundle | built 2026-09-16 (`attest_retention`, `archive` under a signed checkpoint, 2.31.0) |
| Art. 13: instructions for use, including how to collect and interpret logs | provider | generated "instructions for use" section describing the ledger schema and the verifier | built 2026-09-15 (`instructions_for_use`) |
| Art. 14: human oversight, ability to intervene and stop | provider (design), deployer (assign persons) | **oversight ledger**: approvals, refusals, overrides, stops, each a signed event naming the person or role | build |
| Art. 15: accuracy, robustness, cybersecurity, resilience to data and model poisoning | provider | measured poisoning defense (influence gate, echo guard), split-view detection, witness co-signing | have; add the measurement receipt to the report |
| Art. 10: data governance for training, validation and testing data | provider | for agent memory: provenance per record, PII report, source diversity, retention | have (`provenance`, `pii_report`, `retention`) |
| Art. 11 and Annex IV: technical documentation | provider | **Annex IV generator**: fills the sections that evidence can fill (2(a) to 2(g), 3, 4, 5) from the ledgers and reports, marks the rest as operator input | built 2026-09-15 (`annex_iv`, free skeleton); pro dossier open |
| Art. 17 and Art. 18: quality management system, keep documentation ten years | provider | export bundle with a signed manifest, versioned | have (bundle); add documentation retention |
| Art. 20, Art. 73: corrective actions, serious incident reporting (fifteen days) | provider | **incident ledger** with a report generator carrying the linked evidence | build |
| Art. 26: deployer duties: use per instructions, assign oversight, monitor, keep logs, inform workers, DPIA under GDPR Art. 35 | deployer | deployer view of the same ledgers, DPIA evidence appendix | built 2026-09-15 (`deployer_report`, 2.30.0) |
| Art. 27: fundamental rights impact assessment (public bodies and named private uses) | deployer | FRIA evidence appendix from the same data | built 2026-09-15 (`fria_appendix`, cross-references the DPIA per Art. 27(4)) |
| Art. 49: registration in the EU database | provider, some deployers | export of the identifying fields | built 2026-09-16 (`registration_export`, Annex VIII A, B, C) |
| Art. 50: disclosure that the user interacts with an AI system; marking of generated content | provider, deployer | **disclosure receipt** per session: what was shown, when, in which channel | build (small, applies now) |
| Art. 72: post-market monitoring | provider | periodic monitoring report from the ledgers | build (report mode) |
| GDPR Art. 5(2): accountability | controller | everything above, verifiable offline | have (verifier) |
| GDPR Art. 15: right of access | controller | **subject export**: every record, action and derived fact about a subject, with provenance | build |
| GDPR Art. 16: rectification | controller | keyed supersession with a receipt naming the correction | have; add a rectification receipt type |
| GDPR Art. 17: erasure | controller | signed content-free tombstone, residue check, offline certificate, cross-store manifest | have |
| GDPR Art. 22: automated decisions with legal effect | controller | oversight ledger shows the human review | build (same as Art. 14) |
| GDPR Art. 30: records of processing | controller | live record from the store | have (overlay) |
| GDPR Art. 35: DPIA | controller | evidence appendix | built 2026-09-15 (`dpia_appendix`) |

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
