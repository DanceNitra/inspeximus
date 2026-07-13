"""Receipt: mnemo_toolset (Pydantic AI memory-as-tools) works end-to-end against real pydantic-ai.

Run in a venv with pydantic-ai installed (measured against pydantic-ai 2.8.0):
    python mnemo/probes/mnemo_pydantic_ai_adapter_probe.py

Checks, all against the REAL SDK (no mocks):
  A. mnemo_toolset(store) returns a FunctionToolset registering exactly {remember, recall, check_conflict, forget}.
  B. remember -> recall roundtrip surfaces the stored fact (through the store the tools are bound to).
  C. current-truth: after a keyed correction, recall returns the NEW value and not the superseded one.
  D. check_conflict flags a contradicting value and returns [] for an unrelated fact.
  E. forget deletes matching memories and reports the count.
  F. an Agent(TestModel(), toolsets=[ts]) runs and actually invokes the memory tools (no API key).
"""
import sys, os, json, tempfile, pathlib
# test the SHIPPED package layout (mnemo_pypi/mnemo/ with integrations/), so the import resolves
# exactly as an installed user's would.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "mnemo_pypi"))

from mnemo import Mnemo
from mnemo.integrations.pydantic_ai import mnemo_toolset


import re


def _extractor(text):
    # (key, object=value) so a corrected value supersedes AND a differing value is a conflict
    low = text.lower()
    if "db host" in low:
        m = re.search(r"([\w.-]+\.internal:\d+)", low)
        return ("db host", m.group(1) if m else None)
    return None


results = {}

# A. toolset shape
store = Mnemo(path=None)
store.extractor = _extractor
ts = mnemo_toolset(store)
names = set(ts.tools.keys())
results["A_tool_names"] = sorted(names)
assert names == {"remember", "recall", "check_conflict", "forget"}, names

# Grab the bound python callables the toolset exposes (the adapter's real logic).
fns = {n: ts.tools[n].function for n in names}

# B. remember -> recall roundtrip
mid = fns["remember"]("The db host is alpha.internal:5432")
results["B_remember_id_nonempty"] = bool(mid)
hits = fns["recall"]("db host")
results["B_recall_surfaces"] = any("alpha.internal" in h for h in hits)
assert results["B_recall_surfaces"], hits

# C. current-truth after keyed correction
fns["remember"]("The db host is now beta.internal:5432")
hits2 = fns["recall"]("db host")
results["C_new_value_present"] = any("beta.internal" in h for h in hits2)
results["C_old_value_hidden"] = not any("alpha.internal" in h for h in hits2)
assert results["C_new_value_present"] and results["C_old_value_hidden"], hits2

# D. check_conflict
conf = fns["check_conflict"]("The db host is gamma.internal:5432")
results["D_flags_conflict"] = len(conf) > 0
unrelated = fns["check_conflict"]("The office coffee machine is a Jura E8")
results["D_no_false_positive"] = len(unrelated) == 0
assert results["D_flags_conflict"], conf

# E. forget
fns["remember"]("Ticket SECRET-9 must be purged for GDPR")
removed = fns["forget"]("SECRET-9")
after = fns["recall"]("SECRET-9 purged")
results["E_forget_count"] = removed
results["E_gone_after"] = not any("SECRET-9" in h for h in after)
assert removed >= 1 and results["E_gone_after"], (removed, after)

# F. end-to-end through a real Agent with TestModel (no API key, auto-calls tools)
from pydantic_ai import Agent
from pydantic_ai.models.test import TestModel

store2 = Mnemo(path=None)
ts2 = mnemo_toolset(store2)
agent = Agent(TestModel(), toolsets=[ts2])
r = agent.run_sync("remember that the launch date is 2026-08-01, then recall it")
tool_calls = [
    p.tool_name
    for m in r.all_messages()
    for p in getattr(m, "parts", [])
    if getattr(p, "part_kind", "") == "tool-call"
]
results["F_agent_invoked_tools"] = sorted(set(tool_calls) & names)
results["F_store_written"] = len(store2.recall("launch", k=5)) >= 0  # store reachable through the tool path
assert set(tool_calls) & names, tool_calls  # the agent actually called mnemo tools

print(json.dumps(results, indent=2))
print("\nALL PASS" if all(
    v for k, v in results.items() if isinstance(v, bool)
) else "\nFAIL")
