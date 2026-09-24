# inspeximus in a CrewAI crew: agent memory, and a ledger of every tool call

**Tested against `crewai==1.15.22`** (the newest CrewAI release on PyPI when this was written, 2026-09-24), Python 3.11,
with inspeximus 3.9.0 from this repository. The pin is exact on purpose: inside the 1.x line CrewAI
replaced its memory storage protocol and added the tool hooks this example records through, so a range
would promise something nobody has run. `test_the_installed_crewai_is_the_pinned_one` fails when the
installed CrewAI, `requirements.txt`, `tool_ledger.TESTED_CREWAI` and this line disagree.

No API key and no network. The LLM is a scripted stub and the embedder is a hashing function; the test
suite runs the crew with every `*_API_KEY` removed and every outbound socket refused.

## What it shows

1. **A minimal crew whose agent memory is inspeximus.** One agent, one task, two tools. The agent's
   `memory=` is CrewAI's own `Memory`, with `InspeximusMemoryBackend` (from
   `inspeximus.integrations.crewai`) as its storage. What CrewAI remembers, including the result it
   saves after the task, is an inspeximus record under a signed write receipt.
2. **Every tool call in the inspeximus action ledger, with what it was based on.** CrewAI's
   before/after tool-call hooks write one signed entry per call into `memory.json.actions.json`: the
   tool, its arguments and result as salted digests, the store's state digest and receipt tail at that
   moment, the memory records CrewAI's recall put into the prompt for this task, and the earlier tool
   calls of the same task by seq and hash.
3. **A verifier that fails when one entry changes.** `verify_ledger.py` exits 1 on any one-byte edit
   to the ledger, and `negative_control.py` proves it at every byte position.

## Run it

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r examples/integrations/crewai/requirements.txt    # crewai==1.15.22, cryptography, pytest
pip install "inspeximus[crypto]"                                # or: export PYTHONPATH=<this checkout>

python examples/integrations/crewai/crew.py --out run            # the crew, once
python examples/integrations/crewai/verify_ledger.py run         # exit 0
python examples/integrations/crewai/negative_control.py run      # every one-byte edit must fail
pytest examples/integrations/crewai                              # all of the above, and the attacks below
```

`crew.py` prints the tool calls it executed next to the ones in the ledger and exits 1 if they differ.

| file | what it is |
|---|---|
| `crew.py` | the crew: `Agent(memory=TrackedMemory(storage=InspeximusMemoryBackend(...)))`, two tools, one task; writes `run/` |
| `tool_ledger.py` | `CrewToolLedger`, the hooks that record each tool call; `TrackedMemory`, which reports what recall returned |
| `stub_llm.py` | `StubLLM` (a `crewai.llms.base_llm.BaseLLM`) and `stub_embedder` |
| `verify_ledger.py` | the verifier, five steps, below |
| `negative_control.py` | the one-byte sweep |
| `test_crewai_example.py` | the tests; `conftest.py` puts this checkout on the path |

## What one entry says

Abbreviated from a real run (`issue_refund`, the second call):

```json
{
 "seq": 1, "action": "tool:issue_refund", "actor": "support agent", "status": "ok",
 "inputs_sha256": "7d05...", "output_sha256": "204c...",
 "memory_state": {"digest": "cc7f...", "receipts": 2, "last_receipt": "5e17...",
                  "recalled": ["67a4e4fbc7", "59579de2ae"], "recall_scope": "crewai Memory.recall, this task"},
 "meta": {"framework": "crewai", "task": "refund-4711",
          "based_on": {"memories": [{"id": "67a4e4fbc7", "crewai_id": "c334...", "score": 0.6945}, "..."],
                       "earlier_tool_calls": [{"seq": 0, "hash": "1bde..."}]}},
 "prev": "1bde...", "hash": "e566...", "sig": "4944...", "pubkey": "eff4..."
}
```

`67a4e4fbc7` is the refund policy ("support may refund up to 50 EUR"). The stub agent really reads the
limit from its prompt, so the basis is not decoration: remember 30 EUR and the refund is 30 EUR; remember
no limit and there is no refund call at all (`test_the_tool_call_follows_the_memory`). With the salt file,
`ActionLedger.matches(seq, inputs=...)` confirms the exact arguments, and a one-cent change does not match.
`ActionLedger.what_it_knew(seq)` resolves each recalled id through the store's `provenance()`.

## What the verifier checks

1. **Bytes.** The file must be exactly what `ActionLedger` writes, and every hash, signature and key
   lowercase hex. Step 2 alone does not see an edit that leaves the parsed entries equal.
2. **Chain.** `inspeximus.actions.verify_file`: hashes, links, Ed25519 signatures, offline.
3. **Pins.** Entry count and tail hash, from `run.json` or the command line. A chain cannot see its own
   tail cut off.
4. **Memory binding.** `ActionLedger.verify()` binds each entry to the store's receipt chain;
   `verify_writes()` re-hashes every memory against its receipt; the ledger must be signed by the key
   that signs the store's receipts.
5. **Basis.** Every tool entry names its memories, each exists and still matches its receipt, and each
   earlier tool call it cites resolves to that entry. A memory CrewAI erased later through `forget()`
   (its consolidation updates and deletes that way) is reported as a note, because it left a signed
   tombstone; a memory that vanished without one is a failure.

By default the key and the tail are read from `receipt.pub` and `run.json` beside the ledger. That
catches an edit to the ledger, not someone who can also rewrite those two files: keep copies elsewhere
and pass `--expected-pubkey` and `--expected-tail`.

### The negative control, measured

On a run of this example (2 entries, 3,870 bytes), 2026-09-24, crewai 1.15.22, `negative_control.py`:

| edit, at every byte position | edits | verified |
|---|---|---|
| hardest substitution (space to tab, `a-f` to upper case, digit plus one, else low bit flipped) | 3,870 | 0 |
| byte deleted | 3,870 | 0 |
| space inserted | 3,870 | 0 |

Why step 1 exists: on the same ledger, a second sweep of 11,610 substitutions (three per byte: low bit
flipped, case bit flipped, space or tab) run through `verify_file` alone accepted 704. 598 of them
parse to exactly the same entries (590 whitespace changes, 8 digits in the last place of a float that
round to the same number) and 106 upper-case a hex digit of `sig` or `pubkey`, which decodes to the
same bytes, so the signature still verifies. None of them changes what an entry means; every one of
them changes the file, and the verifier here rejects all of them.

The tests also rewrite a remembered policy in the store's database (fails at steps 4 and 5), delete
it from the database outright (fails: no tombstone) versus erase it with `forget()` (verifies), re-sign
the whole ledger with a new key after changing one entry (fails on the pinned key, and without any pin
fails because the store's receipts are signed by a different key), and cut the last entry (fails on
the pin; without the pin it verifies, which is the ledger's documented limit and is asserted as such).

## What CrewAI does not allow, and what this example does instead

- **No hook sees what memory the agent was given.** The tool hooks carry the tool, its input, the
  agent, the task and the crew, but not the prompt or the recalled memories. And CrewAI 1.x never calls
  the store's `recall()`: `Memory.recall()` embeds the query and searches the `StorageBackend` by
  vector, so the ledger's own `memory_state()` would record an empty recall window. `TrackedMemory`
  subclasses `Memory` to report each recall's matches, keyed by `crewai.context.get_current_task_id()`.
- **Hooks are fail-open.** CrewAI swallows every exception a hook raises except `HookAborted`. A
  `HookAborted` in the before hook blocks that one tool call. A `HookAborted` in the after hook, when
  the tool already ran, becomes an `Error executing tool: ...` observation, and the crew carries on.
  The recorder uses both: if the basis cannot be captured the tool is blocked; if the entry cannot be
  written the agent never sees the result, and every later tool call is blocked. No hook can stop the
  crew, so `crew.py` compares the tools' own execution count and CrewAI's `ToolUsageStartedEvent`
  count with the ledger and exits 1 when they differ (`test_a_ledger_that_cannot_be_written_stops_every_later_tool`).
- **Hooks are process-wide.** `register_before_tool_call_hook` applies to every crew in the process.
  The recorder registers only inside `installed()`, and `crew=` makes it ignore other crews' calls.
- **Hook order decides what is recorded.** Global hooks run in registration order, then scoped ones. A
  hook that runs after the recorder's can still change the tool input or the result, so the entry
  holds what the recorder saw. Install it last if other hooks rewrite inputs.
- **The memory pipeline is an LLM pipeline.** Every save asks the LLM to extract, scope and categorise
  the memory and to plan a consolidation; recall of a long query asks it to analyse the query. With a
  real model those are paid calls, and what reaches inspeximus is the LLM's extraction, not the raw
  task output. `Memory()` defaults to an OpenAI model and the OpenAI embedder, so both must be passed
  explicitly to run without a key.
- **No supersession key.** The 1.x `StorageBackend` protocol has no key for a fact, so inspeximus's
  keyed correction ("a corrected fact stays corrected") is not reachable through CrewAI's `Memory`.
  CrewAI's own consolidation updates records through `update()`, which the adapter implements as a
  receipted erase and rewrite.
- **Telemetry and trace collection are on by default.** `crew.py` sets `OTEL_SDK_DISABLED`,
  `CREWAI_DISABLE_TELEMETRY` and `CREWAI_TRACING_ENABLED=false` before importing CrewAI, and passes
  `tracing=False`. The network test is what shows no connection is attempted.
- **Python 3.10 to 3.13.** CrewAI 1.15.22 declares `>=3.10,<3.14`; inspeximus alone runs on 3.9. The
  verifier needs no CrewAI (`test_the_verifier_does_not_need_crewai`).

Not covered: the stub declares no native function calling, so the tests drive CrewAI's ReAct loop.
CrewAI's native tool-calling path calls the same two hooks (`crew_agent_executor.py`), but no test here
exercises it.
