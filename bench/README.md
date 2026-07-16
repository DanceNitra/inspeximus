# MemoryAgentBench — Conflict Resolution, mnemo eval

An external, published benchmark run of mnemo on the **Conflict Resolution (FactConsolidation, multi-hop)**
axis of [MemoryAgentBench](https://arxiv.org/abs/2507.05257) (Hu et al., 2025), where the reported systems
(mem0, MemGPT, Cognee, BM25 RAG, full-context) score single digits. CR = "update outdated information
instead of accumulating", which is exactly what mnemo's keyed supersession does.

This is neutral ground: their data, their metric (`substring_exact_match` with their `normalize_answer`),
one fixed answering model for every arm, so the only thing that differs between arms is the memory layer.

## What it measures

Three arms across four context lengths (6k / 32k / 64k / 262k tokens), same model + same scoring:

- **full-context** — feed the whole ordered fact stream to the LLM ("use the most recent value").
- **single-shot retrieval** — top-k lexical recall then answer (the accumulate-style memory regime).
- **mnemo iterative + supersession** — facts are `remember()`'d keyed by `(subject, relation)` so a later
  restatement supersedes the earlier; then iterative multi-hop retrieval (recall → the LLM names the next
  entity → recall again) over the superseded store.

## Result (deepseek-v4-flash, n=50, substring_exact_match)

| context | facts | full-context | single-shot retrieval | mnemo iterative + supersession |
|--------:|------:|-------------:|----------------------:|-------------------------------:|
| 6k   |   455 | 0.80 | 0.28 | 0.66 |
| 32k  |  2310 | 0.34 | 0.08 | **0.44** |
| 64k  |  4580 | 0.30 | 0.04 | **0.50** |
| 262k | 18332 | N/A (exceeds window) | 0.10 | **0.36** |

Full-context degrades sharply with length and cannot fit at 262k; single-shot retrieval stays in the
reported single-digit regime; mnemo's iterative-retrieval-over-a-superseded-store crosses over full-context
at 32k and is the only working option at 262k. `results_cr_sweep.json` is the machine-readable output.

Honest scope: one answering model (absolute numbers are not comparable to the paper's GPT-4o table — the
fair signals are the per-length deltas and the crossover); the iterative arm spends 2 extra LLM calls per
question; only the multi-hop split is run; `substring_exact_match` is the benchmark's own (lenient) metric.

## Run it

```bash
# any OpenAI-compatible endpoint
export LLM_BASE_URL=... LLM_API_KEY=... LLM_MODEL=...
python run_cr_benchmark.py 50 0,1,2,3     # n_questions, comma-separated row indices (lengths)
```

Files: `memoryagentbench_cr.py` (fact-template parser + keyed-supersession consolidation),
`run_cr_benchmark.py` (the three-arm length sweep), `results_cr_sweep.json` (output).
