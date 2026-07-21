"""mnemo.integrations — thin adapters that plug mnemo into agent frameworks.

Optional extras; importing them is opt-in and never pulls a dependency into mnemo's zero-dependency core —
nothing here is imported by `mnemo/__init__.py`, so `pip install agora-mnemo` stays dependency-free.

MOST adapters match the target framework's protocol STRUCTURALLY (duck-typed) and do NOT import it, so they
work against an installed framework with no extra install. The exception is `langgraph`, which SUBCLASSES
LangGraph's `BaseStore` / `BaseCheckpointSaver` and therefore imports langgraph at module level: importing
`mnemo.integrations.langgraph` without langgraph installed raises ImportError, by design.
"""
