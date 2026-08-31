# Becoming the signed ledger for agent memory

Written 2026-08-31 from primary sources. Every claim here is either quoted from an RFC, an official
journal text, or a public issue thread. Where the evidence says the idea is weak, it says so.

## The one-sentence position

Be the **trust boundary beside the memory store** that signs what was written, in the formats an
auditor already knows how to check, so an operator can prove what an agent knew and when without
anyone having to trust the operator.

## Why this position is open, and it is not the reason we assumed

It is not open because nobody thought of it. It is open because everyone who thought of it declined
it for the same correct reason.

[mem0ai/mem0#4717](https://github.com/mem0ai/mem0/issues/4717), 5 April 2026, a third party asked the
market leader for signed integrity receipts on memory writes. The maintainer closed it on 8 April:

> "An attacker who can modify vector store entries could also tamper with any signing keys stored
> alongside them, so the security guarantee would only hold with external key management
> infrastructure that's outside Mem0's scope."

and then told users what to do instead:

> "we'd recommend wrapping `m.add()` and `m.get()` with your own signing/verification layer backed by
> a dedicated KMS, which gives you stronger guarantees and keeps the trust boundary under your
> control."

That is a description of this product, written by a competitor. In-store signing is genuinely weak,
so the store vendors are right to refuse, and the shape that remains is a separate layer with its own
key. Nobody has built it as a conformant standard implementation.

The maintainer also left the door open: "If there's significant community demand for this in the
future, we'd be happy to revisit." So the window is real and it is not permanent.

## What the evidence says AGAINST it, kept here on purpose

- **No paying demand today.** Nine agent-memory vendors were checked (Mem0, Zep, Letta, Cognee,
  Supermemory, LangSmith, Redis, Honcho): zero sell integrity. Every Enterprise upsell in the market
  is SSO, RBAC, self-hosting, retention, and SLA.
- **#4717 has zero reactions.** One requester, no crowd behind him.
- **The buy side is hard to find at all.** The UK DSIT market study (22 November 2024) sized AI
  assurance at 524 firms, GBP 1.01bn, 12,572 employees, and its consultants reported they "found it
  challenging to identify and engage with demand-side stakeholders in this market".
- **The regulatory trigger moved 16 months.** Regulation (EU) 2026/1744 (Digital Omnibus on AI, in
  force 27 July 2026) rewrote AI Act Article 113: Annex III high-risk now applies from **2 December
  2027**, Annex I embedded high-risk from **2 August 2028**.
- **No regulation requires signing.** The consolidated AI Act contains zero occurrences of `tamper`,
  `immutab` or `unalterab`; the GDPR contains zero `tamper`, `immutab` or `cryptograph`. Article 12
  requires logs to exist, Article 19 requires six months' retention, and neither mentions integrity.
  The only confirmed integrity mandate is NIST SP 800-53 **AU-9(3)**, HIGH baseline only, so it binds
  FedRAMP High and nobody else.

**Therefore this is a bet on a window, not a response to a queue.** Sell it as evidentiary quality
under GDPR Article 5(2) and AI Act Article 19, never as compliance. Say "helps you demonstrate", never
"makes you compliant".

## The standards to conform to, in the order they pay

Chosen because an auditor can check an artifact whose meaning comes from an RFC, and cannot check one
whose meaning comes from us.

| # | Standard | Status | What it buys | State |
|---|---|---|---|---|
| 1 | **RFC 9942** COSE Receipts | Proposed Standard, June 2026 | an inclusion proof any implementation reads | **done** |
| 2 | **RFC 9943** SCITT Signed Statements | Proposed Standard, June 2026 | WHO said it and WHAT it is about | **done** |
| 3 | Registration Policy + service identity | RFC 9943 section 5.1.1 | the right to call it a Transparency Service | next |
| 4 | **RFC 3161** timestamp from an EU Trusted List QTSP | ETSI EN 319 422 | eIDAS Article 41 presumption of time | after |
| 5 | Rekor v2 or Tessera as an external witness | GA October 2025 | a log we do not control | optional |
| 6 | DSSE / in-toto for release provenance | in-toto 1.2.0, SLSA 1.1 | provenance of the library itself | optional |

Skipped deliberately: C2PA (binds media, wrong layer), RFC 9162 as a citation target (Experimental,
and the ecosystem follows RFC 6962 plus C2SP static-ct-api), W3C VC 2.0.

## The points, in order

1. **RFC 9942 conformance.** Detach the payload. `84b3159`.
2. **RFC 9943 Signed Statements.** CWT Claims with issuer and subject; Transparent Statements with the
   Receipt in the unprotected header. `08edfc8`.
3. **A bound pair.** `Inspeximus.transparent_statement()` builds both from one inclusion bundle, and
   `verify_transparent_statement()` refuses a statement and receipt that are about different records.
4. Export the surface from `inspeximus` and give it CLI verbs, because a capability reachable only by
   writing Python against internals is one a stranger evaluating the package never finds.
5. Name the subject properly: the record's key, not `write:0`.
6. A Registration Policy, published and applied at registration time. This is the last thing standing
   between us and honestly saying "Transparency Service".
7. Documentation that states the honest scope, including everything in the section above that argues
   against the product.
8. Release.

## Two competitors found late and not yet examined

- `scopeblind` on PyPI, Ed25519 signing primitives, named in #4717 as the wrapper pattern's toolkit.
- `draft-farley-acta-signed-receipts`, a competing IETF receipt draft.

Check both before claiming any position. Not doing so is how a day gets spent on something filed
twenty hours earlier.
