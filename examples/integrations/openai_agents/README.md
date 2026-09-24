# OpenAI Agents SDK: memory in inspeximus, every tool call in the action ledger

A minimal [OpenAI Agents SDK](https://openai.github.io/openai-agents-python/) agent whose memory is
inspeximus, with every tool call recorded in the inspeximus action ledger alongside what it was based on,
and a script that verifies the ledger afterwards and fails if one entry is changed.

**Pinned SDK version: `openai-agents==0.22.3`** (the newest release on 2026-09-24, needs Python ≥ 3.10).
Everything here was measured against that version. `requirements.txt` carries the same pin, and the tests
check that the two agree.

```bash
pip install -r requirements.txt          # openai-agents==0.22.3 and inspeximus[crypto]
python agent.py                          # stub model: no API key, no network
python verify_ledger.py                  # OK, exit 0
```

**If the same environment runs inspeximus's MCP server**, install the two together:
`pip install "inspeximus[crypto,mcp]" "openai-agents==0.22.3"`. The SDK accepts `mcp>=1.19,<3`, and on
its own pip gives it mcp 2.2.0, where `mcp.server.fastmcp` no longer exists, so `inspeximus-mcp` fails
to import. Installed together, pip settles on mcp 1.30.0, which both accept (measured 2026-09-24). This
example does not use MCP itself.

The agent runs three turns: it sends Ana invoice INV-1001, it records that her billing email changed, and
it sends INV-1002. The output ends with one line per ledger entry:

```
  seq 0  tool:recall_memory   ok      memory f014f8cb5979 receipt bccd264da8ba  outputs seen []  recalled -
  seq 1  tool:send_invoice    ok      memory 33af57a81e0a receipt bf3b6ec169ae  outputs seen [0]  recalled 88255756a7 'ana@old-mail.example' (superseded now)
  seq 2  tool:remember_fact   ok      memory 0363d7fadb7a receipt 88009da51c11  outputs seen [0,1]  recalled -
  seq 3  tool:recall_memory   ok      memory 9c09b8131a81 receipt 247a8db7ac16  outputs seen [0,1,2]  recalled -
  seq 4  tool:send_invoice    ok      memory 1eb11e4a2e6d receipt 747b8b6dbd6f  outputs seen [0,1,2,3]  recalled 28f4107193 'ana@new-mail.example' (active now)
```

`recalled` is what memory returned before the call, and the status is that record's status now.
`outputs seen` lists the entries whose outputs the model had in its input. Digests and ids differ on
every run.

The first invoice went out on a value that has since been superseded, and the ledger shows it.

## The files

| file | what it is |
|---|---|
| [`agent.py`](agent.py) | the agent: three tools (`recall_memory`, `remember_fact`, `send_invoice`), an `InspeximusSession` for the conversation, and a stub model |
| [`ledger_hooks.py`](ledger_hooks.py) | `InspeximusLedgerHooks`, a `RunHooks` subclass that writes one signed ledger entry per tool call; this is the integration |
| [`verify_ledger.py`](verify_ledger.py) | the verifier: exit 0 when every check holds, 1 with the list of problems otherwise |
| `requirements.txt` | the pin |

The tests live with the rest of the suite, in
[`tests/test_the_openai_agents_example_ledgers_every_tool_call.py`](../../../tests/test_the_openai_agents_example_ledgers_every_tool_call.py).

## Memory is inspeximus

One store holds both kinds of memory, with Ed25519-signed write receipts (`receipts=True`, and the key
from `receipt_key_for`, which keeps it outside the data directory):

- **The conversation** is an `InspeximusSession`, which fills the SDK's `Session` slot the way
  `SQLiteSession` does. The SDK writes every turn into it verbatim, tool calls and their outputs included,
  and replays the turns into later runs.
- **The facts** the agent acts on are keyed inspeximus records. `recall_memory` reads them
  (`recall(where={"kind": "fact"})`, so conversation turns never answer a lookup), and `remember_fact`
  writes a correction under the same key, which supersedes the old value so recall stops returning it.

## What each entry says the call was based on

`InspeximusLedgerHooks` goes in the `hooks=` argument of `Runner.run`, the way
`InspeximusActionCallback` goes into LangChain's callbacks and the way the MCP server wraps its own tool
boundary. Each entry carries:

| field | what it records |
|---|---|
| `memory_state` | captured when the tool started: the store's state digest, the tail of its signed receipt chain, and the ids the last recall on this store handle returned (for `send_invoice`, the email record it was sent on) |
| `meta.based_on` | the earlier ledger entries whose tool outputs were in the model's input when it asked for this call, as `{seq, hash}` references |
| `model`, `meta.response_id` | the model that asked for the call, and the response it came in |
| `inputs_sha256`, `output_sha256` | salted digests of the arguments and of the output the model was given (content-free by default; the salt sits beside the ledger in `*.salt`) |
| `principal`, `session`, `actor` | who the agent acted for, which conversation, which agent |

## What the verifier checks

`python verify_ledger.py [--dir agent_data] [--pubkey HEX]` runs five checks. A failure in the first two
stops it, because a chain that does not verify makes every later answer about it meaningless.

1. **Form.** The file is byte for byte what `ActionLedger` writes for its own content. This catches the
   one edit no parser sees: a space swapped for a tab between two tokens.
2. **Chain.** Every hash is recomputed, every `prev` link and `seq` is followed, and every signature is
   checked against one pinned public key.
3. **Memory.** The store's receipt chain verifies under the same key, and each entry names the receipt
   that was the chain's tail at the moment it recorded.
4. **Based on.** Every tool entry has a memory digest, and every `based_on` reference resolves to an
   earlier entry with that exact hash.
5. **Transcript.** The session transcript and the ledger account for the same calls: exactly one entry per
   call, and the digests match the arguments and outputs the transcript holds.

The key is named after the store's absolute path. If you move or copy the data directory, verify with
`--pubkey`: the `pubkey` field of any entry, checked against a copy you kept elsewhere.

### The negative control

```bash
python agent.py
# change one byte: the last letter of the first "send_invoice", e -> d
python -c "p='agent_data/memory.json.actions.json'; b=bytearray(open(p,'rb').read()); b[b.index(b'send_invoice')+11]^=1; open(p,'wb').write(b)"
python verify_ledger.py
```

```
FAIL agent_data/memory.json.actions.json: 1 problem(s)
  - chain: seq 1: hash does not match the entry's content
```

Exit code 1. Flip the byte back and it exits 0 again.

The tests go further. They flip bit 0 of **every byte** of the ledger file, one byte at a time, and the
verifier fails on all of them. The file is about 8,340 bytes (the length moves by a few bytes between
runs), and on the run measured for this README the verifier failed on 8,340 of 8,340. They also swap one
space for a tab, forge an entry and re-hash it without the key, re-sign the whole ledger with a different
key, drop the last entry, and edit the transcript in the store. Every one of those fails, and the
untouched copy beside it passes.

## What the SDK does not allow

Measured on 0.22.3. Each point says what the hooks see and what this example does about it.

- **No `on_tool_error` hook.** A tool that raises is turned into an error string by the SDK's default
  `failure_error_function`, and `on_tool_end` receives that string as an ordinary result. The SDK does
  track that the result is an error, but only in a private attribute it clears before `on_tool_end` runs.
  The workaround is to pass `failure_error_function=hooks.tool_error` on each tool: the entry then records
  status `error` with the exception, and the model still gets the SDK's default message.
- **An unhandled tool exception drops the turn.** With `failure_error_function=None`, `on_tool_end` never
  fires, `Runner.run` raises, and the SDK saves nothing of that turn to the `Session`. Call
  `hooks.flush(result)` in a `finally`: it writes the call with status `error` and `meta.flushed`, so the
  ledger is the only record that the tool ran. The verifier accepts a flushed entry being absent from the
  transcript and reports it separately. Passing the `result` matters when a run stops for approval: the
  calls in `result.interruptions` stay open, because the resumed run decides them.
- **Refused calls never reach the tool hooks.** A call rejected by a tool input guardrail or at approval
  fires no `on_tool_start`. The hooks see it in the model response (`on_llm_end`) and see the refusal in
  the next model input (`on_llm_start`), and record it there with status `not_run`, with the refusal
  message as the output digest. Both refusal paths are tested, and so is an approved call.
- **Hosted tools fire no hook.** Web search, file search, code interpreter, image generation and hosted MCP
  run on the provider's side. They are recorded from the model response that carried them, with
  `meta.executed_by: "provider"` and the returned item as the input digest. What the provider did inside
  the call is not visible to the SDK, so it is not in the ledger. The tests cover web search; the other
  four take the same code path and are not exercised.
- **`on_handoff` has no call id.** The SDK runs the first handoff the model asks for and refuses the rest,
  so that one is matched. The entry is written when the transfer message reaches the next model input.
- **Some tool families get no call id or arguments.** Computer, shell, local shell and apply-patch tools
  are handed a plain `RunContextWrapper`. Their entries carry the tool name, the output and the memory
  state, and no input digest. This example uses none of them, and the tests do not exercise them.
- **`recall_scope` is one store handle.** The ledger's `recalled` field sees recalls made through the
  store object the hooks were given. A recall made through another process or another handle is not
  attributed.

## What this does not prove

- The operator who holds the signing key can rewrite the store and the ledger consistently, and nothing in
  these files shows it. Only a copy of the chain's tail held by someone else does:
  `ActionLedger.timestamp_tail()`, or `anchor()` co-signed by independent witnesses.
- `--model gpt-...` runs the same agent on a real model through the SDK's default provider and needs
  `OPENAI_API_KEY`. The tests never exercise that path: they use the stub only (`agents.testing.ScriptedModel`,
  the SDK's own test double, answering from a small policy that reads the address out of the
  `recall_memory` output it was given).

## Running the tests

From the repository root:

```bash
pip install -e ".[crypto]" "openai-agents==0.22.3" pytest pytest-xdist
python -m pytest tests/test_the_openai_agents_example_ledgers_every_tool_call.py
```

Without the SDK installed, every test that needs it is reported as skipped by name. The one that checks
the pin still runs.

No test needs an API key. Every environment variable that starts with `OPENAI_` is removed before the
example runs.
