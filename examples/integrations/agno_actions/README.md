# agno action receipts

Every tool call an [agno](https://github.com/agno-agi/agno) agent makes is written into the inspeximus
action ledger. Each entry is signed and hash-chained, and it records what the call was based on: the
memory state the agent had when it acted, and the facts its last recall returned. A verify script
checks the whole run offline and fails on a one-byte edit.

Pinned to **agno 3.0.11** (`requirements.txt`). It runs without an API key: the model is a scripted
stub, and agno still runs the tools and the hooks exactly as it would with a real model.

## How this builds on the agno cookbook example

agno ships an inspeximus example in its own cookbook,
[`cookbook/11_memory/integrations/inspeximus_integration.py`](https://github.com/agno-agi/agno/blob/main/cookbook/11_memory/integrations/inspeximus_integration.py),
merged in [agno-agi/agno#10146](https://github.com/agno-agi/agno/pull/10146). This example continues
that one and does not repeat it.

| | cookbook example (agno#10146) | this example |
|---|---|---|
| question it answers | what does the agent know? | what did the agent do, and what was that based on? |
| inspeximus feature | keyed correction (`remember(key=)`), `revert()` | the action ledger (`inspeximus.actions.ActionLedger`) |
| agno feature | `dependencies` added to the context | `tool_hooks`, the agent's tool-call middleware |
| tools | none | `recall_memory`, `run_migration`, one hook over both |
| scenario | `staging-db`: db-3, corrected to db-7 | the same fact and correction; the agent runs a migration before and after it |
| evidence left behind | the store's history | a signed, hash-chained entry per tool call, bound to the store's receipt chain |
| verification | two `assert`s in the script | `verify.py`: exit 1 on a one-byte edit; 28 tests |
| model | `OpenAIChat()`, needs an OpenAI key | `ScriptedModel`, no key; any agno model plugs in |

The cookbook example shows that a corrected fact stays corrected in what the agent is told. This one
shows the other side of that correction: the migration the agent ran on db-3 *before* the correction
carries a receipt saying db-3 was the current fact at that moment. So an auditor can tell "acted on a
stale value" apart from "acted on what was current then".

agno also has `@approval(type="audit")`. It writes a row to the agent's database after a
human-in-the-loop decision on a tool marked that way. It is not a record of every tool call, and the
row is not signed, not chained, and not bound to what the agent remembered.

## Run it

From the repository root:

```bash
pip install -r examples/integrations/agno_actions/requirements.txt
pip install -e .
python examples/integrations/agno_actions/run.py --out agno_actions_run
python examples/integrations/agno_actions/verify.py agno_actions_run
```

```
agent, before the correction: Done: migration 0042 applied on db-3.internal
agent, after the correction:  Done: migration 0042 applied on db-7.internal

seq 0  lifecycle:start  store e3b0c44298fc, 0 records
seq 1  tool:recall_memory({"query": "Run the pending migration on the staging database."})  ok
       based on: store bae762120df8, 1 record, receipt e6dcbee67392
       recalled: nothing since the previous entry
seq 2  tool:run_migration({"database": "db-3.internal"})  ok
       based on: store bae762120df8, 1 record, receipt e6dcbee67392
       recalled: 050aaebb30 'The staging database is db-3.internal', superseded now, replaced by 'The staging database is db-7.internal'
seq 3  tool:recall_memory({"query": "Run the pending migration on the staging database."})  ok
       based on: store 306a8370338c, 2 records, receipt b327bab1ac73
       recalled: nothing since the previous entry
seq 4  tool:run_migration({"database": "db-7.internal"})  ok
       based on: store 306a8370338c, 2 records, receipt b327bab1ac73
       recalled: 6b9d600e4d 'The staging database is db-7.internal', active now
seq 5  lifecycle:stop  store 306a8370338c, 2 records

4 tool calls, 6 entries in agno_actions_run/memory.json.actions.json
check it: python examples/integrations/agno_actions/verify.py agno_actions_run
OK: 6 ledger entries in agno_actions_run: chain, signatures, memory binding, store receipts and the agno transcript all verify
```

Digests and ids differ on every run. The ledger holds only digests. The arguments and fact texts
printed above come from agno's transcript and from the store.

The signing key is created by `receipt_key_for()` outside the run directory (in the per-user config
directory, or in `INSPEXIMUS_KEY_HOME`). So copying the run directory does not copy the key needed
to re-sign it.

## Wiring it into your own agent

```python
from agno.agent import Agent
from agno.models.openai import OpenAIChat
from inspeximus import Inspeximus, receipt_key_for
from inspeximus.actions import ActionLedger
from ledger_hook import ledger_hook          # this directory

store = Inspeximus("memory.json", receipts=True, receipt_key=receipt_key_for("memory.json"))
ledger = ActionLedger(store, actor="agno:my-agent")

agent = Agent(model=OpenAIChat(id="gpt-4o-mini"), tools=[...], tool_hooks=[ledger_hook(ledger)])
```

agno runs every tool call through `tool_hooks`, including a call answered from agno's tool cache. So
one hook covers every tool, whether it is a plain function, a toolkit method or an `async def` under
`arun()`. The model is not involved in recording anything; swapping `ScriptedModel` for a real
model changes nothing in the ledger path.

## One entry

```json
{
 "kind": "action", "seq": 2, "prev": "394a87a7…",
 "actor": "agno:ops-agent", "action": "tool:run_migration", "status": "ok",
 "inputs_sha256": "7df7a892…", "output_sha256": "b291b383…",
 "memory_state": {
  "digest": "bae76212…", "records": 1, "last_receipt": "e6dcbee6…", "receipts": 1,
  "recalled": ["050aaebb30"], "recall_scope": "this handle", "recall_before_previous_entry": false
 },
 "model": "scripted-stub", "principal": "ops@example.com", "session": "staging-maintenance",
 "meta": {"framework": "agno", "run_id": "353c49ac-…", "tool_call_id": "call_643a97c4…"},
 "hash": "707ea129…", "sig": "c5c562c4…", "pubkey": "44737432…"
}
```

| field | from | meaning |
|---|---|---|
| `inputs_sha256`, `output_sha256` | the hook | salted SHA-256 of the arguments the tool ran with and of the result as text (what agno puts into the tool message). The salt sits in `<ledger>.salt`; `ActionLedger.matches()` proves a transcript against them |
| `memory_state.digest`, `records` | the store | the store's state when the call **started** |
| `memory_state.last_receipt`, `receipts` | the store | the tail of the store's signed write-receipt chain at that moment; `ActionLedger.verify()` checks it is really there |
| `memory_state.recalled` | the store | ids of the facts the last recall returned before this call. `what_it_knew(seq)` resolves them to their text and current status |
| `model`, `principal`, `session` | agno | `agent.model.id`, `run_context.user_id`, `run_context.session_id` |
| `meta.run_id`, `meta.tool_call_id` | agno | the run, and the id of the model's tool call; the hook reads it from the assistant message that issued the call |

## What verify.py checks

1. **The ledger file and the store's receipt file are byte-exact.** JSON parsing forgives a changed
   space. It also forgives a changed last digit of a 17-digit timestamp, which often parses to the
   same float, so a hash over the parsed value cannot see that edit. Re-serialising and comparing
   bytes can.
2. **The chain**: every hash, every link, and every Ed25519 signature against the pinned public key.
   Every entry's memory binding against the store's receipt chain (`ActionLedger.verify`).
3. **The store**: every record matches its signed write receipt (`verify_writes`).
4. **The transcript** (agno's `RunOutput.tools`): byte-exact, with exactly the expected fields. Each
   tool call pairs with one ledger entry by run id and tool call id, and its arguments and result
   match that entry's digests. A call agno ran that the ledger does not hold fails, and so does a
   ledger entry agno never reported.
5. **Every tool entry says what it was based on**: a store digest and a bound receipt.
6. **The store is as the run left it.** Its state digest (ids, status, timestamps, keys, content)
   equals the one recorded by the closing `lifecycle:stop` entry. Write receipts cover content, not
   status. Without this check, a superseded fact could be flipped back to active unnoticed.

### Measured

- The ledger, the receipt file, the transcript, the salt and the pinned key: **every byte position of
  every file** was edited twice, once by flipping its low bit and once by swapping it for a byte of
  the same kind (digit for digit, hex letter for hex letter, space for tab, newline for space). That
  second kind keeps the file valid JSON. On the run above, 11,589 positions and 23,178 edits:
  **every one fails verification.** The restored files verify again.
- The store is SQLite, so most of its bytes are page layout and free space rather than records. Every
  byte of every live record's `text`, `key`, `id` and `status` was flipped: **every one fails.**

## Limits

- **A missing tail is not detected.** Whoever holds the signing key can drop the last entries and
  re-sign them; `ActionLedger.verify()` says the same about itself. An independent witness over the
  tail closes this (`Inspeximus.anchor()`, `docs/TRANSPARENCY.md`); this example does not set one up.
- **The key file only proves the files agree with each other.** `ledger.pub` sits in the run
  directory. In production, pass `--pubkey` from somewhere the ledger's writer cannot edit.
- **Store bookkeeping is out of scope.** `last_access`, `value`, `tags`, `links` and `meta`, and
  SQLite page bytes, are covered neither by the write receipts nor by the state digest. Recall
  rewrites `last_access` on every call anyway.
- **`recalled` covers one store handle.** It lists what recall returned through this process's
  store handle. A recall made through another process (an MCP server, a second adapter) is not
  visible, and the entry says so (`recall_scope: "this handle"`).
- **Streamed tool output is not recorded.** A tool that returns a generator is streamed after the
  hook returns, so its output digest is `null`.
- **A process killed mid-tool leaves no entry.** The entry is written when the tool returns or
  raises.
- Not tested here: tools paused for human confirmation and resumed with `continue_run`, and teams.

## Tests

```bash
python -m pytest examples/integrations/agno_actions -q
```

28 tests, about 30 s with the repository's default `-n auto`, no network. They fail if the
installed agno is not the pinned version. They are not part of `tests/` and CI does not run them.

The tests cover:
- one signed entry per tool call, whether sync, an `async def` tool under `arun()`, streamed, a
  `Toolkit` method, or answered from agno's tool cache;
- a tool that raises: one entry with status `error`;
- each migration naming the fact it was based on, with the first one's fact superseded afterwards;
- the exhaustive one-byte sweeps above;
- an agent run without the hook, a dropped transcript row, and an argument rewritten in clean
  canonical form: each fails verification;
- the two scripts run as subprocesses, as a reader would run them.

| file | what it is |
|---|---|
| `ledger_hook.py` | the agno tool hook: one ledger entry per tool call |
| `stub_model.py` | `ScriptedModel`, an agno `Model` that needs no key |
| `run.py` | the scenario: remember, act, correct, act again |
| `verify.py` | the offline check; exit 0 or 1 |
| `test_agno_actions.py` | the tests |
| `requirements.txt` | `agno==3.0.11`, `cryptography` |
