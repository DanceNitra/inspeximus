# inspeximus 3.0.0

receipts on an existing store, an event table other processes tail, and an L1 cache inside the core. UPGRADE IF YOU RUN MORE THAN ONE AGENT PROCESS ON ONE STORE, OR HOLD A STORE THAT PREDATES ITS RECEIPTS. AFFECTS: adds `enable_receipts()` and `inspeximus receipts enable`; adds the `memory_events` table, `publish_event`/`poll_events`/`subscribe`/`unsubscribe`/`dispatch_events`/`events_tip`, the `events=` constructor flag, and two MCP tools (111); adds `current(key)` with an L1 behind it, `l1_stats`/`l1_invalidate`/`l1_flush` and the `l1_size=`/`l1_auto_refresh=` constructor flags; `_TenantView` gets a `__repr__`. NOT A BREAKING RELEASE: every 2.x call keeps its signature and its behaviour, and no row of `records` changes shape. The major number moves because the core gains a second table that other processes read, which is a change to what a store IS, not to what a call returns.

## Who should upgrade

Upgrade if this is true of you: **YOU RUN MORE THAN ONE AGENT PROCESS ON ONE STORE, OR HOLD A STORE THAT PREDATES ITS RECEIPTS. AFFECTS**.

## What changed

`enable_receipts(receipt_key=None, backfill_genesis=True, reason="")` turns write receipts on for a
store that already holds records with no chain, or a chain that started part-way, and covers the
records the chain does not name. Each gets an ordinary receipt over the record AS IT STANDS at
backfill time, with a `backfill` field inside its hash carrying the Merkle root of the batch
(`inspeximus.merkle`, RFC 6962), so no reader can mistake it for a receipt made at write time and
stripping the marker breaks the chain link. Retirements that happened while the chain was not
looking (a record born active under receipts, superseded by a writer that had receipts off, which
`verify_writes()` reports as hidden) are declared with the same marker, through the amendment path
every legitimate retirement uses. Idempotent; refuses a second signing key. CLI:
`inspeximus receipts enable --backfill`. Measured on our own store: 2,907 records, 2,030 uncovered
and 2 hidden, `verify_writes()` False; after one call, True, in 1.7 s. The concealment criterion
moved into `_unaccounted_retirements()` so the verifier and the backfill share one definition.

`memory_events` is a table the row writer appends to INSIDE the transaction that writes the rows,
one content-free row per committed change: `record.added`, `record.changed` (a supersession, a
status flip) and `record.removed`, carrying id, key, status and mtype and never text. A second
process tails it by `seq` with `poll_events(since_seq)` and sees another writer's commit on the
next call, with no reload and no broker; `publish_event(type, payload, agent_id)` queues an
application event for the same commit and returns its seq after a forced save. Because the
INSERT shares the transaction, an event exists exactly when its row does: the test fails the row
INSERT from inside the open transaction and requires zero events. On a tenant-bound handle only
that tenant's events return; on an agent-bound handle a record event returns only when `can_read`
allows the record, and an application event only when that agent published it, "system" did, or
`payload["to"]` names it. `subscribe(type, callback)` and `dispatch_events()` are in-process. A
store created before the table gets it on the next open; `events=False` opts out; a legacy JSON
store has no table and `publish_event` says so. MCP: `poll_memory_events`, `subscribe_memory_event`.

`current(key)` returns the record the handle serves for a key, and an LRU inside the core
answers repeat reads. The cache key is (tenant, agent, key), so an entry primed through
`as_agent("alice")` is never served to `as_agent("bob")`: bob's read misses and scans bob's own
scoped view. A keyed write drops that key from every scope, an access-control write drops the
whole cache, a hit is re-validated against the record's status, and a replaced record list
(reload, refresh, forget, retention) resets it through the `_items` setter. With
`l1_auto_refresh` (default on) a read stats the file at most once a second and merges a peer
process's write before answering. Measured at 2,000 records: 0.57 us per hit against 254 us
for the scan it replaces, 443x. Four mutations, one per guard, each killed by its own test.

`as_agent()` now documents why the view is not iterable (Python resolves `__iter__` on the type;
read `.items`, which is scoped), how to check one decision with `can_read`, that grants take an
exact selector, and that `remember(agent_id=...)` stamps attribution and grants nothing. The
view prints as `<Inspeximus view: tenant=..., agent=...>`.

## What breaks

- receipts on an existing store, an event table other processes tail, and an L1 cache inside the core. UPGRADE IF YOU RUN MORE THAN ONE AGENT PROCESS ON ONE STORE, OR HOLD A STORE THAT PREDATES ITS RECEIPTS. AFFECTS: adds `enable_receipts()` and `inspeximus receipts enable`; adds the `memory_events` table, `publish_event`/`poll_events`/`subscribe`/`unsubscribe`/`dispatch_events`/`events_tip`, the `events=` constructor flag, and two MCP tools (111); adds `current(key)` with an L1 behind it, `l1_stats`/`l1_invalidate`/`l1_flush` and the `l1_size=`/`l1_auto_refresh=` constructor flags; `_TenantView` gets a `__repr__`. NOT A BREAKING RELEASE: every 2.x call keeps its signature and its behaviour, and no row of `records` changes shape. The major number moves because the core gains a second table that other processes read, which is a change to what a store IS, not to what a call returns.

## Try it -- one command

```bash
pip install -U "inspeximus==3.0.0"
```

No server, no API key, no database, no LLM on the write path. A correction, and the retired value
staying retired:

```python
from inspeximus import Inspeximus, regex_extractor

m = Inspeximus(path="demo.json")
m.extractor = regex_extractor                            # deterministic subject/predicate keys
m.remember("The staging database is db-1.internal.")
m.remember("The staging database is db-7.internal.")     # a correction, not a second fact
print([h["text"] for h in m.recall("staging database", k=3)])
# -> ['The staging database is db-7.internal.']
```

For the MCP server (the one part that has a dependency): `pip install -U "inspeximus[mcp]"` and point
your client at `inspeximus-mcp`. In Claude Code: `/plugin marketplace add DanceNitra/inspeximus` then
`/plugin install inspeximus@inspeximus`.

## Check it yourself

Nothing above asks you to take our word for it:

- `pip download inspeximus` -- PyPI records a signed attestation binding the wheel to this repository,
  this workflow and this commit. Built by GitHub OIDC; no API token exists anywhere.
- `python -m pytest tests/ -q` in a clone -- the suite that had to be green before this was tagged.
- `python tools/release_check.py` -- the pre-release checklist itself, including the example above.
- `inspeximus residue --root ./your-deployment --value <a value you deleted>` -- points at ANY store,
  not just ours, and exits non-zero if the bytes are still there.

Full detail, including what we got wrong and had to correct: [CHANGELOG.md](
https://github.com/DanceNitra/inspeximus/blob/main/CHANGELOG.md).
