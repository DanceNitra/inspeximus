# Why does my agent's memory bring back a fact I corrected?

## Short answer

Because the store holds the old value and the new value as two separate records, and nothing records
that one replaces the other. Retrieval ranks both by how well they match the query. A query that
mentions the old value ranks the old record first. If the old value is written again later (a pasted
transcript, a re-ingested document), the store gains another copy of it.

In inspeximus the link is a key. Write the fact with `remember(text, key=...)`. A later write under
the same key retires the earlier record: it stays in history, and `recall()` stops returning it. If
someone writes a value that was already retired under that key again, the write is retired on
arrival by the echo guard. Going back to an old value is an explicit call: `revert(key)`, or
`remember(..., reaffirm=True)`.

## Example

No extra packages needed. Run it in an empty directory. It creates `plain.json` and `keyed.json`
there.

```python
from inspeximus import Inspeximus

QUESTION = "is the staging database still db-3"

# 1. Without a key, a correction is a second, separate fact.
plain = Inspeximus("plain.json")
plain.remember("The staging database is db-3.internal")
plain.remember("The staging database is db-7.internal")
print("no key:  ", [h["text"] for h in plain.recall(QUESTION, k=2)])

# 2. With a key, the second write retires the first.
m = Inspeximus("keyed.json")
m.remember("The staging database is db-3.internal", key="staging-db")
m.remember("The staging database is db-7.internal", key="staging-db")
print("with key:", [h["text"] for h in m.recall(QUESTION, k=2)])

# 3. Writing the old value again (a pasted transcript, a re-ingested doc) does not bring it back.
m.remember("The staging database is db-3.internal", key="staging-db")
print("restated: blocked =", m.last_write["blocked"], "| policy =", m.last_write["policy"])
print("current: ", m.recall(QUESTION, k=1)[0]["text"])

# 4. Going back is an explicit call, and it is recorded.
m.revert("staging-db")
print("reverted:", m.recall(QUESTION, k=1)[0]["text"])
for row in m.history("staging-db"):
    print("  ", row["status"], row["policy"], row["text"])

# 5. A different key is a different fact. Nothing connects them.
m.remember("The staging database is db-9.internal", key="staging_database")
print("two keys:", [h["text"] for h in m.recall(QUESTION, k=2)])
```

Output:

```text
no key:   ['The staging database is db-3.internal', 'The staging database is db-7.internal']
with key: ['The staging database is db-7.internal']
restated: blocked = True | policy = echo_guard
current:  The staging database is db-7.internal
reverted: The staging database is db-3.internal
   superseded keyed_lww The staging database is db-3.internal
   superseded keyed_reaffirm The staging database is db-7.internal
   superseded echo_guard The staging database is db-3.internal
   active None The staging database is db-3.internal
two keys: ['The staging database is db-9.internal', 'The staging database is db-3.internal']
```

What the output shows:

- Without a key, both values come back, and the one the question mentions (`db-3`) ranks first.
- With a key, only the correction comes back. Writing `db-3` again is blocked by the echo guard, and
  `db-7` stays current.
- `revert()` restores `db-3` as a new active record, and `history()` lists every value the key has
  held, with the policy that retired each one.
- The same fact written under `staging_database` instead of `staging-db` is a separate fact, so both
  come back.

## Limits: what this does not do

- **The key is the only link.** A correction written under a different key, or under no key, does
  not retire anything (step 5). Keys have to be stable across writers. inspeximus does not derive
  keys from free text unless you set the store's `extractor` to a function that returns
  `(key, object)`, and then the result is only as good as that function.
- **It does not decide which value is true.** A later write under a key retires the earlier one,
  with no similarity check and no model call. A wrong correction retires a right value the same way.
- **An earlier `valid_from` does not win.** A write whose `valid_from` is earlier than the current
  value's is treated as a back-fill: it is the back-filled record that gets retired, not the newer
  value.
- **Retired is not erased.** A retired record stays in the store. `history()` returns it, and so does
  `recall(..., include_superseded=True)`. To remove data, see the
  [erasure page](delete-a-users-data-and-prove-it.md).
- **The echo guard can be turned off.** It is on by default. `Inspeximus(..., echo_guard=False)` or
  the environment variable `INSPEXIMUS_ECHO_GUARD=0` turns it off, and then writing a retired value
  again makes it current.
- **Unkeyed records are ranked as text.** An unkeyed note that repeats the old value can still be
  recalled next to the keyed correction.

## See also

- [docs/API.md](../API.md): "Correction is a first-class operation", `revert`, `history`, and the
  echo guard.
