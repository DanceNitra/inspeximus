# mnemo MCP server (agora-mnemo) — zero-dependency agent memory over stdio MCP.
# Build:  docker build -t mnemo-mcp .
# Run  :  docker run -i --rm mnemo-mcp        # stdio transport; wire into any MCP client
FROM python:3.12-slim
WORKDIR /app
# install the published package with the MCP extra (zero-dependency core + the MCP server)
RUN pip install --no-cache-dir "agora-mnemo[mcp]"
# stdio MCP server; responds to MCP introspection (tools/list) on start
ENTRYPOINT ["mnemo-mcp"]
