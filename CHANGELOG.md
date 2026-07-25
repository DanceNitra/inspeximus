# Changelog

All notable changes to inspeximus (`inspeximus`). Format loosely follows Keep a Changelog; versioning is semver
(MAJOR = stable/breaking, MINOR = features, PATCH = fixes).

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
fixed: probe paths pointed at `inspeximus/probes/` (2 files) instead of `probes/`; `claims_audit.py`
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
real 27,290-record deployment (1.49.0); inferring it from content was withdrawn at **precision 0.06-0.23**
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
| topically **diverse** store, same-topic negatives | **precision 0.06-0.23**, recall 0.03-0.22 — at its best setting it stamps **43 wrong parents for every 13 right ones** |
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
`inspeximus/probes/erasure_certificate_probe.py` (9/9) + `inspeximus/probes/erasure_raw_store_probe.py` (12/12).

## 1.12.4

**`inspeximus` shell CLI.** A new console command to script the memory layer from the terminal — no Python and no
MCP server needed: `inspeximus remember "..." --key k`, `inspeximus recall "..."` (current-truth, superseded values
hidden), `inspeximus revert <key>`, `inspeximus forget --key/--id/--contains`, `inspeximus list`, `inspeximus stats`. Shares the
store with `inspeximus-mcp` (`--path` / `$INSPEXIMUS_PATH` / `./inspeximus_memory.json`); `--json` for scripting; lexical by
default, semantic when `$INSPEXIMUS_EMBED_URL` is set. Zero dependencies. Receipt: `inspeximus/probes/inspeximus_cli_probe.py`
(6/6).

## 1.12.3

**Optional reranker hook: `recall(rerank=callable, rerank_pool=N)`.** A retrieve-then-rerank extension point:
`rerank(query, records) -> list[float]` (one relevance score per record, higher=better) reorders the top
candidates before truncation to `k`. Model-agnostic (inspeximus imports no model) and moat-safe: no model runs
unless the caller supplies one, the WRITE path is untouched, default `None` = zero behavior change, and it
fails open (a broken or wrong-length reranker keeps the pre-rerank order). Honest scope: the lift is only as
good as the reranker — a model-READER reranker is the measured multi-hop lever (LoCoMo ~0.30->~0.48), whereas a
generic query-relevance cross-encoder does NOT help multi-hop (measured: it hurts, because 2nd-hop evidence
isn't directly query-relevant). Receipt: `inspeximus/probes/inspeximus_rerank_hook_probe.py` (5/5).

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
Opt-in extra: `pip install "inspeximus[crewai]"`. Receipt: `inspeximus/probes/inspeximus_crewai_adapter_probe.py` (6/6).

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

**Tuned recall recipe + a measured LOCOMO number.** `inspeximus/examples/recall_recipe_locomo.py` shows the built-in
levers (an embedder → lexical+semantic hybrid RRF; a soft speaker/entity prefilter via `recall(prefer=...)`) that
put inspeximus in the top tier on retrieval. Measured on the full LOCOMO benchmark (n=1536), LLM-free and reproducible:
retrieval-recall@25 = 0.783 (any evidence turn) / 0.648 (all). Run `inspeximus/probes/retrieval_recall_locomo.py`.

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
