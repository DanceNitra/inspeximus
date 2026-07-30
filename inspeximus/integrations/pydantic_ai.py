"""inspeximus_toolset — memory-as-tools for Pydantic AI, backed by inspeximus.

Pydantic AI has no built-in persistent memory by design; the established pattern (Hindsight's
hindsight-pydantic-ai, etc.) is to expose memory as agent tools. `inspeximus_toolset(store)` returns a Pydantic AI
`FunctionToolset` the agent can call: remember, recall, check_conflict, forget. Retrieval is current-truth
(inspeximus's supersession-filtered recall hides corrected values), and the agent can check a fact for conflicts
before storing it.

    from pydantic_ai import Agent
    from inspeximus.integrations.pydantic_ai import inspeximus_toolset
    agent = Agent("openai:gpt-4o-mini", toolsets=[inspeximus_toolset(path="mem.json")])

Pass a supersession `extractor` (text -> (key, object)) so remembered facts auto-key and a corrected value
stops surfacing; see Inspeximus.extractor. Importing this module imports Pydantic AI (opt-in extra); `import inspeximus`
stays zero-dependency.
"""
from __future__ import annotations
from typing import Any
from .._surface import open_store          # one surface posture; see _surface.py


def inspeximus_toolset(store: Any = None, path: str | None = None, k: int = 5, extractor=None):
    """Build a Pydantic AI FunctionToolset of memory tools bound to a inspeximus store."""
    if store is None:
        from inspeximus import Inspeximus
        store = open_store(path, resolve=False)
    if extractor is not None:
        store.extractor = extractor

    def remember(text: str, subject: str = "pydantic-ai") -> str:
        """Store a fact in long-term memory. Returns the stored memory's id."""
        return store.remember(text, source={"doc": subject})

    def recall(query: str) -> list[str]:
        """Retrieve the most relevant facts for a query. Superseded (corrected-away) values are not
        returned, so you get current-truth."""
        return [h["text"] for h in store.recall(query, k=k)]

    def check_conflict(text: str) -> list[str]:
        """Before storing a fact, check whether it would CONTRADICT something already in memory (a value
        change on a managed key, or a numeric/negation clash with a similar memory). Returns the conflicting
        memories' text (empty list means no conflict). A pure duplicate is not a conflict."""
        key = obj = None
        if store.extractor is not None:            # auto-key so a value change on a managed slot is caught
            ex = store.extractor(text)
            if ex:
                key, obj = ex
        return [c["text"] for c in store.check_conflict(text, key=key, object=obj)]

    def forget(contains: str) -> int:
        """Delete every memory whose text contains this substring (case-insensitive) — an erasure / hard
        correction. Returns how many were removed.

        A BLANK substring is refused. Every string contains "", so `forget("")` is not a filter at all --
        MEASURED, it deleted 3 of 3 memories and left the store empty. This is a tool the MODEL calls, so
        the argument is model-generated and an empty slot is an ordinary failure mode, not an exotic one;
        the delete is irreversible. Raising gives the model something it can correct, which returning 0
        would not. A non-blank needle is still deliberately broad -- "ann" reaches "Joanna" -- and that is
        the documented contract."""
        if not (contains or "").strip():
            raise ValueError(
                "forget(contains=...) needs a non-blank substring: every memory contains a blank one, so "
                "this would erase the whole store, irreversibly. Pass the text you want removed.")
        return store.forget(where=lambda r: contains.lower() in r["text"].lower())["forgotten"]

    from pydantic_ai.toolsets import FunctionToolset
    return FunctionToolset([remember, recall, check_conflict, forget])
