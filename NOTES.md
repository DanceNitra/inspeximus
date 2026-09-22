# inspeximus 3.6.2

witnessing a log you do not operate is now `pip install inspeximus` and one command. UPGRADE IF YOU WITNESS A TRANSPARENCY LOG, OR WANT TO INVITE SOMEBODY TO WITNESS YOURS. AFFECTS: the witness logic moves from `tools/witness_static_log.py` into the package as `inspeximus/witness_log.py`, reached by `inspeximus witness watch --url <log> --state <file>`; the tool keeps working and now wraps it; a log that cannot be READ exits 1 with one line instead of a traceback, and leaves the remembered head untouched; HTTPS uses certifi's bundle when certifi is installed, the platform's trust store otherwise. No dependency is added. The default store is byte-identical; the library is untouched.

## Who should upgrade

Upgrade if this is true of you: **YOU WITNESS A TRANSPARENCY LOG, OR WANT TO INVITE SOMEBODY TO WITNESS YOURS. AFFECTS**.

## What changed

The invitation was the defect. A witness is the one part of a transparency log its operator cannot
run, so the ask goes to strangers, and until today the ask was "clone our repository and run a
script out of `tools/`". Three invitations were about to go out with that in them.

`inspeximus witness watch` is dispatched before the CLI's Ed25519 check, because watching needs no
key: an unsigned run still remembers the head it saw, and remembering is the half that refuses.
Signing only makes the observation checkable by a third party. `deploy/witness-template.yml` drops
its `curl` step and pins the version, so a witness runs the code they read.

Two failure modes that a stranger would have blamed on our server. A TLS or network failure raised
a traceback and exited 1 from deep inside urllib; it now prints one line, says that an unreadable
log is not a verdict, and does not touch the state file, because exit 2 means REFUSED and that is a
claim about the publisher. And the Windows Store build of Python 3.12 rejected a current Let's
Encrypt chain with "certificate has expired" on the day this shipped, while curl on the same machine
accepted it: `certifi`, if it happens to be installed, is now the trust store.

Measured against the live service at `https://92.5.74.17.sslip.io/log`: FIRST_CONTACT then EXTENDS
over 9 entries, exit 0 both times, with no key and no checkout.

## What breaks

No line in the 3.6.2 changelog entry carries a `BEHAVIOUR CHANGE` or `BREAKING` marker. That is a statement about the entry, which you can check against the source, and it is the only claim this section will make for you.

## Try it -- one command

```bash
pip install -U "inspeximus==3.6.2"
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
