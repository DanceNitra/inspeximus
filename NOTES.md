# inspeximus 3.6.0

the two partial rows of the coverage matrix close: the Art. 15 robustness measurements are carried into compliance_report() as dated evidence rows with the receipt sha, and Art. 17 has a signed QMS register; a deletion made outside the library can be declared so the write chain reads accounted for. UPGRADE IF YOU HAND compliance_report() OR THE DEPLOYER REPORT TO AN ASSESSOR, OR IF verify_writes() REPORTS A RECORD DELETED OUT-OF-BAND. AFFECTS: `compliance_report()` gains `robustness_evidence` and a `probes_dir` argument, and its AI Act Art. 15 control carries the rows (status `STALE_EVIDENCE` when a receipt no longer hashes to its packaged sha); `robustness_evidence()` is new, with `INSPEXIMUS_PROBES_DIR`; the package ships `robustness_evidence.json`; the ledger gains the `qms` entry kind, `record_qms()` and `qms_register()`, the deployer report a `17_quality_management_register` duty, `post_market_report()` a `qms_procedures` count; the store gains `declare_out_of_band_deletion()`; three MCP tools (133) and the CLI subcommands `actions qms` and `actions qms-register`. The default store is byte-identical.

## Who should upgrade

Upgrade if this is true of you: **YOU HAND compliance_report() OR THE DEPLOYER REPORT TO AN ASSESSOR, OR IF verify_writes() REPORTS A RECORD DELETED OUT-OF-BAND. AFFECTS**.

## What changed

The matrix said 36 of 36 with two footnotes, and a footnote is a row that is not closed. Art. 15
said the poisoning and split-view measurements lived in `probes/` and were not carried into the
report; Art. 17 said the library stores the QMS records but the QMS is the provider's. Both are
now closed by what the footnote asked for, and the two footnotes that remain (Art. 43 and Art.
72: the conformity assessment and the post-market plan are acts and documents of others) stay,
because a footnote that states a fact is not a defect.

Art. 15. `tools/gen_robustness_evidence.py` reads three receipts in `probes/` and writes
`inspeximus/robustness_evidence.json`, which the wheel ships: the echo panel (a re-asserted
stale value is retired on arrival under the default policy, `echo_blocked 1.0`, measured
2026-07-27), the AgentPoison influence gate (`raw_hijack` 0.875 to 1.0 at the retriever and
`influence_hijack` 0.0 once the gate decides what may drive a response, three dense retrievers,
2026-07-26), and a split view (two histories served to two readers proven from one witness's two
signatures, with the honest pair as the control; a new probe, 2026-09-22). Each row carries the
probe path, the receipt's sha256 and the date. `compliance_report()` reads the rows and, when the
receipts are reachable (a source checkout, or `INSPEXIMUS_PROBES_DIR`), re-hashes each: `verified`,
or `STALE` with the current hash beside the packaged one, in which case the Art. 15 control reads
`STALE_EVIDENCE` and the coverage row for Art. 15 drops out of EVIDENCE. On an installed wheel with
no receipts in reach the rows read `packaged`. The numbers are read from the receipts by the
generator and pinned by a test against their source, never typed. The control is a test that
copies the receipts, changes one value, and reads STALE on the row, the control and the coverage
probe; the first version of that test stubbed the probe and the mutation survived.

Art. 17. `record_qms(actor, procedure, version, owner, review_due_ts, aspect, ref, sha256)` is one
signed ledger entry per procedure of the provider's quality management system; `aspect` is the
Art. 17(1) letter it covers, (a) to (m), validated. `qms_register()` names the current entry per
procedure, the ones overdue for review at `now`, and which letters have a current procedure. The
deployer report lists the register as a duty and `post_market_report()` counts the procedures.
The QMS is still the provider's; what the ledger holds is the signed record that it exists, who
owns it and when it was last confirmed current.

A deletion made outside the library. Measured on the Crew OS store 2026-09-22: two records that a
receipt vouched for had been removed with a raw SQL DELETE, `verify_writes()` reported them as
"deleted out-of-band", and `forget()` on an id that is already gone erased nothing and wrote no
tombstone, so the chain could never read as accounted for. `declare_out_of_band_deletion(id, actor,
reason)` appends the tombstone the deletion should have carried, with the actor and the reason
inside the committed hash and the basis marked `out_of_band`. It is the operator's declaration, not
evidence of what was deleted; it is refused while the record is present or when no receipt names
it. Twelve mutations, all killed.

## What breaks

No line in the 3.6.0 changelog entry carries a `BEHAVIOUR CHANGE` or `BREAKING` marker. That is a statement about the entry, which you can check against the source, and it is the only claim this section will make for you.

If that is wrong -- if something a caller relies on changed shape, name or default -- the entry is what needs fixing, not this section: RELEASING.md requires a behaviour change to carry the marker on its own line, and this reads that marker.

## Try it -- one command

```bash
pip install -U "inspeximus==3.6.0"
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
