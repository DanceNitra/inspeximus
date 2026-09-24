# How do I detect that someone edited my agent's memory file?

## Short answer

Turn on write receipts when you create the store: `Inspeximus(path, receipts=True, receipt_key=...,
receipt_pubkey=...)`. Each write then appends a signed receipt to a sidecar file
(`<store>.receipts.json`). The receipt is hash-chained to the previous one and commits to the
record's content and part of its metadata. The library also keeps the chain's head in a file under
your user config directory, outside the store's directory.

`verify_writes(expected_pubkey=...)` checks the chain and the signatures, and compares every stored
record with its receipt. It reports three things: a record whose content no longer matches its
receipt, a record deleted outside the library, and a chain shorter than the head kept outside the
store. Changes made through the library, such as a keyed correction, verify clean.

## Example

Needs the `cryptography` package for the signing keys: `pip install "inspeximus[crypto]"`. Run it in
an empty directory. It creates `memory.json` and its sidecar files there, and a small head file
under your user config directory.

```python
import json
import sqlite3
from pathlib import Path

from inspeximus import Inspeximus, new_receipt_keypair

sk, pk = new_receipt_keypair()
m = Inspeximus("memory.json", receipts=True, receipt_key=sk, receipt_pubkey=pk)
wire = m.remember("Wire transfers above 10000 EUR need two approvers", key="policy::wire")
dana = m.remember("The on-call engineer this week is Dana", key="oncall")
priya = m.remember("The on-call engineer this week is Priya", key="oncall")   # a correction, via the API
m.flush()
print("after normal use:", m.verify_writes(expected_pubkey=pk))

# Someone edits the files directly, outside the library. The store file is SQLite.
con = sqlite3.connect("memory.json")
doc = json.loads(con.execute("SELECT doc FROM records WHERE id = ?", (wire,)).fetchone()[0])
doc["text"] = "Wire transfers above 100000 EUR need two approvers"
con.execute("UPDATE records SET doc = ? WHERE id = ?", (json.dumps(doc), wire))   # edit a record
con.execute("DELETE FROM records WHERE id = ?", (dana,))                           # delete one
con.execute("DELETE FROM records WHERE id = ?", (priya,))                          # delete the newest...
con.commit()
con.close()
receipts = Path("memory.json.receipts.json")
receipts.write_text(json.dumps(json.loads(receipts.read_text(encoding="utf-8"))[:-1]),
                    encoding="utf-8")                                              # ...and its receipt

# Open the store again, as the agent would on its next start.
m = Inspeximus("memory.json", receipts=True, receipt_pubkey=pk)
print("recall serves:", m.recall("wire transfer approvers", k=1)[0]["text"])
ok, problems = m.verify_writes(expected_pubkey=pk)
print("after the edit:", ok)
names = {wire: "<wire>", dana: "<dana>", priya: "<priya>"}   # record ids are random; name them
for p in problems:
    for rid, name in names.items():
        p = p.replace(rid, name)
    print(" -", p)
```

Output:

```text
after normal use: (True, [])
recall serves: Wire transfers above 100000 EUR need two approvers
after the edit: False
 - memory <wire>: its TEXT or KEY no longer matches its write receipt (edited after write)
 - memory <dana>: written but missing from the store (deleted out-of-band)
 - write log shrank below the head kept outside the store: 2 < 3 (rolled back or truncated, receipts included); a deliberate restore is accepted with reanchor_head()
```

What the output shows:

- A correction made through the library does not count as tampering.
- After the edit, `recall()` serves the edited text. Receipts detect an edit; they do not prevent it
  or undo it.
- `verify_writes` names each of the three alterations. The third one removed a record together with
  its receipt, so the receipts file alone looks consistent. It is caught because the head kept
  outside the store's directory records one more write than the chain now holds.

## Limits: what this does not do

- **It detects; it does not prevent or repair.** Run `verify_writes` where you can act on the result,
  for example when the agent starts or in a scheduled job, and decide what happens when it fails.
- **Receipts must be on when a record is written.** Records written while receipts were off have no
  receipt to compare against.
- **Pin the public key.** A signed receipt carries its own public key. Without `expected_pubkey`,
  someone who rewrites the store and the receipts can sign them with their own key, and
  `verify_writes()` passes. Without any signing key, the receipts are a plain hash chain, and someone
  who can rewrite the receipts file can recompute it.
- **Whoever holds the private key can rewrite everything consistently.** Someone with access to the
  whole user account can also delete the head file. For both cases, publish the output of `anchor()`
  to a party you do not control, and check against it later with `verify_consistency()`.
- **The head file is optional.** `INSPEXIMUS_HEADS=0` turns it off. Without it, removing the newest
  records together with their receipts is not reported.
- **It checks only the fields a receipt commits to.** Those are the record's text, key, type,
  `object` value, serving status, `valid_from` and source attribution. An edit to any other field of
  a record is not compared against anything. Check which fields your application relies on.
- **It covers the store and its receipts only.** An embedding index or cache outside the store, and
  the action ledger, are not covered. The ledger has its own `verify()`.

## See also

- [docs/API.md](../API.md): "Governance, erasure & audit" for `anchor()` and `verify_consistency()`.
- [SECURITY.md](../../SECURITY.md): "Known residual footguns".
- [docs/TRANSPARENCY.md](../TRANSPARENCY.md): witnessing the anchor.
