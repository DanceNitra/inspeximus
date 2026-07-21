"""LangGraph BaseStore backed by inspeximus.

    from langgraph.store.inspeximus import InspeximusStore

    store = InspeximusStore(path="store.jsonl")
    store.put(("user", "42"), "timezone", {"tz": "UTC"})
    store.put(("user", "42"), "timezone", {"tz": "PST"})    # overwrites, like InMemoryStore
    store.get(("user", "42"), "timezone").value             # -> {"tz": "PST"}
    store.history(("user", "42"), "timezone")               # -> both values, oldest first

LangGraph publishes no conformance suite for stores; its documentation says to test against
`InMemoryStore` as the reference, which is what `store_audit.py` in the source repository does --
operation by operation, three repeats, with a falsification control that must fail. One divergence is
deliberate and documented there: a namespace whose last key was deleted is not listed, because a name
should not outlive the erasure of every value it held.

The implementation lives in `inspeximus.integrations.langgraph`; this is a thin re-export.
"""
from inspeximus.integrations.langgraph import InspeximusStore

__all__ = ["InspeximusStore"]
__version__ = "0.1.0"
