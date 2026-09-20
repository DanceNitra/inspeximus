# inspeximus 3.2.0

an opt-in authority rule for keyed writes, measured on MemTX before it was built. UPGRADE IF A WEAKER SOURCE CAN REACH THE SAME KEY AS A STRONGER ONE: AN AGENT NEXT TO A SYSTEM OF RECORD, A TOOL RESULT NEXT TO A HUMAN. AFFECTS: adds the `supersession=` constructor flag (`"lww"`, the default, or `"authority"`) and the `INSPEXIMUS_SUPERSESSION` variable; under `"authority"` a keyed write whose effective `source.authority` is below the incumbent's is retired on arrival with `meta.superseded_by_policy == "keyed_authority"`, and a present but non-numeric authority is refused at the write. NOT A BREAKING RELEASE: the default store is byte-identical to 3.1.0, `source.authority` stays inert under it, and no call changes signature.

## Who should upgrade

Upgrade if this is true of you: **A WEAKER SOURCE CAN REACH THE SAME KEY AS A STRONGER ONE**.

## What changed

`supersession="authority"` runs after the echo guard and before last-write-wins. A write below the
incumbent's authority is held: the record is stored `superseded` with `rejected_authority` and
`retained_authority` in its meta, `last_write` carries the same verdict, the L1 keeps serving the
incumbent, and the receipt chain verifies. Equal goes to the later write; `reaffirm=True` bypasses
the rule. Effective authority is `min(declared, every parent's effective authority)` over
`derived_from`, so a 1.0 summary of a 0.3 rumour is 0.3, and a summary that declares nothing is as
weak as its weakest parent. Authority decides only when BOTH sides declare one: a legacy record
with no authority accepts a declared write, and a declared incumbent accepts an undeclared write,
on last-write-wins. That is the migration rule, chosen over "missing reads as 1.0", which would
have frozen every legacy value against every declared writer below 1.0 the moment the flag went on.

Measured before it was built, and then measured again through the product before it was tagged,
on the MemTX corpus (318 replayable cases, labelled committed beliefs). As a pure replay of the
schedule with no guard: last-write-wins matches the label in 231, the authority rule in 280, and
every one of the 49 disagreements sides with authority. Through a real store per case: the default
matches in 278 and authority in 307, none going the other way. The two tables differ because the
store carries the echo guard and the pure replay does not; with INSPEXIMUS_ECHO_GUARD=0 the store
reproduces 231 and 280 exactly. So through the product the echo guard already decides 50 of the 55
stale_late_write cases on its own (a stale writer restates a value the key has moved away from)
and authority adds nothing there; what authority adds is 29 cases where a weaker source writes a
value the key never held: permission_laundering 48 to 53 of 53, tool_result_pollution 38 to 52 of
52, semantic_conflict 38 to 48 of 54. The assignment that proposed the rule cited "58 of 92
stale_late_write cases decided by authority"; the corpus holds 55, and through the product the rule
decides none of them that the guard had not. The 11 cases still wrong are lost updates between
writers of equal authority writing a fresh value and need a read-snapshot check, which is not in
this release. The rule is also wrong in one direction, MemTX lost_update_0001: a fact the system
seeds at 1.0 can never be corrected by agents writing at 0.8, so last-write-wins serves 47,
authority serves 50, and the label is 48. A test pins that both are wrong so the docstring cannot
outlive the behaviour.

The second item of the assignment, collapsing two records with one `source.doc` into one witness
in the corroboration count, was already the behaviour: `_distinct_sources` has counted canonical
sources since 2.5.1 and a test covers it. Nothing was changed for it.

## What breaks

- an opt-in authority rule for keyed writes, measured on MemTX before it was built. UPGRADE IF A WEAKER SOURCE CAN REACH THE SAME KEY AS A STRONGER ONE: AN AGENT NEXT TO A SYSTEM OF RECORD, A TOOL RESULT NEXT TO A HUMAN. AFFECTS: adds the `supersession=` constructor flag (`"lww"`, the default, or `"authority"`) and the `INSPEXIMUS_SUPERSESSION` variable; under `"authority"` a keyed write whose effective `source.authority` is below the incumbent's is retired on arrival with `meta.superseded_by_policy == "keyed_authority"`, and a present but non-numeric authority is refused at the write. NOT A BREAKING RELEASE: the default store is byte-identical to 3.1.0, `source.authority` stays inert under it, and no call changes signature.

## Try it -- one command

```bash
pip install -U "inspeximus==3.2.0"
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
