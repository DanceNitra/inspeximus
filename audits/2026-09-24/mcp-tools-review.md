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

Status: families 1-3 are written up; the remaining families follow as they are finished.

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
