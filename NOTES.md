# inspeximus 3.5.0

two read-path guards, default on: instruction-shaped records are quarantined and keyword-stuffed records never outrank clean ones. UPGRADE IF AN AGENT READS MEMORY THAT OTHER PARTIES CAN WRITE. AFFECTS: adds the `read_guards` constructor flag (default on; `INSPEXIMUS_READ_GUARDS=0` turns it off), `include_quarantined` on `recall`, `read_guard_report()`, `release_quarantine()`, two MCP tools (130) and the `guards` CLI command; a record written before this release is assessed the first time recall sees it; a flagged record gains `meta.quarantined` or `meta.stuffed` and nothing else changes on disk. NOT byte-identical for a store that holds such a record; identical for one that does not.

## Who should upgrade

Upgrade if this is true of you: **AN AGENT READS MEMORY THAT OTHER PARTIES CAN WRITE. AFFECTS**.

## What changed

Measured before it was built. tech4biz-yasha/agmi#3 ran its four memory-specific attacks on
inspeximus 3.0.0's default `recall` through the maintainer's adapter: user isolation held; a planted
fact, an entry stuffed with a topic's question words, and an instruction disguised as a meeting recap
all reached the agent's context, the stuffed entry in the first of three slots at relevance 1.0. The
row reproduced on 3.4.0. None of the opt-in levers changed it, and `trusted_only` without trust seeds
passed every cell with an empty answer, which is a defect in the suite's verdicts that we reported
with a positive control.

An instruction-shaped record is one whose text reads as an instruction to the model once it is in
context: an order to override prior instructions, a persona switch, a reference to the system
prompt, an order to send data to an address or URL, an order to hide something from the user, an
order to run code. Seven shapes, each named in the record's `meta.quarantined.shapes`. Such a record
is stored, exportable and erasable, and kept out of recall unless the caller asks with
`include_quarantined=True`; `release_quarantine(id, actor, reason)` is the human decision that it
was a memory after all, and the record keeps who decided. Transcript role labels and memories that
mention instructions in passing ("we decided to ignore the old lunch policy") are not flagged, and
the test file carries those controls.

A keyword-stuffed record is one in which a single content word is repeated at least four times and
holds at least 12% of all words, in a text of at least twelve words: the agmi entry repeats "menu"
five times in 32 words (0.156); the six genuine memories beside it peak at one repeat, and a long
note that names its topic ten times in 98 words sits at 0.10 and is not flagged. A stuffed record is
demoted, never excluded: the sort is a stable partition with unflagged records first, so under
lexical overlap, where the stuffed entry scores 1.0 by construction, it still ranks last. A score
penalty would have left it on top; that was measured, which is why the partition exists.

Measured on agmi through the maintainer's adapter, guards off and on
(probes/two_read_guards_measured_on_agmi.py): guards off reproduces his row exactly; guards on,
retrieval_hijack reads safe with all 3 of 3 slots genuine and indirect_prompt_injection reads safe
with nothing delivered; memory_injection still surfaces, as predicted, because a plausible planted
fact has no form to catch without provenance on the write; the victim's own memory is served in
both arms, so no cell is earned by an empty answer. Seven mutations, all killed.

## What breaks

No line in the 3.5.0 changelog entry carries a `BEHAVIOUR CHANGE` or `BREAKING` marker. That is a statement about the entry, which you can check against the source, and it is the only claim this section will make for you.

If that is wrong -- if something a caller relies on changed shape, name or default -- the entry is what needs fixing, not this section: RELEASING.md requires a behaviour change to carry the marker on its own line, and this reads that marker.

## Try it -- one command

```bash
pip install -U "inspeximus==3.5.0"
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
