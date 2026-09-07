# inspeximus 2.3.0

UPGRADE IF YOU BRANCH ON `recall(with_warrant=...)`: the top tier was settable by the writer

## Who should upgrade

Upgrade if this is true of you: **YOU BRANCH ON `recall(with_warrant=...)`**.

## What changed

Three findings from one afternoon, and they are the same finding: **a mechanism that is correct and
unreached reports SAFE.** Found while drafting a public answer about what our warrant tiers mean —
one hour after we publicly invoked Biba ("integrity requires a label the writer cannot set").

**FIXED — the top warrant tier was writer-settable in one call.** `recall(with_warrant=True)` read

    elif (_good > 0 and _good >= _bad) or r.get("mtype") == "semantic":

and `mtype="semantic"` is an accepted argument to `remember()`. A record written that way reported
`earned` — the strongest tier we expose — with no outcome credit, no corroboration and no lineage. The
influence gate one screen down already refused exactly this and says so in its own comment; the LABEL
was never updated to match the GATE. The semantic clause now requires the `graduated_from_episodic`
marker the code already stamps, and the credit test reads the warrant-gated counter, so
`credit_requires_warrant` reaches the tier instead of being computed and discarded.

**NEW — `writer_key`: a store can sign its own writes.** Measured across every store our deployment
runs — 111,264 records — `attested_key` coverage was **0.0000%**, so `strict_corroboration` (which
counts distinct verified keys) could not fire for a single record anywhere, and `good_warranted > 0`
was 0 of 60,077. The flags were fine; the only way to attest was for the CALLER to hold a keypair and
sign each claim, so nobody ever did, ourselves included. `Inspeximus(writer_key=<hex>)` signs its own
writes; `INSPEXIMUS_WRITER_KEY_FILE` wires it into the MCP server; `inspeximus writer-key --new` mints
one. Honest scope: this attests AUTHORSHIP, not truth. It raises manufactured independence from "type
two different source strings" to "hold two distinct persisted keys" — real cost, not a trust root, and
a process free to mint keys can still mint witnesses. Pin the writers you trust with `trust_seeds`.

**FIXED — attestations are now re-verifiable.** The signature was verified at write time and then
thrown away: records carried a public key and no way to re-check it. `attested_sig` is now stored on
both the explicit and the auto-signed path, so an auditor reading the store later can verify it
instead of trusting the write path's word.

**NEW — no silent masking in the warrant tier.** `warrant` is one scalar and `earned` is tested first,
so a record backed by BOTH channels reported only the outcome one and the corroboration vanished.
`recall(with_warrant=True)` now also returns `warrant_earned`, `warrant_corroborated` and
`warrant_sources`. A record with 24 links but no attestations no longer reads identically to a record
with no links at all.

**NEW — the MCP server can return the tier at all.** `with_warrant` existed in the library and appeared
nowhere in `mcp_server.py`; the default projection kept only `{id, text, score, value, tags}`, so the
tier was uncomputable and then unrepresentable for every MCP consumer, `full=True` included. The tier
exists so a low score is not read downstream as a weak "yes" — a state the caller cannot obtain does
not do that job.

Defaults unchanged: `strict_corroboration` and `credit_requires_warrant` both stay opt-in. Flipping
them on today would move all 111,264 records to `unwarranted` and delete both positive tiers — the gap
is coverage, not the default. Write attestations first, then flip, and re-measure both on the same day.

## What breaks

No line in the 2.3.0 changelog entry carries a `BEHAVIOUR CHANGE` or `BREAKING` marker. That is a statement about the entry, which you can check against the source, and it is the only claim this section will make for you.

If that is wrong -- if something a caller relies on changed shape, name or default -- the entry is what needs fixing, not this section: RELEASING.md requires a behaviour change to carry the marker on its own line, and this reads that marker.

## Try it -- one command

```bash
pip install -U "inspeximus==2.3.0"
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
