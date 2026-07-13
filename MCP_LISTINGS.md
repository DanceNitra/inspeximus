# mnemo MCP — registry listing pack

`mnemo` ships an MCP stdio server (`mnemo-mcp`, 12 tools) as `agora-mnemo` on PyPI. This is the ready-to-submit
content for the MCP directories. Registry manifest: [`server.json`](server.json). Zero code — pure distribution.

**One-liner:** Zero-dependency memory layer for AI agents with a first-class correction channel — recall,
consolidation, revert, echo-guard, lineage-aware retract + re-derive, and tamper-evident erasure proof.

**Install / run:**
```bash
pip install agora-mnemo
mnemo-mcp            # stdio; persists to ./mnemo_memory.json (set MNEMO_PATH to change)
```

**Client config (Claude Desktop / Cursor / any MCP client):**
```json
{
  "mcpServers": {
    "mnemo": { "command": "mnemo-mcp", "env": { "MNEMO_PATH": "./mnemo_memory.json" } }
  }
}
```

**Tools (12):** remember · recall · route · revert · forget · consolidate · consolidate_clusters · sleep ·
contradictions · check_conflict · value_by_cohort · credit

**Links:** repo https://github.com/DanceNitra/agora · PyPI https://pypi.org/project/agora-mnemo/ ·
category: memory / knowledge-management.

---

## Where to submit (owner action for the web/PR ones; anon identity for any commit)

1. **Official community servers list** — `github.com/modelcontextprotocol/servers` → add one line to the
   community-servers section of `README.md` via PR:
   `- **[mnemo](https://github.com/DanceNitra/agora)** - Zero-dependency agent memory with a first-class correction channel (revert, echo-guard, lineage retract + re-derive) and tamper-evident erasure.`
2. **Smithery** (smithery.ai) — "Add server", point at the GitHub repo; a `smithery.yaml` can be added later
   for hosted deploy, stdio listing works from the repo + server.json.
3. **Glama** (glama.ai/mcp/servers) — auto-indexes public GitHub MCP servers; the `server.json` + README MCP
   section make it crawlable. Submit the repo if not picked up.
4. **PulseMCP** (pulsemcp.com) — submit form, repo + one-liner above.
5. **mcp.so** and **Awesome MCP Servers** (`punkpeye/awesome-mcp-servers`) — PR / submit, same one-liner.

Keep the one-liner and tool list identical across all so the entry is recognizable and de-dups cleanly.
