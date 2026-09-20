# inspeximus 3.1.0

retire a key with no replacement; the store lock file goes with the lock on Windows; two false alarms found on a 20,000-store corpus. UPGRADE IF YOU RUN THE SUITE ON WINDOWS, OR NEED TO END A KEY WITHOUT WRITING A NEW VALUE. AFFECTS: adds `retire(key, reason, source=None)`, the `retire` CLI command and the `retire_key` MCP tool (112); `history()` rows gain a `reason` field (None unless the key was retired); on Windows `_StoreLock` removes its file on release; opening a JSON list of non-records raises ValueError instead of AttributeError; `verify_writes()` no longer reports `store not persisted (differs in vec)` on a vector-persisted store opened without the embedder. Nothing existing changes shape.

## Who should upgrade

Upgrade if this is true of you: **YOU RUN THE SUITE ON WINDOWS, OR NEED TO END A KEY WITHOUT WRITING A NEW VALUE. AFFECTS**.

## What changed

`retire(key, reason, source=None)` ends a key: every active value for it in the handle's scope
becomes `superseded` with `meta.superseded_by_policy == "retired"`, the reason in
`meta.retired_reason`, the declaration in the receipt chain, and NO new record. It exists because a
key migration tried `remember(key=k, object="__superseded__")` to end a key and got the opposite: a
keyed write replaces, so the placeholder became the key's new active value. `retract_lineage` is
by source and `forget` erases; nothing ended a key and kept its history. Not a fourth status: the
record reads `superseded`, which every reader and the concealment sweep already handle; what tells
"ended" from "replaced" is the policy, the reason, and the absence of a newer record. A reason is
required. Scoped like a write. The L1 drops the key and the event table records the change.

`_StoreLock` kept one `inspeximus-<hex>.lock` per store path in the system Temp, for ever: 596,291
of them on the machine that runs the suite. On Windows the file is removed on release, safe by the
sharing rules (`open()` sets no FILE_SHARE_DELETE, so the unlink fails while any process holds the
lock and succeeds only when none does; a later opener creates a fresh file every later opener
shares). On POSIX `unlink` succeeds under an open handle, so the file stays there. Tested with a
second process holding the lock for four seconds; the twelve-writers probe lost 0 records in 12
of 12 trials at 12, 24 and 48 processes. The suite itself now writes every temporary file under
pytest's basetemp (tests/conftest.py), which pytest prunes.

Before removing the 422,798 fixture directories the suite had left behind, 20,000 were sampled and
archived (`probes/fixture_corpus_sample_and_census.py`). Residue: 558 of them had run an erasure
and none leaked a secret. Compatibility: 7,880 stores from 2.4x opened with 3.0.0; every refusal
was right except two, fixed here. A file holding `[1, 2, 3]` crashed with AttributeError instead
of the refusal a dict gets. And 117 stores written with `persist_vectors=True` and opened without an
embedder reported `store not persisted (differs in vec)`: a false integrity alarm from comparing a
cache one side keeps and the other never loads. Both sides now drop `vec` when the handle does not
persist it; a real unpersisted edit is still reported.

## What breaks

No line in the 3.1.0 changelog entry carries a `BEHAVIOUR CHANGE` or `BREAKING` marker. That is a statement about the entry, which you can check against the source, and it is the only claim this section will make for you.

If that is wrong -- if something a caller relies on changed shape, name or default -- the entry is what needs fixing, not this section: RELEASING.md requires a behaviour change to carry the marker on its own line, and this reads that marker.

## Try it -- one command

```bash
pip install -U "inspeximus==3.1.0"
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
