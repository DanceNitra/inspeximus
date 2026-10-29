# inspeximus context engine for Hermes

A Hermes context engine that replaces an oversized tool result with an intent-ranked digest and
the path of the file holding the whole output. Hermes's conversation compaction is untouched:
this takes over one step, the tool-result prune.

It acts on the request, through `select_context`, which Hermes runs before every provider call
and which never touches the stored transcript, so the session database keeps every output in
full. The last 6 messages keep their output whole: that is the window the agent is reasoning in.
`prune_tool_results_only` is overridden as well, but it is not the main path, because Hermes
calls it only when the context is over threshold and compaction is blocked.

## Why

Hermes prunes old tool results with per-tool summarizers. For a tool it has no summarizer for,
the result becomes a byte count, and the agent re-runs the tool to get the content back.

Measured on one real Hermes transcript of 3,233 messages, where tool results are 84.4% of the
bytes. Of 264 results over 2,000 characters, counting the question's own terms that appear only
in the middle of the output, where neither the command echo nor the exit status can carry them:

| strategy | bytes kept | buried question terms kept |
|---|---|---|
| Hermes's prune | 0.5% | 0.0% |
| this engine | 27.3% | 61.5% |
| head and tail truncation, same budget | 27.4% | 54.9% |

The probe is `probes/hermes_prune_vs_our_saver_on_his_transcript.py` in the agora repo, run on
2026-10-29. Its first three metrics all favoured truncation, and each was the instrument rather
than the result: byte counts reward whichever strategy loses the most content, rare-term coverage
rewards contiguity, and question-term survival is circular on a shell result because the output's
head echoes the command. The table above uses the metric that survived those corrections.

Two findings shaped the code:

- Hermes's own pass demotes a digest as readily as raw output, which took 61.5% back to 0.0%.
  The engine spares its own digests on the proactive pass and yields to the pressure pass.
- Padding the budget with a head and a tail lost on the same metric (58.8%), so the digest is
  not padded.

The digest ranks against the arguments of the call that produced the result, which the request
list carries on the preceding assistant message. That choice is worth 20 points: ranking against
the user's last message instead kept 56.1% of the question's terms, the call's own arguments
kept 76.7%. Where the call is missing, the tool's name is the fallback.

Lexical ranking is the default. The embedder reordered 62 of the 264 results and changed nothing
in the outcome, at about half a second each.

## Install

```bash
cp -r integrations/hermes-context-engine-inspeximus "$HERMES_HOME/plugins/inspeximus"
pip install "inspeximus>=3.26.0"
```

Then name the engine in `$HERMES_HOME/config.yaml`:

```yaml
context:
  engine: inspeximus
```

Hermes reads the engine at startup, so restart it after the change. To go back to Hermes's
built-in prune, delete the `context` block.

## What the agent sees

```
[inspeximus] digest of the full output; the file below holds all of it
---
<the ranked sections of the output>
---
full output: C:\Users\...\inspeximus\saver-out\capture-ab12cd34.txt
```

The file holds the output verbatim and is swept on the saver's own schedule, so a follow-up read
costs one tool call instead of re-running the command.

`/status` reports `inspeximus_digests`, `inspeximus_bytes_in` and `inspeximus_bytes_kept`.

## Known limits

- A digest is lost on Hermes's pressure pass, by design. The capture file outlives it.
- A result is digested only once its message is 6 messages old, so the raw output does sit in
  the window for the turn that produced it. Hermes has no hook at capture time; the Claude Code
  integration, which does, digests immediately.
- The tests in `tests/test_the_hermes_context_engine_digests_and_is_spared.py` run against a stub
  parent where Hermes is absent, and against the real `ContextCompressor` wherever Hermes is
  importable. A signature-parity test fails if the contract this engine subclasses is renamed.
