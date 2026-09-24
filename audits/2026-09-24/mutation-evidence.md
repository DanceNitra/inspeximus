# Mutation testing of the evidence modules

2026-09-24 · source commit `21b6cdd` (release 3.9.1) · branch `mutation-evidence`, not merged, no library code changed

This audit asks whether the tests would notice if the code that produces or verifies evidence were
wrong. The code in scope is the erasure certificate, write receipts, the audit bundle, the action
ledger and the transparency log. Each **mutant** is one small deliberate change to that code: a `<`
flipped, an `and` turned into `or`, an argument dropped. The whole suite then runs against the
mutant. A mutant that no test notices is a **survivor**, and each one is listed below with its file,
line, mutation and the reason no test caught it.

## Status: stopped early, on request

The run was stopped to save budget. Here is what finished and what did not:

| area | mutants | run | killed | timeout | survived | score | killed by new tests | survived now | score now |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Transparency log (`merkle.py`, `transparency.py`) | 829 | 829 | 632 | 9 | 188 | 77.3% | 79 | 109 | 86.9% |
| Erasure certificate (`core.py`) | 914 | 914 | 693 | 0 | 221 | 75.8% | 106 | 115 | 87.4% |
| Write receipts (`core.py`) | 1,193 | 1,193 | 895 | 1 | 297 | 75.1% | 67 | 230 | 80.7% |
| Audit bundle (`audit_bundle.py`) | 1,608 | **1,367** | 881 | 0 | 486 | 64.5% | not yet checked | 486 | 64.5% |
| Action ledger (`actions.py`) | 6,957 | **0** | | | | | | | |

How to read the columns:

- **score**: killed plus timeout, divided by all mutants run.
- **killed by new tests**: survivors that the tests on this branch now kill. Each was checked with
  `mutate_evidence.py kill`, which confirms the new test passes on the original code and fails on
  the mutant.
- **survived now**: survivors still alive, including those judged equivalent (no behaviour can tell
  them apart from the original).

A **control run** passed 140 of 140. It ran each mutant's selected tests on *unmutated* code, so no
kill above comes from a test that fails anyway.

These parts are finished:

- **Transparency log, erasure certificate and write receipts** were run in full. Every survivor has
  a hand-written triage note (`notes.json`), and the most important ones have new tests.
- **Audit bundle**: 1,367 of 1,608 mutants were run. That covers every mutant in `build_bundle`,
  `bind_content`, `_rewalk`, `_derived_store_id`, `_acl_acts`, the content-free helpers and
  `verify_bundle` up to line 974.
  - 9 new tests target the survivors that matter most (see below). They pass on the original code,
    but the run was stopped before they were checked against their mutants.
  - About 30 audit-bundle survivors have a hand-written note. The rest carry the harness's
    automatic classification, which is described under "Survivors by module".
- **Action ledger**: not run. See "Not run yet".

## What the survivors show

A survivor only counts as a finding if a real defect of the same shape would reach a release with
the tests still green. The survivors in the tables below do exactly that: they turn a refusal into
an acceptance, or they let a piece of evidence stop committing to what it claims.

The same pattern appears in every module: **a check that only ever failed alongside another check.**
Each existing tamper test broke two things at once. So when a mutant disabled either of the two
checks, the verdict did not change.

The other large groups are:

- **Messages.** Tests assert that a problem is reported, not what the message says. This is the
  largest group, and it is harmless.
- **Formats that the producer and the verifier share.** Renaming a field inside a hash preimage
  changes both sides together, so the tests stay green, but every receipt already on disk stops
  verifying. The golden 3.9.1 store fixture now pins this for write receipts.
- **Branches no test reaches.** Examples are the no-`cryptography` fallback, legacy formats and
  Windows paths.

## The survivors that matter most

Each survivor in the three tables below now has a test that fails on the mutant and passes on the
original code, checked with `mutate_evidence.py kill`.

### Transparency log (`merkle.py`, `transparency.py`)

| mutant | what the suite let through | new test |
|---|---|---|
| `merkle:174:15:5451bad7` `return False` → `True` | an **empty** consistency proof verifies for any two roots and sizes | `test_an_empty_consistency_proof_proves_nothing` |
| `merkle:181:19:1c53a5a2` `return False` → `True` | one junk hash appended to any proof returns True before either root is compared | `test_a_consistency_proof_with_a_hash_to_spare_is_refused` |
| `merkle:165:15:ab4a184d` `and` → `or` | two **different** roots at the same size verify as consistent: an equal-size fork | `test_two_different_roots_at_the_same_size_are_a_fork_not_a_consistency` |
| `merkle:162:7:3b54fd68` `or` → `and` | a rollback (`n < m`) passes with a crafted proof; `m = -1` loops forever | `test_a_rollback_is_refused_whatever_the_proof_says` |
| `merkle:106:17:f7317f2a` `m < n` → `m <= n` | an inclusion proof at index `n`, past the end of the log, verifies | `test_an_inclusion_proof_for_index_n_is_refused` |
| `merkle:122:11:aabe62bb` | a path too short for the claimed size verifies, so a Receipt's tree size is whatever the issuer wrote | `test_a_path_too_short_for_the_claimed_tree_size_is_refused` |
| `merkle:187:*` | honest proofs at 13 → 14 leaves rejected; the round-trip tests stopped at 12 | `test_consistency_round_trips_past_the_sizes_the_suite_stopped_at` |
| `transparency:336:9:*` `expected_root` dropped | `verify_registered_statement` checks the Receipt against the root it carries, not the root the caller trusts | `test_a_receipt_is_checked_against_the_root_the_caller_trusts` |
| `transparency:327:9:*` `expected_issuer` dropped | a pinned issuer is ignored | `test_a_pinned_issuer_is_enforced` |
| `transparency:363:21:567e2a2d` `and` → `or` | a statement bound to its entry reads `ok` with a failing receipt or signature | `test_the_verdict_needs_the_receipt_and_the_statement_not_only_the_binding` |
| `transparency:161:*`, `:93:*` | leaf and policy-digest encoding can change; producer and verifier move together and every issued Receipt silently stops matching | `test_the_library_re_derives_the_log_it_published` (re-derives all 281 leaves of `transparency/`) |
| `transparency:135:66:dcd484e1` `str(path)` → `str(None)` | every Transparency Service gets the **same** `store_id`, which witnesses key on | `test_two_logs_at_two_paths_have_two_identities` |

### Erasure certificate (`verify_erasure_certificate` and its producer)

| mutant | what the suite let through | new test |
|---|---|---|
| `core:1041:23:e24cd658` `chain_ok = False` → `True` | a tombstone's `memory_id` rewritten in place (hash kept, so the **signature still verifies**), anchor recomputed without a key: `valid: true` for an erasure that never happened | `test_a_tombstone_edited_in_place_fails_on_the_chain_alone` |
| `core:1038:23:543714b3` same, on the `prev` link | an unsigned chain with a middle tombstone removed verifies | `test_a_tombstone_removed_from_the_middle_fails_on_the_chain_alone` |
| `core:1055:*` `sigs_ok = False` → `True` | a tombstone carrying another tombstone's signature verifies while its own problem list says "invalid signature" (the branch had never run) | `test_a_tombstone_carrying_another_tombstones_signature_does_not_verify` |
| `core:1045:*` | without `cryptography` installed (the base package has no dependencies) signatures nothing checked read as valid | `test_without_an_ed25519_backend_signatures_are_not_reported_valid` |
| `core:1106:*`, `1113:*`, `1117:*` | the anchor's count, Merkle root and `sth_hash` checks can each be switched off; every test that broke one broke another | three `..._fails_on_its_own` tests |
| `core:1131/1139/1142:*` `ok_w = False` → `True` | a witnessed anchor that pins nothing, or is rewritten at the witnessed position, reads as witnessed | `test_a_witnessed_anchor_that_pins_nothing_is_not_a_witness`, `test_a_chain_rewritten_at_the_witnessed_position_fails` |
| `core:1248/1252:*` | the absence proof counted as bound to a store the certificate was not issued from | two `store_bound` tests |
| `core:1280:65:*` | `scope_covers` is never compared, so a certificate claiming the app's vector index verifies | `test_a_widened_scope_covers_list_is_refused` |
| `core:948:34:f91c5908` `_canon({subject, request_id})` → `_canon(None)` | the erasure challenge names nothing, so one principal's signature authorizes **every** erasure | `test_an_erasure_authorization_is_bound_to_its_subject_and_request` |
| `core:7933/7934:*` | tombstones lose their tenant stamp; a tenant's own erasures vanish from its reports (the tests only asserted the negative) | `test_a_tenant_sees_its_own_erasures_and_not_another_tenants` |
| `core:7916:67:*` | the erasure time leaves the tombstone's commitment | `test_the_time_of_an_erasure_is_committed_in_its_tombstone` |
| `core:10492:12:*` | the producer stops writing `pubkey`; see finding F1 | `test_a_tombstone_signed_by_another_key_is_refused_by_the_key_the_certificate_names` |
| `core:10483:26:a28a1dd5` | on any server not in UTC, `issued_iso` is local time with a `Z` | `test_the_issue_time_is_utc_on_a_server_that_is_not` |

### Write receipts (`_write_commit`, `_append_receipt`, `enable_receipts`, `verify_writes`, ...)

| mutant | what the suite let through | new test |
|---|---|---|
| `core:3586:12:3d0c1533` nonce → `None` | receipts of erased records become guessable again; the guessing test checks the pre-2.40 formula, which a `nonce: null` preimage also fails | `test_the_nonce_is_what_makes_an_erased_records_receipt_unguessable` |
| `core:3628-3659:*` | any key inside `status_sha256` / `time_sha256` renamed; producer and verifier move together, and every receipt already on disk would raise a tamper alarm after an upgrade | `test_a_store_written_by_3_9_1_still_verifies` (golden fixture) |
| `core:3742/3743/3745:*` | a rechained amendment loses `amends` / `amend_reason` / `ts` | `test_a_rechained_amendment_keeps_what_it_amends_why_and_when` |
| `core:4111:29:466f261b` | the backfill `genesis_root` computed over nulls; nothing re-derived it | `test_the_backfill_genesis_root_commits_to_the_records_it_covers` |
| `core:4129-4132:*` | a retirement declared at backfill without `commit` is checked against nothing | `test_a_retirement_declared_at_backfill_is_a_committed_amendment` |
| `core:3777:*`, `4116:*` | receipts that say nothing about when their write happened | `test_a_receipt_records_when_its_record_was_written`, `test_the_backfill_follows_write_order_and_keeps_each_records_time` |

### Audit bundle (`audit_bundle.py`, the part that was run)

These tests pass on the original code. They have **not yet been run against their mutants**; the
command that does this is under "Not run yet".

| mutant | what the suite let through | new test |
|---|---|---|
| `audit_bundle:586:16:124321ce` `threshold=` dropped | the caller's k-of-n witness threshold never reaches the check, so one co-signature satisfies "need 2". The only co-signature test used one witness and threshold 1 | `test_the_witness_threshold_is_the_one_the_caller_asked_for` |
| `audit_bundle:519:*` `expected_pubkey or _r.get("pubkey")` → `and` / key renamed | an unpinned bundle checks no signature at all, so a wrong signature passes. The bad-signature test pins the key | `test_an_unpinned_bundle_still_catches_a_signature_that_does_not_verify` |
| `audit_bundle:568:13:5c4cc7d8` | `require_signed=True` accepts signatures that nobody verified (no key pinned) | `test_require_signed_refuses_signatures_nobody_verified` |
| `audit_bundle:531:13:be36401b` `+` → `-` | a bundle with as many tombstones as writes skips the whole signature assessment, including "partially signed". Every bundle in the suite had more writes than erasures | `test_a_partly_signed_bundle_with_as_many_tombstones_as_writes_is_refused` |
| `audit_bundle:257:35:25307e01` `and` → `or` | an unbound handle claims `cross_tenant_chain`, which switches off the record-count coverage check | `test_an_unbound_handle_does_not_claim_a_cross_tenant_chain` |
| `audit_bundle:260:22:f8f08a55` | the governance section is computed against no key, so a bundle built with the wrong key reads as verified | `test_the_governance_section_is_computed_against_the_pinned_key` |
| `audit_bundle:268-270:*` | `baseline_complete` can say anything; no test read it | `test_the_bundle_says_whether_its_baseline_was_complete` |
| `audit_bundle:376:80:2ee038fb` | the default witness threshold is never exercised | `test_one_witness_meets_the_default_threshold` |
| `audit_bundle:363:7:589b7b8b` `and` → `or` | a clean `bind_content` also reports "NOT ONE of the records was found" | `test_bind_content_on_an_honest_store_reports_nothing` |

The other audit-bundle survivors are listed below under their automatic classification. By that
classification:

- 110 are message wording.
- 84 are call arguments dropped or set to None.
- 68 are output keys that no test reads back.
- 46 are on statements that no test executes.

The 50 `condition` rows and 47 `constant` rows have **not been triaged by hand**, and they may hide
more gaps of the kind in the table above. Triaging them is the first item under "Not run yet".


## Findings that are not test gaps

### F1. The unpinned certificate verifier accepts a tombstone signed with a stranger's key once `pubkey` is deleted

Found while triaging `core:10492:12:f27e8d8f`, a mutant that stops `erasure_certificate()` writing the
certificate's `pubkey` field. When `expected_pubkey` is not given, `verify_erasure_certificate`
checks each tombstone's signature against **the key that tombstone embeds**. It then compares that
key with `cert["pubkey"]`. If the certificate has no `pubkey`, that comparison is skipped
(`if pub and t.get("pubkey") and ...`). A chain whose tombstones are signed by different keys then
verifies.

Reproduction, on the unmodified library at `21b6cdd`:

1. Take any honest signed certificate.
2. Append a tombstone for a record that still exists, chained to the tip, signed with a freshly
   generated key, with that key embedded.
3. Recompute the anchor's count, tip, root and `sth_hash`, and the summary (none of this needs the
   operator's key).
4. Delete `cert["pubkey"]`.

```
with cert pubkey:    valid False  ['tombstone 1: signed by an unexpected key']
cert pubkey removed: valid True   signatures_valid True  problems []
pinned:              valid False  ['tombstone 1: signed by an unexpected key']
```

This is the mixed-key sibling of the partly-signed hole closed in 3.9.1 (F7 of the verifier-page
review): a party **without** the key extends the chain and gets "N erasure(s) attested". The
docstring says signatures are load-bearing against a non-holder of `receipt_key`; this attacker is
a non-holder. Pinning `expected_pubkey` closes it, and the CLI and docs recommend pinning.
`verify_entries` in the action ledger already refuses a chain signed by more than one key
("chain signed by N different keys"); the certificate verifier has no such rule. Suggested fix:
refuse a certificate whose signed tombstones embed more than one distinct key, or whose tombstones
embed a key while the certificate names none. Library code was not changed in this audit. The new
test `test_a_tombstone_signed_by_another_key_is_refused_by_the_key_the_certificate_names` pins the
half the producer owns (it writes `pubkey`), and it passes today.

### F2. Two tests break a staticmethod for the rest of the process

`tests/test_the_checks_can_actually_fail.py::test_a_pure_surface_that_always_reports_clean_is_scored_MISSED`
and `..._rejects_the_valid_input_is_scored_CONTROL_FAILED` each replace four `Inspeximus`
staticmethods. They restore them with `orig = {n: getattr(_core.Inspeximus, n) ...}`. On a class,
`getattr` returns the **unwrapped** function of a staticmethod, so after either test
`check_self_narration` is an ordinary method. `tests/test_mcp_surface.py::test_every_mcp_tool_can_be_called`
then fails with `TypeError: Inspeximus.check_self_narration() takes 1 positional argument but 2 were
given`.

The default xdist run rarely puts the two in one worker, so the suite looks green. It reproduces
every time with
`pytest -n 0 tests/test_the_checks_can_actually_fail.py tests/test_mcp_surface.py::test_every_mcp_tool_can_be_called`.
The mutation run found it because its control run, which runs every mutant's selection on unmutated
code, failed. Those two tests are excluded from mutant runs for that reason. Fix:
`_core.Inspeximus.__dict__[n]` or `monkeypatch.setattr`. Not changed here.


## Not run yet

This run stopped here. Each item below comes with the exact command to finish it locally.

### Setup (once)

The finished areas measured the suite **as it was at `21b6cdd`**, before the tests on this branch
existed. To keep the remaining areas comparable, they must be measured on the same code. That means
generating the mutants and the coverage map from a checkout of that commit, with this branch's
harness copied in. Run this from a checkout of the `mutation-evidence` branch:

```sh
pip install mutmut==3.8.0 coverage==7.16.1 pytest-xdist libcst
git worktree add ../inspeximus-21b6cdd 21b6cdd
cp -r audits/2026-09-24 ../inspeximus-21b6cdd/audits/
cd ../inspeximus-21b6cdd
python audits/2026-09-24/mutate_evidence.py generate          # 11,501 mutants -> $TMPDIR/inspeximus-mutation-evidence/mutants.json
python audits/2026-09-24/mutate_evidence.py covmap --workers 4 # full suite under coverage, about 5 min on 4 CPUs
cp audits/2026-09-24/results.jsonl "${TMPDIR:-/tmp}/inspeximus-mutation-evidence/results.jsonl"   # the 4,303 finished verdicts
```

Mutant ids are stable: each is built from the file, line, column and a hash of the mutation. So
`run` skips every mutant that already has a verdict in `results.jsonl` and appends only the new
ones. `run` also checks that each worktree holds the same library source the mutants were generated
from, and stops if it does not.

### 1. The rest of the audit bundle (241 mutants)

These are `verify_bundle` from line 975 to the end (35 mutants), `load_store_items` (8),
`load_store_receipts` (16) and `_cli` (182).

```sh
python audits/2026-09-24/mutate_evidence.py run --workers 4 --only '^audit_bundle$'
```

Then triage the `condition` and `constant` survivors of the whole audit bundle by hand, adding
entries to `audits/2026-09-24/notes.json`.

### 2. The action ledger: verification functions only (791 of 6,957 mutants)

Per the budget, only the functions that return a verdict are run. The report builders (registers,
`*_report`, `record_*`) are skipped.

| function | mutants |
|---|---:|
| `_verify_chain` | 328 |
| `verify_entries` | 273 |
| `ActionLedger.verify` | 103 |
| `ActionLedger.matches` | 41 |
| `verify_file` | 23 |
| `_verify_sig` | 12 |
| `_split` (the helper `verify_file` reads the file through) | 11 |

```sh
python audits/2026-09-24/mutate_evidence.py run --workers 4 \
  --only '^action_ledger/(ActionLedger\.verify|ActionLedger\.matches|verify_entries|_verify_chain|verify_file|_verify_sig|_split)$'
```

`--only` matches the mutant id, the area, or `area/function`; `area/function` was added for this
selection. The hashing primitives the verifiers call (`_canon`, `_entry_hash`, `_content_hash`,
`_sign`) are left out as well, since they build digests rather than return verdicts. Add
`|_canon|_entry_hash|_content_hash` to the group to include them (29 more).

### 3. Kill checks for the new tests written after the last kill run

These tests pass on the original code but have not yet been run against the mutants they target.
Run them from a checkout of this branch, not the `21b6cdd` worktree. `kill` builds its own worktree
at `HEAD` and copies the named test files into it.

```sh
python audits/2026-09-24/mutate_evidence.py generate    # if mutants.json is not already in $TMPDIR/inspeximus-mutation-evidence
python audits/2026-09-24/mutate_evidence.py kill --tests tests/test_evidence_survivors_audit_bundle.py \
  --json-out kill_ab.json --ids \
  audit_bundle:586:16:124321ce audit_bundle:376:80:2ee038fb \
  audit_bundle:519:14:0a76916c audit_bundle:519:33:2c537337 audit_bundle:519:40:363bc0fc audit_bundle:519:40:30ce8f36 \
  audit_bundle:568:13:5c4cc7d8 audit_bundle:531:13:be36401b audit_bundle:257:35:25307e01 audit_bundle:260:22:f8f08a55 \
  audit_bundle:363:7:589b7b8b \
  audit_bundle:268:46:4a40905a audit_bundle:268:46:04878971 audit_bundle:269:37:bf632a34 audit_bundle:269:43:be98e1dd \
  audit_bundle:269:43:3758d0e7 audit_bundle:269:49:85691568 audit_bundle:269:57:7a4265ed audit_bundle:269:64:11a5f425 \
  audit_bundle:269:64:6e659263 audit_bundle:270:68:56966c71 audit_bundle:270:68:fea69954 audit_bundle:270:68:3bc97368 \
  audit_bundle:270:83:1fbbbf16 audit_bundle:270:83:30733d62
python audits/2026-09-24/mutate_evidence.py kill --tests tests/test_evidence_survivors_write_receipts.py \
  --json-out kill_wr2.json --ids core:6549:51:57081ac5 core:4148:12:195f8dae core:4148:37:dd1d2f11 core:4148:37:b1344cc5
python audits/2026-09-24/mutate_evidence.py kill --tests tests/test_evidence_survivors_transparency_log.py \
  --json-out kill_tl2.json --ids transparency:263:57:9ee88280 transparency:284:16:93dda8d6 transparency:284:16:b5073a5e
```

Pass the new JSON files to `report --kills` together with the earlier ones to update the "now"
column of the tables below.

## How this was run

**Tool.** mutmut 3.8.0's own mutation operators (`mutmut.mutation.file_mutation.MutationVisitor`
with `mutmut.mutation.mutators.mutation_operators`), driven by `audits/2026-09-24/mutate_evidence.py`.
`mutmut run` itself was not used. It rewrites each mutated file into trampolines and puts every mutant
of that file into it; for a 17,465-line `core.py` that file does not import in useful time. Three
properties of this suite would also turn trampolines into wrong verdicts: tests read source text back,
tests drive the CLI through `subprocess` (mutmut's per-function map never sees a child process), and
mutmut skips decorated functions. So each mutant is one edit to a real file in a private git worktree,
and one pytest process runs against it.

**Operators** (mutmut's set, unchanged): number `n -> n+1`; string `"s" -> "XXsXX"`, lower-case, upper-case;
`True <-> False`; `==/!=`, `</<=`, `>/>=`, `is/is not`, `in/not in`, `and/or`, `+/-`, `*//`; `not x -> x`;
`a = b -> a = None`; `x += y -> x = y`; each call argument set to `None` and each dropped; ternary forced
down each arm; `break -> return`, `continue -> break`. Decorated functions are mutated too (mutmut skips
them only because a trampoline cannot wrap them).

**Test selection.** One full-suite run under coverage.py 7.16.1 with a context per test, including
the test's child processes (the subprocess config is re-serialised per test, and injected into the
`env=` dicts tests pass to `subprocess`). Code a test module runs at import is attributed to the file.
Each mutant then runs:

1. stage 1: the tests that execute the mutated statement, cheapest first, `-x`. A test that does not
   execute the statement runs exactly the code it ran before, so it cannot see the mutant, with two
   exceptions that stage 2 covers;
2. stage 2, only if stage 1 stays green: (a) for a mutant on a `def` line (a default argument is
   evaluated at import, under no test's context) or on a statement no test executes, every test that
   executes any line of the enclosing function (mutmut's own granularity); (b) otherwise, the rest of
   each test file that holds a stage-1 test and declares a module-, class- or session-scoped fixture
   (15 files), where one test's context builds what another test's assertions read.

A mutant is a **survivor** only if both stages pass, or if no test executes it at all. Those are
classified `unexecuted` in the tables. A test that fails or errors **kills** the mutant. A run over
its time budget is a **timeout** and counts as detected; the budget is 90 s plus 4 times the measured
duration of the selected tests.

On a sample of stage 2 (a) for statements no test executes, run for 1 in 10 of those mutants, no
mutant was killed. So the coverage map had not missed a test.

**Parallel.** 4 workers (the machine has 4 CPUs), each with its own worktree, `HOME`, `TMPDIR` and
bytecode cache. The mutated module's cached bytecode is deleted before and after every mutant: several
operators keep the file size (`+ -> -`, `lower -> upper`) and CPython's pyc check is size plus
whole-second mtime, so a stale pyc would silently run the original code.

**Excluded from every run**, decided before the run: the repository's own in-place mutation harness
tests (they edit source in place), the doc-cited probe runner, the skip census and the perf gate
(long, whole-repository, verdict independent of the mutated behaviour), and one test that needs
outbound HTTPS to the published log (refused by this sandbox's proxy). The two tests in finding F2 are excluded from mutant runs.
The baseline run under coverage had no failures.

**Control.** Before the real run, each mutant's selected tests (stages 1 and 2) ran against unmutated
code for a sample of 140 mutants spread over every function. All 140 passed. A control that failed is
how F2 was found.

**Why the reason column.** Every survivor in the transparency log, erasure certificate and write
receipts, and about 30 in the audit bundle, has a note written by hand after reading the code and
the tests (`notes.json`). The category in brackets is one of:

- `soundness`: a wrong verdict could ship.
- `format`: the producer and verifier share an encoding.
- `message`: wording only.
- `unread-output`: a field no test reads back.
- `unexecuted`: no test runs the statement.
- `equivalent`: no input can tell the mutant from the original.
- `legacy`, `platform`, `defensive`, `unreachable`, `redundant`, `performance`: branches that are
  explained case by case in the notes.

Every other row carries the harness's automatic class (`condition`, `call_arg`, `unread_field`,
`constant`, `literal`, ...) with a generic reason.


## Scope

| area | code mutated | mutants |
|---|---|---:|
| Erasure certificate | `core.py`: `erasure_challenge`, `sign_erasure`, `verify_erasure_certificate`, `Inspeximus.erasure_certificate`, `_tombstone_core`, `_emit_tombstone`, `_flush_tombstones`, `anchor` | 914 |
| Write receipts | `core.py`: `new_receipt_keypair`, `_write_commit`, `_chain_core`, `_recompute_tip`, `_receipts_disk_sig`, `_reconcile_receipts_with_disk`, `_append_receipt`, `_emit_write_receipt`, `enable_receipts`, `_persist_receipts`, `_chain_holds_tip`, `verify_writes` | 1,193 |
| Audit bundle | `audit_bundle.py`, whole module (1,367 run) | 1,608 |
| Action ledger | `actions.py`, whole module (not run; 791 verification mutants selected for the next run) | 6,957 |
| Transparency log | `transparency.py` and `merkle.py` (the RFC 6962 tree it is built on), whole modules | 829 |
| **total** | | **11,501** |

`core.py` is 17,465 lines; only the functions above were mutated. `anchor()` is listed under the
certificate because the certificate carries it, and `_chain_core` / `_recompute_tip` under receipts
because they define and re-derive the write chain. Not in scope: `cose.py`, `scitt.py`,
`checkpoint.py` and the witness modules (they consume the log rather than produce it),
`deletion_manifest.py`, `erasure_auditor.py` and `erasure_residue.py` (cross-store erasure,
not the certificate), and the MCP and CLI wrappers.

Module-level code (constants, `__all__`) is not mutated, as in mutmut. Decorated functions are,
unlike mutmut (see method).

## The new tests

68 test functions (88 cases once parametrized). All of them pass on the original code; the full run of the four files takes about 2 s with `-n 4`. Except where the list below says otherwise, each was checked with `kill`: it passes on the original code and fails on at least one survivor.


**Transparency log**: `tests/test_evidence_survivors_transparency_log.py` (21)

- `test_an_empty_consistency_proof_proves_nothing`
- `test_a_consistency_proof_with_a_hash_to_spare_is_refused`
- `test_consistency_round_trips_past_the_sizes_the_suite_stopped_at`
- `test_two_different_roots_at_the_same_size_are_a_fork_not_a_consistency`
- `test_an_inclusion_proof_for_index_n_is_refused`
- `test_a_path_too_short_for_the_claimed_tree_size_is_refused`
- `test_a_rollback_is_refused_whatever_the_proof_says`
- `test_a_receipt_is_checked_against_the_root_the_caller_trusts`
- `test_the_verdict_needs_the_receipt_and_the_statement_not_only_the_binding`
- `test_a_pinned_issuer_is_enforced`
- `test_a_registration_after_a_policy_change_records_the_new_policy`
- `test_a_subject_prefix_admits_what_it_names`
- `test_the_payload_ceiling_admits_exactly_its_own_size`
- `test_each_missing_claim_is_its_own_refusal`
- `test_a_service_without_a_signer_or_without_a_verifier_is_refused`
- `test_two_logs_at_two_paths_have_two_identities`
- `test_the_head_says_a_transparency_log_has_no_tombstones`
- `test_a_receipt_for_the_next_index_is_none_not_an_error`
- `test_the_library_re_derives_the_log_it_published`
- `test_a_leaf_is_ascii_whatever_the_statement_says`
- `test_one_witness_meets_the_default_threshold` (kill check pending)

**Erasure certificate**: `tests/test_evidence_survivors_erasure_certificate.py` (26)

- `test_a_tombstone_edited_in_place_fails_on_the_chain_alone`
- `test_a_tombstone_removed_from_the_middle_fails_on_the_chain_alone`
- `test_a_tombstone_carrying_another_tombstones_signature_does_not_verify`
- `test_a_witnessed_anchor_that_pins_nothing_is_not_a_witness`
- `test_a_chain_rewritten_at_the_witnessed_position_fails`
- `test_a_witness_of_an_empty_chain_must_hold_the_genesis_tip`
- `test_an_anchor_without_a_writes_tip_cannot_be_bound_to_a_store`
- `test_a_certificate_from_a_receiptless_store_does_not_bind_to_one_with_receipts`
- `test_an_erasure_authorization_is_bound_to_its_subject_and_request`
- `test_the_erasure_challenge_is_the_documented_message`
- `test_an_anchor_count_that_disagrees_with_the_chain_fails_on_its_own`
- `test_an_anchor_root_that_disagrees_with_the_chain_fails_on_its_own`
- `test_an_sth_hash_that_is_not_its_fields_fails_on_its_own`
- `test_checks_that_were_not_asked_for_say_none`
- `test_a_widened_scope_covers_list_is_refused`
- `test_a_pre_marker_certificate_is_scoped_by_its_claimed_requests`
- `test_tombstones_signed_through_an_external_signer_verify_against_the_pinned_key`
- `test_a_tenant_sees_its_own_erasures_and_not_another_tenants`
- `test_the_certificates_self_check_honours_the_pinned_key`
- `test_a_tenant_bound_handle_refuses_to_issue_a_certificate`
- `test_without_an_ed25519_backend_signatures_are_not_reported_valid`
- `test_the_time_of_an_erasure_is_committed_in_its_tombstone`
- `test_a_tombstone_signed_by_another_key_is_refused_by_the_key_the_certificate_names`
- `test_the_certificate_carries_the_scope_it_is_checked_against`
- `test_the_certificate_says_when_it_was_issued_in_utc`
- `test_the_issue_time_is_utc_on_a_server_that_is_not`

**Write receipts**: `tests/test_evidence_survivors_write_receipts.py` (12)

- `test_the_nonce_is_what_makes_an_erased_records_receipt_unguessable`
- `test_a_store_written_by_3_9_1_still_verifies`
- `test_a_rechained_amendment_keeps_what_it_amends_why_and_when`
- `test_a_receipt_records_when_its_record_was_written`
- `test_the_backfill_genesis_root_commits_to_the_records_it_covers`
- `test_the_backfill_follows_write_order_and_keeps_each_records_time`
- `test_enable_receipts_says_whether_it_signed`
- `test_a_second_call_reports_nothing_declared`
- `test_enabling_receipts_on_an_empty_store_creates_the_sidecar`
- `test_a_retirement_declared_at_backfill_is_a_committed_amendment`
- `test_an_unsigned_tombstone_appended_to_a_signed_store_fails_verify_writes` (kill check pending)
- `test_a_sidecar_write_that_recovers_stops_being_reported` (kill check pending)

**Audit bundle**: `tests/test_evidence_survivors_audit_bundle.py` (9)

- `test_the_witness_threshold_is_the_one_the_caller_asked_for` (kill check pending)
- `test_one_witness_meets_the_default_threshold` (kill check pending)
- `test_an_unpinned_bundle_still_catches_a_signature_that_does_not_verify` (kill check pending)
- `test_require_signed_refuses_signatures_nobody_verified` (kill check pending)
- `test_a_partly_signed_bundle_with_as_many_tombstones_as_writes_is_refused` (kill check pending)
- `test_the_bundle_says_whether_its_baseline_was_complete` (kill check pending)
- `test_an_unbound_handle_does_not_claim_a_cross_tenant_chain` (kill check pending)
- `test_the_governance_section_is_computed_against_the_pinned_key` (kill check pending)
- `test_bind_content_on_an_honest_store_reports_nothing` (kill check pending)

`tests/test_evidence_survivors_write_receipts.py::test_a_store_written_by_3_9_1_still_verifies` reads `tests/fixtures/golden_store_3.9.1/`. That store was written by 3.9.1 using `audits/2026-09-24/make_golden_store.py`, and only the public key is kept. It covers every field a write receipt commits to, so if the hashing of any of them changes, this test fails the same way every store written today would fail after an upgrade.

## Files

| file | what it is |
|---|---|
| `audits/2026-09-24/mutate_evidence.py` | the harness: `generate`, `covmap`, `run`, `report`, `kill` |
| `audits/2026-09-24/results.jsonl` | one verdict per mutant run (4,303), with the killing test and the stage |
| `audits/2026-09-24/notes.json` | the hand-written triage, one entry per mutant id |
| `audits/2026-09-24/survivors.json` | every survivor with its reason and whether a new test kills it now, from `report` |
| `audits/2026-09-24/kills/*.json` | the `kill` verdicts behind the "killed by new tests" column |
| `audits/2026-09-24/make_golden_store.py` | writes `tests/fixtures/golden_store_3.9.1/` |

`mutants.json` (7 MB) and `covmap.json` (190 MB) are not committed; `generate` and `covmap`
rebuild them, as described under "Not run yet".

To rebuild this report's tables from the committed files, after `generate`:

```sh
python audits/2026-09-24/mutate_evidence.py report --results audits/2026-09-24/results.jsonl \
  --kills audits/2026-09-24/kills/*.json --md-out tables.md
```

`audits/2026-09-24/kills/` holds the verdicts of the `kill` runs, one JSON per area, recording for each
survivor whether the new tests kill it and which test did. `kill_ec_1.json` is an earlier run of the
certificate area, and `kill_ec.json` is the later one.

## Survivors by module

Every surviving mutant from the mutants that were run, grouped by area, then file and function, in
line order. The columns are:

- **mutation**: the changed node, `old → new`.
- **why no test caught it**: `[category] reason`.
- **now**: the new test that kills the mutant, if one does.

### Erasure certificate: 221 survivors

**`inspeximus/core.py` `erasure_challenge`** (7)

| line | mutation | why no test caught it | now |
|---:|---|---|---|
| 948 | `"erase:"` → `"ERASE:"` | [format] the signed message format is only compared with itself (signer and checker call the same function); renaming a field orphans every authorization already issued, and nothing pinned the format | killed by `test_the_erasure_challenge_is_the_documented_message` |
| 948 | `"erase:"` → `"XXerase:XX"` | [format] the signed message format is only compared with itself (signer and checker call the same function); renaming a field orphans every authorization already issued, and nothing pinned the format | killed by `test_the_erasure_challenge_is_the_documented_message` |
| 948 | `_canon({"subject": subject, "request_id": request_id})` → `_canon(None)` | [soundness] the only sign_erasure test verifies the signature against erasure_challenge() computed by the same function, so a challenge that no longer names the subject or request -- one message for every erasure -- verified just as well | killed by `test_an_erasure_authorization_is_bound_to_its_subject_and_request` |
| 948 | `"subject"` → `"XXsubjectXX"` | [format] the signed message format is only compared with itself (signer and checker call the same function); renaming a field orphans every authorization already issued, and nothing pinned the format | killed by `test_the_erasure_challenge_is_the_documented_message` |
| 948 | `"subject"` → `"SUBJECT"` | [format] the signed message format is only compared with itself (signer and checker call the same function); renaming a field orphans every authorization already issued, and nothing pinned the format | killed by `test_the_erasure_challenge_is_the_documented_message` |
| 948 | `"request_id"` → `"REQUEST_ID"` | [format] the signed message format is only compared with itself (signer and checker call the same function); renaming a field orphans every authorization already issued, and nothing pinned the format | killed by `test_the_erasure_challenge_is_the_documented_message` |
| 948 | `"request_id"` → `"XXrequest_idXX"` | [format] the signed message format is only compared with itself (signer and checker call the same function); renaming a field orphans every authorization already issued, and nothing pinned the format | killed by `test_the_erasure_challenge_is_the_documented_message` |

**`inspeximus/core.py` `sign_erasure`** (3)

| line | mutation | why no test caught it | now |
|---:|---|---|---|
| 956 | `` RuntimeError("signing an erasure needs the `cryptography` p… `` → `RuntimeError(None)` | [unexecuted] the no-cryptography branch of sign_erasure: the suite always has `cryptography` installed |  |
| 956 | `` "signing an erasure needs the `cryptography` package (pip i… `` → `` "SIGNING AN ERASURE NEEDS THE `CRYPTOGRAPHY` PACKAGE (PIP I… `` | [unexecuted] the no-cryptography branch of sign_erasure: the suite always has `cryptography` installed |  |
| 956 | `` "signing an erasure needs the `cryptography` package (pip i… `` → `` "XXsigning an erasure needs the `cryptography` package (pip… `` | [unexecuted] the no-cryptography branch of sign_erasure: the suite always has `cryptography` installed |  |

**`inspeximus/core.py` `verify_erasure_certificate`** (148)

| line | mutation | why no test caught it | now |
|---:|---|---|---|
| 1037 | `problems.append(f"tombstone {j}: broken chain link (a prior…` → `problems.append(None)` | [message] problems.append(None) still leaves a problem; the verdict comes from chain_ok and every test that reached here checks the verdict | killed by `test_a_tombstone_removed_from_the_middle_fails_on_the_chain_alone` |
| 1038 | `chain_ok = False` → `chain_ok = None` | [condition] chain_ok = None is falsy like False, so `valid` is still falsy; only `valid is False` / `chain_intact is False` distinguish it, and the tests that reached here asserted `not valid` | killed by `test_a_tombstone_removed_from_the_middle_fails_on_the_chain_alone` |
| 1038 | `False` → `True` | [soundness] every test that broke a prev link also broke a signature, the anchor or the summary, so a flag left True here changed no verdict; an unsigned chain with a middle tombstone removed and the anchor recomputed verified valid:true | killed by `test_a_tombstone_removed_from_the_middle_fails_on_the_chain_alone` |
| 1041 | `False` → `True` | [soundness] every test that broke a tombstone hash also broke something else; a tombstone whose memory_id is rewritten in place keeps its hash, so its SIGNATURE still verifies, and with the anchor recomputed the hash mismatch is the only thing left -- flipped, the forged certificate verified valid:true | killed by `test_a_tombstone_edited_in_place_fails_on_the_chain_alone` |
| 1044 | `problems.append("cannot verify signatures (cryptography not…` → `problems.append(None)` | [unexecuted] the verifier-without-cryptography branch never ran (the suite always has it installed); with `sigs_ok` flipped, a signed certificate verified without any Ed25519 backend reported signatures_valid: True | killed by `test_without_an_ed25519_backend_signatures_are_not_reported_valid` |
| 1044 | `"cannot verify signatures (cryptography not installed)"` → `"XXcannot verify signatures (cryptography not installed)XX"` | [unexecuted] the verifier-without-cryptography branch never ran (the suite always has it installed); with `sigs_ok` flipped, a signed certificate verified without any Ed25519 backend reported signatures_valid: True |  |
| 1044 | `"cannot verify signatures (cryptography not installed)"` → `"CANNOT VERIFY SIGNATURES (CRYPTOGRAPHY NOT INSTALLED)"` | [unexecuted] the verifier-without-cryptography branch never ran (the suite always has it installed); with `sigs_ok` flipped, a signed certificate verified without any Ed25519 backend reported signatures_valid: True | killed by `test_without_an_ed25519_backend_signatures_are_not_reported_valid` |
| 1045 | `sigs_ok = False` → `sigs_ok = None` | [unexecuted] the verifier-without-cryptography branch never ran (the suite always has it installed); with `sigs_ok` flipped, a signed certificate verified without any Ed25519 backend reported signatures_valid: True | killed by `test_without_an_ed25519_backend_signatures_are_not_reported_valid` |
| 1045 | `False` → `True` | [unexecuted] the verifier-without-cryptography branch never ran (the suite always has it installed); with `sigs_ok` flipped, a signed certificate verified without any Ed25519 backend reported signatures_valid: True | killed by `test_without_an_ed25519_backend_signatures_are_not_reported_valid` |
| 1048 | `t.get("pubkey") or pub or ""` → `t.get("pubkey") or pub and ""` | [condition] the key comes from `pub` only when a tombstone carries no pubkey of its own, i.e. one signed through receipt_signer with no receipt_pubkey; no test verified such a certificate | killed by `test_tombstones_signed_through_an_external_signer_verify_against_the_pinned_key` |
| 1048 | `""` → `"XXXX"` | [equivalent] equivalent: reached only when both keys are absent, and bytes.fromhex('') and bytes.fromhex('XXXX') both end in an exception that is reported as an invalid signature |  |
| 1054 | `problems.append(f"tombstone {j}: invalid signature")` → `problems.append(None)` | [unexecuted] no test presents a tombstone signature that fails to verify, so this branch never ran | killed by `test_a_tombstone_carrying_another_tombstones_signature_does_not_verify` |
| 1055 | `sigs_ok = False` → `sigs_ok = None` | [soundness] no test presents a tombstone signature that fails to verify; `valid` reads sigs_ok, so with the flag left True a certificate carrying another tombstone's signature verified valid:true with 'invalid signature' in its own problem list | killed by `test_a_tombstone_carrying_another_tombstones_signature_does_not_verify` |
| 1055 | `False` → `True` | [soundness] no test presents a tombstone signature that fails to verify; `valid` reads sigs_ok, so with the flag left True a certificate carrying another tombstone's signature verified valid:true with 'invalid signature' in its own problem list | killed by `test_a_tombstone_carrying_another_tombstones_signature_does_not_verify` |
| 1077 | `"UNSIGNED: no tombstone carries a signature, so nothing was…` → `"UNSIGNED: NO TOMBSTONE CARRIES A SIGNATURE, SO NOTHING WAS…` | [message] wording of the UNSIGNED limit: tests assert that `limits` mentions UNSIGNED or that signed is False, not the sentence |  |
| 1078 | `` "`pubkey` — the chain proves integrity, not authorship. Set… `` → `` "XX`pubkey` — the chain proves integrity, not authorship. S… `` | [message] wording of the UNSIGNED limit: tests assert that `limits` mentions UNSIGNED or that signed is False, not the sentence |  |
| 1078 | `` "`pubkey` — the chain proves integrity, not authorship. Set… `` → `` "`PUBKEY` — THE CHAIN PROVES INTEGRITY, NOT AUTHORSHIP. SET… `` | [message] wording of the UNSIGNED limit: tests assert that `limits` mentions UNSIGNED or that signed is False, not the sentence |  |
| 1078 | `` "`pubkey` — the chain proves integrity, not authorship. Set… `` → `` "`pubkey` — the chain proves integrity, not authorship. set… `` | [message] wording of the UNSIGNED limit: tests assert that `limits` mentions UNSIGNED or that signed is False, not the sentence |  |
| 1088 | `t.get("sig")` → `t.get(None)` | [message] details inside the PARTIALLY SIGNED problem (which ids are listed, the count arithmetic, how many ids are shown); the verdict comes from sigs_ok and the test asserts the phrase |  |
| 1088 | `"sig"` → `"SIG"` | [message] details inside the PARTIALLY SIGNED problem (which ids are listed, the count arithmetic, how many ids are shown); the verdict comes from sigs_ok and the test asserts the phrase |  |
| 1088 | `"sig"` → `"XXsigXX"` | [message] details inside the PARTIALLY SIGNED problem (which ids are listed, the count arithmetic, how many ids are shown); the verdict comes from sigs_ok and the test asserts the phrase |  |
| 1089 | `len(toms) - len(signed)` → `len(toms) + len(signed)` | [message] details inside the PARTIALLY SIGNED problem (which ids are listed, the count arithmetic, how many ids are shown); the verdict comes from sigs_ok and the test asserts the phrase |  |
| 1090 | `5` → `6` | [message] details inside the PARTIALLY SIGNED problem (which ids are listed, the count arithmetic, how many ids are shown); the verdict comes from sigs_ok and the test asserts the phrase |  |
| 1099 | `"anchor tombstones_tip does not match the tombstone chain t…` → `"XXanchor tombstones_tip does not match the tombstone chain…` | [message] wording or interpolated value of a problem line; the verdict comes from the anchor flags |  |
| 1099 | `"anchor tombstones_tip does not match the tombstone chain t…` → `"ANCHOR TOMBSTONES_TIP DOES NOT MATCH THE TOMBSTONE CHAIN T…` | [message] wording or interpolated value of a problem line; the verdict comes from the anchor flags |  |
| 1104 | `anc.get('n_tombstones')` → `anc.get(None)` | [message] wording or interpolated value of a problem line; the verdict comes from the anchor flags |  |
| 1104 | `'n_tombstones'` → `'XXn_tombstonesXX'` | [message] wording or interpolated value of a problem line; the verdict comes from the anchor flags |  |
| 1104 | `'n_tombstones'` → `'N_TOMBSTONES'` | [message] wording or interpolated value of a problem line; the verdict comes from the anchor flags |  |
| 1106 | `False` → `True` | [soundness] the tests that reached this line trimmed the chain, which also breaks the Merkle root, so the count check never decided a verdict alone; an anchor whose n_tombstones alone lies (sth_hash recomputed, no key needed) verified | killed by `test_an_anchor_count_that_disagrees_with_the_chain_fails_on_its_own` |
| 1106 | `anchor_ok = False` → `anchor_ok = None` | [soundness] the tests that reached this line trimmed the chain, which also breaks the Merkle root, so the count check never decided a verdict alone; an anchor whose n_tombstones alone lies (sth_hash recomputed, no key needed) verified | killed by `test_an_anchor_count_that_disagrees_with_the_chain_fails_on_its_own` |
| 1110 | `Inspeximus._chain_core(t, "tombstone")` → `Inspeximus._chain_core(t, None)` | [equivalent] equivalent: _chain_core returns the tombstone core for every kind that is not 'write' |  |
| 1110 | `"tombstone"` → `"XXtombstoneXX"` | [equivalent] equivalent: _chain_core returns the tombstone core for every kind that is not 'write' |  |
| 1110 | `"tombstone"` → `"TOMBSTONE"` | [equivalent] equivalent: _chain_core returns the tombstone core for every kind that is not 'write' |  |
| 1112 | `"anchor tombstones_root does not re-derive from the tombsto…` → `"XXanchor tombstones_root does not re-derive from the tombs…` | [message] wording or interpolated value of a problem line; the verdict comes from the anchor flags |  |
| 1113 | `anchor_ok = False` → `anchor_ok = None` | [soundness] same shape: every test with a wrong tombstones_root also had a wrong count; sth_hash does not cover the root, so a root committing to another history changes nothing else and verified | killed by `test_an_anchor_root_that_disagrees_with_the_chain_fails_on_its_own` |
| 1113 | `False` → `True` | [soundness] same shape: every test with a wrong tombstones_root also had a wrong count; sth_hash does not cover the root, so a root committing to another history changes nothing else and verified | killed by `test_an_anchor_root_that_disagrees_with_the_chain_fails_on_its_own` |
| 1115 | `problems.append(f"anchor tombstones_root could not be re-de…` → `problems.append(None)` | [defensive] unreachable from a JSON certificate: a tombstone that got past the chain loop is a dict of JSON values, which _canon always serialises, so re-deriving the root cannot raise |  |
| 1115 | `repr(e)` → `repr(None)` | [defensive] unreachable from a JSON certificate: a tombstone that got past the chain loop is a dict of JSON values, which _canon always serialises, so re-deriving the root cannot raise |  |
| 1115 | `60` → `61` | [defensive] unreachable from a JSON certificate: a tombstone that got past the chain loop is a dict of JSON values, which _canon always serialises, so re-deriving the root cannot raise |  |
| 1116 | `anchor_ok = False` → `anchor_ok = None` | [defensive] unreachable from a JSON certificate: a tombstone that got past the chain loop is a dict of JSON values, which _canon always serialises, so re-deriving the root cannot raise |  |
| 1116 | `False` → `True` | [defensive] unreachable from a JSON certificate: a tombstone that got past the chain loop is a dict of JSON values, which _canon always serialises, so re-deriving the root cannot raise |  |
| 1117 | `"sth_hash"` → `"XXsth_hashXX"` | [soundness] each of these skips the sth_hash comparison; every test that broke sth_hash broke another check too, so skipping it changed no verdict. sth_hash is the value a witness co-signs | killed by `test_an_sth_hash_that_is_not_its_fields_fails_on_its_own` |
| 1117 | `"sth_hash"` → `"STH_HASH"` | [soundness] each of these skips the sth_hash comparison; every test that broke sth_hash broke another check too, so skipping it changed no verdict. sth_hash is the value a witness co-signs | killed by `test_an_sth_hash_that_is_not_its_fields_fails_on_its_own` |
| 1117 | `is not` → `is` | [soundness] each of these skips the sth_hash comparison; every test that broke sth_hash broke another check too, so skipping it changed no verdict. sth_hash is the value a witness co-signs | killed by `test_an_sth_hash_that_is_not_its_fields_fails_on_its_own` |
| 1117 | `in` → `not in` | [soundness] each of these skips the sth_hash comparison; every test that broke sth_hash broke another check too, so skipping it changed no verdict. sth_hash is the value a witness co-signs | killed by `test_an_sth_hash_that_is_not_its_fields_fails_on_its_own` |
| 1117 | `anc.get("sth_hash")` → `anc.get(None)` | [soundness] each of these skips the sth_hash comparison; every test that broke sth_hash broke another check too, so skipping it changed no verdict. sth_hash is the value a witness co-signs | killed by `test_an_sth_hash_that_is_not_its_fields_fails_on_its_own` |
| 1117 | `anc.get("sth_hash") is not None and all(k in anc for k in _…` → `anc.get("sth_hash") is not None or all(k in anc for k in _S…` | [condition] stricter mutant: it differs only for an anchor WITHOUT sth_hash (then it refuses it); no test hands one over, so whether that is allowed is not pinned | killed by `test_an_anchor_without_a_writes_tip_cannot_be_bound_to_a_store` |
| 1119 | `"anchor sth_hash does not re-derive from its four fields"` → `"ANCHOR STH_HASH DOES NOT RE-DERIVE FROM ITS FOUR FIELDS"` | [message] wording or interpolated value of a problem line; the verdict comes from the anchor flags | killed by `test_an_sth_hash_that_is_not_its_fields_fails_on_its_own` |
| 1119 | `"anchor sth_hash does not re-derive from its four fields"` → `"XXanchor sth_hash does not re-derive from its four fieldsX…` | [message] wording or interpolated value of a problem line; the verdict comes from the anchor flags |  |
| 1124 | `checks["anchor_witnessed"] = None` → `checks["anchor_witnessed"] = ""` | [unread-output] 'not performed' is None throughout this verifier and `valid` reads it with `is not False`, so an empty string passes every verdict; no test asserted the None when the input was not given | killed by `test_checks_that_were_not_asked_for_say_none` |
| 1129 | `not isinstance(w_n, int) or w_tip is None` → `not isinstance(w_n, int) and w_tip is None` | [unexecuted] expected_anchor was only ever passed well-formed; `and` lets a non-integer count through to a TypeError and a missing tip through to the next branch | killed by `test_a_witnessed_anchor_that_pins_nothing_is_not_a_witness[count-is-a-string]` |
| 1130 | `problems.append("expected_anchor carries no n_tombstones/to…` → `problems.append(None)` | [unexecuted] a witnessed-anchor / store-binding refusal no test triggered, so only its wording or its None-append was mutated here; the verdict flags beside it are covered by the new tests | killed by `test_a_witnessed_anchor_that_pins_nothing_is_not_a_witness[count-is-a-string]` |
| 1130 | `"expected_anchor carries no n_tombstones/tombstones_tip; no…` → `"XXexpected_anchor carries no n_tombstones/tombstones_tip; …` | [unexecuted] a witnessed-anchor / store-binding refusal no test triggered, so only its wording or its None-append was mutated here; the verdict flags beside it are covered by the new tests |  |
| 1130 | `"expected_anchor carries no n_tombstones/tombstones_tip; no…` → `"EXPECTED_ANCHOR CARRIES NO N_TOMBSTONES/TOMBSTONES_TIP; NO…` | [unexecuted] a witnessed-anchor / store-binding refusal no test triggered, so only its wording or its None-append was mutated here; the verdict flags beside it are covered by the new tests | killed by `test_a_witnessed_anchor_that_pins_nothing_is_not_a_witness[count-is-a-string]` |
| 1131 | `ok_w = False` → `ok_w = None` | [soundness] no test passed an expected_anchor that pins nothing / is rewritten at the witnessed position / claims an empty chain with a non-genesis tip; with ok_w left True the certificate read as witnessed | killed by `test_a_witnessed_anchor_that_pins_nothing_is_not_a_witness[count-is-a-string]` |
| 1131 | `False` → `True` | [soundness] no test passed an expected_anchor that pins nothing / is rewritten at the witnessed position / claims an empty chain with a non-genesis tip; with ok_w left True the certificate read as witnessed | killed by `test_a_witnessed_anchor_that_pins_nothing_is_not_a_witness[count-is-a-string]` |
| 1136 | `> 0` → `>= 0` | [boundary] the only witnessed-anchor test covered a TRUNCATED chain; none covered a chain of the right length rewritten at the witnessed position, and position 1 is exactly what `w_n > 1` skips | killed by `test_a_witness_of_an_empty_chain_must_hold_the_genesis_tip` |
| 1136 | `0` → `1` | [boundary] the only witnessed-anchor test covered a TRUNCATED chain; none covered a chain of the right length rewritten at the witnessed position, and position 1 is exactly what `w_n > 1` skips | killed by `test_a_chain_rewritten_at_the_witnessed_position_fails[1]` |
| 1137 | `f"tombstone {w_n - 1} is not the one the witness saw at tha…` → `None` | [unexecuted] a witnessed-anchor / store-binding refusal no test triggered, so only its wording or its None-append was mutated here; the verdict flags beside it are covered by the new tests | killed by `test_a_chain_rewritten_at_the_witnessed_position_fails[1]` |
| 1137 | `w_n - 1` → `w_n + 1` | [boundary] the only witnessed-anchor test covered a TRUNCATED chain; none covered a chain of the right length rewritten at the witnessed position, and position 1 is exactly what `w_n > 1` skips | killed by `test_a_chain_rewritten_at_the_witnessed_position_fails[1]` |
| 1137 | `1` → `2` | [boundary] the only witnessed-anchor test covered a TRUNCATED chain; none covered a chain of the right length rewritten at the witnessed position, and position 1 is exactly what `w_n > 1` skips | killed by `test_a_chain_rewritten_at_the_witnessed_position_fails[1]` |
| 1139 | `ok_w = False` → `ok_w = None` | [soundness] no test passed an expected_anchor that pins nothing / is rewritten at the witnessed position / claims an empty chain with a non-genesis tip; with ok_w left True the certificate read as witnessed | killed by `test_a_chain_rewritten_at_the_witnessed_position_fails[1]` |
| 1139 | `False` → `True` | [soundness] no test passed an expected_anchor that pins nothing / is rewritten at the witnessed position / claims an empty chain with a non-genesis tip; with ok_w left True the certificate read as witnessed | killed by `test_a_chain_rewritten_at_the_witnessed_position_fails[1]` |
| 1140 | `0` → `1` | [condition] no test passed a witness of an EMPTY chain, honest or not | killed by `test_a_chain_rewritten_at_the_witnessed_position_fails[1]` |
| 1140 | `!= _GENESIS` → `== _GENESIS` | [condition] no test passed a witness of an EMPTY chain, honest or not | killed by `test_a_witness_of_an_empty_chain_must_hold_the_genesis_tip` |
| 1141 | `problems.append("the witnessed anchor claims an empty chain…` → `problems.append(None)` | [unexecuted] a witnessed-anchor / store-binding refusal no test triggered, so only its wording or its None-append was mutated here; the verdict flags beside it are covered by the new tests | killed by `test_a_witness_of_an_empty_chain_must_hold_the_genesis_tip` |
| 1141 | `"the witnessed anchor claims an empty chain with a non-gene…` → `"THE WITNESSED ANCHOR CLAIMS AN EMPTY CHAIN WITH A NON-GENE…` | [unexecuted] a witnessed-anchor / store-binding refusal no test triggered, so only its wording or its None-append was mutated here; the verdict flags beside it are covered by the new tests | killed by `test_a_witness_of_an_empty_chain_must_hold_the_genesis_tip` |
| 1141 | `"the witnessed anchor claims an empty chain with a non-gene…` → `"XXthe witnessed anchor claims an empty chain with a non-ge…` | [unexecuted] a witnessed-anchor / store-binding refusal no test triggered, so only its wording or its None-append was mutated here; the verdict flags beside it are covered by the new tests |  |
| 1142 | `ok_w = False` → `ok_w = None` | [soundness] no test passed an expected_anchor that pins nothing / is rewritten at the witnessed position / claims an empty chain with a non-genesis tip; with ok_w left True the certificate read as witnessed | killed by `test_a_witness_of_an_empty_chain_must_hold_the_genesis_tip` |
| 1142 | `False` → `True` | [soundness] no test passed an expected_anchor that pins nothing / is rewritten at the witnessed position / claims an empty chain with a non-genesis tip; with ok_w left True the certificate read as witnessed | killed by `test_a_witness_of_an_empty_chain_must_hold_the_genesis_tip` |
| 1163 | `cert.get("request_ids") or []` → `cert.get("request_ids") and []` | [coverage-depth] the only pre-scoped_to (legacy) certificate test used a store with one request, where 'scoped to the claimed requests' and 'the whole chain' are the same set | killed by `test_a_pre_marker_certificate_is_scoped_by_its_claimed_requests` |
| 1163 | `cert.get("request_ids")` → `cert.get(None)` | [coverage-depth] the only pre-scoped_to (legacy) certificate test used a store with one request, where 'scoped to the claimed requests' and 'the whole chain' are the same set | killed by `test_a_pre_marker_certificate_is_scoped_by_its_claimed_requests` |
| 1163 | `"request_ids"` → `"XXrequest_idsXX"` | [coverage-depth] the only pre-scoped_to (legacy) certificate test used a store with one request, where 'scoped to the claimed requests' and 'the whole chain' are the same set | killed by `test_a_pre_marker_certificate_is_scoped_by_its_claimed_requests` |
| 1163 | `"request_ids"` → `"REQUEST_IDS"` | [coverage-depth] the only pre-scoped_to (legacy) certificate test used a store with one request, where 'scoped to the claimed requests' and 'the whole chain' are the same set | killed by `test_a_pre_marker_certificate_is_scoped_by_its_claimed_requests` |
| 1163 | `claimed = set(cert.get("request_ids") or [])` → `claimed = None` | [coverage-depth] the only pre-scoped_to (legacy) certificate test used a store with one request, where 'scoped to the claimed requests' and 'the whole chain' are the same set | killed by `test_a_pre_marker_certificate_is_scoped_by_its_claimed_requests` |
| 1164 | `claimed` → `(claimed) or True` | [coverage-depth] the only pre-scoped_to (legacy) certificate test used a store with one request, where 'scoped to the claimed requests' and 'the whole chain' are the same set | killed by `test_a_pre_marker_certificate_is_scoped_by_its_claimed_requests` |
| 1164 | `claimed` → `(claimed) and False` | [coverage-depth] the only pre-scoped_to (legacy) certificate test used a store with one request, where 'scoped to the claimed requests' and 'the whole chain' are the same set | killed by `test_a_pre_marker_certificate_is_scoped_by_its_claimed_requests` |
| 1165 | `t.get("request_id")` → `t.get(None)` | [coverage-depth] the only pre-scoped_to (legacy) certificate test used a store with one request, where 'scoped to the claimed requests' and 'the whole chain' are the same set | killed by `test_a_pre_marker_certificate_is_scoped_by_its_claimed_requests` |
| 1165 | `"request_id"` → `"XXrequest_idXX"` | [coverage-depth] the only pre-scoped_to (legacy) certificate test used a store with one request, where 'scoped to the claimed requests' and 'the whole chain' are the same set | killed by `test_a_pre_marker_certificate_is_scoped_by_its_claimed_requests` |
| 1165 | `"request_id"` → `"REQUEST_ID"` | [coverage-depth] the only pre-scoped_to (legacy) certificate test used a store with one request, where 'scoped to the claimed requests' and 'the whole chain' are the same set | killed by `test_a_pre_marker_certificate_is_scoped_by_its_claimed_requests` |
| 1176 | `cert.get('count')` → `cert.get(None)` | [message] the value interpolated into the count problem; summary_derivable parses only the prefix 'count', which is unchanged |  |
| 1176 | `'count'` → `'XXcountXX'` | [message] the value interpolated into the count problem; summary_derivable parses only the prefix 'count', which is unchanged |  |
| 1176 | `'count'` → `'COUNT'` | [message] the value interpolated into the count problem; summary_derivable parses only the prefix 'count', which is unchanged |  |
| 1196 | `"the tombstone chain is empty"` → `"THE TOMBSTONE CHAIN IS EMPTY"` | [message] wording of the ZERO-erasures explanation (`_why`); the verdict comes from attests_an_erasure, and the tests assert 'ZERO erasures' or the flag |  |
| 1196 | `not toms` → `(not toms) or True` | [message] wording of the ZERO-erasures explanation (`_why`); the verdict comes from attests_an_erasure, and the tests assert 'ZERO erasures' or the flag |  |
| 1196 | `"the tombstone chain is empty"` → `"XXthe tombstone chain is emptyXX"` | [message] wording of the ZERO-erasures explanation (`_why`); the verdict comes from attests_an_erasure, and the tests assert 'ZERO erasures' or the flag |  |
| 1196 | `not toms` → `(not toms) and False` | [message] wording of the ZERO-erasures explanation (`_why`); the verdict comes from attests_an_erasure, and the tests assert 'ZERO erasures' or the flag |  |
| 1196 | `not toms` → `toms` | [message] wording of the ZERO-erasures explanation (`_why`); the verdict comes from attests_an_erasure, and the tests assert 'ZERO erasures' or the flag |  |
| 1196 | `("the tombstone chain is empty" if not toms else "no tombst…` → `None` | [message] wording of the ZERO-erasures explanation (`_why`); the verdict comes from attests_an_erasure, and the tests assert 'ZERO erasures' or the flag |  |
| 1197 | `"no tombstone in the chain falls within this certificate's …` → `"NO TOMBSTONE IN THE CHAIN FALLS WITHIN THIS CERTIFICATE'S …` | [message] wording of the ZERO-erasures explanation (`_why`); the verdict comes from attests_an_erasure, and the tests assert 'ZERO erasures' or the flag |  |
| 1197 | `"no tombstone in the chain falls within this certificate's …` → `"XXno tombstone in the chain falls within this certificate'…` | [message] wording of the ZERO-erasures explanation (`_why`); the verdict comes from attests_an_erasure, and the tests assert 'ZERO erasures' or the flag |  |
| 1198 | `'request_id='` → `'REQUEST_ID='` | [message] wording of the ZERO-erasures explanation (`_why`); the verdict comes from attests_an_erasure, and the tests assert 'ZERO erasures' or the flag |  |
| 1198 | `'request_id='` → `'XXrequest_id=XX'` | [message] wording of the ZERO-erasures explanation (`_why`); the verdict comes from attests_an_erasure, and the tests assert 'ZERO erasures' or the flag |  |
| 1198 | `'request_id=' + repr(cert.get('scoped_to')) if 'scoped_to' …` → `'request_id=' + repr(cert.get('scoped_to')) if ('scoped_to'…` | [message] wording of the ZERO-erasures explanation (`_why`); the verdict comes from attests_an_erasure, and the tests assert 'ZERO erasures' or the flag |  |
| 1198 | `'request_id=' + repr(cert.get('scoped_to')) if 'scoped_to' …` → `'request_id=' + repr(cert.get('scoped_to')) if ('scoped_to'…` | [message] wording of the ZERO-erasures explanation (`_why`); the verdict comes from attests_an_erasure, and the tests assert 'ZERO erasures' or the flag |  |
| 1198 | `repr(cert.get('scoped_to'))` → `repr(None)` | [message] wording of the ZERO-erasures explanation (`_why`); the verdict comes from attests_an_erasure, and the tests assert 'ZERO erasures' or the flag |  |
| 1198 | `cert.get('scoped_to')` → `cert.get(None)` | [message] wording of the ZERO-erasures explanation (`_why`); the verdict comes from attests_an_erasure, and the tests assert 'ZERO erasures' or the flag |  |
| 1198 | `'scoped_to'` → `'XXscoped_toXX'` | [message] wording of the ZERO-erasures explanation (`_why`); the verdict comes from attests_an_erasure, and the tests assert 'ZERO erasures' or the flag |  |
| 1198 | `'scoped_to'` → `'SCOPED_TO'` | [message] wording of the ZERO-erasures explanation (`_why`); the verdict comes from attests_an_erasure, and the tests assert 'ZERO erasures' or the flag |  |
| 1198 | `'scoped_to'` → `'XXscoped_toXX'` | [message] wording of the ZERO-erasures explanation (`_why`); the verdict comes from attests_an_erasure, and the tests assert 'ZERO erasures' or the flag |  |
| 1198 | `'scoped_to'` → `'SCOPED_TO'` | [message] wording of the ZERO-erasures explanation (`_why`); the verdict comes from attests_an_erasure, and the tests assert 'ZERO erasures' or the flag |  |
| 1198 | `in` → `not in` | [message] wording of the ZERO-erasures explanation (`_why`); the verdict comes from attests_an_erasure, and the tests assert 'ZERO erasures' or the flag |  |
| 1198 | `'the claimed request_ids'` → `'THE CLAIMED REQUEST_IDS'` | [message] wording of the ZERO-erasures explanation (`_why`); the verdict comes from attests_an_erasure, and the tests assert 'ZERO erasures' or the flag |  |
| 1198 | `'the claimed request_ids'` → `'XXthe claimed request_idsXX'` | [message] wording of the ZERO-erasures explanation (`_why`); the verdict comes from attests_an_erasure, and the tests assert 'ZERO erasures' or the flag |  |
| 1201 | `f"this certificate attests to ZERO erasures: {_why}. Nothin…` → `None` | [message] wording of the ZERO-erasures explanation (`_why`); the verdict comes from attests_an_erasure, and the tests assert 'ZERO erasures' or the flag |  |
| 1213 | `store_items is None and store_path` → `store_items is None or store_path` | [condition] differs only when NO store is named: the mutant then tries to read path None and appends 'cannot read store at None' without changing the verdict; no test checked that a bare verification reports no problems | killed by `test_checks_that_were_not_asked_for_say_none` |
| 1226 | `5` → `6` | [message] the encrypted magic is 5 bytes, so raw[:6] never matches and the store falls through to 'cannot read store': still refused, only the explanation changes, and the test checks the verdict |  |
| 1227 | `"store is encrypted — supply decrypted store_items to check…` → `"XXstore is encrypted — supply decrypted store_items to che…` | [message] wording or interpolated detail of a problem line; the verdict comes from the flags beside it |  |
| 1227 | `"store is encrypted — supply decrypted store_items to check…` → `"STORE IS ENCRYPTED — SUPPLY DECRYPTED STORE_ITEMS TO CHECK…` | [message] wording or interpolated detail of a problem line; the verdict comes from the flags beside it |  |
| 1228 | `"or rely on shred() (crypto-erasure) for the encrypted case"` → `"XXor rely on shred() (crypto-erasure) for the encrypted ca…` | [message] wording or interpolated detail of a problem line; the verdict comes from the flags beside it |  |
| 1228 | `"or rely on shred() (crypto-erasure) for the encrypted case"` → `"OR RELY ON SHRED() (CRYPTO-ERASURE) FOR THE ENCRYPTED CASE"` | [message] wording or interpolated detail of a problem line; the verdict comes from the flags beside it |  |
| 1230 | `"utf-8"` → `"UTF-8"` | [equivalent] equivalent: codec names are case-insensitive |  |
| 1232 | `repr(e)` → `repr(None)` | [message] wording or interpolated detail of a problem line; the verdict comes from the flags beside it |  |
| 1232 | `80` → `81` | [message] wording or interpolated detail of a problem line; the verdict comes from the flags beside it |  |
| 1238 | `5` → `6` | [message] wording or interpolated detail of a problem line; the verdict comes from the flags beside it |  |
| 1242 | `checks["store_bound"] = None` → `checks["store_bound"] = ""` | [unread-output] 'not performed' is None throughout this verifier and `valid` reads it with `is not False`, so an empty string passes every verdict; no test asserted the None when the input was not given | killed by `test_checks_that_were_not_asked_for_say_none` |
| 1247 | `problems.append("the certificate's anchor carries no writes…` → `problems.append(None)` | [unexecuted] a witnessed-anchor / store-binding refusal no test triggered, so only its wording or its None-append was mutated here; the verdict flags beside it are covered by the new tests | killed by `test_an_anchor_without_a_writes_tip_cannot_be_bound_to_a_store` |
| 1247 | `"the certificate's anchor carries no writes_tip, so it cann…` → `"XXthe certificate's anchor carries no writes_tip, so it ca…` | [unexecuted] a witnessed-anchor / store-binding refusal no test triggered, so only its wording or its None-append was mutated here; the verdict flags beside it are covered by the new tests |  |
| 1247 | `"the certificate's anchor carries no writes_tip, so it cann…` → `"THE CERTIFICATE'S ANCHOR CARRIES NO WRITES_TIP, SO IT CANN…` | [unexecuted] a witnessed-anchor / store-binding refusal no test triggered, so only its wording or its None-append was mutated here; the verdict flags beside it are covered by the new tests | killed by `test_an_anchor_without_a_writes_tip_cannot_be_bound_to_a_store` |
| 1248 | `checks["store_bound"] = False` → `checks["store_bound"] = None` | [soundness] no test hands store_receipts with an anchor that lacks writes_tip, or with a genesis writes_tip against a store that has receipts; with store_bound left True / renamed the absence proof counted as bound to a store it was not issued from | killed by `test_an_anchor_without_a_writes_tip_cannot_be_bound_to_a_store` |
| 1248 | `"store_bound"` → `"XXstore_boundXX"` | [soundness] no test hands store_receipts with an anchor that lacks writes_tip, or with a genesis writes_tip against a store that has receipts; with store_bound left True / renamed the absence proof counted as bound to a store it was not issued from | killed by `test_an_anchor_without_a_writes_tip_cannot_be_bound_to_a_store` |
| 1248 | `"store_bound"` → `"STORE_BOUND"` | [soundness] no test hands store_receipts with an anchor that lacks writes_tip, or with a genesis writes_tip against a store that has receipts; with store_bound left True / renamed the absence proof counted as bound to a store it was not issued from | killed by `test_an_anchor_without_a_writes_tip_cannot_be_bound_to_a_store` |
| 1248 | `False` → `True` | [soundness] no test hands store_receipts with an anchor that lacks writes_tip, or with a genesis writes_tip against a store that has receipts; with store_bound left True / renamed the absence proof counted as bound to a store it was not issued from | killed by `test_an_anchor_without_a_writes_tip_cannot_be_bound_to_a_store` |
| 1250 | `"the certificate was issued from a store with no write rece…` → `None` | [unexecuted] a witnessed-anchor / store-binding refusal no test triggered, so only its wording or its None-append was mutated here; the verdict flags beside it are covered by the new tests | killed by `test_a_certificate_from_a_receiptless_store_does_not_bind_to_one_with_receipts` |
| 1250 | `"the certificate was issued from a store with no write rece…` → `"THE CERTIFICATE WAS ISSUED FROM A STORE WITH NO WRITE RECE…` | [unexecuted] a witnessed-anchor / store-binding refusal no test triggered, so only its wording or its None-append was mutated here; the verdict flags beside it are covered by the new tests | killed by `test_a_certificate_from_a_receiptless_store_does_not_bind_to_one_with_receipts` |
| 1250 | `"the certificate was issued from a store with no write rece…` → `"XXthe certificate was issued from a store with no write re…` | [unexecuted] a witnessed-anchor / store-binding refusal no test triggered, so only its wording or its None-append was mutated here; the verdict flags beside it are covered by the new tests |  |
| 1251 | `"store has some, so it is not that store"` → `"XXstore has some, so it is not that storeXX"` | [unexecuted] a witnessed-anchor / store-binding refusal no test triggered, so only its wording or its None-append was mutated here; the verdict flags beside it are covered by the new tests |  |
| 1251 | `"store has some, so it is not that store"` → `"STORE HAS SOME, SO IT IS NOT THAT STORE"` | [unexecuted] a witnessed-anchor / store-binding refusal no test triggered, so only its wording or its None-append was mutated here; the verdict flags beside it are covered by the new tests |  |
| 1252 | `checks["store_bound"] = False` → `checks["store_bound"] = None` | [soundness] no test hands store_receipts with an anchor that lacks writes_tip, or with a genesis writes_tip against a store that has receipts; with store_bound left True / renamed the absence proof counted as bound to a store it was not issued from | killed by `test_a_certificate_from_a_receiptless_store_does_not_bind_to_one_with_receipts` |
| 1252 | `"store_bound"` → `"STORE_BOUND"` | [soundness] no test hands store_receipts with an anchor that lacks writes_tip, or with a genesis writes_tip against a store that has receipts; with store_bound left True / renamed the absence proof counted as bound to a store it was not issued from | killed by `test_a_certificate_from_a_receiptless_store_does_not_bind_to_one_with_receipts` |
| 1252 | `"store_bound"` → `"XXstore_boundXX"` | [soundness] no test hands store_receipts with an anchor that lacks writes_tip, or with a genesis writes_tip against a store that has receipts; with store_bound left True / renamed the absence proof counted as bound to a store it was not issued from | killed by `test_a_certificate_from_a_receiptless_store_does_not_bind_to_one_with_receipts` |
| 1252 | `False` → `True` | [soundness] no test hands store_receipts with an anchor that lacks writes_tip, or with a genesis writes_tip against a store that has receipts; with store_bound left True / renamed the absence proof counted as bound to a store it was not issued from | killed by `test_a_certificate_from_a_receiptless_store_does_not_bind_to_one_with_receipts` |
| 1254 | `"the handed store's receipt chain does not contain the cert…` → `None` | [message] wording or interpolated detail of a problem line; the verdict comes from the flags beside it |  |
| 1254 | `"the handed store's receipt chain does not contain the cert…` → `"XXthe handed store's receipt chain does not contain the ce…` | [message] wording or interpolated detail of a problem line; the verdict comes from the flags beside it |  |
| 1254 | `"the handed store's receipt chain does not contain the cert…` → `"THE HANDED STORE'S RECEIPT CHAIN DOES NOT CONTAIN THE CERT…` | [message] wording or interpolated detail of a problem line; the verdict comes from the flags beside it |  |
| 1255 | `"writes_tip: the absence proof ran against a store this cer…` → `"WRITES_TIP: THE ABSENCE PROOF RAN AGAINST A STORE THIS CER…` | [message] wording or interpolated detail of a problem line; the verdict comes from the flags beside it |  |
| 1255 | `"writes_tip: the absence proof ran against a store this cer…` → `"XXwrites_tip: the absence proof ran against a store this c…` | [message] wording or interpolated detail of a problem line; the verdict comes from the flags beside it |  |
| 1256 | `"not issued from"` → `"XXnot issued fromXX"` | [message] wording or interpolated detail of a problem line; the verdict comes from the flags beside it |  |
| 1256 | `"not issued from"` → `"NOT ISSUED FROM"` | [message] wording or interpolated detail of a problem line; the verdict comes from the flags beside it |  |
| 1265 | `"the absence proof was REQUESTED but could not run (store u…` → `"XXthe absence proof was REQUESTED but could not run (store…` | [message] wording or interpolated detail of a problem line; the verdict comes from the flags beside it |  |
| 1266 | `"encrypted) — this certificate is NOT verified against a st…` → `"ENCRYPTED) — THIS CERTIFICATE IS NOT VERIFIED AGAINST A ST…` | [message] wording or interpolated detail of a problem line; the verdict comes from the flags beside it |  |
| 1266 | `"encrypted) — this certificate is NOT verified against a st…` → `"encrypted) — this certificate is not verified against a st…` | [message] wording or interpolated detail of a problem line; the verdict comes from the flags beside it |  |
| 1266 | `"encrypted) — this certificate is NOT verified against a st…` → `"XXencrypted) — this certificate is NOT verified against a …` | [message] wording or interpolated detail of a problem line; the verdict comes from the flags beside it |  |
| 1277 | `"certificate's own declaration of what it does NOT certify …` → `"XXcertificate's own declaration of what it does NOT certif…` | [message] wording or interpolated detail of a problem line; the verdict comes from the flags beside it |  |
| 1277 | `"certificate's own declaration of what it does NOT certify …` → `"CERTIFICATE'S OWN DECLARATION OF WHAT IT DOES NOT CERTIFY …` | [message] wording or interpolated detail of a problem line; the verdict comes from the flags beside it |  |
| 1277 | `"certificate's own declaration of what it does NOT certify …` → `"certificate's own declaration of what it does not certify …` | [message] wording or interpolated detail of a problem line; the verdict comes from the flags beside it |  |
| 1280 | `"scope_covers"` → `"SCOPE_COVERS"` | [soundness] the scope tests alter `scope` and `scope_excludes`; none altered `scope_covers`, so a loop that looked it up under the wrong key -- never comparing it -- passed a certificate that claims more coverage than it verified | killed by `test_a_widened_scope_covers_list_is_refused` |
| 1280 | `"scope_covers"` → `"XXscope_coversXX"` | [soundness] the scope tests alter `scope` and `scope_excludes`; none altered `scope_covers`, so a loop that looked it up under the wrong key -- never comparing it -- passed a certificate that claims more coverage than it verified | killed by `test_a_widened_scope_covers_list_is_refused` |

**`inspeximus/core.py` `Inspeximus._emit_tombstone`** (19)

| line | mutation | why no test caught it | now |
|---:|---|---|---|
| 7916 | `"ts"` → `"XXtsXX"` | [unread-output] no test reads a tombstone's ts; stored under another key, the hash commits to ts=None consistently, so every chain still verifies while the time of the erasure act leaves the commitment | killed by `test_the_time_of_an_erasure_is_committed_in_its_tombstone` |
| 7916 | `"ts"` → `"TS"` | [unread-output] no test reads a tombstone's ts; stored under another key, the hash commits to ts=None consistently, so every chain still verifies while the time of the erasure act leaves the commitment | killed by `test_the_time_of_an_erasure_is_committed_in_its_tombstone` |
| 7918 | `basis is not None or authorized_by is not None or authoriza…` → `basis is not None or authorized_by is not None and authoriz…` | [equivalent] equivalent through the library: both callers (forget, declare_out_of_band_deletion) always pass a non-None basis, so the first operand decides and the others are never evaluated |  |
| 7918 | `is not` → `is` | [equivalent] equivalent through the library: both callers (forget, declare_out_of_band_deletion) always pass a non-None basis, so the first operand decides and the others are never evaluated |  |
| 7918 | `is not` → `is` | [equivalent] equivalent through the library: both callers (forget, declare_out_of_band_deletion) always pass a non-None basis, so the first operand decides and the others are never evaluated |  |
| 7933 | `is not` → `is` | [condition] the tenant tests assert the NEGATIVE (globex does not see acme's tombstone), which an unstamped tombstone also satisfies because the view filter fails closed; that acme sees its OWN erasure was never asserted | killed by `test_a_tenant_sees_its_own_erasures_and_not_another_tenants` |
| 7934 | `t["tenant"] = self.tenant` → `t["tenant"] = None` | [condition] the tenant tests assert the NEGATIVE (globex does not see acme's tombstone), which an unstamped tombstone also satisfies because the view filter fails closed; that acme sees its OWN erasure was never asserted | killed by `test_a_tenant_sees_its_own_erasures_and_not_another_tenants` |
| 7934 | `"tenant"` → `"TENANT"` | [condition] the tenant tests assert the NEGATIVE (globex does not see acme's tombstone), which an unstamped tombstone also satisfies because the view filter fails closed; that acme sees its OWN erasure was never asserted | killed by `test_a_tenant_sees_its_own_erasures_and_not_another_tenants` |
| 7934 | `"tenant"` → `"XXtenantXX"` | [condition] the tenant tests assert the NEGATIVE (globex does not see acme's tombstone), which an unstamped tombstone also satisfies because the view filter fails closed; that acme sees its OWN erasure was never asserted | killed by `test_a_tenant_sees_its_own_erasures_and_not_another_tenants` |
| 7951 | `type(e)` → `type(None)` | [message] type name inside an error message |  |
| 7954 | `"receipt signer returned no signature; refusing to append a…` → `None` | [unexecuted] no test's signer returns an empty signature, so this refusal never ran |  |
| 7954 | `"receipt signer returned no signature; refusing to append a…` → `"RECEIPT SIGNER RETURNED NO SIGNATURE; REFUSING TO APPEND A…` | [unexecuted] no test's signer returns an empty signature, so this refusal never ran |  |
| 7954 | `"receipt signer returned no signature; refusing to append a…` → `"XXreceipt signer returned no signature; refusing to append…` | [unexecuted] no test's signer returns an empty signature, so this refusal never ran |  |
| 7955 | `"unsigned tombstone while a signer is configured"` → `"XXunsigned tombstone while a signer is configuredXX"` | [unexecuted] no test's signer returns an empty signature, so this refusal never ran |  |
| 7955 | `"unsigned tombstone while a signer is configured"` → `"UNSIGNED TOMBSTONE WHILE A SIGNER IS CONFIGURED"` | [unexecuted] no test's signer returns an empty signature, so this refusal never ran |  |
| 7956 | `t["sig"] = _sig` → `t["sig"] = None` | [unread-output] the one receipt_signer test checks that the signer was called, not what the tombstone carries; nothing verified a signer-signed tombstone or its embedded key | killed by `test_tombstones_signed_through_an_external_signer_verify_against_the_pinned_key` |
| 7958 | `t["pubkey"] = self.receipt_pubkey` → `t["pubkey"] = None` | [unread-output] the one receipt_signer test checks that the signer was called, not what the tombstone carries; nothing verified a signer-signed tombstone or its embedded key | killed by `test_tombstones_signed_through_an_external_signer_verify_against_the_pinned_key` |
| 7958 | `"pubkey"` → `"PUBKEY"` | [unread-output] the one receipt_signer test checks that the signer was called, not what the tombstone carries; nothing verified a signer-signed tombstone or its embedded key | killed by `test_tombstones_signed_through_an_external_signer_verify_against_the_pinned_key` |
| 7958 | `"pubkey"` → `"XXpubkeyXX"` | [unread-output] the one receipt_signer test checks that the signer was called, not what the tombstone carries; nothing verified a signer-signed tombstone or its embedded key | killed by `test_tombstones_signed_through_an_external_signer_verify_against_the_pinned_key` |

**`inspeximus/core.py` `Inspeximus._flush_tombstones`** (7)

| line | mutation | why no test caught it | now |
|---:|---|---|---|
| 7985 | `json.dumps(self._tombstones, indent=2, ensure_ascii=False)` → `json.dumps(self._tombstones, indent=2, ensure_ascii=None)` | [format] layout of the tombstone sidecar on disk (indent, escaping); every reader parses it, so no test can see it |  |
| 7985 | `json.dumps(self._tombstones, indent=2, ensure_ascii=False)` → `json.dumps(self._tombstones, indent=None, ensure_ascii=Fals…` | [format] layout of the tombstone sidecar on disk (indent, escaping); every reader parses it, so no test can see it |  |
| 7985 | `json.dumps(self._tombstones, indent=2, ensure_ascii=False)` → `json.dumps(self._tombstones, ensure_ascii=False)` | [format] layout of the tombstone sidecar on disk (indent, escaping); every reader parses it, so no test can see it |  |
| 7985 | `json.dumps(self._tombstones, indent=2, ensure_ascii=False)` → `json.dumps(self._tombstones, indent=2, )` | [format] layout of the tombstone sidecar on disk (indent, escaping); every reader parses it, so no test can see it |  |
| 7985 | `2` → `3` | [format] layout of the tombstone sidecar on disk (indent, escaping); every reader parses it, so no test can see it |  |
| 7985 | `False` → `True` | [format] layout of the tombstone sidecar on disk (indent, escaping); every reader parses it, so no test can see it |  |
| 7990 | `type(e)` → `type(None)` | [message] type name inside an error message |  |

**`inspeximus/core.py` `Inspeximus.erasure_certificate`** (33)

| line | mutation | why no test caught it | now |
|---:|---|---|---|
| 10470 | `"erasure_certificate() is operator-only and is not availabl…` → `None` | [unexecuted] no test asks a tenant-bound handle for a certificate, so the refusal never ran (only its message is mutable here) | killed by `test_a_tenant_bound_handle_refuses_to_issue_a_certificate` |
| 10471 | `"erasure_certificate() is operator-only and is not availabl…` → `"XXerasure_certificate() is operator-only and is not availa…` | [unexecuted] no test asks a tenant-bound handle for a certificate, so the refusal never ran (only its message is mutable here) | killed by `test_a_tenant_bound_handle_refuses_to_issue_a_certificate` |
| 10471 | `"erasure_certificate() is operator-only and is not availabl…` → `"ERASURE_CERTIFICATE() IS OPERATOR-ONLY AND IS NOT AVAILABL…` | [unexecuted] no test asks a tenant-bound handle for a certificate, so the refusal never ran (only its message is mutable here) | killed by `test_a_tenant_bound_handle_refuses_to_issue_a_certificate` |
| 10472 | `"handle. The certificate must ship the whole tombstone chai…` → `"handle. the certificate must ship the whole tombstone chai…` | [unexecuted] no test asks a tenant-bound handle for a certificate, so the refusal never ran (only its message is mutable here) |  |
| 10472 | `"handle. The certificate must ship the whole tombstone chai…` → `"XXhandle. The certificate must ship the whole tombstone ch…` | [unexecuted] no test asks a tenant-bound handle for a certificate, so the refusal never ran (only its message is mutable here) | killed by `test_a_tenant_bound_handle_refuses_to_issue_a_certificate` |
| 10472 | `"handle. The certificate must ship the whole tombstone chai…` → `"HANDLE. THE CERTIFICATE MUST SHIP THE WHOLE TOMBSTONE CHAI…` | [unexecuted] no test asks a tenant-bound handle for a certificate, so the refusal never ran (only its message is mutable here) | killed by `test_a_tenant_bound_handle_refuses_to_issue_a_certificate` |
| 10473 | `"third party can re-derive it; scoping it to one tenant bre…` → `"THIRD PARTY CAN RE-DERIVE IT; SCOPING IT TO ONE TENANT BRE…` | [unexecuted] no test asks a tenant-bound handle for a certificate, so the refusal never ran (only its message is mutable here) |  |
| 10473 | `"third party can re-derive it; scoping it to one tenant bre…` → `"XXthird party can re-derive it; scoping it to one tenant b…` | [unexecuted] no test asks a tenant-bound handle for a certificate, so the refusal never ran (only its message is mutable here) |  |
| 10474 | `"signatures, and the chain carries every tenant's request_i…` → `"XXsignatures, and the chain carries every tenant's request…` | [unexecuted] no test asks a tenant-bound handle for a certificate, so the refusal never ran (only its message is mutable here) |  |
| 10474 | `"signatures, and the chain carries every tenant's request_i…` → `"SIGNATURES, AND THE CHAIN CARRIES EVERY TENANT'S REQUEST_I…` | [unexecuted] no test asks a tenant-bound handle for a certificate, so the refusal never ran (only its message is mutable here) |  |
| 10475 | `"Issue it from an unbound handle, or give each tenant its o…` → `"issue it from an unbound handle, or give each tenant its o…` | [unexecuted] no test asks a tenant-bound handle for a certificate, so the refusal never ran (only its message is mutable here) |  |
| 10475 | `"Issue it from an unbound handle, or give each tenant its o…` → `"ISSUE IT FROM AN UNBOUND HANDLE, OR GIVE EACH TENANT ITS O…` | [unexecuted] no test asks a tenant-bound handle for a certificate, so the refusal never ran (only its message is mutable here) |  |
| 10475 | `"Issue it from an unbound handle, or give each tenant its o…` → `"XXIssue it from an unbound handle, or give each tenant its…` | [unexecuted] no test asks a tenant-bound handle for a certificate, so the refusal never ran (only its message is mutable here) |  |
| 10479 | `self.verify_writes(expected_pubkey)` → `self.verify_writes(None)` | [condition] every test that pinned expected_pubkey pinned the right key, so a self_check that dropped the pin read verified: True either way | killed by `test_the_certificates_self_check_honours_the_pinned_key` |
| 10481 | `"1.0"` → `"XX1.0XX"` | [unread-output] the certificate's format version is never read by a test (nor by the verifier) | killed by `test_the_certificate_says_when_it_was_issued_in_utc` |
| 10482 | `"issued_ts"` → `"XXissued_tsXX"` | [unread-output] no test reads issued_ts / issued_iso, the time an auditor reads off the certificate | killed by `test_the_certificate_says_when_it_was_issued_in_utc` |
| 10482 | `"issued_ts"` → `"ISSUED_TS"` | [unread-output] no test reads issued_ts / issued_iso, the time an auditor reads off the certificate | killed by `test_the_certificate_says_when_it_was_issued_in_utc` |
| 10483 | `"issued_iso"` → `"XXissued_isoXX"` | [unread-output] no test reads issued_ts / issued_iso, the time an auditor reads off the certificate | killed by `test_the_certificate_says_when_it_was_issued_in_utc` |
| 10483 | `"issued_iso"` → `"ISSUED_ISO"` | [unread-output] no test reads issued_ts / issued_iso, the time an auditor reads off the certificate | killed by `test_the_certificate_says_when_it_was_issued_in_utc` |
| 10483 | `time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())` → `time.strftime("%Y-%m-%dT%H:%M:%SZ", )` | [platform] equivalent only where local time is UTC, which is where the suite runs: without its argument strftime formats local time and still appends 'Z' | killed by `test_the_issue_time_is_utc_on_a_server_that_is_not` |
| 10483 | `"%Y-%m-%dT%H:%M:%SZ"` → `"%y-%m-%dt%h:%m:%sz"` | [unread-output] no test reads issued_ts / issued_iso, the time an auditor reads off the certificate | killed by `test_the_certificate_says_when_it_was_issued_in_utc` |
| 10483 | `"%Y-%m-%dT%H:%M:%SZ"` → `"XX%Y-%m-%dT%H:%M:%SZXX"` | [unread-output] no test reads issued_ts / issued_iso, the time an auditor reads off the certificate | killed by `test_the_certificate_says_when_it_was_issued_in_utc` |
| 10483 | `"%Y-%m-%dT%H:%M:%SZ"` → `"%Y-%M-%DT%H:%M:%SZ"` | [unread-output] no test reads issued_ts / issued_iso, the time an auditor reads off the certificate | killed by `test_the_certificate_says_when_it_was_issued_in_utc` |
| 10492 | `"pubkey"` → `"PUBKEY"` | [soundness] no test puts a tombstone signed by another key into a chain, and unpinned that comparison is made against this field; without it the verifier compares nothing (finding F1) | killed by `test_a_tombstone_signed_by_another_key_is_refused_by_the_key_the_certificate_names` |
| 10492 | `"pubkey"` → `"XXpubkeyXX"` | [soundness] no test puts a tombstone signed by another key into a chain, and unpinned that comparison is made against this field; without it the verifier compares nothing (finding F1) | killed by `test_a_tombstone_signed_by_another_key_is_refused_by_the_key_the_certificate_names` |
| 10494 | `"problems"` → `"XXproblemsXX"` | [unread-output] self_check's problem list is never read by a test | killed by `test_the_certificates_self_check_honours_the_pinned_key` |
| 10494 | `"problems"` → `"PROBLEMS"` | [unread-output] self_check's problem list is never read by a test | killed by `test_the_certificates_self_check_honours_the_pinned_key` |
| 10502 | `"scope_covers"` → `"SCOPE_COVERS"` | [soundness] the verifier compares scope_covers only when present, and no test checks that the producer writes it; dropped, the coverage statement is unchecked | killed by `test_a_widened_scope_covers_list_is_refused` |
| 10502 | `"scope_covers"` → `"XXscope_coversXX"` | [soundness] the verifier compares scope_covers only when present, and no test checks that the producer writes it; dropped, the coverage statement is unchecked | killed by `test_a_widened_scope_covers_list_is_refused` |
| 10504 | `"verify_with"` → `"VERIFY_WITH"` | [unread-output] the `verify_with` hint is never read |  |
| 10504 | `"verify_with"` → `"XXverify_withXX"` | [unread-output] the `verify_with` hint is never read |  |
| 10504 | `"inspeximus.verify_erasure_certificate(cert, store_path=<fi…` → `"INSPEXIMUS.VERIFY_ERASURE_CERTIFICATE(CERT, STORE_PATH=<FI…` | [unread-output] the `verify_with` hint is never read |  |
| 10504 | `"inspeximus.verify_erasure_certificate(cert, store_path=<fi…` → `"XXinspeximus.verify_erasure_certificate(cert, store_path=<…` | [unread-output] the `verify_with` hint is never read |  |

**`inspeximus/core.py` `Inspeximus.anchor`** (4)

| line | mutation | why no test caught it | now |
|---:|---|---|---|
| 10768 | `self.merkle_root("tombstone")` → `self.merkle_root(None)` | [equivalent] equivalent: merkle_root/_merkle_leaves read the tombstone log for every kind that is not 'write' |  |
| 10768 | `"tombstone"` → `"XXtombstoneXX"` | [equivalent] equivalent: merkle_root/_merkle_leaves read the tombstone log for every kind that is not 'write' |  |
| 10768 | `"tombstone"` → `"TOMBSTONE"` | [equivalent] equivalent: merkle_root/_merkle_leaves read the tombstone log for every kind that is not 'write' |  |
| 10785 | `type(e)` → `type(None)` | [message] type name inside the witness-signer error message |  |


### Write receipts: 297 survivors

**`inspeximus/core.py` `new_receipt_keypair`** (1)

| line | mutation | why no test caught it | now |
|---:|---|---|---|
| 435 | `` "signing write receipts needs the `cryptography` package (p… `` → `` "XXsigning write receipts needs the `cryptography` package … `` | [unexecuted] the no-cryptography branch: the suite always has `cryptography` |  |

**`inspeximus/core.py` `Inspeximus._write_commit`** (17)

| line | mutation | why no test caught it | now |
|---:|---|---|---|
| 3586 | `_imm["nonce"] = _con["nonce"] = _val["nonce"] = _n` → `_imm["nonce"] = _con["nonce"] = _val["nonce"] = None` | [soundness] the guessing test compares the receipt with sha256({text, key}), the pre-2.40 formula; a preimage with `nonce: null` fails that comparison too, while being exactly as guessable -- the attacker adds the null | killed by `test_the_nonce_is_what_makes_an_erased_records_receipt_unguessable` |
| 3586 | `"nonce"` → `"NONCE"` | [format] the nonce's key name inside the three content hashes: producer and verifier share _write_commit, so the rename is consistent everywhere; it breaks only receipts already on disk, which no test re-verified | killed by `test_the_nonce_is_what_makes_an_erased_records_receipt_unguessable` |
| 3586 | `"nonce"` → `"XXnonceXX"` | [format] the nonce's key name inside the three content hashes: producer and verifier share _write_commit, so the rename is consistent everywhere; it breaks only receipts already on disk, which no test re-verified | killed by `test_the_nonce_is_what_makes_an_erased_records_receipt_unguessable` |
| 3586 | `"nonce"` → `"XXnonceXX"` | [format] the nonce's key name inside the three content hashes: producer and verifier share _write_commit, so the rename is consistent everywhere; it breaks only receipts already on disk, which no test re-verified | killed by `test_the_nonce_is_what_makes_an_erased_records_receipt_unguessable` |
| 3586 | `"nonce"` → `"NONCE"` | [format] the nonce's key name inside the three content hashes: producer and verifier share _write_commit, so the rename is consistent everywhere; it breaks only receipts already on disk, which no test re-verified | killed by `test_the_nonce_is_what_makes_an_erased_records_receipt_unguessable` |
| 3586 | `"nonce"` → `"XXnonceXX"` | [format] the nonce's key name inside the three content hashes: producer and verifier share _write_commit, so the rename is consistent everywhere; it breaks only receipts already on disk, which no test re-verified | killed by `test_a_store_written_by_3_9_1_still_verifies` |
| 3586 | `"nonce"` → `"NONCE"` | [format] the nonce's key name inside the three content hashes: producer and verifier share _write_commit, so the rename is consistent everywhere; it breaks only receipts already on disk, which no test re-verified | killed by `test_a_store_written_by_3_9_1_still_verifies` |
| 3628 | `"serving"` → `"SERVING"` | [format] a key inside status_sha256 / time_sha256: producer and verifier share _write_commit, so the rename verifies in every same-run test and breaks every receipt already on disk; nothing re-verified an older store | killed by `test_a_store_written_by_3_9_1_still_verifies` |
| 3628 | `"serving"` → `"XXservingXX"` | [format] a key inside status_sha256 / time_sha256: producer and verifier share _write_commit, so the rename verifies in every same-run test and breaks every receipt already on disk; nothing re-verified an older store | killed by `test_a_store_written_by_3_9_1_still_verifies` |
| 3629 | `"confirmed_by"` → `"CONFIRMED_BY"` | [format] a key inside status_sha256 / time_sha256: producer and verifier share _write_commit, so the rename verifies in every same-run test and breaks every receipt already on disk; nothing re-verified an older store | killed by `test_a_store_written_by_3_9_1_still_verifies` |
| 3629 | `"confirmed_by"` → `"XXconfirmed_byXX"` | [format] a key inside status_sha256 / time_sha256: producer and verifier share _write_commit, so the rename verifies in every same-run test and breaks every receipt already on disk; nothing re-verified an older store | killed by `test_a_store_written_by_3_9_1_still_verifies` |
| 3641 | `"active"` → `"XXactiveXX"` | [legacy-default] `born_status` falls back to 'active' only for a record with no status, and every record the store writes gets one (core.py:3167); only a hand-inserted record plus a backfill reaches it, which no test builds |  |
| 3641 | `"active"` → `"ACTIVE"` | [legacy-default] `born_status` falls back to 'active' only for a record with no status, and every record the store writes gets one (core.py:3167); only a hand-inserted record plus a backfill reaches it, which no test builds |  |
| 3658 | `"valid_from"` → `"VALID_FROM"` | [format] a key inside status_sha256 / time_sha256: producer and verifier share _write_commit, so the rename verifies in every same-run test and breaks every receipt already on disk; nothing re-verified an older store | killed by `test_a_store_written_by_3_9_1_still_verifies` |
| 3658 | `"valid_from"` → `"XXvalid_fromXX"` | [format] a key inside status_sha256 / time_sha256: producer and verifier share _write_commit, so the rename verifies in every same-run test and breaks every receipt already on disk; nothing re-verified an older store | killed by `test_a_store_written_by_3_9_1_still_verifies` |
| 3659 | `"valid_from_source"` → `"VALID_FROM_SOURCE"` | [format] a key inside status_sha256 / time_sha256: producer and verifier share _write_commit, so the rename verifies in every same-run test and breaks every receipt already on disk; nothing re-verified an older store | killed by `test_a_store_written_by_3_9_1_still_verifies` |
| 3659 | `"valid_from_source"` → `"XXvalid_from_sourceXX"` | [format] a key inside status_sha256 / time_sha256: producer and verifier share _write_commit, so the rename verifies in every same-run test and breaks every receipt already on disk; nothing re-verified an older store | killed by `test_a_store_written_by_3_9_1_still_verifies` |

**`inspeximus/core.py` `Inspeximus._reconcile_receipts_with_disk`** (13)

| line | mutation | why no test caught it | now |
|---:|---|---|---|
| 3712 | `and` → `or` | [equivalent] equivalent: a handle with receipts off has no in-memory chain, so reconciling it adopts the sidecar into a list nothing reads until receipts are enabled, and enable_receipts reconciles anyway |  |
| 3713 | `0` → `1` | [unread-output] the number of adopted receipts `_reconcile_receipts_with_disk` returns; every caller ignores it |  |
| 3715 | `getattr(self, "_receipts_sig", None)` → `getattr(self, "_receipts_sig", )` | [equivalent] equivalent: `_receipts_sig` is set in __init__, so the getattr default is never used |  |
| 3716 | `0` → `1` | [unread-output] the number of adopted receipts `_reconcile_receipts_with_disk` returns; every caller ignores it |  |
| 3718 | `self._receipts_path.read_text(encoding="utf-8")` → `self._receipts_path.read_text(encoding=None)` | [platform] encoding=None reads the sidecar in the locale encoding, UTF-8 here; 'UTF-8' is the same codec |  |
| 3718 | `"utf-8"` → `"UTF-8"` | [platform] encoding=None reads the sidecar in the locale encoding, UTF-8 here; 'UTF-8' is the same codec |  |
| 3720 | `0` → `1` | [unread-output] the number of adopted receipts `_reconcile_receipts_with_disk` returns; every caller ignores it |  |
| 3722 | `0` → `1` | [unread-output] the number of adopted receipts `_reconcile_receipts_with_disk` returns; every caller ignores it |  |
| 3724 | `0` → `1` | [condition] starting the prefix scan at 1 skips comparing receipt 0; every reconcile test shares a genesis receipt, so a chain that forks AT genesis was never reconciled |  |
| 3725 | `< len(disk)` → `<= len(disk)` | [unexecuted-combo] `<=` indexes one past the disk chain when the disk is a strict prefix of this handle's chain and moved; no test makes the sidecar shorter than a live handle's chain |  |
| 3727 | `self._receipts_sig = sig` → `self._receipts_sig = None` | [performance] equivalent in behaviour: the disk signature is re-read and the sidecar re-parsed on every emit instead of when it changed |  |
| 3729 | `0` → `1` | [unread-output] the number of adopted receipts `_reconcile_receipts_with_disk` returns; every caller ignores it |  |
| 3734 | `len(disk) - n` → `len(disk) + n` | [unread-output] the number of adopted receipts `_reconcile_receipts_with_disk` returns; every caller ignores it |  |

**`inspeximus/core.py` `Inspeximus._append_receipt`** (14)

| line | mutation | why no test caught it | now |
|---:|---|---|---|
| 3742 | `"ts"` → `"TS"` | [coverage-depth] the one rechain test moves an ordinary write, which has no `amends` / `amend_reason`, and never compares the moved receipt's time with the original's | killed by `test_a_rechained_amendment_keeps_what_it_amends_why_and_when` |
| 3742 | `"ts"` → `"XXtsXX"` | [coverage-depth] the one rechain test moves an ordinary write, which has no `amends` / `amend_reason`, and never compares the moved receipt's time with the original's | killed by `test_a_rechained_amendment_keeps_what_it_amends_why_and_when` |
| 3743 | `"amends"` → `"XXamendsXX"` | [coverage-depth] the one rechain test moves an ordinary write, which has no `amends` / `amend_reason`, and never compares the moved receipt's time with the original's | killed by `test_a_rechained_amendment_keeps_what_it_amends_why_and_when` |
| 3743 | `"amends"` → `"AMENDS"` | [coverage-depth] the one rechain test moves an ordinary write, which has no `amends` / `amend_reason`, and never compares the moved receipt's time with the original's | killed by `test_a_rechained_amendment_keeps_what_it_amends_why_and_when` |
| 3743 | `"amend_reason"` → `"XXamend_reasonXX"` | [coverage-depth] the one rechain test moves an ordinary write, which has no `amends` / `amend_reason`, and never compares the moved receipt's time with the original's | killed by `test_a_rechained_amendment_keeps_what_it_amends_why_and_when` |
| 3743 | `"amend_reason"` → `"AMEND_REASON"` | [coverage-depth] the one rechain test moves an ordinary write, which has no `amends` / `amend_reason`, and never compares the moved receipt's time with the original's | killed by `test_a_rechained_amendment_keeps_what_it_amends_why_and_when` |
| 3745 | `r[k] = old[k]` → `r[k] = None` | [coverage-depth] the one rechain test moves an ordinary write, which has no `amends` / `amend_reason`, and never compares the moved receipt's time with the original's | killed by `test_a_rechained_amendment_keeps_what_it_amends_why_and_when` |
| 3754 | `f"receipt signer failed ({type(e).__name__}: {e}); refusing…` → `None` | [unexecuted] no test's receipt signer raises or returns an empty signature, so these refusals never ran (only their messages are mutable here) |  |
| 3754 | `type(e)` → `type(None)` | [unexecuted] no test's receipt signer raises or returns an empty signature, so these refusals never ran (only their messages are mutable here) |  |
| 3757 | `"receipt signer returned no signature; refusing to append a…` → `None` | [unexecuted] no test's receipt signer raises or returns an empty signature, so these refusals never ran (only their messages are mutable here) |  |
| 3757 | `"receipt signer returned no signature; refusing to append a…` → `"RECEIPT SIGNER RETURNED NO SIGNATURE; REFUSING TO APPEND A…` | [unexecuted] no test's receipt signer raises or returns an empty signature, so these refusals never ran (only their messages are mutable here) |  |
| 3757 | `"receipt signer returned no signature; refusing to append a…` → `"XXreceipt signer returned no signature; refusing to append…` | [unexecuted] no test's receipt signer raises or returns an empty signature, so these refusals never ran (only their messages are mutable here) |  |
| 3758 | `"receipt while a signer is configured"` → `"XXreceipt while a signer is configuredXX"` | [unexecuted] no test's receipt signer raises or returns an empty signature, so these refusals never ran (only their messages are mutable here) |  |
| 3758 | `"receipt while a signer is configured"` → `"RECEIPT WHILE A SIGNER IS CONFIGURED"` | [unexecuted] no test's receipt signer raises or returns an empty signature, so these refusals never ran (only their messages are mutable here) |  |

**`inspeximus/core.py` `Inspeximus._emit_write_receipt`** (12)

| line | mutation | why no test caught it | now |
|---:|---|---|---|
| 3769 | `""` → `"XXXX"` | [equivalent] equivalent through the library: every caller that passes `amends` also passes `reason` explicitly, so the default is never used |  |
| 3777 | `rec.get("ts")` → `rec.get(None)` | [unread-output] no test compares a receipt's `ts` with its record's; `ts` is inside the hash, so a receipt with ts None is a consistent link that says nothing about when | killed by `test_a_receipt_records_when_its_record_was_written` |
| 3777 | `"ts"` → `"XXtsXX"` | [unread-output] no test compares a receipt's `ts` with its record's; `ts` is inside the hash, so a receipt with ts None is a consistent link that says nothing about when | killed by `test_a_receipt_records_when_its_record_was_written` |
| 3777 | `"ts"` → `"TS"` | [unread-output] no test compares a receipt's `ts` with its record's; `ts` is inside the hash, so a receipt with ts None is a consistent link that says nothing about when | killed by `test_a_receipt_records_when_its_record_was_written` |
| 3809 | `json.dumps(self._receipts, indent=2, ensure_ascii=False)` → `json.dumps(self._receipts, indent=2, )` | [format] layout of the receipt sidecar on disk (indent, escaping); every reader parses it |  |
| 3809 | `json.dumps(self._receipts, indent=2, ensure_ascii=False)` → `json.dumps(self._receipts, indent=2, ensure_ascii=None)` | [format] layout of the receipt sidecar on disk (indent, escaping); every reader parses it |  |
| 3809 | `json.dumps(self._receipts, indent=2, ensure_ascii=False)` → `json.dumps(self._receipts, ensure_ascii=False)` | [format] layout of the receipt sidecar on disk (indent, escaping); every reader parses it |  |
| 3809 | `json.dumps(self._receipts, indent=2, ensure_ascii=False)` → `json.dumps(self._receipts, indent=None, ensure_ascii=False)` | [format] layout of the receipt sidecar on disk (indent, escaping); every reader parses it |  |
| 3809 | `2` → `3` | [format] layout of the receipt sidecar on disk (indent, escaping); every reader parses it |  |
| 3809 | `False` → `True` | [format] layout of the receipt sidecar on disk (indent, escaping); every reader parses it |  |
| 3815 | `self._sidecar_errors["receipts"] = f"{self._receipts_path}:…` → `self._sidecar_errors["receipts"] = None` | [message] the text of the sidecar error; verify_writes reports the error whatever it says |  |
| 3815 | `type(e)` → `type(None)` | [message] the text of the sidecar error; verify_writes reports the error whatever it says |  |

**`inspeximus/core.py` `Inspeximus.enable_receipts`** (83)

| line | mutation | why no test caught it | now |
|---:|---|---|---|
| 4046 | `""` → `"XXXX"` | [unread-output] enable_receipts() is never asserted to record 'unstated' when called without a reason | killed by `test_the_backfill_genesis_root_commits_to_the_records_it_covers` |
| 4070 | `ValueError("this store signs through receipt_signer; pass n…` → `ValueError(None)` | [message] wording of a refusal; the only one any test reaches (a different key) is asserted with pytest.raises(ValueError) alone |  |
| 4070 | `"this store signs through receipt_signer; pass no receipt_k…` → `"XXthis store signs through receipt_signer; pass no receipt…` | [message] wording of a refusal; the only one any test reaches (a different key) is asserted with pytest.raises(ValueError) alone |  |
| 4070 | `"this store signs through receipt_signer; pass no receipt_k…` → `"THIS STORE SIGNS THROUGH RECEIPT_SIGNER; PASS NO RECEIPT_K…` | [message] wording of a refusal; the only one any test reaches (a different key) is asserted with pytest.raises(ValueError) alone |  |
| 4072 | `` "signing write receipts needs the `cryptography` package " … `` → `None` | [message] wording of a refusal; the only one any test reaches (a different key) is asserted with pytest.raises(ValueError) alone |  |
| 4072 | `` "signing write receipts needs the `cryptography` package " `` → `` "SIGNING WRITE RECEIPTS NEEDS THE `CRYPTOGRAPHY` PACKAGE " `` | [message] wording of a refusal; the only one any test reaches (a different key) is asserted with pytest.raises(ValueError) alone |  |
| 4072 | `` "signing write receipts needs the `cryptography` package " `` → `` "XXsigning write receipts needs the `cryptography` package … `` | [message] wording of a refusal; the only one any test reaches (a different key) is asserted with pytest.raises(ValueError) alone |  |
| 4073 | `"(pip install cryptography)"` → `"XX(pip install cryptography)XX"` | [message] wording of a refusal; the only one any test reaches (a different key) is asserted with pytest.raises(ValueError) alone |  |
| 4073 | `"(pip install cryptography)"` → `"(PIP INSTALL CRYPTOGRAPHY)"` | [message] wording of a refusal; the only one any test reaches (a different key) is asserted with pytest.raises(ValueError) alone |  |
| 4078 | `"receipt_key must be a 32-byte Ed25519 private key as hex "…` → `None` | [message] wording of a refusal; the only one any test reaches (a different key) is asserted with pytest.raises(ValueError) alone |  |
| 4078 | `"receipt_key must be a 32-byte Ed25519 private key as hex "` → `"RECEIPT_KEY MUST BE A 32-BYTE ED25519 PRIVATE KEY AS HEX "` | [message] wording of a refusal; the only one any test reaches (a different key) is asserted with pytest.raises(ValueError) alone |  |
| 4078 | `"receipt_key must be a 32-byte Ed25519 private key as hex "` → `"XXreceipt_key must be a 32-byte Ed25519 private key as hex…` | [message] wording of a refusal; the only one any test reaches (a different key) is asserted with pytest.raises(ValueError) alone |  |
| 4078 | `"receipt_key must be a 32-byte Ed25519 private key as hex "` → `"receipt_key must be a 32-byte ed25519 private key as hex "` | [message] wording of a refusal; the only one any test reaches (a different key) is asserted with pytest.raises(ValueError) alone |  |
| 4079 | `"(use new_receipt_keypair()); got an unusable value"` → `"(USE NEW_RECEIPT_KEYPAIR()); GOT AN UNUSABLE VALUE"` | [message] wording of a refusal; the only one any test reaches (a different key) is asserted with pytest.raises(ValueError) alone |  |
| 4079 | `"(use new_receipt_keypair()); got an unusable value"` → `"XX(use new_receipt_keypair()); got an unusable valueXX"` | [message] wording of a refusal; the only one any test reaches (a different key) is asserted with pytest.raises(ValueError) alone |  |
| 4081 | `"receipt_key does not match the key this store already sign…` → `None` | [message] wording of a refusal; the only one any test reaches (a different key) is asserted with pytest.raises(ValueError) alone |  |
| 4081 | `"receipt_key does not match the key this store already sign…` → `"XXreceipt_key does not match the key this store already si…` | [message] wording of a refusal; the only one any test reaches (a different key) is asserted with pytest.raises(ValueError) alone |  |
| 4081 | `"receipt_key does not match the key this store already sign…` → `"RECEIPT_KEY DOES NOT MATCH THE KEY THIS STORE ALREADY SIGN…` | [message] wording of a refusal; the only one any test reaches (a different key) is asserted with pytest.raises(ValueError) alone |  |
| 4082 | `"a chain signed by two keys is two chains"` → `"A CHAIN SIGNED BY TWO KEYS IS TWO CHAINS"` | [message] wording of a refusal; the only one any test reaches (a different key) is asserted with pytest.raises(ValueError) alone |  |
| 4082 | `"a chain signed by two keys is two chains"` → `"XXa chain signed by two keys is two chainsXX"` | [message] wording of a refusal; the only one any test reaches (a different key) is asserted with pytest.raises(ValueError) alone |  |
| 4085 | `was_enabled = self.receipts_enabled` → `was_enabled = None` | [equivalent] equivalent: with was_enabled None the sidecar is loaded here even when receipts were already on, but only if the in-memory chain is empty, and then _reconcile_receipts_with_disk on the next line adopts the same file |  |
| 4087 | `self._receipts_path is None and self.path` → `self._receipts_path is None or self.path` | [unreachable] the receipts path is always set at construction, so `self._receipts_path is None` never holds and the path is never rebuilt here |  |
| 4087 | `is` → `is not` | [unreachable] the receipts path is always set at construction, so `self._receipts_path is None` never holds and the path is never rebuilt here |  |
| 4088 | `self._receipts_path = self.path.parent / (self.path.name + …` → `self._receipts_path = None` | [unreachable] the receipts path is always set at construction, so `self._receipts_path is None` never holds and the path is never rebuilt here |  |
| 4088 | `self.path.parent / (self.path.name + ".receipts.json")` → `self.path.parent * (self.path.name + ".receipts.json")` | [unreachable] the receipts path is always set at construction, so `self._receipts_path is None` never holds and the path is never rebuilt here |  |
| 4088 | `+` → `-` | [unreachable] the receipts path is always set at construction, so `self._receipts_path is None` never holds and the path is never rebuilt here |  |
| 4088 | `".receipts.json"` → `"XX.receipts.jsonXX"` | [unreachable] the receipts path is always set at construction, so `self._receipts_path is None` never holds and the path is never rebuilt here |  |
| 4088 | `".receipts.json"` → `".RECEIPTS.JSON"` | [unreachable] the receipts path is always set at construction, so `self._receipts_path is None` never holds and the path is never rebuilt here |  |
| 4089 | `not was_enabled and self._receipts_path` → `not was_enabled or self._receipts_path` | [redundant] loading the sidecar here is redundant with _reconcile_receipts_with_disk two lines below, which adopts the same chain; with the load skipped, broken or inverted, the chain comes out the same, and the branch that loads it has never run |  |
| 4089 | `not was_enabled` → `was_enabled` | [redundant] loading the sidecar here is redundant with _reconcile_receipts_with_disk two lines below, which adopts the same chain; with the load skipped, broken or inverted, the chain comes out the same, and the branch that loads it has never run |  |
| 4089 | `not was_enabled and self._receipts_path and self._receipts_…` → `not was_enabled and self._receipts_path and self._receipts_…` | [redundant] loading the sidecar here is redundant with _reconcile_receipts_with_disk two lines below, which adopts the same chain; with the load skipped, broken or inverted, the chain comes out the same, and the branch that loads it has never run |  |
| 4089 | `not was_enabled and self._receipts_path and self._receipts_…` → `not was_enabled and self._receipts_path or self._receipts_p…` | [redundant] loading the sidecar here is redundant with _reconcile_receipts_with_disk two lines below, which adopts the same chain; with the load skipped, broken or inverted, the chain comes out the same, and the branch that loads it has never run |  |
| 4089 | `not self._receipts` → `self._receipts` | [redundant] loading the sidecar here is redundant with _reconcile_receipts_with_disk two lines below, which adopts the same chain; with the load skipped, broken or inverted, the chain comes out the same, and the branch that loads it has never run |  |
| 4092 | `self._receipts = json.loads(self._receipts_path.read_text(e…` → `self._receipts = None` | [redundant] loading the sidecar here is redundant with _reconcile_receipts_with_disk two lines below, which adopts the same chain; with the load skipped, broken or inverted, the chain comes out the same, and the branch that loads it has never run |  |
| 4092 | `json.loads(self._receipts_path.read_text(encoding="utf-8"))` → `json.loads(None)` | [redundant] loading the sidecar here is redundant with _reconcile_receipts_with_disk two lines below, which adopts the same chain; with the load skipped, broken or inverted, the chain comes out the same, and the branch that loads it has never run |  |
| 4092 | `self._receipts_path.read_text(encoding="utf-8")` → `self._receipts_path.read_text(encoding=None)` | [redundant] loading the sidecar here is redundant with _reconcile_receipts_with_disk two lines below, which adopts the same chain; with the load skipped, broken or inverted, the chain comes out the same, and the branch that loads it has never run |  |
| 4092 | `"utf-8"` → `"UTF-8"` | [redundant] loading the sidecar here is redundant with _reconcile_receipts_with_disk two lines below, which adopts the same chain; with the load skipped, broken or inverted, the chain comes out the same, and the branch that loads it has never run |  |
| 4092 | `"utf-8"` → `"XXutf-8XX"` | [redundant] loading the sidecar here is redundant with _reconcile_receipts_with_disk two lines below, which adopts the same chain; with the load skipped, broken or inverted, the chain comes out the same, and the branch that loads it has never run |  |
| 4094 | `self._receipts = []` → `self._receipts = None` | [redundant] loading the sidecar here is redundant with _reconcile_receipts_with_disk two lines below, which adopts the same chain; with the load skipped, broken or inverted, the chain comes out the same, and the branch that loads it has never run |  |
| 4095 | `self._receipts_sig = self._receipts_disk_sig()` → `self._receipts_sig = None` | [redundant] loading the sidecar here is redundant with _reconcile_receipts_with_disk two lines below, which adopts the same chain; with the load skipped, broken or inverted, the chain comes out the same, and the branch that loads it has never run |  |
| 4099 | `"retirements_declared"` → `"XXretirements_declaredXX"` | [unread-output] `retirements_declared` of the idempotent second call is never read | killed by `test_a_second_call_reports_nothing_declared` |
| 4099 | `"retirements_declared"` → `"RETIREMENTS_DECLARED"` | [unread-output] `retirements_declared` of the idempotent second call is never read | killed by `test_a_second_call_reports_nothing_declared` |
| 4099 | `0` → `1` | [unread-output] `retirements_declared` of the idempotent second call is never read | killed by `test_a_second_call_reports_nothing_declared` |
| 4103 | `is not` → `is` | [unread-output] only the signed case of the `signed` flag is asserted; an unsigned backfill reporting signed: True passed | killed by `test_enable_receipts_says_whether_it_signed` |
| 4103 | `and` → `or` | [unread-output] only the signed case of the `signed` flag is asserted; an unsigned backfill reporting signed: True passed | killed by `test_enable_receipts_says_whether_it_signed` |
| 4105 | `self._receipts_path and not self._receipts_path.exists()` → `self._receipts_path or not self._receipts_path.exists()` | [equivalent] equivalent: with `or`, a sidecar that already exists is rewritten with the chain it already holds |  |
| 4105 | `not self._receipts_path.exists()` → `self._receipts_path.exists()` | [condition] no test enables receipts on a store with nothing to cover and then looks for the sidecar | killed by `test_enabling_receipts_on_an_empty_store_creates_the_sidecar` |
| 4108 | `r.get("ts")` → `r.get(None)` | [coverage-depth] the backfill tests never compare the order of the backfill receipts with the order the records were written | killed by `test_the_backfill_follows_write_order_and_keeps_each_records_time` |
| 4108 | `r.get("ts") or 0` → `r.get("ts") and 0` | [coverage-depth] the backfill tests never compare the order of the backfill receipts with the order the records were written | killed by `test_the_backfill_follows_write_order_and_keeps_each_records_time` |
| 4108 | `"ts"` → `"TS"` | [coverage-depth] the backfill tests never compare the order of the backfill receipts with the order the records were written | killed by `test_the_backfill_follows_write_order_and_keeps_each_records_time` |
| 4108 | `"ts"` → `"XXtsXX"` | [coverage-depth] the backfill tests never compare the order of the backfill receipts with the order the records were written | killed by `test_the_backfill_follows_write_order_and_keeps_each_records_time` |
| 4108 | `0` → `1` | [equivalent] equivalent: every record carries a `ts`, so the sort-key default is never used |  |
| 4111 | `_canon(c)` → `_canon(None)` | [soundness] the tests assert genesis_root exists and is 64 hex characters, which a root over nulls also is; nothing re-derived it from the commitments it claims to cover | killed by `test_the_backfill_genesis_root_commits_to_the_records_it_covers` |
| 4112 | `now = time.time()` → `now = None` | [unread-output] the backfill marker's `n` / `at` / `reason` are never read back (only genesis_root is) | killed by `test_the_backfill_genesis_root_commits_to_the_records_it_covers` |
| 4113 | `"n"` → `"XXnXX"` | [unread-output] the backfill marker's `n` / `at` / `reason` are never read back (only genesis_root is) | killed by `test_the_backfill_genesis_root_commits_to_the_records_it_covers` |
| 4113 | `"n"` → `"N"` | [unread-output] the backfill marker's `n` / `at` / `reason` are never read back (only genesis_root is) | killed by `test_the_backfill_genesis_root_commits_to_the_records_it_covers` |
| 4113 | `"at"` → `"AT"` | [unread-output] the backfill marker's `n` / `at` / `reason` are never read back (only genesis_root is) | killed by `test_the_backfill_genesis_root_commits_to_the_records_it_covers` |
| 4113 | `"at"` → `"XXatXX"` | [unread-output] the backfill marker's `n` / `at` / `reason` are never read back (only genesis_root is) | killed by `test_the_backfill_genesis_root_commits_to_the_records_it_covers` |
| 4114 | `"reason"` → `"REASON"` | [unread-output] the backfill marker's `n` / `at` / `reason` are never read back (only genesis_root is) | killed by `test_the_backfill_genesis_root_commits_to_the_records_it_covers` |
| 4114 | `"reason"` → `"XXreasonXX"` | [unread-output] the backfill marker's `n` / `at` / `reason` are never read back (only genesis_root is) | killed by `test_the_backfill_genesis_root_commits_to_the_records_it_covers` |
| 4114 | `str(reason)` → `str(None)` | [unread-output] the backfill marker's `n` / `at` / `reason` are never read back (only genesis_root is) | killed by `test_the_backfill_genesis_root_commits_to_the_records_it_covers` |
| 4114 | `or` → `and` | [unread-output] the backfill marker's `n` / `at` / `reason` are never read back (only genesis_root is) | killed by `test_the_backfill_genesis_root_commits_to_the_records_it_covers` |
| 4114 | `"unstated"` → `"XXunstatedXX"` | [unread-output] the backfill marker's `n` / `at` / `reason` are never read back (only genesis_root is) | killed by `test_the_backfill_genesis_root_commits_to_the_records_it_covers` |
| 4114 | `"unstated"` → `"UNSTATED"` | [unread-output] the backfill marker's `n` / `at` / `reason` are never read back (only genesis_root is) | killed by `test_the_backfill_genesis_root_commits_to_the_records_it_covers` |
| 4114 | `200` → `201` | [unread-output] the backfill marker's `n` / `at` / `reason` are never read back (only genesis_root is) | killed by `test_the_backfill_genesis_root_commits_to_the_records_it_covers` |
| 4116 | `"ts"` → `"TS"` | [unread-output] the backfill receipts' `ts` is never compared with the records' | killed by `test_the_backfill_follows_write_order_and_keeps_each_records_time` |
| 4116 | `"ts"` → `"XXtsXX"` | [unread-output] the backfill receipts' `ts` is never compared with the records' | killed by `test_the_backfill_follows_write_order_and_keeps_each_records_time` |
| 4116 | `rec.get("ts")` → `rec.get(None)` | [unread-output] the backfill receipts' `ts` is never compared with the records' | killed by `test_the_backfill_follows_write_order_and_keeps_each_records_time` |
| 4116 | `"ts"` → `"TS"` | [unread-output] the backfill receipts' `ts` is never compared with the records' | killed by `test_the_backfill_follows_write_order_and_keeps_each_records_time` |
| 4116 | `"ts"` → `"XXtsXX"` | [unread-output] the backfill receipts' `ts` is never compared with the records' | killed by `test_the_backfill_follows_write_order_and_keeps_each_records_time` |
| 4129 | `"ts"` → `"XXtsXX"` | [coverage-depth] the one test of a retirement declared at backfill checks that the store verifies afterwards; a declaration without `commit` is checked against nothing, so it verified too | killed by `test_a_retirement_declared_at_backfill_is_a_committed_amendment` |
| 4129 | `"ts"` → `"TS"` | [coverage-depth] the one test of a retirement declared at backfill checks that the store verifies afterwards; a declaration without `commit` is checked against nothing, so it verified too | killed by `test_a_retirement_declared_at_backfill_is_a_committed_amendment` |
| 4129 | `rec.get("ts")` → `rec.get(None)` | [coverage-depth] the one test of a retirement declared at backfill checks that the store verifies afterwards; a declaration without `commit` is checked against nothing, so it verified too | killed by `test_a_retirement_declared_at_backfill_is_a_committed_amendment` |
| 4129 | `"ts"` → `"XXtsXX"` | [coverage-depth] the one test of a retirement declared at backfill checks that the store verifies afterwards; a declaration without `commit` is checked against nothing, so it verified too | killed by `test_a_retirement_declared_at_backfill_is_a_committed_amendment` |
| 4129 | `"ts"` → `"TS"` | [coverage-depth] the one test of a retirement declared at backfill checks that the store verifies afterwards; a declaration without `commit` is checked against nothing, so it verified too | killed by `test_a_retirement_declared_at_backfill_is_a_committed_amendment` |
| 4130 | `"commit"` → `"COMMIT"` | [coverage-depth] the one test of a retirement declared at backfill checks that the store verifies afterwards; a declaration without `commit` is checked against nothing, so it verified too | killed by `test_a_retirement_declared_at_backfill_is_a_committed_amendment` |
| 4130 | `"commit"` → `"XXcommitXX"` | [coverage-depth] the one test of a retirement declared at backfill checks that the store verifies afterwards; a declaration without `commit` is checked against nothing, so it verified too | killed by `test_a_retirement_declared_at_backfill_is_a_committed_amendment` |
| 4131 | `"status_sha256"` → `"STATUS_SHA256"` | [coverage-depth] the one test of a retirement declared at backfill checks that the store verifies afterwards; a declaration without `commit` is checked against nothing, so it verified too | killed by `test_a_retirement_declared_at_backfill_is_a_committed_amendment` |
| 4131 | `"status_sha256"` → `"XXstatus_sha256XX"` | [coverage-depth] the one test of a retirement declared at backfill checks that the store verifies afterwards; a declaration without `commit` is checked against nothing, so it verified too | killed by `test_a_retirement_declared_at_backfill_is_a_committed_amendment` |
| 4132 | `"amend_reason"` → `"AMEND_REASON"` | [coverage-depth] the one test of a retirement declared at backfill checks that the store verifies afterwards; a declaration without `commit` is checked against nothing, so it verified too | killed by `test_a_retirement_declared_at_backfill_is_a_committed_amendment` |
| 4132 | `"amend_reason"` → `"XXamend_reasonXX"` | [coverage-depth] the one test of a retirement declared at backfill checks that the store verifies afterwards; a declaration without `commit` is checked against nothing, so it verified too | killed by `test_a_retirement_declared_at_backfill_is_a_committed_amendment` |
| 4132 | `"retired while receipts were off; declared at backfill"` → `"RETIRED WHILE RECEIPTS WERE OFF; DECLARED AT BACKFILL"` | [coverage-depth] the one test of a retirement declared at backfill checks that the store verifies afterwards; a declaration without `commit` is checked against nothing, so it verified too | killed by `test_a_retirement_declared_at_backfill_is_a_committed_amendment` |
| 4132 | `"retired while receipts were off; declared at backfill"` → `"XXretired while receipts were off; declared at backfillXX"` | [coverage-depth] the one test of a retirement declared at backfill checks that the store verifies afterwards; a declaration without `commit` is checked against nothing, so it verified too | killed by `test_a_retirement_declared_at_backfill_is_a_committed_amendment` |

**`inspeximus/core.py` `Inspeximus._persist_receipts`** (14)

| line | mutation | why no test caught it | now |
|---:|---|---|---|
| 4146 | `json.dumps(self._receipts, indent=2, ensure_ascii=False)` → `json.dumps(self._receipts, indent=2, ensure_ascii=None)` | [format] layout of the receipt sidecar on disk (indent, escaping); every reader parses it |  |
| 4146 | `json.dumps(self._receipts, indent=2, ensure_ascii=False)` → `json.dumps(self._receipts, indent=2, )` | [format] layout of the receipt sidecar on disk (indent, escaping); every reader parses it |  |
| 4146 | `json.dumps(self._receipts, indent=2, ensure_ascii=False)` → `json.dumps(self._receipts, indent=None, ensure_ascii=False)` | [format] layout of the receipt sidecar on disk (indent, escaping); every reader parses it |  |
| 4146 | `json.dumps(self._receipts, indent=2, ensure_ascii=False)` → `json.dumps(self._receipts, ensure_ascii=False)` | [format] layout of the receipt sidecar on disk (indent, escaping); every reader parses it |  |
| 4146 | `2` → `3` | [format] layout of the receipt sidecar on disk (indent, escaping); every reader parses it |  |
| 4146 | `False` → `True` | [format] layout of the receipt sidecar on disk (indent, escaping); every reader parses it |  |
| 4147 | `self._receipts_sig = self._receipts_disk_sig()` → `self._receipts_sig = None` | [performance] equivalent in behaviour: the next emit re-reads the sidecar it just wrote |  |
| 4148 | `self._sidecar_errors.pop("receipts", None)` → `self._sidecar_errors.pop(None, None)` | [condition] no test recovers from a failed sidecar write, so the error left behind by it is never shown to be cleared |  |
| 4148 | `"receipts"` → `"XXreceiptsXX"` | [condition] no test recovers from a failed sidecar write, so the error left behind by it is never shown to be cleared |  |
| 4148 | `"receipts"` → `"RECEIPTS"` | [condition] no test recovers from a failed sidecar write, so the error left behind by it is never shown to be cleared |  |
| 4150 | `self._sidecar_errors["receipts"] = f"{self._receipts_path}:…` → `self._sidecar_errors["receipts"] = None` | [unexecuted] no test makes the backfill's sidecar write fail |  |
| 4150 | `"receipts"` → `"XXreceiptsXX"` | [unexecuted] no test makes the backfill's sidecar write fail |  |
| 4150 | `"receipts"` → `"RECEIPTS"` | [unexecuted] no test makes the backfill's sidecar write fail |  |
| 4150 | `type(e)` → `type(None)` | [unexecuted] no test makes the backfill's sidecar write fail |  |

**`inspeximus/core.py` `Inspeximus._chain_holds_tip`** (12)

| line | mutation | why no test caught it | now |
|---:|---|---|---|
| 4160 | `n0 <= 0 or not tip0` → `n0 <= 0 and not tip0` | [unreachable] differ only for a head that recorded zero writes, or one with no tip; verify_writes checks the head only when its genesis matches a non-empty chain, which such a head never has |  |
| 4160 | `<= 0` → `< 0` | [unreachable] differ only for a head that recorded zero writes, or one with no tip; verify_writes checks the head only when its genesis matches a non-empty chain, which such a head never has |  |
| 4160 | `0` → `1` | [unreachable] differs only for a head with n_writes <= 1 or no tip; verify_writes consults the head only when its genesis equals the chain's first hash, and then position 0 holds the tip by construction |  |
| 4161 | `True` → `False` | [unreachable] differs only for a head with n_writes <= 1 or no tip; verify_writes consults the head only when its genesis equals the chain's first hash, and then position 0 holds the tip by construction |  |
| 4162 | `>= n0` → `> n0` | [equivalent] equivalent: the positional check is subsumed by the `any(...)` fallback on the next line, which finds the tip at any position |  |
| 4162 | `receipts[n0 - 1].get("hash")` → `receipts[n0 - 1].get(None)` | [equivalent] equivalent: the positional check is subsumed by the `any(...)` fallback on the next line, which finds the tip at any position |  |
| 4162 | `1` → `2` | [equivalent] equivalent: the positional check is subsumed by the `any(...)` fallback on the next line, which finds the tip at any position |  |
| 4162 | `"hash"` → `"HASH"` | [equivalent] equivalent: the positional check is subsumed by the `any(...)` fallback on the next line, which finds the tip at any position |  |
| 4162 | `"hash"` → `"XXhashXX"` | [equivalent] equivalent: the positional check is subsumed by the `any(...)` fallback on the next line, which finds the tip at any position |  |
| 4164 | `r.get("hash")` → `r.get(None)` | [equivalent] equivalent in reachable states: without a rechain the tip is where the positional check looks, and a rechained tip is found through `rechained_from` |  |
| 4164 | `"hash"` → `"HASH"` | [equivalent] equivalent in reachable states: without a rechain the tip is where the positional check looks, and a rechained tip is found through `rechained_from` |  |
| 4164 | `"hash"` → `"XXhashXX"` | [equivalent] equivalent in reachable states: without a rechain the tip is where the positional check looks, and a rechained tip is found through `rechained_from` |  |

**`inspeximus/core.py` `Inspeximus.verify_writes`** (131)

| line | mutation | why no test caught it | now |
|---:|---|---|---|
| 6290 | `isinstance(r, dict) and r.get("id")` → `isinstance(r, dict) or r.get("id")` | [defensive] rows loaded from the store are always dicts with an id, so `or` and `and` agree on every row the loader returns |  |
| 6297 | `isinstance(_r, dict) and _r.get("id")` → `isinstance(_r, dict) or _r.get("id")` | [defensive] rows loaded from the store are always dicts with an id, so `or` and `and` agree on every row the loader returns |  |
| 6305 | `"_"` → `"XX_XX"` | [message] detail of the 'store not persisted' problem (which ids, which fields differ, how many are listed); the verdict comes from whether the problem is raised |  |
| 6311 | `_diverged.append("%s (%s)" % (_rid, _e))` → `_diverged.append(None)` | [message] detail of the 'store not persisted' problem (which ids, which fields differ, how many are listed); the verdict comes from whether the problem is raised |  |
| 6311 | `"%s (%s)"` → `"%S (%S)"` | [message] detail of the 'store not persisted' problem (which ids, which fields differ, how many are listed); the verdict comes from whether the problem is raised |  |
| 6311 | `"%s (%s)" % (_rid, _e)` → `"%s (%s)" / (_rid, _e)` | [message] detail of the 'store not persisted' problem (which ids, which fields differ, how many are listed); the verdict comes from whether the problem is raised |  |
| 6311 | `"%s (%s)"` → `"XX%s (%s)XX"` | [message] detail of the 'store not persisted' problem (which ids, which fields differ, how many are listed); the verdict comes from whether the problem is raised |  |
| 6312 | `continue` → `break` | [message] detail of the 'store not persisted' problem (which ids, which fields differ, how many are listed); the verdict comes from whether the problem is raised |  |
| 6314 | `_diverged.append("%s (absent from the store file)" % _rid)` → `_diverged.append(None)` | [message] detail of the 'store not persisted' problem (which ids, which fields differ, how many are listed); the verdict comes from whether the problem is raised |  |
| 6314 | `"%s (absent from the store file)" % _rid` → `"%s (absent from the store file)" / _rid` | [message] detail of the 'store not persisted' problem (which ids, which fields differ, how many are listed); the verdict comes from whether the problem is raised |  |
| 6314 | `"%s (absent from the store file)"` → `"%S (ABSENT FROM THE STORE FILE)"` | [message] detail of the 'store not persisted' problem (which ids, which fields differ, how many are listed); the verdict comes from whether the problem is raised |  |
| 6314 | `"%s (absent from the store file)"` → `"XX%s (absent from the store file)XX"` | [message] detail of the 'store not persisted' problem (which ids, which fields differ, how many are listed); the verdict comes from whether the problem is raised |  |
| 6323 | `set(_a) \| set(_b)` → `set(_a) & set(_b)` | [message] detail of the 'store not persisted' problem (which ids, which fields differ, how many are listed); the verdict comes from whether the problem is raised |  |
| 6323 | `_a.get(k)` → `_a.get(None)` | [message] detail of the 'store not persisted' problem (which ids, which fields differ, how many are listed); the verdict comes from whether the problem is raised |  |
| 6323 | `_b.get(k)` → `_b.get(None)` | [message] detail of the 'store not persisted' problem (which ids, which fields differ, how many are listed); the verdict comes from whether the problem is raised |  |
| 6325 | `_keys = []` → `_keys = None` | [message] detail of the 'store not persisted' problem (which ids, which fields differ, how many are listed); the verdict comes from whether the problem is raised |  |
| 6326 | `"%s (differs in %s)"` → `"XX%s (differs in %s)XX"` | [message] detail of the 'store not persisted' problem (which ids, which fields differ, how many are listed); the verdict comes from whether the problem is raised |  |
| 6326 | `", "` → `"XX, XX"` | [message] detail of the 'store not persisted' problem (which ids, which fields differ, how many are listed); the verdict comes from whether the problem is raised |  |
| 6326 | `"content"` → `"XXcontentXX"` | [message] detail of the 'store not persisted' problem (which ids, which fields differ, how many are listed); the verdict comes from whether the problem is raised |  |
| 6326 | `"content"` → `"CONTENT"` | [message] detail of the 'store not persisted' problem (which ids, which fields differ, how many are listed); the verdict comes from whether the problem is raised |  |
| 6329 | `"store not persisted: %d record(s) in memory do not match t…` → `"XXstore not persisted: %d record(s) in memory do not match…` | [message] detail of the 'store not persisted' problem (which ids, which fields differ, how many are listed); the verdict comes from whether the problem is raised |  |
| 6330 | `"(in-memory state has not reached disk)"` → `"XX(in-memory state has not reached disk)XX"` | [message] detail of the 'store not persisted' problem (which ids, which fields differ, how many are listed); the verdict comes from whether the problem is raised |  |
| 6330 | `"(in-memory state has not reached disk)"` → `"(IN-MEMORY STATE HAS NOT REACHED DISK)"` | [message] detail of the 'store not persisted' problem (which ids, which fields differ, how many are listed); the verdict comes from whether the problem is raised |  |
| 6331 | `", "` → `"XX, XX"` | [message] detail of the 'store not persisted' problem (which ids, which fields differ, how many are listed); the verdict comes from whether the problem is raised |  |
| 6331 | `3` → `4` | [message] detail of the 'store not persisted' problem (which ids, which fields differ, how many are listed); the verdict comes from whether the problem is raised |  |
| 6335 | `f"receipts are enabled but the chain is EMPTY while the sto…` → `None` | [message] problems.append(None) still records a problem, and the verdict is `len(problems) == 0` |  |
| 6340 | `getattr(self, "_read_errors", None)` → `getattr(self, "_read_errors", )` | [equivalent] equivalent: both attributes are set in __init__, so the getattr default is never used |  |
| 6343 | `getattr(self, "_sidecar_errors", None)` → `getattr(self, "_sidecar_errors", )` | [equivalent] equivalent: both attributes are set in __init__, so the getattr default is never used |  |
| 6346 | `getattr(self, "_extractor_errors", 0)` → `getattr(self, "_extractor_errors", None)` | [equivalent] equivalent: the default only has to be falsy |  |
| 6350 | `getattr(self, "_persist_error", None)` → `getattr(self, "_persist_error", )` | [equivalent] equivalent: `_persist_error` is set in __init__ |  |
| 6364 | `problems.append(f"receipt {i}: broken chain link (a prior r…` → `problems.append(None)` | [message] problems.append(None) still records a problem, and the verdict is `len(problems) == 0` |  |
| 6374 | `problems.append(f"receipt {i}: invalid signature")` → `problems.append(None)` | [unexecuted] no test presents a receipt whose signature fails, or pins a key over an unsigned chain, so these lines never ran (the flag they set is `len(problems)`, so only None-appends are mutable) |  |
| 6376 | `problems.append(f"receipt {i}: unsigned, but a signature wa…` → `problems.append(None)` | [unexecuted] no test presents a receipt whose signature fails, or pins a key over an unsigned chain, so these lines never ran (the flag they set is `len(problems)`, so only None-appends are mutable) |  |
| 6412 | `0` → `1` | [equivalent] equivalent: every receipt carries a `seq`, so the `.get` default is never used |  |
| 6412 | `x.get("seq", 0)` → `x.get("seq", None)` | [equivalent] equivalent: every receipt carries a `seq`, so the `.get` default is never used |  |
| 6412 | `x.get("seq", 0)` → `x.get("seq", )` | [equivalent] equivalent: every receipt carries a `seq`, so the `.get` default is never used |  |
| 6412 | `0` → `1` | [equivalent] equivalent: every receipt carries a `seq`, so the `.get` default is never used |  |
| 6412 | `r.get("seq", 0)` → `r.get("seq", )` | [equivalent] equivalent: every receipt carries a `seq`, so the `.get` default is never used |  |
| 6412 | `r.get("seq", 0)` → `r.get("seq", None)` | [equivalent] equivalent: every receipt carries a `seq`, so the `.get` default is never used |  |
| 6442 | `legacy_flagged.add(r["memory_id"])` → `legacy_flagged.add(None)` | [message] which id is added to legacy_flagged; the problem is raised whenever the set is non-empty, and only its count changes |  |
| 6443 | `default=r.get("seq", 0)` → `` | [legacy] the pre-1.68 receipt path with legacy_strict=False ('only the latest receipt binds'); no test builds a legacy receipt whose latest version mismatches under that flag |  |
| 6443 | `r.get("seq", 0)` → `None` | [legacy] the pre-1.68 receipt path with legacy_strict=False ('only the latest receipt binds'); no test builds a legacy receipt whose latest version mismatches under that flag |  |
| 6443 | `x.get("seq", 0)` → `x.get("seq", None)` | [legacy] the pre-1.68 receipt path with legacy_strict=False ('only the latest receipt binds'); no test builds a legacy receipt whose latest version mismatches under that flag |  |
| 6443 | `x.get("seq", 0)` → `x.get("seq", )` | [legacy] the pre-1.68 receipt path with legacy_strict=False ('only the latest receipt binds'); no test builds a legacy receipt whose latest version mismatches under that flag |  |
| 6443 | `0` → `1` | [legacy] the pre-1.68 receipt path with legacy_strict=False ('only the latest receipt binds'); no test builds a legacy receipt whose latest version mismatches under that flag |  |
| 6444 | `0` → `1` | [legacy] the pre-1.68 receipt path with legacy_strict=False ('only the latest receipt binds'); no test builds a legacy receipt whose latest version mismatches under that flag |  |
| 6444 | `r.get("seq", 0)` → `r.get("seq", None)` | [legacy] the pre-1.68 receipt path with legacy_strict=False ('only the latest receipt binds'); no test builds a legacy receipt whose latest version mismatches under that flag |  |
| 6444 | `r.get("seq", 0)` → `r.get("seq", )` | [legacy] the pre-1.68 receipt path with legacy_strict=False ('only the latest receipt binds'); no test builds a legacy receipt whose latest version mismatches under that flag |  |
| 6444 | `r.get("seq", 0)` → `r.get(0)` | [legacy] the pre-1.68 receipt path with legacy_strict=False ('only the latest receipt binds'); no test builds a legacy receipt whose latest version mismatches under that flag |  |
| 6444 | `r.get("seq", 0)` → `r.get(None, 0)` | [legacy] the pre-1.68 receipt path with legacy_strict=False ('only the latest receipt binds'); no test builds a legacy receipt whose latest version mismatches under that flag |  |
| 6444 | `"seq"` → `"XXseqXX"` | [legacy] the pre-1.68 receipt path with legacy_strict=False ('only the latest receipt binds'); no test builds a legacy receipt whose latest version mismatches under that flag |  |
| 6444 | `"seq"` → `"SEQ"` | [legacy] the pre-1.68 receipt path with legacy_strict=False ('only the latest receipt binds'); no test builds a legacy receipt whose latest version mismatches under that flag |  |
| 6444 | `0` → `1` | [legacy] the pre-1.68 receipt path with legacy_strict=False ('only the latest receipt binds'); no test builds a legacy receipt whose latest version mismatches under that flag |  |
| 6444 | `r.get("seq", 0)` → `r.get("seq", )` | [legacy] the pre-1.68 receipt path with legacy_strict=False ('only the latest receipt binds'); no test builds a legacy receipt whose latest version mismatches under that flag |  |
| 6444 | `r.get("seq", 0)` → `r.get(0)` | [legacy] the pre-1.68 receipt path with legacy_strict=False ('only the latest receipt binds'); no test builds a legacy receipt whose latest version mismatches under that flag |  |
| 6444 | `r.get("seq", 0)` → `r.get("seq", None)` | [legacy] the pre-1.68 receipt path with legacy_strict=False ('only the latest receipt binds'); no test builds a legacy receipt whose latest version mismatches under that flag |  |
| 6444 | `r.get("seq", 0)` → `r.get(None, 0)` | [legacy] the pre-1.68 receipt path with legacy_strict=False ('only the latest receipt binds'); no test builds a legacy receipt whose latest version mismatches under that flag |  |
| 6444 | `"seq"` → `"SEQ"` | [legacy] the pre-1.68 receipt path with legacy_strict=False ('only the latest receipt binds'); no test builds a legacy receipt whose latest version mismatches under that flag |  |
| 6444 | `"seq"` → `"XXseqXX"` | [legacy] the pre-1.68 receipt path with legacy_strict=False ('only the latest receipt binds'); no test builds a legacy receipt whose latest version mismatches under that flag |  |
| 6445 | `bad = any(cc.get(k) != v for k, v in rc.items())` → `bad = None` | [legacy] the pre-1.68 receipt path with legacy_strict=False ('only the latest receipt binds'); no test builds a legacy receipt whose latest version mismatches under that flag |  |
| 6447 | `bad = False` → `bad = None` | [legacy] the pre-1.68 receipt path with legacy_strict=False ('only the latest receipt binds'); no test builds a legacy receipt whose latest version mismatches under that flag |  |
| 6456 | `"mtype"` → `"MTYPE"` | [message] the human description of which committed field changed (`_WHAT` and how it is joined); the problem is raised either way |  |
| 6456 | `"mtype"` → `"XXmtypeXX"` | [message] the human description of which committed field changed (`_WHAT` and how it is joined); the problem is raised either way |  |
| 6456 | `"its TYPE"` → `"ITS TYPE"` | [message] the human description of which committed field changed (`_WHAT` and how it is joined); the problem is raised either way |  |
| 6456 | `"its TYPE"` → `"XXits TYPEXX"` | [message] the human description of which committed field changed (`_WHAT` and how it is joined); the problem is raised either way |  |
| 6456 | `"its TYPE"` → `"its type"` | [message] the human description of which committed field changed (`_WHAT` and how it is joined); the problem is raised either way |  |
| 6457 | `"value_sha256"` → `"VALUE_SHA256"` | [message] the human description of which committed field changed (`_WHAT` and how it is joined); the problem is raised either way |  |
| 6457 | `"value_sha256"` → `"XXvalue_sha256XX"` | [message] the human description of which committed field changed (`_WHAT` and how it is joined); the problem is raised either way |  |
| 6457 | `` "its VALUE (`object`)" `` → `` "its value (`object`)" `` | [message] the human description of which committed field changed (`_WHAT` and how it is joined); the problem is raised either way |  |
| 6457 | `` "its VALUE (`object`)" `` → `` "XXits VALUE (`object`)XX" `` | [message] the human description of which committed field changed (`_WHAT` and how it is joined); the problem is raised either way |  |
| 6457 | `` "its VALUE (`object`)" `` → `` "ITS VALUE (`OBJECT`)" `` | [message] the human description of which committed field changed (`_WHAT` and how it is joined); the problem is raised either way |  |
| 6458 | `"whether the store SERVES it, or who confirmed it"` → `"XXwhether the store SERVES it, or who confirmed itXX"` | [message] the human description of which committed field changed (`_WHAT` and how it is joined); the problem is raised either way |  |
| 6459 | `` "WHEN the fact became true (`valid_from`), or where that " `` → `` "XXWHEN the fact became true (`valid_from`), or where that … `` | [message] the human description of which committed field changed (`_WHAT` and how it is joined); the problem is raised either way |  |
| 6459 | `` "WHEN the fact became true (`valid_from`), or where that " `` → `` "when the fact became true (`valid_from`), or where that " `` | [message] the human description of which committed field changed (`_WHAT` and how it is joined); the problem is raised either way |  |
| 6460 | `"time came from"` → `"XXtime came fromXX"` | [message] the human description of which committed field changed (`_WHAT` and how it is joined); the problem is raised either way |  |
| 6461 | `"content_sha256"` → `"CONTENT_SHA256"` | [message] the human description of which committed field changed (`_WHAT` and how it is joined); the problem is raised either way |  |
| 6461 | `"content_sha256"` → `"XXcontent_sha256XX"` | [message] the human description of which committed field changed (`_WHAT` and how it is joined); the problem is raised either way |  |
| 6461 | `"its text/key/type"` → `"XXits text/key/typeXX"` | [message] the human description of which committed field changed (`_WHAT` and how it is joined); the problem is raised either way |  |
| 6461 | `"its text/key/type"` → `"ITS TEXT/KEY/TYPE"` | [message] the human description of which committed field changed (`_WHAT` and how it is joined); the problem is raised either way |  |
| 6467 | `> 1` → `>= 1` | [message] the human description of which committed field changed (`_WHAT` and how it is joined); the problem is raised either way |  |
| 6469 | `_diff` → `(_diff) or True` | [message] wording, count or joining of a problem line; `verify_writes` returns `len(problems) == 0`, so any text -- or None -- still fails the verdict |  |
| 6469 | `"; "` → `"XX; XX"` | [message] wording, count or joining of a problem line; `verify_writes` returns `len(problems) == 0`, so any text -- or None -- still fails the verdict |  |
| 6470 | `"a field its receipt commits to"` → `"XXa field its receipt commits toXX"` | [message] wording, count or joining of a problem line; `verify_writes` returns `len(problems) == 0`, so any text -- or None -- still fails the verdict |  |
| 6470 | `"a field its receipt commits to"` → `"A FIELD ITS RECEIPT COMMITS TO"` | [message] wording, count or joining of a problem line; `verify_writes` returns `len(problems) == 0`, so any text -- or None -- still fails the verdict |  |
| 6480 | `problems.append(f"tombstone {j}: broken chain link (a prior…` → `problems.append(None)` | [message] wording, count or joining of a problem line; `verify_writes` returns `len(problems) == 0`, so any text -- or None -- still fails the verdict |  |
| 6482 | `problems.append(f"tombstone {j}: tombstone tampered (hash m…` → `problems.append(None)` | [message] wording, count or joining of a problem line; `verify_writes` returns `len(problems) == 0`, so any text -- or None -- still fails the verdict |  |
| 6488 | `problems.append(f"tombstone {j}: signed by an unexpected ke…` → `problems.append(None)` | [message] wording, count or joining of a problem line; `verify_writes` returns `len(problems) == 0`, so any text -- or None -- still fails the verdict |  |
| 6490 | `problems.append(f"tombstone {j}: invalid signature")` → `problems.append(None)` | [message] wording, count or joining of a problem line; `verify_writes` returns `len(problems) == 0`, so any text -- or None -- still fails the verdict |  |
| 6492 | `problems.append(f"tombstone {j}: unsigned, but a signature …` → `problems.append(None)` | [message] wording, count or joining of a problem line; `verify_writes` returns `len(problems) == 0`, so any text -- or None -- still fails the verdict |  |
| 6498 | `any("sig" in t for t in self._tombstones)` → `any(None)` | [unexecuted-combo] differs only for a store whose receipts are unsigned while a tombstone is signed (the `or` short-circuits on signed receipts); a single key signs both, so the combination does not arise, and warn_unpinned is never asked of an unsigned store with tombstones |  |
| 6498 | `"sig"` → `"XXsigXX"` | [unexecuted-combo] differs only for a store whose receipts are unsigned while a tombstone is signed (the `or` short-circuits on signed receipts); a single key signs both, so the combination does not arise, and warn_unpinned is never asked of an unsigned store with tombstones |  |
| 6498 | `"sig"` → `"SIG"` | [unexecuted-combo] differs only for a store whose receipts are unsigned while a tombstone is signed (the `or` short-circuits on signed receipts); a single key signs both, so the combination does not arise, and warn_unpinned is never asked of an unsigned store with tombstones |  |
| 6498 | `in` → `not in` | [unexecuted-combo] differs only for a store whose receipts are unsigned while a tombstone is signed (the `or` short-circuits on signed receipts); a single key signs both, so the combination does not arise, and warn_unpinned is never asked of an unsigned store with tombstones |  |
| 6499 | `"signatures present but expected_pubkey not pinned: a store…` → `"XXsignatures present but expected_pubkey not pinned: a sto…` | [message] wording, count or joining of a problem line; `verify_writes` returns `len(problems) == 0`, so any text -- or None -- still fails the verdict |  |
| 6500 | `"key and still pass — pass expected_pubkey, or witness anch…` → `"XXkey and still pass — pass expected_pubkey, or witness an…` | [message] wording, count or joining of a problem line; `verify_writes` returns `len(problems) == 0`, so any text -- or None -- still fails the verdict |  |
| 6500 | `"key and still pass — pass expected_pubkey, or witness anch…` → `"KEY AND STILL PASS — PASS EXPECTED_PUBKEY, OR WITNESS ANCH…` | [message] wording, count or joining of a problem line; `verify_writes` returns `len(problems) == 0`, so any text -- or None -- still fails the verdict |  |
| 6517 | `r.get("seq", 0)` → `r.get(None, 0)` | [equivalent] equivalent: receipts carry strictly increasing `seq`, so `>=` and `>` pick the same latest receipt, the defaults are never used, and a lookup that always returns -1 still ends on the last receipt per record |  |
| 6517 | `r.get("seq", 0)` → `r.get("seq", None)` | [equivalent] equivalent: receipts carry strictly increasing `seq`, so `>=` and `>` pick the same latest receipt, the defaults are never used, and a lookup that always returns -1 still ends on the last receipt per record |  |
| 6517 | `r.get("seq", 0)` → `r.get("seq", )` | [equivalent] equivalent: receipts carry strictly increasing `seq`, so `>=` and `>` pick the same latest receipt, the defaults are never used, and a lookup that always returns -1 still ends on the last receipt per record |  |
| 6517 | `"seq"` → `"XXseqXX"` | [equivalent] equivalent: receipts carry strictly increasing `seq`, so `>=` and `>` pick the same latest receipt, the defaults are never used, and a lookup that always returns -1 still ends on the last receipt per record |  |
| 6517 | `"seq"` → `"SEQ"` | [equivalent] equivalent: receipts carry strictly increasing `seq`, so `>=` and `>` pick the same latest receipt, the defaults are never used, and a lookup that always returns -1 still ends on the last receipt per record |  |
| 6517 | `0` → `1` | [equivalent] equivalent: receipts carry strictly increasing `seq`, so `>=` and `>` pick the same latest receipt, the defaults are never used, and a lookup that always returns -1 still ends on the last receipt per record |  |
| 6517 | `>= latest.get(r["memory_id"], {}).get("seq", -1)` → `> latest.get(r["memory_id"], {}).get("seq", -1)` | [equivalent] equivalent: receipts carry strictly increasing `seq`, so `>=` and `>` pick the same latest receipt, the defaults are never used, and a lookup that always returns -1 still ends on the last receipt per record |  |
| 6517 | `latest.get(r["memory_id"], {}).get("seq", -1)` → `latest.get(r["memory_id"], {}).get(None, -1)` | [equivalent] equivalent: receipts carry strictly increasing `seq`, so `>=` and `>` pick the same latest receipt, the defaults are never used, and a lookup that always returns -1 still ends on the last receipt per record |  |
| 6517 | `latest.get(r["memory_id"], {})` → `latest.get(None, {})` | [equivalent] equivalent: receipts carry strictly increasing `seq`, so `>=` and `>` pick the same latest receipt, the defaults are never used, and a lookup that always returns -1 still ends on the last receipt per record |  |
| 6517 | `"seq"` → `"SEQ"` | [equivalent] equivalent: receipts carry strictly increasing `seq`, so `>=` and `>` pick the same latest receipt, the defaults are never used, and a lookup that always returns -1 still ends on the last receipt per record |  |
| 6517 | `"seq"` → `"XXseqXX"` | [equivalent] equivalent: receipts carry strictly increasing `seq`, so `>=` and `>` pick the same latest receipt, the defaults are never used, and a lookup that always returns -1 still ends on the last receipt per record |  |
| 6517 | `1` → `2` | [equivalent] equivalent: receipts carry strictly increasing `seq`, so `>=` and `>` pick the same latest receipt, the defaults are never used, and a lookup that always returns -1 still ends on the last receipt per record |  |
| 6532 | `', '` → `'XX, XX'` | [message] wording, count or joining of a problem line; `verify_writes` returns `len(problems) == 0`, so any text -- or None -- still fails the verdict |  |
| 6532 | `5` → `6` | [message] wording, count or joining of a problem line; `verify_writes` returns `len(problems) == 0`, so any text -- or None -- still fails the verdict |  |
| 6533 | `len(uncovered) > 5` → `(len(uncovered) > 5) and False` | [message] wording, count or joining of a problem line; `verify_writes` returns `len(problems) == 0`, so any text -- or None -- still fails the verdict |  |
| 6533 | `len(uncovered) > 5` → `(len(uncovered) > 5) or True` | [message] wording, count or joining of a problem line; `verify_writes` returns `len(problems) == 0`, so any text -- or None -- still fails the verdict |  |
| 6533 | `len(uncovered) - 5` → `len(uncovered) + 5` | [message] wording, count or joining of a problem line; `verify_writes` returns `len(problems) == 0`, so any text -- or None -- still fails the verdict |  |
| 6533 | `5` → `6` | [message] wording, count or joining of a problem line; `verify_writes` returns `len(problems) == 0`, so any text -- or None -- still fails the verdict |  |
| 6533 | `> 5` → `>= 5` | [message] wording, count or joining of a problem line; `verify_writes` returns `len(problems) == 0`, so any text -- or None -- still fails the verdict |  |
| 6533 | `5` → `6` | [message] wording, count or joining of a problem line; `verify_writes` returns `len(problems) == 0`, so any text -- or None -- still fails the verdict |  |
| 6533 | `""` → `"XXXX"` | [message] wording, count or joining of a problem line; `verify_writes` returns `len(problems) == 0`, so any text -- or None -- still fails the verdict |  |
| 6533 | `")"` → `"XX)XX"` | [message] wording, count or joining of a problem line; `verify_writes` returns `len(problems) == 0`, so any text -- or None -- still fails the verdict |  |
| 6549 | `self._tombstones or ()` → `self._tombstones and ()` | [soundness] unpinned, the partial-signing rule is the only thing that sees an unsigned tombstone appended to a signed store; every partial-signing test appended a write receipt, never a tombstone |  |
| 6554 | `len(_chain) - _signed` → `len(_chain) + _signed` | [message] wording, count or joining of a problem line; `verify_writes` returns `len(problems) == 0`, so any text -- or None -- still fails the verdict |  |
| 6589 | `5` → `6` | [message] formatting of the uncovered-records problem (how many ids are shown, the ellipsis); the verdict is `len(problems) == 0` |  |
| 6589 | `' ...' if len(_uncovered) > 5 else ''` → `' ...' if (len(_uncovered) > 5) and False else ''` | [message] formatting of the uncovered-records problem (how many ids are shown, the ellipsis); the verdict is `len(problems) == 0` |  |
| 6589 | `' ...'` → `'XX ...XX'` | [message] formatting of the uncovered-records problem (how many ids are shown, the ellipsis); the verdict is `len(problems) == 0` |  |
| 6589 | `' ...' if len(_uncovered) > 5 else ''` → `' ...' if (len(_uncovered) > 5) or True else ''` | [message] formatting of the uncovered-records problem (how many ids are shown, the ellipsis); the verdict is `len(problems) == 0` |  |
| 6589 | `> 5` → `>= 5` | [message] formatting of the uncovered-records problem (how many ids are shown, the ellipsis); the verdict is `len(problems) == 0` |  |
| 6589 | `5` → `6` | [message] formatting of the uncovered-records problem (how many ids are shown, the ellipsis); the verdict is `len(problems) == 0` |  |
| 6589 | `''` → `'XXXX'` | [message] formatting of the uncovered-records problem (how many ids are shown, the ellipsis); the verdict is `len(problems) == 0` |  |
| 6604 | `self.receipts_enabled and self._receipts` → `self.receipts_enabled or self._receipts` | [equivalent] equivalent: with receipts disabled the in-memory chain is empty, so both retirement sweeps find nothing either way |  |
| 6647 | `disk_receipts = self._receipts` → `disk_receipts = None` | [unreachable] the in-memory fallback only matters when the sidecar is missing, and then the head check it feeds is skipped (a store with no sidecar has no head to compare) |  |
| 6650 | `self._receipts_path.read_text(encoding="utf-8")` → `self._receipts_path.read_text(encoding=None)` | [platform] encoding=None reads the sidecar in the locale encoding, UTF-8 here; 'UTF-8' is the same codec |  |
| 6650 | `"utf-8"` → `"UTF-8"` | [platform] encoding=None reads the sidecar in the locale encoding, UTF-8 here; 'UTF-8' is the same codec |  |


### Audit bundle: 486 survivors (1,367 of 1,608 mutants run)

**`inspeximus/audit_bundle.py` `_content_free_tombstones`** (10)

| line | mutation | why no test caught it | now |
|---:|---|---|---|
| 91 | `"request_id"` → `"REQUEST_ID"` | [literal] literal `"request_id"` is not asserted by any test |  |
| 91 | `"request_id"` → `"XXrequest_idXX"` | [literal] literal `"request_id"` is not asserted by any test |  |
| 94 | `t.get("sig")` → `t.get(None)` | [call_arg] a call argument dropped or set to None, and no test observes the difference |  |
| 94 | `"sig"` → `"XXsigXX"` | [unread_field] lookup of `sig`: no test input makes the renamed key read differently |  |
| 94 | `"sig"` → `"SIG"` | [unread_field] lookup of `sig`: no test input makes the renamed key read differently |  |
| 95 | `rec["sig"] = t["sig"]` → `rec["sig"] = None` | [unexecuted] no test executes line 95; `_content_free_tombstones` runs under 0 test(s), none of which reaches this statement |  |
| 95 | `"sig"` → `"XXsigXX"` | [unexecuted] no test executes line 95; `_content_free_tombstones` runs under 0 test(s), none of which reaches this statement |  |
| 95 | `"sig"` → `"SIG"` | [unexecuted] no test executes line 95; `_content_free_tombstones` runs under 0 test(s), none of which reaches this statement |  |
| 95 | `"sig"` → `"XXsigXX"` | [unexecuted] no test executes line 95; `_content_free_tombstones` runs under 0 test(s), none of which reaches this statement |  |
| 95 | `"sig"` → `"SIG"` | [unexecuted] no test executes line 95; `_content_free_tombstones` runs under 0 test(s), none of which reaches this statement |  |

**`inspeximus/audit_bundle.py` `_acl_acts`** (17)

| line | mutation | why no test caught it | now |
|---:|---|---|---|
| 119 | `"key"` → `"XXkeyXX"` | [literal] literal `"key"` is not asserted by any test |  |
| 119 | `"key"` → `"KEY"` | [literal] literal `"key"` is not asserted by any test |  |
| 119 | `"agent"` → `"XXagentXX"` | [literal] literal `"agent"` is not asserted by any test |  |
| 119 | `"agent"` → `"AGENT"` | [literal] literal `"agent"` is not asserted by any test |  |
| 119 | `"by"` → `"BY"` | [literal] literal `"by"` is not asserted by any test |  |
| 119 | `"by"` → `"XXbyXX"` | [literal] literal `"by"` is not asserted by any test |  |
| 119 | `"kind"` → `"KIND"` | [literal] literal `"kind"` is not asserted by any test |  |
| 119 | `"kind"` → `"XXkindXX"` | [literal] literal `"kind"` is not asserted by any test |  |
| 119 | `"value"` → `"XXvalueXX"` | [literal] literal `"value"` is not asserted by any test |  |
| 119 | `"value"` → `"VALUE"` | [literal] literal `"value"` is not asserted by any test |  |
| 119 | `"ts"` → `"XXtsXX"` | [literal] literal `"ts"` is not asserted by any test |  |
| 119 | `"ts"` → `"TS"` | [literal] literal `"ts"` is not asserted by any test |  |
| 120 | `x.get("ts") or 0` → `x.get("ts") and 0` | [condition] condition changed and no test input makes the original and the mutant disagree |  |
| 120 | `x.get("ts")` → `x.get(None)` | [call_arg] a call argument dropped or set to None, and no test observes the difference |  |
| 120 | `"ts"` → `"XXtsXX"` | [unread_field] lookup of `ts`: no test input makes the renamed key read differently |  |
| 120 | `"ts"` → `"TS"` | [unread_field] lookup of `ts`: no test input makes the renamed key read differently |  |
| 120 | `0` → `1` | [constant] constant 0 -> 1: no test sits on the boundary this constant draws |  |

**`inspeximus/audit_bundle.py` `_derived_store_id`** (12)

| line | mutation | why no test caught it | now |
|---:|---|---|---|
| 140 | `getattr(store, "_receipts", None)` → `getattr(store, "_receipts", )` | [call_arg] a call argument dropped or set to None, and no test observes the difference |  |
| 142 | `32` → `33` | [constant] constant 32 -> 33: no test sits on the boundary this constant draws |  |
| 143 | `str(getattr(store, "path", "") or "store")` → `str(None)` | [call_arg] a call argument dropped or set to None, and no test observes the difference |  |
| 143 | `getattr(store, "path", "")` → `getattr(store, "path", )` | [call_arg] a call argument dropped or set to None, and no test observes the difference |  |
| 143 | `getattr(store, "path", "") or "store"` → `getattr(store, "path", "") and "store"` | [condition] condition changed and no test input makes the original and the mutant disagree |  |
| 143 | `getattr(store, "path", "")` → `getattr(store, "path", None)` | [call_arg] a call argument dropped or set to None, and no test observes the difference |  |
| 143 | `getattr(store, "path", "")` → `getattr(None, "path", "")` | [call_arg] a call argument dropped or set to None, and no test observes the difference |  |
| 143 | `"path"` → `"XXpathXX"` | [literal] literal `"path"` is not asserted by any test |  |
| 143 | `"path"` → `"PATH"` | [literal] literal `"path"` is not asserted by any test |  |
| 143 | `""` → `"XXXX"` | [literal] literal `""` is not asserted by any test |  |
| 143 | `"store"` → `"STORE"` | [literal] literal `"store"` is not asserted by any test |  |
| 143 | `"store"` → `"XXstoreXX"` | [literal] literal `"store"` is not asserted by any test |  |

**`inspeximus/audit_bundle.py` `build_bundle`** (45)

| line | mutation | why no test caught it | now |
|---:|---|---|---|
| 182 | `getattr(store, "tenant", None)` → `getattr(store, "tenant", )` | [call_arg] a call argument dropped or set to None, and no test observes the difference |  |
| 185 | `getattr(store, 'tenant', None)` → `getattr(None, 'tenant', None)` | [call_arg] a call argument dropped or set to None, and no test observes the difference |  |
| 185 | `getattr(store, 'tenant', None)` → `getattr(store, 'tenant', )` | [call_arg] a call argument dropped or set to None, and no test observes the difference |  |
| 185 | `'tenant'` → `'XXtenantXX'` | [message] wording of a human-readable message; the tests assert that it is reported, not what it says |  |
| 185 | `'tenant'` → `'TENANT'` | [message] wording of a human-readable message; the tests assert that it is reported, not what it says |  |
| 190 | `store.anchor(sign=sign)` → `store.anchor(sign=None)` | [unread-output] No test verifies the anchor's signature when build_bundle(sign=True) is called; the bundle verifier reads the anchor's root and size, not whether it was signed. |  |
| 212 | `"refusing to witness a store with receipts disabled: its an…` → `"XXrefusing to witness a store with receipts disabled: its …` | [message] wording of a human-readable message; the tests assert that it is reported, not what it says |  |
| 213 | `"(n_writes=0, writes_tip=all zeros, whatever the store actu…` → `"(N_WRITES=0, WRITES_TIP=ALL ZEROS, WHATEVER THE STORE ACTU…` | [message] wording of a human-readable message; the tests assert that it is reported, not what it says |  |
| 213 | `"(n_writes=0, writes_tip=all zeros, whatever the store actu…` → `"XX(n_writes=0, writes_tip=all zeros, whatever the store ac…` | [message] wording of a human-readable message; the tests assert that it is reported, not what it says |  |
| 214 | `"co-signature over it would verify while proving nothing, a…` → `"XXco-signature over it would verify while proving nothing,…` | [message] wording of a human-readable message; the tests assert that it is reported, not what it says |  |
| 214 | `"co-signature over it would verify while proving nothing, a…` → `"CO-SIGNATURE OVER IT WOULD VERIFY WHILE PROVING NOTHING, A…` | [message] wording of a human-readable message; the tests assert that it is reported, not what it says |  |
| 215 | `` "the filename, which `cp` changes. Open the store with rece… `` → `` "XXthe filename, which `cp` changes. Open the store with re… `` | [message] wording of a human-readable message; the tests assert that it is reported, not what it says |  |
| 215 | `` "the filename, which `cp` changes. Open the store with rece… `` → `` "the filename, which `cp` changes. open the store with rece… `` | [message] wording of a human-readable message; the tests assert that it is reported, not what it says |  |
| 215 | `` "the filename, which `cp` changes. Open the store with rece… `` → `` "THE FILENAME, WHICH `CP` CHANGES. OPEN THE STORE WITH RECE… `` | [message] wording of a human-readable message; the tests assert that it is reported, not what it says |  |
| 216 | `store_id or _wid` → `store_id and _wid` | [unread-output] The explicit store_id argument is never passed by a test and no test reads the bundle's store_id back against it. |  |
| 216 | `_sid = store_id or _wid` → `_sid = None` | [unread-output] The explicit store_id argument is never passed by a test and no test reads the bundle's store_id back against it. |  |
| 239 | `"inspeximus_version"` → `"INSPEXIMUS_VERSION"` | [unread_field] output key `inspeximus_version` is written but no test reads it back |  |
| 239 | `"inspeximus_version"` → `"XXinspeximus_versionXX"` | [unread_field] output key `inspeximus_version` is written but no test reads it back |  |
| 241 | `getattr(store, "tenant", None)` → `getattr(None, "tenant", None)` | [call_arg] a call argument dropped or set to None, and no test observes the difference |  |
| 241 | `getattr(store, "tenant", None)` → `getattr(store, "tenant", )` | [call_arg] a call argument dropped or set to None, and no test observes the difference |  |
| 241 | `"tenant"` → `"TENANT"` | [unread_field] output key `tenant` is written but no test reads it back |  |
| 241 | `"tenant"` → `"XXtenantXX"` | [unread_field] output key `tenant` is written but no test reads it back |  |
| 241 | `"tenant"` → `"XXtenantXX"` | [unread_field] output key `tenant` is written but no test reads it back |  |
| 241 | `"tenant"` → `"TENANT"` | [unread_field] output key `tenant` is written but no test reads it back |  |
| 253 | `"store_id"` → `"STORE_ID"` | [unread_field] output key `store_id` is written but no test reads it back |  |
| 253 | `"store_id"` → `"XXstore_idXX"` | [unread_field] output key `store_id` is written but no test reads it back |  |
| 257 | `and` → `or` | [soundness] cross_tenant_chain switches off the verifier's record-count coverage check; no test built a bundle with the flag on an unbound handle. Test written: tests/test_evidence_survivors_audit_bundle.py::test_an_unbound_handle_does_not_claim_a_cross_tenant_chain |  |
| 258 | `getattr(store, "tenant", None)` → `getattr(store, "tenant", )` | [call_arg] a call argument dropped or set to None, and no test observes the difference |  |
| 260 | `store.governance_report(expected_pubkey)` → `store.governance_report(None)` | [soundness] No test built a bundle with the wrong key pinned, so governance computed against no key read the same. Test written: tests/test_evidence_survivors_audit_bundle.py::test_the_governance_section_is_computed_against_the_pinned_key |  |
| 261 | `"supersession"` → `"SUPERSESSION"` | [unread_field] output key `supersession` is written but no test reads it back |  |
| 261 | `"supersession"` → `"XXsupersessionXX"` | [unread_field] output key `supersession` is written but no test reads it back |  |
| 268 | `getattr(store, "items", [])` → `getattr(store, "items", )` | [unread-output] No test read baseline_complete, the field the verifier uses to tell a planted record from a baseline that was never clean. Test written: tests/test_evidence_survivors_audit_bundle.py::test_the_bundle_says_whether_its_baseline_was_complete |  |
| 268 | `getattr(store, "items", [])` → `getattr(store, "items", None)` | [unread-output] No test read baseline_complete, the field the verifier uses to tell a planted record from a baseline that was never clean. Test written: tests/test_evidence_survivors_audit_bundle.py::test_the_bundle_says_whether_its_baseline_was_complete |  |
| 269 | `r.get("id")` → `r.get(None)` | [unread-output] No test read baseline_complete, the field the verifier uses to tell a planted record from a baseline that was never clean. Test written: tests/test_evidence_survivors_audit_bundle.py::test_the_bundle_says_whether_its_baseline_was_complete |  |
| 269 | `"id"` → `"ID"` | [unread-output] No test read baseline_complete, the field the verifier uses to tell a planted record from a baseline that was never clean. Test written: tests/test_evidence_survivors_audit_bundle.py::test_the_bundle_says_whether_its_baseline_was_complete |  |
| 269 | `"id"` → `"XXidXX"` | [unread-output] No test read baseline_complete, the field the verifier uses to tell a planted record from a baseline that was never clean. Test written: tests/test_evidence_survivors_audit_bundle.py::test_the_bundle_says_whether_its_baseline_was_complete |  |
| 269 | `not in` → `in` | [unread-output] No test read baseline_complete, the field the verifier uses to tell a planted record from a baseline that was never clean. Test written: tests/test_evidence_survivors_audit_bundle.py::test_the_bundle_says_whether_its_baseline_was_complete |  |
| 269 | `rc.get("memory_id")` → `rc.get(None)` | [unread-output] No test read baseline_complete, the field the verifier uses to tell a planted record from a baseline that was never clean. Test written: tests/test_evidence_survivors_audit_bundle.py::test_the_bundle_says_whether_its_baseline_was_complete |  |
| 269 | `"memory_id"` → `"XXmemory_idXX"` | [unread-output] No test read baseline_complete, the field the verifier uses to tell a planted record from a baseline that was never clean. Test written: tests/test_evidence_survivors_audit_bundle.py::test_the_bundle_says_whether_its_baseline_was_complete |  |
| 269 | `"memory_id"` → `"MEMORY_ID"` | [unread-output] No test read baseline_complete, the field the verifier uses to tell a planted record from a baseline that was never clean. Test written: tests/test_evidence_survivors_audit_bundle.py::test_the_bundle_says_whether_its_baseline_was_complete |  |
| 270 | `getattr(store, "_receipts", None)` → `getattr(store, "_receipts", )` | [unread-output] No test read baseline_complete, the field the verifier uses to tell a planted record from a baseline that was never clean. Test written: tests/test_evidence_survivors_audit_bundle.py::test_the_bundle_says_whether_its_baseline_was_complete |  |
| 270 | `or` → `and` | [unread-output] No test read baseline_complete, the field the verifier uses to tell a planted record from a baseline that was never clean. Test written: tests/test_evidence_survivors_audit_bundle.py::test_the_bundle_says_whether_its_baseline_was_complete |  |
| 270 | `getattr(store, "_receipts", None)` → `getattr(None, "_receipts", None)` | [unread-output] No test read baseline_complete, the field the verifier uses to tell a planted record from a baseline that was never clean. Test written: tests/test_evidence_survivors_audit_bundle.py::test_the_bundle_says_whether_its_baseline_was_complete |  |
| 270 | `"_receipts"` → `"XX_receiptsXX"` | [unread-output] No test read baseline_complete, the field the verifier uses to tell a planted record from a baseline that was never clean. Test written: tests/test_evidence_survivors_audit_bundle.py::test_the_bundle_says_whether_its_baseline_was_complete |  |
| 270 | `"_receipts"` → `"_RECEIPTS"` | [unread-output] No test read baseline_complete, the field the verifier uses to tell a planted record from a baseline that was never clean. Test written: tests/test_evidence_survivors_audit_bundle.py::test_the_bundle_says_whether_its_baseline_was_complete |  |

**`inspeximus/audit_bundle.py` `bind_content`** (21)

| line | mutation | why no test caught it | now |
|---:|---|---|---|
| 322 | `"mismatched"` → `"MISMATCHED"` | [unread_field] output key `mismatched` is written but no test reads it back |  |
| 322 | `"mismatched"` → `"XXmismatchedXX"` | [unread_field] output key `mismatched` is written but no test reads it back |  |
| 322 | `"unreceipted"` → `"XXunreceiptedXX"` | [unread_field] output key `unreceipted` is written but no test reads it back |  |
| 322 | `"unreceipted"` → `"UNRECEIPTED"` | [unread_field] output key `unreceipted` is written but no test reads it back |  |
| 322 | `"orphaned"` → `"XXorphanedXX"` | [unread_field] output key `orphaned` is written but no test reads it back |  |
| 322 | `"orphaned"` → `"ORPHANED"` | [unread_field] output key `orphaned` is written but no test reads it back |  |
| 323 | `"the bundle carries no write chain, so there is nothing to …` → `"XXthe bundle carries no write chain, so there is nothing t…` | [unread_field] lookup of `the bundle carries no write chain, so there is nothing to bind content to`: no test input makes the renamed key read differently |  |
| 323 | `"the bundle carries no write chain, so there is nothing to …` → `"THE BUNDLE CARRIES NO WRITE CHAIN, SO THERE IS NOTHING TO …` | [unread_field] lookup of `the bundle carries no write chain, so there is nothing to bind content to`: no test input makes the renamed key read differently |  |
| 326 | `x.get("seq", 0)` → `x.get("seq", )` | [call_arg] a call argument dropped or set to None, and no test observes the difference |  |
| 326 | `x.get("seq", 0)` → `x.get("seq", None)` | [call_arg] a call argument dropped or set to None, and no test observes the difference |  |
| 326 | `x.get("seq", 0)` → `x.get(None, 0)` | [call_arg] a call argument dropped or set to None, and no test observes the difference |  |
| 326 | `"seq"` → `"SEQ"` | [unread_field] lookup of `seq`: no test input makes the renamed key read differently |  |
| 326 | `"seq"` → `"XXseqXX"` | [unread_field] lookup of `seq`: no test input makes the renamed key read differently |  |
| 326 | `0` → `1` | [constant] constant 0 -> 1: no test sits on the boundary this constant draws |  |
| 343 | `"immutable_sha256"` → `"XXimmutable_sha256XX"` | [literal] literal `"immutable_sha256"` is not asserted by any test |  |
| 343 | `"immutable_sha256"` → `"IMMUTABLE_SHA256"` | [literal] literal `"immutable_sha256"` is not asserted by any test |  |
| 343 | `"content_sha256"` → `"XXcontent_sha256XX"` | [literal] literal `"content_sha256"` is not asserted by any test |  |
| 343 | `"content_sha256"` → `"CONTENT_SHA256"` | [literal] literal `"content_sha256"` is not asserted by any test |  |
| 344 | `"status_sha256"` → `"XXstatus_sha256XX"` | [literal] literal `"status_sha256"` is not asserted by any test |  |
| 344 | `"status_sha256"` → `"STATUS_SHA256"` | [literal] literal `"status_sha256"` is not asserted by any test |  |
| 363 | `compared == 0 and first` → `compared == 0 or first` | [message] With or, every successful comparison also reported 'NOT ONE of the records was found'; ok is computed separately and no test read the problem list of a clean check. Test written: tests/test_evidence_survivors_audit_bundle.py::test_bind_content_on_an_honest_store_reports_nothing |  |

**`inspeximus/audit_bundle.py` `verify_bundle`** (381)

| line | mutation | why no test caught it | now |
|---:|---|---|---|
| 376 | `1` → `2` | [legacy-default] Every test passes threshold= explicitly, so the default was never exercised. Test written: tests/test_evidence_survivors_audit_bundle.py::test_one_witness_meets_the_default_threshold |  |
| 411 | `"checks"` → `"XXchecksXX"` | [unread_field] output key `checks` is written but no test reads it back |  |
| 411 | `"checks"` → `"CHECKS"` | [unread_field] output key `checks` is written but no test reads it back |  |
| 411 | `"problems"` → `"PROBLEMS"` | [unread_field] output key `problems` is written but no test reads it back |  |
| 411 | `"problems"` → `"XXproblemsXX"` | [unread_field] output key `problems` is written but no test reads it back |  |
| 411 | `"summary"` → `"XXsummaryXX"` | [unread_field] output key `summary` is written but no test reads it back |  |
| 411 | `"summary"` → `"SUMMARY"` | [unread_field] output key `summary` is written but no test reads it back |  |
| 415 | `"bundle_hash matches (no field was altered after export)"` → `"BUNDLE_HASH MATCHES (NO FIELD WAS ALTERED AFTER EXPORT)"` | [literal] literal `"bundle_hash matches (no field was alter` is not asserted by any test |  |
| 415 | `"bundle_hash matches (no field was altered after export)"` → `"XXbundle_hash matches (no field was altered after export)X…` | [literal] literal `"bundle_hash matches (no field was alter` is not asserted by any test |  |
| 417 | `"bundle_hash MISMATCH -- the bundle was modified after expo…` → `"XXbundle_hash MISMATCH -- the bundle was modified after ex…` | [message] wording of a human-readable message; the tests assert that it is reported, not what it says |  |
| 417 | `"bundle_hash MISMATCH -- the bundle was modified after expo…` → `"bundle_hash mismatch -- the bundle was modified after expo…` | [message] wording of a human-readable message; the tests assert that it is reported, not what it says |  |
| 429 | `12` → `13` | [constant] constant 12 -> 13: no test sits on the boundary this constant draws |  |
| 429 | `anchor.get('n_writes')` → `anchor.get(None)` | [call_arg] a call argument dropped or set to None, and no test observes the difference |  |
| 429 | `'n_writes'` → `'XXn_writesXX'` | [message] wording of a human-readable message; the tests assert that it is reported, not what it says |  |
| 429 | `'n_writes'` → `'N_WRITES'` | [message] wording of a human-readable message; the tests assert that it is reported, not what it says |  |
| 430 | `str(anchor.get('writes_tip'))` → `str(None)` | [call_arg] a call argument dropped or set to None, and no test observes the difference |  |
| 430 | `anchor.get('writes_tip')` → `anchor.get(None)` | [call_arg] a call argument dropped or set to None, and no test observes the difference |  |
| 430 | `'writes_tip'` → `'XXwrites_tipXX'` | [message] wording of a human-readable message; the tests assert that it is reported, not what it says |  |
| 430 | `'writes_tip'` → `'WRITES_TIP'` | [message] wording of a human-readable message; the tests assert that it is reported, not what it says |  |
| 430 | `12` → `13` | [constant] constant 12 -> 13: no test sits on the boundary this constant draws |  |
| 435 | `_rewalk(tc, "tombstone")` → `_rewalk(tc, None)` | [equivalent] Core._chain_core only branches on kind == 'write'; any other value takes the tombstone preimage, so renaming the tombstone kind changes nothing. |  |
| 435 | `"tombstone"` → `"XXtombstoneXX"` | [equivalent] Core._chain_core only branches on kind == 'write'; any other value takes the tombstone preimage, so renaming the tombstone kind changes nothing. |  |
| 435 | `"tombstone"` → `"TOMBSTONE"` | [equivalent] Core._chain_core only branches on kind == 'write'; any other value takes the tombstone preimage, so renaming the tombstone kind changes nothing. |  |
| 437 | `bad(f"tombstone chain breaks at index {t_bad} (bad prev-lin…` → `bad(None)` | [unexecuted] no test executes line 437; `verify_bundle` runs under 0 test(s), none of which reaches this statement |  |
| 439 | `"tombstone chain tip/count does not match anchor"` → `"XXtombstone chain tip/count does not match anchorXX"` | [message] wording of a human-readable message; the tests assert that it is reported, not what it says |  |
| 448 | `"anchor sth_hash is internally consistent"` → `"ANCHOR STH_HASH IS INTERNALLY CONSISTENT"` | [literal] literal `"anchor sth_hash is internally consisten` is not asserted by any test |  |
| 448 | `"anchor sth_hash is internally consistent"` → `"XXanchor sth_hash is internally consistentXX"` | [literal] literal `"anchor sth_hash is internally consisten` is not asserted by any test |  |
| 450 | `"anchor sth_hash does not match its own fields"` → `"ANCHOR STH_HASH DOES NOT MATCH ITS OWN FIELDS"` | [message] wording of a human-readable message; the tests assert that it is reported, not what it says |  |
| 450 | `"anchor sth_hash does not match its own fields"` → `"XXanchor sth_hash does not match its own fieldsXX"` | [message] wording of a human-readable message; the tests assert that it is reported, not what it says |  |
| 450 | `bad("anchor sth_hash does not match its own fields")` → `bad(None)` | [call_arg] a call argument dropped or set to None, and no test observes the difference |  |
| 467 | `"tombstone"` → `"XXtombstoneXX"` | [equivalent] The same kind label goes to _chain_core (any non-'write' value takes the tombstone preimage) and into the message text; the verdict cannot change. |  |
| 467 | `"tombstone"` → `"TOMBSTONE"` | [equivalent] The same kind label goes to _chain_core (any non-'write' value takes the tombstone preimage) and into the message text; the verdict cannot change. |  |
| 469 | `continue` → `break` | [unexecuted] no test executes line 469; `verify_bundle` runs under 0 test(s), none of which reaches this statement |  |
| 474 | `12` → `13` | [constant] constant 12 -> 13: no test sits on the boundary this constant draws |  |
| 474 | `str(anchor.get(_field))` → `str(None)` | [call_arg] a call argument dropped or set to None, and no test observes the difference |  |
| 474 | `anchor.get(_field)` → `anchor.get(None)` | [call_arg] a call argument dropped or set to None, and no test observes the difference |  |
| 474 | `12` → `13` | [constant] constant 12 -> 13: no test sits on the boundary this constant draws |  |
| 482 | `"anchor root_hash does not match its own root fields"` → `"XXanchor root_hash does not match its own root fieldsXX"` | [message] wording of a human-readable message; the tests assert that it is reported, not what it says |  |
| 484 | `type(e)` → `type(None)` | [unexecuted] no test executes line 484; `verify_bundle` runs under 0 test(s), none of which reaches this statement |  |
| 484 | `f"merkle roots NOT re-derived ({type(e).__name__}: {e}) -- …` → `None` | [unexecuted] no test executes line 484; `verify_bundle` runs under 123 test(s), none of which reaches this statement |  |
| 519 | `expected_pubkey or _r.get("pubkey")` → `expected_pubkey and _r.get("pubkey")` | [soundness] Unpinned, each signature is checked against the key it names; the only bad-signature test pins the key, so skipping every unpinned check stayed green. Test written: tests/test_evidence_survivors_audit_bundle.py::test_an_unpinned_bundle_still_catches_a_signature_that_does_not_verify |  |
| 519 | `_r.get("pubkey")` → `_r.get(None)` | [soundness] Unpinned, each signature is checked against the key it names; the only bad-signature test pins the key, so skipping every unpinned check stayed green. Test written: tests/test_evidence_survivors_audit_bundle.py::test_an_unpinned_bundle_still_catches_a_signature_that_does_not_verify |  |
| 519 | `"pubkey"` → `"PUBKEY"` | [soundness] Unpinned, each signature is checked against the key it names; the only bad-signature test pins the key, so skipping every unpinned check stayed green. Test written: tests/test_evidence_survivors_audit_bundle.py::test_an_unpinned_bundle_still_catches_a_signature_that_does_not_verify |  |
| 519 | `"pubkey"` → `"XXpubkeyXX"` | [soundness] Unpinned, each signature is checked against the key it names; the only bad-signature test pins the key, so skipping every unpinned check stayed green. Test written: tests/test_evidence_survivors_audit_bundle.py::test_an_unpinned_bundle_still_catches_a_signature_that_does_not_verify |  |
| 521 | `_wrongkey.append(_r.get("memory_id") or _r.get("seq"))` → `_wrongkey.append(None)` | [call_arg] a call argument dropped or set to None, and no test observes the difference |  |
| 521 | `_r.get("memory_id") or _r.get("seq")` → `_r.get("memory_id") and _r.get("seq")` | [condition] condition changed and no test input makes the original and the mutant disagree |  |
| 521 | `_r.get("memory_id")` → `_r.get(None)` | [call_arg] a call argument dropped or set to None, and no test observes the difference |  |
| 521 | `"memory_id"` → `"XXmemory_idXX"` | [unread_field] lookup of `memory_id`: no test input makes the renamed key read differently |  |
| 521 | `"memory_id"` → `"MEMORY_ID"` | [unread_field] lookup of `memory_id`: no test input makes the renamed key read differently |  |
| 521 | `_r.get("seq")` → `_r.get(None)` | [call_arg] a call argument dropped or set to None, and no test observes the difference |  |
| 521 | `"seq"` → `"SEQ"` | [unread_field] lookup of `seq`: no test input makes the renamed key read differently |  |
| 521 | `"seq"` → `"XXseqXX"` | [unread_field] lookup of `seq`: no test input makes the renamed key read differently |  |
| 522 | `continue` → `break` | [other] Continue mutation not observed by any test |  |
| 523 | `and` → `or` | [condition] condition changed and no test input makes the original and the mutant disagree |  |
| 530 | `_badsig += 1` → `_badsig = 1` | [assignment] assigned value replaced and no test observes it |  |
| 530 | `_badsig += 1` → `_badsig -= 1` | [assignment] assigned value replaced and no test observes it |  |
| 530 | `1` → `2` | [constant] constant 1 -> 2: no test sits on the boundary this constant draws |  |
| 531 | `len(wc) + len(tc)` → `len(wc) - len(tc)` | [boundary] With minus, a bundle with as many tombstones as writes has _total == 0 and skips the whole signature assessment; every bundle in the suite has more writes than erasures. Test written: tests/test_evidence_survivors_audit_bundle.py::test_a_partly_signed_bundle_with_as_many_tombstones_as_writes_is_refused |  |
| 535 | `5` → `6` | [constant] constant 5 -> 6: no test sits on the boundary this constant draws |  |
| 537 | `bad(f"{_badsig} chain signature(s) DO NOT VERIFY against th…` → `bad(None)` | [call_arg] a call argument dropped or set to None, and no test observes the difference |  |
| 564 | `bad(f"only {_verified} of {_signed} chain signatures verify…` → `bad(None)` | [call_arg] a call argument dropped or set to None, and no test observes the difference |  |
| 568 | `require_signed` → `(require_signed) and False` | [soundness] No test combined require_signed=True with an unpinned signed bundle, so the flag could accept signatures nobody verified. Test written: tests/test_evidence_survivors_audit_bundle.py::test_require_signed_refuses_signatures_nobody_verified |  |
| 576 | `""` → `"XXXX"` | [equivalent] A missing store_id_derived becomes '' or 'XXXX', and neither starts with 'unkeyed:', so the branch is decided the same way. |  |
| 580 | `"this bundle carries witness co-signatures over a store wit…` → `"XXthis bundle carries witness co-signatures over a store w…` | [message] wording of a human-readable message; the tests assert that it is reported, not what it says |  |
| 580 | `"this bundle carries witness co-signatures over a store wit…` → `"this bundle carries witness co-signatures over a store wit…` | [message] wording of a human-readable message; the tests assert that it is reported, not what it says |  |
| 580 | `"this bundle carries witness co-signatures over a store wit…` → `"THIS BUNDLE CARRIES WITNESS CO-SIGNATURES OVER A STORE WIT…` | [message] wording of a human-readable message; the tests assert that it is reported, not what it says |  |
| 581 | `"anchor they signed commits to nothing (n_writes=0, writes_…` → `"XXanchor they signed commits to nothing (n_writes=0, write…` | [message] wording of a human-readable message; the tests assert that it is reported, not what it says |  |
| 581 | `"anchor they signed commits to nothing (n_writes=0, writes_…` → `"ANCHOR THEY SIGNED COMMITS TO NOTHING (N_WRITES=0, WRITES_…` | [message] wording of a human-readable message; the tests assert that it is reported, not what it says |  |
| 586 | `Inspeximus.verify_cosigned_anchor(anchor, cosigs, witnesses…` → `Inspeximus.verify_cosigned_anchor(anchor, cosigs, witnesses…` | [soundness] The caller's k-of-n witness threshold was never passed on; the only co-signature test used 1 witness and threshold 1, the callee's default. Test written: tests/test_evidence_survivors_audit_bundle.py::test_the_witness_threshold_is_the_one_the_caller_asked_for |  |
| 588 | `v.get('count')` → `v.get(None)` | [call_arg] a call argument dropped or set to None, and no test observes the difference |  |
| 588 | `'count'` → `'COUNT'` | [unread_field] lookup of `count`: no test input makes the renamed key read differently |  |
| 588 | `'count'` → `'XXcountXX'` | [unread_field] lookup of `count`: no test input makes the renamed key read differently |  |
| 590 | `bad(f"witness co-signature check FAILED (need {threshold}, …` → `bad(None)` | [unexecuted] no test executes line 590; `verify_bundle` runs under 0 test(s), none of which reaches this statement |  |
| 590 | `v.get('count')` → `v.get(None)` | [unexecuted] no test executes line 590; `verify_bundle` runs under 0 test(s), none of which reaches this statement |  |
| 590 | `'count'` → `'COUNT'` | [unexecuted] no test executes line 590; `verify_bundle` runs under 0 test(s), none of which reaches this statement |  |
| 590 | `'count'` → `'XXcountXX'` | [unexecuted] no test executes line 590; `verify_bundle` runs under 0 test(s), none of which reaches this statement |  |
| 592 | `"witnesses supplied but the anchor carries no co-signatures…` → `"WITNESSES SUPPLIED BUT THE ANCHOR CARRIES NO CO-SIGNATURES…` | [message] wording of a human-readable message; the tests assert that it is reported, not what it says |  |
| 592 | `"witnesses supplied but the anchor carries no co-signatures…` → `"XXwitnesses supplied but the anchor carries no co-signatur…` | [message] wording of a human-readable message; the tests assert that it is reported, not what it says |  |
| 594 | `ok(f"anchor carries {len(cosigs)} co-signature(s) (pass wit…` → `ok(None)` | [call_arg] a call argument dropped or set to None, and no test observes the difference |  |
| 602 | `"SELF-CERTIFIED: this anchor carries no external co-signatu…` → `"SELF-CERTIFIED: THIS ANCHOR CARRIES NO EXTERNAL CO-SIGNATU…` | [message] wording of a human-readable message; the tests assert that it is reported, not what it says |  |
| 602 | `"SELF-CERTIFIED: this anchor carries no external co-signatu…` → `"XXSELF-CERTIFIED: this anchor carries no external co-signa…` | [message] wording of a human-readable message; the tests assert that it is reported, not what it says |  |
| 603 | `"operator's own record-keeping verified against itself -- a…` → `"OPERATOR'S OWN RECORD-KEEPING VERIFIED AGAINST ITSELF -- A…` | [message] wording of a human-readable message; the tests assert that it is reported, not what it says |  |
| 603 | `"operator's own record-keeping verified against itself -- a…` → `"XXoperator's own record-keeping verified against itself --…` | [message] wording of a human-readable message; the tests assert that it is reported, not what it says |  |
| 604 | `"can rewrite the whole history and re-sign it so it verifie…` → `"can rewrite the whole history and re-sign it so it verifie…` | [message] wording of a human-readable message; the tests assert that it is reported, not what it says |  |
| 604 | `"can rewrite the whole history and re-sign it so it verifie…` → `"XXcan rewrite the whole history and re-sign it so it verif…` | [message] wording of a human-readable message; the tests assert that it is reported, not what it says |  |
| 604 | `"can rewrite the whole history and re-sign it so it verifie…` → `"CAN REWRITE THE WHOLE HISTORY AND RE-SIGN IT SO IT VERIFIE…` | [message] wording of a human-readable message; the tests assert that it is reported, not what it says |  |
| 605 | `"anchor is adversarial against the operator. Pass require_w…` → `"XXanchor is adversarial against the operator. Pass require…` | [message] wording of a human-readable message; the tests assert that it is reported, not what it says |  |
| 605 | `"anchor is adversarial against the operator. Pass require_w…` → `"anchor is adversarial against the operator. pass require_w…` | [message] wording of a human-readable message; the tests assert that it is reported, not what it says |  |
| 605 | `"anchor is adversarial against the operator. Pass require_w…` → `"ANCHOR IS ADVERSARIAL AGAINST THE OPERATOR. PASS REQUIRE_W…` | [message] wording of a human-readable message; the tests assert that it is reported, not what it says |  |
| 619 | `""` → `"XXXX"` | [literal] literal `""` is not asserted by any test |  |
| 620 | `_att_keys.add(_wk)` → `_att_keys.add(None)` | [call_arg] a call argument dropped or set to None, and no test observes the difference |  |
| 628 | `verify_attestation(_att, witness_pubkey=(_wk if witnesses a…` → `verify_attestation(_att, )` | [call_arg] a call argument dropped or set to None, and no test observes the difference |  |
| 628 | `verify_attestation(_att, witness_pubkey=(_wk if witnesses a…` → `verify_attestation(_att, witness_pubkey=None)` | [call_arg] a call argument dropped or set to None, and no test observes the difference |  |
| 628 | `witnesses and _wk in witnesses` → `(witnesses and _wk in witnesses) or True` | [condition] condition changed and no test input makes the original and the mutant disagree |  |
| 628 | `witnesses and _wk in witnesses` → `(witnesses and _wk in witnesses) and False` | [condition] condition changed and no test input makes the original and the mutant disagree |  |
| 628 | `in` → `not in` | [condition] condition changed and no test input makes the original and the mutant disagree |  |
| 630 | `12` → `13` | [constant] constant 12 -> 13: no test sits on the boundary this constant draws |  |
| 632 | `continue` → `break` | [other] Continue mutation not observed by any test |  |
| 634 | `f"an attestation from {_wk[:12]}... is unsigned, so it " f"…` → `None` | [unexecuted] no test executes line 634; `verify_bundle` runs under 123 test(s), none of which reaches this statement |  |
| 634 | `12` → `13` | [unexecuted] no test executes line 634; `verify_bundle` runs under 0 test(s), none of which reaches this statement |  |
| 643 | `12` → `13` | [constant] constant 12 -> 13: no test sits on the boundary this constant draws |  |
| 648 | `12` → `13` | [constant] constant 12 -> 13: no test sits on the boundary this constant draws |  |
| 667 | `(not _att.get("store_id")` → `_att.get("store_id"` | [condition] condition changed and no test input makes the original and the mutant disagree |  |
| 667 | `_att.get("store_id")` → `_att.get(None)` | [call_arg] a call argument dropped or set to None, and no test observes the difference |  |
| 667 | `"store_id"` → `"STORE_ID"` | [unread_field] lookup of `store_id`: no test input makes the renamed key read differently |  |
| 667 | `"store_id"` → `"XXstore_idXX"` | [unread_field] lookup of `store_id`: no test input makes the renamed key read differently |  |
| 668 | `_int_or(_seen.get("n_writes"), 0)` → `_int_or(None, 0)` | [call_arg] a call argument dropped or set to None, and no test observes the difference |  |
| 668 | `_int_or(_seen.get("n_writes"), 0)` → `_int_or(_seen.get("n_writes"), None)` | [call_arg] a call argument dropped or set to None, and no test observes the difference |  |
| 668 | `_seen.get("n_writes")` → `_seen.get(None)` | [call_arg] a call argument dropped or set to None, and no test observes the difference |  |
| 668 | `"n_writes"` → `"XXn_writesXX"` | [unread_field] lookup of `n_writes`: no test input makes the renamed key read differently |  |
| 668 | `"n_writes"` → `"N_WRITES"` | [unread_field] lookup of `n_writes`: no test input makes the renamed key read differently |  |
| 668 | `0` → `1` | [constant] constant 0 -> 1: no test sits on the boundary this constant draws |  |
| 669 | `1` → `2` | [constant] constant 1 -> 2: no test sits on the boundary this constant draws |  |
| 669 | `"hash"` → `"XXhashXX"` | [unread_field] lookup of `hash`: no test input makes the renamed key read differently |  |
| 669 | `"hash"` → `"HASH"` | [unread_field] lookup of `hash`: no test input makes the renamed key read differently |  |
| 669 | `== _seen["writes_tip"]` → `!= _seen["writes_tip"]` | [condition] condition changed and no test input makes the original and the mutant disagree |  |
| 669 | `bool(_seen.get("writes_tip")) and 0 < _n_seen <= len(wc)` → `bool(_seen.get("writes_tip")) or 0 < _n_seen <= len(wc)` | [condition] condition changed and no test input makes the original and the mutant disagree |  |
| 669 | `bool(_seen.get("writes_tip"))` → `bool(None)` | [call_arg] a call argument dropped or set to None, and no test observes the difference |  |
| 669 | `_seen.get("writes_tip")` → `_seen.get(None)` | [call_arg] a call argument dropped or set to None, and no test observes the difference |  |
| 669 | `"writes_tip"` → `"WRITES_TIP"` | [unread_field] lookup of `writes_tip`: no test input makes the renamed key read differently |  |
| 669 | `"writes_tip"` → `"XXwrites_tipXX"` | [unread_field] lookup of `writes_tip`: no test input makes the renamed key read differently |  |
| 669 | `0` → `1` | [constant] constant 0 -> 1: no test sits on the boundary this constant draws |  |
| 669 | `< _n_seen` → `<= _n_seen` | [condition] condition changed and no test input makes the original and the mutant disagree |  |
| 669 | `<= len(wc)` → `< len(wc)` | [condition] condition changed and no test input makes the original and the mutant disagree |  |
| 669 | `_chain_links = bool(_seen.get("writes_tip")) and 0 < _n_see…` → `_chain_links = None` | [assignment] assigned value replaced and no test observes it |  |
| 669 | `wc[_n_seen - 1].get("hash")` → `wc[_n_seen - 1].get(None)` | [call_arg] a call argument dropped or set to None, and no test observes the difference |  |
| 670 | `not _chain_links` → `_chain_links` | [condition] condition changed and no test input makes the original and the mutant disagree |  |
| 671 | `f"witness {_wk[:12]}... attested store {str(_att['store_id'…` → `None` | [call_arg] a call argument dropped or set to None, and no test observes the difference |  |
| 671 | `12` → `13` | [constant] constant 12 -> 13: no test sits on the boundary this constant draws |  |
| 671 | `str(_att['store_id'])` → `str(None)` | [call_arg] a call argument dropped or set to None, and no test observes the difference |  |
| 671 | `24` → `25` | [constant] constant 24 -> 25: no test sits on the boundary this constant draws |  |
| 673 | `str(_wid_b)` → `str(None)` | [call_arg] a call argument dropped or set to None, and no test observes the difference |  |
| 673 | `24` → `25` | [constant] constant 24 -> 25: no test sits on the boundary this constant draws |  |
| 675 | `continue` → `break` | [other] Continue mutation not observed by any test |  |
| 676 | `not _name_matches` → `_name_matches` | [condition] condition changed and no test input makes the original and the mutant disagree |  |
| 677 | `f"witness {_wk[:12]}... calls this store {str(_att['store_i…` → `None` | [unexecuted] no test executes line 677; `verify_bundle` runs under 0 test(s), none of which reaches this statement |  |
| 678 | `12` → `13` | [unexecuted] no test executes line 678; `verify_bundle` runs under 0 test(s), none of which reaches this statement |  |
| 678 | `str(_att['store_id'])` → `str(None)` | [unexecuted] no test executes line 678; `verify_bundle` runs under 0 test(s), none of which reaches this statement |  |
| 678 | `'store_id'` → `'XXstore_idXX'` | [unexecuted] no test executes line 678; `verify_bundle` runs under 0 test(s), none of which reaches this statement |  |
| 678 | `'store_id'` → `'STORE_ID'` | [unexecuted] no test executes line 678; `verify_bundle` runs under 0 test(s), none of which reaches this statement |  |
| 678 | `24` → `25` | [unexecuted] no test executes line 678; `verify_bundle` runs under 0 test(s), none of which reaches this statement |  |
| 679 | `str(_wid_b)` → `str(None)` | [unexecuted] no test executes line 679; `verify_bundle` runs under 0 test(s), none of which reaches this statement |  |
| 679 | `24` → `25` | [unexecuted] no test executes line 679; `verify_bundle` runs under 0 test(s), none of which reaches this statement |  |
| 684 | `_int_or(_seen.get("n_writes"), 0)` → `_int_or(_seen.get("n_writes"), None)` | [call_arg] a call argument dropped or set to None, and no test observes the difference |  |
| 684 | `0` → `1` | [constant] constant 0 -> 1: no test sits on the boundary this constant draws |  |
| 685 | `_int_or(anchor.get("n_writes"), 0)` → `_int_or(anchor.get("n_writes"), None)` | [call_arg] a call argument dropped or set to None, and no test observes the difference |  |
| 685 | `0` → `1` | [constant] constant 0 -> 1: no test sits on the boundary this constant draws |  |
| 685 | `> _n_seen` → `>= _n_seen` | [condition] condition changed and no test input makes the original and the mutant disagree |  |
| 691 | `_forked = False` → `_forked = None` | [assignment] assigned value replaced and no test observes it |  |
| 691 | `False` → `True` | [condition] condition changed and no test input makes the original and the mutant disagree |  |
| 692 | `_newer and 0 < _n_seen <= len(wc)` → `_newer or 0 < _n_seen <= len(wc)` | [condition] condition changed and no test input makes the original and the mutant disagree |  |
| 692 | `0` → `1` | [constant] constant 0 -> 1: no test sits on the boundary this constant draws |  |
| 692 | `< _n_seen` → `<= _n_seen` | [condition] condition changed and no test input makes the original and the mutant disagree |  |
| 692 | `<= len(wc)` → `< len(wc)` | [condition] condition changed and no test input makes the original and the mutant disagree |  |
| 695 | `12` → `13` | [constant] constant 12 -> 13: no test sits on the boundary this constant draws |  |
| 695 | `str(_seen['writes_tip'])` → `str(None)` | [call_arg] a call argument dropped or set to None, and no test observes the difference |  |
| 695 | `12` → `13` | [constant] constant 12 -> 13: no test sits on the boundary this constant draws |  |
| 697 | `str(wc[_n_seen - 1].get('hash'))` → `str(None)` | [call_arg] a call argument dropped or set to None, and no test observes the difference |  |
| 697 | `wc[_n_seen - 1].get('hash')` → `wc[_n_seen - 1].get(None)` | [call_arg] a call argument dropped or set to None, and no test observes the difference |  |
| 697 | `_n_seen - 1` → `_n_seen + 1` | [arithmetic] arithmetic operator swapped and no test observes the result |  |
| 697 | `1` → `2` | [constant] constant 1 -> 2: no test sits on the boundary this constant draws |  |
| 697 | `'hash'` → `'HASH'` | [message] wording of a human-readable message; the tests assert that it is reported, not what it says |  |
| 697 | `'hash'` → `'XXhashXX'` | [message] wording of a human-readable message; the tests assert that it is reported, not what it says |  |
| 697 | `12` → `13` | [constant] constant 12 -> 13: no test sits on the boundary this constant draws |  |
| 700 | `_newer` → `(_newer) or True` | [condition] condition changed and no test input makes the original and the mutant disagree |  |
| 701 | `12` → `13` | [constant] constant 12 -> 13: no test sits on the boundary this constant draws |  |
| 702 | `str(_seen.get('writes_tip'))` → `str(None)` | [call_arg] a call argument dropped or set to None, and no test observes the difference |  |
| 702 | `_seen.get('writes_tip')` → `_seen.get(None)` | [call_arg] a call argument dropped or set to None, and no test observes the difference |  |
| 702 | `'writes_tip'` → `'XXwrites_tipXX'` | [message] wording of a human-readable message; the tests assert that it is reported, not what it says |  |
| 702 | `'writes_tip'` → `'WRITES_TIP'` | [message] wording of a human-readable message; the tests assert that it is reported, not what it says |  |
| 702 | `12` → `13` | [constant] constant 12 -> 13: no test sits on the boundary this constant draws |  |
| 702 | `_seen.get('n_writes')` → `_seen.get(None)` | [call_arg] a call argument dropped or set to None, and no test observes the difference |  |
| 702 | `'n_writes'` → `'N_WRITES'` | [message] wording of a human-readable message; the tests assert that it is reported, not what it says |  |
| 702 | `'n_writes'` → `'XXn_writesXX'` | [message] wording of a human-readable message; the tests assert that it is reported, not what it says |  |
| 703 | `'which this bundle extends, verified against its own chain'` → `'XXwhich this bundle extends, verified against its own chai…` | [message] wording of a human-readable message; the tests assert that it is reported, not what it says |  |
| 703 | `'which this bundle extends, verified against its own chain'…` → `'which this bundle extends, verified against its own chain'…` | [condition] condition changed and no test input makes the original and the mutant disagree |  |
| 703 | `'which this bundle extends, verified against its own chain'` → `'WHICH THIS BUNDLE EXTENDS, VERIFIED AGAINST ITS OWN CHAIN'` | [message] wording of a human-readable message; the tests assert that it is reported, not what it says |  |
| 703 | `'which this bundle extends, verified against its own chain'…` → `'which this bundle extends, verified against its own chain'…` | [condition] condition changed and no test input makes the original and the mutant disagree |  |
| 703 | `'which this bundle CONTRADICTS at the same or lower height'` → `'which this bundle contradicts at the same or lower height'` | [message] wording of a human-readable message; the tests assert that it is reported, not what it says |  |
| 703 | `'which this bundle CONTRADICTS at the same or lower height'` → `'XXwhich this bundle CONTRADICTS at the same or lower heigh…` | [message] wording of a human-readable message; the tests assert that it is reported, not what it says |  |
| 703 | `'which this bundle CONTRADICTS at the same or lower height'` → `'WHICH THIS BUNDLE CONTRADICTS AT THE SAME OR LOWER HEIGHT'` | [message] wording of a human-readable message; the tests assert that it is reported, not what it says |  |
| 708 | `not in` → `in` | [condition] condition changed and no test input makes the original and the mutant disagree |  |
| 712 | `str(m)` → `str(None)` | [call_arg] a call argument dropped or set to None, and no test observes the difference |  |
| 712 | `12` → `13` | [constant] constant 12 -> 13: no test sits on the boundary this constant draws |  |
| 712 | `'...'` → `'XX...XX'` | [message] wording of a human-readable message; the tests assert that it is reported, not what it says |  |
| 712 | `3` → `4` | [constant] constant 3 -> 4: no test sits on the boundary this constant draws |  |
| 720 | `r.get('reason', '')` → `r.get('reason', )` | [call_arg] a call argument dropped or set to None, and no test observes the difference |  |
| 720 | `r.get('reason', '')` → `r.get('reason', None)` | [call_arg] a call argument dropped or set to None, and no test observes the difference |  |
| 720 | `r.get('reason', '')` → `r.get(None, '')` | [call_arg] a call argument dropped or set to None, and no test observes the difference |  |
| 720 | `'reason'` → `'REASON'` | [message] wording of a human-readable message; the tests assert that it is reported, not what it says |  |
| 720 | `'reason'` → `'XXreasonXX'` | [message] wording of a human-readable message; the tests assert that it is reported, not what it says |  |
| 720 | `''` → `'XXXX'` | [message] wording of a human-readable message; the tests assert that it is reported, not what it says |  |
| 720 | `80` → `81` | [constant] constant 80 -> 81: no test sits on the boundary this constant draws |  |
| 720 | `3` → `4` | [constant] constant 3 -> 4: no test sits on the boundary this constant draws |  |
| 739 | `"the store reported its own write-verification as FAILED at…` → `"the store reported its own write-verification as failed at…` | [message] wording of a human-readable message; the tests assert that it is reported, not what it says |  |
| 739 | `"the store reported its own write-verification as FAILED at…` → `"XXthe store reported its own write-verification as FAILED …` | [message] wording of a human-readable message; the tests assert that it is reported, not what it says |  |
| 740 | `""` → `"XXXX"` | [message] wording of a human-readable message; the tests assert that it is reported, not what it says |  |
| 740 | `proof.get("problems")` → `(proof.get("problems")) and False` | [condition] condition changed and no test input makes the original and the mutant disagree |  |
| 740 | `proof.get("problems")` → `(proof.get("problems")) or True` | [condition] condition changed and no test input makes the original and the mutant disagree |  |
| 740 | `'; '` → `'XX; XX'` | [message] wording of a human-readable message; the tests assert that it is reported, not what it says |  |
| 740 | `proof.get('problems') or []` → `proof.get('problems') and []` | [condition] condition changed and no test input makes the original and the mutant disagree |  |
| 740 | `proof.get('problems')` → `proof.get(None)` | [call_arg] a call argument dropped or set to None, and no test observes the difference |  |
| 740 | `'problems'` → `'PROBLEMS'` | [message] wording of a human-readable message; the tests assert that it is reported, not what it says |  |
| 740 | `'problems'` → `'XXproblemsXX'` | [message] wording of a human-readable message; the tests assert that it is reported, not what it says |  |
| 740 | `proof.get("problems")` → `proof.get(None)` | [call_arg] a call argument dropped or set to None, and no test observes the difference |  |
| 740 | `"problems"` → `"PROBLEMS"` | [message] wording of a human-readable message; the tests assert that it is reported, not what it says |  |
| 740 | `"problems"` → `"XXproblemsXX"` | [message] wording of a human-readable message; the tests assert that it is reported, not what it says |  |
| 741 | `" -- the chains below re-walk consistently, but the RECORDS…` → `" -- the chains below re-walk consistently, but the records…` | [message] wording of a human-readable message; the tests assert that it is reported, not what it says |  |
| 741 | `" -- the chains below re-walk consistently, but the RECORDS…` → `"XX -- the chains below re-walk consistently, but the RECOR…` | [message] wording of a human-readable message; the tests assert that it is reported, not what it says |  |
| 741 | `" -- the chains below re-walk consistently, but the RECORDS…` → `" -- THE CHAINS BELOW RE-WALK CONSISTENTLY, BUT THE RECORDS…` | [message] wording of a human-readable message; the tests assert that it is reported, not what it says |  |
| 758 | `isinstance(gov_by_req, dict) and isinstance(gov_total, int)` → `isinstance(gov_by_req, dict) or isinstance(gov_total, int)` | [condition] condition changed and no test input makes the original and the mutant disagree |  |
| 759 | `v.get("erased", 0)` → `v.get("erased", )` | [call_arg] a call argument dropped or set to None, and no test observes the difference |  |
| 759 | `v.get("erased", 0)` → `v.get("erased", None)` | [call_arg] a call argument dropped or set to None, and no test observes the difference |  |
| 759 | `0` → `1` | [constant] constant 0 -> 1: no test sits on the boundary this constant draws |  |
| 770 | `"cross_tenant_chain"` → `"CROSS_TENANT_CHAIN"` | [unread_field] lookup of `cross_tenant_chain`: no test input makes the renamed key read differently |  |
| 770 | `"cross_tenant_chain"` → `"XXcross_tenant_chainXX"` | [unread_field] lookup of `cross_tenant_chain`: no test input makes the renamed key read differently |  |
| 770 | `bundle.get("cross_tenant_chain")` → `bundle.get(None)` | [call_arg] a call argument dropped or set to None, and no test observes the difference |  |
| 771 | `"record-count coverage NOT CHECKED: this bundle's chain spa…` → `"XXrecord-count coverage NOT CHECKED: this bundle's chain s…` | [unexecuted] no test executes line 771; `verify_bundle` runs under 0 test(s), none of which reaches this statement |  |
| 771 | `"record-count coverage NOT CHECKED: this bundle's chain spa…` → `"RECORD-COUNT COVERAGE NOT CHECKED: THIS BUNDLE'S CHAIN SPA…` | [unexecuted] no test executes line 771; `verify_bundle` runs under 0 test(s), none of which reaches this statement |  |
| 771 | `"record-count coverage NOT CHECKED: this bundle's chain spa…` → `"record-count coverage not checked: this bundle's chain spa…` | [unexecuted] no test executes line 771; `verify_bundle` runs under 0 test(s), none of which reaches this statement |  |
| 771 | `"record-count coverage NOT CHECKED: this bundle's chain spa…` → `None` | [unexecuted] no test executes line 771; `verify_bundle` runs under 0 test(s), none of which reaches this statement |  |
| 772 | `"its record count is tenant-scoped, so the two are not comp…` → `"ITS RECORD COUNT IS TENANT-SCOPED, SO THE TWO ARE NOT COMP…` | [unexecuted] no test executes line 772; `verify_bundle` runs under 0 test(s), none of which reaches this statement |  |
| 772 | `"its record count is tenant-scoped, so the two are not comp…` → `"XXits record count is tenant-scoped, so the two are not co…` | [unexecuted] no test executes line 772; `verify_bundle` runs under 0 test(s), none of which reaches this statement |  |
| 772 | `"its record count is tenant-scoped, so the two are not comp…` → `"its record count is tenant-scoped, so the two are not comp…` | [unexecuted] no test executes line 772; `verify_bundle` runs under 0 test(s), none of which reaches this statement |  |
| 773 | `"from an unbound handle for this check."` → `"FROM AN UNBOUND HANDLE FOR THIS CHECK."` | [unexecuted] no test executes line 773; `verify_bundle` runs under 0 test(s), none of which reaches this statement |  |
| 773 | `"from an unbound handle for this check."` → `"XXfrom an unbound handle for this check.XX"` | [unexecuted] no test executes line 773; `verify_bundle` runs under 0 test(s), none of which reaches this statement |  |
| 774 | `n_records = None` → `n_records = ""` | [unexecuted] no test executes line 774; `verify_bundle` runs under 0 test(s), none of which reaches this statement |  |
| 780 | `n_records - len(wc)` → `n_records + len(wc)` | [arithmetic] arithmetic operator swapped and no test observes the result |  |
| 787 | `"store is empty: nothing to verify (this is not evidence of…` → `"XXstore is empty: nothing to verify (this is not evidence …` | [literal] literal `"store is empty: nothing to verify (this` is not asserted by any test |  |
| 789 | `"this bundle carries NO write or tombstone receipts -- noth…` → `None` | [unexecuted] no test executes line 789; `verify_bundle` runs under 0 test(s), none of which reaches this statement |  |
| 789 | `"this bundle carries NO write or tombstone receipts -- noth…` → `"this bundle carries no write or tombstone receipts -- noth…` | [unexecuted] no test executes line 789; `verify_bundle` runs under 0 test(s), none of which reaches this statement |  |
| 789 | `"this bundle carries NO write or tombstone receipts -- noth…` → `"THIS BUNDLE CARRIES NO WRITE OR TOMBSTONE RECEIPTS -- NOTH…` | [unexecuted] no test executes line 789; `verify_bundle` runs under 0 test(s), none of which reaches this statement |  |
| 789 | `"this bundle carries NO write or tombstone receipts -- noth…` → `"XXthis bundle carries NO write or tombstone receipts -- no…` | [unexecuted] no test executes line 789; `verify_bundle` runs under 0 test(s), none of which reaches this statement |  |
| 790 | `"not the same as verified"` → `"XXnot the same as verifiedXX"` | [unexecuted] no test executes line 790; `verify_bundle` runs under 0 test(s), none of which reaches this statement |  |
| 790 | `"not the same as verified"` → `"NOT THE SAME AS VERIFIED"` | [unexecuted] no test executes line 790; `verify_bundle` runs under 0 test(s), none of which reaches this statement |  |
| 802 | `a.get("id")` → `a.get(None)` | [call_arg] a call argument dropped or set to None, and no test observes the difference |  |
| 802 | `"id"` → `"ID"` | [unread_field] lookup of `id`: no test input makes the renamed key read differently |  |
| 802 | `"id"` → `"XXidXX"` | [unread_field] lookup of `id`: no test input makes the renamed key read differently |  |
| 803 | `bool(wc) and isinstance(n_records, int)` → `bool(wc) or isinstance(n_records, int)` | [condition] condition changed and no test input makes the original and the mutant disagree |  |
| 803 | `bool(wc) and isinstance(n_records, int) and n_records <= le…` → `bool(wc) and isinstance(n_records, int) or n_records <= len…` | [condition] condition changed and no test input makes the original and the mutant disagree |  |
| 804 | `1` → `2` | [constant] constant 1 -> 2: no test sits on the boundary this constant draws |  |
| 804 | `a.get("status") == "active" and a.get("state") == "granted"` → `a.get("status") == "active" or a.get("state") == "granted"` | [condition] condition changed and no test input makes the original and the mutant disagree |  |
| 804 | `a.get("status")` → `a.get(None)` | [call_arg] a call argument dropped or set to None, and no test observes the difference |  |
| 804 | `"status"` → `"STATUS"` | [unread_field] lookup of `status`: no test input makes the renamed key read differently |  |
| 804 | `"status"` → `"XXstatusXX"` | [unread_field] lookup of `status`: no test input makes the renamed key read differently |  |
| 804 | `== "active"` → `!= "active"` | [condition] condition changed and no test input makes the original and the mutant disagree |  |
| 804 | `"active"` → `"ACTIVE"` | [literal] literal `"active"` is not asserted by any test |  |
| 804 | `"active"` → `"XXactiveXX"` | [literal] literal `"active"` is not asserted by any test |  |
| 804 | `a.get("state")` → `a.get(None)` | [call_arg] a call argument dropped or set to None, and no test observes the difference |  |
| 804 | `"state"` → `"XXstateXX"` | [unread_field] lookup of `state`: no test input makes the renamed key read differently |  |
| 804 | `"state"` → `"STATE"` | [unread_field] lookup of `state`: no test input makes the renamed key read differently |  |
| 804 | `== "granted"` → `!= "granted"` | [condition] condition changed and no test input makes the original and the mutant disagree |  |
| 804 | `live = sum(1 for a in acl_acts if a.get("status") == "activ…` → `live = None` | [assignment] assigned value replaced and no test observes it |  |
| 804 | `"granted"` → `"XXgrantedXX"` | [literal] literal `"granted"` is not asserted by any test |  |
| 804 | `"granted"` → `"GRANTED"` | [literal] literal `"granted"` is not asserted by any test |  |
| 807 | `', '` → `'XX, XX'` | [message] wording of a human-readable message; the tests assert that it is reported, not what it says |  |
| 807 | `5` → `6` | [constant] constant 5 -> 6: no test sits on the boundary this constant draws |  |
| 807 | `' ...' if len(missing) > 5 else ''` → `' ...' if (len(missing) > 5) or True else ''` | [condition] condition changed and no test input makes the original and the mutant disagree |  |
| 807 | `' ...'` → `'XX ...XX'` | [message] wording of a human-readable message; the tests assert that it is reported, not what it says |  |
| 807 | `' ...' if len(missing) > 5 else ''` → `' ...' if (len(missing) > 5) and False else ''` | [condition] condition changed and no test input makes the original and the mutant disagree |  |
| 807 | `> 5` → `>= 5` | [condition] condition changed and no test input makes the original and the mutant disagree |  |
| 807 | `5` → `6` | [constant] constant 5 -> 6: no test sits on the boundary this constant draws |  |
| 807 | `''` → `'XXXX'` | [message] wording of a human-readable message; the tests assert that it is reported, not what it says |  |
| 810 | `f"{len(missing)} access-control act(s) predate this store's…` → `None` | [unexecuted] no test executes line 810; `verify_bundle` runs under 0 test(s), none of which reaches this statement |  |
| 824 | `", "` → `"XX, XX"` | [message] wording of a human-readable message; the tests assert that it is reported, not what it says |  |
| 824 | `m.get('field')` → `m.get(None)` | [call_arg] a call argument dropped or set to None, and no test observes the difference |  |
| 824 | `'field'` → `'FIELD'` | [message] wording of a human-readable message; the tests assert that it is reported, not what it says |  |
| 824 | `'field'` → `'XXfieldXX'` | [message] wording of a human-readable message; the tests assert that it is reported, not what it says |  |
| 824 | `5` → `6` | [constant] constant 5 -> 6: no test sits on the boundary this constant draws |  |
| 825 | `len(mismatched) > 5` → `(len(mismatched) > 5) and False` | [condition] condition changed and no test input makes the original and the mutant disagree |  |
| 825 | `" ..."` → `"XX ...XX"` | [message] wording of a human-readable message; the tests assert that it is reported, not what it says |  |
| 825 | `len(mismatched) > 5` → `(len(mismatched) > 5) or True` | [condition] condition changed and no test input makes the original and the mutant disagree |  |
| 825 | `> 5` → `>= 5` | [condition] condition changed and no test input makes the original and the mutant disagree |  |
| 825 | `5` → `6` | [constant] constant 5 -> 6: no test sits on the boundary this constant draws |  |
| 825 | `""` → `"XXXX"` | [message] wording of a human-readable message; the tests assert that it is reported, not what it says |  |
| 831 | `b.get('checked', 0)` → `b.get('checked', None)` | [call_arg] a call argument dropped or set to None, and no test observes the difference |  |
| 831 | `b.get('checked', 0)` → `b.get(None, 0)` | [call_arg] a call argument dropped or set to None, and no test observes the difference |  |
| 831 | `b.get('checked', 0)` → `b.get('checked', )` | [call_arg] a call argument dropped or set to None, and no test observes the difference |  |
| 831 | `b.get('checked', 0)` → `b.get(0)` | [call_arg] a call argument dropped or set to None, and no test observes the difference |  |
| 831 | `'checked'` → `'XXcheckedXX'` | [unread_field] lookup of `checked`: no test input makes the renamed key read differently |  |
| 831 | `'checked'` → `'CHECKED'` | [unread_field] lookup of `checked`: no test input makes the renamed key read differently |  |
| 831 | `0` → `1` | [constant] constant 0 -> 1: no test sits on the boundary this constant draws |  |
| 831 | `b.get('receipted', 0)` → `b.get('receipted', None)` | [call_arg] a call argument dropped or set to None, and no test observes the difference |  |
| 831 | `b.get('receipted', 0)` → `b.get(0)` | [call_arg] a call argument dropped or set to None, and no test observes the difference |  |
| 831 | `b.get('receipted', 0)` → `b.get('receipted', )` | [call_arg] a call argument dropped or set to None, and no test observes the difference |  |
| 831 | `b.get('receipted', 0)` → `b.get(None, 0)` | [call_arg] a call argument dropped or set to None, and no test observes the difference |  |
| 831 | `'receipted'` → `'XXreceiptedXX'` | [unread_field] lookup of `receipted`: no test input makes the renamed key read differently |  |
| 831 | `'receipted'` → `'RECEIPTED'` | [unread_field] lookup of `receipted`: no test input makes the renamed key read differently |  |
| 831 | `0` → `1` | [constant] constant 0 -> 1: no test sits on the boundary this constant draws |  |
| 857 | `bundle.get("generated_ts")` → `bundle.get(None)` | [call_arg] a call argument dropped or set to None, and no test observes the difference |  |
| 857 | `"generated_ts"` → `"GENERATED_TS"` | [unread_field] lookup of `generated_ts`: no test input makes the renamed key read differently |  |
| 857 | `"generated_ts"` → `"XXgenerated_tsXX"` | [unread_field] lookup of `generated_ts`: no test input makes the renamed key read differently |  |
| 857 | `_gen_ts = bundle.get("generated_ts")` → `_gen_ts = None` | [assignment] assigned value replaced and no test observes it |  |
| 861 | `"GROWTH NOT VERIFIED: no store receipt chain was supplied, …` → `"XXGROWTH NOT VERIFIED: no store receipt chain was supplied…` | [message] wording of a human-readable message; the tests assert that it is reported, not what it says |  |
| 861 | `"GROWTH NOT VERIFIED: no store receipt chain was supplied, …` → `"GROWTH NOT VERIFIED: NO STORE RECEIPT CHAIN WAS SUPPLIED, …` | [message] wording of a human-readable message; the tests assert that it is reported, not what it says |  |
| 862 | `` "bundle does not cover are classified by their own `ts` -- … `` → `` "BUNDLE DOES NOT COVER ARE CLASSIFIED BY THEIR OWN `TS` -- … `` | [message] wording of a human-readable message; the tests assert that it is reported, not what it says |  |
| 862 | `` "bundle does not cover are classified by their own `ts` -- … `` → `` "XXbundle does not cover are classified by their own `ts` -… `` | [message] wording of a human-readable message; the tests assert that it is reported, not what it says |  |
| 863 | `"writer controls. Pass store_receipts= for the chain-member…` → `"XXwriter controls. Pass store_receipts= for the chain-memb…` | [message] wording of a human-readable message; the tests assert that it is reported, not what it says |  |
| 863 | `"writer controls. Pass store_receipts= for the chain-member…` → `"WRITER CONTROLS. PASS STORE_RECEIPTS= FOR THE CHAIN-MEMBER…` | [message] wording of a human-readable message; the tests assert that it is reported, not what it says |  |
| 863 | `"writer controls. Pass store_receipts= for the chain-member…` → `"writer controls. pass store_receipts= for the chain-member…` | [message] wording of a human-readable message; the tests assert that it is reported, not what it says |  |
| 877 | `"this bundle's receipt chain is not a PREFIX of the store's…` → `None` | [unexecuted] no test executes line 877; `verify_bundle` runs under 0 test(s), none of which reaches this statement |  |
| 877 | `"this bundle's receipt chain is not a PREFIX of the store's…` → `"THIS BUNDLE'S RECEIPT CHAIN IS NOT A PREFIX OF THE STORE'S…` | [unexecuted] no test executes line 877; `verify_bundle` runs under 0 test(s), none of which reaches this statement |  |
| 877 | `"this bundle's receipt chain is not a PREFIX of the store's…` → `"XXthis bundle's receipt chain is not a PREFIX of the store…` | [unexecuted] no test executes line 877; `verify_bundle` runs under 0 test(s), none of which reaches this statement |  |
| 877 | `"this bundle's receipt chain is not a PREFIX of the store's…` → `"this bundle's receipt chain is not a prefix of the store's…` | [unexecuted] no test executes line 877; `verify_bundle` runs under 0 test(s), none of which reaches this statement |  |
| 878 | `"history was rewritten after the export, not merely appende…` → `"HISTORY WAS REWRITTEN AFTER THE EXPORT, NOT MERELY APPENDE…` | [unexecuted] no test executes line 878; `verify_bundle` runs under 0 test(s), none of which reaches this statement |  |
| 878 | `"history was rewritten after the export, not merely appende…` → `"XXhistory was rewritten after the export, not merely appen…` | [unexecuted] no test executes line 878; `verify_bundle` runs under 0 test(s), none of which reaches this statement |  |
| 880 | `f"the store's chain extends this bundle's append-only " f"(…` → `None` | [call_arg] a call argument dropped or set to None, and no test observes the difference |  |
| 890 | `r.get("memory_id")` → `r.get(None)` | [call_arg] a call argument dropped or set to None, and no test observes the difference |  |
| 890 | `"memory_id"` → `"XXmemory_idXX"` | [unread_field] lookup of `memory_id`: no test input makes the renamed key read differently |  |
| 890 | `"memory_id"` → `"MEMORY_ID"` | [unread_field] lookup of `memory_id`: no test input makes the renamed key read differently |  |
| 890 | `r.get("sig")` → `r.get(None)` | [call_arg] a call argument dropped or set to None, and no test observes the difference |  |
| 890 | `"sig"` → `"XXsigXX"` | [unread_field] lookup of `sig`: no test input makes the renamed key read differently |  |
| 890 | `"sig"` → `"SIG"` | [unread_field] lookup of `sig`: no test input makes the renamed key read differently |  |
| 893 | `5` → `6` | [constant] constant 5 -> 6: no test sits on the boundary this constant draws |  |
| 894 | `' ...'` → `'XX ...XX'` | [message] wording of a human-readable message; the tests assert that it is reported, not what it says |  |
| 894 | `' ...' if len(_unsigned) > 5 else ''` → `' ...' if (len(_unsigned) > 5) and False else ''` | [condition] condition changed and no test input makes the original and the mutant disagree |  |
| 894 | `' ...' if len(_unsigned) > 5 else ''` → `' ...' if (len(_unsigned) > 5) or True else ''` | [condition] condition changed and no test input makes the original and the mutant disagree |  |
| 894 | `> 5` → `>= 5` | [condition] condition changed and no test input makes the original and the mutant disagree |  |
| 894 | `5` → `6` | [constant] constant 5 -> 6: no test sits on the boundary this constant draws |  |
| 894 | `''` → `'XXXX'` | [message] wording of a human-readable message; the tests assert that it is reported, not what it says |  |
| 897 | `bundle.get("baseline_complete")` → `bundle.get(None)` | [call_arg] a call argument dropped or set to None, and no test observes the difference |  |
| 897 | `"baseline_complete"` → `"BASELINE_COMPLETE"` | [unread_field] lookup of `baseline_complete`: no test input makes the renamed key read differently |  |
| 897 | `"baseline_complete"` → `"XXbaseline_completeXX"` | [unread_field] lookup of `baseline_complete`: no test input makes the renamed key read differently |  |
| 897 | `is` → `is not` | [condition] condition changed and no test input makes the original and the mutant disagree |  |
| 897 | `False` → `True` | [condition] condition changed and no test input makes the original and the mutant disagree |  |
| 898 | `"the store already held records covered by no receipt when …` → `None` | [call_arg] a call argument dropped or set to None, and no test observes the difference |  |
| 898 | `"the store already held records covered by no receipt when …` → `"THE STORE ALREADY HELD RECORDS COVERED BY NO RECEIPT WHEN …` | [message] wording of a human-readable message; the tests assert that it is reported, not what it says |  |
| 898 | `"the store already held records covered by no receipt when …` → `"XXthe store already held records covered by no receipt whe…` | [message] wording of a human-readable message; the tests assert that it is reported, not what it says |  |
| 899 | `"taken, so an uncovered record today is not evidence on its…` → `"XXtaken, so an uncovered record today is not evidence on i…` | [message] wording of a human-readable message; the tests assert that it is reported, not what it says |  |
| 899 | `"taken, so an uncovered record today is not evidence on its…` → `"TAKEN, SO AN UNCOVERED RECORD TODAY IS NOT EVIDENCE ON ITS…` | [message] wording of a human-readable message; the tests assert that it is reported, not what it says |  |
| 900 | `"bundle was a partial claim from the start"` → `"XXbundle was a partial claim from the startXX"` | [message] wording of a human-readable message; the tests assert that it is reported, not what it says |  |
| 900 | `"bundle was a partial claim from the start"` → `"BUNDLE WAS A PARTIAL CLAIM FROM THE START"` | [message] wording of a human-readable message; the tests assert that it is reported, not what it says |  |
| 921 | `(_growth if mid in _live_ids else _preexisting).append(mid)` → `(_growth if mid in _live_ids else _preexisting).append(None)` | [call_arg] a call argument dropped or set to None, and no test observes the difference |  |
| 922 | `continue` → `break` | [other] Continue mutation not observed by any test |  |
| 925 | `<= _gen_ts` → `< _gen_ts` | [condition] condition changed and no test input makes the original and the mutant disagree |  |
| 926 | `_preexisting.append(mid)` → `_preexisting.append(None)` | [call_arg] a call argument dropped or set to None, and no test observes the difference |  |
| 928 | `_growth.append(mid)` → `_growth.append(None)` | [call_arg] a call argument dropped or set to None, and no test observes the difference |  |
| 937 | `"is covered by no receipt in the store's CURRENT chain eith…` → `"XXis covered by no receipt in the store's CURRENT chain ei…` | [message] wording of a human-readable message; the tests assert that it is reported, not what it says |  |
| 937 | `"is covered by no receipt in the store's CURRENT chain eith…` → `"is covered by no receipt in the store's current chain eith…` | [message] wording of a human-readable message; the tests assert that it is reported, not what it says |  |
| 937 | `"is covered by no receipt in the store's CURRENT chain eith…` → `"IS COVERED BY NO RECEIPT IN THE STORE'S CURRENT CHAIN EITH…` | [message] wording of a human-readable message; the tests assert that it is reported, not what it says |  |
| 938 | `"baseline was ALREADY incomplete when it was taken, so this…` → `"XXbaseline was ALREADY incomplete when it was taken, so th…` | [message] wording of a human-readable message; the tests assert that it is reported, not what it says |  |
| 939 | `"chain rather than have been inserted -- compare against a …` → `"CHAIN RATHER THAN HAVE BEEN INSERTED -- COMPARE AGAINST A …` | [message] wording of a human-readable message; the tests assert that it is reported, not what it says |  |
| 939 | `"chain rather than have been inserted -- compare against a …` → `"XXchain rather than have been inserted -- compare against …` | [message] wording of a human-readable message; the tests assert that it is reported, not what it says |  |
| 940 | `and` → `or` | [condition] condition changed and no test input makes the original and the mutant disagree |  |
| 941 | `"is covered by no receipt in the store's CURRENT chain eith…` → `"XXis covered by no receipt in the store's CURRENT chain ei…` | [message] wording of a human-readable message; the tests assert that it is reported, not what it says |  |
| 942 | `"written by this store -- it was inserted out of band"` → `"XXwritten by this store -- it was inserted out of bandXX"` | [message] wording of a human-readable message; the tests assert that it is reported, not what it says |  |
| 942 | `"written by this store -- it was inserted out of band"` → `"WRITTEN BY THIS STORE -- IT WAS INSERTED OUT OF BAND"` | [message] wording of a human-readable message; the tests assert that it is reported, not what it says |  |
| 944 | `` "existed when this bundle was generated (by its own `ts`, w… `` → `` "XXexisted when this bundle was generated (by its own `ts`,… `` | [message] wording of a human-readable message; the tests assert that it is reported, not what it says |  |
| 945 | `"controls) and is covered by no receipt"` → `"CONTROLS) AND IS COVERED BY NO RECEIPT"` | [message] wording of a human-readable message; the tests assert that it is reported, not what it says |  |
| 945 | `"controls) and is covered by no receipt"` → `"XXcontrols) and is covered by no receiptXX"` | [message] wording of a human-readable message; the tests assert that it is reported, not what it says |  |
| 946 | `5` → `6` | [constant] constant 5 -> 6: no test sits on the boundary this constant draws |  |
| 947 | `' ...'` → `'XX ...XX'` | [message] wording of a human-readable message; the tests assert that it is reported, not what it says |  |
| 947 | `' ...' if len(_preexisting) > 5 else ''` → `' ...' if (len(_preexisting) > 5) and False else ''` | [condition] condition changed and no test input makes the original and the mutant disagree |  |
| 947 | `' ...' if len(_preexisting) > 5 else ''` → `' ...' if (len(_preexisting) > 5) or True else ''` | [condition] condition changed and no test input makes the original and the mutant disagree |  |
| 947 | `> 5` → `>= 5` | [condition] condition changed and no test input makes the original and the mutant disagree |  |
| 947 | `5` → `6` | [constant] constant 5 -> 6: no test sits on the boundary this constant draws |  |
| 947 | `''` → `'XXXX'` | [message] wording of a human-readable message; the tests assert that it is reported, not what it says |  |
| 948 | `5` → `6` | [constant] constant 5 -> 6: no test sits on the boundary this constant draws |  |
| 952 | `5` → `6` | [constant] constant 5 -> 6: no test sits on the boundary this constant draws |  |
| 955 | `> 5` → `>= 5` | [condition] condition changed and no test input makes the original and the mutant disagree |  |
| 955 | `5` → `6` | [constant] constant 5 -> 6: no test sits on the boundary this constant draws |  |
| 957 | `len(orph) - 5` → `len(orph) + 5` | [arithmetic] arithmetic operator swapped and no test observes the result |  |
| 957 | `5` → `6` | [constant] constant 5 -> 6: no test sits on the boundary this constant draws |  |
| 959 | `"CONTENT NOT CHECKED: this bundle is content-free by design…` → `"XXCONTENT NOT CHECKED: this bundle is content-free by desi…` | [message] wording of a human-readable message; the tests assert that it is reported, not what it says |  |
| 959 | `"CONTENT NOT CHECKED: this bundle is content-free by design…` → `"CONTENT NOT CHECKED: THIS BUNDLE IS CONTENT-FREE BY DESIGN…` | [message] wording of a human-readable message; the tests assert that it is reported, not what it says |  |
| 960 | `"substituted text verifies here. Pass store_items= (or call…` → `"substituted text verifies here. pass store_items= (or call…` | [message] wording of a human-readable message; the tests assert that it is reported, not what it says |  |
| 960 | `"substituted text verifies here. Pass store_items= (or call…` → `"SUBSTITUTED TEXT VERIFIES HERE. PASS STORE_ITEMS= (OR CALL…` | [message] wording of a human-readable message; the tests assert that it is reported, not what it says |  |
| 960 | `"substituted text verifies here. Pass store_items= (or call…` → `"XXsubstituted text verifies here. Pass store_items= (or ca…` | [message] wording of a human-readable message; the tests assert that it is reported, not what it says |  |
| 961 | `and` → `or` | [condition] condition changed and no test input makes the original and the mutant disagree |  |
| 962 | `"NOT OPERATOR-ADVERSARIAL: without witness co-signatures on…` → `"XXNOT OPERATOR-ADVERSARIAL: without witness co-signatures …` | [message] wording of a human-readable message; the tests assert that it is reported, not what it says |  |
| 962 | `"NOT OPERATOR-ADVERSARIAL: without witness co-signatures on…` → `"NOT OPERATOR-ADVERSARIAL: WITHOUT WITNESS CO-SIGNATURES ON…` | [message] wording of a human-readable message; the tests assert that it is reported, not what it says |  |
| 963 | `"key-holder rewrite is internally consistent by constructio…` → `"XXkey-holder rewrite is internally consistent by construct…` | [message] wording of a human-readable message; the tests assert that it is reported, not what it says |  |
| 963 | `"key-holder rewrite is internally consistent by constructio…` → `"KEY-HOLDER REWRITE IS INTERNALLY CONSISTENT BY CONSTRUCTIO…` | [message] wording of a human-readable message; the tests assert that it is reported, not what it says |  |
| 974 | `"erasure_requests"` → `"XXerasure_requestsXX"` | [unread_field] output key `erasure_requests` is written but no test reads it back |  |
| 974 | `"erasure_requests"` → `"ERASURE_REQUESTS"` | [unread_field] output key `erasure_requests` is written but no test reads it back |  |
| 975 | `"superseded_total"` → `"XXsuperseded_totalXX"` | [unread_field] output key `superseded_total` is written but no test reads it back |  |
| 975 | `"superseded_total"` → `"SUPERSEDED_TOTAL"` | [unread_field] output key `superseded_total` is written but no test reads it back |  |
| 975 | `(bundle.get("supersession") or {}).get("superseded_total", …` → `(bundle.get("supersession") or {}).get("superseded_total", )` | [call_arg] a call argument dropped or set to None, and no test observes the difference |  |
| 975 | `(bundle.get("supersession") or {}).get("superseded_total", …` → `(bundle.get("supersession") or {}).get(0)` | [call_arg] a call argument dropped or set to None, and no test observes the difference |  |
| 975 | `(bundle.get("supersession") or {}).get("superseded_total", …` → `(bundle.get("supersession") or {}).get(None, 0)` | [call_arg] a call argument dropped or set to None, and no test observes the difference |  |
| 975 | `(bundle.get("supersession") or {}).get("superseded_total", …` → `(bundle.get("supersession") or {}).get("superseded_total", …` | [call_arg] a call argument dropped or set to None, and no test observes the difference |  |


### Transparency log: 188 survivors

**`inspeximus/merkle.py` `inclusion_proof`** (2)

| line | mutation | why no test caught it | now |
|---:|---|---|---|
| 80 | `IndexError("leaf index %d out of range for %d leaves" % (m,…` → `IndexError(None)` | [message] exception text only: the bounds tests assert `pytest.raises(IndexError)`, never the message |  |
| 80 | `"leaf index %d out of range for %d leaves"` → `"XXleaf index %d out of range for %d leavesXX"` | [message] exception text only: the bounds tests assert `pytest.raises(IndexError)`, never the message |  |

**`inspeximus/merkle.py` `root_from_inclusion`** (3)

| line | mutation | why no test caught it | now |
|---:|---|---|---|
| 106 | `< n` → `<= n` | [soundness] no test gave the VERIFIER an out-of-range index: bounds were only tested through inclusion_proof(), which raises first. Widened, leaf 0 of a 2-leaf tree verifies at index 2 with its honest path | killed by `test_an_inclusion_proof_for_index_n_is_refused` |
| 115 | `0` → `1` | [equivalent] equivalent: the loop is entered only when `fn & 1 or fn == sn`, and `fn == sn == 0` returned None above, so fn is never 0 inside it; for an even fn != 0, `fn != 0` and `fn != 1` are both true |  |
| 122 | `r if sn == 0 else None` → `r if (sn == 0) or True else None` | [soundness] no test gave a path shorter than the claimed tree size; with the final check forced, a 2-leaf path verifies leaf 0 as a member of a 3-, 4- or 6-leaf tree whose 'root' is the 2-leaf root | killed by `test_a_path_too_short_for_the_claimed_tree_size_is_refused[3]` |

**`inspeximus/merkle.py` `consistency_proof`** (2)

| line | mutation | why no test caught it | now |
|---:|---|---|---|
| 143 | `IndexError("m=%d out of range for %d leaves" % (m, n))` → `IndexError(None)` | [message] exception text only: the bounds tests assert `pytest.raises(IndexError)`, never the message |  |
| 143 | `"m=%d out of range for %d leaves"` → `"XXm=%d out of range for %d leavesXX"` | [message] exception text only: the bounds tests assert `pytest.raises(IndexError)`, never the message |  |

**`inspeximus/merkle.py` `_subproof`** (1)

| line | mutation | why no test caught it | now |
|---:|---|---|---|
| 156 | `_subproof(m - k, mtl[k:], False)` → `_subproof(m - k, mtl[k:], None)` | [equivalent] equivalent: `b` is only read as `if b`, and None is falsy exactly like False |  |

**`inspeximus/merkle.py` `verify_consistency_proof`** (7)

| line | mutation | why no test caught it | now |
|---:|---|---|---|
| 162 | `m < 0 or n < m` → `m < 0 and n < m` | [soundness] the only rollback test (n < m) used an EMPTY proof, which a later guard refuses anyway; the size check itself never decided a test. With `and`, a crafted non-empty proof passes n < m, and m = -1 loops forever | killed by `test_a_rollback_is_refused_whatever_the_proof_says[5-4]` |
| 165 | `first_root == second_root and not proof` → `first_root == second_root or not proof` | [soundness] every m == n test passed the same root twice; with `or`, two DIFFERENT roots at one size and an empty proof verify as consistent -- an equal-size fork | killed by `test_two_different_roots_at_the_same_size_are_a_fork_not_a_consistency[1]` |
| 174 | `False` → `True` | [soundness] no test passed an empty proof for 0 < m < n, so the refusal never ran; flipped, any two roots verify for any sizes | killed by `test_an_empty_consistency_proof_proves_nothing[2]` |
| 181 | `False` → `True` | [soundness] every negative test used a proof of the exact length; flipped, one junk hash appended to any proof returns True before either root is compared | killed by `test_a_consistency_proof_with_a_hash_to_spare_is_refused[2]` |
| 185 | `0` → `1` | [equivalent] equivalent: same shape as merkle.py:115 -- node is never 0 inside this loop (node == last == 0 returned False on the line above); checked by brute force over every (m, n) up to 40 |  |
| 187 | `last >>= 1` → `last = 1` | [coverage-depth] the right-edge climb only runs when the old tree's edge is several levels deep; the round-trip tests stop at 12 leaves and the first honest proof these mutants reject is 13 -> 14 | killed by `test_consistency_round_trips_past_the_sizes_the_suite_stopped_at` |
| 187 | `1` → `2` | [coverage-depth] the right-edge climb only runs when the old tree's edge is several levels deep; the round-trip tests stop at 12 leaves and the first honest proof these mutants reject is 13 -> 14 | killed by `test_consistency_round_trips_past_the_sizes_the_suite_stopped_at` |

**`inspeximus/transparency.py` `RegistrationPolicy.__init__`** (6)

| line | mutation | why no test caught it | now |
|---:|---|---|---|
| 68 | `4096` → `4097` | [boundary] the default ceiling (4096) is never asserted; every test passes max_payload_bytes or a payload far from it | killed by `test_the_payload_ceiling_admits_exactly_its_own_size` |
| 69 | `""` → `"XXXX"` | [unread-output] the default `notes` value is never read back | killed by `test_the_library_re_derives_the_log_it_published` |
| 71 | `ValueError("a Registration Policy needs a name a reader can…` → `ValueError(None)` | [message] exception text only: the empty-name test asserts ValueError, not its message |  |
| 71 | `"a Registration Policy needs a name a reader can cite"` → `"XXa Registration Policy needs a name a reader can citeXX"` | [message] exception text only: the empty-name test asserts ValueError, not its message |  |
| 71 | `"a Registration Policy needs a name a reader can cite"` → `"a registration policy needs a name a reader can cite"` | [message] exception text only: the empty-name test asserts ValueError, not its message |  |
| 71 | `"a Registration Policy needs a name a reader can cite"` → `"A REGISTRATION POLICY NEEDS A NAME A READER CAN CITE"` | [message] exception text only: the empty-name test asserts ValueError, not its message |  |

**`inspeximus/transparency.py` `RegistrationPolicy.as_dict`** (9)

| line | mutation | why no test caught it | now |
|---:|---|---|---|
| 80 | `"scitt_registration_policy"` → `"XXscitt_registration_policyXX"` | [format] the policy's format label (`scitt_registration_policy: 1.0`) is hashed into policy_sha256 but only ever compared with a digest computed by the same code; the published-log pin (new test) re-derives the digest and kills the digest-changing ones, but no test reads the label itself | killed by `test_the_library_re_derives_the_log_it_published` |
| 80 | `"scitt_registration_policy"` → `"SCITT_REGISTRATION_POLICY"` | [format] the policy's format label (`scitt_registration_policy: 1.0`) is hashed into policy_sha256 but only ever compared with a digest computed by the same code; the published-log pin (new test) re-derives the digest and kills the digest-changing ones, but no test reads the label itself | killed by `test_the_library_re_derives_the_log_it_published` |
| 80 | `"1.0"` → `"XX1.0XX"` | [format] the policy's format label (`scitt_registration_policy: 1.0`) is hashed into policy_sha256 but only ever compared with a digest computed by the same code; the published-log pin (new test) re-derives the digest and kills the digest-changing ones, but no test reads the label itself | killed by `test_the_library_re_derives_the_log_it_published` |
| 84 | `"ANY issuer is admitted: this service is deliberately open,…` → `"XXANY issuer is admitted: this service is deliberately ope…` | [message] wording of the open-policy sentence: the test asserts only the phrase 'ANY issuer is admitted', which the mutated tail keeps | killed by `test_the_library_re_derives_the_log_it_published` |
| 85 | `"Receipt from it says a statement was recorded, never that …` → `"RECEIPT FROM IT SAYS A STATEMENT WAS RECORDED, NEVER THAT …` | [message] wording of the open-policy sentence: the test asserts only the phrase 'ANY issuer is admitted', which the mutated tail keeps | killed by `test_the_library_re_derives_the_log_it_published` |
| 85 | `"Receipt from it says a statement was recorded, never that …` → `"receipt from it says a statement was recorded, never that …` | [message] wording of the open-policy sentence: the test asserts only the phrase 'ANY issuer is admitted', which the mutated tail keeps | killed by `test_the_library_re_derives_the_log_it_published` |
| 85 | `"Receipt from it says a statement was recorded, never that …` → `"XXReceipt from it says a statement was recorded, never tha…` | [message] wording of the open-policy sentence: the test asserts only the phrase 'ANY issuer is admitted', which the mutated tail keeps | killed by `test_the_library_re_derives_the_log_it_published` |
| 86 | `"was vetted"` → `"WAS VETTED"` | [message] wording of the open-policy sentence: the test asserts only the phrase 'ANY issuer is admitted', which the mutated tail keeps | killed by `test_the_library_re_derives_the_log_it_published` |
| 86 | `"was vetted"` → `"XXwas vettedXX"` | [message] wording of the open-policy sentence: the test asserts only the phrase 'ANY issuer is admitted', which the mutated tail keeps | killed by `test_the_library_re_derives_the_log_it_published` |

**`inspeximus/transparency.py` `RegistrationPolicy.canonical`** (9)

| line | mutation | why no test caught it | now |
|---:|---|---|---|
| 93 | `json.dumps(self.as_dict(), sort_keys=True, separators=(",",…` → `json.dumps(self.as_dict(), sort_keys=True, separators=None)` | [format] canonical encoding of the policy digest: every test compares policy_sha256 with a digest the same function computes, so a changed encoding is consistent everywhere and invisible -- until a digest already published stops matching | killed by `test_the_library_re_derives_the_log_it_published` |
| 93 | `json.dumps(self.as_dict(), sort_keys=True, separators=(",",…` → `json.dumps(self.as_dict(), sort_keys=None, separators=(",",…` | [format] canonical encoding of the policy digest: every test compares policy_sha256 with a digest the same function computes, so a changed encoding is consistent everywhere and invisible -- until a digest already published stops matching | killed by `test_the_library_re_derives_the_log_it_published` |
| 93 | `json.dumps(self.as_dict(), sort_keys=True, separators=(",",…` → `json.dumps(None, sort_keys=True, separators=(",", ":"))` | [format] canonical encoding of the policy digest: every test compares policy_sha256 with a digest the same function computes, so a changed encoding is consistent everywhere and invisible -- until a digest already published stops matching | killed by `test_a_registration_after_a_policy_change_records_the_new_policy` |
| 93 | `json.dumps(self.as_dict(), sort_keys=True, separators=(",",…` → `json.dumps(self.as_dict(), sort_keys=True, )` | [format] canonical encoding of the policy digest: every test compares policy_sha256 with a digest the same function computes, so a changed encoding is consistent everywhere and invisible -- until a digest already published stops matching | killed by `test_the_library_re_derives_the_log_it_published` |
| 93 | `json.dumps(self.as_dict(), sort_keys=True, separators=(",",…` → `json.dumps(self.as_dict(), separators=(",", ":"))` | [format] canonical encoding of the policy digest: every test compares policy_sha256 with a digest the same function computes, so a changed encoding is consistent everywhere and invisible -- until a digest already published stops matching | killed by `test_the_library_re_derives_the_log_it_published` |
| 93 | `True` → `False` | [format] canonical encoding of the policy digest: every test compares policy_sha256 with a digest the same function computes, so a changed encoding is consistent everywhere and invisible -- until a digest already published stops matching | killed by `test_the_library_re_derives_the_log_it_published` |
| 93 | `","` → `"XX,XX"` | [format] canonical encoding of the policy digest: every test compares policy_sha256 with a digest the same function computes, so a changed encoding is consistent everywhere and invisible -- until a digest already published stops matching | killed by `test_the_library_re_derives_the_log_it_published` |
| 93 | `":"` → `"XX:XX"` | [format] canonical encoding of the policy digest: every test compares policy_sha256 with a digest the same function computes, so a changed encoding is consistent everywhere and invisible -- until a digest already published stops matching | killed by `test_the_library_re_derives_the_log_it_published` |
| 93 | `"utf-8"` → `"UTF-8"` | [equivalent] equivalent: codec names are case-insensitive, 'UTF-8' is 'utf-8' |  |

**`inspeximus/transparency.py` `RegistrationPolicy.check`** (16)

| line | mutation | why no test caught it | now |
|---:|---|---|---|
| 102 | `why.append("the statement's signature does not verify")` → `why.append(None)` | [unexecuted] no test sends a statement with a failed signature, no issuer or no subject, so these three refusals never ran; `why.append(None)` also turns the RegistrationRefused message into a TypeError | killed by `test_each_missing_claim_is_its_own_refusal` |
| 102 | `"the statement's signature does not verify"` → `"XXthe statement's signature does not verifyXX"` | [unexecuted] no test sends a statement with a failed signature, no issuer or no subject, so these three refusals never ran; `why.append(None)` also turns the RegistrationRefused message into a TypeError | killed by `test_each_missing_claim_is_its_own_refusal` |
| 102 | `"the statement's signature does not verify"` → `"THE STATEMENT'S SIGNATURE DOES NOT VERIFY"` | [unexecuted] no test sends a statement with a failed signature, no issuer or no subject, so these three refusals never ran; `why.append(None)` also turns the RegistrationRefused message into a TypeError | killed by `test_each_missing_claim_is_its_own_refusal` |
| 105 | `why.append("no Issuer claim")` → `why.append(None)` | [unexecuted] no test sends a statement with a failed signature, no issuer or no subject, so these three refusals never ran; `why.append(None)` also turns the RegistrationRefused message into a TypeError | killed by `test_each_missing_claim_is_its_own_refusal` |
| 105 | `"no Issuer claim"` → `"no issuer claim"` | [unexecuted] no test sends a statement with a failed signature, no issuer or no subject, so these three refusals never ran; `why.append(None)` also turns the RegistrationRefused message into a TypeError | killed by `test_each_missing_claim_is_its_own_refusal` |
| 105 | `"no Issuer claim"` → `"XXno Issuer claimXX"` | [unexecuted] no test sends a statement with a failed signature, no issuer or no subject, so these three refusals never ran; `why.append(None)` also turns the RegistrationRefused message into a TypeError | killed by `test_each_missing_claim_is_its_own_refusal` |
| 105 | `"no Issuer claim"` → `"NO ISSUER CLAIM"` | [unexecuted] no test sends a statement with a failed signature, no issuer or no subject, so these three refusals never ran; `why.append(None)` also turns the RegistrationRefused message into a TypeError | killed by `test_each_missing_claim_is_its_own_refusal` |
| 107 | `"issuer %r is not a trust anchor of this service"` → `"XXissuer %r is not a trust anchor of this serviceXX"` | [message] wording: the tests look for a phrase inside the message ('trust anchor', 'does not start with', 'over the 8'), which the XX-wrapped text still contains |  |
| 110 | `why.append("no Subject claim")` → `why.append(None)` | [unexecuted] no test sends a statement with a failed signature, no issuer or no subject, so these three refusals never ran; `why.append(None)` also turns the RegistrationRefused message into a TypeError | killed by `test_each_missing_claim_is_its_own_refusal` |
| 110 | `"no Subject claim"` → `"no subject claim"` | [unexecuted] no test sends a statement with a failed signature, no issuer or no subject, so these three refusals never ran; `why.append(None)` also turns the RegistrationRefused message into a TypeError | killed by `test_each_missing_claim_is_its_own_refusal` |
| 110 | `"no Subject claim"` → `"NO SUBJECT CLAIM"` | [unexecuted] no test sends a statement with a failed signature, no issuer or no subject, so these three refusals never ran; `why.append(None)` also turns the RegistrationRefused message into a TypeError | killed by `test_each_missing_claim_is_its_own_refusal` |
| 110 | `"no Subject claim"` → `"XXno Subject claimXX"` | [unexecuted] no test sends a statement with a failed signature, no issuer or no subject, so these three refusals never ran; `why.append(None)` also turns the RegistrationRefused message into a TypeError | killed by `test_each_missing_claim_is_its_own_refusal` |
| 111 | `str(subject)` → `str(None)` | [condition] the prefix rule was only tested with a subject that FAILS it; 'None' fails every prefix too, so a rule that refuses everything passed | killed by `test_a_registration_after_a_policy_change_records_the_new_policy` |
| 112 | `"subject %r does not start with %r"` → `"XXsubject %r does not start with %rXX"` | [message] wording: the tests look for a phrase inside the message ('trust anchor', 'does not start with', 'over the 8'), which the XX-wrapped text still contains |  |
| 113 | `> self.max_payload_bytes` → `>= self.max_payload_bytes` | [boundary] no payload exactly at the ceiling: `>` and `>=` differ only there | killed by `test_the_payload_ceiling_admits_exactly_its_own_size` |
| 114 | `"payload is %d bytes, over the %d this policy admits"` → `"XXpayload is %d bytes, over the %d this policy admitsXX"` | [message] wording: the tests look for a phrase inside the message ('trust anchor', 'does not start with', 'over the 8'), which the XX-wrapped text still contains |  |

**`inspeximus/transparency.py` `TransparencyService.__init__`** (15)

| line | mutation | why no test caught it | now |
|---:|---|---|---|
| 129 | `not callable(sign) or not callable(verify_issuer)` → `not callable(sign) and not callable(verify_issuer)` | [condition] no test constructs a service missing only ONE of signer / issuer verifier | killed by `test_a_service_without_a_signer_or_without_a_verifier_is_refused` |
| 130 | `TypeError("a Transparency Service needs a signer and an iss…` → `TypeError(None)` | [unexecuted] no test constructs a service without a signer or verifier, so the TypeError never ran | killed by `test_a_service_without_a_signer_or_without_a_verifier_is_refused` |
| 130 | `"a Transparency Service needs a signer and an issuer verifi…` → `"a transparency service needs a signer and an issuer verifi…` | [unexecuted] no test constructs a service without a signer or verifier, so the TypeError never ran |  |
| 130 | `"a Transparency Service needs a signer and an issuer verifi…` → `"XXa Transparency Service needs a signer and an issuer veri…` | [unexecuted] no test constructs a service without a signer or verifier, so the TypeError never ran |  |
| 130 | `"a Transparency Service needs a signer and an issuer verifi…` → `"A TRANSPARENCY SERVICE NEEDS A SIGNER AND AN ISSUER VERIFI…` | [unexecuted] no test constructs a service without a signer or verifier, so the TypeError never ran | killed by `test_a_service_without_a_signer_or_without_a_verifier_is_refused` |
| 135 | `16` → `17` | [identity] store_id is only compared with itself (head vs describe vs the witness that read it from head), so any deterministic value passes -- including str(None), which gives every log in the world the same identity | killed by `test_two_logs_at_two_paths_have_two_identities` |
| 135 | `"scitt:"` → `"SCITT:"` | [identity] store_id is only compared with itself (head vs describe vs the witness that read it from head), so any deterministic value passes -- including str(None), which gives every log in the world the same identity | killed by `test_two_logs_at_two_paths_have_two_identities` |
| 135 | `"scitt:"` → `"XXscitt:XX"` | [identity] store_id is only compared with itself (head vs describe vs the witness that read it from head), so any deterministic value passes -- including str(None), which gives every log in the world the same identity | killed by `test_two_logs_at_two_paths_have_two_identities` |
| 135 | `str(path)` → `str(None)` | [identity] store_id is only compared with itself (head vs describe vs the witness that read it from head), so any deterministic value passes -- including str(None), which gives every log in the world the same identity | killed by `test_two_logs_at_two_paths_have_two_identities` |
| 135 | `"utf-8"` → `"UTF-8"` | [equivalent] equivalent: codec names are case-insensitive, 'UTF-8' is 'utf-8' |  |
| 142 | `open(self.path, encoding="utf-8")` → `open(self.path, )` | [platform] equivalent on this platform: the locale encoding here is UTF-8, and every line is ASCII anyway (ensure_ascii=True); would matter only for a non-ASCII log on a non-UTF-8 locale |  |
| 142 | `open(self.path, encoding="utf-8")` → `open(self.path, encoding=None)` | [platform] equivalent on this platform: the locale encoding here is UTF-8, and every line is ASCII anyway (ensure_ascii=True); would matter only for a non-ASCII log on a non-UTF-8 locale |  |
| 142 | `"utf-8"` → `"UTF-8"` | [equivalent] equivalent: codec names are case-insensitive, 'UTF-8' is 'utf-8' |  |
| 145 | `"ts"` → `"TS"` | [unread-output] the entry's `ts` key is committed into the leaf but no test reads it back |  |
| 145 | `"ts"` → `"XXtsXX"` | [unread-output] the entry's `ts` key is committed into the leaf but no test reads it back |  |

**`inspeximus/transparency.py` `TransparencyService._append`** (13)

| line | mutation | why no test caught it | now |
|---:|---|---|---|
| 152 | `json.dumps(entry, sort_keys=True, separators=(",", ":"), en…` → `json.dumps(entry, sort_keys=True, separators=(",", ":"), )` | [equivalent] equivalent: drops `ensure_ascii=True`, which is json.dumps's default |  |
| 152 | `json.dumps(entry, sort_keys=True, separators=(",", ":"), en…` → `json.dumps(entry, sort_keys=True, ensure_ascii=True)` | [format] layout of the line on disk only: every reader parses the line and `_leaves()` re-encodes it canonically, so key order, spacing and escaping on disk never reach a leaf (the new published-log test stores the lines re-ordered and spaced to pin exactly that) |  |
| 152 | `json.dumps(entry, sort_keys=True, separators=(",", ":"), en…` → `json.dumps(entry, sort_keys=True, separators=None, ensure_a…` | [format] layout of the line on disk only: every reader parses the line and `_leaves()` re-encodes it canonically, so key order, spacing and escaping on disk never reach a leaf (the new published-log test stores the lines re-ordered and spaced to pin exactly that) |  |
| 152 | `json.dumps(entry, sort_keys=True, separators=(",", ":"), en…` → `json.dumps(entry, sort_keys=True, separators=(",", ":"), en…` | [format] layout of the line on disk only: every reader parses the line and `_leaves()` re-encodes it canonically, so key order, spacing and escaping on disk never reach a leaf (the new published-log test stores the lines re-ordered and spaced to pin exactly that) |  |
| 152 | `json.dumps(entry, sort_keys=True, separators=(",", ":"), en…` → `json.dumps(entry, separators=(",", ":"), ensure_ascii=True)` | [format] layout of the line on disk only: every reader parses the line and `_leaves()` re-encodes it canonically, so key order, spacing and escaping on disk never reach a leaf (the new published-log test stores the lines re-ordered and spaced to pin exactly that) |  |
| 152 | `json.dumps(entry, sort_keys=True, separators=(",", ":"), en…` → `json.dumps(entry, sort_keys=None, separators=(",", ":"), en…` | [format] layout of the line on disk only: every reader parses the line and `_leaves()` re-encodes it canonically, so key order, spacing and escaping on disk never reach a leaf (the new published-log test stores the lines re-ordered and spaced to pin exactly that) |  |
| 152 | `True` → `False` | [format] layout of the line on disk only: every reader parses the line and `_leaves()` re-encodes it canonically, so key order, spacing and escaping on disk never reach a leaf (the new published-log test stores the lines re-ordered and spaced to pin exactly that) |  |
| 152 | `True` → `False` | [format] layout of the line on disk only: every reader parses the line and `_leaves()` re-encodes it canonically, so key order, spacing and escaping on disk never reach a leaf (the new published-log test stores the lines re-ordered and spaced to pin exactly that) |  |
| 153 | `open(self.path, "a", encoding="utf-8", newline="\n")` → `open(self.path, "a", encoding="utf-8", )` | [platform] equivalent on POSIX: newline=None writes '\n' here; on Windows it would write '\r\n', which the line reader strips, so the parsed entries (and the leaves, re-encoded from them) are the same |  |
| 153 | `open(self.path, "a", encoding="utf-8", newline="\n")` → `open(self.path, "a", encoding="utf-8", newline=None)` | [platform] equivalent on POSIX: newline=None writes '\n' here; on Windows it would write '\r\n', which the line reader strips, so the parsed entries (and the leaves, re-encoded from them) are the same |  |
| 153 | `open(self.path, "a", encoding="utf-8", newline="\n")` → `open(self.path, "a", newline="\n")` | [platform] equivalent on this platform: the locale encoding here is UTF-8, and every line is ASCII anyway (ensure_ascii=True); would matter only for a non-ASCII log on a non-UTF-8 locale |  |
| 153 | `open(self.path, "a", encoding="utf-8", newline="\n")` → `open(self.path, "a", encoding=None, newline="\n")` | [platform] equivalent on this platform: the locale encoding here is UTF-8, and every line is ASCII anyway (ensure_ascii=True); would matter only for a non-ASCII log on a non-UTF-8 locale |  |
| 153 | `"utf-8"` → `"UTF-8"` | [equivalent] equivalent: codec names are case-insensitive, 'UTF-8' is 'utf-8' |  |

**`inspeximus/transparency.py` `TransparencyService._leaves`** (7)

| line | mutation | why no test caught it | now |
|---:|---|---|---|
| 161 | `json.dumps(e, sort_keys=True, separators=(",", ":"), ensure…` → `json.dumps(e, sort_keys=True, separators=(",", ":"), )` | [equivalent] equivalent: drops `ensure_ascii=True`, which is json.dumps's default |  |
| 161 | `json.dumps(e, sort_keys=True, separators=(",", ":"), ensure…` → `json.dumps(e, separators=(",", ":"), ensure_ascii=True)` | [format] leaf encoding: producer and verifier both call _leaves(), so the tree is rebuilt consistently and every Receipt already issued silently stops matching; no test compared a leaf with bytes written by an earlier run | killed by `test_the_library_re_derives_the_log_it_published` |
| 161 | `json.dumps(e, sort_keys=True, separators=(",", ":"), ensure…` → `json.dumps(e, sort_keys=True, separators=(",", ":"), ensure…` | [format] leaf encoding: producer and verifier both call _leaves(), so the tree is rebuilt consistently and every Receipt already issued silently stops matching; no test compared a leaf with bytes written by an earlier run | killed by `test_a_leaf_is_ascii_whatever_the_statement_says` |
| 161 | `json.dumps(e, sort_keys=True, separators=(",", ":"), ensure…` → `json.dumps(e, sort_keys=None, separators=(",", ":"), ensure…` | [format] leaf encoding: producer and verifier both call _leaves(), so the tree is rebuilt consistently and every Receipt already issued silently stops matching; no test compared a leaf with bytes written by an earlier run | killed by `test_the_library_re_derives_the_log_it_published` |
| 161 | `True` → `False` | [format] leaf encoding: producer and verifier both call _leaves(), so the tree is rebuilt consistently and every Receipt already issued silently stops matching; no test compared a leaf with bytes written by an earlier run | killed by `test_the_library_re_derives_the_log_it_published` |
| 161 | `True` → `False` | [format] leaf encoding: producer and verifier both call _leaves(), so the tree is rebuilt consistently and every Receipt already issued silently stops matching; no test compared a leaf with bytes written by an earlier run | killed by `test_a_leaf_is_ascii_whatever_the_statement_says` |
| 161 | `"utf-8"` → `"UTF-8"` | [equivalent] equivalent: codec names are case-insensitive, 'UTF-8' is 'utf-8' |  |

**`inspeximus/transparency.py` `TransparencyService.policy_in_force`** (4)

| line | mutation | why no test caught it | now |
|---:|---|---|---|
| 179 | `RuntimeError("this log has no policy entry, so it is not a …` → `RuntimeError(None)` | [unexecuted] unreachable through the public API: the constructor always writes entry 0 as a policy, so policy_in_force() never finds none |  |
| 179 | `"this log has no policy entry, so it is not a Transparency …` → `"this log has no policy entry, so it is not a transparency …` | [unexecuted] unreachable through the public API: the constructor always writes entry 0 as a policy, so policy_in_force() never finds none |  |
| 179 | `"this log has no policy entry, so it is not a Transparency …` → `"XXthis log has no policy entry, so it is not a Transparenc…` | [unexecuted] unreachable through the public API: the constructor always writes entry 0 as a policy, so policy_in_force() never finds none |  |
| 179 | `"this log has no policy entry, so it is not a Transparency …` → `"THIS LOG HAS NO POLICY ENTRY, SO IT IS NOT A TRANSPARENCY …` | [unexecuted] unreachable through the public API: the constructor always writes entry 0 as a policy, so policy_in_force() never finds none |  |

**`inspeximus/transparency.py` `TransparencyService.set_policy`** (5)

| line | mutation | why no test caught it | now |
|---:|---|---|---|
| 184 | `self.policy = policy` → `self.policy = None` | [unread-output] `self.policy` is not read after construction -- registration reads the policy from the log -- and no test asserted it | killed by `test_a_registration_after_a_policy_change_records_the_new_policy` |
| 185 | `"ts"` → `"XXtsXX"` | [condition] the only test that changed the policy then had a registration REFUSED, which stops before the new entry's policy_sha256 is read; a successful registration under a changed policy was never tried | killed by `test_a_registration_after_a_policy_change_records_the_new_policy` |
| 185 | `"ts"` → `"TS"` | [condition] the only test that changed the policy then had a registration REFUSED, which stops before the new entry's policy_sha256 is read; a successful registration under a changed policy was never tried | killed by `test_a_registration_after_a_policy_change_records_the_new_policy` |
| 186 | `"policy_sha256"` → `"POLICY_SHA256"` | [condition] the only test that changed the policy then had a registration REFUSED, which stops before the new entry's policy_sha256 is read; a successful registration under a changed policy was never tried | killed by `test_a_registration_after_a_policy_change_records_the_new_policy` |
| 186 | `"policy_sha256"` → `"XXpolicy_sha256XX"` | [condition] the only test that changed the policy then had a registration REFUSED, which stops before the new entry's policy_sha256 is read; a successful registration under a changed policy was never tried | killed by `test_a_registration_after_a_policy_change_records_the_new_policy` |

**`inspeximus/transparency.py` `TransparencyService.register`** (5)

| line | mutation | why no test caught it | now |
|---:|---|---|---|
| 209 | `"; "` → `"XX; XX"` | [message] wording: the tests look for a phrase inside the message ('trust anchor', 'does not start with', 'over the 8'), which the XX-wrapped text still contains |  |
| 212 | `"signed-statement"` → `"XXsigned-statementXX"` | [unread-output] the registration entry's `kind` label is never read: the verifier recognises a registration by `statement_sha256`, not by `kind` |  |
| 212 | `"signed-statement"` → `"SIGNED-STATEMENT"` | [unread-output] the registration entry's `kind` label is never read: the verifier recognises a registration by `statement_sha256`, not by `kind` |  |
| 212 | `"ts"` → `"TS"` | [unread-output] the entry's `ts` key is committed into the leaf but no test reads it back |  |
| 212 | `"ts"` → `"XXtsXX"` | [unread-output] the entry's `ts` key is committed into the leaf but no test reads it back |  |

**`inspeximus/transparency.py` `TransparencyService.receipt_for`** (1)

| line | mutation | why no test caught it | now |
|---:|---|---|---|
| 233 | `< len(self._entries)` → `<= len(self._entries)` | [boundary] no test asked for the receipt of index == size (the next one, the one a poller asks for) | killed by `test_a_receipt_for_the_next_index_is_none_not_an_error` |

**`inspeximus/transparency.py` `TransparencyService.head`** (10)

| line | mutation | why no test caught it | now |
|---:|---|---|---|
| 258 | `0` → `1` | [unread-output] the head's fixed fields are only ever hashed and compared with the same head; nothing asserts what they say | killed by `test_the_head_says_a_transparency_log_has_no_tombstones` |
| 258 | `"tombstones_tip"` → `"TOMBSTONES_TIP"` | [unread-output] the head's fixed fields are only ever hashed and compared with the same head; nothing asserts what they say | killed by `test_the_head_says_a_transparency_log_has_no_tombstones` |
| 258 | `"tombstones_tip"` → `"XXtombstones_tipXX"` | [unread-output] the head's fixed fields are only ever hashed and compared with the same head; nothing asserts what they say | killed by `test_the_head_says_a_transparency_log_has_no_tombstones` |
| 258 | `""` → `"XXXX"` | [unread-output] the head's fixed fields are only ever hashed and compared with the same head; nothing asserts what they say | killed by `test_the_head_says_a_transparency_log_has_no_tombstones` |
| 259 | `"store_id"` → `"XXstore_idXX"` | [unread-output] the head's fixed fields are only ever hashed and compared with the same head; nothing asserts what they say | killed by `test_two_logs_at_two_paths_have_two_identities` |
| 259 | `"store_id"` → `"STORE_ID"` | [unread-output] the head's fixed fields are only ever hashed and compared with the same head; nothing asserts what they say | killed by `test_two_logs_at_two_paths_have_two_identities` |
| 259 | `"kind"` → `"XXkindXX"` | [unread-output] the head's fixed fields are only ever hashed and compared with the same head; nothing asserts what they say | killed by `test_the_head_says_a_transparency_log_has_no_tombstones` |
| 259 | `"kind"` → `"KIND"` | [unread-output] the head's fixed fields are only ever hashed and compared with the same head; nothing asserts what they say | killed by `test_the_head_says_a_transparency_log_has_no_tombstones` |
| 259 | `"scitt-transparency-log"` → `"SCITT-TRANSPARENCY-LOG"` | [unread-output] the head's fixed fields are only ever hashed and compared with the same head; nothing asserts what they say | killed by `test_the_head_says_a_transparency_log_has_no_tombstones` |
| 259 | `"scitt-transparency-log"` → `"XXscitt-transparency-logXX"` | [unread-output] the head's fixed fields are only ever hashed and compared with the same head; nothing asserts what they say | killed by `test_the_head_says_a_transparency_log_has_no_tombstones` |

**`inspeximus/transparency.py` `TransparencyService.witnessed_head`** (3)

| line | mutation | why no test caught it | now |
|---:|---|---|---|
| 263 | `1` → `2` | [boundary] the default threshold is never exercised: every witnessed_head test passes `threshold=` explicitly or uses enough witnesses for 2 |  |
| 284 | `"threshold"` → `"XXthresholdXX"` | [unread-output] `threshold` in the witnessed_head result is never read back |  |
| 284 | `"threshold"` → `"THRESHOLD"` | [unread-output] `threshold` in the witnessed_head result is never read back |  |

**`inspeximus/transparency.py` `TransparencyService.describe`** (18)

| line | mutation | why no test caught it | now |
|---:|---|---|---|
| 293 | `"scitt_transparency_service"` → `"XXscitt_transparency_serviceXX"` | [unread-output] describe() keys no test reads (the test checks the scope sentence, the policy digest and that the public key appears somewhere in the JSON) |  |
| 293 | `"scitt_transparency_service"` → `"SCITT_TRANSPARENCY_SERVICE"` | [unread-output] describe() keys no test reads (the test checks the scope sentence, the policy digest and that the public key appears somewhere in the JSON) |  |
| 293 | `"1.0"` → `"XX1.0XX"` | [unread-output] describe() keys no test reads (the test checks the scope sentence, the policy digest and that the public key appears somewhere in the JSON) |  |
| 294 | `"service_pubkey"` → `"SERVICE_PUBKEY"` | [unread-output] describe() keys no test reads (the test checks the scope sentence, the policy digest and that the public key appears somewhere in the JSON) |  |
| 294 | `"service_pubkey"` → `"XXservice_pubkeyXX"` | [unread-output] describe() keys no test reads (the test checks the scope sentence, the policy digest and that the public key appears somewhere in the JSON) |  |
| 295 | `"store_id"` → `"XXstore_idXX"` | [unread-output] describe()['store_id'] was never read (the new identity test reads it) | killed by `test_two_logs_at_two_paths_have_two_identities` |
| 295 | `"store_id"` → `"STORE_ID"` | [unread-output] describe()['store_id'] was never read (the new identity test reads it) | killed by `test_two_logs_at_two_paths_have_two_identities` |
| 297 | `"policy_at_seq"` → `"POLICY_AT_SEQ"` | [unread-output] describe() keys no test reads (the test checks the scope sentence, the policy digest and that the public key appears somewhere in the JSON) |  |
| 297 | `"policy_at_seq"` → `"XXpolicy_at_seqXX"` | [unread-output] describe() keys no test reads (the test checks the scope sentence, the policy digest and that the public key appears somewhere in the JSON) |  |
| 298 | `"One operator holds this log. Inclusion and consistency are…` → `"XXOne operator holds this log. Inclusion and consistency a…` | [message] wording of describe()'s scope: the test asserts the phrase 'NON-EQUIVOCATION is not', which survives these edits |  |
| 298 | `"One operator holds this log. Inclusion and consistency are…` → `"ONE OPERATOR HOLDS THIS LOG. INCLUSION AND CONSISTENCY ARE…` | [message] wording of describe()'s scope: the test asserts the phrase 'NON-EQUIVOCATION is not', which survives these edits |  |
| 298 | `"One operator holds this log. Inclusion and consistency are…` → `"one operator holds this log. inclusion and consistency are…` | [message] wording of describe()'s scope: the test asserts the phrase 'NON-EQUIVOCATION is not', which survives these edits |  |
| 299 | `"these bytes; NON-EQUIVOCATION is not, because a single ope…` → `"XXthese bytes; NON-EQUIVOCATION is not, because a single o…` | [message] wording of describe()'s scope: the test asserts the phrase 'NON-EQUIVOCATION is not', which survives these edits |  |
| 300 | `"its own append-only claim proves nothing. Have witnesses c…` → `"ITS OWN APPEND-ONLY CLAIM PROVES NOTHING. HAVE WITNESSES C…` | [message] wording of describe()'s scope: the test asserts the phrase 'NON-EQUIVOCATION is not', which survives these edits |  |
| 300 | `"its own append-only claim proves nothing. Have witnesses c…` → `"XXits own append-only claim proves nothing. Have witnesses…` | [message] wording of describe()'s scope: the test asserts the phrase 'NON-EQUIVOCATION is not', which survives these edits |  |
| 300 | `"its own append-only claim proves nothing. Have witnesses c…` → `"its own append-only claim proves nothing. have witnesses c…` | [message] wording of describe()'s scope: the test asserts the phrase 'NON-EQUIVOCATION is not', which survives these edits |  |
| 301 | `"(see witness_pool) if that property is needed."` → `"XX(see witness_pool) if that property is needed.XX"` | [message] wording of describe()'s scope: the test asserts the phrase 'NON-EQUIVOCATION is not', which survives these edits |  |
| 301 | `"(see witness_pool) if that property is needed."` → `"(SEE WITNESS_POOL) IF THAT PROPERTY IS NEEDED."` | [message] wording of describe()'s scope: the test asserts the phrase 'NON-EQUIVOCATION is not', which survives these edits |  |

**`inspeximus/transparency.py` `verify_registered_statement`** (52)

| line | mutation | why no test caught it | now |
|---:|---|---|---|
| 325 | `"statement"` → `"XXstatementXX"` | [unread-output] the initial placeholder keys are overwritten on the full path; on the two early returns (no Receipt, unreadable leaf) a caller reading `bound` / `entry` / `receipt` would get KeyError under the mutant, and no test reads those keys there |  |
| 325 | `"statement"` → `"STATEMENT"` | [unread-output] the initial placeholder keys are overwritten on the full path; on the two early returns (no Receipt, unreadable leaf) a caller reading `bound` / `entry` / `receipt` would get KeyError under the mutant, and no test reads those keys there |  |
| 325 | `"receipt"` → `"XXreceiptXX"` | [unread-output] the initial placeholder keys are overwritten on the full path; on the two early returns (no Receipt, unreadable leaf) a caller reading `bound` / `entry` / `receipt` would get KeyError under the mutant, and no test reads those keys there |  |
| 325 | `"receipt"` → `"RECEIPT"` | [unread-output] the initial placeholder keys are overwritten on the full path; on the two early returns (no Receipt, unreadable leaf) a caller reading `bound` / `entry` / `receipt` would get KeyError under the mutant, and no test reads those keys there |  |
| 325 | `"bound"` → `"BOUND"` | [unread-output] the initial placeholder keys are overwritten on the full path; on the two early returns (no Receipt, unreadable leaf) a caller reading `bound` / `entry` / `receipt` would get KeyError under the mutant, and no test reads those keys there |  |
| 325 | `"bound"` → `"XXboundXX"` | [unread-output] the initial placeholder keys are overwritten on the full path; on the two early returns (no Receipt, unreadable leaf) a caller reading `bound` / `entry` / `receipt` would get KeyError under the mutant, and no test reads those keys there |  |
| 325 | `"entry"` → `"XXentryXX"` | [unread-output] the initial placeholder keys are overwritten on the full path; on the two early returns (no Receipt, unreadable leaf) a caller reading `bound` / `entry` / `receipt` would get KeyError under the mutant, and no test reads those keys there |  |
| 325 | `"entry"` → `"ENTRY"` | [unread-output] the initial placeholder keys are overwritten on the full path; on the two early returns (no Receipt, unreadable leaf) a caller reading `bound` / `entry` / `receipt` would get KeyError under the mutant, and no test reads those keys there |  |
| 327 | `scitt.verify_signed_statement(statement, verify_issuer, exp…` → `scitt.verify_signed_statement(statement, verify_issuer, )` | [soundness] the one test that passed expected_issuer passed the issuer the statement really has; a verifier that dropped the pin answered the same | killed by `test_a_pinned_issuer_is_enforced` |
| 327 | `scitt.verify_signed_statement(statement, verify_issuer, exp…` → `scitt.verify_signed_statement(statement, verify_issuer, exp…` | [soundness] the one test that passed expected_issuer passed the issuer the statement really has; a verifier that dropped the pin answered the same | killed by `test_a_pinned_issuer_is_enforced` |
| 328 | `out["statement"] = st` → `out["statement"] = None` | [unread-output] out['statement'] is read by no test on a path where it matters (the new verdict test reads it) | killed by `test_the_verdict_needs_the_receipt_and_the_statement_not_only_the_binding` |
| 328 | `"statement"` → `"XXstatementXX"` | [unread-output] out['statement'] is read by no test on a path where it matters (the new verdict test reads it) | killed by `test_the_verdict_needs_the_receipt_and_the_statement_not_only_the_binding` |
| 328 | `"statement"` → `"STATEMENT"` | [unread-output] out['statement'] is read by no test on a path where it matters (the new verdict test reads it) | killed by `test_the_verdict_needs_the_receipt_and_the_statement_not_only_the_binding` |
| 329 | `"statement: "` → `"XXstatement: XX"` | [message] prefix of each problem line ('statement: ', 'receipt: '); no test reads it. The `+` -> `-` variant raises TypeError only when there IS a statement problem, which no test produced |  |
| 329 | `"statement: "` → `"STATEMENT: "` | [message] prefix of each problem line ('statement: ', 'receipt: '); no test reads it. The `+` -> `-` variant raises TypeError only when there IS a statement problem, which no test produced |  |
| 329 | `"statement: " + p` → `"statement: " - p` | [message] prefix of each problem line ('statement: ', 'receipt: '); no test reads it. The `+` -> `-` variant raises TypeError only when there IS a statement problem, which no test produced | killed by `test_the_verdict_needs_the_receipt_and_the_statement_not_only_the_binding` |
| 329 | `out["problems"] += ["statement: " + p for p in st["problems…` → `out["problems"] = ["statement: " + p for p in st["problems"…` | [message] problems from one stage overwrite the other's; the verdict comes from the flags, and no test counts or inspects the combined problem list |  |
| 333 | `"no Receipt: this statement was never registered, or the Re…` → `"no receipt: this statement was never registered, or the re…` | [message] wording: the tests check for 'never registered' / 'stripped' / 'different registration', which the mutated text still contains or which the other alternative satisfies |  |
| 333 | `"no Receipt: this statement was never registered, or the Re…` → `"NO RECEIPT: THIS STATEMENT WAS NEVER REGISTERED, OR THE RE…` | [message] wording: the tests check for 'never registered' / 'stripped' / 'different registration', which the mutated text still contains or which the other alternative satisfies |  |
| 333 | `"no Receipt: this statement was never registered, or the Re…` → `"XXno Receipt: this statement was never registered, or the …` | [message] wording: the tests check for 'never registered' / 'stripped' / 'different registration', which the mutated text still contains or which the other alternative satisfies |  |
| 334 | `"stripped"` → `"XXstrippedXX"` | [message] wording: the tests check for 'never registered' / 'stripped' / 'different registration', which the mutated text still contains or which the other alternative satisfies |  |
| 334 | `"stripped"` → `"STRIPPED"` | [message] wording: the tests check for 'never registered' / 'stripped' / 'different registration', which the mutated text still contains or which the other alternative satisfies |  |
| 336 | `bytes(expected_root)` → `None` | [soundness] every call passed the log's own current root as expected_root, which is also the root inside the receipt, so a verifier that ignored the argument answered the same way | killed by `test_a_receipt_is_checked_against_the_root_the_caller_trusts` |
| 336 | `expected_root=bytes(expected_root)` → `` | [soundness] every call passed the log's own current root as expected_root, which is also the root inside the receipt, so a verifier that ignored the argument answered the same way | killed by `test_a_receipt_is_checked_against_the_root_the_caller_trusts` |
| 339 | `"receipt: "` → `"XXreceipt: XX"` | [message] prefix of each problem line ('statement: ', 'receipt: '); no test reads it. The `+` -> `-` variant raises TypeError only when there IS a statement problem, which no test produced |  |
| 339 | `"receipt: "` → `"RECEIPT: "` | [message] prefix of each problem line ('statement: ', 'receipt: '); no test reads it. The `+` -> `-` variant raises TypeError only when there IS a statement problem, which no test produced |  |
| 339 | `out["problems"] += ["receipt: " + p for p in rc["problems"]]` → `out["problems"] = ["receipt: " + p for p in rc["problems"]]` | [message] problems from one stage overwrite the other's; the verdict comes from the flags, and no test counts or inspects the combined problem list |  |
| 342 | `"utf-8"` → `"UTF-8"` | [equivalent] equivalent: codec names are case-insensitive, 'UTF-8' is 'utf-8' |  |
| 344 | `"problems"` → `"PROBLEMS"` | [unexecuted] no test hands the verifier a leaf that is not JSON, so this branch never ran |  |
| 344 | `"problems"` → `"XXproblemsXX"` | [unexecuted] no test hands the verifier a leaf that is not JSON, so this branch never ran |  |
| 344 | `"the entry is not readable JSON (%s)"` → `"the entry is not readable json (%s)"` | [unexecuted] no test hands the verifier a leaf that is not JSON, so this branch never ran |  |
| 344 | `"the entry is not readable JSON (%s)"` → `"XXthe entry is not readable JSON (%s)XX"` | [unexecuted] no test hands the verifier a leaf that is not JSON, so this branch never ran |  |
| 344 | `"the entry is not readable JSON (%s)" % type(e).__name__` → `"the entry is not readable JSON (%s)" / type(e).__name__` | [unexecuted] no test hands the verifier a leaf that is not JSON, so this branch never ran |  |
| 344 | `"the entry is not readable JSON (%s)"` → `"THE ENTRY IS NOT READABLE JSON (%S)"` | [unexecuted] no test hands the verifier a leaf that is not JSON, so this branch never ran |  |
| 344 | `type(e)` → `type(None)` | [unexecuted] no test hands the verifier a leaf that is not JSON, so this branch never ran |  |
| 344 | `out["problems"].append("the entry is not readable JSON (%s)…` → `out["problems"].append(None)` | [unexecuted] no test hands the verifier a leaf that is not JSON, so this branch never ran |  |
| 354 | `"this leaf is not a Transparency Service registration entry…` → `None` | [unexecuted] no test verifies a statement against a leaf that is not a registration entry (for instance the policy entry), so this message never ran |  |
| 354 | `"problems"` → `"XXproblemsXX"` | [unexecuted] no test verifies a statement against a leaf that is not a registration entry (for instance the policy entry), so this message never ran |  |
| 354 | `"problems"` → `"PROBLEMS"` | [unexecuted] no test verifies a statement against a leaf that is not a registration entry (for instance the policy entry), so this message never ran |  |
| 355 | `"this leaf is not a Transparency Service registration entry…` → `"XXthis leaf is not a Transparency Service registration ent…` | [unexecuted] no test verifies a statement against a leaf that is not a registration entry (for instance the policy entry), so this message never ran |  |
| 355 | `"this leaf is not a Transparency Service registration entry…` → `"THIS LEAF IS NOT A TRANSPARENCY SERVICE REGISTRATION ENTRY…` | [unexecuted] no test verifies a statement against a leaf that is not a registration entry (for instance the policy entry), so this message never ran |  |
| 355 | `"this leaf is not a Transparency Service registration entry…` → `"this leaf is not a transparency service registration entry…` | [unexecuted] no test verifies a statement against a leaf that is not a registration entry (for instance the policy entry), so this message never ran |  |
| 356 | `"binding does not apply. Use inspeximus.verify_transparent_…` → `"BINDING DOES NOT APPLY. USE INSPEXIMUS.VERIFY_TRANSPARENT_…` | [unexecuted] no test verifies a statement against a leaf that is not a registration entry (for instance the policy entry), so this message never ran |  |
| 356 | `"binding does not apply. Use inspeximus.verify_transparent_…` → `"XXbinding does not apply. Use inspeximus.verify_transparen…` | [unexecuted] no test verifies a statement against a leaf that is not a registration entry (for instance the policy entry), so this message never ran |  |
| 356 | `"binding does not apply. Use inspeximus.verify_transparent_…` → `"binding does not apply. use inspeximus.verify_transparent_…` | [unexecuted] no test verifies a statement against a leaf that is not a registration entry (for instance the policy entry), so this message never ran |  |
| 357 | `"issued by a store."` → `"XXissued by a store.XX"` | [unexecuted] no test verifies a statement against a leaf that is not a registration entry (for instance the policy entry), so this message never ran |  |
| 357 | `"issued by a store."` → `"ISSUED BY A STORE."` | [unexecuted] no test verifies a statement against a leaf that is not a registration entry (for instance the policy entry), so this message never ran |  |
| 360 | `"the log entry this Receipt proves does not name this state…` → `"XXthe log entry this Receipt proves does not name this sta…` | [message] wording: the tests check for 'never registered' / 'stripped' / 'different registration', which the mutated text still contains or which the other alternative satisfies |  |
| 360 | `"the log entry this Receipt proves does not name this state…` → `"the log entry this receipt proves does not name this state…` | [message] wording: the tests check for 'never registered' / 'stripped' / 'different registration', which the mutated text still contains or which the other alternative satisfies |  |
| 360 | `"the log entry this Receipt proves does not name this state…` → `"THE LOG ENTRY THIS RECEIPT PROVES DOES NOT NAME THIS STATE…` | [message] wording: the tests check for 'never registered' / 'stripped' / 'different registration', which the mutated text still contains or which the other alternative satisfies |  |
| 361 | `"about a different registration"` → `"XXabout a different registrationXX"` | [message] wording: the tests check for 'never registered' / 'stripped' / 'different registration', which the mutated text still contains or which the other alternative satisfies |  |
| 363 | `st["ok"] and rc["ok"] and out["bound"]` → `st["ok"] and rc["ok"] or out["bound"]` | [soundness] every negative test also broke the binding, so `bound` was False whenever the verdict had to be; a bound statement with a failing receipt or signature was never tried | killed by `test_a_receipt_is_checked_against_the_root_the_caller_trusts` |

