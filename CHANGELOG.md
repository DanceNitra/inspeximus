# Changelog

All notable changes to inspeximus (`inspeximus`). Format loosely follows Keep a Changelog; versioning is semver
(MAJOR = stable/breaking, MINOR = features, PATCH = fixes).

## 2.10.0 - AFFECTS NOBODY'S CODE UNLESS YOU OPT IN: two new features, no change to existing behaviour (one exception, below)

**Who should upgrade.** Anyone whose writers cannot vouch for what they produce — a sentence
splitter, an extraction pass, any pipeline that mints records from text it did not verify — and
anyone who needs an audit trail to tell a correction from a quiet rewrite. Both features are opt-in
(`provisional=True`, `reason=`); existing calls behave exactly as they did in 2.9.1.

**One behaviour change, and it is a narrowing.** `recall(include_superseded=True)` previously also
returned `candidate` records — rows whose IDENTITY was unresolved, surfaced by a flag named for
supersession. It no longer does. If you were relying on that to enumerate candidates, use
`candidates()`, which is the API for it.

Both features in this release were proposed by **[@yun520-1](https://github.com/yun520-1)**, in public
review on `NousResearch/hermes-agent#34352` and `openclaw/openclaw#7707`. They are his ideas; the
implementation and any defects in it are ours.

### Provisional records: the splitter may write, but it may not mint

We measured a period-and-capital sentence splitter at 30% wrong on hard text: "J. R. R. Tolkien"
becomes three fabricated first-class claims, one of which is the standalone fact `J.`. yun520-1 named
what that means:

> the splitter now gets to mint first-class records, which means the splitter is a trust boundary, and
> it is a worse one than the chunking it replaced (chunk boundaries are syntactic, sentence boundaries
> are semantic) ... the splitter must never be the sole authority that mints a record.

So `remember(..., provisional=True)` stores a record with its key, lineage and receipts intact and
**unretrievable** — by any flag — until something confirms it. `provisional()` is the queue,
`confirm(id, by=...)` accepts (recording WHO vouched; an unnamed voucher is written `unstated`), and
`discard_provisional(id, basis=...)` rejects. An unconfirmed record stays out of recall forever: the
cost of a forgotten confirmation is a missing memory, the cost of the other direction is a fabricated
one.

This is NOT the existing `candidates()` queue, and conflating them would be a bug. That one asks
WHICH KEY a write belongs to, and promoting one supersedes an authoritative value. This one asks
whether the CONTENT is real, and confirming one supersedes nothing at the moment of confirmation.

**Two defects found while building it, both by tests written against the feature rather than for it.**
A provisional write initially SUPERSEDED the authoritative value on its key: writing an unconfirmed
"Tolkien was born in 1802" against a verified 1892 marked the verified record superseded and left the
unconfirmed one invisible, so `recall()` returned NOTHING. A fabricated sentence would have silently
knocked out a real fact — strictly worse than not shipping the feature. `_supersede_by_key` now
refuses to let a non-authoritative record retire an authoritative one.

And the gate itself sat below the bitemporal branch, so `recall(as_of=...)` returned provisional
records; that branch returns True before any status check runs. It is first in `_eligible` now, and
it is an ALLOWLIST (`_RECALLABLE = {active, superseded, hub}`) rather than a list of statuses to
exclude — because the first version was a denylist, and `discard_provisional`'s new `discarded`
status fell straight through it into `return include_superseded`. A rejected record, surfaced by a
flag named for supersession, minutes after the gate above it was written. A status added next year is
invisible until someone deliberately admits it.

Side effect worth stating: `include_superseded=True` previously surfaced `candidate` records too.
It no longer does.

### Amendments record why, and the reason is committed

`amends` says WHICH committed field a receipt legitimately rewrites. It did not say why, and as
yun520-1 put it, "if the amendment record doesn't say why (corrected typo vs updated fact), the audit
trail can't distinguish a correction from a quiet rewrite."

`slash()` and `restore()` now take `reason=`, recorded on the amendment **inside the receipt hash**.
Forging it or stripping it breaks the chain: it can be contradicted, never rewritten. A caller who
states nothing gets `unstated` recorded rather than the field omitted — a missing key is
indistinguishable from a caller who had nothing to say.

It went into `_chain_core`, the one shared preimage definition, and that immediately broke the offline
bundle verifier: `audit_bundle` kept a hand-maintained list of preimage fields two hundred lines away
from the preimage, so `verify_writes()` said clean while the bundle said "write chain breaks at index
1". The same thing had happened with `amends` in 1.68.0 and was fixed by special-casing that field,
which left the defect in place. The export is now DERIVED from `_chain_core`, so there is nothing left
to forget a field in.

## 2.9.1 - UPGRADE IF YOU ERASE THROUGH THE LLM ERRATA ADAPTER: an erasure claimed success it had not achieved

**2.9.0 returned `aggregate: verified` for an `erase` erratum while the erased proposition remained in
the store, and on disk, verbatim.** `retire()` demoted the record and kept its text in both branches.
The reference contract keys this on `superseded_at`: the protocol supplies it only for a supersession,
where the proposition was true until an instant and the history is evidence. Its ABSENCE means
correction or erasure, where the content must go. We treated both the same, and the retaining way.

Fixed: the destructive branch now calls `forget()`, the store's verified-forgetting primitive, which
hard-deletes the record, scrubs its id from surviving links and supersession pointers, drops cached
vectors and writes a content-free tombstone. The capability was always there; the adapter never
reached for it.

**Two further defects, one of them introduced by that fix, and both fixed here.**

`forget()` reports `{"forgotten": 0}` without raising when the id falls outside the caller's tenant
rows. The first version of the destructive branch discarded that result and no longer set a status, so
an erasure that removed nothing left the record ACTIVE, which is worse than the demotion it replaced.
The result is now read; a removal that did nothing demotes the record and is recorded.

**And the erasure is still not complete, which is why `coverage()` no longer says `verified`.**
`forget()` documents its completeness on the premise that "consolidation never copies raw text into
other records". That premise does not hold for `remember(derived=True)`, which copies the text
verbatim, so a summariser's derivative can still hold the erased proposition after a successful
forget, and the record that would have noticed is the one just destroyed. A surviving copy is now
recorded and coverage reports `partial`. Widening the deletion to every record whose text contains
the proposition is a data-loss decision that needs its own review; the specification's own rule is
that incomplete disposal stays `partial` or `unknown` rather than silently complete.

Checked before shipping, because the obvious worry is trading a disclosure bug for a data-loss bug:
after a destructive retire, `erasure_audit()` still returns `residue_found` and `verify_writes()` is
clean. The audit trail survives by design and destroying content on a correction is what the reference
contract requires.

Found by our own candidate conformance case, but only after an independent adversarial pass showed
that case was itself vacuous: it searched present-tense recall, where concealment and erasure are
identical. Strengthened to search the persisted state, it failed immediately, against us.

## 2.9.0 - UPGRADE IF YOU IMPLEMENT LLM ERRATA: the adapter is rebound to the corrected spec surface

Rebound from `a477fe4f` to **`ac4468faf73c2cc7949dd29b2a2a151f5bd23116`**, canonical G2 digest
`7e0d6c88c1ca3a87743ac70ba2a3dfea0b350d112d2d3c59a3c6cbb537568f12`.

**The spec author accepted and fixed both findings our implementation produced.** The durable checkpoint
now records `adapter.quarantine_coverage(root)` instead of inferring `verified` from a successful
enumeration, with a direct regression test for the `partial -> verified` mismatch we reported; and
`StoreAdapter` now declares the full runtime surface, with `retire` reduced to one keyword-only
signature. The record-decomposition requirement in the implementer contract came from a mistake in our
own test fixture, where two propositions shared a record.

**`register_into` is deleted, and that is the point.** It existed only to register our lineage into the
reference `LineageLedger`, an undocumented coupling we found when a repair silently retired every gated
artifact and rebuilt nothing with no exception raised. `RebuildStrategy` now asks the adapter through
`source_artifact()` and `repair_inputs()`, so an external store no longer registers anything. Measured
here: the full quarantine, repair and attest cycle completes against the reference controller with an
empty reference ledger.

```
clean store                      checkpoint=verified  triad all pass  aggregate=verified
store with one undeclared deriv.  checkpoint=unknown   triad all pass  aggregate=unknown
```

New: `quarantine_coverage`, `source_artifact`, `repair_inputs`, `snapshot`.

**One defect of our own, found by reading the store rather than the receipt.** `rebuild` re-asserted
every named input's payload, including inputs that were never gated and were still active, so after a
repair the store asserted "prefers quiet restaurants" and "moderate budget" twice each. The receipt was
clean either way: the preservation check asks only whether a term is recallable, and a duplicated fact is
recallable exactly as well as a single one. Now only a gated input is re-asserted.

Standing: G2 and G4 remain BLOCKED and the repository remains NOT_PROD_READY. Our 2.7.0 is recorded
upstream as an externally authored candidate with a claimed clean-room rewrite, not as established
independent evidence, and a third producer must validate both implementations. That is the correct
treatment and we asked for it.

## 2.8.2 - AFFECTS NOBODY'S CODE: a test that checked our documented commands was passing for the wrong reason

No library change. `test_docs_examples_are_runnable` extracts every `inspeximus ...` line from the
README and DEEP_DIVE, runs it in a fresh temp directory, and requires exit 0. It passed the subprocess
`{**os.environ}`, and **eighteen** test modules assign `INSPEXIMUS_*` into `os.environ` directly rather
than through `monkeypatch`, so the value survives for the rest of that worker process.

Inherited, `INSPEXIMUS_PATH` sent the documented `remember` to some other temp store, and the documented
`audit-verify bundle.json --store inspeximus_memory.json` two lines later then found nothing:

```
FAIL --store inspeximus_memory.json does not exist; refusing to create a store while verifying
```

So the test's verdict depended on WHICH TESTS RAN BEFORE IT. Green on a dev machine and in the release
job; red in the `integrations` job, where the optional extras pull in more modules and the ordering
changes. It had been measuring "this works if a stale variable happens to be set", not "this works when a
reader pastes it".

The subprocess now runs with a READER'S environment: every `INSPEXIMUS_*` key is stripped. That is the
correct behaviour independently of the leak, because someone copying a command out of our documentation
has none of our variables set.

Reproduced before and after rather than assumed: with `INSPEXIMUS_PATH` exported, the old code fails and
the new code passes.

**Not fixed here, and stated so it is not mistaken for done:** the eighteen modules still write to
`os.environ` directly. This closes the one route the failure arrived by, not the class.

**And a second one found while fixing the first.** The release-notes gate refused this entry because its
heading names no audience, and its message offered two remedies: name the users, *or say plainly that it
affects nobody's code*. Only the first was implemented. A release that genuinely changes nothing
observable had no honest way through except to invent an audience for itself. The second remedy now
exists, this entry uses it, and a test asserts both arms plus a control that a heading naming nobody is
still refused.

Worth recording how that fix failed twice before it took. The branch was written with `` word
boundaries that a heredoc turned into literal **backspace characters** (``), which are invisible when
the line is printed, so the source read correctly, the pattern matched when retyped by hand in a test, and
the file itself could never match. Two rounds of "the code I am reading is not the code that runs" before
`repr()` showed it.

## 2.8.1 - UPGRADE IF YOU TOOK 2.8.0: it added ~10% to every stored record to write down the absence of a claim

2.8.0 wrote `valid_from_source` on every record, `declared` or `ingest`. CI's work-counter gate caught
what that costs, on three benchmarks independently:

```
write_n1000.serialized_bytes       147,642,011 -> 162,159,535  (+9.8%, band +/-2%)
erase_k200_n2000.serialized_bytes      552,251 ->     610,452  (+10.5%)
session_n500.serialized_bytes       36,607,471 ->  40,233,984  (+9.9%)
```

The gate offers to re-record the baseline, and we had a justification ready. We did not take it. Paying a
tenth of every user's disk to record, on every record, that nobody made a claim is the wrong trade.

**Presence is now the claim.** `valid_from_source: "declared"` is written only when a caller actually
supplied an event time. Its ABSENCE means nobody asserted one, which is the fact an auditor needs and is
equally true of a defaulted record and of a pre-2.8.0 record. Nothing is lost: the positive claim still
carries its own evidence, and the ambiguity 2.8.0 set out to remove -- `valid_from == ts` meaning either
"true from then" or "nobody told us" -- stays removed.

After the change, no regression: 147,626,351 / 552,353 / 36,605,878 against baselines of 147,642,011 /
552,251 / 36,607,471.

The performance gate did not make us drop the feature. It made us design it properly, which is what an
exact counter is for: "a change here is real work being done that was not being done before."

## 2.8.0 - UPGRADE IF YOU BACK-DATE FACTS: `valid_from` took only a float, and never said whether anyone declared it

Two defects in the same field, found by the Scout reading someone else's PR.

**`valid_from` accepted only an epoch float.** `remember(..., valid_from="2024-03-01T00:00:00Z")` --
the natural thing to pass, and the form every other timestamp on the record already uses (`iso`) -- died
with `ValueError: could not convert string to float`. It now goes through `_iso_to_epoch`, the parser
already in this file, rather than a second implementation of the same guarantee.

**An unparseable value now RAISES instead of falling back to the ingest time**, and the message names a
form that works. A silent fallback would be the exact defect the next paragraph closes: a guessed event
time that reads as a declared one.

**`valid_from` defaults to the ingest time and nothing recorded that it had.** So `valid_from == ts` was
ambiguous between "the fact became true when we wrote it" and "nobody told us and we used the clock".
Records now carry `valid_from_source`: `declared` or `ingest`. This is the same shape as a `source` field
holding the WRITER rather than the origin, which is how a store reaches 98.3% source coverage with 0.01%
actually re-checkable.

It is surfaced in `as_of`, not merely stored. A field nothing reads is decoration, which is how a README
marker deleted in a trim broke registry publishing for a whole release with every test green (2.6.1).
Records written before this existed report `None` rather than `ingest`: unknown provenance is a different
claim from "we defaulted it", and guessing would re-create the ambiguity the field exists to remove.

Credit: the distinction is named `eventTimeSource` in joshuaswarren/remnic#1666, which our GitHub Scout
surfaced as a `learn` lead. We already shipped bi-temporal `valid_from`/`as_of` and had measured it as
parity rather than an advantage; what we did not have was the provenance of the timestamp.

Safe by construction: `valid_from` is in neither the receipt commit (text/key/mtype/object) nor
`state_digest` (id, status, ts, key, tenant, content hash), so old stores and old receipts verify
unchanged and a reader that does not know the new field ignores it.

Verified by mutating the fix back out: 5 of the 7 new tests fail on the old code, and the two that still
pass are the epoch-number regression test and the control, which is what they are for.

## 2.7.0 - UPGRADE IF YOU WANT TO RECEIVE CORRECTIONS FROM ANOTHER SYSTEM: an LLM Errata importer adapter that conforms

`inspeximus.integrations.llm_errata.InspeximusErrataAdapter` implements the LLM Errata importer contract
(Thomas Willner, https://github.com/thomaswillner/llm-errata) at frozen commit `a477fe4f`. It runs the
full quarantine, repair and attest cycle through the reference controller:

```
clean store                       triad all pass    stores=verified   aggregate=verified
store with an undeclared deriv.   triad all pass    stores=unknown    aggregate=unknown
```

**Provenance, because the contract turns on it.** The 2.6.1 version of this file was written with
`prototype/adapters.py` open, and its `rebuild` reproduced the reference algorithm line for line,
including the arbitrary `"; "` separator. `INDEPENDENT_IMPLEMENTATION.md` excludes exactly that ("Shared
reference-adapter code disqualifies the implementation as independent evidence"), so the file was
rewritten from the protocol signature and the prose requirements alone. Measured after the rewrite: zero
shared runs of three or more consecutive lines with the reference, and three shared single lines, being
`from __future__ import annotations` and two protocol signatures that must match by definition. Disclosed
upstream rather than quietly corrected.

**`rebuild` appends; it does not rewrite.** inspeximus is append-only, so a repair writes the correction
and the surviving payload as new active records and leaves the quarantined originals superseded and
readable under `include_superseded`. A store whose repair destroys the pre-repair state cannot answer
"what did you hold before the correction", which is half of what a receipt is for. First version anchored
the correction to the QUARANTINED record, so taint flowed from the demoted parent and the store came back
with nothing active; the correction is now asserted with no local parent and the preserved payload
derives from it.

**`lineage_complete(root)` is how we earn `verified`.** An empty artifact list is never evidence of
absence on its own. A record that announced derivation and resolved no parent might descend from this
root and the store cannot say it does not, so one such record anywhere makes the walk incomplete for
every root and coverage reports `unknown`.

**What the published protocol does not carry, found one failure at a time.** `StoreAdapter` declares
`enumerate`, `lineage_complete`, `quarantine`, `is_quarantined`, `coverage`, `dispositions`. Repair also
requires `rebuild`, `retire` (in two signatures discoverable only through a `TypeError` fallback),
`recall(term)` returning objects with `.content`, optionally `source_artifact`/`source_of`, and
registration into `importer.ledger` via `register_import`/`register_derivation`, which
`INDEPENDENT_IMPLEMENTATION.md` never mentions. The last one fails silently: without it the rebuild
strategy retires every gated artifact in pass one, rebuilds nothing in pass two, and the receipt reads
FAILED with no exception raised anywhere. Reported upstream.

## 2.6.1 - UPGRADE IF YOU INSTALL US FROM THE MCP REGISTRY: 2.6.0 reached PyPI but its listing was refused

No library change. 2.6.0 published to PyPI correctly and the MCP registry rejected the accompanying
listing, so the registry still pointed at 2.5.0.

The registry proves ownership by reading a marker out of the README of the PUBLISHED package:

    mcp-name: io.github.DanceNitra/inspeximus

`ddf47a3` cut the landing page from 1,068 lines to 156 and took that line with it. Nothing failed at the
time. Tests, audits and CI are all blind to it because no code reads it, and it surfaced one release
later as a 400 from the registry. It could not be repaired in 2.6.0 either, since the registry reads the
artifact that is already published -- which is the whole reason this is a version of its own rather than
a note.

Restored, with a guard (`tests/test_mcp_registry_ownership.py`). The guard reads the expected name from
`server.json` rather than a literal, because that name is the registry's join key and a rename with the
README left behind breaks publishing the same way; and it resolves the readme path out of `pyproject`
instead of hardcoding `README.md`, since a guard aimed at a file by name keeps passing on the day the
document moves. Verified by re-removing the marker: the guard fails and both controls stay green.

## 2.6.0 - UPGRADE IF YOU READ erasure_audit AS A DELETION SIGN-OFF: it returned the pass verdict on partial coverage

`erasure_audit()` demoted its verdict to `unaudited` when declared lineage was **exactly zero**, and the
principle behind that rule is wider than the rule was. One resolvable edge in a store of four hundred
records that had announced derivation and resolved none was enough to return `no_declared_residue` -- the
pass verdict -- for a walk with four hundred known holes.

**Reported against us by Thomas Willner, in the LLM Errata `PRIOR_ART.md`, while we were reviewing his
spec.** His sentence, verbatim: "Its tests force `unaudited` when declared lineage is zero, but a nonzero
incomplete ratio can still return `no_declared_residue`." He is right. It is also the mirror image of the
finding we had just filed against his prototype the same day, where an adapter that enumerates cleanly and
returns an empty list was recorded identically to one that was walked and genuinely had none. Same defect,
two codebases, each of us blind to our own copy.

**The gate is the orphan count, not `declared_ratio`.** A threshold on the ratio would have been an
absolute cut on a relative score: most records are roots and derive from nothing, so a healthy store sits
at a low ratio permanently and any cut either fires always or never. `undeclared_derived` counts records
that ANNOUNCED derivation the walk could not resolve, which is evidence of a hole rather than a proportion
nobody can calibrate. New verdict:

```
residue_found | partially_audited | no_declared_residue | unaudited
```

`no_declared_residue` is now the only pass, and it asserts exactly one thing: every record that announced
itself as derived resolved its parents, and walking those parents found nothing surviving that carries the
erased material.

**Second, narrower finding, ours: store-wide coverage cannot vouch for one subject.** Our own test carried
the admission in its assertion comment -- "lineage exists elsewhere, so not `unaudited`" -- and the lineage
that existed was about billing, while the erased subject was `user-42`. Not one edge the walk followed
could have reached the erased material. `coverage` now reports `subject_reachable_records`: the surviving
records the walk could actually follow to the subject asked about, `None` when no subject was given.

It is REPORTED and deliberately does not gate the verdict. After a correct cascade the derivatives are
erased too, so a reach of 0 is also what success looks like, and tombstones are content-free by design (a
hash of PII is still PII), so nothing at audit time can separate "the cascade erased them" from "they were
never declared". A gate a correct erasure can never pass measures nothing, which is the same defect facing
the other way.

**Reproduction, and why the tests are worth something.** `test_one_resolvable_edge_does_not_buy_a_pass_for_a_store_full_of_orphans`
fails on the pre-fix code with `no_declared_residue` and passes after. Measured by mutating the shipped
branch back to `elif False:` and re-running: 1 failed, 16 passed, and BOTH negative controls
(`test_CONTROL_complete_lineage_still_earns_the_pass`, `test_a_ratio_threshold_would_have_been_the_wrong_gate`)
stayed green under the mutation -- which is what says they measure something other than the defect, rather
than following the verdict wherever it goes.

## 2.5.0 - UPGRADE IF YOUR MEMORIES COME FROM SOURCES THAT CHANGE: decay was temporal, and time is the wrong question

`check_sources()` answers the causal question instead of the chronological one: **did the thing this
memory is about actually change?** Age cannot tell a fact that has been true for five years from one
that rotted in a week. Four verdicts per record — FRESH, DRIFTED, ORPHANED, and UNCHECKABLE — and the
report leads with the last one.

**This is a gap we measured on ourselves rather than imagined.** On 2026-08-10, across our own
deployment: 210,544 records, `source` coverage **98.3%**, sources resolving to anything re-checkable
**0.01%** — twenty-four records. The field held `agent:scholar`: the identity of the WRITER, not the
origin of the content. So source reconciliation here was not unimplemented, it was impossible, because
there was no key to diff against. The probe is public
(https://github.com/DanceNitra/agora/blob/main/research/probes/can_we_reconcile_our_own_index.py —
it lives in the agora repo, since it measures that deployment) and carries its own control,
since a 0% can be a broken classifier.

Credit where it is due: the causal framing is the right one and we saw it done first elsewhere.
OmniMemory (SinghAbhinav04) flags a memory stale only when *its symbol — or a symbol that calls it —
changed in git*, rather than when a file was touched. Anchoring to git gives that design the
re-checkable key by construction. `remember(source={"doc": <path>})` now fingerprints the source with
SHA-256 at write time when the doc is a file that exists, which is the same idea reached through a
different door.

**The fingerprint is NOT stored in `source`.** That dict is the caller's, verbatim — a digest a writer
can set is a drift check the writer can defeat, which is exactly the trust-tier hole closed in 2.4.1.
It lives in the reserved meta keyspace, which callers cannot write. A mutation that moves it back into
`source` turns three tests red.

**A report that cannot flatter.** `ok` is False whenever nothing was checkable, and the report says
"this verified NOTHING" — because zero drifted over zero checked is the same sentence as a clean store
and must not read like one. This is the `verify_attestations` rule pointed at the outside world. It
matters most on stores like ours, where 98.3% of records are UNCHECKABLE and a naive report would have
returned a clean bill.

**Scoped, unlike `verify_attestations`.** That one is store-level because a record relocated between
tenants is only visible whole-store. Drift is per-record and per-source, so a tenant's report is
complete inside its own slice — and the report carries record ids, which is precisely what must not
cross a tenant boundary.

`resolver` is an optional callable taking the source doc and returning bytes (or None if gone), so this
can be pointed at a git object store, S3 or HTTP. The default reads local files only, deliberately:
guessing how to fetch an arbitrary identifier is how a checker starts inventing ORPHANED verdicts.

## 2.4.1 - UPGRADE IF YOU RELY ON THE `warrant` TIER: the top tier could be asked for through `meta`

Raised by yun520-1 (openclaw/openclaw#7707): "any design where the tier is set by the writer has a
forgeable top tier, because the writer is motivated to mark itself highest." Correct in the general
form, and further than the specific case named.

**2.4.0 closed the front door and the hole moved one level down.** That release stopped
`mtype="semantic"` from reaching the top tier by additionally requiring `meta.graduated_from_episodic`,
which the library stamps at the corroboration bar. Measured on the published 2.4.0:

```
remember(mtype="semantic")                                          -> unwarranted
remember(mtype="semantic", meta={"graduated_from_episodic": True})  -> earned      <-- the hole
remember("a plain record")                                          -> unwarranted   [control]
```

`earned` is the strongest tier we report, and it was reachable on a record with no outcome credit, no
links and no witnesses — by asking. The cause is structural rather than local: `remember(meta=...)`
copies the caller's dict onto the record verbatim, and the library reads its own decisions back out of
that same dict. So the fix is a **reserved keyspace**, not another condition: 23 keys the library
stamps and then reads are stripped from caller-supplied meta. Stripped silently, not refused — a
writer probing for the tier gets no error and no privilege, and no honest caller was setting them.

**Not an ACL bypass**, checked before saying otherwise: a grant is identified by the reserved `key`
prefix through `_is_acl_record`, which `remember()` already refuses to mint, so a caller-supplied
`meta["acl"]` never becomes a grant. It is reserved because the library reads it.

`uid`/`aid`/`sid`/`project` are **routed** to their named parameters rather than dropped: passing them
through `meta` worked, so someone may depend on it, and silently discarding their value would break a
caller who is getting the behaviour they intended. The explicit parameter wins on conflict. This
converges two routes into one; it does **not** add validation — `remember(agent_id="*")` stores `*`
unchecked too, because `_check_agent_id` guards the grant path and `remember()` never calls it.

**The library writes these keys through the same door**, so four internal call sites — two revert
paths, session open, session digest — go through `_stamp()`, the escape hatch `grant()`/`revoke()`
already use for the reserved key prefix. Closing this broke two things on the way, both caught by the
existing suite rather than by the tests written for the fix:

* `_stamp` is a private name, and `_TenantView` forwards private names to the PARENT — so every
  internal marker was written with `tenant=None` and a tenant's session digest came back EMPTY. It is
  now rebound on the view.
* renaming `self.remember(` to `self._stamp(` hid a write site from the internal-lineage audit, which
  scans for the call by name. The scanner now recognises both.

**The drift guard is the part meant to outlive this release.**
`test_every_meta_key_the_library_reads_is_reserved` scans the source and fails if the library reads a
meta key a caller can still set. It earned its place on first run by finding `entries`, a key dismissed
as a regex artefact while the list was being assembled by hand.

## 2.4.0 - UPGRADE IF YOU RUN TENANT- OR PROJECT-SCOPED STORES: the signature did not cover which tenant a record belonged to, and nothing could re-check it anyway

Two gaps, one raised by an external reviewer and one found while closing it.

**The tenant is now part of the signed message.** A writer-key signature covered `{text, source}`, so
it stayed valid after a record was moved into another tenant's rows — the one fact tenant isolation
most needs to be non-repudiable was the one the signature did not cover. Raised by yun520-1
(NousResearch/hermes-agent#34352), who signs the binding in their own design for exactly this reason.
`tenant=None` is OMITTED rather than serialised as null, so **unbound stores produce a byte-identical
message to every earlier version and their existing signatures keep verifying**. A record moved
between the two regimes now fails in both directions.

The asymmetry that remains, stated rather than buried: **externally-attested records are not
tenant-bound.** An outside source signs "I authored this text, as this source" before it ever reaches
a store and cannot sign a binding to a tenant it has never heard of, so `remember(..., attestation=...)`
rows still verify in any tenant. That is not closable from this side.

**`verify_attestations()` — because until now the signature was written and never read.** 2.3.0 began
storing `attested_sig` precisely so an auditor would not have to take the write path's word for it; its
changelog says "a non-repudiable identity you cannot re-verify is not one" — and then shipped no way to
re-verify it. Across the whole library the field was set in two places and checked in none; the only
verification anywhere was hand-rolled inside a test. Binding the tenant without a verifier would have
been unenforceable, so both land together.

It re-checks text, canonical source and tenant against each stored signature, catches a signature
present without its key (or the reverse), and takes an optional `expected_key` to pin WHO — without it
the answer is "internally consistent", which any forger holding any key also satisfies. **A store
carrying no attestations is reported as a FAILURE, not a pass**, because an empty check that reads as
green is the defect this release is about.

**The compatibility choice was a downgrade channel, and it is closed.** Omitting the tenant for unbound
stores is what keeps old signatures valid — and the verifier tried the record's tenant and *then*
no-tenant for every key alike, so a row signed while unbound and later GIVEN a tenant still verified.
Measured before the fix: placing an unbound-signed row into `beta` returned `ok=True`, zero problems —
a promotion into a tenant it was never signed for. The no-tenant fallback now belongs only to keys that
had an excuse for omitting it: for a key this store can NAME (its own `writer_pubkey`, or one pinned
through `expected_key`) the bound form is the only form accepted, while a foreign signer keeps both
candidates. Caught by red-teaming this note before publishing it, not by a user.

**What this does NOT do, since the pitch is easy to over-hear.** A signature attests to the records
that are PRESENT; tenant binding was built in answer to a data-LOSS incident (2.3.2) and does not
address it. A row that is gone carries no failing signature: delete every `acme` row from a signed
five-record store and `verify_attestations()` returns `ok=True` with zero problems, while relocating a
single row in the same run returns `ok=False` — so the verifier is demonstrably alive and simply
cannot see absence. **`verify_writes()` is the check for that**: with receipts on it names the vanished
ids ("written but missing from the store (deleted out-of-band)"). Signature for placement, receipt
chain for cardinality; offering the first as protection against loss would be a category error, and
both limits are now pinned by tests so they cannot quietly become claims we do not have.

None of the above is a new idea. Binding the audience into the signed message is Abadi & Needham's
principle 3 (*Prudent Engineering Practice for Cryptographic Protocols*, IEEE TSE 22(1):6–15, 1996);
the familiar instances are JWT's `aud` (RFC 7519 §4.1.3), X.509 name constraints (RFC 5280 §4.2.1.10)
and Macaroons' first-party caveats. We had not applied it.

## 2.3.2 - UPGRADE IF YOU BIND A HANDLE TO A TENANT OR PROJECT: a scoped save dropped every other tenant's rows

**A tenant-scoped handle's save wrote only its own rows and dropped every other tenant's records from
the file.** If you construct `Inspeximus(path=..., tenant=...)` — or any scoped handle — and two of them
write the same store in sequence, the second flush erases the first tenant's data. Measured: projA
writes 3 records and flushes; a projB-bound handle on the same path then flushes; projA is left with
**0 of 3**. Unbound handles were never affected, so this bit only once a handle was scoped, which is
exactly when isolation is supposed to be protecting you.

`items` is a TENANT-SCOPED property over the real list (`_items`) — that is the structural half of
tenant isolation and it is correct. `_save` serialised `self.items`, so the view became the file. The
`items` SETTER already refuses this exact move ("Route deliberate whole-list writes to `_items`"); the
persist path read the same property and was missed. One guarantee, two implementations, one unchecked.

The fix persists `_items`. Paired tests: a scoped flush must keep the other tenant's rows, and an
unbound handle must still write the whole store — without the second, a persist path that wrote nothing
at all would pass the first by leaving the earlier file untouched. Mutation-verified against the
`_save` line specifically (the first attempt mutated an identically-spelled line 5,800 lines away and
reported a false survival).

Not a 2.3.1 regression: the line dates to 2026-07-21. Every release since carries it.

**If you have run scoped handles against a shared store, check your file before upgrading** — the lost
rows are not recoverable from the store itself.

## 2.3.1 - UPGRADE IF YOU USE `credit_requires_warrant`: a record could vouch for ITSELF, and MCP could not warrant at all

Two halves of one hole, both in the exogenous-warrant guard (`credit_requires_warrant`, the MINJA
self-graded-outcome defence). Neither was found by auditing the guard — both surfaced while wiring a
CALLER up to it, which is the only thing that ever tests whether the input arrives.

**FIXED — self-vouching.** `_warrant_is_exogenous` promised the warrant is "neither the record's own
CANONICAL source nor any tenant/source in its transitive lineage", then compared a **raw** warrant
against `_rec_sources()`, which returns `_canon_source(doc)`. `_canon_source("crucible/claim-17")` is
`"crucible"`; `"https://x.com/a/b"` is `"x"`. The two normalizations never met, so the one concrete
protection the docstring names was DEAD for every realistic source — any path, URL or scheme-prefixed
id — and alive only for a single-token source like `"plainword"`. Measured: a record with
`source={"doc": "crucible/claim-17"}` credited with `warrant="crucible/claim-17"` earned
`good_warranted=1.0`. Both sides are now canonicalized.

  Deliberately the CONSERVATIVE direction: a warrant that canonicalizes onto the record's own source
  is refused even when the raw strings differ. For a guard, a false refusal costs a credit; a false
  acceptance costs the guarantee. **The only behaviour that changes is that a self-vouching warrant no
  longer earns warranted-good** — nothing that was refused before is accepted now.

**FIXED — the MCP `credit` tool had no `warrant` parameter.** The library has accepted `warrant=` all
along; the tool dropped it, so every credit an agent could make over MCP was unwarranted BY
CONSTRUCTION. This is the identical shape as `with_warrant` missing from `recall` in 2.3.0: the
mechanism works given its input, and the surface never delivered the input. Measured on a real
deployment before the fix: `good` populated on 470 records, `good_warranted` on **0 of 220,213**.

**UPGRADE IF** you rely on `credit_requires_warrant`. Before 2.3.1 it could be satisfied by a record
naming its own provenance, and could not be satisfied at all through MCP.

Tests are paired throughout, and mutation-checked: dropping the fix turns the regression tests red
while "a genuinely external warrant still counts" and "an unwarranted credit earns nothing" stay
green — which is what separates a working guard from one that refuses everything or stamps everything.

## 2.3.0 - UPGRADE IF YOU BRANCH ON `recall(with_warrant=...)`: the top tier was settable by the writer

Three findings from one afternoon, and they are the same finding: **a mechanism that is correct and
unreached reports SAFE.** Found while drafting a public answer about what our warrant tiers mean —
one hour after we publicly invoked Biba ("integrity requires a label the writer cannot set").

**FIXED — the top warrant tier was writer-settable in one call.** `recall(with_warrant=True)` read

    elif (_good > 0 and _good >= _bad) or r.get("mtype") == "semantic":

and `mtype="semantic"` is an accepted argument to `remember()`. A record written that way reported
`earned` — the strongest tier we expose — with no outcome credit, no corroboration and no lineage. The
influence gate one screen down already refused exactly this and says so in its own comment; the LABEL
was never updated to match the GATE. The semantic clause now requires the `graduated_from_episodic`
marker the code already stamps, and the credit test reads the warrant-gated counter, so
`credit_requires_warrant` reaches the tier instead of being computed and discarded.

**NEW — `writer_key`: a store can sign its own writes.** Measured across every store our deployment
runs — 111,264 records — `attested_key` coverage was **0.0000%**, so `strict_corroboration` (which
counts distinct verified keys) could not fire for a single record anywhere, and `good_warranted > 0`
was 0 of 60,077. The flags were fine; the only way to attest was for the CALLER to hold a keypair and
sign each claim, so nobody ever did, ourselves included. `Inspeximus(writer_key=<hex>)` signs its own
writes; `INSPEXIMUS_WRITER_KEY_FILE` wires it into the MCP server; `inspeximus writer-key --new` mints
one. Honest scope: this attests AUTHORSHIP, not truth. It raises manufactured independence from "type
two different source strings" to "hold two distinct persisted keys" — real cost, not a trust root, and
a process free to mint keys can still mint witnesses. Pin the writers you trust with `trust_seeds`.

**FIXED — attestations are now re-verifiable.** The signature was verified at write time and then
thrown away: records carried a public key and no way to re-check it. `attested_sig` is now stored on
both the explicit and the auto-signed path, so an auditor reading the store later can verify it
instead of trusting the write path's word.

**NEW — no silent masking in the warrant tier.** `warrant` is one scalar and `earned` is tested first,
so a record backed by BOTH channels reported only the outcome one and the corroboration vanished.
`recall(with_warrant=True)` now also returns `warrant_earned`, `warrant_corroborated` and
`warrant_sources`. A record with 24 links but no attestations no longer reads identically to a record
with no links at all.

**NEW — the MCP server can return the tier at all.** `with_warrant` existed in the library and appeared
nowhere in `mcp_server.py`; the default projection kept only `{id, text, score, value, tags}`, so the
tier was uncomputable and then unrepresentable for every MCP consumer, `full=True` included. The tier
exists so a low score is not read downstream as a weak "yes" — a state the caller cannot obtain does
not do that job.

Defaults unchanged: `strict_corroboration` and `credit_requires_warrant` both stay opt-in. Flipping
them on today would move all 111,264 records to `unwarranted` and delete both positive tiers — the gap
is coverage, not the default. Write attestations first, then flip, and re-measure both on the same day.

## 2.2.2 - UPGRADE IF YOU USE THE CLAUDE CODE PLUGIN: the recall hook could go silent, and did

The hook that injects "what we decided, and why" before each prompt spent a full working day printing
`ran: git status` while the decision that answered the prompt sat in the store the whole time. Two
independent causes, both silent by construction.

**It read the wrong file.** The hook opens the PROJECT coding store; `remember_decision` over MCP writes
to whatever store the MCP server was configured with. Measured on the deployment where this was found:
the project store held **6,779 records, of which 5,857 were `ran: ...` captures and 16 were decisions**;
the MCP store held **350 records, all of them decisions**. The writes were happening, correctly typed,
into a file the reader never opened — the same class the `_store_dir` docstring already records for
intra-project fragmentation, one level up. **`INSPEXIMUS_DECISION_STORE`** now names a second store,
consulted for decisions only, read-only, fail-open, de-duplicated against the project hits. Explicit
rather than guessed: defaulting to a path we assume is your configuration would be inventing it.

**The console codepage deleted the block.** Decision prose carries em dashes, arrows, Cyrillic, Chinese.
On a cp1250 console `print()` raised `UnicodeEncodeError`, the caller swallowed it, and the process
exited **0 with empty stdout and empty stderr**. Measured on one event: **18,032 bytes under
`PYTHONIOENCODING=utf-8`, 0 bytes under cp1250.** Not truncated, not mojibake — nothing, and
indistinguishable from "no relevant memory found". The richer the record, the likelier a character that
erases the whole block, so the hook went quiet exactly when it had most to say. Fixed with
`errors="replace"` and **not** a switch to UTF-8: the encoding is a contract with whatever reads the
pipe, and trading a silent crash for silent mojibake is not a fix. It costs one `?`.

**And a bound, because the fix without it is its own problem.** Eight unbounded decision records is 18 KB
of context spent before the user has typed. Now ~4,500 bytes for the same eight. The block is a pointer
to a record; the record is one `recall` away.

Six tests drive the real hook as a subprocess and read its output as **bytes** — a harness that decodes
the pipe with the console codec dies on the same character the hook died on, then reports the crash it
caused as an empty result. Three mutants registered and caught: guard removed, store unread, bound
dropped.

## 2.2.1 - UPGRADE IF YOU RAN 2.2.0 AND WANTED `observe_recall` OVER MCP: the server could not switch it on

2.2.0 added `observe_recall` to the library and shipped it with **no consumer**. `inspeximus/mcp_server.py`
built its store as `open_store(..., receipts=_RECEIPTS)` — no `observe_recall`, and no environment variable
for it, while the same file already read env vars for receipts, the echo guard, `max_k` and snippet chars.
The pattern was right there and the switch was simply missing.

That mattered more on this surface than anywhere else, which is why it is a same-day fix rather than a note.
The MCP server holds ONE module-level store for the whole process, so a `recall` call followed by a
`remember` / `remember_decision` call is the same agent writing to the same store, causally linked. It is the
one deployment where the `recall -> write` flow the field exists to observe is actually real — and it was the
one place that could not turn the field on.

**`INSPEXIMUS_OBSERVE_RECALL=1`** now enables it, accepting the same spellings as the other switches
(`1/true/yes/on`). Default off, unchanged behaviour, and documented in the module's Config block — including
that the query digest is a GROUPING key and not anonymisation.

**The test that would have caught this is not `assert _OBSERVE_RECALL is True`.** A module flag that is read
and then dropped passes that and changes nothing; the whole defect was a value that existed and went nowhere.
Every assertion in `tests/test_mcp_observe_recall.py` goes through `_MEM` and lands on a written record,
including one that drives the actual MCP `recall` and `remember_decision` tools rather than the store
directly. Registered as a mutation (the exact line 2.2.0 shipped); it kills 3 of the 4 tests.

## 2.2.0 - UPGRADE IF YOU WANT THE STORE TO RECORD WHICH MEMORIES PRECEDED EACH WRITE: `observe_recall`

Opt-in and inert. Default **off**; a store written without it is byte-identical to one written before it
existed, and no gate, ranking or branch reads the new field. **If you do not turn it on, this release
changes nothing for you** — there is no fix here and no reason to hurry.

**What it adds.** `Inspeximus(observe_recall=True)` stamps `recall_window = {ids, at, q, w}` on any write
that followed a recall: the ids as served in rank order, when the recall happened, a 12-char fingerprint of
the query, and how many writes have already followed that recall.

`q` is a **grouping key, not a privacy measure** — it exists so an analysis can tell which writes came from
the same retrieval. Do not read it as anonymisation: hashing personal data is pseudonymisation, the AEPD/EDPS
joint paper is explicit that it does not put the data out of scope, and a short query drawn from a
low-entropy space is recoverable by dictionary search. If your queries carry personal data, so does `q`.

**Why.** Declared lineage measured **0.00%** across a 27,290-record deployment, and the window that could
have stood in for it lived in memory and died with the process -- so *"on writes where the store observed a
recall, how much of the true parent set does the free window already capture?"* is unanswerable on any store
ever written. Re-measured on our own swarm before building this (2026-08-07): `derived_from` filled on
**0 of 181,523** records across all eight live stores, and no record carrying a window field of any kind.
There is nothing to replay, which is why this had to be a write before it could be a measurement.

It also generalises *why* declaration failed. `derived_from` needs a judgement **per write**, from a caller
that does not track lineage, and gets skipped. `observe_recall` needs **one decision per store**, at
construction, after which the store fills it from a flow it already sees.

**PRIOR ART: none of this is new, and the parts that are old are old by twenty years.** Stating it here so a
reader does not have to find it:

- The mechanism is verbatim **PASS** (Muniswamy-Reddy et al., USENIX ATC 2006): *"when a process issues a
  `read`… PASS creates a record that the process depends upon the file… when that process then issues a
  `write`… the written file depends upon the process."* Read-before-write as observed provenance is theirs.
- Keeping observation in a **separate channel from declaration** is likewise established, not our idea:
  **PASSv2** (ATC 2009) shipped a distinct *Disclosed Provenance API* precisely so applications could
  disclose alongside what the system observed.
- **W3C PROV-DM** (Recommendation, 2013) names our inference as an explicit anti-pattern — *"if an artifact
  was used by an activity that also generated a new artifact, it does not always follow that the second
  artifact was derived from the first"* — and supplies `wasInfluencedBy` as the weaker relation. That is the
  right reading of `recall_window`: **influence, not derivation.** `derived_from` remains the only
  `wasDerivedFrom` in this library.
- Instrumenting a chokepoint instead of asking authors to annotate is the oldest pattern here: `gcc -M`
  replacing hand-written Makefile dependencies, and **Dapper** (Google, 2010), which rejected declaration in
  as many words — *"a tracing infrastructure that relies on active collaboration from application-level
  developers… becomes extremely fragile, and is often broken due to instrumentation bugs or omissions."*
  Our 0.00% is a re-measurement of a known result, not a discovery.

**And the known failure mode, which we do not escape.** Read-before-write over-approximates: **BackTracker**
(King & Chen, SOSP 2003) measured an unfiltered dependency graph of 5,281 objects / 9,825 events against an
analyzable truth of 24 / 28. Theory says the same — a read-*set* is why-provenance, a lossy image
(Green/Karvounarakis/Tannen, PODS 2007: two outputs can share why-provenance `{r,s}` while one derives from
`s` alone), which is exactly why this field **must not** drive trust or deletion propagation, and does not.
And for retrieval specifically, *served ≠ used*: Wallat et al. (ICTIR 2025) measure up to **57%** of RAG
citations as post-rationalised — retrieved and cited without being causally used. A record that sits in
every top-k becomes a parent of everything, the direct analogue of PASS finding `/etc/mtab` in the
provenance of every process that touched glibc.

**So what is actually unmeasured?** Not the mechanism — the *number*, in this setting. The 99.5% is
OS-syscall, the 57% is RAG-citation; we found no peer-reviewed measurement of read-before-write precision in
a document/memory store. That gap is the whole reason to persist the window: **this release buys the data,
it does not report a result.** If that measurement never happens, this field is a correlation log, and we
would rather say so now than discover it later.

**Observation, not claim -- and the separation is load-bearing.** `derived_from` asserts parentage and earns
consequences: taint inheritance, the orphan rule, the influence gate, evidence-grade capping.
`recall_window` asserts only that the store served these ids before this write -- true by construction, no
threshold, no embedding, no model. It feeds nothing. If a gate ever consumes it, it stops being evidence and
becomes a claim, and the measurement it exists to enable would be measuring its own stamp.

**A second reason it must stay inert, which is adversarial rather than statistical.** The window is
attacker-influenceable: anyone who can issue a recall against a store immediately before another writer's
write decides what lands in that write's `recall_window`, and `observe=False` only helps where the caller
knows the read was foreign. Today this is harmless precisely because nothing consumes the field — but it
means the field can never be promoted into a trust, corroboration or erasure decision without first solving
an injection problem that does not currently exist. Treat "recall_window feeds nothing" as a security
property, not just a measurement hygiene rule.

**Nothing is thresholded at write time** -- no age cutoff, no relevance filter, no classification of the
write. Each would be a parameter a later analysis could never reach past (a window stamped only when it is
under 60s old cannot answer what the window captures at 300s). `w` carries what a write-time classifier
would have been used for, as data: one recall followed by a burst stamps `w=0,1,2,...`, so an analysis can
restrict to `w=0` without anyone having decided which writes were real. Excluding the library's own
maintenance writes by inspecting the call stack was tried and rejected -- `remember_decision` is a
library-internal caller too, and it is the main MCP write path.

**`recall(..., observe=False)`** marks a read that is not part of a write flow: a scoring pass, a maintenance
sweep, or one agent reading **another agent's** store. That last case is why it exists -- a foreign read
resets both the window and the write counter, so the other agent's next write would look exactly like one
that followed its own recall, with nothing left in the record to separate them. It *invalidates* the window
rather than freezing it: pairing one recall's ids with another's timestamp yields a record that looks
complete and is internally false, which is worse than no record. `_last_recall` / `_last_recall_text` are
still updated, so the pre-existing `derived=True` and `infer_lineage` paths are untouched.

**Erasure treats it as history, not a pointer to chase.** `forget()` keeps it for the same reason it keeps
`derived_from` and `taint` -- scrubbing history deletes the evidence and makes the audit read clean --
and `erasure_audit()` now reports a window id whose record is gone as `dangling_recall_window`, counted in
`coverage` as `with_recall_window`, deliberately apart from `with_declared_lineage`. This was not optional:
`recall_window.ids` is a new inter-record pointer channel, and the audit walked only `derived_from`, so a
dangling window id would have been residue that reports as no residue.

**Multi-hop retrieval gets ONE window.** `recall_iterative` returns the union of every hop but each internal
`recall()` overwrote the window, so a write after it would have been stamped with the last hop alone —
understating what the free window captures, silently and in a known direction, in the one metric the field
exists to produce. The observation now has its own id list (`_last_recall_window`), set once per logical
operation: the union for `recall_iterative`, and round-1-plus-the-bridge (`merged`) for
`recall_iterative_followup`. `_last_recall` itself is untouched, so `derived=True` / `infer_lineage` behave
exactly as before.

**Two things we are open about rather than settled on.** First, as shipped this field has no consumer, so
nothing yet can return the verdict *"these edges are wrong"* — the falsification is the planned adjudication
against human-labelled parent sets, and until that runs the honest status is *unverified*, not *sound*.
Second, our "declaration doesn't work" argument has a real counter-example in its own best source: Dapper
eliminated *mandatory* annotation, yet 70% of its spans and 90% of its traces went on to carry a **hand**
annotation once the automatic spine existed. Automatic capture may not replace declaration so much as give
people a skeleton worth decorating. If `derived_from` coverage rises from 0.00% on stores that turn this on,
that is the more interesting result, and it argues against us.

20 tests, all eight deliberate mutants caught (stamp removed, `forget()` scrubbing the window, audit blind to
the channel, silent truncation, counter never resetting, iterative stamping the last hop, the two-phase
surface dropping round 1, and a foreign read still being observed).

## 2.1.2 - UPGRADE IF YOU RAN 2.1.1: the review flag it added could land on the attacker's record

2.1.1 gave the corroboration guard a fail-loud flag and shipped three defects with it. All three were
found by an adversarial review of the *reply* explaining the release, not by the gate, which passed
8/8 with 2456 tests.

**The flag depended on sort order.** It was set on the outer loop's anchor, and that loop sorts by
`-value`. Which record counts as "standing" is decided by `valid_from`/`ts`, and two writes in the same
clock tick tie -- so the decision fell through to the value order, which an attacker reaches through
reinforcement or credit. Measured: boosting the contradiction's value moved the flag onto the
ATTACKER's record and left a plain `recall()` of the surviving value showing `under_review=None`,
exactly the state 2.1.1 was cut to end. Both records now carry the flag, because both survive a refused
overturn and picking one re-introduces the ordering dependence.

**Only one of the two guards had it.** `supersede_persistence` reaches the same refusal and had no flag
at all, so a store hardened with persistence alone still refused overturns silently.

**Consolidation was borrowing the wrong function.** `_do_reopen` belongs to the `observe()` flow, where
an accrual has just reached its threshold and is being consumed -- which is why it pops
`_reopen_contra`/`_reopen_support` and force-saves. Called from `consolidate()` both are wrong:
measured, a single attacker write reset another party's in-flight 1-of-2 observation, and the
force-save fired per pair inside an O(n^2) loop. New `_flag_contested` sets the flag and nothing else.

**`reopened()` now returns `contested_by`.** It was written into meta and never surfaced, so a steward
facing N entries from one unverified source had N investigations instead of one filter.

### Written as properties first, and two of them were wrong

`tests/test_blocked_overturn_properties.py` was written BEFORE the fix, against released 2.1.1, where
it fails 5 times; it passes 11/11 after. Two of the properties had to be corrected by measurement
rather than by argument:

* the queue property started as `reopened() <= 1`. That is an arbitrary cap, and it is wrong: if one
  actor contests eight genuine facts, eight facts ARE contested and every reader deserves to know.
  Capping suppresses true information to make a number look good. The real property is that the queue
  is TRIAGEABLE -- every entry names its reason and the record that contested it.
* the annotation property demanded a flag in a case where the contradiction had simply LOST: under one
  configuration the attacker's own record is superseded, nothing is contested, and flagging the winner
  would be a false alarm. The test now separates three outcomes -- guard failed, dispute resolved,
  dispute unresolved -- and only the last owes the reader a warning.

**Unchanged and deliberate:** both guards remain opt-in and OFF by default; in the default build a
contradicting write still supersedes. And the contested value is still returned -- we annotate rather
than withhold.

## 2.1.1 - UPGRADE IF YOU SET `supersede_requires_corroboration`: a refused overturn is now visible to the reader

2.1.0 made the guard refuse an uncorroborated overturn. It did not tell anyone. A consumer calling
plain `recall(query)` saw the correct live value and no sign that a retraction had arrived and been
rejected -- the only trace was an extra link on the record, unlabelled and indistinguishable from any
other link.

The substrate already has a fail-loud channel: `observe()` marks a contested record `under_review` and
lists it in `reopened()`, and that flag rides on the record, so a reader who does nothing special sees
it. One of the two contradiction paths simply was not using it. Measured on the least curious consumer
possible, a plain `recall()` with no options:

| | before 2.1.1 | after |
|---|---|---|
| contradiction refused by the corroboration bar | `under_review = None` | **`under_review = True`**, reason `uncorroborated_contradiction` |
| contradiction arriving via `observe()` | `under_review = True` | unchanged, reason `novel_support_contradiction` |
| no contradiction at all | silent | silent |

The two reasons are distinct so a consumer can tell which path flagged the record.

**The cost, stated rather than buried.** The opt-in path is noisier now: every refused single-source
claim marks the record it targeted, and those land in `reopened()` for steward review. That is the
trade against a continuous-weight design, where the same event is a small negative increment nobody
has to look at. A test asserts the other side of it -- a properly corroborated correction must NOT
leave a review flag, or the queue fills with resolved cases and stops being read.

**What is deliberately unchanged:** the contested value is still RETURNED. A substrate that withholds
it leaves the agent with nothing, which is a different failure rather than a safer one. We fail loud
by annotating, never by hiding.

Mutation-tested: removing the flag fails the visibility assertion and nothing else.

Raised by yun520-1 on deepseek-ai/DeepSeek-V3#1466, asking whether a surviving live value tells the
reader a retraction arrived. On one of the two paths it did not.

## 2.1.0 - UPGRADE IF YOU SET `supersede_requires_corroboration`: it was counting raw links, not sources

**BEHAVIOUR CHANGE, opt-in flag only.** The guard's own comment said "same bar as graduation (earned
credit, or >=2 links)". The code read `len(newer["links"]) >= 2` -- a raw LINK COUNT, requiring neither
distinct sources nor verified keys -- while graduation counts distinct canonical sources, or distinct
Ed25519 keys under `strict_corroboration`. The guard was strictly weaker than the bar it named, and
`strict_corroboration = True` did not touch it.

Measured on a fixture that reaches the guard: an attacker holding ONE source string and two filler
records it also wrote overturned a standing fact. The old predicate returned True; the graduation bar
returned False on the identical records.

| | old predicate | new | standing fact |
|---|---|---|---|
| one actor, one source, two self-supplied links | True | **False** | stays **active** |
| legitimate change, three distinct sources | True | True | correctly superseded |

The guard now calls `_graduation_corroborated` -- the same function, not a similar rule, so the two
cannot drift apart again. That is what the comment always claimed.

### The measurement mistake, kept because it is the useful part

The first investigation reported that the attacker wins even with `strict_corroboration` on. That was
true and measured off the WRONG PATH: both records were written under the same `key`, so keyed
last-write-wins superseded at WRITE time and the consolidate-time guard never executed. The number
looked like evidence for the bug and was evidence of something else. The regression test therefore
carries a control asserting that with the guard OFF the fixture DOES supersede via `state_toggle` --
without it, the two real assertions are satisfied by a fixture that never reaches the guard.

Mutation-tested: restoring the old raw-link predicate fails the attacker assertion and nothing else.

### A limit now written down rather than rediscovered

The keyed path bypasses this guard by design. Writing the same `key` supersedes at write time under
last-write-wins with no corroboration asked for at all -- whoever knows the key overturns the fact in
one write, no sources, no links, no attestation. That is the keyed-supersession model working as
intended, and it is also the most forgeable route in the substrate. `test_the_keyed_path_bypasses_this_guard_by_design`
asserts it so the scope of the guard is stated instead of implied.

Found by answering a direct question from yun520-1 on deepseek-ai/DeepSeek-V3#1462, who asked whether
corroboration here is counted from source strings -- the forgeable dimension. It was counted from
something weaker still.

## 2.0.2 - UPGRADE IF YOU RUN THE EXAMPLES: five of them could not work on a plain install, and the test that watched them could not see it

**No library behaviour changes.** Packaging, examples and the check that should have caught this.

`pip install inspeximus` gives a package with zero dependencies, which is the point. Five examples sign
with Ed25519 and therefore need `cryptography`, so on a clean machine they raise:

```
RuntimeError: signing write receipts needs the `cryptography` package
```

Affected: `04_encryption`, `06_gdpr_erasure_receipt`, `07_witness_pool`, `12_split_view_detection`,
`trust_is_not_truth`. Measured on a fresh virtualenv against the released 2.0.1: **6 of 15 examples
failed**, five for that reason and one needing `langgraph`.

**There was no `crypto` extra**, so `pip install "inspeximus[crypto]"` -- the form everyone guesses --
installed nothing extra and failed identically, which is the most confusing possible outcome. The extra
now exists.

### Why the existing test said nothing

`tests/test_examples_run.py` has run every example on every suite run for months, and it was green. It
invokes them with `sys.executable`, the developer's interpreter, and the base CI leg installs
`pytest cryptography pyyaml` -- so `cryptography` is present in **both** places the check ever runs. The
five failing examples passed there while failing for every reader. The check ran in the one environment
where the defect could not occur.

### The fix, and it is the check rather than the packaging

A second sweep runs each example with every non-stdlib optional import BLOCKED, reproducing
`pip install inspeximus` regardless of what the machine has. Each example must then either run clean, or
declare its dependency in `NEEDS`, with the failure mentioning that dependency so a reader can act on it,
and the named dependency must resolve to an extra `pyproject.toml` actually defines.

Blocking imports rather than building a virtualenv per example is deliberate: milliseconds instead of
minutes, and it cannot be defeated by whatever the CI image happens to ship.

Mutation-tested rather than asserted. Removing the `trust_is_not_truth` declaration fails that exact
parameter; pointing the `crypto` extra at the wrong package fails the extras check as well. Both were
run and both were caught, because a guard nobody has watched fire is a guard nobody has tested.

### Also

README's quickstart names the crypto extra and lists the five examples, instead of leaving a reader to
discover it from a traceback.

## 2.0.1 - UPGRADE IF YOU RAN 2.0.0: nothing could mature into the semantic tier, and nothing said so

**BEHAVIOUR FIX.** 2.0.0 made `recall(reinforce=...)` default to False so a read stops writing. Episodic
to semantic graduation was implemented as a side effect of that same read, guarded by
`if reinforce and ...`, so it left with it. Measured on shipped 2.0.0, with a positive control:

| | graduated |
|---|---|
| `recall(..., reinforce=True)`, 6 corroborated records over the value bar | 5 of 6 |
| the 2.0.0 default | **0 of 6** |
| `credit()` + `sleep()` + `consolidate()`, no reinforcing read | **0** |

There was no other route in the package. `_GRADUATE_VALUE` had exactly one call site and it was inside
the reinforcement block, so on 2.0.0 defaults the durable slow-decay tier was unreachable and a store
could not mature. Anyone who upgraded and relied on episodic memories settling into semantic ones got a
store that quietly stopped doing it.

**Nothing went red.** 2422 tests passed, `tools/release_check.py` reported READY, CI was 19 of 19. Every
test that touched graduation had been written for a store whose reads reinforced, so not one of them
could tell "graduation is correct" from "graduation never ran". That is the failure this project keeps
rediscovering, and it found us on the release that was supposed to be about rigour.

**The fix: maturation is a consolidation concern, so it now runs in `consolidate()`.** The dream pass
promotes any active episodic record that clears the same bar as before -- value at or above
`_GRADUATE_VALUE`, corroborated by earned credit or two distinct sources, not slashed, not orphaned --
and reports the count as `graduated`. Reads stay pure. Maturation now happens at a moment the caller
chooses instead of as a side effect of asking a question, which is where it should have been.

The bar itself moved into two shared helpers, `_graduation_corroborated()` and `_may_graduate()`, used
by both the opted-in read path and `consolidate()`. Two copies of a corroboration rule drift, and the
drift is invisible because each side keeps passing its own tests.

`recall(..., reinforce=True)` still matures on read, unchanged.

**New regression test:** `tests/test_maturation_survives_a_pure_read.py` asserts the PAIR, because
either half alone is satisfiable by a bug -- a corroborated record DOES mature when consolidation runs,
AND a read still matures nothing. It carries a control proving the fixture can graduate at all (else
the pure-read assertion is vacuous) and two negative controls, since a pass that promotes whatever it
is handed would satisfy the positive assertion and be worthless.

**One thing I broke while fixing this, kept here because it is the argument for the helper.** Moving
the graduation block out of `recall()` took four lines with it that the `with_warrant` tier reporting
a few lines below still referred to, so `recall(with_warrant=True)` raised `NameError: name '_good' is
not defined`. Two tests caught it. The point is not the typo: those four lines were a SECOND copy of
the corroboration inputs, and the copy is what made the move unsafe. They now live in one place,
`_corroboration_facts()`, used by the graduation bar and by the warrant tier both.

**Still true, and not fixed here.** With reads pure, `last_access` is only written at write time, so
the decay clock no longer resets on use: a memory recalled 500 times and one never recalled now age
identically unless you pass `reinforce=True`. That is a real trade rather than an oversight -- usage
that changes ranking IS a write, and you cannot have a pure read and use-based decay at the same time.
Deciding where usage evidence should live, most likely an access log applied during consolidation, is
2.1 work and is not going to be smuggled into a patch release.

## 2.0.0 - BREAKING, UPGRADE IF YOU CALL `recall()` OR SET `m.extractor = regex_extractor`: a read no longer writes, so the order your questions arrive in no longer changes the answers

**If you are upgrading from PyPI, you are upgrading from 1.89.0.** 1.90.0 and 1.91.0 were tagged and
merged but never published, so everything in those entries arrives with this one. That is also why this
is a major: two behaviours change under you, and shipping either as a minor would be the quiet kind of
break we complain about in other people's libraries.

### Breaking 1 - `recall()` no longer reinforces what it returns

`reinforce` now defaults to **False**. Before this release a read was also a write: every returned record
had its `value` bumped and its `last_access` clock reset, and `value` multiplies the rank. The
consequences were measured, not suspected, and they are in `tests/test_determinism_conformance.py`:

| | 1.89.0 default | 2.0.0 default |
|---|---|---|
| records mutated by one `recall(k=5)`, hybrid | 5 of 11 | **0 of 11** |
| answers that change when the same 8 questions are asked in a different order, hybrid | **64 of 64** | **0 of 64** |
| answers that change under reordering, lexical / semantic | 5/64 / 10/64 | 0/64 / 0/64 |
| `admit()` rejecting a duplicate | promoted the record it collided with, value 1.00 -> 1.25 | writes nothing |
| `mcp_server.token_report()`, documented DETERMINISTIC | mutated 4 of 11 records per call | 0 of 11 |

Read the second row twice. On the shipped default path -- `mode="auto"`, which routes to hybrid on any
store past `semantic_threshold` -- **no answer survived a reordering of the same question set**. Asking
your questions in a different order gave you a different memory.

Eleven strict-xfail markers in the conformance suite became xpasses when the default flipped, which is
that suite doing the job it was written for. They are ordinary assertions now, and each block carries a
control pinned to `reinforce=True` that must still reproduce the old number -- because "the defect is
gone" and "the harness stopped looking" are the same colour of green.

Accuracy did not pay for this. It improved. Reinforcement was measured against an oracle on our own
recall benchmark and lost 20 comparisons out of 20 (oracle +0.394, reinforce -0.090), and the LOCOMO
retrieval pair scores 4-5 points HIGHER with it off -- 0.83/0.70 against the old 0.783/0.648, on the same
1536-question denominator. The old pair was not optimistic; it was measuring a memory that was learning
from the benchmark while being measured by it.

One measured side effect, at its honest size. Re-running `probes/minja_influence_gate.py` under the
new default moves two numbers in its cold-start arm: raw ASR 0.8 -> 0.7 and raw utility 0.8 -> 0.9,
both in the favourable direction. `n_victims` is 10, so each is a single victim changing side, and the
probe is seeded (two consecutive runs are byte-identical), which is how we know it is the default flip
and not noise. It is one case, reported because the receipt moved, not because it establishes anything.
The headline number that arm exists to produce, `E_adaptive_FORGED_warrant_asr = 0.7`, is unchanged.

A second measured side effect, and this one has a mechanism. The ungated retrieval-hijack rate in
`probes/agentpoison_influence_gate.py` DROPS under read purity, because a poisoned record no longer
gets promoted by the act of being retrieved. One run per process, 8 of 8 runs identical in each cell:

| environment | 1.89.0 (reinforce=True) | 2.0.0 (reinforce=False) |
|---|---|---|
| no numpy (the base CI leg) | 0.812 | 0.625 |
| with numpy | 1.000 | 0.938 |

The gated arm stays at 0.0 in all four. This lowered the base CI leg below the positive control's 0.8
floor, which was calibrated under the old default; the floor is now 0.6, still far above the gated 0.0
that the control exists to make meaningful. Read purity is a partial mitigation for retrieval
hijacking, which we did not set out to buy and are not going to advertise as a security feature.

The behaviour is not gone, only un-defaulted. `recall(..., reinforce=True)` restores it exactly, and the
`credit()` path -- earned, outcome-driven value, which is the one we actually argue for -- is untouched.

**Migration.** If you relied on reads warming the store, pass `reinforce=True` at the call sites where you
want it. If you did not know reads were writing, you needed this release. Stores written by older versions
load unchanged; nothing on disk is migrated.

### Breaking 2 - first-person keys changed (`my title` -> `self::title`)

Shipped in 1.90.0, never published, so it lands here. `regex_extractor` now emits `self::title` for
first-person statements. A live store written by 1.89.0 keeps its old keys, and a new write under 2.0.0
will not supersede them -- the key is the supersession identity, so the two forms sit side by side.
See the 1.90.0 entry below for the re-measured scope of the extractor, and re-key with `revert`/`remember`
against `self::…` if you need a chain to bind across the upgrade.

### Also in 2.0.0

Everything from the 1.90.0 and 1.91.0 entries below, plus the docs and tooling work described next.

### Agent-to-agent memory grants - scoped, revocable, and in the chain that already exists

**New: `grant()` / `revoke()` / `as_agent()`.** Multi-agent is the normal deployment shape, and two agents
sharing a store immediately raises which agent may read which memories, who granted it, and how it is taken
back. A grant names a subset by `scope`, `tag`, `key` or `ids` — exact-match on a stored field, so the
authorised set is decidable with no embedder and means the same tomorrow as today. A query selector was
deliberately refused: its membership is a similarity score, so the same grant would cover a different set
after a re-embed, and an ACL that silently widens is not an ACL. Reads go through `store.as_agent("bob")`;
`revoke()` takes effect on the next read. Exposed over MCP (`grant`, `revoke`, `grants`, `grant_log`,
`can_read`, `recall_as`, `get_as`) and the CLI (`grant`, `revoke`, `grants [--log]`,
`recall --as-agent`). **Backwards compatible: with no grants configured, an unbound handle behaves exactly
as before** — no owner stamp on writes, byte-identical recall.

**A grant is a record, not a second log.** Each act is an ordinary hash-chained write, so it inherits the
write receipts, the anchor, `history()`, `provenance()` and `supersession_report()` — revocation *is* keyed
supersession. The audit bundle gains a `grants` block (the acts, never memory text) plus a check that every
act it lists is covered by the write chain, so a grant appended to a bundle but never written to the store
fails verification. Access-control rows are carved out of the content readers (`recall`, `memory_report`,
`contradictions`, eviction, the consolidate keep-budget) so issuing a grant cannot inflate a memory count or
surface as a recall hit.

**Fail-closed, including the empty cases.** No grants means an agent reads only its own writes. A grant that
cannot be evaluated authorises nothing: unknown selector kind, missing/empty granter, two active acts
disagreeing on a key — and, the sharp one, an *empty selector value*. `scope` and `key` are compared with
`==` against a field most records do not carry, so a grant whose value went missing degenerates to
`None == None` and matches every record *lacking* the field: the widest grant in the store from the emptiest
input, on the read path. The evaluator refuses it independently of the minting path. `remember()` also now
refuses the reserved `acl::grant::` keyspace, because a writer who can mint a grant key can authorise itself
and the ACL would be decorative.

**Two isolation defects found by the new sweep, both fixed.** (1) `believed_at()` was on the tenant view's
store-level passthrough while reading record text off `self.items`, so it returned another tenant's
plaintext; the existing tenant sweep missed it because its fixture puts the secret on a *superseded* record
and `believed_at` returns the latest-asserted value — a check that never sees its target. (2) The new
`_content_rows()` helper was not rebound on the view, so `memory_report()` reported 2 records to a tenant
that owned 1.

**Composition with the persist path, measured.** `_save()` serialises `self.items` — the scoped view — so
any *directly bound* handle (`Inspeximus(tenant=...)`, and now `Inspeximus(agent=...)`) writes only its own
rows and silently drops the rest; `StoreChangedOnDisk` cannot see it, because a sequential handoff is not a
concurrent write. The ACL's documented entry point does **not** reach that path: `as_agent()` returns a view
that shares the parent's `_items` and forwards `_save` to the parent, so a scoped write persists the whole
store. Both halves are asserted — the safe one as a passing test over every route to a write (remember,
grant, revoke, flush, reopen), the unsafe one as `xfail(strict=True)` so this feature records the
interaction instead of quietly depending on it. The fix belongs to the persist path, not here.

**Write-side consequence, stated rather than discovered.** Supersession is unauthenticated, so in a
multi-agent store agent B could retire agent A's current value by guessing the key, with no read access at
any point — a read ACL does not close a write hole. `_supersede_by_key` now resolves against the handle's own
scoped view: a write can only retire what its writer can see. The cost is real and asserted in the tests —
two agent-bound handles keying the same string each keep an active record, so the operator view sees both.
Unscoped (operator) writes are untouched: keys stay global, last-write-wins.

### Docs and tooling - every published number now has a command, and a test that fails when one does not

Docs and tooling only; no library behaviour changed.

We had already marked the headline retrieval pair (recall@25 0.783 / 0.648) "reported, not
independently reproducible from this repo". Auditing the rest of the reader-facing surface found that
this was a **class**, not an instance:

- **31 receipt paths** in the README, `docs/` and this file pointed at `inspeximus/probes/...` while
  the probes live at `probes/...`. The 1.48.0 entry below already records fixing *two* of them. Every
  one of the other 31 resolved correctly once the prefix was dropped, so every "run this to reproduce"
  line in `docs/API.md` and `docs/INTEGRATIONS.md` had been broken since it was written.
- **five README anchors** pointed at sections that had been moved out of the file — including the one
  advertising "the measured integrity number below", which was no longer on the page at all.
- **the MCP tool count was published three ways at once**: 30 in `MCP_LISTINGS.md`, 15 and 56 on the
  homepage, against a server registering 56. Three surfaces, one truth, no error anywhere.
- **two figures had no producing artifact**: "measured 15/15 on a verified-forgetting severe-test" and
  "severe-test 8/8". Both are withdrawn; the sentences now cite what the committed probes actually
  print (`0.17` / `1.00` over six stores, and `0/24`).
- **a whole README section documented `maintain.py` and `second_brain_mcp.py`**, neither of which is in
  this repository. Removed, with the gap stated where the pointer used to be.
- **`Letta has no undo` was false** — and our own `claims_audit.py` said so, on the line below it. Letta
  has an engine-level `undo_checkpoint_block`; the honest claim is that inspeximus exposes revert
  deterministically as a named memory operation, not that reverting is unavailable elsewhere. The same
  correction went into `docs/API.md`, and the two unscoped universal negatives (one asserting a rival
  could not copy the write-path design, one asserting nobody shipped external witnessing) are
  rephrased as what we measured on a dated scan of nine libraries.

**The fix is a gate, not a proofread.** `claims_audit.py` gained a published-number audit: every numeric
token a reader sees on `README.md`, `MCP_LISTINGS.md` and `index.html` must be registered, either as a
claim with a reproduction command and a status (`REPRODUCIBLE`, `REPRODUCIBLE-WITH-DEPS`,
`PENDING-HARNESS`, `EXTERNAL`, `WITHDRAWN`) or as a declared non-claim with a reason and an **exact
expected count**. It runs on every invocation and counts toward the exit code.
`docs/CLAIMS.md` is generated from that registry: **229 published tokens, 96 of them claims in 53 rows,
25 reproducible by a committed command**. Self-referential figures — the MCP tool count, the example
audit summary — are read from the code rather than trusted.

`tests/test_claims_coverage.py` carries the controls, because a guard nobody has watched fail is not a
guard: planting a bogus number, a second occurrence of a registered token, a moved pin sentence, a
deleted probe, a disagreeing tool count and a figure hidden in an HTML `data-count` attribute each
fail the audit by name. Review of the first version caught two **false-safe** holes in the scanner
itself — a unit-glued number (`--object 90d`) and a negative number (`z=-4.79`) were invisible, so the
registry's counts agreed with a checker that shared its blind spot — plus a control that ran the CLI
against the real repo instead of its own sandbox and passed forever while testing nothing.

### Integration conformance - three adapters were broken against current upstream

`tools/integration_conformance.py` runs every adapter in `inspeximus.integrations` through a real round
trip -- write IN through the framework's own interface, read back OUT through it -- and reports
**VERIFIED / SKIPPED / BROKEN** as three separate counts against a recorded ledger
(`docs/integration_conformance.json`) that says which upstream version each was last checked against.
First full run, every optional dependency installed: **9 verified, 0 skipped, 3 broken.**

- **`InspeximusStorage` (CrewAI) does not work with crewai 1.x.** 1.x deleted `crewai.memory.external`,
  dropped `external_memory` from `Crew`, and replaced the three-method `Storage` protocol our adapter
  implements with an eleven-method `StorageBackend`. Every existing test passed throughout, because none
  of them touched a CrewAI type.
- **`InspeximusSession` no longer satisfies `agents.memory.Session`** (openai-agents 0.18.3 added
  `session_settings`). The turn log still round-trips -- the SDK reads the attribute with a `getattr`
  default -- but the object fails a type check and any SDK behaviour keyed on that attribute is inert.
- **`InspeximusSaver` raises `StoreChangedOnDisk` in ~30% of ordinary LangGraph runs.** Reproduced away
  from LangGraph: one `Inspeximus` handle written from four threads raised it in 20 of 20 trials. The
  single-writer guard compares the file signature before `os.replace` and refreshes it after, unlocked,
  so a second thread reads its own peer's write as a competing process. Correct across processes,
  misfiring within one -- and LangGraph calls a checkpointer from its executor.

Also: **`haystack` is an extra now** (`pip install "inspeximus[haystack]"`). The adapter, its parity
script and two test modules had shipped for months with no declared install path; CI installed
`haystack-ai` by hand in one job, which is why nothing surfaced it. Floor `>=2`, no cap -- 3.0.0 is
what the round trip is verified against and it passes, so there is no observed breakage to cap on.

Nothing in the library changed. `import inspeximus` still has zero required dependencies.
## 1.91.0 - UPGRADE IF YOUR AGENT WORKS IN SESSIONS: the next one now starts already knowing what the last one decided

The most-asked thing of an agent memory is that a session END should mean something to the session that
follows. The usual way to build it is to send the transcript to a model and inject its prose summary.
**This does it with no LLM at all** — `SessionEnd` writes a **ledger diff** off the store's own
supersession ledger (which keys changed value, which decisions were recorded, what was erased, what is
still open) and `SessionStart` injects it, size-bounded and ranked.

Three new primitives, and the Claude Code hooks that drive them:

```python
from inspeximus import Inspeximus

m = Inspeximus(path=None)
m.open_session("sess-1")                       # boundary; keyed, so one session is open at a time
m.remember_decision("use Postgres for the ledger", because="sqlite locks", topic="db")
m.close_session("sess-1")                      # ONE digest record, keyed `session::digest`, no LLM

m.open_session("sess-2")                       # ...and the next session already knows
print("Postgres" in m.session_context()["text"])
# -> True
```

`python -m inspeximus.claude_code --install` now writes a fourth hook, `SessionEnd` (with an explicit
`timeout`, because Claude Code gives all SessionEnd hooks 1.5 s together and a hook killed mid-write
writes nothing). Off switch: `INSPEXIMUS_SESSION_DIGEST=0`, or `{"session_digest": {"enabled": false}}`
in `.inspeximus/config.json`.

**Measured** on an 8-session, 2,606-record fixture (`probes/session_digest_multisession.py`; our own
dogfood recall failed at ~2,550, so a smaller one cannot reproduce the problem this fixes):

| | |
|---|---|
| conclusions of session *i* that reach session *i+1* | **1.000** (12/12) |
| below-threshold items kept out | **1.0000** (5,799/5,799) |
| the same rejection with the salience bar removed | **0.2213** — the threshold is what does the work |
| injected block | **<= 1,200 chars**, never exceeded |
| cost at 2,606 records | `close_session` 7 ms, `session_context` 1 ms (budget 1,500 ms) |
| `recall("what changed last session", k=1)` | returns the current digest, **rank 1** |

Two properties a frozen summary cannot have. The injected block is **re-resolved against the live store**
at injection time: a decision reversed in a later session is replaced by the current one, and an erased
record leaves the injected context too. And the digest is **byte-reproducible** — two independently-built
stores replaying the same event log render an identical digest, which is the zero-LLM claim in falsifiable
form (the rendered text is content-ordered and carries no timestamp or record id; those live in `meta`).

**What it deliberately refuses to carry**, because a digest that injects everything has merely reinvented
"paste the whole log". Raw tool exhaust — a file's current contents, a shell command — is **capped** below
the admission bar no matter how much value it accrues, so no amount of recall promotes it. A plain durable
fact with no tag and no key scores 1.2 against a bar of 2.5 and is not injected either; record it with
`remember_decision`, give it a `key` so a later change registers as a correction, or tag it `knowledge`.
`Inspeximus.session_salience` documents every weight.

**Fixed on the way, and it would have ended the loop silently.** A session digest is a cross-cutting
summary, so it is similar to everything it covers *by construction* — and every consolidation heuristic
reads that similarity as a reason to demote it. Measured: the digest cleared the hub pass and was then
retired by the near-duplicate pass as a `state_toggle` against a note it summarises, after which every
following `SessionStart` injects nothing and reports a truthful, useless `items: 0`. Guarding one pass was
not guarding the mechanism; `consolidate`, `consolidate_clusters` and `sleep` now all skip session
bookkeeping, and the test is parametrised over one fixture per pass because a single fixture made two of
the three parameters pass with the guard removed.

**`remember_decision` now forwards `user_id`/`agent_id`/`session_id`.** It dropped them, so the one record
type the digest most depends on could not be attributed to the session that recorded it.

**Honest scope, measured on our own dogfood store.** 884 records, of which **0** clear the salience bar:
that store is 720 shell commands and 164 file states, because the capture hook writes mechanics and
nothing writes decisions through it. The digest correctly reports nothing to resume rather than padding
with 770 mechanics rows. The cross-session loop is bounded by what gets *written*, not by recall quality —
it is a ledger read, so it does not depend on recall ranking at all.

## 1.90.0 - UPGRADE IF YOU SET `m.extractor = regex_extractor` AND HAVE AN EXISTING STORE: first-person keys changed, and old records will no longer be superseded

**What you will see if that is you.** Facts you wrote as "my title is X" are keyed `my title` in your
store. From 1.90.0 the same sentence keys `self::title`, so a later correction lands on a NEW key and the
old record stays ACTIVE beside it. Both values are then live and recall can serve either. Re-key the
affected records, or accept that supersession applies only to writes made from this version forward.
Nobody who passes `key=` explicitly, and nobody who does not set `extractor`, is affected.

The product's promise is that corrections stick, and supersession is keyed, so the promise is only as good
as the key. On conversational input the deterministic keyer could not hold one key across a correction
chain, so nothing bound and the store degenerated toward keep-everything — the advertised behaviour was
not firing at all on the input the product is sold for. Measured on the new `benchmarks/chain_binding/`
harness (15 chains, 18 unrelated pairs, 60 prose sentences), before → after:

| measurement | before | after |
|---|---|---|
| correction chains that collapse to one record holding the final value | 2/15 | **9/15** |
| correction turns landing on their chain's key | 6/22 | **15/22** |
| **false binds on unrelated pairs** (the control; lower is better) | 1/18 | **0/18** |
| non-declarative prose keyed (lower is more conservative) | 8/60 | **4/60** |
| records retired by ingesting 60 unrelated prose sentences | 0 | **0** |

Still zero dependencies and **no model on the write path**: everything added is closed-list surface
normalisation. Leading discourse markers (`actually`, `correction:`, `so`) are stripped from the subject
side as they already were from the object side; trailing time adverbials (`... now`, `... last week`) are
tried-then-fallen-back-on so `the meeting is today` keeps its value; `I'm`/`you're` expand (`'s` does not —
it is the possessive as often as the copula); a leading clause no longer blocks the match (`Dana left, so
my manager is Priya now` used to yield no key at all); the head noun of a complement can carry the relation
(`I'm on the Payments team` → `self::team`); and two relational verb frames (`lives in`, `works at`) are
recognised.

**A data-loss path is closed.** The non-referring guard read `i` but not `i'm`, so `I'm now in the PST
timezone` and `I'm now the on-call engineer` both keyed on `i'm` and retired each other. Contractions now
expand before the guard runs, and quantifier/demonstrative subjects (`both approaches`, `some of the
tests`) are rejected as well.

**BREAKING for first-person keys — read this before upgrading a live store.** `my X is Y` now keys
`self::X` instead of `my X`, and a *current-marking* modifier folds away, so `my title`, `my current title`
and `I work at ...` can meet. A store written by an older `regex_extractor` holds the old keys and a new
write will **not** supersede them; re-key, or accept that supersession applies from the upgrade forward.
Third-person (`Alice's email` → `alice::email`) and bare-copula (`The API rate limit is 500 rps` → `api
rate limit`) keys are byte-identical to before.

`former`/`old`/`previous` are pointedly **not** folded away: they name a different, historical fact, and
folding them in would let `my employer is Globex` destroy `my former employer is Acme`.

**Where it stops, on purpose.** A key is derived only when the sentence NAMES the relation. When a later
turn names only the value and leaves the relation to world knowledge — `I'm a Principal Engineer now` (that
a Principal Engineer is a *title*), `I'm vegan now`, `Dan is now an engineering manager` — it returns None
and the write is a plain append. That is not a gap awaiting a bigger regex: no deterministic keyer crosses
it without an ontology or a model. Pass `key=` explicitly, or plug `make_llm_extractor`, for those.
`derive_key(text)` is exported as the reusable keying core so nothing has to re-derive what a key is.

### benchmarks/locomo - the LOCOMO number is reproducible, and it was understated

No library change: `inspeximus/` is untouched, and nothing here adds an install requirement. What changed is
that a claim this README has carried for a year can now be re-run by anyone who has the dataset.

**`benchmarks/locomo/` — one command, a pinned operating point, a committed result.** From 1.54.0 the
README said the harness behind LOCOMO **retrieval-recall@25 = 0.783 / 0.648** was *"not currently in this
repository"* and asked readers to treat the pair as **reported, not independently reproducible**. Three
things were wrong, and all three are fixed:

1. **The probes could not find their own data.** `probes/retrieval_recall_locomo.py` and
   `probes/locomo_qa.py` resolve the dataset as `<HERE>/../../agora_output/lab/data/locomo10.json`. They
   were written under `research/probes/` in another repository; copied into `probes/`, that path points
   *outside* this repo at a file that does not exist. Both fail on load. `run.py` resolves via `--data`,
   `$INSPEXIMUS_LOCOMO_PATH`, `$LOCOMO_PATH` or `benchmarks/locomo/data/`, pins the dataset's sha256, and
   **skips with a reason and exit code 3** when it is absent rather than scoring on a substitute.
2. **Nothing was pinned.** `recall()` defaults to `reinforce=True`, which updates the value and
   last-access time of everything it returns — so during a benchmark each query is answered by a store
   the previous queries modified, and the score depends on the order the questions were asked in.
3. **No result was committed**, so a re-run had nothing to disagree with. `results/` now holds both.

**The published pair reproduces, and the old number was too low.** At the original probe's own operating
point (`reinforce=True`) the harness measures **0.7839 / 0.6484** against the published **0.783 / 0.648** —
+0.0009 and +0.0004, on the identical denominator of 1536 questions. At the operating point the benchmark
now pins (`reinforce=False`, deterministic) it measures **0.8262 / 0.6986**. The README has been corrected
to **0.83 / 0.70** with the reason stated in place.

*(The denominator is part of the claim. Five of the 1536 questions carry evidence ids matching no turn in
their own conversation, so no retriever can ever hit them; the original probe scored them as misses. This
harness reports that denominator as the headline and `*_resolvable` (n=1531) beside it, because dropping
five unwinnable questions would have flattered the result by roughly the margin that makes a reproduction
look like an improvement.)*

**End-to-end QA, which we had never actually run.** Six arms under one judge on 20 questions of
conversation 0, measured on a quiesced card: `fullcontext` 0.20 > `inspeximus` 0.10 > `naive_recency`
0.05, so the subject lands strictly inside the band; controls `floor_empty` 0.05, `floor_shuffled` 0.10,
`ceiling_verbatim` 0.90 all pass. The arms are a full-context ceiling, a naive-recency floor, inspeximus,
and three controls — an empty-context floor, a shuffled-context floor (each
question answered from another question's retrieved context), and a verbatim-answer ceiling (the gold answer
written into the store as a record and retrieved normally). If a floor scores well or the ceiling scores
badly the harness prints **HARNESS BROKEN** and exits non-zero instead of publishing a number.

Retrieval recall on that same conversation is 0.80 while end-to-end QA is 0.10, and `fullcontext` -- the
whole conversation, no retrieval at all -- reaches only 0.20. On this slice the local 8B answerer, not the
memory, is the binding constraint, which is why the QA score is reported and not headlined. The judge
clears a calibration gate first (GOLD / WRONG / REFUSAL, ≥90% each) or the run is void, reusing the design
of `benchmarks/memops/judge_calibration.py`. The absolute QA score is judge-dependent and is not comparable
to mem0's 66.9% or Zep's 71.2%; the arm ORDERING is, because one judge grades all six.

**Two defects the benchmark's own tests found, both the same shape.** The ceiling control erased its
records with `forget(CEILING_KEY)` — but `forget()` takes record *ids*, not keys, so it erased nothing,
raised nothing, and returned a normal-looking `{"forgotten": 0}` that a bare `try/except` then hid. And the
GPU pre-flight forbade `llama-server.exe` as evidence of a competing job, which on Windows is exactly how
Ollama runs a model: the gate forbade the benchmark's own inference backend and could never pass once a
model loaded. Both are now checked by tests that assert the count and the classification rather than the
absence of an exception.

### witness co-signing - UPGRADE IF YOU RELY ON IT: a co-signed anchor authenticated nothing a reader used

**The witness signature did not bind the fields every consumer reads, and the failure INVERTED the
guarantee rather than merely weakening it.** `verify_cosigned_anchor` verified Ed25519 signatures over the
anchor's `sth_hash` string and never re-derived that hash from the head's own fields. But nothing
downstream reads `sth_hash`: `verify_consistency(prior_anchor)` pins a store to `n_writes`/`writes_tip`,
and `detect_split_view` compares those same fields. So an operator could take a genuinely co-signed
anchor, paste in the tip of a **rewritten** history, keep the original `sth_hash` and the original
signatures — and collect `ok: true, count: 3, threshold: 3` from three honest witnesses. Measured
end-to-end: the auditor then ran `verify_consistency` against that anchor and got a clean **append-only**
verdict on the rewritten store, while the honest store was reported as `fork detected`. A signature over
a hash nobody re-derives authenticates nothing.

The check already existed in `audit_bundle.verify_bundle` as its check (4) and had simply never reached
the primitive that every other witness surface calls — the Python API, both MCP tools, and the audit
bundle's own co-signature step. There is one implementation now (`core.sth_hash_of` /
`core.anchor_binds_its_fields`), used by all of them. `witness_cosign` also refuses to sign a head whose
`sth_hash` does not commit to its own fields, closing the same class at the write end.

**Three vacuous passes closed alongside it** — a verifier that succeeds over nothing:

- `threshold <= 0` made `count >= threshold` true for an anchor with **no signatures**, an **empty
  allowlist**, and no witnesses in existence. A caller computing k from a config that failed to load got
  "externally witnessed" for free. Now refused with an error, in the library and in the CLI.
- An anchor over a store with **no receipt chain** is a valid signed head of nothing; witnesses co-sign
  it and it verifies. `ok` keeps its narrow contract, but the result now carries `covers_history` and a
  `limits` line, and the CLI prints a `NOTE`.
- `detect_split_view` now reports `malformed`, naming any side whose head does not bind its own fields —
  otherwise an auditor reads "inconsistent, no witness proof" when the answer is "that is not a head any
  witness could have signed".

**New: `inspeximus anchor` and `inspeximus witness`.** `witness_pool.py` and `witness_server.py` have
worked since 1.34.0 and were reachable from no shell command at all, so the strongest operator-adversarial
property in the package was invisible to anyone who did not read the source. `anchor`, `witness keygen`,
`witness cosign`, `witness verify`, `witness split-view` and `witness serve`, with exit codes meant for
CI (0 pass, 1 fail, 2 refused-to-co-sign or usage, 3 undetermined, 4 no Ed25519). A witness's `--state`
file defaults to `<key>.state.json` rather than being optional: each CLI call is a fresh process, so a
witness with no state file has no memory and can never refuse anything.

**New: `docs/TRANSPARENCY.md`**, a cold-reader quickstart from an empty directory to a verified co-signed
anchor, plus the worked split-view proof. Every command on the page is **executed** by
`tests/test_witness_quickstart.py` and its output asserted, so the page cannot rot. Both controls are
asserted in both directions: the detector fires on a divergent pair and stays silent on an identical one,
a three-witness fork attempt cannot reach threshold at k=1, 2 or 3, and a tampered anchor fails —
parametrised over all four committed fields, beside an honest anchor that must still pass.
`examples/12_split_view_detection.py` runs the whole story. Prior art credited rather than reinvented:
RFC 6962 (Certificate Transparency), Sigstore/Rekor.

## 1.89.0 - UPGRADE IF YOU USE `slash()`/`restore()`: a retraction could be lost, and it walked a stale graph

Two defects on the accountability path, both found by adversarially reviewing a claim we were about to
publish about our own behaviour. Both are in the wheel.

**A retraction the process could lose was not a retraction.** `slash()` and `restore()` went through the
throttled `_save()`, which batches on a 5-second timer. That is right for `remember()` in a hot loop and
wrong for the one operation whose purpose is to take standing away. Measured: slash a credited record,
lose the process before the timer fires, reopen the store, and you get `meta:{}`, `good:null`,
`bad:null`. The retraction is gone and the record is load-bearing again, while the caller was told
`{"slashed": 1}` and had no way to learn otherwise. Silent on both sides. Both operations now force the
write; re-measured across a lost process, slash survives (`meta.slashed` true, good 0.0, bad 6.0) and
restore survives (good 5.0 restored exactly).

**`slash(scope='source')` picked targets from a frozen `taint` set.** That set is computed once inside
`remember()` and never revisited, so a `derived_from` edge arriving AFTER the write was invisible to the
lever. The ordinary case is a summariser that learns its inputs late, or an app repairing lineage it
discovered downstream: the descendant named the retracted record as its evidence and kept full standing
anyway. `forget_subject` had been closing forward over those same edges all along, so two operations
answering "who does this reach?" walked different graphs and only one was right. They now walk the same
one, a forward closure over declared `derived_from`, mirrored in `restore()` so an appeal can never be
narrower than the penalty. (Doyle, *A Truth Maintenance System*, AIJ 12(3) 1979, for the mechanism;
Biba 1975 for the integrity direction.)

**New: `scope='lineage'`** — the named records plus everything transitively derived from them, and
nothing else. Neither existing scope served the common case: `'memory'` stops at the named record and
`'source'` forfeits every sibling sharing a source label, so catching ONE poisoned memory and retiring
the conclusions built on it had no primitive. `scope='memory'` keeps its documented meaning.

**`derived_from` ids that do not resolve are no longer dropped silently.** A typo produced a record with
no lineage AND full primary standing: the write announced itself as derived and was banked as an
observation. Unresolvable parents are kept as `derived_from_unresolved`, and a write whose entire claimed
lineage fails to resolve is an orphan.

12 tests, 7 of which fail on the pre-fix code. `test_the_frozen_taint_really_is_empty` is a fixture
control that fails if the defect stops reproducing, so a later refactor cannot leave the suite green
because the case never arises.

### Also in this release - measurement tooling (no library code; nothing in the wheel changes)

**The stated +/- was normal theory, and it ran too tight in the flattering direction.** `sd_rel_error(n)`
returned `sqrt(1-c4^2)/c4`, exact for normal data. Var(s) carries an excess-kurtosis term, so the truth
scales by roughly `sqrt(1 + kappa/2)`. Measured over 40,000 subsamples per cell, quoted-over-actual:

    pool (excess kurtosis)      n=5     n=20
    ungated  (-0.26)           1.04x   1.07x    conservative
    normal   ( 0.00)           0.98x   0.99x    right
    gated    (+0.52)           0.84x   0.87x    TOO TIGHT
    lognormal(+~4)             0.58x   0.40x    far too tight

Too tight is the direction that flatters, which is the failure this module exists to stop, so it was
reporting the disease it was written to cure. `sd_rel_error(n, values)` now applies the sample inflation
`sqrt(1 + g2/2)`, floored at 1.0 so the figure can only widen, and `spread()` passes its values.

**It is a partial fix and the docstring says so, with the residual as a number.** The correction moves
quoted-over-actual from 0.84x to 0.90x on the gated arm and 0.58x to 0.66x on the lognormal. It cannot do
better: sample kurtosis is a FOURTH-moment statistic, even more extremum-sensitive than a range, and a
small sample from a heavy tail usually contains no tail point, so it looks light-tailed. The detector
fails in the same regime as the thing it detects. Two alternatives were measured and are worse -- a
bootstrap SE of the estimate reports 0.69x-0.89x of the truth across these arms (0.47x on the lognormal).
So the printed figure is labelled a FLOOR, and a non-positive g2 does not clear a sample as normal.

**The bootstrap paragraph in `spread()` was wrong and is replaced.** Schenker 1985 (JASA 80:360-361)
already established that percentile and bias-corrected intervals for a normal variance under-cover badly
at small n, which is why Efron built BCa in 1987; our run reproduces that rather than discovers it
(percentile 62.1% and 64.0% at n=5 on our pool and a normal control, BCa 59.7% and 65.1%, so BCa does not
rescue it -- Schenker's own point). The stated MECHANISM was a guess: "five points cannot be resampled
into a tail they never sampled" is falsified by a chi-square interval built from those same five points,
which covers 94.2% on normals and 96.4% on our pool. The support is not the binding constraint; the
non-pivotality of s is. No figure is quoted for a studentized interval because two of our own
implementations disagree by ten points on the identical question.

**A coverage number should not be read to three digits.** Across six independently built 120-seed pools
the n=5 coverage ranged 46.6% to 67.6%; binomial error alone would have said +/-0.8 points. Separately,
the 4-value arm's low coverage is partly not about skew: 6.2% of five-draws there come out all-identical,
giving a zero-width interval that cannot cover by construction, and excluding those it is 50.5% against
62.3% on the 13-value arm.

Seven mutations registered and killed. One of them survived its first run: the guard compared a rounded
printed percentage against a truncated threshold (15 against 14), so deleting the entire fix still passed.
It now asserts the exact figure. A threshold loose enough to admit the absence of the thing it guards is
not a guard.

This came out of a retroactive adversarial audit that should have run before the result was described
publicly and did not.

**The trial floor was the symptom; the RANGE was the defect.** `ProbeGate.spread()` gated on `n >= 20`
and then quoted a range. A range is an extremum statistic: its expectation only grows with n, so a small
sample can only ever understate it -- which is why an under-sampled spread does not read as noisy, it
reads as *tight*, the direction that flatters a result. Measured over 200,000 draws of five, a 5-sample
range sits below its own 25-sample expectation in 95.7% of runs. The sample SD has no such shape; its
small-sample bias is a known constant (c4 = 0.9400 at n=5) and dividing it out leaves an estimator that is
too low in 52.8% of runs -- a coin flip, not a direction. `spread()` now reports the bias-corrected SD and
keeps the range only as a descriptive figure, marked not-comparable across different n.

Subsampling a 400-seed pool of `identity_gate_supersession_probe.py` (true SD 0.0569, true range 0.300):

    n            5       10      20      25      50
    E[s/c4]   0.0574  0.0572  0.0569  0.0570  0.0570   <- settled by five
    E[range]  0.131   0.170   0.203   0.213   0.242    <- still climbing at fifty

c4 is normal theory and this metric is a bounded proportion on a 0.025 grid, so the transfer was measured,
not assumed: the correction lands at 1.008x the pool SD on the milder arm (skew 0.40) and 0.971x on the
skewed one (skew 1.13, four distinct values), so ~3% of bias survives there at n=5. Both beat the range's
0.437x and 0.489x.

**The floor stays, for a different reason than it was given.** De-biasing fixes the DIRECTION of the
error, not its SIZE: a corrected SD from five trials still lands between 0.55x and 1.49x the truth. New
`sd_rel_error(n)` states that outright and `spread()` prints it with every number (+/-36% at n=5, +/-16%
at n=20), so the estimator declares its own uncertainty instead of a threshold standing in for it.

**A bootstrap interval was the other candidate, and it was measured and rejected.** A percentile-bootstrap
95% CI for the SD covers 62.7% at n=5 on that pool (47.6% on the skewed arm), 85.8% at n=25, and only
nears 90% by n=50. Resampling five points cannot invent a tail five points never sampled, and a CI that
announces 95% and delivers 63% is worse than the range it replaced because it ships a guarantee.

**The probe's saved artifact disagreed with the line it printed.** `identity_gate_supersession_result.json`
recorded `candidates_per_run: 326.0` where stdout said 65: `ncand` was divided by the literal `5`, the
seed count from before it was raised to 25. 326 is not merely wrong, it is impossible -- the scenario only
makes E*ROUNDS = 240 corrections, so it cannot fork more than 240. Guarded now by that physical bound plus
an artifact-vs-stdout agreement check, neither of which needs to know the right answer. The artifact also
carries `seeds`, both SDs and `sd_rel_error` now. A sweep of every probe declaring a trial count found no
second instance.

The falsification control for all of this failed its own first mutation: it re-implemented `stdev(x)/c4(5)`
inside the test, so deleting the correction from `spread()` left it green. It now reads the number the
gate reports. Three mutations registered in `tools/mutations.json` and killed.

Credit throughout: jacksonxly, who pointed out that the range is an extremum estimator and the SD's bias
is correctable.

## 1.88.1 - UPGRADE IF YOU USE THE MCP SERVER OR THE LANGGRAPH STORE

Two of these are broken for users on 1.88.0 right now, not latent.

**The MCP server would not start on a fresh install.** The `mcp` extra declared `mcp[cli]>=1.0` with no
upper bound. mcp 2.0 renamed `FastMCP` to `MCPServer` and removed `mcp.server.fastmcp`, which this package
imports, so `pip install "inspeximus[mcp]"` resolved to 2.0.0 and every MCP tool raised ImportError. The
extra is now `mcp[cli]>=1.28,<2` -- the bound the MCP SDK's own migration guide prescribes for v1
dependents, not a judgement call of ours -- with the floor raised because `>=1.0` was never a tested claim
and a lowest-resolution install could hand you an API this code does not match. The cap is on an optional
server extra; the core library stays zero-dependency, and the cap lifts once the server is ported to v2's
`MCPServer`.

The error message also named `pip install "mcp[cli]"` as the remedy -- the command that produces the
failure. It now distinguishes "SDK absent" from "SDK present but 2.x" and gives a bounded install in
both. Supporting mcp 2.x is a real port and is not attempted here.

**The `google-adk` extra now declares `google-adk>=2`.** `register()` imports
`google.adk.cli.service_registry`, which exists in 2.5.0 and does not in 1.14.1. Declared unbounded, the
new `mcp<2` cap was enough to change pip's search order and an all-extras install resolved to 1.14.1,
where that import fails. This is a floor, not a cap: it states the versions the integration actually
works against.

**A LangGraph search matched the namespace prefix as a string instead of by segment.**
`search(("user1",))` also returned records under `("user10",)` -- a sibling namespace the caller never
asked for -- because `"lg::user1"` is a string prefix of `"lg::user10::notes"`. It is now a segment-wise
prefix; LangGraph's reference `InMemoryStore` returns only `user1` and ours now matches it, while deeper
namespaces under `user1` stay in scope as they should.

This is a scoping-correctness defect against the `BaseStore` contract. It is NOT a failure of this
library's separate `tenant=` isolation, which we measured and found unaffected: two `for_tenant()` handles
writing the same `(namespace, key)` through the adapter each read back only their own record, and
`search`/`list_namespaces` stay scoped. But if your application relied on namespace scoping to keep users
apart, upgrade.

**Two distinct (namespace, key) addresses could resolve to one record.** The record id joined the
namespace with `/` and appended the key after `::`, and both separators are legal INSIDE a namespace
element and inside a key (LangGraph rejects only `.` in namespace labels, and does not validate keys at
all), so `("a","b")+"k"` and `("a/b",)+"k"` shared a record, as did `("u1",)+"b::k"` and `("u1::b",)+"k"`.
The reference `InMemoryStore` keeps each pair distinct. Components are now percent-escaped, and -- more
importantly -- reads no longer depend on that string at all: `get`, `search`, `history` and delete resolve
identity from the structured namespace/key already stored on every record.

No user reported this. We fixed it because the failure is silent -- the second write overwrites the first
with no error -- and a store advertising `BaseStore` parity should not depend on which characters the
caller happens to avoid.

Existing stores need no migration and no record is rewritten: because identity is read from the
structured fields rather than the joined string, records written by earlier versions keep resolving
through `get`, `search`, `history`, delete and erasure exactly as before.

**New: `InspeximusStore.erase_namespace(namespace, include_children=False)`.** The per-record subject
string is `"lg::" + "::".join(namespace)`, which is lossy and is deliberately left that way for
compatibility, so `store.forget_subject("lg::a::b")` still erases both `("a","b")` and `("a::b",)`. Use
`erase_namespace()` for a data-subject request: it resolves ids from the structured namespace, so it is
exact, and it reaches records written by earlier versions.

**A blank substring deleted the whole store.** The two surfaces differed, so precisely: the pydantic-ai
`forget(contains=...)` tool deleted 3 of 3 memories on `forget("")`. The CLI already refused
`--contains ""` -- but only as an accident of falsiness, and it answered "pass --key, --id, or
--contains" to someone who had just passed `--contains` -- while `--contains " "` deleted 3 of 3, because
every multi-word memory contains a space. Both now refuse any blank (empty or whitespace-only) needle. The pydantic-ai one is a tool the MODEL
calls, so an empty slot is an ordinary failure mode, not an exotic one. A non-blank needle is still
deliberately broad ("ann" reaches "Joanna"); that is unchanged.

**`influence_gate_report` contradicted the gate it reports on.** It re-derived the corroboration test
inline and had drifted: it ignored the slashed/orphan blocks and `credit_requires_warrant`. On a store
with `credit_requires_warrant=True`, six records with unwarranted credit and two slashed, the gate passed
0 of 6 while the report claimed 6 of 6 with `would_block_frac 0.00` -- wrong in the direction that causes
an outage, since the report exists to say whether enabling the gate is affordable. One predicate now
serves both, and `why_recalled` reports which bar decided a record.

**An audit bundle's governance summary could contradict the tombstone chain beside it.** A bundle claiming
`erasures_total: 0` while carrying two tombstones verified `ok=True`. The summary is now cross-checked
against the chain, and the per-request breakdown against its own total.

### Faster

Measured on one machine; fixtures stated because these numbers move with the data.

- **Erasure.** `_emit_tombstone` rewrote the entire tombstone chain on every tombstone, so erasing k
  records cost k rewrites of a chain growing to k. It is now written once per batch. TWO changes compound
  on this path -- that batching and the serialization change below -- and we did not measure them
  separately, so these figures are their joint effect, not the tombstone fix alone. `forget_subject` with
  k subject records among n others, median of 3: k=50/n=2,000 ~0.12s -> ~0.03s; k=200/n=4,000 ~0.53s ->
  ~0.13s; k=400/n=8,000 ~2.0s -> ~0.37s; k=800/n=8,000 ~5.2s -> ~0.7s. Read that as roughly 3x to 7x, not
  as precise figures: run-to-run spread on this machine reaches 20% on some workloads. What is not noisy
  is the shape -- at fixed n, doubling k used to cost ~2.6x and now costs ~1.95x, i.e. linear in the size
  of the erasure. (Not 4x before: the total also carries O(n) work that does not scale with k, so the
  quadratic term was never the whole cost -- only the part that is now gone.) Deferring is also safer:
  the old order left j-of-k tombstones on disk claiming erasures the store save had not performed.
- **Store writes.** The store is serialized one record per line instead of `json.dumps(indent=1)`, which
  keeps CPython's C encoder. n=20,000 records of ~48 characters, median of 5: ~240ms -> ~80ms, roughly
  3x. The byte counts are exact rather than timed: 7,704,892 -> 6,164,892, i.e. 20.0% smaller. Still
  line-diffable; fully compact would be ~45ms and just 40,001 bytes smaller.
- **`contradictions()`** tokenizes each anchor once instead of once per pair: 1.46-1.88x depending on n.
  It remains an all-pairs O(n^2) scan -- ~9.5s at n=2,000 and ~162s at n=8,000 on a store with real
  clashes -- and the MCP session-start digest that runs it now says so with current numbers.

### Documented, not fixed

- **`memory_report` is a ~12 second call at n=8,000** (~2s at n=2,000; median of 5, run-to-run spread
  15-25%). It samples 400 records and runs a full recall for each, so it is O(400 x n). Both the method
  and the MCP tool description now state this; the cheap counts are single passes.
- **The erasure residue scan is a literal, case-sensitive byte match.** Planting one secret in eight
  encodings and scanning for the original: exact and JSON-quoted are FOUND; lowercased, uppercased,
  double-spaced, newline-separated, base64 and hex are MISSED. `ok=True` means "this exact byte sequence
  is absent", never "the value is gone".

### Also

CLI: `-k 0` and `-k -5` are rejected instead of returning an empty result with exit 0; a `--path` whose
directory does not exist now warns instead of reading as an empty store. `check_code` no longer reports a
clean build gate on a store it could not read. `install` refuses a non-object config file instead of
crashing on it. `credit()`, `slash()`, `monitor()`, `spend_irreversible()` and `rederive()` accept a bare
string id -- previously a str was iterated over its characters and the call silently did nothing. Opt-in
`credit_burst_window` collapses repeated credit from one source within a window.

## 1.88.0 - UPGRADE IF YOU RELY ON ERASURE: an erasure left the subject's CURRENT value behind

Same class as the 1.87.0 fix below, pointing the other way. 1.86.0 erased a stranger's records
(over-erasure); this is under-erasure, and what survives is the live data rather than the stale copy.

```
remember("alice home address is 5 Elm St", key="alice::addr", source={"doc": "hr/alice"})
route("actually alice moved to 9 Oak Ave", key="alice::addr", object="9 Oak Ave")

forget_subject('hr/alice')  ->  erased = 1, reported as success
  survived:  [active] 'actually alice moved to 9 Oak Ave'
  residue of the CURRENT value:  True
  residue of the OLD value:      False
```

A correction written through `route()` carried no source and no lineage, so nothing connected it to the
person it was about. The erasure removed the stale address and kept the live one. The identical correction
through `remember(source=...)` erased both.

**The fix is not a parameter.** `route()` knows which record it is correcting, so it declares that edge
itself — joining `rederive`/`revert`/`submit_revert` in the category where the store asserts provenance
about its own work. `forget_subject` cascades along `derived_from`, so this closes with **no change
required of the caller**, which is the point: the callers who hit it are the ones who never passed
provenance. `route(source=...)` exists as well, for the case lineage cannot reach — a `route()` asserting
on a new key has no parent.

**NEW: an erasure now reports whether a SURVIVOR still holds what it just erased.** Every erasure return
carries `residue_in_store`:

```
remember("alice home address is 5 Elm St", source={"doc": "hr/alice"})
remember("summary: she lives at 5 Elm St", source={"doc": "svc"})   # not attributable, not derived
forget_subject("hr/alice")
  -> erased: 1
     residue_in_store: {ok: False, findings: [{id: ..., field: "text", fingerprint: "4016c1ad3454"}]}
```

`scan_residue` answered this for OTHER stores on disk; nothing answered it for this one. It can only be
done AT ERASURE TIME — tombstones are content-free by design, so the values vanish with the rows and no
later audit can compare against them.

The report carries a **fingerprint, never the value**: a compliance report gets pasted into tickets, and
reprinting the erased string would undo part of the erasure it certifies. A search that compared nothing
reports `ok: False` (values under 4 characters are skipped, because "ok" matches everywhere — and skipping
them all is not a clean result), and a bounded scan says how many records it did not examine. It is a
**heuristic** and says so: a paraphrase carries the fact without the string, so a clean result is evidence,
not proof. Measured cost: 18 ms over a 1000-record store.

Also: **`inspeximus decision` (CLI) gained `--source` / `--derived-from`**, the last surface that could not
attribute a decision to the person it is about; and the skip census stopped **reading a guard out of
prose** (it text-searched top-level nodes, and a module docstring is one, so a file whose comment merely
mentioned `importorskip` was counted as entirely hidden).

`observe` remains without provenance, and the reason is now measured rather than assumed: it writes no
record at all — it accrues evidence and can reopen a settled value — so a `source` on it would have
nothing to attach to.

## 1.87.0 - UPGRADE IF YOU RELY ON ERASURE: 1.86.0 deletes the wrong person's records

**Who should upgrade: everyone using `forget_subject`, immediately.** Verified against the wheel
downloaded from PyPI, not against this repo:

```
inspeximus 1.86.0, store holding only hr/alice
  forget_subject('hr/nobody-here')  ->  erased = 1      <- a subject NEVER written to the store
inspeximus 1.87.0, same store
  forget_subject('hr/nobody-here')  ->  erased = 0
```

Subject matching ran on the canonical source form, which keeps only the host, so `hr/alice`, `hr/bob` and
`hr/nobody-here` were one key. The ambiguity guard cannot fire when only ONE real source sits in the
bucket, because there is nothing for it to collide with — so a right-to-erasure request naming a subject
that is not in your store hard-deletes a different subject's records and reports success. With siblings
present the guard does refuse (measured on the same wheel), which is why this went unseen: the dangerous
case is the quiet one.

Erasure now matches on a path-preserving form applied at `_resolve_subject`, so all five subject-scoped
destructive paths get it, and the derived-lineage cascade is preserved.

### Also in this release

- **The compliance moat was unreachable from the MCP server and half-unreachable from the CLI.** The write
  tools took no `source` or `derived_from`, so a store written through the product surface answered
  `would_erase = 0` to every phrasing of an erasure request while the same write through the library
  answered 1. `remember` and `remember_decision` now take both (CLI: `--derived-from`), and the write
  result carries `attributable` — the caller is the only one who can fix an unattributable write, and only
  while they still know where the text came from. `route`, `observe` and `resolve_reopened` still cannot;
  a census test names them so the list cannot rot into a lie.
- **A retired write was reported to the caller as one that landed** — see below.
- **`INSPEXIMUS_ECHO_GUARD=0` did nothing in the library** — see below.
- **A dissenting erasure target was displayed as a confirmation.** `coverage.confirmed` counted the store's
  own self-attestation, so one external target that answered "still recoverable" read as `1 of 1
  confirmed`. It now counts external targets only; the store's answer is reported as `store_self_check`.
- **`erasure_audit(subject)` matched on the coarse key**, so a correctly completed DSAR reported residue
  and a subject never written was told a stranger's record was attributable to it.

## Audit notes from the day 1.87.0 was assembled

1.87.0 shipped two changes big enough to be worth attacking immediately: a default flipped ON for everyone,
and a rewrite of how a destructive path resolves a subject. Both were audited the same afternoon, both had
defects, and all of them were the same shape the whole month has been producing — **a surface returning a
clean verdict about input it never examined.**

- **A write the guard RETIRED was reported to the caller as one that LANDED.** `remember()` returns an id
  either way, so a legitimate A→B→A reversal read as a success and left the store on B (measured, with an
  `echo_guard=False` control: a LangGraph `put({'theme':'dark'})`/`get()` round-trip returned
  `{'theme':'light'}`; an oscillating flag dropped 2 of 6 writes and inverted its final value). Seven audit
  findings, one defect, reached through seven doors — and the signal already existed on `route()`, just not
  on the path everyone uses. **`store.last_write`** now carries it: `{id, key, status, blocked, policy,
  current_id, note}`, where the note names `reaffirm=True` as the remedy. It is reset at the START of every
  write, because a verdict left over from an earlier call would be read as this call's.
- **`INSPEXIMUS_ECHO_GUARD=0` did nothing in the library.** The surfaces honoured it; the constructor
  hardcoded `True` and took no argument at all. Measured in three subprocesses, `=0`, `=1` and unset were
  identical. **`Inspeximus(echo_guard=...)`** now exists, and one resolver decides for the library and the
  surfaces both — explicit argument > env var > ON.
- **`memory_report()` could not see a refusal.** A store that retired a write on arrival and one that took
  an ordinary correction returned byte-identical summaries and the same `superseded` count. It now carries
  `retired_on_arrival` + `retired_by_policy`, read from `supersession_report()` rather than recounted.
- **`erasure_audit(subject)` still matched on the coarse key** — the morning's own fix, one lever over, on
  the surface an operator reads to decide whether a DSAR is discharged. On a two-person store all three of
  `hr/carol` (correctly erased), `hr/dave` and `hr/nobody-here` (never written) returned the SAME record,
  with the requested subject interpolated into the detail string. It now reuses `_narrow_to_subject`.
- **`dry_run` gained `coverage`.** The real run got it earlier the same day; the preview — the one surface
  built for deciding *whether* to erase — did not say what the erasure covers.

**Correction to the 1.87.0 note below:** it credits the default flip with fixing the nine adapters. It did
not. `_surface.open_store` had already fixed them earlier the same day (1.86.0); the flip removed the
*reason* the split existed. Two audit findings that made the same release notes were also checked and are
**not real**: the erased value does not survive in `object`/`key`/`meta` (measured, no residue), and
`slash`/`spend_irreversible` already refuse an ambiguous subject by default with a named collision and an
explicit `allow_ambiguous=True` escape.

### 1.87.0 in detail — the echo guard is ON by default, and a DSAR for a stranger no longer deletes you

**BEHAVIOUR CHANGE 1: `echo_guard` now defaults to ON.** It shipped OFF so a direct API caller got exactly
what they constructed, byte-identical to legacy. What that meant in practice is that the mechanism this
library exists for was off unless you knew to ask — every product surface had to re-enable it, and the
nine framework adapters missed it for ten releases (that is what 1.86.0 was about). Measured live against
the real products, same procedure and same rank-1 reading for each, n=8:

```
correct a fact, then restate the RETIRED value in different words; what does rank 1 serve?
  inspeximus, product surface / new default   0.000 stale
  inspeximus, echo_guard = False (old default) 1.000 stale
  mem0 2.0.14 (live)                           1.000 stale   -- it keeps the superseded memory AND ranks
                                                                 it above its own correction (0.872 / 0.828)
  Graphiti                                     not run — needs a graph database
```

"Correct a fact once and it stays corrected" is the first line of the README; a default that contradicts it
was protecting byte-compatibility at the cost of the promise. **To restore the old behaviour:** set
`store.echo_guard = False` after construction. Nothing else changed; the wedge test that used to rely on
the old default now asks for it explicitly and still reproduces.

**BEHAVIOUR CHANGE 2 (data loss, fixed): a right-to-erasure request naming a subject that was never in the
store hard-deleted a DIFFERENT subject's records and reported success.** `forget_subject("crm/nobody-here")`
returned `erased=2` and took both of `crm/alice`'s records with it. Subject matching ran on the canonical
source form, which keeps only the host, so `crm/alice`, `crm/bob` and `crm/nobody-here` were one key — and
the ambiguity guard cannot fire when only one real source is in the bucket, because there is nothing to
collide with. Erasure now matches on a path-preserving form, applied at `_resolve_subject` so all five
subject-scoped destructive paths get it, and the derived-lineage cascade is preserved. `crm.example.com/alice`
and `.../bob` no longer raise `AmbiguousSubject` — they are simply different subjects now, so the request
completes and the other person is untouched. Genuine ambiguity (`User_42` vs `user-42`) still refuses.

**NEW: `forget_subject()` returns `coverage`.** It used to return `{"erased": N, ...}` and say nothing about
the world outside this store, while a store-native delete leaves the application's own vector index fully
populated (8/8 residue; wired to a registered target, 0/8). `coverage.complete` is true only when at least
one external target was registered AND every one verified the data absent; with none registered it says so
and names the remedy. A broken integration still leaks, but it can no longer produce a clean receipt while
leaking.

## 1.86.0 - a correction could be undone through any adapter, and the store then refused to be put right

**BEHAVIOUR CHANGE, and the reason to upgrade: through any of the nine framework adapters, one restatement
of a corrected value UNDID the correction — and then wedged the store, because the honest re-correction
looked like an echo of the value the guard had just retired.** Measured on one store file, before:

```
1. the CLI corrects the payout wallet 0xAAA -> 0xBBB     store serves 0xBBB
2. an adapter restates the OLD value                     store serves 0xAAA   <- correction undone
3. the CLI corrects it again                             store serves 0xAAA   <- and now it is STUCK
```

After: `0xBBB` at all three steps. "Correct a fact once and it stays corrected" — the first line of the
README — was false through the ordinary integration path, and only through it.

The guard was not broken. `echo_guard` and the receipts-sidecar rule were re-declared at every entry
point, and the adapters were never told: `cli._store` and `mcp_server` turned the guard on (with a comment
in the CLI explaining why those two had to agree), while the nine adapters built `Inspeximus(path=...)`
directly and inherited the LIBRARY default, which is OFF. `inspeximus/_surface.py` now holds both rules and
every write surface calls it — twelve adapter sites, the CLI, the MCP server, the Claude Code hook.

**The library default is deliberately unchanged.** A caller who constructs `Inspeximus` directly still gets
exactly what they wrote; surfaces are what needed one posture. If an adapter-backed workflow relied on a
restatement resurrecting a retired value, set `INSPEXIMUS_ECHO_GUARD=0` — it now reaches every surface,
which it did not before.

**Receipts, the same shape.** A store that already has a `.receipts.json` sidecar keeps receipts ON. That
rule lived in `cli._store` alone, so an MCP or hook write against a receipted store did not extend the
chain and the next `verify_writes()` reported an uncovered record — the surface punching a hole in the
evidence it exists to produce. Nothing is created unasked: no sidecar, no receipts.

**Three surfaces could not reach the check they advertised.** All one shape — a clean answer about input
the surface structurally never examined — and in each case the check already existed one file over:

- MCP `verify_audit_bundle` never passed `store_items`, so content was never compared. A bundle is
  content-free by design, so a clean chain over SUBSTITUTED text verifies PASS — and the returned `limits`
  told the auditor to "pass `store_items=`", a parameter this surface did not have. New `store_path=`; a
  path that does not exist is REFUSED rather than downgraded to the content-blind verdict, because opening
  a store creates it and a typo would otherwise return `ok` over an empty store the call had just made.
- MCP `compliance_check` dropped `prior_anchor`, so `not_append_only` (Art. 12/19) could never fire there
  however the history was rewritten — while the tool's own docstring listed it among the violations it
  returns. It is the only operator-adversarial check of the four; the CLI has had `--prior-anchor` all
  along.
- `inspeximus audit-verify`'s `--store` existence guard reached one of its two entry points. One
  implementation now, called from both.

**And the smaller true things:**

- `memory_report` sampled the OLDEST 400 records and called the result a sample. The same store reported
  1.0 / 0.245 / 0.99 depending on insertion order. Seeded random sample now.
- The CLI `revert` exited 0 on a REFUSED revert, so `inspeximus revert key && echo done` printed `done`
  after nothing had happened.
- `docs/API.md` published precision 0.06–0.23 and "43 wrong parents" where the cited probe prints 1.000
  and ZERO false parents. Corrected, with the CHANGELOG annotated rather than rewritten.
- The CHANGELOG cited two probes that were never committed; it is now inside the citation guard.
- README claimed "one exception" to every number tracing to a runnable probe. There were three.

**The gate that certifies all of this was itself reporting a clean result over work it had not done.**
Found by running it: `74/75 killed, 0 survived, 1 skipped` — and exit code **0**, which is all CI reads. A
skip now fails the run. The skip's cause was worth more than the exit code: the allowlist of files a run
may restore keyed on the filename shape `*_result.json`, a convention a fifth of the receipts do not
follow, so `probes/governance_sufficiency_bytes.json` was left dirty by every run (45 lines of it were
committed as churn). Dirt that survives a run is then recorded as pre-existing by the NEXT run, protected
as though a human had written it, and read as fact — so a test compared our published echo-policy receipt
against a MUTANT's output, went red, and took its mutation out of the count. Reproduced deliberately
before fixing. The rule is now the directory plus the extension, asserted against the tree rather than a
remembered list.

**Tests.** 50 new, **1394 passing**. The wedge is pinned as a CONTROL as well as a fix — the failing
sequence is asserted against the library default, so the passing tests cannot pass for a trivial reason —
and all twelve adapter construction sites are checked with the opener SPIED on rather than replaced, so
the assertion is about the store that actually reaches the adapter. An AST guard fails if any write
surface constructs `Inspeximus` itself again: the fix landing while the class lives one file over is the
shape this repository meets most often, and it is a test now rather than a habit. 77 mutations in the
committed spec.

One older test had to be rewritten. It asserted the literal `INSPEXIMUS_ECHO_GUARD` appeared in both
`cli.py` and `mcp_server.py`, so it went RED at the moment those two surfaces — plus nine adapters and the
hook — came into agreement for the first time. It reads the posture off the surfaces now, in both
directions.

## 1.85.0 - recall is deterministic, and two policies that were running on luck are now declared

**BEHAVIOUR CHANGE.** Two identical stores, one query, and 7% of the time a different answer. That is now
zero, and the noise was removed at its source rather than absorbed downstream.

**Two sources, both measured.**

`_bm25_scores` iterated `qtok`, a SET, when summing per-term contributions. Float addition is not
associative and set iteration order is randomised per process, so the same record scored differently
between runs. Sorted now.

`_effective_value` computed the decay age from wall clocks at full precision, while the half-lives it
feeds are hours to days — so sub-second resolution carried no meaning and plenty of noise. The age is
quantised to whole seconds.

Measured across 120 runs of one fixture: **the score spread is 0.000e+00** on every record (it was
5.7e-10, with 19 distinct values for a single record). One top-k order per mode over 120 runs, identical
across six `PYTHONHASHSEED` values.

**Two policies were running on that noise, and both are now explicit.**

- *Recall*: among equally relevant memories the newer one now comes first. It always did — through
  microscopically less decay on the newer record — and that accident was what surfaced the memory you
  asked for in a crowded store. Removing the noise removed the accident; the ADK crowded-store audit
  caught it before release, on the attempt that quantised the ranking score instead of fixing the source.
- *Capacity eviction*: among equally valuable memories the older one is discarded. Same accident, and
  quantising the decay silently **flipped** it to evicting the newest.

An earlier attempt quantised the RANKING score. It was reverted: in a crowded store the target ranks first
while being exactly tied with 58 competitors and 5.7e-10 above two more, so the noise and the smallest
meaningful gap were the same size and no quantum could separate them.

Found by `recall_reinforce_flag_probe.py`, one of the 48 probes that no doc cited and no test ran until
1.84.0. It had been failing 4 of 20 runs.

13 tests. Two of them had to be rebuilt before they could fail at all: the BM25 test ran in ONE process
(where a set iterates identically every time) with eight short tokens (two or three addends per score), so
it was green while the mutation restoring set order survived. It now runs in five processes under
different hash seeds, with 45 tokens and ~40 addends. The score test read `recall`'s output, which rounds
to three places and cannot see 1e-10; it tests the mechanism directly instead. Mutation-verified 3/3.
1302 tests pass.

## 1.84.0 - remember_decision kept every decision on a topic active at once

**BUG FIX in a flagship API, found by running the probes nothing cites.**

`remember_decision`'s docstring promises the product's thesis applied to decisions: *"a NEW decision on the
same topic RETIRES the old one ... recall always returns the CURRENT decision ... and
`revert('decision::<topic>')` restores the prior one."* None of it happened.

```python
m.remember(key="decision::database", ...) x2        -> 1 active   (correct)
m.remember_decision(topic="database", ...) x2       -> 2 active   (1.83.0)
```

One line: `object=(topic.strip() if topic else None)`. The topic is already the KEY. Passing it as the
VALUE too made every decision on a topic look like a restatement of the same value, and keyed supersession
is object-identity aware precisely so a paraphrase does not count as a correction — so the second decision
was read as a reaffirm and retired nothing. `object` is now the decision. Supersession, history and
`revert()` all behave as documented.

It is exposed over MCP, so an agent asking "what did we decide about X" could be handed two contradictory
current answers.

**How it was found, which is the more useful part.** 48 of our 101 probes were cited by no doc and executed
by no test. A sweep of all 48: 36 ran clean, 5 exceed the per-probe budget, 6 failed — and two of those six
were failing on correct assertions about `remember_decision`. They were not stale probes; the product had
regressed underneath them, and nothing was looking. `identity_gate_supersession_probe.py` had also rotted
into a crash on its first line (`tempfile.mkstemp` creates the file empty, and the store correctly refuses
to open what it cannot parse).

The suite now runs **every** probe, cited or not. Exemptions are named, not silent: 5 that exceed the time
budget, 3 that read `server/.env` from the private research repo, and the dataset-dependent ones. This adds
~5.5 minutes to the suite; a probe that rots for months costs more.

**Also: `tools/probe_gate.py`**, the pre-flight gate for any number we report, ported here from the research
harness with two checks added after an outside review (jacksonxly): `manipulation()` is two-sided — what
you meant to change changed AND nothing else did, cardinality included, because the patch that once
"confirmed" a hypothesis had collapsed 400 records into 1 while landing exactly as written — and `spread()`
refuses a range from fewer than 20 trials. Measured on our own probe: 5 seeds gave an ungated range of
0.100 where 25 give 0.200, with the mean unmoved. The range only ever approaches the truth from below, so
an under-sampled spread does not look noisy; it looks tight.

Mutation-verified 53/53. 1283 tests pass.

## 1.83.0 - a deletion manifest could be repointed at a different data subject

**SECURITY FIX (evidence integrity) and a BEHAVIOUR CHANGE.** `DeletionManifest.verify()` recomputed
`complete` and `residual_targets` from the entries and walked the entry hash chain. It never read the
HEADER — `subject`, `request_id`, `basis`, `authorized_by`, `targets` were bound to nothing:

```python
manifest["subject"]       = "Bob"
manifest["authorized_by"] = "attacker@evil"
manifest["request_id"]    = "DSAR-999"
manifest["targets"]       = ["none"]
verify(manifest)          # 1.82.0 -> (True, [])   1.83.0 -> (False, ['entry 0: broken chain link'])
```

This is the artifact whose entire job is to be evidence that a NAMED person's data was erased, under a
NAMED authority, on a NAMED request. The entries were faithful; the sentence they were attached to was
anyone's to write.

The chain is now **seeded** with a hash of those five fields rather than given a separate signature, so a
single edit to any of them breaks entry 0's `prev` link — a future verifier that only walks the chain
cannot forget to check them.

`verify()` gains `expected_pubkey=` (without it, each entry is checked against the key stored INSIDE it,
so a rewriter signs with their own and passes) and `legacy_header=` for pre-1.83 manifests. A pre-1.83
manifest is REFUSED by default: "predates the binding" and "was repointed at someone else" look identical
from outside. The flag cannot launder a 1.83 manifest, whose chain is seeded rather than starting at
genesis.

The unpinned-signature warning and the legacy acceptance are **notes**, returned to the caller but not
counted against `ok`. That distinction earned itself immediately: the first version appended the legacy
note to `problems`, and since `ok` is `not problems` the escape hatch changed nothing at all — a flag that
does not do what its name says. Failing on an unpinned signature would likewise have broken every signed
manifest in existence, and a check that cries wolf gets switched off.

Mutation-verified 43/43. 1207 tests pass.

## 1.82.0 - the value the store SERVES is now inside the commitment

**BEHAVIOUR CHANGE (receipt format) and a security fix.** Write receipts committed to text+key, `mtype`
and the canonical sources. Never `object` — the field supersession, the echo guard, `revert()`,
`check_conflict` and `_obj_sig` all treat as authoritative. It is the answer the store gives:

```
remember("retention policy is 90 days", key="policy::retention", object="90d")
# edit rec["object"] to "30d" on disk, nothing else
1.81.0:  verify_writes() -> True     audit-verify --store -> "content checked ... PASS"
1.82.0:  verify_writes() -> False
```

Text and key were untouched, so every hash still matched. The receipts were faithful about everything
except the answer.

`value_sha256` is a NEW commit field rather than `object` folded into `immutable_sha256`: changing that
hash would make every receipt ever written mismatch, so an upgrade would raise a tamper alarm on every
honest store. Pre-1.82 receipts simply lack it and are checked on what they do carry — and they cannot be
stripped of it, because the receipt hash covers the whole commit dict, so deleting a field breaks the
chain link instead. `bind_content` compares it too, or the fix would have survived one call site over.

**Records written before 1.82 are NAMED, not silently exempted.** Applying the new check only where the
field happens to be present would make an unverifiable record read exactly like a verified one — the
defect this whole audit kept finding. So `verify_writes()` reports them on the same terms as the pre-1.68
case: fail closed, explain, offer `value_strict=False`. Only records that actually carry an `object` are
flagged; a record with no value has nothing to protect.

**New: `recommit(ids=[...])`.** The first version of that message told operators to "re-write the record",
and checking it showed that does not work — `slash()` appends a receipt only for a GRADUATED memory, so an
ordinary record had no upgrade path at all. Building the path beat rewording the limitation. Honest scope,
in the docstring and in a test: it binds the record's state AS IT IS NOW and is not a validation of the
past — which is why it takes explicit ids, is not part of any automatic upgrade, and is not exposed to
agents over MCP. Unlike `value_strict=False`, which silences the report, it leaves the decision IN the
chain as a new receipt with a timestamp. Tenant-scoped: it sweeps `_tenant_rows()`, so a tenant view can
never re-commit another tenant's records — the isolation guard refused to let it exist unclassified.

Mutation-verified 38/38. 1192 tests pass.

## 1.81.0 - uncounted is not unchecked

**DATA LOSS FIX (AutoGen adapter) and two BEHAVIOUR CHANGES.** The same audit, the next three findings.
The common error is not a wrong comparison: it is a set that the thing being looked for cannot enter.

**`InspeximusMemory.clear()` erased the whole store, not this memory.** It selected every record with
`status == "active"` and called `forget()` — the one irreversible operation — so a store shared with any
other agent, adapter or the application itself lost all of it on a call whose contract is "clear MY
memory". Measured: 3 active records in, 0 out, 2 of them written by someone else. The LangChain, CrewAI
and OpenAI-Agents adapters all scope theirs; this one held a `source` tag for exactly this purpose and
never used it. Writes now carry an owned tag and `clear()` erases only those (the `source` doc is accepted
too, so records written by an earlier version are still reachable). **If you share a store across
components, upgrade before calling `clear()`.**

**`verify_attribution` returned `ok=True` on a store whose every source label had been rewritten.** It
built its committed set from the receipt chain, so a record with NO receipt could not land in `relabeled`
or in `uncommitted` — it was not unchecked, it was uncounted. Receipts default OFF, so this was the
ordinary case. In the same breath, on the same store, `verify_writes()` answered `False` with exactly the
right words. Unreceipted active records now appear in `uncommitted` (which the docstring already promised),
`ok` requires everything to have been CHECKABLE, and a `problems` list carries the reason.

**`compliance_check` measured receipt coverage as `n_records > n_receipts` — two integers.** A store whose
receipted rows were erased through our own Art.17 path can hold more receipts than records while none of
the survivors is covered by any of them. Coverage is now matched per record id and the violation NAMES the
uncovered records. Honest scope: the audit claimed such a store passed with no violations at all, and that
did **not** reproduce — it was already failing via `integrity_failed`. The coverage check really was blind
to its own subject; it was not the last line of defence.

Also fixed, **and this corrects a number reported yesterday**: `tools/skip_census.py` counted a
`pytest.importorskip` inside a helper function as a module-level guard, because a `def` is a top-level node
whose source text includes its body. Caught by the census's own pin firing about the instrument.

The consequence is that yesterday's figure was inflated. **The base CI job hides 102 tests across 11
modules, not 155 across 16**, and there is no per-interpreter difference — `test_install.py` guards
`tomllib` inside a single test, so it skips one test on 3.9 and always collected. The split pin that chased
that phantom difference is gone. The GAP is real and unchanged: the base job runs ~1001 tests where the
integrations job runs 1144.

Mutation-verified 32/32. 1179 tests pass.

## 1.80.0 - three verifiers that answered "yes" about input they never looked at

**BEHAVIOUR CHANGE, and a safety fix.** An adversarial audit of our own verifiers found one shape
repeated: a verdict that reads as assurance while the check was structurally incapable of failing. All
three reproduce on 1.79.0.

**`verify_claim` returned `supported` for a claim its own evidence contradicted.** With no `object` on
either side -- the state most stores are in, since `object=` is optional on `remember()` -- the match test
was `not (numeric_clash or negation_clash)`. Two different nouns clash neither way:

```
store:  "the patient is allergic to shellfish"
claim:  "the patient is allergic to peanuts"
1.79.0: {'verdict': 'supported', 'matched': {...'allergic to shellfish'}}
1.80.0: {'verdict': 'unverifiable', 'matched': {...'allergic to shellfish'}}
```

"I found nothing that disagrees" was being returned as "the store says so" -- by the gate an agent calls
immediately BEFORE asserting something to a person, with a citation attached. There is a new verdict,
`unverifiable`: a similar record exists, does not refute the claim, and nothing here can confirm it.
**Only `supported` means the store backs the claim.** A genuine restatement is still `supported`, a
fabrication still `unsupported`, a real contradiction still `contradicted`, and the keyed path -- which
has a real value axis -- is unchanged. The keyed path could also be undecidable (a key does not imply a
value) and now says so instead of falling through to `contradicted`, which would have made the store
disagree with a user who was right.

**`scan_residue` reported "clean" while the value sat in `.git`.** Six directories were pruned from the
walk with nothing appended to `skipped`: `ok:True`, exit 0, byte-identical to a genuinely clean scan --
and `.git` is where a deleted store survives longest. The module already applied the right rule to a file
too large to read ("a store is not clean because part of it was not looked at"); a pruned directory is the
same claim at larger scale. `skip_dirs` now REPLACES the default instead of being unioned with it, so
`skip_dirs=set()` can finally search those directories. A root that does not exist no longer returns
`ok:True` with zero files -- a typo in a DSAR runbook was producing a clean bill of health, which is the
erasure-certificate defect of 1.70.0 in a second place. An existing but EMPTY root stays clean, with the
caveat attached: failing it would cry wolf on the ordinary case.

**`bind_content` passed an audit in which nothing was compared.** `checked` counted RECEIPTS, not
comparisons, and `ok` was `not mismatched`. Hand the auditor an empty store -- or re-mint the ids while
rewriting the text -- and every record lands in `orphaned`, zero records are re-hashed, and
`audit-verify --store` printed "content checked ... VERDICT: PASS" and exited 0. `checked` is now the
number actually re-hashed, zero comparisons is a FAIL, and the orphan list says when it has been
truncated instead of showing five of twenty.

Found by a six-lens adversarial audit whose findings were each re-run by a skeptic; every one above was
then reproduced by hand before being fixed. Mutation-verified 28/28.

## 1.79.0 - verify_bundle says what it did NOT check

**BEHAVIOUR CHANGE.** `verify_bundle()` gains `store_items=` and returns a new `limits` list plus
`summary.content_checked`. Passing the store can now turn a PASS into a FAIL. The CLI prints `NOTE` lines
and the verdict line gained `content checked / content NOT checked`. No existing call changes its verdict:
without `store_items` the result is what it always was, now with its scope stated.

The bundle is content-free by design -- it carries hashes, never text -- so checks 1-7 are structurally
blind to what the store serves today. That is defensible. The output was not: an auditor holding a
substituted store ran the documented command, read `VERDICT: PASS`, and nothing said the one question they
came to ask had gone unasked. Measured against every published version: a record edited after export
verified clean.

```
inspeximus.audit_bundle verify bundle.json
#   OK   write chain verifies from genesis: 2 append-only records -> anchor tip
#   NOTE CONTENT NOT CHECKED: this bundle is content-free by design, so a clean chain over
#        substituted text verifies here. Pass store_items= (or call bind_content) to close it.
#   VERDICT: PASS  (2 writes, 0 erasures, content NOT checked)

inspeximus.audit_bundle verify bundle.json --store inspeximus_memory.json
#   FAIL 1 record(s) no longer match the commitment their FIRST receipt made: 966d756909 (immutable_sha256)
#   VERDICT: FAIL  (2 writes, 0 erasures, content checked)     # exit 1, so it gates CI
```

Only `mismatched` fails the verdict. A store that GREW since the bundle was taken, or a record erased
since, is ordinary operation -- a bundle is a snapshot, not a lease -- so those are `NOTE` lines. Calling
them failures would false-alarm on every normal write, which is the same defect as the naive anchor-tip
comparison that looked like detection and fired on benign growth.

`bind_content` (1.74.0) already did the content half. It was a separate function nobody was routed to,
which is why this is a defect in the verdict rather than a missing capability.

## 1.78.0 - the residue check where you can actually reach it: CLI and MCP

A capability nobody can run in three seconds may as well not exist. `erasure_residue` was Python-only in
1.76-1.77; now it is a command and an agent tool.

```
inspeximus residue --root ./deployment --value alice@example.com
#   PLAIN        trace.jsonl                fp=337961f64779
#   LIVE         v.sqlite [t.x x1]          fp=337961f64779
#   ! the value is still held in a LIVE row: the system retained it
#   RESULT: residue found
```

It **exits non-zero when residue is found**, so it drops straight into a CI job or a DSAR runbook as a
gate rather than something a human has to read and interpret.

As an MCP tool it is reachable from any agent: `erasure_residue(root, values)`. Same three verdicts, same
refusal to echo what it was given — findings carry a fingerprint, because a tool that hunts a secret and
then prints it into a chat transcript is itself the leak. There is a test asserting exactly that on the
MCP path.

Our own surface guards did the work again, and this is why they exist: adding the tool failed the MCP
sweep until it was driveable, and the sweep now points it at a real temp directory rather than being told
to skip it. A tool silently skipped is a tool with no coverage at all.

825 tests.

## 1.77.0 - forget(verify_residue_in=...): prove the bytes went, at the only moment you can

1.76.0 shipped a residue check you run yourself. This wires it into erasure, because of a constraint that
is easy to miss and impossible to work around: **after `forget()` the value is gone with the row, so it can
never be searched for afterwards.** A residue check bolted on later has nothing to look for. During the
erasure is the only moment it can run at all.

```python
res = store.forget(ids=[rid], request_id="DSAR-1", verify_residue_in="./deployment")
res["residue"]["ok"]        # False if the value survives anywhere under that root
res["residue"]["findings"]  # [{path, kind: LIVE|UNRECLAIMED|PLAIN, fingerprint}, ...]
```

Opt-in, because it walks a filesystem: an erasure that silently scanned a directory would be a surprise,
and on a large deployment an expensive one. It routes through `forget()`, so `forget_subject()` and
`forget_pii()` get it too.

**The limit, pinned by its own test rather than only documented.** By default the search uses the erased
records' own text, which catches VERBATIM copies — backups, WAL files, a log that logged the whole row.
A FRAGMENT is not matched by the full text: `"Alice contact is alice@example.com"` does not find a backup
containing only `alice@example.com`. Only the caller knows which part was the sensitive one, so name it:

```python
store.forget(ids=[rid], verify_residue_in="./deployment", residue_values=["alice@example.com"])
```

The values are captured into a local, written nowhere, and dropped when the call returns. The result never
carries them — findings hold a 12-char fingerprint — and there is a test asserting the erasure result does
not reintroduce what it just erased.

820 tests.

## 1.76.0 - erasure_residue: did the bytes actually go? (works on ANY store, not just ours)

You called `delete()`. It returned success and the value stopped being served. That is not the same as the
value being gone from disk, and for anyone with an erasure obligation it is the only part that matters.

```
python -m inspeximus.erasure_residue --root ./data --value alice@example.com
```

Point it at any directory — a vector store, a sqlite history, a JSONL trace, another library's data dir —
and it answers for THAT deployment. It reports three outcomes and keeping them apart is the entire point:

- **LIVE** — a SQLite table still holds it in a row. The system retained it.
- **UNRECLAIMED** — in the file's bytes but in no live row. SQLite and most embedded stores do not zero a
  page on delete, so the record is gone logically and the bytes linger until VACUUM or compaction. This is
  a property of the storage engine, **not a vendor defect**.
- **PLAIN** — a JSON/JSONL/log/backup still contains it. Nothing reclaims this on its own.

That distinction is not theoretical. Building this, we ran the same instrument against mem0 2.0.11 with a
local qdrant: after its documented `delete()` and `reset()`, **no live row anywhere held the value** — it
survived only as unreclaimed bytes in the vector store's sqlite. Calling that retention would have been a
false accusation. Its `history()` surface also reported ADD/UPDATE/DELETE faithfully with the old value
recoverable. We went looking for a class and found an honest null, which is why the tool ships as a
measuring instrument rather than an argument.

Turned on ourselves with the same script: after `forget()` the value is gone from every file, and the
erasure certificate's absence proof confirms it. There is a test asserting exactly that, so the claim
cannot outlive the behaviour.

Two properties are tested as hard as the detection:

- **it never echoes the value.** You are searching for a secret; findings carry a 12-char SHA-256
  fingerprint. A tool that hunts for a secret and then prints it into a log or a ticket is itself the leak.
- **a file it could not read spoils the verdict.** Unreadable or oversized files are reported and make
  `ok` False, because "clean" must never mean "we did not look at that part."

Six mutations die, including one that collapses LIVE into UNRECLAIMED and one that lets an empty search
report clean.

815 tests.

## 1.75.0 - explain_growth(): the chain never said the new entries were ones you asked for

A hash chain proves nobody rewrote the PAST. It says nothing about whether what was APPENDED since is
yours — and that is the whole of the post-compromise gap (Schneier & Kelsey, USENIX Security 1998): once
an attacker can write, new entries are attacker-chosen and internally valid. Laundering an edited record
costs exactly ONE extra receipt, and the chain cannot flag it, because from the chain's point of view it
is an ordinary amendment.

The missing piece is a DENOMINATOR, and only the application has it.

```python
a = store.anchor()                       # witness this externally
... your application does its work ...
store.explain_growth(a, writes=2, amendments=0)
# -> {'ok': False, 'actual': {'writes': 2, 'amendments': 1, ...},
#     'unexplained': [{'seq': 3, 'memory_id': 'ee0326ee62', 'kind': 'amendment', 'amends': ['mtype']}]}
```

It itemises the surplus with `seq` and `memory_id`, so an operator gets somewhere to look rather than a
count, and it separates four different failures instead of lumping them: unexplained writes, unexplained
amendments, unaccounted erasures (each leaves a signed tombstone, so they are attributable), and a
SHORTFALL — receipts you expected that are missing, which an append-only chain should not be able to do.
It also re-derives the witnessed prefix, and reports a rewritten past separately from unexpected growth,
because those are different properties and conflating them hides which one broke.

**Honest scope, asserted by its own tests rather than only documented:** it detects unexpected GROWTH. It
is blind to substitution that appends nothing — editing a record without touching the chain is
`bind_content`'s job, and there is a test that pins exactly that division of labour. And a caller who
passes whatever makes it pass has built a gate that cannot fail; the denominator is only worth what the
honesty of the caller is worth.

Together with 1.74.0 this closes the three procedures an adversarial review named as the ones that would
have caught our own laundering defect and that we did not offer: bind content across time
(`bind_content`), reconcile growth against expectation (`explain_growth`), and move the signing key out
of the writing process (`receipt_signer`).

Our own structural guard caught the new method before the tests did: adding a public method fails the
tenant-isolation sweep until it is classified, which is why it exists.

802 tests.

## 1.74.0 - an auditor can now bind a bundle to CONTENT, not just to a chain

Two things an adversarial review of this project's own audit story said were missing, built.

### `bind_content(bundle, store_items)` — the check nobody had

`verify_bundle` proves the chain re-walks from genesis and matches the signed anchor. It proves NOTHING
about what the store now says, because the bundle is content-free by design: it carries hashes, never
text. So an auditor holding a bundle can be shown a clean chain over substituted content — which is
exactly the shape an out-of-band edit followed by a legitimate amendment produces, and exactly how a
public `slash()` cleared this library's own tamper alarm under <=1.67.

`bind_content` closes it without putting content in the bundle. Hand it the bundle AND a store dump; it
re-derives each record's commitment and compares it against the **EARLIEST** receipt covering that record
— deliberately not the latest, because the latest is precisely what an amendment rewrites. It separates
three outcomes rather than lumping them: `mismatched` (content changed under an intact chain),
`unreceipted` (the store grew after the bundle was taken — not tampering), and `orphaned` (the chain
covers a record the store no longer holds — check the tombstone chain, a legitimate erasure leaves one).

Measured: an auditor's bundle taken while everything was honest still passes `verify_bundle` after the
record's text is edited, and `bind_content` against the same store fails, naming the record and the field.

Worth stating plainly: `verify_bundle` catching a *fresh* export of a tampered store is a SELF-REPORT —
`build_bundle` records the store's own verdict at export time, and the module already calls those checks
advisory. It is worth nothing against a store that reads clean, which is what laundering produces. That
is why the content check had to be separate.

### `receipt_signer=` — an opt-in write-authority boundary

The signing key can now live outside the process (KMS, HSM, a signing sidecar): pass a callable
`sign(hash_hex) -> sig_hex` and the store can ASK for a signature but never mint one. A signer that fails
or returns nothing REFUSES the write rather than appending an unsigned receipt, which is the fail-open
this exists to prevent. `receipt_key` and `receipt_signer` together are rejected — holding the key
in-process defeats the boundary.

**Honest scope, because this is where audit logging gets oversold:** it stops an attacker with FILE access
only. It does NOT stop one who can call the API in-process — they ask the signer exactly as the
application does. Separating those is a deployment property, not something a library can assert.

Prior art, cited rather than reinvented: RFC 6962 (a log proves inclusion, never validity) and Schneier &
Kelsey, USENIX Security 1998 (post-compromise entries are attacker-chosen by construction). The
contribution is not the principle — it is that the check is now a function an auditor can run.

794 tests.

## 1.73.0 - a tamper laundered under <=1.67 stayed invisible forever (BEHAVIOUR CHANGE)

**If you ever ran <=1.67.0 with receipts on, `verify_writes()` may now report a problem it previously did
not. Read this before assuming a regression.**

1.68.0 fixed the laundering path: under <=1.67.0 you could edit a stored text out of band and then call
the PUBLIC `slash()`, which appended a receipt committing to the FORGED text, and `verify_writes()` went
False -> True. What that fix did NOT address is what the attack leaves behind, and this audit measured it
end to end with installed packages:

```
1.67.0: after tamper=False   after public slash=True   text now='Revenue is 900M'
1.72.0: opening that store -> verify_writes=True   serves='Revenue is 900M'
        audit bundle from it -> ok=True
```

Nothing in the past is rewritten -- a new, well-formed receipt is appended -- so append-only holds, the
chain stays internally consistent, and **an externally witnessed anchor still re-derives its prefix
intact**. We checked that specifically: the precise test (does the witnessed prefix still re-derive?)
returns "prefix intact" for the laundered store AND for ordinary growth. There was no detection path at
all, and upgrading did not create one, because 1.68-1.72 checked pre-split receipts only against the
LATEST receipt -- which is exactly the forged one.

**What changed.** `verify_writes(..., legacy_strict=True)` -- the new default -- checks pre-1.68 receipts
against EVERY receipt instead of only the latest. It fails CLOSED.

**The cost, stated plainly.** A LEGITIMATE `slash()`/`restore()` performed under <=1.67 produces the same
shape as the attack and is indistinguishable from it on disk. So this can be a false positive, and the
message says so rather than accusing anyone: it names the version range, says the finding may be benign,
tells you to compare the text against a copy you trust, and names `legacy_strict=False` to silence it once
you have. Re-writing a record upgrades its receipt to the split format and removes the ambiguity for good.

Stores written by 1.68.0 or later are unaffected: their receipts carry the split fields and are checked by
the stronger field-wise rule, which never had this hole.

Five mutations die, including a default flipped back to off and a note that accuses instead of scoping --
the wording is part of the fix, because an alarm that overstates gets switched off.

786 tests.

## 1.72.0 - the anchor truncated and the offline bundle would not verify (AUDIT PATH)

**Upgrade if you use `anchor()` or `audit_bundle()` on a store where `slash()` or `restore()` has ever
run.** Those stores currently publish a truncated anchor and export a bundle nobody can verify -- including
the version that wrote it.

1.68.0 put `amends` into a write receipt's hash preimage. It reached `_emit_write_receipt` and
`verify_writes`, and NOT `_chain_core` -- which the offline bundle verifier uses -- nor
`_content_free_writes`, which decides what actually travels in the bundle. Measured after a single
`slash()`, against the installed 1.68.0 and 1.71.0 packages:

```
verify_writes()   -> True          the store says it is fine
verify_bundle()   -> False         "write chain breaks at index 1"
```

`audit_bundle` is what an auditor verifies OFFLINE with no store and no key, so for any store where
standing had ever been revoked, the offline audit path did not work -- not even under the version that
produced the bundle.

**CORRECTION to the first version of this entry.** It also claimed `anchor()` was committing to a
truncated chain ("n_writes = 1 with TWO receipts present"). That was WRONG, and it was my own misreading:
the fixture behind it used a record that never graduated, so `slash()` changed no committed field, no
amendment was emitted, and the store genuinely held ONE receipt. Re-measured against the installed
packages with a fixture that does graduate: `anchor_n_writes=2` with `receipts=2` on 1.68.0, 1.71.0 AND
1.72.0 alike. The anchor was never affected. The bundle defect and the fix are unchanged.

The fix is one fix, not four: `Inspeximus._chain_core` is now THE definition of the preimage, and the
emitter and the verifier both call it. The bundle exporter carries `amends` the way it already carried the
tombstone chain's optional `auth` block.

Nothing changes for a chain that never amended, so existing anchors and bundles keep verifying -- there is
a test for exactly that. Seven tests assert the surfaces AGREE, which is the property that was violated:
each one was internally self-consistent while disagreeing with the others.

Found by auditing the day's own fixes rather than a new area, on the evidence that every defect found today
was created inside the previous fix.

779 tests.

## 1.71.0 - the Claude Code installer destroyed your settings.json (DATA LOSS)

**Upgrade if you used `python -m inspeximus.claude_code --install`.**

`install()` read your `./.claude/settings.json`, and on ANY parse error fell back to `cfg = {}` -- then
wrote that empty dict over the file. A trailing comma, the usual hand-edit mistake, and you lost your
`model`, your `permissions`, and **your own hooks**. From a function whose docstring says "merging, not
clobbering". Measured on a realistic settings file: model gone, permissions gone, the user's own linter
hook gone, ours the only thing left.

`uninstall()` then called bare `json.load` with no guard and RAISED on the same file, so a user whose
settings had been broken could not even undo the install.

Both now refuse and change nothing, printing what is wrong and what to fix. Neither ever overwrites a
config it could not read.

The write is also atomic now (temp file + `os.replace` + fsync): `json.dump(open(p, "w"))` truncates the
target the instant it opens it, so a crash or a full disk mid-write left a half-written settings.json --
and this is not our file.

`inspeximus/claude_code.py` is first-party, needs no optional dependency, and had SEVEN functions with zero
executed body lines (111 of them). Twenty tests now cover install/uninstall merge, idempotence, round-trip,
refusal, atomicity, and the three hook entry points actually storing and recalling. Six mutations of the
repaired logic each fail their own test -- including one that reverts the write to the truncating form,
which needed a test that makes the write FAIL, because "no leftover temp file" passes either way.

Found by measuring coverage rather than trusting the carried figure: 77 of 373 public functions (21%) have
no executed body line, not the recorded 56/318 (18%).

659 tests.

## 1.70.0 - the erasure certificate said "valid" when the absence proof did not run (BEHAVIOUR CHANGE)

**Read this if you verify certificates with `store_path`.** Some certificates that returned `valid: True`
now return `valid: False`. That is the fix, not a regression.

`verify_erasure_certificate` runs four checks. The fourth is the one this product is sold on: given the
store, every erased id is genuinely ABSENT from it -- the "read the raw store" proof that soft-delete
systems fail. Its verdict line read `checks["store_absent"] is not False`, so a check that never RAN
counted as a pass. Measured:

```
correct plaintext path   valid=True   store_absent=True
WRONG/missing path       valid=True   store_absent=None    <- typo the path, get a clean verdict
ENCRYPTED store          valid=True   store_absent=None    <- proof skipped, still "valid"
no store given at all    valid=True   store_absent=None
```

An auditor reads `valid`. A typo in `store_path` silently downgraded the strongest check in the function
to "not performed" while the verdict stayed clean. The explanation was in `problems`, which `valid`
ignored.

The distinction that was missing: NOT asking for the absence proof is honest chain-only verification;
asking and not getting it is not. `valid` now requires `store_absent is True` whenever a store was
supplied (`store_path` or `store_items`), and stays True for a caller who supplied neither. A new problem
line says so explicitly rather than leaving the caller to compare fields.

- **If you pass an encrypted `store_path`**, you now get `valid: False`. Supply decrypted `store_items`,
  or rely on `shred()` (crypto-erasure) for the encrypted case -- the same advice the old problem text
  gave while returning valid.
- The `store_path` branch had NO test, and inverting its encryption-magic check survived the whole suite.
  Seven tests now cover it, and five mutations of the repaired logic each fail their own test -- including
  a check that the proof reads the RAW FILE rather than the live process, which is the reason `store_path`
  exists at all.

619 tests.

## 1.69.0 - the absolute revert path was dead on arrival (BUGFIX)

**Upgrade if you use `restore_now()` or an absolute `submit_revert()` intent.** Both raised
`UnboundLocalError` on every call that had something to do.

`submit_revert`'s ABSOLUTE branch (`restore:key=value#nonce`) ended in
`derived_from=[tgt["id"]]`, but `tgt` is bound only in the RELATIVE branch, which returns before reaching
it. So any absolute restore to an existing, non-current target crashed -- and `restore_now()`, documented
as the "mint + submit in ONE call" liveness primitive written precisely so a caller cannot wedge writes
into the mint->submit window, crashed with it. The store's "maximum bypass of a submitted revert is ZERO"
guarantee was a guarantee about a function that could not be called.

572 tests did not catch it: every existing revert test exercises the relative path. An entire documented
half of a public API had no test that reached its final statement.

The fix resolves the source record explicitly -- id-bound intents already hold it, legacy value-resolved
intents take the most recent record that actually held the value -- so the restore keeps a real lineage
edge instead of dropping provenance to silence the crash. A mutation that drops the edge instead of fixing
it now fails its own test.

Found while writing tests for unrelated mutation survivors.

- 13 tests for the absolute path: land, no-op land on an already-current target, refusal of a value that
  never held the key, nonce consumed on evaluation even when refused, ABA-immunity of an id-bound intent
  against a same-value look-alike, and a relative revert refusing to revive an `echo_blocked` row.
- Five mutations of the repaired logic each fail their own test.
- 585 tests (up from 572).

## 1.68.0 - the accountability lever laundered the tamper (SECURITY)

**Upgrade if you rely on `verify_writes()`.** 1.67.0 (a few hours old) let any caller clear a tamper alarm
through the public API. Found by our own round-nine audit; no report from a user.

**The defect.** 1.67.0 fixed a false positive: `slash()` revokes graduation by rewriting `mtype`, a field the
write receipt commits to, so a legitimate revocation made `verify_writes()` report "edited after write" (it
fired in 27 of 45 random operation sequences). The fix let `slash()`/`restore()` AMEND the receipt chain, and
had verification bind only the LATEST receipt.

But a receipt's commit was ONE hash over text+key+mtype, and `_emit_write_receipt` recomputes it from the
record's CURRENT state. "Only the latest binds" therefore forgave the text too. Measured: edit the stored
text out of band (`verify_writes()` -> False), call `slash()` — no key, no privilege, documented API — and it
returned True with the forged text standing. It also propagated into `erasure_certificate`'s `self_check`,
the document handed to an auditor. (`audit_bundle` is content-free and was never on this path.)

**The fix is structural, not a patch on the instance.** A receipt now DECLARES the fields it legitimately
rewrites (`"amends": ["mtype"]`), and verification forgives exactly what was declared and nothing else. The
declaration is inside the receipt hash — an unhashed authorisation is not one, and without that an attacker
could append `"amends": ["immutable_sha256"]` to an existing receipt and switch the text check off while the
chain still verified. `slash()`/`restore()` are the only call sites and they declare only `mtype`.

The first version of this fix bound text+key on every receipt but still forgave **attribution** on an
amendment — and detecting a later RELABEL is the entire reason attribution is committed. Dropping the
attribution check from `verify_writes()` survived all 541 tests, so the gap was invisible. Both are covered
now; six mutations of the new logic each fail their own test.

- `_write_commit` gains `immutable_sha256` (text+key, bound on every receipt for the life of the record) and
  a plain `mtype`, alongside the existing `content_sha256` and `attrib_sha256`.
- Stores written by <= 1.67 keep verifying under the old whole-commit rule; any new write re-commits with the
  split. A 1.67.0 store that already contains amendments keeps the weaker 1.67 guarantee until then.
- 546 tests (up from 529).

## 1.67.0 - what testing SEQUENCES found, and four defects inside my own recent fixes

Eight rounds tested functions. This one tested **sequences**: 2,700 random operations over a small pool of
keys, subjects and tenants, with eight invariants re-checked after every single one (~21,600 evaluations).
That is a different lens, and it found two things no unit test had.

### `reload()` retired another tenant's value

Its last-write-wins keyed on `key` **alone**. So tenant A's current value was superseded because tenant B
happened to use the same key name — cross-tenant data loss in the *recovery* path, with `verify_writes()`
reporting True throughout. A three-operation random sequence found it:

```
acme.remember(key="auth", object="oauth") ; globex.remember(key="auth", object="saml") ; reload()
acme.recall("auth")  ->  []          # acme's row is now superseded
```

It also demoted **restatements** that `_supersede_by_key` deliberately keeps ("a restatement is not a
supersession"), so `reload()` was not state-preserving where `flush()`+reopen is. Now keyed on
`(tenant, key)` and only across differing values — the store's own rule, not a second one invented beside it.

### `slash()` reported itself as tampering

`slash()` revokes graduation by rewriting `mtype`, which the write receipt commits to — so a legitimate
in-band operation made `verify_writes()` report *"stored content no longer matches its write receipt (edited
after write)"*. It fired in **27 of 45** random sequences, first at operation 3. For a tamper-evidence
product, a false positive raised by its own accountability lever poisons the signal.

`slash()` and `restore()` now **amend the chain** — appending a receipt for the legitimate change, so the log
records when standing was revoked — and only the LATEST receipt's content commitment binds a record. The code
had already called this mutation legitimate in a comment; the follow-through was missing.

**And my first version of that fix broke the chain walk**: it used `continue` to skip a superseded receipt,
which also skipped the `prev = r["hash"]` at the end of the loop body, so every later receipt reported
"broken chain link". A guard that jumps over the loop's own bookkeeping breaks what it sits beside.

### `exact=True` reintroduced the over-erasure it was written to prevent

1.66.0's escape simply cleared `collisions` — but the resolver had only excluded records whose **raw** source
collides. A record derived from the *other* subject carries the shared canonical taint with its own raw
source, so it survived the exclusion and was hard-deleted:

```
exact=True  ->  erased 3   # alice, her summary, AND the other subject's summary
```

`exact` now takes the forward lineage closure of the exact-source records, so it erases what descends from the
subject and nothing that descends from the collider. The test fixture could not see this because the attacker
had no derived record — the same fixture-blindness that hid two earlier fixes' defects.

### An honest unscoped certificate failed its own chain

`request_ids` drops `None`, so a verifier could not distinguish "unscoped" from "scoped to exactly these" —
and an unscoped certificate failed in any store where one erasure ran without a request id (ordinary
housekeeping `forget()`). The producer now emits an explicit `scoped_to` marker; certificates minted before it
still verify.

### Invariants that HELD across 2,700 operations

Worth stating as a result rather than only listing what broke: one active value per `(tenant, key)`;
`recall()` never returning a superseded record; `state_digest()` surviving `flush()`+reopen; tombstone
soundness in both directions; tenant containment for tenant-scoped operations; `erasure_audit` residue iff a
live record is attributable; monotonic receipts with `anchor().n_writes == len(_receipts)`. Capacity eviction,
consolidation, `apply_retention`, `forget_pii` and `forget_subject` all route deletions through the tombstone
chain without a gap.

One framing correction from the run: "at most one active RECORD per (tenant, key)" is false by design — a
repeat keyed write with the same object but different wording leaves two active records deliberately. As "one
active VALUE" it holds.

529 tests pass; 7 mutations, each killed by its own test.

## 1.66.0 - four attacker findings, verified before acting

All four were reported by an adversarial review and carried as UNVERIFIED. I reproduced each one first. All
four hold. Three are inherent to a store with no writer identity, so they get an accurate disclosure and a
test that keeps the disclosure true; the fourth turned one hostile write into a permanent block on a legal
obligation, and that one got code.

Attacker model throughout: someone who can call `remember()` / `credit()` through the normal API — a
compromised agent, a hostile document in a RAG corpus — who does **not** hold `receipt_key`.

### One hostile write blocked every later DSAR — fixed

A single junk write whose source canonicalises onto a victim's (`User_42` vs `user-42`) made
`forget_subject("user-42")` raise `AmbiguousSubject` forever. The only escape, `allow_ambiguous=True`, erases
**both** subjects. So an attacker could deny a legal obligation with one write, and the guard we added to
prevent over-erasure was the mechanism.

`exact=True` now proceeds on the collision-safe subset the resolver had **already computed** — the victim's
records and what inherited from them — leaving the colliding source untouched:

```
default             -> AmbiguousSubject (still right: erasing both is worse)
exact=True          -> erased 2   (the record + its derived summary; attacker junk kept)
allow_ambiguous=True-> erased 3   (blunt, unchanged)
```

A guard that cannot be satisfied is a denial of service with good intentions.

### `credit()` leaves no evidence trail — disclosed

It sets `good`, which the influence gate and corroboration check read, so it decides whether a record can be
served under `recall(influence_only=True)`. Measured after `credit([poison], outcome=True, weight=1e6)`:
receipts `1 -> 1`, `state_digest` unchanged, `verify_writes() -> True`. **The promotion is invisible to every
integrity surface this library sells.** The self-grading risk was already disclaimed; the absence of any
after-the-fact trace was not.

### `derived_from` inherits a source you do not own — disclosed

Taint inheritance is deliberate (a summary must charge its origins), but nothing checks the writer was
entitled to derive from that parent. Measured:

```
evil = remember("Revenue is 900M", source=attacker, derived_from=[audited_record])
_rec_sources(evil)                          -> {'evilexample', 'bigfourauditor'}
spend_irreversible([evil],   1.0, budget=1)  -> allowed
spend_irreversible([audited], 1.0, budget=1) -> DENIED
```

The attacker spends the auditor's irreversible budget. Same root as the unauthenticated write path, and now
documented beside it.

### `capacity=` is not a clean mitigation — SECURITY.md corrected

SECURITY.md told you to set `capacity=` against resource exhaustion. Eviction ranks by `value`, which is
caller-supplied and unbounded, and the two-tier policy protects the top slice **by raw value** — exactly what
an attacker buys. Measured: `capacity=10`, fifty writes at `value=1000.0`, and **5 of 5** victim records at
`value=1.0` were evicted. The recommended mitigation is the weapon. Now stated where the recommendation is
made, with the measured number.

516 tests pass; 4 mutations, each killed by its own test — after two of my mutations first had to be redone,
because they replaced the opening line of a disclosure while the test asserted a phrase further down it.

## 1.65.1 - the mitigation I documented was overstated, and my test could not see it

Caught while verifying 1.65.0 from the published wheel — the run printed `trusted_only: []` where I expected
the true value.

1.65.0 disclosed that supersession is unauthenticated and named `trust_seeds` + `recall(trusted_only=True)`
as the mitigation. That is only half true. The attacker's write still **retires** the honest record, so:

```
recall(trusted_only=True)                          ->  []          # not the truth, nothing
recall(trusted_only=True, include_superseded=True) ->  ["...0xTRUE"]
```

The guarantee is **"you will not be told the attacker's answer"**, not "you will be told the right one". A
store taking writes from an untrusted agent needs an authenticated write path, not just a trusted read path.
The docstring now says exactly that, with the measured output.

**And the test I wrote to prove the mitigation could not see this**: it asserted only *"no 0xEVIL in the
result"*, which passes trivially when the result is empty — and it was. Same weak-assertion shape this series
keeps finding, this time in the test guarding a security claim. It now pins all three facts: the poison is not
served, the truth is not served either, and the truth survives as history.

509 tests pass.

## 1.65.0 - a path regression of mine, and the first attacker-model pass

### `os.PathLike` and `bytes` paths were silently corrupted

1.64.0 added `expanduser` via `str(path)` — and `str()` REPR's an `os.PathLike` into `<object at 0x...>` and
`bytes` into `"b'...'"`. `Path(x)` had honoured `__fspath__` correctly before, so the fix that made the
documented install paths work **broke callers who were doing it right**: the store went to a junk-named file,
or on POSIX to a real one nobody meant. Now `os.fspath`, which raises `TypeError` on a type the library does
not accept rather than inventing a filename.

### The first deliberate attacker-model pass

Seven rounds hunted correctness defects. None had asked what someone who can WRITE to the store — a
compromised agent, a hostile document in a RAG corpus — but does not hold `receipt_key` can actually do. Two
findings, and both are answered by correcting a CLAIM rather than the mechanism, because both limits are
inherent.

**`state_digest` was blind to the two fields that decide which fact wins.** Its docstring said "any
supersession, revert, erasure, or **out-of-band edit** changes the digest". Measured: editing `value`, or
calling `credit()`, leaves the digest identical — and those are exactly what ranking uses, so they decide
which record `recall()` returns first. `verify_witness()` still reports `valid: True` afterwards.

The mechanism cannot simply be widened: `recall()` itself bumps `value` and `last_access`, so a digest
covering them would change on every READ and no witness could ever match anything. The docstring now states
the covered set exactly, names the blind spot, and says why it is a trade rather than an oversight. `witness()`
carries the same note, since that is where a reader looks.

**Supersession is unauthenticated, and never said so.** It branches on tenant, `valid_from`, `object` and
`asserts_change` — never on WHO wrote:

```
remember("Payout wallet is 0xTRUE", key="payout::wallet", object="0xTRUE", source=finance)
remember("Payout wallet is 0xEVIL", key="payout::wallet", object="0xEVIL", source=attacker)
-> recall("payout wallet")  ->  0xEVIL
```

That is ordinary last-write-wins, but the asymmetry is worth stating: `revert()` is capability-gated while the
write path that achieves the same outcome is not. The docstring now says so, names the mitigations
(`trust_seeds` + `recall(trusted_only=True)`, which fails CLOSED with no trust root, and `attestation=`), and
notes that a far-future `valid_from` is the same coin's other side — only finiteness is checked, so an
unbounded future timestamp locks a key against every honest correction.

Tests pin both: the limit itself, that the docstring states it, **and that the named mitigation actually
works** — a disclosed limit whose mitigation does not work is worse than the limit.

Also fixed: `verify_erasure_certificate` filtered `request_id` on truthiness while the producer used
`is not None`, so an honest empty-string `request_id` failed its own certificate.

509 tests pass; 3 mutations killed by their own tests. A fourth — removing the explicit
`if not self.trust_seeds: pool = []` short-circuit — survives and is an **equivalent mutant**: with no seeds
the trusted closure is empty, so the filter branch returns nothing either. Recorded rather than papered over.

## 1.64.0 - the documented install path silently lost everything

Six rounds audited the source. This one audited the **shipped wheel, as a new user meets it** — install from
PyPI into a clean venv, follow the README verbatim, never look at the checkout. Two of the three findings mean
the product forgot everything between sessions on the paths its own documentation tells you to use, with no
error at all.

### No directory was ever created

The plugin advertises `.inspeximus/memory.json`. In a fresh project that folder does not exist, and nothing
created it:

```
remember("My deploy key is ABC123")  ->  {"id": "27d7f73051"}      # looks fine
recall("deploy key")  (same process) ->  ["My deploy key is ABC123"]
recall("deploy key")  (new  process) ->  []
disk                                 ->  nothing written, nothing printed
```

Over MCP there was not even a warning. A memory layer that forgets everything between sessions.

### `~` was never expanded

The README's headline MCP command is `INSPEXIMUS_PATH=~/.inspeximus_memory.json`, and so is the Claude
Desktop / Cursor JSON. A literal `~` is not a directory, so **every documented MCP setup lost its memory on
restart** — and over MCP the failure went to a log nobody reads.

Both are fixed at the same place: the path is `expanduser`'d and its parent created. An *unwritable* parent
still surfaces — a test pins that, so creating the directory cannot turn a real failure into a silent one.

### The 1.63.0 certificate fix rejected honest certificates

Mine, and the sharpest lesson here. `erasure_certificate(request_id=X)` summarises ONE request but ships the
**whole** tombstone chain (deliberately, so the chain re-derives from genesis). 1.63.0 compared the scoped
claim against the unscoped chain, so **any store that had served more than one DSAR failed its own honest
certificate**:

```
cert claims count 1 reqs ['DSAR-ALICE']
VALID: False  -  count says 1 but the tombstone chain holds 2
```

The derivation is now scoped by the certificate's own `request_ids`. Forgery is still caught, including the
sharp case: keep the scope label, swap in the other request's erased ids.

**The test I wrote for that fix could not see it** — its fixture created a single request, the one shape where
scoped and unscoped are identical. A fixture that cannot express the failing shape is not coverage.

### Smaller, all from the shipped artefact

- **The update check pointed at a package that 404s.** `agora-inspeximus` is not on PyPI, so the notice could
  never fire — and if it had, it would have told the user to install a package that does not exist. (The same
  wrong name was in `claims_audit.py`, fixed in 1.54.0; this was its twin.)
- **The MCP handshake reported the SDK's version as its own.** `FastMCP` takes no `version=`, so a client
  asking which inspeximus it was talking to got `1.28.1` — the MCP SDK. Set on the inner server now.
- **The LlamaIndex adapter named the wrong missing package.** Its first import was `pydantic`, which
  llama-index pulls in, so a user without the extra installed pydantic and hit the next missing import. It
  now says `pip install "inspeximus[llamaindex]"`.

500 tests pass; 6 mutations, each killed by its own test.

## 1.63.0 - covering the surfaces that had no tests, and a certificate that could be forged

### The erasure certificate's summary was forgeable

Found on the first call while writing a test for it. `verify_erasure_certificate` echoed `count`,
`erased_memory_ids` and `request_ids` straight from the certificate and never re-derived them from the
tombstones — which ARE hash-chained and signed:

```
forged count             -> valid=True
forged erased_memory_ids -> valid=True     # ids that never existed in the store
forged request_ids       -> valid=True
```

An operator could hand an auditor a certificate claiming to have erased records that never existed, and the
independent verifier said `valid: True` with no problems. That is the DeletionManifest defect fixed in 1.59.0,
one artifact over — on the thing literally called a certificate. The summary is now re-derived from the
tombstone chain and absence is checked against the CHAIN, not against the claim.

**And the first cut of the fix did not work.** It appended the finding to `problems`, but `valid` is computed
from `checks` alone, so a forged count still verified. A check absent from the verdict expression is
decorative.

### Coverage: 132 of 318 public functions had zero executed body lines

Measured with `coverage`, counting only BODY lines — the `def` line executes at import, so including it makes
every function in the package look covered (my first measurement said 2% uncovered instead of 42%).

The uncovered set was not obscure. It was `verify_erasure_certificate` (62 lines), `submit_revert` (58),
`erasure_certificate` (22), `witness` / `verify_witness`, the entire revert capability chain — and 58 of the
MCP tools, the surface most users actually touch.

Two new files, 21 tests: the crypto and capability surface (witness round-trip and its rejection after the
state moves, honest and forged certificates, challenge/intent binding, capability minting refused without an
authority, replay, `sign_revert` verified against a real Ed25519 public key), and an MCP sweep that calls
**every** tool and fails if one cannot be driven or is not named in an exemption list with a reason.

**Zero-body-coverage public functions: 132/318 (42%) → 56/318 (18%).**

### Tests that ran in no environment at all

`tests/test_haystack.py` skips locally (haystack-ai is optional) and its CI job ran a *different* file, so all
9 of its tests — including `test_delete_removes_the_value_from_disk` — executed **nowhere**. Now wired in,
plus a new `optional-adapters` job installing crewai and llama-index-core so those permanent skips un-skip
somewhere. A skip that is never un-skipped is an untested path with a green tick next to it.

### On the mutation score, honestly

A fresh run over a **code-only** mutant population (docstrings and comments excluded via `tokenize`, since
mutating prose produces dead mutants that always survive and deflate the number) gives **36.0% — 36 of 100,
seed 1337, clean tree**. That is **not** comparable to the 32.8% quoted at 1.62.0: different population,
different operators, different sample. Treat 36.0% as the new baseline and compare future runs to it.

What *is* comparable is the coverage figure above, measured identically before and after, and the five
specific survivors from the previous round, each re-applied and each now killed.

**The harness itself failed open, and that is the finding worth keeping.** An earlier run reported **92.5%**
— because a previous overlapping run had left three files mutated, so the suite was *already red* and every
mutant looked killed. A measurement instrument that reports success when it is broken before the measurement
is exactly the defect class this whole series has been about. The harness now refuses to start unless the
suite is green.

487 tests pass.

## 1.62.0 - three regressions from the last two releases, and what a mutation run said about the tests

### The 1.60.0 erasure fix turned under-erasure into OVER-erasure

Giving every adapter write a `source=` made those records erasable — and two of the subjects were **not
namespaced**, so they collided with real ones. Measured: a Haystack document whose id happened to be
`user_42`, and a CrewAI store whose default tag is the bare word `crewai`:

```
forget_subject("user_42")  ->  erased 2   # the person's record AND the corpus document
forget_subject("crewai")   ->  erased 1   # a user's own note that used that word
```

Before 1.60.0 a DSAR matched nothing; after it, a DSAR matched too much and **hard-deleted a third party** —
which is the worse of the two failures. Every adapter subject is now prefixed (`haystack::`, `crewai::`,
`lc::`, `lg::`, `llamaindex::`, `mab::`).

### A read failure was reported as a persistence failure

1.61.0 made an unreadable `.irrev.json` fail closed, correctly — but recorded it in `_sidecar_errors`, which
`flush()` raises on. So a store that had persisted perfectly reported `OSError: could not persist`, and under
1.60.0's CLI check that is `exit 3 NOT PERSISTED` with every write safely on disk. Read failures now have
their own channel: `verify_writes()` reports them, `flush()` does not raise on them.

### A reader needed write access

1.60.0's `_flush_or_fail` ran on every command, and `recall` bumps `last_access` — so on a read-only store
`inspeximus recall` printed the right answer and then exited 3. Read commands now warn on stderr and exit 0;
write commands still exit 3.

### The mutation run, and what it says

A systematic pass over **400 single-point mutants** killed 131 — a **32.8% mutation score**, and **54.9%** on
lines the suite executes at all (165 mutants sat on lines it never reaches). The survivors clustered on the
predicates this product is sold on: `verify_attribution`'s source-hash comparison could be **inverted** with
the suite green; three of `verify_bundle`'s tamper checks could have `or` changed to `and`; the MCP echo-guard
default could be flipped to **off**; and tenant-scope comparisons in `contradictions`, `resolve_reopened` and
`_stale_by_value` could be inverted. Thirteen new tests target those specific mutants, each naming the
mutation in its docstring, and all five re-checked survivors now die.

**Three of our own tests could not fail**, and are fixed:

- `test_resolve_reopened_declares_the_reopened_record` — two plain writes never reopen anything, so the
  early-return branch was the one that ran, and it asserted a **regex over `core.py` as a string**. The live
  branch was dead *and* broken: it read a key `resolve_reopened` has never returned.
- `assert not hasattr(m.forget_subject, "dry_run")` — an attribute lookup on a bound method, so it could
  never fail, and it pinned a limitation **1.53.0 had already removed**.
- `assert A and B if False else C` — the `if False` made the whole first clause dead.

And a test that asserted a **spelling**: "the CLI and the MCP agree on the echo guard" grepped both files for
the env-var name, which is exactly why flipping the default survived. It now imports the module and reads the
resulting value.

466 tests pass. The honest number to carry forward is not that one — it is the mutation score, and 32.8% is
the real measure of what this suite currently proves.

## 1.61.0 - three controls that failed OPEN

The last of round four's sweep. All three share a shape worse than a crash: each **silently granted what it
exists to withhold**, and its presence is what stopped anyone looking.

### The value signature erased whole writing systems

`_obj_sig` normalised with `[^a-z0-9]`, which deletes every non-Latin character — so `東京` and `北京` both
became the **empty string** and compared equal. `observe()` therefore recorded a flat contradiction as
**agreement** and marked its support seen, so later corrections were discounted. Any store keeping values in
Chinese, Japanese, Korean, Cyrillic, Greek, Hebrew, Arabic or Thai had one signature for all of them.

Now Unicode-aware: letters and digits of any script survive, only punctuation and spacing fold (`3-2` and
`3/2` still match, which was the point). A second layer falls back to the raw value if normalisation would
still leave nothing, so no value can ever share the empty signature.

### The lifetime irreversible budget reset itself on a corrupt file

Its own docstring says *"a patient attacker must not reset its spent budget by spanning sessions."* A corrupt
`.irrev.json` was swallowed and the state reset to `{}`, so a 0.9 spend against a 1.0 budget was allowed a
**second** time — cumulative 1.8 — and nothing reported it. Corrupting one file *was* the reset. It now fails
CLOSED: an unreadable balance refuses the spend rather than authorising against an unknown one. A **missing**
file is still fine — an empty budget is the correct starting state.

### An anchor that looked witnessed

`anchor(sign=…)` swallowed a raising signer and returned a dict byte-identical to `anchor()` with no signer at
all. External witnessing is the **only** operator-adversarial property in the whole design, and the caller who
asked for it could not tell it had not happened. It now raises.

453 tests pass. Mutation note, recorded because it nearly misled me: reverting *either* signature layer alone
leaves the tests green — the Unicode regex and the empty-signature fallback each cover the other. Only
reverting both together kills them, which is what defence in depth is supposed to look like. A single
mutation reporting "no teeth" is not proof that a test is toothless.

## 1.60.0 - the surfaces, brought in line with the library

Four rounds hardened `core.py`. Everything here is one step out from it, and it shares a shape: **the library
was correct and the thing the user actually touches was not.** A guarantee that holds in `Inspeximus` and
fails in the CLI, the MCP tool or an adapter is not a guarantee, it is a footnote.

### The two product surfaces disagreed on a flagship property

`cli.py` and `mcp_server.py` are documented as sharing one store, and the echo guard was **on** in the MCP and
**off** in the CLI (the library's legacy default). So one CLI write could resurrect a value the MCP had
retired — undoing the measured 0.00 → 1.00 echo-resistance on the very store that advertises it. Both now read
`INSPEXIMUS_ECHO_GUARD` with the same default; the library keeps its legacy default for compatibility.

### A "clear" that left the data on disk

`InspeximusChatMessageHistory.clear()` and `InspeximusStorage.reset()` (CrewAI) set `status="deleted"` on the
in-memory records and stopped: no `forget()`, no tombstone, no save. `.messages` filters by TAG and never
looked at status, so the history still returned them, and a reload brought every record back **active with the
content still in the file**. Both now erase through `forget()`, which persists and tombstones.

### Records nothing could erase

Seven adapters wrote without `source=`, so every record fell back to `id:<record id>` and **no subject erasure
could ever reach it** — the governance surface the integrations advertise did not apply to anything they
themselves stored. All write sites now carry a subject: the storage tag (CrewAI), the session (LangChain,
LlamaIndex), the namespace and thread (LangGraph), the document id (Haystack), an explicit argument
(pydantic-ai).

A per-user DSAR through LangGraph now works: `forget_subject("lg::users::u1")` erases u1 and leaves u2.

**The trap that cost a round-trip:** the first version joined the namespace with `/`, and `_canon_source`
keeps only the host of a path-shaped id — so `users/u1` and `users/u2` both resolved to `users` and every
per-user erasure was refused as ambiguous by our own guard. **A path-shaped subject is a collision by
construction.** The separator is `::`, which survives canonicalisation.

### Smaller

- The MCP `forget_subject` and `forget_pii` tools can now record a **`request_id`**. Without it the erasure
  report keyed everything under `None`, `governance_report()["by_request"]` was `{None: {...}}` and
  `erasure_certificate(request_id=…)` could never be scoped — on tools whose docstrings sell them as Art.17
  evidence.
- `inspeximus remember --source` exists. `forget-subject`'s own help had pointed at it for a release while
  `remember` had no such flag, so nothing written from the CLI could ever be erased by subject.

442 tests pass; 5 mutations, each killed by its own test.

## 1.59.0 - round four: three regressions from 1.58.0, and three verdicts that signed an untruth

### The concurrency guard had the shape of the bug it replaced

`_file_sig = None` meant BOTH "this store has no path" and "the file is not there yet", and `_save` skipped
the guard on `None`. So two handles opening a store that does not exist — **two workers starting together, the
commonest concurrency case there is** — both had an ungated first write, and the second silently replaced the
first. Absent is now a distinct sentinel.

### The recovery path broke the property the store exists for

`reload()` unions by id, and the disk copy of a record this handle had SUPERSEDED came back active:

```
after reload -> [('salary is 100','active'), ('city is Rome','active'), ('salary is 200','active')]
verify_writes -> True
```

Two contradictory active values under one key, certified healthy. `reload()` now re-applies the store's own
last-write-wins rule per key, and no longer resurrects a record this handle **tombstoned** — the first version
of that filter only covered the re-added side, so a deliberate erasure was undone by its own recovery path
while the tombstone still claimed it had happened.

Load-time normalisation also used `time.time()` for a missing `ts`, so `state_digest()` differed across two
opens of **identical bytes** and a witness or anchor pinned to such a store could never re-verify. An undated
legacy record is now honestly undated.

### Three verdicts that signed an untruth

- **`DeletionManifest.verify` returned `(True, [])` on a forged manifest.** `complete`, `residual_targets`,
  `subject` and `authorized_by` sat OUTSIDE the hash chain, so flipping `complete` to True and emptying the
  residuals verified clean — on the one artifact whose entire job is to be evidence. An empty manifest
  verified clean too. The verdict is now re-derived from the entries.
- **`ErasureAuditor.audit()` with no registered probes returned `erasure_verified: True`**, and
  `compliance_receipt()` signs that. `DeletionManifest.execute` had guarded this exact case for years;
  `audit()` never did.
- **The CLI printed `remembered <id>` and exited 0 on a store that never reached disk.** A typo'd `--path` or
  `INSPEXIMUS_PATH` silently discarded every write for the whole session, while the library had recorded the
  failure the whole time. Every command now confirms persistence and exits 3 if it failed.

### Three more silent wrongs

- **`forget(where=...)` swallowed a raising predicate** and reported the success shape of a complete sweep:
  2 forgotten, and the record the predicate choked on left behind. On a deletion path a partial sweep must
  never look complete — it now aborts without deleting.
- **`source={"who": ...}` was accepted and silently un-attributed.** `_rec_sources` reads only `doc`, so the
  record fell back to `id:<record id>`: provenance gone, `slash(scope='source')` matching nothing, and
  `verify_attribution` reporting ok on a relabel. A source dict without `doc` is now refused.
- **`route()` matched a key inside a longer word and executed on it.** "go back to the earlier **heart**
  condition" reverted the key `art` — unconfirmed, because a default store has no revert authority
  configured. Now word-bounded, the same rule the rest of the file uses for values.

433 tests pass; 10 mutations, each killed by its own test.

## 1.58.0 - the known-and-unfixed list, cleared

Every item below was reported, reproduced and carried in the handoff as **known and unfixed** for three
releases, because each needed a change larger than the release it was found in. Naming them beat quietly
dropping them; shipping them beats naming them.

### Cross-process data loss — the largest, and the worst-shaped

The store is one JSON file written whole and read **once** at open, with no lock and no re-check. A second
handle therefore won by writing last:

```
A.remember("base");  A.flush()
B.remember("B-only"); B.flush()      # second handle, loaded before A's next write
A.remember("A-only"); A.flush()
on disk -> ['base', 'A-only']        # B's committed, flushed record is GONE
A.verify_writes() -> True            # and both sides still certify clean
```

That is the shipped default, not an exotic setup: `mcp_server.py` and `cli.py` both resolve
`$INSPEXIMUS_PATH`, so a long-running MCP server plus one CLI invocation is the ordinary case. Losing data and
then certifying it is the worst thing a store whose pitch is integrity can do.

inspeximus is a **single-writer** store, and that is now enforced rather than assumed. Each handle records the
file's `(mtime_ns, size)` when it loads and after each of its own saves; if the file changed underneath, the
save raises **`StoreChangedOnDisk`** instead of overwriting. `reload()` is the recovery path: it re-reads the
file and re-adds this handle's records by id, so **neither writer loses a write**. A single writer never sees
a false conflict — its own saves refresh the fingerprint.

Receipt and tombstone sidecars are now written atomically (temp + `os.replace`) too; they were plain writes, so
a crash mid-write could leave the *evidence* file truncated while the store itself was fine.

### The store file stays valid JSON

`json.dumps` ran without `allow_nan=False`, so `remember(..., value=float('nan'))` wrote a bare `NaN` literal.
Python re-reads it; **jq, JS and serde do not** — the file silently stopped being JSON for the audit bundle and
every non-Python consumer, while `state_digest` and `verify_writes` both reported healthy. `inf` also sorted
first in every recall forever, and `nan` never compared true so the record sank without trace. Non-finite
`value`/`valid_from` are now refused at the write, and the serializer refuses as a second layer.

### Foreign and older records no longer crash — or miscount

A record missing `status` raised a bare `KeyError` inside six methods, and made `index_coherence` report
`coherent: true` with an **undercount** — a wrong answer, which is worse than a crash. Records are normalised
once at load (absent keys only; nothing present is touched), so hand-edited, foreign and pre-upgrade stores
work.

### The erasure path is reachable from every surface

The library has had subject erasure since 1.0 and **the CLI never exposed it**, so the one operation a DSAR
actually needs was unreachable from a terminal. `inspeximus forget-subject <source>` now exists, with
`--dry-run` (direct vs inherited, plus which other subjects go with it) and `--allow-ambiguous`. And
`forget_pii` on MCP, `google_adk.forget_subject_for` and `openai_agents.forget_subject` gained the
`allow_ambiguous` escape hatch the guard needs — without it, a legitimate erasure was unreachable there too.

420 tests pass; 6 mutations, each killed by its own test.

## 1.57.0 - round three, including two regressions the previous fix introduced

The pattern held a third time, and this round I was the source of half of it. **A fix is new code and carries
the same defect rate as any other**; the round that audits the fix is not optional.

### Two regressions from the 1.56.0 tenant work

- **`view.items.append(rec)` planted a phantom record.** The scoped view was cached as a list and returned as
  the object itself, so a write into it did not merely fail to persist — every later reader saw the record,
  including freshly created handles, `recall` ranked it **first**, it was never on disk, and it vanished on
  the next write. A bound store now returns an immutable **tuple**: the mistake raises instead of haunting.
- **`get()` was rebound onto the tenant view and `Inspeximus.get` does not exist**, so every tenant-scoped
  `get()` raised `AttributeError`. `neighbors` too — both are MCP-level tools, not core methods. A test now
  fails if the view rebinds anything that is not there.

### An unreadable store no longer destroys itself

A truncated or corrupt plaintext store loaded as `[]` and the **very next save wrote that empty list over
it**: 5 records in, 0 loaded, 1 on disk afterwards. The encrypted branch had always raised here; the plaintext
one silently destroyed the file. It now refuses to open and leaves the file untouched. Receipts would have
caught it — but they are off by default, so the default path was a silent total wipe.

### The collision class, on the levers round two missed

`monitor()`, `spend_irreversible()` and `restore(scope='source')` all expand from the records you name to
every record sharing their **canonical** source. Measured: 20 bad outcomes on Alice left Bob one call from an
alarm he never earned; Alice's spend left Bob `allowed: False` on his own lifetime budget; and restoring Alice
**cleared a slash Bob had earned on his own catch** — the worst of the three, because it re-admits a source
that was correctly forfeited. All three now refuse, with `allow_ambiguous=True` to proceed deliberately, and
genuine sybil expansion still works.

### Verifiers

- **`compliance_check` did not see partial receipt coverage.** `verify_bundle` got that check in 1.54.0; its
  sibling gate never did, so 5 unreceipted + 1 receipted records reported `ok: True, violations: []`.
- **The forgeable checks are now labelled.** `bundle_hash` is an **unkeyed** SHA-256, so an exporter can set
  `governance.proof.verified` to True or `n_records` to `len(write_chain)` and recompute it in three lines —
  both demonstrated against our own 1.54/1.55 checks. Those checks stay, because the accidental case (a
  good-faith export from a misconfigured store) is the common one, but they are documented as **ADVISORY**:
  they prove nothing against a determined operator. Only the witness co-signature is operator-adversarial.

### The last silent writes

The `.cusum.json` and `.irrev.json` sidecars still swallowed write failures. Both promise **cross-session**
state in their own docstrings — the poison detector and the lifetime irreversible budget — and both lost it
without a word, so a restart reset the cap the budget exists to enforce. Now surfaced by `verify_writes()` and
raised by `flush()`.

403 tests pass; 5 mutations, each killed by its own test.

## 1.56.0 - tenant isolation, structurally

1.54.0 and 1.55.0 patched tenant leaks one method at a time and each round found more, because the shape was
wrong: `self.items` was a plain attribute holding **every** tenant's records, and **46 methods read it
directly**. Isolation therefore depended on each of them remembering to filter, and a method added tomorrow
would read the shared list again, silently. 1.55.0 shipped with that stated as a known limitation.

**`items` is now a tenant-scoped property over the real list.** The records live in `_items`; `items` returns
only the bound tenant's rows (cached per tenant+revision). Scoping moved *under* the reads instead of sitting
beside them, so a method is isolated **by construction** rather than by review. Only four call sites touch the
real list — load, append, forget, shred — and those are what a reviewer should look at. The setter refuses a
whole-list assignment, because `self.items = [...]` under a scoped read would replace every tenant's records
with this tenant's survivors, which is exactly how `forget()` used to work.

Measured after the change, from a tenant handle aimed at the other tenant's data: `history`, `provenance`,
`as_of`, `why_recalled`, `revert`, `recall`, `contradictions`, `graph`, `memory_report`, `value_by_cohort`,
`check_conflict`, `verify_claim`, `route`, `convergence_report`, `grade`, `consolidate` and **`items` itself**
— no leak. The destructive paths are scoped by the same change: `apply_retention`, `sleep`, `forget`,
`forget_pii`, `retract_lineage` and `consolidate` no longer reach another tenant's records, and the
integration adapters (`langgraph`, `autogen`, `haystack`, `google_adk`, `code_guard`) that read `store.items`
became correct without being touched.

**`shred()` now refuses from a tenant view.** It destroys the encryption key for the whole file; dropping one
tenant's rows while making every other tenant's data unrecoverable is not a coherent operation, and not one a
tenant should be able to perform on the others.

### The test that keeps this true as the API grows

A sweep calls **every** public method from a tenant handle with arguments aimed at the other tenant and fails
if their secret appears anywhere in the output. It runs over **both** ways to bind a tenant, because they are
protected differently: `for_tenant()` returns a view whose default-deny `__getattr__` refuses an unclassified
method, while `Inspeximus(path=..., tenant=...)` has no view at all and relies solely on the scoped property.
A method that the sweep cannot drive must be named in `_UNSWEEPABLE` **with a reason** — the first version of
this test skipped such methods on `TypeError`, so a newly added leaking method was invisible to it. Verified
by injecting one: a new method reading `items` passes; the same method reading `_items` fails the sweep.

### Honest scope, unchanged

This is isolation between *your own* workloads on one store. It is not a substitute for separate stores
between mutually distrusting parties: the file, the receipt chain, the anchor and the encryption key remain
shared, and anything holding `_items` holds everything.

391 tests pass; 6 mutations, each killed by its own test.

## 1.55.0 - round two: the same audit, run again after the fixes

We re-ran the audit on the fixed code. Every 1.54.0 fix held **at the instance it was reported** and the
**class survived in every case**. That is the finding worth keeping; the individual defects below are its
evidence.

**The tenant guard's own allow-list contained destructive methods.** 1.54.0 made `_TenantView.__getattr__`
default-deny, then listed `apply_retention`, `shred`, `sleep`, `grade` and `erasure_certificate` as
"store-level" passthroughs. They iterate the whole store, so from a tenant view they still reached every
tenant. All are now tenant-bound, and a test fails if a destructive method reappears in that list — which it
caught immediately: `sleep` was still there after the first pass.

**A method that WAS rebound still leaked.** `revert()` scanned the shared list for its key, so
`A.revert(B_key)` returned B's plaintext and wrote a copy of it into A. Rebinding a method does not scope it;
the body has to use `_tenant_rows()`.

**The collision guard covered erasure but not the standing levers.** Caught on `crm.example.com/alice`,
`slash(scope='source')` forfeited `crm.example.com/bob` too — `slashed: 2`, Bob's standing inverted to bad.
Now guarded by `_source_expansion_collisions`, with `allow_ambiguous=True` to proceed deliberately, and a
test proving genuine sybil expansion still works.

**The persistence fix covered the store file, not the evidence.** The receipt and tombstone sidecars still
had `except Exception: pass`. Measured: 4 receipts in memory, `verify_writes() -> (True, [])`, **zero on
reload**; and an erasure whose certificate said verified while a reload showed `erasures_total: 0` — the
deletion record a DSAR response rests on, gone without a word. Both now surface and make `flush()` raise.
They also needed their **own** error slot: the first version shared one with the store save, so the next
successful write erased the record that the chain had never been persisted.

**Two verifiers still passed vacuously.** `verify_bundle`'s empty-chain check only fired when *nothing* was
receipted — five records written with receipts off, reopened with them on, one more written: 6 records,
1 receipt, bundle clean, and forging one of the five changed nothing. And `governance_report` reported
`proof.verified: True` on a store with no receipts at all, while its sibling surface refused the same store.

**A guard with no escape hatch is its own defect.** `AmbiguousSubject` made a legitimate GDPR erasure
*unreachable* through the MCP tool, which had no `allow_ambiguous`. Added, with the message telling the agent
what to do when it fires.

### Known limitation, stated rather than implied

Tenant isolation is **not complete**. 46 methods still read the shared `self.items` list directly; many are
legitimately store-wide (the receipt chain, the vector cache), but the boundary has not been verified
method-by-method, and the correct fix is structural rather than another round of patches. What is now true:
the confirmed leaks are closed, destructive methods are tenant-bound, and an **unclassified** method raises
instead of silently running as admin. Treat multi-tenancy as a soft boundary for isolating *your own*
workloads, not as a security boundary between mutually distrusting parties.

379 tests pass; 7 mutations, each killed by its own test — including one that revealed a test covering only
half the branch it claimed.

## 1.54.0 - what a full codebase audit found, and it was not flattering

After 1.53.0 we audited the whole package for the defect CLASS we had just fixed, rather than for the defect.
Every finding below has the same shape as the one that prompted the search: **an instrument reported safe
while the guarantee it was measuring was broken.** All were reproduced before being fixed, and each fix is
pinned by a test that a mutation kills.

### Tenant isolation was enforced in 25 of 79 methods

`_TenantView` rebound the tenant-aware methods and **forwarded everything else** to the parent store, which
runs unbound — i.e. as admin. `recall()` honoured the boundary. These did not:

```
A = store.for_tenant("acme"); B = store.for_tenant("globex")
A.recall("api key")            ->  []                                     # isolation holds
A.history("globex api key")    ->  ['... the globex api key is sk-globex-999']
A.provenance("globex api key") ->  found=True, full text
```

`as_of()` and `why_recalled()` did the same; `beta.forget([acme_id])` returned `{'forgotten': 1}` and the row
was gone; `credit()` wrote across the boundary; `memory_report`, `value_by_cohort`, `index_coherence`,
`supersession_report`, `erasure_report` and `governance_report` counted and attributed other tenants, and two
tenants produced an **identical `state_digest`**.

Fixed at both levels. 18 read sites across 12 methods now resolve through `_tenant_rows()`; `forget()` scopes
its SELECTION before deleting from the shared list; 32 more methods are rebound. And `__getattr__` is now
**default-deny** — an unclassified public method raises instead of silently running as admin, so a method
added tomorrow cannot leak by default. A test fails the moment a public method is added without classifying
it. *(The class docstring had asked for this in words since the beginning: "Any new tenant-aware method
belongs in this list." A note is not a mechanism.)*

### A failed save was reported as a successful one

`_save()` swallowed every exception AND left `_dirty` False — so `flush()` became a no-op and every later
write was lost too, while `verify_writes()` returned True:

```
in-memory 5 | verify_writes -> True
RELOADED from disk -> ['fact 0']
```

Now the failure is recorded, retried on the next save, reported by `verify_writes()`, and **raised by
`flush()`** — the call whose entire purpose is "make sure it is on disk". `remember()` also rejects an
unserialisable `meta` at the write that carries it: the damage was never the bad record, it was every good
record written after it.

### The 1.53.0 collision guard covered one of five paths

`forget_pii(subject=...)` erased the colliding subject (`erased 2`, Bob gone), `retract_lineage` demoted him,
and `rederive` **rewrote his text and re-emitted it**. All subject resolution now goes through one
`_resolve_subject()` that carries the guard, and a test caps how many sites may inline it — because the
regression happened precisely because five call sites each had their own copy.

### The auditor-facing verifier passed a bundle carrying its own failure

`build_bundle()` wrote `governance.proof.verified` and `verify_bundle()` never read it, so a bundle exported
from a store whose records had been edited out of band verified **PASS with zero problems**. A
receipts-disabled store also passed, with `writes: 0`. Both now fail — and an *empty* store is reported as
empty rather than failed, so the finding stays meaningful.

### Smaller, same theme

- **`remember()` returned the id of a record it had just evicted.** At `capacity=3`, a fourth low-value write
  returned an id that was not in the store and could not be recalled. The new record is now exempt from its
  own write's eviction pass (a later write can still evict it); the capacity bound still holds exactly.
- **`verify_claim` verdicted `supported` for a retired value** whenever the store had not recorded `object=`
  (which is optional, so most stores) — even when the caller passed `object=` explicitly. The caller's value
  is now used as the discriminator against the record's text.
- **A rewriter that RAISED was folded into `skipped`**, so a caller read "paraphrased, nothing to do" when
  their LLM was down. `rederive` now returns a separate `failed` list.
- **An extractor that raised silently disabled keying and supersession** for that write — and the store then
  looked exactly like one that was never keyed. Counted and surfaced by `verify_writes()`.
- **`selection_integrity` reported `stable=True` with the whole top-k untrusted** when `trust_seeds` held a
  literal source string instead of its canonical form. Empty seeds already failed closed; wrong seeds failed
  open, which is the more dangerous half. Now `None` with a note naming the canonical forms it looked for.
- **Documented return keys vanished on early-return paths** (`forget` without `tombstones`, `rederive`
  without `new_value`) — a `KeyError` exactly when the answer was zero.
- **A docstring still offered `'key:<hex>'`** on `_canon_of`, the same false claim fixed for `forget_subject`
  in 1.53.0. No caller has ever seen that prefix.

### README, honestly

The README claimed *"every number in this README traces to a runnable probe"*. The audit found one that does
not: the harness behind the LOCOMO **retrieval-recall@25 0.78 / 0.65** pair is not in the repository, and the
file the README named does not exist. The numbers are now marked **reported, not independently reproducible
from this repo**, and the "every number" line says "almost every", with the exception flagged in place. Also
fixed: probe paths pointed at `probes/` (2 files) instead of `probes/`; `claims_audit.py`
pip-downloaded a package name that is not on PyPI; the version line said 1.48.0; `recall()` examples showed
strings when it returns dicts; and "zero dependencies — one file" is now "zero required dependencies —
pure-Python package" (15 modules).

368 tests pass. Nothing here was found by a user or a regulator; it was found by looking for one defect and
refusing to stop at the first instance.

## 1.53.0 - a DSAR for one person no longer erases another, and forget_subject can be previewed

The preview was the intended work. Reviewing it found a **data-loss defect in the erasure path itself**, which
is the headline.

**`forget_subject` merged two people.** `_canon_source` keeps only the host — it exists to collapse sybil
variants of one *publisher* ('Wikipedia', 'wikipedia.org', 'https://www.wikipedia.org/wiki/X') into one
attribution key, and for that it is right. As an erasure selector it is not. Measured before the fix, on a
store holding three people:

```
forget_subject("crm.example.com/alice")   ->  erased 2
survivors                                 ->  ['Carol Kiss, unrelated']
```

Bob's record was gone. `crm.example.com/alice` and `crm.example.com/bob` both canonicalize to `crmexample`,
and the new preview's `also_carrying` field reported `{}` — no collateral — because as far as the selector
could tell, Bob *was* the request. A compliant operator doing the right thing would have read that as "two
records, all Alice's, no entanglement" and committed an irreversible delete of a third party.

Now an erasure whose subject was written **exactly** as given, but whose canonical key is shared by a
different raw source in the store, raises `AmbiguousSubject` (a `ValueError` subclass) naming the colliding
sources, and deletes nothing. Pass `allow_ambiguous=True` to proceed deliberately. Detection is narrow on
purpose, so what canonicalization is *for* still works: writing `user-42` and erasing `User 42` has no exact
raw match and still resolves.

**The limit that cannot be fixed here:** `taint` stores already-canonical keys, so a record that merely
*inherited* from Alice is indistinguishable from one that inherited from Bob. Colliding subjects cannot be
separated in the derived tier without rewriting taint. This catches the direct case and refuses rather than
guessing.

**`forget_subject(subject, dry_run=True)`** returns what the erasure would remove, without writing:

- `would_erase` — active records that would be hard-deleted
- `direct` — records naming `subject` as their own canonical source
- `inherited` — records reached only through `derived_from` taint
- `sample` — up to five of each, with id, key, a text snippet and the record's taint
- `also_carrying` — other source ids present on those same records, with counts
- `ambiguous_with` / `excluded_by_ambiguity` — present only when the collision above applies

It mutates nothing: no delete, no tombstone, no manifest cascade, no save, and it is tenant-scoped exactly
like the real call. `dry_run` defaults to `False`.

Why the split matters: taint is the transitive union of every parent's sources computed at write time, so a
record's inherited attribution is not readable from its own `source` field. It is not hypothetical — a record
repaired by `rederive()` inherits the taint of the source it was rewritten from, so erasing that source takes
the repair with it and the store keeps neither the old value nor the corrected one.

**What the preview does NOT cover.** Records that survive but are *modified*: `forget()` scrubs deleted ids
from survivors' `links` and drops `superseded_by_toggle` pointers into the deleted set, so a survivor can
lose corroboration or its `revert()` target — none of which appears in `would_erase`. Registered erasure
targets are named in `targets` but the manifest is not run. Nothing outside this store. And `sample` returns
record text, including records ordinary recall would not surface, while writing no receipt.

**Accuracy bound, unchanged from 1.52.0:** the lineage path follows *declared* `derived_from` edges. A
paraphrase written back as a fresh unparented record is invisible to both the preview and the erasure. In our
own 27,290-record production store, writer-declared lineage fields measured **0.00%** — if your writers do not
pass `derived_from`, expect `inherited: 0` and read `would_erase` as a source-match delete. The figures above
(`would_erase 3 / direct 1 / inherited 2`) are one constructed fixture, not a typical ratio.

A dry run is an ordinary affordance — `terraform plan`, a rolled-back transaction, `SELECT` before `DELETE`;
nothing novel is claimed for the mechanism. Also fixed: the docstring had offered `'key:<hex>'` as a subject
form, which `_rec_sources` has never emitted, so such a call silently erased nothing. 335 tests pass.

## 1.52.0 - two more internal writes declare the record their text came from

Erasure in inspeximus follows **declared** lineage edges. 1.51.0 fixed `revert()`, which rebuilt a record's
text from a predecessor without declaring it. Two sibling sites had the same shape.

**`rederive()` declared the wrong parent.** It builds new text out of a demoted record —
`rewrite(r["text"], old, new)` — but declared only the *corrected root*, filing the actual text parent in
`meta["rederived_from"]`, which nothing traverses. Measured on the pre-fix code:

```
forget_subject("alice-ticket")  ->  erased 1
rederived copy still live      ->  True
  its text                     ->  'alice bernard reaches the nightly backup with oauth2'
erasure_audit verdict          ->  no_declared_residue
```

The erasure was reported as done, the copy carrying that subject's wording stayed, and the audit found no
declared residue — a correctly scoped answer about declared edges, and blind to the undeclared copy. Now both
parents are declared: taint carries `aliceticket`, `erased` goes 1 → 2, and the correction still works.

**A stale `meta["rederived_to"]` froze a record on its corrected-away value.** That pointer is `rederive`'s
single-shot guard, so it gates behaviour. Erase the rederived copy and the pointer outlived it: the derived
fact stayed on the value just corrected away and `rederive` returned `{"rederived": 0, "skipped": 0}` with no
note. `forget()` now drops it, like `superseded_by_toggle`. Re-applying the correction goes 0 → 1.

**The other six id-bearing fields are deliberately KEPT** on erasure. `derived_from`, `taint`, `revert_of`,
`rederived_from`, `duplicate_of` and `resolved_over` are history, and `erasure_audit` reports them as
`dangling_lineage`. Scrubbing them would delete the evidence and turn the audit's answer into a false clean.
A test fails on that tempting over-fix.

**A CONSEQUENCE, stated rather than discovered later.** Because a repair now declares the demoted record it
was rewritten from, it also inherits the *retracted source's* taint. Erasing that source takes the repair with
it (`erased` 2 → 3), leaving neither the wrong value nor the right one. This is not new damage — before, the
repair survived by being invisible to erasure, not by being judged safe — but `forget_subject()` has **no
`dry_run`**, so there is no preview of the blast radius. Pinned by a test; a preview is not yet built.

**A static guard against recurrence, and its honest scope.** A test walks the AST of every module in the
package and fails on any `.remember()` whose text is lifted from a store record absent from `derived_from`.
The first version of this guard was a regex; an adversarial review injected four offending shapes and it
caught two, missed a multi-line call and an unfamiliar local name, and false-alarmed on a legitimate
`pid = r["id"]; derived_from=[pid]`. The AST version catches 5 of 5 and is pinned by a negative control in
both directions. It is **syntactic**: it cannot verify the declared parent is the semantically right one, and
it cannot see text no static reader can attribute.

**What this does not do.** Declared-edge lineage cannot follow a paraphrase written back as a fresh unparented
record — the dominant production write path. In our own 27,290-record deployment, writer-declared lineage
fields measured **0.00%**. This binds the library's own writes; it does not make erasure sound, and it is not
a compliance control.

Prior art this is an instance of, not a contribution to: deletion propagation through views is NP-hard for
project–join and join–union queries under the source side-effect objective (Buneman, Khanna & Tan, PODS 2002);
the missing-propagator failure is *under-tainting* (DTA++, NDSS 2011); the static rule is what the Checker
Framework's Tainting Checker and Semgrep taint-mode propagators already express. 323 tests pass.

## 1.51.0 - the store declares lineage where it owns the write, and revert stops hiding from erasure

Third attempt at the same problem, and the first that is exact. Declared lineage measured **0.00%** across a
real 27,290-record deployment (1.49.0); inferring it from content was withdrawn at **precision 0.06-0.23** **[CORRECTED 2026-07-27: the probe prints precision 1.000 with ZERO false parents and recall 0.133→0.000; the failure is recall, not precision. See docs/API.md.]**
(1.50.0). This does neither: at a write site *inside the library*, the store already knows the parent, so it
states it.

**The bug this closes is not a coverage number.** `revert()` rebuilds a record's text from a specific
predecessor and recorded that parent in `meta['revert_of']` — a field no lineage check traverses. So a
restored value looked parentless. Erase the subject the value came from and the reverted copy **survived,
still carrying that subject's data**. `forget_subject()` missed it; `erasure_audit()` reported clean.

Now:

```python
m.remember("billing uses api keys", key="billing::auth", source={"doc": "runbook-v1"})
m.remember("billing uses oauth2",   key="billing::auth", source={"doc": "adr-014"})
restored = m.revert("billing::auth")["restored"]

m.provenance(id=restored)["origin"]["inherited_taint"]   # ['runbookv1'] - the ORIGIN rides the edge
m.forget_subject("runbook-v1")                           # now erases the reverted copy too
```

**Audited every internal write site, and deliberately did not declare most of them.** Of 18 `self.remember`
call sites in the library, **5 are genuine derivations** — `rederive` (already declared), `revert`,
`submit_revert` (x2) and `resolve_reopened` — and the other 13 are not: a plain write, a decision, an admitted
external record and the routing paths have no in-store parent, and inventing one would taint everything with
everything. Over-declaring is its own failure mode, so the test pins the exact set.

5 new tests, including the erasure hole as a regression. 316 passed, no behaviour change to any write a
caller makes directly.

## 1.50.0 - CORRECTION: infer_lineage measured against ground truth, and it does not work

**1.49.0 shipped four hours ago and oversold this feature. This entry withdraws the claim.**

What 1.49.0 reported was a **firing rate** — ~22% of writes stamped on our own 27,290-record deployment,
stable across thresholds — and called it *calibrated*. A firing rate is not an accuracy. There was no ground
truth in that corpus for "was this write actually derived from that recall", which the release notes said,
and then the summary line went on to imply the number meant more than it did.

Measured properly (`probes/infer_lineage_precision.py`, constructed corpora where derivation is known):

| regime | result |
|---|---|
| topically **diverse** store, same-topic negatives | **precision 0.06-0.23**, recall 0.03-0.22 — at its best setting it stamps **43 wrong parents for every 13 right ones** *(CORRECTED 2026-07-27: the cited probe prints precision **1.000**, **zero** false parents, recall 0.133→0.000. The withdrawal stands; the reason was recall, not precision.)* |
| topically **homogeneous** store | recall **~0**, blind BY CONSTRUCTION: the derived write overlaps the whole store exactly as much as it overlaps its parent (0.943 vs 0.943, lift **+0.000**), so the null adjustment removes the entire signal along with the noise |

It fails in both directions. The ~22% seen on the real corpus is therefore not 22% of true derivations — on
this evidence most of it is noise. The threshold is not the tuning knob it appears to be; the governing
variable is how confusable a random slice of the store is with the parent, and neither extreme works.

**What changed:** the docstring claim is withdrawn (not softened), the probe that killed it ships so anyone
can re-run it, and the default remains `0.0` = OFF. The code is kept because the null-adjusted shape may be a
useful substrate for a better signal — but nobody should enable it expecting lineage recovery.

**What stands from 1.49.0:** the measurement that motivated it. Declared fields still read **0.00%** across
27,290 records in a 43-day deployment while store-computed fields run at 88-90%. The problem is real. This
attempt at solving it is not the answer.

## 1.49.0 - infer_lineage: stamp a derivation edge without asking the writer

**The measurement that forced this.** `remember(derived=True)` auto-stamps the last recall as a write's
parents - the right shape, since the store carries the edge and the untrusted model never holds the switch.
But that flag is writer-set, and on our own 8-agent, 43-day, **27,290-record** deployment its coverage was
**0.00%** - alongside `key`, `object`, `source`, `taint` and `attested_key`, all also 0.00%, while the fields
the STORE computes for itself ran at 88-90% (`links`, `superseded`). One write call, four arguments, six
weeks, nobody noticed. A mechanism that requires the writer to opt in does not run.

**`Inspeximus(infer_lineage=0.2)`** stamps the edge from what the store can already see: how much more the new
text shares with what was just recalled than with a same-store baseline it was not built from.

**Null-adjusted, and that is the whole design.** Measured on 27,342 real agent writes:

| | |
|---|---|
| raw overlap threshold | median **1.000**; 0.8 still stamps **77%** -> degenerate |
| vs a random same-store window | 0.860 vs 0.540 -> most of the score is vocabulary |
| null-adjusted (shipped) | **~22% stamped**, stable across thresholds 0.10-0.50 |

Agents reuse a small vocabulary, so a raw score measures how repetitive the corpus is, not what the write
came from. Subtracting the store's own baseline leaves the part that is about THIS recall.

**Honest limits.** There is no ground truth for "truly derived" in that corpus, so ~22% is a FIRING rate, not
a precision; a separate null-model check put discrimination at **61.6%** (chance 50%), so a real share of
inferred edges will be wrong. It over-taints by design - a false parent is visible in `provenance()`, a
missing one is silent. **Default 0.0 = OFF**, byte-identical legacy: silently changing a shipped write path
is worse than either failure. An explicit `derived_from` always wins over the inference.

8 new tests, including the regression that made the raw version unusable (shared vocabulary alone must not
earn an edge) and a check that taint rides the inferred edge into `provenance()`.

## 1.48.0 - erasure_audit(): after a deletion, check what the lineage says survived

Erasing the record is the easy half. The half that bites is the **summary built from it**, which no longer
resembles the subject's data — so a text-match delete walks past it and the fact is back next session.
`forget_subject()` already cascades along lineage; **`erasure_audit(subject=, values=)`** reports what
survived: `subject_still_attributable`, `taint_without_origin` (a derivative outlived the origin it
inherited), `dangling_lineage`, `tombstone_gap`. CLI `inspeximus erasure-audit --subject X` (exit 1 on residue,
usable as a regression gate) and an MCP tool (surface = 55).

**`coverage` is the load-bearing field, and the CLI prints it first.** Every structural check walks DECLARED
`derived_from` edges. A store whose writers never threaded lineage has no edges to walk, so it would report
"nothing found" while having inspected nothing — and most real writers (LLM summarizers, RAG chunkers,
consolidation passes) declare nothing. Collapsing "checked, clean" and "couldn't check" into one reassuring
boolean is how a deletion audit becomes a false assurance, so when nothing is declared the verdict is
**`unaudited`**, never a pass, and `coverage` reports the declared-lineage ratio outright.

**Housekeeping is separated from erasure.** Capacity eviction and the consolidation keep-budget hard-delete
for size reasons and tombstone exactly like a real erasure does — so a naive check fires constantly on any
bounded store. Only deletions carrying a request id or a real basis (not the generic default) count as
`residue`; the rest land in `advisory` with the cause attached, reported but never counted.

**Honest limits, shipped in the response and as tests** — this is evidence about what the store RECORDED, not
proof that no copy remains, and it does not discharge an erasure obligation. A derivative whose writer never
declared its parents is invisible to every structural check (`test_an_undeclared_derivative_is_NOT_found_
structurally` asserts we do NOT find it); it covers this store only, never your vector index, prompt logs,
model weights or backups; and a party that stops declaring lineage always looks clean. The value text scan is
an explicit heuristic that never moves the verdict, and matches with longer-token exclusion (plain `` lets
`UTC` fire inside `UTC-8`, reporting a different, longer value as recovered).

**Prior art, credited:** DELF-style deletion-correctness auditing (Cohn-Gordon et al., USENIX Security 2020)
applied to an agent-memory store; the orphan/dangling half is classical referential-integrity checking.
Stronger formal treatments exist (Garg/Goldwasser/Vasudevan, *Formalizing Data Deletion in the Context of
the Right to be Forgotten*, EUROCRYPT 2020; Chakraborty et al., PVLDB 18(10) 2025).
Ours is the shipped implementation, not the mechanism.

**Fix:** the CLI opened stores with receipts OFF, so a shell `inspeximus remember` against a receipted store
silently did **not** extend the receipt chain — the CLI punched a hole in the evidence it exists to produce,
and the next `verify_writes()` saw an unreceipted record. `_store()` now detects an existing
`<path>.receipts.json` sidecar and keeps receipts on. Regression test included.

New tests (10), including a mutation-killing negative control: without a case where a surviving taint's origin
also survives, `taint_without_origin` could degenerate into "has a taint field at all" and the suite would
still pass. No behavior change to `forget`, `forget_subject` or any existing call.

## 1.47.0 - provenance(): one answer to "where did this fact come from?", and every adapter goes compliance-aware

**Correction first, because we got it wrong in public.** Until today this project's README, `docs/AI_ACT.md`,
`docs/COMPLIANCE.md` and the ai-act landing page all said the EU AI Act's Annex III high-risk obligations start
applying on **2 Aug 2026**. That was correct when written and is now wrong: **Regulation (EU) 2026/1744** (the
Digital Omnibus on AI, adopted 8 Jul 2026, published in the OJ on 24 Jul 2026, in force 27 Jul 2026 —
[ELI](https://eur-lex.europa.eu/eli/reg/2026/1744/oj/eng)) deferred those obligations to **2 Dec 2027**
(standalone Annex III, Art. 6(2)) and **2 Aug 2028** (Annex I product-embedded, Art. 6(1)). Every date in this
repo is corrected as of this release. We are stating it here rather than quietly editing the pages, because a
compliance-adjacent project that silently fixes its own deadline copy has not earned the word evidence. We have
also removed the implication that the Act *requires* memory provenance or tamper-evidence: Art. 12 requires
automatic event logging, Art. 19 retention, Art. 15 accuracy/robustness/cybersecurity, and none of them name
memory, provenance or tamper-evidence. These features exceed the text; they are not mandated by it.

**`provenance(key=…)` / `provenance(id=…)`** assembles the answer a memory layer is asked for most often, from
primitives that already existed but had to be called separately and in the right order: `origin` (the declared
source, the taint inherited **transitively** through summarization, origin attestation, acting
user/agent/session, the orphan flag, and any ancestor since erased), `trust` (the evidence grade — earned, never
writer-settable), `timeline` (`history()`, incl. the policy that retired each value), and `integrity` (whether
the record still matches the content **and attribution** its write receipt committed to — so a post-hoc relabel
of a source is loud, not silent — plus the current `anchor()`). A `limits` field rides along stating what this
does NOT prove (tamper-*evident*, not *correct*; unsigned it only catches an editor who cannot also rewrite the
`.receipts` sidecar), so a renderer cannot quietly drop the caveat. Exposed as `inspeximus provenance <key>`
(`--json`) and the `provenance` MCP tool — MCP surface = 54 tools. Read-only; no new state, no write-path cost.

**Fix (same area):** the CLI opens stores without receipts by default, so a report *about* the receipt chain
described a receipted store as "receipts off at write time" — wrong, not merely unhelpful. `provenance` now
forces receipts on for the read, like `audit-build` / `compliance` / `retention` already did.

**`ComplianceMixin` now on every class-based adapter** (was LangGraph + CrewAI only): LangChain
`InspeximusRetriever` and `InspeximusChatMessageHistory`, LlamaIndex `InspeximusMemoryBlock`, AutoGen
`InspeximusMemory`, OpenAI-Agents `InspeximusSession`, Haystack `InspeximusDocumentStore`, ADK
`InspeximusMemoryService`. Whichever framework you already use, the AI-Act evidence comes off the same object
your agent writes memory to. Pydantic AI stays out on purpose — it exposes a function toolset, not a class.

**Measured, not assumed:** the mixin's `store: Any` class annotation had to be REMOVED. Pydantic collects
annotations from plain mixin bases too, so it was promoted to a model field on the pydantic-based adapters and
shadowed LlamaIndex's `store` **property** — `self.store` returned the property object and every compliance
call would have failed on a non-store. Caught before rollout; pinned by a regression test.

New tests (16, in `tests/test_provenance.py` + `tests/test_governance_mixin.py`); the mixin test also runs in
the four CI audit jobs that install a real framework. No behavior change to any existing call.

## 1.46.0 - forget(dry_run=True): preview a bulk delete before you commit it

A safety valve on the one irreversible operation. `forget(..., dry_run=True)` returns
`{would_forget, ids, sample, dry_run:True}` — a count plus a few matched record texts so you can eyeball what a
bulk `where=`/`--contains` selector actually caught — and deletes NOTHING (no delete, no tombstone, no save).
Exposed on the CLI (`inspeximus forget --contains X --dry-run`) and over MCP (`forget(dry_run=True)`). This is
the "bulk forget with dryRun" the docs call a moat: review before you erase. New tests (2). No behavior change
to a normal forget (dry_run defaults False).

## 1.45.0 - the EU AI Act compliance surface over MCP

The whole agent-memory compliance capability is now callable by any MCP client (Claude Code, Cursor, …). Five
new MCP tools delegating to the free modules: `compliance_report`, `compliance_check`, `retention`,
`audit_bundle`, `verify_audit_bundle`. New env `INSPEXIMUS_RECEIPTS=1` (opt-in, default off) turns on the
tamper-evident write/erasure chain the record-keeping tools evidence, without an existing MCP store gaining a
sidecar unexpectedly. So an agent can produce and verify its own EU AI Act evidence in-loop. docs/AI_ACT.md
notes the MCP surface. New tests (2). No behavior change to existing tools; the store defaults to receipts off.

## 1.44.0 - compliance-aware framework integrations (LangGraph / CrewAI)

New `inspeximus.integrations.governance.ComplianceMixin` — an integration store that holds an inspeximus in
`self.store` gains the EU AI Act evidence operations on the SAME object the framework uses as memory, by pure
delegation to the free compliance/audit APIs: `compliance_report`, `write_compliance_report`,
`compliance_check`, `retention`, `audit_bundle`, `verify_audit_bundle`. Wired into the LangGraph
`InspeximusStore` and CrewAI `InspeximusStorage`, both of which also gain a `receipts=True` constructor flag for
the tamper-evident record-keeping chain those reports evidence. So an agent framework's memory produces
auditor-ready AI-Act evidence with zero extra wiring. New tests (4, LangGraph skipped when absent). No behavior
change to existing APIs.

## 1.43.0 - retention enforcement: `inspeximus retention` (storage limitation)

The enforce-side of `compliance --check`'s `pii_over_retention` flag — close the detect->enforce loop for GDPR
Art. 5(1)(e) storage limitation. New `compliance.retention_sweep(store, max_age_days, now_ts=, pii_only=True,
apply=False, basis=, request_id=)`: finds ACTIVE records older than the window and, with `apply=True`,
hard-deletes them, emitting a signed tombstone per record so the erasure is itself auditable. DRY-RUN by
default (returns what WOULD be erased). CLI `inspeximus retention --max-age-days N [--all] [--apply]` (dry-run
unless `--apply`; `--all` applies to every record, default PII-tagged only). Deterministic, no LLM. New tests
(4), docs/AI_ACT.md enforcement snippet. No behavior change to existing APIs.

## 1.42.0 - continuous compliance gate: `inspeximus compliance --check`

Turn the point-in-time compliance overlay into an enforceable CI gate — the same pattern that made
`check-code` a build gate, now for the AI-Act memory posture. New `compliance.compliance_check(store,
require_receipts=, max_pii_age_days=, prior_anchor=, now_ts=)` asserts the invariants a store claiming AI-Act
record-keeping must hold and returns {ok, violations, checked}:
  - `receipts_disabled` (Art. 12/19) — the store has records but no write receipts (logging was off at write time)
  - `integrity_failed` (Art. 12/15) — the receipt/tombstone chain fails verify_writes (altered out of band)
  - `not_append_only` (Art. 12/19) — history isn't a consistent extension of a pinned `prior_anchor`
  - `pii_over_retention` (GDPR 5(1)(e)) — active PII older than `max_pii_age_days` (storage limitation)
CLI: `inspeximus compliance --check [--max-pii-age-days N] [--prior-anchor a.json] [--allow-no-receipts]`
exits non-zero on any violation; `.pre-commit-hooks.yaml` gains `id: inspeximus-compliance-check`. New tests (5),
docs/AI_ACT.md "continuous compliance gate" section. No behavior change to the existing `compliance` report.

## 1.41.0 - agent-memory compliance overlay: `inspeximus compliance`

The runnable, honest EU-AI-Act-memory-slice overlay — turn a live store into an article-labelled EVIDENCE
report with LIVE counts, so the compliance mapping is demonstrable per store, not asserted. New module
`inspeximus.compliance`:
  - `compliance_report(store, expected_pubkey=)` — for each memory-relevant control (EU AI Act Art. 12
    record-keeping, Art. 19 logs-kept-≥6-months, Art. 15 accuracy/robustness/cybersecurity, Art. 10 data
    governance; GDPR Art. 17 erasure, Art. 30 records-of-processing, Art. 5(1)(d) accuracy) returns the
    obligation, the inspeximus evidence, a LIVE count from the store, and an honest per-store status:
    'evidence' (exercised), 'available' (shipped, not exercised here), or 'needs_receipts'.
  - `render_html(report)` — a self-contained, theme-aware, JS-free DPO-facing page.
  - CLI `inspeximus compliance [--out report.html | --json]`.
Scope is stated in every output: the AGENT-MEMORY slice only, EVIDENCE not certification, obligations bind the
controller/provider/deployer not the library. Article wording traceable to Reg (EU) 2024/1689 / 2016/679 (see
docs/COMPLIANCE.md, updated with the audit bundle + the staggered-enforcement note: the memory-relevant
high-risk duties bite 2 Aug 2026 for Annex III systems, not "the whole Act at once" -- SUPERSEDED: deferred to 2 Dec 2027 by Reg. (EU) 2026/1744; see 1.47.0). New
`tests/test_compliance.py` (6), `examples/10_compliance_overlay.py`. No behavior change to existing APIs.

## 1.40.0 - portable audit bundle: hand an auditor one file they verify offline

The governance / EU AI Act Art.12 wedge — a portable, content-free record-keeping artifact + a STANDALONE
verifier that needs neither the live store nor the receipt key. New module `inspeximus.audit_bundle`:
  - `build_bundle(store, expected_pubkey=, sign=)` — serialise the store's record-keeping state (signed anchor,
    governance_report, supersession_report, and the content-free write + tombstone hash-chains) into one
    self-verifying json. Content-free: receipts commit to content/attribution HASHES, tombstones to surrogate
    ids — no memory text leaves the store.
  - `verify_bundle(bundle, witnesses=, threshold=)` — OFFLINE verification: re-walks both chains from genesis
    (every hash + prev-link), matches tips/counts to the anchor, checks the anchor's sth_hash, and (if witnesses
    given) verifies external co-signatures — the only operator-adversarial check. Returns {ok, checks, problems,
    summary}; any post-export tamper fails it.
  - CLI: `inspeximus --receipts remember ...` (opt-in tamper-evident chain), `inspeximus audit-build --out
    bundle.json`, `inspeximus audit-verify bundle.json` (exit 0 PASS / 1 FAIL). Also runnable as
    `python -m inspeximus.audit_bundle build|verify`.
New `tests/test_audit_bundle.py` (9: build/verify, content-free, three tamper classes, dropped-tombstone,
witness operator-adversarial, CLI contract), `examples/09_audit_bundle.py`, README "Portable audit bundle"
section. Honest scope restated in-band: a tamper-evident record-keeping ARTIFACT, not a compliance
certification. No behavior change to existing APIs.

## 1.39.0 - code_guard as a CI gate: `inspeximus check-code` + pre-commit hook

Turn 1.38.0's coding-agent guard from a library call into an enforceable build gate — the distribution wedge:
  - `code_guard.scan_lines(store, code)` — per-occurrence view of check_code with 1-based line numbers
    ([{symbol, replacement, reason, line, snippet}]); the CI-grade output shape.
  - `inspeximus deprecate <old> <new> [--reason ...]` — record a refactor from the shell (keyed supersession).
  - `inspeximus check-code <files...>` — scan files and EXIT NON-ZERO (with `file:line: resurrected ...`) if any
    deprecated symbol reappears; exit 0 when clean. `--json` for machine output.
  - `.pre-commit-hooks.yaml` — reference this repo as a pre-commit hook (`id: inspeximus-check-code`) so a
    resurrected API cannot be committed. Commit the store (`.inspeximus/memory.json`) and the deterministic
    token scan is a pass/fail every clone reproduces.
New tests (scan_lines line numbers, CLI exit-code contract), README "Enforce it in CI" section. No behavior
change to existing APIs; check_code/symbol_status/deprecate_symbol unchanged.

## 1.38.0 - code_guard: the coding-agent "don't resurrect the deleted API" wedge

New module `inspeximus.code_guard` + three MCP tools that shape keyed supersession for the coding loop — the
single most common way agent memory fails there: a refactor renamed/removed a function, but the model re-emits
the old call because the old signature is still in its context.
  - `deprecate_symbol(store, old, new, reason)` — record a refactor (a keyed supersession, deterministic, no
    LLM). A later deprecation of the same `old` supersedes the replacement.
  - `symbol_status(store, name)` — one-shot verdict for a symbol about to be emitted: 'superseded' (with the
    `replacement` to use) or 'active' (no recorded deprecation).
  - `check_code(store, code)` — the echo-guard for code: scan a whole generated snippet and flag every
    deprecated symbol it resurrects (whole-identifier match — `foo` matches `foo(`/`x.foo`, never `foobar`;
    a lexical token scan, not an AST parse). Returns [{symbol, replacement, reason, occurrences}], empty = clean.
Exposed over MCP as `deprecate_symbol` / `symbol_status` / `check_code`. Built entirely on the proven core
(`remember` keyed supersession + `_current_active`) — no new storage, no LLM, no embeddings. New
`tests/test_code_guard.py` (8), `examples/08_code_guard.py`, README "For coding agents" section. Serves the
vendor-abandoned need behind Claude Code #14227. No behavior change to existing APIs.

## 1.37.0 - reference witness server: stand up your own witness network

Turns 1.36.0's witness pool into something you can actually deploy across independent hosts, with zero new
dependencies (stdlib `http.server` + `urllib`):
  - `inspeximus.witness_server` — a runnable reference witness: `python -m inspeximus.witness_server --port
    9700 --state witness.json`. `GET /pubkey` returns its key; `POST /cosign {store_id, anchor}` co-signs (200)
    or REFUSES a fork/rollback with `409 {"refused": reason}` (the split-view defense over the wire). Persists
    its per-store last-signed head to `--state` so the refusal survives a restart.
  - `witness_pool.http_witness(url)` — a client-side callable `(store_id, anchor) -> (pubkey, sig)` that
    co-signs via a remote witness and raises on a 409 refusal, so `collect_cosignatures` records a remote fork
    as an alarm exactly like a local one. Mix local `Witness` objects and `http_witness(...)` in one k-of-n set.
This is the operator-adversarial layer made deployable: independent parties each run a witness, a client
requires k-of-n, and a compromised host cannot show two histories that both reach threshold — honest witnesses
refuse the fork (locally or over HTTP). New: `tests/test_witness_pool.py::test_http_witness_roundtrip`,
README "Witness network" section. No behavior change to existing APIs.

## 1.36.0 - witness pool: the k-of-n co-signing layer made usable

New module `inspeximus.witness_pool` turns the 1.34.0 witness primitives (witness_cosign /
verify_cosigned_anchor / detect_split_view) into a runnable gossip layer that stops a compromised host from
showing two different memory histories to different clients:
  - `Witness` — an independent co-signing party that holds one Ed25519 key and remembers, PER STORE, the last
    signed tree head, so it REFUSES to co-sign a fork or rollback. That memory is PERSISTED (atomic json) — the
    refusal must survive a witness restart, or an operator could restart it and fork past it.
  - `collect_cosignatures(store_id, anchor, witnesses)` — a client gathers k-of-n co-signatures and surfaces
    any witness that REFUSED as a fork alarm (a refusal is the split-view signal, not a silent drop). Feeds
    straight into `verify_cosigned_anchor(..., threshold=k)`; a forked head cannot reach threshold because
    honest witnesses refuse it.
Witnesses can be local/in-process or wrapped behind HTTP by the caller (a callable `(store_id, anchor) ->
(pubkey, sig)`); the core logic needs no network, no LLM, no GPU. This is the one operator-adversarial
guarantee a free single-party certificate structurally cannot provide (it needs an independent third party),
and the lightest such layer in the field — no competitor ships external witnessing. New example
`examples/07_witness_pool.py` (end-to-end: honest k-of-n, honest extension, and a forked head that all
witnesses refuse). 7 tests incl. persistence-survives-restart and split-view proof; full suite green (233).
No new dependencies (Ed25519 only).

## 1.35.0 - selection_integrity + a compliance mapping + adversarial-gate fixes

New primitive `selection_integrity(query, k)` (library + MCP tool): make SELECTION-LEVEL manipulation
auditable. Tamper-evidence checks that what you retrieved is authentic, but is blind to an attacker who
injects authentic-looking UNTRUSTED writes that reroute WHICH trusted facts reach the top-k (Fei et al.,
'Selection Integrity for LLM Graph Memory', arXiv 2606.12290). It diffs the top-k actual recall against the
top-k of only trust-anchored memories and surfaces any trusted fact displaced by untrusted writes, plus the
untrusted records occupying top-k slots. Flags, never rewrites. Returns stable=None (unknown, not "safe")
when no trust root is configured.

Also: `docs/COMPLIANCE.md` — an honest control mapping (NIST SP 800-53r5 / 800-218A / AI 600-1 / 800-88,
OWASP LLM Top 10 & ASI06, GDPR, EU AI Act) with a mapping-is-not-certification disclaimer and a gaps section.

Adversarial-gate fixes (a two-cluster security audit of every new function this cycle):
  - **verify_claim (correctness):** the numeric/negation clash heuristic was blind to CATEGORICAL corrections,
    so with `object` omitted a claim citing a corrected categorical value (e.g. "Berlin" after Berlin->Munich)
    could read as `supported`. Now the record's stored `object` is the discriminator on BOTH the keyed and
    keyless paths, so categorical stale/contradiction is caught. (Fix to the 1.32.0 primitive.)
  - **witness co-signing (robustness):** `verify_cosigned_anchor` and `detect_split_view` now reject malformed
    anchors/cosignatures safely instead of crashing; `detect_split_view` returns an explicit `undetermined`
    field so different-size heads (not settleable from tree heads alone) do not read as "no fork".
  - `selection_integrity` returns `stable=None` rather than `True` when it is blind.
Exposed MCP tools 46 -> 47. New tests across all three areas; full suite green (226 passed).

## 1.34.0 - witness co-signing: split-view detection (the gossip layer no competitor ships)

anchor()/verify_consistency() catch a rewrite on ONE timeline, but a compromised operator can still show
DIFFERENT histories to different clients (a split-view / fork). This release adds the Certificate-Transparency
GOSSIP layer that closes it — external witnesses co-sign the signed tree head, k-of-n:
  - `witness_cosign(witness_sk, anchor, prior_anchor=None)` — a witness co-signs the sth_hash and REFUSES
    (raises) an obvious fork it can see with no log: a rolled-back size, or the SAME size with a different tip.
  - `Inspeximus.verify_cosigned_anchor(anchor, cosignatures, witnesses, threshold=k)` — client-side k-of-n
    trust: an operator that forks must get k independent allowlisted witnesses to co-sign the fork; honest
    witnesses refuse. Supports a {pubkey: class} allowlist so Sybil variants collapse to one vote.
  - `Inspeximus.detect_split_view(anchor_a, cosigs_a, anchor_b, cosigs_b, witnesses)` — auditor-side FORK
    PROOF: a witness that validly co-signed two inconsistent heads (same size, different tip) is cryptographic
    proof the operator presented divergent histories.
  - `new_ed25519_keypair()` convenience for minting witness/attestation keys.
Result: a compromised host cannot silently show two different memory histories without corrupting the
witnesses — the operator-adversarial guarantee none of the 2026 memory-integrity peers (MemLineage, Portable
Agent Memory, mnemosyne-guard) provide. Honest limit: split-view is decidable from tree heads alone only at a
shared log size; different sizes still need verify_consistency against a replica. Ed25519 (already a dep of the
signed-store path); no NEW dependencies. Exposed MCP tools 44 -> 46. 13 tests incl. the split-view scenario;
full suite green (217 passed).

## 1.33.0 - check_self_narration: keep the assistant's self-talk out of the store

New write-gate primitive `check_self_narration(text)` (library + MCP tool). An LLM memory-writer routinely
stores its OWN reasoning and hedges ("as an AI...", "I think...", "I remember that you...") as if they were
facts about the user, silently polluting the store. This deterministic, zero-LLM phrase guard flags such
candidate writes at word boundaries and returns `{'self_narration': bool, 'markers': [...]}` so the caller
can gate or rewrite before remember(). It FLAGS, never blocks (a first-person quote can legitimately trip it),
matching inspeximus's no-silent-rewrite stance. Pairs with check_conflict (contradiction gate) and
verify_claim (grounding gate) to complete the write/assert boundary. Exposed MCP tools 43 -> 44. 8 tests;
full suite green. No new dependencies. (Note: write-time ORIGIN-binding — a source cryptographically signing
authorship of a write — is already provided by the attestation layer: remember(..., attestation=), plus
verify_attribution() and verify_writes().)

## 1.32.0 - verify_claim: read-time grounding, the output-side complement to check_conflict

New primitive `verify_claim(text, key=, object=)` (library + MCP tool). `check_conflict` gates WRITES; this
governs the ASSERTION side — call it on a memory-claim an agent is about to state back to the user ("you told
me X") to see whether the CURRENT stored truth supports it. Deterministic (no LLM), read-only, and — the point
— supersession-AWARE, so it separates four verdicts: `supported` (matches an active memory), `stale_superseded`
(matches a value that has since been CORRECTED/reverted — the reply is citing an outdated fact; the response
carries the current value), `contradicted` (clashes with current truth), `unsupported` (no matching memory —
possible fabrication). The `stale_superseded` case is the differentiator: a write-gate/tombstone store stops a
corrected fact being re-STORED, but only a check against current-truth-vs-history catches the same corrected
fact being re-ASSERTED in a generated reply — and a cosine/LLM grounding judge tends to miss it because the old
value is usually MORE embedding-similar to the claim than a rephrase. Exposed MCP tools 42 -> 43. 8 new tests;
full suite green. No new dependencies.

## 1.31.0 - expose the auditor's toolkit over MCP

Eight more read-mostly governance/audit primitives are now MCP tools, completing the DPO/auditor surface an
agent can call without dropping to the library: `erasure_certificate` (portable, independently-verifiable
GDPR Art.17 / EU AI Act Art.12 receipt), `erasure_report` (the erasure log), `state_digest` (deterministic
state fingerprint), `history` (a key's full validity timeline), `as_of` (bitemporal point-in-time recall),
`verify_attribution` (tamper-evidence for the poison-defense layer), `irreversible_budget_report`, and
`memory_report` (inspector overview). All read-only/deterministic; the mutating governance actions
(slash/shred/spend/submit_revert) are deliberately left to the library API. Exposed MCP tools 32 -> 42.
Verified: all eight execute end-to-end on a signed store. No new dependencies.

## 1.30.0 - expose the operator-adversarial provenance primitives over MCP

`anchor()` and `verify_consistency()` are now MCP tools. Both already existed in the core but were
unreachable over MCP, which meant the one part of the tamper-evidence story that survives an adversarial
*operator* was invisible to agents. `verify_writes()` proves the write chain wasn't silently edited — but
an operator who holds the receipt key can rewrite the whole history *and* re-sign it so it still verifies
internally. `anchor()` emits a Certificate-Transparency-style signed tree head (RFC 6962): a compact,
externally-publishable commitment to the entire write + erasure history at this instant. Publish it where
the operator can't retroactively alter it (a public log, a third-party witness, the auditor's own records),
and `verify_consistency(prior_anchor)` later detects any append-only violation against it — the forged tip
won't reconcile with the tip an outsider already pinned. Verified end-to-end: a valid forward-extension
stays consistent; a tampered tip is caught as a fork. No new dependencies; the primitives are unchanged,
only newly reachable. Also corrected two stale claim strings in `claims_audit.py`: revert-to-predecessor is
*rare* (absent in mem0 and Graphiti), not unique — Letta ships an engine-level checkpoint-undo.

## 1.29.1 - remove an internal path from a docstring

A docstring in the core referenced an internal repository path (`agora_output/lab/memops/keying_recall.py`)
that describes where a behaviour was measured. That path means nothing outside the private repo and had no
business shipping in a public package; it is now just "(measured)". No code or behaviour change — a
hygiene fix, found while re-vendoring this core into a public benchmark and grepping it for internal
references. The whole package was re-scanned: no other internal path, secret, or identifier leaks.

## 1.29.0 - a Haystack DocumentStore

`InspeximusDocumentStore` implements Haystack's `DocumentStore` protocol (write_documents /
filter_documents / delete_documents / count_documents), a drop-in for `InMemoryDocumentStore` that
persists to a file and whose delete removes the value from disk. Duplicate policies (SKIP / OVERWRITE /
NONE / FAIL) match the reference exactly, and filtering reuses Haystack's own `document_matches_filter`,
so a `FilterRetriever` and pipeline serialization work unchanged. `haystack_audit.py` checks all of it
against `InMemoryDocumentStore` with a falsification control; nine tests cover the duplicate policies,
filter semantics, no-op delete, reopen, and on-disk erasure.

## 1.28.1 - receipts signed with `receipt_key` alone could never be verified

Passing `receipt_key` without `receipt_pubkey` signed every write receipt with `"pubkey": None`, so
`verify_writes()` could not check the signature and reported **"invalid signature" on records the store had
just written itself**. The data was fine; the integrity report was crying tampering at its own output. For a
layer whose whole job is to be believed, a false alarm is worse than no alarm — it teaches the reader to
ignore the one signal that matters.

The public half is now derived from the private key when it is not supplied, and a malformed key is rejected
at construction instead of raising from `bytes.fromhex` thousands of writes later, inside `remember()`.

Found by using the library the way a new user would, while checking a claim before putting it in a pull
request. Every existing receipts test passed *both* halves — the documented happy path — which is exactly
why it survived. Three regression tests now cover the key-only path, the malformed key, and the control that
tamper detection still fires on a real out-of-band edit.

## 1.28.0 - the ADK memory service ingests idempotently, and supports incremental writes

Google ADK ships no conformance suite for `BaseMemoryService`, so `InspeximusMemoryService` was called a
drop-in replacement for `InMemoryMemoryService` without anything checking it. `adk_audit.py` now does:
eight scenarios against ADK's own service, three repeats each, and `ADK_FALSIFY=1` breaks ingestion on
purpose so the comparison has to be able to fail.

Writing it found two real defects:

- **Re-adding a session stored it again.** ADK documents that a session "may be added multiple times
  during its lifetime", and the runner does exactly that, so a long conversation was written once per
  turn. Ingestion is now idempotent per event, keyed on the event id, and the seen-set is rebuilt from
  the store so it survives a restart.
- **`add_events_to_memory` was not implemented**, so the incremental path fell through to the base
  class and raised `NotImplementedError`. Both it and `add_memory` now work; a direct memory write has
  no position in a conversation, so it dedupes on its text.

Also new: `InspeximusMemoryService.from_uri()` and `register()`, which put the service behind
`adk web --memory_service_uri=inspeximus://memory.json` with no Python glue. Published as
`adk-inspeximus` for people who search PyPI rather than the docs.

## 1.27.2 - InspeximusStore now matches the reference on namespace lifetime

`InMemoryStore` keeps listing a namespace after its last key is deleted; this store dropped it. That
made "drop-in" need a footnote, and a footnote on a contract you claim to implement is the kind of
thing that gets an integration rejected -- rightly.

Default is now parity: deleting the last value erases the VALUE and leaves only the namespace name
behind, as a marker carrying no data. It never surfaces in `get` or `search`, and the deleted value
is still absent from the bytes on disk, which the audit checks.

The stricter behaviour is available as `InspeximusStore(prune_empty_namespaces=True)`, because a
namespace is not neutral metadata: `("user", "42")` names a person, and retaining that after every
value it held has been erased leaves an identifier behind. It is offered rather than imposed.

11 new tests pin both modes and the marker's invisibility.

## 1.27.1 — LangGraph adapter: conformance and parity fixes

Both of LangGraph's official verification routes were run against the adapter for the first time,
and each found a real defect.

- **Checkpointer, `langgraph-checkpoint-conformance`: BASE 4/5 -> FULL 5/5.** `put_writes` was not
  idempotent: the write-collection loop returned records regardless of status, so a superseded write
  came back as a pending one and re-putting a write left two. Checkpoint listing had the same missing
  filter. The suite now runs in CI and fails the build on a base capability.
- **Store, parity audit against `InMemoryStore` (the method LangGraph's docs prescribe):**
  `list_namespaces` ignored `match_conditions` and `max_depth` outright, so filtering by prefix
  returned every namespace in the store, and an unsorted result made `limit` return a different
  subset than the reference. Now filters prefix/suffix including `*` wildcards, truncates to
  `max_depth`, dedupes and sorts before slicing.
- **A literal duplicate is not a restatement.** 1.26.0's "agreement is not correction" kept both rows
  when the same key was written twice with identical text -- which is what broke put_writes
  idempotency. Same key + same text now collapses to one row; two differently-worded sentences
  carrying one value still keep both, which is the measured behaviour that change existed for.

## 1.27.0 — `inspeximus install --ide <host>`

One command wires the MCP server into an editor's own config. Hosts: claude, cursor, windsurf,
codex, cline. `--dry-run` prints the exact unified diff and writes nothing; `--scope project`
where the host supports one.

It edits files it did not write, so it is deliberately timid:

- **Never clobbers.** Unknown top-level keys survive, other people's servers survive, and -- the
  case that bit during testing -- keys on OUR OWN entry survive too. Re-running without `--store`
  used to drop the env the first run wrote, along with any `timeout` the user had added by hand.
- **Refuses malformed input.** A config that exists but does not parse is a hard stop with the
  parser's message, never an overwrite with a "clean" file.
- **Idempotent.** A second run reports "already present, unchanged".
- **Backs up** the original next to it, and writes through a temp file.
- **Says UNVERIFIED when it is.** `verified` means the shape came from the host's own documentation
  AND was exercised here. Only `claude` carries it: written to a real `~/.claude.json`, then
  `claude mcp list` reported Connected. The other four print the diff and the doc URL instead of
  implying they work.

Host-specific facts that a shared writer would have got wrong, each taken from the host's own docs:
Claude Code needs an explicit `type` (a missing one is skipped with a warning); Codex is TOML with
`deny_unknown_fields`, so one extra key is a parse error; Cline's timeout is in SECONDS and its
settings moved to `~/.cline/data/settings/` -- the VS Code globalStorage path most guides still
quote is legacy; Windsurf has no project-scoped config at all, so none is invented.

`uvx` is resolved to an absolute path at install time, because a GUI-launched editor does not
necessarily inherit the shell PATH and the failure mode is a bare "failed to connect".

## 1.26.1 — the MCP server could not start (shadowed its own SDK)

1.26.0 renamed `mnemo_mcp.py` to `mcp.py`. That file also carried an old line inserting its own
package directory onto `sys.path` so it could be run as a loose script. Harmless under the old name;
fatal under the new one: with the package directory on the path, the module became importable as
top-level `mcp` and shadowed the MCP SDK, so `from mcp.server.fastmcp import FastMCP` resolved to
itself and every launch died with `'mcp' is not a package`.

- the module is now `inspeximus/mcp_server.py` (console script `inspeximus-mcp` unchanged), a name
  that cannot collide with the SDK
- the `sys.path` insertion is gone

Found by the acceptance test for `inspeximus install`: the config was written correctly and Claude
Code listed the server, but it reported "Failed to connect" -- which is exactly the failure an
installer must be tested against rather than assumed away.

## 1.26.0 — the name is gone from the code, not just the label

1.25.0 renamed the distribution but kept the old name alive inside: the core class, two module names,
a compatibility alias package, the environment variable and the store filename. That was a
backwards-compatibility argument for an installed base that measurement had already shown does not
exist, so it bought nothing and left the product half-renamed.

**Breaking, deliberately and all at once:**

- `Mnemo` -> `Inspeximus`; every integration class follows (`MnemoStore` -> `InspeximusStore`,
  `MnemoSaver` -> `InspeximusSaver`, and the rest).
- `inspeximus.mnemo` -> `inspeximus.core`; `inspeximus.mnemo_mcp` -> `inspeximus.mcp_server`.
- the `mnemo` compatibility alias package is **removed**, as are the `mnemo` / `mnemo-mcp` console
  scripts. `pip install inspeximus`, `from inspeximus import Inspeximus`.
- `MNEMO_PATH` -> `INSPEXIMUS_PATH`; default store `mnemo_memory.json` -> `inspeximus_memory.json`;
  the Claude Code plugin store `.mnemo/` -> `.inspeximus/`.
- the encrypted-store magic changes from `MNMO` to `INSP`, so a store encrypted before this
  release must be rewritten.

**Fixed:** the MCP module wrote "needs the MCP SDK" to stderr at import time. Anything that walked the
package's submodules therefore printed it on unrelated output. It now raises with that message
instead, where the caller who actually tried to start the server sees it.

## 1.25.0 — renamed to inspeximus

The package is now **`inspeximus`** (`pip install inspeximus`, `import inspeximus`). The name is the
medieval charter that recites an earlier charter verbatim and attests it unaltered — the same act this
library performs on a corrected fact.

- `pip install agora-inspeximus` -> `pip install inspeximus`; console scripts `inspeximus` and
  `inspeximus-mcp`.
- **`import inspeximus` keeps working** and resolves to the *identical* objects, not copies, so
  `isinstance` checks, monkeypatching and module state behave the same across both namespaces. The
  alias is deprecated and will be removed in 2.0.
- Old console scripts `inspeximus` / `inspeximus-mcp` remain as deprecated aliases.
- **Unchanged on purpose:** the default store file (`inspeximus_memory.json`), the `INSPEXIMUS_PATH` environment
  variable, the plugin's `.inspeximus/memory.json` project store, and the public class names
  (`InspeximusStore`, `InspeximusSaver`, ...). Renaming any of them would orphan existing stores or break
  callers for no benefit; they can follow in 2.0.
- Repository and homepage moved to `DanceNitra/inspeximus`; GitHub redirects the old paths.

## 1.24.4

Adds `examples/trust_is_not_truth.py` — a standalone, pip-installable demonstration that the provenance
gate is an authorization control and not a truth detector: a trusted key signing a false fact returns
the false fact at full weight, and a correct fact signed by an unknown key is dropped. The earlier
version of that test lived in a gitignored directory and reached into a sibling checkout, so nobody
outside this machine could run it — for a test whose whole point is "check us", that made it worthless.

First release published through GitHub Actions with PyPI Trusted Publishing, so the wheel carries a
signed attestation binding it to this repository, this workflow and this commit.

## 1.24.3

**BUGFIX (regression from 1.24.0): double deletion receipts.** `forget_subject()` and `forget_pii()`
call `forget()` and then emitted their own tombstones. Once `forget()` started emitting in 1.24.0 that
produced **two receipts per erased record** — one carrying the caller's real basis, one carrying a
generic `basis="forget"` — so an auditor saw a single deletion twice, with conflicting reasons. Both
now pass `request_id` / `basis` / `authorized_by` / `authorization` through `forget()` and emit once.

**New: `governance_audit.py`.** Attacks the claim "tell it to forget everything about a subject and it
can prove it" across three scenarios x three repeats: erasure through `derived_from` lineage, absence
from records, from recall under several phrasings, and from the BYTES of every file including sidecars;
exactly one receipt per record carrying the caller's basis; tamper detection; survival across a reload;
unrelated records intact; identical end state every run. `GOV_FALSIFY=1` skips the erasure and 7 of 11
checks must fail — a test that cannot fail is a demo.

That audit is what caught the double-receipt regression, and only after its own first version was
tightened: it asserted "at least one receipt per record", which passed the bug.

## 1.24.2

**Docs only: the landing page is readable again.** The README had grown to 124 KB / 1587 lines — ten
times mem0's — with a 600-line API reference and a 300-line integration catalogue sitting between the
pitch and the proof. Nothing was deleted: those blocks moved verbatim to `docs/API.md`,
`docs/INTEGRATIONS.md` and `docs/SECOND_BRAIN.md`, leaving pointers. README is now 31 KB.

Also fixes stale version strings that had been shipping for months: the header still said v1.12.1, the
CLI section v1.12.4, and `server.json` — the manifest the official MCP registry reads — was pinned at
1.12.2 while the live registry entry still advertised **0.7.19** and pointed at the wrong repository.

## 1.24.1

**Docs only.** `claims_audit.py` is now the first thing the README offers: one command downloads the
published wheel and checks every claim on the page against that artifact, printing raw evidence per
claim. Claims about other systems are listed separately and never counted as passing.

Adds the measured write cost from the MemOps run (600-730 s of LLM extraction per scenario for an
LLM-on-write pipeline against zero model calls here) **together with the finding that answer accuracy
was statistically indistinguishable** — the honest claim is same answers at no write-time model cost,
not better answers.

## 1.24.0

**`forget()` now emits a deletion receipt, like every other erasure path.** Previously only
`forget_subject()` and `forget_pii()` wrote a hash-chained tombstone. A record removed with plain
`forget(ids=…, where=…)` was therefore deleted correctly — gone from the store and from the bytes on disk —
but *unaccounted for*: `verify_writes()` found a write receipt whose record no longer existed, with nothing
explaining the absence, and reported `deleted out-of-band`, which is precisely the signature of someone
editing the store behind its back. The store flagged its own legitimate API call as tampering.

`forget()` takes optional `request_id=` and `basis=`, both committed inside the tombstone hash, and returns
`tombstones` alongside `forgotten` / `ids` / `scrubbed_links`. Regression probe:
`probes/forget_emits_tombstone_probe.py`.

Found by installing the published wheel into a clean room and testing the README's own claim — the record
was gone, the bytes were gone, and the receipt count was zero.

**Probe fix (was reporting FAILED against correct code):** `trusted_only_poison_defense_probe` still
asserted the pre-1.19.0 fail-OPEN behaviour of `recall(trusted_only=True)`, which 1.19.0 deliberately
reversed. The code was right and the test was wrong, so the suite carried a permanent red — the kind that
teaches you to stop reading red.

## 1.23.1

**BUGFIX (silent data loss): `regex_extractor` minted keys from non-referring subjects.** On natural
conversational prose the copula patterns fire on pronouns, expletives and interrogatives — "It is
important to ...", "There is a growing ...", "These are just a few ...", "What is ...?" — producing the
keys `it`, `there`, `these`, `what`. Those keys collide across completely unrelated sentences, and keyed
supersession then RETIRES the earlier record, hiding it from recall. Measured on a real conversational
corpus (the MemOps dataset, arXiv 2607.12893) before the fix: **103 supersessions in one 3.7k-sentence
transcript, 83% of them driven by such a key** — a universal-basic-income sentence was retired because a
London-landmark sentence shared the subject `what`. The README advertises this extractor precisely so
"supersession engages over free text", so the exposure was real.

Fix: a subject that IS, or ENDS IN, a non-referring word yields no key, which is the extractor's already
documented fallback (return None -> plain append). Nothing that produced a key before loses one:
`my zip code`, `my manager`, `my current title`, `alice::email`, `france::capital`, `api rate limit` all
still key, and a real correction still supersedes. Spurious supersessions on the same corpus drop
**103 -> 18, 74 -> 13, 71 -> 17**.

Why no probe caught it: every existing extractor probe fed clean declarative statements. New regression
probe `probes/extractor_nonreferring_subject_probe.py` (16/16) ingests the failing shapes end to end.
Suite 148.

## 1.23.0

**Read-time conflict resolver: `recall(resolve_conflicts=True)` (default OFF → byte-identical legacy).**
The write-time guards (keyed supersession, echo_guard) cannot reach an UN-KEYED re-assertion of a retired
value — it lands as an independent record, embeds near-identically to the correction, and can out-rank it
(our own 1.21.0 validation demonstrated the failure; the mechanism matches the stale-serve findings in
arXiv 2606.01435, whose read-time deterministic resolution reports +10.8 pts single-hop). The resolver
clusters near-duplicate same-subject candidates in the top pool (token-Jaccard ≥ 0.6 or identical
normalized text) and resolves each cluster by **value birth**: a value's timestamp is its EARLIEST
assertion anywhere in the store, superseded rows included — so restating an old value never refreshes it
(the echo keeps its old birth and loses), while a genuinely new value wins as the newest birth. Losers are
demoted below the kept pool (backfilled, not hidden); the surviving hit carries `resolved_over: [ids]`.
Deterministic, zero-LLM, read-only. Documented limit (same as echo_guard): a deliberate un-keyed reversal
to an older value reads as an echo — use keys + `reaffirm=True` for authoritative reversals.

MCP: the `recall` tool takes `resolve_conflicts`, or set `INSPEXIMUS_READ_RESOLVER=1` server-wide.

Receipts: `probes/read_conflict_resolver_probe.py` (9/9 — incl. proof the failure EXISTS without the flag,
honest-update wins, keyed-superseded birth inheritance, no false clustering across subjects, determinism);
LoCoMo regression with the resolver ON is IDENTICAL to baseline on every k (0.397/0.582/0.668/0.750/0.839,
n=1536 — no conflicts to resolve there, and no damage from clustering). Suite 148.

## 1.22.1

**Measurement correction propagated to the shipped text (no code change).** The `Inspeximus` docstring and the README
still cited the 1.15.0 `recall_any@1 0.19 → 0.29` delta, which the 1.15.0 CHANGELOG correction had already
declared contaminated by the recall-reinforcement confound. Both now carry the clean, reinforcement-controlled
number (recall_any@1 **0.397** with nomic prefixes, LoCoMo n=1536) and point to the correction. The correction
note itself gained a paired-bootstrap re-verification (5000 resamples, fixed seed, Bonferroni across the 5 k's):
vs a raw-cosine baseline over the same embeddings, @1 is a statistical tie, k=3/k=5 are small Bonferroni-surviving
wins (Δ +0.023 / +0.032), @10/@25 positive but not significant. Receipts:
`agora_output/lab/locomo_recall_clean_reinforce.result.json` + `locomo_reinforce_flag_fair.result.json`
(both re-run 2026-07-19 evening and reproduced exactly — the pipeline is deterministic).

## 1.22.0

**MCP: the hydration-witness primitives are now tools.** `witness`, `verify_witness`, and `index_coherence`
are exposed over the MCP server, so any Claude/Cursor/agent client can pin an answer to the store revision it
was derived from ("this answer reflects store state as of revision X"), check later whether that answer
predates a change, and ask whether the derived semantic index agrees with the store — all deterministic,
zero-LLM, read-only (witness/verify) exactly as in the 1.21.0 core. Smoke-tested end to end through the MCP
module (witness → verify true on unchanged → false after a write; coherence report fields present).

## 1.21.0

**Hydration witness: `witness()` / `verify_witness()` / `state_digest()`.** A compact, deterministic receipt
of the store state an answer was derived from — "this answer reflects store state as of revision X".
`state_digest()` is an order-independent SHA-256 over exactly what retrieval can serve (id, status, ts, key,
tenant, content hash), so any write, supersession, revert, erasure, or out-of-band edit changes it;
`verify_witness()` later says whether the answer predates a change. With `receipts=True` the witness also
carries the write-receipt chain tip, anchoring the pinned state to the tamper-evident write history. Honest
scope: the witness pins THIS store and its view of its index inputs; it cannot attest external caches or
copies it never saw. Motivated by the shared-team-memory discussion (anthropics/claude-code#38536): governed,
git-backed stores are still read through a derived index, and provenance receipts need a cheap thing to pin to.

**Index coherence: `index_coherence()`.** Deterministic, read-only answer to "does the derived semantic index
agree with the store?" — reports active text records missing a vector while an embedder is configured (index
behind store), persisted-vector recipe vs the current `embed_id` (the sidecar guard's view), and the
`persist_vectors` regime. This operationalizes the exact bug class behind the 1.15–1.18 realign fixes as a
user-callable check instead of tribal knowledge.

**README honesty pass (from the same adversarial review):** the `echo_guard` bullet now states its real scope
(keyed or extractor-derived assertions — a free-text write nothing keys is an independent record), and the
org-wide erasure receipt heading no longer says "your WHOLE stack": the manifest is an auditable trail over
the stores you REGISTER, and cannot attest a copy nobody registered (unknown caches, backups, already-hydrated
contexts).

Probe: `probes/hydration_witness_probe.py` (12/12 — determinism, every retrieval-visible mutation flips the
digest, receipts-tip anchoring, lag + recipe-mismatch detection).

## 1.20.0

**Claude Code hooks are now LEXICAL by default (opt in to semantic with `INSPEXIMUS_EMBED_HOOKS=1` or config
`{"embed": {"hooks": true}}`).** The hooks run in the agent's hot path — PostToolUse after every
Edit/Write/Bash, UserPromptSubmit blocking prompt submission — and with a local GPU embedder each capture
cost one embedding call: ~2s on an idle GPU, unbounded on a busy one (this plugin's own dogfood machine runs
a 21GB LLM on the same card). The capture is deterministic and keyed either way, and on a coding store the
embedder buys little (its bulk is `ran: ...` mechanics, the least semantic content there is). Measured on the
dogfood store: 2.8s -> 0.65s per hook, zero GPU traffic. Semantic recall in the MCP server, CLI and library
is unchanged — this narrows only the hook hot path.

Two core guarantees added so a lexical open is a pure bystander on a semantic store: the plugin always opens
with `persist_vectors=True` (a vec-less open would otherwise strip every persisted vector on its first save),
and `_save()` leaves the `.embedid` sidecar untouched when `embed_id` is None (blanking it would make the
next semantic open see `''->recipe` and realign for nothing). Probe gains regressions 9/9b.

## 1.19.0

Security and correctness pass over everything 1.16.0–1.18.0 shipped, from an audit of the whole unreleased
range. Three of these contradicted guarantees this CHANGELOG had already made.

**SECURITY — stored XSS in the memory browser.** `render_html()` inlines the rows into an inline `<script>`
via `json.dumps`, which does not escape `<` `>` `&`. A memory containing `</script>` therefore closed the
element and everything after it was parsed as live HTML in the opened `file://` document; the JS-side `esc()`
never ran, because the breakout happens at parse time. Memory text is exactly what agents ingest from tools,
web pages and MCP callers, so this was reachable through ordinary use. Now escaped as `\uXXXX` — transport
only, text round-trips byte-identical.

**SECURITY — `route()` could hard-delete on a default store, by content alone.** The routed DELETE gated on
`_revert_authorized()`, which returns True when neither `revert_authority` nor `revert_pubkey` is configured
(the "legacy" rule — safe for revert, which only moves along the version graph). On a default store that let
`route("forget that address")` reach `forget()` and irreversibly destroy every active record for the key,
directly contradicting 1.17.0's claim that DELETE is "capability-gated: content alone can't destroy memory".
A routed delete now requires an authority to be **configured**, then satisfied; otherwise it returns
`authorization_required` and points at out-of-band `forget()`/`forget_subject()`.

**Delete no longer pre-empts corrections and reverts.** The delete vocabulary overlaps both, and was tested
first, so `route("drop the beta flag; region is now us-east")` (a correction) and `route("undo that, it is no
longer valid")` (a revert) were swallowed as deletes and their writes never happened. DELETE now requires the
utterance to carry no value and no revert marker.

**BEHAVIOR REVERSAL — `recall(trusted_only=True)` now fails CLOSED.** It previously skipped the filter
entirely when no `trust_seeds` were configured and returned the whole untrusted pool — deliberate ("fail-open,
not empty") but wrong for a security flag: it returned exactly the poisoned records the caller asked to
exclude, indistinguishable from a successful trusted recall. With no trust root nothing can be anchored to it,
so the honest answer is no trusted memories. Configure `trust_seeds` to get hits.

**Cross-tenant leak through the newer surface.** `_TenantView` forwards unlisted methods to the parent, where
they run parent-bound (`self.tenant` = None): `remember_decision()` and `distill_and_remember()` wrote records
with **no tenant stamp** (visible to every other view), `graph()`/`subgraph()` returned **every** tenant's
edges, and `route()`'s delete id-selection matched the wrong tenant. All five are now rebound.

**MMR was cubic in the pool.** The greedy loop selected the entire pool (only the first `k` survive) and
recomputed every pairwise cosine uncached: ~p³/6 similarity calls. Fine at the default p=50, ~1.3M at k=50,
~1e9 for a caller passing `rerank_pool=2000` — an effective hang. Now bounded to `k` and memoized.

**`reembed()` + `inspeximus reembed`.** The explicit counterpart to 1.18.0's bounded embed-recipe guard: when a
recipe change finds more than `INSPEXIMUS_REALIGN_MAX` stale vectors the guard drops them (lexical fallback) rather
than making every open pay a network call per record. This is how you deliberately rebuild that space —
foreground, with a count, `--batch`-able — instead of implicitly on a load path. `route()`'s NOOP now also
carries an explicit `"id": None`, so callers reading `["id"]` no longer `KeyError` on a duplicate write.

## 1.18.0

**Fix: the embed-recipe guard could re-embed the whole store on EVERY open.** With `persist_vectors=True` and a
stored recipe differing from the current `embed_id`, the realignment (a) re-embedded every record rather than only
the vector-bearing ones, and (b) recorded the new recipe only inside `_save()` — so any caller that never saves (a
read-only `recall()`, a session digest, a short-lived hook process) redid the entire realignment on every open.
Together that turned a one-time migration into a permanent per-open network storm: a 1214-record store issued 1214
embedding calls *per open*, forever — which froze a Claude Code session through the `inspeximus.claude_code` hooks
(~44 min per hook; the hook blocks prompt submission). The guard now realigns only vector-bearing records, persists
the realignment exactly once (vectors and sidecar together — never the sidecar alone, which would label old vectors
with a new recipe), and is bounded by `INSPEXIMUS_REALIGN_MAX` (default 256): past the cap it drops the stale vectors
(those records degrade to lexical recall and are re-embedded on their next write) instead of stalling the load
path. Measured on the affected store: 44 min -> 17 s once, then 2.6 s per open. Probe
`embed_recipe_migration_guard_probe.py` gains regressions 5/5b/6/6b/6c/7/7b.

**Fix: `inspeximus.claude_code._make_embedder` returned a bare `None` when unconfigured** while its caller unpacked three
values — a `TypeError` that the hook's fail-open swallowed, so in any project without `.inspeximus/config.json` the
plugin silently captured nothing at all.

**Deterministic knowledge graph (`graph()` + `subgraph()`).** Every keyed `(subject::relation, object)` memory is an edge subject-[relation]->object; `graph()` exports nodes+edges and `subgraph(entity, hops)` does multi-hop traversal — the graph-memory view mem0/Zep/cognee ship, but DERIVED deterministically from inspeximus's supersession triples (no LLM entity-extraction, no graph DB). Superseded facts drop out (graph = current truth). Probe `graph_layer_probe.py` (5 checks incl. supersession-drop + 2-hop).

## 1.17.0

**Named reranker menu (`recall(rerank_by=...)`).** A discoverable set of deterministic, zero-LLM reorderings of the
top relevant pool — `recency` (newest by event-time first), `value` (highest accrued importance), `reliability`
(best Beta good/bad track record — was-it-right, not just similar), `relevance` (explicit no-op). Complements the
`mmr=` diversity knob and the `rerank=` cross-encoder hook; exposed on the MCP recall tool. Probe
`rerank_menu_probe.py` (3 strategies engineered to pick 3 different top-1s, proving the menu discriminates).

**One-call `route()` now emits mem0-parity ADD / UPDATE / DELETE / NOOP.** The single-call write router decides the
ledger op deterministically (zero-LLM): a new keyed fact -> ADD, a new value for a key -> UPDATE (keyed
supersession), re-stating the current value -> **NOOP** (skips the duplicate write — directly attacks unbounded
growth), a deletion utterance ("forget that", "no longer true") -> **DELETE** (capability-gated: content alone can't
destroy memory, preserving the channel-separation moat), and a revert utterance -> REVERT. Each return carries an
`event` field so mem0's add()-reconcile mental model maps 1:1 — a deterministic drop-in. Probe
`route_add_update_delete_noop_probe.py` (6 checks incl. NOOP-writes-nothing + delete-moat).

**Memory hierarchy — `user_id` / `agent_id` / `session_id` scoping (mem0/Letta-style).** `remember(...)` stamps a
memory's scope; `recall(...)` filters by hierarchical visibility: a session query sees that session's memories PLUS
the user/agent-level shared ones, but never a peer session's; users are isolated from each other; a user-only query
sees all that user's own memories; unscoped = global. Deterministic, in-core (on top of the existing hard `tenant`
isolation + soft `scope`); exposed on the MCP `remember`/`recall` tools too. Probe `memory_hierarchy_probe.py`
(4 checks incl. peer-session + cross-user isolation).

## 1.16.0

**LangGraph checkpointer (`InspeximusSaver`).** The thread-state half of LangGraph memory (InspeximusStore was the long-term
half): a `BaseCheckpointSaver` so a graph can persist + resume, same contract as SqliteSaver/PostgresSaver but in a
single zero-dependency inspeximus file (no DB, no server). Checkpoints + pending writes serialized via LangGraph's own
serde, tagged so they never pollute recall. Sync + async; `inspeximus.integrations.langgraph.InspeximusSaver`.

**Offline memory browser (`inspeximus.browser` + `inspeximus browse`).** Renders the store to a SINGLE self-contained HTML
file (all data inlined, vanilla JS, inline CSS — no server, no build, works offline) with client-side search +
filters and a summary header (counts, cohorts, contradictions); shows active vs superseded so you can SEE
corrections. Read-only by design. The console every competitor ships and inspeximus lacked.

**Rich MCP server — resources + prompts + governance/integrity tools.** The MCP server was tools-only (19/60
methods). Now exposes the 3 MCP primitives: +8 governance/integrity tools (forget_subject, governance_report,
verify_writes, pii_report, forget_pii, influence_gate_report, why_recalled, supersession_report), 3 resources
(`inspeximus://digest`, `://contradictions`, `://governance`) + a `inspeximus://memory/{id}` template, and 3 prompts
(recall_before_answer, consolidate_session, review_contradictions); `recall` now takes `mmr` + `trusted_only`.
27 tools total.

**Fatter CLI (6 → 13 commands)** + **`default_distiller`.** New: `browse`, `decision`, `contradictions`,
`governance`, `consolidate`, `why`, `distill`. `inspeximus.default_distiller()` is a zero-dep urllib chat caller (any
OpenAI-compatible endpoint via `INSPEXIMUS_LLM_URL`) so `distill_and_remember` works out of the box — opt-in (the core
stays zero-LLM), raises a clear error if no endpoint is set.

**`recall(mmr=λ)` — result-level diversity / dedup.** A top-k that isn't dominated by near-identical memories, via
greedy Maximal Marginal Relevance (Carbonell & Goldstein 1998 — a standard IR technique, not novel here). The value
is that it is **in-core, zero-LLM, and works with OR without an embedder** (diversity by record vectors, falling back
to token-Jaccard so lexical recall dedups too) — the "unbounded redundant results" lever that mem0/Hindsight
explicitly declined. `next = argmax[λ·rel − (1−λ)·max cos(d, chosen)]`; `rel` is the composite score min-max
normalized over the reranked pool. `mmr=1.0` is a no-op (pure relevance); lower = more diverse. Default off (no
behavior change); composes after the `rerank` hook. Probe `mmr_result_dedup_probe.py` (5 checks incl. plain-returns-
duplicates so the test can fail).

## 1.15.0

**Asymmetric query embedder (`embed_query`) — a recall correctness fix for nomic-embed-text.** nomic-embed-text is
trained to prefix stored text with `search_document: ` and queries with `search_query: ` (Nomic's model card;
asymmetric prefixing is standard retrieval practice, cf. E5's `passage:`/`query:`). inspeximus was omitting the prefixes,
which is simply using the model wrong. `Inspeximus(embed=…, embed_query=…)` now lets the recall QUERY be embedded
differently from stored TEXT (defaults to `embed`, so existing setups are byte-identical); the MCP server
auto-applies the nomic prefixes when `INSPEXIMUS_EMBED_MODEL` contains `nomic` (opt out with `INSPEXIMUS_NOMIC_PREFIX=0`).
Impact, measured against our OWN prior (unprefixed) behavior on one LoCoMo config (`agora`'s `locomo_prefix_scale.py`,
deterministic, all 10 conversations, n=1536): **recall_any@1 0.193 → 0.294, @25 0.754 → 0.807**. Scope, stated
plainly: this is a self-comparison bug-fix on a single dataset/embedder; `recall_any` (≥1 gold turn retrieved) is a
retrieval upper bound, not end-to-end QA, and multi-hop full-recall barely moves. We make no cross-system claim here.

> **Correction (post-release, self-comparison only).** The `0.193 → 0.294` figures above were measured with a second
> defect still active: `recall()` reinforces each hit's value, and sweeping many queries against one store makes the
> ranking order-dependent (later queries see values shifted by earlier hits) — a confound that depresses benchmark
> recall_any by up to ~0.10 at low k. With reinforcement disabled (a new `recall(reinforce=False)` kwarg returns a
> non-mutating read — no value bump, decay-clock reset, or graduation; default `reinforce=True` is unchanged),
> re-measured on the same LoCoMo config against our OWN plain-cosine baseline over the same nomic embeddings, inspeximus is
> **indistinguishable from that cosine baseline within measurement noise** (recall_any@1 0.397 vs 0.390; single run,
> n≈1536, no confidence interval — read as "no measurable gap", not a proven win).
> *Re-verified 2026-07-19 with a paired bootstrap (n=1536, 5000 resamples, fixed seed, Bonferroni across the 5 k's
> tested): @1 remains statistically indistinguishable (Δ +0.007, 99% CI [−0.009, +0.024]); at k=3 and k=5 inspeximus's
> native ranking is a small but Bonferroni-surviving WIN over raw cosine (Δ +0.023, 99% CI [+0.005, +0.044] and
> Δ +0.032, 99% CI [+0.014, +0.051]); @10/@25 positive but not significant after correction. Same scope as above:
> one dataset, one embedder, retrieval upper bound, self-built baseline — no cross-system claim.* So the integrity core adds no
> detectable recall penalty *in this eval mode*; under the default reinforced path the number is lower (0.294), so that
> statement is scoped to `reinforce=False`. The two fixes (prefixes + reinforcement) substantially account for the
> earlier gap. We make no claim about any external system's retrieval — none was run here.

**Migration guard (persisted vectors).** Because a query and its stored vectors must live in the SAME embedding
space, changing the embed recipe (e.g. turning prefixes on) would silently mis-rank an existing `persist_vectors=True`
store. `Inspeximus(embed_id=…)` fingerprints the recipe into a `<path>.embedid` sidecar; on open with a different
`embed_id`, the persisted vectors are re-embedded once with the current embedder so the space realigns (RAM-only
default stores are unaffected). Probes: `probes/embed_query_asymmetric_probe.py`, `probes/embed_recipe_migration_guard_probe.py`. Suite 148/148.

## 1.14.0

**Compact MCP recall + progressive disclosure (standard context-economy practice, applied to inspeximus).** A memory
server that returns every internal field burns the agent's context on data it never reads. Over MCP, `recall` now
returns a **compact projection** — `{id, text, score, value, tags}` — dropping internal bookkeeping (links,
provenance, ISO stamps, relevance/reliability breakdown); `k` is hard-capped (`INSPEXIMUS_MAX_K`, default 50). **Full
text is kept by default** — snippet truncation is **opt-in** (`snippet_chars>0`), deliberately NOT the default,
because truncating a hit could cut off a corrected value that sits past the boundary and silently defeat inspeximus's
own supersession/echo-guard. Two companion tools do progressive disclosure: `get(id)` returns one full record,
`neighbors(id, k)` a bounded local expansion (excludes self). `token_report(query, k)` is a **deterministic,
no-LLM** payload-size estimate (~chars/4) comparing the compact projection to the FULL records for the **same k
hits** — the honest apples-to-apples baseline, explicitly **not** a whole-store comparison and **not** a measured
token/cost saving. None of these are novel (progressive disclosure / small-to-big retrieval are standard MCP/RAG
practice); inspeximus already never emitted embedding vectors in recall. Core library and on-disk format unchanged;
`recall(full=True)` returns complete records. Receipt: `probes/inspeximus_mcp_token_pack_probe.py` (7/7), suite 148/148.
Eighteen MCP tools total.

## 1.13.0

**Auditor-grade erasure certificate — independently verifiable, no trust in the operator.** `m.erasure_certificate(request_id=...)`
packages the signed deletion tombstones (full hash-chain), the request-scoped erased ids, the receipt public
key, and a CT-style anchor into ONE portable, content-free JSON document. A third party runs the new module
function `verify_erasure_certificate(cert, store_path=...)` — WITHOUT the private key and WITHOUT trusting the
operator — and gets a machine-checkable verdict: the tombstone chain re-derives, every Ed25519 signature
verifies (pinnable to an expected pubkey), the anchor commits to the chain tip, AND every erased id is genuinely
ABSENT from inspeximus's store records (the value is deleted, not soft-deleted or kept in a history table by design
as most libraries do). Tampering a tombstone, faking an "erased" id that is still present, or pinning the wrong
key all flip the verdict to INVALID. This is the erasure primitive built for a right-to-erasure demand (GDPR
Art.17) with an Art.30-style auditable record — a governance capability most agent-memory libraries do not
expose. Honest scope stays in-band: it proves erasure from THIS store's records (the ACT, not the content;
witness the anchor externally for an operator-adversarial audit) — it is NOT secure at-rest erasure against
raw-disk/backup forensics (a plaintext store of any library leaves bytes in free space/backups → use an
encrypted store + `shred()`, NIST SP 800-88 crypto-erasure) and NOT the app's own vector store/logs (register
`ErasureTarget`s for cross-store cascade). Receipts:
`probes/erasure_certificate_probe.py` (9/9) + `probes/erasure_raw_store_probe.py` (12/12).

## 1.12.4

**`inspeximus` shell CLI.** A new console command to script the memory layer from the terminal — no Python and no
MCP server needed: `inspeximus remember "..." --key k`, `inspeximus recall "..."` (current-truth, superseded values
hidden), `inspeximus revert <key>`, `inspeximus forget --key/--id/--contains`, `inspeximus list`, `inspeximus stats`. Shares the
store with `inspeximus-mcp` (`--path` / `$INSPEXIMUS_PATH` / `./inspeximus_memory.json`); `--json` for scripting; lexical by
default, semantic when `$INSPEXIMUS_EMBED_URL` is set. Zero dependencies. Receipt: `probes/inspeximus_cli_probe.py`
(6/6).

## 1.12.3

**Optional reranker hook: `recall(rerank=callable, rerank_pool=N)`.** A retrieve-then-rerank extension point:
`rerank(query, records) -> list[float]` (one relevance score per record, higher=better) reorders the top
candidates before truncation to `k`. Model-agnostic (inspeximus imports no model) and moat-safe: no model runs
unless the caller supplies one, the WRITE path is untouched, default `None` = zero behavior change, and it
fails open (a broken or wrong-length reranker keeps the pre-rerank order). Honest scope: the lift is only as
good as the reranker — a model-READER reranker is the measured multi-hop lever (LoCoMo ~0.30->~0.48), whereas a
generic query-relevance cross-encoder does NOT help multi-hop (measured: it hurts, because 2nd-hop evidence
isn't directly query-relevant). Receipt: `probes/inspeximus_rerank_hook_probe.py` (5/5).

## 1.12.2

**Opt-out "a newer version is available" check.** When inspeximus runs (Claude Code `SessionStart`, or the MCP
server starting), it checks PyPI at most once per 24h and prints a single ASCII line if the installed version
is behind — the standard pip/npm/gh courtesy, so users who installed weeks ago learn about new integrity
features instead of silently staying on an old release. Fail-open (offline = silent), never blocks, and the
MCP server routes it to stderr so the stdio JSON-RPC channel is untouched. Silence with `INSPEXIMUS_NO_UPDATE_CHECK=1`.

## 1.12.1

**Claude Code plugin: a one-time, opt-out star nudge.** After inspeximus has actually been useful — 25 captured
writes in a project — the plugin prints a single, warm request to star the repo on the next prompt, then never
again. ASCII-only (safe on non-UTF-8 consoles), never blocks, and silenced anytime with `INSPEXIMUS_NO_NUDGE=1`.
Tied to a moment of demonstrated value, not to install time (which wheels can't run anyway).

## 1.12.0

Additive only, no breaking changes.

**CrewAI integration.** `inspeximus.integrations.crewai` ships `InspeximusStorage`, a drop-in CrewAI `Storage`
(`save`/`search`/`reset`) you hand to `ExternalMemory` (or any custom-storage slot). `search()` retrieves
through inspeximus's supersession-filtered `recall()`, so a corrected fact never returns into the crew's context.
Duck-typed — CrewAI is matched structurally and never imported, so the zero-dependency core is untouched.
Opt-in extra: `pip install "inspeximus[crewai]"`. Receipt: `probes/inspeximus_crewai_adapter_probe.py` (6/6).

**Claude Code plugin: optional semantic recall.** The auto-capture plugin (`inspeximus.claude_code`) now supports
SEMANTIC recall against any OpenAI-compatible `/embeddings` endpoint (e.g. local Ollama), configured by env
(`INSPEXIMUS_EMBED_URL` / `INSPEXIMUS_EMBED_MODEL`) or a per-project `.inspeximus/config.json`. Default stays deterministic
LEXICAL (runs anywhere, no service). Writes remain verbatim, keyed and no-LLM; the embedder only builds a
retrieval index and fails open (a down endpoint degrades to lexical, never drops a capture).

**New `Inspeximus(persist_vectors=True)` option.** By default embedding vectors are a RAM-only cache stripped on
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

**LangChain integration.** `inspeximus.integrations.langchain` ships `InspeximusRetriever` (a `BaseRetriever` whose
results are supersession-filtered — a corrected fact is never retrieved back into the prompt) and
`InspeximusChatMessageHistory`. Opt-in extra: `pip install "inspeximus[langchain]"`.

**Tuned recall recipe + a measured LOCOMO number.** `examples/recall_recipe_locomo.py` shows the built-in
levers (an embedder → lexical+semantic hybrid RRF; a soft speaker/entity prefilter via `recall(prefer=...)`) that
put inspeximus in the top tier on retrieval. Measured on the full LOCOMO benchmark (n=1536), LLM-free and reproducible:
retrieval-recall@25 = 0.783 (any evidence turn) / 0.648 (all). Run `probes/retrieval_recall_locomo.py`.
*(CORRECTED 2026-07-27: that path was `probes/...` and the file was not in the repository at all
— cited as a receipt for two years and committed only after CHANGELOG.md was brought into the probe-citation
guard. "Reproducible" holds only if you have the LoCoMo dataset, which we cannot redistribute.)*

## 1.10.0

Claude Code integration: deterministic, no-LLM auto-capture of coding-agent memory. `python -m
inspeximus.claude_code --install` writes lifecycle hooks (`PostToolUse` / `UserPromptSubmit` / `SessionStart`) into
`.claude/settings.json`. `PostToolUse` captures Edit/Write/MultiEdit/Bash events into a deterministic keyed
store (`file:<path>`), so a corrected fact supersedes the stale one and `echo_guard` blocks its resurrection;
`UserPromptSubmit` injects the current-state memory; `SessionStart` digests the project's known files. No LLM on
the write path (unlike the LLM-summarizing coding memories, which drop facts, leak on erasure, and are
non-reproducible). Fail-open hooks, local JSON store at `.inspeximus/coding_memory.json`, `--uninstall` to remove.

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
  (default 0.7, `Inspeximus.fork_below`) supersedes as before; **below it the write forks a CANDIDATE**
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
our cell, inspeximus included) — the store alone cannot fix that, because it cannot see infrastructure it was never
told about. 1.8.0 wires the fan-out into the erasure path:

- **`register_erasure_target(target)`** — register app-side stores (the app's vector index, embedding/response
  caches, retrieval logs) implementing the two-method `ErasureTarget` protocol (`erase(subject)`,
  `still_recoverable(subject, values)`). Targets are live client adapters, so they are RAM-only: re-register on
  process start.
- **`forget_subject(...)` cascades**: with targets registered it erases the store (as before), then every
  registered target, re-checks residual recoverability per target, and returns a hash-chained **`manifest`**
  in its result — honest by construction: `complete` is True only if EVERY store (inspeximus itself included, as the
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

- **`Inspeximus(path=..., encrypt_key=...)`** (raw 32-byte key from `new_encryption_key()`) or **`encrypt_passphrase=...`**
  (scrypt-stretched) encrypts the store at rest with **AES-256-GCM** (AEAD: confidentiality + tamper-detection),
  a fresh random 96-bit nonce per save, file layout `MAGIC(5)+salt(16)+nonce(12)+ciphertext` with the header
  authenticated as AAD. Opt-in, default OFF → byte-identical plaintext-JSON legacy. inspeximus never persists the
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

- **Tenant isolation, bound to the store (not a per-call arg).** `Inspeximus(tenant="acme")` binds a store to one
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
inspeximus keeps ZERO external dependencies (no qdrant/psycopg/boto3 import).

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
  Motivation is measured (inspeximus lab, ToolEmu 330 tools, 2 labelers): tool reversibility is ~93% decidable from
  the signature (Cohen's κ=0.82); the ~7% undecidable residual is exactly the universal-executor class, whose
  realized harm-reach is environment-conditional (isolated executor ~0% external, networked ~0.66). Honest
  bound: the detector is a heuristic and `contained` is a caller assertion inspeximus cannot verify — it forces the
  declaration, it does not enforce the sandbox. Credits the reversibility×scope grid of arXiv:2607.07474.

## 1.1.0

Security hardening from the first internal security pass (see SECURITY.md). Both additions are OPT-IN; the
default behaviour is byte-identical to 1.0.0 (verified by tests).

- **`Inspeximus(max_text=N)`** — availability guard: `remember()` truncates a single record's text to N chars and
  stamps `meta["truncated_from"]`, so one runaway/malicious write can't exhaust memory. Default `None` =
  unbounded (legacy).
- **`verify_writes(warn_unpinned=True)`** — surfaces the self-referential-pubkey footgun: when signatures are
  present but no `expected_pubkey` is pinned, it reports a problem (a store-rewriter can swap sig+key and still
  pass). Default `False` = legacy. `governance_report()` now also states `proof.signature_authenticity`
  ("pinned to expected_pubkey" vs "self-referential — pin expected_pubkey or witness anchor() externally").

## 1.0.0

First stable release. The library matured over the 0.4–0.7 line into a real, shipped product; 1.0.0 marks a
**stable public API** (`inspeximus.__all__`), a **runnable test suite** (`tests/`, CI on every push), a documented
changelog, and the governance/erasure tooling consolidated. No functional change from 0.7.22 — this release is
about production-readiness and API stability, not new features.

- **Public API frozen** in `inspeximus.__all__`: `Inspeximus`, `new_receipt_keypair`, `new_source_keypair`, `sign_revert`,
  `sign_erasure`, `erasure_challenge`, `attest`. Governance/erasure tools live in submodules
  `inspeximus.deletion_manifest` and `inspeximus.erasure_auditor`.
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
