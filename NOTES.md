# inspeximus 3.3.0

AI literacy (Art. 4), prohibited-practice attestations (Art. 5), responsibilities along the value chain (Art. 25), the EU declaration of conformity (Art. 43, 47, 48) and documentation keeping (Art. 18), as signed ledger entries. UPGRADE IF AN ASSESSOR WILL ASK WHO TRAINED THE STAFF, WHO IS THE PROVIDER, OR WHERE THE DECLARATION IS. AFFECTS: adds nine `ActionLedger` methods, nine CLI subcommands under `actions`, and nine MCP tools (121); the ledger accepts five new entry kinds, `literacy`, `attestation`, `responsibilities`, `declaration` and `documentation`; the deployer report gains an Art. 4 section; nothing existing changes.

## Who should upgrade

Upgrade if this is true of you: **AN ASSESSOR WILL ASK WHO TRAINED THE STAFF, WHO IS THE PROVIDER, OR WHERE THE DECLARATION IS. AFFECTS**.

## What changed

`record_literacy()` records one measure taken under Art. 4 as amended (training, guidance,
documentation, briefing, assessment), for which audience (staff, contractors, operators, other
persons acting on the operator's behalf), and which of the article's considerations it took into
account (technical knowledge, experience, education, training, the context of use, the persons the
system is used on). The article asks for measures and guarantees no level for any individual, so
the record carries no score. `literacy_register()` counts by audience and by measure; the deployer
report carries the count under a new `4_ai_literacy` section.

`record_attestation()` is one signed, dated statement per Art. 5(1) class, ten of them with (ba)
and (bb) from the amendment: the system is `not_used` for the practice, or the class is
`not_applicable` with the basis that rules it out, because "not applicable" is the easier claim
and is refused without one. `attestation_register()` shows the latest per class and names the
classes never attested. The ledger records what was attested and when; whether a practice is in
fact absent is not something a ledger can see.

`record_responsibilities()` records the Art. 25(4) written agreement by reference and hash, the
parties with their roles from the article's list, the 25(1) trigger by which a party became the
provider (name or trademark, substantial modification, changed intended purpose), and the 25(2)
items the initial provider made available (technical documentation, known limitations and failure
modes, targeted technical access). At least one party must carry the provider's obligations. The
25(2) opt-out (specified not to be changed into a high-risk system) and cooperation items are
refused together, because the opt-out is what removes the duty.

`record_declaration()` carries every Annex V item: the system's name, type and reference; the
provider or authorised representative; the sole-responsibility and conformity statements; the
data-protection statement where personal data is processed; the harmonised standards or common
specifications; the notified body where Annex VII applied; the place, date and signer. The Art. 43
procedure is a field, and Annex VII is refused without the notified body's name and number, which
Art. 48(4) then puts after the CE marking, so a CE record naming a different body is refused too.
`annex_iv_sha256` pins the technical documentation the declaration rests on.
`declaration_document(seq)` renders it in the Annex V order as the machine-readable document Art.
47(1) asks for, with the ledger hash that binds it. The assessment itself stays the provider's or
the notified body's, and the coverage row says so.

`attest_documentation_retention()` is a signed statement of which Art. 18(1) documents are at the
authorities' disposal: (a) the technical documentation, (b) the quality management system
documentation, (c) changes approved by notified bodies, (d) their decisions, (e) the EU declaration,
each by sha256 or reference, present or not applicable with the reason. (a) and (e) have no
not-applicable case and are required; (c) and (d) may be not applicable when no notified body was
involved; an absent (b) is recorded as a gap rather than refused. The statement carries the end of
the ten-year period from placing on the market and whether the attestation falls inside it, and
links the declaration entry the way `attest_retention` links logs under Art. 19.

`inspeximus coverage` on a fresh store: 33 of 36 in-scope duties covered, 3 not covered (was 28
and 8); every AI Act row is now covered, and GDPR Art. 13/14, 21 and 28 remain. Eight mutations,
all killed by
`tests/test_literacy_attestation_responsibilities_declaration_and_documentation_are_ledger_entries.py`.
This is the release the evidence plan numbered 2.45.0 before the 3.x line began.

## What breaks

No line in the 3.3.0 changelog entry carries a `BEHAVIOUR CHANGE` or `BREAKING` marker. That is a statement about the entry, which you can check against the source, and it is the only claim this section will make for you.

If that is wrong -- if something a caller relies on changed shape, name or default -- the entry is what needs fixing, not this section: RELEASING.md requires a behaviour change to carry the marker on its own line, and this reads that marker.

## Try it -- one command

```bash
pip install -U "inspeximus==3.3.0"
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
