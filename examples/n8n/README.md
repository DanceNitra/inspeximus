# n8n template — AI agent with self-correcting memory (mnemo)

`self_correcting_memory_agent.json` is an importable [n8n](https://n8n.io) workflow: a chat agent whose
long-term memory is mnemo over MCP — so **a corrected fact stays corrected**, a restated stale value lands
retired instead of winning, and the value history stays auditable.

## Use it

1. Bridge mnemo's MCP server to SSE (n8n's MCP Client Tool speaks SSE/HTTP; one command, needs
   [uv](https://docs.astral.sh/uv/) + Node):

   ```bash
   npx -y supergateway --stdio "uvx --from agora-mnemo[mcp] mnemo-mcp" --port 8808
   ```

2. In n8n: **Workflows → Import from file** → pick `self_correcting_memory_agent.json`.
3. Set your chat-model credential on the model node, open the chat, and run the demo conversation from the
   sticky note (Frankfurt → correction: Ohio → stale echo: Frankfurt → ask → **Ohio**).

Memory persists in `mnemo_memory.json` where the bridge runs (`MNEMO_PATH` changes it). Both the import and
the bridge command are tested against n8n 2.3.6.
