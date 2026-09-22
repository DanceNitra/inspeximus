# inspeximus 3.6.1

a receipt holder on another machine can check inclusion: the service serves the leaf; a static publish without the service key completes. UPGRADE IF YOU RUN THE HOSTED TRANSPARENCY SERVICE OR PUBLISH ITS STATIC COPY. AFFECTS: SCRAPI gains `GET /entries/{id}/leaf` (application/json, the exact bytes the tree hashed; 200 or 404); `tools/publish_static_log.py` without `INSPEXIMUS_SERVICE_SECRET` writes the head, the leaves, the key set, the verifier and the page with zero receipts instead of raising on the first entry; `probes/register_against_the_hosted_log.py` is the acceptance client for a hosted service. The default store is byte-identical; the library is untouched.

## Who should upgrade

Upgrade if this is true of you: **YOU RUN THE HOSTED TRANSPARENCY SERVICE OR PUBLISH ITS STATIC COPY. AFFECTS**.

## What changed

Both found on 2026-09-22, the day the service went live on its own host (`deploy/RUNBOOK.md`).

A receipt's payload is detached and the leaf carries fields the service assigned, its clock and the
index, so a client that holds only its statement and the receipt could verify the signature and the
proof's arithmetic but never the inclusion of its own entry: the first registration from another
machine read "no leaf supplied: inclusion NOT checked". The leaf holds digests, an issuer and a
subject and no payload, so serving it discloses nothing the receipt did not already commit to. With
it, `cose.verify_receipt(receipt, verify, leaf_data=leaf, expected_root=root)` verifies against the
root the key set publishes, and the leaf of a different entry does not (the test's control).

The static publisher's startup note said that without a key receipts are read from the log rather
than re-signed; the loop then asked the no-op signer for one and raised on the first entry, so the
service's first cron run wrote `head.json` and `log.jsonl` and died before `verify.py` and
`index.html`. A receipt needs the service key by definition; the copy without them is still the
head, the leaves and the key set, which is what a witness reads, and it now completes and verifies.
Three mutations, all killed.

## What breaks

No line in the 3.6.1 changelog entry carries a `BEHAVIOUR CHANGE` or `BREAKING` marker. That is a statement about the entry, which you can check against the source, and it is the only claim this section will make for you.

## Try it -- one command

```bash
pip install -U "inspeximus==3.6.1"
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
