# inspeximus 3.5.2

a write the store could not persist says so where the caller looks, and a SQLite lock held by a client outside inspeximus is retried and named. UPGRADE IF ANY OTHER PROGRAM OPENS YOUR STORE FILE, OR IF MORE THAN ONE PROCESS WRITES IT. AFFECTS: `store.last_write` gains `persisted` (and `persist_error` when False) after every save; `retire()` returns the same two fields; the MCP `remember` and `remember_decision` results and the CLI `remember --json` output carry them, and the CLI exits 4 on a write that did not reach disk; a row write that meets `database is locked` is retried up to `INSPEXIMUS_SAVE_RETRIES` (default 2) more times; `INSPEXIMUS_BUSY_TIMEOUT_S` overrides the 10 s busy timeout; the inter-process lock key ignores the case of the path. Byte-identical for every store.

## Who should upgrade

Upgrade if this is true of you: **ANY OTHER PROGRAM OPENS YOUR STORE FILE, OR IF MORE THAN ONE PROCESS WRITES IT. AFFECTS**.

## What changed

Crew OS, 2026-09-22, on the live 23.7 MB row store with 14 processes open on it: `remember()`
returned an id, `last_write` read `blocked: False`, the record was not on disk, and the only
witness was `_persist_error`, a private field, holding "OperationalError: database is locked".
Five calls in a row failed that way and the sixth landed. Their pattern is a fresh handle per
write, so a transient failure became a permanent loss: the record lived in a handle that was
dropped, and nothing retried it.

Two things were wrong on our side. The failure branch of `_save` recorded the error, marked the
store dirty for the next save and let `flush()` raise, as 1.54.0 designed, but it never told the
write's own verdict: `last_write` was stamped before the save and read as landed after it failed.
Now the branch stamps `persisted: False` and `persist_error` there, the success branch stamps
`persisted: True`, and every surface that reports a write reports it. The second: the error said
nothing about where the lock came from. The writer already holds the inter-process store lock, so
a `database is locked` at that moment belongs to a client outside it, which on that machine is
the reporter's own tooling opening raw sqlite3 connections to the store. A raw connection in
Python's default isolation keeps a write transaction open until commit or close, and a script that
sleeps between statements holds the file for as long as it sleeps. Readers do not cause it:
measured on a copy of the same store with 12 processes opening a fresh handle in a loop, 354 loads
in 90 s beside 40 keyed writes, 0 persist errors, 40 of 40 landed
(probes/database_is_locked_under_many_readers.py). The row write is now retried a bounded number
of times, the wait stays under the peer's lock budget, and the surviving error names the cause.

The busy timeout and the retry count are environment knobs for an operator who has measured a
longer foreign hold; the store lock key now passes the path through `normcase`, because two
spellings of one path on a case-insensitive filesystem hashed to two lock files, which is no lock.
Seven mutations, all killed. Nothing on disk changes.

## What breaks

No line in the 3.5.2 changelog entry carries a `BEHAVIOUR CHANGE` or `BREAKING` marker. That is a statement about the entry, which you can check against the source, and it is the only claim this section will make for you.

## Try it -- one command

```bash
pip install -U "inspeximus==3.5.2"
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
