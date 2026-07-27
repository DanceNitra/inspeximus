"""inspeximus.integrations — thin adapters that plug inspeximus into agent frameworks.

Optional extras; importing them is opt-in and never pulls a dependency into inspeximus's zero-dependency core —
nothing here is imported by `inspeximus/__init__.py`, so `pip install inspeximus` stays dependency-free.

MOST adapters match the target framework's protocol STRUCTURALLY (duck-typed) and do NOT import it, so they
work against an installed framework with no extra install. The exception is `langgraph`, which SUBCLASSES
LangGraph's `BaseStore` / `BaseCheckpointSaver` and therefore imports langgraph at module level: importing
`inspeximus.integrations.langgraph` without langgraph installed raises ImportError, by design.
"""
