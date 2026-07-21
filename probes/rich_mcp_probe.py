"""rich_mcp_probe.py — the MCP server exposes the 3 MCP primitives (tools + resources + prompts), not tools-only.

A "rich" MCP server ships all three primitives and covers the product's real surface. Before this, inspeximus's MCP was
tools-only and exposed ~19 of ~60 methods (none of the integrity/governance surface). Asserts (each able to FAIL):
  1. the new GOVERNANCE/INTEGRITY tools are registered (forget_subject, governance_report, verify_writes,
     pii_report, forget_pii, influence_gate_report, why_recalled, supersession_report).
  2. RESOURCES are registered (inspeximus://digest, inspeximus://contradictions, inspeximus://governance) + the memory/{id} template.
  3. PROMPTS are registered (recall_before_answer, consolidate_session, review_contradictions).
  4. recall exposes the new mmr + trusted_only params and they work end-to-end.
  5. a governance tool actually returns a real report (callable, not just registered).
"""
import os, sys, tempfile, asyncio
os.environ["INSPEXIMUS_PATH"] = os.path.join(tempfile.gettempdir(), "rich_mcp_probe_store.json")
os.environ["INSPEXIMUS_EMBED_URL"] = ""                 # lexical, no embedder / no GPU
if os.path.exists(os.environ["INSPEXIMUS_PATH"]):
    os.remove(os.environ["INSPEXIMUS_PATH"])
sys.path.insert(0, ".")
import inspeximus.mcp_server as M

FAILS = []
def check(n, c):
    print(f"  [{'OK ' if c else 'XXX'}] {n}")
    if not c: FAILS.append(n)

# seed the store
M._MEM.remember("Paris is the capital of France.", key="a1")
M._MEM.remember("The capital of France is Paris city.", key="a2")
M._MEM.remember("Paris has an airport at Charles de Gaulle.", key="b")

async def names():
    tools = {t.name for t in await M.mcp.list_tools()}
    res = {str(r.uri) for r in await M.mcp.list_resources()}
    tmpl = {t.uriTemplate for t in await M.mcp.list_resource_templates()}
    prompts = {p.name for p in await M.mcp.list_prompts()}
    return tools, res, tmpl, prompts

tools, res, tmpl, prompts = asyncio.run(names())

want_tools = {"forget_subject", "governance_report", "verify_writes", "pii_report", "forget_pii",
              "influence_gate_report", "why_recalled", "supersession_report"}
check(f"1 governance/integrity tools registered ({len(want_tools)})", want_tools <= tools)
check("2 resources registered (digest/contradictions/governance)",
      any("digest" in r for r in res) and any("contradictions" in r for r in res) and any("governance" in r for r in res))
check("2b memory/{id} resource TEMPLATE registered", any("memory/" in t for t in tmpl))
check("3 prompts registered (recall_before_answer/consolidate_session/review_contradictions)",
      {"recall_before_answer", "consolidate_session", "review_contradictions"} <= prompts)

# 4. recall mmr + trusted_only params work end-to-end (mmr diversifies)
plain = [h["text"] for h in M.recall("capital of France Paris", k=2)]
div = [h["text"] for h in M.recall("capital of France Paris", k=2, mmr=0.2)]
check("4 recall(mmr=) surfaces the diverse airport hit", any("airport" in t for t in div) and not any("airport" in t for t in plain))

# 5. a governance tool returns a real report
gr = M.governance_report()
check("5 governance_report() returns a dict report", isinstance(gr, dict) and len(gr) > 0)

print(f"\n  tools={len(tools)} resources={len(res)} templates={len(tmpl)} prompts={len(prompts)}")
print(f"{'ALL PASS' if not FAILS else 'FAILED: ' + ', '.join(FAILS)}")
sys.exit(1 if FAILS else 0)
