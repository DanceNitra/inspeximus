"""Does one store serve three harnesses at once, each seeing the others' writes without a restart?

WHY THIS FILE EXISTS. "One memory, every agent, at once" is the product claim, and until 2.28.0 it
held only on the write path. The read path was fixed the same day (`refresh()`), with unit tests
that inject one peer. This is the end-to-end receipt: three real harnesses on one file, in three
processes, in the order a working day produces them.

WHAT IT MEASURES. (1) A long-lived `inspeximus-mcp` server is started over stdio and kept running
for the whole probe. (2) The shell CLI writes a record. (3) The MCP server's `recall` tool is asked
for it, with no restart in between. (4) The MCP server writes a record through its `remember` tool.
(5) A library handle opened BEFORE step 4 is asked for it after `refresh()`. (6) The CLI lists the
store and both records are there. Every verdict is computed from what each harness returned.

HOW TO READ IT. `mcp_sees_cli_write`, `lib_sees_mcp_write` and `cli_sees_both` are the three
claims; all three True is the product claim holding end to end. Any False names the harness pair
where one memory is two.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time

import mcp  # noqa: F401  the server half needs the [mcp] extra; a missing one is a precondition, reported as such

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _receipt import write_receipt  # noqa: E402

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class _Mcp:
    """A minimal stdio JSON-RPC client for one long-lived inspeximus-mcp process."""

    def __init__(self, env):
        self.p = subprocess.Popen([sys.executable, "-m", "inspeximus.mcp_server"], stdin=subprocess.PIPE,
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                  encoding="utf-8", env=env, cwd=REPO)
        self.n = 0
        self.call("initialize", {"protocolVersion": "2024-11-05", "capabilities": {},
                                 "clientInfo": {"name": "probe", "version": "0"}})
        self.p.stdin.write(json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n")
        self.p.stdin.flush()

    def call(self, method, params):
        self.n += 1
        self.p.stdin.write(json.dumps({"jsonrpc": "2.0", "id": self.n, "method": method, "params": params}) + "\n")
        self.p.stdin.flush()
        while True:
            line = self.p.stdout.readline()
            if not line:
                raise RuntimeError("mcp server closed stdout: " + self.p.stderr.read()[-800:])
            msg = json.loads(line)
            if msg.get("id") == self.n:
                return msg

    def tool(self, name, args):
        out = self.call("tools/call", {"name": name, "arguments": args})
        res = out.get("result", {})
        texts = [c.get("text", "") for c in res.get("content", []) if c.get("type") == "text"]
        return res.get("structuredContent") or "\n".join(texts)

    def close(self):
        try:
            self.p.stdin.close()
            self.p.wait(timeout=10)
        except Exception:
            self.p.kill()


def main() -> int:
    d = tempfile.mkdtemp(prefix="onemem_")
    store = os.path.join(d, "s.json")
    env = dict(os.environ, INSPEXIMUS_PATH=store, PYTHONPATH=REPO)
    env.pop("INSPEXIMUS_STORE", None)
    out = {"probe": os.path.basename(__file__), "store_format": "rows (default)"}
    sys.path.insert(0, REPO)
    from inspeximus import Inspeximus

    t0 = time.time()
    mcp = _Mcp(env)
    try:
        lib = Inspeximus(path=store)                          # opened before anything is written
        cli = subprocess.run([sys.executable, "-m", "inspeximus.cli", "--path", store, "remember",
                              "the staging database is db-9"],
                             capture_output=True, text=True, env=env, cwd=REPO)
        out["cli_write_rc"] = cli.returncode
        got = mcp.tool("recall", {"query": "staging database"})
        out["mcp_recall_raw"] = str(got)[:300]
        out["mcp_sees_cli_write"] = "db-9" in json.dumps(got)
        got = mcp.tool("remember", {"text": "the deploy window is 02:00 UTC"})
        out["mcp_write_raw"] = str(got)[:200]
        before = any("02:00" in r["text"] for r in lib.items)
        # `getattr`, so the probe runs on a tree without refresh() (before 2.28.0) and reports the
        # stale read there instead of crashing before the verdict; that run is the positive control.
        out["refresh_available"] = hasattr(lib, "refresh")
        if out["refresh_available"]:
            lib.refresh()
        out["lib_saw_it_before_refresh"] = before
        out["lib_sees_mcp_write"] = any("02:00" in r["text"] for r in lib.recall("deploy window", k=5))
        ls = subprocess.run([sys.executable, "-m", "inspeximus.cli", "--path", store, "list"],
                            capture_output=True, text=True, env=env, cwd=REPO)
        out["cli_sees_both"] = ("db-9" in ls.stdout) and ("02:00" in ls.stdout)
        out["cli_list_rc"] = ls.returncode
    finally:
        mcp.close()
    out["seconds"] = round(time.time() - t0, 2)
    claims = ["mcp_sees_cli_write", "lib_sees_mcp_write", "cli_sees_both"]
    held = [c for c in claims if out.get(c) is True]
    out["verdict"] = ("one memory across three harnesses at once: all three cross-harness reads saw the "
                      "other harness's write with no restart" if len(held) == 3 else
                      "one memory is two somewhere: failed " + ", ".join(c for c in claims if c not in held))
    for c in claims:
        print("  %-22s %s" % (c, out.get(c)))
    print("  " + out["verdict"])
    write_receipt(__file__, out)
    return 0 if len(held) == 3 else 1


if __name__ == "__main__":
    sys.exit(main())
