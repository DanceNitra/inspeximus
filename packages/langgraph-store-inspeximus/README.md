# langgraph-store-inspeximus

A LangGraph `BaseStore` backed by [inspeximus](https://pypi.org/project/inspeximus/) — a drop-in for
`InMemoryStore` that also remembers what a value used to be.

```bash
pip install langgraph-store-inspeximus
```

```python
from langgraph.store.inspeximus import InspeximusStore

store = InspeximusStore(path="store.jsonl")
store.put(("user", "42"), "timezone", {"tz": "UTC"})
store.put(("user", "42"), "timezone", {"tz": "PST"})   # overwrites, like InMemoryStore
store.get(("user", "42"), "timezone").value            # -> {"tz": "PST"}
store.history(("user", "42"), "timezone")              # -> [{"tz": "UTC"}, {"tz": "PST"}]
```

LangGraph ships no conformance suite for stores; its docs say to test against `InMemoryStore` as the
reference. That is what [`store_audit.py`](https://github.com/DanceNitra/inspeximus/blob/main/store_audit.py)
does: every operation run twice, against the reference and against this store, three repeats, with a
falsification control that breaks the adapter on purpose and must fail. It covers CRUD, namespace
isolation, search/limit/delete-by-None, and `list_namespaces` with prefix, suffix, `*` wildcards,
`max_depth`, ordering, limit and offset.

**One deliberate divergence, stated rather than hidden:** after the last key in a namespace is
deleted, `InMemoryStore` still lists the now-empty namespace and this store does not. Matching it
would mean a namespace name outliving the erasure of every value it held.

MIT · [source](https://github.com/DanceNitra/inspeximus)
