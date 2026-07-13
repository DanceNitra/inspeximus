"""mnemo.integrations — thin adapters that plug mnemo into agent frameworks.

Optional extras; importing them is opt-in and never pulls a dependency into mnemo's zero-dependency core.
Each adapter matches the target framework's protocol STRUCTURALLY (duck-typed) — it does NOT import the
framework, so `pip install agora-mnemo` alone is enough to use them against an installed framework.
"""
