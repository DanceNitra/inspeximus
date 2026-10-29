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

Measured on one Hermes session store holding 3,233 messages across 11 sessions, where tool
results are 84.4% of the message bytes. Of 264 results over 2,000 characters, counting the
question's own terms that appear only in the middle of the output, where neither the command echo
nor the exit status can carry them:

| strategy | bytes kept | mid-output question terms kept |
|---|---|---|
| Hermes's prune | 0.5% | 0.0% |
| this engine | 27.3% | 61.5% |

Scope: inspeximus 3.26.0, measured 2026-10-29 on one operator's sessions on one machine, lexical
ranking with no embedder, and a mid-output term is a word of the tool call's own arguments present
in the output but absent from its first and last 400 bytes. The probe is
`probes/hermes_prune_vs_our_saver_on_his_transcript.py` in the agora repo.

Hermes's prune emits a byte count, so it carries no term from the output at any setting of that
400-byte window. That is the comparison this engine rests on.

## What we tested and could not show

An earlier version of this page also compared the engine with head-and-tail truncation at the same
byte budget, at 61.5% against 54.9%. That comparison is withdrawn. A second probe,
`probes/the_digest_win_survives_its_own_kill_tests.py`, attacked it on three fronts. Two land on
both corpora, the third on one:

- The 400-byte window decides the direction. On this corpus at a 100-byte window truncation wins,
  36.5% against 24.7%; at 400 bytes the digest wins, 19.6% against 7.1%. Claude Code transcripts
  flip the same way, 36.9% against 35.7% at 100 bytes and 29.6% against 8.3% at 400.
- The digest ends with a `Searchable terms:` line, and a substring scorer counts a term printed
  there as kept with none of its content present. Removing that line takes the digest from 19.6%
  to 14.3% here, and from 29.6% to 26.0% on Claude Code transcripts.
- An unranked, evenly spaced sampler at the same budget also scores 14.3% here, so ranking adds
  nothing on this corpus. On Claude Code transcripts ranking does help, 26.0% against 16.6%. Two
  corpora, two answers.

The first probe's sample also included rows the saver returns unchanged, because an output under
5,000 bytes passes through: 146 of its 264 rows gave every strategy the same string and full
credit. The second probe drops those rows for every strategy at once, which is why its percentages
are lower than the withdrawn row's. It scored 118 Hermes rows and 351 Claude Code rows.

Query-aware compression of a long context is published, peer-reviewed work. Jiang et al.,
[LongLLMLingua: Accelerating and Enhancing LLMs in Long Context Scenarios via Prompt
Compression](https://arxiv.org/abs/2310.06839), ACL 2024, and Xu et al.,
[RECOMP: Improving Retrieval-Augmented LMs with Context Compression and Selective
Augmentation](https://arxiv.org/abs/2310.04408), ICLR 2024, both report a query-conditioned
selector beating query-agnostic ones such as BM25 and random sentence selection. Neither runs a
head-and-tail truncation baseline, so neither supports the comparison the withdrawn row attempted.
Position also changes what a model can use at all: accuracy is highest at the start and the end of
a long context and drops in the middle, reported by Liu et al.,
[Lost in the Middle: How Language Models Use Long Contexts](https://arxiv.org/abs/2307.03172),
TACL 2023. Read the withdrawn row as our own failed attempt at a comparison the literature does
not make, not as a finding.

Two findings shaped the code:

- Hermes's own pass demotes a digest as readily as raw output, which took 61.5% back to 0.0%.
  The engine spares its own digests on the proactive pass and yields to the pressure pass.
- Padding the budget with a head and a tail did not help on the same metric, so the digest is
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
