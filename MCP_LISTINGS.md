# inspeximus MCP — registry listing pack

`inspeximus` ships an MCP stdio server (`inspeximus-mcp`, 73 tools). Registry manifest:
[`server.json`](server.json). Zero code — pure distribution.

*(The tool count is checked, not typed: `python claims_audit.py --numbers` counts `@mcp.tool()` in
`inspeximus/mcp_server.py` and fails if this file disagrees. It said 30 until 2026-08-01, when it was
26 short.)*

**One-liner:** Zero-dependency memory layer for AI agents that can say where a fact came from and prove
what it erased — one-call provenance, an auditable correction trail (supersession + echo-guard + revert),
and offline-verifiable erasure receipts, all deterministic with no LLM on the write path.

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

**Tools (73):** provenance and verification first, because that is what people ask for; the ordinary
memory operations follow. The count and the names are both checked against `@mcp.tool()` in
`inspeximus/mcp_server.py` (`python claims_audit.py --numbers`, plus `tests/test_readme_capabilities.py`),
so this list cannot drift from the server again.

*Provenance & verification:* provenance · why_recalled · history · supersession_report · verify_attribution · verify_writes · audit_bundle · verify_audit_bundle · anchor · witness · verify_witness · verify_cosigned_anchor · detect_split_view · verify_consistency · check_sources · identifier_contract · admissibility_preconditions · audit_the_audits · state_digest · selection_integrity · index_coherence

*Memory operations:* remember · remember_decision · memory_index · set_index_line · recall · recall_iterative · recall_followup · get · neighbors · as_of · token_report · route · observe · reopened · resolve_reopened · revert · check_conflict · contradictions · consolidate · consolidate_clusters · sleep · credit · value_by_cohort · where_am_i · projects

*Erasure & governance:* forget · forget_subject · forget_pii · erasure_certificate · erasure_residue · erasure_audit · erasure_report · retention · compliance_report · compliance_check · governance_report · pii_report · irreversible_budget_report · influence_gate_report · memory_report · verify_claim · check_self_narration

*Agent access control:* grant · revoke · grants · grant_log · can_read · recall_as · get_as

*Code guard:* deprecate_symbol · symbol_status · check_code

**Links:** repo https://github.com/DanceNitra/inspeximus · PyPI https://pypi.org/project/inspeximus/ ·
category: memory / knowledge-management.

---

## Where to submit

Corrected 2026-07-21. The previous version of this file was wrong in three ways and would have sent
people nowhere: it told them to `pip install agora-inspeximus` (that name 404s on PyPI — the package is
`inspeximus`), it claimed 12 tools when the server registers 30, and route 1 below had already been
retired upstream. A count maintained by hand drifts on exactly the schedule you stop watching it, which is
why both the count and the tool list are now checked against `inspeximus/mcp_server.py` rather than typed.

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
