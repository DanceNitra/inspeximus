"""LangGraph checkpointer backed by inspeximus.

This distribution exists because LangChain does not accept integration code into its own
repositories -- third-party integrations are published independently and only their documentation is
contributed back. It adds a leaf to the `langgraph.checkpoint.*` namespace so the import reads the way
every other checkpointer does:

    from langgraph.checkpoint.inspeximus import InspeximusSaver

    graph = builder.compile(checkpointer=InspeximusSaver(path="threads.jsonl"))

The implementation lives in `inspeximus.integrations.langgraph`; this is a thin re-export, so there is
exactly one implementation to keep correct. It passes LangGraph's own conformance suite
(`langgraph-checkpoint-conformance`) at FULL 5/5 on the base capabilities, which is run in CI.
"""
from inspeximus.integrations.langgraph import InspeximusSaver

__all__ = ["InspeximusSaver"]
__version__ = "0.1.0"
