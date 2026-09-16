# inspeximus MCP server: zero-dependency agent memory over stdio MCP.
# Build:  docker build -t inspeximus-mcp .
# Run  :  docker run -i --rm inspeximus-mcp        # stdio transport; wire into any MCP client
#
# The image installs the published package by its PyPI name, `inspeximus`. It read `agora-inspeximus`
# until 2026-09-16, a name that 404s on PyPI since the 1.26.0 rename, so every registry build that
# ran this file failed (Glama, measured on the 2.35.0 release).
FROM python:3.12-slim
WORKDIR /app
# the zero-dependency core plus the MCP extra
RUN pip install --no-cache-dir "inspeximus[mcp]"
# stdio MCP server; responds to MCP introspection (tools/list) on start
ENTRYPOINT ["inspeximus-mcp"]
