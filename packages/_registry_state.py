"""Is this exact server version already in the MCP registry?

Run as:  python packages/_registry_state.py            # reads server.json, writes to $GITHUB_OUTPUT

Prints `already=true|false`. The registry refuses a duplicate version with a 400, which is correct on its
side but makes the publish step fail on any re-run -- including a re-run of a release that worked. Asking
first turns "it is already listed" into the success it actually is, without making the publish step
swallow real errors.
"""
import json
import os
import pathlib
import sys
import urllib.parse
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[1]
REGISTRY = "https://registry.modelcontextprotocol.io/v0.1/servers"


def listed_version(name: str) -> str | None:
    url = f"{REGISTRY}?search={urllib.parse.quote(name)}"
    with urllib.request.urlopen(url, timeout=30) as r:
        data = json.load(r)
    for entry in data.get("servers", []):
        server = entry.get("server", entry)
        if server.get("name") == name:
            return server.get("version")
    return None


def main() -> int:
    manifest = json.loads((ROOT / "server.json").read_text(encoding="utf-8"))
    name, version = manifest["name"], manifest["version"]
    try:
        current = listed_version(name)
    except Exception as e:
        # A registry we cannot reach is not evidence that the version is absent; let publish decide.
        print(f"could not query the registry ({type(e).__name__}); assuming not listed")
        current = None
    already = current == version
    print(f"{name}: registry has {current!r}, we want {version!r} -> already={already}")
    out = os.environ.get("GITHUB_OUTPUT")
    if out:
        with open(out, "a", encoding="utf-8") as fh:
            fh.write(f"already={'true' if already else 'false'}\n")
            fh.write(f"version={version}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
