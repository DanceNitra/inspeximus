<!--
THE RELEASE-NOTES TEMPLATE. `python tools/release_notes.py` fills the {{...}} placeholders from
CHANGELOG.md and prints the result; everything outside the placeholders is literal and is edited HERE,
not in the generated file. This comment block is stripped from the output.

Why it exists: the changelog is written for us -- it is a record of what we found and repaired, in the
order we found it. A reader arriving from PyPI or the MCP registry is answering a different question,
"should I install this, and what will it cost me?", and the changelog does not answer it. The release
is the moment they ask: 555 downloads/day on release days against 9 on quiet days, r=0.977, from an
analysis of the public PyPI download series -- reproduced as reported, not recomputed by anything in
this repo (provenance in RELEASING.md). Same text, four frames it does not currently have -- who,
what, what breaks, how to try it.

RULES, enforced by `tools/release_notes.py --check` (which `tools/release_check.py` runs, so a
violation fails the pre-release gate rather than being a matter of taste):

  1. NO UNFILLED PLACEHOLDER. `TODO(...)` in the output is a failure. The generator emits a TODO
     instead of guessing when the changelog does not answer a question -- e.g. it will not invent an
     audience for a release whose heading does not say who should upgrade.
  2. NO CLAIM A READER CANNOT CHECK. A deterministic word list rejects "revolutionary",
     "game-changing", "seamless", "blazing", "industry-leading" and the rest, plus the comparative
     negative ("no other library", "competitors can't"). We are allowed to say what inspeximus does --
     deterministically, zero-LLM, in a single file -- because each of those is a property someone can
     verify. We are not allowed to say what other people cannot do.
  3. THE EXAMPLE MUST RUN, AND ITS OUTPUT IS ASSERTED. The last comment line of the Python block
     starts with `# -> ` and states the expected stdout. The checker executes the block and compares.
     A "30 seconds to try it" example that no longer works is worse than no example.
  4. NUMBERS CARRY THEIR ARTIFACT. Any figure quoted here names the test, probe or command that
     produced it, so the reader can re-run it. If it cannot be re-run, it does not go in.
-->
# inspeximus {{VERSION}}

{{HEADLINE}}

## Who should upgrade

{{WHO_SHOULD_UPGRADE}}

## What changed

{{WHAT_CHANGED}}

## What breaks

{{WHAT_BREAKS}}

## Try it -- one command

```bash
pip install -U "inspeximus=={{VERSION}}"
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
