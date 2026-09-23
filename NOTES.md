# inspeximus 3.7.0

checkpoints other people's witnesses can read, an offline Bitcoin-anchor check, the assessor pack, and signers that keep the key outside this process. UPGRADE IF YOU PUBLISH OR WITNESS A TRANSPARENCY LOG, VERIFY AN OPENTIMESTAMPS ANCHOR, OR PREPARE EU AI ACT DOCUMENTATION FROM A STORE. AFFECTS: new modules `inspeximus/checkpoint.py` (signed-note checkpoints and vkeys in the format used by transparency-log witnesses), `inspeximus/witness_checkpoint.py` (cosign another log's checkpoint, refuse when its tree moved), `inspeximus/opentimestamps.py` with `inspeximus ots verify` and `inspeximus ots upgrade`, `inspeximus/signers.py`, and `inspeximus/assessor_pack.py` (`intake_form()`, `assessor_pack()`); `governance_report()` names a tombstone with no request id as "(no request id)" instead of a None key; the two coverage notes for Art. 43 and Art. 72 move from `partial` to `boundary`, and `partial` still carries the same text. No dependency is added. The default store is byte-identical.

## Who should upgrade

Upgrade if this is true of you: **YOU PUBLISH OR WITNESS A TRANSPARENCY LOG, VERIFY AN OPENTIMESTAMPS ANCHOR, OR PREPARE EU AI ACT DOCUMENTATION FROM A STORE. AFFECTS**.

## What changed

**Checkpoints.** The log head is published as a signed note, `origin`, tree size and root, signed
with Ed25519 under a key id derived the way the field derives it. `vkey()` and `verify_note()` read
and write the same format, and a vkey is split on its first two plus signs, because the base64 key
can contain a third. `witness_checkpoint` cosigns somebody else's checkpoint and refuses with a
reason when the tree it remembered is no longer a prefix of the one it is shown.

**`inspeximus ots verify <file> --upgrade --block-header <hex>`** checks an OpenTimestamps proof
without python-bitcoinlib, which crashes on Windows. You supply the block header, so the check runs
offline. Exit codes: 0 ANCHORED, 1 MISMATCH, 3 PENDING or INCOMPLETE. A MISMATCH on a file that git
checked out on Windows is usually line-ending conversion, not tampering: mark hashed files `-text`.

**The assessor pack.** `intake_form()` asks the operator once for the 66 fields only they can
write, each listed with every document it feeds. `assessor_pack()` renders Annex IV, the deployer
report with its DPIA and FRIA appendices, the Annex VIII export for sections A, B and C, the audit
bundle and the IETF trail, and refuses to call itself complete while any field is unanswered. The
gap check runs by two independent routes, the rendered text and each generator's own list, so one
cannot report clean while the other carries a marker. Measured on a 7,696-record store: complete
once the 66 fields are answered. It takes 126 s on that store; a pack-scoped cache is the next
change and is not in this release.

**Fixed.** A tombstone written without a request id became a None dict key in
`governance_report()`, and `json.dumps(sort_keys=True)` raised on it, so the audit bundle and the
pack could not be serialised. A regression test fails when the None key is put back.

**Signers.** `Inspeximus(..., receipt_signer=signer)` takes any callable `signer(hash_hex) ->
sig_hex`, so the key that signs write receipts does not have to sit beside the store.
`VaultTransitSigner` is the first implementation, against HashiCorp Vault's transit engine, which
signs with Ed25519 and never returns the private key.

## What breaks

No line in the 3.7.0 changelog entry carries a `BEHAVIOUR CHANGE` or `BREAKING` marker. That is a statement about the entry, which you can check against the source, and it is the only claim this section will make for you.

If that is wrong -- if something a caller relies on changed shape, name or default -- the entry is what needs fixing, not this section: RELEASING.md requires a behaviour change to carry the marker on its own line, and this reads that marker.

## Try it -- one command

```bash
pip install -U "inspeximus==3.7.0"
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
