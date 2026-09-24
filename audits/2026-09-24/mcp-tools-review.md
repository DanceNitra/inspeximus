# Correctness review: the inspeximus MCP server's tools

**Question:** does each tool the MCP server exposes do what its own description says? Per tool: the return
shape, what it writes, whether the guards that apply in the library also apply when the same operation goes
through the tool (echo guard, objectless guard, write receipts, read guards and quarantine, Art. 21
objections, project scope, key pinning), and whether errors are reported rather than swallowed.

**Scope:** `inspeximus/mcp_server.py` on `main` at `cff4e29` (inspeximus 3.9.1). The brief named
`inspeximus/mcp.py`. That file does not exist: the module was renamed `mcp_server.py` so that it cannot
shadow the MCP SDK (see the comment at `mcp_server.py:57`). 133 tools, 4 resources, 3 prompts. Resources and
prompts are outside this review.
**Yardstick:** the tool's docstring, which is the description an MCP client is shown. For guard parity, the
yardstick is also the server's documented configuration: the module docstring (`mcp_server.py:1-44`) and
the `--project` help text (`mcp_server.py:2727`).
**Method:** every call goes through a real MCP client session held in memory (`tests/_mcp_review.py`), so
argument validation, the `isError` flag and JSON serialisation are the ones a client gets. Every test builds
its own throwaway store in pytest's `tmp_path` and clears every environment variable the server reads
first. The live `inspeximus` MCP server attached to this session was never called.
**Reproducers:** `tests/test_mcp_review_*.py`, one file per tool family. Each mismatch is a test that asserts
the described behaviour and is marked `xfail(strict=True, raises=AssertionError)`, so it fails today and
XPASSes when the tool is fixed. Preconditions use `pytest.fail`, so a setup that goes wrong fails the run
and is not counted as the expected failure.
**No library code was changed.** The branch adds this report, the harness and the tests.
**Environment:** Python 3.11.15, mcp 1.30.0, pytest 9.1.1 with xdist.

## Verdict

60 mismatches across the 7 tool families: **24 High, 30 Medium, 6 Low**. Each one has a reproducing
test, 98 strict xfails in all. Run them in parallel with `python -m pytest tests/test_mcp_review_*.py`
(about 10 s), or one at a time with `-n 0`. All 98 xfail. Run with `--runxfail`, every one of them fails
on its final `AssertionError`, not on a precondition.

**What holds.** The core write and read guards behave through the tools most clients use. Through
`remember` and `remember_decision`:
- The echo guard retires a restated retired value, verbatim or reworded.
- The objectless guard retires a keyed write that carries no object.
- A write the guards retire is reported as `blocked`, with its `policy`.

Across the recall family:
- Instruction-shaped records stay quarantined out of recall.
- Keyword-stuffed records do not outrank clean ones.
- The server that recorded an Art. 21 objection withholds the subject's records from every recall
  variant.
- Access grants fail closed.

Erasure scrubs links, and its dry runs change nothing. `verify_writes` and `governance_report` honour the
configured key pin. Families 3, 4, 6 and 7 checked their read-only tools byte for byte, and each left the
store file and its sidecars unchanged. The exception is `audit_the_audits` (I5), whose leftovers land
outside the store.

**Where it breaks.** Most of the 60 come down to seven causes. Each is a gap between what `remember`,
`recall` and `verify_writes` do and what a later tool re-implemented around them:

| | Root cause | Findings |
|---|---|---|
| X1 | **The server cannot sign.** `open_store()` is called without `receipt_key`/`receipt_signer` (`mcp_server.py:272`), and no environment variable supplies one. Yet the module docstring describes signed stores. On a store the library signed, one MCP write or erasure appends an unsigned receipt or tombstone, and `verify_writes` fails from then on. `retention` says its tombstones are signed, and on this server they never are. | W7, E6 |
| X2 | **`INSPEXIMUS_RECEIPT_PUBKEY` is honoured only by `verify_writes` and `governance_report`**, the two tools that call `_pin()`. Eight other tools that give a tamper-evidence verdict pass the caller's argument or nothing, so they certify a chain re-signed under a foreign key that `verify_writes` on the same server rejects: `erasure_certificate`, `compliance_check`, `compliance_report`, `verify_attribution`, `audit_bundle`, `technical_documentation`, `deployer_report` and `registration_export`. | E5, I1, I2, I3, I6, L2 |
| X3 | **Twenty-two tools write to the action ledger through a second, keyless `ActionLedger`.** 19 were measured, and the other 3 use the same construction in the code. The boundary ledger signs with the writer key (`mcp_server.py:373-374`). One call to any `record_*` or rights tool leaves an unsigned entry in a signed chain, and `actions_verify` answers `ok: false` for the rest of the ledger's life. | L1, S5 |
| X4 | **The project scope is applied only by `remember`, `remember_decision` and the recall family.** Six write tools stamp no project: `revert`, `route`, `resolve_reopened`, `remember_in_partition`, `rectify_subject` and `deprecate_symbol`. An unstamped record is global, so a reverted or rectified value leaks into every project. Four reads cross projects: `recall_as`, `why_recalled` (quoting record text), `token_report` and `check_sources`. | W2, E7, S7, R1, R6, M5 |
| X5 | **Several tools change the store and return before the change is on disk.** `release_quarantine`, `credit`, `consolidate` and `consolidate_clusters` end with the throttled `_save()`. Within 5 s of the previous save that only marks the store dirty, and nothing flushes at exit. `sleep(keep)` never saves its budget pass in the call that reports it. | W4, M1, M4 |
| X6 | **Write tools that bypass `_write_verdict()`.** `route`, `remember_in_partition`, `rectify_subject` and `deprecate_symbol` report a write the echo or objectless guard retired on arrival as a landed write. `rectify_subject` also logs it to the ledger as an `ok` rectification. | W1, E3, S2, S4 |
| X7 | **State kept in sidecars, or held in memory, is not re-read by the per-call `refresh()`.** A second server on the same store misses an Art. 21 objection and a spent influence budget. A running server's `verify_consistency` trusts the receipt chain in memory over a rolled-back disk. | S1, M9, I4 |

The remaining findings are local: a report that counts the wrong thing, a description that drifted from
the library, a scan with a hole in it. The per-family sections give each one's cause.

### Findings index

| ID | Severity | Family | Finding |
|---|---|---|---|
| W1 | High | 1 writes | `route` reports a write the objectless guard retired as `remembered` |
| W7 | High | 1 writes | on a signed store, one ordinary write through the server breaks the receipt chain |
| E1 | High | 3 erasure | `close_partition` leaves a context partition's superseded records in the store |
| E2 | High | 3 erasure | `sweep_partitions` leaves expired superseded records in place |
| E3 | High | 3 erasure | `remember_in_partition` reports a write the guard retired as landed |
| E4 | High | 3 erasure | `erasure_residue` returns a clean verdict over a directory it could not list |
| E5 | High | 3 erasure | `erasure_certificate` ignores `INSPEXIMUS_RECEIPT_PUBKEY` |
| E6 | High | 3 erasure | on a signed store, MCP erasures break the chain, and `retention`'s tombstones are never signed |
| I1 | High | 4 integrity | `compliance_check` passes a chain the configured pin rejects |
| I2 | High | 4 integrity | `compliance_report` reports `integrity_verified: true` over the same chain |
| I3 | High | 4 integrity | `verify_attribution` is a tamper-evidence check that cannot be pinned |
| I4 | High | 4 integrity | on a running server, `verify_consistency` misses a rollback made on disk |
| I5 | High | 4 integrity | `audit_the_audits` leaves copies of the store, erased text included, in the temp directory |
| L1 | High | 5 ledger | 18 ledger-writing tools append unsigned entries to a signed ledger |
| L2 | High | 5 ledger | the documentation tools report `memory_chain_verified: true` against the configured pin |
| L3 | High | 5 ledger | `actions_verify` passes a ledger it cannot read |
| L4 | High | 5 ledger | a ledger write replaces a ledger it cannot read with a new chain |
| S1 | High | 6 rights/access | an Art. 21 objection recorded through one server is not honoured by another on the same store |
| S2 | High | 6 rights/access | `rectify_subject` reports a correction the guard retired as done, and logs it as done |
| S3 | High | 6 rights/access | `subscribe_memory_event`'s default cursor never delivers anything |
| S4 | High | 6 rights/access | `deprecate_symbol` returns a deprecation the echo guard retired as "the recorded deprecation" |
| S5 | High | 6 rights/access | rights ledger entries break a signed action ledger |
| M1 | High | 7 maintenance | `credit` reports an update that never reaches the store file |
| M2 | High | 7 maintenance | `credit` records a positive number as a **bad** outcome |
| W2 | Medium | 1 writes | writes other than `remember` ignore the server's project scope |
| W3 | Medium | 1 writes | `observe(object="")` reopens on a single, uncorroborated observation |
| W4 | Medium | 1 writes | `release_quarantine` can leave the release only in memory |
| R1 | Medium | 2 reads | `why_recalled` explains, and quotes, records that recall never shows this project |
| R2 | Medium | 2 reads | `recall(full=True)` does not return complete records |
| R3 | Medium | 2 reads | `recall(trusted_only=True)` returns a bare `[]`, and no trust root can be configured |
| R4 | Medium | 2 reads | `where_am_i` reports receipts off for a store that keeps them on |
| R5 | Medium | 2 reads | `supersession_report` gives counts, not the per-key ledger it describes |
| E7 | Medium | 3 erasure | `remember_in_partition` ignores the server's project scope |
| E8 | Medium | 3 erasure | re-opening a partition echoes rules that are not in force |
| E9 | Medium | 3 erasure | `pii_report` does not count PII held in superseded records |
| E10 | Medium | 3 erasure | `forget_subject` returns no `scrubbed_links` |
| E11 | Medium | 3 erasure | `erasure_residue` returns a clean verdict without entering a symlinked directory |
| I6 | Medium | 4 integrity | `audit_bundle` exports `verified: true` under the configured pin |
| I7 | Medium | 4 integrity | `admissibility_preconditions` does not check the receipt invariant it describes |
| L5 | Medium | 5 ledger | `operator_json` is refused whenever it is a JSON object string |
| L6 | Medium | 5 ledger | after `archive_actions`, three tools refuse entries that are still live |
| L7 | Medium | 5 ledger | `post_market_report` counts a reported incident twice and keeps it overdue |
| L8 | Medium | 5 ledger | a risk that refers to an entry is not listed as referring to it |
| L9 | Medium | 5 ledger | a risk with free-text `evidence` breaks four tools |
| S6 | Medium | 6 rights/access | "stop serving their records" holds for recall only |
| S7 | Medium | 6 rights/access | `rectify_subject`, `recall_as` and `deprecate_symbol` ignore the server's project scope |
| S8 | Medium | 6 rights/access | on a JSON-format store, `poll_memory_events` reports no events after writes, with no error |
| M3 | Medium | 7 maintenance | `credit` with a negative `weight` shrinks the counts it says only grow |
| M4 | Medium | 7 maintenance | `consolidate`, `consolidate_clusters` and `sleep(keep=...)` report changes they do not save |
| M5 | Medium | 7 maintenance | `check_sources` is not scoped to the server's project |
| M6 | Medium | 7 maintenance | `check_sources` says `ok: true` when nothing was checked |
| M7 | Medium | 7 maintenance | `verify_claim` says `stale_superseded` with `current: None` |
| M8 | Medium | 7 maintenance | `memory_report` and `selection_integrity` overwrite the recall-window observation |
| M9 | Medium | 7 maintenance | `irreversible_budget_report` never changes after its first call |
| W5 | Low | 1 writes | `remember` says recall raises a memory's value; it does not |
| W6 | Low | 1 writes | `route` accepts an unknown `policy` without an error |
| W8 | Low | 1 writes | `remember` reports lineage that was never stored |
| R6 | Low | 2 reads | `token_report` does not size "the SAME top-k recall" |
| R7 | Low | 2 reads | `why_recalled` does not name the quarantine when that is why a record did not surface |
| I8 | Low | 4 integrity | `anchor`'s "SIGNED HEAD COMMITMENT" carries no signature |

Severity: **High**: a guard is bypassed, data is wrongly written, erased or exposed, or a failure reads as
success or as a clean result. **Medium**: a promised field or behaviour is wrong or missing. **Low**:
wording that misleads, with little consequence.

---

## Family 1: writes, corrections and the quarantine release

Tools: `remember`, `remember_decision`, `revert`, `route`, `observe`, `reopened`, `resolve_reopened`,
`retire_key`, `read_guard_report`, `release_quarantine`. Tests: `tests/test_mcp_review_writes.py`.

### W1 (High): `route` reports a write the objectless guard retired as `remembered`

- **Description** (`mcp_server.py:574`): "hand it any utterance and it decides the right ledger operation
  ... Returns {intent, action, key, ...} describing what was done."
- **What happens:** `route(text="the region is lima", key="svc::region")` with no `object`, on a key whose
  values carry objects, returns `{"intent": "assert", "action": "remembered", "event": "ADD"}`. The record it
  wrote is `superseded` with `meta.objectless_blocked`, and `last_write` says `blocked: true, policy:
  objectless_guard`. The guard works; the result reports the opposite. The same write through `remember`
  returns `blocked: true`, because that tool appends `_write_verdict()` (3.5.1). `route` does not.
- **Cause:** `mcp_server.py:588` returns `_MEM.route(...)` as it is. `core.py:11788-11791` is the
  `object is None or key is None` branch, which answers `remembered` without reading `last_write`.
- **Test:** `test_route_reports_a_write_the_objectless_guard_retired`

### W2 (Medium): writes other than `remember` ignore the server's project scope

- **Description:** the module docstring for `INSPEXIMUS_PROJECT` (`mcp_server.py:19`): "Writes are stamped
  with it and recalls are filtered to it". `--project` (`mcp_server.py:2727`): "tag writes with this
  project/workspace and filter recalls to it".
- **What happens:** only `remember` and `remember_decision` pass `project=_PROJECT`. Three write tools in
  this family write records with no project stamp, and an unstamped record is global by design, so every
  project recalls it:
  - `revert` writes a new record for the restored value. On a server with `--project alpha`, reverting
    `alpha::wallet` makes the restored wallet address visible to a `--project beta` server, which could not
    see any of alpha's values before the revert.
  - `route` writes every fact it remembers unstamped.
  - `resolve_reopened(decision="reaffirm_prior")` writes the reaffirmed value unstamped.
- **Cause:** `mcp_server.py:570`, `:588` and `:621` pass no project. The library calls behind them take none:
  `core.py:11343` (revert), `core.py:11646` (route) and `core.py:7706` (resolve_reopened). Other families
  report the same gap for other write tools: see the cross-family section.
- **Tests:** `test_revert_keeps_the_restored_value_inside_the_project`, `test_route_stamps_the_server_project`,
  `test_resolve_reopened_reaffirm_stamps_the_server_project`

### W3 (Medium): `observe(object="")` reopens on a single, uncorroborated observation

- **Description** (`mcp_server.py:595-603`): "Feed it an OBSERVATION ... or object="" for a value-obscuring
  revert ("go back to what we had", names no value). ... this REOPENS that settled record for review — but
  only once the contradiction is CORROBORATED, so a lone stray restatement stays an echo and does not
  reopen."
- **What happens:** one `observe(text="go back to what we had", key=k, object="")` returns `reopened: true`
  and puts the record in the `reopened()` queue. A named contradiction does wait for two observations, as
  described. The value-obscuring form, which is the one the description names, does not.
- **Cause:** `mcp_server.py:604` turns `""` into `None`. `core.py:7572` reopens a value-obscuring revert on
  first sight. The library's own docstring says so ("value-obscuring revert reopens on first sight"). The
  tool's description says the opposite.
- **Consequence:** a single injected "go back" sentence puts any settled key into steward review. The
  record stays active, so no value changes.
- **Test:** `test_observe_value_obscuring_revert_needs_corroboration`

### W4 (Medium): `release_quarantine` can leave the release only in memory

- **Description** (`mcp_server.py:2133`): "A human decision that a quarantined record is a memory after all:
  it returns to recall and keeps who released it and why."
- **What happens:** a release made within 5 s of the store's previous save is not written to disk. A fresh
  handle on the same file, which is what a restarted server or a peer process reads, still sees the record
  quarantined with `released: None`. The tool's result gives no sign of this. Compare `retire_key`, which
  force-saves and returns `persisted`.
- **Cause:** `core.py:8144` ends `release_quarantine()` with `self._save()`, not `self._save(force=True)`.
  `core.py:16284` turns an unforced save within `_save_min_s` (`core.py:2764`, 5.0 s) into `_dirty = True`,
  and neither `core.py` nor `mcp_server.py` flushes at exit. The release does reach disk with the next forced
  save, if one happens before the process stops.
- **Test:** `test_release_quarantine_reaches_disk`. The test sets `_last_save` to now, which is the state
  right after any write, so the result does not depend on timing.

### W5 (Low): `remember` says recall raises a memory's value; it does not

- **Description** (`mcp_server.py:431-432`): "`value` (>=1) is its importance — higher-value memories outrank
  merely-similar ones at recall, and recall itself nudges value up."
- **What happens:** after five recalls that returned the record, its `value` is still 1.0.
- **Cause:** `core.py:12545` defaults `reinforce=False`, and the `recall` tool (`mcp_server.py:668`) never
  passes it. The library has already corrected the same claim in its own comments (`core.py:14782-14788`).
- **Test:** `test_remember_description_recall_nudges_value_up`

### W6 (Low): `route` accepts an unknown `policy` without an error

- **Description** (`mcp_server.py:583-586`): "`policy` picks the failure mode: "safe" (default) ...;
  "context" ...; "trusting" always restores."
- **What happens:** `policy="trusted"`, a likely typo for `trusting`, is accepted and echoed back as
  `"policy": "trusted"`, and the call behaves as `safe`. A caller who asked for a restore gets `blocked`, and
  nothing tells them the policy name was wrong. This fails safe, which is why it is rated Low.
- **Cause:** `core.py:11816` checks for `trusting` and `context`, and anything else falls through to the echo
  branch. Neither layer validates the value.
- **Test:** `test_route_refuses_an_unknown_policy`

### W7 (High): on a signed store, one ordinary write through the server breaks the receipt chain

- **Description:** the module docstring (`mcp_server.py:41-44`): "INSPEXIMUS_RECEIPT_PUBKEY ... Set it
  whenever the store is signed". Guard parity: the same `remember` through the library, holding the store's
  key, keeps `verify_writes` ok.
- **What happens:** on a store the library signed, one `remember` through the server appends an
  **unsigned** receipt. `verify_writes` (pinned) goes from ok to `["receipt 1: unsigned, but a signature
  was required", "1 of 2 chain entries carry NO signature while 1 do. A chain signed in places is not
  signed: something without the key appended to it."]`, and it stays failed.
- **Cause:** `mcp_server.py:272` calls `open_store(...)` without `receipt_key` or `receipt_signer`, and no
  environment variable supplies either. `INSPEXIMUS_WRITER_KEY` only attests records. Every write and
  erasure tool is affected: see X1.
- **Test:** `test_remember_on_a_signed_store_keeps_the_chain_verifiable`

### W8 (Low): `remember` reports lineage that was never stored

- **Description** (`mcp_server.py:453-455`, `:463`): "`derived_from` — the ids this memory was BUILT
  FROM. Provenance rides along the edge ..." and "Returns the new id, and the VERDICT on the write".
- **What happens:** `remember(derived_from=["deadbeef00"])` names a parent that does not exist. The library
  drops the edge and stores `derived_from: None, orphan: True`. The tool returns `derived_from:
  ["deadbeef00"]` and `attributable: true`, both built from its arguments
  (`mcp_server.py:478-482`). It tells the caller the record is attributable through lineage that is
  not there, at the only moment the caller could still fix it.
- **Test:** `test_remember_reports_the_lineage_that_was_stored`

### Holds

| Tool | Checked |
|---|---|
| `remember` | The echo guard retires a restated retired value, verbatim without `object` and reworded with `object`, and the result says `blocked: true, policy: echo_guard`. The objectless guard gives `blocked: true, policy: objectless_guard` with `current_id` and `note`. `reaffirm=True` restores. `quarantined` and `stuffed` are reported at the write. The project is stamped. `source` is stored as `{"doc": ...}`. Every write extends the receipt chain. |
| `remember_decision` | `topic` gives `decision::<topic>` supersession. Restating an earlier decision is retired by the echo guard and reported `blocked`. `source` and the project reach the record. `revert("decision::<topic>")` restores the prior decision. |
| `revert` | `{ok, restored, superseded, reverted_to_object}`, or `{ok: false, reason}` when there is no predecessor. `verify_writes` stays ok afterwards. (Project scope: W2.) |
| `route` | Echo: `action: blocked`. Revert marker: `reverted` through `revert()`. Current value: `noop`. (W1, W2 and W6 aside.) |
| `observe` | A named contradiction needs `reopen_corroboration` (2) observations. Result shape as described. (W3 aside.) |
| `reopened` | Queue entries carry the current value, the reason and the surfaced prior. `key` scopes the queue. |
| `resolve_reopened` | `keep_current` clears the flag. `reaffirm_prior` restores the prior value. An unknown id or decision comes back as `isError`. |
| `retire_key` | `{key, retired, ids, reason, status: "superseded", policy: "retired", persisted}`. `verify_writes` stays ok. A missing reason comes back as `isError`. |
| `read_guard_report` | Ids, shapes and release state for quarantined records, the word and share for stuffed ones, and no text. |
| `release_quarantine` | Errors come back as `{"error": ...}` (no actor, unknown id, not quarantined). The record returns to recall in this process. (W4 aside.) |

---

## Family 2: reads

Tools: `recall`, `recall_iterative`, `recall_followup`, `get`, `neighbors`, `token_report`, `why_recalled`,
`where_am_i`, `projects`, `history`, `as_of`, `provenance`, `supersession_report`. Tests:
`tests/test_mcp_review_reads.py`.

### R1 (Medium): `why_recalled` explains, and quotes, records that recall never shows this project

- **Description** (`mcp_server.py:1383-1384`): "EXPLAINABILITY: why did (or didn't) a memory surface for
  `query`? Returns the per-channel breakdown (relevance/value/provenance) for the top hits, or for a
  specific `id`."
- **What happens:** on a server with `--project alpha`, `recall` returns alpha's one record. `why_recalled`
  lists that record and three of project beta's, each with the first 80 characters of its text. Its `rank`
  and `surfaced` describe a recall this client never gets: unscoped, and with k=12. The server treats
  crossing a project as a leak elsewhere: `neighbors` says "expanding around a hit must not be a side door
  back into another project's memories". This tool is such a side door.
- **Cause:** `mcp_server.py:1385` passes no project. `core.py:14789` ranks with its own
  `self.recall(query, k=k, reinforce=False)`.
- **Test:** `test_why_recalled_explains_only_what_recall_can_surface`

### R2 (Medium): `recall(full=True)` does not return complete records

- **Description** (`mcp_server.py:639`): "Set `full=True` to return complete records (all fields)."
- **What happens:** a full hit has `id, text, score, value, tags, links, source, iso, relevance, reliability,
  stale_derived`. It has no `key`, `object`, `status`, `meta`, `mtype`, `ts` or `valid_from`, all of which
  `get(id)` returns for the same record. The server already knows this: its own comment at
  `mcp_server.py:676-677` says "a recall hit is a projection and carries no `meta` on either the compact or
  the full path".
- **Cause:** `mcp_server.py:682-683` returns the library's hit projection as it is.
- **Test:** `test_recall_full_returns_complete_records`

### R3 (Medium): `recall(trusted_only=True)` returns a bare `[]`, and no trust root can be configured

- **Description** (`mcp_server.py:642-644`): "`trusted_only=True` (needs a configured trust root) returns
  only memories anchored to a trusted signing key".
- **What happens:** no environment variable and no tool on this server sets `trust_seeds`. The library
  fails closed and returns `[]`, which is correct. But the answer carries nothing to tell "no trust root is
  configured" apart from "nothing trusted matched". `selection_integrity` needs the same root and says so in
  its result ("no trust root configured"). `recall` does not. Over MCP, the defence the description offers
  against poisoned memories cannot be switched on, and the empty result does not say why.
- **Cause:** `core.py:12788` (the fail-closed branch). `mcp_server.py` has no configuration for
  `trust_seeds`.
- **Test:** `test_recall_trusted_only_says_when_there_is_no_trust_root`

### R4 (Medium): `where_am_i` reports receipts off for a store that keeps them on

- **Description** (`mcp_server.py:765`): "Returns ... the active project scope, and the embedder/receipt
  posture."
- **What happens:** open a store that already has a `.receipts.json` sidecar, with `INSPEXIMUS_RECEIPTS`
  unset. `open_store()` keeps receipts on, as documented (`_surface.py:122-124`), and every write extends
  the chain. `where_am_i` still answers `"receipts": false`.
- **Cause:** `mcp_server.py:784` reports `bool(_RECEIPTS)`, the environment variable, instead of the
  store's own state.
- **Test:** `test_where_am_i_reports_the_receipts_the_store_actually_keeps`

### R5 (Medium): `supersession_report` gives counts, not the per-key ledger it describes

- **Description** (`mcp_server.py:1390-1391`): "The correction ledger: which facts have been
  superseded/reverted, by key — the auditable 'what changed and what's current' view".
- **What happens:** the result is `{superseded_total, by_policy: {policy: count},
  values_too_short_to_suppress}`. It names no key that changed (only keys whose retired values are under 4
  characters) and no current value. The per-key view the description promises is `history(key)`, one key at
  a time.
- **Cause:** `core.py:12197-12212` counts per policy. The tool passes the result through
  (`mcp_server.py:1392`), under a description written for a different report.
- **Test:** `test_supersession_report_names_the_keys_and_what_is_current`

### R6 (Low): `token_report` does not size "the SAME top-k recall"

- **Description** (`mcp_server.py:841-842`): "DETERMINISTIC payload-size estimate ... for the SAME top-k
  recall: how much smaller the compact projection is than the full records for those same k hits."
- **What happens:** on a server with `--project alpha` where recall returns 1 hit, `token_report` sizes 4:
  alpha's and three of beta's. It also ignores `INSPEXIMUS_READ_RESOLVER`, which `recall` applies. Only
  estimates come back, not content, so nothing leaks.
- **Cause:** `mcp_server.py:850`, `_MEM.recall(query, k=k)`, with no project and no resolver.
- **Test:** `test_token_report_sizes_the_hits_recall_returns`

### R7 (Low): `why_recalled` does not name the quarantine when that is why a record did not surface

- **Description** (`mcp_server.py:1383`): "why did (or didn't) a memory surface for `query`? ... or for a
  specific `id`."
- **What happens:** for a quarantined record the answer is `rank: None, surfaced: False, gated_out: True,
  gate_reason: "0 distinct corroborating source(s), need 2"`. That gate reason belongs to the influence
  gate, which a default recall does not apply. The read guard that actually withheld the record is not
  mentioned.
- **Cause:** `core.py:14773-14824` builds the breakdown and has no read-guard field.
- **Test:** `test_why_recalled_names_the_quarantine`

### Holds

| Tool | Checked |
|---|---|
| `recall` | Quarantined records are left out unless `include_quarantined`. A keyword-stuffed record does not outrank the clean one. The project filter applies, and `all_projects=True` labels each hit with its `project`, read from the store record. `k` is capped at `INSPEXIMUS_MAX_K`. `snippet_chars` truncates and sets `truncated`. `with_warrant` keeps `warrant` in the compact projection. (R2 and R3 aside.) |
| `recall_iterative` | Shape `{k, max_followups, round, hits, prior_ids, ask, next_call, bounds}`. The query is not echoed anywhere in the result. The project scope, quarantine and stuffing rules apply. |
| `recall_followup` | Shape `{followups_used, followups_dropped, new_hits, bridged, merged_ids, recall_calls, bounds}`. The project scope, quarantine and stuffing rules apply. |
| `get` | The full record. `{}` for an unknown id. |
| `neighbors` | Excludes the record itself. `[]` for an unknown id. The project scope, quarantine and stuffing rules apply. |
| `projects` | Per-project counts, `unscoped`, `active` and `total` match the store. |
| `history` | Every value in event-time order, each with the policy that retired it. |
| `as_of` | Returns the value current at `when`. |
| `provenance` | Shape as described, with `limits`. Passing neither `key` nor `id` comes back as `isError`. |

---

## Family 3: erasure, retention and partitions

Tools: `forget`, `forget_subject`, `forget_pii`, `pii_report`, `retention`, `erasure_report`,
`erasure_certificate`, `erasure_audit`, `erasure_residue`, `declare_out_of_band_deletion`, `open_partition`,
`remember_in_partition`, `sweep_partitions`, `close_partition`, `partitions_report`. Tests:
`tests/test_mcp_review_erasure.py`.

### E1 (High): `close_partition` leaves a context partition's superseded records in the store

- **Description:** `close_partition` (`mcp_server.py:2269`): "A context partition erases its records
  (disposition erased)". `open_partition`: "a context partition erases its records at close".
- **What happens:** a context partition gets two keyed writes ("customer card ends 4471", then "... 9920").
  The close returns `{disposition: erased, erased: 1}`. The first record is still in the store, tagged
  `partition:c1` and `superseded`, and it survives a restart. `history("ctx::card")` returns its text.
- **Cause:** `partitions.py:140-141`. `_records()` keeps only active records, and the close erases what
  it returns (`partitions.py:230-233`). The same filter keeps superseded records out of the cap count
  (`:78`) and out of `partitions_report` (`:257`).
- **Test:** `test_close_partition_erases_every_record_of_a_context_partition`

### E2 (High): `sweep_partitions` leaves expired superseded records in place

- **Description** (`mcp_server.py:2259-2260`): "records past max_age_days and beyond max_records are
  hard-deleted with a tombstone".
- **What happens:** an agent partition has `max_age_days=0` and two keyed writes. The sweep reports
  `expired: 1`. The superseded record, still tagged with the partition, stays in the store, and
  `partitions_report` then says `records: 0, sweep_due: False`.
- **Cause:** as in E1: `partitions.py:186-190` sweeps `_records()`.
- **Test:** `test_sweep_partitions_erases_every_expired_partition_record`

### E3 (High): `remember_in_partition` reports a write the guard retired as landed

- **Description** (`mcp_server.py:2247-2248`): "Remember into a partition: the record is tagged
  partition:<name>, counted against its cap ...". The yardstick is guard parity: `remember` reports a
  blocked write as `blocked`.
- **What happens:** the tool has no `object` parameter. On a key whose values carry objects, every
  partition write is retired on arrival by the objectless guard (`last_write.blocked: true`). A restated
  retired value is retired by the echo guard. Either way the result is `{id, partition}`, which looks
  the same as a landed write.
- **Cause:** `mcp_server.py:2251-2254` never adds `_write_verdict()`.
- **Test:** `test_remember_in_partition_reports_a_write_the_guard_retired`

### E4 (High): `erasure_residue` returns a clean verdict over a directory it could not list

- **Description** (`mcp_server.py:1516-1517`): "A file it could not read makes the verdict False:
  "clean" must never mean "we did not look at that part"."
- **What happens:** the value sits in `root/locked/export.json`. If that directory cannot be listed
  (permission denied), the answer is `{ok: True, checked_files: 1, skipped: [], problems: []}`. An
  unreadable file is handled; an unreadable directory disappears from the scan. The test simulates the
  permission error by failing `os.scandir` for that directory, because the suite may run as root.
- **Cause:** `erasure_residue.py:221`. `os.walk(...)` has no `onerror`, so listing errors are swallowed.
- **Test:** `test_erasure_residue_an_unreadable_directory_is_not_clean`

### E5 (High): `erasure_certificate` ignores `INSPEXIMUS_RECEIPT_PUBKEY`

- **Description:** the module docstring (`mcp_server.py:41-44`): "Set it whenever the store is signed:
  without it the tamper-evidence tools verify that receipts are signed by SOMEBODY". The tool: "the
  auditor-grade receipt proving records were erased".
- **What happens:** the store is signed by key K2 and the server is pinned to K1. `verify_writes` and
  `governance_report` report "signed by an unexpected key". The certificate's `self_check` says
  `verified: True, problems: []`.
- **Cause:** `mcp_server.py:2415` passes `expected_pubkey or None` instead of `_pin(expected_pubkey)`
  (`mcp_server.py:1041`). See X2.
- **Test:** `test_erasure_certificate_self_check_honours_the_configured_pubkey`

### E6 (High): on a signed store, MCP erasures break the chain, and `retention`'s tombstones are never signed

- **Description:** `retention` (`mcp_server.py:1441-1443`): "with apply=True, hard-delete them — each
  erasure leaving a signed tombstone, so the enforcement is itself auditable." Guard parity for `forget`:
  through the library, with the key, `verify_writes` stays ok.
- **What happens:**
  - The server cannot sign (see X1), so every tombstone it writes is unsigned, in every configuration.
  - On a store the library signed, one `retention(apply=True)` or `forget` turns the pinned
    `verify_writes` false: "tombstone 1: unsigned, but a signature was required".
  - After an MCP `forget_subject`, a third party's `verify_erasure_certificate(cert, expected_pubkey=...)`
    for that request returns `valid: False` ("PARTIALLY SIGNED").
  - Also measured: `declare_out_of_band_deletion` swaps "deleted out-of-band" for "unsigned, but a
    signature was required".
  - Not measured, but the code runs through the same `forget()` path: `forget_pii`, and the partition
    sweep, close and cap eviction.
- **Cause:** `mcp_server.py:272`. `core.py:7947-7962` signs a tombstone only with
  `_receipt_signer`/`_receipt_sk`.
- **Tests:** `test_retention_apply_leaves_a_signed_tombstone`, `test_forget_on_a_signed_store_keeps_the_chain_verifiable`

### E7 (Medium): `remember_in_partition` ignores the server's project scope

- **Description:** `INSPEXIMUS_PROJECT`: "Writes are stamped with it and recalls are filtered to it".
- **What happens:** a record written through `remember_in_partition` on `--project alpha` carries no
  stamp, and a `--project beta` server recalls it. A plain `remember` from alpha stays hidden, as it
  should. See X4.
- **Cause:** `mcp_server.py:2251` passes no project. `partitions.py:86` forwards only what it is given.
- **Test:** `test_remember_in_partition_stamps_the_server_project`

### E8 (Medium): re-opening a partition echoes rules that are not in force

- **Description** (`mcp_server.py:2233-2236`): "Open a memory partition: a named scope ... with a size cap
  and an expiry ... `kind` is context, process or agent".
- **What happens:** `open_partition("w1", kind="process")`, then `open_partition("w1", kind="context",
  max_records=1, max_age_days=1)`. The second call returns the requested `{kind: context, max_records: 1,
  max_age_days: 1}`. `partitions_report` shows `w1` still as an uncapped `process` partition with no
  expiry, and at close its records are kept. The caller was told they would be erased.
- **Cause:** `partitions.py:156-160` returns a handle to the existing partition and ignores the new
  arguments. `mcp_server.py:2242` echoes the arguments rather than the registered rules.
- **Test:** `test_open_partition_reports_the_rules_actually_in_force`. Refusing the conflicting re-open
  would also pass it.

### E9 (Medium): `pii_report` does not count PII held in superseded records

- **Description** (`mcp_server.py:1357`): "What PII the store currently holds, by type ... Read-only; pair
  with forget_pii to act on it."
- **What happens:** with `INSPEXIMUS_PII_DETECT=1`, a contact whose email was corrected keeps
  `alice@example.com` in a superseded record tagged `pii=['email']`. `pii_report` says
  `records_with_pii: 0`, and its pair `forget_pii()` then erases that record (`erased: 1`).
- **Cause:** `core.py:9340` skips every record that is not active. The library docstring says "ACTIVE
  records". The tool description does not.
- **Test:** `test_pii_report_counts_pii_held_in_superseded_records`

### E10 (Medium): `forget_subject` returns no `scrubbed_links`

- **Description** (`mcp_server.py:1011-1013`): "delete every memory about `subject` AND scrub its id from
  survivors' links/supersession pointers ... Returns a receipt (forgotten count, ids, scrubbed_links) you
  can keep as evidence."
- **What happens:** the link is scrubbed. The receipt has `erased, ids, request_id, tombstones, coverage,
  residue_in_store`. It has no `scrubbed_links`, and the count is named `erased`, not `forgotten`.
- **Cause:** `core.py:8278-8285` builds its own result from `forget()`'s and drops `scrubbed_links`.
- **Test:** `test_forget_subject_returns_scrubbed_links`

### E11 (Medium): `erasure_residue` returns a clean verdict without entering a symlinked directory

- **Description:** as in E4.
- **What happens:** `root/data -> ../volume` holds the value. The result is `ok: True` with nothing in
  `skipped`. A broken symlink, and a directory pruned by `skip_dirs`, are both reported as not looked at.
  A symlinked directory is not. Not following links is a reasonable default. Leaving the unsearched
  subtree out of the verdict is the gap.
- **Cause:** `erasure_residue.py:221-231`.
- **Test:** `test_erasure_residue_a_symlinked_directory_is_not_clean`

### Holds

| Tool | Checked |
|---|---|
| `forget` | `dry_run` returns `{would_forget, ids, sample, dry_run: true}` with matched texts and changes no bytes. The real call returns `{forgotten, ids, scrubbed_links, ...}` and scrubs survivors' links. `basis`, `request_id`, `authorized_by` and `authorization` reach the tombstone inside the committed hash. On an unsigned store `verify_writes` stays ok. (E6 aside.) |
| `forget_subject` | `AmbiguousSubject` comes back as `isError` with the message. `dry_run` returns `{would_erase, direct, inherited, sample, also_carrying}` and changes neither the store nor the receipts. `exact=True` leaves the colliding subject alone, and `allow_ambiguous=True` erases both. (E10 aside.) |
| `forget_pii` | `subject` and `types` scoping. `basis` and `request_id` reach the tombstone. With detection off, the result says the sweep is PARTIAL rather than clean. |
| `retention` | Dry-run by default, returning `{eligible, ids, applied: false, erased: 0}` with no bytes changed. `pii_only` defaults to true. `basis` and `request_id` are recorded. (E6 aside.) |
| `erasure_report` | `{tombstoned_total, erasures: [{memory_id, ts, request_id, ...}]}`, content-free and read-only. |
| `erasure_certificate` | Scoped by `request_id`. An explicit `expected_pubkey` pins `self_check`. Read-only. (E5 aside.) |
| `erasure_audit` | `unaudited` with no declared lineage, `partially_audited` with an orphaned edge, `residue_found` after a parent is erased. `values` adds advisories and never moves the verdict. Read-only. |
| `declare_out_of_band_deletion` | Refused while the record is present, when no receipt names it, and for an empty id. After a raw delete it appends a tombstone with `actor` and `reason` in the committed hash, and `verify_writes` goes from "deleted out-of-band" to ok on an unsigned store. |
| `open_partition` | A bad kind or a bad name comes back as `{"error"}`. Re-opening a closed name is refused. (E8 aside.) |
| `remember_in_partition` | Tags `partition:<name>`. Extends the receipt chain. The cap evicts the oldest record with a tombstone. An unknown or closed partition comes back as `{"error"}`. (E3 and E7 aside.) |
| `sweep_partitions` | Active expired records are erased with basis `partition_expiry:<name>`, and unpartitioned records are untouched. With the action ledger on, it records a `partitions:sweep` entry. (E2 aside.) |
| `close_partition` | Context closes as `erased`, process as `retained`. A double close, an unknown name, or a write after close comes back as `{"error"}`. (E1 aside.) |
| `partitions_report` | Shape as described. Read-only. |
| `pii_report` | Shape `{records_with_pii, by_type, ids, coverage}`. With detection off, it says so. (E9 aside.) |
| `erasure_residue` | A missing root, a root that is a file, empty `values` and an oversized file all give `ok: false`. Findings carry a 12-character fingerprint, never the value. (E4 and E11 aside.) |

---

## Family 4: tamper evidence, audit and compliance reports

Tools: `verify_writes`, `anchor`, `verify_consistency`, `verify_cosigned_anchor`, `detect_split_view`,
`witness`, `verify_witness`, `state_digest`, `governance_report`, `admissibility_preconditions`,
`audit_the_audits`, `audit_bundle`, `verify_audit_bundle`, `verify_attribution`, `compliance_report`,
`compliance_check`, `coverage`. Tests: `tests/test_mcp_review_integrity.py`.

F1 to F4 all use the same setup. A library holder rewrites the store and re-signs it under a key the owner
never held, which is the attack the module docstring names for `INSPEXIMUS_RECEIPT_PUBKEY`. The server is
then started with `INSPEXIMUS_RECEIPT_PUBKEY` set to the owner's key. In every test, a precondition checks
that `verify_writes` on that server rejects the chain ("signed by an unexpected key"), so the pin is live.

### I1 (High): `compliance_check` passes a chain the configured pin rejects

- **Description** (`mcp_server.py:1423-1425`): "violations include ... integrity_failed (Art.12/15)". The
  library defines `integrity_failed` as "the chain fails verify_writes".
- **What happens:** `verify_writes` rejects the chain. On the same server, `compliance_check` returns
  `{ok: true, violations: []}`. The CI gate passes a chain that the configured key rejects.
- **Cause:** the tool (`mcp_server.py:1420-1435`) has no key parameter and never calls `_pin()`.
  `compliance.py:389` calls `store.governance_report()` with no key. Neither the environment pin nor a
  caller can bind this verdict. See X2.
- **Test:** `test_compliance_check_reports_integrity_failed_for_a_chain_the_configured_pin_rejects`

### I2 (High): `compliance_report` reports `integrity_verified: true` over the same chain

- **Description** (`mcp_server.py:1410-1412`): "compliance EVIDENCE ... with LIVE counts from this store and
  an honest per-control status".
- **What happens:** `summary.integrity_verified` is true, and nothing in the report says the verdict is
  unpinned.
- **Cause:** `mcp_server.py:1416` passes `expected_pubkey or None` instead of `_pin(expected_pubkey)`.
- **Test:** `test_compliance_report_integrity_verdict_is_bound_to_the_configured_pin`

### I3 (High): `verify_attribution` is a tamper-evidence check that cannot be pinned

- **Description** (`mcp_server.py:2461-2462`): "TAMPER-EVIDENCE for the attribution / poison-defense layer:
  are k, the influence budget, the influence gate, and the slash ledger internally consistent and
  unedited?"
- **What happens:** the store's attribution (a `treasury/cfo` source on an inflated wire-transfer limit) is
  committed under a foreign key. The pin is set. The tool returns `{ok: true, chain_ok: true, relabeled:
  []}`.
- **Cause:** `mcp_server.py:2463` passes nothing. The library method (`core.py:6705`) takes no key and checks
  each signature against the key the receipt itself carries (`core.py:6746-6751`).
- **Test:** `test_verify_attribution_is_bound_to_the_configured_pin`

### I4 (High): on a running server, `verify_consistency` misses a rollback made on disk

- **Description** (`mcp_server.py:1265-1267`): "confirm the store is a consistent forward-extension of the
  witnessed anchor (nothing was rewritten, rolled back, or re-signed away)".
- **What happens:**
  - Setup: three receipted writes, then `anchor()` records `n_writes=3`.
  - The store file and its receipt sidecar are restored to the one-write state while the server runs.
  - The running server's `verify_consistency(prior)` returns `consistent: true`, and `anchor()` still says
    `n_writes=3`.
  - A freshly started server correctly reports "write log shrank: 1 < anchored 3".
- **Cause:**
  - The per-call `refresh()` (`mcp_server.py:342`) merges with disk, re-adding this handle's records that
    are no longer on disk (`core.py:9088`).
  - It keeps the longer in-memory receipt chain when the disk chain is a prefix of it (`core.py:3729`).
  - `verify_consistency` then walks the chain in memory (`core.py:10911`).
- **Mitigation:** `verify_writes` on the same server does flag the rollback, so
  `compliance_check(prior_anchor)` still fails, but on `integrity_failed`, not `not_append_only`. The
  server's next write puts the rows back on disk. A read-only auditor's server never writes, so the rollback
  stays.
- **Test:** `test_verify_consistency_on_a_running_server_catches_a_rollback_on_disk`

### I5 (High): `audit_the_audits` leaves copies of the store, erased text included, in the temp directory

- **Description** (`mcp_server.py:1108`): "Corrupts a temporary COPY (never your store) in ways each surface
  claims to detect".
- **What happens:**
  - One call on a two-record store leaves 52 to 110 `mkdtemp` directories behind. They contain 24 full
    copies of the store and 4 `.pre-rows.bak` files.
  - It also leaves 25 head files in `INSPEXIMUS_KEY_HOME`, which defaults to `~/.config/inspeximus/heads`.
  - After `forget_subject` erases a subject from the live store, 28 files in the temp directory still hold
    the erased text. The live store itself is untouched, as described.
- **Cause:** `core.py:4390` onwards calls `tempfile.mkdtemp()` six times (`core.py:4696, 4741, 4894, 5029,
  5157, 5404`) and never removes the directories. There is no `rmtree` anywhere in 4390-5494.
- **Test:** `test_audit_the_audits_leaves_no_copy_of_the_store_behind`. It points `tempfile.tempdir` into
  `tmp_path`, runs the tool, erases the subject, then searches what is left.

### I6 (Medium): `audit_bundle` exports `verified: true` under the configured pin

- **Description** (`mcp_server.py:1455-1456`): "Export a portable, CONTENT-FREE audit bundle of this store's
  whole write + erasure history (EU AI Act Art. 12/19)". The bundle carries `governance.proof.verified`.
- **What happens:** with the pin set, the exported bundle says `verified: true, expected_pubkey: null`. This
  is Medium because an auditor running `verify_audit_bundle(expected_pubkey=...)` still rejects the chain.
  The false statement sits inside the artifact.
- **Cause:** `mcp_server.py:1459` passes `expected_pubkey or None`.
- **Test:** `test_audit_bundle_governance_verdict_is_bound_to_the_configured_pin`

### I7 (Medium): `admissibility_preconditions` does not check the receipt invariant it describes

- **Description** (`mcp_server.py:1089`): "receipt_chain_covers_records: if receipts are enabled and records
  exist, the chain is not empty". It also cites "the same shape found in our own 450-record store, which
  had receipts enabled, an empty chain". The description also says an invariant that cannot apply "does
  NOT count as holding".
- **What happens:**
  - Setup: a store with two records, written without receipts, opened with `INSPEXIMUS_RECEIPTS=1`.
    Receipts are now enabled, the records exist, and the chain is empty.
  - `verify_writes` reports "receipts are enabled but the chain is EMPTY".
  - This tool reports the invariant as `applicable: false, holds: true`, with a top-level `ok: true`.
- **Cause:** `core.py:4364-4367` narrowed the invariant on purpose, to "this store was WRITTEN with
  receipts". The tool description still states the old rule. The fix is probably the description, plus
  `holds` when the invariant cannot apply.
- **Test:** `test_admissibility_receipt_invariant_fires_when_receipts_are_enabled_and_the_chain_is_empty`

### I8 (Low): `anchor`'s "SIGNED HEAD COMMITMENT" carries no signature

- **Description** (`mcp_server.py:1252`): "emit a SIGNED HEAD COMMITMENT".
- **What happens:** no field of the result is a signature, and the tool has no way to ask for one. The head
  is a hash commitment meant for witnesses to co-sign.
- **Cause:** `mcp_server.py:1260` calls `anchor()` without `sign=`. The library signs only with an external
  callable (`core.py:10777`).
- **Test:** `test_anchor_returns_a_signed_head_commitment`

### Holds

| Tool | Checked |
|---|---|
| `verify_writes` | `{ok, problems, expected_pubkey, signed[, limits]}`. The environment pin rejects a foreign-signed chain. A rollback is caught through the head kept outside the store. Read-only. |
| `governance_report` | The pin applies through the environment and through the argument. `limits` is present when a signed chain is left unpinned. Read-only. |
| `verify_consistency` | On a fresh server it catches a rollback and a rewrite. Append-only growth stays consistent. A malformed anchor comes back as an error or `consistent: false`, never as a pass. (I4 aside.) |
| `verify_cosigned_anchor` | `threshold` below 1 is rejected. A substituted head is re-derived and rejected. An empty chain gives `covers_history: false` with `limits`. |
| `detect_split_view` | All six promised keys. A real double-signed fork is detected, and an honest pair is not. A malformed side is named. |
| `witness` / `verify_witness` | `digest_match` flips after a write. A moved source gives `stale_at_use`. A URL source lands in `sources_orphaned` with `valid: false`. |
| `state_digest` | Changes on a write, a supersession, a revert, and an erasure of an active or a superseded record. |
| `verify_audit_bundle` | A missing `store_path` is refused and not created. Substituted text is caught through `store_path`. `require_signed` and `expected_pubkey` both work. |
| `compliance_check` | All four violation codes fire under their documented names. `prior_anchor` adds `append_only` to `checked`. (I1 aside.) |
| `compliance_report`, `audit_bundle`, `coverage`, `admissibility_preconditions`, `audit_the_audits` | Shapes as described. Store bytes and sidecars unchanged. (I2, I5, I6 and I7 aside.) |

Read-only sweep: the store file, every sidecar, the key home and the temp directory were snapshotted around
every tool in this family. This was done three times: with receipts, with receipts plus the action ledger,
and with neither. Nothing changed except the documented one ledger entry per call, and I5.

---

## Family 5: the action ledger and its regulatory registers

Tools: `actions_verify`, `record_oversight`, `record_disclosure`, `oversight_report`, `record_incident`,
`incident_report`, `incident_reported`, `record_risk`, `risk_register`, `post_market_report`,
`record_corrective_action`, `corrective_action_report`, `record_authority_request`, `decision_explanation`,
`record_breach`, `breach_notified`, `breach_report`, `record_literacy`, `literacy_register`,
`record_attestation`, `attestation_register`, `record_responsibilities`, `responsibilities_register`,
`record_declaration`, `declaration_document`, `attest_documentation_retention`, `record_notice`,
`notice_register`, `record_processing_role`, `processing_roles`, `record_qms`, `qms_register`,
`archive_actions`, `record_lifecycle`, `export_audit_trail`, `action_timeline`, `timestamp_actions`,
`attest_retention`, `actions_match`, `what_it_knew`, `technical_documentation`, `deployer_report`,
`registration_export`. Also the recording done at the tool boundary (`_FreshFastMCP.tool`,
`_action_ledger()`). Tests: `tests/test_mcp_review_ledger.py`: 38 strict xfails from 11 test functions,
4 of them parametrized over tools.

### L1 (High): 18 ledger-writing tools append unsigned entries to a signed ledger

- **Description:**
  - The module docstring (`mcp_server.py:22-24`): "INSPEXIMUS_ACTIONS 1 to record every tool call in the
    ACTION LEDGER (<store>.actions.json): one signed, hash-chained entry per call".
  - `_action_ledger()`: "Signed with the store's receipt key when it has one, else with the server's
    writer key".
  - Three tools say it outright: `post_market_report` ("signed into the ledger as a `monitoring` entry"),
    `attest_documentation_retention` ("Append a signed statement") and `record_qms` ("the signed record").
- **What happens:** with `INSPEXIMUS_ACTIONS=1` and a writer key, the boundary entries are signed. Each
  of these tools writes its own entry without a signature and returns `signed: false`:
  - every `record_*` tool
  - `breach_notified` and `attest_documentation_retention`
  - `post_market_report(actor=...)` and `decision_explanation(actor=...)`

  From then on, `actions_verify` answers `ok: false` ("seq N: no signature") on every call, and
  `post_market_report.chain` and `technical_documentation` report the failure too. The chain does not fork,
  because `record()` re-reads the file first (`actions.py:498`). Only the signing is wrong.
- **Cause:** each tool builds `ActionLedger(_MEM, actor=_ACTOR)` without a key. That constructor falls back
  to `store._receipt_sk` (`actions.py:317`), which this server never has. Only `_LED` gets the writer key
  (`mcp_server.py:373-374`). Once any entry is signed, verification requires every entry to be signed. See
  X3.
- **Test:** `test_a_ledger_writing_tool_keeps_a_signed_ledger_signed[<18 tools>]`

### L2 (High): the documentation tools report `memory_chain_verified: true` against the configured pin

- **Description:** the module docstring's `INSPEXIMUS_RECEIPT_PUBKEY` promise (`mcp_server.py:41-44`).
  The three tools report "chain verification".
- **What happens:** the receipts are signed with key A and the server is pinned to key B. `governance_report`
  says the chain is not verified. `technical_documentation`, `deployer_report` and `registration_export`,
  called with their defaults as a client would call them, say `memory_chain_verified: true`. So the Annex
  IV, DPIA/FRIA and registration evidence all certify a foreign-signed chain.
- **Cause:** `mcp_server.py:2172`, `:2191-2192` and `:2212-2213` pass `expected_pubkey` through instead of
  `_pin(expected_pubkey)`. Fix note: the same argument also goes to the action ledger's `verify()`. That
  ledger is signed with the writer key, not the receipt key, so a fix should pin only the memory side.
- **Test:** `test_the_documentation_tools_honour_the_configured_receipt_pubkey[...]`

### L3 (High): `actions_verify` passes a ledger it cannot read

- **Description** (`mcp_server.py:1563-1566`): "Verify the ACTION LEDGER beside this store ... Recomputes
  every hash, link and signature".
- **What happens:** `<store>.actions.json` holds two incidents and is truncated to half its bytes. The tool
  returns `{ok: true, entries: 0, problems: []}`. The offline `verify_file` on the same file says "cannot
  read". Every register reads the same file as empty.
- **Cause:** `ActionLedger._load` turns an unparseable or non-list file into `[]` (`actions.py:363-365`),
  and an empty chain verifies.
- **Test:** `test_actions_verify_does_not_pass_a_ledger_it_cannot_read`

### L4 (High): a ledger write replaces a ledger it cannot read with a new chain

- **Description:** `record_risk` (`mcp_server.py:1715`): "Append one entry to the risk register". The other
  `record_*` tools "Record ... in the action ledger". `archive_actions`: "Nothing is deleted".
- **What happens:** on the truncated ledger from L3, `record_risk` returns an ordinary success at
  `seq: 0`. It rewrites the file from genesis with only the new entry, and the two incident records are
  gone. With `INSPEXIMUS_ACTIONS=1`, the first call of any tool does the same through the boundary.
- **Cause:** `_load` yields `[]` (`actions.py:363-365`). `record()` then numbers from genesis, and `_save()`
  replaces the file (`actions.py:382-388`).
- **Test:** `test_a_ledger_write_does_not_overwrite_a_ledger_it_cannot_read`

### L5 (Medium): `operator_json` is refused whenever it is a JSON object string

- **Description** (`mcp_server.py:2161`, and the same text in the other two tools): "`operator_json` is a
  JSON object string with the provider's own fields".
- **What happens:** `technical_documentation(operator_json='{"system_name": "Support agent"}')` through an
  MCP session is an error: "operator_json: Input should be a valid string [input_type=dict]". The plain
  Python function accepts the same string. No MCP client can supply operator fields, so every field stays
  OPERATOR INPUT REQUIRED.
- **Cause:** FastMCP decodes any string argument that parses as JSON when the parameter's annotation is not
  exactly `str` (`mcp/server/fastmcp/utilities/func_metadata.py:179`, mcp 1.30.0). `operator_json: str |
  None` (`mcp_server.py:2157, 2176, 2196`) therefore arrives as a dict and fails validation. The same
  mechanism reaches other `str | None` parameters. `record_oversight(decision='{"amount": 100}')` is
  refused. `record_lifecycle(note="null")` quietly stores no note.
- **Test:** `test_the_documentation_tools_accept_operator_json_through_mcp[...]`

### L6 (Medium): after `archive_actions`, three tools refuse entries that are still live

- **Description:** `what_it_knew` (`mcp_server.py:2393`): "What the agent KNEW when it performed action
  number `seq`". `actions_match` and `incident_report` make the same promise for a given seq.
  `archive_actions`: "the chain verifies across the files".
- **What happens:** after seqs 0-2 are archived, the live file holds seq 3 onwards. `what_it_knew(3)` and
  `actions_match(3)` answer "no action #3; the ledger has 3 entries". `incident_report(4)` refuses an
  incident that exists.
- **Cause:** `if seq < 0 or seq >= len(led)` at `mcp_server.py:2148`, `:2386` and `:2398`. After rotation,
  live seqs start at `base_seq`, so the live count is the wrong bound. The library's own lookups are
  correct.
- **Test:** `test_a_live_entry_is_found_after_the_ledger_was_rotated[...]`

### L7 (Medium): `post_market_report` counts a reported incident twice and keeps it overdue

- **Description** (`mcp_server.py:1748-1749`): "the Art. 72 post-market monitoring report ... incidents and
  their clocks".
- **What happens:** one serious incident, 30 days old, is followed by `incident_reported`.
  `incident_report` and `oversight_report` treat it as closed. `post_market_report` says `{opened: 2,
  by_severity: {serious: 2}, overdue: [0]}`.
- **Cause:** `actions.py:816` counts the `incident:reported` follow-up as an incident. `actions.py:827-830`
  reads `reported_ts` off the incident entry itself, instead of using `_reported_ts()` as the other two
  reports do.
- **Test:** `test_post_market_report_counts_a_reported_incident_once_and_not_overdue`

### L8 (Medium): a risk that refers to an entry is not listed as referring to it

- **Description:** `incident_report`: "later entries that refer to the incident". `corrective_action_report`:
  "later entries that refer to it". `decision_explanation`: "the incidents, risks and corrective actions
  that refer to it". `archive_actions`: "anything a kept entry refers to stay[s] live".
- **What happens:** `record_risk(refers_to=[X])` is missing from all three reports, and `archive_actions`
  archives X even though the kept risk refers to it.
- **Cause:** the scans read `refers_to` only when it is a single dict, and otherwise treat `evidence` as the
  list of references (`actions.py:925, 989, 1614, 1909`). A risk stores `refers_to` as a list and
  `evidence` as free text.
- **Tests:** `test_a_risk_that_refers_to_an_entry_is_listed_as_referring_to_it[...]`,
  `test_archive_actions_keeps_live_what_a_kept_risk_refers_to`

### L9 (Medium): a risk with free-text `evidence` breaks four tools

- **Description:** the reports promise "The Art. 73 report skeleton for incident `seq`", "The Art. 20
  record ..." and "The material for an Art. 86 explanation". `record_risk` takes `evidence: list[str]`,
  documented as free references such as "a probe path, a receipt hash, a test name".
- **What happens:** after one `record_risk(evidence=["probes/x.py"])`, `incident_report`,
  `corrective_action_report` and `decision_explanation` raise `'str' object has no attribute 'get'` for
  every earlier entry, and `archive_actions` raises the same error. The error is reported, but the reports
  can no longer be produced.
- **Cause:** the same scan lines as L8 call `.get("seq")` on each evidence string.
- **Tests:** `test_a_risk_with_free_text_evidence_does_not_break_the_reports[...]`,
  `test_archive_actions_rotates_past_a_risk_with_free_text_evidence`

### Holds

Every tool in the family was checked for its return shape, its validation and refusals, and read-only
behaviour where it is described as read-only. What was checked:

- **Refused as described:**
  - A `refers_to` or `evidence` seq that does not exist.
  - An unknown event, severity, measure, audience, practice or role.
  - An Annex VII declaration without a notified body.
  - A notification more than 72 h late without `reasons_for_delay`.
  - `not_applicable` without a basis.
  - A processor without a controller.
  - Art. 14 without a source.
  - A decommission without a disposition.
- **Reports and exports:**
  - Registers give the latest entry per id, with the counts each description lists (for example
    `qms_register.overdue` and `attestation_register.missing`).
  - `export_audit_trail` writes JSONL that passes `verify_jsonl`, with the 12 mandatory fields.
  - `timestamp_actions` on an unreachable URL returns `{"error": "URLError ..."}` and writes nothing.
- **Tool boundary** (with `INSPEXIMUS_ACTIONS=1`): one content-free `mcp:<tool>` entry per call. The
  memory state is captured before the call. A raising tool gives a `status=error` entry together with
  `isError`.
- **Read-only:** `oversight_report`, `risk_register`, `breach_report` and the other reports change nothing
  when the action ledger is off.

---

## Family 6: subject rights, access grants, events and the code guard

Tools: `export_subject`, `record_objection`, `resolve_objection`, `objections`, `rectify_subject`, `grant`,
`revoke`, `grants`, `grant_log`, `can_read`, `recall_as`, `get_as`, `poll_memory_events`,
`subscribe_memory_event`, `deprecate_symbol`, `symbol_status`, `check_code`. Tests:
`tests/test_mcp_review_rights_access.py`.

A correction to the brief's working assumption: the default store is **not** JSON. A new store is written
as rows (SQLite) even when it is named `.json`, and an existing JSON store is converted when it is opened
(`core.py:8651-8672`). The event tools therefore work on the default store.

### S1 (High): an Art. 21 objection recorded through one server is not honoured by another on the same store

- **Description** (`mcp_server.py:1640-1643`): "GDPR Art. 21: record the subject's objection and stop
  serving their records. From this call on, recall withholds every record whose source resolves to
  `subject`, including later writes, until the objection is resolved."
- **What happens:** two server instances run on one store file, which is what two MCP processes, or the
  server and the Claude Code hook, amount to. The server documents that set-up at `mcp_server.py:277-345`.
  An objection recorded through server B makes B withhold the record. Server A goes on returning it until A
  restarts:
  - A's `recall` still returns the record, and a new write through A sourced to the subject is served too.
  - A's `objections()` answers `{objections: [], standing: 0}`.
  - An override on B leaves A withholding, which is the same fault in reverse.
- **Cause:** objections are read from `<store>.objections.json` only in `Inspeximus.__init__`
  (`core.py:2909-2915`). `refresh()` and `_merge_with_disk()` never read it again. Recording an objection
  writes only that sidecar, so the store file's signature does not change, and the per-call `refresh()`
  returns early.
- **Test:** `test_record_objection_is_honoured_by_every_server_on_the_store`

### S2 (High): `rectify_subject` reports a correction the guard retired as done, and logs it as done

- **Description** (`mcp_server.py:1678-1680`): "GDPR Art. 16 rectification: supersede the value under `key`
  with `text` through the ordinary keyed write (every write guard applies), and record who asked and why as
  a rights:rectify entry on the action ledger".
- **What happens:** the tool takes no `object`. On a key whose value was written with one, as `remember`
  asks callers to do, the objectless guard retires every rectification on arrival, and recall keeps
  serving the old value. The tool returns `{key, previous_id, new_id, memory_receipt, previous_status:
  "active", ledger_entry}`. That has no `blocked`, no `policy` and no `status`, so it reads as a landed
  correction. It also writes a `rights:rectify` ledger entry with `status="ok"`. The echo guard behaves the
  same way. The guard applying matches the description. Reporting nothing when it applies does not.
- **Cause:** `mcp_server.py:1684-1685` passes the library result through without `_write_verdict()`.
  `subject_rights.py:214` ignores `store.last_write`, and `subject_rights.py:223-224` records `status="ok"`
  unconditionally.
- **Test:** `test_rectify_subject_reports_a_rectification_the_objectless_guard_retired`

### S3 (High): `subscribe_memory_event`'s default cursor never delivers anything

- **Description** (`mcp_server.py:2612-2615`): "returns the cursor to poll from ({event_type, since_seq})
  ... call `poll_memory_events` with this `since_seq` (and `event_type`) to receive everything published
  after this moment."
- **What happens:** the default subscription returns `event_type: "*"`. `poll_memory_events(since_seq,
  event_type="*")` returns `{events: []}` after every write, indefinitely. The same poll without
  `event_type` returns the events. A tail following the documented recipe reads "no changes" for ever.
- **Cause:** `mcp_server.py:2616` hands back `"*"` (the in-process subscription wildcard, `core.py:4036`).
  `poll_memory_events` passes it on, and `sqlite_store.py:292` filters on it literally, as `AND type=?`.
- **Test:** `test_subscribe_memory_event_default_cursor_receives_the_later_events`

### S4 (High): `deprecate_symbol` returns a deprecation the echo guard retired as "the recorded deprecation"

- **Description** (`mcp_server.py:1524-1529`): "A later deprecate_symbol of the same `old` supersedes the
  replacement. ... Returns the recorded deprecation."
- **What happens:** `old_fn -> new_fn`, then `old_fn -> newer_fn`, then `old_fn -> new_fn` again (a change
  of mind). The third write restates a retired object, so the echo guard retires it on arrival. The tool
  returns `{replacement: new_fn}`, while `symbol_status` and `check_code` keep saying `newer_fn`.
- **Cause:** `code_guard.py:88` ignores `store.last_write`, and `code_guard.py:91` builds the result from
  its arguments. `mcp_server.py:1530` passes it through.
- **Test:** `test_deprecate_symbol_reports_a_deprecation_the_echo_guard_retired`

### S5 (High): rights ledger entries break a signed action ledger

- **Description:** `export_subject` (`mcp_server.py:1624-1625`): "Writes one rights:export entry to the
  action ledger". The module docstring: "one signed, hash-chained entry per call".
- **What happens:** with a writer key and `INSPEXIMUS_ACTIONS=1`, `actions_verify` is ok. One
  `export_subject` later it answers `ok: false` ("seq 2: no signature") for good. A legitimate access
  request leaves the tamper-evidence check permanently failed. `record_objection`, `resolve_objection` and
  `rectify_subject` build the same keyless ledger.
- **Cause:** `mcp_server.py:1631, 1647, 1662, 1685`: `ActionLedger(_MEM, actor=_ACTOR)`. Same as L1; see
  X3.
- **Test:** `test_export_subject_rights_entry_keeps_the_signed_action_ledger_verifiable`

### S6 (Medium): "stop serving their records" holds for recall only

- **Description:** `record_objection`: "record the subject's objection and stop serving their records.
  From this call on, recall withholds ...". The second sentence names recall. The first promises more.
- **What happens:** every recall variant withholds the records: `recall`, `recall_iterative`,
  `recall_followup`, `neighbors`, `recall_as`, `token_report`, `selection_integrity` and the top hits of
  `why_recalled`. Other reads still serve a withheld record's text:
  - `memory_index`, described as "THE ALWAYS-LOADED INDEX", still carries the record's line.
  - `verify_claim` answers `supported` and quotes the record.
  - `check_conflict`, `get`, `get_as` and `why_recalled(id=...)` return it too. Of these, `get` and
    `get_as` need the id, which recall no longer hands out.
- **Cause:** the library filters only recall's candidate pool (`core.py:12727-12733`).
- **Test:** `test_record_objection_stops_serving_the_subject_outside_recall[memory_index|verify_claim]`

### S7 (Medium): `rectify_subject`, `recall_as` and `deprecate_symbol` ignore the server's project scope

- **Description:** `INSPEXIMUS_PROJECT`: "Writes are stamped with it and recalls are filtered to it".
  `recall_as`: "the same ranking as `recall`, hard-filtered to what that agent owns or has an active grant
  for".
- **What happens:**
  - A rectified record carries no project stamp. The subject's corrected value, whose original was
    confined to project alpha, is recalled from project beta.
  - `recall_as(bob)` on a server scoped to alpha returns project beta's granted record. On this server
    `recall_as` is the only recall variant that crosses projects. The ACL itself holds.
  - A deprecation recorded in repo alpha is an unscoped record, so every project recalls it.
- **Cause:** `mcp_server.py:1684`, `:2567` and `:1530` pass no project. `code_guard.py:88` takes no
  project parameter. See X4.
- **Tests:** `test_rectify_subject_stamps_the_servers_project_scope`,
  `test_recall_as_honours_the_servers_project_scope`, `test_deprecate_symbol_stamps_the_servers_project_scope`

### S8 (Medium): on a JSON-format store, `poll_memory_events` reports no events after writes, with no error

- **Description** (`mcp_server.py:2587-2592`): "What changed in the store since `since_seq`, from the
  `memory_events` table ... Another process's write is visible on the next call."
- **What happens:** the store is JSON-format, because it is pinned with `INSPEXIMUS_STORE_FORMAT=json`, is
  encrypted, or failed to convert to rows. After two writes the tool returns `{events: [], tip: 0}` with no
  error and no note, and `subscribe_memory_event` returns `since_seq: 0`. The library's own
  `publish_event` raises on the same store.
- **Cause:** `core.py:3965` and `:4002` return `[]` and `0` when rows are unavailable.
- **Test:** `test_poll_memory_events_on_a_json_store_does_not_read_as_no_changes`

### Holds

| Tool | Checked |
|---|---|
| `export_subject` | Returns direct and inherited records, their provenance and history. Writes one `rights:export` entry carrying the manifest hash. `basis="portability"` gives the Art. 20 form and a `rights:portability` entry. An ambiguous subject comes back as `{"error"}`, and `allow_ambiguous` exports. Store and receipts are unchanged. (S5 aside.) |
| `record_objection` | The server that records it withholds at once, including later writes. A neighbouring subject is not withheld. Persists across a restart. Bad ground, bad scope, no actor, a duplicate or an ambiguous subject comes back as `{"error"}`. (S1 and S6 aside.) |
| `resolve_objection` | `overridden` without grounds, and any override of a direct-marketing objection, are refused. `overridden` resumes recall, and `upheld` keeps withholding. |
| `objections` | `{objections, standing}`. Read-only. |
| `grant` / `revoke` | Two selectors, no selector, or `ids=[]` are refused. Each act adds one receipt, and `verify_writes` stays ok. `was_granted` is correct. A revoke takes effect on the next read and leaves other grants alone. |
| `grants` / `grant_log` | In force only, or every act, newest first. Read-only. |
| `can_read` | `{allowed, reason, via}`, where `via` is a grant id, `"owner"` or `None`. |
| `recall_as` | Fail-closed for an agent with no grants. Quarantined records are held out, and objections are honoured. (S7 aside.) |
| `get_as` | `{}` for an unknown id and for no access, indistinguishably. |
| `poll_memory_events` | Events carry key, status and mtype, never text. `tip` is the highest seq. Another handle's write shows up on the next call. The `event_type` filter works for concrete types. (S3 and S8 aside.) |
| `subscribe_memory_event` | With a concrete type, polling from the cursor returns exactly the later events. (S3 aside.) |
| `deprecate_symbol` | Goes through the keyed write with `object=new`, so the echo guard applies. A new replacement supersedes. Receipts are extended. (S4 and S7 aside.) |
| `symbol_status` / `check_code` | Verdicts as described. Whole-identifier matching, so `old_fn(` and `obj.old_fn` match and `old_fnx` does not. Read-only. |

---

## Family 7: maintenance, conflict checks and store analysis

Tools: `consolidate`, `sleep`, `consolidate_clusters`, `contradictions`, `check_conflict`, `verify_claim`,
`check_self_narration`, `selection_integrity`, `value_by_cohort`, `memory_report`, `index_coherence`,
`identifier_contract`, `check_sources`, `influence_gate_report`, `irreversible_budget_report`, `credit`,
`memory_index`, `set_index_line`. Tests: `tests/test_mcp_review_maintenance.py`.

M1 to M4 share a cause with W4 (X5 in the verdict). An unforced `_save()` within 5 s of the
previous save only marks the handle dirty (`core.py:2764`, `:16284`), and nothing flushes at exit:
`main()` ends in `mcp.run()` (`mcp_server.py:2761`). An end-to-end run through a real stdio server process
shows the loss. `remember`, then `credit(outcome="good")`, returns `updated: [id]`. After the client closes
the session, the store file has no `good` on that record.

### M1 (High): `credit` reports an update that never reaches the store file

- **Description** (`mcp_server.py:957-961`): "call credit(those ids, outcome) so each memory's track record
  updates. Future `recall` then ranks by WAS-IT-RIGHT ... Counts only grow ... Returns what updated."
- **What happens:** the result is `updated: [id]` and memory says `good: 1.0`. A fresh handle on the file
  has no `good`. The outcome signal is lost, and nothing recomputes it.
- **Cause:** `core.py:13889` ends with an unforced `_save()`.
- **Test:** `test_credit_reaches_the_store_file`

### M2 (High): `credit` records a positive number as a **bad** outcome

- **Description** (`mcp_server.py:960-961`): "`outcome`: 'good'/'right'/'correct' vs 'bad'/'wrong'/'failed'
  (or pass a bool / a signed number)."
- **What happens:** the schema is `outcome: string`, so JSON `true`, `1`, `2.5` and `-1` are all rejected.
  The string forms `"1"`, `"+1"` and `"2"` are accepted and recorded as `outcome: 'bad'`, so the record's
  `bad` count goes up. So does any word outside the list, such as "success", "yes" or "ok".
- **Cause:** `mcp_server.py:956` types `outcome` as `str`. `core.py:13828-13834` parses numbers only when
  they arrive as int or float, and treats every other unlisted string as bad.
- **Test:** `test_credit_accepts_a_positive_signed_number_as_a_good_outcome`

### M3 (Medium): `credit` with a negative `weight` shrinks the counts it says only grow

- **Description** (`mcp_server.py:961`): "Counts only grow; raw text is never edited."
- **What happens:** `credit(outcome="bad", weight=2)` then `credit(outcome="bad", weight=-5)` leaves
  `bad = -3`. The same works on `good` and `good_warranted`. A caller can wipe a record's recorded failures,
  which the influence gate reads.
- **Cause:** `core.py:13883` adds `float(weight)` with no check on its sign.
- **Test:** `test_credit_counts_only_grow`

### M4 (Medium): `consolidate`, `consolidate_clusters` and `sleep(keep=...)` report changes they do not save

- **Description:**
  - `consolidate` (`mcp_server.py:866-870`): "(if `keep` is given) supersede the lowest-value surplus".
  - `consolidate_clusters`: "consolidate a semantic cluster only once it has grown past `threshold`".
  - `sleep`: "prunes/re-affirms the memory budget".
- **What happens:**
  - `consolidate(keep=2)` over five records reports `active: 2`. A fresh handle sees five active.
  - A ripe preference flip reports `toggled: 1` in `consolidate_clusters`. On disk the older record is
    still active.
  - `sleep(keep=2)` never saves its budget pass in the call that reports it, however long it has been
    since the last write. `consolidate_clusters()` runs first (`core.py:15437`) and its save resets the
    throttle clock, so `consolidate`'s save (`core.py:15439 -> 15281`) always lands inside the 5 s window.
    `sleep` is meant for idle time, which is exactly when no later write comes along to carry the change
    to disk.
  - With receipts on, `consolidate(keep)` writes its retirement receipts to the sidecar at once while the
    rows stay active on disk. A fresh handle's `verify_writes` still says ok.
- **Cause:** `core.py:15281` and `:15389` end with unforced saves.
- **Tests:** `test_consolidate_keep_budget_reaches_the_store_file`,
  `test_consolidate_clusters_reaches_the_store_file`, `test_sleep_keep_budget_reaches_the_store_file`

### M5 (Medium): `check_sources` is not scoped to the server's project

- **Description** (`mcp_server.py:1211`): "Scoped to the bound tenant/project when there is one."
- **What happens:** on a server with `INSPEXIMUS_PROJECT=a`, `recall` sees only project a's record.
  `check_sources` returns `checked: 2` and names project b's drifted record in `drifted`, which makes the
  verdict `ok: false`.
- **Cause:** the server's project is passed per call and never bound to the store. `mcp_server.py:1212`
  passes nothing, and `core.py:5891` walks every record of the tenant. See X4.
- **Test:** `test_check_sources_is_scoped_to_the_servers_project`

### M6 (Medium): `check_sources` says `ok: true` when nothing was checked

- **Description** (`mcp_server.py:1203-1208`): "`ok` is false whenever NOTHING was checkable, and the report
  says so — zero drifted over zero checked is the same sentence as a clean store."
- **What happens:** records written without a `source` (the default for `remember`) are filed as
  `NOT_BINDABLE`, a bucket the description never mentions. The result is `checked: 0, ok: true`, next to a
  `problem` saying "this verified NOTHING". A writer-name source gives `UNCHECKABLE` and `ok: false`, as
  described.
- **Cause:** `core.py:5913` and `:6099-6100` made this change on purpose (see
  `tests/test_not_bindable_is_not_a_backlog.py`). The tool description was not updated. The fix is probably
  the description.
- **Test:** `test_check_sources_ok_is_false_when_nothing_was_checked`

### M7 (Medium): `verify_claim` says `stale_superseded` with `current: None`

- **Description** (`mcp_server.py:916-917`): "'stale_superseded' (matches a value that has since been
  CORRECTED/reverted — the reply is citing an outdated fact; 'current' is the truth now)".
- **What happens:** a region is corrected from frankfurt to osaka. With `key` and `object`, the answer is
  `stale_superseded, current: 'osaka'`. Without them it is `stale_superseded, current: None`, although the
  retired record's key has a current value.
- **Cause:** `core.py:16047`. The path without a key does not look up the retired record's key.
- **Test:** `test_verify_claim_stale_superseded_names_the_current_value`

### M8 (Medium): `memory_report` and `selection_integrity` overwrite the recall-window observation

- **Description:** `memory_report` (`mcp_server.py:2476`): "Read-only". `selection_integrity`
  (`mcp_server.py:939`): "(read-only, no LLM)". `INSPEXIMUS_OBSERVE_RECALL` (`mcp_server.py:35-36`):
  "record which memories were served immediately before each write, as an observation".
- **What happens:** with `INSPEXIMUS_OBSERVE_RECALL=1`, run `recall` (serves D), then `memory_report()`,
  then a write. The write's `recall_window` holds the ids of whatever `memory_report` sampled, not D.
  `selection_integrity` does the same, and returns none of the ids it puts there. The false observation
  lands with the next write, with a fresh timestamp.
- **Cause:** `core.py:15050` and `:16077` call `recall` with the default `observe=True`. The library has
  `observe=False` "for a maintenance sweep" (`core.py:13340-13352`).
- **Tests:** `test_memory_report_leaves_the_recall_window_alone`,
  `test_selection_integrity_leaves_the_recall_window_alone`

### M9 (Medium): `irreversible_budget_report` never changes after its first call

- **Description** (`mcp_server.py:2468-2470`): "Audit view of the per-source lifetime IRREVERSIBLE-influence
  budget: how much durable pull each source has spent against its cap".
- **What happens:** the first call answers `{}`. Another handle then spends 0.7 of 1.0. The server still
  answers `{}`, while a freshly started one shows the spend. No MCP tool spends this budget, so every spend
  the report could show comes from another handle.
- **Cause:** `core.py:14385` loads the sidecar once. `refresh()` never reloads it.
- **Test:** `test_irreversible_budget_report_shows_a_spend_made_after_its_first_call`

### Holds

| Tool | Checked |
|---|---|
| `consolidate` / `sleep` / `consolidate_clusters` | Raw text and record count unchanged. `keep=N` retires the lowest-value records. A polarity flip retires the older side. A second immediate `sleep` does no new work (15 randomised stores). Report keys present. (M4 aside.) |
| `contradictions` | Pairs `{a, b, a_text, b_text}`. Resolves nothing. Read-only. |
| `check_conflict` | A pure duplicate returns `[]`, keyless or with the same object. Numeric, negation and keyed changes are flagged. Read-only. |
| `verify_claim` | All five verdicts are reachable. Shape `{verdict, current, matched}`. Read-only. (M7 aside.) |
| `check_self_narration` | `{self_narration, markers}`, matching whole words. |
| `selection_integrity` | Shape as described. Without a trust root it says so. Store bytes unchanged. (M8 aside.) |
| `value_by_cohort`, `index_coherence`, `identifier_contract`, `influence_gate_report` | Shapes as described. Read-only. |
| `memory_report` | Counts and the duplicate estimate are present. Store bytes unchanged. (M8 aside.) |
| `check_sources` | FRESH, DRIFTED, ORPHANED and UNCHECKABLE all reached. Drift or orphan gives `ok: false`. (M5 and M6 aside.) |
| `credit` | `warrant` reaches the library and raises `good_warranted`. A warrant equal to the record's own source does not. (M1, M2 and M3 aside.) |
| `memory_index` | Never drops a record. A budget too small is reported as exceeded. `needs_line` and `limits` present. |
| `set_index_line` | Empty and whitespace-only lines are refused. The line persists on disk, and `verify_writes` stays ok. |

---

## Observations that are not description mismatches

These were found along the way. Either no tool's description promises otherwise, or the behaviour is a
documented library choice. They are listed because each one changes how a result should be read.

- **Two ways to report an error.** 45 tools report a failure inside a successful result, as
  `{"error": ...}` with `isError: false`. The rest raise, which reaches the client as `isError: true`. A
  client that checks only `isError` misses the first kind. With `INSPEXIMUS_ACTIONS=1`, the boundary logs a
  `{"error": ...}` result as an action with status `ok`.
- **FastMCP's JSON pre-parse.** This is L5's cause, and it reaches every `str | None` parameter. A string
  argument that happens to parse as JSON is decoded before validation. So
  `record_oversight(decision='{"amount": 100}')` is refused, and `record_lifecycle(note="null")` stores no
  note.
- **No trust root on this server.** `selection_integrity` can never give a verdict over MCP, for the same
  reason as R3. Even with every write attested through `INSPEXIMUS_WRITER_KEY`, its note still tells the
  user to "attest writes".
- **`forget(where_contains=...)` crosses projects.** On a project-scoped server it matches and deletes other
  projects' records. Its description promises no project scoping.
- **Rights and `record_*` tools write the ledger even when it is off.** They write `<store>.actions.json`
  with `INSPEXIMUS_ACTIONS` unset. Meanwhile `incident_reported`, `archive_actions`, `record_lifecycle` and
  four more refuse when it is off. So an incident recorded over MCP with the ledger off can never be marked
  reported.
- **`verify_audit_bundle(store_path=<legacy JSON store>)` changes the file.** It converts the file to rows
  in place and leaves a `.pre-rows.bak` beside it. That is the library's convert-on-open, applied to the
  auditor's evidence file.
- **The audit bundle carries metadata.** It is content-free of record text, but carries grant keys, agent
  ids and request ids, and keys can themselves hold identifiers.
- **`check_conflict` with a key and no object.** On a key whose records carry objects, it flags even a
  verbatim duplicate as `keyed_value_unchecked`. The library does this on purpose; the description does not
  mention it.
- **`value_by_cohort` counts access grants.** It counts ACL grant rows as untagged memories.
- **Throttled saves also affect receipts.** `consolidate(keep)` inside the throttle window writes its
  retirement receipts to the sidecar at once, while the rows stay active on disk.
- **Environment flags are parsed two ways.** `INSPEXIMUS_ACTIONS` accepts `1/true/yes` but not `on`
  (`mcp_server.py:366`). `_flag_from_env` accepts `on`, and its docstring says it is "One spelling rule for
  every on/off environment flag this server reads". `INSPEXIMUS_READ_RESOLVER` accepts only `1` (`mcp_server.py:667`).
- **Head files in `~/.config`.** A receipted store records its chain head under `INSPEXIMUS_KEY_HOME`,
  which defaults to `~/.config/inspeximus/heads`. The shared harness now points it into `tmp_path`. Before
  that change, runs of this review left head files for their throwaway `/tmp` stores in the container's
  `~/.config`. Those files were removed.

## Reproducing

```bash
pip install "mcp[cli]>=1.28,<2" cryptography pytest pytest-xdist
python -m pytest tests/test_mcp_review_*.py            # 98 xfailed
python -m pytest tests/test_mcp_review_*.py -n 0 --runxfail -q   # each fails on its own assertion
```

`tests/_mcp_review.py` holds the shared harness. It reloads `inspeximus.mcp_server` on `tmp_path/store.json`
with every server environment variable cleared, points the key home into `tmp_path`, and calls tools through
an in-memory MCP client session.
