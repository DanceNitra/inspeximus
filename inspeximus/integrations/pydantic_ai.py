"""mnemo_toolset — memory-as-tools for Pydantic AI, backed by mnemo.

Pydantic AI has no built-in persistent memory by design; the established pattern (Hindsight's
hindsight-pydantic-ai, etc.) is to expose memory as agent tools. `mnemo_toolset(store)` returns a Pydantic AI
`FunctionToolset` the agent can call: remember, recall, check_conflict, forget. Retrieval is current-truth
(mnemo's supersession-filtered recall hides corrected values), and the agent can check a fact for conflicts
before storing it.

    from pydantic_ai import Agent
    from inspeximus.integrations.pydantic_ai import mnemo_toolset
    agent = Agent("openai:gpt-4o-mini", toolsets=[mnemo_toolset(path="mem.json")])

Pass a supersession `extractor` (text -> (key, object)) so remembered facts auto-key and a corrected value
stops surfacing; see Mnemo.extractor. Importing this module imports Pydantic AI (opt-in extra); `import inspeximus`
stays zero-dependency.
"""
from __future__ import annotations
from typing import Any


def mnemo_toolset(store: Any = None, path: str | None = None, k: int = 5, extractor=None):
    """Build a Pydantic AI FunctionToolset of memory tools bound to a mnemo store."""
    if store is None:
        from inspeximus import Mnemo
        store = Mnemo(path=path)
    if extractor is not None:
        store.extractor = extractor

    def remember(text: str) -> str:
        """Store a fact in long-term memory. Returns the stored memory's id."""
        return store.remember(text)

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
        correction. Returns how many were removed."""
        return store.forget(where=lambda r: contains.lower() in r["text"].lower())["forgotten"]

    from pydantic_ai.toolsets import FunctionToolset
    return FunctionToolset([remember, recall, check_conflict, forget])
