# Verifiable erasure in three commands

**A delete that returns success tells you the call ran. It does not tell you the data left.**

In most stacks a value lives in more than one place — the vector, the payload beside it, and often a
history table or an audit log. A delete that clears one and not the others returns exactly the same
`200 OK` as a delete that cleared all three. The gap between "the call succeeded" and "the bytes are
gone" is invisible from the caller's seat, and it is the only part an erasure obligation is about.

inspeximus deletes the content rather than tombstoning it, and hands back a receipt you can re-verify
without trusting us. This page is the path from "I have a deletion obligation" to "I have a receipt":

1. **delete** — `forget-subject`
2. **certificate** — `erasure-certificate`
3. **verify the residue** — `residue`

Every command below is executed by `tests/test_erasure_quickstart.py`, which asserts the output printed
here. If a command or its output changes, that test goes red — this page cannot rot into fiction.

---

## Read this first: what the residue check does NOT tell you

A verification tool that overstates its reach is worse than none, so the three limits come before the
demo, not after it.

**1. It checks LOGICAL residue, not at-rest security.** It reads the current contents of files. A
plaintext store — ours or anyone's — also leaves bytes in filesystem free space, on over-provisioned
SSD blocks that the drive never exposes to the OS, in snapshots, and in backups. Nothing that reads
files can see any of that. The defence at that layer is full-disk encryption plus crypto-erasure
(destroy the key, not the row: EDPB 05/2019, NIST SP 800-88 cryptographic erase), and **this tool
cannot judge whether you have it**. A clean scan is a statement about your live files and nothing else.

**2. It matches LITERAL BYTES.** The search is a case-sensitive substring match for each value exactly
as you passed it. A stored value is caught; a paraphrase of it is not. Neither is a lowercased copy, a
copy with different whitespace, or a base64/hex encoding of it — `inspeximus/erasure_residue.py`
documents the measured eight-encoding table, and `tests/test_erasure_residue_matching_scope.py` pins it
so the gap cannot silently widen. **A clean result is evidence, not proof.** Pass every form you know
the value can take: `--value alice@example.com --value ALICE@EXAMPLE.COM`.

**3. A finding is not automatically a defect.** Point this at another system and a value still present
in an audit log, an event stream or a WORM archive may be exactly what that system was built to do —
retaining a deletion record is often a legal requirement, not a bug. The tool reports where the value
is and what kind of presence it is; whether a given hit is a defect or a deliberate design choice is a
judgement it does not make for you. It reports three kinds, and keeping them apart is the whole point:

| kind | meaning | what to do |
|---|---|---|
| `LIVE` | a SQLite table still holds the value in an addressable row | the system retained it — a retention question for whoever wrote that store |
| `UNRECLAIMED` | in the file's bytes but in no live row | a property of the storage engine, not a vendor's choice; `VACUUM` or compact |
| `PLAIN` | a non-SQLite file (JSON, JSONL, log, backup) contains it | nothing reclaims this on its own |

---

## Setup

`inspeximus` and `python -m inspeximus.cli` are the same program; use the second one from a source
checkout. Everything happens in one directory so the residue scan has a root to search.

```console
$ mkdir dsar
$ python -c "from inspeximus import new_receipt_keypair; sk, pk = new_receipt_keypair(); open('receipt.key','w').write(sk); open('receipt.key.pub','w').write(pk); print('public key:', pk)"
public key: 046f6596e0b68293fcf1b8f66d20ad06b4223b43aa19e413cfb9cf40be43587d
$ export INSPEXIMUS_RECEIPT_KEY_FILE=receipt.key
```

The key is minted by **you**. The secret half signs the tombstones; the public half is what an auditor
pins. There is deliberately no `--receipt-key <hex>` flag: a key on a command line lands in `ps`
output, in shell history and in CI logs, and a signing key that leaks makes every tombstone it ever
signed forgeable — the one property the certificate sells. Pass a file, or set
`$INSPEXIMUS_RECEIPT_KEY_FILE`. In production that file comes from your KMS and the secret half never
sits on the memory host.

Now four records about two people. The third is the one a naive text-match delete misses: a summary
built **from** Alice's record, which names neither her nor her address.

```console
$ inspeximus --path ./dsar/store.json remember "Alice Novak, alice@example.com, lives in Frankfurt" --key alice::contact --object Frankfurt --source alice@example.com
remembered 295d6c5490 [key=alice::contact]
$ inspeximus --path ./dsar/store.json remember "correction: Alice Novak relocated to Ohio" --key alice::contact --object Ohio --source alice@example.com
remembered 923e28b97a [key=alice::contact]
$ inspeximus --path ./dsar/store.json remember "summary: the Frankfurt hire is on the priority tier" --source analytics.internal --derived-from 295d6c5490
remembered 65f86e00a7
$ inspeximus --path ./dsar/store.json remember "Bob Weber, bob@example.com, lives in Munich" --key bob::contact --source bob@example.com
remembered 99e7c63beb [key=bob::contact]
```

Three things are now true and all three matter later. The second write **corrected** the first, so the
pre-correction value ("Frankfurt") survives as a superseded row — history a DSAR must still reach. The
third declares `--derived-from`, so it inherits Alice's provenance without mentioning her. And Bob is
in the same file, which is what makes the check at the end mean anything.

---

## 1. Delete

```console
$ inspeximus --path ./dsar/store.json forget-subject alice@example.com --request-id DSAR-2026-014 --basis "GDPR Art.17"
erased 3 record(s), 3 tombstone(s)
```

Three, not one. The subject's own record, the superseded value the correction retired, and the derived
summary that never named her. `--request-id` is your ticket number and `--basis` the legal ground;
both are recorded in the tombstones and bound into the certificate's hash chain.

A tombstone is content-free by construction — it commits to a surrogate id, a timestamp and the
request, never to the content, because a hash of PII is still PII. That is why the erasure can be
proved without re-exposing the thing erased.

> **Preview first.** `forget-subject --dry-run` reports the blast radius — how many records name the
> subject directly, how many are reached only through lineage, and which **other** subjects would go
> down with the request — and touches nothing. The `inherited` number is the one an operator cannot
> predict.

## 2. Get a signed certificate

```console
$ inspeximus --path ./dsar/store.json erasure-certificate --request-id DSAR-2026-014 --out cert.json
wrote erasure certificate -> cert.json  (3 erasure(s) attested, scoped to DSAR-2026-014)
```

`cert.json` is self-contained: the full signed tombstone chain (so it re-derives from genesis), the
request-scoped erased ids, the public key, a Certificate-Transparency-style anchor over the whole
history, and the certificate's own statement of what it does not certify. It carries no personal data.
Hand it to the auditor; hand them `receipt.key.pub` separately.

## 3. Verify the residue

```console
$ inspeximus residue --root ./dsar --value alice@example.com
checked 3 file(s) under ./dsar
RESULT: clean - no residue found
# exit status: 0
```

Three files: the store and its two sidecars. This searched all of them — including the receipt and
tombstone chains, which is where an audit trail would leak the content it is auditing — and found
nothing. Exit 0, so it drops into a DSAR runbook or CI as a gate.

`residue` is vendor-neutral. It takes a directory, not a inspeximus store: point it at a Chroma
directory, a sqlite history, a JSONL trace or another library's data dir and it answers for that
deployment. Values are never echoed back; each finding carries a short SHA-256 fingerprint instead,
so the report is safe to paste into a ticket.

---

## The control: a clean result you can believe

**A store that had silently wiped everything would pass step 3 perfectly.** So the scan is only
evidence if you also show it can still find something. Same directory, same command, the other
person's data:

```console
$ inspeximus residue --root ./dsar --value bob@example.com
checked 3 file(s) under ./dsar
  PLAIN        store.json   fp=5ff860bf1190
  ! a plain file still contains the value; nothing reclaims this automatically
RESULT: residue found (listed earlier)
# exit status: 1
```

Found, in the same three files that came back clean for Alice. The scanner is reading the right file
and can detect presence in it, so "clean for Alice" is a measurement rather than a silence. And Bob's
memory still answers:

```console
$ inspeximus --path ./dsar/store.json list -n 5
- Bob Weber, bob@example.com, lives in Munich [key=bob::contact]
```

Deleted gone **and** neighbour still there. Run both halves every time; the second half is the one that
makes the first mean anything.

That `PLAIN store.json` line is also limit 1 in the flesh: the store is a plaintext JSON file. Bob's
address is readable to anyone holding the file, and when Bob's own DSAR arrives, the bytes leave the
live file but the filesystem may still hold the previous version. Encrypt at rest and crypto-shred if
that matters to you — this tool cannot tell you whether you did.

---

## What the auditor runs (and why they need not trust us)

Verification takes no private key and does not trust the operator:

```console
$ inspeximus erasure-verify cert.json --store ./dsar/store.json --expected-pubkey-file receipt.key.pub
  OK   chain_intact
  OK   signatures_valid
  OK   signed
  OK   anchor_matches_tip
  OK   summary_derivable
  OK   attests_an_erasure
  OK   store_absent
  OK   scope_intact

VERDICT: PASS  (3 erasure(s) attested, absence checked)
# exit status: 0
```

What each check actually proves:

- **chain_intact** — every tombstone's hash re-derives, and each links to its predecessor back to
  genesis. Removing or editing one breaks the link.
- **signatures_valid / signed** — every tombstone's Ed25519 signature verifies against the pinned key.
  Without `--expected-pubkey-file` a signature is checked against the key carried *inside* the
  document, which a rewriter can replace with their own; pin the key you were given out of band.
  A certificate whose tombstones carry no signature reports `signatures_valid: n/a`, never `OK`.
- **anchor_matches_tip** — the anchor commits to the chain tip. If you witnessed an earlier anchor
  yourself, an operator who re-signs the whole history still fails against it.
- **summary_derivable** — the human-readable summary (`count`, `erased_memory_ids`, `request_ids`) is
  recomputed from the tombstones instead of being believed.
- **attests_an_erasure** — the certificate covers at least one erasure. See the defect below.
- **store_absent** — the strongest check: given the store, every erased id is genuinely **absent from
  the raw file**. This is the one a soft-delete system cannot pass. Without `--store` it is reported
  `n/a`, never `OK` — and a `--store` path that does not exist is refused rather than silently
  downgraded, because an empty store the typo just created would show every id absent.

`erasure-verify` is a thin wrapper over `inspeximus.verify_erasure_certificate(cert, store_items=...)`,
which is a public, dependency-light function you can read in `inspeximus/core.py`. Nothing in it is
secret and nothing needs our cooperation: the certificate is JSON, the hashes are SHA-256 over
sorted-key canonical JSON, and the signatures are stock Ed25519. An auditor who does not want to run
our code can re-implement the check against any crypto library.

### It fails on a tampered certificate

The scope statement is the sentence a regulator most needs — "NOT a compliance certification" — and it
used to be free text nobody compared. Rewrite it into something flattering:

```console
$ python -c "import json; c=json.load(open('cert.json',encoding='utf-8')); c['scope']='Full GDPR compliance certification, all systems.'; json.dump(c, open('cert-tampered.json','w',encoding='utf-8'), ensure_ascii=False, indent=2)"
$ inspeximus erasure-verify cert-tampered.json --store ./dsar/store.json --expected-pubkey-file receipt.key.pub
  FAIL scope_intact
  FAIL the `scope` statement does not match the one this library issues ...
VERDICT: FAIL  (3 erasure(s) attested, absence checked)
# exit status: 1
```

Every other field still derives from the chain, which is precisely why an unchecked scope statement
verified: a document whose limitations can be edited away is not a limited document. Editing a
tombstone's timestamp, swapping the erased ids, or forging the count fails the same way.

---

## A defect we found writing this page, and fixed

Measured 2026-08-01, on the axis this library is strongest on. **A certificate that attested to ZERO
erasures verified `valid: true`.**

Every other check in the verifier is a consistency check, and all of them pass *vacuously* on an empty
scope: no link to break, no signature to mis-verify, no id to still be present in the store. Two
documents came back valid:

- a store with no erasures at all — and `signatures_valid: true` on a document carrying no signatures;
- worse, `erasure-certificate --request-id DSAR-2026-999` on a **busy** store, for a request that was
  never performed. `signed: true` (other requests' tombstones are signed), count 0, valid true. An
  operator could hand a regulator an independently-verifiable certificate for a deletion that never
  happened, with every field in it honest.

`DeletionManifest.verify` already refused this ("nothing was audited, which is not the same as
verified") and `ErasureAuditor.audit` was fixed for it in the same terms; this verifier was the sibling
that kept the hole. It now refuses at both ends — the producer will not let you walk past it:

```console
$ inspeximus --path ./dsar/store.json erasure-certificate --request-id DSAR-2026-999 --out cert-never.json
wrote erasure certificate -> cert-never.json  (0 erasure(s) attested, scoped to DSAR-2026-999)
REFUSED as evidence: this certificate attests to ZERO erasures for request 'DSAR-2026-999'. ...
# exit status: 1
```

and the verifier fails it, with `attests_an_erasure: FAIL`. `tests/test_erasure_quickstart.py` pins
both directions.

### A second one: a mistyped `--derived-from` cost a record

Found the same way — by mistyping an id while writing the setup block above. `--derived-from` takes an
id that must already exist. Given one that does not, the library records the unresolved claim
(`derived_from_unresolved`, `orphan: true`) but the CLI printed `remembered <id>` and exited 0. The
operator is told the write succeeded and nothing is said about the lineage, which did **not** land: the
record inherits no taint, `forget-subject` cannot reach it, and it survives the DSAR that erased
everything else about that person — the exact opposite of what the flag promises. It now warns:

```
warning: 1 --derived-from id(s) do not exist in this store and were NOT linked: 0000000000. This
record inherits no lineage from them, so `forget-subject` will NOT reach it and it will survive
their erasure. Check the id, or re-write the record once the parent exists.
```

Still exit 0 — the write itself is legitimate, and a parent erased by an *earlier* DSAR is an honest
reason for an id not to resolve. To catch it systematically, read `coverage` in `erasure-audit`:

```console
$ inspeximus --path ./dsar/store.json erasure-audit --subject alice@example.com
  coverage  0/1 record(s) declare lineage (ratio 0.0)
  UNAUDITED  no record declares lineage, so nothing structural was inspected. This is NOT a pass ...
```

`UNAUDITED`, not `PASS`. A store where nobody declares lineage has nothing to inspect, and a check that
never sees its target must not report safe.

---

## Full scope

What the three commands together establish, and what they do not:

**They establish** that the records attributable to a subject — including what a correction retired and
what was derived from them — are absent from this store's raw files; that the act of deletion is
recorded in a hash-chained, signed, content-free ledger; and that the exact byte sequences you named
are not present in the files you pointed the scan at.

**They do not establish** anything about:

- **at-rest bytes** — free space, over-provisioned SSD blocks, snapshots, backups (limit 1 above);
- **stores you did not point them at** — the app's own vector index, prompt and retrieval logs, caches,
  an LLM provider's request logs. `forget-subject` covers *this* store. For the fan-out, register each
  location with `register_erasure_target()` and get a `DeletionManifest` that names any store still
  leaking, or probe them adversarially with `inspeximus/erasure_auditor.py` (`erasure_audit`);
- **paraphrases and re-encodings** of the value (limit 2 above);
- **model weights** that trained on the data, or text reconstructible from *retained embeddings*
  (Morris et al., "Text Embeddings Reveal (Almost) As Much As Text", EMNLP 2023). If a vector survives
  the row, treat the content as recoverable;
- **compliance itself.** These are integrity primitives that produce evidence. They are not a
  compliance certification, and the certificate says so in a field the verifier now checks.

Signatures are load-bearing only against a party who does **not** hold the receipt key: an operator who
holds it can forge tombstones too. For operator-adversarial audit, witness the anchor externally
(`verify_consistency`, `witness`) so a re-signed history fails against a head you saw earlier.

---

## The same path from an agent

The MCP server exposes each step as a tool, with the same scope statements in their output:
`forget_subject`, `forget_pii` (erase by PII type), `erasure_certificate`, `erasure_residue`,
`erasure_audit`, `erasure_report`, and `compliance_check` / `compliance_report` for the article-labelled
evidence view. `examples/11_verifiable_erasure.py` runs the whole path end to end, including both halves
of the control and the tampered-certificate check, and exits non-zero if any of it stops holding.

Related: [COMPLIANCE.md](COMPLIANCE.md) for the article mapping, [AI_ACT.md](AI_ACT.md) for the
record-keeping view, and `inspeximus/erasure_residue.py` for the measured matching-scope table.
