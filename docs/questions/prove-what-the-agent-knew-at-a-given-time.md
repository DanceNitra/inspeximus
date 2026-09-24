# How do I prove what my AI agent knew at a given time?

## Short answer

Record the agent's actions in an `ActionLedger` attached to the memory store. Each entry is
hash-chained to the previous one, and signed when the store has a receipt key. It also carries the
store's state at that moment: a digest of the store, the tail hash of the store's write-receipt chain,
and the ids that the last `recall()` returned.

Three calls answer the question afterwards:

- `what_it_knew(seq)` resolves those ids to the records.
- `verify(expected_pubkey=...)` checks the chain, the signatures, and the binding to the store's write
  receipts.
- `matches(seq, inputs=..., output=...)` checks a transcript you kept against the salted digests in
  the entry.

To ask what the store held for one key at a moment `t`, use `as_of(key, t, as_recorded=t)`. Without
`as_recorded`, `as_of` answers a different question: what is known now to have been true at `t`. That
answer includes corrections recorded after `t`.

## Example

Needs the `cryptography` package for the signing keys: `pip install "inspeximus[crypto]"`. Run it in
an empty directory. It creates `memory.json`, the ledger `memory.json.actions.json` and their
sidecar files there. With receipts on, the library also writes a small head file under your user
config directory.

```python
import json

from inspeximus import Inspeximus, new_receipt_keypair
from inspeximus.actions import ActionLedger

sk, pk = new_receipt_keypair()
m = Inspeximus("memory.json", receipts=True, receipt_key=sk, receipt_pubkey=pk)
led = ActionLedger(m, actor="support-agent")      # appends to memory.json.actions.json

KEY = "policy::refund-limit"
m.remember("Refunds up to 100 EUR need no approval", key=KEY, valid_from="2024-01-01T00:00:00Z")
m.recall("refund approval limit", k=1)
acted = led.record("tool:refund", inputs={"order": 4711, "eur": 80}, output="approved")
t = acted["ts"]                                   # when the action was recorded

# Later, someone records a correction and back-dates it to before the action.
new_id = m.remember("Refunds up to 50 EUR need no approval", key=KEY, valid_from="2024-02-01T00:00:00Z")

# 1. What had the agent recalled when it acted (ledger entry 0)?
for p in led.what_it_knew(0)["recalled_now"]:
    print("entry 0 recalled:", p["current"]["text"])
    print("  status today:", p["current"]["status"])
    print("  text matches its signed write receipt:", p["integrity"]["content_matches_receipt"])

# 2. Two different questions about the same moment:
print("true at t, as known now:  ", m.as_of(KEY, t)["text"])
print("held by the store at t:   ", m.as_of(KEY, t, as_recorded=t)["text"])

# 3. Is the ledger intact, signed by the key you expect, and bound to the store's write chain?
print("ledger verifies:", led.verify(expected_pubkey=pk))

# 4. Is the transcript you kept the one the ledger digested?
print("kept transcript:", led.matches(0, inputs={"order": 4711, "eur": 80}, output="approved"))
print("altered transcript:", led.matches(0, inputs={"order": 4711, "eur": 90}, output="approved"))

# Control: rewrite entry 0 so it claims the agent had recalled the corrected policy.
entries = json.loads(led.path.read_text(encoding="utf-8"))
entries[0]["memory_state"]["recalled"] = [new_id]
led.path.write_text(json.dumps(entries), encoding="utf-8")
ok, problems = ActionLedger(m, actor="support-agent").verify(expected_pubkey=pk)
print("rewritten ledger verifies:", ok)
```

Output:

```text
entry 0 recalled: Refunds up to 100 EUR need no approval
  status today: superseded
  text matches its signed write receipt: True
true at t, as known now:   Refunds up to 50 EUR need no approval
held by the store at t:    Refunds up to 100 EUR need no approval
ledger verifies: (True, [])
kept transcript: {'seq': 0, 'action': 'tool:refund', 'inputs': True, 'output': True}
altered transcript: {'seq': 0, 'action': 'tool:refund', 'inputs': False, 'output': True}
rewritten ledger verifies: False
```

What the output shows:

- Entry 0 names the record the agent recalled before it approved order 4711. That record has since
  been superseded, and its text still matches the receipt written when it was stored.
- The back-dated correction changes the answer to "what was true at `t`", but not the answer to
  "what did the store hold at `t`".
- The ledger verifies. A kept transcript that differs from the digested one does not match. A ledger
  whose entry 0 was rewritten afterwards does not verify.

## Limits: what this does not do

- **It records what the store returned, not what the model did with it.** By default the ledger
  keeps salted digests of inputs and outputs, not the content. To show the content later you keep the
  transcript yourself, or construct the ledger with `keep_content=True`, and then the ledger file
  holds that content.
- **`matches()` needs the salt file** (`memory.json.actions.json.salt` in the example). Without it,
  no transcript can be checked against the ledger.
- **`recalled` covers one store handle in one process.** A recall made through another handle, such
  as an MCP server running in another process, is not seen, and the entry says
  `"recall_scope": "this handle"`.
- **`what_it_knew` resolves ids against the store as it is now.** If a recalled record has been
  erased since, the entry still holds its id and the state digest, but the text is gone and the
  lookup reports `found: False`.
- **Removing the newest entries is not detected by `verify()`.** The same holds for an operator who
  holds the signing key and rewrites both the ledger and the store's receipt chain consistently. The
  [Article 12 page](eu-ai-act-article-12-logs.md) demonstrates the first case. Detecting either
  needs the ledger's last hash held somewhere the operator cannot rewrite, and compared later.
- **Timestamps come from the machine's clock.** The ledger does not show that the clock was right.
  `timestamp_tail(url)` adds a token from an external timestamp authority to the chain.
- **`as_of` works per key.** A record written without a key has no validity interval to query.

## See also

- [docs/API.md](../API.md): "The action ledger" and "Point-in-time / bi-temporal reads".
