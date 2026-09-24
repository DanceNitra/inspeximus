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

Status: families 1 and 2 are written up; the remaining families follow as they are finished.

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
