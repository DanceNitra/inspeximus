# inspeximus MCP — registry listing pack

`inspeximus` ships an MCP stdio server (`inspeximus-mcp`, 30 tools). Registry manifest:
[`server.json`](server.json). Zero code — pure distribution.

**One-liner:** Zero-dependency memory layer for AI agents with a first-class correction channel — recall,
consolidation, revert, echo-guard, lineage-aware retract + re-derive, and tamper-evident erasure proof.

**Install / run:**
```bash
pip install "inspeximus[mcp]"
inspeximus-mcp            # stdio; persists to ./inspeximus_memory.json (set INSPEXIMUS_PATH to change)
```

**Client config (Claude Desktop / Cursor / any MCP client):**
```json
{
  "mcpServers": {
    "inspeximus": { "command": "inspeximus-mcp", "env": { "INSPEXIMUS_PATH": "./inspeximus_memory.json" } }
  }
}
```

Or let the CLI write it: `inspeximus install --ide claude` (also cursor, windsurf, codex, cline).

**Tools (30):** remember · remember_decision · revert · route · observe · reopened · resolve_reopened ·
recall · get · neighbors · token_report · consolidate · sleep · consolidate_clusters · contradictions ·
check_conflict · value_by_cohort · credit · forget · forget_subject · governance_report · verify_writes ·
witness · verify_witness · index_coherence · pii_report · forget_pii · influence_gate_report ·
why_recalled · supersession_report

**Links:** repo https://github.com/DanceNitra/inspeximus · PyPI https://pypi.org/project/inspeximus/ ·
category: memory / knowledge-management.

---

## Where to submit

Corrected 2026-07-21. The previous version of this file was wrong in three ways and would have sent
people nowhere: it told them to `pip install agora-inspeximus` (that name 404s on PyPI — the package is
`inspeximus`), it claimed 12 tools when the server registers 30, and route 1 below had already been
retired upstream.

1. **The official MCP registry** — `registry.modelcontextprotocol.io`. This is now the primary route and
   the one the reference repo itself redirects to. Self-serve, no review queue. Ownership is proven by the
   `mcp-name: io.github.DanceNitra/inspeximus` marker already present in the published PyPI README, so all
   that is left is:
   ```bash
   mcp-publisher login github      # must authenticate as DanceNitra (device flow — needs the owner)
   mcp-publisher publish --dry-run && mcp-publisher publish
   ```
   Keep `server.json`'s two `version` fields in step with the released package, or the listing points at a
   version nobody can install.

2. **`punkpeye/awesome-mcp-servers`** — PR with the one-liner above. Large reach, low bar.

3. **Glama** (glama.ai/mcp/servers) — auto-indexes public GitHub MCP servers; `glama.json` is already in
   the repo. Claim the listing if it is not picked up.

4. **PulseMCP** (pulsemcp.com/submit) — submit form, repo + one-liner. Worth it partly as instrumentation:
   PulseMCP publishes per-server visitor estimates, which is the only place we can measure our own traffic.

5. **mcp.so** (mcp.so/submit) — cheap, low signal.

**Retired route:** `github.com/modelcontextprotocol/servers` no longer accepts community server entries.
Its README now says only *"If you are looking for a list of MCP servers, you can browse published servers
on the MCP Registry."* PR #4413 was closed on exactly that basis in June 2026. Do not spend time there.

**Needs a hosted endpoint, not stdio:** Smithery (remote HTTPS or an `.mcpb` bundle) and the Anthropic
Connectors Directory (remote MCP only, manual review). Both are blocked until there is a hosted inspeximus.

Keep the one-liner and tool list identical across all of them so the entry is recognizable and de-dups
cleanly.
