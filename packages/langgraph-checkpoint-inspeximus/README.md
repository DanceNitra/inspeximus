# langgraph-checkpoint-inspeximus

A LangGraph checkpointer backed by [inspeximus](https://pypi.org/project/inspeximus/).

```bash
pip install langgraph-checkpoint-inspeximus
```

```python
from langgraph.checkpoint.inspeximus import InspeximusSaver

graph = builder.compile(checkpointer=InspeximusSaver(path="threads.jsonl"))
```

Passes LangGraph's own conformance suite (`langgraph-checkpoint-conformance`) at **FULL 5/5** on the
base capabilities — `put`, `put_writes`, `get_tuple`, `list`, `delete_thread` — and the suite runs in
CI on every push, so the claim is checkable rather than asserted.

What it adds over a plain checkpointer: the store underneath keeps superseded values on a bi-temporal
ledger, so a thread's state has queryable history and erasure leaves a signed receipt. Nothing calls a
model on the write path.

MIT · [source](https://github.com/DanceNitra/inspeximus)
