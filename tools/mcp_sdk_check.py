"""The MCP server under the installed mcp SDK: it imports, registers every tool, and answers a write and a recall.

    python tools/mcp_sdk_check.py [expected mcp version]

CI runs it once on the 1.x floor and once on 2.x (ci.yml, job mcp-sdk), because the server resolves the SDK's
layout at import (inspeximus/mcp_server.py, "THE ONE PLACE THE SDK MAJOR VERSION IS RESOLVED") and a single leg
leaves the other layout untested. The expected tool count is read from the source's `@mcp.tool()` lines, so a new
tool does not need this file changed; a tool that the SDK silently failed to register still fails it.

Exit 0 when every check passes, 1 otherwise. The store is a throwaway directory; nothing outside it is written.
"""
import importlib.metadata
import json
import os
import re
import sys
import tempfile


def main(argv):
    want = argv[1] if len(argv) > 1 else None
    have = importlib.metadata.version("mcp")
    scratch = tempfile.mkdtemp(prefix="mcp-sdk-check-")
    for k in [k for k in os.environ if k.startswith("INSPEXIMUS_")]:
        del os.environ[k]
    os.makedirs(os.path.join(scratch, "store"))
    os.environ.update(INSPEXIMUS_PATH=os.path.join(scratch, "store", "store.json"),
                      INSPEXIMUS_KEY_HOME=os.path.join(scratch, "key_home"), INSPEXIMUS_NO_UPDATE_CHECK="1")

    import anyio
    import inspeximus
    from inspeximus import mcp_server as m

    with open(m.__file__, encoding="utf-8") as f:
        declared = len(re.findall(r"^@mcp\.tool\(", f.read(), re.M))
    registered = len(m.mcp._tool_manager.list_tools())

    async def roundtrip():
        try:
            from mcp import Client                                   # mcp 2.x
            session = Client(m.mcp)
        except ImportError:                                          # mcp 1.x
            from mcp.shared.memory import create_connected_server_and_client_session
            session = create_connected_server_and_client_session(m._lowlevel(m.mcp))
        async with session as c:
            listed = len((await c.list_tools()).tools)
            await c.call_tool("remember_decision", {"decision": "the check wrote zebra-quartz", "because": "check",
                                                    "topic": "mcp-sdk-check"})
            res = await c.call_tool("recall", {"query": "zebra-quartz"})
            found = "zebra-quartz" in "".join(getattr(b, "text", "") for b in res.content)
            return listed, found

    listed, found = anyio.run(roundtrip)
    report = {"mcp": have, "declared": declared, "registered": registered, "listed": listed,
              "server_version": m._lowlevel(m.mcp).version, "inspeximus": inspeximus.__version__,
              "write_then_recall": found}
    print(json.dumps(report))
    problems = []
    if want and have != want:
        problems.append("mcp %s is installed, not %s: this leg tests the wrong SDK" % (have, want))
    if not declared or not (declared == registered == listed):
        problems.append("%d tools declared, %d registered, %d listed to a client" % (declared, registered, listed))
    if report["server_version"] != inspeximus.__version__:
        problems.append("serverInfo.version is %r, not inspeximus %s" % (report["server_version"],
                                                                         inspeximus.__version__))
    if not found:
        problems.append("a decision written through the client was not found by recall")
    for p in problems:
        print("FAIL: " + p, file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
