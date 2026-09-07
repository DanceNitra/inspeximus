# Migrating from mem0 to inspeximus

A migration tool ships with inspeximus: `migrate_mem0.py`. It does not ask you
to retype your memories — it reads mem0's own operation ledger and rebuilds
your **correction chains** as inspeximus supersession keys, then verifies the
end state against the live export before it reports success.

## What it actually does (three phases)

1. **Read the ledger.** mem0 OSS records every operation in a SQLite
   `history.db`: `history(id, memory_id, old_memory, new_memory, event,
   created_at, updated_at, is_deleted, actor_id, role)` with events
   `ADD` / `UPDATE` / `DELETE` — schema verified against the mem0 OSS source
   (`mem0/memory/storage.py`, `_create_history_table`), audit clone read
   2026-09-07. Events sharing one `memory_id` are a real correction chain.
2. **Replay into inspeximus.** Each chain becomes a deterministic supersession
   key `mem0:{memory_id}`: `ADD` remembers, `UPDATE` supersedes the previous
   value (no similarity threshold, no LLM), `DELETE` hard-forgets. mem0's
   `created_at` timestamps are back-filled as `valid_from`, so the bi-temporal
   order survives the move.
3. **Reconcile and report.** The live export (`get_all()`) is ground truth.
   If a replayed chain ends somewhere other than the live store, the live value
   wins and the drift is written to the report. Chain-less memories (ledger
   pruned) import unkeyed and the report says so instead of guessing.

## Usage

```bash
# 1. export the live store (per user):
python -c "import json; from mem0 import Memory; \
  m = Memory.from_config({...}); \
  print(json.dumps(m.get_all(filters={'user_id': 'u1'}, top_k=1_000_000)))" > current.json

# 2. locate mem0's history DB (whatever path you configured as history_db_path)

# 3. migrate:
python migrate_mem0.py --current current.json --history history.db \
    --store migrated.json --report migration_report.json
```

The report JSON states counts, every reconciliation, and every honest limit.
An end-state test proves the behaviour: `python tests/test_migrate_mem0.py`.

## What survives, what does not (read before you switch)

| | survives migration | does not |
|---|---|---|
| Memory text | yes | |
| Correction chains (dark→light) | yes — as supersession keys | |
| Timestamps | yes — bi-temporal `valid_from` | |
| User/actor/role attribution | yes — in `user_id`/`meta` | |
| Deletion history | yes — deleted chains stay deleted | |
| | | mem0's LLM extraction internals (lemmatized BM25 text, entity graph) |
| | | chains if mem0 ran with the default in-memory history DB (`:memory:`) — the ledger is gone |
| | | a live parity check without mem0ai installed |

mem0 memories are LLM-extracted facts; inspeximus stores raw text. After the
move, behaviour changes by design: corrections are deterministic, not an
LLM coin-flip.

## Why the two systems differ (measured, version-pinned)

Claims here are scoped, not absolute:

- **Revert on an unmarked "go back"** (undo a correction from natural language,
  n=20, native configs, shared ground-truth-blind judge — our integrity
  benchmark, `probes/INTEGRITY_BENCHMARK.md`): inspeximus **0.75** [0.53, 0.89],
  mem0 OSS **2.0.11** **0.20** [0.08, 0.42] — CIs do not overlap. mem0 has
  `update`/`delete` operations (verified in source); what it lacks is a channel
  that restores a previous value on command. CIs are n=20 directional, not a
  leaderboard; run the harness yourself.
- **Echo-resurrection is a tie** — all systems in our benchmark defend against a
  restated stale value. We lead with the cell we do not win.
- API surface quoted in this guide (`add`/`search(top_k, filters)`/`get_all`/
  `update`/`delete`/`delete_all`, ledger schema, `top_k` not `limit`) was read
  from the mem0 OSS source on 2026-09-07, not recalled from memory.

## Steps

1. `pip install inspeximus` (or copy `inspeximus.py` — zero dependencies).
2. Export the live store per user (`get_all`, above) and locate `history.db`.
3. Run `migrate_mem0.py` (above). Review the report; keep it as the migration receipt.
4. Point your agent at inspeximus — same store file, MCP config in the README.
5. Optional parity check (needs mem0ai live): run matched queries through both
   stores and compare top-1. `parity_check()` in the tool does this for you.
6. Verify: `store.verify_writes()` for the tamper-evident chain,
   `store.governance_report()` for erasure/retention posture.

## Honest costs of switching

- mem0's add-time LLM extraction summarises conversations into facts;
  inspeximus keeps what you write verbatim. If you liked the extraction
  behaviour, you keep the extraction in your app layer — inspeximus does not
  do it for you.
- `top_k` vs `limit`: mem0's `search`/`get_all` take `top_k` (a common harness
  bug is passing `limit=` which lands in `**kwargs` and is ignored) — verified
  in mem0's source. If your tooling wrapped mem0, fix the keyword.
- No hosted tier: inspeximus is local-first. There is no cloud API to migrate to.

## Where to get help

Issues: [DanceNitra/inspeximus](https://github.com/DanceNitra/inspeximus) —
include the migration report JSON; it contains everything needed to debug.
